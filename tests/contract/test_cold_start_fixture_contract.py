import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_OUTPUT = ROOT / "tests" / "fixtures" / "cold_start" / "no_api_key_expected_output.json"


class TestColdStartFixtureContract(unittest.TestCase):
    def test_cold_start_fixture_has_stage_and_reason_code(self) -> None:
        payload = json.loads(EXPECTED_OUTPUT.read_text(encoding="utf-8"))

        self.assertIsInstance(payload, dict)
        self.assertIn("quality", payload)
        self.assertIsInstance(payload["quality"], dict)
        self.assertIn("stage", payload["quality"])
        self.assertTrue(payload["quality"]["stage"])
        self.assertIn("reason_code", payload)
        self.assertTrue(payload["reason_code"])
