"""P6-2：局部循环边界与 step 级 trace 的最小断言。"""

from __future__ import annotations

from host_brain.integrations.graph_contract import (
    LOCAL_LOOP_FAILURE_MAX_STEPS,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    LOCAL_LOOP_FAILURE_INTERNAL_ERROR,
    LOCAL_LOOP_VIRTUAL_NODE_ID,
)


def test_local_loop_max_steps_emitted_when_step_limit_reached() -> None:
    """超过 max_local_steps 时输出 LOCAL_LOOP_MAX_STEPS（P6-2 验收 3）。"""
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler

    # 3 节点子图，但只允许 2 步 -> 未跑完即达上限
    payload = {"request_id": "p6-max-steps"}
    ctx = {
        "request_id": "p6-max-steps",
        "invoke_model_node": lambda nid, pl=None: {},
        "node_binding": {"max_local_steps": 2, "max_retry_count": 0, "local_loop_timeout_ms": 60_000},
    }
    out = intent_agent_entry_handler(payload, ctx)
    assert "subgraph_failure" in out
    assert out["subgraph_failure"]["error_code"] == LOCAL_LOOP_FAILURE_MAX_STEPS
    assert "subgraph_trace" in out
    # 2 个执行 step + 1 条边界记录（max_local_steps reached）
    trace = out["subgraph_trace"]
    assert len(trace) >= 2
    assert trace[0].get("step_index") == 0
    assert trace[1].get("step_index") == 1
    # 最后一条为 local_loop 边界
    assert trace[-1].get("node_id") == LOCAL_LOOP_VIRTUAL_NODE_ID
    assert trace[-1].get("reason_code") == LOCAL_LOOP_FAILURE_MAX_STEPS


def test_subgraph_trace_has_step_level_fields() -> None:
    """subgraph_trace 含 step 级记录：step_index, node_id, ts_ms, status（P6-2 验收 5）。"""
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler

    payload = {"request_id": "p6-step-trace"}
    ctx = {"request_id": "p6-step-trace", "invoke_model_node": lambda nid, pl=None: {}, "node_binding": {}}
    out = intent_agent_entry_handler(payload, ctx)
    assert "subgraph_trace" in out
    trace = out["subgraph_trace"]
    assert len(trace) == 3
    for i, entry in enumerate(trace):
        assert entry.get("step_index") == i
        assert "node_id" in entry
        assert "ts_ms" in entry
        assert "status" in entry


def test_local_loop_failure_codes_constants() -> None:
    """P6 局部循环失败码常量与 P4 区分。"""
    assert LOCAL_LOOP_FAILURE_MAX_STEPS == "LOCAL_LOOP_MAX_STEPS"
    assert LOCAL_LOOP_FAILURE_TIMEOUT == "LOCAL_LOOP_TIMEOUT"
    assert LOCAL_LOOP_FAILURE_INTERNAL_ERROR == "LOCAL_LOOP_INTERNAL_ERROR"
    assert LOCAL_LOOP_VIRTUAL_NODE_ID == "local_loop"
