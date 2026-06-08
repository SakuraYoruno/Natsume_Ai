from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping

_OBS_WARM_SLOT = threading.Lock()


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _bounded_float_env(name: str, *, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(str(os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = float(default)
    return max(float(min_value), min(float(max_value), float(value)))


def _status_path() -> Path:
    raw = str(os.getenv("HOST_RESOURCE_MANAGER_STATUS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "data" / "runtime_resource_manager.json"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _load_resource_pressure() -> dict[str, Any]:
    try:
        path = _status_path()
        stat = path.stat()
        stale_after_s = max(5.0, float(os.getenv("HOST_OBS_WARM_RESOURCE_STATUS_STALE_S") or "45"))
        if time.time() - float(stat.st_mtime) > stale_after_s:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    snapshot = _as_dict(_as_dict(payload).get("last_snapshot"))
    return _as_dict(snapshot.get("pressure"))


def _has_reason_prefix(reasons: set[str], prefixes: tuple[str, ...]) -> bool:
    return any(reason.startswith(prefix) for reason in reasons for prefix in prefixes)


def should_defer_obs_warm_refresh(*, task_name: str) -> tuple[bool, str]:
    _ = task_name
    if not _parse_bool_env("HOST_RESOURCE_MANAGER_ENABLED", default=False):
        return False, ""
    if not _parse_bool_env("HOST_OBS_WARM_RESOURCE_GUARD_ENABLED", default=True):
        return False, ""
    pressure = _load_resource_pressure()
    if not pressure:
        return False, ""
    status = str(pressure.get("status") or "").strip().upper()
    reasons = {str(item).strip().lower() for item in list(pressure.get("reasons") or [])}
    if status == "HIGH":
        return True, "resource_pressure_high"
    runtime_cpu_pressure = (
        "natsume_runtime_cpu_high" in reasons
        or "natsume_runtime_cpu_elevated" in reasons
    )
    mixed_resource_pressure = _has_reason_prefix(
        reasons,
        ("memory_", "vram_", "gpu_", "disk_"),
    )
    if status == "ELEVATED" and runtime_cpu_pressure and mixed_resource_pressure:
        return True, "mixed_resource_pressure"
    if (
        status == "ELEVATED"
        and runtime_cpu_pressure
        and _parse_bool_env("HOST_OBS_WARM_DEFER_ON_RUNTIME_CPU_ELEVATED", default=True)
    ):
        return True, "natsume_runtime_cpu_pressure"
    return False, ""


def try_acquire_obs_warm_slot(*, task_name: str) -> tuple[bool, str]:
    _ = task_name
    if not _parse_bool_env("HOST_OBS_WARM_SINGLE_FLIGHT_ENABLED", default=True):
        return True, ""
    wait_s = _bounded_float_env(
        "HOST_OBS_WARM_SLOT_WAIT_S",
        default=20.0,
        min_value=0.0,
        max_value=120.0,
    )
    if wait_s > 0:
        acquired = _OBS_WARM_SLOT.acquire(timeout=wait_s)
    else:
        acquired = _OBS_WARM_SLOT.acquire(blocking=False)
    if acquired:
        return True, ""
    return False, "obs_warm_slot_busy"


def release_obs_warm_slot() -> None:
    try:
        _OBS_WARM_SLOT.release()
    except RuntimeError:
        pass
