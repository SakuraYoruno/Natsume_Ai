from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_dag_state_store import (
    TaskDagStateStoreMixin,
)
from host_brain.persistence.task_outbox_delivery_store import (
    TaskOutboxDeliveryStoreMixin,
)
from host_brain.persistence.task_ready_claim_store import (
    TaskReadyClaimStoreMixin,
)
from host_brain.persistence.task_retry_mutation_store import (
    TaskRetryMutationStoreMixin,
)


DAG_STATE_METHODS = (
    "create_graph",
    "insert_graph_in_transaction",
    "insert_outbox_in_transaction",
    "get_graph",
    "list_graphs",
    "get_node",
    "read_scheduler_snapshot",
    "transition_graph",
    "transition_node",
)
READY_CLAIM_METHODS = ("claim_ready_node", "_replay_ready_node_claim")


def test_task_outbox_mutation_helpers_have_one_mixin_owner() -> None:
    assert (
        task_repository.DurableTaskStore._insert_outbox
        is TaskOutboxDeliveryStoreMixin._insert_outbox
    )
    assert (
        task_repository.DurableTaskStore._assert_mutation_outbox_replay
        is TaskOutboxDeliveryStoreMixin._assert_mutation_outbox_replay
    )
    assert "_insert_outbox" not in task_repository.DurableTaskStore.__dict__
    assert (
        "_assert_mutation_outbox_replay"
        not in task_repository.DurableTaskStore.__dict__
    )


def _module_tree(owner: type) -> ast.Module:
    module = inspect.getmodule(owner)
    assert module is not None
    return ast.parse(inspect.getsource(module))


def _class_ast(owner: type) -> ast.ClassDef:
    return next(
        node for node in _module_tree(owner).body if isinstance(node, ast.ClassDef)
    )


def _method_ast(owner: type, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in _class_ast(owner).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _transaction_blocks(method: ast.FunctionDef) -> list[ast.With]:
    return [
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


def _named_calls(method: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == name
            or isinstance(node.func, ast.Name)
            and node.func.id == name
        )
    ]


def _assert_first_arg_is_conn(method: ast.FunctionDef, names: set[str]) -> None:
    for name in names:
        calls = _named_calls(method, name)
        assert calls, name
        assert all(
            call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "conn"
            for call in calls
        ), name


def _assert_execute_calls_use_conn(
    method: ast.FunctionDef,
    *,
    inside: ast.With | None = None,
) -> None:
    execute_calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert execute_calls
    allowed_nodes = None if inside is None else {id(node) for node in ast.walk(inside)}
    for call in execute_calls:
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "conn"
        if allowed_nodes is not None:
            assert id(call) in allowed_nodes


def test_dag_state_and_ready_claim_mixins_preserve_method_identity() -> None:
    assert task_repository.DurableTaskStore.__bases__[-2:] == (
        TaskDagStateStoreMixin,
        TaskReadyClaimStoreMixin,
    )
    for owner, names in (
        (TaskDagStateStoreMixin, DAG_STATE_METHODS),
        (TaskReadyClaimStoreMixin, READY_CLAIM_METHODS),
    ):
        for name in names:
            assert getattr(task_repository.DurableTaskStore, name) is getattr(owner, name)


def test_dag_state_and_ready_claim_mixins_borrow_store_state() -> None:
    for owner in (TaskDagStateStoreMixin, TaskReadyClaimStoreMixin):
        module = inspect.getmodule(owner)
        assert module is not None
        source = inspect.getsource(module)
        assert len(source.splitlines()) < 800
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "host_brain.persistence.task_repository" not in imported_modules
        defined_methods = {
            node.name
            for node in _class_ast(owner).body
            if isinstance(node, ast.FunctionDef)
        }
        assert not {"__init__", "_connect", "_transaction", "_now"} & defined_methods
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sqlite3"
            and node.func.attr == "connect"
            for node in ast.walk(tree)
        )


