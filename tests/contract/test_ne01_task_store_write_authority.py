from __future__ import annotations

import ast
from functools import lru_cache
import inspect
from pathlib import Path
import re
import warnings

from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_acceptance_effect_staging import (
    TaskAcceptanceEffectStagingMixin,
)
from host_brain.persistence.task_dag_state_store import TaskDagStateStoreMixin


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "host_brain"
GRAPH_INSERT_HOOK = "_insert_graph_from_host_command_in_transaction"
EFFECT_STAGE_HOOK = "_stage_acceptance_effect_intent_in_transaction"


class _AttributeCallVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.function_stack: list[str] = []
        self.calls: dict[
            str,
            list[tuple[Path, int, str | None, str, str | None]],
        ] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            self.calls.setdefault(node.func.attr, []).append(
                (
                    self.path,
                    node.lineno,
                    self.function_stack[-1] if self.function_stack else None,
                    ast.unparse(node.func.value),
                    ast.unparse(node.args[0]) if node.args else None,
                )
            )
        self.generic_visit(node)


@lru_cache(maxsize=1)
def _production_syntax_trees() -> tuple[tuple[Path, ast.Module], ...]:
    trees: list[tuple[Path, ast.Module]] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        trees.append((path, tree))
    return tuple(trees)


@lru_cache(maxsize=1)
def _production_attribute_call_inventory(
) -> dict[
    str,
    tuple[tuple[Path, int, str | None, str, str | None], ...],
]:
    calls: dict[
        str,
        list[tuple[Path, int, str | None, str, str | None]],
    ] = {}
    for path, tree in _production_syntax_trees():
        visitor = _AttributeCallVisitor(path=path.relative_to(ROOT))
        visitor.visit(tree)
        for attribute, entries in visitor.calls.items():
            calls.setdefault(attribute, []).extend(entries)
    return {
        attribute: tuple(sorted(entries))
        for attribute, entries in calls.items()
    }


def _production_attribute_calls(
    attribute: str,
) -> list[tuple[Path, int, str | None, str, str | None]]:
    return list(_production_attribute_call_inventory().get(attribute, ()))


def test_task_store_exposes_no_standalone_graph_or_effect_staging_authority() -> None:
    assert not hasattr(DurableTaskStore, "create_graph")
    assert not hasattr(DurableTaskStore, "insert_graph_in_transaction")
    assert not hasattr(DurableTaskStore, "stage_acceptance_effect_intent")
    assert hasattr(DurableTaskStore, GRAPH_INSERT_HOOK)
    assert hasattr(DurableTaskStore, EFFECT_STAGE_HOOK)

    assert _production_attribute_calls("create_graph") == []
    assert _production_attribute_calls("insert_graph_in_transaction") == []
    assert _production_attribute_calls("stage_acceptance_effect_intent") == []


def test_host_command_repository_is_the_only_graph_insert_hook_caller() -> None:
    calls = _production_attribute_calls(GRAPH_INSERT_HOOK)

    assert len(calls) == 1
    path, _line, owner, receiver, first_arg = calls[0]
    assert path == Path("host_brain/persistence/host_command_repository.py")
    assert owner == "_admit_normalized"
    assert receiver == "self._task_store"
    assert first_arg == "conn"


def test_effect_insert_hook_remains_inside_canonical_acceptance_uows() -> None:
    callers = {
        (path, owner, receiver, first_arg)
        for path, _line, owner, receiver, first_arg in _production_attribute_calls(
            EFFECT_STAGE_HOOK
        )
    }

    assert callers == {
        (
            Path("host_brain/persistence/task_acceptance_effect_staging.py"),
            "transition_graph_and_stage_acceptance_effects",
            "self",
            "conn",
        ),
        (
            Path("host_brain/persistence/task_node_artifact_store.py"),
            "commit_node_acceptance",
            "self",
            "conn",
        ),
        (
            Path("host_brain/persistence/task_parallel_success_store.py"),
            "complete_parallel_graph_success",
            "self",
            "conn",
        ),
    }
    assert len(_production_attribute_calls(EFFECT_STAGE_HOOK)) == 6


def test_private_write_hooks_borrow_the_owner_transaction() -> None:
    for method in (
        TaskDagStateStoreMixin._insert_graph_from_host_command_in_transaction,
        TaskAcceptanceEffectStagingMixin._stage_acceptance_effect_intent_in_transaction,
    ):
        source = inspect.getsource(method)
        assert "self._transaction" not in source
        assert "sqlite3.connect" not in source
        assert ".commit(" not in source
        assert ".rollback(" not in source


def test_canonical_insert_sql_has_one_persistence_owner() -> None:
    table_owners: dict[str, set[tuple[Path, str | None]]] = {
        "task_runtime_graph": set(),
        "task_runtime_node": set(),
        "task_runtime_acceptance_effect_intent": set(),
    }
    insert_pattern = re.compile(
        r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+"
        r"(task_runtime_graph|task_runtime_node|"
        r"task_runtime_acceptance_effect_intent)\b",
        re.IGNORECASE,
    )
    for path, tree in _production_syntax_trees():
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for match in insert_pattern.finditer(node.value):
                owner: str | None = None
                current: ast.AST = node
                while current in parents:
                    current = parents[current]
                    if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner = current.name
                        break
                table_owners[match.group(1).lower()].add(
                    (path.relative_to(ROOT), owner)
                )

    assert table_owners == {
        "task_runtime_graph": {
            (
                Path("host_brain/persistence/task_dag_state_store.py"),
                GRAPH_INSERT_HOOK,
            )
        },
        "task_runtime_node": {
            (
                Path("host_brain/persistence/task_dag_state_store.py"),
                GRAPH_INSERT_HOOK,
            )
        },
        "task_runtime_acceptance_effect_intent": {
            (
                Path("host_brain/persistence/task_acceptance_effect_staging.py"),
                EFFECT_STAGE_HOOK,
            )
        },
    }


def test_production_does_not_import_test_only_write_fixtures() -> None:
    violations: list[Path] = []
    for path, tree in _production_syntax_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "tests.task_runtime_authority_fixtures"
            ):
                violations.append(path.relative_to(ROOT))
            elif isinstance(node, ast.Import) and any(
                alias.name == "tests.task_runtime_authority_fixtures"
                for alias in node.names
            ):
                violations.append(path.relative_to(ROOT))

    assert violations == []


def test_business_layers_do_not_write_task_runtime_tables_directly() -> None:
    write_pattern = re.compile(
        r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|"
        r"REPLACE\s+INTO)\s+task_runtime_",
        re.IGNORECASE,
    )
    bypasses = [
        path.relative_to(ROOT)
        for path in PRODUCTION_ROOT.rglob("*.py")
        if "persistence" not in path.relative_to(PRODUCTION_ROOT).parts
        and write_pattern.search(path.read_text(encoding="utf-8-sig"))
    ]

    assert bypasses == []
