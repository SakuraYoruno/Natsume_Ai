"""Whole-system resource manager.

The manager is intentionally conservative: it monitors the entire Windows
machine, asks a small LLM pass to classify what is safe to release, and only
performs non-destructive automatic actions by default.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from host_brain.core.ids import now_ms

logger = logging.getLogger("Natsume.resource_manager")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        value = float(default)
    return max(float(min_value), min(float(max_value), value))


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _compact_process(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pid": row.get("pid"),
        "name": row.get("name"),
        "cpu_percent": row.get("cpu_percent"),
        "memory_mb": row.get("memory_mb"),
        "private_mb": row.get("private_mb"),
        "vram_mb": row.get("vram_mb"),
        "role": row.get("role"),
        "protected": bool(row.get("protected")),
    }


def _compact_llm_attempt(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        provider = row.get("provider")
        model = row.get("model")
        status = row.get("status")
        reason_code = row.get("reason_code")
        detail = row.get("detail")
        latency_ms = row.get("latency_ms")
    else:
        provider = getattr(row, "provider", None)
        model = getattr(row, "model", None)
        status = getattr(row, "status", None)
        reason_code = getattr(row, "reason_code", None)
        detail = getattr(row, "detail", None)
        latency_ms = getattr(row, "latency_ms", None)
    return {
        "provider": str(provider or "").strip(),
        "model": str(model or "").strip(),
        "status": str(status or "").strip(),
        "reason_code": str(reason_code or "").strip() or None,
        "detail": str(detail or "").strip()[:220] or None,
        "latency_ms": latency_ms,
    }


def _summarize_llm_attempts(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "no usable LLM candidates"
    parts: list[str] = []
    for item in attempts[:4]:
        provider = str(item.get("provider") or "?").strip()
        model = str(item.get("model") or "?").strip()
        status = str(item.get("status") or "?").strip()
        reason = str(item.get("reason_code") or "").strip()
        detail = str(item.get("detail") or "").strip()
        reason_part = reason or detail
        if reason_part:
            parts.append(f"{provider}/{model} {status}({reason_part[:80]})")
        else:
            parts.append(f"{provider}/{model} {status}")
    return "; ".join(parts)


def _provider_model_env_name(provider: str) -> str:
    provider_token = str(provider or "").strip().upper()
    if not provider_token:
        return ""
    return f"HOST_{provider_token}_MODEL_ID"


def _provider_base_url_env_name(provider: str) -> str:
    provider_token = str(provider or "").strip().upper()
    if not provider_token:
        return ""
    return f"HOST_{provider_token}_BASE_URL"


_RESOURCE_GATE_CRITICAL_TASK_IDS = {
    "agent_health_check",
    "resource_manager",
    "system_resource_manager",
    "security_immune_periodic",
}
_RESOURCE_GATE_HEAVY_TASK_IDS = {
    "startup_retrieval_warmup",
    "retrieval_runtime_warmup",
    "long_term_memory_distillation",
    "distillation_engine_periodic",
    "agent_immune_auto_dispatch_tick",
    "proactive_talk_periodic",
    "evolution_cycle_daily",
    "diary_writer_daily",
    "dream_generator_sleep",
    "inner_world_monologue_tick",
    "gaming_companion_tick",
    "emergent_growth_weekly",
    "lightnovel_recommender_daily",
    "service_health_heartbeat_llm_probe",
}
_RESOURCE_GATE_LIGHT_TASK_IDS = {
    "inner_world_numeric_tick",
    "silence_tick",
    "body_sense_tick",
    "emotion_decay_tick",
    "service_health_heartbeat",
}
_PROTECTED_PROCESS_ROLES = {
    "system_protected",
    "natsume_runtime",
    "edge_runtime",
    "container_runtime",
    "model_training",
}
_AUTO_TRIM_ALLOWED_ROLES = {
    "browser",
    "communication_app",
    "user_or_system_process",
}


def _bounded_int_env(name: str, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(str(os.environ.get(name) or "").strip()))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(min_value), min(int(max_value), parsed))


def _coerce_percent(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _pressure_rank(status: Any) -> int:
    token = str(status or "").strip().upper()
    return {
        "NORMAL": 0,
        "UNKNOWN": 0,
        "ELEVATED": 1,
        "HIGH": 2,
    }.get(token, 0)


def _is_memory_pressure(pressure: Mapping[str, Any]) -> bool:
    status = str(pressure.get("status") or "").strip().upper()
    reasons = {str(item).strip().lower() for item in _as_list(pressure.get("reasons"))}
    memory_percent = _coerce_percent(pressure.get("memory_percent"))
    if "memory_high" in reasons or "memory_elevated" in reasons:
        return True
    if status in {"HIGH", "ELEVATED"} and memory_percent is not None and memory_percent >= 78.0:
        return True
    return False


def _has_any_reason(reasons: set[str], prefixes: tuple[str, ...]) -> bool:
    return any(any(reason.startswith(prefix) for prefix in prefixes) for reason in reasons)


def _resolve_degradation_capability(
    *,
    task_id: str,
    task_type: str,
    uses_llm: bool,
    explicit_capability_id: str = "",
    explicit_risk_level: int | None = None,
) -> tuple[str, int]:
    explicit = str(explicit_capability_id or "").strip().lower()
    if explicit:
        try:
            risk = max(1, min(5, int(explicit_risk_level or 5)))
        except (TypeError, ValueError):
            risk = 5
        return explicit, risk
    task_key = str(task_id or "").strip().lower()
    task_type_key = str(task_type or "").strip().lower()
    if task_type_key in {"memory_heavy", "retrieval_warmup", "embedding_warmup"} or any(
        token in task_key for token in ("memory_distill", "retrieval", "canon_apply")
    ):
        return "memory_write", 4
    if any(token in task_key for token in ("planner", "evolution", "capability_upgrade")):
        return "autonomous_planning", 4
    if bool(uses_llm) or task_type_key in {
        "background_llm",
        "heartbeat_llm",
        "social_light_llm",
        "proactive_llm",
    }:
        return "model_inference", 2
    return "", 1


def _apply_degradation_state_gate(
    decision: dict[str, Any],
    *,
    degradation_state: Mapping[str, Any] | None,
    capability_id: str,
    static_risk_level: int,
) -> dict[str, Any]:
    state = _as_dict(degradation_state)
    raw_restrictions = state.get("capability_restrictions")
    restrictions = [
        _as_dict(row)
        for row in (
            list(raw_restrictions)
            if isinstance(raw_restrictions, (list, tuple))
            else []
        )
    ]
    restriction = next(
        (
            row
            for row in restrictions
            if str(row.get("capability_id") or "").strip().lower() == capability_id
        ),
        None,
    )
    if not capability_id or restriction is None:
        return decision
    high_risk = bool(restriction.get("high_risk")) or int(static_risk_level) >= 3
    out = dict(decision)
    out["degradation_state_revision"] = state.get("revision")
    out["degradation_status"] = state.get("status")
    out["degradation_capability_id"] = capability_id
    out["degradation_restriction"] = {
        "status": restriction.get("status"),
        "origin_dependencies": list(restriction.get("origin_dependencies") or []),
        "safe_fallbacks": list(restriction.get("safe_fallbacks") or []),
        "automatic_release": False,
        "permission_effect": "RESTRICT_ONLY",
    }
    if high_risk:
        out.update(
            {
                "allowed": False,
                "decision": "defer",
                "reason_code": "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED",
                "detail": "affected high-risk capability is disabled by DegradationState",
                "retry_after_s": max(int(out.get("retry_after_s") or 0), 300),
            }
        )
    elif out.get("decision") == "allow":
        out["decision"] = "degrade"
        out["reason_code"] = "DEPENDENCY_DEGRADED_SAFE_FALLBACK"
        out["detail"] = "dependency degraded; caller must use an enumerated safe fallback"
    return out


_RUNTIME_CPU_ONLY_REASONS = {
    "cpu_elevated",
    "cpu_high",
    "natsume_runtime_cpu_elevated",
    "natsume_runtime_cpu_high",
}


def _is_runtime_cpu_only_pressure(pressure: Mapping[str, Any]) -> bool:
    pressure_map = _as_dict(pressure)
    reasons = {str(item).strip().lower() for item in _as_list(pressure_map.get("reasons")) if str(item).strip()}
    if not reasons or not reasons.issubset(_RUNTIME_CPU_ONLY_REASONS):
        return False
    reason_tokens = set(reasons)
    return not (
        _is_memory_pressure(pressure_map)
        or _has_any_reason(reason_tokens, ("memory_", "vram_", "gpu_", "disk_"))
    )


def _should_promote_llm_pressure(
    *,
    pressure: Mapping[str, Any],
    decision_pressure: str,
) -> bool:
    promoted = str(decision_pressure or "").strip().upper()
    current = str(_as_dict(pressure).get("status") or "UNKNOWN").strip().upper()
    if promoted not in {"HIGH", "ELEVATED"}:
        return False
    if _pressure_rank(promoted) <= _pressure_rank(current):
        return False
    if (
        _is_runtime_cpu_only_pressure(pressure)
        and not _parse_bool_env(
            "HOST_RESOURCE_MANAGER_PROMOTE_LLM_PRESSURE_ON_RUNTIME_CPU_ONLY",
            default=False,
        )
    ):
        return False
    return True


def assess_background_task_pressure(
    *,
    task_id: str,
    pressure: Mapping[str, Any] | None,
    priority: str = "LOW",
    task_type: str = "",
    uses_llm: bool = False,
    uses_gpu: bool = False,
    reason: str = "background",
    degradation_state: Mapping[str, Any] | None = None,
    capability_id: str = "",
    static_risk_level: int | None = None,
) -> dict[str, Any]:
    """Return resource-gate decision for a background task.

    The policy is conservative and non-destructive: it never kills work. It
    only allows, defers, or asks callers to degrade optional probes.
    """

    task_key = str(task_id or "").strip().lower()
    priority_token = str(priority or "LOW").strip().upper() or "LOW"
    task_type_token = str(task_type or "").strip().lower()
    pressure_map = _as_dict(pressure)
    status = str(pressure_map.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    memory_percent = _coerce_percent(pressure_map.get("memory_percent"))
    vram_percent = _coerce_percent(pressure_map.get("vram_percent"))
    reasons = [str(item) for item in _as_list(pressure_map.get("reasons")) if str(item).strip()]
    reason_tokens = {str(item).strip().lower() for item in reasons if str(item).strip()}

    if not _parse_bool_env("HOST_RESOURCE_MANAGER_GATE_ENABLED", default=True):
        base_decision = {
            "allowed": True,
            "decision": "allow",
            "reason_code": None,
            "task_id": task_id,
            "pressure_status": status,
            "detail": "resource gate disabled",
        }
        resolved_capability, resolved_risk = _resolve_degradation_capability(
            task_id=task_id,
            task_type=task_type,
            uses_llm=uses_llm,
            explicit_capability_id=capability_id,
            explicit_risk_level=static_risk_level,
        )
        return _apply_degradation_state_gate(
            base_decision,
            degradation_state=degradation_state,
            capability_id=resolved_capability,
            static_risk_level=resolved_risk,
        )

    critical = task_key in _RESOURCE_GATE_CRITICAL_TASK_IDS or priority_token in {"P0", "CRITICAL"}
    memory_heavy = task_type_token == "memory_heavy" or task_key in {"long_term_memory_distillation", "distillation_engine_periodic"}
    browser_heavy = task_type_token == "browser_heavy"
    gpu_heavy = task_type_token == "gpu_heavy" or bool(uses_gpu)
    background_llm = task_type_token in {"background_llm", "heartbeat_llm", "social_light_llm", "proactive_llm"} or bool(uses_llm)
    social_light_llm = task_type_token in {"social_light_llm", "proactive_llm"} or task_key in {
        "proactive_talk",
        "proactive_talk_periodic",
    }
    heavy = (
        task_key in _RESOURCE_GATE_HEAVY_TASK_IDS
        or task_type_token
        in {
            "background_llm",
            "social_light_llm",
            "proactive_llm",
            "retrieval_warmup",
            "embedding_warmup",
            "memory_heavy",
            "gpu_heavy",
            "browser_heavy",
            "heartbeat_llm",
        }
        or background_llm
        or gpu_heavy
    )
    light = task_key in _RESOURCE_GATE_LIGHT_TASK_IDS or task_type_token in {"heartbeat", "light_tick"}

    high_mem = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_HIGH_MEMORY_PERCENT", default=88, min_value=50, max_value=99)
    elevated_mem = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_ELEVATED_MEMORY_PERCENT", default=78, min_value=40, max_value=98)
    high_vram = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_HIGH_VRAM_PERCENT", default=90, min_value=50, max_value=99)
    elevated_vram = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_ELEVATED_VRAM_PERCENT", default=80, min_value=40, max_value=98)

    derived_high = status == "HIGH" or (memory_percent is not None and memory_percent >= high_mem) or (vram_percent is not None and vram_percent >= high_vram)
    derived_elevated = (
        derived_high
        or status == "ELEVATED"
        or (memory_percent is not None and memory_percent >= elevated_mem)
        or (vram_percent is not None and vram_percent >= elevated_vram)
    )
    memory_pressure = (
        (memory_percent is not None and memory_percent >= elevated_mem)
        or _has_any_reason(reason_tokens, ("memory_",))
    )
    vram_pressure = (
        (vram_percent is not None and vram_percent >= elevated_vram)
        or _has_any_reason(reason_tokens, ("vram_",))
    )
    gpu_pressure = _has_any_reason(reason_tokens, ("gpu_",))
    synthetic_cpu_or_llm_high = (
        derived_high
        and bool(reason_tokens)
        and reason_tokens.issubset(
            {
                "cpu_elevated",
                "cpu_high",
                "llm_pressure_elevated",
                "llm_pressure_high",
                "natsume_runtime_cpu_elevated",
                "natsume_runtime_cpu_high",
            }
        )
        and not memory_pressure
        and not vram_pressure
        and not gpu_pressure
    )
    runtime_cpu_only_elevated = (
        derived_elevated
        and not derived_high
        and bool(reason_tokens)
        and reason_tokens.issubset({"natsume_runtime_cpu_elevated", "cpu_elevated"})
        and not memory_pressure
        and not vram_pressure
        and not gpu_pressure
    )

    retry_after_s = 0
    reason_code: str | None = None
    decision = "allow"
    detail = "resource pressure allows background task"

    allow_light_under_high = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_LIGHT_TICKS_UNDER_HIGH",
        default=False,
    )
    allow_remote_llm_under_runtime_cpu = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_REMOTE_LLM_UNDER_RUNTIME_CPU_ELEVATED",
        default=True,
    )
    allow_memory_heavy_under_runtime_cpu = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_MEMORY_HEAVY_UNDER_RUNTIME_CPU_ELEVATED",
        default=True,
    )
    allow_browser_heavy_under_runtime_cpu = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_BROWSER_HEAVY_UNDER_RUNTIME_CPU_ELEVATED",
        default=True,
    )
    allow_social_llm_under_vram = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_SOCIAL_LLM_UNDER_VRAM_ELEVATED",
        default=True,
    )
    allow_life_ticks_under_vram_high = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_LIFE_TICKS_UNDER_VRAM_HIGH",
        default=True,
    )
    allow_social_llm_under_vram_high = _parse_bool_env(
        "HOST_RESOURCE_MANAGER_GATE_ALLOW_SOCIAL_LLM_UNDER_VRAM_HIGH",
        default=True,
    )
    gpu_percent = _coerce_percent(pressure_map.get("gpu_percent"))
    vram_only_high = (
        derived_high
        and vram_pressure
        and not memory_pressure
        and not gpu_pressure
        and (gpu_percent is None or gpu_percent < 65.0)
        and not _has_any_reason(reason_tokens, ("memory_high", "cpu_high", "gpu_high"))
    )
    social_llm_vram_only_elevated = (
        derived_elevated
        and not derived_high
        and social_light_llm
        and background_llm
        and allow_social_llm_under_vram
        and not browser_heavy
        and not gpu_heavy
        and not memory_heavy
        and not memory_pressure
        and vram_pressure
        and not gpu_pressure
        and (gpu_percent is None or gpu_percent < 65.0)
        and not _has_any_reason(reason_tokens, ("memory_high", "cpu_high", "gpu_high", "vram_high"))
    )

    if critical:
        decision = "allow"
        detail = "critical resource-management task is allowed"
    elif derived_high:
        if light and vram_only_high and allow_life_ticks_under_vram_high:
            decision = "allow"
            detail = "light life tick allowed under VRAM-only high pressure"
        elif social_light_llm and vram_only_high and allow_social_llm_under_vram_high and not browser_heavy and not gpu_heavy and not memory_heavy:
            decision = "allow"
            detail = "social proactive LLM task allowed under VRAM-only high pressure"
        elif light and (allow_light_under_high or synthetic_cpu_or_llm_high):
            decision = "allow"
            detail = (
                "light life tick allowed under CPU/LLM-only high pressure"
                if synthetic_cpu_or_llm_high
                else "light life tick allowed under high resource pressure"
            )
        else:
            default_retry = 120 if light else (900 if memory_heavy else (600 if browser_heavy or gpu_heavy else 300))
            retry_after_s = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_HIGH_RETRY_AFTER_S", default=default_retry, min_value=30, max_value=3600)
            reason_code = "RESOURCE_PRESSURE_HIGH"
            decision = "defer"
            detail = "high resource pressure; defer non-critical background task"
    elif derived_elevated:
        must_defer_under_cpu_only = (
            (browser_heavy and not allow_browser_heavy_under_runtime_cpu)
            or gpu_heavy
            or (memory_heavy and not allow_memory_heavy_under_runtime_cpu)
            or task_type_token in {"heartbeat_llm", "retrieval_warmup", "embedding_warmup"}
        )
        if (
            runtime_cpu_only_elevated
            and (background_llm or memory_heavy)
            and not must_defer_under_cpu_only
            and allow_remote_llm_under_runtime_cpu
        ):
            decision = "allow"
            detail = "background task allowed under CPU-only runtime pressure"
        elif social_llm_vram_only_elevated:
            decision = "allow"
            detail = "social proactive LLM task allowed under non-GPU elevated VRAM pressure"
        elif heavy and priority_token not in {"HIGH", "P0"}:
            default_retry = 600 if memory_heavy else (420 if browser_heavy or gpu_heavy else 180)
            retry_after_s = _bounded_int_env("HOST_RESOURCE_MANAGER_GATE_ELEVATED_RETRY_AFTER_S", default=default_retry, min_value=30, max_value=1800)
            reason_code = "RESOURCE_PRESSURE_ELEVATED"
            decision = "defer"
            detail = "elevated resource pressure; defer heavy background task"

    allowed = decision == "allow"
    output = {
        "allowed": allowed,
        "decision": decision,
        "reason_code": reason_code,
        "task_id": task_id,
        "task_type": task_type,
        "priority": priority_token,
        "uses_llm": bool(uses_llm),
        "uses_gpu": bool(uses_gpu),
        "critical": critical,
        "heavy": heavy,
        "light": light,
        "resource_class": task_type_token,
        "memory_heavy": memory_heavy,
        "browser_heavy": browser_heavy,
        "gpu_heavy": gpu_heavy,
        "background_llm": background_llm,
        "social_light_llm": social_light_llm,
        "pressure_status": "HIGH" if derived_high else ("ELEVATED" if derived_elevated else status),
        "runtime_cpu_only_elevated": runtime_cpu_only_elevated,
        "vram_only_high": vram_only_high,
        "social_llm_vram_only_elevated": social_llm_vram_only_elevated,
        "pressure": {
            "status": status,
            "memory_percent": memory_percent,
            "vram_percent": vram_percent,
            "gpu_percent": gpu_percent,
            "reasons": reasons,
        },
        "retry_after_s": retry_after_s,
        "detail": detail,
        "reason": reason,
    }
    resolved_capability, resolved_risk = _resolve_degradation_capability(
        task_id=task_id,
        task_type=task_type,
        uses_llm=uses_llm,
        explicit_capability_id=capability_id,
        explicit_risk_level=static_risk_level,
    )
    return _apply_degradation_state_gate(
        output,
        degradation_state=degradation_state,
        capability_id=resolved_capability,
        static_risk_level=resolved_risk,
    )


class SystemResourceManager:
    """Background whole-system resource monitor and LLM-backed release planner."""

    def __init__(
        self,
        *,
        interval_s: float | None = None,
        analysis_cooldown_s: float | None = None,
        status_path: str | Path | None = None,
        llm_enabled: bool | None = None,
        auto_gc_enabled: bool | None = None,
        auto_trim_enabled: bool | None = None,
    ) -> None:
        self._interval_s = float(interval_s if interval_s is not None else _parse_float_env(
            "HOST_RESOURCE_MANAGER_INTERVAL_S",
            default=15.0,
            min_value=10.0,
            max_value=3600.0,
        ))
        self._analysis_cooldown_s = float(analysis_cooldown_s if analysis_cooldown_s is not None else _parse_float_env(
            "HOST_RESOURCE_MANAGER_LLM_COOLDOWN_S",
            default=600.0,
            min_value=30.0,
            max_value=7200.0,
        ))
        self._llm_enabled = _parse_bool_env("HOST_RESOURCE_MANAGER_LLM_ENABLED", default=True) if llm_enabled is None else bool(llm_enabled)
        self._auto_gc_enabled = _parse_bool_env("HOST_RESOURCE_MANAGER_AUTO_GC_ENABLED", default=True) if auto_gc_enabled is None else bool(auto_gc_enabled)
        self._auto_trim_enabled = _parse_bool_env("HOST_RESOURCE_MANAGER_AUTO_TRIM_ENABLED", default=True) if auto_trim_enabled is None else bool(auto_trim_enabled)
        default_status_path = Path(__file__).resolve().parents[1] / "data" / "runtime_resource_manager.json"
        self._status_path = Path(status_path) if status_path is not None else default_status_path
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._running = False
        self._last_snapshot: dict[str, Any] = {}
        self._last_decision: dict[str, Any] = {}
        self._last_actions: list[dict[str, Any]] = []
        self._last_error: str = ""
        self._last_llm_error: str = ""
        self._last_llm_attempts: list[dict[str, Any]] = []
        self._last_gate_decisions: list[dict[str, Any]] = []
        self._last_pressure_analysis: dict[str, Any] = {}
        self._last_trim_ts_by_pid: dict[int, float] = {}
        self._last_tick_ts_ms = 0
        self._last_analysis_ts = 0.0
        self._last_pressure_analysis_monotonic_ts = 0.0
        self._tick_count = 0
        self._analysis_count = 0
        self._auto_action_count = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self, *, run_immediately: bool = True) -> None:
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="system-resource-manager",
            daemon=True,
        )
        self._thread.start()
        if run_immediately:
            threading.Thread(
                target=lambda: self.run_once(reason="startup"),
                name="system-resource-manager-startup-sample",
                daemon=True,
            ).start()
        logger.info(
            "SystemResourceManager started (interval=%.1fs, llm=%s, auto_gc=%s, auto_trim=%s)",
            self._interval_s,
            self._llm_enabled,
            self._auto_gc_enabled,
            self._auto_trim_enabled,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("SystemResourceManager stopped")

    def get_status(self) -> dict[str, Any]:
        degradation_state: dict[str, Any] = {}
        try:
            from host_brain.observability.degradation_state import get_degradation_state  # noqa: PLC0415

            degradation_state = get_degradation_state()
        except Exception:
            degradation_state = {}
        with self._lock:
            return {
                "running": self._running,
                "interval_s": self._interval_s,
                "llm_enabled": self._llm_enabled,
                "auto_gc_enabled": self._auto_gc_enabled,
                "auto_trim_enabled": self._auto_trim_enabled,
                "last_tick_ts_ms": self._last_tick_ts_ms,
                "tick_count": self._tick_count,
                "analysis_count": self._analysis_count,
                "auto_action_count": self._auto_action_count,
                "last_error": self._last_error or None,
                "last_llm_error": self._last_llm_error or None,
                "last_llm_attempts": list(self._last_llm_attempts),
                "last_gate_decisions": list(self._last_gate_decisions),
                "last_pressure_analysis": dict(self._last_pressure_analysis),
                "last_snapshot": dict(self._last_snapshot),
                "last_decision": dict(self._last_decision),
                "last_actions": list(self._last_actions),
                "degradation_state": degradation_state,
                "status_path": str(self._status_path),
            }

    def run_once(self, *, reason: str = "manual") -> dict[str, Any]:
        started = time.monotonic()
        try:
            from host_brain.skills.impl.system_executor import execute_system_skill  # noqa: PLC0415

            result = execute_system_skill(
                {
                    "action": "resource_snapshot",
                    "manifest_ref": f"system_resource_snapshot::{reason}",
                    "include_gpu": True,
                    "include_processes": True,
                    "include_network": False,
                    "top_n": 12,
                    "sample_seconds": 0.1,
                }
            )
            detail = _as_dict(result.get("detail"))
            pressure = _as_dict(detail.get("pressure"))
            decision = self._maybe_analyze(detail=detail, pressure=pressure, reason=reason)
            actions = self._apply_safe_actions(decision=decision, pressure=pressure, detail=detail)
            pressure_analysis = self._maybe_record_pressure_analysis(
                pressure=pressure,
                reason=reason,
            )
            tick = {
                "status": "OK",
                "reason": reason,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "ts_ms": now_ms(),
                "snapshot": detail,
                "decision": decision,
                "actions": actions,
                "pressure_analysis": pressure_analysis,
            }
            with self._lock:
                self._tick_count += 1
                self._last_tick_ts_ms = int(tick["ts_ms"])
                self._last_snapshot = detail
                self._last_decision = decision
                self._last_actions = actions
                if pressure_analysis:
                    self._last_pressure_analysis = pressure_analysis
                self._last_error = ""
            self._persist_status()
            return tick
        except Exception as exc:
            with self._lock:
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                self._last_tick_ts_ms = now_ms()
            logger.warning("SystemResourceManager tick failed: %s", exc)
            self._persist_status()
            return {"status": "FAILED", "reason": reason, "error": str(exc), "ts_ms": now_ms()}

    def _loop(self) -> None:
        while not self._shutdown_event.wait(timeout=self._interval_s):
            self.run_once(reason="periodic")

    def assess_background_task(
        self,
        *,
        task_id: str,
        priority: str = "LOW",
        task_type: str = "",
        uses_llm: bool = False,
        uses_gpu: bool = False,
        reason: str = "background",
        capability_id: str = "",
        static_risk_level: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            pressure = _as_dict(_as_dict(self._last_snapshot).get("pressure"))
            decision_pressure = str(_as_dict(self._last_decision).get("pressure_level") or "").strip().upper()
        if _should_promote_llm_pressure(pressure=pressure, decision_pressure=decision_pressure):
            reasons = [str(item) for item in _as_list(pressure.get("reasons")) if str(item).strip()]
            reasons.append(f"llm_pressure_{decision_pressure.lower()}")
            pressure = {**pressure, "status": decision_pressure, "reasons": reasons}
        degradation_state: dict[str, Any] = {}
        try:
            from host_brain.observability.degradation_state import get_degradation_state  # noqa: PLC0415

            degradation_state = get_degradation_state()
        except Exception:
            degradation_state = {}
        decision = assess_background_task_pressure(
            task_id=task_id,
            pressure=pressure,
            priority=priority,
            task_type=task_type,
            uses_llm=uses_llm,
            uses_gpu=uses_gpu,
            reason=reason,
            degradation_state=degradation_state,
            capability_id=capability_id,
            static_risk_level=static_risk_level,
        )
        with self._lock:
            row = {**decision, "ts_ms": now_ms()}
            self._last_gate_decisions = [row, *self._last_gate_decisions[:19]]
        self._persist_status()
        return decision

    def _maybe_analyze(
        self,
        *,
        detail: Mapping[str, Any],
        pressure: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        status = str(pressure.get("status") or "UNKNOWN").strip().upper()
        now = time.monotonic()
        should_analyze = status in {"HIGH", "ELEVATED"}
        if not should_analyze and reason in {"manual", "startup"}:
            should_analyze = True
        if not should_analyze:
            return self._heuristic_decision(detail=detail, source="heuristic:no_pressure")
        if now - self._last_analysis_ts < self._analysis_cooldown_s and reason != "manual":
            with self._lock:
                cached = dict(self._last_decision)
            cached_pressure = str(cached.get("pressure_level") or "").strip().upper()
            cached_is_usable = (
                cached_pressure == status
                or (status == "ELEVATED" and cached_pressure == "HIGH")
            )
            if cached and cached_is_usable:
                cached["cached"] = True
                return cached
        if self._llm_enabled:
            llm_decision = self._analyze_with_llm(detail=detail, pressure=pressure)
            if llm_decision:
                self._last_analysis_ts = now
                with self._lock:
                    self._analysis_count += 1
                return llm_decision
        return self._heuristic_decision(detail=detail, source="heuristic:fallback")

    def _analyze_with_llm(self, *, detail: Mapping[str, Any], pressure: Mapping[str, Any]) -> dict[str, Any]:
        try:
            from host_brain.app.config import load_host_config  # noqa: PLC0415
            from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

            config_result = load_host_config()
            config = config_result.config if config_result.success and config_result.config is not None else None
            provider_name = str(os.environ.get("HOST_RESOURCE_MANAGER_LLM_PROVIDER") or "").strip().lower() or None
            model_id = str(os.environ.get("HOST_RESOURCE_MANAGER_LLM_MODEL_ID") or "").strip() or None
            api_key = str(os.environ.get("HOST_RESOURCE_MANAGER_LLM_API_KEY") or "").strip() or None
            base_url_override = str(os.environ.get("HOST_RESOURCE_MANAGER_LLM_BASE_URL") or "").strip() or None
            if not provider_name and config is not None:
                provider_name = str(getattr(config, "llm_provider", "") or "").strip().lower() or None
            if provider_name and config is not None and not api_key:
                api_key = str(getattr(config, "llm_api_key", "") or "").strip() or None
            if provider_name and not model_id:
                model_env_name = _provider_model_env_name(provider_name)
                model_id = str(os.environ.get(model_env_name) or "").strip() or None
            if provider_name and not base_url_override:
                base_url_env_name = _provider_base_url_env_name(provider_name)
                base_url_override = str(os.environ.get(base_url_env_name) or "").strip() or None
            explicit_single_model = bool(provider_name and model_id and api_key)
            timeout_ms = int(_parse_float_env(
                "HOST_RESOURCE_MANAGER_LLM_TIMEOUT_MS",
                default=30000,
                min_value=2000,
                max_value=90000,
            ))
            process_top = _as_dict(detail.get("process_top"))
            release_candidates = [_compact_process(_as_dict(row)) for row in _as_list(detail.get("release_candidates"))[:10]]
            processes = [_compact_process(_as_dict(row)) for row in _as_list(process_top.get("processes"))[:12]]
            gpu = _as_dict(_as_dict(detail.get("snapshot")).get("gpu"))
            payload = {
                "pressure": dict(pressure),
                "gpu": _as_dict(gpu.get("summary")),
                "processes": processes,
                "release_candidates": release_candidates,
                "policy": {
                    "do_not_kill_processes_automatically": True,
                    "auto_gc_allowed": self._auto_gc_enabled,
                    "auto_trim_allowed_only_if_env_enabled": self._auto_trim_enabled,
                    "natsume_runtime_and_system_processes_are_protected": True,
                    "model_training_and_voice_training_processes_are_protected": True,
                },
            }
            system_prompt = (
                "你是 Windows 整机资源管家。请分析 CPU、内存、GPU、显存和进程热点，"
                "只输出 JSON。把进程分成 can_release、cannot_release、needs_user_confirm、auto_actions。"
                "不要建议自动杀进程；Natsume/Host/Edge/Docker/系统进程以及模型训练、语音训练进程默认不可自动释放。"
            )
            user_prompt = (
                "根据以下资源快照给出处置建议。JSON schema: "
                "{\"summary\":str,\"pressure_level\":str,\"can_release\":[{\"pid\":int,\"name\":str,\"why\":str,\"action\":str}],"
                "\"cannot_release\":[{\"pid\":int,\"name\":str,\"why\":str}],"
                "\"needs_user_confirm\":[{\"pid\":int,\"name\":str,\"why\":str,\"action\":str}],"
                "\"auto_actions\":[{\"action\":str,\"why\":str,\"pids\":[int]}],\"confidence\":float}.\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)[:9000]
            )
            result = complete_llm_with_pool_sync(
                request_id=f"resource-manager::{now_ms()}",
                scene_type="RESOURCE_MANAGEMENT",
                cognitive_tier="C",
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                base_url_override=base_url_override,
                timeout_ms=timeout_ms,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                gen_params={"temperature": 0.1, "top_p": 0.8, "max_tokens": 900},
                response_format="json_object",
            )
            attempts = [_compact_llm_attempt(item) for item in list(result.attempts or [])]
            if result.pool_exhausted:
                with self._lock:
                    self._last_llm_error = f"LLM_POOL_EXHAUSTED: {_summarize_llm_attempts(attempts)}"
                    self._last_llm_attempts = attempts
                return {}
            parsed = json.loads(str(result.content or "{}"))
            if not isinstance(parsed, dict):
                with self._lock:
                    self._last_llm_error = "LLM_RESPONSE_NOT_OBJECT"
                    self._last_llm_attempts = attempts
                return {}
            parsed["source"] = "llm"
            parsed["model"] = result.final_model
            parsed["provider"] = result.final_provider
            parsed["cached"] = bool(result.cached)
            with self._lock:
                self._last_llm_error = ""
                self._last_llm_attempts = attempts
            return parsed
        except Exception as exc:
            with self._lock:
                self._last_llm_error = f"{exc.__class__.__name__}: {exc}"
                self._last_llm_attempts = []
            logger.debug("resource manager LLM analysis skipped: %s", exc)
            return {}

    def _heuristic_decision(self, *, detail: Mapping[str, Any], source: str) -> dict[str, Any]:
        candidates = [_as_dict(row) for row in _as_list(detail.get("release_candidates"))]
        can_release: list[dict[str, Any]] = []
        cannot_release: list[dict[str, Any]] = []
        needs_user_confirm: list[dict[str, Any]] = []
        for row in candidates[:12]:
            role = str(row.get("role") or "")
            item = {
                "pid": row.get("pid"),
                "name": row.get("name"),
                "memory_mb": row.get("memory_mb"),
                "vram_mb": row.get("vram_mb"),
                "why": f"role={role}",
            }
            if role in _PROTECTED_PROCESS_ROLES or bool(row.get("protected")):
                cannot_release.append(item)
            elif role in {"browser", "foreground_media_or_game", "recording_streaming", "communication_app"}:
                needs_user_confirm.append({**item, "action": "ask_user_to_close_or_pause"})
            elif bool(row.get("safe_to_trim")):
                can_release.append({**item, "action": "trim_working_set_candidate"})
            else:
                needs_user_confirm.append({**item, "action": "manual_review"})
        with self._lock:
            llm_error = self._last_llm_error
        if source == "heuristic:no_pressure":
            summary = "当前资源压力正常，未触发 LLM 深度分析；启发式巡检继续运行。"
            if llm_error:
                summary = f"{summary} 最近一次 LLM 深度分析状态：{llm_error}。"
        else:
            summary = "LLM 深度分析未完成，已使用启发式资源分级。"
            if llm_error:
                summary = f"{summary} 最近 LLM 状态：{llm_error}。"
        return {
            "source": source,
            "summary": summary,
            "pressure_level": str(_as_dict(detail.get("pressure")).get("status") or "UNKNOWN"),
            "can_release": can_release[:5],
            "cannot_release": cannot_release[:5],
            "needs_user_confirm": needs_user_confirm[:5],
            "auto_actions": [{"action": "gc_collect", "why": "安全回收当前 Host Python 垃圾对象", "pids": []}],
            "confidence": 0.55,
        }

    def _auto_trim_pids(
        self,
        *,
        decision: Mapping[str, Any],
        detail: Mapping[str, Any],
        pressure: Mapping[str, Any],
    ) -> list[int]:
        if not _is_memory_pressure(pressure):
            return []

        max_pids = _bounded_int_env(
            "HOST_RESOURCE_MANAGER_AUTO_TRIM_MAX_PIDS",
            default=3,
            min_value=1,
            max_value=10,
        )
        min_memory_mb = _parse_float_env(
            "HOST_RESOURCE_MANAGER_AUTO_TRIM_MIN_MEMORY_MB",
            default=512.0,
            min_value=128.0,
            max_value=32768.0,
        )
        cooldown_s = _parse_float_env(
            "HOST_RESOURCE_MANAGER_AUTO_TRIM_COOLDOWN_S",
            default=900.0,
            min_value=60.0,
            max_value=86400.0,
        )

        requested_pids: set[int] = set()
        for row in _as_list(decision.get("auto_actions")):
            item = _as_dict(row)
            if str(item.get("action") or "").strip() != "trim_working_set":
                continue
            for pid in _as_list(item.get("pids")):
                try:
                    requested_pids.add(int(pid))
                except (TypeError, ValueError):
                    continue

        candidates = [_as_dict(row) for row in _as_list(detail.get("release_candidates"))]
        candidates.sort(key=lambda row: float(row.get("memory_mb") or 0.0), reverse=True)
        now = time.monotonic()
        out: list[int] = []
        for row in candidates:
            try:
                pid = int(row.get("pid"))
            except (TypeError, ValueError):
                continue
            role = str(row.get("role") or "").strip()
            memory_mb = float(row.get("memory_mb") or 0.0)
            if pid <= 0 or memory_mb < min_memory_mb:
                continue
            if role in _PROTECTED_PROCESS_ROLES or bool(row.get("protected")):
                continue
            if not bool(row.get("safe_to_trim")):
                continue
            if role not in _AUTO_TRIM_ALLOWED_ROLES and pid not in requested_pids:
                continue
            last_trim_ts = float(self._last_trim_ts_by_pid.get(pid) or 0.0)
            if last_trim_ts > 0 and now - last_trim_ts < cooldown_s:
                continue
            out.append(pid)
            if len(out) >= max_pids:
                break
        return out

    def _should_record_pressure_analysis(self, *, pressure: Mapping[str, Any]) -> bool:
        if not _parse_bool_env("HOST_RESOURCE_MANAGER_AUTO_PRESSURE_ANALYSIS_ENABLED", default=True):
            return False
        status = str(pressure.get("status") or "UNKNOWN").strip().upper()
        memory_percent = _coerce_percent(pressure.get("memory_percent"))
        vram_percent = _coerce_percent(pressure.get("vram_percent"))
        if status == "HIGH":
            return True
        return bool(
            status == "ELEVATED"
            and memory_percent is not None
            and vram_percent is not None
            and memory_percent >= _bounded_int_env("HOST_RESOURCE_MANAGER_ANALYSIS_MEMORY_PERCENT", default=78, min_value=40, max_value=99)
            and vram_percent >= _bounded_int_env("HOST_RESOURCE_MANAGER_ANALYSIS_VRAM_PERCENT", default=60, min_value=30, max_value=99)
        )

    def _compact_pressure_analysis(self, result: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        detail = _as_dict(result.get("detail"))
        if not detail:
            return {}
        hypotheses = []
        for row in _as_list(detail.get("hypotheses"))[:4]:
            item = _as_dict(row)
            hypotheses.append({
                "cause_id": item.get("cause_id"),
                "title": item.get("title"),
                "confidence": item.get("confidence"),
                "severity": item.get("severity"),
            })
        return {
            "status": result.get("status"),
            "success": bool(detail.get("success", result.get("status") == "OK")),
            "reason": reason,
            "ts_ms": detail.get("ts_ms") or now_ms(),
            "summary": detail.get("summary"),
            "hypotheses": hypotheses,
            "analysis_path": detail.get("analysis_path"),
            "history_path": detail.get("history_path"),
        }

    def _maybe_record_pressure_analysis(self, *, pressure: Mapping[str, Any], reason: str) -> dict[str, Any]:
        if not self._should_record_pressure_analysis(pressure=pressure):
            return {}
        cooldown_s = _parse_float_env(
            "HOST_RESOURCE_MANAGER_AUTO_PRESSURE_ANALYSIS_COOLDOWN_S",
            default=300.0,
            min_value=60.0,
            max_value=7200.0,
        )
        now = time.monotonic()
        if self._last_pressure_analysis_monotonic_ts > 0 and now - self._last_pressure_analysis_monotonic_ts < cooldown_s:
            return {}
        self._last_pressure_analysis_monotonic_ts = now
        try:
            from host_brain.skills.impl.system_executor import execute_system_skill  # noqa: PLC0415

            result = execute_system_skill(
                {
                    "action": "resource_pressure_analysis",
                    "manifest_ref": f"resource_pressure_analysis::{reason}",
                    "top_n": 40,
                    "sample_seconds": 0.0,
                    "persist": True,
                }
            )
            compact = self._compact_pressure_analysis(result, reason=reason)
            if compact:
                logger.info("SystemResourceManager pressure root-cause analysis recorded: %s", compact.get("summary"))
            return compact
        except Exception as exc:
            logger.debug("SystemResourceManager pressure root-cause analysis skipped: %s", exc)
            return {
                "status": "FAILED",
                "success": False,
                "reason": reason,
                "ts_ms": now_ms(),
                "summary": f"pressure analysis failed: {exc.__class__.__name__}: {exc}",
                "hypotheses": [],
            }

    def _apply_safe_actions(
        self,
        *,
        decision: Mapping[str, Any],
        pressure: Mapping[str, Any],
        detail: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        pressure_status = str(pressure.get("status") or "UNKNOWN").strip().upper()
        if self._auto_gc_enabled and pressure_status in {"HIGH", "ELEVATED"}:
            try:
                from host_brain.skills.impl.system_executor import execute_system_skill  # noqa: PLC0415

                result = execute_system_skill({"action": "resource_release", "mode": "gc_collect", "dry_run": False})
                actions.append({"action": "gc_collect", "result": result})
            except Exception as exc:
                actions.append({"action": "gc_collect", "error": str(exc)})
        if self._auto_trim_enabled:
            trim_pids = self._auto_trim_pids(decision=decision, detail=detail, pressure=pressure)
            if trim_pids:
                try:
                    from host_brain.skills.impl.system_executor import execute_system_skill  # noqa: PLC0415

                    result = execute_system_skill(
                        {
                            "action": "resource_release",
                            "mode": "trim_working_sets",
                            "dry_run": False,
                            "confirm": True,
                            "pids": trim_pids[:5],
                        }
                    )
                    actions.append({"action": "trim_working_sets", "pids": trim_pids[:5], "result": result})
                    with self._lock:
                        trimmed_ok = bool(_as_dict(result.get("detail")).get("success"))
                        if trimmed_ok:
                            now = time.monotonic()
                            for pid in trim_pids[:5]:
                                self._last_trim_ts_by_pid[int(pid)] = now
                except Exception as exc:
                    actions.append({"action": "trim_working_sets", "error": str(exc)})
        if actions:
            with self._lock:
                self._auto_action_count += len(actions)
        return actions

    def _persist_status(self) -> None:
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            self._status_path.write_text(
                json.dumps(self.get_status(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("resource manager status persist skipped: %s", exc)
