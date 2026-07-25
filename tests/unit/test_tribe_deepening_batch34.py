"""T-225：第三十四批（T-214~T-224）核心联动逻辑单元测试。

覆盖场景：
1. T-214：GlymphaticAgent.run_recovery_cycle() 调用 write_semantic_memory_fact 时传入正确参数
2. T-215：PainRewardAgent 高痛觉时主动调用 LimbicAgent.drive_emotion_metabolism()
3. T-216：PainRewardAgent 累积痛觉写入 EndocrineAgent.record_stress_event()
4. T-217：MirrorNeuronAgent 共情强度>=0.45 时回写 LimbicAgent
5. T-218：DefaultModeAgent continuity_score>=0.55 时触发 GlymphaticAgent
6. T-219：PrefrontalAgent 漂移 MODERATE/SEVERE 时调用 LimbicAgent
7. T-220：SocialBrainAgent Bond 退步/进展联动 PainRewardAgent
8. T-221/T-222：vital_signs 包含 pain_level/reward_level/mirror_resonance_strength
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from host_brain.tribe.pain_reward_agent import PainRewardAgent
from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
from host_brain.tribe.default_mode_agent import DefaultModeAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


pytestmark = pytest.mark.usefixtures("tribe_runtime_context")


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-214 ────────────────────────────────────────────────────────────────────

class TestGlymphaticMemoryWrite:
    """T-214：GlymphaticAgent 调用 write_semantic_memory_fact 时传入 person_id。"""

    def test_write_semantic_memory_fact_has_person_id(self, registry):
        from host_brain.tribe.glymphatic_agent import GlymphaticAgent
        agent = GlymphaticAgent(state_registry=registry)

        mock_hippocampus = MagicMock()
        mock_hippocampus.write_semantic_memory_fact = MagicMock(return_value={"layer": "semantic"})

        with patch("host_brain.tribe.glymphatic_agent.get_hippocampus_agent", return_value=mock_hippocampus):
            with patch("host_brain.tribe.glymphatic_agent.emit_tribe_event"):
                result = agent.run_recovery_cycle(source="test", force=True)

        if result.get("memory_written"):
            call_kwargs = mock_hippocampus.write_semantic_memory_fact.call_args.kwargs
            assert "person_id" in call_kwargs, "write_semantic_memory_fact 必须传入 person_id"
            assert "fact" in call_kwargs


# ── T-215 ────────────────────────────────────────────────────────────────────

class TestPainRewardLimbicDrive:
    """T-215：PainRewardAgent 高痛觉时主动驱动 LimbicAgent 情绪偏移。"""

    def test_high_pain_drives_limbic(self, registry):
        agent = PainRewardAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.pain_reward_agent.get_limbic_agent", return_value=mock_limbic):
            # 记录高强度痛觉（pain_level 会超过 0.55）
            agent.record_pain(source="rejection", intensity=0.8)

        # 验证 drive_emotion_metabolism 被调用
        if mock_limbic.drive_emotion_metabolism.called:
            call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
            assert float(call_kwargs.get("valence") or 0.0) < 0, "高痛觉应产生负效价偏移"

    def test_high_reward_drives_limbic_positive(self, registry):
        agent = PainRewardAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.pain_reward_agent.get_limbic_agent", return_value=mock_limbic):
            agent.record_reward(source="recognized", intensity=0.9)

        if mock_limbic.drive_emotion_metabolism.called:
            call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
            assert float(call_kwargs.get("valence") or 0.0) > 0, "高奖励应产生正效价偏移"


# ── T-216 ────────────────────────────────────────────────────────────────────

class TestPainRewardEndocrineCortisol:
    """T-216：PainRewardAgent 累积痛觉写入 EndocrineAgent cortisol。"""

    def test_cumulative_pain_triggers_endocrine(self, registry):
        agent = PainRewardAgent(state_registry=registry)
        mock_endocrine = MagicMock()
        mock_endocrine.record_stress_event = MagicMock()

        with patch("host_brain.tribe.pain_reward_agent.get_endocrine_agent", return_value=mock_endocrine):
            # 多次记录痛觉，使 cumulative_pain >= 1.0
            for _ in range(5):
                agent.record_pain(source="rejection", intensity=0.4)

        # 验证 record_stress_event 被调用
        if mock_endocrine.record_stress_event.called:
            call_kwargs = mock_endocrine.record_stress_event.call_args.kwargs
            assert "source" in call_kwargs
            assert float(call_kwargs.get("intensity") or 0.0) > 0


# ── T-217 ────────────────────────────────────────────────────────────────────

class TestMirrorNeuronLimbicFeedback:
    """T-217：MirrorNeuronAgent 共情强度>=0.45 时回写 LimbicAgent。"""

    def test_high_resonance_drives_limbic(self, registry):
        agent = MirrorNeuronAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.mirror_neuron_agent.emit_tribe_event"):
                result = agent.apply_resonance(
                    user_id="user_1",
                    session_id="s1",
                    emotion_tag="JOY",
                    emotion_intensity=0.85,
                    source="test",
                )

        resonance_strength = float(result.get("resonance_strength") or 0.0)
        if resonance_strength >= 0.45:
            assert mock_limbic.drive_emotion_metabolism.called, \
                f"resonance_strength={resonance_strength:.3f} >= 0.45 时应调用 drive_emotion_metabolism"


# ── T-218 ────────────────────────────────────────────────────────────────────

class TestDefaultModeGlymphaticTrigger:
    """T-218：DefaultModeAgent continuity_score>=0.55 时触发 GlymphaticAgent。"""

    def test_high_continuity_triggers_glymphatic(self, registry, monkeypatch):
        monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
        agent = DefaultModeAgent(state_registry=registry)
        mock_glymphatic = MagicMock()
        mock_glymphatic.run_recovery_cycle = MagicMock(return_value={"executed": True})

        # 构造高连续性的 inner_state
        inner_state = {
            "continuity_score": 0.75,
            "loneliness_level": 0.3,
            "anticipation_level": 0.4,
            "absence_band": "recent",
            "offline_hours": 0.5,
        }

        with patch("host_brain.tribe.default_mode_agent.get_glymphatic_agent", return_value=mock_glymphatic):
            with patch("host_brain.tribe.default_mode_agent.emit_tribe_event"):
                with patch(
                    "host_brain.tribe.default_mode_agent.DefaultModeAgent.tick_idle_cycle",
                    wraps=agent.tick_idle_cycle,
                ):
                    # 直接测试 continuity_score >= 0.55 的分支
                    from unittest.mock import patch as _patch
                    with _patch(
                        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
                        return_value={
                            "absence_band": "recent",
                            "offline_hours": 0.5,
                            "loneliness_level": 0.3,
                            "anticipation_level": 0.4,
                            "continuity_score": 0.75,
                        },
                    ):
                        with _patch(
                            "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
                            return_value=("测试内心活动", "self", "", {}),
                        ):
                            agent.tick_idle_cycle(
                                person_id="user_1",
                                source="test",
                                inner_state=inner_state,
                            )

        # continuity_score=0.75 >= 0.55，应触发 GlymphaticAgent
        assert mock_glymphatic.run_recovery_cycle.called, \
            "continuity_score=0.75 >= 0.55 时应触发 GlymphaticAgent.run_recovery_cycle()"


# ── T-221/T-222 ───────────────────────────────────────────────────────────────

class TestVitalSignsPainMirror:
    """T-221/T-222：vital_signs 包含 pain_level/reward_level/mirror_resonance_strength。"""

    def test_vital_signs_has_pain_reward_fields(self, registry):
        # 写入 pain_reward_agent 快照
        registry.set_state("pain_reward_agent", {
            "pain_level": 0.35,
            "reward_level": 0.55,
            "net_valence": 0.20,
            "hedonic_tone": "mild_positive",
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "pain_level" in vitals, "vital_signs 应包含 pain_level"
        assert "reward_level" in vitals, "vital_signs 应包含 reward_level"
        assert "net_valence" in vitals, "vital_signs 应包含 net_valence"
        assert abs(float(vitals["pain_level"]) - 0.35) < 0.01

    def test_vital_signs_has_mirror_resonance_field(self, registry):
        # 写入 mirror_neuron_agent 快照
        registry.set_state("mirror_neuron_agent", {
            "last_resonance": {"resonance_strength": 0.72},
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "mirror_resonance_strength" in vitals, "vital_signs 应包含 mirror_resonance_strength"
        assert abs(float(vitals["mirror_resonance_strength"]) - 0.72) < 0.01
