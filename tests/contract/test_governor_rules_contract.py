import json
import unittest
from pathlib import Path

from host_brain.governor.rules import evaluate_governor_gates


ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "governor_rules"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8-sig"))


class TestGovernorRulesContract(unittest.TestCase):
    def test_t_gov_01_rule_trace_has_threshold_and_snapshot_summary(self) -> None:
        payload = _load("snapshot_hit_trust.json")
        result = evaluate_governor_gates(payload)

        self.assertEqual("REJECT", result["decision"])
        self.assertTrue(result["reason_code"])
        self.assertEqual(8, len(result["rule_trace"]))

        for row in result["rule_trace"]:
            self.assertIn("gate_id", row)
            self.assertIn("hit", row)
            self.assertIn("threshold_summary", row)
            self.assertIn("snapshot_summary", row)
            self.assertIn("reason_hint", row)

    def test_t_gov_01_rule_trace_is_stable_for_same_input(self) -> None:
        payload = _load("snapshot_pass.json")
        result_1 = evaluate_governor_gates(payload)
        result_2 = evaluate_governor_gates(payload)
        self.assertEqual(result_1, result_2)
