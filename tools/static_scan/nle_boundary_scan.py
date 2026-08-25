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
        if kind in {
            "import",
            "module_import",
            "call_attribute",
            "call_name",
            "world_canon_mutation",
        }:
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
        elif kind == "call_name":
            names = rule.get("names")
            if not isinstance(names, list) or not names:
                raise ValueError(f"{rule_id} requires names")
            for name in names:
                _validate_exact_symbol(str(name), field=f"{rule_id}.names")
        elif kind == "world_canon_mutation":
            modules = rule.get("store_modules")
            mutators = rule.get("mutator_names")
            issuer_names = rule.get("issuer_names", [])
            trusted_sites = rule.get("trusted_call_sites", [])
            if not isinstance(modules, list) or not modules:
                raise ValueError(f"{rule_id} requires store_modules")
            if not isinstance(mutators, list) or not mutators:
                raise ValueError(f"{rule_id} requires mutator_names")
            if not isinstance(issuer_names, list):
                raise ValueError(f"{rule_id}.issuer_names must be a list")
            if not isinstance(trusted_sites, list):
                raise ValueError(f"{rule_id}.trusted_call_sites must be a list")
            for module in modules:
                _validate_exact_symbol(str(module), field=f"{rule_id}.store_modules")
            for mutator in mutators:
                _validate_exact_symbol(str(mutator), field=f"{rule_id}.mutator_names")
            for issuer_name in issuer_names:
                _validate_exact_symbol(str(issuer_name), field=f"{rule_id}.issuer_names")
            seen_sites: set[tuple[str, str, int]] = set()
            for site in trusted_sites:
                if not isinstance(site, dict) or set(site) != {"path", "symbol", "line"}:
                    raise ValueError(
                        f"{rule_id}.trusted_call_sites entries require path/symbol/line"
                    )
                trusted_path = str(site["path"])
                _validate_exact_relative_path(
                    trusted_path, field=f"{rule_id}.trusted_call_sites.path"
                )
                trusted_symbol = str(site["symbol"])
                _validate_exact_symbol(
                    trusted_symbol, field=f"{rule_id}.trusted_call_sites.symbol"
                )
                trusted_line = int(site["line"])
                if trusted_line <= 0:
                    raise ValueError(f"{rule_id}.trusted_call_sites.line must be positive")
                site_key = (trusted_path, trusted_symbol, trusted_line)
                if site_key in seen_sites:
                    raise ValueError(f"duplicate trusted call site: {site_key}")
                seen_sites.add(site_key)
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
        if kind in {
            "import",
            "module_import",
            "call_attribute",
            "call_name",
            "world_canon_mutation",
        }:
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
    rule_kind = str(rule["kind"])
    repo_file_flow = (
        _repo_file_flow_context(repo_root, rule["source_roots"])
        if rule_kind == "world_canon_mutation"
        else {}
    )
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
            source = ""
            try:
                source = file_path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(file_path))
            except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                if rule_kind == "world_canon_mutation" and isinstance(exc, SyntaxError):
                    for symbol, line in _scan_unparsed_world_canon_source(source):
                        _add_finding(
                            findings,
                            rule_id=str(rule["rule_id"]),
                            path=relative_path,
                            symbol=symbol,
                            line=line,
                        )
                    continue
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
            if rule_kind in {"import", "module_import"}:
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
            elif rule_kind == "world_canon_mutation":
                trusted_sites = {
                    (str(site["path"]), str(site["symbol"]), int(site["line"]))
                    for site in rule.get("trusted_call_sites", [])
                }
                string_facts = _StringFacts(tree)
                (
                    external_sinks,
                    external_returns,
                    external_parameters,
                    local_sinks,
                    local_returns,
                ) = repo_file_flow.get(relative_path, ({}, {}, {}, {}, {}))
                source_findings = [
                    *_iter_world_canon_mutations(tree, rule, facts=string_facts),
                    *_iter_canon_file_mutations(
                        tree,
                        source_path=relative_path,
                        facts=string_facts,
                        external_sink_summaries=external_sinks,
                        external_return_summaries=external_returns,
                        external_parameter_names=external_parameters,
                        precomputed_sink_summaries=local_sinks,
                        precomputed_return_summaries=local_returns,
                    ),
                ]
                for symbol, line in source_findings:
                    if (relative_path, symbol, line) in trusted_sites:
                        continue
                    _add_finding(
                        findings,
                        rule_id=str(rule["rule_id"]),
                        path=relative_path,
                        symbol=symbol,
                        line=line,
                    )
            elif str(rule["kind"]) == "call_attribute":
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
            else:
                names = {str(value) for value in rule["names"]}
                aliases = set(names)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    for alias in node.names:
                        if str(alias.name) in names:
                            aliases.add(str(alias.asname or alias.name))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    matched_name = ""
                    if isinstance(node.func, ast.Name) and str(node.func.id) in aliases:
                        matched_name = str(node.func.id)
                    elif isinstance(node.func, ast.Attribute) and str(node.func.attr) in names:
                        matched_name = str(node.func.attr)
                    if not matched_name:
                        continue
                    _add_finding(
                        findings,
                        rule_id=str(rule["rule_id"]),
                        path=relative_path,
                        symbol=next(
                            (name for name in sorted(names) if name == matched_name),
                            sorted(names)[0],
                        ),
                        line=int(getattr(node, "lineno", 0) or 0),
                    )


