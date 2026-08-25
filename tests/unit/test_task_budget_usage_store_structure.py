from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_budget_usage_store import TaskBudgetUsageStoreMixin


PUBLIC_METHODS = (
    "consume_budget_once",
    "get_budget_usage",
    "reconcile_budget",
)


def test_budget_usage_mixin_owns_public_api_without_a_second_uow() -> None:
    assert task_repository.DurableTaskStore.__bases__[-3] is TaskBudgetUsageStoreMixin
    for name in PUBLIC_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskBudgetUsageStoreMixin,
            name,
        )
    module = inspect.getmodule(TaskBudgetUsageStoreMixin)
    assert module is not None
    source = inspect.getsource(module)
    assert len(source.splitlines()) < 750
    tree = ast.parse(source)
    forbidden_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        if node.func.attr in {"commit", "rollback"}
    }
    assert forbidden_calls == set()


def test_budget_charge_uses_owner_transaction_and_bounded_terminal_branch() -> None:
    source = inspect.getsource(TaskBudgetUsageStoreMixin.consume_budget_once)
    assert "with self._transaction() as conn" in source
    assert "self._require_version" in source
    assert "record_already_exceeded_usage" in source
    tree = ast.parse(inspect.cleandoc(source))
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree))
