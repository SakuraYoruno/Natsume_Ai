"""Unified delivery gate runner for contract/e2e/static/doc checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


JOB_ID = "delivery-gate"

CHECK_CONTRACT = "contract"
CHECK_E2E = "e2e"
CHECK_STATIC_SCAN = "static_scan"
CHECK_DOC_GATE = "doc_gate"
DEFAULT_CHECKS = (CHECK_CONTRACT, CHECK_E2E, CHECK_STATIC_SCAN, CHECK_DOC_GATE)

REASON_CI_CONTRACT_FAILED = "CI_CONTRACT_FAILED"
REASON_CI_E2E_FAILED = "CI_E2E_FAILED"
REASON_CI_INVALID_CHECK = "CI_INVALID_CHECK"


def run_delivery_gate(
    *,
    root: str | Path,
    checks: list[str] | tuple[str, ...] | None = None,
    job_id: str = JOB_ID,
    static_scan_root: str | Path | None = None,
    include_tests_in_static_scan: bool = False,
    doc_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    selected_checks = list(checks or DEFAULT_CHECKS)

    check_reports: list[dict[str, Any]] = []
    for check_id in selected_checks:
        if check_id == CHECK_CONTRACT:
            check_reports.append(
                _run_unittest_check(
                    check_id=CHECK_CONTRACT,
                    reason_code=REASON_CI_CONTRACT_FAILED,
                    target="tests/contract",
                    command=[
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests/contract",
                        "-p",
                        "test_*.py",
                    ],
                    cwd=repo_root,
                )
            )
            continue

        if check_id == CHECK_E2E:
            check_reports.append(
                _run_unittest_check(
                    check_id=CHECK_E2E,
                    reason_code=REASON_CI_E2E_FAILED,
                    target="tests/e2e",
                    command=[
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests/e2e",
                        "-p",
                        "test_*.py",
                    ],
                    cwd=repo_root,
                )
            )
            continue

        if check_id == CHECK_STATIC_SCAN:
            runner = _load_static_scan_runner()
            scan_root = Path(static_scan_root).resolve() if static_scan_root else repo_root
            raw_report = runner.run_static_scans(
                scan_root,
                include_tests=include_tests_in_static_scan,
            )
            check_reports.append(_attach_job_id(raw_report, job_id=job_id))
            continue

        if check_id == CHECK_DOC_GATE:
            doc_lint = _load_doc_gate_module()
            raw_report = doc_lint.build_report(
                root=repo_root,
                paths=doc_paths,
            )
            check_reports.append(_attach_job_id(raw_report, job_id=job_id))
            continue

        check_reports.append(
            {
                "check_id": check_id,
                "status": "failed",
                "reason_code": REASON_CI_INVALID_CHECK,
                "failures": [
                    {
                        "job_id": job_id,
                        "check_id": check_id,
                        "reason_code": REASON_CI_INVALID_CHECK,
                        "failed_artifact": check_id,
                        "message": "Unknown check id in delivery gate configuration",
                    }
                ],
            }
        )

    failures = _flatten_failures(check_reports=check_reports, job_id=job_id)
    failed_checks = sum(1 for check in check_reports if check.get("status") == "failed")
    status = "failed" if failures else "passed"
    return {
        "job_id": job_id,
        "status": status,
        "checks": check_reports,
        "failures": failures,
        "summary": {
            "total_checks": len(check_reports),
            "failed_checks": failed_checks,
            "failure_count": len(failures),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run unified delivery gate checks.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument(
        "--checks",
        default=",".join(DEFAULT_CHECKS),
        help="Comma-separated checks: contract,e2e,static_scan,doc_gate",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--include-tests-in-static-scan",
        action="store_true",
        help="Include tests/ paths when static scan runs.",
    )
    parser.add_argument(
        "--static-scan-root",
        default=None,
        help="Optional path override for static scan root.",
    )
    parser.add_argument(
        "--doc-path",
        action="append",
        dest="doc_paths",
        default=None,
        help="Optional markdown path for doc gate (repeatable).",
    )
    args = parser.parse_args(argv)

    selected_checks = [part.strip() for part in args.checks.split(",") if part.strip()]
    report = run_delivery_gate(
        root=args.root,
        checks=selected_checks,
        static_scan_root=args.static_scan_root,
        include_tests_in_static_scan=args.include_tests_in_static_scan,
        doc_paths=args.doc_paths,
    )
    if args.report_file:
        Path(args.report_file).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _run_unittest_check(
    *,
    check_id: str,
    reason_code: str,
    target: str,
    command: list[str],
    cwd: Path,
) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0:
        return {
            "check_id": check_id,
            "status": "passed",
            "reason_code": None,
            "failures": [],
            "metrics": {"exit_code": 0},
        }

    return {
        "check_id": check_id,
        "status": "failed",
        "reason_code": reason_code,
        "failures": [
            {
                "check_id": check_id,
                "reason_code": reason_code,
                "failed_artifact": target,
                "message": f"{check_id} unittest discovery failed",
                "exit_code": int(proc.returncode),
            }
        ],
        "metrics": {"exit_code": int(proc.returncode)},
    }


def _load_doc_gate_module():
    script = Path(__file__).resolve().parent / "doc_audience_lint.py"
    spec = importlib.util.spec_from_file_location("doc_audience_lint", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load doc lint script: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_static_scan_runner():
    script = Path(__file__).resolve().parents[1] / "static_scan" / "run_all.py"
    spec = importlib.util.spec_from_file_location("static_scan_run_all", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load static scan runner: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _attach_job_id(report: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    report_copy = dict(report)
    failures = [dict(row) for row in report.get("failures", [])]
    for row in failures:
        row["job_id"] = job_id
    report_copy["failures"] = failures
    return report_copy


def _flatten_failures(
    *,
    check_reports: list[dict[str, Any]],
    job_id: str,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for check in check_reports:
        check_id = str(check.get("check_id") or "")
        for row in check.get("failures", []):
            normalized = dict(row)
            normalized["job_id"] = str(normalized.get("job_id") or job_id)
            normalized["check_id"] = str(normalized.get("check_id") or check_id)
            normalized["reason_code"] = str(normalized.get("reason_code") or "")
            normalized["failed_artifact"] = str(normalized.get("failed_artifact") or "")
            failures.append(normalized)

    failures.sort(
        key=lambda row: (
            str(row.get("check_id") or ""),
            str(row.get("failed_artifact") or ""),
            int(row.get("line") or 0),
            str(row.get("reason_code") or ""),
        )
    )
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
