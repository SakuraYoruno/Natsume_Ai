from __future__ import annotations

import ast
import inspect
import textwrap

from host_brain.persistence import task_repository
from host_brain.persistence.task_budget_usage_store import TaskBudgetUsageStoreMixin
from host_brain.persistence.task_dag_state_store import TaskDagStateStoreMixin
from host_brain.persistence.task_lease_takeover_authority import (
    TaskLeaseTakeoverAuthorityMixin,
)
from host_brain.persistence.task_node_artifact_store import TaskNodeArtifactStoreMixin
from host_brain.persistence.task_node_budget_store import TaskNodeBudgetStoreMixin
from host_brain.persistence.task_node_verification_store import (
    TaskNodeVerificationStoreMixin,
)
from host_brain.persistence.task_node_verification_terminal_store import (
    TaskNodeVerificationTerminalStoreMixin,
)
from host_brain.persistence.task_ready_claim_store import TaskReadyClaimStoreMixin


def _module_tree() -> ast.Module:
    module = inspect.getmodule(TaskLeaseTakeoverAuthorityMixin)
    assert module is not None
    return ast.parse(inspect.getsource(module))


def test_lease_takeover_authority_mixin_preserves_repository_mro() -> None:
    bases = task_repository.DurableTaskStore.__bases__
    artifact_index = bases.index(TaskNodeArtifactStoreMixin)
    takeover_index = bases.index(TaskLeaseTakeoverAuthorityMixin)
    assert artifact_index < takeover_index
    assert bases[takeover_index:] == (
        TaskLeaseTakeoverAuthorityMixin,
        TaskNodeVerificationTerminalStoreMixin,
        TaskNodeVerificationStoreMixin,
        TaskNodeBudgetStoreMixin,
        TaskBudgetUsageStoreMixin,
        TaskDagStateStoreMixin,
        TaskReadyClaimStoreMixin,
    )
    assert (
        task_repository.DurableTaskStore._assert_lease_takeover_operation_authority
        is TaskLeaseTakeoverAuthorityMixin._assert_lease_takeover_operation_authority
    )
    assert (
        task_repository.DurableTaskStore._require_preoperation_recovery_graph_budget
        is TaskLeaseTakeoverAuthorityMixin._require_preoperation_recovery_graph_budget
    )
    assert (
        task_repository.DurableTaskStore._replay_committed_lease_acquisition
        is TaskLeaseTakeoverAuthorityMixin._replay_committed_lease_acquisition
    )


def test_lease_takeover_authority_borrows_canonical_transaction() -> None:
    module = inspect.getmodule(TaskLeaseTakeoverAuthorityMixin)
    assert module is not None
    source = inspect.getsource(module)
    assert len(source.splitlines()) < 250
    assert "host_brain.persistence.task_repository" not in source
    tree = _module_tree()
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }
    assert method_names == {
        "_require_preoperation_recovery_graph_budget",
        "_assert_lease_takeover_operation_authority",
        "_replay_committed_lease_acquisition",
    }
    assert not {"__init__", "_connect", "_transaction", "_now"} & method_names
    forbidden_calls = {
        "_connect",
        "_transaction",
        "connect",
        "commit",
        "rollback",
        "executemany",
        "executescript",
    }
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden_calls
        for node in ast.walk(tree)
    )
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ):
            continue
        assert isinstance(node.func.value, ast.Name)
        assert node.func.value.id == "conn"
        assert node.args and isinstance(node.args[0], ast.Constant)
        assert str(node.args[0].value).strip().upper().startswith("SELECT")
    normalized = source.upper()
    assert "INSERT INTO" not in normalized
    assert "UPDATE " not in normalized
    assert "DELETE FROM" not in normalized
    for verb in ("REPLACE ", "CREATE ", "DROP ", "ALTER ", "PRAGMA ", "VACUUM "):
        assert verb not in normalized
    allowed_self_calls = {
        "_assert_graph_accepts_execution_authority",
        "_require_graph",
        "_require_preoperation_recovery_graph_budget",
        "_assert_mutation_outbox_replay",
    }
    self_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    }
    assert self_calls <= allowed_self_calls


def test_lease_takeover_authority_checks_exact_and_scope_operation_absence() -> None:
    source = inspect.getsource(
        TaskLeaseTakeoverAuthorityMixin._assert_lease_takeover_operation_authority
    )
    assert "reservation_id = ?" in source
    assert "business_scope = ? AND operation_fingerprint = ?" in source
    assert "IdempotencyStatus.COMMITTED.value" in source
    assert "TaskGraphStatus.ACTIVE.value" in source
    assert "_require_preoperation_recovery_graph_budget" in source


def test_lease_acquisition_replay_precedes_cas_and_writes() -> None:
    source = inspect.getsource(task_repository.DurableTaskStore.acquire_lease)
    replay_index = source.index("_replay_committed_lease_acquisition")
    cas_index = source.index("self._require_version")
    first_write_index = min(source.index("UPDATE task_runtime_"), source.index("INSERT INTO"))
    assert source.count("_replay_committed_lease_acquisition") == 1
    assert replay_index < cas_index < first_write_index

    replay_source = inspect.getsource(
        TaskLeaseTakeoverAuthorityMixin._replay_committed_lease_acquisition
    )
    assert replay_source.count("_assert_mutation_outbox_replay") == 1
    assert "_insert_outbox" not in replay_source

    tree = ast.parse(textwrap.dedent(source))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    transactions = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.With)
        and isinstance(node.items[0].context_expr, ast.Call)
        and isinstance(node.items[0].context_expr.func, ast.Attribute)
        and node.items[0].context_expr.func.attr == "_transaction"
    ]
    assert len(transactions) == 1
    transaction = transactions[0]
    assert isinstance(transaction.items[0].optional_vars, ast.Name)
    assert transaction.items[0].optional_vars.id == "conn"

    def _statement_calls(
        statement: ast.stmt,
        method: str,
        *,
        first_arg: str | None = None,
    ) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
            and (
                first_arg is None
                or (
                    node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == first_arg
                )
            )
            for node in ast.walk(statement)
        )

    authority_statement = next(
        index
        for index, statement in enumerate(transaction.body)
        if _statement_calls(
            statement,
            "_assert_lease_takeover_operation_authority",
            first_arg="conn",
        )
    )
    replay_statement = next(
        index
        for index, statement in enumerate(transaction.body)
        if _statement_calls(
            statement,
            "_replay_committed_lease_acquisition",
            first_arg="conn",
        )
    )
    cas_statement = next(
        index
        for index, statement in enumerate(transaction.body)
        if _statement_calls(statement, "_require_version")
    )
    assert authority_statement < replay_statement < cas_statement
    replay_return = transaction.body[replay_statement + 1]
    assert isinstance(replay_return, ast.If)
    returns = [node for node in ast.walk(replay_return) if isinstance(node, ast.Return)]
    assert len(returns) == 1
    assert isinstance(returns[0].value, ast.Name)
    assert returns[0].value.id == "replayed_lease"
