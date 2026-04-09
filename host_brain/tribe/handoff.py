from __future__ import annotations

from typing import Any, Callable

from host_brain.core.ids import now_ms
from host_brain.tribe.event_bus import new_tribe_event
from host_brain.tribe.registry import HostTribeRegistry, get_host_tribe_registry
from host_brain.tribe.tribe_state import TribeStateRegistry


EventPublisher = Callable[[dict[str, Any]], None]


class TribeHandoffCoordinator:
    def __init__(
        self,
        *,
        state_registry: TribeStateRegistry | None = None,
        tribe_registry: HostTribeRegistry | None = None,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._state_registry = state_registry or TribeStateRegistry()
        self._tribe_registry = tribe_registry or get_host_tribe_registry()
        self._event_publisher = event_publisher

    @property
    def state_registry(self) -> TribeStateRegistry:
        return self._state_registry

    @property
    def tribe_registry(self) -> HostTribeRegistry:
        return self._tribe_registry

    def record_dispatch_handoff(
        self,
        *,
        request_id: str,
        session_id: str,
        route: str,
        target_id: str | None,
        reason: str | None,
        source_family: str = "cortex",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ts_ms = now_ms()
        target_family = self._tribe_registry.resolve_target_family(route=route, target_id=target_id)
        normalized_route = str(route or "").strip().lower() or "chat"
        normalized_target = str(target_id or "").strip() or None
        normalized_reason = str(reason or "").strip() or None
        source = str(source_family or "").strip().lower() or "cortex"

        payload = {
            "request_id": str(request_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "route": normalized_route,
            "target_id": normalized_target,
            "reason": normalized_reason,
            "source_family": source,
            "target_family": target_family,
            "ts_ms": ts_ms,
        }
        if isinstance(extra, dict) and extra:
            payload["extra"] = dict(extra)

        self._record_family_state(
            source,
            direction="outbound",
            payload=payload,
            writer="dispatch_agent",
        )
        self._record_family_state(
            target_family,
            direction="inbound",
            payload=payload,
            writer="dispatch_agent",
        )
        self._publish_handoff_event(payload)
        return dict(payload)

    def snapshot(self) -> dict[str, Any]:
        return {
            "families": self._tribe_registry.list_families(),
            "agents": self._tribe_registry.list_agents(),
            "states": self._state_registry.snapshot(),
            "timestamp": now_ms(),
        }

    def _record_family_state(
        self,
        family: str,
        *,
        direction: str,
        payload: dict[str, Any],
        writer: str,
    ) -> None:
        namespace = str(family or "").strip() or "cortex"
        previous = self._state_registry.get_state(namespace) or {}
        previous_state = dict(previous.get("state") or {})
        previous_count = int(previous_state.get("handoff_count") or 0)
        last_key = "last_handoff_outbound" if direction == "outbound" else "last_handoff_inbound"
        merged = dict(previous_state)
        merged[last_key] = dict(payload)
        merged["handoff_count"] = previous_count + 1
        merged["last_active_ms"] = payload.get("ts_ms")
        self._state_registry.set_state(namespace, merged, writer=writer)

    def _publish_handoff_event(self, payload: dict[str, Any]) -> None:
        if self._event_publisher is not None:
            self._event_publisher(dict(payload))
            return
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            event = new_tribe_event(
                event_type="dispatch_handoff",
                source_agent=f"{payload.get('source_family', 'cortex')}_agent",
                target_agent=f"{payload.get('target_family', 'cortex')}_agent",
                payload=payload,
            )
            bus.publish(event)
        except Exception:
            return


_HANDOFF_COORDINATOR: TribeHandoffCoordinator | None = None


def get_tribe_handoff_coordinator() -> TribeHandoffCoordinator:
    global _HANDOFF_COORDINATOR
    if _HANDOFF_COORDINATOR is None:
        _HANDOFF_COORDINATOR = TribeHandoffCoordinator()
    return _HANDOFF_COORDINATOR


def set_tribe_handoff_coordinator(coordinator: TribeHandoffCoordinator | None) -> None:
    global _HANDOFF_COORDINATOR
    _HANDOFF_COORDINATOR = coordinator


__all__ = [
    "TribeHandoffCoordinator",
    "get_tribe_handoff_coordinator",
    "set_tribe_handoff_coordinator",
]

