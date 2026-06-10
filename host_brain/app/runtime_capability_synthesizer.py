"""Runtime capability synthesizer (temporary capability path)."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.app.capability_gap_detector import CapabilityGapDecision


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class RuntimeCapabilitySpec:
    runtime_capability_id: str
    route_type: str
    target_id: str
    task: str
    skill_family: str
    reason_code: str
    ttl_seconds: int
    created_ts_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_capability_id": self.runtime_capability_id,
            "route_type": self.route_type,
            "target_id": self.target_id,
            "task": self.task,
            "skill_family": self.skill_family,
            "reason_code": self.reason_code,
            "ttl_seconds": self.ttl_seconds,
            "created_ts_ms": self.created_ts_ms,
            "metadata": dict(self.metadata or {}),
        }

    @property
    def expire_ts_ms(self) -> int:
        return int(self.created_ts_ms + self.ttl_seconds * 1000)


class RuntimeCapabilityRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, RuntimeCapabilitySpec] = {}

    def register(self, spec: RuntimeCapabilitySpec) -> RuntimeCapabilitySpec:
        with self._lock:
            self._store[str(spec.runtime_capability_id)] = spec
        return spec

    def get(self, runtime_capability_id: str) -> RuntimeCapabilitySpec | None:
        key = str(runtime_capability_id or "").strip()
        if not key:
            return None
        with self._lock:
            spec = self._store.get(key)
        if spec is None:
            return None
        if spec.expire_ts_ms < _now_ms():
            self.remove(key)
            return None
        return spec

    def remove(self, runtime_capability_id: str) -> None:
        key = str(runtime_capability_id or "").strip()
        if not key:
            return
        with self._lock:
            self._store.pop(key, None)

    def list_active(self) -> list[RuntimeCapabilitySpec]:
        now = _now_ms()
        with self._lock:
            expired = [key for key, spec in self._store.items() if spec.expire_ts_ms < now]
            for key in expired:
                self._store.pop(key, None)
            return list(self._store.values())


def _choose_executor(missing_capability_tags: tuple[str, ...], suggested_family: str | None) -> tuple[str, str]:
    tags = set(str(tag or "").strip().lower() for tag in missing_capability_tags if str(tag or "").strip())
    family = str(suggested_family or "").strip().lower()

    if "coding" in tags or family == "code":
        return "skill", "agent_worker"
    if "translation" in tags or family == "translation":
        return "delegate", "translate_agent"
    if "vision" in tags or family in {"vision", "perception"}:
        return "delegate", "vision_agent"
    if "system" in tags or family == "system":
        return "delegate", "repair_agent"
    return "delegate", "intent_agent"


def _build_runtime_capability_id(
    *,
    request_id: str,
    session_id: str,
    reason_code: str,
    missing_capability_tags: tuple[str, ...],
) -> str:
    payload = "|".join(
        [
            str(request_id or "").strip().lower(),
            str(session_id or "").strip().lower(),
            str(reason_code or "").strip().lower(),
            ",".join(sorted(str(tag or "").strip().lower() for tag in missing_capability_tags)),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:14]
    return f"runtime_cap_{digest}"


def synthesize_runtime_capability(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    gap_decision: CapabilityGapDecision,
    registry: RuntimeCapabilityRegistry | None = None,
    ttl_seconds: int = 1800,
) -> RuntimeCapabilitySpec:
    route_type, target_id = _choose_executor(gap_decision.missing_capability_tags, gap_decision.suggested_family)
    runtime_capability_id = _build_runtime_capability_id(
        request_id=request_id,
        session_id=session_id,
        reason_code=gap_decision.reason_code,
        missing_capability_tags=gap_decision.missing_capability_tags,
    )
    family = str(gap_decision.suggested_family or "reasoning").strip().lower() or "reasoning"
    task = (
        f"[RuntimeCapability:{runtime_capability_id}] 当前请求触发能力缺口({gap_decision.reason_code})。"
        f"请以 {family} 能力优先执行任务：{str(user_message or '').strip()}。"
        "如果无法直接执行，请先拆解为可执行步骤后完成，并返回可验证结果。"
    )
    spec = RuntimeCapabilitySpec(
        runtime_capability_id=runtime_capability_id,
        route_type=route_type,
        target_id=target_id,
        task=task,
        skill_family=family,
        reason_code=gap_decision.reason_code,
        ttl_seconds=max(60, int(ttl_seconds)),
        created_ts_ms=_now_ms(),
        metadata={
            "missing_capability_tags": list(gap_decision.missing_capability_tags),
            "gap_confidence": float(gap_decision.confidence),
            "gap_details": dict(gap_decision.details or {}),
        },
    )
    if registry is not None:
        registry.register(spec)
    return spec


__all__ = ["RuntimeCapabilitySpec", "RuntimeCapabilityRegistry", "synthesize_runtime_capability"]

