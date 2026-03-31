"""graph_executor.py streaming 集成测试（M38-K，T-STR-INT-01~04）。

验证 Presence / Commit / Render 三类流在 _run_single_step 中的正确集成。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_contract import (
    STATUS_OK,
    STREAM_EVENT_NODE_END,
    STREAM_EVENT_NODE_START,
    STREAM_PHASE_PRESENCE,
    GraphState,
)
from host_brain.integrations.graph_executor import GraphExecutor


def _make_state(**kwargs: Any) -> GraphState:
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "req-strint-m38",
        "graph_id": "graph-strint-m38",
        "timeout_ms": 5000,
        "payload": {},
        "trace": [],
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "",
        "pending_node_id": "node_a",
        "edge_map": {"node_a": None},
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
        "policy_timeout_ms": 30_000,
        "clock_events": [],
        "node_exec_stats": {},
        "branch_specs": [],
        "branch_manifest": None,
        "presence_events": [],
        "commit_snapshot": None,
        "render_snapshot_ref": None,
        "streaming_phase": None,
    }
    base.update(kwargs)
    return base


def _noop_handler(payload, ctx):
    return {}


def _intent_handler(payload, ctx):
    return {"intent_commit": "test_intent_value"}


# ---------------------------------------------------------------------------
# T-STR-INT-01：节点执行前后各追加一条 Presence 事件（NODE_START / NODE_END）
# ---------------------------------------------------------------------------
def test_str_int_01_presence_events_emitted_around_node():
    """T-STR-INT-01：节点执行前后各追加一条 Presence 事件（NODE_START / NODE_END）"""
    state = _make_state()
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    presence_events = result.get("presence_events") or []
    assert len(presence_events) >= 2
    event_types = [e.get("event_type") for e in presence_events]
    assert STREAM_EVENT_NODE_START in event_types
    assert STREAM_EVENT_NODE_END in event_types
    # 所有 Presence 事件的 phase 应为 PRESENCE
    for e in presence_events:
        assert e.get("phase") == STREAM_PHASE_PRESENCE


# ---------------------------------------------------------------------------
# T-STR-INT-02：committed_route_token = GOV_OK_RENDER 时生成 commit_snapshot
# ---------------------------------------------------------------------------
def test_str_int_02_commit_snapshot_generated_on_gov_ok_render():
    """T-STR-INT-02：committed_route_token = GOV_OK_RENDER 时生成 commit_snapshot"""
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
    )
    executor = GraphExecutor(
        handlers={"handler_a": _intent_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    commit_snapshot = result.get("commit_snapshot")
    assert commit_snapshot is not None
    assert "s1_output_hash" in commit_snapshot
    assert "render_input_snapshot_ref" in commit_snapshot
    assert result.get("streaming_phase") in {"COMMIT", "RENDER"}


# ---------------------------------------------------------------------------
# T-STR-INT-03：commit_snapshot 存在时生成 render_snapshot_ref
# ---------------------------------------------------------------------------
def test_str_int_03_render_snapshot_ref_generated_when_snapshot_exists():
    """T-STR-INT-03：commit_snapshot 存在且 intent_commit 一致时生成 render_snapshot_ref"""
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
        payload={"intent_commit": "stable_intent"},
    )
    executor = GraphExecutor(
        handlers={"handler_a": lambda p, c: {"intent_commit": "stable_intent"}},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    # commit_snapshot 应存在
    assert result.get("commit_snapshot") is not None
    # render_snapshot_ref 应存在（intent_commit 一致）
    assert result.get("render_snapshot_ref") is not None
    assert result.get("streaming_phase") == "RENDER"


# ---------------------------------------------------------------------------
# T-STR-INT-04：presence_events 不影响 payload、trace 等核心字段
# ---------------------------------------------------------------------------
def test_str_int_04_presence_events_do_not_affect_core_fields():
    """T-STR-INT-04：presence_events 不影响 payload、trace 等核心字段"""
    initial_payload = {"key": "value", "data": 42}
    state = _make_state(payload=dict(initial_payload))
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    # presence_events 存在
    assert len(result.get("presence_events") or []) >= 2
    # payload 中不包含 presence_events 相关字段
    payload = result.get("payload") or {}
    assert "presence_events" not in payload
    assert "phase" not in payload
    assert "event_type" not in payload
    # trace 正常记录节点执行
    trace = result.get("trace") or []
    assert len(trace) == 1
    assert trace[0]["node_id"] == "node_a"
