import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import TOKEN_INVALID
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.mcp_adapter import MCPAdapter, to_mcp_tool_id


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "mcp"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _search_issues(args: dict, _context: dict | None) -> dict:
    return {"items": [{"id": 1, "query": args.get("query", "")}]}


def _create_issue(args: dict, _context: dict | None) -> dict:
    return {"id": 1001, "title": args.get("title", "")}


class TestMCPAdapterContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=20_000, confirm_ttl_ms=20_000, undo_ttl_ms=20_000)
        self.catalog = ToolCatalog()
        self.broker = ToolBroker(catalog=self.catalog, token_service=self.tokens, ledger=self.ledger)
        self.adapter = MCPAdapter(catalog=self.catalog, broker=self.broker)

    def test_t_mcp_01_sync_and_read_call(self) -> None:
        payload = _load_json(FIXTURES / "good" / "provider_tools.json")
        synced = self.adapter.sync_provider(
            provider_id=payload["provider_id"],
            tools=payload["tools"],
            handlers={
                "search_issues": _search_issues,
                "create_issue": _create_issue,
            },
        )
        self.assertEqual(
            {
                "mcp::github::search_issues",
                "mcp::github::create_issue",
            },
            set(synced),
        )

        read_meta = self.catalog.get("mcp::github::search_issues").as_dict()
        write_meta = self.catalog.get("mcp::github::create_issue").as_dict()
        self.assertEqual("READ", read_meta["side_effect_level"])
        self.assertEqual("NONE", read_meta["auth_level"])
        self.assertEqual("WRITE", write_meta["side_effect_level"])
        self.assertEqual("APPROVAL", write_meta["auth_level"])

        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="search_issues",
            request_id="req_mcp_001",
            intent_id="intent_mcp_001",
            session_id="session_mcp",
            seq=1,
            args={"query": "bug"},
            ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", result.status)
        self.assertTrue(result.evidence_ref and result.evidence_ref.startswith("evidence::"))
        self.assertEqual("mcp::github::search_issues", result.tool_id)
        event = self.ledger.get_event(result.evidence_ref)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("req_mcp_001", event["request_id"])
        self.assertEqual("intent_mcp_001", event["intent_id"])
        self.assertEqual("mcp::github::search_issues", event["skill_name"])

    def test_t_mcp_01_write_without_token_rejected(self) -> None:
        payload = _load_json(FIXTURES / "good" / "provider_tools.json")
        self.adapter.sync_provider(
            provider_id=payload["provider_id"],
            tools=payload["tools"],
            handlers={
                "search_issues": _search_issues,
                "create_issue": _create_issue,
            },
        )
        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_002",
            intent_id="intent_mcp_002",
            session_id="session_mcp",
            seq=2,
            args={"title": "Need fix"},
            approval_token_ref=None,
            ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(TOKEN_INVALID, result.result_code)
        self.assertIsNone(result.evidence_ref)
        self.assertEqual("mcp::github::create_issue", result.tool_id)

    def test_t_mcp_01_write_with_token_success(self) -> None:
        payload = _load_json(FIXTURES / "good" / "provider_tools.json")
        self.adapter.sync_provider(
            provider_id=payload["provider_id"],
            tools=payload["tools"],
            handlers={
                "search_issues": _search_issues,
                "create_issue": _create_issue,
            },
        )
        mapped_tool_id = to_mcp_tool_id("github", "create_issue")
        approval = self.tokens.issue_approval_token(
            intent_id="intent_mcp_003",
            skill_name=mapped_tool_id,
            session_id="session_mcp",
            seq=3,
            now_ts_ms=self.now_ms,
        )
        result = self.adapter.call_tool(
            provider_id="github",
            tool_id="create_issue",
            request_id="req_mcp_003",
            intent_id="intent_mcp_003",
            session_id="session_mcp",
            seq=3,
            args={"title": "Need fix"},
            approval_token_ref=approval,
            ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", result.status)
        self.assertEqual("mcp::github::create_issue", result.tool_id)
        self.assertTrue(result.evidence_ref and result.evidence_ref.startswith("evidence::"))

    def test_t_mcp_01_read_with_auth_level_is_rejected_on_sync(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "read_with_auth.json")
        with self.assertRaises(ValueError):
            self.adapter.sync_provider(
                provider_id=payload["provider_id"],
                tools=payload["tools"],
                handlers={"search_issues": _search_issues},
            )


if __name__ == "__main__":
    unittest.main()
