"""Dual-Clock Supervision 专项测试（M36-H，T-DCK-01~10）。

验证 ExecutionClock、PolicyClock、DualClockSupervisor 的正确性，
以及 graph_executor.py 集成双时钟后的行为。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_clock import (
    ClockEvent,
    DualClockSupervisor,
    ExecutionClock,
    PolicyClock,
)
from host_brain.integrations.graph_contract import (
    CLOCK_EVENT_EXEC_TIMEOUT,
    CLOCK_EVENT_POLICY_TIMEOUT,
    DEFAULT_POLICY_TIMEOUT_MS,
    STATUS_FAILED,
    STATUS_OK,
    GraphState,
    build_clock_event,
)
from host_brain.integrations.graph_executor import GraphExecutor


def _make_state(**kwargs: Any) -> GraphState:
    """构建最小化 GraphState 用于测试。"""
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "test-req-m36",
        "graph_id": "test-graph-m36",
        "timeout_ms": 5000,
        "payload": {},
        "trace": [],
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "",
        "pending_node_id": "node_a",
        "edge_map": {"node_a": "node_b"},
        "fallback_map": {},
        "node_bindings": {
            "node_a": {"node_id": "node_a", "handler_id": "handler_a"},
        },
        "dependency_statuses": {},
        "model_nodes": {},
        "model_profile_id": None,
        "model_calls": [],
        "committed_route_token": None,
        "committed_route_token_detail": None,
        "subgraph_trace": [],
        "policy_start_ms": now,
        "policy_timeout_ms": DEFAULT_POLICY_TIMEOUT_MS,
        "clock_events": [],
        "node_exec_stats": {},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# T-DCK-01：ExecutionClock.start/stop 正确记录 exec_start_ms 和 exec_end_ms
# ---------------------------------------------------------------------------
def test_dck_01_execution_clock_start_stop():
    """T-DCK-01：ExecutionClock.start/stop 正确记录时间戳"""
    clock = ExecutionClock(node_timeout_ms=5000)
    assert clock.exec_start_ms is None
    assert clock.exec_end_ms is None

    before = int(time.time() * 1000)
    clock.start()
    after_start = int(time.time() * 1000)
    assert clock.exec_start_ms is not None
    assert before <= clock.exec_start_ms <= after_start + 5

    clock.stop()
    after_stop = int(time.time() * 1000)
    assert clock.exec_end_ms is not None
    assert clock.exec_start_ms <= clock.exec_end_ms <= after_stop + 5


# ---------------------------------------------------------------------------
# T-DCK-02：ExecutionClock.elapsed_ms 正确计算耗时
# ---------------------------------------------------------------------------
def test_dck_02_execution_clock_elapsed_ms():
    """T-DCK-02：ExecutionClock.elapsed_ms 正确计算耗时"""
    clock = ExecutionClock(node_timeout_ms=5000)
    clock.start()
    time.sleep(0.01)  # 10ms
    clock.stop()
    assert clock.elapsed_ms >= 5  # 至少 5ms（允许系统误差）


# ---------------------------------------------------------------------------
# T-DCK-03：ExecutionClock.is_timeout 在超时时返回 True
# ---------------------------------------------------------------------------
def test_dck_03_execution_clock_is_timeout():
    """T-DCK-03：ExecutionClock.is_timeout 在超时时返回 True"""
    clock = ExecutionClock(node_timeout_ms=1)  # 1ms 超时
    clock.start()
    time.sleep(0.005)  # 5ms，超过 1ms 阈值
    clock.stop()
    assert clock.is_timeout is True

    clock2 = ExecutionClock(node_timeout_ms=10_000)  # 10s 超时
    clock2.start()
    clock2.stop()
    assert clock2.is_timeout is False


# ---------------------------------------------------------------------------
# T-DCK-04：PolicyClock.check_policy_timeout 在全局超时时返回 POLICY_TIMEOUT 事件
# ---------------------------------------------------------------------------
def test_dck_04_policy_clock_timeout_returns_event():
    """T-DCK-04：PolicyClock.check_policy_timeout 在全局超时时返回 POLICY_TIMEOUT 事件"""
    # 设置 policy_start_ms 为 1ms 前，policy_timeout_ms 为 0（立即超时）
    past_ms = int(time.time() * 1000) - 100  # 100ms 前启动
    clock = PolicyClock(policy_start_ms=past_ms, policy_timeout_ms=1)  # 1ms 超时
    event = clock.check_policy_timeout(node_id="node_a")
    assert event is not None
    assert event.event_type == CLOCK_EVENT_POLICY_TIMEOUT
    assert event.node_id == "node_a"
    assert event.elapsed_ms >= 1
    assert event.threshold_ms == 1


# ---------------------------------------------------------------------------
# T-DCK-05：PolicyClock.check_policy_timeout 在未超时时返回 None
# ---------------------------------------------------------------------------
def test_dck_05_policy_clock_no_timeout_returns_none():
    """T-DCK-05：PolicyClock.check_policy_timeout 在未超时时返回 None"""
    clock = PolicyClock(policy_timeout_ms=30_000)  # 30s 超时，不会触发
    event = clock.check_policy_timeout(node_id="node_a")
    assert event is None


# ---------------------------------------------------------------------------
# T-DCK-06：DualClockSupervisor.supervise_node 节点执行前检查 Policy Clock
# ---------------------------------------------------------------------------
def test_dck_06_dual_clock_supervisor_checks_policy_before_node():
    """T-DCK-06：DualClockSupervisor.supervise_node 节点执行前检查 Policy Clock"""
    past_ms = int(time.time() * 1000) - 100
    supervisor = DualClockSupervisor(
        policy_start_ms=past_ms,
        policy_timeout_ms=1,  # 1ms 超时，立即触发
        node_timeout_ms=5000,
    )
    with supervisor.supervise_node(node_id="node_a") as ctx:
        # Policy Clock 超时，ctx.policy_timeout_event 应非 None
        assert ctx.policy_timeout_event is not None
        assert ctx.policy_timeout_event.event_type == CLOCK_EVENT_POLICY_TIMEOUT


# ---------------------------------------------------------------------------
# T-DCK-07：Policy Clock 超时时图执行强制终止（status = STATUS_FAILED）
# ---------------------------------------------------------------------------
def test_dck_07_policy_timeout_forces_graph_termination():
    """T-DCK-07：Policy Clock 超时时图执行强制终止"""
    past_ms = int(time.time() * 1000) - 100
    state = _make_state(
        policy_start_ms=past_ms,
        policy_timeout_ms=1,  # 1ms 超时
        pending_node_id="node_a",
    )

    def noop_handler(payload, ctx):
        return {}

    executor = GraphExecutor(
        handlers={"handler_a": noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    assert result["status"] == STATUS_FAILED
    assert result["reason_code"] == CLOCK_EVENT_POLICY_TIMEOUT
    assert result["pending_node_id"] is None
    # clock_events 包含 POLICY_TIMEOUT 事件
    assert any(e.get("event_type") == CLOCK_EVENT_POLICY_TIMEOUT for e in result["clock_events"])


# ---------------------------------------------------------------------------
# T-DCK-08：Execution Clock 超时时 clock_events 追加 EXEC_TIMEOUT 事件
# ---------------------------------------------------------------------------
def test_dck_08_exec_timeout_appends_clock_event():
    """T-DCK-08：Execution Clock 超时时 clock_events 追加 EXEC_TIMEOUT 事件"""
    state = _make_state(
        pending_node_id="node_a",
        timeout_ms=1,  # 1ms 节点超时
    )

    def slow_handler(payload, ctx):
        time.sleep(0.01)  # 10ms，超过 1ms 阈值
        return {}

    executor = GraphExecutor(
        handlers={"handler_a": slow_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=1,
    )
    result = executor._run_single_step(state)
    # EXEC_TIMEOUT 事件应被追加（不强制终止）
    assert any(e.get("event_type") == CLOCK_EVENT_EXEC_TIMEOUT for e in result["clock_events"])
    # 节点仍然执行完成（不强制终止）
    assert result["status"] == STATUS_OK


# ---------------------------------------------------------------------------
# T-DCK-09：node_exec_stats 包含正确的 exec_duration_ms
# ---------------------------------------------------------------------------
def test_dck_09_node_exec_stats_contains_duration():
    """T-DCK-09：node_exec_stats 包含正确的 exec_duration_ms"""
    state = _make_state(pending_node_id="node_a")

    def noop_handler(payload, ctx):
        return {}

    executor = GraphExecutor(
        handlers={"handler_a": noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    assert "node_a" in result["node_exec_stats"]
    stats = result["node_exec_stats"]["node_a"]
    assert "exec_start_ms" in stats
    assert "exec_end_ms" in stats
    assert "exec_duration_ms" in stats
    assert isinstance(stats["exec_duration_ms"], int)
    assert stats["exec_duration_ms"] >= 0


# ---------------------------------------------------------------------------
# T-DCK-10：build_clock_event 返回正确的字典结构
# ---------------------------------------------------------------------------
def test_dck_10_build_clock_event_returns_correct_dict():
    """T-DCK-10：build_clock_event 返回正确的字典结构"""
    before = int(time.time() * 1000)
    event = build_clock_event(
        event_type=CLOCK_EVENT_EXEC_TIMEOUT,
        node_id="node_x",
        elapsed_ms=1500,
        threshold_ms=1000,
    )
    after = int(time.time() * 1000)

    assert event["event_type"] == CLOCK_EVENT_EXEC_TIMEOUT
    assert event["node_id"] == "node_x"
    assert event["elapsed_ms"] == 1500
    assert event["threshold_ms"] == 1000
    assert isinstance(event["ts_ms"], int)
    assert before <= event["ts_ms"] <= after + 10
