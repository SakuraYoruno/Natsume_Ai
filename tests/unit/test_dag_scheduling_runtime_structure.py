from __future__ import annotations

import ast
import copy
import hashlib
import inspect
from collections import Counter

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import dag_scheduling_records
from host_brain.task_runtime import dag_scheduling_runtime
from host_brain.task_runtime.dag_scheduling_runtime import (
    DurableDagSchedulingRuntime,
)


# Frozen after the scheduler became the only composition owner that can resume
# an exact multi-node COMMITTED checkpoint after every live owner fence clears.
# The current baseline also permits one exact denied Review to terminalize
# without minting a worker grant, while retaining fail-closed durable guards.
_EXECUTE_STREAM_BASELINE_SHA256 = (
    "3efe8c2b0f93576b523a3fb2f2e50f05f18290da593a4b5d4db6f4d0a17b5cce"
)
_REENTERED_OUTCOMES_BASELINE_SHA256 = (
    "22ceb5f01e2b2e47f50bd9d2ec755774b3c45e26867195c53a1bce5616978207"
)
_SUPPORTS_POLICY_BASELINE_SHA256 = (
    "0d8633080b6b026804024b9064f572a554f41eeadea6ebf8fd73ff87cf17ef00"
)
_RUN_CLAIMED_NODE_BASELINE_SHA256 = (
    "f925b2430acd70334a073a11536b34929344cc75c00bec21b7b71426b6fedbe1"
)
_GRANT_REFS_BASELINE_SHA256 = (
    "672bcdb9a7048c3ecc59e229b45a2b7d802921b883cf8025f0ec58615f98f2b7"
)
_CONSTRUCTOR_PARAMETERS = (
    "task_store",
    "runtime_outbox",
    "budget_runtime",
    "node_lifecycle",
    "node_execution_projection",
    "node_execution",
    "parallel_settlement",
    "parallel_success",
    "graph_cancellations",
    "review_runtime",
    "bounded_fanout_enabled",
    "atomic_graph_success_enabled",
    "acceptance_effects_enabled",
    "accepted_graph_finalization",
    "projection_reader",
    "fanout_event_buffer_size",
)
_DEPENDENCY_SLOTS = {
    "_store",
    "_runtime_outbox",
    "_budgets",
    "_node_lifecycle",
    "_node_execution_projection",
    "_node_execution",
    "_parallel_settlement",
    "_parallel_success",
    "_graph_cancellations",
    "_reviews",
    "_bounded_fanout_enabled",
    "_atomic_graph_success_enabled",
    "_acceptance_effects_enabled",
    "_accepted_graph_finalization",
    "_projection_reader",
    "_scheduler_checkpoint_continuation_authority",
    "_fanout_event_buffer_size",
}


