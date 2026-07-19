"""Legacy MCP direct calls produce rejection audit, never provider effects."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
    MCP_TASK_AUTHORITY_REQUIRED,
)
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, to_mcp_tool_id


NOW_MS = 1_800_000_000_000


@pytest.fixture
def full_stack() -> tuple[MCPAdapter, TokenService, SQLiteLedger]:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(
        approval_ttl_ms=60_000,
        confirm_ttl_ms=60_000,
        undo_ttl_ms=60_000,
    )
    catalog = ToolCatalog()
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    return MCPAdapter(catalog=catalog, broker=broker), tokens, ledger


def _make_tools() -> list[dict]:
    return [
        {
            "tool_id": "search_docs",
            "name": "Search Docs",
            "desc": "Search",
            "side_effect_level": "READ",
        },
        {
            "tool_id": "create_doc",
            "name": "Create Doc",
            "desc": "Create",
            "side_effect_level": "WRITE",
        },
    ]


def _execute(
    adapter: MCPAdapter,
    tool_id: str,
    *,
    request_id: str,
    intent_id: str,
    seq: int,
    args: dict,
    approval_token_ref: str | None = None,
) -> dict:
    return adapter.execute_mcp_tool(
        tool_id,
        args,
        provider_id="test_provider",
        request_id=request_id,
        intent_id=intent_id,
        session_id="session-audit",
        seq=seq,
        approval_token_ref=approval_token_ref,
        ts_ms=NOW_MS,
    )


class TestMCPRetirementAuditChain:
    def test_read_rejection_is_bound_to_request_intent_and_tool(self, full_stack):
        adapter, _tokens, ledger = full_stack
        callback = MagicMock(return_value={"results": [{"id": 1}]})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": callback},
        )

        result = _execute(
            adapter,
            "search_docs",
            request_id="req-audit-read",
            intent_id="intent-audit-read",
            seq=1,
            args={"query": "audit"},
        )

        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert result["evidence_ref"] is None
        callback.assert_not_called()
        assert len(ledger.skill_exec_log) == 1
        row = ledger.skill_exec_log[0]
        assert row["request_id"] == "req-audit-read"
        assert row["intent_id"] == "intent-audit-read"
        assert row["skill_name"] == "mcp::test_provider::search_docs"
        assert row["result_status"] == "REJECTED"
        assert row["result_code"] == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED

    def test_call_tool_maps_internal_rejection_without_provider_callback(self, full_stack):
        adapter, _tokens, ledger = full_stack
        callback = MagicMock(return_value={"results": []})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": callback},
        )

        result = adapter.call_tool(
            provider_id="test_provider",
            tool_id="search_docs",
            request_id="req-call-tool",
            intent_id="intent-call-tool",
            session_id="session-audit",
            seq=2,
            args={"query": "audit"},
            ts_ms=NOW_MS,
        )

        assert result.status == "REJECTED"
        assert result.result_code == MCP_TASK_AUTHORITY_REQUIRED
        assert result.output is None
        callback.assert_not_called()
        assert ledger.skill_exec_log[-1]["result_code"] == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED

    def test_valid_legacy_write_token_is_not_execution_authority(self, full_stack):
        adapter, tokens, ledger = full_stack
        callback = MagicMock(return_value={"id": 999})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": callback},
        )
        approval = tokens.issue_approval_token(
            intent_id="intent-audit-write",
            skill_name=to_mcp_tool_id("test_provider", "create_doc"),
            session_id="session-audit",
            seq=3,
            now_ts_ms=NOW_MS,
        )

        result = _execute(
            adapter,
            "create_doc",
            request_id="req-audit-write",
            intent_id="intent-audit-write",
            seq=3,
            args={"title": "must not create"},
            approval_token_ref=approval,
        )

        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert result["output"] is None
        callback.assert_not_called()
        row = ledger.skill_exec_log[-1]
        assert row["result_status"] == "REJECTED"
        assert row["result_code"] == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED


class TestAirlockRejectionReceipt:
    def test_sensitive_fields_are_removed_before_audited_rejection(self, full_stack):
        adapter, _tokens, _ledger = full_stack
        adapter.sync_provider_tools("test_provider", tools=_make_tools())

        result = _execute(
            adapter,
            "search_docs",
            request_id="req-airlock",
            intent_id="intent-airlock",
            seq=4,
            args={"query": "safe", "api_key": "secret", "hard_state": "raw"},
        )

        report = result["sanitization_report"]
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert set(report["removed_fields"]) == {"api_key", "hard_state"}
        assert report["original_field_count"] == 3
        assert report["sanitized_field_count"] == 1
        assert report["payload_digest"].startswith("sha256:")

    def test_same_payload_keeps_same_sanitization_digest(self, full_stack):
        adapter, _tokens, _ledger = full_stack
        adapter.sync_provider_tools("test_provider", tools=_make_tools())

        first = _execute(
            adapter,
            "search_docs",
            request_id="req-digest-a",
            intent_id="intent-digest-a",
            seq=5,
            args={"query": "deterministic"},
        )
        second = _execute(
            adapter,
            "search_docs",
            request_id="req-digest-b",
            intent_id="intent-digest-b",
            seq=6,
            args={"query": "deterministic"},
        )

        assert first["sanitization_report"]["payload_digest"] == second[
            "sanitization_report"
        ]["payload_digest"]
