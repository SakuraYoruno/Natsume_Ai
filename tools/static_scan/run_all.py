"""Aggregate static scans for delivery gate checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


CHECK_ID = "static_scan"
REASON_STATIC_SCAN_VIOLATION = "STATIC_SCAN_VIOLATION"


def run_static_scans(
    root: str | Path,
    *,
    include_tests: bool = False,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    scanners = (
        lambda: _load_bond_scan_module().scan_repo(repo_root, include_tests=include_tests),
        lambda: _load_no_rule_degrade_module().scan_repo(repo_root),
        lambda: _load_nle_boundary_scan_module().scan_repo(repo_root),
        lambda: _load_edge_contracts_guard_module().scan_repo(repo_root),
    )
    raw_violations: list[dict[str, Any]] = []
    for loader in scanners:
        raw_violations.extend(loader())
    failures: list[dict[str, Any]] = []

    for row in raw_violations:
        failures.append(
            {
                "check_id": CHECK_ID,
                "reason_code": REASON_STATIC_SCAN_VIOLATION,
                "failed_artifact": str(row.get("path") or ""),
                "line": int(row.get("line") or 0),
                "message": str(row.get("message") or ""),
                "rule_id": str(row.get("rule_id") or ""),
            }
        )

    failures.sort(
        key=lambda record: (
            str(record.get("failed_artifact") or ""),
            int(record.get("line") or 0),
            str(record.get("rule_id") or ""),
        )
    )
    status = "failed" if failures else "passed"
    return {
        "check_id": CHECK_ID,
        "status": status,
        "reason_code": REASON_STATIC_SCAN_VIOLATION if failures else None,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static scan checks.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include tests/ paths in static scan.",
    )
    args = parser.parse_args(argv)

    report = run_static_scans(root=args.root, include_tests=args.include_tests)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _load_bond_scan_module():
    script_path = Path(__file__).resolve().parent / "bond_ssot_scan.py"
    spec = importlib.util.spec_from_file_location("bond_ssot_scan", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load static scan script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_no_rule_degrade_module():
    script_path = Path(__file__).resolve().parents[1] / ".." / "scripts" / "ci" / "check_no_rule_degrade.py"
    script_path = script_path.resolve()
    spec = importlib.util.spec_from_file_location("check_no_rule_degrade", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load static scan script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_nle_boundary_scan_module():
    script_path = Path(__file__).resolve().parent / "nle_boundary_scan.py"
    spec = importlib.util.spec_from_file_location("nle_boundary_scan", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load static scan script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_edge_contracts_guard_module():
    script_path = Path(__file__).resolve().parents[1] / "architecture" / "edge_contracts_guard.py"
    spec = importlib.util.spec_from_file_location("edge_contracts_guard", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load architecture guard: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


if __name__ == "__main__":
    raise SystemExit(main())
