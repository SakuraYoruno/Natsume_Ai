"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from collections.abc import Mapping

from ._shared import *  # noqa: F403
from ._shared import (
    _classify_tick_health,
    _coerce_float,
    _coerce_int,
    _extract_interval_seconds,
    _seconds_since_timestamp,
)

def _build_background_runtime_snapshot(
    *,
    now_ms: int,
    persona_user_id: str,
    inner_world_state: dict[str, Any],
    scheduler_snapshot_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

        persona_user_id = resolve_persona_self_id(persona_user_id)
    except Exception:
        persona_user_id = str(persona_user_id or "").strip() or "natsume_ai"

    scheduler_snapshot: dict[str, Any] = {}
    scheduler_error = ""
    if scheduler_snapshot_override is not None:
        scheduler_snapshot = dict(scheduler_snapshot_override or {})
    else:
        scheduler_error = "SCHEDULER_SERVICE_SNAPSHOT_REQUIRED"
        scheduler_snapshot = {
            "scheduler_status": "UNAVAILABLE",
            "total_tasks": 0,
            "enabled_tasks": 0,
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [],
            "ts_ms": now_ms,
        }

    scheduler_status = str(scheduler_snapshot.get("scheduler_status") or "UNAVAILABLE").strip().upper()
    scheduler_active = scheduler_status in {"HEALTHY", "DEGRADED", "RUNNING", "UP", "OK"}
    # AW-04 retired the split Life timers.  One governed ``life_tick`` owns
    # numeric/emotion/proactive policy evaluation and is therefore also the
    # only scheduler health identity exposed by this read model.
    core_tick_ids = ["life_tick"]
    proactive_task_id = "life_tick"
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for item in list(scheduler_snapshot.get("tasks") or []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            tasks_by_id[task_id] = dict(item)

    core_ticks_missing = [task_id for task_id in core_tick_ids if task_id not in tasks_by_id]
    core_ticks_registered = len(core_ticks_missing) == 0

    core_tick_tasks: dict[str, Any] = {}
    for task_id in core_tick_ids:
        task_health = tasks_by_id.get(task_id, {})
        status_value = str(task_health.get("status") or "MISSING").strip().upper() or "MISSING"
        enabled = bool(task_health.get("enabled", False)) if task_health else False
        expected_interval_s = _extract_interval_seconds(task_health) or 60
        last_run_ts = _coerce_int(task_health.get("last_run_ts"), 0)
        if last_run_ts <= 0:
            last_run_ts = None
        core_tick_tasks[task_id] = {
            "status": status_value,
            "enabled": enabled,
            "last_run_ts": last_run_ts,
            "expected_interval_s": expected_interval_s,
            "seconds_since_last_run": _seconds_since_timestamp(ts_ms=last_run_ts, now_ms=now_ms),
            "last_reason_code": str(task_health.get("last_reason_code") or "").strip() or None,
            "last_gate_state": str(task_health.get("last_gate_state") or "").strip().lower() or None,
            "last_mood_state": str(task_health.get("last_mood_state") or "").strip().lower() or None,
            "last_endocrine_cortisol": _coerce_float(task_health.get("last_endocrine_cortisol"), 0.0),
            "last_endocrine_energy": _coerce_float(task_health.get("last_endocrine_energy"), 0.0),
        }

    proactive_task_health = tasks_by_id.get(proactive_task_id, {})
    proactive_last_run_ts = _coerce_int(proactive_task_health.get("last_run_ts"), 0)
    if proactive_last_run_ts <= 0:
        proactive_last_run_ts = None
    proactive_task_snapshot = {
        "task_id": proactive_task_id,
        "status": str(proactive_task_health.get("status") or "MISSING").strip().upper() or "MISSING",
        "enabled": bool(proactive_task_health.get("enabled", False)) if proactive_task_health else False,
        "last_run_ts": proactive_last_run_ts,
        "seconds_since_last_run": _seconds_since_timestamp(ts_ms=proactive_last_run_ts, now_ms=now_ms),
        "last_reason_code": str(proactive_task_health.get("last_reason_code") or "").strip() or None,
        "last_gate_state": str(proactive_task_health.get("last_gate_state") or "").strip().lower() or None,
        "last_mood_state": str(proactive_task_health.get("last_mood_state") or "").strip().lower() or None,
        "last_endocrine_cortisol": _coerce_float(proactive_task_health.get("last_endocrine_cortisol"), 0.0),
        "last_endocrine_energy": _coerce_float(proactive_task_health.get("last_endocrine_energy"), 0.0),
    }

    gate_block_reason_codes = {
        "EMOTION_GATE_BLOCKED",
        "ENDOCRINE_GATE_BLOCKED",
        "ENDOCRINE_PROACTIVE_BLOCKED",
    }
    gate_blocked_tasks: list[str] = []
    for task_id, task_health in tasks_by_id.items():
        reason_code = str(task_health.get("last_reason_code") or "").strip().upper()
        if reason_code in gate_block_reason_codes:
            gate_blocked_tasks.append(task_id)

    last_inner_world_tick_ts_ms = _coerce_int(inner_world_state.get("last_tick_ts_ms"), 0)
    if last_inner_world_tick_ts_ms <= 0:
        last_inner_world_tick_ts_ms = None
    numeric_tick_last_run_ts = _coerce_int(
        core_tick_tasks.get("life_tick", {}).get("last_run_ts"),
        0,
    )
    if numeric_tick_last_run_ts > 0 and (
        last_inner_world_tick_ts_ms is None
        or numeric_tick_last_run_ts > last_inner_world_tick_ts_ms
    ):
        last_inner_world_tick_ts_ms = numeric_tick_last_run_ts
    seconds_since_inner_world_tick = _seconds_since_timestamp(
        ts_ms=last_inner_world_tick_ts_ms,
        now_ms=now_ms,
    )

    inner_tick_expected_interval_s = int(
        core_tick_tasks.get("inner_world_numeric_tick", {}).get("expected_interval_s") or 60
    )
    tick_health = _classify_tick_health(
        scheduler_status=scheduler_status,
        core_ticks_registered=core_ticks_registered,
        seconds_since_last_tick=seconds_since_inner_world_tick,
        expected_interval_s=inner_tick_expected_interval_s,
    )

    inner_world_user_id = str(inner_world_state.get("user_id") or "").strip()
    persona_user_alignment_ok = (not inner_world_user_id) or inner_world_user_id == persona_user_id
    is_advancing = bool(
        scheduler_active
        and core_ticks_registered
        and tick_health in {"fresh", "delayed"}
        and core_tick_tasks.get("inner_world_numeric_tick", {}).get("enabled", False)
        and persona_user_alignment_ok
    )

    return {
        "scheduler_status": scheduler_status,
        "scheduler_active": scheduler_active,
        "scheduler_total_tasks": _coerce_int(scheduler_snapshot.get("total_tasks"), 0),
        "scheduler_enabled_tasks": _coerce_int(scheduler_snapshot.get("enabled_tasks"), 0),
        "core_tick_ids": core_tick_ids,
        "core_ticks_registered": core_ticks_registered,
        "core_ticks_missing": core_ticks_missing,
        "core_tick_tasks": core_tick_tasks,
        "proactive_task": proactive_task_snapshot,
        "gate_blocked_tasks": gate_blocked_tasks,
        "last_inner_world_tick_ts_ms": last_inner_world_tick_ts_ms,
        "seconds_since_inner_world_tick": seconds_since_inner_world_tick,
        "tick_health": tick_health,
        "persona_user_alignment_ok": persona_user_alignment_ok,
        "inner_world_user_id": inner_world_user_id or None,
        "is_advancing": is_advancing,
        "scheduler_error": scheduler_error or None,
    }
