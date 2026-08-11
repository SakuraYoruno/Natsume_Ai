"""Legacy MCPAdapter is metadata-only and cannot execute provider callbacks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, to_mcp_tool_id


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "mcp"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestMCPAdapterContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(
            approval_ttl_ms=20_000,
            confirm_ttl_ms=20_000,
            undo_ttl_ms=20_000,
        )
        self.catalog = ToolCatalog()
        self.broker = ToolBroker(
            catalog=self.catalog,
            token_service=self.tokens,
            ledger=self.ledger,
        )
        self.adapter = MCPAdapter(catalog=self.catalog, broker=self.broker)

    def _sync_with_handlers(self, calls: list[str]) -> list[str]:
        payload = _load_json(FIXTURES / "good" / "provider_tools.json")
        return self.adapter.sync_provider(
            provider_id=payload["provider_id"],
            tools=payload["tools"],
            handlers={
                "search_issues": lambda _args, _ctx: calls.append("read") or {},
                "create_issue": lambda _args, _ctx: calls.append("write") or {},
            },
        )

    def test_t_mcp_01_sync_is_metadata_only_and_callbacks_are_not_registered(self) -> None:
        calls: list[str] = []
        synced = self._sync_with_handlers(calls)

        self.assertEqual(
            {"mcp::github::search_issues", "mcp::github::create_issue"},
            set(synced),
        )
        self.assertEqual("READ", self.catalog.get("mcp::github::search_issues").side_effect_level)
        self.assertEqual("WRITE", self.catalog.get("mcp::github::create_issue").side_effect_level)
        self.assertEqual((), self.broker.readiness_snapshot()["registered_ids"])

        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="search_issues",
            request_id="req_mcp_metadata_only",
            intent_id="intent_mcp_metadata_only",
            session_id="session_mcp",
            seq=1,
            args={"query": "bug"},
            ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(MCP_TASK_AUTHORITY_REQUIRED, result.result_code)
        self.assertIsNone(result.output)
        self.assertEqual([], calls)

    def test_t_mcp_01_write_without_token_requires_task_authority(self) -> None:
        calls: list[str] = []
        self._sync_with_handlers(calls)

        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_no_token",
            intent_id="intent_mcp_no_token",
            session_id="session_mcp",
            seq=2,
            args={"title": "Need fix"},
            approval_token_ref=None,
            ts_ms=self.now_ms,
        )

        self.assertEqual("REJECTED", result.status)
        self.assertEqual(MCP_TASK_AUTHORITY_REQUIRED, result.result_code)
        self.assertIsNone(result.evidence_ref)
        self.assertEqual([], calls)

    def test_t_mcp_01_legacy_token_cannot_restore_direct_execution(self) -> None:
        calls: list[str] = []
        self._sync_with_handlers(calls)
        mapped_tool_id = to_mcp_tool_id("github", "create_issue")
        approval = self.tokens.issue_approval_token(
            intent_id="intent_mcp_legacy_token",
            skill_name=mapped_tool_id,
            session_id="session_mcp",
            seq=3,
            now_ts_ms=self.now_ms,
        )

        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_legacy_token",
            intent_id="intent_mcp_legacy_token",
            session_id="session_mcp",
            seq=3,
            args={"title": "Need fix"},
            approval_token_ref=approval,
            ts_ms=self.now_ms,
        )

        self.assertEqual("REJECTED", result.status)
        self.assertEqual(MCP_TASK_AUTHORITY_REQUIRED, result.result_code)
        self.assertEqual([], calls)

    def test_t_mcp_01_read_with_auth_level_is_rejected_on_sync(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "read_with_auth.json")
        with self.assertRaises(ValueError):
            self.adapter.sync_provider(
                provider_id=payload["provider_id"],
                tools=payload["tools"],
                handlers={"search_issues": lambda _args, _ctx: None},
            )


if __name__ == "__main__":
    unittest.main()
