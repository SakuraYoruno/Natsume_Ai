from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = ROOT / "tools" / "static_scan" / "nle_boundary_scan.py"
POLICY = ROOT / "tools" / "static_scan" / "nle_boundary_policy.json"
FIXTURES = ROOT / "tests" / "fixtures" / "static_scan" / "nle_boundary"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("nle_boundary_scan_contract", SCAN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scan script: {SCAN_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t_nle_01_production_boundary_baseline_is_exact_and_current() -> None:
    scanner = _load_scanner()
    report = scanner.scan_report(ROOT, policy_path=POLICY)

    assert report["policy_id"] == "TASK-0465_NLE_BOUNDARY_CANON_APPLY"
    assert report["violations"] == []
    counts = Counter(row["rule_id"] for row in report["existing_debt"])
    assert counts == {
        "NLE-BND-EDGE-HOST-IMPL": 3,
        "NLE-BND-AGENTRUNTIME-APP": 1,
    }
    assert len(report["existing_debt"]) == 4
    for row in report["existing_debt"]:
        assert row["path"].endswith(".py")
        assert row["symbol"]
        assert row["owner"]
        assert row["reason"]
        assert row["remove_task"]
        assert row["lines"]


def test_t_nle_01_stale_allowlist_is_a_contract_failure() -> None:
    scanner = _load_scanner()
    report = scanner.scan_report(
        FIXTURES / "stale_repo",
        policy_path=FIXTURES / "stale_policy.json",
    )

    assert report["existing_debt"] == []
    assert len(report["violations"]) == 1
    violation = report["violations"][0]
    assert violation["violation_type"] == scanner.STALE_ALLOWLIST
    assert violation["path"] == "edge_client/removed_dependency.py"
    assert "remove the entry" in violation["message"]
