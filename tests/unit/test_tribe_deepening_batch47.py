"""T-370：第四十七批联动机制单元测试（T-359 ~ T-369）。

覆盖场景：
- T-359: SomaticAgent 极度疲劳（>=0.75）时联动 PainRewardAgent.record_pain()
- T-360: EndocrineAgent 极高 cortisol（>=0.80）时联动 PainRewardAgent.record_pain()
- T-361: CircadianAgent 夜间高睡眠压力（>=0.70）时联动 PainRewardAgent.record_pain()
- T-362: MirrorNeuronAgent 高正向共情时联动 PainRewardAgent.record_reward()
- T-363: ImmuneAgent 安全通过（score<0.15）时联动 PainRewardAgent.record_reward()
- T-364: ANSAgent 退出恢复模式时联动 LimbicAgent.drive_emotion_metabolism()
- T-365: DefaultModeAgent 高连续性时联动 LimbicAgent.drive_emotion_metabolism()
- T-366: CircadianAgent 日间相位时联动 LimbicAgent.drive_emotion_metabolism()
- T-367: ProprioceptionAgent 任务成功时联动 DefaultModeAgent.tick_idle_cycle()
- T-368: InitiativeGovernorAgent APPROVE 时联动 DefaultModeAgent.tick_idle_cycle()
- T-369: CortexAgent 对话结束时联动 PainRewardAgent.record_reward()
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── T-359 ──────────────────────────────────────────────────────────────────

class TestT359SomaticExtremeFatiguePain:
    """SomaticAgent 极度疲劳（>=0.75）时联动 PainRewardAgent.record_pain()"""

    def test_extreme_fatigue_records_pain(self):
        from host_brain.tribe.somatic_agent import SomaticAgent
        agent = SomaticAgent.__new__(SomaticAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}

        mock_pr = MagicMock()
        mock_intero = MagicMock()
        mock_endo = MagicMock()
        with patch("host_brain.tribe.somatic_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.somatic_agent.get_endocrine_agent", return_value=mock_endo):
            import asyncio
            asyncio.run(agent._on_body_sense_updated(
                MagicMock(payload={"fatigue_level": 0.85, "ts_ms": 1000}, ts_ms=1000)
            ))

        mock_pr.record_pain.assert_called_once()
        call_kwargs = mock_pr.record_pain.call_args.kwargs
        assert "somatic_extreme_fatigue" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0

    def test_moderate_fatigue_no_pain(self):
        from host_brain.tribe.somatic_agent import SomaticAgent
        agent = SomaticAgent.__new__(SomaticAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}

        mock_pr = MagicMock()
        mock_intero = MagicMock()
        mock_endo = MagicMock()
        with patch("host_brain.tribe.somatic_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.somatic_agent.get_endocrine_agent", return_value=mock_endo):
            import asyncio
            asyncio.run(agent._on_body_sense_updated(
                MagicMock(payload={"fatigue_level": 0.65, "ts_ms": 1000}, ts_ms=1000)
            ))

        mock_pr.record_pain.assert_not_called()


# ── T-360 ──────────────────────────────────────────────────────────────────

class TestT360EndocrineHighCortisolPain:
    """EndocrineAgent 极高 cortisol（>=0.80）时联动 PainRewardAgent.record_pain()"""

    def test_extreme_cortisol_records_pain(self):
        from host_brain.tribe.endocrine_agent import EndocrineAgent
        import threading
        agent = EndocrineAgent.__new__(EndocrineAgent)
        agent._bond_lock = threading.Lock()
        agent._bond_cortisol_delta = 0.0
        agent._bond_oxytocin_delta = 0.0
        agent._last_state = {}

        mock_pr = MagicMock()
        # net_stress_delta=0 避免 cortisol 被拉低
        mock_pr.get_stress_signal.return_value = {"net_stress_delta": 0.0}
        mock_limbic = MagicMock()
        mock_intero = MagicMock()
        with patch("host_brain.tribe.endocrine_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.endocrine_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.endocrine_agent.get_interoception_agent", return_value=mock_intero):
            # energy_level=0 + mood_state=critical + sys_mode=locked 产生极高 cortisol
            agent.resolve_endocrine_state(
                hard_state={"energy_level": 0, "mood_state": "critical", "sys_mode": "locked"}
            )

        mock_pr.record_pain.assert_called_once()
        call_kwargs = mock_pr.record_pain.call_args.kwargs
        assert "endocrine_high_cortisol" in call_kwargs["source"]

    def test_moderate_cortisol_no_pain(self):
        from host_brain.tribe.endocrine_agent import EndocrineAgent
        import threading
        agent = EndocrineAgent.__new__(EndocrineAgent)
        agent._bond_lock = threading.Lock()
        agent._bond_cortisol_delta = 0.0
        agent._bond_oxytocin_delta = 0.0
        agent._last_state = {}

        mock_pr = MagicMock()
        mock_pr.get_stress_signal.return_value = {"net_stress_delta": 0.0}
        mock_limbic = MagicMock()
        mock_intero = MagicMock()
        with patch("host_brain.tribe.endocrine_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.endocrine_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.endocrine_agent.get_interoception_agent", return_value=mock_intero):
            # energy_level=60 + mood_state=steady 产生中等 cortisol，不达到 0.80
            agent.resolve_endocrine_state(
                hard_state={"energy_level": 60, "mood_state": "steady", "sys_mode": "normal"}
            )

        mock_pr.record_pain.assert_not_called()


# ── T-361 ──────────────────────────────────────────────────────────────────

class TestT361CircadianNightSleepPressurePain:
    """CircadianAgent 夜间高睡眠压力（>=0.70）时联动 PainRewardAgent.record_pain()"""

    def test_night_high_sleep_pressure_records_pain(self):
        from host_brain.tribe.circadian_agent import CircadianAgent
        agent = CircadianAgent.__new__(CircadianAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent._last_phase = "dusk"  # 上一个相位是 dusk，切换到 night 才触发

        mock_pr = MagicMock()
        mock_limbic = MagicMock()
        mock_intero = MagicMock()
        mock_ans = MagicMock()
        # mock evaluate_profile 返回夜间高睡眠压力
        with patch("host_brain.tribe.circadian_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.circadian_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=mock_ans), \
             patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.emit_tribe_event"), \
             patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile", return_value={
                 "phase": "night", "sleep_pressure": 0.80, "local_hour": 2,
                 "phase_energy_baseline": 0.20, "proactive_multiplier": 0.40,
                 "recovery_bias": 0.80, "response_latency_ms": 600, "ts_ms": 1000,
             }):
            agent.refresh_state(source="test", ts_ms=1000)

        # record_pain 应被调用（夜间高睡眠压力）
        pain_calls = [c for c in mock_pr.record_pain.call_args_list
                      if "sleep_pressure" in str(c)]
        assert len(pain_calls) >= 1

    def test_night_low_sleep_pressure_no_pain(self):
        from host_brain.tribe.circadian_agent import CircadianAgent
        agent = CircadianAgent.__new__(CircadianAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent._last_phase = "dusk"

        mock_pr = MagicMock()
        mock_limbic = MagicMock()
        mock_intero = MagicMock()
        mock_ans = MagicMock()
        with patch("host_brain.tribe.circadian_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.circadian_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=mock_ans), \
             patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.emit_tribe_event"), \
             patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile", return_value={
                 "phase": "night", "sleep_pressure": 0.40, "local_hour": 2,
                 "phase_energy_baseline": 0.20, "proactive_multiplier": 0.40,
                 "recovery_bias": 0.80, "response_latency_ms": 600, "ts_ms": 1000,
             }):
            agent.refresh_state(source="test", ts_ms=1000)

        pain_calls = [c for c in mock_pr.record_pain.call_args_list
                      if "sleep_pressure" in str(c)]
        assert len(pain_calls) == 0


# ── T-362 ──────────────────────────────────────────────────────────────────

class TestT362MirrorPositiveEmpathyReward:
    """MirrorNeuronAgent 高正向共情时联动 PainRewardAgent.record_reward()"""

    def test_high_joy_resonance_records_reward(self):
        from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
        agent = MirrorNeuronAgent.__new__(MirrorNeuronAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent.state_registry.aggregate_vital_signs = MagicMock(return_value={})

        mock_pr = MagicMock()
        mock_endo = MagicMock()
        mock_intero = MagicMock()
        mock_social = MagicMock()
        # 提供高 bond 值，确保 resonance_strength >= 0.60
        mock_social.get_bond_context.return_value = {
            "bond_vector": {"trust": 0.9, "affinity": 0.9, "safety": 0.9, "repair_confidence": 0.9}
        }
        mock_limbic = MagicMock()
        mock_dma = MagicMock()
        with patch("host_brain.tribe.mirror_neuron_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", return_value=mock_endo), \
             patch("host_brain.tribe.mirror_neuron_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", return_value=mock_social), \
             patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.mirror_neuron_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.mirror_neuron_agent.emit_tribe_event"):
            agent.apply_resonance(
                user_id="user_1",
                emotion_tag="JOY",
                emotion_intensity=0.75,
                source="test",
            )

        mock_pr.record_reward.assert_called_once()
        call_kwargs = mock_pr.record_reward.call_args.kwargs
        assert "empathy_joy" in call_kwargs["source"]

    def test_high_sad_resonance_no_reward(self):
        from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
        agent = MirrorNeuronAgent.__new__(MirrorNeuronAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent.state_registry.aggregate_vital_signs = MagicMock(return_value={})

        mock_pr = MagicMock()
        mock_endo = MagicMock()
        mock_intero = MagicMock()
        mock_social = MagicMock()
        mock_social.get_bond_context.return_value = {
            "bond_vector": {"trust": 0.9, "affinity": 0.9, "safety": 0.9, "repair_confidence": 0.9}
        }
        mock_limbic = MagicMock()
        mock_dma = MagicMock()
        with patch("host_brain.tribe.mirror_neuron_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", return_value=mock_endo), \
             patch("host_brain.tribe.mirror_neuron_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", return_value=mock_social), \
             patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.mirror_neuron_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.mirror_neuron_agent.emit_tribe_event"):
            agent.apply_resonance(
                user_id="user_1",
                emotion_tag="SAD",
                emotion_intensity=0.75,
                source="test",
            )

        mock_pr.record_reward.assert_not_called()


# ── T-363 ──────────────────────────────────────────────────────────────────

class TestT363ImmunesSafePassReward:
    """ImmuneAgent 安全通过（score<0.15）时联动 PainRewardAgent.record_reward()"""

    def test_safe_pass_records_reward(self):
        from host_brain.tribe.immune_agent import ImmuneAgent
        from host_brain.tribe.tribe_state import TribeStateRegistry
        # 使用完整构造，避免 slots 属性问题
        agent = ImmuneAgent(state_registry=TribeStateRegistry())

        mock_pr = MagicMock()
        mock_limbic = MagicMock()
        mock_endo = MagicMock()
        with patch("host_brain.tribe.immune_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.immune_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.immune_agent.get_endocrine_agent", return_value=mock_endo):
            # inspect_text 是关键字参数方法
            agent.inspect_text(text="hello world", source="chat")

        mock_pr.record_reward.assert_called_once()
        call_kwargs = mock_pr.record_reward.call_args.kwargs
        assert call_kwargs["source"] == "immune_safe_pass"


# ── T-364 ──────────────────────────────────────────────────────────────────

class TestT364ANSRecoveryExitEmotionMetabolism:
    """ANSAgent 退出恢复模式时联动 LimbicAgent.drive_emotion_metabolism()"""

    def test_recovery_exit_triggers_emotion(self):
        from host_brain.tribe.ans_agent import ANSAgent
        agent = ANSAgent.__new__(ANSAgent)
        agent._recovery_mode = True  # 初始为恢复模式
        agent._bond_stage = "friend"
        # slots=True 不能直接赋值 emit_from_thread，用 patch 替代
        mock_limbic = MagicMock()
        with patch("host_brain.tribe.ans_agent.get_limbic_agent", return_value=mock_limbic), \
             patch.object(ANSAgent, "emit_from_thread"):
            agent.set_recovery_mode(False, source="circadian_day_phase")

        mock_limbic.drive_emotion_metabolism.assert_called_once()
        call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
        assert call_kwargs["valence"] > 0
        assert "ans_recovery_exit" in call_kwargs["event_kind"]

    def test_no_change_no_emotion(self):
        from host_brain.tribe.ans_agent import ANSAgent
        agent = ANSAgent.__new__(ANSAgent)
        agent._recovery_mode = False  # 已经是非恢复模式
        agent._bond_stage = "friend"

        mock_limbic = MagicMock()
        with patch("host_brain.tribe.ans_agent.get_limbic_agent", return_value=mock_limbic), \
             patch.object(ANSAgent, "emit_from_thread"):
            agent.set_recovery_mode(False, source="test")  # 无变化

        mock_limbic.drive_emotion_metabolism.assert_not_called()


# ── T-365 ──────────────────────────────────────────────────────────────────

class TestT365DefaultModeHighContinuityEmotion:
    """DefaultModeAgent 高连续性时联动 LimbicAgent.drive_emotion_metabolism()"""

    def test_high_continuity_triggers_emotion(self, monkeypatch):
        monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
        from host_brain.tribe.default_mode_agent import DefaultModeAgent
        agent = DefaultModeAgent.__new__(DefaultModeAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}

        mock_limbic = MagicMock()
        mock_pr = MagicMock()
        mock_intero = MagicMock()
        # build_offline_state_from_inner_state 决定 continuity_score，需要 mock 它
        with patch("host_brain.tribe.default_mode_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.default_mode_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.default_mode_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.default_mode_agent.get_glymphatic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.default_mode_agent.get_ans_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.default_mode_agent.emit_tribe_event"), \
             patch("host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
                   return_value={"continuity_score": 0.82, "absence_band": "recent",
                                 "offline_hours": 0.0, "loneliness_level": 0.0, "anticipation_level": 0.0}):
            agent.tick_idle_cycle(source="test", person_id="user_1")

        mock_limbic.drive_emotion_metabolism.assert_called_once()
        call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
        assert call_kwargs["valence"] > 0
        assert "default_mode_continuity" in call_kwargs["event_kind"]


# ── T-366 ──────────────────────────────────────────────────────────────────

class TestT366CircadianDayPhaseEmotion:
    """CircadianAgent 日间相位时联动 LimbicAgent.drive_emotion_metabolism()"""

    def test_day_phase_triggers_emotion(self):
        from host_brain.tribe.circadian_agent import CircadianAgent
        agent = CircadianAgent.__new__(CircadianAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent._last_phase = "dawn"  # 上一个相位是 dawn，切换到 day 才触发

        mock_limbic = MagicMock()
        mock_pr = MagicMock()
        mock_intero = MagicMock()
        mock_ans = MagicMock()
        with patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=mock_limbic), \
             patch("host_brain.tribe.circadian_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.circadian_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=mock_ans), \
             patch("host_brain.tribe.circadian_agent.get_default_mode_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.emit_tribe_event"), \
             patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile", return_value={
                 "phase": "day", "sleep_pressure": 0.20, "local_hour": 9,
                 "phase_energy_baseline": 0.84, "proactive_multiplier": 1.0,
                 "recovery_bias": 0.30, "response_latency_ms": 360, "ts_ms": 1000,
             }):
            agent.refresh_state(source="test", ts_ms=1000)

        # drive_emotion_metabolism 应被调用（日间活力情绪）
        wake_calls = [c for c in mock_limbic.drive_emotion_metabolism.call_args_list
                      if "circadian_wake" in str(c)]
        assert len(wake_calls) >= 1


# ── T-367 ──────────────────────────────────────────────────────────────────

class TestT367ProprioceptionTaskSuccessContinuity:
    """ProprioceptionAgent 任务成功时联动 DefaultModeAgent.tick_idle_cycle()"""

    def test_task_success_triggers_tick_idle_cycle(self):
        from host_brain.tribe.proprioception_agent import ProprioceptionAgent
        agent = ProprioceptionAgent.__new__(ProprioceptionAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent._interrupt_ts_ms = []  # slots=True 需要手动初始化

        mock_dma = MagicMock()
        mock_pr = MagicMock()
        mock_limbic = MagicMock()
        with patch("host_brain.tribe.proprioception_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.proprioception_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.proprioception_agent.get_limbic_agent", return_value=mock_limbic):
            agent.on_task_success(intensity=0.70, task_count=2, ts_ms=1000)

        mock_dma.tick_idle_cycle.assert_called_once()
        call_kwargs = mock_dma.tick_idle_cycle.call_args.kwargs
        assert "proprioception_task_success" in call_kwargs["source"]


# ── T-368 ──────────────────────────────────────────────────────────────────

class TestT368InitiativeApprovedContinuity:
    """InitiativeGovernorAgent APPROVE 时联动 DefaultModeAgent.tick_idle_cycle()"""

    def test_approve_triggers_tick_idle_cycle(self):
        from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
        agent = InitiativeGovernorAgent.__new__(InitiativeGovernorAgent)
        agent.state_registry = MagicMock()
        agent.state_registry.get_state.return_value = {}
        agent._recovery_mode = False

        mock_dma = MagicMock()
        mock_pr = MagicMock()
        mock_vest = MagicMock()
        mock_vest.state_registry = MagicMock()
        mock_vest.state_registry.get_state.return_value = {}
        mock_circadian = MagicMock()
        mock_circadian.export_runtime_state.return_value = {
            "proactive_multiplier": 1.0, "sleep_pressure": 0.20
        }
        mock_social = MagicMock()
        mock_social.get_bond_context.return_value = {
            "bond_vector": {"trust": 0.8, "affinity": 0.8, "safety": 0.8}
        }
        mock_intero = MagicMock()
        mock_intero.export_runtime_state.return_value = {"body_budget": 0.80}
        mock_proprio = MagicMock()
        mock_proprio.export_runtime_state.return_value = {
            "cognitive_load_index": 0.10, "continuity_hint": 0.60
        }
        # aggregate_vital_signs 返回高能量、低压力、开放闸门
        agent.state_registry.aggregate_vital_signs = MagicMock(return_value={
            "energy_reserve": 0.80, "stress_index": 0.10,
            "gate_state": "open", "immune_risk_level": "none"
        })
        with patch("host_brain.tribe.initiative_governor_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.initiative_governor_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.initiative_governor_agent.get_vestibular_agent", return_value=mock_vest), \
             patch("host_brain.tribe.initiative_governor_agent.get_circadian_agent", return_value=mock_circadian), \
             patch("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", return_value=mock_social), \
             patch("host_brain.tribe.initiative_governor_agent.get_interoception_agent", return_value=mock_intero), \
             patch("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", return_value=mock_proprio):
            agent.evaluate_initiative(
                action="proactive_greeting",
                motive=0.75,
                context={"readiness_score": 0.80, "continuity_score": 0.65},
            )

        mock_dma.tick_idle_cycle.assert_called_once()
        call_kwargs = mock_dma.tick_idle_cycle.call_args.kwargs
        assert "initiative_approved" in call_kwargs["source"]


# ── T-369 ──────────────────────────────────────────────────────────────────

class TestT369CortexConversationEndedReward:
    """CortexAgent 对话结束时联动 PainRewardAgent.record_reward()"""

    def test_positive_emotion_end_records_reward(self):
        from host_brain.tribe.cortex_agent import CortexAgent
        agent = CortexAgent.__new__(CortexAgent)

        mock_pr = MagicMock()
        with patch("host_brain.tribe.cortex_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            agent.emit_conversation_ended(
                user_id="user_1",
                session_id="sess_1",
                emotion_tag="JOY",
                emotion_intensity=0.80,
            )

        mock_pr.record_reward.assert_called_once()
        call_kwargs = mock_pr.record_reward.call_args.kwargs
        assert "cortex_conversation_ended" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0

    def test_neutral_emotion_end_records_lower_reward(self):
        from host_brain.tribe.cortex_agent import CortexAgent
        agent = CortexAgent.__new__(CortexAgent)

        mock_pr = MagicMock()
        with patch("host_brain.tribe.cortex_agent.get_pain_reward_agent", return_value=mock_pr), \
             patch("host_brain.tribe.cortex_agent.emit_tribe_event"):
            agent.emit_conversation_ended(
                user_id="user_1",
                session_id="sess_1",
                emotion_tag="NEUTRAL",
                emotion_intensity=0.50,
            )

        mock_pr.record_reward.assert_called_once()
        call_kwargs = mock_pr.record_reward.call_args.kwargs
        # NEUTRAL 情绪奖励应低于正向情绪
        assert call_kwargs["intensity"] <= 0.22
