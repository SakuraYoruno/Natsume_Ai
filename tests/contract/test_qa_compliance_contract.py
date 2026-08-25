import json
import unittest
from pathlib import Path

from host_brain.core.qa import (
    REASON_FORMAT_DRIFT,
    REASON_QA_TIMEOUT,
    STAGE_QA_DOWNGRADE,
    STAGE_QA_REWRITE_OK,
    STAGE_QA_TIMEOUT,
    VIOLATION_CANON_AS_FACT,
    VIOLATION_FALSE_EXECUTION_CLAIM,
    check_canon_as_fact,
    enforce_qa_contract,
    lint_llm_output,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "qa"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class TestQAComplianceContract(unittest.TestCase):
    def test_t67_format_drift_triggers_one_rewrite(self) -> None:
        raw = _read_text(FIXTURES / "bad" / "format_drift_with_prefix.txt")
        rewritten = _read_text(FIXTURES / "good" / "rewrite_ok.json")

        calls = {"count": 0}

        def rewrite_once(_raw: str, _lint) -> str:
            calls["count"] += 1
            return rewritten

        result = enforce_qa_contract(raw, rewrite_once=rewrite_once)
        self.assertTrue(result.lint_result.valid, msg=result.lint_result.as_dict())
        self.assertEqual(1, calls["count"])
        self.assertTrue(result.rewrite_attempted)
        self.assertFalse(result.downgraded)
        self.assertEqual(STAGE_QA_REWRITE_OK, result.stage)
        self.assertEqual("narrative_only", result.payload["intent_draft"]["action"])
        # Ensure the resulting payload remains strict-contract JSON.
        self.assertTrue(
            lint_llm_output(json.dumps(result.payload, ensure_ascii=False)).valid
        )

    def test_t68_false_execution_claim_is_blocked(self) -> None:
        raw = _read_text(FIXTURES / "bad" / "false_execution_claim.json")
        result = enforce_qa_contract(raw)

        self.assertTrue(result.downgraded)
        self.assertEqual(VIOLATION_FALSE_EXECUTION_CLAIM, result.reason_code)
        self.assertEqual(STAGE_QA_DOWNGRADE, result.stage)
        self.assertTrue(result.payload["meta"]["degraded"])
        self.assertTrue(
            lint_llm_output(json.dumps(result.payload, ensure_ascii=False)).valid
        )

    def test_t69_canon_as_fact_is_blocked(self) -> None:
        raw = _read_text(FIXTURES / "bad" / "canon_as_fact.json")
        result = enforce_qa_contract(raw)

        self.assertTrue(result.downgraded)
        self.assertEqual(VIOLATION_CANON_AS_FACT, result.reason_code)
        self.assertEqual(STAGE_QA_DOWNGRADE, result.stage)
        self.assertTrue(
            lint_llm_output(json.dumps(result.payload, ensure_ascii=False)).valid
        )

    def test_t70_failure_path_still_returns_valid_json(self) -> None:
        raw = _read_text(FIXTURES / "bad" / "non_json.txt")

        def rewrite_once(_raw: str, _lint) -> str:
            return "still not json"

        result = enforce_qa_contract(raw, rewrite_once=rewrite_once)
        self.assertTrue(result.downgraded)
        self.assertEqual(REASON_FORMAT_DRIFT, result.reason_code)
        self.assertEqual(STAGE_QA_DOWNGRADE, result.stage)
        self.assertTrue(
            lint_llm_output(json.dumps(result.payload, ensure_ascii=False)).valid
        )

    def test_t71_timeout_path_still_returns_valid_json(self) -> None:
        raw = _read_text(FIXTURES / "bad" / "format_drift_with_prefix.txt")

        def rewrite_once(_raw: str, _lint) -> str:
            raise TimeoutError("rewrite timed out")

        result = enforce_qa_contract(raw, rewrite_once=rewrite_once)
        self.assertTrue(result.downgraded)
        self.assertEqual(REASON_QA_TIMEOUT, result.reason_code)
        self.assertEqual(STAGE_QA_TIMEOUT, result.stage)
        self.assertTrue(
            lint_llm_output(json.dumps(result.payload, ensure_ascii=False)).valid
        )

    def test_t_wcanon_06_canon_as_fact_fixture_is_blocked_by_current_qa(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "canon" / "world" / "qa_canon_as_fact_bad.json"
        cases = json.loads(fixture.read_text(encoding="utf-8-sig"))["cases"]

        for case in cases:
            result = check_canon_as_fact(case["narrative"])
            self.assertEqual(case["expect_violated"], result.violated, msg=case["id"])
            if case["expect_violated"]:
                self.assertIn(
                    case["expect_code"],
                    {violation.code for violation in result.violations},
                    msg=case["id"],
                )
