from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_operation_checkpoint_store import (
    TaskOperationCheckpointStoreMixin,
)
from host_brain.persistence import task_operation_checkpoint_validation
from host_brain.task_runtime.ports import TaskStorePort


def test_operation_checkpoint_store_is_one_focused_repository_mixin() -> None:
    assert (
        task_repository.DurableTaskStore.reconcile_operation_reservation_state
        is TaskOperationCheckpointStoreMixin.reconcile_operation_reservation_state
    )
    assert (
        task_repository.DurableTaskStore.get_committed_operation_checkpoint
        is TaskOperationCheckpointStoreMixin.get_committed_operation_checkpoint
    )
    assert TaskOperationCheckpointStoreMixin in task_repository.DurableTaskStore.__bases__

    module = inspect.getmodule(TaskOperationCheckpointStoreMixin)
    assert module is not None
    source = inspect.getsource(module)
    assert len(source.splitlines()) <= 240
    tree = ast.parse(source)
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
    assert methods == {
        "get_committed_operation_checkpoint",
        "reconcile_operation_reservation_state",
    }
    helper_source = inspect.getsource(task_operation_checkpoint_validation)
    assert len(helper_source.splitlines()) < 150
    assert "sqlite3" not in helper_source
    assert "task_repository" not in helper_source


def test_operation_checkpoint_ports_expose_the_atomic_store_boundary() -> None:
    assert hasattr(TaskStorePort, "reconcile_operation_reservation_state")
    assert hasattr(TaskStorePort, "get_committed_operation_checkpoint")
