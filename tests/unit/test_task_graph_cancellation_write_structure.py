from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_graph_cancellation_finalization import (
    TaskGraphCancellationFinalizationMixin,
)
from host_brain.persistence.task_graph_cancellation_request import (
    TaskGraphCancellationRequestMixin,
)


def _method_ast(owner: type, name: str) -> ast.FunctionDef:
    module = inspect.getmodule(owner)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    return next(
        node
        for node in mixin.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_graph_cancellation_write_mixins_borrow_owner_authorities() -> None:
    assert task_repository.DurableTaskStore.__bases__[-2:] == (
        TaskGraphCancellationRequestMixin,
        TaskGraphCancellationFinalizationMixin,
    )
    owners = {
        "request_graph_cancellation": TaskGraphCancellationRequestMixin,
        "advance_graph_cancellation": TaskGraphCancellationFinalizationMixin,
        "finalize_graph_cancellation": TaskGraphCancellationFinalizationMixin,
    }
    critical_conn_calls = {
        "request_graph_cancellation": {
            "_assert_graph_cancellation_decision_bindings",
            "_assert_mutation_outbox_replay",
            "_insert_outbox",
            "_mark_operation_recovery_required",
            "_require_budget",
            "_require_graph",
        },
        "advance_graph_cancellation": {
            "_graph_cancellation_evidence_refs",
            "_insert_outbox",
        },
        "finalize_graph_cancellation": {
            "_assert_mutation_outbox_replay",
            "_graph_cancellation_evidence_refs",
            "_insert_outbox",
            "_require_budget",
            "_require_graph",
            "_transition_graph_in_transaction",
        },
    }
    for name, owner in owners.items():
        assert getattr(task_repository.DurableTaskStore, name) is getattr(owner, name)
        method = _method_ast(owner, name)
        owner_transactions = [
            node
            for node in ast.walk(method)
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
        transaction = owner_transactions[0]
        transaction_nodes = {id(node) for node in ast.walk(transaction)}
        execute_calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]
        assert execute_calls
        for call in execute_calls:
            assert id(call) in transaction_nodes, name
            assert isinstance(call.func.value, ast.Name), name
            assert call.func.value.id == "conn", name

        scoped_calls: dict[str, list[ast.Call]] = {}
        for node in ast.walk(transaction):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id
            else:
                continue
            scoped_calls.setdefault(call_name, []).append(node)
        assert critical_conn_calls[name] <= scoped_calls.keys()
        for call_name in critical_conn_calls[name]:
            for call in scoped_calls[call_name]:
                assert call.args and isinstance(call.args[0], ast.Name), call_name
                assert call.args[0].id == "conn", call_name

        if name in {"request_graph_cancellation", "finalize_graph_cancellation"}:
            sql = "\n".join(
                str(node.value)
                for node in ast.walk(transaction)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            )
            assert "task_runtime_parallel_success_decision" in sql

    for owner in (
        TaskGraphCancellationRequestMixin,
        TaskGraphCancellationFinalizationMixin,
    ):
        module = inspect.getmodule(owner)
        assert module is not None
        tree = ast.parse(inspect.getsource(module))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "host_brain.persistence.task_repository" not in imported_modules
        mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        defined_methods = {
            node.name for node in mixin.body if isinstance(node, ast.FunctionDef)
        }
        assert not {"__init__", "_connect", "_transaction", "_now"} & defined_methods
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr
            in {"connect", "commit", "rollback", "executemany", "executescript"}
        }

    for name in ("advance_graph_cancellation", "finalize_graph_cancellation"):
        method = _method_ast(TaskGraphCancellationFinalizationMixin, name)
        evidence_calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_graph_cancellation_evidence_refs"
        ]
        assert evidence_calls
        assert all(
            call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "conn"
            for call in evidence_calls
        )

    evidence_builder = inspect.getsource(
        task_repository.DurableTaskStore._graph_cancellation_evidence_refs
    )
    assert "self._operation_record(row)" in evidence_builder
