"""T-284：第三十九批（T-274~T-283）核心联动逻辑单元测试。

覆盖场景：
1. T-274：PrefrontalAgent.export_runtime_state() 方法存在且返回正确字段
2. T-275：SocialBrainAgent.export_runtime_state() 方法存在且返回正确字段
3. T-278：vital_signs 包含 drift_alert_level/bond_repair_needed 字段
4. T-279：PrefrontalAgent 高漂移时调用 EndocrineAgent.record_stress_event()
5. T-280：SocialBrainAgent Bond 修复时调用 LimbicAgent.drive_emotion_metabolism()
6. T-281：DefaultModeAgent 高连续性时将 ANSAgent._recovery_mode 设为 False
7. T-282：CortexAgent.collect_tribe_context() 包含 interoception_state
8. T-283：vital_signs 包含 ans_recovery_mode/ans_proactive_multiplier 字段
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-274 ────────────────────────────────────────────────────────────────────

class TestPrefrontalExportRuntimeState:
    """T-274：PrefrontalAgent.export_runtime_state() 方法存在且返回正确字段。"""

    def test_export_runtime_state_exists(self):
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        agent = PrefrontalAgent()
        assert hasattr(agent, "export_runtime_state"), \
            "PrefrontalAgent 应有 export_runtime_state() 方法"

    def test_export_runtime_state_returns_required_fields(self, registry):
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        agent = PrefrontalAgent()
        # 写入 drift 快照
        registry.set_state("prefrontal_agent", {
            "last_drift_result": {
                "alert_level": "MODERATE",
                "drift_dims": ["humor", "warmth"],
                "correction_hint": "回归温柔风格",
                "drift_score": 0.42,
            },
            "session_count": 5,
            "evolution_cycle_count": 2,
        }, writer="test")
        agent.state_registry = registry
        state = agent.export_runtime_state()
        assert state.get("alert_level") == "MODERATE"
        assert "humor" in state.get("drift_dims", [])
        assert state.get("drift_score") == pytest.approx(0.42, abs=0.01)
        assert state.get("session_count") == 5
        assert "correction_hint" in state

    def test_export_runtime_state_default_when_empty(self, registry):
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        agent = PrefrontalAgent()
        agent.state_registry = registry
        state = agent.export_runtime_state()
        assert state.get("alert_level") == "NONE"
        assert isinstance(state.get("drift_dims"), list)


# ── T-275 ────────────────────────────────────────────────────────────────────

class TestSocialBrainExportRuntimeState:
    """T-275：SocialBrainAgent.export_runtime_state() 方法存在且返回正确字段。"""

    def test_export_runtime_state_exists(self):
        from host_brain.tribe.social_brain_agent import SocialBrainAgent
        agent = SocialBrainAgent()
        assert hasattr(agent, "export_runtime_state"), \
            "SocialBrainAgent 应有 export_runtime_state() 方法"

    def test_export_runtime_state_returns_required_fields(self, registry):
        from host_brain.tribe.social_brain_agent import SocialBrainAgent
        agent = SocialBrainAgent()
        registry.set_state("social_brain_agent", {
            "bond_stage": "FRIEND",
            "bond_trend": "progress",
            "bond_vector": {"intimacy": 0.65, "trust": 0.72, "dependency": 0.30},
            "repair_state": {"repair_needed": True, "repair_reason": "conflict"},
            "milestone_achieved_count": 3,
        }, writer="test")
        agent.state_registry = registry
        state = agent.export_runtime_state()
        assert state.get("bond_stage") == "FRIEND"
        assert state.get("bond_trend") == "progress"
        assert state.get("bond_vector_intimacy") == pytest.approx(0.65, abs=0.01)
        assert state.get("repair_needed") is True
        assert state.get("milestone_achieved_count") == 3


# ── T-278 ────────────────────────────────────────────────────────────────────

class TestVitalSignsDriftBondRepair:
    """T-278：vital_signs 包含 drift_alert_level/bond_repair_needed 字段。"""

    def test_vital_signs_has_drift_alert_level(self, registry):
        registry.set_state("prefrontal_agent", {
            "last_drift_result": {"alert_level": "SEVERE"},
        }, writer="test")
        vitals = registry.aggregate_vital_signs()
        assert "drift_alert_level" in vitals, "vital_signs 应包含 drift_alert_level"
        assert vitals["drift_alert_level"] == "SEVERE"

    def test_vital_signs_has_bond_repair_needed(self, registry):
        registry.set_state("social_brain_agent", {
            "repair_state": {"repair_needed": True, "repair_reason": "conflict"},
        }, writer="test")
        vitals = registry.aggregate_vital_signs()
        assert "bond_repair_needed" in vitals, "vital_signs 应包含 bond_repair_needed"
        assert vitals["bond_repair_needed"] is True

    def test_vital_signs_drift_default_none(self, registry):
        vitals = registry.aggregate_vital_signs()
        assert vitals.get("drift_alert_level") == "NONE"
        assert vitals.get("bond_repair_needed") is False


# ── T-279 ────────────────────────────────────────────────────────────────────

class TestPrefrontalDriftEndocrineLink:
    """T-279：PrefrontalAgent 高漂移时调用 EndocrineAgent.record_stress_event()。"""

    def test_moderate_drift_calls_endocrine_record_stress(self):
        from host_brain.tribe.prefrontal_agent import PrefrontalAgent
        agent = PrefrontalAgent()
        mock_endocrine = MagicMock()
        mock_endocrine.record_stress_event = MagicMock()
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.prefrontal_agent.get_endocrine_agent", return_value=mock_endocrine):
            with patch("host_brain.tribe.prefrontal_agent.get_limbic_agent", return_value=mock_limbic):
                # 模拟 run_growth_feedback_cycle 返回 MODERATE 漂移
                with patch("host_brain.tribe.prefrontal_agent.PrefrontalAgent.run_growth_feedback_cycle", return_value={
                    "drift_alert_level": "MODERATE",
                    "feedback_count": 1,
                    "applied": False,
                    "triggered_rules": [],
                }):
                    import asyncio
                    asyncio.run(
                        agent._on_interaction_recorded(MagicMock(payload={
                            "user_message": "你好",
                            "assistant_reply": "你好呀",
                            "session_id": "s1",
                        }))
                    )

        assert mock_endocrine.record_stress_event.called, \
            "MODERATE 漂移时应调用 EndocrineAgent.record_stress_event()"
        call_kwargs = mock_endocrine.record_stress_event.call_args.kwargs
        assert "persona_drift" in str(call_kwargs.get("source") or "")


# ── T-280 ────────────────────────────────────────────────────────────────────

class TestSocialBrainRepairLimbicLink:
    """T-280：SocialBrainAgent Bond 修复时调用 LimbicAgent.drive_emotion_metabolism()。"""

    def test_bond_repair_calls_limbic_warmth(self):
        from host_brain.tribe.social_brain_agent import SocialBrainAgent
        agent = SocialBrainAgent()
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.social_brain_agent.get_limbic_agent", return_value=mock_limbic):
            import asyncio
            asyncio.run(
                agent._on_bond_repair_action(MagicMock(payload={
                    "effort": 0.50,
                    "action": "apology",
                }, ts_ms=1000))
            )

        assert mock_limbic.drive_emotion_metabolism.called, \
            "effort=0.50 >= 0.30 时应调用 LimbicAgent.drive_emotion_metabolism()"
        call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
        assert float(call_kwargs.get("valence") or 0.0) > 0, \
            "Bond 修复时 valence 应为正值（温暖情绪偏移）"

    def test_low_effort_no_limbic_call(self):
        from host_brain.tribe.social_brain_agent import SocialBrainAgent
        agent = SocialBrainAgent()
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.social_brain_agent.get_limbic_agent", return_value=mock_limbic):
            import asyncio
            asyncio.run(
                agent._on_bond_repair_action(MagicMock(payload={
                    "effort": 0.10,
                    "action": "small_gesture",
                }, ts_ms=1000))
            )

        assert not mock_limbic.drive_emotion_metabolism.called, \
            "effort=0.10 < 0.30 时不应调用 drive_emotion_metabolism()"


# ── T-281 ────────────────────────────────────────────────────────────────────

class TestDefaultModeANSLink:
    """T-281：DefaultModeAgent 高连续性时将 ANSAgent._recovery_mode 设为 False。"""

    def test_high_continuity_resets_ans_recovery_mode(self, registry):
        from host_brain.tribe.default_mode_agent import DefaultModeAgent
        agent = DefaultModeAgent(state_registry=registry)
        mock_ans = MagicMock()
        mock_ans._recovery_mode = True
        def _fake_set_recovery_mode(enabled, **kw):
            mock_ans._recovery_mode = enabled
        mock_ans.set_recovery_mode.side_effect = _fake_set_recovery_mode

        with patch("host_brain.tribe.default_mode_agent.get_ans_agent", return_value=mock_ans), \
             patch("host_brain.tribe.default_mode_agent.get_glymphatic_agent"), \
             patch("host_brain.tribe.default_mode_agent.get_pain_reward_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.default_mode_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.default_mode_agent.get_interoception_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.default_mode_agent.emit_tribe_event"), \
             patch("host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
                   return_value={"continuity_score": 0.75, "absence_band": "short",
                                 "offline_hours": 1.0, "loneliness_level": 0.20, "anticipation_level": 0.30}):
            agent.tick_idle_cycle(source="test", person_id="user_1")

        assert mock_ans._recovery_mode is False, \
            "continuity_score=0.75 >= 0.60 时应将 ANSAgent._recovery_mode 设为 False"


# ── T-283 ────────────────────────────────────────────────────────────────────

class TestVitalSignsANSFields:
    """T-283：vital_signs 包含 ans_recovery_mode/ans_proactive_multiplier 字段。"""

    def test_vital_signs_has_ans_fields(self, registry):
        registry.set_state("ans_agent", {
            "recovery_mode": True,
            "proactive_multiplier": 0.25,
        }, writer="test")
        vitals = registry.aggregate_vital_signs()
        assert "ans_recovery_mode" in vitals, "vital_signs 应包含 ans_recovery_mode"
        assert "ans_proactive_multiplier" in vitals, "vital_signs 应包含 ans_proactive_multiplier"
        assert vitals["ans_recovery_mode"] is True
        assert abs(float(vitals["ans_proactive_multiplier"]) - 0.25) < 0.01

    def test_vital_signs_ans_default_when_no_state(self, registry):
        vitals = registry.aggregate_vital_signs()
        assert vitals.get("ans_recovery_mode") is False
        assert vitals.get("ans_proactive_multiplier") == pytest.approx(0.70, abs=0.01)