def test_create_graph_keeps_graph_nodes_and_outbox_in_one_transaction() -> None:
    create = _method_ast(TaskDagStateStoreMixin, "create_graph")
    transactions = _transaction_blocks(create)
    assert len(transactions) == 1
    _assert_first_arg_is_conn(create, {"insert_graph_in_transaction"})
    insert_call = _named_calls(create, "insert_graph_in_transaction")[0]
    assert id(insert_call) in {id(node) for node in ast.walk(transactions[0])}

    insert_graph = _method_ast(
        TaskDagStateStoreMixin,
        "insert_graph_in_transaction",
    )
    assert [arg.arg for arg in insert_graph.args.args[:2]] == ["self", "conn"]
    assert _transaction_blocks(insert_graph) == []
    _assert_execute_calls_use_conn(insert_graph)
    _assert_first_arg_is_conn(insert_graph, {"insert_outbox_in_transaction"})
    insert_source = inspect.getsource(
        TaskDagStateStoreMixin.insert_graph_in_transaction
    )
    assert "not conn.in_transaction" in insert_source
    graph_insert = insert_source.index("INSERT INTO task_runtime_graph")
    node_insert = insert_source.index("INSERT INTO task_runtime_node")
    outbox_insert = insert_source.index("self.insert_outbox_in_transaction")
    assert graph_insert < node_insert < outbox_insert
    assert "BEGIN" not in insert_source
    assert "conn.commit" not in insert_source
    assert "conn.rollback" not in insert_source

    insert_outbox = _method_ast(
        TaskDagStateStoreMixin,
        "insert_outbox_in_transaction",
    )
    assert [arg.arg for arg in insert_outbox.args.args[:2]] == ["self", "conn"]
    assert _transaction_blocks(insert_outbox) == []
    _assert_first_arg_is_conn(insert_outbox, {"_insert_outbox"})
    outbox_source = inspect.getsource(
        TaskDagStateStoreMixin.insert_outbox_in_transaction
    )
    assert "not conn.in_transaction" in outbox_source
    assert "BEGIN" not in outbox_source
    assert "conn.commit" not in outbox_source
    assert "conn.rollback" not in outbox_source


def test_scheduler_snapshot_uses_one_consistent_read_transaction() -> None:
    snapshot = _method_ast(TaskDagStateStoreMixin, "read_scheduler_snapshot")
    _assert_execute_calls_use_conn(snapshot)
    _assert_first_arg_is_conn(snapshot, {"_require_graph"})
    source = inspect.getsource(TaskDagStateStoreMixin.read_scheduler_snapshot)
    assert source.count('conn.execute("BEGIN")') == 1
    assert source.count("conn.commit()") == 1
    assert source.count("conn.rollback()") == 1
    assert source.count("conn.close()") == 1
    assert source.index('conn.execute("BEGIN")') < source.index(
        "graph_row = self._require_graph"
    )
    assert source.index("snapshot = {") < source.index("conn.commit()")


def test_public_transitions_borrow_shared_transaction_helpers() -> None:
    helper_by_method = {
        "transition_graph": "_transition_graph_in_transaction",
        "transition_node": "_transition_node_in_transaction",
    }
    for method_name, helper_name in helper_by_method.items():
        method = _method_ast(TaskDagStateStoreMixin, method_name)
        transactions = _transaction_blocks(method)
        assert len(transactions) == 1
        _assert_first_arg_is_conn(
            method,
            {"_assert_graph_not_cancelling", helper_name},
        )

    shared_helpers = {
        "_assert_dependencies_accepted",
        "_assert_graph_not_cancelling",
        "_require_active_graph_budget",
        "_transition_graph_in_transaction",
        "_transition_node_in_transaction",
    }
    assert shared_helpers <= task_repository.DurableTaskStore.__dict__.keys()
    assert not shared_helpers & TaskDagStateStoreMixin.__dict__.keys()
    assert not shared_helpers & TaskReadyClaimStoreMixin.__dict__.keys()