def _iter_world_canon_mutations(
    tree: ast.Module,
    rule: dict[str, Any],
    *,
    facts: _StringFacts,
) -> Iterable[tuple[str, int]]:
    """Resolve common aliases so only WorldCanon ``upsert`` calls are banned.

    Method names unique to the raw Canon API are rejected regardless of the
    receiver.  Generic ``upsert`` is rejected only when dataflow identifies a
    WorldCanonStore instance, which avoids flagging Chroma collection writes.
    """

    store_modules = {str(value) for value in rule["store_modules"]}
    mutators = {str(value) for value in rule["mutator_names"]}
    issuer_names = {str(value) for value in rule.get("issuer_names", [])}
    class_aliases = {"WorldCanonStore"}
    issuer_aliases = set(issuer_names)
    module_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()
    builtins_aliases: set[str] = set()
    builtin_import_aliases: set[str] = set()
    builtin_dynamic_aliases: dict[str, str] = {}
    private_permit_aliases: set[str] = set()
    private_permit_import_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in store_modules:
            for alias in node.names:
                if alias.name == "WorldCanonStore":
                    class_aliases.add(str(alias.asname or alias.name))
                if alias.name == "_CANON_APPLY_PERMIT":
                    private_permit_aliases.add(str(alias.asname or alias.name))
                    private_permit_import_lines.append(int(getattr(node, "lineno", 0) or 0))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in issuer_names:
                    issuer_aliases.add(str(alias.asname or alias.name))
                if node.module == "importlib" and alias.name == "import_module":
                    import_module_aliases.add(str(alias.asname or alias.name))
                if node.module == "builtins" and alias.name in {"eval", "exec"}:
                    builtin_dynamic_aliases[str(alias.asname or alias.name)] = alias.name
                if node.module == "builtins" and alias.name == "__import__":
                    builtin_import_aliases.add(str(alias.asname or alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in store_modules:
                    module_aliases.add(str(alias.asname or alias.name))
                if alias.name == "importlib":
                    importlib_aliases.add(str(alias.asname or alias.name))
                if alias.name == "builtins":
                    builtins_aliases.add(str(alias.asname or alias.name))

    receivers: set[str] = set()
    callable_aliases: dict[str, str] = {}
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    store_factories = {
        str(node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _function_returns_world_canon_store(node, class_aliases, module_aliases)
    }
    while changed:
        changed = False
        for node in assignments:
            value = node.value
            targets = _assignment_targets(node)
            if not targets:
                continue
            value_path = _expression_path(value)
            if _is_dynamic_store_import(
                value,
                store_modules=store_modules,
                importlib_aliases=importlib_aliases,
                import_module_aliases=import_module_aliases,
                builtin_import_aliases=builtin_import_aliases,
                builtins_aliases=builtins_aliases,
                facts=facts,
            ):
                for target in targets:
                    if target not in module_aliases:
                        module_aliases.add(target)
                        changed = True
            lookup_object, lookup_names = _static_lookup(value, facts)
            if (
                "WorldCanonStore" in lookup_names
                and _is_store_module_expression(
                    lookup_object,
                    module_aliases=module_aliases,
                    store_modules=store_modules,
                    importlib_aliases=importlib_aliases,
                    import_module_aliases=import_module_aliases,
                    builtin_import_aliases=builtin_import_aliases,
                    builtins_aliases=builtins_aliases,
                    facts=facts,
                )
            ):
                for target in targets:
                    if target not in class_aliases:
                        class_aliases.add(target)
                        changed = True
            if (
                "_CANON_APPLY_PERMIT" in lookup_names
                and _is_store_module_expression(
                    lookup_object,
                    module_aliases=module_aliases,
                    store_modules=store_modules,
                    importlib_aliases=importlib_aliases,
                    import_module_aliases=import_module_aliases,
                    builtin_import_aliases=builtin_import_aliases,
                    builtins_aliases=builtins_aliases,
                    facts=facts,
                )
            ):
                for target in targets:
                    if target not in private_permit_aliases:
                        private_permit_aliases.add(target)
                        changed = True
            if value_path in class_aliases:
                for target in targets:
                    if target not in class_aliases:
                        class_aliases.add(target)
                        changed = True
            if value_path in module_aliases:
                for target in targets:
                    if target not in module_aliases:
                        module_aliases.add(target)
                        changed = True
            is_unshadowed_builtin_import = (
                isinstance(value, ast.Name)
                and value.id == "__import__"
                and not facts.is_bound(value, "__import__")
            )
            is_explicit_builtin_import = (
                isinstance(value, ast.Attribute)
                and value.attr == "__import__"
                and _expression_path(value.value) in builtins_aliases
            )
            if (
                value_path in builtin_import_aliases
                or is_unshadowed_builtin_import
                or is_explicit_builtin_import
            ):
                for target in targets:
                    if target not in builtin_import_aliases:
                        builtin_import_aliases.add(target)
                        changed = True
            if value_path in private_permit_aliases:
                for target in targets:
                    if target not in private_permit_aliases:
                        private_permit_aliases.add(target)
                        changed = True
            if value_path in callable_aliases:
                for target in targets:
                    if callable_aliases.get(target) != callable_aliases[value_path]:
                        callable_aliases[target] = callable_aliases[value_path]
                        changed = True
            if value_path in builtin_dynamic_aliases:
                for target in targets:
                    dynamic = f"dynamic:{builtin_dynamic_aliases[value_path]}"
                    if callable_aliases.get(target) != dynamic:
                        callable_aliases[target] = dynamic
                        changed = True
            if (
                isinstance(value, ast.Attribute)
                and _expression_path(value.value) in builtins_aliases
                and value.attr in {"eval", "exec"}
            ):
                for target in targets:
                    dynamic = f"dynamic:{value.attr}"
                    if callable_aliases.get(target) != dynamic:
                        callable_aliases[target] = dynamic
                        changed = True
            is_store = _is_world_canon_constructor(
                value,
                class_aliases=class_aliases,
                module_aliases=module_aliases,
            ) or value_path in receivers or (
                isinstance(value, ast.Call) and _expression_path(value.func) in store_factories
            )
            if is_store:
                for target in targets:
                    if target not in receivers:
                        receivers.add(target)
                        changed = True
            if isinstance(value, ast.Attribute) and value.attr in mutators:
                receiver = _expression_path(value.value)
                if value.attr != "upsert" or receiver in receivers:
                    for target in targets:
                        if callable_aliases.get(target) != value.attr:
                            callable_aliases[target] = value.attr
                            changed = True
            dynamic_mutator = _lookup_mutator_name(
                value,
                receivers,
                mutators,
                facts,
                class_aliases=class_aliases,
                module_aliases=module_aliases,
            )
            if dynamic_mutator:
                method = dynamic_mutator
                for target in targets:
                    if callable_aliases.get(target) != method:
                        callable_aliases[target] = method
                        changed = True
            if isinstance(value, ast.Attribute) and value.attr in issuer_names:
                for target in targets:
                    callable_aliases[target] = f"issuer:{value.attr}"
            issuer_lookup = _lookup_sensitive_name(value, issuer_names, facts)
            if issuer_lookup:
                for target in targets:
                    callable_aliases[target] = f"issuer:{issuer_lookup}"

    for line in private_permit_import_lines:
        yield "WorldCanonStore._CANON_APPLY_PERMIT", line

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in private_permit_aliases:
            yield "WorldCanonStore._CANON_APPLY_PERMIT", int(getattr(node, "lineno", 0) or 0)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "_CANON_APPLY_PERMIT"
            and _is_store_module_expression(
                node.value,
                module_aliases=module_aliases,
                store_modules=store_modules,
                importlib_aliases=importlib_aliases,
                import_module_aliases=import_module_aliases,
                builtin_import_aliases=builtin_import_aliases,
                builtins_aliases=builtins_aliases,
                facts=facts,
            )
        ):
            yield "WorldCanonStore._CANON_APPLY_PERMIT", int(getattr(node, "lineno", 0) or 0)
        elif _is_private_permit_lookup(
            node,
            module_aliases,
            store_modules=store_modules,
            importlib_aliases=importlib_aliases,
            import_module_aliases=import_module_aliases,
            builtin_import_aliases=builtin_import_aliases,
            builtins_aliases=builtins_aliases,
            facts=facts,
        ):
            yield "WorldCanonStore._CANON_APPLY_PERMIT", int(getattr(node, "lineno", 0) or 0)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = int(getattr(node, "lineno", 0) or 0)
        if isinstance(node.func, ast.Name):
            if node.func.id in builtin_dynamic_aliases:
                yield f"DynamicExecution.{builtin_dynamic_aliases[node.func.id]}", line
                continue
            if node.func.id in {"eval", "exec"} and not facts.is_bound(
                node.func, node.func.id
            ):
                yield f"DynamicExecution.{node.func.id}", line
                continue
        if (
            isinstance(node.func, ast.Attribute)
            and _expression_path(node.func.value) in builtins_aliases
            and node.func.attr in {"eval", "exec"}
        ):
            yield f"DynamicExecution.{node.func.attr}", line
            continue
        if isinstance(node.func, ast.Name) and node.func.id in callable_aliases:
            method = callable_aliases[node.func.id]
            if method.startswith("dynamic:"):
                yield f"DynamicExecution.{method.split(':', 1)[1]}", line
                continue
            if method.startswith("issuer:"):
                yield f"CanonApprovalIssuer.{method.split(':', 1)[1]}", line
                continue
            yield f"WorldCanonStore.{method}", line
            continue
        if isinstance(node.func, ast.Name) and node.func.id in issuer_aliases:
            canonical = next(
                (name for name in issuer_names if name == node.func.id),
                "CanonApprovalTokenIssuer",
            )
            yield f"CanonApprovalIssuer.{canonical}", line
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in issuer_names:
            yield f"CanonApprovalIssuer.{node.func.attr}", line
            continue
        issuer_lookup = _lookup_sensitive_name(node.func, issuer_names, facts)
        if issuer_lookup:
            yield f"CanonApprovalIssuer.{issuer_lookup}", line
            continue
        dynamic_mutator = _lookup_mutator_name(
            node.func,
            receivers,
            mutators,
            facts,
            class_aliases=class_aliases,
            module_aliases=module_aliases,
        )
        if dynamic_mutator:
            method = dynamic_mutator
            yield f"WorldCanonStore.{method}", line
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in mutators:
            continue
        method = str(node.func.attr)
        receiver = _expression_path(node.func.value)
        class_dispatch = receiver in class_aliases or any(
            receiver == f"{alias}.WorldCanonStore" for alias in module_aliases
        )
        if method == "upsert" and receiver not in receivers and not class_dispatch:
            continue
        yield f"WorldCanonStore.{method}", line

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for function_name, parameter_index in _world_canon_upsert_wrappers(tree):
        function = functions.get(function_name)
        if function is None:
            continue
        positional = list(function.args.posonlyargs) + list(function.args.args)
        if parameter_index >= len(positional):
            continue
        parameter_name = positional[parameter_index].arg
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _expression_path(node.func) != function_name:
                continue
            argument: ast.AST | None = (
                node.args[parameter_index] if len(node.args) > parameter_index else None
            )
            if argument is None:
                argument = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == parameter_name),
                    None,
                )
            argument_path = _expression_path(argument)
            if argument_path in receivers or _is_world_canon_constructor(
                argument,
                class_aliases=class_aliases,
                module_aliases=module_aliases,
            ):
                yield "WorldCanonStore.upsert", int(getattr(node, "lineno", 0) or 0)


