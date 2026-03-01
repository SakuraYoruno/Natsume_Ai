import json
import unittest
from pathlib import Path

from host_brain.core.errors import GOVERNOR_OUTPUT_INVALID, SKILL_TOKEN_MISSING
from host_brain.governor.schemas import validate_governor_output


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "governor_output"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestGovernorOutputContract(unittest.TestCase):
    def test_t14_reject_requires_reason_code(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "reject_missing_reason.json")
        result = validate_governor_output(payload)
        self.assertFalse(result.valid)
        self.assertEqual(GOVERNOR_OUTPUT_INVALID, result.reason_code)

    def test_t15_approve_requires_approval_token_ref(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "approve_missing_token.json")
        result = validate_governor_output(payload)
        self.assertFalse(result.valid)
        self.assertEqual(SKILL_TOKEN_MISSING, result.reason_code)

    def test_t16_same_invalid_reject_maps_to_stable_reason_code(self) -> None:
        payload_1 = _load_json(FIXTURES / "bad" / "reject_missing_reason.json")
        payload_2 = _load_json(FIXTURES / "bad" / "reject_missing_reason.json")
        result_1 = validate_governor_output(payload_1)
        result_2 = validate_governor_output(payload_2)
        self.assertEqual(GOVERNOR_OUTPUT_INVALID, result_1.reason_code)
        self.assertEqual(GOVERNOR_OUTPUT_INVALID, result_2.reason_code)

    def test_t17_reject_requires_rule_trace(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "reject_missing_rule_trace.json")
        result = validate_governor_output(payload)
        self.assertFalse(result.valid)
        self.assertEqual(GOVERNOR_OUTPUT_INVALID, result.reason_code)

    def test_valid_fixtures_pass_contract(self) -> None:
        approve = _load_json(FIXTURES / "good" / "approve_valid.json")
        reject = _load_json(FIXTURES / "good" / "reject_valid.json")
        self.assertTrue(validate_governor_output(approve).valid)
        self.assertTrue(validate_governor_output(reject).valid)
