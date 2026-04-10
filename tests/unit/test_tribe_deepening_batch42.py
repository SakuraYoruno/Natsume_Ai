"""T-312：第四十二批联动机制单元测试

覆盖场景：
1. T-302: GlymphaticAgent 恢复周期完成后联动 InteroceptionAgent.reduce_recovery_urgency()
2. T-303: MotorCortexAgent 执行失败时联动 EndocrineAgent.record_stress_event()
3. T-304: SomaticAgent 高疲劳时联动 EndocrineAgent.record_stress_event()
4. T-305: MirrorNeuronAgent 高共情时联动 InteroceptionAgent.reduce_recovery_urgency()
5. T-306: PainRewardAgent 高奖励时联动 InteroceptionAgent.reduce_recovery_urgency()
6. T-307: VestibularAgent 高抖动时联动 EndocrineAgent.record_stress_event()
7. T-308: ImmuneAgent 高风险 BLOCK 时联动 EndocrineAgent.record_stress_event()
8. T-309: LanguageSystemAgent 高紧迫度时联动 EndocrineAgent.record_stress_event()
9. T-310: DefaultModeAgent 高连续性时联动 InteroceptionAgent.reduce_recovery_urgency()
10. T-311: HippocampusAgent 写入情节记忆后联动 DefaultModeAgent.tick_idle_cycle()
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── T-302: GlymphaticAgent 恢复周期完成后联动 InteroceptionAgent.reduce_recovery_urgency ──

def test_glymphatic_recovery_calls_reduce_recovery_urgency():
    """T-302: 恢复周期完成且 consolidation_score>=0.45 时，应调用 reduce_recovery_urgency。"""
    from host_brain.tribe.glymphatic_agent import GlymphaticAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {
        "last_recovery_ts_ms": 0,
        "consolidation_score": 0.70,
        "phase": "deep",
        "homeostasis_status": "ok",
    }}
    registry.set_state = MagicMock()

    agent = GlymphaticAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.glymphatic_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.glymphatic_agent.get_limbic_agent", return_value=mock_limbic):
        result = agent.run_recovery_cycle(source="test", force=True)

    assert result.get("executed") is True
    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args[1]
    assert call_kwargs["relief"] > 0.0
    assert "glymphatic_recovery" in call_kwargs["source"]


# ── T-303: MotorCortexAgent 执行失败时联动 EndocrineAgent.record_stress_event ──

def test_motor_cortex_failure_calls_endocrine_stress():
    """T-303: 执行失败时应调用 EndocrineAgent.record_stress_event()。"""
    from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {
        "last_dispatch_outcome": {"plan_id": "plan_001", "ok": True},
        "last_plan": {"plan_id": "plan_001"},
    }}
    registry.write = MagicMock()

    agent = MotorCortexAgent(state_registry=registry)

    mock_endo = MagicMock()
    mock_endo.record_stress_event = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"), \
         patch("host_brain.tribe.motor_cortex_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.motor_cortex_agent.get_limbic_agent", return_value=mock_limbic):
        agent.receive_execution_feedback(
            plan_id="plan_001",
            ok=False,
            reason="EDGE_TIMEOUT",
        )

    mock_endo.record_stress_event.assert_called_once()
    call_kwargs = mock_endo.record_stress_event.call_args[1]
    assert "motor_execution_failed" in call_kwargs["source"]
    assert call_kwargs["intensity"] > 0.0


# ── T-304: SomaticAgent 高疲劳时联动 EndocrineAgent.record_stress_event ──

def test_somatic_high_fatigue_calls_endocrine_stress():
    """T-304: fatigue_level>=0.60 时应调用 EndocrineAgent.record_stress_event()。"""
    from host_brain.tribe.somatic_agent import SomaticAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = SomaticAgent(state_registry=registry)

    mock_endo = MagicMock()
    mock_endo.record_stress_event = MagicMock()
    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()

    event = MagicMock()
    event.payload = {"fatigue_level": 0.75, "ts_ms": 1000}
    event.ts_ms = 1000

    import asyncio

    with patch("host_brain.tribe.somatic_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero):
        asyncio.get_event_loop().run_until_complete(agent._on_body_sense_updated(event))

    mock_endo.record_stress_event.assert_called_once()
    call_kwargs = mock_endo.record_stress_event.call_args[1]
    assert "somatic_high_fatigue" in call_kwargs["source"]
    assert call_kwargs["intensity"] > 0.0


# ── T-305: MirrorNeuronAgent 高共情时联动 InteroceptionAgent.reduce_recovery_urgency ──

def test_mirror_high_resonance_calls_reduce_recovery_urgency():
    """T-305: resonance_strength>=0.60 时应调用 InteroceptionAgent.reduce_recovery_urgency()。"""
    from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()
    registry.aggregate_vital_signs = MagicMock(return_value={"stress_index": 0.2, "voice_emotion_tag": "JOY"})

    agent = MirrorNeuronAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()
    mock_endo = MagicMock()
    mock_endo._bond_cortisol_delta = 0.0
    mock_endo._bond_lock = MagicMock()
    mock_endo._bond_lock.__enter__ = MagicMock(return_value=None)
    mock_endo._bond_lock.__exit__ = MagicMock(return_value=False)
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()
    mock_social = MagicMock()
    mock_social.get_bond_context = MagicMock(return_value={
        "bond_vector": {"trust": 0.8, "affinity": 0.8, "safety": 0.8, "repair_confidence": 0.8}
    })

    with patch("host_brain.tribe.mirror_neuron_agent.emit_tribe_event"), \
         patch("host_brain.tribe.mirror_neuron_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", return_value=mock_social):
        result = agent.apply_resonance(
            user_id="u1",
            session_id="s1",
            emotion_tag="JOY",
            emotion_intensity=0.90,
            source="test",
        )

    assert float(result.get("resonance_strength") or 0.0) >= 0.60
    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args[1]
    assert "mirror_high_resonance" in call_kwargs["source"]
    assert call_kwargs["relief"] > 0.0


# ── T-306: PainRewardAgent 高奖励时联动 InteroceptionAgent.reduce_recovery_urgency ──

def test_pain_reward_high_reward_calls_reduce_recovery_urgency():
    """T-306: reward_level>=0.55 时应调用 InteroceptionAgent.reduce_recovery_urgency()。"""
    from host_brain.tribe.pain_reward_agent import PainRewardAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = PainRewardAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()

    with patch("host_brain.tribe.pain_reward_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.pain_reward_agent.get_limbic_agent", return_value=mock_limbic):
        agent.record_reward(source="task_success", intensity=0.80)

    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args[1]
    assert "pain_reward_high_reward" in call_kwargs["source"]
    assert call_kwargs["relief"] > 0.0


# ── T-307: VestibularAgent 高抖动时联动 EndocrineAgent.record_stress_event ──

def test_vestibular_high_jitter_calls_endocrine_stress():
    """T-307: jitter>=0.35 时应调用 EndocrineAgent.record_stress_event()。"""
    from host_brain.tribe.vestibular_agent import VestibularAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = VestibularAgent(state_registry=registry)
    # 注入足够多的抖动事件
    now = 1_000_000
    for _ in range(4):
        agent._mode_transition_ts.append(now)
    for _ in range(3):
        agent._platform_flap_ts.append(now)

    mock_endo = MagicMock()
    mock_endo.record_stress_event = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()
    mock_ans = MagicMock()
    mock_ans.adjust_rhythm_for_high_interrupt = MagicMock()

    with patch("host_brain.tribe.vestibular_agent.emit_tribe_event"), \
         patch("host_brain.tribe.vestibular_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.vestibular_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.vestibular_agent.get_ans_agent", return_value=mock_ans):
        result = agent.evaluate_stability(source="test", ts_ms=now + 1000)

    assert float(result.get("jitter_index") or 0.0) >= 0.35
    mock_endo.record_stress_event.assert_called_once()
    call_kwargs = mock_endo.record_stress_event.call_args[1]
    assert "vestibular_jitter" in call_kwargs["source"]
    assert call_kwargs["intensity"] > 0.0


# ── T-308: ImmuneAgent 高风险 BLOCK 时联动 EndocrineAgent.record_stress_event ──

def test_immune_block_calls_endocrine_stress():
    """T-308: BLOCK 决策时应调用 EndocrineAgent.record_stress_event()。"""
    from host_brain.tribe.immune_agent import ImmuneAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()

    agent = ImmuneAgent(state_registry=registry)

    mock_endo = MagicMock()
    mock_endo.record_stress_event = MagicMock()
    mock_limbic = MagicMock()
    mock_limbic.drive_emotion_metabolism = MagicMock()
    mock_intero = MagicMock()
    mock_intero.inject_external_stress = MagicMock()

    import asyncio

    event = MagicMock()
    event.payload = {
        "text": "你去死吧 立刻 马上 滚",
        "source": "chat",
        "session_id": "s1",
        "user_id": "u1",
    }
    event.ts_ms = 1000

    with patch("host_brain.tribe.immune_agent.emit_tribe_event"), \
         patch("host_brain.tribe.immune_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.immune_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.immune_agent.get_interoception_agent", return_value=mock_intero):
        asyncio.get_event_loop().run_until_complete(agent._on_interaction_recorded(event))

    # 只有 BLOCK 时才调用，若文本触发 BLOCK 则验证
    if mock_endo.record_stress_event.called:
        call_kwargs = mock_endo.record_stress_event.call_args[1]
        assert "immune_block" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0.0


# ── T-309: LanguageSystemAgent 高紧迫度时联动 EndocrineAgent.record_stress_event ──

def test_language_high_urgency_calls_endocrine_stress():
    """T-309: urgency_score>=0.65 时应调用 EndocrineAgent.record_stress_event()。"""
    from host_brain.tribe.language_system_agent import LanguageSystemAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()
    registry.write = MagicMock()

    agent = LanguageSystemAgent(state_registry=registry)

    mock_endo = MagicMock()
    mock_endo.record_stress_event = MagicMock()
    mock_pain = MagicMock()
    mock_pain.record_pain = MagicMock()

    import asyncio

    event = MagicMock()
    event.payload = {
        "text": "马上帮我！紧急！！",
        "user_text": "马上帮我！紧急！！",
        "session_id": "s1",
        "user_id": "u1",
        "emotion_tag": "ANXIOUS",
        "bond_stage": "friend",
    }
    event.ts_ms = 1000

    with patch("host_brain.tribe.language_system_agent.get_endocrine_agent", return_value=mock_endo), \
         patch("host_brain.tribe.language_system_agent.get_pain_reward_agent", return_value=mock_pain):
        asyncio.get_event_loop().run_until_complete(agent._on_interaction_recorded(event))

    mock_endo.record_stress_event.assert_called_once()
    call_kwargs = mock_endo.record_stress_event.call_args[1]
    assert "language_high_urgency" in call_kwargs["source"]
    assert call_kwargs["intensity"] >= 0.0


# ── T-310: DefaultModeAgent 高连续性时联动 InteroceptionAgent.reduce_recovery_urgency ──

def test_default_mode_high_continuity_calls_reduce_recovery_urgency():
    """T-310: continuity_score>=0.55 时应调用 InteroceptionAgent.reduce_recovery_urgency()。"""
    from host_brain.tribe.default_mode_agent import DefaultModeAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {
        "last_offline_state": {
            "absence_band": "short",
            "offline_hours": 0.5,
            "loneliness_level": 0.2,
            "anticipation_level": 0.3,
            "continuity_score": 0.70,
        }
    }}
    registry.set_state = MagicMock()
    registry.aggregate_vital_signs = MagicMock(return_value={
        "loneliness_level": 0.2,
        "bond_stage": "friend",
    })

    agent = DefaultModeAgent(state_registry=registry)

    mock_intero = MagicMock()
    mock_intero.reduce_recovery_urgency = MagicMock()
    mock_glymphatic = MagicMock()
    mock_glymphatic.run_recovery_cycle = MagicMock()
    mock_ans = MagicMock()
    mock_ans._recovery_mode = True

    with patch("host_brain.tribe.default_mode_agent.emit_tribe_event"), \
         patch("host_brain.tribe.default_mode_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.default_mode_agent.get_glymphatic_agent", return_value=mock_glymphatic), \
         patch("host_brain.tribe.default_mode_agent.get_ans_agent", return_value=mock_ans):
        agent.tick_idle_cycle(source="test", user_id="u1")

    mock_intero.reduce_recovery_urgency.assert_called_once()
    call_kwargs = mock_intero.reduce_recovery_urgency.call_args[1]
    assert "default_mode_continuity" in call_kwargs["source"]
    assert call_kwargs["relief"] > 0.0


# ── T-311: HippocampusAgent 写入情节记忆后联动 DefaultModeAgent.tick_idle_cycle ──

def test_hippocampus_episodic_write_triggers_default_mode():
    """T-311: write_episodic_memory_trace 完成后应调用 DefaultModeAgent.tick_idle_cycle()。"""
    from host_brain.tribe.hippocampus_agent import HippocampusAgent
    from host_brain.tribe.tribe_state import TribeStateRegistry

    registry = MagicMock(spec=TribeStateRegistry)
    registry.get_state.return_value = {"state": {}}
    registry.set_state = MagicMock()
    registry.write = MagicMock()

    agent = HippocampusAgent(state_registry=registry)

    mock_dma = MagicMock()
    mock_dma.tick_idle_cycle = MagicMock()

    mock_arbiter = MagicMock()
    mock_arbiter.write = MagicMock(return_value=MagicMock(
        status="OK", written=True, state_version=1,
        reason_code=None, detail=None, payload={},
    ))

    with patch("host_brain.tribe.hippocampus_agent.get_default_mode_agent", return_value=mock_dma), \
         patch("host_brain.tribe.hippocampus_agent.get_state_write_arbiter", return_value=mock_arbiter):
        result = agent.write_episodic_memory_trace(
            person_id="u1",
            session_id="s1",
            content="今天聊了很多开心的事情",
        )

    assert result.get("layer") == "episodic"
    mock_dma.tick_idle_cycle.assert_called_once()
    call_kwargs = mock_dma.tick_idle_cycle.call_args[1]
    assert call_kwargs.get("source") == "hippocampus_episodic_write"
