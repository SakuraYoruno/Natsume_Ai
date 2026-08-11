from __future__ import annotations

import ast
import inspect
from pathlib import Path

from host_brain.persistence import task_review_denial_store
from host_brain.persistence import task_review_denial_validation
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_review_denial_store import (
    TaskReviewDenialStoreMixin,
)
from host_brain.task_runtime import dag_scheduling_runtime
from host_brain.task_runtime import review_denial_lifecycle


ROOT = Path(__file__).resolve().parents[2]


def _method(
    module: object,
    owner: str,
    method: str,
) -> ast.FunctionDef:
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


def _calls(method: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == name
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        )
    ]


def test_0135_schema_freezes_review_and_three_terminal_outboxes() -> None:
    source = (
        ROOT
        / "host_brain"
        / "audit"
        / "migrations"
        / "0135_task_review_denial_settlement.sql"
    ).read_text(encoding="utf-8")
    required = (
        "task_runtime_review_denial_settlement",
        "review_status_version INTEGER NOT NULL",
        "review_sha256 TEXT NOT NULL",
        "node_version_after = node_version_before + 1",
        "graph_version_after = graph_version_before + 1",
        "budget_version_after = budget_version_before + 1",
        "acceptance_ref TEXT",
        "attempt_id TEXT",
        "blocking_decision_ref TEXT",
        "node_outbox_id TEXT NOT NULL UNIQUE",
        "graph_outbox_id TEXT NOT NULL UNIQUE",
        "budget_outbox_id TEXT NOT NULL UNIQUE",
        "trg_task_runtime_review_denial_no_update",
        "trg_task_runtime_review_denial_no_delete",
        "trg_agent_runtime_settled_review_no_update",
        "trg_agent_runtime_settled_review_no_delete",
    )
    assert all(token in source for token in required)


def test_review_denial_store_is_canonical_focused_and_single_transaction() -> None:
    assert issubclass(DurableTaskStore, TaskReviewDenialStoreMixin)
    assert len(inspect.getsource(task_review_denial_store).splitlines()) < 800
    assert len(inspect.getsource(task_review_denial_validation).splitlines()) < 500
    assert len(inspect.getsource(review_denial_lifecycle).splitlines()) < 400
    settle = _method(
        task_review_denial_store,
        "TaskReviewDenialStoreMixin",
        "settle_review_denial",
    )
    assert len(_calls(settle, "_transaction")) == 1
    assert len(_calls(settle, "_transition_node_in_transaction")) == 1
    assert len(
        _calls(settle, "_cancel_unstarted_review_node_in_transaction")
    ) == 1
    assert len(_calls(settle, "_transition_graph_in_transaction")) == 1
    assert not _calls(settle, "transition_node")
    assert not _calls(settle, "transition_graph")
    assert not _calls(settle, "transition_budget")


def test_scheduler_uses_review_denial_uow_instead_of_three_public_writers() -> None:
    tree = ast.parse(inspect.getsource(dag_scheduling_runtime))
    execute = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "execute_stream"
    )
    assert len(_calls(execute, "settle_denied_review")) == 2
    assert len(_calls(execute, "reopen_denied_review_terminal")) == 1
    assert not _calls(execute, "cancel_waiting_review_node")
