from __future__ import annotations

from typing import Any, Mapping

from host_brain.tribe.event_bus import new_tribe_event


def emit_tribe_event(
    *,
    event_type: str,
    source_agent: str,
    target_agent: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

        bus = get_tribe_event_bus()
        event = new_tribe_event(
            event_type=str(event_type or "").strip() or "UNKNOWN",
            source_agent=str(source_agent or "").strip() or "unknown_source",
            target_agent=(str(target_agent).strip() or None) if target_agent is not None else None,
            payload=dict(payload or {}),
        )
        bus.publish(event)
        return event.to_dict()
    except Exception:
        return None


__all__ = ["emit_tribe_event"]
