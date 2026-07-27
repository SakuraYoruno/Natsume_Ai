from __future__ import annotations

import ast
import inspect

from host_brain.persistence import task_repository
from host_brain.persistence.task_atomic_failure_store import (
    TaskAtomicFailureStoreMixin,
)
from host_brain.persistence.task_retry_mutation_store import (
    TaskRetryMutationStoreMixin,
)
from host_brain.persistence.task_retry_schedule_facade import (
    TaskRetryScheduleFacadeMixin,
)
from host_brain.persistence.task_retry_replay_store import (
    TaskRetryReplayStoreMixin,
)


RETRY_MUTATION_METHODS = (
    "promote_retry_due",
    "_schedule_retry",
)
RETRY_FACADE_METHODS = ("schedule_retry", "schedule_retry_node_outcome")
RETRY_REPLAY_METHODS = ("_replay_scheduled_retry_in_transaction",)
ATOMIC_FAILURE_METHODS = (
    "fail_running_attempt_bundle",
    "fail_running_attempt_node_bundle",
    "_fail_running_attempt_bundle",
    "_terminal_failure_bundle_outbox",
    "_replay_running_attempt_failure_bundle",
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
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_transaction"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            for item in node.items
            for call in ast.walk(item.context_expr)
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


def test_retry_and_atomic_failure_mixins_preserve_public_method_identity() -> None:
    bases = task_repository.DurableTaskStore.__bases__
    retry_index = bases.index(TaskRetryMutationStoreMixin)
    assert bases[retry_index : retry_index + 3] == (
        TaskRetryMutationStoreMixin,
        TaskRetryReplayStoreMixin,
        TaskAtomicFailureStoreMixin,
    )
    for owner, names in (
        (TaskRetryMutationStoreMixin, RETRY_MUTATION_METHODS),
        (TaskRetryScheduleFacadeMixin, RETRY_FACADE_METHODS),
        (TaskRetryReplayStoreMixin, RETRY_REPLAY_METHODS),
        (TaskAtomicFailureStoreMixin, ATOMIC_FAILURE_METHODS),
    ):
        for name in names:
            assert getattr(task_repository.DurableTaskStore, name) is getattr(owner, name)


def test_retry_and_atomic_failure_mixins_are_stateless_borrowers() -> None:
    for owner in (
        TaskRetryMutationStoreMixin,
        TaskRetryScheduleFacadeMixin,
        TaskRetryReplayStoreMixin,
        TaskAtomicFailureStoreMixin,
    ):
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
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr
            in {"connect", "commit", "rollback", "executemany", "executescript"}
        }
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip().upper() in {"BEGIN", "BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}
            for node in ast.walk(tree)
        )


def test_retry_mutation_owns_one_transaction_and_passes_its_connection() -> None:
    promote = _method_ast(TaskRetryMutationStoreMixin, "promote_retry_due")
    schedule = _method_ast(TaskRetryMutationStoreMixin, "_schedule_retry")
    for method in (promote, schedule):
        transactions = _transaction_blocks(method)
        assert len(transactions) == 1
        _assert_execute_calls_use_conn(method, inside=transactions[0])

    for wrapper_name in ("schedule_retry", "schedule_retry_node_outcome"):
        wrapper = _method_ast(TaskRetryScheduleFacadeMixin, wrapper_name)
        assert _transaction_blocks(wrapper) == []
        calls = _named_calls(wrapper, "_schedule_retry")
        assert len(calls) == 1

    _assert_first_arg_is_conn(
        promote,
        {
            "_assert_graph_not_cancelling",
            "_require_graph",
            "_require_node",
            "_insert_outbox",
        },
    )
    _assert_first_arg_is_conn(
        schedule,
        {
            "_active_lease",
            "_assert_graph_not_cancelling",
            "_insert_outbox",
            "_replay_scheduled_retry_in_transaction",
            "_require_active_graph_budget",
            "_require_attempt",
            "_require_budget",
            "_require_graph",
            "_require_node",
        },
    )


