"""T-346：第四十五批 Tribe Agent 联动机制单元测试。

覆盖场景：
- T-335: CerebellumAgent 高纠偏策略时联动 PainRewardAgent.record_pain()
- T-336: PrefrontalAgent 漂移告警时联动 PainRewardAgent.record_pain()
- T-337: ImmuneAgent BLOCK 时联动 PainRewardAgent.record_pain()
- T-338: MotorCortexAgent 执行失败时联动 PainRewardAgent.record_pain()
- T-339: GlymphaticAgent 清理完成时联动 PainRewardAgent.record_reward()
- T-340: HippocampusAgent 记忆巩固成功时联动 PainRewardAgent.record_reward()
- T-341: CerebellumAgent NO_ADJUST 时联动 PainRewardAgent.record_reward()
- T-342: CerebellumAgent 持续高纠偏时联动 InteroceptionAgent.inject_external_stress()
- T-343: SocialBrainAgent Bond 退步时联动 InteroceptionAgent.inject_external_stress()
- T-344: HippocampusAgent 自传体记忆写入后联动 InteroceptionAgent.reduce_recovery_urgency()
- T-345: SomaticAgent 低疲劳时联动 InteroceptionAgent.reduce_recovery_urgency()
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


# ── 辅助工厂 ──────────────────────────────────────────────────────────────────

def _make_pain_reward_mock() -> MagicMock:
    m = MagicMock()
    m.record_pain = MagicMock(return_value={"hedonic_state": {}})
    m.record_reward = MagicMock(return_value={"hedonic_state": {}})
    m.record_reward_event = MagicMock(return_value={"hedonic_state": {}})
    return m


def _make_intero_mock() -> MagicMock:
    m = MagicMock()
    m.inject_external_stress = MagicMock(return_value={})
    m.reduce_recovery_urgency = MagicMock(return_value={})
    return m


def _make_registry_mock(correction_count: int = 0) -> MagicMock:
    """构造带 correction_count 的 state_registry mock。"""
    reg = MagicMock()
    reg.get_state = MagicMock(return_value={"state": {"correction_count": correction_count}})
    reg.set_state = MagicMock()
    reg.write = MagicMock()
    reg.aggregate_vital_signs = MagicMock(return_value={
        "circadian_phase": "night",
        "sleep_pressure": 0.70,
        "stress_index": 0.20,
        "cognitive_load_index": 0.20,
        "loneliness_level": 0.10,
        "continuity_hint": 0.60,
    })
    reg.get_homeostasis_report = MagicMock(return_value={"status": "stable"})
    return reg


# ── T-335：CerebellumAgent 高纠偏策略时联动 PainRewardAgent.record_pain() ──────

class TestT335CerebellumHighCorrectionRecordsPain:
    def test_retry_once_calls_record_pain(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        pr_mock = _make_pain_reward_mock()
        reg = _make_registry_mock(correction_count=0)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=_make_intero_mock()):
            agent.evaluate_correction(
                execution_feedback={"accepted": False, "dispatched": False, "latency_ms": 0},
                source="test",
            )

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert "cerebellum_retry_once" in call_kwargs["source"]
        assert call_kwargs["intensity"] == 0.20

    def test_safe_fallback_calls_record_pain_higher_intensity(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        pr_mock = _make_pain_reward_mock()
        reg = _make_registry_mock(correction_count=0)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=_make_intero_mock()):
            agent.evaluate_correction(
                execution_feedback={
                    "accepted": False, "dispatched": False,
                    "latency_ms": 0, "reason": "PUSHER_UNAVAILABLE",
                },
                source="test",
            )

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert call_kwargs["intensity"] == 0.32


# ── T-336：PrefrontalAgent 漂移告警时联动 PainRewardAgent.record_pain() ─────────

class TestT336PrefrontalDriftRecordsPain:
    def test_moderate_drift_calls_record_pain(self) -> None:
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        import asyncio

        pr_mock = _make_pain_reward_mock()
        agent = PrefrontalAgent()

        drift_result = {
            "drift_alert_level": "MODERATE",
            "drift_dims": ["humor"],
            "correction_hint": "tone drift",
        }

        with patch.object(agent, "run_growth_feedback_cycle", return_value=drift_result), \
             patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.events.emit_tribe_event"):
            event = MagicMock()
            event.payload = {
                "user_message": "帮我写一首诗",
                "assistant_reply": "好的，这是一首诗",
                "session_id": "test_session",
            }
            event.ts_ms = 0
            asyncio.get_event_loop().run_until_complete(
                agent._on_interaction_recorded(event)
            )

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert "persona_drift:moderate" in call_kwargs["source"]
        assert call_kwargs["intensity"] == 0.22

    def test_severe_drift_calls_record_pain_higher(self) -> None:
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        import asyncio

        pr_mock = _make_pain_reward_mock()
        agent = PrefrontalAgent()

        drift_result = {
            "drift_alert_level": "SEVERE",
            "drift_dims": ["humor", "task"],
            "correction_hint": "severe drift",
        }

        with patch.object(agent, "run_growth_feedback_cycle", return_value=drift_result), \
             patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.events.emit_tribe_event"):
            event = MagicMock()
            event.payload = {
                "user_message": "帮我写一首诗",
                "assistant_reply": "好的，这是一首诗",
                "session_id": "test_session",
            }
            event.ts_ms = 0
            asyncio.get_event_loop().run_until_complete(
                agent._on_interaction_recorded(event)
            )

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert call_kwargs["intensity"] == 0.38

    def test_no_drift_no_record_pain(self) -> None:
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        import asyncio

        pr_mock = _make_pain_reward_mock()
        agent = PrefrontalAgent()

        drift_result = {"drift_alert_level": "NONE", "drift_dims": []}

        with patch.object(agent, "run_growth_feedback_cycle", return_value=drift_result), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.events.emit_tribe_event"):
            event = MagicMock()
            event.payload = {
                "user_message": "你好",
                "assistant_reply": "你好！",
                "session_id": "test_session",
            }
            event.ts_ms = 0
            asyncio.get_event_loop().run_until_complete(
                agent._on_interaction_recorded(event)
            )

        pr_mock.record_pain.assert_not_called()


# ── T-337：ImmuneAgent BLOCK 时联动 PainRewardAgent.record_pain() ─────────────

class TestT337ImmuneBlockRecordsPain:
    def test_block_calls_record_pain(self) -> None:
        from host_brain.tribe.immune_agent import ImmuneAgent

        pr_mock = _make_pain_reward_mock()
        agent = ImmuneAgent()

        with patch("host_brain.tribe.immune_agent.ImmuneAgent._load_config") as mock_cfg, \
             patch("host_brain.tribe.events.emit_tribe_event"), \
             patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=_make_intero_mock()), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            from host_brain.tribe.immune_agent import _ImmuneConfig
            mock_cfg.return_value = _ImmuneConfig(
                enabled=True,
                blocking_enabled=True,
                warn_threshold=0.30,
                block_threshold=0.40,
                max_matches=6,
            )
            # 触发高分规则：destructive_shell_pattern
            agent.inspect_text(text="rm -rf /", source="test", record=True)

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert "immune_block" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0

    def test_allow_no_record_pain(self) -> None:
        from host_brain.tribe.immune_agent import ImmuneAgent

        pr_mock = _make_pain_reward_mock()
        agent = ImmuneAgent()

        with patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            agent.inspect_text(text="今天天气真好", source="test", record=True)

        pr_mock.record_pain.assert_not_called()


# ── T-338：MotorCortexAgent 执行失败时联动 PainRewardAgent.record_pain() ─────────

class TestT338MotorExecutionFailedRecordsPain:
    def test_execution_failed_calls_record_pain(self) -> None:
        from host_brain.tribe.motor_cortex_agent import MotorCortexAgent

        pr_mock = _make_pain_reward_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        agent = MotorCortexAgent(state_registry=reg)

        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"), \
             patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            agent.receive_execution_feedback(
                plan_id="plan_001",
                ok=False,
                latency_ms=500,
                reason="EDGE_TIMEOUT",
                source="test",
            )

        pr_mock.record_pain.assert_called_once()
        call_kwargs = pr_mock.record_pain.call_args.kwargs
        assert "motor_execution_failed" in call_kwargs["source"]
        assert call_kwargs["intensity"] == 0.22

    def test_execution_success_no_record_pain(self) -> None:
        from host_brain.tribe.motor_cortex_agent import MotorCortexAgent

        pr_mock = _make_pain_reward_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        agent = MotorCortexAgent(state_registry=reg)

        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            agent.receive_execution_feedback(
                plan_id="plan_002",
                ok=True,
                latency_ms=100,
                source="test",
            )

        pr_mock.record_pain.assert_not_called()


# ── T-339：GlymphaticAgent 清理完成时联动 PainRewardAgent.record_reward() ────────

class TestT339GlymphaticCleanRecordsReward:
    def test_recovery_cycle_calls_record_reward(self) -> None:
        from host_brain.tribe.glymphatic_agent import GlymphaticAgent

        pr_mock = _make_pain_reward_mock()
        reg = _make_registry_mock()
        agent = GlymphaticAgent(state_registry=reg)

        with patch("host_brain.tribe.glymphatic_agent.emit_tribe_event"), \
             patch("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", return_value=MagicMock(
                 resolve_memory_layer_snapshot=MagicMock(return_value={"last_write_type": "semantic"}),
                 write_semantic_memory_fact=MagicMock(),
             )), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 export_runtime_state=MagicMock(return_value={"continuity_score": 0.70}),
             )), \
             patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=_make_intero_mock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            result = agent.run_recovery_cycle(source="test", force=True)

        assert result["executed"] is True
        pr_mock.record_reward.assert_called_once()
        call_kwargs = pr_mock.record_reward.call_args.kwargs
        assert "glymphatic_deep_clean" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0

    def test_no_recovery_window_no_record_reward(self) -> None:
        from host_brain.tribe.glymphatic_agent import GlymphaticAgent

        pr_mock = _make_pain_reward_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.set_state = MagicMock()
        reg.aggregate_vital_signs = MagicMock(return_value={
            "circadian_phase": "day",
            "sleep_pressure": 0.10,
            "stress_index": 0.20,
            "cognitive_load_index": 0.20,
            "loneliness_level": 0.10,
        })
        reg.get_homeostasis_report = MagicMock(return_value={"status": "stable"})
        agent = GlymphaticAgent(state_registry=reg)

        with patch("host_brain.tribe.glymphatic_agent.emit_tribe_event"), \
             patch("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", return_value=MagicMock(
                 resolve_memory_layer_snapshot=MagicMock(return_value={}),
             )), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 export_runtime_state=MagicMock(return_value={"continuity_score": 0.30}),
             )), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            result = agent.run_recovery_cycle(source="test", force=False)

        assert result["executed"] is False
        pr_mock.record_reward.assert_not_called()


# ── T-340：HippocampusAgent 记忆巩固成功时联动 PainRewardAgent.record_reward() ───

class TestT340HippocampusMemoryConsolidationRecordsReward:
    def test_semantic_write_calls_record_reward(self) -> None:
        from host_brain.tribe.hippocampus_agent import HippocampusAgent

        pr_mock = _make_pain_reward_mock()
        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        reg.set_state = MagicMock()
        agent = HippocampusAgent(state_registry=reg)

        with patch("host_brain.tribe.hippocampus_agent.HippocampusAgent._write_memory_scope",
                   return_value={"status": "OK", "written": True, "state_version": 1,
                                 "reason_code": None, "detail": None, "payload": {}}), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 state_registry=MagicMock(
                     get_state=MagicMock(return_value={"state": {}}),
                     set_state=MagicMock(),
                 ),
             )), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.write_semantic_memory_fact(
                person_id="user_1",
                fact="test_fact",
                source_refs=["ref_1"],
            )

        pr_mock.record_reward.assert_called_once()
        call_kwargs = pr_mock.record_reward.call_args.kwargs
        assert call_kwargs["source"] == "hippocampus_semantic_write"
        assert call_kwargs["intensity"] == 0.20

    def test_autobiographical_write_calls_record_reward(self) -> None:
        from host_brain.tribe.hippocampus_agent import HippocampusAgent

        pr_mock = _make_pain_reward_mock()
        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        reg.set_state = MagicMock()
        agent = HippocampusAgent(state_registry=reg)

        with patch("host_brain.tribe.hippocampus_agent.HippocampusAgent._write_memory_scope",
                   return_value={"status": "OK", "written": True, "state_version": 1,
                                 "reason_code": None, "detail": None, "payload": {}}), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 tick_idle_cycle=MagicMock(),
             )), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.write_autobiographical_memory(
                person_id="user_1",
                narrative="今天发生了很多事情",
                gate_result={"decision": "ALLOW", "trace_id": "trace_001"},
            )

        pr_mock.record_reward.assert_called_once()
        call_kwargs = pr_mock.record_reward.call_args.kwargs
        assert call_kwargs["source"] == "hippocampus_autobiographical_write"
        assert call_kwargs["intensity"] == 0.35


# ── T-341：CerebellumAgent NO_ADJUST 时联动 PainRewardAgent.record_reward() ─────

class TestT341CerebellumNoAdjustRecordsReward:
    def test_no_adjust_calls_record_reward(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        pr_mock = _make_pain_reward_mock()
        reg = _make_registry_mock(correction_count=0)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock):
            agent.evaluate_correction(
                execution_feedback={"accepted": True, "dispatched": True, "latency_ms": 100},
                source="test",
            )

        pr_mock.record_reward.assert_called_once()
        call_kwargs = pr_mock.record_reward.call_args.kwargs
        assert call_kwargs["source"] == "cerebellum_no_adjust"
        assert call_kwargs["intensity"] == 0.18

    def test_retry_once_no_record_reward(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        pr_mock = _make_pain_reward_mock()
        reg = _make_registry_mock(correction_count=0)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=pr_mock), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=_make_intero_mock()):
            agent.evaluate_correction(
                execution_feedback={"accepted": False, "dispatched": False, "latency_ms": 0},
                source="test",
            )

        # RETRY_ONCE 时不应调用 record_reward（只调用 record_pain）
        pr_mock.record_reward.assert_not_called()


# ── T-342：CerebellumAgent 持续高纠偏时联动 InteroceptionAgent.inject_external_stress() ──

class TestT342CerebellumPersistentCorrectionInjectsStress:
    def test_correction_count_ge_3_injects_stress(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        intero_mock = _make_intero_mock()
        # correction_count=5 表示已有5次纠偏，满足>=3的条件
        reg = _make_registry_mock(correction_count=5)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=_make_pain_reward_mock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.evaluate_correction(
                execution_feedback={"accepted": False, "dispatched": False, "latency_ms": 0},
                source="test",
            )

        intero_mock.inject_external_stress.assert_called_once()
        call_kwargs = intero_mock.inject_external_stress.call_args.kwargs
        assert "cerebellum_persistent_correction" in call_kwargs["source"]
        assert call_kwargs["intensity"] > 0

    def test_correction_count_lt_3_no_stress(self) -> None:
        from host_brain.tribe.cerebellum_agent import CerebellumAgent

        intero_mock = _make_intero_mock()
        reg = _make_registry_mock(correction_count=1)
        agent = CerebellumAgent(state_registry=reg)

        with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=_make_pain_reward_mock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.evaluate_correction(
                execution_feedback={"accepted": False, "dispatched": False, "latency_ms": 0},
                source="test",
            )

        intero_mock.inject_external_stress.assert_not_called()


# ── T-343：SocialBrainAgent Bond 退步时联动 InteroceptionAgent.inject_external_stress() ──

class TestT343SocialBondRegressInjectsStress:
    def test_bond_regress_injects_stress(self) -> None:
        from host_brain.tribe.social_brain_agent import SocialBrainAgent
        import asyncio

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.set_state = MagicMock()
        agent = SocialBrainAgent(state_registry=reg)

        with patch("host_brain.tribe.social_brain_agent.SocialBrainAgent._refresh_bond_vector"), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=_make_pain_reward_mock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            event = MagicMock()
            event.payload = {
                "user_id": "user_1",
                "bond_stage": "FRIEND",
                "bond_trend": "REGRESS",
                "intensity": 0.60,
            }
            event.ts_ms = 0
            asyncio.get_event_loop().run_until_complete(
                agent._on_bond_regress_snapshot(event)
            )

        intero_mock.inject_external_stress.assert_called_once()
        call_kwargs = intero_mock.inject_external_stress.call_args.kwargs
        assert "social_bond_regress" in call_kwargs["source"]
        assert call_kwargs["intensity"] == round(min(0.45, 0.60 * 0.50), 4)


# ── T-344：HippocampusAgent 记忆巩固后联动 InteroceptionAgent.reduce_recovery_urgency() ──

class TestT344HippocampusConsolidationReducesUrgency:
    def test_semantic_write_reduces_urgency(self) -> None:
        from host_brain.tribe.hippocampus_agent import HippocampusAgent

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        reg.set_state = MagicMock()
        agent = HippocampusAgent(state_registry=reg)

        with patch("host_brain.tribe.hippocampus_agent.HippocampusAgent._write_memory_scope",
                   return_value={"status": "OK", "written": True, "state_version": 1,
                                 "reason_code": None, "detail": None, "payload": {}}), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 state_registry=MagicMock(
                     get_state=MagicMock(return_value={"state": {}}),
                     set_state=MagicMock(),
                 ),
             )), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=_make_pain_reward_mock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.write_semantic_memory_fact(
                person_id="user_1",
                fact="test_fact",
            )

        intero_mock.reduce_recovery_urgency.assert_called_once()
        call_kwargs = intero_mock.reduce_recovery_urgency.call_args.kwargs
        assert call_kwargs["source"] == "hippocampus_semantic_consolidation"
        assert call_kwargs["relief"] == 0.12

    def test_autobiographical_write_reduces_urgency_more(self) -> None:
        from host_brain.tribe.hippocampus_agent import HippocampusAgent

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.write = MagicMock()
        reg.set_state = MagicMock()
        agent = HippocampusAgent(state_registry=reg)

        with patch("host_brain.tribe.hippocampus_agent.HippocampusAgent._write_memory_scope",
                   return_value={"status": "OK", "written": True, "state_version": 1,
                                 "reason_code": None, "detail": None, "payload": {}}), \
             patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=MagicMock(
                 tick_idle_cycle=MagicMock(),
             )), \
             patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=_make_pain_reward_mock()), \
             patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock):
            agent.write_autobiographical_memory(
                person_id="user_1",
                narrative="今天发生了很多事情",
                gate_result={"decision": "ALLOW", "trace_id": "trace_001"},
            )

        intero_mock.reduce_recovery_urgency.assert_called_once()
        call_kwargs = intero_mock.reduce_recovery_urgency.call_args.kwargs
        assert call_kwargs["source"] == "hippocampus_autobiographical_consolidation"
        assert call_kwargs["relief"] == 0.25


# ── T-345：SomaticAgent 低疲劳时联动 InteroceptionAgent.reduce_recovery_urgency() ──

class TestT345SomaticLowFatigueReducesUrgency:
    def test_low_fatigue_reduces_urgency(self) -> None:
        from host_brain.tribe.somatic_agent import SomaticAgent
        import asyncio

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.set_state = MagicMock()
        agent = SomaticAgent(state_registry=reg)

        with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()):
            event = MagicMock()
            event.payload = {
                "fatigue_level": 0.10,  # 低疲劳，< 0.30
                "motion_level": 0.20,
            }
            event.ts_ms = 1000
            asyncio.get_event_loop().run_until_complete(
                agent._on_body_sense_updated(event)
            )

        intero_mock.reduce_recovery_urgency.assert_called_once()
        call_kwargs = intero_mock.reduce_recovery_urgency.call_args.kwargs
        assert "somatic_low_fatigue" in call_kwargs["source"]
        assert call_kwargs["relief"] > 0

    def test_high_fatigue_no_reduce_urgency(self) -> None:
        from host_brain.tribe.somatic_agent import SomaticAgent
        import asyncio

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.set_state = MagicMock()
        agent = SomaticAgent(state_registry=reg)

        with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()):
            event = MagicMock()
            event.payload = {
                "fatigue_level": 0.80,  # 高疲劳，>= 0.60
                "motion_level": 0.10,
            }
            event.ts_ms = 1000
            asyncio.get_event_loop().run_until_complete(
                agent._on_body_sense_updated(event)
            )

        # 高疲劳时只注入压力，不降低urgency
        intero_mock.reduce_recovery_urgency.assert_not_called()
        intero_mock.inject_external_stress.assert_called_once()

    def test_mid_fatigue_no_reduce_no_inject(self) -> None:
        from host_brain.tribe.somatic_agent import SomaticAgent
        import asyncio

        intero_mock = _make_intero_mock()
        reg = MagicMock()
        reg.get_state = MagicMock(return_value={"state": {}})
        reg.set_state = MagicMock()
        agent = SomaticAgent(state_registry=reg)

        with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=intero_mock), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=MagicMock()):
            event = MagicMock()
            event.payload = {
                "fatigue_level": 0.45,  # 中等疲劳，0.30~0.60 区间
                "motion_level": 0.20,
            }
            event.ts_ms = 1000
            asyncio.get_event_loop().run_until_complete(
                agent._on_body_sense_updated(event)
            )

        # 中等疲劳：既不注入压力，也不降低urgency
        intero_mock.reduce_recovery_urgency.assert_not_called()
        intero_mock.inject_external_stress.assert_not_called()
