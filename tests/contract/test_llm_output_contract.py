import json
import unittest
from pathlib import Path

from host_brain.core.qa import (
    VIOLATION_FALSE_EXECUTION_CLAIM,
    VIOLATION_JSON_PARSE_ERROR,
    VIOLATION_MISSING_REQUIRED_FIELD,
    VIOLATION_SINGLE_JSON_REQUIRED,
    lint_llm_output,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "llm"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class TestLLMOutputContract(unittest.TestCase):
    def test_t_llm_01_good_fixture_is_valid(self) -> None:
        good = _read_text(FIXTURES / "good" / "strict_json_ok.json")
        result = lint_llm_output(good)
        self.assertTrue(result.valid, msg=result.as_dict())

    def test_t_llm_01_multi_json_with_prefix_is_rejected(self) -> None:
        bad = _read_text(FIXTURES / "bad" / "multi_json_with_prefix.txt")
        result = lint_llm_output(bad)
        codes = {v.code for v in result.violations}
        self.assertTrue(
            VIOLATION_SINGLE_JSON_REQUIRED in codes
            or VIOLATION_JSON_PARSE_ERROR in codes,
            msg=result.as_dict(),
        )

    def test_t_llm_01_missing_required_field_is_rejected(self) -> None:
        bad = _read_text(FIXTURES / "bad" / "missing_required_fields.json")
        result = lint_llm_output(bad)
        codes = {v.code for v in result.violations}
        self.assertIn(VIOLATION_MISSING_REQUIRED_FIELD, codes, msg=result.as_dict())

    def test_t_llm_01_false_execution_claim_is_rejected(self) -> None:
        bad = _read_text(FIXTURES / "bad" / "false_execution_claim.json")
        result = lint_llm_output(bad)
        codes = {v.code for v in result.violations}
        self.assertIn(VIOLATION_FALSE_EXECUTION_CLAIM, codes, msg=result.as_dict())

    def test_fixture_files_are_valid_json_when_expected(self) -> None:
        json.loads(_read_text(FIXTURES / "good" / "strict_json_ok.json"))
        json.loads(_read_text(FIXTURES / "bad" / "missing_required_fields.json"))
        json.loads(_read_text(FIXTURES / "bad" / "false_execution_claim.json"))
