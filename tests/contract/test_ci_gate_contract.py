import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC_LINT_SCRIPT = ROOT / "tools" / "doc_lint" / "doc_audience_lint.py"
DELIVERY_GATE_SCRIPT = ROOT / "tools" / "doc_lint" / "delivery_gate.py"

CI_FIXTURES = ROOT / "tests" / "fixtures" / "ci"
DOC_BAD = CI_FIXTURES / "doc_gate" / "bad"
DOC_GOOD = CI_FIXTURES / "doc_gate" / "good" / "valid_headers.md"
STATIC_BAD_ROOT = CI_FIXTURES / "static_scan" / "bad"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCIGateContract(unittest.TestCase):
    def setUp(self) -> None:
        self.doc_lint = _load_module(DOC_LINT_SCRIPT, "doc_audience_lint")
        self.delivery_gate = _load_module(DELIVERY_GATE_SCRIPT, "delivery_gate")

    def test_t_ci_01_doc_gate_rejects_missing_headers(self) -> None:
        failures = self.doc_lint.scan_markdown_files(
            root=ROOT,
            paths=[DOC_BAD / "missing_audience.md", DOC_BAD / "missing_title.md"],
        )
        self.assertEqual(2, len(failures))
        codes = {row["reason_code"] for row in failures}
        self.assertIn("DOC_AUDIENCE_INVALID", codes)
        self.assertIn("DOC_TITLE_HEADER_MISSING", codes)
        for row in failures:
            self.assertEqual("doc_gate", row["check_id"])
            self.assertIsInstance(row["failed_artifact"], str)

    def test_t_ci_01_doc_gate_accepts_valid_headers(self) -> None:
        failures = self.doc_lint.scan_markdown_files(root=ROOT, paths=[DOC_GOOD])
        self.assertEqual([], failures)

    def test_t_ci_01_unified_gate_report_shape(self) -> None:
        report = self.delivery_gate.run_delivery_gate(
            root=ROOT,
            checks=["doc_gate", "static_scan"],
            doc_paths=[DOC_BAD / "missing_audience.md"],
            static_scan_root=STATIC_BAD_ROOT,
            include_tests_in_static_scan=True,
        )
        self.assertEqual("delivery-gate", report["job_id"])
        self.assertEqual("failed", report["status"])
        self.assertEqual(2, report["summary"]["total_checks"])
        self.assertEqual(2, report["summary"]["failed_checks"])
        self.assertGreaterEqual(report["summary"]["failure_count"], 2)
        self.assertGreaterEqual(len(report["failures"]), 2)
        for row in report["failures"]:
            self.assertEqual("delivery-gate", row["job_id"])
            self.assertIn(row["check_id"], {"doc_gate", "static_scan"})
            self.assertIsInstance(row["reason_code"], str)
            self.assertIsInstance(row["failed_artifact"], str)
        self.assertTrue(any(row["check_id"] == "doc_gate" for row in report["failures"]))
        self.assertTrue(any(row["check_id"] == "static_scan" for row in report["failures"]))


if __name__ == "__main__":
    unittest.main()
