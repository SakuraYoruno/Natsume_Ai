"""Edge voice resource gate contract (priority, mutex, and observability)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_GATE_PREEMPTED,
    VOICE_GATE_PRIORITY_BLOCKED,
    VOICE_GATE_RESOURCE_LIMITED,
    VOICE_GATE_TASK_INVALID,
)
from host_brain.core.ids import now_ms


TASK_ASR_INGRESS = "ASR_INGRESS"
TASK_PLAYBACK = "PLAYBACK"
TASK_FALLBACK_LLM = "FALLBACK_LLM"
TASK_TTS_SYNTH = "TTS_SYNTH"

TASK_PRIORITY = {
    TASK_ASR_INGRESS: 0,
    TASK_PLAYBACK: 1,
    TASK_FALLBACK_LLM: 2,
    TASK_TTS_SYNTH: 3,
}

EVENT_VOICE_RESOURCE_GATE = "VOICE_RESOURCE_GATE"
DIRECTION_HOST_TO_EDGE = "HOST_TO_EDGE"
S_VOICE_RESOURCE_GATE = "S_VOICE_RESOURCE_GATE"

STATUS_ADMITTED = "ADMITTED"
STATUS_REJECTED = "REJECTED"
STATUS_PREEMPTED = "PREEMPTED"
STATUS_RELEASED = "RELEASED"


@dataclass(frozen=True)
class ResourceGateDecision:
    admitted: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


# ---------------------------------------------------------------------------
# M15-E: ResourcePreemptionEvent（优先级抢占审计事件）
# ---------------------------------------------------------------------------

from host_brain.core.errors import RESOURCE_GATE_PREEMPTED as _RESOURCE_GATE_PREEMPTED  # noqa: E402


@dataclass(frozen=True)
class ResourcePreemptionEvent:
    """
    资源抢占审计事件（M15-E）。
    - 写入审计链（barge_in_log / voice_event_log）
    - 含 preemptor/preempted/reason_code/evidence_ref
    """
    event_id: str
    preemptor: str                  # 抢占者（"ASR" | "PLAYBACK" | "TTS_SYNTH"）
    preempted: str                  # 被抢占者
    reason_code: str                # RESOURCE_GATE_PREEMPTED
    ts_ms: int
    evidence_ref: str               # 审计链引用

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "preemptor": self.preemptor,
            "preempted": self.preempted,
            "reason_code": self.reason_code,
            "ts_ms": self.ts_ms,
            "evidence_ref": self.evidence_ref,
        }


def build_resource_preemption_event(
    *,
    preemptor: str,
    preempted: str,
    evidence_ref: str,
    event_id: str | None = None,
    ts_ms: int | None = None,
) -> ResourcePreemptionEvent:
    """
    工厂函数：构建 ResourcePreemptionEvent。
    - preemptor/preempted 必须是合法的 TASK_* 常量
    - evidence_ref 必填
    """
    from host_brain.core.ids import new_request_id as _new_request_id, now_ms as _now_ms

    valid_tasks = set(TASK_PRIORITY.keys())
    if preemptor not in valid_tasks:
        raise ValueError(f"invalid preemptor task: {preemptor!r}")
    if preempted not in valid_tasks:
        raise ValueError(f"invalid preempted task: {preempted!r}")
    if not evidence_ref or not str(evidence_ref).strip():
        raise ValueError("ResourcePreemptionEvent requires evidence_ref")

    return ResourcePreemptionEvent(
        event_id=event_id or _new_request_id(),
        preemptor=str(preemptor),
        preempted=str(preempted),
        reason_code=_RESOURCE_GATE_PREEMPTED,
        ts_ms=int(ts_ms if ts_ms is not None else _now_ms()),
        evidence_ref=str(evidence_ref),
    )


class VoiceResourceGate:
    """Deterministic resource gate with priority/mutex and auditable decisions."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        vram_soft_limit_ratio: float = 0.90,
        max_fallback_queue_depth: int = 1,
    ) -> None:
        self.ledger = ledger
        self.vram_soft_limit_ratio = float(vram_soft_limit_ratio)
        self.max_fallback_queue_depth = int(max_fallback_queue_depth)
        self._active: dict[str, dict[str, Any]] = {}
        self._last_metrics: dict[str, Any] = {
            "vram_total_mb": 0,
            "vram_used_mb": 0,
            "queue_depth": 0,
            "vram_utilization": 0.0,
        }

    def request(
        self,
        *,
        request_id: str,
        task: str,
        session_id: str = "",
        metrics: dict[str, Any] | None = None,
        hold: bool = True,
        ts_ms: int | None = None,
    ) -> ResourceGateDecision:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        task_name = str(task).strip().upper()
        normalized_metrics = _normalize_metrics(metrics)
        self._last_metrics = dict(normalized_metrics)

        if task_name not in TASK_PRIORITY:
            return self._reject(
                request_id=request_id,
                task=task_name or str(task),
                session_id=session_id,
                reason_code=VOICE_GATE_TASK_INVALID,
                detail=f"unknown gate task: {task}",
                metrics=normalized_metrics,
                ts_ms=now_value,
            )

        if self._is_resource_limited(task_name, normalized_metrics):
            return self._reject(
                request_id=request_id,
                task=task_name,
                session_id=session_id,
                reason_code=VOICE_GATE_RESOURCE_LIMITED,
                detail="resource limited by vram/queue policy",
                metrics=normalized_metrics,
                ts_ms=now_value,
            )

        higher_active = [
            active_task
            for active_task in self._active.keys()
            if TASK_PRIORITY[active_task] < TASK_PRIORITY[task_name]
        ]
        if higher_active:
            return self._reject(
                request_id=request_id,
                task=task_name,
                session_id=session_id,
                reason_code=VOICE_GATE_PRIORITY_BLOCKED,
                detail=f"blocked by higher-priority task(s): {sorted(higher_active)}",
                metrics=normalized_metrics,
                ts_ms=now_value,
            )

        preempted = self._preempt_lower_tasks(
            request_id=request_id,
            incoming_task=task_name,
            metrics=normalized_metrics,
            ts_ms=now_value,
        )
        if hold:
            self._active[task_name] = {
                "request_id": request_id,
                "session_id": session_id,
                "task": task_name,
                "since_ts_ms": now_value,
            }

        reason_code = VOICE_GATE_PREEMPTED if preempted else None
        event = {
            "task": task_name,
            "status": STATUS_ADMITTED,
            "preempted_tasks": preempted,
            "active_tasks": self._active_task_summary(),
            "metrics": normalized_metrics,
            "hold": bool(hold),
            "ts_ms": now_value,
        }
        detail = (
            f"admitted with preemption: {preempted}"
            if preempted
            else "admitted by gate policy"
        )
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            status=STATUS_ADMITTED,
            reason_code=reason_code,
            detail=detail,
            payload=event,
            ts_ms=now_value,
            task=task_name,
        )
        return ResourceGateDecision(
            admitted=True,
            status=STATUS_ADMITTED,
            reason_code=reason_code,
            stage=S_VOICE_RESOURCE_GATE,
            detail=detail,
            event=event,
        )

    def release(
        self,
        *,
        request_id: str,
        task: str,
        session_id: str = "",
        ts_ms: int | None = None,
    ) -> ResourceGateDecision:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        task_name = str(task).strip().upper()
        active = self._active.get(task_name)
        released = False
        if active is not None:
            bound_request_id = str(active.get("request_id") or "")
            if not bound_request_id or bound_request_id == request_id:
                self._active.pop(task_name, None)
                released = True

        event = {
            "task": task_name,
            "status": STATUS_RELEASED,
            "released": released,
            "active_tasks": self._active_task_summary(),
            "metrics": self.snapshot()["metrics"],
            "ts_ms": now_value,
        }
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            status=STATUS_RELEASED,
            reason_code=None,
            detail="task released" if released else "no matching task to release",
            payload=event,
            ts_ms=now_value,
            task=task_name,
        )
        return ResourceGateDecision(
            admitted=released,
            status=STATUS_RELEASED,
            reason_code=None,
            stage=S_VOICE_RESOURCE_GATE,
            detail="task released" if released else "no matching task to release",
            event=event,
        )

    def snapshot(self) -> dict[str, Any]:
        metrics = dict(self._last_metrics)
        return {
            "stage": S_VOICE_RESOURCE_GATE,
            "priority_order": [
                TASK_ASR_INGRESS,
                TASK_PLAYBACK,
                TASK_FALLBACK_LLM,
                TASK_TTS_SYNTH,
            ],
            "active_tasks": self._active_task_summary(),
            "metrics": {
                "vram_total_mb": int(metrics.get("vram_total_mb", 0)),
                "vram_used_mb": int(metrics.get("vram_used_mb", 0)),
                "queue_depth": int(metrics.get("queue_depth", 0)),
                "vram_utilization": float(metrics.get("vram_utilization", 0.0)),
            },
        }

    def _preempt_lower_tasks(
        self,
        *,
        request_id: str,
        incoming_task: str,
        metrics: dict[str, Any],
        ts_ms: int,
    ) -> list[str]:
        incoming_priority = TASK_PRIORITY[incoming_task]
        preempted: list[str] = []
        for active_task in list(self._active.keys()):
            if TASK_PRIORITY[active_task] <= incoming_priority:
                continue
            preempted.append(active_task)
            victim = dict(self._active.pop(active_task))
            event = {
                "task": active_task,
                "status": STATUS_PREEMPTED,
                "preempted_by_task": incoming_task,
                "preempted_by_request_id": request_id,
                "victim_request_id": str(victim.get("request_id") or ""),
                "active_tasks": self._active_task_summary(),
                "metrics": metrics,
                "ts_ms": ts_ms,
            }
            self._log_event(
                request_id=request_id,
                session_id=str(victim.get("session_id") or ""),
                status=STATUS_PREEMPTED,
                reason_code=VOICE_GATE_PREEMPTED,
                detail=f"{active_task} preempted by {incoming_task}",
                payload=event,
                ts_ms=ts_ms,
                task=active_task,
            )
        return sorted(preempted)

    def _is_resource_limited(self, task: str, metrics: dict[str, Any]) -> bool:
        total = int(metrics.get("vram_total_mb", 0))
        used = int(metrics.get("vram_used_mb", 0))
        queue_depth = int(metrics.get("queue_depth", 0))
        utilization = float(metrics.get("vram_utilization", 0.0))

        lower_priority_task = task in {TASK_FALLBACK_LLM, TASK_TTS_SYNTH}
        if lower_priority_task and total > 0 and utilization >= self.vram_soft_limit_ratio:
            return True
        if used > total > 0 and lower_priority_task:
            return True
        if task == TASK_FALLBACK_LLM and queue_depth > self.max_fallback_queue_depth:
            return True
        return False

    def _reject(
        self,
        *,
        request_id: str,
        task: str,
        session_id: str,
        reason_code: str,
        detail: str,
        metrics: dict[str, Any],
        ts_ms: int,
    ) -> ResourceGateDecision:
        event = {
            "task": task,
            "status": STATUS_REJECTED,
            "active_tasks": self._active_task_summary(),
            "metrics": metrics,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            detail=detail,
            payload=event,
            ts_ms=ts_ms,
            task=task,
        )
        return ResourceGateDecision(
            admitted=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=S_VOICE_RESOURCE_GATE,
            detail=detail,
            event=event,
        )

    def _active_task_summary(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task_name, row in sorted(self._active.items(), key=lambda item: TASK_PRIORITY[item[0]]):
            rows.append(
                {
                    "task": task_name,
                    "request_id": str(row.get("request_id") or ""),
                    "session_id": str(row.get("session_id") or ""),
                    "since_ts_ms": int(row.get("since_ts_ms") or 0),
                }
            )
        return rows

    def _log_event(
        self,
        *,
        request_id: str,
        session_id: str,
        status: str,
        reason_code: str | None,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
        task: str,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": EVENT_VOICE_RESOURCE_GATE,
                "direction": DIRECTION_HOST_TO_EDGE,
                "seq": TASK_PRIORITY.get(task),
                "status": status,
                "reason_code": reason_code,
                "stage": S_VOICE_RESOURCE_GATE,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )


def _normalize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(metrics or {})
    total = int(raw.get("vram_total_mb", 0) or 0)
    used = int(raw.get("vram_used_mb", 0) or 0)
    queue_depth = int(raw.get("queue_depth", 0) or 0)
    utilization = float(used / total) if total > 0 else 0.0
    return {
        "vram_total_mb": max(total, 0),
        "vram_used_mb": max(used, 0),
        "queue_depth": max(queue_depth, 0),
        "vram_utilization": utilization,
    }
