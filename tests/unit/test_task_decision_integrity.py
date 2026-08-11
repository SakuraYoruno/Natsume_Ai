from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_decision_integrity, task_repository


INTEGRITY_FUNCTIONS = (
    "_assert_graph_cancellation_decision_bindings",
    "_assert_parallel_success_decision_bindings",
    "_assert_parallel_settlement_decision_outbox_binding",
    "_assert_parallel_budget_usage_lineage",
    "_find_invalid_declared_parallel_settlement_decision_ref",
)


def test_decision_integrity_aliases_are_read_only_and_connection_borrowing() -> None:
    for name in INTEGRITY_FUNCTIONS:
        assert getattr(task_repository, name) is getattr(task_decision_integrity, name)

    tree = ast.parse(inspect.getsource(task_decision_integrity))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "host_brain.persistence.task_repository" not in imported_modules

    forbidden_attributes = {
        "connect",
        "commit",
        "rollback",
        "executemany",
        "executescript",
        "_connect",
        "_transaction",
    }
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }

    execute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert execute_calls
    for call in execute_calls:
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "conn"
        assert call.args and isinstance(call.args[0], ast.Constant)
        assert str(call.args[0].value).strip().upper().startswith("SELECT")
