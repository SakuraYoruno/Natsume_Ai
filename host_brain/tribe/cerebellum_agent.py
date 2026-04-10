from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class CerebellumAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="cerebellum_agent",
            family="cerebellum",
            execution_side="host",
            owner_domain="brain",
            capabilities=("error_correction", "latency_compensation", "retry_strategy"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("MOTOR_ACTION_DISPATCHED", self._on_motor_action_dispatched)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_disconnected)
        except Exception:
            pass

    def evaluate_correction(
        self,
        *,
        execution_feedback: Mapping[str, Any],
        source: str = "runtime",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        feedback = dict(execution_feedback or {})
        resolved_ts = int(ts_ms or now_ms())
        accepted = bool(feedback.get("accepted", feedback.get("ok", False)))
        dispatched = bool(feedback.get("dispatched", feedback.get("ok", False)))
        latency_ms = max(0, int(feedback.get("latency_ms") or 0))
        reason = str(feedback.get("reason") or "").strip().upper()

        strategy = "NO_ADJUST"
        urgency = 0.15
        backoff_ms = 0
        if not accepted or not dispatched:
            strategy = "RETRY_ONCE"
            urgency = 0.74
            backoff_ms = 120
        elif latency_ms > 1200:
            strategy = "DEGRADE_OUTPUT"
            urgency = 0.52
            backoff_ms = 80
        elif latency_ms > 700:
            strategy = "LATENCY_COMPENSATE"
            urgency = 0.38
            backoff_ms = 40
        if reason in {"PUSHER_UNAVAILABLE", "HTTP_REJECTED", "PUSH_FAILED"}:
            strategy = "SAFE_FALLBACK"
            urgency = 0.81
            backoff_ms = 180

        decision = {
            "plan_id": str(feedback.get("plan_id") or "").strip() or None,
            "strategy": strategy,
            "urgency": round(_clamp(float(urgency)), 4),
            "backoff_ms": int(backoff_ms),
            "accepted": accepted,
            "dispatched": dispatched,
            "latency_ms": latency_ms,
            "reason": reason or None,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("cerebellum_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state["last_correction"] = decision
        prev_state["correction_count"] = int(prev_state.get("correction_count") or 0) + 1
        self.state_registry.set_state("cerebellum_agent", prev_state, writer="cerebellum_agent")
        emit_tribe_event(
            event_type="CEREBELLUM_CORRECTION_DECIDED",
            source_agent="cerebellum_agent",
            payload=decision,
        )
        return decision

    def export_runtime_state(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("cerebellum_agent") or {}
        state = dict(snap.get("state") or {})
        decision = dict(state.get("last_correction") or {})
        return {
            "last_strategy": str(decision.get("strategy") or "NO_ADJUST"),
            "last_urgency": float(decision.get("urgency") or 0.0),
            "last_backoff_ms": int(decision.get("backoff_ms") or 0),
            "last_latency_ms": int(decision.get("latency_ms") or 0),
            "correction_count": int(state.get("correction_count") or 0),
            "last_updated_ts_ms": int(decision.get("ts_ms") or 0),
        }

    async def _on_motor_action_dispatched(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        self.evaluate_correction(
            execution_feedback=payload,
            source="motor_action_dispatched",
            ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )

    async def _on_platform_disconnected(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        self.evaluate_correction(
            execution_feedback={
                "accepted": False,
                "dispatched": False,
                "latency_ms": 0,
                "reason": f"PLATFORM_DISCONNECTED:{str(payload.get('platform') or '').strip().lower()}",
            },
            source="platform_disconnected",
            ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )


_CEREBELLUM_AGENT: CerebellumAgent | None = None


def get_cerebellum_agent() -> CerebellumAgent:
    global _CEREBELLUM_AGENT
    if _CEREBELLUM_AGENT is None:
        _CEREBELLUM_AGENT = CerebellumAgent()
    return _CEREBELLUM_AGENT


def set_cerebellum_agent(agent: CerebellumAgent | None) -> None:
    global _CEREBELLUM_AGENT
    _CEREBELLUM_AGENT = agent


__all__ = ["CerebellumAgent", "get_cerebellum_agent", "set_cerebellum_agent"]
