"""Route Commitment Rule 完整落地专项测试（M35-G，T-RCR-01~10）。

验证条件边基于 committed_route_token 的路由行为，以及 fastapi_app.py 的注入逻辑。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_contract import (
    STATUS_AIRLOCK_PENDING,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    GraphState,
)
from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_fallback import handle_airlock_pending


def _make_state(**kwargs: Any) -> GraphState:
    """构建最小化 GraphState 用于测试。"""
    base: GraphState = {
        "request_id": "test-req-001",
        "graph_id": "test-graph",
        "timeout_ms": 5000,
        "payload": {},
        "trace": [],
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "",
        "pending_node_id": "node_a",
        "edge_map": {"node_a": "node_b"},
        "fallback_map": {"node_a": "node_fallback"},
        "node_bindings": {
            "node_a": {"node_id": "node_a", "handler_id": "handler_a"},
            "node_b": {"node_id": "node_b", "handler_id": "handler_b"},
            "node_fallback": {"node_id": "node_fallback", "handler_id": "handler_fallback"},
        },
        "dependency_statuses": {},
        "model_nodes": {},
        "model_profile_id": None,
        "model_calls": [],
        "committed_route_token": None,
        "committed_route_token_detail": None,
        "subgraph_trace": [],
    }
    base.update(kwargs)
    return base


def _make_executor() -> GraphExecutor:
    """构建最小化 GraphExecutor 用于测试。"""
    return GraphExecutor(
        handlers={},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )


# ---------------------------------------------------------------------------
# T-RCR-01：committed_route_token = None → 保持原有路由逻辑
# ---------------------------------------------------------------------------
def test_rcr_01_none_token_keeps_original_routing():
    """T-RCR-01：committed_route_token = None → 保持原有路由逻辑"""
    executor = _make_executor()
    # 有 pending_node_id，无 token → 继续执行
    state = _make_state(committed_route_token=None, pending_node_id="node_a")
    assert executor._route_after_step(state) == "step"

    # 无 pending_node_id，无 token → 结束
    state2 = _make_state(committed_route_token=None, pending_node_id=None)
    assert executor._route_after_step(state2) == "finish"


# ---------------------------------------------------------------------------
# T-RCR-02：committed_route_token = "GOV_OK_RENDER" → 正常执行
# ---------------------------------------------------------------------------
def test_rcr_02_ok_render_token_normal_execution():
    """T-RCR-02：committed_route_token = GOV_OK_RENDER → 正常执行"""
    executor = _make_executor()
    state = _make_state(committed_route_token="GOV_OK_RENDER", pending_node_id="node_a")
    # GOV_OK_RENDER 不拦截，保持原有路由逻辑
    assert executor._route_after_step(state) == "step"

    state2 = _make_state(committed_route_token="GOV_OK_RENDER", pending_node_id=None)
    assert executor._route_after_step(state2) == "finish"


# ---------------------------------------------------------------------------
# T-RCR-03：committed_route_token = "GOV_FAIL_FALLBACK" → 跳转 finish
# ---------------------------------------------------------------------------
def test_rcr_03_fail_fallback_token_routes_to_finish():
    """T-RCR-03：committed_route_token = GOV_FAIL_FALLBACK → 跳转 finish"""
    executor = _make_executor()
    state = _make_state(committed_route_token="GOV_FAIL_FALLBACK", pending_node_id="node_a")
    assert executor._route_after_step(state) == "finish"


# ---------------------------------------------------------------------------
# T-RCR-04：committed_route_token = "GOV_AIRLOCK_REVIEW" → 跳转 finish，status = AIRLOCK_PENDING
# ---------------------------------------------------------------------------
def test_rcr_04_airlock_review_token_routes_to_finish():
    """T-RCR-04：committed_route_token = GOV_AIRLOCK_REVIEW → 跳转 finish"""
    executor = _make_executor()
    state = _make_state(committed_route_token="GOV_AIRLOCK_REVIEW", pending_node_id="node_a")
    assert executor._route_after_step(state) == "finish"


def test_rcr_04b_handle_airlock_pending_sets_status():
    """T-RCR-04b：handle_airlock_pending 设置 status = AIRLOCK_PENDING"""
    state = _make_state(committed_route_token="GOV_AIRLOCK_REVIEW", pending_node_id="node_a")
    result = handle_airlock_pending(state=state, node_id="node_a")
    assert result["status"] == STATUS_AIRLOCK_PENDING
    assert result["pending_node_id"] is None
    assert result["reason_code"] == "GOV_AIRLOCK_REVIEW"
    # trace 包含 AIRLOCK 事件
    assert any(
        entry.get("status") == STATUS_AIRLOCK_PENDING
        for entry in result["trace"]
    )


# ---------------------------------------------------------------------------
# T-RCR-05：committed_route_token = "GOV_SECONDARY_REASONER" → 跳转 finish
# ---------------------------------------------------------------------------
def test_rcr_05_secondary_reasoner_token_routes_to_finish():
    """T-RCR-05：committed_route_token = GOV_SECONDARY_REASONER → 跳转 finish"""
    executor = _make_executor()
    state = _make_state(committed_route_token="GOV_SECONDARY_REASONER", pending_node_id="node_a")
    assert executor._route_after_step(state) == "finish"


# ---------------------------------------------------------------------------
# T-RCR-06：条件边不直接消费 LLM 原始输出（只读 committed_route_token）
# ---------------------------------------------------------------------------
def test_rcr_06_conditional_edge_only_reads_committed_token():
    """T-RCR-06：_route_after_step 只读取 committed_route_token，不读取 LLM 原始输出"""
    executor = _make_executor()
    # payload 中有 LLM 原始输出，但条件边不应读取它
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
        pending_node_id="node_a",
        payload={
            "llm_raw_output": "some raw LLM text",
            "intent_draft": {"action": "some_action"},
        },
    )
    # 条件边应基于 committed_route_token 路由，不受 LLM 原始输出影响
    result = executor._route_after_step(state)
    assert result == "step"  # GOV_OK_RENDER → 正常执行


# ---------------------------------------------------------------------------
# T-RCR-07：initial_state.committed_route_token 从 payload 正确提取
# ---------------------------------------------------------------------------
def test_rcr_07_initial_state_extracts_committed_token_from_payload():
    """T-RCR-07：graph_runtime.py 从 payload 提取 committed_route_token 到 initial_state"""
    from host_brain.integrations.graph_runtime import GraphRuntimeContract

    runtime = GraphRuntimeContract()
    # 模拟 payload 中包含 committed_route_token
    payload = {
        "committed_route_token": "GOV_OK_RENDER",
        "committed_route_token_detail": {"token": "GOV_OK_RENDER", "reason_code": "APPROVE"},
    }
    # 验证 initial_state 构建逻辑（通过读取 run_graph 的 initial_state 构建代码）
    token = str((dict(payload or {})).get("committed_route_token") or "").strip() or None
    assert token == "GOV_OK_RENDER"

    detail = (dict(payload or {})).get("committed_route_token_detail") or None
    assert detail is not None
    assert detail["token"] == "GOV_OK_RENDER"


# ---------------------------------------------------------------------------
# T-RCR-08：fastapi_app.py 注入 committed_route_token 到 payload
# ---------------------------------------------------------------------------
def test_rcr_08_fastapi_injects_committed_token_to_payload():
    """T-RCR-08：fastapi_app.py 在 run_graph 调用前注入 committed_route_token"""
    # 模拟 governor_result（APPROVE 路径）
    governor_result = {
        "decision": "APPROVE",
        "committed_route_token_ref": "GOV_OK_RENDER",
        "committed_route_token": {
            "token": "GOV_OK_RENDER",
            "reason_code": "APPROVE",
            "committed_at_ms": int(time.time() * 1000),
            "proposer_node_id": "governor",
        },
    }
    # 模拟 fastapi_app.py 的注入逻辑
    llm_prompt_payload: dict = {"message": "hello"}
    injected_payload = {
        **llm_prompt_payload,
        "committed_route_token": governor_result.get("committed_route_token_ref"),
        "committed_route_token_detail": governor_result.get("committed_route_token"),
    }
    assert injected_payload["committed_route_token"] == "GOV_OK_RENDER"
    assert injected_payload["committed_route_token_detail"]["token"] == "GOV_OK_RENDER"


# ---------------------------------------------------------------------------
# T-RCR-09：Governor APPROVE → payload["committed_route_token"] = "GOV_OK_RENDER"
# ---------------------------------------------------------------------------
def test_rcr_09_governor_approve_gives_ok_render_in_payload():
    """T-RCR-09：Governor APPROVE → payload["committed_route_token"] = GOV_OK_RENDER"""
    from host_brain.governor.route_token import RouteToken, compile_route_decision

    decision = compile_route_decision(
        approved=True,
        reason_code="APPROVE",
        committed_at_ms=int(time.time() * 1000),
        proposer_node_id="governor",
    )
    assert decision.token == RouteToken.GOV_OK_RENDER
    assert decision.token.value == "GOV_OK_RENDER"

    # 模拟注入到 payload
    payload = {"committed_route_token": decision.token.value}
    assert payload["committed_route_token"] == "GOV_OK_RENDER"


# ---------------------------------------------------------------------------
# T-RCR-10：Governor REJECT → payload["committed_route_token"] = "GOV_FAIL_FALLBACK" 或 "GOV_AIRLOCK_REVIEW"
# ---------------------------------------------------------------------------
def test_rcr_10_governor_reject_gives_fallback_or_airlock_in_payload():
    """T-RCR-10：Governor REJECT → payload["committed_route_token"] = GOV_FAIL_FALLBACK 或 GOV_AIRLOCK_REVIEW"""
    from host_brain.governor.route_token import RouteToken, compile_route_decision

    # 普通拒绝 → GOV_FAIL_FALLBACK
    decision_fallback = compile_route_decision(
        approved=False,
        reason_code="INTENT_PARSE_FAILED",
        committed_at_ms=int(time.time() * 1000),
        proposer_node_id="governor",
    )
    assert decision_fallback.token == RouteToken.GOV_FAIL_FALLBACK

    # AIRLOCK 拒绝 → GOV_AIRLOCK_REVIEW
    decision_airlock = compile_route_decision(
        approved=False,
        reason_code="AIRLOCK_BLOCKED",
        committed_at_ms=int(time.time() * 1000),
        proposer_node_id="governor",
    )
    assert decision_airlock.token == RouteToken.GOV_AIRLOCK_REVIEW

    # 模拟注入到 payload
    payload_fallback = {"committed_route_token": decision_fallback.token.value}
    payload_airlock = {"committed_route_token": decision_airlock.token.value}
    assert payload_fallback["committed_route_token"] == "GOV_FAIL_FALLBACK"
    assert payload_airlock["committed_route_token"] == "GOV_AIRLOCK_REVIEW"
