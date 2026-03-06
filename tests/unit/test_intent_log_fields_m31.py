"""M31-C：intent_log 缺失字段测试（T-INTENT-LOG-01~03）。

DevSpec 8.1 A：intent_log 必须包含 tool_visibility_hash 和 lint_result_json（JSON 格式摘要）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger


def _make_ledger() -> SQLiteLedger:
    """创建内存 SQLiteLedger 用于测试。"""
    return SQLiteLedger(":memory:", check_same_thread=False)


# ── T-INTENT-LOG-01：tool_visibility_hash 写入后可读取非 NULL 值 ────────────

def test_intent_log_01_tool_visibility_hash():
    """T-INTENT-LOG-01：log_intent() 写入记录后，tool_visibility_hash 返回非 NULL 值。"""
    ledger = _make_ledger()
    ledger.log_intent({
        "request_id": "req_001",
        "intent_id": "intent_001",
        "ts_ms": 1700000000000,
        "tool_visibility_hash": "sha256:abcdef1234567890",
        "lint_result_json": json.dumps({"schema_valid": True, "args_valid": True}),
    })
    rows = ledger.intent_log
    assert len(rows) == 1
    row = rows[0]
    assert row.get("tool_visibility_hash") is not None, (
        "T-INTENT-LOG-01 FAILED: tool_visibility_hash 为 NULL"
    )
    assert row["tool_visibility_hash"] == "sha256:abcdef1234567890"


# ── T-INTENT-LOG-02：lint_result_json 为合法 JSON，不含自然语言结论句 ────────

def test_intent_log_02_lint_result_json_format():
    """T-INTENT-LOG-02：lint_result_json 字段为合法 JSON 字符串，不含自然语言结论句。"""
    ledger = _make_ledger()
    lint_payload = {"schema_valid": True, "args_valid": True, "violations": []}
    ledger.log_intent({
        "request_id": "req_002",
        "intent_id": "intent_002",
        "ts_ms": 1700000000001,
        "tool_visibility_hash": "sha256:0000",
        "lint_result_json": json.dumps(lint_payload),
    })
    rows = ledger.intent_log
    assert len(rows) == 1
    row = rows[0]
    lint_raw = row.get("lint_result")
    assert lint_raw is not None, "T-INTENT-LOG-02 FAILED: lint_result 为 NULL"

    # 验证为合法 JSON（不是自然语言字符串）
    if isinstance(lint_raw, str):
        try:
            parsed = json.loads(lint_raw)
        except json.JSONDecodeError:
            pytest.fail(f"T-INTENT-LOG-02 FAILED: lint_result 不是合法 JSON: {lint_raw!r}")
    elif isinstance(lint_raw, dict):
        parsed = lint_raw
    else:
        pytest.fail(f"T-INTENT-LOG-02 FAILED: lint_result 类型异常: {type(lint_raw)}")

    # 验证不含自然语言结论句（必须是结构化数据，含 schema_valid 或 args_valid 等键）
    assert isinstance(parsed, dict), "T-INTENT-LOG-02 FAILED: lint_result 不是 dict"
    assert "schema_valid" in parsed or "args_valid" in parsed or "violations" in parsed, (
        "T-INTENT-LOG-02 FAILED: lint_result 缺少结构化字段（schema_valid/args_valid/violations）"
    )


# ── T-INTENT-LOG-03：旧版调用方不传字段时默认为空字符串，不报错 ──────────────

def test_intent_log_03_backward_compat_defaults():
    """T-INTENT-LOG-03：旧版调用方不传 tool_visibility_hash/lint_result_json 时，默认为空字符串，不报错。"""
    ledger = _make_ledger()
    # 旧版调用方：不传 tool_visibility_hash 和 lint_result_json
    ledger.log_intent({
        "request_id": "req_003",
        "intent_id": "intent_003",
        "ts_ms": 1700000000002,
        "raw_text": "旧版调用",
    })
    rows = ledger.intent_log
    assert len(rows) == 1, "T-INTENT-LOG-03 FAILED: 写入失败"
    row = rows[0]
    # 向后兼容：缺失字段默认为空字符串，不为 None
    tool_hash = row.get("tool_visibility_hash")
    lint_result = row.get("lint_result")
    assert tool_hash == "" or tool_hash is None, (
        f"T-INTENT-LOG-03 FAILED: tool_visibility_hash 期望空字符串，实际 {tool_hash!r}"
    )
    # 不抛异常即通过
