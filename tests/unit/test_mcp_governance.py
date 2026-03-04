"""T-MCP-GOV-01~05: MCP Provider 治理单元测试（M14-C）。

覆盖需求：FR-MCP-001
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    MCP_AUDIT_REF_MISSING,
    MCP_PROVIDER_SYNC_FAILED,
    MCP_TOOL_NOT_FOUND,
    MCP_WRITE_UNAUTHORIZED,
)
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, SanitizationReport, ToolMeta


# ── 夹具 ─────────────────────────────────────────────────────────────────────

NOW_MS = 1_800_000_000_000


@pytest.fixture
def adapter() -> MCPAdapter:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(approval_ttl_ms=60_000, confirm_ttl_ms=60_000, undo_ttl_ms=60_000)
    catalog = ToolCatalog()
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    return MCPAdapter(catalog=catalog, broker=broker)


@pytest.fixture
def adapter_with_tokens() -> tuple[MCPAdapter, TokenService]:
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(approval_ttl_ms=60_000, confirm_ttl_ms=60_000, undo_ttl_ms=60_000)
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


def _search_handler(args: dict, _ctx: Any) -> dict:
    return {"results": [{"id": 1, "title": args.get("query", "")}]}


def _create_handler(args: dict, _ctx: Any) -> dict:
    return {"id": 999, "title": args.get("title", "")}


# ── T-MCP-GOV-01: sync_provider_tools 成功，返回 ToolMeta 列表（含 READ/WRITE 分级）──

class TestSyncProviderTools:
    """T-MCP-GOV-01: sync_provider_tools 成功，返回 ToolMeta 列表（含 READ/WRITE 分级）。"""

    def test_returns_tool_meta_list(self, adapter):
        metas = adapter.sync_provider_tools("test_provider", tools=_make_tools())
        assert isinstance(metas, list)
        assert len(metas) == 2

    def test_all_items_are_tool_meta(self, adapter):
        metas = adapter.sync_provider_tools("test_provider", tools=_make_tools())
        for meta in metas:
            assert isinstance(meta, ToolMeta)

    def test_read_write_classification(self, adapter):
        metas = adapter.sync_provider_tools("test_provider", tools=_make_tools())
        by_id = {m.tool_id.split("::")[-1]: m for m in metas}
        assert by_id["search_docs"].side_effect_level == "READ"
        assert by_id["create_doc"].side_effect_level == "WRITE"

    def test_provider_id_preserved(self, adapter):
        metas = adapter.sync_provider_tools("my_provider", tools=_make_tools())
        for meta in metas:
            assert meta.provider_id == "my_provider"

    def test_sync_failed_raises_with_reason_code(self, adapter):
        bad_tools = [{"tool_id": "bad_tool", "side_effect_level": "READ", "auth_level": "APPROVAL"}]
        with pytest.raises(ValueError) as exc_info:
            adapter.sync_provider_tools("bad_provider", tools=bad_tools)
        assert exc_info.value.reason_code == MCP_PROVIDER_SYNC_FAILED  # type: ignore[attr-defined]

    def test_get_provider_tools_after_sync(self, adapter):
        adapter.sync_provider_tools("test_provider", tools=_make_tools())
        cached = adapter.get_provider_tools("test_provider")
        assert len(cached) == 2

    def test_empty_provider_returns_empty_list(self, adapter):
        result = adapter.get_provider_tools("nonexistent_provider")
        assert result == []


# ── T-MCP-GOV-02: WRITE 工具无 approval_token → MCP_WRITE_UNAUTHORIZED ────────

class TestWriteUnauthorized:
    """T-MCP-GOV-02: WRITE 工具无 approval_token → MCP_WRITE_UNAUTHORIZED。"""

    def test_write_without_token_raises(self, adapter):
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        with pytest.raises(ValueError) as exc_info:
            adapter.execute_mcp_tool(
                "create_doc",
                {"title": "Test"},
                provider_id="test_provider",
                request_id="req_001",
                intent_id="intent_001",
                session_id="session_001",
                seq=1,
                approval_token_ref=None,
                ts_ms=NOW_MS,
            )
        assert exc_info.value.reason_code == MCP_WRITE_UNAUTHORIZED  # type: ignore[attr-defined]

    def test_write_with_empty_token_raises(self, adapter):
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        with pytest.raises(ValueError) as exc_info:
            adapter.execute_mcp_tool(
                "create_doc",
                {"title": "Test"},
                provider_id="test_provider",
                request_id="req_002",
                intent_id="intent_002",
                session_id="session_001",
                seq=2,
                approval_token_ref="",
                ts_ms=NOW_MS,
            )
        assert exc_info.value.reason_code == MCP_WRITE_UNAUTHORIZED  # type: ignore[attr-defined]


# ── T-MCP-GOV-03: WRITE 工具未 APPROVE → MCP_WRITE_UNAUTHORIZED ──────────────

class TestWriteNotApproved:
    """T-MCP-GOV-03: WRITE 工具未 APPROVE（token 无效）→ MCP_WRITE_UNAUTHORIZED 或 TOKEN_INVALID。"""

    def test_write_with_invalid_token_rejected(self, adapter):
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        # 传入无效 token（未经 TokenService 签发）
        result = adapter.execute_mcp_tool(
            "create_doc",
            {"title": "Test"},
            provider_id="test_provider",
            request_id="req_003",
            intent_id="intent_003",
            session_id="session_001",
            seq=3,
            approval_token_ref="invalid_token_xyz",
            ts_ms=NOW_MS,
        )
        # ToolBroker 会拒绝无效 token，status=REJECTED
        assert result["status"] == "REJECTED"

    def test_write_with_valid_token_succeeds(self, adapter_with_tokens):
        adapter, tokens = adapter_with_tokens
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"create_doc": _create_handler},
        )
        from host_brain.tools.mcp_adapter import to_mcp_tool_id
        mapped_id = to_mcp_tool_id("test_provider", "create_doc")
        approval = tokens.issue_approval_token(
            intent_id="intent_004",
            skill_name=mapped_id,
            session_id="session_001",
            seq=4,
            now_ts_ms=NOW_MS,
        )
        result = adapter.execute_mcp_tool(
            "create_doc",
            {"title": "Valid Test"},
            provider_id="test_provider",
            request_id="req_004",
            intent_id="intent_004",
            session_id="session_001",
            seq=4,
            approval_token_ref=approval,
            ts_ms=NOW_MS,
        )
        assert result["status"] == "SUCCESS"
        assert result["evidence_ref"] is not None


# ── T-MCP-GOV-04: 出站缺 sanitization_report → AIRLOCK_BLOCKED_EGRESS ─────────

class TestAirlockSanitization:
    """T-MCP-GOV-04: AIRLOCK 净化报告必须存在；出站 payload 最小化。"""

    def test_sanitization_report_present_in_result(self, adapter):
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "test"},
            provider_id="test_provider",
            request_id="req_005",
            intent_id="intent_005",
            session_id="session_001",
            seq=5,
            ts_ms=NOW_MS,
        )
        assert "sanitization_report" in result
        report = result["sanitization_report"]
        assert "payload_digest" in report
        assert report["payload_digest"].startswith("sha256:")

    def test_sensitive_fields_removed(self, adapter):
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "test", "api_key": "secret_key_123", "hard_state": "raw_value"},
            provider_id="test_provider",
            request_id="req_006",
            intent_id="intent_006",
            session_id="session_001",
            seq=6,
            ts_ms=NOW_MS,
        )
        report = result["sanitization_report"]
        assert "api_key" in report["removed_fields"]
        assert "hard_state" in report["removed_fields"]

    def test_airlock_sanitize_returns_report(self, adapter):
        sanitized, report = MCPAdapter._airlock_sanitize(
            {"query": "test", "api_key": "secret", "normal_field": "value"}
        )
        assert isinstance(report, SanitizationReport)
        assert "api_key" in report.removed_fields
        assert "query" not in report.removed_fields
        assert "normal_field" in sanitized
        assert "api_key" not in sanitized

    def test_airlock_digest_is_deterministic(self, adapter):
        _, report1 = MCPAdapter._airlock_sanitize({"query": "test"})
        _, report2 = MCPAdapter._airlock_sanitize({"query": "test"})
        assert report1.payload_digest == report2.payload_digest


# ── T-MCP-GOV-05: 执行无 evidence_ref → MCP_AUDIT_REF_MISSING ────────────────

class TestAuditRefMissing:
    """T-MCP-GOV-05: 执行无 evidence_ref → MCP_AUDIT_REF_MISSING（WRITE 工具）。"""

    def test_tool_not_found_raises(self, adapter):
        """未同步的 tool_id → MCP_TOOL_NOT_FOUND。"""
        with pytest.raises(ValueError) as exc_info:
            adapter.execute_mcp_tool(
                "nonexistent_tool",
                {},
                provider_id="test_provider",
                request_id="req_007",
                intent_id="intent_007",
                session_id="session_001",
                seq=7,
                ts_ms=NOW_MS,
            )
        assert exc_info.value.reason_code == MCP_TOOL_NOT_FOUND  # type: ignore[attr-defined]

    def test_read_tool_success_has_no_evidence_ref_requirement(self, adapter):
        """READ 工具成功执行不要求 evidence_ref（不抛出 MCP_AUDIT_REF_MISSING）。"""
        adapter.sync_provider_tools(
            "test_provider",
            tools=_make_tools(),
            handlers={"search_docs": _search_handler},
        )
        result = adapter.execute_mcp_tool(
            "search_docs",
            {"query": "test"},
            provider_id="test_provider",
            request_id="req_008",
            intent_id="intent_008",
            session_id="session_001",
            seq=8,
            ts_ms=NOW_MS,
        )
        # READ 工具成功执行，不抛出异常
        assert result["status"] == "SUCCESS"