def _tree(module: object) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _class(module: object, name: str) -> ast.ClassDef:
    return next(
        node
        for node in _tree(module).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(
    owner: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _sha256(node: ast.AST) -> str:
    material = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _self_dependency_calls(owner: ast.AST, dependency: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == dependency
    ]


def _constructor(owner: ast.ClassDef, field: str) -> ast.Call:
    initializer = _method(owner, "__init__")
    assignments = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == field
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    call = assignments[0].value
    assert isinstance(call, ast.Call)
    return call


def test_dag_scheduling_runtime_has_an_explicit_injected_boundary() -> None:
    runtime_tree = _tree(dag_scheduling_runtime)
    runtime_classes = [
        node for node in runtime_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in runtime_classes] == ["DurableDagSchedulingRuntime"]
    assert tuple(inspect.signature(DurableDagSchedulingRuntime).parameters) == (
        _CONSTRUCTOR_PARAMETERS
    )
    assert set(DurableDagSchedulingRuntime.__slots__) == _DEPENDENCY_SLOTS
    assert len(inspect.getsource(dag_scheduling_runtime).splitlines()) < 1500

    source = inspect.getsource(dag_scheduling_runtime).lower()
    assert "callback" not in source
    import_modules = {
        node.module or ""
        for node in runtime_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        name.endswith("coordinator")
        or "local_graph_execution_registry" in name
        for name in import_modules
    )
    forbidden_constructions = {
        "GovernedTaskCoordinator",
        "RuntimeOutbox",
        "DurableBudgetRuntime",
        "DurableNodeLifecycleRuntime",
        "DurableNodeExecutionRuntime",
        "ParallelSettlementRuntime",
        "ParallelSuccessRuntime",
        "GraphCancellationRuntime",
        "TaskNodeReviewRuntime",
        "AcceptedGraphFinalizationRuntime",
        "LocalGraphExecutionRegistry",
    }
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_constructions
        for node in ast.walk(runtime_tree)
    )


def test_extracted_methods_match_the_frozen_coordinator_semantics() -> None:
    owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    execute = copy.deepcopy(_method(owner, "execute_stream"))
    execute.name = "EXECUTE_STREAM"
    assert _sha256(execute) == _EXECUTE_STREAM_BASELINE_SHA256
    assert _sha256(_method(owner, "_reentered_parallel_outcomes")) == (
        _REENTERED_OUTCOMES_BASELINE_SHA256
    )
    assert _sha256(_method(owner, "supports_completion_policy")) == (
        _SUPPORTS_POLICY_BASELINE_SHA256
    )

    nested = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_claimed_node"
    ]
    assert len(nested) == 1
    assert _sha256(nested[0]) == _RUN_CLAIMED_NODE_BASELINE_SHA256


def test_execute_stream_dependency_call_matrices_are_frozen() -> None:
    owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    execute = _method(owner, "execute_stream")
    expected = {
        "_store": {
            "assert_dag_scheduling_ready": 1,
            "assert_parallel_settlement_decision_ready": 1,
            "assert_graph_cancellation_ready": 1,
            "assert_parallel_success_decision_ready": 1,
            "get_graph": 3,
            "read_scheduler_snapshot": 1,
            "get_node": 1,
            "resolve_node_input_artifacts": 1,
            "promote_retry_due": 1,
        },
        "_budgets": {"ensure_active": 1, "refresh": 3, "finalize": 5},
        "_node_lifecycle": {
            "transition_graph": 11,
            "transition_node": 4,
            "accept_node_output": 1,
            "cancel_waiting_review_node": 1,
        },
        "_accepted_graph_finalization": {"complete": 1},
        "_node_execution_projection": {
            "try_operation": 3,
            "attempts_for_node": 2,
            "matching_parallel_decision": 4,
            "selected_parallel_decision_outcome": 4,
        },
        "_node_execution": {"execute_stream": 8},
        "_parallel_settlement": {
            "assert_parallel_reentry_idle": 1,
            "resolve_parallel_outcomes": 3,
        },
        "_parallel_success": {"complete": 1},
        "_reviews": {
            "pre_execution_gate": 2,
            "post_execution_acceptance_gate": 1,
            "lookup_pre_execution_gate": 1,
        },
        "_graph_cancellations": {"continue_requested_graph_cancellation": 1},
        "_runtime_outbox": {"build": 1, "publish_pending": 7},
    }
    for dependency, matrix in expected.items():
        assert Counter(
            call.func.attr
            for call in _self_dependency_calls(execute, dependency)
        ) == Counter(matrix)
    outbox_calls = _self_dependency_calls(execute, "_runtime_outbox")
    assert not any(
        keyword.arg == "best_effort"
        for call in outbox_calls
        for keyword in call.keywords
    )


