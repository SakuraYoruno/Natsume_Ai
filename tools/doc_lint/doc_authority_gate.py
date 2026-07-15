"""Fail closed when historical architecture plans re-enter the current authority chain."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


CHECK_ID = "doc_authority_gate"
REASON_REQUIRED_AUTHORITY_MISSING = "DOC_REQUIRED_AUTHORITY_MISSING"
REASON_LEGACY_DOC_OUTSIDE_HISTORY = "DOC_LEGACY_OUTSIDE_HISTORY"
REASON_LEGACY_REFERENCE_ACTIVE = "DOC_LEGACY_REFERENCE_ACTIVE"
REASON_MACHINE_STATUS_INVALID = "DOC_MACHINE_STATUS_INVALID"
REASON_LEGACY_STAGE_ACTIVE = "DOC_LEGACY_STAGE_ACTIVE"

REQUIRED_AUTHORITY_PATHS = (
    "README.md",
    "docs/00_index/00_DOCMAP.md",
    "docs/20_prd/PRD.md",
    "docs/30_devspec/DevSpec.md",
    "docs/30_devspec/DevTasks.md",
    "docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md",
    "docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md",
    "docs/50_project/arch/TARGET_ARCHITECTURE.md",
    "docs/50_project/arch/ARCHITECTURE_GAP_MATRIX.md",
    "docs/60_trace/requirements_trace.md",
    "configs/release/current_architecture_status.json",
    "tests/TESTPLAN.md",
)

ACTIVE_STAGE_SCAN_PATHS = (
    "docs/20_prd/PRD.md",
    "docs/30_devspec/DevSpec.md",
    "docs/30_devspec/DevTasks.md",
    "docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md",
    "docs/50_project/arch/TARGET_ARCHITECTURE.md",
    "tests/TESTPLAN.md",
)
LEGACY_STAGE_HEADING = re.compile(
    r"^#{2,6}\s+(?:M4[8-9]|M5[0-7]|AW-\d+|GR-\d+)",
    re.MULTILINE,
)

ARCHIVE_ROOT = "docs/_history/programs/2026-07"
LEGACY_DOCUMENTS = (
    "ARCH_REVIEW_AND_ROADMAP.md",
    "NATSUME_AI_UNIVERSAL_GOVERNED_PLANNING_EXECUTION_20260714.md",
    "NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md",
    "NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md",
    "NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md",
    "NATSUME_AI_V1_LONG_TERM_EVOLUTION_REFACTOR_PLAN_20260710.md",
)

REFERENCE_SUFFIXES = frozenset({".md", ".py", ".json", ".yaml", ".yml", ".toml"})
SKIP_REFERENCE_PREFIXES = (
    "docs/_history/",
    "docs/30_devspec/evidence/",
    "docs/35_quality/audits/",
    "docs/45_testing/",
    "docs/50_release/",
)
SKIP_REFERENCE_PATHS = frozenset({"tools/doc_lint/doc_authority_gate.py"})


def scan_repository(root: str | Path) -> list[dict[str, Any]]:
    repo_root = Path(root).resolve()
    failures: list[dict[str, Any]] = []

    for relative in REQUIRED_AUTHORITY_PATHS:
        if not (repo_root / relative).is_file():
            failures.append(
                _failure(
                    REASON_REQUIRED_AUTHORITY_MISSING,
                    relative,
                    0,
                    "Required current-authority document is missing",
                )
            )

    docs_root = repo_root / "docs"
    for basename in LEGACY_DOCUMENTS:
        expected = f"{ARCHIVE_ROOT}/{basename}"
        expected_path = repo_root / expected
        if not expected_path.is_file():
            failures.append(
                _failure(
                    REASON_REQUIRED_AUTHORITY_MISSING,
                    expected,
                    0,
                    "Required historical snapshot is missing from the archive",
                )
            )
        if docs_root.is_dir():
            for candidate in docs_root.rglob(basename):
                relative = _relative(candidate, repo_root)
                if relative != expected:
                    failures.append(
                        _failure(
                            REASON_LEGACY_DOC_OUTSIDE_HISTORY,
                            relative,
                            0,
                            f"Legacy architecture document must live only at {expected}",
                        )
                    )

    for path in _reference_files(repo_root):
        relative = _relative(path, repo_root)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            normalized_line = line.replace("\\", "/")
            for basename in LEGACY_DOCUMENTS:
                if basename not in normalized_line:
                    continue
                expected = f"{ARCHIVE_ROOT}/{basename}"
                if expected not in normalized_line:
                    failures.append(
                        _failure(
                            REASON_LEGACY_REFERENCE_ACTIVE,
                            relative,
                            line_number,
                            f"Legacy document reference must point to {expected}",
                        )
                    )

    for relative in ACTIVE_STAGE_SCAN_PATHS:
        path = repo_root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig")
        for match in LEGACY_STAGE_HEADING.finditer(text):
            failures.append(
                _failure(
                    REASON_LEGACY_STAGE_ACTIVE,
                    relative,
                    text.count("\n", 0, match.start()) + 1,
                    "Legacy M48-M57/AW/GR heading cannot define a current stage",
                )
            )

    failures.extend(_validate_machine_status(repo_root))
    failures.sort(
        key=lambda row: (
            str(row["failed_artifact"]),
            int(row["line"]),
            str(row["reason_code"]),
        )
    )
    return failures


def build_report(root: str | Path) -> dict[str, Any]:
    failures = scan_repository(root)
    return {
        "check_id": CHECK_ID,
        "status": "failed" if failures else "passed",
        "reason_code": failures[0]["reason_code"] if failures else None,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current vs historical document authority.")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args(argv)
    report = build_report(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _validate_machine_status(repo_root: Path) -> list[dict[str, Any]]:
    relative = "configs/release/current_architecture_status.json"
    path = repo_root / relative
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [
            _failure(
                REASON_MACHINE_STATUS_INVALID,
                relative,
                0,
                f"Machine architecture status is not valid JSON: {exc}",
            )
        ]

    expected_execution = "docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md"
    issues: list[str] = []
    if payload.get("active_execution_contract") != expected_execution:
        issues.append(f"active_execution_contract must equal {expected_execution}")
    if payload.get("overall_status") != "INCOMPLETE":
        issues.append("overall_status must remain INCOMPLETE until final acceptance")
    if not payload.get("historical_receipts_are_scope_bound"):
        issues.append("historical receipts must remain explicitly scope-bound")
    active_wave = str(payload.get("active_wave") or "")
    active_wave_status = str(payload.get("active_wave_status") or "")
    if active_wave != "NE-00":
        issues.append("active_wave must be NE-00 while this document-reset change is active")
    if active_wave_status not in {"IN_PROGRESS", "COMPLETED"}:
        issues.append("active_wave_status must be IN_PROGRESS or COMPLETED")
    execution_path = repo_root / expected_execution
    if execution_path.is_file() and active_wave and active_wave_status:
        execution_text = execution_path.read_text(encoding="utf-8-sig")
        expected_token = f"{active_wave}_{active_wave_status}"
        if expected_token not in execution_text:
            issues.append(
                f"execution contract status must contain {expected_token}"
            )
    return [
        _failure(REASON_MACHINE_STATUS_INVALID, relative, 0, message)
        for message in issues
    ]


def _reference_files(repo_root: Path) -> Iterable[Path]:
    candidates = [repo_root / "README.md"]
    for top in ("docs", "configs", "tools", "tests"):
        base = repo_root / top
        if not base.is_dir():
            continue
        candidates.extend(path for path in base.rglob("*") if path.is_file())
    for path in candidates:
        if path.suffix.lower() not in REFERENCE_SUFFIXES:
            continue
        relative = _relative(path, repo_root)
        if relative in SKIP_REFERENCE_PATHS:
            continue
        if any(relative.startswith(prefix) for prefix in SKIP_REFERENCE_PREFIXES):
            continue
        yield path


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _failure(reason_code: str, artifact: str, line: int, message: str) -> dict[str, Any]:
    return {
        "check_id": CHECK_ID,
        "reason_code": reason_code,
        "failed_artifact": artifact,
        "line": int(line),
        "message": message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
