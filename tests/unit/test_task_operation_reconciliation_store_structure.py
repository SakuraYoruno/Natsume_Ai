from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_operation_reconciliation_store import (
    TaskOperationReconciliationStoreMixin,
)
from host_brain.persistence.task_worker_interruption_store import (
    TaskWorkerInterruptionStoreMixin,
)


def test_operation_reconciliation_entry_is_owned_by_focused_store_mixin() -> None:
    assert (
        task_repository.DurableTaskStore.reconcile_graph_cancellation_operation
        is TaskOperationReconciliationStoreMixin.reconcile_graph_cancellation_operation
    )
    assert TaskOperationReconciliationStoreMixin in task_repository.DurableTaskStore.__bases__
    assert task_repository.DurableTaskStore.__bases__[-2:] == (
        task_repository.TaskDagStateStoreMixin,
        task_repository.TaskReadyClaimStoreMixin,
    )

    module = inspect.getmodule(TaskOperationReconciliationStoreMixin)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "host_brain.persistence.task_repository" not in imported_modules

    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    methods = {
        node.name
        for node in mixin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"reconcile_graph_cancellation_operation"}
    forbidden_attributes = {"_connect", "_transaction", "commit", "rollback"}
    assert not {
        node.attr
        for node in ast.walk(mixin)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }
    recovery_calls = [
        node
        for node in ast.walk(mixin)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "recover_operation_reservation"
    ]
    assert len(recovery_calls) == 1
    assert {
        keyword.arg for keyword in recovery_calls[0].keywords
    } >= {
        "expected_version",
        "outbox",
        "reconciler_actor_id",
        "attestation_ref",
        "attestation_key_id",
        "attestation_signature",
        "_required_cancellation_intent_ref",
    }


def test_worker_interruption_entry_is_owned_by_focused_transactional_mixin() -> None:
    assert (
        task_repository.DurableTaskStore.mark_worker_interruption_recovery_required
        is TaskWorkerInterruptionStoreMixin.mark_worker_interruption_recovery_required
    )
    assert TaskWorkerInterruptionStoreMixin in task_repository.DurableTaskStore.__bases__
    module = inspect.getmodule(TaskWorkerInterruptionStoreMixin)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    methods = {
        node.name
        for node in mixin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"mark_worker_interruption_recovery_required"}
