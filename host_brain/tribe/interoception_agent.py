from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class InteroceptionAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="interoception_agent",
            family="interoception",
            execution_side="host",
            owner_domain="brain",
            capabilities=("interoceptive_index", "body_budget", "recovery_urgency"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("CIRCADIAN_PHASE_UPDATED", self._on_state_signal)
            bus.subscribe("PROPRIOCEPTION_UPDATED", self._on_state_signal)
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_state_signal)
            bus.subscribe("IMMUNE_BLOCK", self._on_state_signal)
            bus.subscribe("CONVERSATION_ENDED", self._on_state_signal)
        except Exception:
            pass

    def evaluate_profile(self, *, source: str = "runtime", ts_ms: int | None = None) -> dict[str, Any]:
        resolved_ts = int(ts_ms or now_ms())
        vital_signs = self.state_registry.aggregate_vital_signs()
        stress = _clamp(float(vital_signs.get("stress_index") or 0.0))
        cognitive_load = _clamp(float(vital_signs.get("cognitive_load_index") or 0.2))
        sleep_pressure = _clamp(float(vital_signs.get("sleep_pressure") or 0.2))
        energy = _clamp(float(vital_signs.get("energy_reserve") or 0.6))
        immune_risk = str(vital_signs.get("immune_risk_level") or "none").strip().lower()
        homeostasis_ok = bool(vital_signs.get("homeostasis_ok", True))
        somatic_fatigue = 0.0
        try:
            from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415

            somatic_state = get_somatic_agent().export_runtime_state()
            somatic_fatigue = _clamp(float(somatic_state.get("fatigue_level") or 0.0))
        except Exception:
            pass

        immune_penalty = 0.0
        if immune_risk == "medium":
            immune_penalty = 0.08
        elif immune_risk in {"high", "critical"}:
            immune_penalty = 0.18

        interoceptive_index = _clamp(
            stress * 0.30 + cognitive_load * 0.25 + sleep_pressure * 0.20 + somatic_fatigue * 0.25 + immune_penalty
        )
        body_budget = _clamp((energy * 0.65 + (1.0 - interoceptive_index) * 0.35), low=0.0, high=1.0)
        recovery_urgency = _clamp(
            sleep_pressure * 0.45 + stress * 0.35 + cognitive_load * 0.20 + (0.18 if not homeostasis_ok else 0.0)
        )
        state = {
            "interoceptive_index": round(interoceptive_index, 4),
            "body_budget": round(body_budget, 4),
            "recovery_urgency": round(recovery_urgency, 4),
            "stress_index": round(stress, 4),
            "cognitive_load_index": round(cognitive_load, 4),
            "sleep_pressure": round(sleep_pressure, 4),
            "energy_reserve": round(energy, 4),
            "somatic_fatigue": round(somatic_fatigue, 4),
            "immune_risk_level": immune_risk,
            "homeostasis_ok": homeostasis_ok,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("interoception_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state.update(state)
        prev_state["sample_count"] = int(prev_state.get("sample_count") or 0) + 1
        self.state_registry.set_state("interoception_agent", prev_state, writer="interoception_agent")
        emit_tribe_event(
            event_type="INTEROCEPTION_UPDATED",
            source_agent="interoception_agent",
            payload={
                "interoceptive_index": state["interoceptive_index"],
                "body_budget": state["body_budget"],
                "recovery_urgency": state["recovery_urgency"],
                "ts_ms": resolved_ts,
            },
        )

        # T-210：高恢复紧迫度时主动触发 GlymphaticAgent 恢复周期
        if recovery_urgency >= 0.75:
            try:
                from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
                get_glymphatic_agent().run_recovery_cycle(
                    source="interoception_urgency",
                    force=True,
                    now_ts_ms=resolved_ts,
                )
            except Exception:
                pass

        return dict(prev_state)

    def export_runtime_state(self) -> dict[str, Any]:
        state = self.evaluate_profile(source="export_runtime_state")
        return {
            "interoceptive_index": float(state.get("interoceptive_index") or 0.0),
            "body_budget": float(state.get("body_budget") or 0.5),
            "recovery_urgency": float(state.get("recovery_urgency") or 0.0),
            "stress_index": float(state.get("stress_index") or 0.0),
            "cognitive_load_index": float(state.get("cognitive_load_index") or 0.0),
            "sleep_pressure": float(state.get("sleep_pressure") or 0.0),
            "energy_reserve": float(state.get("energy_reserve") or 0.6),
            "immune_risk_level": str(state.get("immune_risk_level") or "none"),
            "homeostasis_ok": bool(state.get("homeostasis_ok", True)),
            "sample_count": int(state.get("sample_count") or 0),
            "last_updated_ts_ms": int(state.get("ts_ms") or now_ms()),
        }

    async def _on_state_signal(self, event: Any) -> None:
        self.evaluate_profile(source=str(getattr(event, "event_type", "") or "signal"), ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()))


_INTEROCEPTION_AGENT: InteroceptionAgent | None = None


def get_interoception_agent() -> InteroceptionAgent:
    global _INTEROCEPTION_AGENT
    if _INTEROCEPTION_AGENT is None:
        _INTEROCEPTION_AGENT = InteroceptionAgent()
    return _INTEROCEPTION_AGENT


def set_interoception_agent(agent: InteroceptionAgent | None) -> None:
    global _INTEROCEPTION_AGENT
    _INTEROCEPTION_AGENT = agent


__all__ = ["InteroceptionAgent", "get_interoception_agent", "set_interoception_agent"]
