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

        # ── T-17：接入 PainRewardAgent 压力信号 ──────────────────────────
        # 将痛觉/奖励系统的净压力变化量叠加到 cortisol_level，
        # 使痛觉事件（被拒绝/被攻击/话题受挫等）能够影响内分泌状态。
        # 叠加量上限 ±0.15，避免单次事件过度影响皮质醇。
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            _pain_agent = get_pain_reward_agent()
            _stress_signal = _pain_agent.get_stress_signal()
            _net_stress_delta = float(_stress_signal.get("net_stress_delta") or 0.0)
            if abs(_net_stress_delta) > 0.001:
                _clamped_delta = max(-0.15, min(0.15, _net_stress_delta))
                _cur_cortisol = _safe_float(payload.get("cortisol_level"), 0.0)
                _new_cortisol = max(0.0, min(1.0, _cur_cortisol + _clamped_delta))
                payload["cortisol_level"] = round(_new_cortisol, 4)
                payload["_pain_stress_delta"] = round(_clamped_delta, 4)
        except Exception:
            pass

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
