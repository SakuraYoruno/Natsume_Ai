import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.decision import IntentDecisionEngine


ROOT = Path(__file__).resolve().parents[2]
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"
PROPOSERS_FIX = ROOT / "tests" / "fixtures" / "intent" / "proposals" / "proposer_ab.json"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestIntentProposalContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.engine = IntentDecisionEngine(ledger=self.ledger, expiry_window_ms=300_000)

    def _intent(self, *, seq: int, intent_id: str, nonce: str) -> dict:
        payload = _load_json(INTENT_FIX)
        payload["seq"] = seq
        payload["id"] = intent_id
        payload["nonce"] = nonce
        payload["ts_ms"] = self.now_ms - 1_000
        return payload

    def test_t_intent_01_intent_log_min_fields_present(self) -> None:
        proposers = _load_json(PROPOSERS_FIX)
        intent = self._intent(seq=1, intent_id="intent_proposal_001", nonce="nonce_proposal_001")
        self.engine.evaluate_intent(
            intent,
            request_id="req_proposal_001",
            thread_id="thread_001",
            turn_id="turn_001",
            current_ms=self.now_ms,
            proposer_node_id=proposers[0]["proposer_node_id"],
            model_snapshot=proposers[0]["model_snapshot"],
            prompt_profile_id=proposers[0]["prompt_profile_id"],
            tool_visibility_hash=proposers[0]["tool_visibility_hash"],
        )

        row = self.ledger.intent_log[-1]
        for key in (
            "request_id",
            "thread_id",
            "turn_id",
            "intent_id",
            "session_id",
            "seq",
            "ts_ms",
            "nonce",
            "proposer_node_id",
            "model_snapshot",
            "prompt_profile_id",
            "tool_visibility_hash",
            "proposed_intent_draft_json",
            "lint_result",
        ):
            self.assertIn(key, row)

    def test_t_intent_02_proposal_decision_exec_logs_are_separated(self) -> None:
        proposers = _load_json(PROPOSERS_FIX)
        intent = self._intent(seq=2, intent_id="intent_proposal_002", nonce="nonce_proposal_002")
        self.engine.evaluate_intent(
            intent,
            request_id="req_proposal_002",
            thread_id="thread_002",
            turn_id="turn_002",
            current_ms=self.now_ms,
            proposer_node_id=proposers[1]["proposer_node_id"],
            model_snapshot=proposers[1]["model_snapshot"],
            prompt_profile_id=proposers[1]["prompt_profile_id"],
            tool_visibility_hash=proposers[1]["tool_visibility_hash"],
        )

        intent_row = self.ledger.intent_log[-1]
        governor_row = self.ledger.governor_log[-1]

        self.assertNotIn("decision", intent_row)
        self.assertNotIn("result_status", intent_row)
        self.assertIn("decision", governor_row)
        self.assertIn("reason_code", governor_row)
        self.assertEqual(0, len(self.ledger.skill_exec_log))
