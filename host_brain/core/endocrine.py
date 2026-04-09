"""Endocrine baseline state helpers.

将 HardState（离散状态）映射为可连续读取的“激素基调”。
当前实现为最小可运行版本，目标是：
1) 不破坏现有 `mood_state` 兼容链路；
2) 给调度/治理提供连续门控输入（尤其是 cortisol）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EndocrineState:
    """内分泌基调状态（0~1 连续值）。"""

    dopamine_level: float
    serotonin_level: float
    cortisol_level: float
    energy_reserve: float
    gate_state: str
    source: str = "endocrine_v0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "dopamine_level": round(self.dopamine_level, 4),
            "serotonin_level": round(self.serotonin_level, 4),
            "cortisol_level": round(self.cortisol_level, 4),
            "energy_reserve": round(self.energy_reserve, 4),
            "gate_state": self.gate_state,
            "source": self.source,
        }


def derive_endocrine_state(
    *,
    hard_state: Mapping[str, Any] | Any,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
) -> EndocrineState:
    """从 hard_state 派生激素基调。

    Args:
        hard_state: 支持 dict 或带属性对象（如 HardState dataclass）。
        emotion_tag: 可选情绪标签（当前用于微调 cortisol / dopamine）。
        emotion_intensity: 可选情绪强度 [0, 1]。
    """

    energy_level = _coerce_int(_read_state_value(hard_state, "energy_level", 60), low=0, high=100, default=60)
    mood_state = str(_read_state_value(hard_state, "mood_state", "steady") or "steady").strip().lower() or "steady"
    sys_mode = str(_read_state_value(hard_state, "sys_mode", "normal") or "normal").strip().lower() or "normal"

    energy_reserve = _clamp01(energy_level / 100.0)
    mood_dopamine_bias = {
        "engaged": 0.22,
        "steady": 0.06,
        "calm": 0.03,
        "tired": -0.12,
        "critical": -0.30,
    }.get(mood_state, 0.0)
    mood_serotonin_bias = {
        "calm": 0.20,
        "steady": 0.10,
        "engaged": 0.08,
        "tired": -0.08,
        "critical": -0.25,
    }.get(mood_state, 0.0)
    mood_cortisol_bias = {
        "critical": 0.52,
        "tired": 0.20,
        "steady": 0.03,
        "engaged": -0.05,
        "calm": -0.12,
    }.get(mood_state, 0.02)
    mode_cortisol_bias = {
        "locked": 0.28,
        "maintenance": 0.18,
        "focus": 0.10,
        "gaming": 0.05,
        "normal": 0.0,
        "sleep": -0.12,
    }.get(sys_mode, 0.0)

    energy_stress = _clamp01((0.45 - energy_reserve) * 1.4) if energy_reserve < 0.45 else 0.0
    intensity = _clamp01(_coerce_float(emotion_intensity, default=0.0))
    etag = str(emotion_tag or "").strip().upper()
    emotion_cortisol = 0.0
    emotion_dopamine = 0.0
    if etag in {"ANXIOUS", "ANGRY", "FRUSTRATED", "SAD"}:
        emotion_cortisol = 0.20 * intensity
    if etag in {"HAPPY", "EXCITED", "PROUD", "GRATEFUL", "LOVING"}:
        emotion_dopamine = 0.15 * intensity

    dopamine = _clamp01(0.22 + 0.50 * energy_reserve + mood_dopamine_bias + emotion_dopamine)
    serotonin = _clamp01(0.30 + 0.35 * energy_reserve + mood_serotonin_bias)
    cortisol = _clamp01(0.14 + mood_cortisol_bias + mode_cortisol_bias + energy_stress + emotion_cortisol)

    gate_state = _resolve_gate_state(cortisol_level=cortisol, energy_reserve=energy_reserve, mood_state=mood_state)
    return EndocrineState(
        dopamine_level=dopamine,
        serotonin_level=serotonin,
        cortisol_level=cortisol,
        energy_reserve=energy_reserve,
        gate_state=gate_state,
    )


def _resolve_gate_state(*, cortisol_level: float, energy_reserve: float, mood_state: str) -> str:
    mood = str(mood_state or "").strip().lower()
    if mood == "critical" or cortisol_level >= 0.78 or energy_reserve <= 0.12:
        return "protective"
    if cortisol_level >= 0.62 or energy_reserve <= 0.22:
        return "cautious"
    return "open"


def _read_state_value(state: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    if isinstance(state, Mapping):
        return state.get(key, default)
    return getattr(state, key, default)


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, *, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if number < low:
        return low
    if number > high:
        return high
    return number


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


__all__ = ["EndocrineState", "derive_endocrine_state"]
