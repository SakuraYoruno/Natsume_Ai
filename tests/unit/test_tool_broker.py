"""ToolCatalog 与 app-local governed Tool port 的组件契约。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.core.errors import GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED
from host_brain.tools.broker import (
    RESULT_REJECTED,
    RESULT_SUCCESS,
    ToolBroker,
    ToolRuntimeBoundary,
    build_tool_runtime_boundary,
)
from host_brain.tools.catalog import (
    ToolCatalog,
    compute_tool_visibility_hash,
    normalize_tool_meta,
)
from host_brain.tools.selector import select_tool_meta_pack


def _make_meta(
    tool_id: str = "tool_read_01",
    side_effect: str = "READ",
    auth_level: str = "NONE",
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": tool_id,
        "name": f"Tool {tool_id}",
        "desc": f"Description for {tool_id}",
        "schema_hash": f"sha256:{'a' * 64}",
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


_BOUNDARIES: dict[ToolBroker, ToolRuntimeBoundary] = {}


def _make_broker(catalog: ToolCatalog, *, token_valid: bool = True) -> ToolBroker:
    token_service = MagicMock()
    validation = MagicMock()
    validation.valid = token_valid
    validation.reason_code = None if token_valid else "TOKEN_INVALID"
    validation.detail = None if token_valid else "token invalid"
    token_service.validate_execution_tokens.return_value = validation
    ledger = MagicMock()
    ledger.log_skill_exec = MagicMock()
    boundary = build_tool_runtime_boundary(
        catalog=catalog,
        token_service=token_service,
        ledger=ledger,
    )
    _BOUNDARIES[boundary.broker] = boundary
    return boundary.broker


def _register_bound_handler(
    broker: ToolBroker,
    tool_id: str,
    handler,
) -> dict:
    broker.register_handler(
        tool_id,
        handler,
        replay_safe_local=True,
        adapter_version_id=f"test.{tool_id}.v1",
    )
    return broker.resolve_execution_binding(tool_id)


def _execute_bound(
    broker: ToolBroker,
    *,
    tool_id: str,
    expected_binding: dict,
    request_id: str,
    intent_id: str,
    session_id: str = "sess-001",
    seq: int = 1,
    args: dict | None = None,
    context: dict | None = None,
    approval_token_ref: str | None = None,
    confirm_token_ref: str | None = None,
    before_handler=None,
):
    task_authority = object()
    execution_port = _BOUNDARIES[broker].bind_execution_port(
        authority_verifier=lambda **values: (
            values["task_authority"] is task_authority
            and values["request_id"] == request_id
            and values["intent_id"] == intent_id
            and values["session_id"] == session_id
            and values["tool_id"] == tool_id
        )
    )
    return execution_port.execute_bound(
        task_authority=task_authority,
        expected_binding=expected_binding,
        request_id=request_id,
        intent_id=intent_id,
        session_id=session_id,
        seq=seq,
        tool_id=tool_id,
        args=args,
        context=context,
        approval_token_ref=approval_token_ref,
        confirm_token_ref=confirm_token_ref,
        before_handler=before_handler,
    )


class TestToolMetaPackNoWrite:
    def test_write_tool_excluded_by_default(self):
        catalog = _make_catalog(
            _make_meta("read_tool", "READ"),
            _make_meta("write_tool", "WRITE", "APPROVAL"),
        )
        result = select_tool_meta_pack(catalog, query="test", k=10, include_write=False)
        assert "read_tool" in result.selected_ids
        assert "write_tool" not in result.selected_ids

    def test_write_tool_included_when_explicit(self):
        catalog = _make_catalog(
            _make_meta("read_tool", "READ"),
            _make_meta("write_tool", "WRITE", "APPROVAL"),
        )
        result = select_tool_meta_pack(catalog, query="test", k=10, include_write=True)
        assert "write_tool" in result.selected_ids

    def test_empty_catalog_returns_empty(self):
        result = select_tool_meta_pack(_make_catalog(), query="test", k=5)
        assert result.selected_ids == []
        assert result.tool_meta_pack == []

    def test_top_k_limits_results(self):
        catalog = _make_catalog(*[_make_meta(f"tool_{index:02d}") for index in range(10)])
        assert len(select_tool_meta_pack(catalog, query="test", k=3).selected_ids) == 3


class TestBoundToolTokenGating:
    def test_write_tool_rejected_without_valid_token(self):
        catalog = _make_catalog(_make_meta("write_tool", "WRITE", "APPROVAL"))
        broker = _make_broker(catalog, token_valid=False)
        handler = MagicMock(return_value={"ok": True})
        marker = MagicMock()
        binding = _register_bound_handler(broker, "write_tool", handler)

        result = _execute_bound(
            broker,
            tool_id="write_tool",
            expected_binding=binding,
            request_id="req-001",
            intent_id="intent-001",
            before_handler=marker,
        )

        assert result.status == RESULT_REJECTED
        marker.assert_not_called()
        handler.assert_not_called()

    def test_write_tool_succeeds_with_valid_node_token(self):
        catalog = _make_catalog(_make_meta("write_tool", "WRITE", "APPROVAL"))
        broker = _make_broker(catalog, token_valid=True)
        handler = MagicMock(return_value={"written": True})
        binding = _register_bound_handler(broker, "write_tool", handler)

        result = _execute_bound(
            broker,
            tool_id="write_tool",
            expected_binding=binding,
            request_id="req-002",
            intent_id="intent-002",
            approval_token_ref="node-bound-token",
        )

        assert result.status == RESULT_SUCCESS
        assert result.output == {"written": True}
        handler.assert_called_once()

    def test_read_tool_uses_exact_binding_without_token(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog, token_valid=False)
        handler = MagicMock(return_value={"data": "ok"})
        binding = _register_bound_handler(broker, "read_tool", handler)

        result = _execute_bound(
            broker,
            tool_id="read_tool",
            expected_binding=binding,
            request_id="req-003",
            intent_id="intent-003",
        )

        assert result.status == RESULT_SUCCESS
        broker.token_service.validate_execution_tokens.assert_not_called()


class TestToolVisibilityHash:
    def test_hash_deterministic(self):
        metas = [
            normalize_tool_meta(_make_meta("tool_a")),
            normalize_tool_meta(_make_meta("tool_b")),
        ]
        first = compute_tool_visibility_hash(metas)
        assert first == compute_tool_visibility_hash(metas)
        assert first.startswith("sha256:")

    def test_hash_changes_with_different_tools(self):
        left = [normalize_tool_meta(_make_meta("tool_a"))]
        right = [normalize_tool_meta(_make_meta("tool_b"))]
        assert compute_tool_visibility_hash(left) != compute_tool_visibility_hash(right)

    def test_hash_order_independent(self):
        first = normalize_tool_meta(_make_meta("tool_a"))
        second = normalize_tool_meta(_make_meta("tool_b"))
        assert compute_tool_visibility_hash([first, second]) == compute_tool_visibility_hash(
            [second, first]
        )

    def test_selector_returns_hash(self):
        catalog = _make_catalog(_make_meta("tool_x"), _make_meta("tool_y"))
        assert select_tool_meta_pack(catalog, query="test", k=5).tool_visibility_hash.startswith(
            "sha256:"
        )


class TestToolBrokerAuditAndRetirement:
    def test_bound_success_is_logged(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog)
        binding = _register_bound_handler(
            broker,
            "read_tool",
            MagicMock(return_value={"result": 42}),
        )

        result = _execute_bound(
            broker,
            tool_id="read_tool",
            expected_binding=binding,
            request_id="req-audit",
            intent_id="intent-audit",
        )

        assert result.status == RESULT_SUCCESS
        broker.ledger.log_skill_exec.assert_called_once()
        row = broker.ledger.log_skill_exec.call_args.args[0]
        assert row["skill_name"] == "read_tool"

    def test_direct_execute_rejects_before_handler_and_token_service(self):
        catalog = _make_catalog(_make_meta("write_tool", "WRITE", "APPROVAL"))
        broker = _make_broker(catalog)
        handler = MagicMock(return_value={"should_not": "run"})
        _register_bound_handler(broker, "write_tool", handler)

        result = broker.execute(
            request_id="req-direct",
            intent_id="intent-direct",
            session_id="sess-direct",
            seq=9,
            tool_id="write_tool",
            args={"path": "forbidden"},
            approval_token_ref="legacy-token",
            confirm_token_ref="legacy-confirm-token",
        )

        assert result.status == RESULT_REJECTED
        assert result.result_code == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED
        assert result.output is None
        handler.assert_not_called()
        broker.token_service.validate_execution_tokens.assert_not_called()
        broker.ledger.log_skill_exec.assert_called_once()
        audit_row = broker.ledger.log_skill_exec.call_args.args[0]
        assert audit_row["approval_token_ref"] is None
        assert audit_row["confirm_token_ref"] is None
        assert "legacy-token" not in repr(audit_row)
        assert "legacy-confirm-token" not in repr(audit_row)
        assert "legacy-token" not in repr(result.detail)
        assert "legacy-confirm-token" not in repr(result.detail)

    def test_direct_unknown_tool_does_not_restore_lookup_authority(self):
        broker = _make_broker(_make_catalog())
        result = broker.execute(
            request_id="req-unknown",
            intent_id="intent-unknown",
            session_id="sess-unknown",
            seq=1,
            tool_id="unknown",
        )
        assert result.status == RESULT_REJECTED
        assert result.result_code == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED

    def test_bound_unknown_tool_fails_closed(self):
        broker = _make_broker(_make_catalog())
        with pytest.raises(KeyError, match="tool handler missing"):
            _execute_bound(
                broker,
                expected_binding={},
                request_id="req-bound-unknown",
                intent_id="intent-bound-unknown",
                session_id="sess-bound-unknown",
                seq=1,
                tool_id="unknown",
            )

    def test_raw_bound_api_rejects_even_with_live_binding(self):
        catalog = _make_catalog(_make_meta("read_tool", "READ"))
        broker = _make_broker(catalog)
        handler = MagicMock(return_value={"unexpected": True})
        binding = _register_bound_handler(broker, "read_tool", handler)

        result = broker.execute_bound(
            expected_binding=binding,
            request_id="req-raw-bound",
            intent_id="intent-raw-bound",
            session_id="sess-raw-bound",
            seq=1,
            tool_id="read_tool",
        )

        assert result.status == RESULT_REJECTED
        assert result.result_code == GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED
        handler.assert_not_called()

    def test_catalog_get_raises_key_error(self):
        with pytest.raises(KeyError):
            _make_catalog().get("nonexistent")