def test_ready_claim_rechecks_all_authorities_in_one_writer_transaction() -> None:
    claim = _method_ast(TaskReadyClaimStoreMixin, "claim_ready_node")
    transactions = _transaction_blocks(claim)
    assert len(transactions) == 1
    transaction = transactions[0]
    _assert_execute_calls_use_conn(claim, inside=transaction)
    _assert_first_arg_is_conn(
        claim,
        {
            "_assert_dependencies_accepted",
            "_assert_graph_not_cancelling",
            "_insert_outbox",
            "_replay_ready_node_claim",
            "_require_attempt",
            "_require_graph",
            "_require_lease",
            "_require_node",
        },
    )
    transaction_nodes = {id(node) for node in ast.walk(transaction)}
    assert all(
        id(call) in transaction_nodes
        for name in (
            "_assert_dependencies_accepted",
            "_assert_graph_not_cancelling",
            "_insert_outbox",
            "_replay_ready_node_claim",
            "_require_graph",
        )
        for call in _named_calls(claim, name)
    )
    assert len(_named_calls(claim, "_insert_outbox")) == 3

    source = inspect.getsource(TaskReadyClaimStoreMixin.claim_ready_node)
    cancellation = source.index("self._assert_graph_not_cancelling")
    graph = source.index("graph = self._require_graph")
    budget = source.index("FROM task_runtime_budget_reservation")
    node = source.index("node = self._require_node")
    existing = source.index("existing_attempt = conn.execute")
    replay = source.index("return self._replay_ready_node_claim")
    attempt_sequence = source.index("attempt_stats = conn.execute")
    dependencies = source.index("self._assert_dependencies_accepted")
    capacity = source.index("occupied = conn.execute")
    attempt_insert = source.index("INSERT INTO task_runtime_attempt")
    assert cancellation < graph < budget < node < existing < replay
    assert replay < attempt_sequence < dependencies < capacity < attempt_insert
    assert "TASK_STORE_SCHEDULER_CAPACITY" in source
    assert "INSERT INTO task_runtime_lease" in source
    assert "INSERT INTO task_runtime_heartbeat" in source


def test_ready_claim_exact_replay_uses_same_connection_and_no_second_slot() -> None:
    replay = _method_ast(TaskReadyClaimStoreMixin, "_replay_ready_node_claim")
    assert [arg.arg for arg in replay.args.args[:2]] == ["self", "conn"]
    assert _transaction_blocks(replay) == []
    _assert_execute_calls_use_conn(replay)
    _assert_first_arg_is_conn(replay, {"_assert_mutation_outbox_replay"})
    assert len(_named_calls(replay, "_assert_mutation_outbox_replay")) == 3
    assert _named_calls(replay, "_insert_outbox") == []
    assert _named_calls(replay, "_now") == []

    replay_sql = [
        str(node.value).strip().upper()
        for node in ast.walk(replay)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert not any(
        sql.startswith(("INSERT ", "UPDATE ", "DELETE "))
        for sql in replay_sql
    )
    replay_source = inspect.getsource(
        TaskReadyClaimStoreMixin._replay_ready_node_claim
    )
    assert "occupied" not in replay_source
    assert "TASK_STORE_SCHEDULER_CAPACITY" not in replay_source

    claim_source = inspect.getsource(TaskReadyClaimStoreMixin.claim_ready_node)
    assert claim_source.index("return self._replay_ready_node_claim") < (
        claim_source.index("occupied = conn.execute")
    )


def test_retry_promotion_and_ready_claim_remain_separate_transactions() -> None:
    promotion = _method_ast(TaskRetryMutationStoreMixin, "promote_retry_due")
    claim = _method_ast(TaskReadyClaimStoreMixin, "claim_ready_node")
    assert len(_transaction_blocks(promotion)) == 1
    assert len(_transaction_blocks(claim)) == 1
    assert _named_calls(promotion, "claim_ready_node") == []
    assert _named_calls(claim, "promote_retry_due") == []
