"""T-MCP-AUDIT-01~03: MCP 审计链集成测试（M14-C）。

覆盖需求：FR-MCP-001
验证：MCP 调用可追溯到 intent/governor/skill_exec 记录；
      WRITE 调用含 approval_token_ref + evidence_ref；
      AIRLOCK 净化报告可回放。
"""

from __future__ import annotations

from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, to_mcp_tool_id


NOW_MS = 1_800_000_000_000


@pytest.fixture
def full_stack() -> tuple[MCPAdapter, TokenService, SQLiteLedger]:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(approval_ttl_ms=60_000, confirm_ttl_ms=60_000, undo_ttl_ms=60_000)
    catalog = ToolCatalog()
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    adapter = MCPAdapter(catalog=catalog, broker=broker)
    return adapter, tokens, ledger


def _make_tools() -> list[dict]:
    return [
        {"tool_id": "search_docs", "name": "Search Docs", "desc": "Search", "side_effect_level": "READ"},
        {"tool_id": "create_doc", "name": "Create Doc", "desc": "Create", "side_effect_level": "WRITE"},
    ]


def _search_handler(args: dict, _ctx: Any) -> dict:
    return {"results": [{"id": 1}]}


def _create_handler(args: dict, _ctx: Any) -> dict:
    return {"id": 999, "title": args.get("title", "")}


# ── T-MCP-AUDIT-01: MCP 调用可追溯到 intent/governor/skill_exec 记录 ──────────

class TestMCPAuditChain:
    """T-MCP-AUDIT-01: MCP 调用可追溯到 intent/governor/skill_exec 记录。"""

    def test_read_call_logged_in_skill_exec(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "audit test"},
            provider_id="test_provider",
            request_id="req_audit_01",
            intent_id="intent_audit_01",
            session_id="session_audit",
            seq=1,
            ts_ms=NOW_MS,
        )
        assert result["status"] == "SUCCESS"
        # 验证 skill_exec_log 中有对应记录
        event = ledger.get_event(result["evidence_ref"])
        assert event is not None

    def test_read_call_request_id_in_log(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "audit test"},
            provider_id="test_provider",
            request_id="req_audit_02",
            intent_id="intent_audit_02",
            session_id="session_audit",
            seq=2,
            ts_ms=NOW_MS,
        )
        event = ledger.get_event(result["evidence_ref"])
        assert event is not None
        assert event["request_id"] == "req_audit_02"

    def test_read_call_intent_id_in_log(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "audit test"},
            provider_id="test_provider",
            request_id="req_audit_03",
            intent_id="intent_audit_03",
            session_id="session_audit",
            seq=3,
            ts_ms=NOW_MS,
        )
        event = ledger.get_event(result["evidence_ref"])
        assert event is not None
        assert event["intent_id"] == "intent_audit_03"

    def test_tool_id_in_log(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "audit test"},
            provider_id="test_provider",
            request_id="req_audit_04",
            intent_id="intent_audit_04",
            session_id="session_audit",
            seq=4,
            ts_ms=NOW_MS,
        )
        event = ledger.get_event(result["evidence_ref"])
        assert event is not None
        assert "search_docs" in event["skill_name"]


# ── T-MCP-AUDIT-02: MCP WRITE 调用含 approval_token_ref + evidence_ref ─────────

