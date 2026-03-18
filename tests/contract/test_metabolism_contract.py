import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.metabolism import (
    build_governance_snapshot,
    energy_bucket,
    evolve_hard_state,
    governance_snapshot_ref,
    hard_state_ref,
    normalize_hard_state,
)
from host_brain.governor.decision import IntentDecisionEngine


ROOT = Path(__file__).resolve().parents[2]
INTENT_FIXTURES = ROOT / "tests" / "fixtures" / "intent" / "good"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestMetabolismContract(unittest.TestCase):
    def test_t_meta_01_normalize_and_evolve_hard_state(self) -> None:
        state = normalize_hard_state(
            {
                "energy_level": 130,
                "mood_state": "unknown",
                "sys_mode": "INVALID",
                "safety_flag": 0,
                "last_event_id": "evt_meta_001",
                "ts_ms": 1000,
            }
        )
        self.assertEqual(100, state.energy_level)
        self.assertEqual("steady", state.mood_state)
        self.assertEqual("normal", state.sys_mode)
        self.assertFalse(state.safety_flag)
        self.assertEqual("high", energy_bucket(state.energy_level))

        evolved = evolve_hard_state(
            state,
            event_kind="warning",
            energy_delta=-85,
            sys_mode="focus",
            safety_flag=True,
            event_id="evt_meta_002",
            ts_ms=2000,
        )
        self.assertEqual(15, evolved.energy_level)
        self.assertEqual("critical", evolved.mood_state)
        self.assertEqual("focus", evolved.sys_mode)
        self.assertTrue(evolved.safety_flag)
        self.assertEqual("evt_meta_002", evolved.last_event_id)
        self.assertEqual("very_low", energy_bucket(evolved.energy_level))

    def test_t_meta_01_snapshot_ref_is_stable_and_sensitive_to_change(self) -> None:
        base_state = normalize_hard_state({"energy_level": 60, "sys_mode": "normal"})
        base_snapshot = build_governance_snapshot(base_state, overrides={"trust_level": 50})
        ref_a = governance_snapshot_ref(base_snapshot)
        ref_b = governance_snapshot_ref(base_snapshot)
        self.assertEqual(ref_a, ref_b)
        self.assertTrue(ref_a.startswith("gov_snap::"))

        changed_state = evolve_hard_state(base_state, energy_delta=-30, event_id="evt_meta_003")
        changed_snapshot = build_governance_snapshot(changed_state, overrides={"trust_level": 50})
        ref_c = governance_snapshot_ref(changed_snapshot)
        self.assertNotEqual(ref_a, ref_c)
        self.assertTrue(hard_state_ref(changed_state).startswith("hard_state::"))

    def test_t_meta_01_decision_row_uses_governance_snapshot_ref(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        engine = IntentDecisionEngine(ledger=ledger, expiry_window_ms=300_000)
        now_ms = 1_800_040_000_000

        intent = _load_json(INTENT_FIXTURES / "intent_v1_valid.json")
        intent["id"] = "intent_meta_001"
        intent["nonce"] = "nonce_meta_001"
        intent["seq"] = 1
        intent["ts_ms"] = now_ms - 500
        intent["target"] = "host"

        result = engine.evaluate_intent(
            intent,
            current_ms=now_ms,
            governance_snapshot={
                "trust_level": 65,
                "hard_state": {
                    "energy_level": 62,
                    "mood_state": "steady",
                    "sys_mode": "normal",
                    "safety_flag": False,
                    "last_event_id": "evt_meta_004",
                    "ts_ms": now_ms - 100,
                },
            },
        )
        self.assertEqual("APPROVE", result["decision"])
        self.assertIn("snapshot_refs", result)
        self.assertTrue(str(result["snapshot_refs"]["governance_snapshot"]).startswith("gov_snap::"))
        self.assertTrue(str(result["snapshot_refs"]["hard_state"]).startswith("hard_state::"))
        self.assertNotEqual("runtime", result["snapshot_refs"]["governance_snapshot"])

        ledger.close()


if __name__ == "__main__":
    unittest.main()
