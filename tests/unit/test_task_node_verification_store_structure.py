from __future__ import annotations

import ast
import inspect
from pathlib import Path

from host_brain.persistence import task_node_verification_common
from host_brain.persistence import task_node_verification_store
from host_brain.persistence import task_node_verification_terminal_store
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_node_verification_store import (
    TaskNodeVerificationStoreMixin,
)
from host_brain.persistence.task_node_verification_terminal_store import (
    TaskNodeVerificationTerminalStoreMixin,
)


ROOT = Path(__file__).resolve().parents[2]


def _method(module: object, owner: str, method: str) -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(module))
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == owner
    )
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method
    )


def _self_calls(method: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == name
    ]


def test_0133_schema_is_append_only_and_attempt_bound() -> None:
    source = (
        ROOT
        / "host_brain"
        / "audit"
        / "migrations"
        / "0133_task_node_verification_decision.sql"
    ).read_text(encoding="utf-8")
    required = (
        "task_runtime_node_verification_decision",
        "attempt_id TEXT NOT NULL",
        "node_version_after = node_version_before + 1",
        "result_sha256",
        "node_outbox_id TEXT NOT NULL UNIQUE",
        "trg_task_runtime_node_verification_no_update",
        "trg_task_runtime_node_verification_no_delete",
        "FOREIGN KEY(attempt_id, graph_id, graph_revision, node_id)",
    )
    assert all(token in source for token in required)


def test_verification_store_is_split_and_wired_into_canonical_task_store() -> None:
    assert len(inspect.getsource(task_node_verification_common).splitlines()) < 220
    assert len(inspect.getsource(task_node_verification_store).splitlines()) < 650
    assert (
        len(
            inspect.getsource(
                task_node_verification_terminal_store
            ).splitlines()
        )
        < 450
    )
    assert issubclass(DurableTaskStore, TaskNodeVerificationStoreMixin)
    assert issubclass(
        DurableTaskStore,
        TaskNodeVerificationTerminalStoreMixin,
    )


def test_verification_and_direct_failure_each_own_one_transaction() -> None:
    commit = _method(
        task_node_verification_store,
        "TaskNodeVerificationStoreMixin",
        "commit_node_verification",
    )
    terminal = _method(
        task_node_verification_terminal_store,
        "TaskNodeVerificationTerminalStoreMixin",
        "finalize_node_verification_failure",
    )
    assert len(_self_calls(commit, "_transaction")) == 1
    assert len(_self_calls(commit, "_transition_node_in_transaction")) == 1
    assert len(_self_calls(commit, "_insert_node_verification_decision")) == 1
    assert len(_self_calls(terminal, "_transaction")) == 1
    assert len(_self_calls(terminal, "_transition_node_in_transaction")) == 1
    assert len(_self_calls(terminal, "_transition_graph_in_transaction")) == 1
    assert len(_self_calls(terminal, "_insert_node_verification_decision")) == 1
    assert not _self_calls(terminal, "transition_node")
    assert not _self_calls(terminal, "transition_graph")
    assert not _self_calls(terminal, "transition_budget")