def test_retry_replay_reuses_same_connection_and_keeps_response_loss_fences() -> None:
    replay = _method_ast(
        TaskRetryReplayStoreMixin,
        "_replay_scheduled_retry_in_transaction",
    )
    assert [arg.arg for arg in replay.args.args[:2]] == ["self", "conn"]
    assert _transaction_blocks(replay) == []
    _assert_execute_calls_use_conn(replay)
    _assert_first_arg_is_conn(
        replay,
        {
            "_assert_mutation_outbox_replay",
            "_require_budget",
            "_require_graph",
            "_require_node",
        },
    )

    schedule_source = inspect.getsource(TaskRetryMutationStoreMixin._schedule_retry)
    usage_lookup = schedule_source.index("existing_retry_usage = conn.execute")
    replay_call = schedule_source.index(
        "return self._replay_scheduled_retry_in_transaction"
    )
    version_check = schedule_source.index("self._require_version", replay_call)
    reservation_fence = schedule_source.index(
        "FROM task_runtime_idempotency_reservation"
    )
    first_node_load = schedule_source.index("node = self._require_node")
    assert usage_lookup < replay_call < version_check
    assert reservation_fence < first_node_load
    assert "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED" in schedule_source

    replay_source = inspect.getsource(
        TaskRetryReplayStoreMixin._replay_scheduled_retry_in_transaction
    )
    assert "FROM task_runtime_idempotency_reservation" in replay_source
    assert '"charge_kind"]) != "RETRY"' in replay_source
    assert "retry_not_before_ms" in replay_source
    assert "terminalize_graph_on_budget_rejection" in replay_source


def test_atomic_failure_owns_one_transaction_and_replays_on_same_connection() -> None:
    core = _method_ast(TaskAtomicFailureStoreMixin, "_fail_running_attempt_bundle")
    transactions = _transaction_blocks(core)
    assert len(transactions) == 1
    _assert_execute_calls_use_conn(core, inside=transactions[0])
    _assert_first_arg_is_conn(
        core,
        {
            "_active_lease",
            "_assert_graph_execution_authority_quiescent",
            "_insert_outbox",
            "_replay_running_attempt_failure_bundle",
            "_require_attempt",
            "_require_budget",
            "_require_graph",
            "_require_node",
            "_transition_graph_in_transaction",
        },
    )

    for wrapper_name in (
        "fail_running_attempt_bundle",
        "fail_running_attempt_node_bundle",
    ):
        wrapper = _method_ast(TaskAtomicFailureStoreMixin, wrapper_name)
        assert _transaction_blocks(wrapper) == []
        assert len(_named_calls(wrapper, "_fail_running_attempt_bundle")) == 1

    replay = _method_ast(
        TaskAtomicFailureStoreMixin,
        "_replay_running_attempt_failure_bundle",
    )
    assert [arg.arg for arg in replay.args.args[:2]] == ["self", "conn"]
    assert _transaction_blocks(replay) == []
    _assert_execute_calls_use_conn(replay)
    _assert_first_arg_is_conn(
        replay,
        {
            "_assert_mutation_outbox_replay",
            "_require_budget",
            "_require_graph",
            "_require_node",
        },
    )

    core_source = inspect.getsource(
        TaskAtomicFailureStoreMixin._fail_running_attempt_bundle
    )
    reservation_fence = core_source.index(
        "FROM task_runtime_idempotency_reservation"
    )
    replay_call = core_source.index(
        "return self._replay_running_attempt_failure_bundle"
    )
    version_check = core_source.index("self._require_version", replay_call)
    first_node_load = core_source.index("node = self._require_node")
    assert reservation_fence < replay_call < version_check
    assert reservation_fence < first_node_load
    assert "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED" in core_source


def test_atomic_failure_budget_release_is_explicit_and_excludes_parallel_children() -> None:
    tree = _module_tree(TaskAtomicFailureStoreMixin)
    direct = _method_ast(TaskAtomicFailureStoreMixin, "fail_running_attempt_bundle")
    assert {argument.arg for argument in direct.args.kwonlyargs} >= {
        "budget_id",
        "expected_budget_version",
        "budget_outbox",
    }
    node_only = inspect.getsource(
        TaskAtomicFailureStoreMixin.fail_running_attempt_node_bundle
    )
    assert "budget_id=None" in node_only
    assert "expected_budget_version=None" in node_only
    assert "budget_outbox=None" in node_only
    core_source = inspect.getsource(TaskAtomicFailureStoreMixin._fail_running_attempt_bundle)
    assert "validated_terminal_failure_budget_request" in core_source
    assert "_assert_graph_execution_authority_quiescent" in core_source
    assert "BUDGET_RESERVATION_RELEASED" in core_source
