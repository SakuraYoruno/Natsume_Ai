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
STAGE_WRITE_METHOD = "apply_event"
DIRECT_STATE_ATTRIBUTES = frozenset({"total_score", "current_stage", "rift"})
DEFAULT_ALLOWED_WRITER = "host_brain/bond/bond_dynamics.py"
DEFAULT_ALLOWED_STAGE_WRITER = "host_brain/bond/bond_dynamics.py"
DEFAULT_ALLOWED_STATE_MUTATORS = frozenset(
    {
        "host_brain/bond/bond_dynamics.py",
        "host_brain/bond/bond_stage_engine.py",
    }
)
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
    allowed_stage_writer: str = DEFAULT_ALLOWED_STAGE_WRITER,
    allowed_state_mutators: frozenset[str] = DEFAULT_ALLOWED_STATE_MUTATORS,
) -> list[dict[str, Any]]:
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        return []
    normalized = _normalize(path)
    allowed = _normalize(allowed_writer)
    stage_allowed = _normalize(allowed_stage_writer)
    state_allowed = {_normalize(item) for item in allowed_state_mutators}

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
        called = _called_name(node.func)
        if called == WRITE_METHOD and not normalized.endswith(allowed):
            violations.append(
                _violation(
                    path=normalized,
                    node=node,
                    message=(
                        "Non-single-writer Neo4j write detected: "
                        f"{WRITE_METHOD} can only be called in {allowed}"
                    ),
                )
            )
        if called == STAGE_WRITE_METHOD and not normalized.endswith(stage_allowed):
            violations.append(
                _violation(
                    path=normalized,
                    node=node,
                    message=(
                        "Non-governed Bond stage mutation detected: "
                        f"{STAGE_WRITE_METHOD} can only be called in {stage_allowed}"
                    ),
                )
            )

    if not any(normalized.endswith(item) for item in state_allowed):
        for node in ast.walk(tree):
            targets: list[ast.AST] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                raw_targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in raw_targets:
                    targets.extend(_flatten_targets(target))
            elif isinstance(node, ast.AugAssign):
                targets.extend(_flatten_targets(node.target))
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in DIRECT_STATE_ATTRIBUTES:
                    violations.append(
                        _violation(
                            path=normalized,
                            node=target,
                            message=(
                                "Direct Bond state mutation detected: "
                                f".{target.attr} can only change inside BondDynamics/BondStageEngine"
                            ),
                        )
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


def _flatten_targets(target: ast.AST) -> list[ast.AST]:
    if isinstance(target, (ast.Tuple, ast.List)):
        values: list[ast.AST] = []
        for item in target.elts:
            values.extend(_flatten_targets(item))
        return values
    return [target]


def _violation(*, path: str, node: ast.AST, message: str) -> dict[str, Any]:
    return {
        "rule_id": RULE_ID,
        "rule_name": RULE_NAME,
        "path": path,
        "line": int(getattr(node, "lineno", 0) or 0),
        "column": int(getattr(node, "col_offset", 0) or 0),
        "message": message,
    }


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
