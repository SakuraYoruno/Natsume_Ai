"""No-new-debt dependency boundary scan for TASK-0461 / T-NLE-01.

The policy records exact existing dependency edges.  A new edge fails the scan,
and an allowlist entry that no longer matches source also fails so that the
baseline can only shrink deliberately.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable


POLICY_FILENAME = "nle_boundary_policy.json"
POLICY_SCHEMA_VERSION = 1
UNLISTED_DEPENDENCY = "NLE_BOUNDARY_UNLISTED_DEPENDENCY"
STALE_ALLOWLIST = "NLE_BOUNDARY_STALE_ALLOWLIST"
SOURCE_ROOT_MISSING = "NLE_BOUNDARY_SOURCE_ROOT_MISSING"
SCAN_ERROR = "NLE_BOUNDARY_SCAN_ERROR"
_ALLOWLIST_FIELDS = {
    "rule_id",
    "path",
    "symbol",
    "owner",
    "reason",
    "remove_task",
}
_GLOB_CHARACTERS = frozenset("*?[]{}")


def load_policy(policy_path: str | Path) -> dict[str, Any]:
    path = Path(policy_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_policy(payload)
    return payload


def validate_policy(policy: dict[str, Any]) -> None:
    if int(policy.get("schema_version") or 0) != POLICY_SCHEMA_VERSION:
        raise ValueError(
            f"NLE boundary policy schema_version must be {POLICY_SCHEMA_VERSION}"
        )

    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("NLE boundary policy requires non-empty rules")

    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("NLE boundary policy rule must be an object")
        rule_id = _required_text(rule, "rule_id")
        if rule_id in rule_ids:
            raise ValueError(f"duplicate NLE boundary rule_id: {rule_id}")
        rule_ids.add(rule_id)
        kind = _required_text(rule, "kind")
        if kind in {"import", "module_import", "call_attribute"}:
            roots = rule.get("source_roots")
            if not isinstance(roots, list) or not roots:
                raise ValueError(f"{rule_id} requires source_roots")
            for root in roots:
                _validate_exact_relative_path(str(root), field=f"{rule_id}.source_roots")
        if kind in {"import", "module_import"}:
            prefixes = rule.get("forbidden_module_prefixes")
            if not isinstance(prefixes, list) or not prefixes:
                raise ValueError(f"{rule_id} requires forbidden_module_prefixes")
            for prefix in prefixes:
                _validate_exact_symbol(str(prefix), field=f"{rule_id}.forbidden_module_prefixes")
        elif kind == "call_attribute":
            attributes = rule.get("attributes")
            if not isinstance(attributes, list) or not attributes:
                raise ValueError(f"{rule_id} requires attributes")
            for attribute in attributes:
                _validate_exact_symbol(str(attribute), field=f"{rule_id}.attributes")
        elif kind == "forbidden_path":
            paths = rule.get("paths")
            if not isinstance(paths, list) or not paths:
                raise ValueError(f"{rule_id} requires paths")
            for forbidden_path in paths:
                _validate_exact_relative_path(
                    str(forbidden_path), field=f"{rule_id}.paths"
                )
        else:
            raise ValueError(f"unsupported NLE boundary rule kind: {kind}")

    allowlist = policy.get("allowlist")
    if not isinstance(allowlist, list):
        raise ValueError("NLE boundary policy allowlist must be a list")
    seen_entries: set[tuple[str, str, str]] = set()
    for entry in allowlist:
        if not isinstance(entry, dict):
            raise ValueError("NLE boundary allowlist entry must be an object")
        missing = _ALLOWLIST_FIELDS - set(entry)
        extra = set(entry) - _ALLOWLIST_FIELDS
        if missing or extra:
            raise ValueError(
                "NLE boundary allowlist fields must be exactly "
                f"{sorted(_ALLOWLIST_FIELDS)}; missing={sorted(missing)} extra={sorted(extra)}"
            )
        rule_id = _required_text(entry, "rule_id")
        if rule_id not in rule_ids:
            raise ValueError(f"allowlist references unknown rule_id: {rule_id}")
        path = _required_text(entry, "path")
        symbol = _required_text(entry, "symbol")
        _validate_exact_relative_path(path, field="allowlist.path")
        _validate_exact_symbol(symbol, field="allowlist.symbol")
        _required_text(entry, "owner")
        _required_text(entry, "reason")
        _required_text(entry, "remove_task")
        key = (rule_id, path, symbol)
        if key in seen_entries:
            raise ValueError(f"duplicate NLE boundary allowlist entry: {key}")
        seen_entries.add(key)


def scan_report(
    root: str | Path,
    *,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    resolved_policy = (
        Path(policy_path).resolve()
        if policy_path is not None
        else Path(__file__).resolve().with_name(POLICY_FILENAME)
    )
    policy = load_policy(resolved_policy)

    findings: dict[tuple[str, str, str], dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    for rule in policy["rules"]:
        kind = str(rule["kind"])
        if kind in {"import", "module_import", "call_attribute"}:
            _scan_source_rule(
                repo_root=repo_root,
                rule=rule,
                findings=findings,
                violations=violations,
            )
        elif kind == "forbidden_path":
            _scan_forbidden_paths(repo_root=repo_root, rule=rule, findings=findings)

    allowlist_by_key = {
        (str(row["rule_id"]), str(row["path"]), str(row["symbol"])): row
        for row in policy["allowlist"]
    }
    existing_debt: list[dict[str, Any]] = []
    for key, finding in findings.items():
        allowed = allowlist_by_key.get(key)
        if allowed is None:
            violations.append(
                _violation(
                    rule_id=str(finding["rule_id"]),
                    violation_type=UNLISTED_DEPENDENCY,
                    path=str(finding["path"]),
                    symbol=str(finding["symbol"]),
                    line=int(finding["lines"][0]) if finding["lines"] else 0,
                    message=(
                        "Unlisted NLE boundary dependency: "
                        f"{finding['path']} -> {finding['symbol']}"
                    ),
                )
            )
            continue
        existing_debt.append(
            {
                "rule_id": str(finding["rule_id"]),
                "path": str(finding["path"]),
                "symbol": str(finding["symbol"]),
                "lines": list(finding["lines"]),
                "owner": str(allowed["owner"]),
                "reason": str(allowed["reason"]),
                "remove_task": str(allowed["remove_task"]),
            }
        )

    for key, allowed in allowlist_by_key.items():
        if key in findings:
            continue
        violations.append(
            _violation(
                rule_id=str(allowed["rule_id"]),
                violation_type=STALE_ALLOWLIST,
                path=str(allowed["path"]),
                symbol=str(allowed["symbol"]),
                line=0,
                message=(
                    "Stale NLE boundary allowlist entry: "
                    f"{allowed['path']} -> {allowed['symbol']}; remove the entry"
                ),
            )
        )

    existing_debt.sort(key=_row_sort_key)
    violations.sort(key=_row_sort_key)
    return {
        "policy_id": str(policy.get("policy_id") or ""),
        "schema_version": POLICY_SCHEMA_VERSION,
        "existing_debt": existing_debt,
        "violations": violations,
    }


def scan_repo(
    root: str | Path,
    *,
    policy_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    return scan_report(root, policy_path=policy_path)["violations"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan no-new-debt NLE boundaries.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--policy", default=None, help="Optional policy JSON path.")
    args = parser.parse_args(argv)
    report = scan_report(args.root, policy_path=args.policy)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["violations"] else 0


def _scan_source_rule(
    *,
    repo_root: Path,
    rule: dict[str, Any],
    findings: dict[tuple[str, str, str], dict[str, Any]],
    violations: list[dict[str, Any]],
) -> None:
    for source_root in rule["source_roots"]:
        relative_root = str(source_root).replace("\\", "/").strip("/")
        absolute_root = repo_root / relative_root
        if not absolute_root.is_dir():
            violations.append(
                _violation(
                    rule_id=str(rule["rule_id"]),
                    violation_type=SOURCE_ROOT_MISSING,
                    path=relative_root,
                    symbol="directory",
                    line=0,
                    message=f"NLE boundary source root is missing: {relative_root}",
                )
            )
            continue
        for file_path in sorted(absolute_root.rglob("*.py")):
            relative_path = file_path.relative_to(repo_root).as_posix()
            try:
                source = file_path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(file_path))
            except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                violations.append(
                    _violation(
                        rule_id=str(rule["rule_id"]),
                        violation_type=SCAN_ERROR,
                        path=relative_path,
                        symbol="python_ast",
                        line=int(getattr(exc, "lineno", 0) or 0),
                        message=f"Cannot scan NLE boundary source: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if str(rule["kind"]) in {"import", "module_import"}:
                module_only = bool(rule.get("module_scope_only", False))
                nodes = _module_scope_nodes(tree) if module_only else ast.walk(tree)
                for module, symbol, line in _iter_imports(nodes):
                    if _matches_prefixes(module, rule["forbidden_module_prefixes"]):
                        _add_finding(
                            findings,
                            rule_id=str(rule["rule_id"]),
                            path=relative_path,
                            symbol=symbol,
                            line=line,
                        )
            else:
                attributes = {str(value) for value in rule["attributes"]}
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if str(node.func.attr) not in attributes:
                        continue
                    _add_finding(
                        findings,
                        rule_id=str(rule["rule_id"]),
                        path=relative_path,
                        symbol=str(node.func.attr),
                        line=int(getattr(node, "lineno", 0) or 0),
                    )


def _scan_forbidden_paths(
    *,
    repo_root: Path,
    rule: dict[str, Any],
    findings: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    for value in rule["paths"]:
        relative_path = str(value).replace("\\", "/").strip("/")
        if not (repo_root / relative_path).exists():
            continue
        _add_finding(
            findings,
            rule_id=str(rule["rule_id"]),
            path=relative_path,
            symbol="directory",
            line=0,
        )


def _module_scope_nodes(tree: ast.Module) -> Iterable[ast.AST]:
    pending = list(tree.body)
    while pending:
        node = pending.pop(0)
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list):
                pending.extend(item for item in value if isinstance(item, ast.AST))
            elif isinstance(value, ast.AST):
                pending.append(value)


def _iter_imports(nodes: Iterable[ast.AST]) -> Iterable[tuple[str, str, int]]:
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            module = str(node.module)
            for alias in node.names:
                symbol = f"{module}.{alias.name}"
                yield module, symbol, int(getattr(node, "lineno", 0) or 0)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = str(alias.name)
                yield module, module, int(getattr(node, "lineno", 0) or 0)


def _matches_prefixes(module: str, prefixes: list[Any]) -> bool:
    return any(
        module == str(prefix) or module.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _add_finding(
    findings: dict[tuple[str, str, str], dict[str, Any]],
    *,
    rule_id: str,
    path: str,
    symbol: str,
    line: int,
) -> None:
    key = (rule_id, path, symbol)
    row = findings.setdefault(
        key,
        {"rule_id": rule_id, "path": path, "symbol": symbol, "lines": []},
    )
    if line not in row["lines"]:
        row["lines"].append(line)
        row["lines"].sort()


def _violation(
    *,
    rule_id: str,
    violation_type: str,
    path: str,
    symbol: str,
    line: int,
    message: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_name": "NLE_NO_NEW_BOUNDARY_DEBT",
        "violation_type": violation_type,
        "path": path,
        "symbol": symbol,
        "line": line,
        "column": 0,
        "message": f"[{violation_type}] {message}",
    }


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"NLE boundary policy requires non-empty {key}")
    return value


def _validate_exact_relative_path(value: str, *, field: str) -> None:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
        raise ValueError(f"{field} must be an exact repository-relative path: {value!r}")
    if any(character in normalized for character in _GLOB_CHARACTERS):
        raise ValueError(f"{field} must not contain glob characters: {value!r}")


def _validate_exact_symbol(value: str, *, field: str) -> None:
    normalized = value.strip()
    if not normalized or any(character in normalized for character in _GLOB_CHARACTERS):
        raise ValueError(f"{field} must be an exact symbol without glob characters: {value!r}")


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(row.get("rule_id") or ""),
        str(row.get("path") or ""),
        str(row.get("symbol") or ""),
        int(row.get("line") or 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
