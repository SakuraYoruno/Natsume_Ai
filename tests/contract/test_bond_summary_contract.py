import json
import unittest
from pathlib import Path

from host_brain.bond.summarizer import derive_bond_summary, summary_has_numeric_value
from host_brain.core.context_pack import inject_bond_summary, validate_context_pack


ROOT = Path(__file__).resolve().parents[2]
BOND_FIXTURES = ROOT / "tests" / "fixtures" / "bond"
CONTEXT_FIXTURES = ROOT / "tests" / "fixtures" / "context_pack"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestBondSummaryContract(unittest.TestCase):
    def test_t_74_summary_is_derived_and_numeric_free(self) -> None:
        event = _load_json(BOND_FIXTURES / "good" / "bond_event_for_summary.json")
        summary = derive_bond_summary(event)

        self.assertEqual(
            {"stage", "trend", "capability_hint", "event_id", "evidence_refs"},
            set(summary.keys()),
        )
        self.assertEqual("STRANGER", summary["stage"])
        self.assertEqual("稳定", summary["trend"])
        self.assertEqual("SUPPORTIVE", summary["capability_hint"])
        self.assertEqual("bond_evt_summary_001", summary["event_id"])
        self.assertEqual(["evidence::bond::summary::001"], summary["evidence_refs"])
        self.assertFalse(summary_has_numeric_value(summary))
        self.assertNotIn("trust_value", summary)
        self.assertNotIn("payload", summary)

    def test_t_75_summary_is_injected_to_context_pack_bond_slot(self) -> None:
        context_pack = _load_json(CONTEXT_FIXTURES / "good" / "minimal_valid.json")
        event = _load_json(BOND_FIXTURES / "good" / "bond_event_for_summary.json")

        injected = inject_bond_summary(context_pack, bond_event=event)
        self.assertIn("bond_summary", injected)
        self.assertEqual("bond_evt_summary_001", injected["bond_summary"]["event_id"])
        self.assertEqual(
            ["evidence::bond::summary::001"],
            injected["bond_summary"]["evidence_refs"],
        )
        self.assertFalse(summary_has_numeric_value(injected["bond_summary"]))

        result = validate_context_pack(injected)
        self.assertTrue(result.valid, msg=result.as_dict())

        for entry in injected["authoritative"]:
            if isinstance(entry, dict):
                self.assertNotIn("event_id", entry)
                self.assertNotIn("evidence_refs", entry)


if __name__ == "__main__":
    unittest.main()
