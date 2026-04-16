"""tests/unit/test_tribe_deepening_batch41.py

第四十一批单元测试（T-296 ~ T-300）
覆盖场景：
  1. InteroceptionAgent.reduce_recovery_urgency() 正常降低 recovery_urgency
  2. InteroceptionAgent.inject_external_stress() 负值静默忽略（不再截断为0后叠加）
  3. InteroceptionAgent.inject_external_stress() 正值正常叠加
  4. PeripheralAgent._on_platform_connected 调用 reduce_recovery_urgency 而非负强度 inject
  5. ANSAgent.export_runtime_state() 写入 state_registry 快照
  6. ANSAgent recovery_mode=True 时 proactive_multiplier 降至 35%
  7. GlymphaticAgent 恢复周期完成后联动 LimbicAgent 正向情绪偏移
  8. HippocampusAgent.write_semantic_memory_fact 后联动 DefaultModeAgent 提升 continuity_score
  9. CircadianAgent day/dawn 相位切换时联动 ANSAgent recovery_mode=False
  10. SomaticAgent 高疲劳(>=0.60)时联动 InteroceptionAgent.inject_external_stress
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 场景 1：reduce_recovery_urgency 正常降低 recovery_urgency
# ─────────────────────────────────────────────────────────────────────────────
def test_reduce_recovery_urgency_decreases_value():
    from host_brain.tribe.interoception_agent import InteroceptionAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {"recovery_urgency": 0.60}}
    captured = {}

    def fake_set_state(ns, state, writer=None):
        captured["state"] = state

    registry.set_state.side_effect = fake_set_state

    agent = InteroceptionAgent(state_registry=registry)
    agent.reduce_recovery_urgency(source="test_relief", relief=0.50)

    assert "state" in captured
    new_urgency = captured["state"]["recovery_urgency"]
    # 0.60 - 0.50 * 0.30 = 0.60 - 0.15 = 0.45
    assert abs(new_urgency - 0.45) < 0.01, f"Expected ~0.45, got {new_urgency}"
    assert captured["state"]["last_relief_source"] == "test_relief"


# ─────────────────────────────────────────────────────────────────────────────
# 场景 2：inject_external_stress 负值静默忽略
# ─────────────────────────────────────────────────────────────────────────────
def test_inject_external_stress_ignores_negative_intensity():
    from host_brain.tribe.interoception_agent import InteroceptionAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {"recovery_urgency": 0.40}}

    agent = InteroceptionAgent(state_registry=registry)
    agent.inject_external_stress(source="test_negative", intensity=-0.20)

    # 负值应静默忽略，不调用 set_state
    registry.set_state.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 场景 3：inject_external_stress 正值正常叠加
# ─────────────────────────────────────────────────────────────────────────────
def test_inject_external_stress_positive_value_adds():
    from host_brain.tribe.interoception_agent import InteroceptionAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {"recovery_urgency": 0.30}}
    captured = {}

    def fake_set_state(ns, state, writer=None):
        captured["state"] = state

    registry.set_state.side_effect = fake_set_state

    agent = InteroceptionAgent(state_registry=registry)
    agent.inject_external_stress(source="test_positive", intensity=0.40)

    assert "state" in captured
    new_urgency = captured["state"]["recovery_urgency"]
    # 0.30 + 0.40 * 0.35 = 0.30 + 0.14 = 0.44
    assert abs(new_urgency - 0.44) < 0.01, f"Expected ~0.44, got {new_urgency}"


# ─────────────────────────────────────────────────────────────────────────────
# 场景 4：PeripheralAgent._on_platform_connected 调用 reduce_recovery_urgency
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_peripheral_connected_calls_reduce_recovery_urgency():
    from host_brain.tribe.peripheral_agent import PeripheralAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = PeripheralAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()

    event = MagicMock()
    event.payload = {"platform": "qq"}
    event.ts_ms = 1000

    with patch(
        "host_brain.tribe.peripheral_agent.get_interoception_agent",
        return_value=mock_intero,
    ):
        await agent._on_platform_connected(event)

    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args.kwargs
    assert "peripheral_connected" in call_kwargs["source"]
    assert call_kwargs["relief"] > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 场景 5：ANSAgent.export_runtime_state 写入 state_registry
# ─────────────────────────────────────────────────────────────────────────────
def test_ans_export_runtime_state_writes_registry():
    from host_brain.tribe.ans_agent import ANSAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.set_state = MagicMock()
    # 确保走 set_state 分支而非 write 分支（spec 中有 write 属性，需要删除）
    del registry.write

    agent = ANSAgent()
    agent.state_registry = registry
    agent._bond_stage = "friend"
    agent._recovery_mode = False

    result = agent.export_runtime_state()

    registry.set_state.assert_called_once()
    call_args = registry.set_state.call_args
    assert call_args[0][0] == "ans_agent"
    assert result["bond_stage"] == "friend"
    assert result["recovery_mode"] is False
    assert "proactive_multiplier" in result
    assert result["loop_registered"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 场景 6：ANSAgent recovery_mode=True 时 proactive_multiplier 降至 35%
# ─────────────────────────────────────────────────────────────────────────────
def test_ans_recovery_mode_reduces_proactive_multiplier():
    from host_brain.tribe.ans_agent import ANSAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.set_state = MagicMock()

    agent = ANSAgent()
    agent.state_registry = registry
    agent._bond_stage = "friend"
    agent._recovery_mode = True

    result = agent.export_runtime_state()

    # friend 基础 multiplier = 0.85，recovery_mode 时 * 0.35 ≈ 0.2975
    assert result["proactive_multiplier"] < 0.40
    assert result["recovery_mode"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 场景 7：GlymphaticAgent 恢复周期完成后联动 LimbicAgent 正向情绪偏移
# ─────────────────────────────────────────────────────────────────────────────
def test_glymphatic_recovery_triggers_limbic_positive_emotion():
    from host_brain.tribe.glymphatic_agent import GlymphaticAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {"cycle_count": 0}}
    registry.set_state = MagicMock()
    registry.aggregate_vital_signs.return_value = {
        "circadian_phase": "night",
        "sleep_pressure": 0.80,
        "stress_index": 0.20,
        "cognitive_load_index": 0.20,
        "loneliness_level": 0.10,
        "energy_reserve": 0.70,
        "homeostasis_ok": True,
    }
    registry.get_homeostasis_report.return_value = {"status": "stable"}

    agent = GlymphaticAgent(state_registry=registry)

    mock_hippo = MagicMock()
    mock_hippo.resolve_memory_layer_snapshot.return_value = {"last_write_type": "semantic"}
    mock_hippo.write_semantic_memory_fact = MagicMock()

    mock_dma = MagicMock()
    mock_dma.export_runtime_state.return_value = {"continuity_score": 0.70}

    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with (
        patch("host_brain.tribe.glymphatic_agent.get_hippocampus_agent", return_value=mock_hippo),
        patch("host_brain.tribe.glymphatic_agent.get_default_mode_agent", return_value=mock_dma),
        patch("host_brain.tribe.glymphatic_agent.get_limbic_agent", return_value=mock_limbic),
        patch("host_brain.tribe.glymphatic_agent.emit_tribe_event"),
    ):
        result = agent.run_recovery_cycle(source="test", force=True)

    assert result["executed"] is True
    assert result["consolidation_score"] > 0.0
    mock_limbic.drive_emotion_metabolism.assert_called_once()
    call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
    assert call_kwargs["valence"] > 0.0  # 正向情绪偏移
    assert "glymphatic_recovery" in call_kwargs["event_kind"]


# ─────────────────────────────────────────────────────────────────────────────
# 场景 8：HippocampusAgent.write_semantic_memory_fact 后联动 DefaultModeAgent
# ─────────────────────────────────────────────────────────────────────────────
def test_hippocampus_semantic_write_boosts_continuity():
    from host_brain.tribe.hippocampus_agent import HippocampusAgent

    registry = MagicMock()
    registry.get_state.return_value = {
        "state": {
            "memory_layers": {},
            "last_offline_state": {"continuity_score": 0.50},
        }
    }
    registry.write = MagicMock()
    registry.set_state = MagicMock()

    agent = HippocampusAgent(state_registry=registry)

    mock_arbiter = MagicMock()
    mock_arbiter.write.return_value = MagicMock(
        status="OK", written=True, state_version=1,
        reason_code=None, detail=None, payload={},
    )

    dma_registry = MagicMock()
    dma_registry.get_state.return_value = {
        "state": {"last_offline_state": {"continuity_score": 0.50}}
    }
    dma_captured = {}

    def dma_set_state(ns, state, writer=None):
        dma_captured["state"] = state

    dma_registry.set_state.side_effect = dma_set_state

    mock_dma = MagicMock()
    mock_dma.state_registry = dma_registry

    with (
        patch("host_brain.tribe.hippocampus_agent.get_state_write_arbiter", return_value=mock_arbiter),
        patch("host_brain.tribe.hippocampus_agent.get_default_mode_agent", return_value=mock_dma),
    ):
        agent.write_semantic_memory_fact(person_id="user_1", fact="test fact")

    assert "state" in dma_captured
    new_continuity = dma_captured["state"]["last_offline_state"]["continuity_score"]
    assert new_continuity > 0.50, f"Expected continuity > 0.50, got {new_continuity}"


# ─────────────────────────────────────────────────────────────────────────────
# 场景 9：CircadianAgent day 相位切换时联动 ANSAgent recovery_mode=False
# ─────────────────────────────────────────────────────────────────────────────
def test_circadian_day_phase_resets_ans_recovery_mode():
    from host_brain.tribe.circadian_agent import CircadianAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {"phase": "night"}}
    registry.set_state = MagicMock()

    agent = CircadianAgent(state_registry=registry)
    agent._last_phase = "night"

    mock_ans = MagicMock()
    mock_ans._recovery_mode = True
    # set_recovery_mode 被调用时同步修改 _recovery_mode
    def _fake_set_recovery_mode(enabled, **kw):
        mock_ans._recovery_mode = enabled
    mock_ans.set_recovery_mode.side_effect = _fake_set_recovery_mode

    with (
        patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=mock_ans),
        patch("host_brain.tribe.circadian_agent.emit_tribe_event"),
        patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=MagicMock()),
        patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=MagicMock()),
        patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=MagicMock()),
        patch("host_brain.tribe.circadian_agent.get_pain_reward_agent", return_value=MagicMock()),
        patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile", return_value={
            "phase": "day", "sleep_pressure": 0.20, "local_hour": 10,
            "phase_energy_baseline": 0.84, "proactive_multiplier": 1.0,
            "recovery_bias": 0.30, "response_latency_ms": 360, "ts_ms": 1000,
        }),
    ):
        agent.refresh_state(source="test", ts_ms=1000)

    assert mock_ans._recovery_mode is False


# ─────────────────────────────────────────────────────────────────────────────
# 场景 10：SomaticAgent 高疲劳(>=0.60)时联动 InteroceptionAgent.inject_external_stress
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_somatic_high_fatigue_triggers_interoception_stress():
    from host_brain.tribe.somatic_agent import SomaticAgent

    registry = MagicMock()
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = SomaticAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()

    event = MagicMock()
    event.payload = {"fatigue_level": 0.75, "motion_level": 0.30}
    event.ts_ms = 1000

    with patch(
        "host_brain.tribe.somatic_agent.get_interoception_agent",
        return_value=mock_intero,
    ):
        await agent._on_body_sense_updated(event)

    mock_intero.inject_external_stress.assert_called_once()
    call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
    assert "somatic_high_fatigue" in call_kwargs["source"]
    assert call_kwargs["intensity"] > 0.0
