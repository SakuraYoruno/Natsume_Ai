"""
T-OBS-TRACE-01~08 — TraceNodeBuilder 单元测试
TASK-0190 / FR-OBS-010
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from host_brain.observability.trace_node_builder import TraceNodeBuilder, _IO_MAX_BYTES


def _make_ledger(table_data: dict):
    """构造 mock ledger，按表名返回数据。支持 fetch_by_request_id（SQL 级过滤）。"""
    ledger = MagicMock()

    def fetch(table, start=None, end=None, **kwargs):
        return table_data.get(table, [])

    def fetch_by_rid(table, request_id):
        return [r for r in table_data.get(table, []) if r.get("request_id") == request_id]

    ledger.fetch_by_time_range.side_effect = fetch
    ledger.fetch_by_request_id.side_effect = fetch_by_rid
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
# T-OBS-TRACE-03: parent_node_id 正确反映树形执行结构（WARN-BE-005 修复）
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

    # intent 节点无 parent（根节点）
    intent_node = next(n for n in nodes if n["node_type"] == "intent")
    assert intent_node["parent_node_id"] is None

    # governor 节点的 parent 为 intent 节点
    gov_node = next(n for n in nodes if n["node_type"] == "governor")
    assert gov_node["parent_node_id"] == intent_node["node_id"]


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


# ---------------------------------------------------------------------------
# T-OBS-TRACE-09: 并行 skill 节点 parent 均为同一 governor（WARN-BE-005）
# ---------------------------------------------------------------------------
def test_trace_parallel_skill_nodes_share_governor_parent():
    """两个并行 skill 节点应共享同一 governor parent_node_id。"""
    rid = "req-008"
    now = int(time.time() * 1000)
    gov = _gov_row(rid)
    gov["id"] = "g1"
    gov["ts_ms"] = now - 100

    skill_a = {
        "id": "s1", "request_id": rid, "skill_name": "skill_a",
        "ts_ms": now, "success": True, "args": None, "result": None, "duration_ms": 30,
    }
    skill_b = {
        "id": "s2", "request_id": rid, "skill_name": "skill_b",
        "ts_ms": now + 5, "success": True, "args": None, "result": None, "duration_ms": 25,
    }
    ledger = _make_ledger({
        "intent_log":     [_intent_row(rid)],
        "governor_log":   [gov],
        "skill_exec_log": [skill_a, skill_b],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    nodes = result["nodes"]

    skill_nodes = [n for n in nodes if n["node_type"] == "skill"]
    assert len(skill_nodes) == 2, "应有 2 个 skill 节点"

    gov_node = next(n for n in nodes if n["node_type"] == "governor")
    # 两个并行 skill 节点的 parent 均为 governor
    for sn in skill_nodes:
        assert sn["parent_node_id"] == gov_node["node_id"], (
            f"skill 节点 {sn['node_id']} 的 parent 应为 governor，实际为 {sn['parent_node_id']}"
        )


# ---------------------------------------------------------------------------
# T-OBS-TRACE-10: ASR/TTS 节点 parent 为对应 skill 节点（WARN-BE-005）
# ---------------------------------------------------------------------------
def test_trace_asr_tts_parent_is_skill():
    """ASR/TTS 节点的 parent 应为最近的 skill 节点。"""
    rid = "req-009"
    now = int(time.time() * 1000)

    skill_row = {
        "id": "s1", "request_id": rid, "skill_name": "voice_skill",
        "ts_ms": now - 50, "success": True, "args": None, "result": None, "duration_ms": 100,
    }
    asr_row = {
        "id": "v1", "request_id": rid, "event_type": "ASR_RESULT",
        "ts_ms": now, "status": "OK", "reason_code": None,
        "input_data": None, "output_data": None, "latency_ms": 80,
    }
    tts_row = {
        "id": "v2", "request_id": rid, "event_type": "TTS_RESULT",
        "ts_ms": now + 10, "status": "OK", "reason_code": None,
        "input_data": None, "output_data": None, "latency_ms": 60,
    }
    ledger = _make_ledger({
        "intent_log":     [_intent_row(rid)],
        "governor_log":   [_gov_row(rid)],
        "skill_exec_log": [skill_row],
        "voice_event_log": [asr_row, tts_row],
    })
    builder = TraceNodeBuilder(ledger)
    result = builder.build(rid)
    nodes = result["nodes"]

    skill_node = next(n for n in nodes if n["node_type"] == "skill")
    asr_node = next(n for n in nodes if n["node_type"] == "asr")
    tts_node = next(n for n in nodes if n["node_type"] == "tts")

    assert asr_node["parent_node_id"] == skill_node["node_id"], (
        f"ASR 节点 parent 应为 skill，实际为 {asr_node['parent_node_id']}"
    )
    assert tts_node["parent_node_id"] == skill_node["node_id"], (
        f"TTS 节点 parent 应为 skill，实际为 {tts_node['parent_node_id']}"
    )
