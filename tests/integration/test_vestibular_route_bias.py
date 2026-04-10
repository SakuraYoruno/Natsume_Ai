"""T-213：VestibularAgent 路由偏置全链路集成测试。

覆盖场景：
1. jitter_index=0.0  → route_bias="normal"  → route_intent() 正常路由
2. jitter_index=0.45 → route_bias="conservative" → route_intent() 附加 vestibular_bias="conservative"
3. jitter_index=0.80 → route_bias="safe_only" → route_intent() 返回 route="chat" + vestibular_bias="safe_only"
4. jitter_index=0.85 → route_intent() 返回 route="throttle"（硬阈值）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from host_brain.tribe.vestibular_agent import VestibularAgent
from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


@pytest.fixture()
def vestibular(registry):
    return VestibularAgent(state_registry=registry)


@pytest.fixture()
def cortex(registry):
    return CortexAgent(state_registry=registry)


def _make_tribe_context_with_jitter(registry: TribeStateRegistry, jitter: float) -> dict:
    """构造包含指定 jitter_index 的 tribe_context。"""
    # 计算对应的 route_bias
    if jitter >= 0.72:
        route_bias = "safe_only"
    elif jitter >= 0.35:
        route_bias = "conservative"
    else:
        route_bias = "normal"
    damping_factor = max(0.2, min(1.0, 1.0 - jitter * 0.72))
    return {
        "vital_signs": {
            "gate_state": "open",
            "silence_willingness": 0.3,
            "homeostasis_ok": True,
            "interoceptive_index": 0.1,
        },
        "vestibular_state": {
            "jitter_index": jitter,
            "route_bias": route_bias,
            "damping_factor": round(damping_factor, 4),
            "stability_state": "critical" if jitter >= 0.72 else ("unstable" if jitter >= 0.35 else "stable"),
        },
        "interoception_state": {"interoceptive_index": 0.1},
    }


class TestVestibularRouteBias:
    """T-213：VestibularAgent 路由偏置全链路集成测试。"""

    def test_normal_jitter_normal_route(self, cortex, registry):
        """jitter=0.0 → route_bias='normal' → route='chat' + vestibular_bias='normal'。"""
        ctx = _make_tribe_context_with_jitter(registry, jitter=0.0)
        with patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            result = cortex.route_intent(message="你好", tribe_context=ctx)
        assert result["route"] == "chat"
        assert result.get("vestibular_bias") == "normal"

    def test_conservative_jitter_attaches_bias(self, cortex, registry):
        """jitter=0.45 → route_bias='conservative' → route='chat' + vestibular_bias='conservative'。"""
        ctx = _make_tribe_context_with_jitter(registry, jitter=0.45)
        with patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            result = cortex.route_intent(message="你好", tribe_context=ctx)
        assert result["route"] == "chat"
        assert result.get("vestibular_bias") == "conservative"
        assert result.get("damping_factor") is not None

    def test_safe_only_forces_chat(self, cortex, registry):
        """jitter=0.80 → route_bias='safe_only' → route='chat' + vestibular_bias='safe_only'。"""
        ctx = _make_tribe_context_with_jitter(registry, jitter=0.80)
        with patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            result = cortex.route_intent(message="你好", tribe_context=ctx)
        assert result["route"] == "chat"
        assert result.get("vestibular_bias") == "safe_only"

    def test_hard_threshold_throttle(self, cortex, registry):
        """jitter=0.85 → 超过硬阈值 0.78 → route='throttle'。"""
        ctx = _make_tribe_context_with_jitter(registry, jitter=0.85)
        with patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            result = cortex.route_intent(message="你好", tribe_context=ctx)
        assert result["route"] == "throttle"
        assert result.get("vestibular_bias") == "throttle"

    def test_vestibular_evaluate_stability_route_bias(self, vestibular):
        """VestibularAgent.evaluate_stability() 应根据 jitter 正确计算 route_bias。"""
        with patch("host_brain.tribe.vestibular_agent.emit_tribe_event"):
            # 稳定状态
            vestibular._mode_transition_ts.clear()
            vestibular._platform_flap_ts.clear()
            vestibular._initiative_defer_ts.clear()
            result = vestibular.evaluate_stability(source="test")
        assert result["route_bias"] == "normal"
        assert result["jitter_index"] == 0.0