class TestMCPWriteAudit:
    """T-MCP-AUDIT-02: MCP WRITE 调用含 approval_token_ref + evidence_ref。"""

    def test_write_call_has_evidence_ref(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        mapped_id = to_mcp_tool_id("test_provider", "create_doc")
        approval = tokens.issue_approval_token(
            intent_id="intent_write_01",
            skill_name=mapped_id,
            session_id="session_audit",
            seq=10,
            now_ts_ms=NOW_MS,
        )
        result = adapter.execute_mcp_tool(
            "create_doc",
            {"title": "Audit Write Test"},
            provider_id="test_provider",
            request_id="req_write_01",
            intent_id="intent_write_01",
            session_id="session_audit",
            seq=10,
            approval_token_ref=approval,
            ts_ms=NOW_MS,
        )
        assert result["status"] == "SUCCESS"
        assert result["evidence_ref"] is not None
        assert result["evidence_ref"].startswith("evidence::")

    def test_write_call_approval_token_in_log(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        mapped_id = to_mcp_tool_id("test_provider", "create_doc")
        approval = tokens.issue_approval_token(
            intent_id="intent_write_02",
            skill_name=mapped_id,
            session_id="session_audit",
            seq=11,
            now_ts_ms=NOW_MS,
        )
        result = adapter.execute_mcp_tool(
            "create_doc",
            {"title": "Audit Write Test 2"},
            provider_id="test_provider",
            request_id="req_write_02",
            intent_id="intent_write_02",
            session_id="session_audit",
            seq=11,
            approval_token_ref=approval,
            ts_ms=NOW_MS,
        )
        event = ledger.get_event(result["evidence_ref"])
        assert event is not None
        assert event["approval_token_ref"] == approval

    def test_write_call_result_contains_provider_id(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        mapped_id = to_mcp_tool_id("test_provider", "create_doc")
        approval = tokens.issue_approval_token(
            intent_id="intent_write_03",
            skill_name=mapped_id,
            session_id="session_audit",
            seq=12,
            now_ts_ms=NOW_MS,
        )
        result = adapter.execute_mcp_tool(
            "create_doc",
            {"title": "Provider ID Test"},
            provider_id="test_provider",
            request_id="req_write_03",
            intent_id="intent_write_03",
            session_id="session_audit",
            seq=12,
            approval_token_ref=approval,
            ts_ms=NOW_MS,
        )
        assert result["provider_id"] == "test_provider"


# ── T-MCP-AUDIT-03: AIRLOCK 净化报告可回放（removed_fields/payload_digest）────

class TestAirlockAuditReplay:
    """T-MCP-AUDIT-03: AIRLOCK 净化报告可回放（removed_fields/payload_digest）。"""

    def test_sanitization_report_replayable(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "replay test", "api_key": "secret_123"},
            provider_id="test_provider",
            request_id="req_replay_01",
            intent_id="intent_replay_01",
            session_id="session_audit",
            seq=20,
            ts_ms=NOW_MS,
        )
        report = result["sanitization_report"]
        # 净化报告可回放：removed_fields 记录了被移除的字段
        assert "api_key" in report["removed_fields"]
        # payload_digest 可用于回放验证
        assert report["payload_digest"].startswith("sha256:")

    def test_sanitization_report_field_counts(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "count test", "api_key": "secret", "hard_state": "raw"},
            provider_id="test_provider",
            request_id="req_replay_02",
            intent_id="intent_replay_02",
            session_id="session_audit",
            seq=21,
            ts_ms=NOW_MS,
        )
        report = result["sanitization_report"]
        # 原始 3 个字段，移除 2 个敏感字段，净化后 1 个
        assert report["original_field_count"] == 3
        assert report["sanitized_field_count"] == 1
        assert len(report["removed_fields"]) == 2

    def test_same_payload_same_digest(self, full_stack):
        adapter, tokens, ledger = full_stack
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result1 = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "determinism"},
            provider_id="test_provider",
            request_id="req_replay_03a",
            intent_id="intent_replay_03a",
            session_id="session_audit",
            seq=22,
            ts_ms=NOW_MS,
        )
        result2 = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "determinism"},
            provider_id="test_provider",
            request_id="req_replay_03b",
            intent_id="intent_replay_03b",
            session_id="session_audit",
            seq=23,
            ts_ms=NOW_MS,
        )
        # 相同 payload 产生相同 digest（可回放）
        assert result1["sanitization_report"]["payload_digest"] == result2["sanitization_report"]["payload_digest"]
