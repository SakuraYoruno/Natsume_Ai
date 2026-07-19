import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIRM_REQUIRED,
    GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
    TOKEN_INVALID,
)
from host_brain.governor.tokens import TokenService
from host_brain.tools.broker import build_tool_runtime_boundary
from host_brain.tools.catalog import ToolCatalog


ROOT = Path(__file__).resolve().parents[2]
TOOLS_FIX = ROOT / "tests" / "fixtures" / "tools"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_tool(_args: dict, _context: dict | None) -> dict:
    return {"status": "ok", "mode": "read"}


def _write_tool(args: dict, _context: dict | None) -> dict:
    return {"status": "written", "path": args.get("path", "unknown")}


class TestToolBrokerContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=20_000, confirm_ttl_ms=20_000, undo_ttl_ms=20_000)
        self.catalog = ToolCatalog()
        for row in _load_json(TOOLS_FIX / "good" / "catalog_mixed.json"):
            self.catalog.register(row)
        self.boundary = build_tool_runtime_boundary(
            catalog=self.catalog,
            token_service=self.tokens,
            ledger=self.ledger,
        )
        self.broker = self.boundary.broker
        self.broker.register_handler(
            "read_system_status",
            _read_tool,
            replay_safe_local=True,
            adapter_version_id="contract.read-system-status.v1",
        )
        self.broker.register_handler(
            "write_file_update",
            _write_tool,
            replay_safe_local=True,
            adapter_version_id="contract.write-file-update.v1",
        )
        self.broker.register_handler(
            "delete_file_force",
            _write_tool,
            replay_safe_local=True,
            adapter_version_id="contract.delete-file-force.v1",
        )
        self.task_authority = object()
        self.execution_port = self.boundary.bind_execution_port(
            authority_verifier=lambda **values: (
                values["task_authority"] is self.task_authority
                and values["request_id"] is not None
                and values["intent_id"] != ""
                and values["session_id"] != ""
                and values["tool_id"] != ""
            )
        )

    def _execute_bound(self, tool_id: str, **kwargs):
        return self.execution_port.execute_bound(
            task_authority=self.task_authority,
            expected_binding=self.broker.resolve_execution_binding(tool_id),
            tool_id=tool_id,
            **kwargs,
        )

    def test_t_tool_03_write_without_token_is_rejected_and_logged(self) -> None:
        payload = _load_json(TOOLS_FIX / "bad" / "write_without_token.json")
        result = self._execute_bound(
            payload["tool_id"],
            request_id=payload["request_id"],
            intent_id=payload["intent_id"],
            session_id=payload["session_id"],
            seq=payload["seq"],
            args=payload["args"],
            approval_token_ref=payload["approval_token_ref"],
            confirm_token_ref=payload["confirm_token_ref"],
            ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(TOKEN_INVALID, result.result_code)
        self.assertIsNone(result.evidence_ref)

        self.assertEqual(1, len(self.ledger.skill_exec_log))
        row = self.ledger.skill_exec_log[0]
        self.assertEqual("write_file_update", row["skill_name"])
        self.assertEqual("REJECTED", row["result_status"])
        self.assertEqual(TOKEN_INVALID, row["result_code"])
        self.assertIsNone(row["evidence_ref"])

    def test_t_tool_03_confirm_required_for_confirm_write(self) -> None:
        approval = self.tokens.issue_approval_token(
            intent_id="intent_tool_004",
            skill_name="delete_file_force",
            session_id="session_tool",
            seq=4,
            now_ts_ms=self.now_ms,
        )
        result = self._execute_bound(
            "delete_file_force",
            request_id="req_tool_004",
            intent_id="intent_tool_004",
            session_id="session_tool",
            seq=4,
            args={"path": "workspace/obsolete.txt"},
            approval_token_ref=approval,
            confirm_token_ref=None,
            ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(CONFIRM_REQUIRED, result.result_code)
        self.assertIsNone(result.evidence_ref)

    def test_t_tool_03_success_has_evidence_ref_and_is_replayable(self) -> None:
        approval = self.tokens.issue_approval_token(
            intent_id="intent_tool_005",
            skill_name="write_file_update",
            session_id="session_tool",
            seq=5,
            now_ts_ms=self.now_ms,
        )
        result = self._execute_bound(
            "write_file_update",
            request_id="req_tool_005",
            intent_id="intent_tool_005",
            session_id="session_tool",
            seq=5,
            args={"path": "workspace/app.py"},
            approval_token_ref=approval,
            ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", result.status)
        self.assertEqual("TOOL_EXECUTED", result.result_code)
        self.assertTrue(result.evidence_ref and result.evidence_ref.startswith("evidence::"))

        event = self.ledger.get_event(result.evidence_ref)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("write_file_update", event["skill_name"])
        self.assertEqual("SUCCESS", event["result_status"])
        self.assertEqual("TOOL_EXECUTED", event["result_code"])

    def test_t_tool_03_direct_api_is_audited_rejection_without_handler(self) -> None:
        calls: list[dict] = []
        self.broker.register_handler(
            "read_system_status",
            lambda args, _ctx: calls.append(dict(args)) or {"status": "unexpected"},
            replay_safe_local=True,
            adapter_version_id="contract.direct-api-must-not-run.v1",
        )

        result = self.broker.execute(
            request_id="req_tool_direct_retired",
            intent_id="intent_tool_direct_retired",
            session_id="session_tool",
            seq=6,
            tool_id="read_system_status",
            args={"probe": True},
            ts_ms=self.now_ms,
        )

        self.assertEqual("REJECTED", result.status)
        self.assertEqual(GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED, result.result_code)
        self.assertEqual([], calls)
        row = self.ledger.skill_exec_log[-1]
        self.assertEqual("REJECTED", row["result_status"])
        self.assertEqual(GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED, row["result_code"])

    def test_raw_bound_api_cannot_turn_metadata_binding_into_authority(self) -> None:
        result = self.broker.execute_bound(
            expected_binding=self.broker.resolve_execution_binding(
                "read_system_status"
            ),
            request_id="req_tool_raw_bound_retired",
            intent_id="intent_tool_raw_bound_retired",
            session_id="session_tool",
            seq=7,
            tool_id="read_system_status",
            ts_ms=self.now_ms,
        )

        self.assertEqual("REJECTED", result.status)
        self.assertEqual(
            GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
            result.result_code,
        )


if __name__ == "__main__":
    unittest.main()