def _assignment_targets(node: ast.AST) -> set[str]:
    raw_targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        raw_targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        raw_targets.append(node.target)
    return {
        path
        for target in raw_targets
        if (path := _expression_path(target))
    }


def _expression_path(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return str(node.id)
    if isinstance(node, ast.Attribute):
        parent = _expression_path(node.value)
        return f"{parent}.{node.attr}" if parent else str(node.attr)
    return ""


class _StringFacts:
    """Small, scope-aware constant-string lattice for reflection checks."""

    _MAX_VALUES = 32
    _MAX_LENGTH = 512

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self._parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self._parents[id(child)] = parent
        self._scopes = {
            id(node): node
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            )
        }
        self._scope_cache: dict[int, ast.AST] = {}
        self._scope_parent: dict[int, ast.AST | None] = {}
        for scope in self._scopes.values():
            parent = self._parents.get(id(scope))
            while parent is not None and id(parent) not in self._scopes:
                parent = self._parents.get(id(parent))
            self._scope_parent[id(scope)] = parent
        self._bound: dict[int, set[str]] = {key: set() for key in self._scopes}
        self._facts: dict[tuple[int, str], set[str]] = {}
        self._assignments: list[tuple[ast.AST, str, ast.AST]] = []
        self._collect_bindings()
        self._solve()

    def scope_of(self, node: ast.AST) -> ast.AST:
        cached = self._scope_cache.get(id(node))
        if cached is not None:
            return cached
        current: ast.AST | None = node
        while current is not None and id(current) not in self._scopes:
            current = self._parents.get(id(current))
        scope = current if current is not None else self.tree
        self._scope_cache[id(node)] = scope
        return scope

    def is_bound(self, node: ast.AST, name: str) -> bool:
        scope: ast.AST | None = self.scope_of(node)
        while scope is not None:
            if name in self._bound[id(scope)]:
                return True
            scope = self._scope_parent.get(id(scope))
        return False

    def values(self, node: ast.AST | None, *, scope: ast.AST | None = None) -> set[str]:
        if node is None:
            return set()
        current_scope = scope or self.scope_of(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._bounded({node.value})
        if isinstance(node, ast.Name):
            return self._lookup_name(current_scope, node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._combine(
                self.values(node.left, scope=current_scope),
                self.values(node.right, scope=current_scope),
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._join_path_values(
                self.values(node.left, scope=current_scope),
                self.values(node.right, scope=current_scope),
            )
        if isinstance(node, ast.IfExp):
            return self._bounded(
                self.values(node.body, scope=current_scope)
                | self.values(node.orelse, scope=current_scope)
            )
        if isinstance(node, ast.JoinedStr):
            values = {""}
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    fragment = {part.value}
                elif isinstance(part, ast.FormattedValue):
                    fragment = self.values(part.value, scope=current_scope)
                    if not fragment and isinstance(part.value, ast.Constant):
                        fragment = {str(part.value.value)}
                else:
                    return set()
                values = self._combine(values, fragment)
                if not values:
                    return set()
            return values
        if isinstance(node, ast.Call):
            function_path = _expression_path(node.func)
            if function_path in {"Path", "pathlib.Path", "PurePath", "pathlib.PurePath"}:
                return self._join_path_arguments(node.args, current_scope)
            if function_path in {"os.path.join", "posixpath.join", "ntpath.join"}:
                return self._join_path_arguments(node.args, current_scope)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
                base = self.values(node.func.value, scope=current_scope)
                if not base:
                    return set()
                values = base
                for argument in node.args:
                    values = self._join_path_values(
                        values, self.values(argument, scope=current_scope)
                    )
                    if not values:
                        return set()
                return values
        return set()

    def _collect_bindings(self) -> None:
        for node in ast.walk(self.tree):
            scope = self.scope_of(node)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self._bound[id(scope)].add(node.id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                function_scope = node
                arguments = (
                    list(node.args.posonlyargs)
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                )
                if node.args.vararg is not None:
                    arguments.append(node.args.vararg)
                if node.args.kwarg is not None:
                    arguments.append(node.args.kwarg)
                self._bound[id(function_scope)].update(argument.arg for argument in arguments)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent_scope = self._scope_parent.get(id(node))
                if parent_scope is not None:
                    self._bound[id(parent_scope)].add(node.name)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".", 1)[0]
                    self._bound[id(scope)].add(name)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                for name in _simple_name_targets(node):
                    self._assignments.append((scope, name, node.value))

    def _solve(self) -> None:
        for _iteration in range(16):
            changed = False
            for scope, name, value in self._assignments:
                resolved = self.values(value, scope=scope)
                if not resolved:
                    continue
                key = (id(scope), name)
                merged = self._bounded(self._facts.get(key, set()) | resolved)
                if merged != self._facts.get(key, set()):
                    self._facts[key] = merged
                    changed = True
            if not changed:
                return

    def _lookup_name(self, scope: ast.AST, name: str) -> set[str]:
        current: ast.AST | None = scope
        while current is not None:
            if name in self._bound[id(current)]:
                return set(self._facts.get((id(current), name), set()))
            current = self._scope_parent.get(id(current))
        return set()

    def _combine(self, left: set[str], right: set[str]) -> set[str]:
        if not left or not right:
            return set()
        return self._bounded({first + second for first in left for second in right})

    def _join_path_arguments(self, arguments: list[ast.expr], scope: ast.AST) -> set[str]:
        if not arguments:
            return set()
        values = self.values(arguments[0], scope=scope)
        for argument in arguments[1:]:
            values = self._join_path_values(
                values, self.values(argument, scope=scope)
            )
            if not values:
                return set()
        return values

    def _join_path_values(self, left: set[str], right: set[str]) -> set[str]:
        if not left or not right:
            return set()
        return self._bounded(
            {
                first.rstrip("/\\") + "/" + second.lstrip("/\\")
                for first in left
                for second in right
            }
        )

    def _bounded(self, values: set[str]) -> set[str]:
        return {
            value
            for value in sorted(values)[: self._MAX_VALUES]
            if len(value) <= self._MAX_LENGTH
        }


def _simple_name_targets(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        targets.append(node.target)
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _static_lookup(
    node: ast.AST,
    facts: _StringFacts,
) -> tuple[ast.AST | None, set[str]]:
    if (
        isinstance(node, ast.Call)
        and _expression_path(node.func) in {"getattr", "builtins.getattr"}
        and len(node.args) >= 2
    ):
        return node.args[0], facts.values(node.args[1])
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "__getattribute__"
        and node.args
    ):
        return node.func.value, facts.values(node.args[0])
    if not isinstance(node, ast.Subscript):
        return None, set()
    names = facts.values(node.slice)
    if not names:
        return None, set()
    if (
        isinstance(node.value, ast.Call)
        and _expression_path(node.value.func) in {"vars", "builtins.vars"}
        and node.value.args
    ):
        return node.value.args[0], names
    if isinstance(node.value, ast.Attribute) and node.value.attr == "__dict__":
        return node.value.value, names
    return None, set()


def _is_world_canon_constructor(
    node: ast.AST,
    *,
    class_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    path = _expression_path(node.func)
    return path in class_aliases or any(
        path == f"{module_alias}.WorldCanonStore" for module_alias in module_aliases
    )


def _lookup_mutator_name(
    node: ast.AST,
    receivers: set[str],
    mutators: set[str],
    facts: _StringFacts,
    *,
    class_aliases: set[str],
    module_aliases: set[str],
) -> str:
    receiver, names = _static_lookup(node, facts)
    receiver_path = _expression_path(receiver)
    receiver_is_store = (
        receiver_path in receivers
        or receiver_path in class_aliases
        or _is_world_canon_constructor(
            receiver,
            class_aliases=class_aliases,
            module_aliases=module_aliases,
        )
    )
    if not receiver_is_store:
        return ""
    matches = sorted(names & mutators)
    return matches[0] if matches else ""


def _function_returns_world_canon_store(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if _expression_path(node.returns) in class_aliases:
        return True
    local_receivers: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = child.value
            if _is_world_canon_constructor(
                value,
                class_aliases=class_aliases,
                module_aliases=module_aliases,
            ):
                local_receivers.update(_assignment_targets(child))
        elif isinstance(child, ast.Return):
            if _is_world_canon_constructor(
                child.value,
                class_aliases=class_aliases,
                module_aliases=module_aliases,
            ) or _expression_path(child.value) in local_receivers:
                return True
    return False


def _is_dynamic_store_import(
    node: ast.AST,
    *,
    store_modules: set[str],
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    builtin_import_aliases: set[str],
    builtins_aliases: set[str],
    facts: _StringFacts,
) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    path = _expression_path(node.func)
    import_paths = set(import_module_aliases) | {
        f"{alias}.import_module" for alias in importlib_aliases
    }
    builtin_import = (
        path in builtin_import_aliases
        or (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and not facts.is_bound(node.func, "__import__")
        )
        or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "__import__"
            and _expression_path(node.func.value) in builtins_aliases
        )
    )
    return (path in import_paths or builtin_import) and bool(
        facts.values(node.args[0]) & store_modules
    )


def _is_store_module_expression(
    node: ast.AST | None,
    *,
    module_aliases: set[str],
    store_modules: set[str],
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    builtin_import_aliases: set[str],
    builtins_aliases: set[str],
    facts: _StringFacts,
) -> bool:
    if node is None:
        return False
    if _expression_path(node) in module_aliases:
        return True
    return _is_dynamic_store_import(
        node,
        store_modules=store_modules,
        importlib_aliases=importlib_aliases,
        import_module_aliases=import_module_aliases,
        builtin_import_aliases=builtin_import_aliases,
        builtins_aliases=builtins_aliases,
        facts=facts,
    )


def _is_private_permit_lookup(
    node: ast.AST,
    module_aliases: set[str],
    *,
    store_modules: set[str],
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    builtin_import_aliases: set[str],
    builtins_aliases: set[str],
    facts: _StringFacts,
) -> bool:
    receiver, names = _static_lookup(node, facts)
    return "_CANON_APPLY_PERMIT" in names and _is_store_module_expression(
        receiver,
        module_aliases=module_aliases,
        store_modules=store_modules,
        importlib_aliases=importlib_aliases,
        import_module_aliases=import_module_aliases,
        builtin_import_aliases=builtin_import_aliases,
        builtins_aliases=builtins_aliases,
        facts=facts,
    )


def _lookup_sensitive_name(
    node: ast.AST,
    names: set[str],
    facts: _StringFacts,
) -> str:
    _receiver, resolved = _static_lookup(node, facts)
    matches = sorted(resolved & names)
    return matches[0] if matches else ""


_MAX_FLOW_FUNCTIONS = 256
_MAX_FLOW_PARAMETERS = 64
_MAX_FLOW_ITERATIONS = 512
_MAX_LOCAL_FLOW_ITERATIONS = 128
_MAX_FLOW_FACTS = 16_384


def _call_argument(
    node: ast.Call,
    index: int,
    *keyword_names: str,
) -> ast.AST | None:
    if len(node.args) > index:
        return node.args[index]
    names = set(keyword_names)
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg in names),
        None,
    )


def _file_sink_arguments(node: ast.Call) -> list[tuple[ast.AST, str]]:
    """Return the paths a file operation actually mutates.

    Rename/move operations mutate both source and destination.  Copy and link
    families only mutate their destination; treating their source as a sink
    would turn ordinary read/copy-out operations into false positives.
    """

    path = _expression_path(node.func)
    sinks: list[tuple[ast.AST, str]] = []
    pathlib_class_call = path.startswith(("Path.", "pathlib.Path."))
    if isinstance(node.func, ast.Attribute):
        method = str(node.func.attr)
        receiver: ast.AST | None = node.func.value
        argument_offset = 0
        if pathlib_class_call:
            receiver = _call_argument(node, 0, "self")
            argument_offset = 1
        if method in {"write_text", "write_bytes", "unlink", "touch", "rmdir"}:
            if receiver is not None:
                sinks.append((receiver, f"CanonFile.{method}"))
        elif method in {"replace", "rename"}:
            # ``str.replace(old, new)`` is not a filesystem operation.
            minimum_string_args = 3 if pathlib_class_call else 2
            if len(node.args) < minimum_string_args:
                destination = _call_argument(
                    node, argument_offset, "target", "dst", "destination"
                )
                if receiver is not None:
                    sinks.append((receiver, f"CanonFile.{method}"))
                if destination is not None:
                    sinks.append((destination, f"CanonFile.{method}"))
        elif method == "open" and path != "os.open" and _call_opens_for_write(
            node, bound_method=not pathlib_class_call
        ):
            if receiver is not None:
                sinks.append((receiver, "CanonFile.open"))
        elif method in {"symlink_to", "hardlink_to"}:
            # pathlib writes the receiver path and only reads the target.
            if receiver is not None:
                sinks.append((receiver, f"CanonFile.{method}"))
        elif method == "link_to":
            # Deprecated Path.link_to has the reverse argument order: target
            # is the newly-created directory entry.
            destination = _call_argument(node, argument_offset, "target")
            if destination is not None:
                sinks.append((destination, "CanonFile.link_to"))

    if path in {"open", "io.open"} and _call_opens_for_write(node):
        destination = _call_argument(node, 0, "file")
        if destination is not None:
            sinks.append((destination, "CanonFile.open"))
    elif path == "os.open" and _os_open_may_write(node):
        destination = _call_argument(node, 0, "path")
        if destination is not None:
            sinks.append((destination, "CanonFile.open"))
    elif path in {"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree"}:
        target = _call_argument(node, 0, "path")
        if target is not None:
            sinks.append((target, f"CanonFile.{path.rsplit('.', 1)[-1]}"))
    elif path in {"os.replace", "os.rename", "shutil.move"}:
        source = _call_argument(node, 0, "src", "source")
        destination = _call_argument(node, 1, "dst", "destination")
        symbol = f"CanonFile.{path.rsplit('.', 1)[-1]}"
        if source is not None:
            sinks.append((source, symbol))
        if destination is not None:
            sinks.append((destination, symbol))
    elif path in {
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "os.link",
        "os.symlink",
    }:
        destination = _call_argument(node, 1, "dst", "destination")
        if destination is not None:
            sinks.append(
                (destination, f"CanonFile.{path.rsplit('.', 1)[-1]}")
            )
    return sinks


def _function_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    return (
        list(function.args.posonlyargs)
        + list(function.args.args)
        + list(function.args.kwonlyargs)
    )[:_MAX_FLOW_PARAMETERS]


def _function_call_argument(
    call: ast.Call,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter_index: int,
) -> ast.AST | None:
    parameters = _function_parameters(function)
    if parameter_index >= len(parameters):
        return None
    positional_count = len(function.args.posonlyargs) + len(function.args.args)
    if parameter_index < positional_count and len(call.args) > parameter_index:
        return call.args[parameter_index]
    parameter_name = parameters[parameter_index].arg
    return next(
        (
            keyword.value
            for keyword in call.keywords
            if keyword.arg == parameter_name
        ),
        None,
    )


def _external_call_argument(
    call: ast.Call,
    parameter_index: int,
    parameter_names: list[str],
) -> ast.AST | None:
    if len(call.args) > parameter_index:
        return call.args[parameter_index]
    if parameter_index >= len(parameter_names):
        return None
    parameter_name = parameter_names[parameter_index]
    return next(
        (
            keyword.value
            for keyword in call.keywords
            if keyword.arg == parameter_name
        ),
        None,
    )


def _flow_expression_dependencies(
    node: ast.AST | None,
    dependencies: dict[str, set[int]],
    *,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    return_summaries: dict[str, set[int]],
    external_return_summaries: dict[str, set[int]],
    external_parameter_names: dict[str, list[str]],
) -> set[int]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return set(dependencies.get(node.id, set())) if isinstance(node.ctx, ast.Load) else set()
    if isinstance(node, ast.Call):
        function_path = _expression_path(node.func)
        function = functions.get(function_path)
        if function is not None:
            result: set[int] = set()
            for parameter_index in return_summaries.get(function_path, set()):
                argument = _function_call_argument(node, function, parameter_index)
                result.update(
                    _flow_expression_dependencies(
                        argument,
                        dependencies,
                        functions=functions,
                        return_summaries=return_summaries,
                        external_return_summaries=external_return_summaries,
                        external_parameter_names=external_parameter_names,
                    )
                )
            return result
        if function_path in external_return_summaries:
            result = set()
            parameter_names = external_parameter_names.get(function_path, [])
            for parameter_index in external_return_summaries[function_path]:
                argument = _external_call_argument(
                    node, parameter_index, parameter_names
                )
                result.update(
                    _flow_expression_dependencies(
                        argument,
                        dependencies,
                        functions=functions,
                        return_summaries=return_summaries,
                        external_return_summaries=external_return_summaries,
                        external_parameter_names=external_parameter_names,
                    )
                )
            return result
        if function_path in {
            "Path",
            "pathlib.Path",
            "PurePath",
            "pathlib.PurePath",
            "os.path.join",
            "posixpath.join",
            "ntpath.join",
        }:
            expressions: list[ast.AST] = list(node.args)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {
            "joinpath",
            "with_name",
            "with_suffix",
            "resolve",
            "absolute",
        }:
            expressions = [node.func.value, *node.args]
        else:
            return set()
        result: set[int] = set()
        for expression in expressions:
            result.update(
                _flow_expression_dependencies(
                    expression,
                    dependencies,
                    functions=functions,
                    return_summaries=return_summaries,
                    external_return_summaries=external_return_summaries,
                    external_parameter_names=external_parameter_names,
                )
            )
        return result
    result: set[int] = set()
    for child in ast.iter_child_nodes(node):
        result.update(
            _flow_expression_dependencies(
                child,
                dependencies,
                functions=functions,
                return_summaries=return_summaries,
                external_return_summaries=external_return_summaries,
                external_parameter_names=external_parameter_names,
            )
        )
    return result


def _file_flow_summaries(
    tree: ast.Module,
    *,
    external_sink_summaries: dict[str, set[tuple[int, str]]] | None = None,
    external_return_summaries: dict[str, set[int]] | None = None,
    external_parameter_names: dict[str, list[str]] | None = None,
) -> tuple[dict[str, set[tuple[int, str]]], dict[str, set[int]]]:
    """Compute bounded param-to-sink and param-to-return summaries.

    The monotone fixed point handles arbitrary wrapper depth and mutual
    recursion once a cycle reaches a concrete sink/return.  Hard function,
    parameter, iteration, local-flow, and fact caps keep adversarial source
    bounded; normal modules converge in at most their call-graph diameter.
    """

    external_sink_summaries = external_sink_summaries or {}
    external_return_summaries = external_return_summaries or {}
    external_parameter_names = external_parameter_names or {}
    function_nodes = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        key=lambda node: (int(getattr(node, "lineno", 0) or 0), node.name),
    )[:_MAX_FLOW_FUNCTIONS]
    functions = {function.name: function for function in function_nodes}
    sink_summaries: dict[str, set[tuple[int, str]]] = {
        name: set() for name in functions
    }
    return_summaries: dict[str, set[int]] = {name: set() for name in functions}
    for _iteration in range(_MAX_FLOW_ITERATIONS):
        changed = False
        for function_name, function in functions.items():
            parameters = _function_parameters(function)
            dependencies: dict[str, set[int]] = {
                argument.arg: {index} for index, argument in enumerate(parameters)
            }
            assignments = [
                node
                for node in _walk_function_body(function)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            ]
            for _local_iteration in range(_MAX_LOCAL_FLOW_ITERATIONS):
                local_changed = False
                for assignment in assignments:
                    value_dependencies = _flow_expression_dependencies(
                        assignment.value,
                        dependencies,
                        functions=functions,
                        return_summaries=return_summaries,
                        external_return_summaries=external_return_summaries,
                        external_parameter_names=external_parameter_names,
                    )
                    for target in _simple_name_targets(assignment):
                        merged = dependencies.get(target, set()) | value_dependencies
                        if merged != dependencies.get(target, set()):
                            dependencies[target] = merged
                            local_changed = True
                if not local_changed:
                    break

            new_returns = set(return_summaries[function_name])
            for statement in _walk_function_body(function):
                if isinstance(statement, ast.Return):
                    new_returns.update(
                        _flow_expression_dependencies(
                            statement.value,
                            dependencies,
                            functions=functions,
                            return_summaries=return_summaries,
                            external_return_summaries=external_return_summaries,
                            external_parameter_names=external_parameter_names,
                        )
                    )
            new_sinks = set(sink_summaries[function_name])
            for call in (
                node
                for node in _walk_function_body(function)
                if isinstance(node, ast.Call)
            ):
                for expression, symbol in _file_sink_arguments(call):
                    for parameter_index in _flow_expression_dependencies(
                        expression,
                        dependencies,
                        functions=functions,
                        return_summaries=return_summaries,
                        external_return_summaries=external_return_summaries,
                        external_parameter_names=external_parameter_names,
                    ):
                        new_sinks.add((parameter_index, symbol))
                callee_name = _expression_path(call.func)
                callee = functions.get(callee_name)
                if callee is not None:
                    for callee_index, symbol in sink_summaries.get(callee_name, set()):
                        argument = _function_call_argument(call, callee, callee_index)
                        for parameter_index in _flow_expression_dependencies(
                            argument,
                            dependencies,
                            functions=functions,
                            return_summaries=return_summaries,
                            external_return_summaries=external_return_summaries,
                            external_parameter_names=external_parameter_names,
                        ):
                            new_sinks.add((parameter_index, symbol))
                if callee_name in external_sink_summaries:
                    parameter_names = external_parameter_names.get(callee_name, [])
                    for callee_index, symbol in external_sink_summaries[callee_name]:
                        argument = _external_call_argument(
                            call, callee_index, parameter_names
                        )
                        for parameter_index in _flow_expression_dependencies(
                            argument,
                            dependencies,
                            functions=functions,
                            return_summaries=return_summaries,
                            external_return_summaries=external_return_summaries,
                            external_parameter_names=external_parameter_names,
                        ):
                            new_sinks.add((parameter_index, symbol))

            if new_returns != return_summaries[function_name]:
                return_summaries[function_name] = new_returns
                changed = True
            if new_sinks != sink_summaries[function_name]:
                sink_summaries[function_name] = new_sinks
                changed = True
            fact_count = sum(len(rows) for rows in sink_summaries.values()) + sum(
                len(rows) for rows in return_summaries.values()
            )
            if fact_count >= _MAX_FLOW_FACTS:
                return sink_summaries, return_summaries
        if not changed:
            break
    return sink_summaries, return_summaries


_MAX_REPO_FLOW_MODULES = 2_048
_MAX_REPO_FLOW_ITERATIONS = 512


def _module_name_from_source_path(source_path: str) -> str:
    normalized = source_path.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    components = [part for part in normalized.split("/") if part]
    if components and components[-1] == "__init__":
        components.pop()
    return ".".join(components)


def _resolve_import_from(module_name: str, node: ast.ImportFrom) -> str:
    imported_module = str(node.module or "")
    if not node.level:
        return imported_module
    package = module_name.rsplit(".", 1)[0] if "." in module_name else ""
    parts = [part for part in package.split(".") if part]
    ascend = max(0, int(node.level) - 1)
    if ascend:
        parts = parts[: max(0, len(parts) - ascend)]
    if imported_module:
        parts.extend(imported_module.split("."))
    return ".".join(parts)


def _module_import_targets(
    tree: ast.Module,
    module_name: str,
) -> tuple[dict[str, str], dict[str, str]]:
    callable_aliases: dict[str, str] = {}
    module_aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_from(module_name, node)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = str(alias.asname or alias.name)
                qualified = (
                    f"{imported_module}.{alias.name}"
                    if imported_module
                    else str(alias.name)
                )
                callable_aliases[local_name] = qualified
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module_aliases[str(alias.asname)] = str(alias.name)
                else:
                    root_name = str(alias.name).split(".", 1)[0]
                    module_aliases[root_name] = root_name
    return callable_aliases, module_aliases


def _external_module_flow(
    tree: ast.Module,
    module_name: str,
    *,
    global_sink_summaries: dict[str, set[tuple[int, str]]],
    global_return_summaries: dict[str, set[int]],
    global_parameter_names: dict[str, list[str]],
) -> tuple[
    dict[str, set[tuple[int, str]]],
    dict[str, set[int]],
    dict[str, list[str]],
]:
    callable_aliases, module_aliases = _module_import_targets(tree, module_name)
    local_to_qualified = dict(callable_aliases)
    for local_module, qualified_module in module_aliases.items():
        for qualified_function in global_parameter_names:
            prefix = f"{qualified_module}."
            if qualified_function.startswith(prefix):
                local_to_qualified[
                    f"{local_module}.{qualified_function[len(prefix):]}"
                ] = qualified_function

    sink_summaries: dict[str, set[tuple[int, str]]] = {}
    return_summaries: dict[str, set[int]] = {}
    parameter_names: dict[str, list[str]] = {}
    for local_name, qualified_name in local_to_qualified.items():
        if qualified_name not in global_parameter_names:
            continue
        parameter_names[local_name] = list(global_parameter_names[qualified_name])
        if qualified_name in global_sink_summaries:
            sink_summaries[local_name] = set(global_sink_summaries[qualified_name])
        if qualified_name in global_return_summaries:
            return_summaries[local_name] = set(global_return_summaries[qualified_name])
    return sink_summaries, return_summaries, parameter_names


def _repo_file_flow_context(
    repo_root: Path,
    source_roots: Iterable[str],
) -> dict[
    str,
    tuple[
        dict[str, set[tuple[int, str]]],
        dict[str, set[int]],
        dict[str, list[str]],
        dict[str, set[tuple[int, str]]],
        dict[str, set[int]],
    ],
]:
    modules: dict[str, tuple[str, ast.Module]] = {}
    for source_root in source_roots:
        absolute_root = repo_root / str(source_root).replace("\\", "/").strip("/")
        if not absolute_root.is_dir():
            continue
        for file_path in sorted(absolute_root.rglob("*.py")):
            if len(modules) >= _MAX_REPO_FLOW_MODULES:
                break
            relative_path = file_path.relative_to(repo_root).as_posix()
            try:
                tree = ast.parse(
                    file_path.read_text(encoding="utf-8-sig"),
                    filename=str(file_path),
                )
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            modules[relative_path] = (
                _module_name_from_source_path(relative_path),
                tree,
            )

    global_parameter_names: dict[str, list[str]] = {}
    exported_names: dict[str, set[str]] = {}
    for relative_path, (module_name, tree) in modules.items():
        exported_names[relative_path] = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            global_parameter_names[f"{module_name}.{node.name}"] = [
                argument.arg for argument in _function_parameters(node)
            ]

    global_sink_summaries: dict[str, set[tuple[int, str]]] = {}
    global_return_summaries: dict[str, set[int]] = {}
    latest_local_sinks: dict[str, dict[str, set[tuple[int, str]]]] = {}
    latest_local_returns: dict[str, dict[str, set[int]]] = {}
    for _iteration in range(_MAX_REPO_FLOW_ITERATIONS):
        changed = False
        for relative_path, (module_name, tree) in modules.items():
            external_sinks, external_returns, external_parameters = _external_module_flow(
                tree,
                module_name,
                global_sink_summaries=global_sink_summaries,
                global_return_summaries=global_return_summaries,
                global_parameter_names=global_parameter_names,
            )
            if _iteration > 0 and not external_sinks and not external_returns:
                # With no imported flow facts, this module's local monotone
                # summary is already final from the initial whole-repo pass.
                continue
            local_sinks, local_returns = _file_flow_summaries(
                tree,
                external_sink_summaries=external_sinks,
                external_return_summaries=external_returns,
                external_parameter_names=external_parameters,
            )
            latest_local_sinks[relative_path] = local_sinks
            latest_local_returns[relative_path] = local_returns
            for function_name in exported_names[relative_path]:
                qualified_name = f"{module_name}.{function_name}"
                merged_sinks = global_sink_summaries.get(qualified_name, set()) | set(
                    local_sinks.get(function_name, set())
                )
                if merged_sinks != global_sink_summaries.get(qualified_name, set()):
                    global_sink_summaries[qualified_name] = merged_sinks
                    changed = True
                merged_returns = global_return_summaries.get(qualified_name, set()) | set(
                    local_returns.get(function_name, set())
                )
                if merged_returns != global_return_summaries.get(qualified_name, set()):
                    global_return_summaries[qualified_name] = merged_returns
                    changed = True
        if not changed:
            break

    contexts = {}
    for relative_path, (module_name, tree) in modules.items():
        external_sinks, external_returns, external_parameters = _external_module_flow(
            tree,
            module_name,
            global_sink_summaries=global_sink_summaries,
            global_return_summaries=global_return_summaries,
            global_parameter_names=global_parameter_names,
        )
        contexts[relative_path] = (
            external_sinks,
            external_returns,
            external_parameters,
            latest_local_sinks.get(relative_path, {}),
            latest_local_returns.get(relative_path, {}),
        )
    return contexts


def _world_canon_upsert_wrappers(tree: ast.Module) -> set[tuple[str, int]]:
    summaries: set[tuple[str, int]] = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        positional = list(function.args.posonlyargs) + list(function.args.args)
        dependencies: dict[str, set[int]] = {
            argument.arg: {index} for index, argument in enumerate(positional)
        }
        assignments = [
            node
            for node in _walk_function_body(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        ]
        for _iteration in range(8):
            changed = False
            for assignment in assignments:
                value_dependencies = _expression_dependencies(
                    assignment.value, dependencies
                )
                for target in _simple_name_targets(assignment):
                    merged = dependencies.get(target, set()) | value_dependencies
                    if merged != dependencies.get(target, set()):
                        dependencies[target] = merged
                        changed = True
            if not changed:
                break
        for call in (
            node for node in _walk_function_body(function) if isinstance(node, ast.Call)
        ):
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "upsert":
                continue
            for parameter_index in _expression_dependencies(
                call.func.value, dependencies
            ):
                summaries.add((function.name, parameter_index))
    return summaries


def _walk_function_body(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.AST]:
    stack = list(reversed(function.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _expression_dependencies(
    node: ast.AST | None,
    dependencies: dict[str, set[int]],
) -> set[int]:
    if node is None:
        return set()
    result: set[int] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            result.update(dependencies.get(child.id, set()))
    return result


def _iter_canon_file_mutations(
    tree: ast.Module,
    *,
    source_path: str = "",
    facts: _StringFacts,
    external_sink_summaries: dict[str, set[tuple[int, str]]] | None = None,
    external_return_summaries: dict[str, set[int]] | None = None,
    external_parameter_names: dict[str, list[str]] | None = None,
    precomputed_sink_summaries: dict[str, set[tuple[int, str]]] | None = None,
    precomputed_return_summaries: dict[str, set[int]] | None = None,
) -> Iterable[tuple[str, int]]:
    canon_module = source_path.replace("\\", "/").startswith("host_brain/memory/canon")
    external_sink_summaries = external_sink_summaries or {}
    external_return_summaries = external_return_summaries or {}
    external_parameter_names = external_parameter_names or {}
    if (
        precomputed_sink_summaries is not None
        and precomputed_return_summaries is not None
    ):
        sink_summaries = precomputed_sink_summaries
        return_summaries = precomputed_return_summaries
    else:
        sink_summaries, return_summaries = _file_flow_summaries(
            tree,
            external_sink_summaries=external_sink_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    tainted: set[str] = set()
    factories: set[str] = set()

    def is_tainted(expression: ast.AST | None) -> bool:
        return _canon_tainted_expression(
            expression,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = list(node.args.posonlyargs) + list(node.args.args)
        defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
        arguments_with_defaults = list(zip(positional, defaults, strict=True)) + list(
            zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
        )
        for argument, default in arguments_with_defaults:
            if default is not None and _literal_mentions_canon_root(default, facts):
                tainted.add(str(argument.arg))

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if not is_tainted(node.value):
                continue
            for target in _assignment_targets(node):
                if target not in tainted:
                    tainted.add(target)
                    changed = True
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in factories:
                continue
            if any(
                isinstance(child, ast.Return)
                and is_tainted(child.value)
                for child in ast.walk(node)
            ):
                factories.add(str(node.name))
                changed = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = int(getattr(node, "lineno", 0) or 0)
        for expression, symbol in _file_sink_arguments(node):
            if canon_module or is_tainted(expression):
                yield symbol, line

    for function_name, rows in sink_summaries.items():
        function = functions.get(function_name)
        if function is None:
            continue
        for parameter_index, symbol in rows:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _expression_path(node.func) != function_name:
                    continue
                argument = _function_call_argument(node, function, parameter_index)
                if argument is not None and is_tainted(argument):
                    yield symbol, int(getattr(node, "lineno", 0) or 0)

    for callable_name, rows in external_sink_summaries.items():
        parameter_names = external_parameter_names.get(callable_name, [])
        for parameter_index, symbol in rows:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _expression_path(node.func) != callable_name:
                    continue
                argument = _external_call_argument(
                    node, parameter_index, parameter_names
                )
                if argument is not None and is_tainted(argument):
                    yield symbol, int(getattr(node, "lineno", 0) or 0)


def _canon_tainted_expression(
    node: ast.AST | None,
    tainted: set[str],
    factories: set[str],
    facts: _StringFacts,
    *,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
    return_summaries: dict[str, set[int]] | None = None,
    external_return_summaries: dict[str, set[int]] | None = None,
    external_parameter_names: dict[str, list[str]] | None = None,
) -> bool:
    functions = functions or {}
    return_summaries = return_summaries or {}
    external_return_summaries = external_return_summaries or {}
    external_parameter_names = external_parameter_names or {}
    if node is None:
        return False
    path = _expression_path(node)
    if path in tainted:
        return True
    if _literal_mentions_canon_root(node, facts):
        return True
    if isinstance(node, ast.BinOp):
        return _canon_tainted_expression(
            node.left,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        ) or _canon_tainted_expression(
            node.right,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )
    if isinstance(node, ast.Attribute) and node.attr in {
        "parent",
        "world_root",
    }:
        return _canon_tainted_expression(
            node.value,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )
    if isinstance(node, ast.Subscript):
        if _expression_path(node.value) == "os.environ" and any(
            value.upper() == "NATSUME_WORLD_CANON_ROOT"
            for value in facts.values(node.slice)
        ):
            return True
        return _canon_tainted_expression(
            node.value,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )
    if not isinstance(node, ast.Call):
        return False
    function_path = _expression_path(node.func)
    if function_path in {
        "default_world_canon_root",
        "resolve_world_canon_root",
    }:
        return True
    if function_path in factories or function_path.rsplit(".", 1)[-1] in factories:
        return True
    function = functions.get(function_path)
    if function is not None:
        for parameter_index in return_summaries.get(function_path, set()):
            argument = _function_call_argument(node, function, parameter_index)
            if _canon_tainted_expression(
                argument,
                tainted,
                factories,
                facts,
                functions=functions,
                return_summaries=return_summaries,
                external_return_summaries=external_return_summaries,
                external_parameter_names=external_parameter_names,
            ):
                return True
    if function_path in external_return_summaries:
        parameter_names = external_parameter_names.get(function_path, [])
        for parameter_index in external_return_summaries[function_path]:
            argument = _external_call_argument(
                node, parameter_index, parameter_names
            )
            if _canon_tainted_expression(
                argument,
                tainted,
                factories,
                facts,
                functions=functions,
                return_summaries=return_summaries,
                external_return_summaries=external_return_summaries,
                external_parameter_names=external_parameter_names,
            ):
                return True
    if function_path in {"os.environ.get", "os.getenv"} and node.args:
        return any(
            value.upper() == "NATSUME_WORLD_CANON_ROOT"
            for value in facts.values(node.args[0])
        )
    if function_path in {"Path", "pathlib.Path", "os.path.join"}:
        return any(
            _canon_tainted_expression(
                arg,
                tainted,
                factories,
                facts,
                functions=functions,
                return_summaries=return_summaries,
                external_return_summaries=external_return_summaries,
                external_parameter_names=external_parameter_names,
            )
            for arg in node.args
        )
    if isinstance(node.func, ast.Attribute) and node.func.attr in {
        "joinpath",
        "with_name",
        "with_suffix",
        "resolve",
        "absolute",
    }:
        return _canon_tainted_expression(
            node.func.value,
            tainted,
            factories,
            facts,
            functions=functions,
            return_summaries=return_summaries,
            external_return_summaries=external_return_summaries,
            external_parameter_names=external_parameter_names,
        )
    return False


def _literal_mentions_canon_root(node: ast.AST, facts: _StringFacts) -> bool:
    for value in facts.values(node):
        if value.upper() == "NATSUME_WORLD_CANON_ROOT":
            return True
        components = [
            component
            for component in value.replace("\\", "/").lower().split("/")
            if component not in {"", "."}
        ]
        if any(
            components[index : index + 2] == ["canon", "world"]
            for index in range(max(0, len(components) - 1))
        ):
            return True
    return False


def _call_opens_for_write(node: ast.Call, *, bound_method: bool = False) -> bool:
    mode_index = 0 if bound_method else 1
    mode_node: ast.AST | None = node.args[mode_index] if len(node.args) > mode_index else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if mode_node is None:
        return False
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return any(marker in mode_node.value for marker in ("w", "a", "x", "+"))
    # A dynamic mode on a Canon-tainted path is not provably read-only.
    return True


def _os_open_may_write(node: ast.Call) -> bool:
    flags_node: ast.AST | None = node.args[1] if len(node.args) > 1 else None
    for keyword in node.keywords:
        if keyword.arg == "flags":
            flags_node = keyword.value
    return not _os_open_flags_are_provably_read_only(flags_node)


def _os_open_flags_are_provably_read_only(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value == 0
    if isinstance(node, ast.Attribute):
        return _expression_path(node) in {
            "os.O_RDONLY",
            "os.O_DIRECTORY",
            "os.O_CLOEXEC",
            "os.O_NOFOLLOW",
            "os.O_NONBLOCK",
            "os.O_BINARY",
            "os.O_NOINHERIT",
        }
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _os_open_flags_are_provably_read_only(
            node.left
        ) and _os_open_flags_are_provably_read_only(node.right)
    if (
        isinstance(node, ast.Call)
        and _expression_path(node.func) == "getattr"
        and len(node.args) >= 3
        and _expression_path(node.args[0]) == "os"
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value
        in {
            "O_DIRECTORY",
            "O_CLOEXEC",
            "O_NOFOLLOW",
            "O_NONBLOCK",
            "O_BINARY",
            "O_NOINHERIT",
        }
    ):
        return _os_open_flags_are_provably_read_only(node.args[2])
    return False


def _scan_unparsed_world_canon_source(source: str) -> Iterable[tuple[str, int]]:
    """Conservative fallback for known source fragments that do not parse."""

    lines = source.splitlines()
    permit_seen = "_CANON_APPLY_PERMIT" in source
    store_seen = "WorldCanonStore" in source or "canon_world_store" in source
    canon_path_seen = any(
        marker in source.replace("\\", "/").lower()
        for marker in ("canon/world", "natsume_world_canon_root")
    )
    if permit_seen:
        line = next((index for index, value in enumerate(lines, 1) if "_CANON_APPLY_PERMIT" in value), 1)
        yield "WorldCanonStore._CANON_APPLY_PERMIT", line
    for issuer_name in ("CanonApprovalTokenIssuer", "issue_approved_token"):
        for index, value in enumerate(lines, 1):
            if issuer_name in value:
                yield f"CanonApprovalIssuer.{issuer_name}", index
    if store_seen:
        for method in (
            "upsert_document",
            "approve_proposal_with_content",
            "rollback_proposal",
            "_replace_authorized_payload",
            "_delete_authorized_document",
        ):
            for index, value in enumerate(lines, 1):
                if method in value:
                    yield f"WorldCanonStore.{method}", index
    if canon_path_seen:
        for index, value in enumerate(lines, 1):
            if any(marker in value for marker in ("write_text", "write_bytes", "unlink", "os.remove", "os.replace")):
                yield "CanonFile.unparsed_write", index


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
