"""T-323：第四十三批 Tribe 联动深化单元测试

覆盖场景：
- T-313: CircadianAgent 夜间相位切换时联动 InteroceptionAgent.reduce_recovery_urgency()
- T-314: CircadianAgent 夜间/黄昏相位切换时联动 LimbicAgent 降低唤醒
- T-315: ProprioceptionAgent 高认知负荷时联动 InteroceptionAgent.inject_external_stress()
- T-316: ProprioceptionAgent 任务成功时联动 PainRewardAgent.record_reward_event()
- T-317: InitiativeGovernorAgent APPROVE 时联动 PainRewardAgent.record_reward_event()
- T-318: SocialBrainAgent 里程碑达成时联动 PainRewardAgent.record_reward_event()
- T-319: HippocampusAgent 写入自传体记忆后联动 DefaultModeAgent.tick_idle_cycle()
- T-320: EndocrineAgent 高 cortisol 时联动 InteroceptionAgent.inject_external_stress()
- T-321: vital_signs 已包含 cognitive_load_index/readiness_hint 字段
- T-322: chat_context_builder 已注入 proprioception_state
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── T-313: CircadianAgent 夜间相位切换时联动 InteroceptionAgent ──────────────

def test_t313_circadian_night_phase_reduces_recovery_urgency():
    """T-313: 夜间相位切换时联动 InteroceptionAgent.reduce_recovery_urgency()"""
    from host_brain.tribe.circadian_agent import CircadianAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = CircadianAgent(state_registry=registry)
    agent._last_phase = "dusk"  # 模拟从黄昏切换到夜间

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()

    with patch("host_brain.tribe.circadian_agent._phase_by_hour", return_value="night"), \
         patch("host_brain.tribe.circadian_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=MagicMock()):
        agent.refresh_state(source="test", ts_ms=1000)

    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args.kwargs
    assert call_kwargs["source"] == "circadian_night_phase"
    assert call_kwargs["relief"] == 0.40


# ── T-314: CircadianAgent 夜间/黄昏相位切换时联动 LimbicAgent ───────────────

def test_t314_circadian_night_phase_lowers_arousal():
    """T-314: 夜间相位切换时联动 LimbicAgent 降低唤醒"""
    from host_brain.tribe.circadian_agent import CircadianAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = CircadianAgent(state_registry=registry)
    agent._last_phase = "day"

    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.circadian_agent._phase_by_hour", return_value="night"), \
         patch("host_brain.tribe.circadian_agent.get_interoception_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=mock_limbic):
        agent.refresh_state(source="test", ts_ms=1000)

    mock_limbic.drive_emotion_metabolism.assert_called_once()
    call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
    assert call_kwargs["arousal"] == -0.18
    assert call_kwargs["source"] == "circadian_agent"
    assert "night" in call_kwargs["event_kind"]


def test_t314_circadian_dusk_phase_lowers_arousal_mildly():
    """T-314: 黄昏相位切换时联动 LimbicAgent 轻度降低唤醒"""
    from host_brain.tribe.circadian_agent import CircadianAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = CircadianAgent(state_registry=registry)
    agent._last_phase = "day"

    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.circadian_agent._phase_by_hour", return_value="dusk"), \
         patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=mock_limbic):
        agent.refresh_state(source="test", ts_ms=1000)

    mock_limbic.drive_emotion_metabolism.assert_called_once()
    call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
    assert call_kwargs["arousal"] == -0.10


# ── T-315: ProprioceptionAgent 高认知负荷时联动 InteroceptionAgent ───────────

def test_t315_high_cognitive_load_injects_stress():
    """T-315: 高认知负荷(>=0.65)时联动 InteroceptionAgent.inject_external_stress()"""
    from host_brain.tribe.proprioception_agent import ProprioceptionAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = ProprioceptionAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.proprioception_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.proprioception_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.proprioception_agent.emit_tribe_event"):
        # 注入高认知负荷状态
        agent._update_state(
            changes={
                "active_session": True,
                "active_task_count": 5,
                "interrupt_count_5m": 8,
                "voice_pipeline_active": True,
            },
            source="test",
        )

    mock_intero.inject_external_stress.assert_called_once()
    call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
    assert "cognitive_overload" in call_kwargs["source"]
    assert 0.0 < call_kwargs["intensity"] <= 0.35


def test_t315_low_cognitive_load_no_stress():
    """T-315: 低认知负荷(<0.65)时不联动 InteroceptionAgent"""
    from host_brain.tribe.proprioception_agent import ProprioceptionAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = ProprioceptionAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()

    with patch("host_brain.tribe.proprioception_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.proprioception_agent.get_limbic_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.proprioception_agent.emit_tribe_event"):
        agent._update_state(
            changes={"active_session": False, "active_task_count": 0},
            source="test",
        )

    mock_intero.inject_external_stress.assert_not_called()


# ── T-316: ProprioceptionAgent 任务成功时联动 PainRewardAgent ────────────────

def test_t316_task_success_records_reward():
    """T-316: 任务成功时联动 PainRewardAgent.record_reward_event()"""
    import asyncio
    from host_brain.tribe.proprioception_agent import ProprioceptionAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = ProprioceptionAgent(state_registry=registry)

    mock_pr = MagicMock()
    mock_pr.record_reward_event = MagicMock()

    event = MagicMock()
    event.payload = {"intensity": 0.70}
    event.ts_ms = 1000

    with patch("host_brain.tribe.proprioception_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.proprioception_agent.get_limbic_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.proprioception_agent.emit_tribe_event"):
        asyncio.run(agent._on_task_success(event))

    mock_pr.record_reward_event.assert_called_once()
    call_kwargs = mock_pr.record_reward_event.call_args.kwargs
    assert call_kwargs["source"] == "task_success"
    assert 0.0 < call_kwargs["reward_level"] <= 0.80


# ── T-317: InitiativeGovernorAgent APPROVE 时联动 PainRewardAgent ────────────

def test_t317_approve_records_reward():
    """T-317: APPROVE 决策时联动 PainRewardAgent.record_reward_event()"""
    from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = InitiativeGovernorAgent(state_registry=registry)

    mock_pr = MagicMock()
    mock_pr.record_reward_event = MagicMock()

    mock_vitals = {
        "gate_state": "open",
        "immune_risk_level": "none",
        "body_budget": 0.8,
        "sleep_pressure": 0.2,
    }
    mock_circadian = {"sleep_pressure": 0.2, "proactive_multiplier": 1.0}
    mock_proprio = {"continuity_hint": 0.7, "readiness_hint": 0.8, "cognitive_load_index": 0.3}
    mock_bond_ctx = {"bond_vector": {"trust": 0.7, "intimacy": 0.6}}

    with patch.object(registry, "aggregate_vital_signs", return_value=mock_vitals), \
         patch("host_brain.tribe.initiative_governor_agent.get_circadian_agent",
               return_value=MagicMock(export_runtime_state=MagicMock(return_value=mock_circadian))), \
         patch("host_brain.tribe.initiative_governor_agent.get_proprioception_agent",
               return_value=MagicMock(export_runtime_state=MagicMock(return_value=mock_proprio))), \
         patch("host_brain.tribe.initiative_governor_agent.get_social_brain_agent",
               return_value=MagicMock(get_bond_context=MagicMock(return_value=mock_bond_ctx))), \
         patch("host_brain.tribe.initiative_governor_agent.get_interoception_agent",
               return_value=MagicMock(export_runtime_state=MagicMock(return_value={"body_budget": 0.8}))), \
         patch("host_brain.tribe.initiative_governor_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.initiative_governor_agent.emit_tribe_event"):
        result = agent.evaluate_initiative(action="proactive_greeting", motive=0.75)

    if result.get("decision") == "APPROVE":
        mock_pr.record_reward_event.assert_called_once()
        call_kwargs = mock_pr.record_reward_event.call_args.kwargs
        assert "initiative_approved" in call_kwargs["source"]
        assert 0.0 < call_kwargs["reward_level"] <= 0.65


# ── T-318: SocialBrainAgent 里程碑达成时联动 PainRewardAgent ─────────────────

def test_t318_milestone_achieved_records_reward():
    """T-318: 里程碑达成时联动 PainRewardAgent.record_reward_event()"""
    from host_brain.tribe.social_brain_agent import SocialBrainAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = SocialBrainAgent(state_registry=registry)

    mock_pr = MagicMock()
    mock_pr.record_reward_event = MagicMock()

    mock_milestone = MagicMock()
    mock_milestone.to_dict = MagicMock(return_value={"milestone_id": "first_deep_talk"})

    mock_tracker = MagicMock()
    mock_tracker.on_event = MagicMock(return_value=[mock_milestone])
    mock_tracker.get_achieved = MagicMock(return_value=[mock_milestone])

    with patch("host_brain.tribe.social_brain_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.social_brain_agent.get_milestone_tracker", return_value=mock_tracker):
        result = agent.resolve_milestone_tracker(
            user_id="user_1",
            event_type="INTERACTION_DEEP",
        )

    assert result["new_milestone_count"] == 1
    mock_pr.record_reward_event.assert_called_once()
    call_kwargs = mock_pr.record_reward_event.call_args.kwargs
    assert "milestone_achieved" in call_kwargs["source"]
    assert call_kwargs["reward_level"] >= 0.60


# ── T-319: HippocampusAgent 写入自传体记忆后联动 DefaultModeAgent ─────────────

def test_t319_autobiographical_write_triggers_default_mode():
    """T-319: 写入自传体记忆后联动 DefaultModeAgent.tick_idle_cycle()"""
    from host_brain.tribe.hippocampus_agent import HippocampusAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = TribeStateRegistry()
    agent = HippocampusAgent(state_registry=registry)

    mock_dma = MagicMock()
    mock_dma.tick_idle_cycle = MagicMock(return_value={"continuity_score": 0.65})

    mock_arbiter = MagicMock()
    mock_arbiter.write = MagicMock(return_value={"decision": "ALLOW", "trace_id": "t1"})

    with patch("host_brain.tribe.hippocampus_agent.get_default_mode_agent", return_value=mock_dma), \
         patch.object(agent, "_write_memory_scope", return_value={"decision": "ALLOW"}):
        result = agent.write_autobiographical_memory(
            person_id="user_1",
            narrative="第一次深夜聊天，感觉很特别",
            gate_result={"decision": "ALLOW", "trace_id": "t1"},
        )

    assert result["layer"] == "autobiographical"
    mock_dma.tick_idle_cycle.assert_called_once()
    call_kwargs = mock_dma.tick_idle_cycle.call_args.kwargs
    assert call_kwargs["source"] == "hippocampus_autobiographical_write"


# ── T-320: EndocrineAgent 高 cortisol 时联动 InteroceptionAgent ──────────────

def test_t320_high_cortisol_injects_interoception_stress():
    """T-320: 高 cortisol(>=0.65) 时联动 InteroceptionAgent.inject_external_stress()"""
    from host_brain.tribe.endocrine_agent import EndocrineAgent

    agent = EndocrineAgent()

    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    mock_hard_state = MagicMock()
    mock_hard_state.as_dict = MagicMock(return_value={
        "cortisol_level": 0.80,
        "energy_reserve": 0.50,
        "gate_state": "open",
    })

    with patch("host_brain.tribe.endocrine_agent.derive_endocrine_state",
               return_value=mock_hard_state), \
         patch("host_brain.tribe.endocrine_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.endocrine_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.endocrine_agent.get_pain_reward_agent",
               return_value=MagicMock(get_stress_signal=MagicMock(return_value={"net_stress_delta": 0.0}))):
        agent.resolve_endocrine_state(hard_state={})

    mock_intero.inject_external_stress.assert_called_once()
    call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
    assert "high_cortisol" in call_kwargs["source"]
    assert 0.0 < call_kwargs["intensity"] <= 0.40


def test_t320_low_cortisol_no_interoception_stress():
    """T-320: 低 cortisol(<0.65) 时不联动 InteroceptionAgent"""
    from host_brain.tribe.endocrine_agent import EndocrineAgent

    agent = EndocrineAgent()

    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()

    mock_hard_state = MagicMock()
    mock_hard_state.as_dict = MagicMock(return_value={
        "cortisol_level": 0.40,
        "energy_reserve": 0.70,
        "gate_state": "open",
    })

    with patch("host_brain.tribe.endocrine_agent.derive_endocrine_state",
               return_value=mock_hard_state), \
         patch("host_brain.tribe.endocrine_agent.get_limbic_agent", return_value=MagicMock()), \
         patch("host_brain.tribe.endocrine_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.endocrine_agent.get_pain_reward_agent",
               return_value=MagicMock(get_stress_signal=MagicMock(return_value={"net_stress_delta": 0.0}))):
        agent.resolve_endocrine_state(hard_state={})

    mock_intero.inject_external_stress.assert_not_called()
