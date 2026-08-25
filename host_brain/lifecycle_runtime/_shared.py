"""Helper functions used by http lifecycle hooks."""

from __future__ import annotations

from typing import Any

def read_world_canon_fact(world_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "world_canon_facts":
        return None
    try:
        docs = world_store.list_documents()
    except Exception:
        return None
    chunks: list[str] = []
    for doc in docs[:3]:
        title = str(getattr(doc, "title", "") or "").strip()
        body = str(getattr(doc, "body", "") or "").strip()
        line = f"{title}: {body}" if title and body else (body or title)
        if line:
            chunks.append(line)
    merged = "\n".join(chunks).strip()
    return merged or None


def read_episodic_latest_fact(episodic_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "episodic_latest":
        return None
    records_map = getattr(episodic_store, "_records", None)
    if not isinstance(records_map, dict) or not records_map:
        return None
    try:
        latest = max(records_map.values(), key=lambda row: int(getattr(row, "ts_ms", 0)))
    except Exception:
        return None
    text = str(getattr(latest, "text", "") or "").strip()
    return text or None


def _as_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    for attr_name in ("as_dict", "model_dump", "dict", "to_summary"):
        method = getattr(value, attr_name, None)
        if not callable(method):
            continue
        try:
            payload = method()
        except Exception:
            continue
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _record_snapshot_status(
    slot_status: dict[str, str],
    degraded_reasons: list[str],
    *,
    slot: str,
    status: str,
    detail: str = "",
) -> None:
    normalized_status = str(status or "unknown").strip() or "unknown"
    slot_status[slot] = normalized_status
    if normalized_status != "live":
        reason = f"{slot}:{normalized_status}"
        if detail:
            reason = f"{reason} ({detail})"
        degraded_reasons.append(reason)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _seconds_since_timestamp(*, ts_ms: Any, now_ms: int) -> int | None:
    value = _coerce_int(ts_ms, 0)
    if value <= 0:
        return None
    delta_ms = max(0, int(now_ms) - value)
    return int(delta_ms / 1000)


def _extract_interval_seconds(task_health: dict[str, Any]) -> int | None:
    trigger_config = task_health.get("trigger_config")
    if not isinstance(trigger_config, dict):
        return None
    seconds = _coerce_int(trigger_config.get("seconds"), 0)
    return seconds if seconds > 0 else None


def _classify_tick_health(
    *,
    scheduler_status: str,
    core_ticks_registered: bool,
    seconds_since_last_tick: int | None,
    expected_interval_s: int,
) -> str:
    normalized_scheduler = str(scheduler_status or "").strip().upper()
    if normalized_scheduler in {"UNAVAILABLE", "ERROR", "STOPPED", "DOWN", "OFFLINE"}:
        return "inactive"
    if not core_ticks_registered:
        return "inactive"
    if seconds_since_last_tick is None:
        return "inactive"

    base_interval = max(int(expected_interval_s or 60), 30)
    if seconds_since_last_tick <= int(base_interval * 2.5):
        return "fresh"
    if seconds_since_last_tick <= int(base_interval * 6):
        return "delayed"
    return "stalled"



__all__ = [name for name in globals() if not name.startswith("__")]
