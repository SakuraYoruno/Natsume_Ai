"""MCPAdapter metadata/AIRLOCK compatibility with direct execution retired."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MCP_PROVIDER_SYNC_FAILED,
    MCP_TASK_AUTHORITY_REQUIRED,
)
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, SanitizationReport, ToolMeta, to_mcp_tool_id


NOW_MS = 1_800_000_000_000


@pytest.fixture
def adapter() -> MCPAdapter:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(
        approval_ttl_ms=60_000,
        confirm_ttl_ms=60_000,
        undo_ttl_ms=60_000,
    )
    catalog = ToolCatalog()
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    return MCPAdapter(catalog=catalog, broker=broker)


@pytest.fixture
def adapter_with_tokens() -> tuple[MCPAdapter, TokenService]:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(
        approval_ttl_ms=60_000,
        confirm_ttl_ms=60_000,
        undo_ttl_ms=60_000,
    )
    catalog = ToolCatalog()
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    return MCPAdapter(catalog=catalog, broker=broker), tokens


def _make_tools() -> list[dict]:
    return [
        {
            "tool_id": "search_docs",
            "name": "Search Docs",
            "desc": "Search documentation",
            "side_effect_level": "READ",
        },
        {
            "tool_id": "create_doc",
            "name": "Create Doc",
            "desc": "Create a document",
            "side_effect_level": "WRITE",
        },
    ]


def _execute(
    adapter: MCPAdapter,
    tool_id: str,
    *,
    approval_token_ref: str | None = None,
    args: dict | None = None,
) -> dict:
    return adapter.execute_mcp_tool(
        tool_id,
        dict(args or {}),
        provider_id="test_provider",
        request_id=f"req-{tool_id}",
        intent_id=f"intent-{tool_id}",
        session_id="session-001",
        seq=1,
        approval_token_ref=approval_token_ref,
        ts_ms=NOW_MS,
    )


class TestSyncProviderTools:
    def test_returns_typed_read_write_metadata(self, adapter):
        metas = adapter.sync_provider_tools("test_provider", tools=_make_tools())
        assert len(metas) == 2
        assert all(isinstance(meta, ToolMeta) for meta in metas)
        by_id = {meta.tool_id.split("::")[-1]: meta for meta in metas}
        assert by_id["search_docs"].side_effect_level == "READ"
        assert by_id["create_doc"].side_effect_level == "WRITE"
        assert {meta.provider_id for meta in metas} == {"test_provider"}

    def test_handler_arguments_are_never_registered(self, adapter, caplog):
        callback = MagicMock(return_value={"forbidden": True})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": callback, "create_doc": callback},
        )
        readiness = adapter.broker.readiness_snapshot()
        assert readiness["registered_ids"] == ()
        assert set(readiness["missing_handler_ids"]) == {
            "mcp::test_provider::create_doc",
            "mcp::test_provider::search_docs",
        }
        assert "MCP_HANDLER_REGISTRATION_RETIRED" in caplog.text
        callback.assert_not_called()

    def test_sync_failed_raises_with_reason_code(self, adapter):
        bad_tools = [
            {
                "tool_id": "bad_tool",
                "side_effect_level": "READ",
                "auth_level": "APPROVAL",
            }
        ]
        with pytest.raises(ValueError) as exc_info:
            adapter.sync_provider_tools("bad_provider", tools=bad_tools)
        assert exc_info.value.reason_code == MCP_PROVIDER_SYNC_FAILED  # type: ignore[attr-defined]

    def test_provider_cache_is_metadata_only(self, adapter):
        adapter.sync_provider_tools("test_provider", tools=_make_tools())
        assert len(adapter.get_provider_tools("test_provider")) == 2
        assert adapter.get_provider_tools("missing") == []


class TestDirectExecutionRetirement:
    def test_read_direct_call_is_rejected_without_callback(self, adapter):
        callback = MagicMock(return_value={"results": []})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": callback},
        )

        result = _execute(adapter, "search_docs", args={"query": "test"})

        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert result["output"] is None
        callback.assert_not_called()

    def test_write_without_token_is_task_authority_rejection(self, adapter):
        callback = MagicMock(return_value={"id": 1})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": callback},
        )

        result = _execute(adapter, "create_doc", args={"title": "Test"})

        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        callback.assert_not_called()

    def test_valid_legacy_token_cannot_restore_execution(self, adapter_with_tokens):
        adapter, tokens = adapter_with_tokens
        callback = MagicMock(return_value={"id": 1})
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": callback},
        )
        mapped_id = to_mcp_tool_id("test_provider", "create_doc")
        approval = tokens.issue_approval_token(
            intent_id="intent-create_doc",
            skill_name=mapped_id,
            session_id="session-001",
            seq=1,
            now_ts_ms=NOW_MS,
        )

        result = _execute(
            adapter,
            "create_doc",
            args={"title": "Test"},
            approval_token_ref=approval,
        )

        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        callback.assert_not_called()

    def test_unsynced_tool_cannot_restore_a_direct_lookup_authority(self, adapter):
        result = _execute(adapter, "missing")
        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert result["output"] is None


class TestAirlockSanitization:
    def test_rejected_result_retains_sanitization_receipt(self, adapter):
        adapter.sync_provider_tools("test_provider", tools=_make_tools())
        result = _execute(
            adapter,
            "search_docs",
            args={"query": "test", "api_key": "secret", "hard_state": "raw"},
        )
        assert result["status"] == "REJECTED"
        assert result["result_code"] == MCP_TASK_AUTHORITY_REQUIRED
        report = result["sanitization_report"]
        assert report["payload_digest"].startswith("sha256:")
        assert set(report["removed_fields"]) == {"api_key", "hard_state"}

    def test_airlock_sanitize_returns_report(self, adapter):
        sanitized, report = MCPAdapter._airlock_sanitize(
            {"query": "test", "api_key": "secret", "normal_field": "value"}
        )
        assert isinstance(report, SanitizationReport)
        assert "api_key" in report.removed_fields
        assert "query" not in report.removed_fields
        assert sanitized == {"query": "test", "normal_field": "value"}

    def test_airlock_digest_is_deterministic(self, adapter):
        _, first = MCPAdapter._airlock_sanitize({"query": "test"})
        _, second = MCPAdapter._airlock_sanitize({"query": "test"})
        assert first.payload_digest == second.payload_digest
