"""T-MCP-01~03: MCP ToolMeta registry and ToolBroker contract tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MCP_PROVIDER_UNAVAILABLE,
    MCP_TOOL_NOT_REGISTERED,
    MCP_WRITE_TOKEN_REQUIRED,
)
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    set_global_degradation_state_machine,
)


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
    """T-MCP-02~03: MCPToolBroker 调用链、Token 治理、降级验收测试。"""

    def setUp(self) -> None:
        set_global_degradation_state_machine(DegradationStateMachine())
        self.registry = MCPToolRegistry(db_path=":memory:")
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.broker = MCPToolBroker(registry=self.registry, ledger=self.ledger)
        self.ts_base = 1_800_000_000_000

    def tearDown(self) -> None:
        set_global_degradation_state_machine(None)

    def _register_tools(self) -> None:
        self.registry.register(_make_read_meta())
        self.registry.register(_make_write_meta())
        self.broker.register_handler("github", "search_issues", lambda args: {"items": [{"id": 1}]})
        self.broker.register_handler("github", "create_issue", lambda args: {"id": 1001, "title": args.get("title", "")})

    def test_t_mcp_02_write_no_token_rejected(self) -> None:
        """WRITE 无 token → MCP_WRITE_TOKEN_REQUIRED 拒绝 + skill_exec_log 写入拒绝记录。"""
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
        self.assertFalse(result.success)
        self.assertEqual("REJECT", result.decision)
        self.assertEqual(MCP_WRITE_TOKEN_REQUIRED, result.reason_code)
        # 审计写入
        skill_rows = self.ledger.skill_exec_log
        self.assertEqual(1, len(skill_rows))
        self.assertEqual("REJECTED", skill_rows[0]["result_status"])
        self.assertEqual(MCP_WRITE_TOKEN_REQUIRED, skill_rows[0]["result_code"])

    def test_t_mcp_02_read_no_token_approved(self) -> None:
        """READ 无 token → 正常执行 + skill_exec_log 写入成功记录。"""
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
        self.assertTrue(result.success)
        self.assertEqual("APPROVE", result.decision)
        self.assertIsNone(result.reason_code)
        self.assertIsNotNone(result.output)
        # 审计写入
        skill_rows = self.ledger.skill_exec_log
        self.assertEqual(1, len(skill_rows))
        self.assertEqual("SUCCESS", skill_rows[0]["result_status"])

    def test_t_mcp_03_provider_unavailable_degraded(self) -> None:
        """provider 不可达 → MCP_PROVIDER_UNAVAILABLE 降级 + 输出结构稳定。"""
        self._register_tools()
        # 注册一个会抛异常的 handler
        self.broker.register_handler("github", "search_issues", lambda args: (_ for _ in ()).throw(RuntimeError("network error")))
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
        self.assertFalse(result.success)
        self.assertEqual("REJECT", result.decision)
        self.assertEqual(MCP_PROVIDER_UNAVAILABLE, result.reason_code)
        self.assertIsNone(result.output)

    def test_t_mcp_03_unregistered_tool_rejected(self) -> None:
        """未注册工具 → MCP_TOOL_NOT_REGISTERED 拒绝。"""
        result = self.broker.call(
            provider_id="unknown",
            tool_id="unknown_tool",
            request_id="req_mcp_unreg",
            intent_id="intent_mcp_unreg",
            session_id="session_mcp",
            seq=1,
            ts_ms=self.ts_base,
        )
        self.assertFalse(result.success)
        self.assertEqual(MCP_TOOL_NOT_REGISTERED, result.reason_code)

    def test_t_mcp_02_write_with_token_approved(self) -> None:
        """WRITE 有 token → 正常执行。"""
        self._register_tools()
        result = self.broker.call(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_w02",
            intent_id="intent_mcp_w02",
            session_id="session_mcp",
            seq=2,
            args={"title": "Feature"},
            approval_token_ref="token_abc123",
            ts_ms=self.ts_base,
        )
        self.assertTrue(result.success)
        self.assertEqual("APPROVE", result.decision)
        self.assertEqual(1001, result.output["id"])

    def test_t_mcp_02_fixture_write_no_token(self) -> None:
        """Fixture write_no_token.json 结构正确。"""
        fixture = _load_json(FIXTURES / "write_no_token.json")
        self.assertEqual("REJECT", fixture["decision"])
        self.assertEqual(MCP_WRITE_TOKEN_REQUIRED, fixture["reason_code"])

    def test_t_mcp_03_fixture_provider_unavailable(self) -> None:
        """Fixture provider_unavailable.json 结构正确。"""
        fixture = _load_json(FIXTURES / "provider_unavailable.json")
        self.assertEqual("REJECT", fixture["decision"])
        self.assertEqual(MCP_PROVIDER_UNAVAILABLE, fixture["reason_code"])


if __name__ == "__main__":
    unittest.main()
