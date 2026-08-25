"""T-MCP-01: MCP ToolMeta registry contract tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