def test_preoperation_recovery_joins_bounded_schedule_without_review_replay() -> None:
    owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    execute = _method(owner, "execute_stream")
    classifier_calls = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "classify_preoperation_recovery_candidates"
    ]
    scheduler_calls = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "compute_dag_schedule"
    ]
    assert len(classifier_calls) == len(scheduler_calls) == 1
    classifier = classifier_calls[0]
    scheduler = scheduler_calls[0]
    operation_checks = [
        call
        for call in _self_dependency_calls(execute, "_node_execution_projection")
        if call.func.attr == "try_operation"
    ]
    snapshot_reads = [
        call
        for call in _self_dependency_calls(execute, "_store")
        if call.func.attr == "read_scheduler_snapshot"
    ]
    assert operation_checks
    assert len(snapshot_reads) == 1
    snapshot_read = snapshot_reads[0]
    assert min(call.lineno for call in operation_checks) < snapshot_read.lineno
    assert snapshot_read.lineno < classifier.lineno
    assert classifier.lineno < scheduler.lineno
    assert {keyword.arg for keyword in classifier.keywords} == {
        "graph_id",
        "graph_revision",
        "operation_free_node_ids",
    }

    def has_negated_name(test: ast.AST, name: str) -> bool:
        return any(
            isinstance(child, ast.UnaryOp)
            and isinstance(child.op, ast.Not)
            and isinstance(child.operand, ast.Name)
            and child.operand.id == name
            for child in ast.walk(test)
        )

    preoperation_live_owner_guards = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BinOp)
        and isinstance(node.test.op, ast.Sub)
        and {
            "operation_free_node_ids",
            "preoperation_recovery_node_ids",
        }.issubset(
            {
                child.id
                for child in ast.walk(node.test)
                if isinstance(child, ast.Name)
            }
        )
        and any(
            isinstance(child, ast.Name)
            and child.id == "GOVERNED_EXECUTION_IN_PROGRESS"
            for statement in node.body
            for child in ast.walk(statement)
        )
    ]
    operation_live_owner_guards = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.If)
        and {
            "raw_active_leases",
            "reservation_attempt_ids",
            "observed_ts_ms",
        }.issubset(
            {
                child.id
                for child in ast.walk(node.test)
                if isinstance(child, ast.Name)
            }
        )
        and any(
            isinstance(child, ast.Name)
            and child.id == "GOVERNED_EXECUTION_IN_PROGRESS"
            for statement in node.body
            for child in ast.walk(statement)
        )
    ]
    unresolved_failure_blocks = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Name) and child.id == "unresolved_failure"
            for child in ast.walk(node.test)
        )
    ]
    assert len(preoperation_live_owner_guards) == 1
    assert len(operation_live_owner_guards) == 1
    assert len(unresolved_failure_blocks) == 1
    live_owner_guards = (
        preoperation_live_owner_guards[0],
        operation_live_owner_guards[0],
    )
    assert classifier.lineno < min(guard.lineno for guard in live_owner_guards)
    assert max(guard.lineno for guard in live_owner_guards) < (
        unresolved_failure_blocks[0].lineno
    )
    mutation_dependencies = {
        "_budgets",
        "_node_lifecycle",
        "_parallel_settlement",
        "_parallel_success",
        "_reviews",
        "_runtime_outbox",
    }
    assert not [
        call
        for dependency in mutation_dependencies
        for call in _self_dependency_calls(execute, dependency)
        if classifier.lineno < call.lineno < max(
            guard.lineno for guard in live_owner_guards
        )
    ]

    def shortcut_testing(*names: str) -> ast.If:
        matches = [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.If)
            and set(names).issubset(
                {
                    child.id
                    for child in ast.walk(node.test)
                    if isinstance(child, ast.Name)
                }
            )
        ]
        assert len(matches) == 1
        return matches[0]

    recovery_deferred_shortcuts = (
        shortcut_testing("pre_review_node_id"),
        shortcut_testing("post_review_node_id"),
        shortcut_testing("blocked_node_id", "failed_node_id"),
    )
    assert all(
        has_negated_name(shortcut.test, "preoperation_recovery_node_ids")
        for shortcut in recovery_deferred_shortcuts
    )

    def budget_status_shortcuts(status: str) -> list[ast.If]:
        def compares_status(child: ast.AST) -> bool:
            return (
                isinstance(child, ast.Compare)
                and len(child.ops) == 1
                and isinstance(child.ops[0], ast.Eq)
                and isinstance(child.left, ast.Attribute)
                and isinstance(child.left.value, ast.Name)
                and child.left.value.id == "budget"
                and child.left.attr == "status"
                and len(child.comparators) == 1
                and isinstance(child.comparators[0], ast.Constant)
                and child.comparators[0].value == status
            )

        return [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.If)
            and any(compares_status(child) for child in ast.walk(node.test))
        ]

    def has_operation_free_precedence(shortcut: ast.If) -> bool:
        return has_negated_name(shortcut.test, "operation_free_node_ids")

    budget_exceeded_terminal_ifs = budget_status_shortcuts("EXCEEDED")
    budget_released_terminal_ifs = budget_status_shortcuts("RELEASED")
    parallel_terminal_calls = [
        call
        for call in _self_dependency_calls(execute, "_parallel_settlement")
        if call.func.attr
        in {"assert_parallel_reentry_idle", "resolve_parallel_outcomes"}
    ]
    assert len(budget_exceeded_terminal_ifs) == 2
    assert len(budget_released_terminal_ifs) == 1
    assert parallel_terminal_calls
    assert classifier.lineno < min(
        node.lineno for node in budget_exceeded_terminal_ifs
    )
    assert classifier.lineno < min(call.lineno for call in parallel_terminal_calls)
    assert all(
        has_operation_free_precedence(shortcut)
        for shortcut in (
            *budget_exceeded_terminal_ifs,
            *budget_released_terminal_ifs,
        )
    )

    ready_overrides = [
        node
        for node in ast.walk(scheduler)
        if isinstance(node, ast.IfExp)
        and isinstance(node.body, ast.Attribute)
        and node.body.attr == "READY"
        and isinstance(node.test, ast.Compare)
        and any(isinstance(operator, ast.In) for operator in node.test.ops)
        and any(
            isinstance(comparator, ast.Name)
            and comparator.id == "preoperation_recovery_node_ids"
            for comparator in node.test.comparators
        )
    ]
    assert len(ready_overrides) == 1
    assert isinstance(ready_overrides[0].orelse, ast.Call)

    review_filters = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.ListComp)
        and any(
            isinstance(child, ast.Compare)
            and any(isinstance(operator, ast.NotIn) for operator in child.ops)
            and any(
                isinstance(comparator, ast.Name)
                and comparator.id == "preoperation_recovery_node_ids"
                for comparator in child.comparators
            )
            for child in ast.walk(node)
        )
    ]
    assert len(review_filters) == 1


