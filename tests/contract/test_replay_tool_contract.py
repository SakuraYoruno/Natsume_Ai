import importlib.util
import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    TRACE_MISSING_APPROVAL_TOKEN_REF,
    TRACE_MISSING_SKILL_EXEC,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "replay_logs"
REPLAY_TOOL = ROOT / "tools" / "replay" / "request_replay.py"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_replay_module():
    spec = importlib.util.spec_from_file_location("request_replay", REPLAY_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load replay tool module: {REPLAY_TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestReplayToolContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_060_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.replay_module = _load_replay_module()

    def tearDown(self) -> None:
        self.ledger.close()

    def test_t_replay_01_complete_chain_report(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "complete_chain_request.json")
        request_id = fixture["request_id"]

        self.ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_001",
                "turn_id": "turn_replay_001",
                "intent_id": "intent_replay_001",
                "session_id": "session_replay_001",
                "seq": 1,
                "ts_ms": self.now_ms,
                "nonce": "nonce_replay_001",
                "parse_stage": "LENIENT_OK",
            }
        )
        self.ledger.log_governor(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_001",
                "turn_id": "turn_replay_001",
                "intent_id": "intent_replay_001",
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": [],
                "tokens": {"approval_token_ref": "approval::replay::001"},
                "snapshot_refs": {"governance_snapshot": "runtime"},
                "stage": "S_GOVERNOR_OK",
                "ts_ms": self.now_ms + 1,
            }
        )
        self.ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_001",
                "turn_id": "turn_replay_001",
                "intent_id": "intent_replay_001",
                "event_id": "evt_replay_001",
                "skill_name": "read_only_status",
                "approval_token_ref": "approval::replay::001",
                "result_status": "SUCCESS",
                "result_code": "SKILL_EXECUTED",
                "evidence_ref": "evidence::replay::001",
                "ts_ms": self.now_ms + 2,
            }
        )

        self.ledger.log_model_control(
            {
                "request_id": request_id,
                "node_id": "respond",
                "model_node_id": "renderer_ja_c_style",
                "model_profile_id": "chat_ja_24x7_v1",
                "provider": "gemini",
                "model_id": "gemini-2.5-flash",
                "expected_param_hash": "h1",
                "actual_param_hash": "h1",
                "drift_detected": False,
                "approval_token_ref": "approval::replay::001",
                "evidence_ref": "evidence::replay::001",
                "reason_code": None,
                "status": "OK",
                "stage": "S_ORCH_RUN",
                "ts_ms": self.now_ms + 2,
            }
        )

        report = self.replay_module.build_request_replay_report(ledger=self.ledger, request_id=request_id)
        self.assertEqual("S_REPLAY_REPORT", report["stage"])
        self.assertEqual(fixture["expected_status"], report["status"])
        self.assertEqual(fixture["expected_reason_code"], report["reason_code"])
        self.assertEqual(0, report["summary"]["issue_count"])
        self.assertEqual(1, report["summary"]["intent_count"])
        self.assertEqual(1, report["summary"]["governor_count"])
        self.assertEqual(1, report["summary"]["skill_exec_count"])
        self.assertEqual(1, report["summary"]["model_control_count"])
        self.assertEqual(["evidence::replay::001"], report["summary"]["evidence_refs"])
        self.assertEqual([], report["issues"])
        self.assertIsInstance(report["triptych"]["intent_log"], list)
        self.assertIsInstance(report["triptych"]["governor_log"], list)
        self.assertIsInstance(report["triptych"]["skill_exec_log"], list)
        self.assertIsInstance(report["triptych"]["model_control_log"], list)
        json.loads(json.dumps(report, ensure_ascii=False))

    def test_t_replay_01_missing_skill_exec_is_diagnosed(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "missing_skill_exec_request.json")
        request_id = fixture["request_id"]

        self.ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_002",
                "turn_id": "turn_replay_002",
                "intent_id": "intent_replay_002",
                "session_id": "session_replay_002",
                "seq": 1,
                "ts_ms": self.now_ms + 10,
                "nonce": "nonce_replay_002",
                "parse_stage": "LENIENT_OK",
            }
        )
        self.ledger.log_governor(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_002",
                "turn_id": "turn_replay_002",
                "intent_id": "intent_replay_002",
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": [],
                "tokens": {"approval_token_ref": "approval::replay::002"},
                "snapshot_refs": {"governance_snapshot": "runtime"},
                "stage": "S_GOVERNOR_OK",
                "ts_ms": self.now_ms + 11,
            }
        )

        report = self.replay_module.build_request_replay_report(ledger=self.ledger, request_id=request_id)
        self.assertEqual(fixture["expected_status"], report["status"])
        self.assertEqual(TRACE_MISSING_SKILL_EXEC, report["reason_code"])
        self.assertEqual(1, report["summary"]["issue_count"])
        self.assertTrue(any(issue["code"] == TRACE_MISSING_SKILL_EXEC for issue in report["issues"]))
        self.assertIn(TRACE_MISSING_SKILL_EXEC, report["summary"]["reason_code_chain"])

    def test_t_replay_01_missing_token_ref_is_diagnosed(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "missing_token_ref_request.json")
        request_id = fixture["request_id"]

        self.ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_003",
                "turn_id": "turn_replay_003",
                "intent_id": "intent_replay_003",
                "session_id": "session_replay_003",
                "seq": 1,
                "ts_ms": self.now_ms + 20,
                "nonce": "nonce_replay_003",
                "parse_stage": "LENIENT_OK",
            }
        )
        self.ledger.log_governor(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_003",
                "turn_id": "turn_replay_003",
                "intent_id": "intent_replay_003",
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": [],
                "tokens": {"approval_token_ref": "approval::replay::003"},
                "snapshot_refs": {"governance_snapshot": "runtime"},
                "stage": "S_GOVERNOR_OK",
                "ts_ms": self.now_ms + 21,
            }
        )
        self.ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": "thread_replay_003",
                "turn_id": "turn_replay_003",
                "intent_id": "intent_replay_003",
                "event_id": "evt_replay_003",
                "skill_name": "read_only_status",
                "approval_token_ref": None,
                "result_status": "SUCCESS",
                "result_code": "SKILL_EXECUTED",
                "evidence_ref": "evidence::replay::003",
                "ts_ms": self.now_ms + 22,
            }
        )

        report = self.replay_module.build_request_replay_report(ledger=self.ledger, request_id=request_id)
        self.assertEqual(fixture["expected_status"], report["status"])
        self.assertEqual(TRACE_MISSING_APPROVAL_TOKEN_REF, report["reason_code"])
        self.assertEqual(1, report["summary"]["issue_count"])
        self.assertTrue(any(issue["code"] == TRACE_MISSING_APPROVAL_TOKEN_REF for issue in report["issues"]))
        self.assertIn(TRACE_MISSING_APPROVAL_TOKEN_REF, report["summary"]["reason_code_chain"])
        self.assertIn("SKILL_EXECUTED", report["summary"]["reason_code_chain"])
        self.assertEqual(["evidence::replay::003"], report["summary"]["evidence_refs"])


if __name__ == "__main__":
    unittest.main()
