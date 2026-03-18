import json
import unittest
from pathlib import Path

from host_brain.airlock.quarantine import CLASS_DRAFT_INSTRUCTION, CLASS_FACT, quarantine_ingress
from host_brain.airlock.sanitizer import build_sanitization_report, gate_egress_payload
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import AIRLOCK_BLOCKED_EGRESS, AIRLOCK_BLOCKED_INGRESS


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "airlock"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class TestAirlockContract(unittest.TestCase):
    def test_t39_missing_sanitization_report_blocks_egress_and_logs(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        fixture = _load_json(FIXTURES / "bad" / "egress_missing_report.json")
        request_id = fixture["request_id"]
        payload = fixture["payload"]

        blocked = gate_egress_payload(
            payload=payload,
            sanitization_report_json=None,
            request_id=request_id,
            ledger=ledger,
            ts_ms=1000,
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(AIRLOCK_BLOCKED_EGRESS, blocked.reason_code)

        self.assertEqual(1, len(ledger.airlock_log))
        blocked_row = ledger.airlock_log[0]
        self.assertEqual("EGRESS", blocked_row["direction"])
        self.assertEqual(AIRLOCK_BLOCKED_EGRESS, blocked_row["reason_code"])
        self.assertEqual(1, blocked_row["blocked"])
        self.assertTrue(str(blocked_row["payload_digest"]).startswith("sha256:"))

        sanitized = build_sanitization_report(payload)
        self.assertNotIn("hard_state", sanitized.sanitized_payload)
        self.assertNotIn("rule_trace", sanitized.sanitized_payload)
        self.assertEqual("[REDACTED]", sanitized.sanitized_payload["api_key"])
        self.assertIn("removed_fields", sanitized.sanitization_report_json)
        self.assertIn("redactions", sanitized.sanitization_report_json)
        self.assertIn("payload_digest", sanitized.sanitization_report_json)
        self.assertTrue(sanitized.sanitization_report_json["payload_digest"].startswith("sha256:"))

        allowed = gate_egress_payload(
            payload=sanitized.sanitized_payload,
            sanitization_report_json=sanitized.sanitization_report_json,
            request_id="req_airlock_egress_002",
            ledger=ledger,
            ts_ms=1001,
        )
        self.assertTrue(allowed.allowed)
        self.assertIsNone(allowed.reason_code)
        self.assertEqual(2, len(ledger.airlock_log))
        allow_row = ledger.airlock_log[1]
        self.assertEqual("EGRESS", allow_row["direction"])
        self.assertEqual(0, allow_row["blocked"])
        self.assertIn("hard_state", allow_row["removed_fields"])
        self.assertIn("api_key", allow_row["redactions"])
        ledger.close()

    def test_t40_ingress_classification_and_quarantine_never_direct_executes(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        draft_instruction = _load_text(FIXTURES / "bad" / "ingress_direct_exec.txt")
        fact_text = _load_text(FIXTURES / "good" / "ingress_fact.txt")

        instruction_result = quarantine_ingress(
            draft_instruction,
            request_id="req_airlock_ingress_001",
            ledger=ledger,
            ts_ms=2000,
        )
        self.assertEqual(CLASS_DRAFT_INSTRUCTION, instruction_result.classification)
        self.assertTrue(instruction_result.draft_only)
        self.assertFalse(instruction_result.allow_execute)
        self.assertEqual(AIRLOCK_BLOCKED_INGRESS, instruction_result.reason_code)

        fact_result = quarantine_ingress(
            fact_text,
            request_id="req_airlock_ingress_002",
            ledger=ledger,
            ts_ms=2001,
        )
        self.assertEqual(CLASS_FACT, fact_result.classification)
        self.assertTrue(fact_result.draft_only)
        self.assertFalse(fact_result.allow_execute)
        self.assertEqual(AIRLOCK_BLOCKED_INGRESS, fact_result.reason_code)

        self.assertEqual(2, len(ledger.airlock_log))
        directions = {row["direction"] for row in ledger.airlock_log}
        self.assertEqual({"INGRESS"}, directions)
        classifications = {row["ingress_classification"] for row in ledger.airlock_log}
        self.assertEqual({CLASS_DRAFT_INSTRUCTION, CLASS_FACT}, classifications)
        for row in ledger.airlock_log:
            self.assertEqual(1, row["blocked"])
            self.assertEqual(AIRLOCK_BLOCKED_INGRESS, row["reason_code"])
            self.assertTrue(str(row["payload_digest"]).startswith("sha256:"))
            self.assertTrue(str(row["quarantine_digest"]).startswith("sha256:"))
        ledger.close()


if __name__ == "__main__":
    unittest.main()