def test_records_module_is_one_pure_validation_helper() -> None:
    records_tree = _tree(dag_scheduling_records)
    assert not [node for node in records_tree.body if isinstance(node, ast.ClassDef)]
    functions = [
        node
        for node in records_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert [node.name for node in functions] == ["_validated_node_grant_refs"]
    assert _sha256(functions[0]) == _GRANT_REFS_BASELINE_SHA256
    assert not any(
        isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith, ast.Yield))
        for node in ast.walk(records_tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr != "items"
        for node in ast.walk(functions[0])
    )


def test_coordinator_is_a_small_registry_scoped_facade() -> None:
    assert len(inspect.getsource(coordinator).splitlines()) == 275
    owner = _class(coordinator, "GovernedTaskCoordinator")
    methods = {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {"_execute_stream_impl", "_reentered_parallel_outcomes"} & methods

    construction = _constructor(owner, "_dag_scheduling")
    assert isinstance(construction.func, ast.Name)
    assert construction.func.id == "DurableDagSchedulingRuntime"
    assert tuple(keyword.arg for keyword in construction.keywords) == (
        _CONSTRUCTOR_PARAMETERS
    )

    facade = _method(owner, "execute_stream")
    delegates = _self_dependency_calls(facade, "_dag_scheduling")
    assert Counter(call.func.attr for call in delegates) == Counter(
        {"execute_stream": 1}
    )
    registry_scopes = [
        node
        for node in facade.body
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Attribute)
            and isinstance(call.func.value.value, ast.Name)
            and call.func.value.value.id == "self"
            and call.func.value.attr == "_execution_registry"
            and call.func.attr == "track"
            for call in ast.walk(node.items[0].context_expr)
        )
    ]
    assert len(registry_scopes) == 1
    assert id(delegates[0]) in {id(node) for node in ast.walk(registry_scopes[0])}
