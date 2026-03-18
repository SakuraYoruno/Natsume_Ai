import importlib.util
import json
import unittest
from pathlib import Path

from host_brain.core.errors import (
    ORCH_NODE_MISSING,
    ORCH_PARAM_DRIFT,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SKILL_TOKEN_MISSING,
)


ROOT = Path(__file__).resolve().parents[2]
DRILL_TOOL = ROOT / "tools" / "replay" / "production_drill.py"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_drill_module():
    spec = importlib.util.spec_from_file_location("production_drill", DRILL_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load production drill module: {DRILL_TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProductionDrillE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.drill_module = _load_drill_module()

    def test_t_drill_02_five_orchestration_control_scenarios_are_stable(self) -> None:
        fixture_paths = [
            Path("tests/fixtures/drill/good/provider_down.json"),
            Path("tests/fixtures/drill/bad/orch_missing_profile.json"),
            Path("tests/fixtures/drill/bad/orch_selector_no_candidate.json"),
            Path("tests/fixtures/drill/bad/orch_param_drift.json"),
            Path("tests/fixtures/drill/bad/orch_approval_token_missing.json"),
        ]
        expected_by_id = {
            row["scenario_id"]: row["expected"]
            for row in (_load_json(ROOT / rel_path) for rel_path in fixture_paths)
        }

        suite = self.drill_module.run_drill_suite(
            root=ROOT,
            scenario_fixtures=fixture_paths,
            ts_ms=1_800_100_000_000,
        )
        self.assertEqual(self.drill_module.S_DRILL_SUITE, suite["stage"])
        self.assertEqual(5, suite["summary"]["scenario_total"])
        self.assertEqual(2, suite["summary"]["failed_count"])
        self.assertEqual(3, suite["summary"]["degraded_count"])
        self.assertEqual(SERVICE_UNAVAILABLE_DEPENDENCY, suite["reason_code"])

        rows = {row["scenario_id"]: row for row in suite["scenarios"]}
        self.assertEqual(set(expected_by_id.keys()), set(rows.keys()))

        for scenario_id, expected in expected_by_id.items():
            row = rows[scenario_id]
            self.assertEqual(expected["status"], row["status"])
            self.assertEqual(expected["stage"], row["stage"])
            self.assertEqual(expected["reason_code"], row["reason_code"])
            self.assertIn(expected["reason_code"], row["reason_code_chain"])
            self.assertTrue(row["replay_ref"].startswith("request::"))
            self.assertEqual("COMPLETE_REJECTED", row["replay_report"]["status"])
            self.assertIn("model_profile_id", row)
            self.assertIn(row["reason_code"], row["replay_report"]["summary"]["reason_code_chain"])
            json.loads(json.dumps(row, ensure_ascii=False))

        self.assertEqual(ORCH_NODE_MISSING, rows["drill.orch.missing_profile"]["reason_code"])
        self.assertEqual(ORCH_NODE_MISSING, rows["drill.orch.selector_no_candidate"]["reason_code"])
        self.assertEqual(ORCH_PARAM_DRIFT, rows["drill.orch.param_drift"]["reason_code"])
        self.assertEqual(SKILL_TOKEN_MISSING, rows["drill.orch.approval_token_missing"]["reason_code"])


if __name__ == "__main__":
    unittest.main()
