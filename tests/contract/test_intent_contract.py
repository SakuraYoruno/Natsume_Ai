import json
import unittest
from copy import deepcopy
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.schemas import (
    EXPIRED_INTENT,
    INTENT_SCHEMA_INVALID,
    REPLAY_DETECTED,
    SEQ_INVALID,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intent"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestIntentContract(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.engine = IntentDecisionEngine(ledger=self.ledger, expiry_window_ms=300_000)
        self.now_ms = 1_800_000_000_000

    def _fresh_intent(self, *, seq: int, intent_id: str, nonce: str) -> dict:
        payload = _load_json(FIXTURES / "good" / "intent_v1_valid.json")
        payload["seq"] = seq
        payload["id"] = intent_id
        payload["nonce"] = nonce
        payload["ts_ms"] = self.now_ms - 1_000
        return payload

    def test_t18_lenient_parse_and_strict_validate(self) -> None:
        payload = self._fresh_intent(seq=1, intent_id="intent_t18_001", nonce="nonce_t18_001")
        raw = f"noise-prefix {json.dumps(payload, ensure_ascii=False)} trailing-noise"
        result = self.engine.evaluate_intent(raw, current_ms=self.now_ms)

        self.assertEqual("APPROVE", result["decision"])
        self.assertIsNone(result["reason_code"])
        self.assertTrue(any(row["parse_stage"] == "LENIENT_OK" for row in self.ledger.intent_log))

    def test_t19_replay_detected(self) -> None:
        payload = self._fresh_intent(seq=1, intent_id="intent_t19_001", nonce="nonce_t19_001")
        first = self.engine.evaluate_intent(deepcopy(payload), current_ms=self.now_ms)
        second = self.engine.evaluate_intent(deepcopy(payload), current_ms=self.now_ms)

        self.assertEqual("APPROVE", first["decision"])
        self.assertEqual("REJECT", second["decision"])
        self.assertEqual(REPLAY_DETECTED, second["reason_code"])

    def test_t20_seq_invalid(self) -> None:
        first = self._fresh_intent(seq=2, intent_id="intent_t20_001", nonce="nonce_t20_001")
        second = self._fresh_intent(seq=2, intent_id="intent_t20_002", nonce="nonce_t20_002")

        first_result = self.engine.evaluate_intent(first, current_ms=self.now_ms)
        second_result = self.engine.evaluate_intent(second, current_ms=self.now_ms)

        self.assertEqual("APPROVE", first_result["decision"])
        self.assertEqual("REJECT", second_result["decision"])
        self.assertEqual(SEQ_INVALID, second_result["reason_code"])

    def test_t21_expired_intent(self) -> None:
        payload = self._fresh_intent(seq=1, intent_id="intent_t21_001", nonce="nonce_t21_001")
        payload["ts_ms"] = self.now_ms - 301_000
        result = self.engine.evaluate_intent(payload, current_ms=self.now_ms)

        self.assertEqual("REJECT", result["decision"])
        self.assertEqual(EXPIRED_INTENT, result["reason_code"])

    def test_t22_reason_code_is_stable_for_schema_failure(self) -> None:
        bad_1 = _load_json(FIXTURES / "bad" / "intent_v1_missing_field.json")
        bad_2 = _load_json(FIXTURES / "bad" / "intent_v1_missing_field.json")
        bad_1["id"] = "intent_t22_001"
        bad_1["nonce"] = "nonce_t22_001"
        bad_1["ts_ms"] = self.now_ms - 1_000
        bad_2["id"] = "intent_t22_002"
        bad_2["nonce"] = "nonce_t22_002"
        bad_2["ts_ms"] = self.now_ms - 1_000

        result_1 = self.engine.evaluate_intent(bad_1, current_ms=self.now_ms)
        result_2 = self.engine.evaluate_intent(bad_2, current_ms=self.now_ms)

        self.assertEqual(INTENT_SCHEMA_INVALID, result_1["reason_code"])
        self.assertEqual(INTENT_SCHEMA_INVALID, result_2["reason_code"])

    def test_t23_future_intent_ts_is_clamped_in_intent_log(self) -> None:
        payload = self._fresh_intent(seq=1, intent_id="intent_t23_001", nonce="nonce_t23_001")
        payload["ts_ms"] = self.now_ms + 120_000

        result = self.engine.evaluate_intent(payload, current_ms=self.now_ms)

        self.assertEqual("REJECT", result["decision"])
        self.assertEqual(EXPIRED_INTENT, result["reason_code"])
        self.assertEqual(self.now_ms, self.ledger.intent_log[-1]["ts_ms"])

    def test_t24_extension_fields_do_not_break_strict_validate(self) -> None:
        payload = self._fresh_intent(seq=1, intent_id="intent_t24_001", nonce="nonce_t24_001")
        payload["route"] = "skill"
        payload["target_id"] = "read_only_status"
        payload["planned_steps"] = [
            {
                "step_index": 1,
                "route": "skill",
                "target_id": "read_only_status",
                "description": "status one",
                "task_description": "status one",
                "route_reason": "test:t24:one",
                "allow_parallel": True,
                "skill_family": "social",
                "risk_level": 1,
            },
            {
                "step_index": 2,
                "route": "skill",
                "target_id": "read_only_status",
                "description": "status two",
                "task_description": "status two",
                "route_reason": "test:t24:two",
                "allow_parallel": True,
                "skill_family": "social",
                "risk_level": 1,
            },
        ]

        result = self.engine.evaluate_intent(payload, current_ms=self.now_ms)

        self.assertEqual("APPROVE", result["decision"])
        self.assertIsNone(result["reason_code"])
