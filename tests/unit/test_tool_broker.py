"""T-TOOL-01~06: ToolBroker / ToolCatalog / selector / MCPAdapter 单元测试。"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from host_brain.tools.catalog import (
    ToolCatalog,
    ToolMeta,
    compute_tool_visibility_hash,
    normalize_tool_meta,
)
from host_brain.tools.selector import select_tool_meta_pack
from host_brain.tools.broker import (
    ToolBroker,
    ToolExecutionResult,
    RESULT_REJECTED,
    RESULT_SUCCESS,
    TOOL_NOT_FOUND,
    TOOL_EXECUTED,
)
from host_brain.tools.mcp_adapter import MCPAdapter, to_mcp_tool_id


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_meta(
    tool_id: str = "tool_read_01",
    side_effect: str = "READ",
    auth_level: str = "NONE",
    tags: list[str] | None = None,
) -> dict:
    schema_hash = f"sha256:{'a' * 64}"
    return {
        "id": tool_id,
        "name": f"Tool {tool_id}",
        "desc": f"Description for {tool_id}",
        "schema_hash": schema_hash,
        "side_effect_level": side_effect,
        "auth_level": auth_level,
        "domain_tags": tags or ["test"],
        "version": "1.0.0",
    }


def _make_catalog(*metas: dict) -> ToolCatalog:
    catalog = ToolCatalog()
    for meta in metas:
        catalog.register(meta)
    return catalog


def _make_broker(catalog: ToolCatalog, *, token_valid: bool = True) -> ToolBroker:
    token_service = MagicMock()
    validation = MagicMock()
    validation.valid = token_valid
    validation.reason_code = None if token_valid else "TOKEN_INVALID"
    validation.detail = None if token_valid else "token invalid"
    token_service.validate_execution_tokens.return_value = validation

    ledger = MagicMock()
    ledger.log_skill_exec = MagicMock()

    return ToolBroker(catalog=catalog, token_service=token_service, ledger=ledger)


# ── T-TOOL-01: get_tool_meta_pack 默认不含 WRITE 工具 ─────────────────────────

class TestToolMetaPackNoWrite:
    """T-TOOL-01: select_tool_meta_pack 默认不含 WRITE 工具。"""

    def test_write_tool_excluded_by_default(self):
        catalog = _make_catalog(
            _make_meta("read_tool", "READ"),
            _make_meta("write_tool", "WRITE", "APPROVAL"),
        )
        result = select_tool_meta_pack(catalog, query="test", k=10, include_write=False)
        ids = result.selected_ids
        assert "read_tool" in ids
        assert "write_tool" not in ids

    def test_write_tool_included_when_explicit(self):
        catalog = _make_catalog(
            _make_meta("read_tool", "READ"),
            _make_meta("write_tool", "WRITE", "APPROVAL"),
        )
        result = select_tool_meta_pack(catalog, query="test", k=10, include_write=True)
        assert "write_tool" in result.selected_ids

    def test_empty_catalog_returns_empty(self):
        catalog = _make_catalog()
        result = select_tool_meta_pack(catalog, query="test", k=5)
        assert result.selected_ids == []
        assert result.tool_meta_pack == []

    def test_top_k_limits_results(self):
        catalog = _make_catalog(*[_make_meta(f"tool_{i:02d}") for i in range(10)])
        result = select_tool_meta_pack(catalog, query="test", k=3)
        assert len(result.selected_ids) == 3


# ── T-TOOL-02: WRITE 工具无 token → TOOL_TOKEN_MISSING ───────────────────────

class TestWriteToolTokenGating:
    """T-TOOL-02: WRITE 工具无 approval_token 时被拒绝。"""

    def test_write_tool_rejected_without_token(self):
        catalog = _make_catalog(_make_meta("write_tool", "WRITE", "APPROVAL"))
        broker = _make_broker(catalog, token_valid=False)
        broker.register_handler("write_tool", lambda args, ctx: {"ok": True})

        result = broker.execute(
            request_id="req-001",
            intent_id="intent-001",
            session_id="sess-001",
            seq=1,
            tool_id="write_tool",
            approval_token_ref=None,
        )
        assert result.status == RESULT_REJECTED

    def test_write_tool_succeeds_with_valid_token(self):
        catalog = _make_catalog(_make_meta("write_tool", "WRITE", "APPROVAL"))
        broker = _make_broker(catalog, token_valid=True)
        broker.register_handler("write_tool", lambda args, ctx: {"written": True})

        result = broker.execute(
            request_id="req-002",
            intent_id="intent-002",
            session_id="sess-001",
            seq=1,
            tool_id="write_tool",
            approval_token_ref="tok-abc",
        )
        assert result.status == RESULT_SUCCESS
        assert result.output == {"written": True}

    def test_read_tool_no_token_required(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog, token_valid=False)  # token_service 不会被调用
        broker.register_handler("read_tool", lambda args, ctx: {"data": "ok"})

        result = broker.execute(
            request_id="req-003",
            intent_id="intent-003",
            session_id="sess-001",
            seq=1,
            tool_id="read_tool",
        )
        assert result.status == RESULT_SUCCESS


# ── T-TOOL-03: tool_visibility_hash 可重现 ────────────────────────────────────

class TestToolVisibilityHash:
    """T-TOOL-03: tool_visibility_hash 同输入同输出（可重现）。"""

    def test_hash_deterministic(self):
        meta_list = [
            normalize_tool_meta(_make_meta("tool_a")),
            normalize_tool_meta(_make_meta("tool_b")),
        ]
        h1 = compute_tool_visibility_hash(meta_list)
        h2 = compute_tool_visibility_hash(meta_list)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_hash_changes_with_different_tools(self):
        meta_a = [normalize_tool_meta(_make_meta("tool_a"))]
        meta_b = [normalize_tool_meta(_make_meta("tool_b"))]
        assert compute_tool_visibility_hash(meta_a) != compute_tool_visibility_hash(meta_b)

    def test_hash_order_independent(self):
        meta_a = normalize_tool_meta(_make_meta("tool_a"))
        meta_b = normalize_tool_meta(_make_meta("tool_b"))
        h1 = compute_tool_visibility_hash([meta_a, meta_b])
        h2 = compute_tool_visibility_hash([meta_b, meta_a])
        # 排序后相同
        assert h1 == h2

    def test_selector_returns_hash(self):
        catalog = _make_catalog(_make_meta("tool_x"), _make_meta("tool_y"))
        result = select_tool_meta_pack(catalog, query="test", k=5)
        assert result.tool_visibility_hash.startswith("sha256:")


# ── T-TOOL-04: tool_visibility_hash 写入审计链 ────────────────────────────────

class TestToolVisibilityHashAudit:
    """T-TOOL-04: ToolBroker 执行后写入审计链（ledger.log_skill_exec 被调用）。"""

    def test_audit_logged_on_success(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog)
        broker.register_handler("read_tool", lambda args, ctx: {"result": 42})

        result = broker.execute(
            request_id="req-audit",
            intent_id="intent-audit",
            session_id="sess-001",
            seq=1,
            tool_id="read_tool",
        )
        assert result.status == RESULT_SUCCESS
        broker.ledger.log_skill_exec.assert_called_once()
        call_args = broker.ledger.log_skill_exec.call_args[0][0]
        assert call_args["tool_id" if "tool_id" in call_args else "skill_name"] in ("read_tool",)

    def test_audit_logged_on_rejection(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog)
        # 不注册 handler → TOOL_HANDLER_MISSING

        result = broker.execute(
            request_id="req-rej",
            intent_id="intent-rej",
            session_id="sess-001",
            seq=1,
            tool_id="read_tool",
        )
        assert result.status == RESULT_REJECTED
        broker.ledger.log_skill_exec.assert_called_once()


# ── T-TOOL-05: MCP WRITE 无 APPROVE → 拒绝 ───────────────────────────────────

class TestMCPWriteGating:
    """T-TOOL-05: MCP WRITE 工具无 approval_token → 拒绝。"""

    def test_mcp_write_rejected_without_token(self):
        catalog = _make_catalog()
        broker = _make_broker(catalog, token_valid=False)
        adapter = MCPAdapter(catalog=catalog, broker=broker)

        adapter.sync_provider(
            provider_id="test_provider",
            tools=[
                {
                    "tool_id": "write_op",
                    "name": "Write Op",
                    "desc": "A write operation",
                    "side_effect_level": "WRITE",
                    "auth_level": "APPROVAL",
                    "domain_tags": ["test"],
                    "version": "1.0.0",
                }
            ],
            handlers={"write_op": lambda args, ctx: {"done": True}},
        )

        result = adapter.call_tool(
            provider_id="test_provider",
            tool_id="write_op",
            request_id="req-mcp",
            intent_id="intent-mcp",
            session_id="sess-001",
            seq=1,
            approval_token_ref=None,
        )
        assert result.status == RESULT_REJECTED

    def test_mcp_read_no_token_needed(self):
        catalog = _make_catalog()
        broker = _make_broker(catalog, token_valid=False)
        adapter = MCPAdapter(catalog=catalog, broker=broker)

        adapter.sync_provider(
            provider_id="test_provider",
            tools=[
                {
                    "tool_id": "read_op",
                    "name": "Read Op",
                    "desc": "A read operation",
                    "side_effect_level": "READ",
                    "domain_tags": ["test"],
                    "version": "1.0.0",
                }
            ],
            handlers={"read_op": lambda args, ctx: {"data": "ok"}},
        )

        result = adapter.call_tool(
            provider_id="test_provider",
            tool_id="read_op",
            request_id="req-mcp-r",
            intent_id="intent-mcp-r",
            session_id="sess-001",
            seq=1,
        )
        assert result.status == RESULT_SUCCESS


# ── T-TOOL-06: 未注册工具 → TOOL_NOT_FOUND ────────────────────────────────────

class TestToolNotFound:
    """T-TOOL-06: 未注册工具执行 → TOOL_NOT_FOUND。"""

    def test_unknown_tool_rejected(self):
        catalog = _make_catalog()
        broker = _make_broker(catalog)

        result = broker.execute(
            request_id="req-nf",
            intent_id="intent-nf",
            session_id="sess-001",
            seq=1,
            tool_id="nonexistent_tool",
        )
        assert result.status == RESULT_REJECTED
        assert result.result_code == TOOL_NOT_FOUND

    def test_catalog_get_raises_key_error(self):
        catalog = _make_catalog()
        with pytest.raises(KeyError):
            catalog.get("nonexistent")
