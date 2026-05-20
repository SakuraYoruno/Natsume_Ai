"""Read-only observability for real wall-clock digital-life soak runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from host_brain.app.obs_routes_core import _now_ms
from tools.review_digital_life_wall_clock_soak import (
    DEFAULT_EXPECTED_HOURS,
    DEFAULT_EXPECTED_ORGAN_COUNT,
    review_wall_clock_soak,
)


ROOT = Path(__file__).resolve().parents[2]
REAL_WALL_CLOCK_MODE = "real_wall_clock_digital_life_soak"

router = APIRouter(prefix="/obs", tags=["observability"])


class DigitalLifeWallClockSoakResponse(BaseModel):
    timestamp: int
    available: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    latestCheckpoints: list[dict[str, Any]] = Field(default_factory=list)
    paths: dict[str, str] = Field(default_factory=dict)
    stderrTail: str = ""
    stdoutTail: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


def _dump_model(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _get_artifacts_root() -> Path:
    raw = os.getenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", "").strip()
    return Path(raw).resolve() if raw else (ROOT / "artifacts" / "codex_runs").resolve()


def _summary_sort_key(summary_path: Path) -> tuple[int, float]:
    summary = _read_json(summary_path)
    stamp = int(summary.get("updated_at_ms") or summary.get("started_at_ms") or 0)
    if stamp > 0:
        return stamp, summary_path.stat().st_mtime if summary_path.exists() else 0.0
    try:
        return 0, summary_path.stat().st_mtime
    except OSError:
        return 0, 0.0


def _read_persona_state(run_dir: Path, folder: str, suffix: str, persona_self_id: str) -> dict[str, Any]:
    state_dir = run_dir / "state" / folder
    if not state_dir.exists():
        return {}

    preferred = state_dir / f"{persona_self_id}_{suffix}.json"
    payload = _read_json(preferred)
    if payload:
        return payload

    for path in sorted(state_dir.glob(f"*_{suffix}.json")):
        payload = _read_json(path)
        if payload:
            return payload
    return {}


def _resolve_run_dir(run_id: str = "") -> tuple[Path | None, str]:
    artifacts_root = _get_artifacts_root()
    requested = (run_id or os.getenv("HOST_OBS_WALL_CLOCK_SOAK_RUN_ID", "")).strip()
    if requested:
        return (artifacts_root / requested / "run").resolve(), "requested-run-id"

    candidates: list[Path] = []
    if artifacts_root.exists():
        for summary_path in artifacts_root.glob("*/run/summary.json"):
            summary = _read_json(summary_path)
            if str(summary.get("evaluation_mode") or "") == REAL_WALL_CLOCK_MODE:
                candidates.append(summary_path)
    if not candidates:
        return None, "latest-wall-clock-run-not-found"
    selected = max(candidates, key=_summary_sort_key)
    return selected.parent.resolve(), "latest-wall-clock-summary"


def _read_pid_hint(run_dir: Path) -> int:
    for path in (run_dir / "pid.txt", run_dir.parent / "pid.txt"):
        try:
            text = path.read_text(encoding="utf-8").strip()
            value = int(text)
        except Exception:
            continue
        if value > 0:
            return value
    return 0


def _acceptance_layer(
    *,
    layer_id: str,
    label: str,
    status: str,
    verdict: str,
    value: str,
    detail: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": layer_id,
        "label": label,
        "status": status,
        "verdict": verdict,
        "value": value,
        "detail": detail,
        "evidence": [item for item in (evidence or []) if str(item or "").strip()],
    }


def _build_soak_acceptance(
    *,
    run_dir: Path,
    review_summary: dict[str, Any],
    source_summary: dict[str, Any],
    expected_organ_count: int,
) -> dict[str, Any]:
    verdict = str(review_summary.get("verdict") or "MISSING").upper()
    run_status = str(review_summary.get("run_status") or source_summary.get("run_status") or "")
    soak_status = str(review_summary.get("soak_status") or source_summary.get("status") or "")
    persona_self_id = str(source_summary.get("persona_self_id") or "natsume_ai")
    completed_pass = verdict == "PASS" and run_status == "COMPLETED" and soak_status == "PASS"
    running = verdict == "RUNNING" or run_status == "RUNNING"

    details = _read_json(run_dir / "details.json")
    final_snapshot = _as_dict(details.get("final_snapshot"))
    background = _as_dict(final_snapshot.get("background_runtime"))
    core_tick_tasks_raw = _as_dict(background.get("core_tick_tasks"))
    core_tick_tasks = [
        {
            "task_id": task_id,
            "enabled": bool(_as_dict(task).get("enabled")),
            "status": str(_as_dict(task).get("status") or ""),
            "expected_interval_s": _as_dict(task).get("expected_interval_s"),
            "seconds_since_last_run": _as_dict(task).get("seconds_since_last_run"),
            "last_reason_code": _as_dict(task).get("last_reason_code"),
        }
        for task_id, task in sorted(core_tick_tasks_raw.items())
    ]
    enabled_core_ticks = [item for item in core_tick_tasks if item["enabled"]]
    healthy_core_ticks = [
        item
        for item in enabled_core_ticks
        if item["status"] in {"OK", "INITIALIZED"} and not item.get("last_reason_code")
    ]

    inner_world = _read_persona_state(run_dir, "inner_world_state", "inner_world", persona_self_id)
    self_model = _read_persona_state(run_dir, "self_model_state", "self_model", persona_self_id)
    inner_stream = _as_list(inner_world.get("inner_stream"))
    distillable_inner_stream = [
        item for item in inner_stream if isinstance(item, dict) and bool(item.get("should_distill_to_memory"))
    ]
    continuity_score = _as_float(inner_world.get("continuity_score"), 0.0)
    loneliness = _as_float(inner_world.get("loneliness"), 0.0)
    anticipation = _as_float(inner_world.get("anticipation"), 0.0)
    spontaneous_event_count = _as_int(inner_world.get("spontaneous_event_count"), 0)
    total_offline_hours = _as_float(inner_world.get("total_offline_hours"), 0.0)
    last_spontaneous_source = str(inner_world.get("last_spontaneous_source") or "")

    agency_score = _as_float(self_model.get("agency_score"), 0.0)
    self_continuity_score = _as_float(self_model.get("self_continuity_score"), 0.0)
    self_identity = str(self_model.get("self_identity") or "")
    self_values = _as_list(self_model.get("self_values"))
    self_boundaries = _as_list(self_model.get("self_boundaries"))
    active_intentions = _as_list(self_model.get("active_intentions"))

    registered_organs = _as_int(review_summary.get("registered_organ_count"), 0)
    mapped_organs = _as_int(review_summary.get("last_mapped_organ_count"), 0)
    organ_pass = registered_organs >= expected_organ_count and mapped_organs >= expected_organ_count
    scheduler_status = str(review_summary.get("scheduler_status") or source_summary.get("scheduler_status") or "")
    tick_health = str(review_summary.get("tick_health") or source_summary.get("tick_health") or "")
    idle_tick_count = _as_int(review_summary.get("idle_tick_count"), 0)
    checkpoint_count = _as_int(review_summary.get("checkpoint_count"), 0)

    life_base_status = "healthy" if completed_pass else "running" if running else "down" if verdict == "FAIL" else "unknown"
    core_tick_evidence = f"{len(healthy_core_ticks)}/{len(enabled_core_ticks) or 5} 核心 tick OK"
    main_chain_status = "healthy" if organ_pass else "running" if running and (registered_organs or mapped_organs) else "degraded"
    inner_world_status = (
        "healthy"
        if completed_pass and (continuity_score > 0 or spontaneous_event_count > 0 or inner_stream)
        else "running"
        if running
        else "unknown"
    )
    self_model_status = (
        "healthy"
        if self_identity and agency_score > 0 and self_continuity_score > 0
        else "running"
        if running
        else "unknown"
    )

    external_services = _as_dict(final_snapshot.get("external_services"))
    external_service_items = [
        {"id": key, "status": str(_as_dict(value).get("status") or "")}
        for key, value in sorted(external_services.items())
    ]
    not_probed_services = [
        item["id"] for item in external_service_items if item["status"] == "not_probed_by_supervisor"
    ]
    proactive_task = _as_dict(background.get("proactive_task"))
    proactive_enabled = bool(proactive_task.get("enabled"))
    proactive_status = str(proactive_task.get("status") or "")
    proactive_task_id = str(proactive_task.get("task_id") or "proactive_talk_periodic")
    proactive_outbox = _as_dict(final_snapshot.get("proactive_outbox"))
    proactive_outbox_count = _as_int(proactive_outbox.get("queued_count"), 0)
    proactive_outbox_mode = str(proactive_outbox.get("delivery_mode") or "")
    proactive_loop_pass = proactive_enabled and proactive_outbox_count > 0
    proactive_loop_status = "healthy" if proactive_loop_pass else "running" if proactive_enabled else "unknown"
    proactive_loop_verdict = (
        "PASS"
        if proactive_loop_pass
        else "NOT_COVERED"
        if not proactive_enabled
        else "RUNNING"
        if running
        else proactive_status or "UNKNOWN"
    )
    proactive_interaction = _as_dict(final_snapshot.get("proactive_interaction"))
    proactive_delivery_receipt_count = _as_int(proactive_interaction.get("delivery_receipt_count"), 0)
    proactive_interaction_count = _as_int(proactive_interaction.get("interaction_count"), 0)
    proactive_last_interaction_status = str(proactive_interaction.get("last_status") or "")
    proactive_last_thalamus_reason = str(proactive_interaction.get("last_thalamus_reason_code") or "")
    proactive_interaction_pass = (
        proactive_delivery_receipt_count > 0
        and proactive_interaction_count > 0
        and proactive_last_interaction_status == "PASS"
        and proactive_last_thalamus_reason == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
    )

    layers = [
        _acceptance_layer(
            layer_id="life_base",
            label="生命底盘",
            status=life_base_status,
            verdict="PASS" if completed_pass else "RUNNING" if running else verdict,
            value="24h PASS" if completed_pass else run_status or "--",
            detail="真实墙钟 24 小时跑满，后台调度和 tick 没有断掉。" if completed_pass else "墙钟 soak 仍在推进或尚未形成最终结论。",
            evidence=[
                f"{review_summary.get('elapsed_seconds', 0)} / {review_summary.get('expected_seconds', 0)} 秒",
                f"idle tick {idle_tick_count}",
                f"checkpoint {checkpoint_count}",
                f"scheduler {scheduler_status}",
                f"tick {tick_health}",
                core_tick_evidence,
            ],
        ),
        _acceptance_layer(
            layer_id="main_chain",
            label="主链器官",
            status=main_chain_status,
            verdict="PASS" if organ_pass else "PARTIAL",
            value=f"{mapped_organs}/{expected_organ_count}",
            detail="23 个器官族完成注册和主链映射。" if organ_pass else "器官族注册或映射尚未满额。",
            evidence=[
                f"注册 {registered_organs}/{expected_organ_count}",
                f"映射 {mapped_organs}/{expected_organ_count}",
                "压缩契约无原始上下文丢失" if completed_pass else "",
            ],
        ),
        _acceptance_layer(
            layer_id="inner_world",
            label="内心世界",
            status=inner_world_status,
            verdict="PASS" if inner_world_status == "healthy" else "UNKNOWN",
            value=f"{spontaneous_event_count} 次",
            detail="内心世界持续变化，并留下可沉淀的内心流。" if inner_world_status == "healthy" else "缺少最终内心世界状态文件。",
            evidence=[
                f"continuity {continuity_score:.4g}",
                f"loneliness {loneliness:.4g}",
                f"anticipation {anticipation:.4g}",
                f"offline {total_offline_hours:.2f}h",
                f"inner_stream {len(inner_stream)}",
                f"可沉淀 {len(distillable_inner_stream)}",
                f"source {last_spontaneous_source}" if last_spontaneous_source else "",
            ],
        ),
        _acceptance_layer(
            layer_id="self_model",
            label="自我模型",
            status=self_model_status,
            verdict="PASS" if self_model_status == "healthy" else "UNKNOWN",
            value=f"{agency_score:.3g} / {self_continuity_score:.3g}" if self_identity else "--",
            detail="身份、价值、边界和主动意图在 24 小时结束后仍然存在。" if self_model_status == "healthy" else "缺少最终自我模型状态文件。",
            evidence=[
                f"identity {'present' if self_identity else 'missing'}",
                f"values {len(self_values)}",
                f"boundaries {len(self_boundaries)}",
                f"intentions {len(active_intentions)}",
            ],
        ),
        _acceptance_layer(
            layer_id="external_system",
            label="整系统外设",
            status="unknown",
            verdict="NOT_COVERED",
            value="未覆盖",
            detail="通讯软件 gateway、ASR、VTS 和外部服务探针不是这次 soak 的覆盖面。",
            evidence=[
                "gateway 未纳入本次 soak",
                "ASR / VTS 未纳入本次 soak",
                f"未探测服务 {', '.join(not_probed_services)}" if not_probed_services else "",
            ],
        ),
        _acceptance_layer(
            layer_id="proactive_loop",
            label="主动外呼",
            status=proactive_loop_status,
            verdict=proactive_loop_verdict,
            value=f"queued {proactive_outbox_count}" if proactive_loop_pass else "未覆盖" if not proactive_enabled else proactive_status or "--",
            detail=(
                "主动外呼已写入安全 outbox，等待通讯软件 gateway 接管真实送达。"
                if proactive_loop_pass
                else "本次没有证明她会主动向外发起消息。"
                if not proactive_enabled
                else "主动外呼任务已启用，需要看到 outbox 排队或真实回执。"
            ),
            evidence=[
                proactive_task_id,
                f"enabled={str(proactive_enabled).lower()}",
                f"status={proactive_status or 'missing'}",
                f"outbox={proactive_outbox_count}",
                proactive_outbox_mode,
            ],
        ),
        _acceptance_layer(
            layer_id="proactive_interaction",
            label="主动交互",
            status="healthy" if proactive_interaction_pass else "unknown",
            verdict="PASS" if proactive_interaction_pass else "NOT_COVERED",
            value=f"{proactive_interaction_count} 次" if proactive_interaction_pass else "未覆盖",
            detail=(
                "主动消息已有 dry-run 送达回执，用户回复被 Thalamus 识别为主动会话续接并回到主链。"
                if proactive_interaction_pass
                else "尚未证明主动消息送达后，用户回复能回到 Thalamus 和主链。"
            ),
            evidence=[
                f"receipts={proactive_delivery_receipt_count}",
                f"interactions={proactive_interaction_count}",
                proactive_last_interaction_status or "missing",
                proactive_last_thalamus_reason or "missing",
            ],
        ),
    ]

    not_covered = [
        {"id": "communication_gateway", "label": "通讯软件 gateway", "reason": "不属于本次 24h soak 覆盖面"},
        {"id": "asr_vts", "label": "ASR / VTS", "reason": "语音输入输出链路未参与本次 soak"},
        {"id": "external_services", "label": "Chroma / Neo4j", "reason": "supervisor 标记为 not_probed_by_supervisor"},
    ]
    if not proactive_loop_pass:
        not_covered.append(
            {"id": "proactive_loop", "label": "主动外呼", "reason": f"{proactive_task_id} 未启用或未产生 outbox 证据"}
        )
    if not proactive_interaction_pass:
        not_covered.append(
            {"id": "proactive_interaction", "label": "主动交互", "reason": "缺少 dry-run 送达回执或 Thalamus 主动续接证据"}
        )

    return {
        "title": "生命底盘 PASS，整系统外设未覆盖" if completed_pass else "真实墙钟生命闭环观测中",
        "headline": (
            "24 小时 soak 已证明生命底盘、23 器官主链、内心世界和自我模型持续存在；"
            "通讯软件 gateway、ASR、VTS 仍需单独验收；主动外呼与主动交互按本页层级证据判定。"
            if completed_pass
            else "当前只读读取真实墙钟 soak 记录，最终结论会随 run_status 更新。"
        ),
        "overall_status": "degraded" if completed_pass else life_base_status,
        "base_status": life_base_status,
        "layers": layers,
        "not_covered": not_covered,
        "core_tick_tasks": core_tick_tasks,
        "external_services": external_service_items,
        "proactive_task": {
            "task_id": proactive_task_id,
            "enabled": proactive_enabled,
            "status": proactive_status,
            "outbox_queued_count": proactive_outbox_count,
            "outbox_delivery_mode": proactive_outbox_mode,
            "delivery_receipt_count": proactive_delivery_receipt_count,
            "interaction_count": proactive_interaction_count,
            "last_interaction_status": proactive_last_interaction_status,
            "last_thalamus_reason_code": proactive_last_thalamus_reason,
        },
        "inner_world": {
            "continuity_score": continuity_score,
            "loneliness": loneliness,
            "anticipation": anticipation,
            "total_offline_hours": total_offline_hours,
            "spontaneous_event_count": spontaneous_event_count,
            "inner_stream_size": len(inner_stream),
            "distillable_inner_stream_count": len(distillable_inner_stream),
            "last_spontaneous_source": last_spontaneous_source,
        },
        "self_model": {
            "identity_present": bool(self_identity),
            "agency_score": agency_score,
            "self_continuity_score": self_continuity_score,
            "value_count": len(self_values),
            "boundary_count": len(self_boundaries),
            "active_intention_count": len(active_intentions),
        },
    }


def _build_wall_clock_soak_payload(
    *,
    run_id: str = "",
    pid: int = 0,
    require_complete: bool = False,
) -> dict[str, Any]:
    artifacts_root = _get_artifacts_root()
    run_dir, selection_mode = _resolve_run_dir(run_id)
    timestamp = _now_ms()
    if run_dir is None:
        return _dump_model(DigitalLifeWallClockSoakResponse(
            timestamp=timestamp,
            available=False,
            summary={
                "verdict": "MISSING",
                "run_status": "",
                "failed_checks": ["wall_clock_soak_run_found"],
                "checks": {"wall_clock_soak_run_found": False},
            },
            acceptance={
                "title": "未发现真实墙钟生命闭环记录",
                "headline": "artifacts/codex_runs 中还没有可读取的 real wall-clock soak。",
                "overall_status": "unknown",
                "base_status": "unknown",
                "layers": [],
                "not_covered": [],
            },
            meta={
                "artifactsRoot": str(artifacts_root),
                "selectionMode": selection_mode,
                "readOnly": True,
            },
        ))

    effective_pid = int(pid or 0) or _env_int("HOST_OBS_WALL_CLOCK_SOAK_PID", 0) or _read_pid_hint(run_dir)
    stale_seconds_env = os.getenv("HOST_OBS_WALL_CLOCK_SOAK_STALE_SECONDS", "").strip()
    try:
        stale_seconds = max(1.0, float(stale_seconds_env)) if stale_seconds_env else None
    except ValueError:
        stale_seconds = None
    expected_hours = _env_float(
        "HOST_OBS_WALL_CLOCK_SOAK_EXPECTED_HOURS",
        DEFAULT_EXPECTED_HOURS,
        minimum=0.0,
    )
    expected_organ_count = _env_int(
        "HOST_OBS_WALL_CLOCK_SOAK_EXPECTED_ORGAN_COUNT",
        DEFAULT_EXPECTED_ORGAN_COUNT,
        minimum=1,
    )
    args = argparse.Namespace(
        run_dir=str(run_dir),
        out_dir="",
        pid=effective_pid,
        stdout="",
        stderr="",
        expected_hours=expected_hours,
        expected_seconds=None,
        expected_organ_count=expected_organ_count,
        stale_seconds=stale_seconds,
        log_tail_chars=4_000,
        require_complete=bool(require_complete),
        write_outputs=False,
    )
    result = review_wall_clock_soak(args)
    summary = _as_dict(result.get("summary"))
    details = _as_dict(result.get("details"))
    source_summary = _as_dict(details.get("source_summary"))
    checks = _as_dict(summary.get("checks"))
    paths = _as_dict(details.get("paths"))
    latest_checkpoints = [_as_dict(item) for item in _as_list(details.get("latest_checkpoints"))]
    available = bool(checks.get("summary_file_present")) and bool(summary.get("run_id"))
    acceptance = _build_soak_acceptance(
        run_dir=run_dir,
        review_summary=summary,
        source_summary=source_summary,
        expected_organ_count=expected_organ_count,
    ) if available else {}

    return _dump_model(DigitalLifeWallClockSoakResponse(
        timestamp=timestamp,
        available=available,
        summary=summary,
        acceptance=acceptance,
        latestCheckpoints=latest_checkpoints,
        paths={key: str(value) for key, value in paths.items()},
        stderrTail=str(details.get("stderr_tail") or ""),
        stdoutTail=str(details.get("stdout_tail") or ""),
        meta={
            "artifactsRoot": str(artifacts_root),
            "selectionMode": selection_mode,
            "requestedRunId": run_id or None,
            "readOnly": True,
            "processTracked": effective_pid > 0,
            "expectedOrganCount": expected_organ_count,
        },
    ))


@router.get("/digital-life/wall-clock-soak", response_model=DigitalLifeWallClockSoakResponse)
def get_digital_life_wall_clock_soak(
    run_id: str = Query("", description="Optional artifacts/codex_runs/<run_id>/run selector."),
    pid: int = Query(0, ge=0, description="Optional process id hint for liveness checks."),
    require_complete: bool = Query(False, description="Require final PASS instead of accepting an active RUNNING soak."),
) -> DigitalLifeWallClockSoakResponse:
    return DigitalLifeWallClockSoakResponse(
        **_build_wall_clock_soak_payload(
            run_id=run_id,
            pid=pid,
            require_complete=require_complete,
        )
    )
