import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = ROOT / "tools" / "static_scan" / "bond_ssot_scan.py"
FIXTURES = ROOT / "tests" / "fixtures" / "bond"


def _load_scan_module():
    spec = importlib.util.spec_from_file_location("bond_ssot_scan", SCAN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scan script: {SCAN_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBondStaticScanContract(unittest.TestCase):
    def test_t_ssot_01_flags_non_single_writer_fixture(self) -> None:
        scanner = _load_scan_module()
        bad_file = FIXTURES / "bad" / "non_single_writer_example.py"
        violations = scanner.scan_file(bad_file)
        self.assertEqual(1, len(violations))
        row = violations[0]
        self.assertEqual("T-SSOT-01", row["rule_id"])
        self.assertIn("write_bond_event", row["message"])
        self.assertTrue(str(row["path"]).endswith("tests/fixtures/bond/bad/non_single_writer_example.py"))
        self.assertGreater(row["line"], 0)

    def test_t_ssot_01_allows_bond_dynamics_single_writer_path(self) -> None:
        scanner = _load_scan_module()
        allowed_file = ROOT / "host_brain" / "bond" / "bond_dynamics.py"
        violations = scanner.scan_file(allowed_file)
        self.assertEqual([], violations)

    def test_t_ssot_01_repo_scan_is_clean_for_runtime_sources(self) -> None:
        scanner = _load_scan_module()
        violations = scanner.scan_repo(ROOT)
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
