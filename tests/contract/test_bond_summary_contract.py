import json
import unittest
from pathlib import Path

from host_brain.bond.summarizer import (
    BOND_SUMMARY_ALLOWED_KEYS,
    derive_bond_summary,
    normalize_bond_summary,
    summary_has_numeric_value,
)


ROOT = Path(__file__).resolve().parents[2]
BOND_FIXTURES = ROOT / "tests" / "fixtures" / "bond"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestBondSummaryContract(unittest.TestCase):
    def test_t_74_summary_is_derived_and_numeric_free(self) -> None:
        event = _load_json(BOND_FIXTURES / "good" / "bond_event_for_summary.json")
        summary = derive_bond_summary(event)

        self.assertEqual(
            {"stage", "trend", "capability_hint", "event_id", "evidence_refs"},
            set(summary.keys()),
        )
        self.assertEqual("FRIEND", summary["stage"])
        self.assertEqual("稳定", summary["trend"])
        self.assertEqual("SUPPORTIVE", summary["capability_hint"])
        self.assertEqual("bond_evt_summary_001", summary["event_id"])
        self.assertEqual(["evidence::bond::summary::001"], summary["evidence_refs"])
        self.assertFalse(summary_has_numeric_value(summary))
        self.assertNotIn("trust_value", summary)
        self.assertNotIn("payload", summary)

    def test_t_75_summary_normalization_remains_privacy_safe(self) -> None:
        normalized = normalize_bond_summary(
            {
                "stage": "FRIEND",
                "trend": "稳定",
                "capability_hint": "SUPPORTIVE",
                "event_id": "bond_evt_summary_001",
                "evidence_refs": ["evidence::bond::summary::001"],
                "score": 0.99,
                "trust_value": 1.0,
            }
        )

        self.assertEqual(set(BOND_SUMMARY_ALLOWED_KEYS), set(normalized.keys()))
        self.assertFalse(summary_has_numeric_value(normalized))
        self.assertNotIn("score", normalized)
        self.assertNotIn("trust_value", normalized)


if __name__ == "__main__":
    unittest.main()
