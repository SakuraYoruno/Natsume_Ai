from __future__ import annotations

import ast
import inspect

from host_brain.contracts.agent_runtime import parallel_settlement
from host_brain.persistence import (
    task_decision_integrity,
    task_parallel_settlement_store,
    task_parallel_state,
    task_record_codec,
    task_repository,
    task_store_types,
)
from host_brain.persistence.task_parallel_settlement_store import (
    TaskParallelSettlementStoreMixin,
)


SETTLEMENT_METHODS = (
    "assert_parallel_settlement_decision_ready",
    "get_parallel_settlement_decision",
    "get_latest_parallel_settlement_decision",
    "settle_parallel_graph_budget",
    "_validate_parallel_terminal_snapshot",
    "_validate_parallel_decision_attempt_lineage",
    "_insert_parallel_settlement_decision",
    "_assert_parallel_settlement_decision_replay",
)


def test_parallel_settlement_mixin_borrows_one_owner_transaction() -> None:
    assert task_repository.DurableTaskStore.__bases__[5] is TaskParallelSettlementStoreMixin
    for name in SETTLEMENT_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskParallelSettlementStoreMixin,
            name,
        )
    assert (
        task_repository.DurableTaskStore._normalize_parallel_node_states
        is task_parallel_state._normalize_parallel_node_states
    )
    assert (
        task_repository.DurableTaskStore._assert_parallel_node_states
        is task_parallel_state._assert_parallel_node_states
    )
    assert (
        task_repository._PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS
        is task_store_types._PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS
    )
    assert (
        task_repository._parallel_settlement_decision_record
        is task_record_codec._parallel_settlement_decision_record
        is task_parallel_settlement_store._parallel_settlement_decision_record
    )
    for name in (
        "_assert_parallel_budget_usage_lineage",
        "_assert_parallel_settlement_decision_outbox_binding",
        "_find_invalid_declared_parallel_settlement_decision_ref",
    ):
        assert getattr(task_repository, name) is getattr(task_decision_integrity, name)
        assert getattr(task_parallel_settlement_store, name) is getattr(
            task_decision_integrity,
            name,
        )
    for name in (
        "CanonicalParallelSettlementDecision",
        "PARALLEL_SETTLEMENT_SCHEMA_VERSION",
        "ParallelSettlementContractError",
        "normalize_parallel_settlement_decision",
    ):
        assert getattr(task_repository, name) is getattr(parallel_settlement, name)
        assert getattr(task_parallel_settlement_store, name) is getattr(
            parallel_settlement,
            name,
        )

    module = inspect.getmodule(TaskParallelSettlementStoreMixin)
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
        "executemany",
        "executescript",
    }
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }
    settle = next(
        node
        for node in mixin.body
        if isinstance(node, ast.FunctionDef) and node.name == "settle_parallel_graph_budget"
    )
    owner_transactions = [
        node
        for node in ast.walk(settle)
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
        "_assert_parallel_settlement_decision_replay",
        "_insert_outbox",
        "_insert_parallel_settlement_decision",
        "_require_budget",
        "_require_graph",
        "_transition_graph_in_transaction",
        "_validate_parallel_decision_attempt_lineage",
    }
    assert critical_conn_calls <= transaction_scoped_calls.keys()
    for call_name in critical_conn_calls:
        for call in transaction_scoped_calls[call_name]:
            assert call.args and isinstance(call.args[0], ast.Name), call_name
            assert call.args[0].id == "conn", call_name

    for helper_name in (
        "_validate_parallel_decision_attempt_lineage",
        "_insert_parallel_settlement_decision",
        "_assert_parallel_settlement_decision_replay",
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
        if helper_name == "_validate_parallel_decision_attempt_lineage":
            usage_calls = [
                node
                for node in ast.walk(helper)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_assert_parallel_budget_usage_lineage"
            ]
            assert len(usage_calls) == 1
            assert isinstance(usage_calls[0].args[0], ast.Name)
            assert usage_calls[0].args[0].id == "conn"

    getters = {
        node.name: node
        for node in mixin.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "get_parallel_settlement_decision",
            "get_latest_parallel_settlement_decision",
        }
    }
    assert set(getters) == {
        "get_parallel_settlement_decision",
        "get_latest_parallel_settlement_decision",
    }
    latest = getters["get_latest_parallel_settlement_decision"]
    latest_conn_calls = {
        node.func.id: node
        for node in ast.walk(latest)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {
            "_find_invalid_declared_parallel_settlement_decision_ref",
            "_assert_parallel_settlement_decision_outbox_binding",
        }
    }
    assert set(latest_conn_calls) == {
        "_find_invalid_declared_parallel_settlement_decision_ref",
        "_assert_parallel_settlement_decision_outbox_binding",
    }
    for call_name, call in latest_conn_calls.items():
        assert call.args and isinstance(call.args[0], ast.Name), call_name
        assert call.args[0].id == "conn", call_name

    for getter_name, getter in getters.items():
        binding_calls = [
            node
            for node in ast.walk(getter)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_parallel_settlement_decision_outbox_binding"
        ]
        lineage_calls = [
            node
            for node in ast.walk(getter)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_validate_parallel_decision_attempt_lineage"
        ]
        assert len(binding_calls) == 1, getter_name
        assert len(lineage_calls) == 1, getter_name
        for call in (*binding_calls, *lineage_calls):
            assert call.args and isinstance(call.args[0], ast.Name), getter_name
            assert call.args[0].id == "conn", getter_name

        begin_calls = [
            node
            for node in ast.walk(getter)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "conn"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).strip().upper() == "BEGIN"
        ]
        rollback_calls = [
            node
            for node in ast.walk(getter)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rollback"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "conn"
        ]
        assert len(begin_calls) == 1, getter_name
        assert len(rollback_calls) == 1, getter_name
        try_blocks = [node for node in getter.body if isinstance(node, ast.Try)]
        assert len(try_blocks) == 1, getter_name
        assert any(
            rollback_calls[0] is nested
            for statement in try_blocks[0].finalbody
            for nested in ast.walk(statement)
        ), getter_name

    for method in (
        node for node in mixin.body if isinstance(node, ast.FunctionDef)
    ):
        if method.name in getters:
            continue
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and (
                node.func.attr in {"commit", "rollback"}
                or (
                    node.func.attr == "execute"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and str(node.args[0].value).strip().upper() == "BEGIN"
                )
            )
            for node in ast.walk(method)
        ), method.name

    sql_literals = (
        str(node.value).upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    manual_control_sql = [
        sql.strip()
        for sql in sql_literals
        if sql.strip().startswith("BEGIN")
        or sql.strip() in {"COMMIT", "ROLLBACK"}
    ]
    assert manual_control_sql.count("BEGIN") == 2
    assert not {"COMMIT", "ROLLBACK"} & set(manual_control_sql)


def test_parallel_state_helpers_are_pure_repository_agnostic_functions() -> None:
    tree = ast.parse(inspect.getsource(task_parallel_state))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "host_brain.persistence.task_repository" not in imported_modules
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "connect", "commit", "rollback"}
        for node in ast.walk(tree)
    )
