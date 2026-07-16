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


# Frozen from 65219b5f0 GovernedTaskCoordinator before the eighteenth cut.
_EXECUTE_STREAM_BASELINE_SHA256 = (
    "d56e72b39e37802fc281babbcf87279f02eda2ce2f9642c06fb788984fce7fae"
)
_REENTERED_OUTCOMES_BASELINE_SHA256 = (
    "a26863cbe141c9bc8ce85633b45e3e2881c994fc99656732c939391320960058"
)
_SUPPORTS_POLICY_BASELINE_SHA256 = (
    "0d8633080b6b026804024b9064f572a554f41eeadea6ebf8fd73ff87cf17ef00"
)
_RUN_CLAIMED_NODE_BASELINE_SHA256 = (
    "caaa5a08eaca9d053cf8c6870ad99c827675e0653d4b2ce542a123962dc241b3"
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
    "_projection_reader",
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
            "get_graph": 2,
            "read_scheduler_snapshot": 1,
            "get_node": 1,
            "promote_retry_due": 1,
        },
        "_budgets": {"ensure_active": 1, "refresh": 2, "finalize": 6},
        "_node_lifecycle": {
            "transition_graph": 12,
            "transition_node": 5,
            "cancel_waiting_review_node": 1,
        },
        "_node_execution_projection": {
            "try_operation": 2,
            "attempts_for_node": 2,
            "matching_parallel_decision": 4,
            "selected_parallel_decision_outcome": 4,
        },
        "_node_execution": {"execute_stream": 6},
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
        "_runtime_outbox": {"build": 1, "publish_pending": 6},
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
    assert len(inspect.getsource(coordinator).splitlines()) == 257
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
