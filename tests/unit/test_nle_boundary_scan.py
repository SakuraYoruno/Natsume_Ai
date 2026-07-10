from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = ROOT / "tools" / "static_scan" / "nle_boundary_scan.py"
RUN_ALL_SCRIPT = ROOT / "tools" / "static_scan" / "run_all.py"
FIXTURES = ROOT / "tests" / "fixtures" / "static_scan" / "nle_boundary"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nle_boundary_scan_rejects_each_unlisted_high_risk_edge() -> None:
    scanner = _load_module("nle_boundary_scan_unit", SCAN_SCRIPT)
    report = scanner.scan_report(
        FIXTURES / "unlisted_repo",
        policy_path=FIXTURES / "unlisted_policy.json",
    )

    assert report["existing_debt"] == []
    assert len(report["violations"]) == 5
    assert {row["rule_id"] for row in report["violations"]} == {
        "NLE-BND-EDGE-HOST-IMPL",
        "NLE-BND-AGENTRUNTIME-APP",
        "NLE-BND-PENTEST-WORLDCANON-IMPORT",
        "NLE-BND-PENTEST-WORLDCANON-UPSERT",
        "NLE-BND-NO-PARALLEL-LIFE-ROOT",
    }
    assert {row["violation_type"] for row in report["violations"]} == {
        scanner.UNLISTED_DEPENDENCY
    }


def test_nle_boundary_policy_rejects_glob_allowlist_entries() -> None:
    scanner = _load_module("nle_boundary_scan_glob", SCAN_SCRIPT)
    policy = json.loads((FIXTURES / "stale_policy.json").read_text(encoding="utf-8"))
    policy["allowlist"][0]["path"] = "edge_client/**/*.py"

    with pytest.raises(ValueError, match="must not contain glob"):
        scanner.validate_policy(policy)


def test_run_all_includes_nle_boundary_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    run_all = _load_module("run_all_nle_integration", RUN_ALL_SCRIPT)

    class EmptyScanner:
        @staticmethod
        def scan_repo(*_args, **_kwargs):
            return []

    class FailingNleScanner:
        @staticmethod
        def scan_repo(*_args, **_kwargs):
            return [
                {
                    "rule_id": "NLE-BND-FIXTURE",
                    "path": "edge_client/new_debt.py",
                    "line": 7,
                    "message": "unlisted dependency",
                }
            ]

    monkeypatch.setattr(run_all, "_load_bond_scan_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_no_rule_degrade_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_nle_boundary_scan_module", lambda: FailingNleScanner())

    report = run_all.run_static_scans(ROOT)

    assert report["status"] == "failed"
    assert report["failures"] == [
        {
            "check_id": "static_scan",
            "reason_code": "STATIC_SCAN_VIOLATION",
            "failed_artifact": "edge_client/new_debt.py",
            "line": 7,
            "message": "unlisted dependency",
            "rule_id": "NLE-BND-FIXTURE",
        }
    ]
