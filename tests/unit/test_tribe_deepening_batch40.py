"""T-295：第四十批（T-285~T-294）核心联动逻辑单元测试。

覆盖场景：
1. T-285: chat_context_builder 注入 motor_cortex_state/cerebellum_state/glymphatic_state
2. T-286: chat_context_builder 注入 proprioception_state
3. T-287: MotorCortexAgent 执行失败时调用 LimbicAgent.drive_emotion_metabolism()
4. T-288: CerebellumAgent RETRY_ONCE/SAFE_FALLBACK 时调用 EndocrineAgent.record_stress_event()
5. T-289: ANSAgent.export_runtime_state() 写入 state_registry
6. T-290: GlymphaticAgent 恢复周期完成后调用 LimbicAgent.drive_emotion_metabolism()
7. T-291: HippocampusAgent 写入语义记忆后联动 DefaultModeAgent 更新连续性
8. T-292: CircadianAgent 日间相位切换时将 ANSAgent._recovery_mode 设为 False
9. T-293: SomaticAgent 高疲劳时调用 InteroceptionAgent.inject_external_stress()
10. T-294: PeripheralAgent 外设连接时调用 InteroceptionAgent.inject_external_stress(intensity<0)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
from host_brain.tribe.cerebellum_agent import CerebellumAgent
from host_brain.tribe.ans_agent import ANSAgent
from host_brain.tribe.glymphatic_agent import GlymphaticAgent
from host_brain.tribe.hippocampus_agent import HippocampusAgent
from host_brain.tribe.somatic_agent import SomaticAgent
from host_brain.tribe.peripheral_agent import PeripheralAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-285/T-286 ───────────────────────────────────────────────────────────────

class TestChatContextBuilderMotorCerebellumGlymphatic:
    """T-285/T-286：chat_context_builder 注入 motor_cortex_state/cerebellum_state/glymphatic_state/proprioception_state。"""

    def test_motor_cortex_state_injected(self):
        """motor_cortex_state 应被注入到 runtime_payload。"""
        from host_brain.app.chat_context_builder import _inject_tribe_supervisor_context
        runtime_payload: dict = {}
        tribe_context = {
            "motor_cortex_state": {
                "last_action": "send_message",
                "last_dispatch_ok": True,
                "last_dispatch_latency_ms": 120,
                "failure_rate": 0.05,
                "execution_health": 0.95,
            }
        }
        _inject_tribe_supervisor_context(runtime_payload, tribe_context)
        assert "motor_cortex_state" in runtime_payload, "motor_cortex_state 应被注入"
        assert runtime_payload["motor_cortex_state"]["execution_health"] == pytest.approx(0.95, abs=0.01)

    def test_cerebellum_state_injected(self):
        """cerebellum_state 应被注入到 runtime_payload。"""
        from host_brain.app.chat_context_builder import _inject_tribe_supervisor_context
        runtime_payload: dict = {}
        tribe_context = {
            "cerebellum_state": {
                "correction_rate": 0.12,
                "last_correction_type": "RETRY_ONCE",
                "correction_count": 3,
                "total_interactions": 25,
            }
        }
        _inject_tribe_supervisor_context(runtime_payload, tribe_context)
        assert "cerebellum_state" in runtime_payload, "cerebellum_state 应被注入"

    def test_glymphatic_state_injected(self):
        """glymphatic_state 应被注入到 runtime_payload。"""
        from host_brain.app.chat_context_builder import _inject_tribe_supervisor_context
        runtime_payload: dict = {}
        tribe_context = {
            "glymphatic_state": {
                "phase": "night",
                "sleep_pressure": 0.75,
                "consolidation_score": 0.62,
                "homeostasis_status": "stable",
                "cycle_count": 5,
            }
        }
        _inject_tribe_supervisor_context(runtime_payload, tribe_context)
        assert "glymphatic_state" in runtime_payload, "glymphatic_state 应被注入"
        assert runtime_payload["glymphatic_state"]["phase"] == "night"

    def test_proprioception_state_injected_with_high_interrupt_flag(self):
        """proprioception_state 高打断时应附 high_interrupt_flag=True。"""
        from host_brain.app.chat_context_builder import _inject_tribe_supervisor_context
        runtime_payload: dict = {}
        tribe_context = {
            "proprioception_state": {
                "interrupt_count_5m": 8,
                "last_interrupt_source": "user_message",
            }
        }
        _inject_tribe_supervisor_context(runtime_payload, tribe_context)
        assert "proprioception_state" in runtime_payload
        assert runtime_payload["proprioception_state"]["high_interrupt_flag"] is True


# ── T-287 ────────────────────────────────────────────────────────────────────

class TestMotorCortexLimbicLink:
    """T-287：MotorCortexAgent 执行失败时调用 LimbicAgent.drive_emotion_metabolism()。"""

    def test_execution_failure_calls_limbic(self, registry):
        agent = MotorCortexAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.motor_cortex_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"):
                agent.receive_execution_feedback(
                    plan_id="test_plan_001",
                    ok=False,
                    latency_ms=500,
                    reason="EDGE_EXECUTION_FAILED",
                    source="test",
                )

        assert mock_limbic.drive_emotion_metabolism.called, \
            "执行失败时应调用 LimbicAgent.drive_emotion_metabolism()"
        call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
        assert float(call_kwargs.get("valence") or 0.0) < 0, "挫败感 valence 应为负值"

    def test_execution_success_no_limbic_call(self, registry):
        agent = MotorCortexAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        with patch("host_brain.tribe.motor_cortex_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"):
                agent.receive_execution_feedback(
                    plan_id="test_plan_002",
                    ok=True,
                    latency_ms=200,
                    source="test",
                )

        assert not mock_limbic.drive_emotion_metabolism.called, \
            "执行成功时不应调用 LimbicAgent.drive_emotion_metabolism()"


# ── T-288 ────────────────────────────────────────────────────────────────────

class TestCerebellumEndocrineLink:
    """T-288：CerebellumAgent RETRY_ONCE/SAFE_FALLBACK 时调用 EndocrineAgent.record_stress_event()。"""

    def test_retry_once_calls_endocrine(self, registry):
        agent = CerebellumAgent(state_registry=registry)
        mock_endo = MagicMock()
        mock_endo.record_stress_event = MagicMock()

        with patch("host_brain.tribe.cerebellum_agent.get_endocrine_agent", return_value=mock_endo):
            with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"):
                agent.evaluate_correction(
                    execution_feedback={"accepted": False, "dispatched": False, "latency_ms": 0},
                    source="test",
                )

        assert mock_endo.record_stress_event.called, \
            "RETRY_ONCE 时应调用 EndocrineAgent.record_stress_event()"

    def test_no_adjust_no_endocrine_call(self, registry):
        agent = CerebellumAgent(state_registry=registry)
        mock_endo = MagicMock()
        mock_endo.record_stress_event = MagicMock()

        with patch("host_brain.tribe.cerebellum_agent.get_endocrine_agent", return_value=mock_endo):
            with patch("host_brain.tribe.cerebellum_agent.emit_tribe_event"):
                agent.evaluate_correction(
                    execution_feedback={"accepted": True, "dispatched": True, "latency_ms": 200},
                    source="test",
                )

        assert not mock_endo.record_stress_event.called, \
            "NO_ADJUST 时不应调用 EndocrineAgent.record_stress_event()"


# ── T-289 ────────────────────────────────────────────────────────────────────

class TestANSExportWritesRegistry:
    """T-289：ANSAgent.export_runtime_state() 写入 state_registry。"""

    def test_export_runtime_state_writes_registry(self, registry):
        agent = ANSAgent(state_registry=registry)
        state = agent.export_runtime_state()

        # 验证返回值
        assert "bond_stage" in state
        assert "recovery_mode" in state
        assert "proactive_multiplier" in state

        # 验证写入了 state_registry
        snap = registry.get_state("ans_agent")
        assert snap is not None, "export_runtime_state 应写入 state_registry"
        assert "proactive_multiplier" in snap

    def test_recovery_mode_reduces_proactive_multiplier(self, registry):
        agent = ANSAgent(state_registry=registry)
        agent._recovery_mode = True
        state = agent.export_runtime_state()
        assert float(state["proactive_multiplier"]) < 0.40, \
            "recovery_mode=True 时 proactive_multiplier 应降至 35%"


# ── T-290 ────────────────────────────────────────────────────────────────────

class TestGlymphaticLimbicLink:
    """T-290：GlymphaticAgent 恢复周期完成后调用 LimbicAgent.drive_emotion_metabolism()。"""

    def test_recovery_cycle_calls_limbic_when_executed(self, registry):
        agent = GlymphaticAgent(state_registry=registry)
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        # 构造高巩固分数的 vital_signs
        registry.set_state("circadian_agent", {"phase": "night", "sleep_pressure": 0.75}, writer="test")

        with patch("host_brain.tribe.glymphatic_agent.get_limbic_agent", return_value=mock_limbic):
            with patch("host_brain.tribe.glymphatic_agent.emit_tribe_event"):
                result = agent.run_recovery_cycle(source="test", force=True)

        if result.get("executed") and float(result.get("consolidation_score") or 0) >= 0.45:
            assert mock_limbic.drive_emotion_metabolism.called, \
                "恢复周期完成且巩固分数达标时应调用 LimbicAgent.drive_emotion_metabolism()"
            call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
            assert float(call_kwargs.get("valence") or 0.0) > 0, "恢复感 valence 应为正值"


# ── T-291 ────────────────────────────────────────────────────────────────────

class TestHippocampusDefaultModeLink:
    """T-291：HippocampusAgent 写入语义记忆后联动 DefaultModeAgent 更新连续性。"""

    def test_write_semantic_memory_updates_default_mode_continuity(self, registry):
        agent = HippocampusAgent(state_registry=registry)
        # 预设 DefaultModeAgent 的 continuity_score
        registry.set_state("default_mode_agent", {
            "last_offline_state": {"continuity_score": 0.50}
        }, writer="test")

        with patch("host_brain.tribe.hippocampus_agent.get_default_mode_agent") as mock_get_dma:
            mock_dma = MagicMock()
            mock_dma.state_registry = registry
            mock_get_dma.return_value = mock_dma
            with patch.object(agent, "_write_memory_scope", return_value={}):
                agent.write_semantic_memory_fact(
                    person_id="user_1",
                    fact="test_fact",
                )

        # 验证 DefaultModeAgent 的 state_registry 被更新
        snap = registry.get_state("default_mode_agent") or {}
        _offline = dict(snap.get("last_offline_state") or {})
        new_continuity = float(_offline.get("continuity_score") or 0.50)
        assert new_continuity >= 0.50, "写入语义记忆后 continuity_score 应不低于原值"


# ── T-292 ────────────────────────────────────────────────────────────────────

class TestCircadianANSLink:
    """T-292：CircadianAgent 日间相位切换时将 ANSAgent._recovery_mode 设为 False。"""

    def test_day_phase_resets_ans_recovery_mode(self, registry):
        from host_brain.tribe.circadian_agent import CircadianAgent
        agent = CircadianAgent(state_registry=registry)
        # 预设上一个相位为 night
        registry.set_state("circadian_agent", {"phase": "night"}, writer="test")
        agent._last_phase = "night"

        mock_ans = MagicMock()
        mock_ans._recovery_mode = True

        with patch("host_brain.tribe.circadian_agent.get_ans_agent", return_value=mock_ans):
            with patch("host_brain.tribe.circadian_agent.emit_tribe_event"):
                with patch("host_brain.tribe.circadian_agent.CircadianAgent.evaluate_profile") as mock_profile:
                    mock_profile.return_value = {
                        "phase": "day", "local_hour": 10,
                        "phase_energy_baseline": 0.84,
                        "proactive_multiplier": 1.0,
                        "recovery_bias": 0.30,
                        "sleep_pressure": 0.20,
                        "response_latency_ms": 360,
                        "ts_ms": 1000,
                    }
                    agent.refresh_state(source="test")

        assert mock_ans._recovery_mode is False, \
            "日间相位切换时应将 ANSAgent._recovery_mode 设为 False"


# ── T-293 ────────────────────────────────────────────────────────────────────

class TestSomaticInteroceptionLink:
    """T-293：SomaticAgent 高疲劳时调用 InteroceptionAgent.inject_external_stress()。"""

    def test_high_fatigue_calls_interoception(self, registry):
        agent = SomaticAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {"fatigue_level": 0.75, "motion_level": 0.3}
        event.ts_ms = 1000

        with patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.get_event_loop().run_until_complete(
                agent._on_body_sense_updated(event)
            )

        assert mock_intero.inject_external_stress.called, \
            "fatigue_level=0.75 >= 0.60 时应调用 InteroceptionAgent.inject_external_stress()"
        call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
        assert float(call_kwargs.get("intensity") or 0.0) > 0

    def test_low_fatigue_no_interoception_call(self, registry):
        agent = SomaticAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {"fatigue_level": 0.30, "motion_level": 0.5}
        event.ts_ms = 1000

        with patch("host_brain.tribe.somatic_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.get_event_loop().run_until_complete(
                agent._on_body_sense_updated(event)
            )

        assert not mock_intero.inject_external_stress.called, \
            "fatigue_level=0.30 < 0.60 时不应调用 inject_external_stress()"


# ── T-294 ────────────────────────────────────────────────────────────────────

class TestPeripheralInteroceptionLink:
    """T-294：PeripheralAgent 外设连接时调用 InteroceptionAgent.inject_external_stress(intensity<0)。"""

    def test_platform_connected_calls_interoception_negative(self, registry):
        agent = PeripheralAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {"platform": "vts", "detail": "connected"}
        event.ts_ms = 1000

        with patch("host_brain.tribe.peripheral_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.get_event_loop().run_until_complete(
                agent._on_platform_connected(event)
            )

        assert mock_intero.inject_external_stress.called, \
            "外设连接时应调用 InteroceptionAgent.inject_external_stress()"
        call_kwargs = mock_intero.inject_external_stress.call_args.kwargs
        assert float(call_kwargs.get("intensity") or 0.0) < 0, \
            "外设连接时 intensity 应为负值（恢复信号）"
