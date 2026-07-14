"""P6-3：集成验收补齐 — 真实 runtime + Intent 图下 P6 失败路径与成功路径、结构不变。

四条验收（MULTI_AGENT_P6_3_INTEGRATION_ACCEPTANCE_2026-03.md）：
1. 超时集成用例 → LOCAL_LOOP_TIMEOUT，INTENT_EXEC，主图 4 节点，不跨图逃逸
2. 重试耗尽集成用例 → LOCAL_LOOP_INTERNAL_ERROR，failed_node 为子图节点，trace 有多次失败 step
3. 成功路径集成用例 → subgraph_commit_summary，无 LOCAL_LOOP_*，step 级 trace，下游口径不变
4. 结构不变断言 → active_nodes 仍 4 节点，edges 无 local_loop/子图内部节点，executor 无 P6 业务特判
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from host_brain.app.execution_profile import PROFILE_INTENT_EXEC
from host_brain.app.graph_config_builder import (
    INTENT_AGENT_ENTRY_NODE_ID,
    JUDGE_AGENT_NODE_ID,
    CHAT_RENDER_NODE_ID,
    build_intent_graph,
)
from host_brain.integrations.graph_contract import (
    LOCAL_LOOP_FAILURE_INTERNAL_ERROR,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY,
    PAYLOAD_KEY_SUBGRAPH_FAILURE,
    SUBGRAPH_FAILURE_KEYS,
)

INTENT_MAIN_NODES = [
    INTENT_AGENT_ENTRY_NODE_ID,
    "safety_verifier",
    JUDGE_AGENT_NODE_ID,
    CHAT_RENDER_NODE_ID,
]
SUBGRAPH_INTERNAL_NODES = ("core_reasoner_primary", "planner_constraints", "tool_planner")
LOCAL_LOOP_CODES = ("LOCAL_LOOP_MAX_STEPS", "LOCAL_LOOP_TIMEOUT", "LOCAL_LOOP_INTERNAL_ERROR")


def _intent_config_with_local_loop_overrides(**overrides):
    """Intent 图配置，可覆盖 intent_agent_entry 的 P6 边界（如 local_loop_timeout_ms）。"""
    config = build_intent_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    bindings = config.get("node_bindings") or []
    for b in bindings:
        if b.get("node_id") == INTENT_AGENT_ENTRY_NODE_ID:
            b.update(overrides)
            break
    return config


@pytest.fixture
def intent_graph_config():
    """Intent 图配置（mock provider）。"""
    return build_intent_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )


# ---------------------------------------------------------------------------
# P6-3.1 超时集成用例
# ---------------------------------------------------------------------------
def test_p6_3_timeout_integration_local_loop_timeout(monkeypatch, intent_graph_config):
    """超时：local_loop_timeout_ms 极小 + 首节点耗时，触发 LOCAL_LOOP_TIMEOUT，不跨图逃逸。"""
    import host_brain.integrations.graph_executor as graph_executor_mod

    config = _intent_config_with_local_loop_overrides(local_loop_timeout_ms=15)
    original_invoke = graph_executor_mod.GraphExecutor._invoke_model_node

    def _slow_first_node(self, *, node_id: str, **kwargs):
        if node_id == "core_reasoner_primary":
            time.sleep(0.04)
        return original_invoke(self, node_id=node_id, **kwargs)

    monkeypatch.setattr(
        graph_executor_mod.GraphExecutor,
        "_invoke_model_node",
        _slow_first_node,
    )

    from tests.unit.conftest import mock_llm_runtime

    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from host_brain.composition.process_services import get_graph_runtime

    runtime = get_graph_runtime()
    request_id = "p6-3-timeout-001"
    payload = {
        "request_id": request_id,
        "execution_profile": PROFILE_INTENT_EXEC,
        "committed_route_token": "GOV_OK_RENDER",
        "message": "打开设置",
    }

    result = runtime.run_graph(
        request_id=request_id,
        graph_config=config,
        payload=payload,
        dependency_statuses={"mock": {"available": True, "detail": None}},
        timeout_ms=15_000,
    )

    out = result.output or {}
    pl = out.get("payload") or {}

    assert PAYLOAD_KEY_SUBGRAPH_FAILURE in pl, "超时应写入 subgraph_failure"
    failure = pl[PAYLOAD_KEY_SUBGRAPH_FAILURE]
    assert failure.get("error_code") == LOCAL_LOOP_FAILURE_TIMEOUT
    for key in SUBGRAPH_FAILURE_KEYS:
        assert key in failure, f"subgraph_failure 须含 {key}"

    assert pl.get("execution_profile") == PROFILE_INTENT_EXEC

    graph_trace = out.get("graph_trace") or []
    node_ids = [t.get("node_id") for t in graph_trace if t.get("node_id")]
    for nid in node_ids:
        assert nid in INTENT_MAIN_NODES, f"主图 trace 不得出现非主图节点: {nid}"


# ---------------------------------------------------------------------------
# P6-3.2 重试耗尽集成用例
# ---------------------------------------------------------------------------
def test_p6_3_retry_exhausted_integration_local_loop_internal_error(monkeypatch, intent_graph_config):
    """重试耗尽：子图节点持续失败，超过 max_retry_count 后 LOCAL_LOOP_INTERNAL_ERROR，failed_node 为实际节点。"""
    import host_brain.integrations.graph_executor as graph_executor_mod

    config = _intent_config_with_local_loop_overrides(max_retry_count=1)
    original_invoke = graph_executor_mod.GraphExecutor._invoke_model_node

    def _fail_core_reasoner(self, *, node_id: str, **kwargs):
        if node_id == "core_reasoner_primary":
            raise RuntimeError("P6-3 integration: subgraph node always fails")
        return original_invoke(self, node_id=node_id, **kwargs)

    monkeypatch.setattr(
        graph_executor_mod.GraphExecutor,
        "_invoke_model_node",
        _fail_core_reasoner,
    )

    from tests.unit.conftest import mock_llm_runtime

    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from host_brain.composition.process_services import get_graph_runtime

    runtime = get_graph_runtime()
    request_id = "p6-3-retry-001"
    payload = {
        "request_id": request_id,
        "execution_profile": PROFILE_INTENT_EXEC,
        "committed_route_token": "GOV_OK_RENDER",
        "message": "打开设置",
    }

    result = runtime.run_graph(
        request_id=request_id,
        graph_config=config,
        payload=payload,
        dependency_statuses={"mock": {"available": True, "detail": None}},
        timeout_ms=15_000,
    )

    out = result.output or {}
    pl = out.get("payload") or {}

    assert PAYLOAD_KEY_SUBGRAPH_FAILURE in pl
    failure = pl[PAYLOAD_KEY_SUBGRAPH_FAILURE]
    assert failure.get("error_code") == LOCAL_LOOP_FAILURE_INTERNAL_ERROR
    assert failure.get("failed_node") == "core_reasoner_primary"

    subgraph_trace = out.get("subgraph_trace") or pl.get("subgraph_trace") or []
    failed_steps = [s for s in subgraph_trace if s.get("status") == "FAILED"]
    assert len(failed_steps) >= 1, "重试耗尽应有至少 1 次失败 step 记录"

    assert pl.get("execution_profile") == PROFILE_INTENT_EXEC
    graph_trace = out.get("graph_trace") or []
    node_ids = [t.get("node_id") for t in graph_trace if t.get("node_id")]
    for nid in node_ids:
        assert nid in INTENT_MAIN_NODES, "应在 Intent 图内收口，不跨图逃逸"


# ---------------------------------------------------------------------------
# P6-3.3 成功路径集成用例
# ---------------------------------------------------------------------------
def test_p6_3_success_path_integration_subgraph_commit_and_step_trace(monkeypatch, intent_graph_config):
    """成功路径：INTENT_EXEC 正常跑通，subgraph_commit_summary 存在，无 LOCAL_LOOP_*，subgraph_trace 含 step 级。"""
    from tests.unit.conftest import mock_llm_runtime

    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from host_brain.composition.process_services import get_graph_runtime

    runtime = get_graph_runtime()
    request_id = "p6-3-success-001"
    payload = {
        "request_id": request_id,
        "execution_profile": PROFILE_INTENT_EXEC,
        "committed_route_token": "GOV_OK_RENDER",
        "message": "打开设置",
    }

    result = runtime.run_graph(
        request_id=request_id,
        graph_config=intent_graph_config,
        payload=payload,
        dependency_statuses={"mock": {"available": True, "detail": None}},
        timeout_ms=15_000,
    )

    out = result.output or {}
    pl = out.get("payload") or {}

    assert PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY in pl
    summary = pl[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY]
    assert summary.get("subgraph_id") == "intent_agent_subgraph"

    failure = pl.get(PAYLOAD_KEY_SUBGRAPH_FAILURE)
    if failure:
        assert failure.get("error_code") not in LOCAL_LOOP_CODES, "成功路径不应出现 LOCAL_LOOP_* 失败码"

    subgraph_trace = out.get("subgraph_trace") or pl.get("subgraph_trace") or []
    assert len(subgraph_trace) >= 1, "成功路径应有 step 级 subgraph_trace"
    for entry in subgraph_trace:
        assert "step_index" in entry, "subgraph_trace 须含 step 级（step_index）"
        assert "node_id" in entry
        assert "ts_ms" in entry
        assert "status" in entry


# ---------------------------------------------------------------------------
# P6-3.4 结构不变断言
# ---------------------------------------------------------------------------
def test_p6_3_structure_intent_graph_unchanged():
    """主图 active_nodes 仍 4 节点，edges 不出现 local_loop 或子图内部节点。"""
    config = build_intent_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    meta = config.get("graph_meta") or {}
    active = meta.get("active_nodes") or []
    assert active == INTENT_MAIN_NODES, "主图 active_nodes 必须仍为 4 节点"

    edges = config.get("edges") or []
    edge_pairs = [(e.get("from"), e.get("to")) for e in edges if e.get("from") and e.get("to")]
    for from_n, to_n in edge_pairs:
        assert from_n not in SUBGRAPH_INTERNAL_NODES and to_n not in SUBGRAPH_INTERNAL_NODES, (
            f"主图 edges 不得包含子图内部节点: {from_n} -> {to_n}"
        )
        assert from_n != "local_loop" and to_n != "local_loop", "主图 edges 不得包含 local_loop"


def test_p6_3_structure_executor_no_p6_business_branch():
    """executor 未新增 P6 业务特判（无 LOCAL_LOOP 分支逻辑）。"""
    root = Path(__file__).resolve().parents[2]
    executor_path = root / "host_brain" / "integrations" / "graph_executor.py"
    text = executor_path.read_text(encoding="utf-8-sig")
    assert "LOCAL_LOOP" not in text, "graph_executor 不得包含 LOCAL_LOOP 业务分支，P6 逻辑仅在 handler 内"
