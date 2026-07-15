"""Markdown audience/title lint gate used by delivery CI checks."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


CHECK_ID = "doc_gate"

REASON_DOC_ENCODING_INVALID = "DOC_ENCODING_INVALID"
REASON_DOC_AUDIENCE_HEADER_MISSING = "DOC_AUDIENCE_HEADER_MISSING"
REASON_DOC_AUDIENCE_INVALID = "DOC_AUDIENCE_INVALID"
REASON_DOC_TITLE_HEADER_MISSING = "DOC_TITLE_HEADER_MISSING"
REASON_DOC_TITLE_INVALID = "DOC_TITLE_INVALID"

AUDIENCE_KEY = "\u53d7\u4f17"
TITLE_KEY = "\u6807\u9898"
FULLWIDTH_COLON = "\uff1a"
BOM = "\ufeff"

AUDIENCE_PATTERN = re.compile(
    rf"^{AUDIENCE_KEY}[{FULLWIDTH_COLON}:](MASTER|CODEX|PROJECT)\s*$"
)
TITLE_PATTERN = re.compile(rf"^{TITLE_KEY}[{FULLWIDTH_COLON}:].+\S$")

SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
}
SKIP_SUBSTRINGS = (
    "/tests/fixtures/",
    "/artifacts/",
    "/runtime/",
    "/runtime_logs/",
    "/models/",
    "/docs/45_testing/",
    "/tools/unity_avatar_runtime/Library/",
)


def scan_markdown_files(
    root: str | Path,
    *,
    paths: list[str | Path] | None = None,
) -> list[dict[str, Any]]:
    repo_root = Path(root).resolve()
    md_paths = _collect_markdown_paths(repo_root=repo_root, explicit_paths=paths)
    failures: list[dict[str, Any]] = []

    for path in md_paths:
        rel_path = _to_rel(path, repo_root)
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            failures.append(
                _failure(
                    reason_code=REASON_DOC_ENCODING_INVALID,
                    failed_artifact=rel_path,
                    message="Markdown must be UTF-8 decodable",
                    line=1,
                )
            )
            continue

        headers = _first_non_empty_lines(raw, limit=2)
        if not headers:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_AUDIENCE_HEADER_MISSING,
                    failed_artifact=rel_path,
                    message="First non-empty line must declare audience header",
                    line=1,
                )
            )
            continue

        audience_line = _strip_bom(headers[0])
        if not audience_line:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_AUDIENCE_HEADER_MISSING,
                    failed_artifact=rel_path,
                    message="First non-empty line must declare audience header",
                    line=1,
                )
            )
            continue
        if not AUDIENCE_PATTERN.match(audience_line):
            failures.append(
                _failure(
                    reason_code=REASON_DOC_AUDIENCE_INVALID,
                    failed_artifact=rel_path,
                    message=(
                        f"Audience header must match "
                        f"'{AUDIENCE_KEY}{FULLWIDTH_COLON}MASTER|CODEX|PROJECT'"
                    ),
                    line=1,
                )
            )
            continue

        if len(headers) < 2:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_TITLE_HEADER_MISSING,
                    failed_artifact=rel_path,
                    message="Second non-empty line must declare title header",
                    line=2,
                )
            )
            continue

        title_line = headers[1]
        if not TITLE_PATTERN.match(title_line):
            failures.append(
                _failure(
                    reason_code=REASON_DOC_TITLE_INVALID,
                    failed_artifact=rel_path,
                    message=f"Title header must start with '{TITLE_KEY}{FULLWIDTH_COLON}'",
                    line=2,
                )
            )

    failures.sort(
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            int(row.get("line") or 0),
            str(row.get("reason_code") or ""),
        )
    )
    return failures


def build_report(
    root: str | Path,
    *,
    paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    failures = scan_markdown_files(root=root, paths=paths)
    status = "failed" if failures else "passed"
    return {
        "check_id": CHECK_ID,
        "status": status,
        "reason_code": failures[0]["reason_code"] if failures else None,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint markdown audience/title headers.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=None,
        help="Optional markdown path to lint (repeatable).",
    )
    args = parser.parse_args(argv)

    report = build_report(root=args.root, paths=args.paths)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _collect_markdown_paths(
    *,
    repo_root: Path,
    explicit_paths: list[str | Path] | None,
) -> list[Path]:
    if explicit_paths:
        paths = [Path(path).resolve() for path in explicit_paths]
        return [
            path
            for path in paths
            if path.suffix.lower() == ".md"
            and path.exists()
            and not _should_skip_markdown(path)
        ]

    collected: list[Path] = []
    for path in repo_root.rglob("*.md"):
        if _should_skip_markdown(path):
            continue
        collected.append(path.resolve())
    collected.sort(key=lambda p: _normalize(_to_rel(p, repo_root)))
    return collected


def _should_skip_markdown(path: Path) -> bool:
    normalized = _normalize(path)
    return any(part in SKIP_DIR_NAMES for part in path.parts) or any(
        token in normalized for token in SKIP_SUBSTRINGS
    )


def _first_non_empty_lines(raw: str, *, limit: int) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
        if len(lines) >= limit:
            break
    return lines


def _strip_bom(line: str) -> str:
    return line.lstrip(BOM).strip()


def _normalize(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _to_rel(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return _normalize(path.resolve())
    return _normalize(rel)


def _failure(
    *,
    reason_code: str,
    failed_artifact: str,
    message: str,
    line: int,
) -> dict[str, Any]:
    return {
        "check_id": CHECK_ID,
        "reason_code": reason_code,
        "failed_artifact": failed_artifact,
        "line": int(line),
        "message": message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
