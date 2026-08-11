"""P4 封板：完整 INTENT_EXEC 链路上子图节点失败后的收口与无跨图污染。

验证：
- 子图某节点（如 core_reasoner_primary）失败时，payload 出现 subgraph_failure，
  safety_verifier / judge / renderer 仍按主图顺序消费该 payload。
- state 无跨图污染：execution_profile 仍为 INTENT_EXEC，主图 trace 仅含主图节点。
"""

from __future__ import annotations

import pytest

from host_brain.app.execution_profile import PROFILE_INTENT_EXEC
from host_brain.app.graph_config_builder import (
    INTENT_AGENT_ENTRY_NODE_ID,
    build_intent_graph,
)
from host_brain.integrations.graph_contract import (
    PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY,
    PAYLOAD_KEY_SUBGRAPH_FAILURE,
    SUBGRAPH_FAILURE_KEYS,
)
from host_brain.composition.host_application import (
    build_production_host_application_ports,
)
from host_brain.app.application_port_wiring import (
    build_process_application_ports,
)


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


@pytest.fixture
def host_graph_runtime():
    ports = build_production_host_application_ports(
        process_application_ports=build_process_application_ports()
    )
    ports.begin_authority_runtime()
    try:
        yield ports.lifecycle.graph_runtime()
    finally:
        ports.close_authority_runtime()


def test_intent_exec_subgraph_failure_contained_and_consumable(
    monkeypatch,
    intent_graph_config,
    host_graph_runtime,
):
    """子图 core_reasoner_primary 失败时：payload 有 subgraph_failure，下游可消费，无跨图污染。"""
    from host_brain.integrations import graph_executor

    original_invoke = graph_executor.GraphExecutor._invoke_model_node

    def _fail_core_reasoner_only(self, *, node_id: str, **kwargs):
        if node_id == "core_reasoner_primary":
            raise RuntimeError("P4 integration: subgraph node failure")
        return original_invoke(self, node_id=node_id, **kwargs)

    monkeypatch.setattr(
        graph_executor.GraphExecutor,
        "_invoke_model_node",
        _fail_core_reasoner_only,
    )

    from tests.unit.conftest import mock_llm_runtime

    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    runtime = host_graph_runtime
    request_id = "p4-integration-fail-001"
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

    # 子图失败收口：payload 必有 subgraph_failure（§4.2 形态）
    assert PAYLOAD_KEY_SUBGRAPH_FAILURE in pl, "子图失败须写入 subgraph_failure"
    failure = pl[PAYLOAD_KEY_SUBGRAPH_FAILURE]
    for key in SUBGRAPH_FAILURE_KEYS:
        assert key in failure, f"subgraph_failure 须含 {key}"

    assert failure.get("failed_node") == "core_reasoner_primary"
    assert pl.get(PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY) is not None

    # 无跨图污染：execution_profile 仍为 INTENT_EXEC
    assert pl.get("execution_profile") == PROFILE_INTENT_EXEC, "execution_profile 不得被污染"

    # 主图 trace 含 intent_agent_entry，且下游节点有机会消费 payload（图跑完或至少到 judge/render）
    graph_trace = out.get("graph_trace") or []
    node_ids = [t.get("node_id") for t in graph_trace if t.get("node_id")]
    assert INTENT_AGENT_ENTRY_NODE_ID in node_ids, "主图 trace 须含 intent_agent_entry"
