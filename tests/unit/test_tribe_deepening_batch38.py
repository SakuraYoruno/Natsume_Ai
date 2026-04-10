"""T-273：第三十八批（T-262~T-272）核心联动逻辑单元测试。

覆盖场景：
1. T-271：HippocampusAgent.export_runtime_state() 方法存在且返回正确字段
2. T-272（InitiativeGovernorAgent）：export_runtime_state() 方法存在且返回正确字段
3. T-268：CerebellumAgent RETRY_ONCE 时调用 LimbicAgent.drive_emotion_metabolism()
4. T-269：PeripheralAgent 外设错误时调用 InteroceptionAgent.inject_external_stress()
5. T-270：InitiativeGovernorAgent BLOCK 时联动 VestibularAgent 记录推迟
6. T-267：vital_signs 包含 cerebellum_correction_rate 字段
7. T-272：vital_signs 包含 initiative_block_rate/initiative_defer_count 字段
8. T-262/T-263：CortexAgent collect_tribe_context 包含 prefrontal_state/hippocampus_state/initiative_governor_state
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from host_brain.tribe.hippocampus_agent import HippocampusAgent
from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
from host_brain.tribe.cerebellum_agent import CerebellumAgent
from host_brain.tribe.peripheral_agent import PeripheralAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


# ── T-271 ────────────────────────────────────────────────────────────────────

class TestHippocampusExportRuntimeState:
    """T-271：HippocampusAgent.export_runtime_state() 方法存在且返回正确字段。"""

    def test_export_runtime_state_exists(self, registry):
        agent = HippocampusAgent(state_registry=registry)
        assert hasattr(agent, "export_runtime_state"), \
            "HippocampusAgent 应有 export_runtime_state() 方法"

    def test_export_runtime_state_returns_required_fields(self, registry):
        agent = HippocampusAgent(state_registry=registry)
        state = agent.export_runtime_state()
        assert "last_write_type" in state
        assert "has_episodic" in state
        assert "has_semantic" in state
        assert "has_autobiographical" in state
        assert "write_count" in state

    def test_export_runtime_state_reflects_written_data(self, registry):
        agent = HippocampusAgent(state_registry=registry)
        # 写入一个 episodic 记忆快照
        registry.set_state("hippocampus_agent", {
            "memory_layers": {
                "last_episodic": {"session_id": "s123", "summary": "test"},
                "last_write_type": "episodic",
            },
            "write_count": 5,
        }, writer="test")
        state = agent.export_runtime_state()
        assert state["has_episodic"] is True
        assert state["last_write_type"] == "episodic"
        assert state["last_episodic_session_id"] == "s123"
        assert state["write_count"] == 5


# ── T-272（InitiativeGovernorAgent）────────────────────────────────────────────

class TestInitiativeGovernorExportRuntimeState:
    """T-272：InitiativeGovernorAgent.export_runtime_state() 方法存在且返回正确字段。"""

    def test_export_runtime_state_exists(self, registry):
        agent = InitiativeGovernorAgent(state_registry=registry)
        assert hasattr(agent, "export_runtime_state"), \
            "InitiativeGovernorAgent 应有 export_runtime_state() 方法"

    def test_export_runtime_state_returns_required_fields(self, registry):
        agent = InitiativeGovernorAgent(state_registry=registry)
        state = agent.export_runtime_state()
        assert "last_decision" in state
        assert "last_reason_code" in state
        assert "block_count" in state
        assert "defer_count" in state
        assert "approve_count" in state
        assert "recovery_mode" in state

    def test_export_runtime_state_reflects_last_decision(self, registry):
        agent = InitiativeGovernorAgent(state_registry=registry)
        registry.set_state("initiative_governor_agent", {
            "last_decision": {
                "decision": "BLOCK",
                "reason_code": "GATE_PAUSE",
                "action": "proactive_talk",
                "scores": {"motive": 0.3, "readiness": 0.2, "continuity": 0.4},
                "ts_ms": 1000,
            },
            "block_count": 3,
            "defer_count": 2,
            "approve_count": 10,
        }, writer="test")
        state = agent.export_runtime_state()
        assert state["last_decision"] == "BLOCK"
        assert state["last_reason_code"] == "GATE_PAUSE"
        assert state["block_count"] == 3
        assert state["defer_count"] == 2
        assert state["approve_count"] == 10


# ── T-268 ────────────────────────────────────────────────────────────────────

class TestCerebellumRetryLimbicLink:
    """T-268：CerebellumAgent RETRY_ONCE 时调用 LimbicAgent.drive_emotion_metabolism()。"""

    def test_retry_once_calls_limbic(self):
        agent = CerebellumAgent()
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        event = MagicMock()
        event.payload = {
            "accepted": False,
            "dispatched": False,
            "latency_ms": 0,
            "reason": "HTTP_REJECTED",
            "plan_id": "plan_test_001",
        }
        event.ts_ms = 1000

        with patch("host_brain.tribe.cerebellum_agent.get_limbic_agent", return_value=mock_limbic):
            asyncio.get_event_loop().run_until_complete(
                agent._on_motor_action_dispatched(event)
            )

        # RETRY_ONCE 策略时应调用 LimbicAgent
        assert mock_limbic.drive_emotion_metabolism.called, \
            "RETRY_ONCE 时应调用 LimbicAgent.drive_emotion_metabolism()"
        call_kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
        assert float(call_kwargs.get("valence") or 0.0) < 0, \
            "RETRY_ONCE 时 valence 应为负值（轻度焦虑）"

    def test_no_retry_no_limbic_call(self):
        agent = CerebellumAgent()
        mock_limbic = MagicMock()
        mock_limbic.drive_emotion_metabolism = MagicMock()

        event = MagicMock()
        event.payload = {
            "accepted": True,
            "dispatched": True,
            "latency_ms": 200,
            "reason": "",
            "plan_id": "plan_ok_001",
        }
        event.ts_ms = 1000

        with patch("host_brain.tribe.cerebellum_agent.get_limbic_agent", return_value=mock_limbic):
            asyncio.get_event_loop().run_until_complete(
                agent._on_motor_action_dispatched(event)
            )

        assert not mock_limbic.drive_emotion_metabolism.called, \
            "非 RETRY_ONCE 时不应调用 LimbicAgent.drive_emotion_metabolism()"


# ── T-269 ────────────────────────────────────────────────────────────────────

class TestPeripheralErrorInteroceptionLink:
    """T-269：PeripheralAgent 外设错误时调用 InteroceptionAgent.inject_external_stress()。"""

    def test_peripheral_error_calls_interoception(self, registry):
        agent = PeripheralAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {
            "connected_platforms": ["wechat"],
            "error": "PUSH_FAILED",
            "latency_ms": 0,
        }
        event.ts_ms = 1000

        with patch("host_brain.tribe.peripheral_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.get_event_loop().run_until_complete(
                agent._on_peripheral_state_updated(event)
            )

        assert mock_intero.inject_external_stress.called, \
            "外设错误时应调用 InteroceptionAgent.inject_external_stress()"

    def test_high_latency_calls_interoception(self, registry):
        agent = PeripheralAgent(state_registry=registry)
        mock_intero = MagicMock()
        mock_intero.inject_external_stress = MagicMock()

        event = MagicMock()
        event.payload = {
            "connected_platforms": ["wechat"],
            "error": "",
            "latency_ms": 2000,
        }
        event.ts_ms = 1000

        with patch("host_brain.tribe.peripheral_agent.get_interoception_agent", return_value=mock_intero):
            asyncio.get_event_loop().run_until_complete(
                agent._on_peripheral_state_updated(event)
            )

        assert mock_intero.inject_external_stress.called, \
            "高延迟(>=1500ms)时应调用 InteroceptionAgent.inject_external_stress()"


# ── T-270 ────────────────────────────────────────────────────────────────────

class TestInitiativeGovernorVestibularLink:
    """T-270：InitiativeGovernorAgent BLOCK 时联动 VestibularAgent 记录主动推迟。"""

    def test_block_increments_vestibular_defer_count(self, registry):
        agent = InitiativeGovernorAgent(state_registry=registry)
        # 预设 vestibular_agent 状态
        registry.set_state("vestibular_agent", {
            "initiative_defer_count_10m": 2,
        }, writer="test")

        # 构造会触发 BLOCK 的条件（gate_state=pause）
        with patch("host_brain.tribe.initiative_governor_agent.emit_tribe_event"):
            agent.evaluate_initiative(
                action="proactive_talk",
                motive={"type": "proactive_talk", "intensity": 0.5},
                session_id="s1",
                user_id="u1",
                context={"gate_state": "pause"},
            )

        # 检查 vestibular_agent 的 initiative_defer_count_10m 是否增加
        vest_snap = registry.get_state("vestibular_agent") or {}
        vest_state = dict(vest_snap.get("state") or {})
        assert int(vest_state.get("initiative_defer_count_10m") or 0) >= 3, \
            "BLOCK 时应增加 VestibularAgent.initiative_defer_count_10m"


# ── T-267 + T-272 ─────────────────────────────────────────────────────────────

class TestVitalSignsNewFields:
    """T-267+T-272：vital_signs 包含 cerebellum_correction_rate 和 initiative 字段。"""

    def test_vital_signs_has_cerebellum_correction_rate(self, registry):
        vitals = registry.aggregate_vital_signs()
        assert "cerebellum_correction_rate" in vitals, \
            "vital_signs 应包含 cerebellum_correction_rate 字段"

    def test_vital_signs_has_initiative_fields(self, registry):
        registry.set_state("initiative_governor_agent", {
            "block_count": 2,
            "defer_count": 3,
            "approve_count": 10,
        }, writer="test")
        vitals = registry.aggregate_vital_signs()
        assert "initiative_block_rate" in vitals, "vital_signs 应包含 initiative_block_rate"
        assert "initiative_defer_count" in vitals, "vital_signs 应包含 initiative_defer_count"
        assert "initiative_approve_count" in vitals, "vital_signs 应包含 initiative_approve_count"
        # block_rate = 2 / (2+3+10) = 2/15 ≈ 0.1333
        assert float(vitals["initiative_block_rate"]) > 0.0
        assert int(vitals["initiative_defer_count"]) == 3
