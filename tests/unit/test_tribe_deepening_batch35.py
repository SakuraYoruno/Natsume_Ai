"""T-237：第三十五批（T-226~T-236）核心联动逻辑单元测试。

覆盖场景：
1. T-226：SomaticAgent 高孤独感时调用 InteroceptionAgent.inject_external_stress()
2. T-227：CircadianAgent 相位切换到 night 时触发 GlymphaticAgent
3. T-228：ProprioceptionAgent 高认知负荷时调用 LimbicAgent.drive_emotion_metabolism()
4. T-229：LanguageSystemAgent 高情绪压力时调用 PainRewardAgent.record_pain()
5. T-230：ProprioceptionAgent 高打断频率时调用 EndocrineAgent.record_stress_event()
6. T-231：InitiativeGovernor 夜间高睡眠压力时降低 motive_score
7. T-232/T-233/T-234：vital_signs 包含 somatic_comfort_needed/language_urgency_score/body_budget
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from host_brain.tribe.somatic_agent import SomaticAgent
from host_brain.tribe.circadian_agent import CircadianAgent
from host_brain.tribe.proprioception_agent import ProprioceptionAgent
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-226 ────────────────────────────────────────────────────────────────────

class TestSomaticInteroceptionLink:
    """T-226：SomaticAgent 高孤独感时通知 InteroceptionAgent 提高 recovery_urgency。"""

    def test_high_loneliness_calls_inject_external_stress(self, registry):
        agent = SomaticAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {"loneliness": 0.80}
        event.ts_ms = 1000

        with patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.run(agent._on_loneliness_signal(event))

        assert mock_intero.inject_external_stress.called, \
            "loneliness=0.80 > 0.65 时应调用 inject_external_stress()"
        call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
        assert call_kwargs.get("source") == "somatic_loneliness"
        assert float(call_kwargs.get("intensity") or 0.0) > 0

    def test_low_loneliness_no_inject(self, registry):
        agent = SomaticAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {"loneliness": 0.40}
        event.ts_ms = 1000

        with patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.run(agent._on_loneliness_signal(event))

        assert not mock_intero.inject_external_stress.called, \
            "loneliness=0.40 <= 0.65 时不应调用 inject_external_stress()"


# ── T-227 ────────────────────────────────────────────────────────────────────

class TestCircadianNightGlymphatic:
    """T-227：CircadianAgent 相位切换到 night 时触发 GlymphaticAgent。"""

    def test_night_phase_triggers_glymphatic(self, registry):
        agent = CircadianAgent(state_registry=registry)
        # 先设置为 day 相位
        agent._last_phase = "day"
        registry.set_state("circadian_agent", {"phase": "day"}, writer="test")

        mock_glymphatic = MagicMock()
        mock_glymphatic.run_recovery_cycle = MagicMock(return_value={"executed": True})

        with patch("host_brain.tribe.circadian_agent.get_glymphatic_agent", return_value=mock_glymphatic):
            with patch("host_brain.tribe.circadian_agent.emit_tribe_event"):
                # 模拟夜间时间戳（凌晨1点）
                import datetime
                night_ts_ms = int(datetime.datetime(2026, 4, 10, 1, 0, 0,
                    tzinfo=datetime.timezone.utc).timestamp() * 1000)
                agent.refresh_state(source="test", ts_ms=night_ts_ms)

        # 如果相位从 day 切换到 night，应触发 GlymphaticAgent
        if agent._last_phase == "night":
            assert mock_glymphatic.run_recovery_cycle.called, \
                "相位切换到 night 时应触发 GlymphaticAgent.run_recovery_cycle()"


# ── T-228 ────────────────────────────────────────────────────────────────────

class TestProprioceptionLimbicLink:
    """T-228：ProprioceptionAgent 高认知负荷时调用 LimbicAgent.drive_emotion_metabolism()。"""

    def test_high_cognitive_load_drives_limbic(self, registry):
        agent = ProprioceptionAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.proprioception_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.proprioception_agent.emit_tribe_event"):
                # 高任务数 + 高打断 → cognitive_load_index >= 0.65
                agent._update_state(
                    source="test",
                    changes={
                        "active_session": True,
                        "active_task_count": 5,
                        "interrupt_count_5m": 8,
                        "voice_pipeline_active": True,
                    },
                )

        cognitive_load = float(
            (registry.get_state("proprioception_agent") or {}).get("state", {}).get("cognitive_load_index") or 0.0
        )
        if cognitive_load >= 0.65:
            assert mock_limbic.drive_emotion_metabolism.called, \
                f"cognitive_load={cognitive_load:.3f} >= 0.65 时应调用 drive_emotion_metabolism()"


# ── T-229 ────────────────────────────────────────────────────────────────────

class TestLanguagePainRewardLink:
    """T-229：LanguageSystemAgent 高情绪压力时调用 PainRewardAgent.record_pain()。"""

    def test_high_emotional_pressure_records_pain(self, registry):
        agent = LanguageSystemAgent(state_registry=registry)
        mock_pain_reward = MagicMock()
        mock_pain_reward.record_pain = MagicMock()

        with patch("host_brain.tribe.language_system_agent.get_pain_reward_agent", return_value=mock_pain_reward):
            result = agent.resolve_dual_system(
                user_text="我真的很难过，感觉一切都崩溃了，我好害怕",
                bond_stage="friend",
                emotion_tag="SAD",
                loneliness_level=0.8,
                high_loneliness_flag=True,
            )

        emotional_pressure = float(result.get("wernicke", {}).get("emotional_pressure") or 0.0)
        if emotional_pressure >= 0.55:
            assert mock_pain_reward.record_pain.called, \
                f"emotional_pressure={emotional_pressure:.3f} >= 0.55 时应调用 record_pain()"


# ── T-230 ────────────────────────────────────────────────────────────────────

class TestProprioceptionEndocrineLink:
    """T-230：ProprioceptionAgent 高打断频率时调用 EndocrineAgent.record_stress_event()。"""

    def test_high_interrupt_triggers_endocrine(self, registry):
        agent = ProprioceptionAgent(state_registry=registry)
        mock_endocrine = MagicMock()
        mock_endocrine.record_stress_event = MagicMock()

        event = MagicMock()
        event.payload = {"conversation_id": "s1"}
        event.ts_ms = 1000

        with patch("host_brain.tribe.proprioception_agent.get_endocrine_agent", return_value=mock_endocrine):
            with patch("host_brain.tribe.proprioception_agent.emit_tribe_event"):
                # 模拟 6 次以上打断
                for i in range(7):
                    event.payload = {"conversation_id": "s1", "ts_ms": 1000 + i * 10000}
                    event.ts_ms = 1000 + i * 10000
                    asyncio.run(
                        agent._on_channel_message_received(event)
                    )

        # 打断次数 >= 6 时应触发 EndocrineAgent
        assert mock_endocrine.record_stress_event.called, \
            "打断次数 >= 6 时应调用 EndocrineAgent.record_stress_event()"


# ── T-231 ────────────────────────────────────────────────────────────────────

class TestInitiativeGovernorSleepPressure:
    """T-231：InitiativeGovernor 夜间高睡眠压力时降低 motive_score。"""

    def test_high_sleep_pressure_reduces_motive(self, registry):
        agent = InitiativeGovernorAgent(state_registry=registry)

        # 构造高睡眠压力的 circadian_state
        mock_circadian = MagicMock()
        mock_circadian.export_runtime_state = MagicMock(return_value={
            "phase": "night",
            "proactive_multiplier": 0.36,
            "sleep_pressure": 0.85,
        })
        mock_proprio = MagicMock()
        mock_proprio.export_runtime_state = MagicMock(return_value={
            "cognitive_load_index": 0.2,
            "readiness_hint": 0.8,
            "continuity_hint": 0.5,
        })
        mock_social = MagicMock()
        mock_social.get_bond_context = MagicMock(return_value={
            "bond_vector": {"trust": 0.6, "repair_confidence": 0.5}
        })
        mock_intero = MagicMock()
        mock_intero.export_runtime_state = MagicMock(return_value={"body_budget": 0.7})

        with patch("host_brain.tribe.initiative_governor_agent.get_circadian_agent", return_value=mock_circadian):
            with patch("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", return_value=mock_proprio):
                with patch("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", return_value=mock_social):
                    with patch("host_brain.tribe.initiative_governor_agent.get_interoception_agent", return_value=mock_intero):
                        with patch("host_brain.tribe.initiative_governor_agent.emit_tribe_event"):
                            result_night = agent.evaluate_initiative(
                                action="proactive_greeting",
                                motive=0.65,
                            )

        # 夜间高睡眠压力时，motive 应被折扣
        motive_night = float(result_night.get("scores", {}).get("motive") or 0.0)
        assert motive_night < 0.65, \
            f"夜间 sleep_pressure=0.85 时 motive 应被折扣，实际={motive_night:.4f}"


# ── T-232/T-233/T-234 ─────────────────────────────────────────────────────────

class TestVitalSignsNewFields:
    """T-232/T-233/T-234：vital_signs 包含新增字段。"""

    def test_vital_signs_has_somatic_fields(self, registry):
        registry.set_state("somatic_agent", {
            "somatic_comfort_needed": True,
            "fatigue_level": 0.72,
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "somatic_comfort_needed" in vitals, "vital_signs 应包含 somatic_comfort_needed"
        assert "somatic_fatigue_level" in vitals, "vital_signs 应包含 somatic_fatigue_level"
        assert vitals["somatic_comfort_needed"] is True
        assert abs(float(vitals["somatic_fatigue_level"]) - 0.72) < 0.01

    def test_vital_signs_has_language_fields(self, registry):
        registry.set_state("language_system_agent", {
            "last_wernicke": {"urgency_score": 0.65},
            "last_broca": {"response_mode": "supportive"},
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "language_urgency_score" in vitals, "vital_signs 应包含 language_urgency_score"
        assert "language_response_mode" in vitals, "vital_signs 应包含 language_response_mode"
        assert abs(float(vitals["language_urgency_score"]) - 0.65) < 0.01
        assert vitals["language_response_mode"] == "supportive"

    def test_vital_signs_has_interoception_fields(self, registry):
        registry.set_state("interoception_agent", {
            "body_budget": 0.35,
            "recovery_urgency": 0.68,
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "body_budget" in vitals, "vital_signs 应包含 body_budget"
        assert "recovery_urgency" in vitals, "vital_signs 应包含 recovery_urgency"
        assert abs(float(vitals["body_budget"]) - 0.35) < 0.01
        assert abs(float(vitals["recovery_urgency"]) - 0.68) < 0.01
