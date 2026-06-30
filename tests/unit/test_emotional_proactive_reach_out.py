"""C2-5 统一 reach-out 到 B1 内在天气 单测。

验证：build_emotional_proactive_episode 把 reach_out_bias 当统一冲动源——
有 inner_weather 用其 reach_out_bias、无则从 inner_state 派生兜底；
高 reach_out_bias 上调 emotional_drive，零值不下拉（向后兼容）。
"""

from __future__ import annotations

from host_brain.persona.emotional_proactive import (
    _resolve_reach_out_bias,
    build_emotional_proactive_episode,
)

NOW = 1_800_000_000_000


def _ep(inner_state, inner_weather=None):
    return build_emotional_proactive_episode(
        inner_state=inner_state,
        inner_weather=inner_weather,
        bond_summary={"stage": "CLOSE_FRIEND"},
        scene_awareness={},
        now_ms=NOW,
        last_foreground_ts_ms=NOW - 3 * 3600 * 1000,
        last_proactive_outbox_ts_ms=0,
    )


# ── 纯函数：reach_out_bias 解析 ──

def test_reach_out_bias_uses_weather_when_present() -> None:
    assert _resolve_reach_out_bias({"loneliness": 0.1}, {"reach_out_bias": 0.9}) == 0.9


def test_reach_out_bias_fallback_from_inner_state() -> None:
    # 天气缺失 → loneliness*0.7 + anticipation*0.3
    assert round(_resolve_reach_out_bias({"loneliness": 0.8, "anticipation": 0.0}, None), 3) == 0.56


# ── episode：天气接入冲动 ──

def test_episode_exposes_reach_out_bias() -> None:
    ep = _ep({"loneliness": 0.1, "anticipation": 0.0}, {"reach_out_bias": 0.9})
    assert ep.emotional_state["reach_out_bias"] == 0.9


def test_high_reach_out_bias_raises_drive() -> None:
    low = _ep({"loneliness": 0.1, "anticipation": 0.0}, {"reach_out_bias": 0.0})
    high = _ep({"loneliness": 0.1, "anticipation": 0.0}, {"reach_out_bias": 0.95})
    assert high.emotional_drive > low.emotional_drive


def test_low_reach_out_bias_does_not_lower_drive() -> None:
    # fold 只上调不下调：reach_out_bias 升高时 drive 单调不降
    zero = _ep({"loneliness": 0.6, "anticipation": 0.4}, {"reach_out_bias": 0.0})
    higher = _ep({"loneliness": 0.6, "anticipation": 0.4}, {"reach_out_bias": 0.5})
    assert higher.emotional_drive >= zero.emotional_drive


def test_backward_compatible_without_weather() -> None:
    ep = _ep({"loneliness": 0.7, "anticipation": 0.2})
    assert ep.emotional_state["reach_out_bias"] == round(0.7 * 0.7 + 0.2 * 0.3, 3)
    assert ep.episode_id  # 仍能正常产出 episode
