"""
T-OBS-TRACE-01~08 — TraceNodeBuilder 单元测试
TASK-0190 / FR-OBS-010
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from host_brain.obs.trace_node_builder import TraceNodeBuilder, _IO_MAX_BYTES


def _make_ledger(table_data: dict):
    """构造 mock ledger，按表名返回数据。"""
    ledger = MagicMock()
    def fetch(table, start, end):
        return table_data.get(table, [])
    ledger.fetch_by_time_range.side_effect = fetch
    return ledger


def _gov_row(request_id, decision="APPROVE"):
    return {
        "id": "g1", "request_id": request_id, "intent_id": "i1",
        "ts_ms": int(time.time() * 1000), "decision": decision,
        "reason_code": None if decision == "APPROVE" else "RATE_LIMIT_EXCEEDED",
        "rule_trace": None,
    }


def _intent_row(request_id):
    return {
        "id": "i1", "request_id": request_id, "intent_id": "i1",
        "ts_ms": int(time.time() * 1000) - 100,
        "parse_ok": True, "raw_input": '{"text":"hello"}', "parsed_intent": '{"action":"chat"}',
    }


# ---------------------------------------------------------------------------
# T-OBS-TRACE-01: 给定有效 request_id 返回完整节点列表
# ---------------------------------------------------------------------------
def test_trace_build_basic():
    rid = "req-001"
    ledger = _make_ledger({
        "intent_log":   [_intent_row(rid)],
        "governor_log": [_gov_row(rid)],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    assert result["request_id"] == rid
    assert result["node_count"] >= 2
    assert len(result["nodes"]) >= 2


# ---------------------------------------------------------------------------
# T-OBS-TRACE-02: 不存在的 request_id 抛出 KeyError
# ---------------------------------------------------------------------------
def test_trace_not_found_raises():
    ledger = _make_ledger({})
    builder = TraceNodeBuilder(ledger)
    with pytest.raises(KeyError):
        builder.build("nonexistent-req")


# ---------------------------------------------------------------------------
# T-OBS-TRACE-03: parent_node_id 正确反映执行树结构
# ---------------------------------------------------------------------------
def test_trace_parent_node_id():
    rid = "req-002"
    ledger = _make_ledger({
        "intent_log":   [_intent_row(rid)],
        "governor_log": [_gov_row(rid)],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    nodes = result["nodes"]
    # 第一个节点无 parent，后续节点有 parent
    assert nodes[0]["parent_node_id"] is None
    for node in nodes[1:]:
        assert node["parent_node_id"] is not None


# ---------------------------------------------------------------------------
# T-OBS-TRACE-04: include_io=False 时 input/output 为 None
# ---------------------------------------------------------------------------
def test_trace_exclude_io():
    rid = "req-003"
    ledger = _make_ledger({
        "intent_log":   [_intent_row(rid)],
        "governor_log": [_gov_row(rid)],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid, include_io=False)
    for node in result["nodes"]:
        assert node["input"] is None
        assert node["output"] is None


# ---------------------------------------------------------------------------
# T-OBS-TRACE-05: input/output 超 64KB 时 truncated=True
# ---------------------------------------------------------------------------
def test_trace_io_truncation():
    rid = "req-004"
    big_data = "x" * (_IO_MAX_BYTES + 1000)
    row = _intent_row(rid)
    row["raw_input"] = big_data
    ledger = _make_ledger({"intent_log": [row]})
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    intent_node = next(n for n in result["nodes"] if n["node_type"] == "intent")
    assert intent_node["truncated"] is True


# ---------------------------------------------------------------------------
# T-OBS-TRACE-06: REJECT 决策时节点 status=FAIL
# ---------------------------------------------------------------------------
def test_trace_reject_status():
    rid = "req-005"
    ledger = _make_ledger({
        "governor_log": [_gov_row(rid, decision="REJECT")],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    gov_node = next(n for n in result["nodes"] if n["node_type"] == "governor")
    assert gov_node["status"] == "FAIL"
    assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# T-OBS-TRACE-07: 节点按 start_ts_ms 排序
# ---------------------------------------------------------------------------
def test_trace_nodes_sorted_by_ts():
    rid = "req-006"
    now = int(time.time() * 1000)
    intent = _intent_row(rid)
    intent["ts_ms"] = now - 200
    gov = _gov_row(rid)
    gov["ts_ms"] = now - 100
    ledger = _make_ledger({
        "intent_log":   [intent],
        "governor_log": [gov],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    nodes = result["nodes"]
    ts_list = [n["start_ts_ms"] for n in nodes if n["start_ts_ms"]]
    assert ts_list == sorted(ts_list)


# ---------------------------------------------------------------------------
# T-OBS-TRACE-08: 多日志表聚合（intent + governor + skill）
# ---------------------------------------------------------------------------
def test_trace_multi_table_aggregation():
    rid = "req-007"
    now = int(time.time() * 1000)
    skill_row = {
        "id": "s1", "request_id": rid, "skill_name": "desktop_note",
        "ts_ms": now, "success": True, "args": '{"text":"hi"}', "result": '{"ok":true}',
        "duration_ms": 50,
    }
    ledger = _make_ledger({
        "intent_log":     [_intent_row(rid)],
        "governor_log":   [_gov_row(rid)],
        "skill_exec_log": [skill_row],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    node_types = {n["node_type"] for n in result["nodes"]}
    assert "intent"   in node_types
    assert "governor" in node_types
    assert "skill"    in node_types
