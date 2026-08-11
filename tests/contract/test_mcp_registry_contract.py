"""T-MCP-01~03: MCP ToolMeta registry and ToolBroker contract tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.broker import CapabilityBroker
from host_brain.capability_plane.policies import DescriptorCapabilityPolicy
from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL, TokenService
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "mcp"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _make_read_meta(provider_id: str = "github", tool_id: str = "search_issues") -> ToolMeta:
    return ToolMeta(
        provider_id=provider_id,
        tool_id=tool_id,
        name="Search Issues",
        description="Search GitHub issues",
        access_level="PUBLIC",
        risk_level="LOW",
        reversible=True,
        version="1.0.0",
    )


def _make_write_meta(provider_id: str = "github", tool_id: str = "create_issue") -> ToolMeta:
    return ToolMeta(
        provider_id=provider_id,
        tool_id=tool_id,
        name="Create Issue",
        description="Create a GitHub issue",
        access_level="INTERNAL",
        risk_level="MEDIUM",
        reversible=False,
        version="1.0.0",
    )


class TestMCPRegistryContract(unittest.TestCase):
    """T-MCP-01: ToolMeta 注册/更新/变更留痕验收测试。"""

    def setUp(self) -> None:
        self.registry = MCPToolRegistry(db_path=":memory:")

    def test_t_mcp_01_register_and_query(self) -> None:
        """注册后可按 provider_id/tool_id 查询。"""
        meta = _make_read_meta()
        self.registry.register(meta)
        fetched = self.registry.get(provider_id="github", tool_id="search_issues")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual("github", fetched.provider_id)
        self.assertEqual("search_issues", fetched.tool_id)
        self.assertEqual("PUBLIC", fetched.access_level)
        self.assertEqual("LOW", fetched.risk_level)
        self.assertTrue(fetched.reversible)

    def test_t_mcp_01_update_writes_sync_log_diff(self) -> None:
        """更新 ToolMeta 时 mcp_sync_log 写入变更记录（含 before/after diff）。"""
        meta = _make_read_meta()
        self.registry.register(meta)

        updated = ToolMeta(
            provider_id="github",
            tool_id="search_issues",
            name="Search Issues v2",
            description="Updated description",
            access_level="PUBLIC",
            risk_level="LOW",
            reversible=True,
            version="2.0.0",
        )
        self.registry.register(updated)

        logs = self.registry.sync_log()
        self.assertEqual(2, len(logs))
        insert_log = logs[0]
        update_log = logs[1]
        self.assertEqual("INSERT", insert_log["change_kind"])
        self.assertEqual("UPDATE", update_log["change_kind"])
        # diff 应包含 name 和 version 的变更
        diff = update_log["diff"]
        self.assertIn("name", diff["changed"])
        self.assertIn("version", diff["changed"])
        self.assertEqual("Search Issues", diff["changed"]["name"]["before"])
        self.assertEqual("Search Issues v2", diff["changed"]["name"]["after"])

    def test_t_mcp_01_access_level_risk_level_reversible_complete(self) -> None:
        """access_level/risk_level/reversible 字段完整且类型正确。"""
        meta = _make_write_meta()
        self.registry.register(meta)
        fetched = self.registry.get(provider_id="github", tool_id="create_issue")
        assert fetched is not None
        self.assertIsInstance(fetched.access_level, str)
        self.assertIsInstance(fetched.risk_level, str)
        self.assertIsInstance(fetched.reversible, bool)
        self.assertEqual("INTERNAL", fetched.access_level)
        self.assertEqual("MEDIUM", fetched.risk_level)
        self.assertFalse(fetched.reversible)

    def test_t_mcp_01_invalid_access_level_raises(self) -> None:
        """无效 access_level 抛出 ValueError。"""
        meta = ToolMeta(
            provider_id="p", tool_id="t", name="T", description="",
            access_level="INVALID", risk_level="LOW", reversible=True,
        )
        with self.assertRaises(ValueError):
            self.registry.register(meta)

    def test_t_mcp_01_list_by_provider(self) -> None:
        """list_by_provider 返回该 provider 的所有工具。"""
        self.registry.register(_make_read_meta())
        self.registry.register(_make_write_meta())
        tools = self.registry.list_by_provider("github")
        self.assertEqual(2, len(tools))
        tool_ids = {t.tool_id for t in tools}
        self.assertIn("search_issues", tool_ids)
        self.assertIn("create_issue", tool_ids)

    def test_t_mcp_01_fixture_tool_meta_read(self) -> None:
        """Fixture tool_meta_read.json 结构正确。"""
        fixture = _load_json(FIXTURES / "tool_meta_read.json")
        self.assertEqual("PUBLIC", fixture["access_level"])
        self.assertEqual("LOW", fixture["risk_level"])
        self.assertTrue(fixture["reversible"])

    def test_t_mcp_01_fixture_tool_meta_write(self) -> None:
        """Fixture tool_meta_write.json 结构正确。"""
        fixture = _load_json(FIXTURES / "tool_meta_write.json")
        self.assertEqual("INTERNAL", fixture["access_level"])
        self.assertEqual("MEDIUM", fixture["risk_level"])
        self.assertFalse(fixture["reversible"])


class TestMCPBrokerContract(unittest.TestCase):
    """The retired execution facade rejects while registry reads remain valid."""

    def setUp(self) -> None:
        self.registry = MCPToolRegistry(db_path=":memory:")
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.ts_base = 1_800_000_000_000
        self.token_service = TokenService()
        self.handler_calls: list[tuple[str, dict]] = []
        self.broker = MCPToolBroker(
            registry=self.registry,
            capability_broker=CapabilityBroker(policy=DescriptorCapabilityPolicy()),
            ledger=self.ledger,
            token_service=self.token_service,
        )

    def _register_tools(self) -> None:
        self.registry.register(_make_read_meta())
        self.registry.register(_make_write_meta())
        with self.assertRaisesRegex(PermissionError, MCP_TASK_AUTHORITY_REQUIRED):
            self.broker.register_handler(
                "github",
                "search_issues",
                lambda args: self.handler_calls.append(("read", dict(args)))
                or {"items": [{"id": 1}]},
            )
        with self.assertRaisesRegex(PermissionError, MCP_TASK_AUTHORITY_REQUIRED):
            self.broker.register_handler(
                "github",
                "create_issue",
                lambda args: self.handler_calls.append(("write", dict(args)))
                or {"id": 1001, "title": args.get("title", "")},
            )

    def _assert_retired_rejection(self, result) -> None:  # type: ignore[no-untyped-def]
        self.assertFalse(result.success)
        self.assertEqual("REJECT", result.decision)
        self.assertEqual(MCP_TASK_AUTHORITY_REQUIRED, result.reason_code)
        self.assertIsNone(result.output)
        self.assertEqual([], self.handler_calls)

    def test_t_mcp_02_write_no_token_requires_task_authority(self) -> None:
        self._register_tools()
        result = self.broker.call(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_w01",
            intent_id="intent_mcp_w01",
            session_id="session_mcp",
            seq=1,
            args={"title": "Bug"},
            approval_token_ref=None,
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)
        skill_rows = self.ledger.skill_exec_log
        self.assertEqual(1, len(skill_rows))
        self.assertEqual("REJECTED", skill_rows[0]["result_status"])
        self.assertEqual(MCP_TASK_AUTHORITY_REQUIRED, skill_rows[0]["result_code"])
        self.assertIsNone(skill_rows[0]["evidence_ref"])

    def test_t_mcp_02_read_no_token_is_not_a_direct_execution_exception(self) -> None:
        self._register_tools()
        result = self.broker.call(
            provider_id="github",
            tool_id="search_issues",
            request_id="req_mcp_r01",
            intent_id="intent_mcp_r01",
            session_id="session_mcp",
            seq=1,
            args={"query": "bug"},
            approval_token_ref=None,
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)
        self.assertEqual("REJECTED", self.ledger.skill_exec_log[0]["result_status"])

    def test_t_mcp_03_provider_handler_is_never_reached(self) -> None:
        self._register_tools()
        with self.assertRaisesRegex(PermissionError, MCP_TASK_AUTHORITY_REQUIRED):
            self.broker.register_handler(
                "github",
                "search_issues",
                lambda args: self.handler_calls.append(("unexpected", dict(args)))
                or (_ for _ in ()).throw(RuntimeError("network error")),
            )
        result = self.broker.call(
            provider_id="github",
            tool_id="search_issues",
            request_id="req_mcp_fail01",
            intent_id="intent_mcp_fail01",
            session_id="session_mcp",
            seq=1,
            args={"query": "bug"},
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)

    def test_t_mcp_03_unregistered_tool_uses_the_same_authority_fence(self) -> None:
        result = self.broker.call(
            provider_id="unknown",
            tool_id="unknown_tool",
            request_id="req_mcp_unreg",
            intent_id="intent_mcp_unreg",
            session_id="session_mcp",
            seq=1,
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)

    def test_t_mcp_02_valid_legacy_token_does_not_restore_execution(self) -> None:
        self._register_tools()
        token = self.token_service.issue_approval_token(
            intent_id="intent_mcp_w02",
            skill_name="mcp::github::create_issue",
            session_id="session_mcp",
            seq=2,
            now_ts_ms=self.ts_base,
        )
        result = self.broker.call(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_w02",
            intent_id="intent_mcp_w02",
            session_id="session_mcp",
            seq=2,
            args={"title": "Feature"},
            approval_token_ref=token,
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)
        validation = self.token_service.validate_bound_token(
            token,
            expected_type=TOKEN_TYPE_APPROVAL,
            intent_id="intent_mcp_w02",
            skill_name="mcp::github::create_issue",
            session_id="session_mcp",
            seq=2,
            now_ts_ms=self.ts_base,
            consume=False,
        )
        self.assertTrue(validation.valid)

    def test_t_mcp_02_forged_token_is_not_examined_or_executed(self) -> None:
        self._register_tools()
        result = self.broker.call(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_forged",
            intent_id="intent_mcp_forged",
            session_id="session_mcp",
            seq=3,
            args={"title": "must not run"},
            approval_token_ref="token_attacker_controlled",
            ts_ms=self.ts_base,
        )
        self._assert_retired_rejection(result)

    def test_legacy_execution_fixtures_are_not_current_authority(self) -> None:
        write = _load_json(FIXTURES / "write_no_token.json")
        unavailable = _load_json(FIXTURES / "provider_unavailable.json")
        self.assertEqual("REJECT", write["decision"])
        self.assertEqual("REJECT", unavailable["decision"])
        self.assertNotEqual(MCP_TASK_AUTHORITY_REQUIRED, write["reason_code"])
        self.assertNotEqual(MCP_TASK_AUTHORITY_REQUIRED, unavailable["reason_code"])


if __name__ == "__main__":
    unittest.main()
