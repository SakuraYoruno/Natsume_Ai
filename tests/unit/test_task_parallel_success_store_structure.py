from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository, task_store_types
from host_brain.persistence.task_parallel_success_store import (
    TaskParallelSuccessStoreMixin,
)
from host_brain.persistence.task_parallel_settlement_store import (
    TaskParallelSettlementStoreMixin,
)


PARALLEL_SUCCESS_METHODS = (
    "assert_parallel_success_decision_ready",
    "get_parallel_success_decision",
    "get_latest_parallel_success_decision",
    "complete_parallel_graph_success",
    "_validate_parallel_success_attempt_lineage",
    "_insert_parallel_success_decision",
    "_assert_parallel_success_decision_replay",
)


def test_parallel_success_mixin_preserves_authority_and_owner_transaction() -> None:
    bases = task_repository.DurableTaskStore.__bases__
    success_index = bases.index(TaskParallelSuccessStoreMixin)
    assert bases[success_index : success_index + 2] == (
        TaskParallelSuccessStoreMixin,
        TaskParallelSettlementStoreMixin,
    )
    for name in PARALLEL_SUCCESS_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskParallelSuccessStoreMixin,
            name,
        )
    assert (
        task_repository._PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS
        is task_store_types._PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS
    )

    module = inspect.getmodule(TaskParallelSuccessStoreMixin)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "host_brain.persistence.task_repository" not in imported_modules

    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    defined_methods = {
        node.name for node in mixin.body if isinstance(node, ast.FunctionDef)
    }
    assert not {"__init__", "_connect", "_transaction", "_now"} & defined_methods
    forbidden_attributes = {
        "connect",
        "commit",
        "rollback",
        "executemany",
        "executescript",
    }
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }

    complete = next(
        node
        for node in mixin.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "complete_parallel_graph_success"
    )
    owner_transactions = [
        node
        for node in ast.walk(complete)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "_transaction"
            and isinstance(item.context_expr.func.value, ast.Name)
            and item.context_expr.func.value.id == "self"
            for item in node.items
        )
    ]
    assert len(owner_transactions) == 1
    staging_calls = [
        node
        for node in ast.walk(owner_transactions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_stage_acceptance_effect_intent_in_transaction"
    ]
    assert staging_calls
    for call in staging_calls:
        assert call.args and isinstance(call.args[0], ast.Name)
        assert call.args[0].id == "conn"

    transaction_scoped_calls: dict[str, list[ast.Call]] = {}
    for node in ast.walk(owner_transactions[0]):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            call_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            call_name = node.func.id
        else:
            continue
        transaction_scoped_calls.setdefault(call_name, []).append(node)
    critical_conn_calls = {
        "_assert_graph_not_cancelling",
        "_assert_mutation_outbox_replay",
        "_assert_parallel_success_decision_bindings",
        "_assert_parallel_success_decision_replay",
        "_insert_outbox",
        "_insert_parallel_success_decision",
        "_require_budget",
        "_require_graph",
        "_stage_acceptance_effect_intent_in_transaction",
        "_transition_graph_in_transaction",
        "_validate_parallel_success_attempt_lineage",
    }
    assert critical_conn_calls <= transaction_scoped_calls.keys()
    for call_name in critical_conn_calls:
        for call in transaction_scoped_calls[call_name]:
            assert call.args and isinstance(call.args[0], ast.Name), call_name
            assert call.args[0].id == "conn", call_name

    for helper_name in (
        "_validate_parallel_success_attempt_lineage",
        "_insert_parallel_success_decision",
        "_assert_parallel_success_decision_replay",
    ):
        helper = next(
            node
            for node in mixin.body
            if isinstance(node, ast.FunctionDef) and node.name == helper_name
        )
        assert helper.args.args[0].arg == "conn"
        assert not {
            node.func.attr
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"_connect", "_transaction", "_now"}
        }

    sql_literals = (
        str(node.value).upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert not any(
        sql.strip().startswith("BEGIN ") or sql.strip() in {"COMMIT", "ROLLBACK"}
        for sql in sql_literals
    )
