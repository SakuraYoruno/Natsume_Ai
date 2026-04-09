from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.endocrine import derive_endocrine_state
from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class EndocrineAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="endocrine_agent",
            family="endocrine",
            capabilities=("hormone_baseline", "gate_state", "homeostasis"),
        )
    )
    _last_state: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def resolve_endocrine_state(
        self,
        *,
        hard_state: Mapping[str, Any] | Any,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        state = derive_endocrine_state(
            hard_state=hard_state,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )
        payload = dict(state.as_dict())
        self._last_state = dict(payload)
        return payload

    def resolve_vital_signs(self, *, endocrine_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = dict(endocrine_state or self._last_state or {})
        cortisol = _safe_float(state.get("cortisol_level"), 0.0)
        energy = _safe_float(state.get("energy_reserve"), 0.0)
        gate_state = str(state.get("gate_state") or "open").strip().lower() or "open"
        return {
            "gate_state": gate_state,
            "stress_index": round(cortisol, 4),
            "energy_reserve": round(energy, 4),
            "homeostasis_ok": bool(cortisol < 0.62 and energy > 0.22 and gate_state == "open"),
        }

    def export_runtime_state(self) -> dict[str, Any]:
        last_state = dict(self._last_state or {})
        return {
            "state": last_state,
            "vitals": self.resolve_vital_signs(endocrine_state=last_state),
        }


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


_ENDOCRINE_AGENT: EndocrineAgent | None = None


def get_endocrine_agent() -> EndocrineAgent:
    global _ENDOCRINE_AGENT
    if _ENDOCRINE_AGENT is None:
        _ENDOCRINE_AGENT = EndocrineAgent()
    return _ENDOCRINE_AGENT


def set_endocrine_agent(agent: EndocrineAgent | None) -> None:
    global _ENDOCRINE_AGENT
    _ENDOCRINE_AGENT = agent


__all__ = [
    "EndocrineAgent",
    "get_endocrine_agent",
    "set_endocrine_agent",
]
