"""Validate local links in current Markdown documentation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


CHECK_ID = "doc_link_gate"
REASON_MISSING = "DOC_LOCAL_LINK_MISSING"
REASON_OUTSIDE = "DOC_LOCAL_LINK_OUTSIDE_REPOSITORY"

_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*(?P<target><[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)"
)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "artifacts",
        "runtime",
        "runtime_logs",
        "models",
        "__pycache__",
    }
)
_SKIP_PREFIXES = ("docs/_history/", "tests/fixtures/")


def scan_repository(
    root: str | Path,
    *,
    paths: Iterable[str | Path] | None = None,
) -> list[dict[str, Any]]:
    repo_root = Path(root).resolve()
    failures: list[dict[str, Any]] = []
    for markdown in _markdown_files(repo_root, paths=paths):
        relative = markdown.resolve().relative_to(repo_root).as_posix()
        try:
            text = markdown.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _LINK_RE.finditer(text):
            raw_target = match.group("target").strip().strip("<>")
            if _is_nonlocal(raw_target):
                continue
            path_part = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
            if not path_part:
                continue
            candidate = (markdown.parent / path_part).resolve()
            line = text.count("\n", 0, match.start()) + 1
            try:
                candidate.relative_to(repo_root)
            except ValueError:
                failures.append(
                    _failure(REASON_OUTSIDE, relative, line, raw_target)
                )
                continue
            if not candidate.exists():
                failures.append(_failure(REASON_MISSING, relative, line, raw_target))
    failures.sort(
        key=lambda row: (
            str(row["failed_artifact"]),
            int(row["line"]),
            str(row["target"]),
        )
    )
    return failures


def build_report(
    root: str | Path,
    *,
    paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    failures = scan_repository(root, paths=paths)
    return {
        "check_id": CHECK_ID,
        "status": "failed" if failures else "passed",
        "reason_code": failures[0]["reason_code"] if failures else None,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current Markdown local links.")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--path", action="append", dest="paths", default=None)
    args = parser.parse_args(argv)
    report = build_report(args.root, paths=args.paths)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _markdown_files(
    repo_root: Path,
    *,
    paths: Iterable[str | Path] | None,
) -> list[Path]:
    if paths is not None:
        candidates = [(repo_root / path).resolve() for path in paths]
    else:
        candidates = list(repo_root.rglob("*.md"))
    result: list[Path] = []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        try:
            relative = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in Path(relative).parts):
            continue
        if any(relative.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue
        result.append(path)
    return sorted(set(result))


def _is_nonlocal(target: str) -> bool:
    return (
        not target
        or target.startswith("#")
        or target.startswith("//")
        or _SCHEME_RE.match(target) is not None
    )


def _failure(
    reason_code: str,
    artifact: str,
    line: int,
    target: str,
) -> dict[str, Any]:
    return {
        "check_id": CHECK_ID,
        "reason_code": reason_code,
        "failed_artifact": artifact,
        "line": int(line),
        "target": target,
        "message": f"Local Markdown link is invalid: {target}",
    }


if __name__ == "__main__":
    raise SystemExit(main())
