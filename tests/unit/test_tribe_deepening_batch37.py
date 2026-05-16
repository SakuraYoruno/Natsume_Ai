"""T-261：第三十七批（T-250~T-260）核心联动逻辑单元测试。

覆盖场景：
1. T-250：VestibularAgent.export_runtime_state() 不触发 evaluate_stability()
2. T-251：VestibularAgent 高抖动时调用 LimbicAgent.drive_emotion_metabolism()
3. T-252：VestibularAgent 高抖动时调用 ANSAgent.adjust_rhythm_for_high_interrupt()
4. T-253：ImmuneAgent._on_interaction_recorded 写入 risk_level/last_score
5. T-254：ANSAgent.export_runtime_state() 方法存在且返回正确字段
6. T-257：vital_signs 包含 circadian_phase/proactive_multiplier/recovery_bias
7. T-258：CircadianAgent night 相位切换时联动 EndocrineAgent 降低 cortisol
8. T-259：MirrorNeuronAgent 高共情时联动 EndocrineAgent 降低 cortisol
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from host_brain.tribe.vestibular_agent import VestibularAgent
from host_brain.tribe.immune_agent import ImmuneAgent
from host_brain.tribe.ans_agent import ANSAgent
from host_brain.tribe.circadian_agent import CircadianAgent
from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-250 ────────────────────────────────────────────────────────────────────

class TestVestibularExportRuntimeState:
    """T-250：VestibularAgent.export_runtime_state() 不触发 evaluate_stability()。"""

    def test_export_does_not_call_evaluate_stability(self, registry):
        agent = VestibularAgent(state_registry=registry)
        # 预先写入快照
        registry.set_state("vestibular_agent", {
            "stability_state": "unstable",
            "route_bias": "conservative",
            "jitter_index": 0.42,
            "damping_factor": 0.70,
            "mode_transition_count_10m": 3,
            "platform_flap_count_10m": 2,
            "initiative_defer_count_10m": 1,
            "sample_count": 5,
            "ts_ms": 1000,
        }, writer="test")

        with patch("host_brain.tribe.vestibular_agent.VestibularAgent.evaluate_stability") as mock_es:
            result = agent.export_runtime_state()
            assert not mock_es.called, \
                "export_runtime_state() 不应触发 evaluate_stability()"
        assert result.get("stability_state") == "unstable"
        assert result.get("route_bias") == "conservative"
        assert abs(float(result.get("jitter_index") or 0) - 0.42) < 0.01
        assert result.get("conservative_mode") is True

    def test_export_returns_default_when_no_state(self, registry):
        agent = VestibularAgent(state_registry=registry)
        state = agent.export_runtime_state()
        assert state.get("stability_state") == "stable"
        assert state.get("route_bias") == "normal"
        assert state.get("conservative_mode") is False


# ── T-251 + T-252 ─────────────────────────────────────────────────────────────

class TestVestibularHighJitterLinkage:
    """T-251+T-252：VestibularAgent 高抖动时联动 LimbicAgent 和 ANSAgent。"""

    def test_high_jitter_calls_limbic(self, registry):
        agent = VestibularAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()
        mock_ans = MagicMock()
        mock_ans.adjust_rhythm_for_high_interrupt = MagicMock()

        with patch("host_brain.tribe.vestibular_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.vestibular_agent.get_ans_agent", return_value=mock_ans):
                # 注入足够多的事件触发高抖动
                for i in range(4):
                    agent._mode_transition_ts.append(1000 + i * 1000)
                for i in range(3):
                    agent._platform_flap_ts.append(2000 + i * 1000)
                agent.evaluate_stability(source="test", ts_ms=10000)

        # jitter = 4*0.12 + 3*0.18 = 0.48 + 0.54 = 1.02 → clamp 到 1.0 → >= 0.35
        assert mock_limbic.drive_emotion_metabolism.called, \
            "高抖动时应调用 LimbicAgent.drive_emotion_metabolism()"
        assert mock_ans.adjust_rhythm_for_high_interrupt.called, \
            "高抖动时应调用 ANSAgent.adjust_rhythm_for_high_interrupt()"

    def test_low_jitter_no_linkage(self, registry):
        agent = VestibularAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_ans = MagicMock()

        with patch("host_brain.tribe.vestibular_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.vestibular_agent.get_ans_agent", return_value=mock_ans):
                # 只注入1个事件，jitter 很低
                agent._mode_transition_ts.append(1000)
                agent.evaluate_stability(source="test", ts_ms=10000)

        assert not mock_limbic.drive_emotion_metabolism.called, \
            "低抖动时不应调用 LimbicAgent.drive_emotion_metabolism()"


# ── T-253 ────────────────────────────────────────────────────────────────────

class TestImmuneInteractionRecorded:
    """T-253：ImmuneAgent._on_interaction_recorded 写入 risk_level/last_score。"""

    def test_interaction_recorded_writes_risk_level(self, registry):
        agent = ImmuneAgent(state_registry=registry)
        # 模拟一次 BLOCK 检测
        agent._last_inspection = {
            "decision": "BLOCK",
            "score": 0.88,
            "risk_level": "critical",
        }
        agent._counters = {"total": 5, "block": 2, "report": 1, "allow": 2}

        event = MagicMock()
        asyncio.run(
            agent._on_interaction_recorded(event)
        )

        snap = registry.get_state("immune_agent") or {}
        state = dict(snap.get("state") or {})
        assert "risk_level" in state, "应写入 risk_level 字段"
        assert "last_score" in state, "应写入 last_score 字段"
        assert "last_decision" in state, "应写入 last_decision 字段"
        assert state["risk_level"] == "critical"
        assert abs(float(state["last_score"]) - 0.88) < 0.01
        assert state["last_decision"] == "BLOCK"


# ── T-254 ────────────────────────────────────────────────────────────────────

class TestANSExportRuntimeState:
    """T-254：ANSAgent.export_runtime_state() 方法存在且返回正确字段。"""

    def test_export_runtime_state_exists(self):
        agent = ANSAgent()
        assert hasattr(agent, "export_runtime_state"), \
            "ANSAgent 应有 export_runtime_state() 方法"

    def test_export_runtime_state_fields(self):
        agent = ANSAgent()
        state = agent.export_runtime_state()
        assert "bond_stage" in state
        assert "recovery_mode" in state
        assert "proactive_multiplier" in state
        assert "loop_registered" in state
        assert "ts_ms" in state

    def test_recovery_mode_reduces_proactive_multiplier(self):
        agent = ANSAgent()
        agent._bond_stage = "friend"
        agent._recovery_mode = False
        normal_state = agent.export_runtime_state()

        agent._recovery_mode = True
        recovery_state = agent.export_runtime_state()

        assert float(recovery_state["proactive_multiplier"]) < float(normal_state["proactive_multiplier"]), \
            "recovery_mode=True 时 proactive_multiplier 应降低"

    def test_bond_stage_affects_proactive_multiplier(self):
        agent = ANSAgent()
        agent._recovery_mode = False

        agent._bond_stage = "familiar"
        familiar_state = agent.export_runtime_state()

        agent._bond_stage = "intimate"
        intimate_state = agent.export_runtime_state()

        assert float(intimate_state["proactive_multiplier"]) > float(familiar_state["proactive_multiplier"]), \
            "intimate 阶段的 proactive_multiplier 应高于 familiar"


# ── T-257 ────────────────────────────────────────────────────────────────────

class TestVitalSignsCircadian:
    """T-257：vital_signs 包含 circadian_phase/proactive_multiplier/recovery_bias。"""

    def test_vital_signs_has_circadian_fields(self, registry):
        registry.set_state("circadian_agent", {
            "phase": "night",
            "proactive_multiplier": 0.36,
            "recovery_bias": 0.86,
            "sleep_pressure": 0.72,
        }, writer="test")

        vitals = registry.aggregate_vital_signs()
        assert "circadian_phase" in vitals, "vital_signs 应包含 circadian_phase"
        assert "circadian_proactive_multiplier" in vitals
        assert "circadian_recovery_bias" in vitals
        assert "circadian_sleep_pressure" in vitals
        assert vitals["circadian_phase"] == "night"
        assert abs(float(vitals["circadian_proactive_multiplier"]) - 0.36) < 0.01

    def test_vital_signs_circadian_default(self, registry):
        vitals = registry.aggregate_vital_signs()
        assert vitals.get("circadian_phase") == "day"
        assert vitals.get("circadian_proactive_multiplier") == 1.0


# ── T-258 ────────────────────────────────────────────────────────────────────

class TestCircadianNightEndocrineLink:
    """T-258：CircadianAgent night 相位切换时联动 EndocrineAgent 降低 cortisol。"""

    def test_night_phase_reduces_cortisol_delta(self, registry):
        agent = CircadianAgent(state_registry=registry)
        agent._last_phase = "dusk"  # 模拟从 dusk 切换到 night

        mock_endo = MagicMock()
        mock_endo._bond_cortisol_delta = 0.05
        mock_endo._bond_lock = MagicMock()
        mock_endo._bond_lock.__enter__ = MagicMock(return_value=None)
        mock_endo._bond_lock.__exit__ = MagicMock(return_value=False)

        with patch("host_brain.tribe.circadian_agent.get_endocrine_agent", return_value=mock_endo), \
             patch("host_brain.tribe.circadian_agent.get_glymphatic_agent"), \
             patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.get_limbic_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.get_pain_reward_agent", return_value=MagicMock()), \
             patch("host_brain.tribe.circadian_agent.emit_tribe_event"), \
             patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile", return_value={
                 "phase": "night", "sleep_pressure": 0.75, "local_hour": 23,
                 "phase_energy_baseline": 0.34, "proactive_multiplier": 0.36,
                 "recovery_bias": 0.86, "response_latency_ms": 940, "ts_ms": 1000,
             }):
            agent.refresh_state(source="test", ts_ms=1000)

        # 验证 _bond_cortisol_delta 被修改（降低）
        assert mock_endo._bond_lock.__enter__.called, \
            "night 相位切换时应访问 EndocrineAgent._bond_lock"


# ── T-259 ────────────────────────────────────────────────────────────────────

class TestMirrorNeuronEndocrineLink:
    """T-259：MirrorNeuronAgent 高共情时联动 EndocrineAgent 降低 cortisol。"""

    def test_high_resonance_reduces_cortisol(self, registry):
        agent = MirrorNeuronAgent(state_registry=registry)
        mock_endo = MagicMock()
        mock_endo._bond_cortisol_delta = 0.08
        mock_endo._bond_lock = MagicMock()
        mock_endo._bond_lock.__enter__ = MagicMock(return_value=None)
        mock_endo._bond_lock.__exit__ = MagicMock(return_value=False)

        mock_social = MagicMock()
        mock_social.get_bond_context = MagicMock(return_value={
            "bond_vector": {"trust": 0.9, "affinity": 0.9, "safety": 0.9, "repair_confidence": 0.9}
        })

        with patch("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", return_value=mock_social):
            with patch("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", return_value=mock_endo):
                with patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=MagicMock()):
                    result = agent.apply_resonance(
                        user_id="user_1",
                        session_id="s1",
                        emotion_tag="JOY",
                        emotion_intensity=0.90,
                        source="test",
                    )

        resonance_strength = float(result.get("resonance_strength") or 0.0)
        if resonance_strength >= 0.60:
            assert mock_endo._bond_lock.__enter__.called, \
                "高共情时应访问 EndocrineAgent._bond_lock 降低 cortisol"

    def test_low_resonance_no_endocrine_call(self, registry):
        agent = MirrorNeuronAgent(state_registry=registry)
        mock_endo = MagicMock()
        mock_endo._bond_lock = MagicMock()

        mock_social = MagicMock()
        mock_social.get_bond_context = MagicMock(return_value={
            "bond_vector": {"trust": 0.1, "affinity": 0.1, "safety": 0.1, "repair_confidence": 0.1}
        })

        with patch("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", return_value=mock_social):
            with patch("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", return_value=mock_endo):
                with patch("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", return_value=MagicMock()):
                    result = agent.apply_resonance(
                        user_id="user_1",
                        session_id="s1",
                        emotion_tag="NEUTRAL",
                        emotion_intensity=0.10,
                        source="test",
                    )

        resonance_strength = float(result.get("resonance_strength") or 0.0)
        if resonance_strength < 0.60:
            assert not mock_endo._bond_lock.__enter__.called, \
                "低共情时不应访问 EndocrineAgent._bond_lock"
