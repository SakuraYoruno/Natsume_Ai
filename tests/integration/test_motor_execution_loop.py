"""T-201：Motor 执行闭环集成测试。

覆盖场景：
1. plan_action() → dispatch_action() → receive_execution_feedback(ok=True) 全链路
2. receive_execution_feedback(ok=False) → MOTOR_EXECUTION_FAILED 事件发布
3. vital_signs["motor_execution_ok"] 反映最新执行状态
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.fixture()
def registry():
    return TribeStateRegistry()


@pytest.fixture()
def agent(registry):
    return MotorCortexAgent(state_registry=registry)


class TestMotorExecutionLoop:
    """T-201：Motor 执行闭环集成测试。"""

    def test_plan_action_returns_planned(self, agent):
        """plan_action() 应返回 PLANNED 状态并写入 state_registry。"""
        result = agent.plan_action(action="test_action", session_id="s1", user_id="u1")
        assert result["status"] == "PLANNED"
        plan = result["plan"]
        assert plan["action"] == "test_action"
        assert plan["plan_id"].startswith("motor_plan_")

    def test_dispatch_action_with_executor(self, agent):
        """dispatch_action() 使用自定义 executor 时应正确记录 outcome。"""
        plan_result = agent.plan_action(action="test_action")
        plan = plan_result["plan"]

        def mock_executor(p):
            return {"ok": True, "accepted": True, "dispatched": True}

        outcome = agent.dispatch_action(plan=plan, executor=mock_executor)
        assert outcome["ok"] is True
        assert outcome["plan_id"] == plan["plan_id"]

    def test_receive_execution_feedback_ok(self, agent):
        """receive_execution_feedback(ok=True) 应更新 last_dispatch_outcome 中的 edge_ok。"""
        plan_result = agent.plan_action(action="test_action")
        plan_id = plan_result["plan"]["plan_id"]

        def mock_executor(p):
            return {"ok": True}

        agent.dispatch_action(plan=plan_result["plan"], executor=mock_executor)

        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event") as mock_emit:
            fb = agent.receive_execution_feedback(
                plan_id=plan_id, ok=True, latency_ms=42, source="edge_test"
            )
        assert fb["ok"] is True
        assert fb["plan_id"] == plan_id
        # 验证 state_registry 中 edge_ok 已更新
        snap = agent.state_registry.get_state("motor_cortex_agent") or {}
        state = snap.get("state") or {}
        outcome = state.get("last_dispatch_outcome") or {}
        assert outcome.get("edge_ok") is True
        assert outcome.get("edge_latency_ms") == 42

    def test_receive_execution_feedback_fail_emits_failed_event(self, agent):
        """receive_execution_feedback(ok=False) 应发布 MOTOR_EXECUTION_FAILED 事件。"""
        plan_result = agent.plan_action(action="test_action")
        plan_id = plan_result["plan"]["plan_id"]

        emitted_events = []

        def capture_emit(event_type, source_agent, payload):
            emitted_events.append({"event_type": event_type, "payload": payload})

        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event", side_effect=capture_emit):
            agent.receive_execution_feedback(
                plan_id=plan_id, ok=False, latency_ms=500, reason="EDGE_TIMEOUT"
            )

        event_types = [e["event_type"] for e in emitted_events]
        assert "MOTOR_EXECUTION_FAILED" in event_types
        assert "MOTOR_EXECUTION_FEEDBACK" in event_types

        failed_event = next(e for e in emitted_events if e["event_type"] == "MOTOR_EXECUTION_FAILED")
        assert failed_event["payload"]["reason"] == "EDGE_TIMEOUT"

    def test_vital_signs_motor_execution_ok(self, agent, registry):
        """vital_signs['motor_execution_ok'] 应反映最新执行状态。"""
        plan_result = agent.plan_action(action="test_action")
        plan_id = plan_result["plan"]["plan_id"]

        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"):
            agent.receive_execution_feedback(plan_id=plan_id, ok=False, latency_ms=100)

        # 直接读取 state_registry 验证（不调用 aggregate_vital_signs 避免依赖其他 Agent）
        snap = registry.get_state("motor_cortex_agent") or {}
        state = snap.get("state") or {}
        outcome = state.get("last_dispatch_outcome") or {}
        assert outcome.get("edge_ok") is False

    def test_dispatch_action_no_executor_requires_governed_edge_command(self, agent):
        """Edge plan without an injected governed executor must fail closed."""
        plan_result = agent.plan_action(action="test_action")
        with patch("host_brain.tribe.motor_cortex_agent.emit_tribe_event"):
            outcome = agent.dispatch_action(plan=plan_result["plan"])
        assert outcome["ok"] is False
        assert outcome["reason"] == "GOVERNED_EDGE_COMMAND_REQUIRED"
