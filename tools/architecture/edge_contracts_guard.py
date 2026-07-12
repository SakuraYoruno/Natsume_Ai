"""Fail-closed Edge/Host import boundary guard for TASK-0493.

The default FREEZE_NEW mode accepts only the exact content-addressed debt set
recorded in ``edge_host_import_baseline.json``.  REPORT is non-blocking policy
observation, while ENFORCE rejects every implementation edge in either
direction.  The scanner uses Python's AST, includes local/TYPE_CHECKING imports
and constant-string dynamic imports, and never imports scanned modules.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
import warnings


POLICY_ID = "TASK-0493_EDGE_CONTRACTS_ONLY"
SCHEMA_VERSION = 1
MODE_ENV = "EDGE_CONTRACTS_ONLY_GUARD_MODE"
DEFAULT_BASELINE = Path(__file__).with_name("edge_host_import_baseline.json")
BOUNDARY_REMOVAL_TASK = "TASK-0581"

REASON_MODE_INVALID = "EDGE_CONTRACTS_GUARD_MODE_INVALID"
REASON_BASELINE_INVALID = "EDGE_CONTRACTS_BASELINE_INVALID"
REASON_MIGRATION_INVALID = "EDGE_CONTRACTS_MIGRATION_MANIFEST_INVALID"
REASON_SOURCE_ROOT_MISSING = "EDGE_CONTRACTS_SOURCE_ROOT_MISSING"
REASON_SOURCE_UNREADABLE = "EDGE_CONTRACTS_SOURCE_UNREADABLE"
REASON_SOURCE_SYNTAX = "EDGE_CONTRACTS_SOURCE_SYNTAX_INVALID"
REASON_DYNAMIC_UNRESOLVED = "EDGE_CONTRACTS_DYNAMIC_IMPORT_UNRESOLVED"
REASON_NEW_IMPORT = "EDGE_CONTRACTS_NEW_IMPLEMENTATION_IMPORT"
REASON_STALE_BASELINE = "EDGE_CONTRACTS_STALE_BASELINE_ENTRY"
REASON_UNMAPPED_DEBT = "EDGE_CONTRACTS_DEBT_MIGRATION_UNMAPPED"
REASON_STALE_MIGRATION = "EDGE_CONTRACTS_STALE_MIGRATION_EDGE"
REASON_ENFORCE_IMPORT = "EDGE_CONTRACTS_IMPLEMENTATION_IMPORT_FORBIDDEN"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_SAFE_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_CONTRACT_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "abc",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "json",
        "math",
        "natsume_contracts",
        "numbers",
        "operator",
        "re",
        "types",
        "typing",
        "uuid",
    }
)


class GuardMode(str, Enum):
    FREEZE_NEW = "FREEZE_NEW"
    REPORT = "REPORT"
    ENFORCE = "ENFORCE"


@dataclass(frozen=True, slots=True)
class GuardModeState:
    mode: GuardMode
    configured_value: str
    valid: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class BoundaryRule:
    direction: str
    source_root: str
    forbidden_prefix: str
    excluded_path_prefixes: tuple[str, ...] = ()


RULES = (
    BoundaryRule("EDGE_TO_HOST", "edge_client", "host_brain"),
    BoundaryRule(
        "HOST_TO_EDGE",
        "host_brain",
        "edge_client",
        ("host_brain/composition/",),
    ),
    BoundaryRule("CONTRACTS_TO_HOST", "natsume_contracts", "host_brain"),
    BoundaryRule("CONTRACTS_TO_EDGE", "natsume_contracts", "edge_client"),
    BoundaryRule("CONTRACTS_TO_FRAMEWORK", "natsume_contracts", "fastapi"),
    BoundaryRule("CONTRACTS_TO_FRAMEWORK", "natsume_contracts", "starlette"),
    BoundaryRule("CONTRACTS_TO_FRAMEWORK", "natsume_contracts", "uvicorn"),
    BoundaryRule("CONTRACTS_TO_STORAGE", "natsume_contracts", "sqlite3"),
    BoundaryRule("CONTRACTS_TO_STORAGE", "natsume_contracts", "sqlalchemy"),
    BoundaryRule("CONTRACTS_TO_PROVIDER", "natsume_contracts", "openai"),
    BoundaryRule("CONTRACTS_TO_PROVIDER", "natsume_contracts", "anthropic"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "httpx"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "requests"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "aiohttp"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "urllib3"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "websockets"),
    BoundaryRule("CONTRACTS_TO_NETWORK", "natsume_contracts", "socket"),
    BoundaryRule("CONTRACTS_TO_EXTERNAL", "natsume_contracts", "*"),
)


def load_guard_mode(
    environ: Mapping[str, str] | None = None,
    *,
    explicit: str | GuardMode | None = None,
) -> GuardModeState:
    if isinstance(explicit, GuardMode):
        return GuardModeState(explicit, explicit.value, True)
    source = environ or {}
    raw_value = explicit if explicit is not None else source.get(MODE_ENV, "")
    raw = str(raw_value or "").strip().upper()
    if not raw:
        return GuardModeState(GuardMode.FREEZE_NEW, "", True)
    try:
        return GuardModeState(GuardMode(raw), raw, True)
    except ValueError:
        return GuardModeState(
            GuardMode.FREEZE_NEW,
            raw[:64],
            False,
            REASON_MODE_INVALID,
        )


def inventory_report(root: str | Path) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    findings, scan_violations = _scan_rules(repo_root)
    fingerprints = tuple(row["fingerprint"] for row in findings)
    return {
        "policy_id": POLICY_ID,
        "schema_version": SCHEMA_VERSION,
        "root": repo_root.as_posix(),
        "findings": findings,
        "finding_count": len(findings),
        "fingerprints": list(fingerprints),
        "fingerprints_sha256": fingerprint_digest(fingerprints),
        "scan_violations": scan_violations,
    }


def scan_report(
    root: str | Path,
    *,
    baseline_path: str | Path | None = None,
    mode: str | GuardMode | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    mode_state = load_guard_mode(environ, explicit=mode)
    baseline_file = (
        Path(baseline_path).resolve()
        if baseline_path is not None
        else DEFAULT_BASELINE.resolve()
    )

    configuration_violations: list[dict[str, Any]] = []
    if not mode_state.valid:
        configuration_violations.append(
            _violation(
                REASON_MODE_INVALID,
                path="",
                message="guard mode must be FREEZE_NEW, REPORT or ENFORCE",
            )
        )

    baseline: dict[str, Any]
    migration: dict[str, Any]
    migration_file = baseline_file
    baseline_valid = True
    try:
        baseline = _load_baseline(baseline_file)
        expected = tuple(baseline["expected_fingerprints"])
        expected_set = frozenset(expected)
        if baseline["fingerprints_sha256"] != fingerprint_digest(expected):
            raise ValueError("baseline fingerprint digest mismatch")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        baseline_valid = False
        configuration_violations.append(
            _violation(
                REASON_BASELINE_INVALID,
                path=_display_path(baseline_file, repo_root),
                message=f"invalid content-addressed boundary baseline: {type(exc).__name__}: {exc}",
            )
        )
        baseline = {
            "expected_fingerprints": [],
            "fingerprints_sha256": fingerprint_digest(()),
            "migration_manifest": "",
            "migration_sha256": "",
        }
        migration = {"edges": []}
        expected = ()
        expected_set = frozenset()

    if baseline_valid:
        try:
            migration_file = _resolve_manifest_path(
                baseline_file,
                baseline["migration_manifest"],
            )
            migration = _load_migration_manifest(migration_file)
            if baseline["migration_sha256"] != content_digest(migration):
                raise ValueError("migration manifest digest mismatch")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            configuration_violations.append(
                _violation(
                    REASON_MIGRATION_INVALID,
                    path=_display_path(
                        migration_file,
                        repo_root,
                    ),
                    message=(
                        "invalid content-addressed boundary migration manifest: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            )
            migration = {"edges": []}

    findings, scan_violations = _scan_rules(repo_root)
    finding_by_fingerprint = {row["fingerprint"]: row for row in findings}
    current_set = frozenset(finding_by_fingerprint)
    new_fingerprints = tuple(sorted(current_set - expected_set))
    stale_fingerprints = tuple(sorted(expected_set - current_set))

    policy_violations: list[dict[str, Any]] = []
    for fingerprint in new_fingerprints:
        finding = finding_by_fingerprint[fingerprint]
        policy_violations.append(
            _violation(
                REASON_NEW_IMPORT,
                path=finding["path"],
                line=finding["lines"][0],
                fingerprint=fingerprint,
                message=f"new {finding['direction']} implementation import: {finding['symbol']}",
            )
        )
    for fingerprint in stale_fingerprints:
        policy_violations.append(
            _violation(
                REASON_STALE_BASELINE,
                path=_fingerprint_path(fingerprint),
                fingerprint=fingerprint,
                message="recorded debt disappeared; shrink the baseline and migration manifest intentionally",
            )
        )

    migration_violations, mapped_edges = _validate_migration_coverage(
        findings,
        migration.get("edges", ()),
    )
    policy_violations.extend(migration_violations)

    if mode_state.mode is GuardMode.ENFORCE:
        policy_violations.extend(
            _violation(
                REASON_ENFORCE_IMPORT,
                path=row["path"],
                line=row["lines"][0],
                fingerprint=row["fingerprint"],
                message=f"ENFORCE forbids {row['direction']} implementation import: {row['symbol']}",
            )
            for row in findings
        )

    configuration_violations.extend(scan_violations)
    all_violations = _dedupe_violations(
        [*configuration_violations, *policy_violations]
    )
    if mode_state.mode is GuardMode.REPORT:
        blocking = tuple(configuration_violations)
    else:
        blocking = tuple(all_violations)

    return {
        "policy_id": POLICY_ID,
        "schema_version": SCHEMA_VERSION,
        "mode": mode_state.mode.value,
        "mode_valid": mode_state.valid,
        "baseline_path": _display_path(baseline_file, repo_root),
        "baseline_fingerprints_sha256": baseline["fingerprints_sha256"],
        "current_fingerprints_sha256": fingerprint_digest(current_set),
        "finding_count": len(findings),
        "expected_finding_count": len(expected),
        "findings": findings,
        "existing_debt": [
            row for row in findings if row["fingerprint"] in expected_set
        ],
        "new_findings": [finding_by_fingerprint[item] for item in new_fingerprints],
        "stale_fingerprints": list(stale_fingerprints),
        "mapped_migration_edges": mapped_edges,
        "violations": all_violations,
        "blocking_violations": list(blocking),
        "passed": not blocking,
    }


def scan_repo(root: str | Path) -> list[dict[str, Any]]:
    """Delivery adapter pinned to FREEZE_NEW; environment cannot downgrade CI."""

    report = scan_report(root, mode=GuardMode.FREEZE_NEW, environ={})
    return list(report["blocking_violations"])


def fingerprint_digest(fingerprints: Iterable[str]) -> str:
    normalized = sorted({str(item) for item in fingerprints})
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def content_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _scan_rules(repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    for rule in RULES:
        source_root = (repo_root / rule.source_root).resolve()
        if not source_root.is_dir() or not _is_within(source_root, repo_root):
            violations.append(
                _violation(
                    REASON_SOURCE_ROOT_MISSING,
                    path=rule.source_root,
                    message=f"required source root missing: {rule.source_root}",
                )
            )
            continue
        for path in sorted(
            item
            for item in source_root.rglob("*")
            if item.is_file() and item.suffix.lower() in {".py", ".pyi"}
        ):
            if path.is_symlink() or not _is_within(path.resolve(), source_root):
                violations.append(
                    _violation(
                        REASON_SOURCE_UNREADABLE,
                        path=_display_path(path, repo_root),
                        message="symlinked or escaping Python source is not scanned",
                    )
                )
                continue
            rel = _display_path(path, repo_root)
            if any(rel.startswith(prefix) for prefix in rule.excluded_path_prefixes):
                continue
            try:
                source = path.read_text(encoding="utf-8-sig")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(source, filename=rel)
            except (OSError, UnicodeError) as exc:
                violations.append(
                    _violation(
                        REASON_SOURCE_UNREADABLE,
                        path=rel,
                        message=f"cannot read source: {type(exc).__name__}",
                    )
                )
                continue
            except SyntaxError as exc:
                violations.append(
                    _violation(
                        REASON_SOURCE_SYNTAX,
                        path=rel,
                        line=int(exc.lineno or 0),
                        message="source cannot be parsed for dependency enforcement",
                    )
                )
                continue
            collector = _ImportCollector(
                rule=rule,
                path=rel,
                source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            )
            collector.visit(tree)
            violations.extend(collector.violations)
            for row in collector.findings:
                fingerprint = row["fingerprint"]
                previous = findings.get(fingerprint)
                if previous is None:
                    findings[fingerprint] = row
                else:
                    previous["lines"] = sorted(
                        set(previous["lines"]) | set(row["lines"])
                    )
    ordered = [findings[key] for key in sorted(findings)]
    return ordered, _dedupe_violations(violations)


class _ImportCollector(ast.NodeVisitor):
    _EXEC_NAMES = frozenset({"exec", "eval", "compile"})
    _IMPORTLIB_UTIL_IMPORT_NAMES = frozenset({"find_loader", "find_spec"})
    _IMPORTLIB_UTIL_EXEC_NAMES = frozenset(
        {
            "module_from_spec",
            "spec_from_file_location",
            "spec_from_loader",
        }
    )
    _MACHINERY_EXEC_NAMES = frozenset(
        {
            "ExtensionFileLoader",
            "FileFinder",
            "SourceFileLoader",
            "SourcelessFileLoader",
        }
    )

    def __init__(
        self,
        *,
        rule: BoundaryRule,
        path: str,
        source_sha256: str,
    ) -> None:
        self.rule = rule
        self.path = path
        self.source_sha256 = source_sha256
        self.scope_stack: list[str] = []
        self.control_stack: list[str] = []
        self.findings: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self.importlib_aliases: set[str] = {"importlib"}
        self.importlib_util_aliases: set[str] = set()
        self.importlib_machinery_aliases: set[str] = set()
        self.runpy_aliases: set[str] = {"runpy"}
        self.pkgutil_aliases: set[str] = {"pkgutil"}
        self.pydoc_aliases: set[str] = {"pydoc"}
        self.zipimport_aliases: set[str] = {"zipimport"}
        self.import_module_aliases: set[str] = set()
        self.builtins_aliases: set[str] = {"builtins"}
        self.import_builtin_aliases: set[str] = {"__import__"}
        self.dynamic_exec_aliases: set[str] = set(self._EXEC_NAMES)
        self.dynamic_dictionary_aliases: dict[str, tuple[str, ...]] = {}
        self.string_constants: dict[str, str] = {}
        self.ambiguous_constants: set[str] = set()
        self.occurrences: dict[str, int] = {}

    def visit_Module(self, node: ast.Module) -> Any:
        walked = list(ast.walk(node))
        for child in walked:
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "importlib":
                        self.importlib_aliases.add(alias.asname or alias.name)
                    elif alias.name == "importlib.util":
                        if alias.asname:
                            self.importlib_util_aliases.add(alias.asname)
                        else:
                            self.importlib_aliases.add("importlib")
                    elif alias.name == "importlib.machinery":
                        if alias.asname:
                            self.importlib_machinery_aliases.add(alias.asname)
                        else:
                            self.importlib_aliases.add("importlib")
                    elif alias.name == "builtins":
                        self.builtins_aliases.add(alias.asname or alias.name)
                    elif alias.name == "runpy":
                        self.runpy_aliases.add(alias.asname or alias.name)
                    elif alias.name == "pkgutil":
                        self.pkgutil_aliases.add(alias.asname or alias.name)
                    elif alias.name == "pydoc":
                        self.pydoc_aliases.add(alias.asname or alias.name)
                    elif alias.name == "zipimport":
                        self.zipimport_aliases.add(alias.asname or alias.name)
            elif isinstance(child, ast.ImportFrom) and child.module == "importlib":
                for alias in child.names:
                    if alias.name in {"import_module", "*"}:
                        self.import_module_aliases.add(alias.asname or alias.name)
                        if alias.name == "*":
                            self.import_module_aliases.add("import_module")
                    elif alias.name == "util":
                        self.importlib_util_aliases.add(alias.asname or alias.name)
                    elif alias.name == "machinery":
                        self.importlib_machinery_aliases.add(alias.asname or alias.name)
            elif isinstance(child, ast.ImportFrom) and child.module == "importlib.util":
                for alias in child.names:
                    binding = alias.asname or alias.name
                    if alias.name in self._IMPORTLIB_UTIL_IMPORT_NAMES:
                        self.import_module_aliases.add(binding)
                    elif alias.name in self._IMPORTLIB_UTIL_EXEC_NAMES:
                        self.dynamic_exec_aliases.add(binding)
                    elif alias.name == "*":
                        self.import_module_aliases.update(
                            self._IMPORTLIB_UTIL_IMPORT_NAMES
                        )
                        self.dynamic_exec_aliases.update(
                            self._IMPORTLIB_UTIL_EXEC_NAMES
                        )
            elif (
                isinstance(child, ast.ImportFrom)
                and child.module == "importlib.machinery"
            ):
                for alias in child.names:
                    if alias.name in self._MACHINERY_EXEC_NAMES:
                        self.dynamic_exec_aliases.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        self.dynamic_exec_aliases.update(self._MACHINERY_EXEC_NAMES)
            elif isinstance(child, ast.ImportFrom) and child.module == "builtins":
                for alias in child.names:
                    if alias.name == "__import__":
                        self.import_builtin_aliases.add(alias.asname or alias.name)
                    elif alias.name in self._EXEC_NAMES:
                        self.dynamic_exec_aliases.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        self.import_builtin_aliases.add("__import__")
                        self.dynamic_exec_aliases.update(self._EXEC_NAMES)
            elif isinstance(child, ast.ImportFrom) and child.module == "runpy":
                for alias in child.names:
                    if alias.name in {"run_module", "*"}:
                        self.import_module_aliases.add(alias.asname or alias.name)
                        if alias.name == "*":
                            self.import_module_aliases.add("run_module")
                            self.dynamic_exec_aliases.add("run_path")
                    elif alias.name == "run_path":
                        self.dynamic_exec_aliases.add(alias.asname or alias.name)
            elif isinstance(child, ast.ImportFrom) and child.module == "pkgutil":
                for alias in child.names:
                    if alias.name in {"find_loader", "get_loader", "resolve_name", "*"}:
                        self.import_module_aliases.add(alias.asname or alias.name)
                        if alias.name == "*":
                            self.import_module_aliases.update(
                                {"find_loader", "get_loader", "resolve_name"}
                            )
            elif isinstance(child, ast.ImportFrom) and child.module == "pydoc":
                for alias in child.names:
                    if alias.name in {"locate", "safeimport", "*"}:
                        self.import_module_aliases.add(alias.asname or alias.name)
                        if alias.name == "*":
                            self.import_module_aliases.update({"locate", "safeimport"})
            elif isinstance(child, ast.ImportFrom) and child.module == "zipimport":
                for alias in child.names:
                    if alias.name in {"zipimporter", "*"}:
                        self.dynamic_exec_aliases.add(alias.asname or alias.name)
                        if alias.name == "*":
                            self.dynamic_exec_aliases.add("zipimporter")

        assignments: list[tuple[ast.AST, ast.AST]] = []
        for child in walked:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    assignments.extend(_assignment_bindings(child.value, target))
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                assignments.extend(_assignment_bindings(child.value, child.target))
            elif isinstance(child, ast.NamedExpr):
                assignments.extend(_assignment_bindings(child.value, child.target))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                positional = [*child.args.posonlyargs, *child.args.args]
                for argument, default in zip(
                    positional[-len(child.args.defaults) :],
                    child.args.defaults,
                ):
                    assignments.append((default, argument))
                for argument, default in zip(
                    child.args.kwonlyargs,
                    child.args.kw_defaults,
                ):
                    if default is not None:
                        assignments.append((default, argument))

        changed = True
        while changed:
            changed = False
            for value, target in assignments:
                resolved = self._resolved_text(value)
                if not isinstance(target, (ast.Name, ast.arg)):
                    continue
                target_name = target.id if isinstance(target, ast.Name) else target.arg
                if resolved is not None and target_name not in self.ambiguous_constants:
                    previous = self.string_constants.get(target_name)
                    if previous is None:
                        self.string_constants[target_name] = resolved
                        changed = True
                    elif previous != resolved:
                        self.string_constants.pop(target_name, None)
                        self.ambiguous_constants.add(target_name)
                        changed = True
                if (
                    self._is_import_module_reference(value)
                    and target_name not in self.import_module_aliases
                ):
                    self.import_module_aliases.add(target_name)
                    changed = True
                if (
                    self._is_dynamic_exec_reference(value)
                    and target_name not in self.dynamic_exec_aliases
                ):
                    self.dynamic_exec_aliases.add(target_name)
                    changed = True
                if isinstance(value, ast.Name):
                    for aliases in (
                        self.importlib_aliases,
                        self.importlib_util_aliases,
                        self.importlib_machinery_aliases,
                        self.builtins_aliases,
                        self.runpy_aliases,
                        self.pkgutil_aliases,
                        self.pydoc_aliases,
                        self.zipimport_aliases,
                    ):
                        if value.id in aliases and target_name not in aliases:
                            aliases.add(target_name)
                            changed = True
                    dictionary_owner = self.dynamic_dictionary_aliases.get(value.id)
                    if (
                        dictionary_owner is not None
                        and self.dynamic_dictionary_aliases.get(target_name)
                        != dictionary_owner
                    ):
                        self.dynamic_dictionary_aliases[target_name] = dictionary_owner
                        changed = True
                dictionary_owner = self._dictionary_owner_reference(value)
                if (
                    dictionary_owner is not None
                    and self._is_dynamic_owner_chain(dictionary_owner)
                    and self.dynamic_dictionary_aliases.get(target_name)
                    != dictionary_owner
                ):
                    self.dynamic_dictionary_aliases[target_name] = dictionary_owner
                    changed = True
        return super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        return self._visit_scope(node, f"function:{node.name}")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        return self._visit_scope(node, f"async_function:{node.name}")

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        return self._visit_scope(node, f"class:{node.name}")

    def _visit_scope(self, node: ast.AST, label: str) -> None:
        self.scope_stack.append(label)
        self.generic_visit(node)
        self.scope_stack.pop()

    def _visit_statements(self, label: str, statements: Sequence[ast.stmt]) -> None:
        if not statements:
            return
        self.control_stack.append(label)
        for statement in statements:
            self.visit(statement)
        self.control_stack.pop()

    def visit_If(self, node: ast.If) -> Any:
        self.visit(node.test)
        label = (
            "if:TYPE_CHECKING"
            if _is_type_checking_test(node.test)
            else f"if:{_ast_digest(node.test)}"
        )
        self._visit_statements(label, node.body)
        self._visit_statements(f"else:{label}", node.orelse)

    def visit_Try(self, node: ast.Try) -> Any:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> Any:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        self._visit_statements("try", node.body)
        for handler in node.handlers:
            if handler.type is not None:
                self.visit(handler.type)
            self._visit_statements("except", handler.body)
        self._visit_statements("try_else", node.orelse)
        self._visit_statements("finally", node.finalbody)

    def visit_With(self, node: ast.With) -> Any:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        self._visit_statements("with", node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> Any:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        self._visit_statements("async_with", node.body)

    def visit_For(self, node: ast.For) -> Any:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_statements("for", node.body)
        self._visit_statements("for_else", node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> Any:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_statements("async_for", node.body)
        self._visit_statements("async_for_else", node.orelse)

    def visit_While(self, node: ast.While) -> Any:
        self.visit(node.test)
        label = f"while:{_ast_digest(node.test)}"
        self._visit_statements(label, node.body)
        self._visit_statements(f"else:{label}", node.orelse)

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            if _rule_matches(alias.name, self.rule):
                self._add(
                    "import",
                    alias.name,
                    alias.name,
                    alias.asname or alias.name,
                    node.lineno,
                )
        return self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = str(node.module or "")
        if _rule_matches(module, self.rule, relative=bool(node.level)):
            for alias in node.names:
                symbol = module if alias.name == "*" else f"{module}.{alias.name}"
                self._add(
                    "from",
                    module,
                    symbol,
                    alias.asname or alias.name,
                    node.lineno,
                )
        return self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        if self._contains_dynamic_callable(node.value) and not all(
            self._assignment_callable_is_trackable(node.value, target)
            for target in node.targets
        ):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through an untracked assignment",
            )
        return self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if (
            node.value is not None
            and self._contains_dynamic_callable(node.value)
            and not self._assignment_callable_is_trackable(node.value, node.target)
        ):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through an untracked assignment",
            )
        return self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> Any:
        if node.value is not None and self._contains_dynamic_callable(node.value):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through a return value",
            )
        return self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> Any:
        if node.value is not None and self._contains_dynamic_callable(node.value):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through a yielded value",
            )
        return self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> Any:
        if self._contains_dynamic_callable(node.value):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through a yielded value",
            )
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        dynamic_exec = self._is_dynamic_exec_reference(node.func)
        dynamic_import = self._is_dynamic_import_call(node.func)
        if dynamic_exec:
            self._record_unresolved_dynamic(
                node,
                "dynamic code execution can bypass the contracts-only boundary",
            )
        elif not dynamic_import and self._is_unresolved_dynamic_callable(node.func):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable is not statically resolvable",
            )
        elif not dynamic_import and not dynamic_exec and any(
            self._is_import_module_reference(argument)
            or self._is_dynamic_exec_reference(argument)
            or self._is_unresolved_dynamic_callable(argument)
            for argument in [*node.args, *(item.value for item in node.keywords)]
        ):
            self._record_unresolved_dynamic(
                node,
                "dynamic loader callable escapes through a higher-order call",
            )
        if self.rule.direction == "EDGE_TO_HOST":
            path_literal = self._host_path_literal(node)
            if path_literal is not None:
                self._add(
                    "path",
                    "host_brain.__filesystem__",
                    "host_brain.__filesystem__",
                    path_literal,
                    node.lineno,
                )
        if dynamic_import:
            module = self._dynamic_target(node)
            if module and _rule_matches(module, self.rule):
                self._add("dynamic", module, module, "", node.lineno)
            elif module is None:
                self._record_unresolved_dynamic(
                    node,
                    "dynamic import target is not statically resolvable",
                )
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        if self.rule.direction == "EDGE_TO_HOST" and isinstance(node.op, ast.Div):
            value = self._resolved_text(node.right)
            if value is not None and _contains_path_segment(value, "host_brain"):
                self._add(
                    "path",
                    "host_brain.__filesystem__",
                    "host_brain.__filesystem__",
                    value,
                    node.lineno,
                )
        return self.generic_visit(node)

    def _record_unresolved_dynamic(self, node: ast.AST, message: str) -> None:
        if self.rule.direction == "HOST_TO_EDGE":
            self._add(
                "dynamic_risk",
                "edge_client.__dynamic_risk__",
                "edge_client.__dynamic_risk__",
                f"sha256:{self.source_sha256}",
                node.lineno,
            )
            return
        if self.rule.source_root in {"edge_client", "natsume_contracts"}:
            self.violations.append(
                _violation(
                    REASON_DYNAMIC_UNRESOLVED,
                    path=self.path,
                    line=node.lineno,
                    message=message,
                )
            )

    def _is_dynamic_import_call(self, func: ast.AST) -> bool:
        return self._is_import_module_reference(func)

    def _contains_dynamic_callable(self, value: ast.AST) -> bool:
        if self._is_import_module_reference(value) or self._is_dynamic_exec_reference(value):
            return True
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return any(self._contains_dynamic_callable(item) for item in value.elts)
        if isinstance(value, ast.Dict):
            return any(
                item is not None and self._contains_dynamic_callable(item)
                for item in [*value.keys, *value.values]
            )
        if isinstance(value, ast.IfExp):
            return self._contains_dynamic_callable(
                value.body
            ) or self._contains_dynamic_callable(value.orelse)
        if isinstance(value, ast.Lambda):
            return self._contains_dynamic_callable(value.body)
        return False

    def _assignment_callable_is_trackable(
        self,
        value: ast.AST,
        target: ast.AST,
    ) -> bool:
        if self.scope_stack and self.scope_stack[-1].startswith("class:"):
            return False
        bindings = _assignment_bindings(value, target)
        dynamic_bindings = [
            (bound_value, bound_target)
            for bound_value, bound_target in bindings
            if self._contains_dynamic_callable(bound_value)
        ]
        return bool(dynamic_bindings) and all(
            isinstance(bound_target, (ast.Name, ast.arg))
            and (
                self._is_import_module_reference(bound_value)
                or self._is_dynamic_exec_reference(bound_value)
            )
            for bound_value, bound_target in dynamic_bindings
        )

    def _is_unresolved_dynamic_callable(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Call) and _is_getattr_call(value):
            owner_chain = _attribute_chain(value.args[0])
            return self._is_dynamic_owner_chain(owner_chain) and (
                self._resolved_text(value.args[1]) is None
            )
        dictionary_owner, dictionary_key = self._dictionary_lookup(value)
        if dictionary_owner is not None and dictionary_key is None:
            return self._is_dynamic_owner_chain(dictionary_owner)
        reflective_owner, reflective_key = self._reflective_lookup(value)
        if reflective_owner is not None and reflective_key is None:
            return self._is_dynamic_owner_chain(reflective_owner)
        return bool(
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id in {*self.builtins_aliases, "__builtins__"}
            and self._resolved_text(value.slice) is None
        )

    def _is_import_module_reference(self, value: ast.AST) -> bool:
        if isinstance(value, ast.NamedExpr):
            return self._is_import_module_reference(value.value)
        if isinstance(value, ast.Name):
            return (
                value.id in self.import_module_aliases
                or value.id in self.import_builtin_aliases
            )
        chain = _attribute_chain(value)
        if self._is_import_chain(chain):
            return True
        if isinstance(value, ast.Call) and _is_getattr_call(value):
            attribute = self._resolved_text(value.args[1])
            owner_chain = _attribute_chain(value.args[0])
            return bool(
                attribute is not None
                and self._is_import_chain((*owner_chain, attribute))
            )
        dictionary_owner, dictionary_key = self._dictionary_lookup(value)
        if dictionary_owner is not None and dictionary_key is not None:
            return self._is_import_chain((*dictionary_owner, dictionary_key))
        reflective_owner, reflective_key = self._reflective_lookup(value)
        if reflective_owner is not None and reflective_key is not None:
            return self._is_import_chain((*reflective_owner, reflective_key))
        if isinstance(value, ast.Subscript) and self._contains_dynamic_callable(
            value.value
        ):
            return True
        return bool(
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id in {*self.builtins_aliases, "__builtins__"}
            and self._resolved_text(value.slice) == "__import__"
        )

    def _is_dynamic_exec_reference(self, value: ast.AST) -> bool:
        if isinstance(value, ast.NamedExpr):
            return self._is_dynamic_exec_reference(value.value)
        if isinstance(value, ast.Name):
            return value.id in self.dynamic_exec_aliases
        if isinstance(value, ast.Attribute) and value.attr in {
            "exec_module",
            "load_module",
        }:
            return True
        chain = _attribute_chain(value)
        if self._is_exec_chain(chain):
            return True
        if isinstance(value, ast.Call) and _is_getattr_call(value):
            attribute = self._resolved_text(value.args[1])
            owner_chain = _attribute_chain(value.args[0])
            return bool(
                attribute is not None
                and self._is_exec_chain((*owner_chain, attribute))
            )
        dictionary_owner, dictionary_key = self._dictionary_lookup(value)
        if dictionary_owner is not None and dictionary_key is not None:
            return self._is_exec_chain((*dictionary_owner, dictionary_key))
        reflective_owner, reflective_key = self._reflective_lookup(value)
        if reflective_owner is not None and reflective_key is not None:
            return self._is_exec_chain((*reflective_owner, reflective_key))
        if isinstance(value, ast.Subscript) and self._contains_dynamic_callable(
            value.value
        ):
            return True
        return bool(
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id in {*self.builtins_aliases, "__builtins__"}
            and self._resolved_text(value.slice) in self._EXEC_NAMES
        )

    def _is_import_chain(self, chain: Sequence[str]) -> bool:
        if len(chain) < 2:
            return False
        root, leaf = chain[0], chain[-1]
        if root in self.importlib_aliases:
            return (len(chain) == 2 and leaf in {"__import__", "import_module"}) or (
                len(chain) >= 3
                and chain[-2] == "util"
                and leaf in self._IMPORTLIB_UTIL_IMPORT_NAMES
            )
        if root in self.importlib_util_aliases:
            return leaf in self._IMPORTLIB_UTIL_IMPORT_NAMES
        if root in {*self.builtins_aliases, "__builtins__"}:
            return leaf == "__import__"
        if root in self.runpy_aliases:
            return leaf == "run_module"
        if root in self.pkgutil_aliases:
            return leaf in {"find_loader", "get_loader", "resolve_name"}
        if root in self.pydoc_aliases:
            return leaf in {"locate", "safeimport"}
        return False

    def _is_exec_chain(self, chain: Sequence[str]) -> bool:
        if len(chain) < 2:
            return False
        root, leaf = chain[0], chain[-1]
        if root in {*self.builtins_aliases, "__builtins__"}:
            return leaf in self._EXEC_NAMES
        if root in self.runpy_aliases and leaf == "run_path":
            return True
        if root in self.importlib_aliases and len(chain) >= 3:
            if chain[-2] == "util" and leaf in self._IMPORTLIB_UTIL_EXEC_NAMES:
                return True
            if chain[-2] == "machinery" and leaf in self._MACHINERY_EXEC_NAMES:
                return True
        if root in self.importlib_util_aliases:
            return leaf in self._IMPORTLIB_UTIL_EXEC_NAMES
        if root in self.importlib_machinery_aliases:
            return leaf in self._MACHINERY_EXEC_NAMES
        if root in self.zipimport_aliases:
            return leaf == "zipimporter"
        return leaf in {"exec_module", "load_module"}

    def _is_dynamic_owner_chain(self, chain: Sequence[str]) -> bool:
        if not chain:
            return False
        root = chain[0]
        return root in {
            *self.importlib_aliases,
            *self.importlib_util_aliases,
            *self.importlib_machinery_aliases,
            *self.builtins_aliases,
            *self.runpy_aliases,
            *self.pkgutil_aliases,
            *self.pydoc_aliases,
            *self.zipimport_aliases,
            "__builtins__",
        }

    def _dictionary_lookup(
        self,
        value: ast.AST,
    ) -> tuple[tuple[str, ...] | None, str | None]:
        container: ast.AST | None = None
        key_node: ast.AST | None = None
        if isinstance(value, ast.Subscript):
            container = value.value
            key_node = value.slice
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get"
            and value.args
        ):
            container = value.func.value
            key_node = value.args[0]
        if container is None:
            return None, None
        if isinstance(container, ast.Name):
            owner = self.dynamic_dictionary_aliases.get(container.id)
            if owner is not None:
                return owner, self._resolved_text(key_node)
        if isinstance(container, ast.Name) and container.id == "__builtins__":
            return (container.id,), self._resolved_text(key_node)
        if (
            isinstance(container, ast.Attribute)
            and container.attr == "__dict__"
        ):
            owner_chain = _attribute_chain(container.value)
            if owner_chain:
                return owner_chain, self._resolved_text(key_node)
        return None, None

    def _dictionary_owner_reference(
        self,
        value: ast.AST,
    ) -> tuple[str, ...] | None:
        if isinstance(value, ast.Attribute) and value.attr == "__dict__":
            chain = _attribute_chain(value.value)
            return chain or None
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "vars"
            and value.args
        ):
            chain = _attribute_chain(value.args[0])
            return chain or None
        return None

    def _reflective_lookup(
        self,
        value: ast.AST,
    ) -> tuple[tuple[str, ...] | None, str | None]:
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "__getattribute__"
            and value.args
        ):
            owner_chain = _attribute_chain(value.func.value)
            if owner_chain:
                return owner_chain, self._resolved_text(value.args[0])
        return None, None

    def _host_path_literal(self, node: ast.Call) -> str | None:
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        arguments = [*node.args, *(item.value for item in node.keywords)]
        for argument in arguments:
            value = self._resolved_text(argument)
            if value is None or not _contains_path_segment(value, "host_brain"):
                continue
            normalized = value.replace("\\", "/").lstrip("./")
            if normalized != "host_brain" or function_name in {
                "Path",
                "open",
                "join",
                "joinpath",
            }:
                return value
        return None

    def _dynamic_target(self, node: ast.Call) -> str | None:
        name_node = _call_argument_any(
            node,
            0,
            ("name", "mod_name", "fullname", "path"),
        )
        name = self._resolved_text(name_node)
        if name is None:
            return None
        if not name.startswith("."):
            return name
        package_node = _call_argument(node, 1, "package")
        package = self._resolved_text(package_node)
        if not package:
            return None
        levels = len(name) - len(name.lstrip("."))
        suffix = name[levels:]
        parts = package.split(".")
        if levels > len(parts):
            return None
        base = parts[: len(parts) - levels + 1]
        if suffix:
            base.append(suffix)
        return ".".join(base)

    def _resolved_text(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id not in self.ambiguous_constants:
            return self.string_constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._resolved_text(node.left)
            right = self._resolved_text(node.right)
            if left is not None and right is not None:
                return left + right
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    continue
                if not isinstance(value, ast.FormattedValue):
                    return None
                if value.conversion not in {-1, ord("s")} or value.format_spec is not None:
                    return None
                resolved = self._resolved_text(value.value)
                if resolved is None:
                    return None
                parts.append(resolved)
            return "".join(parts)
        if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Constant):
            branch = node.body if bool(node.test.value) else node.orelse
            return self._resolved_text(branch)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            template = self._resolved_text(node.func.value)
            values = [self._resolved_text(item) for item in node.args]
            keywords = {
                str(item.arg): self._resolved_text(item.value)
                for item in node.keywords
                if item.arg is not None
            }
            if (
                template is not None
                and all(item is not None for item in values)
                and all(item is not None for item in keywords.values())
                and len(keywords) == len(node.keywords)
            ):
                try:
                    return template.format(*values, **keywords)
                except (IndexError, KeyError, ValueError):
                    return None
        return None

    def _add(
        self,
        kind: str,
        module: str,
        symbol: str,
        binding: str,
        line: int,
    ) -> None:
        scope = "/".join(self.scope_stack) if self.scope_stack else "<module>"
        context = "/".join(self.control_stack) if self.control_stack else "eager"
        base = "|".join(
            (
                self.rule.direction,
                self.path,
                scope,
                context,
                kind,
                symbol,
                binding,
            )
        )
        occurrence = self.occurrences.get(base, 0) + 1
        self.occurrences[base] = occurrence
        fingerprint = f"{base}|{occurrence}"
        self.findings.append(
            {
                "direction": self.rule.direction,
                "path": self.path,
                "scope": scope,
                "scope_kind": (
                    self.scope_stack[-1].split(":", 1)[0]
                    if self.scope_stack
                    else "module"
                ),
                "context": context,
                "kind": kind,
                "module": module,
                "symbol": symbol,
                "binding": binding,
                "occurrence": occurrence,
                "fingerprint": fingerprint,
                "lines": [int(line)],
            }
        )


def _load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    required = {
        "schema_version",
        "policy_id",
        "baseline_commit",
        "expected_fingerprints",
        "fingerprints_sha256",
        "migration_manifest",
        "migration_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("baseline fields are not exact")
    if payload["schema_version"] != SCHEMA_VERSION or payload["policy_id"] != POLICY_ID:
        raise ValueError("baseline schema or policy id mismatch")
    fingerprints = payload["expected_fingerprints"]
    if (
        not isinstance(fingerprints, list)
        or any(not isinstance(item, str) or not item for item in fingerprints)
        or fingerprints != sorted(set(fingerprints))
    ):
        raise ValueError("expected_fingerprints must be sorted and unique")
    if not _DIGEST_RE.fullmatch(str(payload["fingerprints_sha256"])):
        raise ValueError("fingerprints_sha256 is invalid")
    if not _DIGEST_RE.fullmatch(str(payload["migration_sha256"])):
        raise ValueError("migration_sha256 is invalid")
    _safe_relative_path(str(payload["migration_manifest"]))
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["baseline_commit"])):
        raise ValueError("baseline_commit must be a full commit hash")
    return payload


def _load_migration_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    required = {"schema_version", "policy_id", "edges"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("migration manifest fields are not exact")
    if payload["schema_version"] != SCHEMA_VERSION or payload["policy_id"] != POLICY_ID:
        raise ValueError("migration manifest schema or policy id mismatch")
    edges = payload["edges"]
    if not isinstance(edges, list):
        raise ValueError("migration manifest edges must be a list")
    required_edge = {
        "edge_id",
        "direction",
        "source_path",
        "target_module",
        "owner",
        "reason",
        "migration_target",
        "remove_by",
        "priority",
    }
    seen: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != required_edge:
            raise ValueError("migration edge fields are not exact")
        edge_id = str(edge["edge_id"])
        if not _SAFE_ID_RE.fullmatch(edge_id) or edge_id in seen:
            raise ValueError("migration edge_id is invalid or duplicate")
        seen.add(edge_id)
        if edge["direction"] not in {rule.direction for rule in RULES}:
            raise ValueError("migration edge direction is invalid")
        _safe_relative_path(str(edge["source_path"]))
        if not _SAFE_MODULE_RE.fullmatch(str(edge["target_module"])):
            raise ValueError("migration target_module is invalid")
        for field in ("owner", "reason", "migration_target", "remove_by"):
            if not isinstance(edge[field], str) or not edge[field].strip():
                raise ValueError(f"migration {field} is required")
        if edge["remove_by"] != BOUNDARY_REMOVAL_TASK:
            raise ValueError(
                f"migration remove_by must be {BOUNDARY_REMOVAL_TASK}"
            )
        if edge["priority"] not in {"P0", "P1", "P2"}:
            raise ValueError("migration priority is invalid")
    return payload


def _validate_migration_coverage(
    findings: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    violations: list[dict[str, Any]] = []
    used: set[str] = set()
    for finding in findings:
        matches = [
            edge
            for edge in edges
            if edge.get("direction") == finding["direction"]
            and edge.get("source_path") == finding["path"]
            and _module_matches(finding["module"], str(edge.get("target_module") or ""))
        ]
        if len(matches) != 1:
            violations.append(
                _violation(
                    REASON_UNMAPPED_DEBT,
                    path=finding["path"],
                    line=finding["lines"][0],
                    fingerprint=finding["fingerprint"],
                    message="each implementation import must map to exactly one migration edge",
                )
            )
            continue
        used.add(str(matches[0]["edge_id"]))
    for edge in edges:
        edge_id = str(edge.get("edge_id") or "")
        if edge_id not in used:
            violations.append(
                _violation(
                    REASON_STALE_MIGRATION,
                    path=str(edge.get("source_path") or ""),
                    message=f"migration edge no longer maps current debt: {edge_id}",
                )
            )
    return violations, sorted(used)


def _resolve_manifest_path(baseline_path: Path, relative: str) -> Path:
    candidate = (baseline_path.parent / relative).resolve()
    if not _is_within(candidate, baseline_path.parent.resolve()):
        raise ValueError("migration manifest escapes baseline directory")
    return candidate


def _safe_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ValueError("path must be a safe exact relative path")


def _module_matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _rule_matches(
    module: str,
    rule: BoundaryRule,
    *,
    relative: bool = False,
) -> bool:
    if relative or not module:
        return False
    if rule.forbidden_prefix != "*":
        return _module_matches(module, rule.forbidden_prefix)
    root = module.split(".", 1)[0]
    if root in _CONTRACT_ALLOWED_IMPORT_ROOTS:
        return False
    return not any(
        candidate.source_root == rule.source_root
        and candidate.forbidden_prefix != "*"
        and _module_matches(module, candidate.forbidden_prefix)
        for candidate in RULES
    )


def _assignment_bindings(value: ast.AST, target: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    if (
        isinstance(value, (ast.Tuple, ast.List))
        and isinstance(target, (ast.Tuple, ast.List))
        and len(value.elts) == len(target.elts)
    ):
        bindings: list[tuple[ast.AST, ast.AST]] = []
        for child_value, child_target in zip(value.elts, target.elts):
            bindings.extend(_assignment_bindings(child_value, child_target))
        return bindings
    return [(value, target)]


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_chain(node.value)
        if parent:
            return (*parent, node.attr)
    return ()


def _ast_digest(node: ast.AST) -> str:
    normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _call_argument(node: ast.Call, position: int, keyword: str) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    for item in node.keywords:
        if item.arg == keyword:
            return item.value
    return None


def _call_argument_any(
    node: ast.Call,
    position: int,
    keywords: Sequence[str],
) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    accepted = frozenset(keywords)
    for item in node.keywords:
        if item.arg in accepted:
            return item.value
    return None


def _contains_path_segment(value: str, segment: str) -> bool:
    normalized = value.replace("\\", "/").strip("./")
    return segment in {part for part in normalized.split("/") if part}


def _is_getattr_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    )


def _is_type_checking_test(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    return bool(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"typing", "t"}
        and node.attr == "TYPE_CHECKING"
    )


def _violation(
    reason_code: str,
    *,
    path: str,
    message: str,
    line: int = 0,
    fingerprint: str = "",
) -> dict[str, Any]:
    return {
        "rule_id": POLICY_ID,
        "reason_code": reason_code,
        "path": path,
        "line": int(line),
        "fingerprint": fingerprint,
        "message": message,
    }


def _dedupe_violations(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("reason_code") or ""),
            str(row.get("path") or ""),
            int(row.get("line") or 0),
            str(row.get("fingerprint") or ""),
        )
        unique[key] = row
    return [unique[key] for key in sorted(unique)]


def _fingerprint_path(fingerprint: str) -> str:
    parts = fingerprint.split("|", 4)
    return parts[1] if len(parts) == 5 else ""


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce the Edge/Host contracts-only boundary.")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--baseline", default=None, help="Optional exact baseline path")
    parser.add_argument("--mode", choices=[item.value for item in GuardMode], default=None)
    parser.add_argument("--inventory", action="store_true", help="Print raw current findings")
    args = parser.parse_args(argv)
    if args.inventory:
        report = inventory_report(args.root)
    else:
        report = scan_report(
            args.root,
            baseline_path=args.baseline,
            mode=args.mode,
            environ=os.environ,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.inventory:
        return 2 if report["scan_violations"] else 0
    configuration_reasons = {
        REASON_MODE_INVALID,
        REASON_BASELINE_INVALID,
        REASON_MIGRATION_INVALID,
        REASON_SOURCE_ROOT_MISSING,
        REASON_SOURCE_UNREADABLE,
        REASON_SOURCE_SYNTAX,
        REASON_DYNAMIC_UNRESOLVED,
    }
    if any(
        row.get("reason_code") in configuration_reasons
        for row in report["blocking_violations"]
    ):
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BASELINE",
    "GuardMode",
    "GuardModeState",
    "MODE_ENV",
    "POLICY_ID",
    "content_digest",
    "fingerprint_digest",
    "inventory_report",
    "load_guard_mode",
    "scan_report",
    "scan_repo",
]
