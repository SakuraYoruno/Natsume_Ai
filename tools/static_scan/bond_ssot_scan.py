"""Static scanner for Bond Neo4j single-writer contract (T-SSOT-01)."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any


RULE_ID = "T-SSOT-01"
RULE_NAME = "BOND_SINGLE_WRITER"
WRITE_METHOD = "write_bond_event"
DEFAULT_ALLOWED_WRITER = "host_brain/bond/bond_dynamics.py"
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "artifacts",
    "node_modules",
    "runtime_logs",
    "tmp",
    "venv",
}


def scan_file(
    file_path: str | Path,
    *,
    allowed_writer: str = DEFAULT_ALLOWED_WRITER,
) -> list[dict[str, Any]]:
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        return []
    normalized = _normalize(path)
    allowed = _normalize(allowed_writer)
    if normalized.endswith(allowed):
        return []

    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _called_name(node.func) != WRITE_METHOD:
            continue
        violations.append(
            {
                "rule_id": RULE_ID,
                "rule_name": RULE_NAME,
                "path": normalized,
                "line": int(getattr(node, "lineno", 0) or 0),
                "column": int(getattr(node, "col_offset", 0) or 0),
                "message": (
                    "Non-single-writer Neo4j write detected: "
                    f"{WRITE_METHOD} can only be called in {allowed}"
                ),
            }
        )
    return violations


def scan_repo(
    root: str | Path,
    *,
    include_tests: bool = False,
    allowed_writer: str = DEFAULT_ALLOWED_WRITER,
) -> list[dict[str, Any]]:
    repo_root = Path(root)
    if not repo_root.exists():
        return []

    violations: list[dict[str, Any]] = []
    for path in _iter_python_files(repo_root, include_tests=include_tests):
        normalized = _normalize(path)
        if _skip_path(normalized, include_tests=include_tests):
            continue
        violations.extend(scan_file(path, allowed_writer=allowed_writer))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    root = Path(args[0]).resolve() if args else Path.cwd()
    violations = scan_repo(root)
    print(json.dumps({"rule_id": RULE_ID, "violations": violations}, ensure_ascii=False, indent=2))
    return 1 if violations else 0


def _called_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return str(func.attr)
    if isinstance(func, ast.Name):
        return str(func.id)
    return ""


def _normalize(path: str | Path) -> str:
    return str(Path(path)).replace("\\", "/")


def _iter_python_files(repo_root: Path, *, include_tests: bool) -> list[Path]:
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root, onerror=lambda _exc: None):
        current = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not _skip_dir(current / dirname, include_tests=include_tests)
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                paths.append(current / filename)
    return paths


def _skip_dir(path: Path, *, include_tests: bool) -> bool:
    name = path.name
    if name in SKIP_DIR_NAMES:
        return True
    normalized = _normalize(path)
    if not include_tests and normalized.endswith("/tests"):
        return True
    if normalized.endswith("/tools/static_scan"):
        return True
    return False


def _skip_path(path: str, *, include_tests: bool) -> bool:
    if "/.venv/" in path or "/venv/" in path or "/__pycache__/" in path:
        return True
    if "/artifacts/" in path or "/runtime_logs/" in path or "/node_modules/" in path:
        return True
    if not include_tests and "/tests/" in path:
        return True
    if "/tools/static_scan/" in path:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
