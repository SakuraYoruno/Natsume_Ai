"""Static T-PERSONA-FENCE-01 guard for persona trait mutation authority."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


RULE_ID = "T-PERSONA-FENCE-01"
RULE_NAME = "PERSONA_GROWTH_SINGLE_AUTHORITY"
ENGINE_PATH = "host_brain/persona/persona_engine.py"
MIGRATION_PREFIX = "host_brain/persona/migrations/"


def scan_repo(
    root: str | Path,
    *,
    include_tests: bool = False,
) -> list[dict[str, Any]]:
    del include_tests  # This production authority gate intentionally excludes tests.
    repo_root = Path(root).resolve()
    host_root = repo_root / "host_brain"
    if not host_root.is_dir():
        return []
    violations: list[dict[str, Any]] = []
    for path in sorted(host_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        violations.extend(scan_file(path, root=repo_root))
    return violations


def scan_file(
    file_path: str | Path,
    *,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = Path(file_path)
    if path.suffix.lower() != ".py" or not path.is_file():
        return []
    repo_root = Path(root).resolve() if root is not None else None
    relative = _relative(path, repo_root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []

    violations: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called == "apply_growth_delta":
                violations.append(
                    _violation(
                        path=relative,
                        node=node,
                        message=(
                            "Legacy persona growth write detected; runtime code must "
                            "produce a reviewed proposal instead of calling apply_growth_delta"
                        ),
                    )
                )
            elif called == "rollback_last_change":
                violations.append(
                    _violation(
                        path=relative,
                        node=node,
                        message=(
                            "Legacy persona rollback write detected; runtime "
                            "rollback must use the version activation authority"
                        ),
                    )
                )
            elif called in {
                "apply_migration_growth_delta",
                "rollback_last_migration_change",
                "PersonaGrowthMigrationAuthority",
            } and not relative.startswith(MIGRATION_PREFIX):
                violations.append(
                    _violation(
                        path=relative,
                        node=node,
                        message=(
                            f"{called} is restricted to the dedicated persona migration namespace"
                        ),
                    )
                )
            elif (
                called == "_apply_migration_delta_unchecked"
                and relative != ENGINE_PATH
            ):
                violations.append(
                    _violation(
                        path=relative,
                        node=node,
                        message="Private persona mutation primitive escaped PersonaEngine",
                    )
                )

        if relative == ENGINE_PATH:
            continue
        for target in _assignment_targets(node):
            if _mutates_user_traits(target):
                violations.append(
                    _violation(
                        path=relative,
                        node=target,
                        message=(
                            "Direct PersonaEngine._user_traits mutation detected outside "
                            "the central authority boundary"
                        ),
                    )
                )
    return violations


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [node.target]
    return []


def _mutates_user_traits(target: ast.AST) -> bool:
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_mutates_user_traits(item) for item in target.elts)
    if isinstance(target, ast.Attribute):
        return target.attr == "_user_traits"
    if isinstance(target, ast.Subscript):
        value = target.value
        return isinstance(value, ast.Attribute) and value.attr == "_user_traits"
    return False


def _called_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return str(func.attr)
    if isinstance(func, ast.Name):
        return str(func.id)
    return ""


def _relative(path: Path, root: Path | None) -> str:
    resolved = path.resolve()
    if root is not None:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            pass
    normalized = resolved.as_posix()
    marker = "/host_brain/"
    if marker in normalized:
        return f"host_brain/{normalized.split(marker, 1)[1]}"
    return path.as_posix()


def _violation(*, path: str, node: ast.AST, message: str) -> dict[str, Any]:
    return {
        "rule_id": RULE_ID,
        "rule_name": RULE_NAME,
        "path": path,
        "line": int(getattr(node, "lineno", 0) or 0),
        "column": int(getattr(node, "col_offset", 0) or 0),
        "message": message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    violations = scan_repo(args.root)
    print(
        json.dumps(
            {"rule_id": RULE_ID, "violations": violations},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
