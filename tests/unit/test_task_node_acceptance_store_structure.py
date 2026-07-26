from __future__ import annotations

import ast
import inspect
from pathlib import Path

from host_brain.persistence import review_pending_uow
from host_brain.persistence import task_node_acceptance_common
from host_brain.persistence import task_node_acceptance_store
from host_brain.persistence import task_node_artifact_store
from host_brain.persistence import review_repository
from host_brain.persistence.task_node_acceptance_store import (
    TaskNodeAcceptanceStoreMixin,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.task_runtime import dag_scheduling_acceptance_review


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


def _calls(method: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == name
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id == name
            )
        )
    ]


def test_0134_schema_is_append_only_attempt_review_and_outbox_bound() -> None:
    source = (
        ROOT
        / "host_brain"
        / "audit"
        / "migrations"
        / "0134_task_node_acceptance_decision.sql"
    ).read_text(encoding="utf-8")
    required = (
        "task_runtime_node_acceptance_decision",
        "attempt_id TEXT NOT NULL",
        "node_version_after = node_version_before + 1",
        "result_sha256",
        "node_outbox_id TEXT NOT NULL UNIQUE",
        "review_id TEXT",
        "graph_outbox_id TEXT UNIQUE",
        "FOREIGN KEY(review_id)",
        "trg_task_runtime_node_acceptance_no_update",
        "trg_task_runtime_node_acceptance_no_delete",
    )
    assert all(token in source for token in required)


def test_acceptance_store_and_single_pending_review_writer_are_canonical() -> None:
    assert issubclass(DurableTaskStore, TaskNodeAcceptanceStoreMixin)
    assert len(inspect.getsource(task_node_acceptance_common).splitlines()) < 300
    assert len(inspect.getsource(task_node_acceptance_store).splitlines()) < 850
    assert len(inspect.getsource(review_pending_uow).splitlines()) < 350
    repository_source = inspect.getsource(review_repository)
    pending_source = inspect.getsource(review_pending_uow)
    assert "INSERT INTO agent_runtime_review(" not in repository_source
    assert pending_source.count("INSERT INTO agent_runtime_review(") == 1


def test_accept_and_review_paths_each_use_one_task_store_transaction() -> None:
    accepted = _method(
        task_node_artifact_store,
        "TaskNodeArtifactStoreMixin",
        "commit_node_acceptance",
    )
    review = _method(
        task_node_acceptance_store,
        "TaskNodeAcceptanceStoreMixin",
        "commit_node_acceptance_review",
    )
    assert len(_calls(accepted, "_transaction")) == 1
    assert len(
        _calls(
            accepted,
            "_insert_node_acceptance_decision_in_transaction",
        )
    ) == 1
    assert len(_calls(review, "_transaction")) == 1
    assert len(_calls(review, "_transition_node_in_transaction")) == 1
    assert len(_calls(review, "_transition_graph_in_transaction")) == 1
    assert len(_calls(review, "insert_pending_review_in_transaction")) == 1
    assert len(
        _calls(
            review,
            "_insert_node_acceptance_decision_in_transaction",
        )
    ) == 1
    assert not _calls(review, "transition_node")
    assert not _calls(review, "transition_graph")


def test_scheduler_acceptance_review_reopen_is_read_only_and_focused() -> None:
    source = inspect.getsource(dag_scheduling_acceptance_review)
    assert len(source.splitlines()) < 120
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]
    assert calls.count("get_node_acceptance_decision") == 1
    assert calls.count("attempts_for_node") == 1
    assert calls.count("post_execution_acceptance_gate") == 1
    assert not {
        "commit_node_acceptance",
        "commit_node_acceptance_review",
        "transition_node",
        "transition_graph",
    }.intersection(calls)
