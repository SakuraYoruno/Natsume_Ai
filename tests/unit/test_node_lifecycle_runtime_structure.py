from __future__ import annotations

import ast
import copy
import hashlib
import inspect
from collections import Counter

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import accepted_graph_finalization
from host_brain.task_runtime import dag_scheduling_runtime
from host_brain.task_runtime import node_execution_runtime
from host_brain.task_runtime import node_lifecycle_runtime
from host_brain.task_runtime.node_lifecycle_runtime import (
    DurableNodeLifecycleRuntime,
)


_MIGRATED_METHODS = (
    "transition_graph",
    "transition_node",
    "accept_node_output",
    "transition_graph_with_acceptance_effects",
    "cancel_waiting_review_node",
    "transition_attempt",
    "finalize_node_budget",
    "reject_budget_charge",
    "mark_worker_failure",
    "mark_pre_reservation_failure",
)

_COORDINATOR_CALL_MATRIX = {
    "transition_graph": 16,
    "transition_node": 10,
    "accept_node_output": 2,
    "transition_graph_with_acceptance_effects": 1,
    "cancel_waiting_review_node": 1,
    "transition_attempt": 2,
    "finalize_node_budget": 8,
    "reject_budget_charge": 3,
    "mark_worker_failure": 3,
    "mark_pre_reservation_failure": 4,
}

_NODE_EXECUTION_CALL_MATRIX = {
    "transition_graph": 4,
    "transition_node": 6,
    "accept_node_output": 1,
    "transition_attempt": 2,
    "finalize_node_budget": 8,
    "reject_budget_charge": 3,
    "mark_worker_failure": 3,
    "mark_pre_reservation_failure": 4,
}

_DAG_SCHEDULING_CALL_MATRIX = {
    "transition_graph": 11,
    "transition_node": 4,
    "accept_node_output": 1,
    "cancel_waiting_review_node": 1,
}

_ACCEPTED_GRAPH_FINALIZATION_CALL_MATRIX = {
    "transition_graph": 1,
    "transition_graph_with_acceptance_effects": 1,
}

_MIGRATED_AST_DIGESTS = {
    "transition_graph": "1e386f1e77cd31cfb63f631a4923d8ad1fcc6dd9c8804b2f4cce3cf3c04ea60e",
    "transition_node": "ccb86a617fdddff46bcf9edc4d1749cc6ec83bd898714d3390d29792ee5aad3b",
    "accept_node_output": "72eb047db97543272758ae104ebb124fa18ccb0956856b50107f2a89d6c9c4f2",
    "transition_graph_with_acceptance_effects": "3af921eec7ad0ad9b6e018d51c4f643bf70ad4150b12b539db16c5c776b4eb30",
    "cancel_waiting_review_node": "92af122b371ccd0c827d23782c7d19a6c5319d78b5ad2200d1d439ff1f38db73",
    "transition_attempt": "c3f562d32a665baaad442255a70d3531cb6d3c73efb39f6afaa18e03ea69c683",
    "finalize_node_budget": "b5145090ef7d0c82c5ceac5093ba16803167155d0230c836a3f5108166713e58",
    "reject_budget_charge": "86afa2bcc95586d6f518db42013bd01206e50a116bfd42c2ffe9322abc1d3c73",
    "mark_worker_failure": "4c8ed4f26ec5b281db0ef7f1dab06a6cabcaa7fe4fdc67ce48bb679b740011dd",
    "mark_pre_reservation_failure": "37f220a8b83ee4188e47653f1ec4ab759cd60b9fd9634c359bfcfa5eb9f82791",
}


class _NormalizeMigratedMethod(ast.NodeTransformer):
    """Map public service names back to the frozen Coordinator baseline."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.AsyncFunctionDef)
        projected.name = "__MIGRATED__"
        return projected

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.Attribute)
        if (
            isinstance(projected.value, ast.Name)
            and projected.value.id == "self"
            and projected.attr in _MIGRATED_METHODS
        ):
            projected.attr = f"_{projected.attr}"
        return projected


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


def _self_assignments(owner: ast.AST, field: str) -> list[ast.Assign]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == field
            for target in node.targets
        )
    ]


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(node is target for node in ast.walk(root))


def test_node_lifecycle_runtime_is_one_narrow_real_boundary() -> None:
    tree = _tree(node_lifecycle_runtime)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert [node.name for node in classes] == ["DurableNodeLifecycleRuntime"]
    assert len(inspect.getsource(node_lifecycle_runtime).splitlines()) < 800

    assert tuple(inspect.signature(DurableNodeLifecycleRuntime).parameters) == (
        "task_store",
        "budget_runtime",
        "runtime_outbox",
        "owner_id",
    )
    assert set(DurableNodeLifecycleRuntime.__slots__) == {
        "_store",
        "_budgets",
        "_runtime_outbox",
        "_owner_id",
    }

    owner = classes[0]
    initializer = _method(owner, "__init__")
    assigned_fields = {
        target.attr
        for node in ast.walk(initializer)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert assigned_fields == {
        "_store",
        "_budgets",
        "_runtime_outbox",
        "_owner_id",
    }

    # Lifecycle orchestration may name worker/review/acceptance outcomes, but it
    # must never acquire those authorities (or synchronization/TTL machinery)
    # as object-graph dependencies of its own.
    forbidden_dependency_tokens = {
        "callback",
        "lock",
        "coordinator",
        "worker",
        "verifier",
        "acceptance",
        "review",
        "ttl",
    }
    dependency_names = {
        *inspect.signature(DurableNodeLifecycleRuntime).parameters,
        *DurableNodeLifecycleRuntime.__slots__,
    }
    assert not {
        token
        for name in dependency_names
        for token in forbidden_dependency_tokens
        if token in name.lower()
    }
    assert not any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".")[-1] == "coordinator"
        for node in tree.body
    )
    forbidden_constructions = {
        "AcceptancePort",
        "Condition",
        "Event",
        "GovernedTaskCoordinator",
        "Lock",
        "RLock",
        "ReviewStore",
        "Semaphore",
        "TaskNodeReviewRuntime",
        "TaskWorker",
        "VerifierPort",
        "WorkerPort",
        "create_task",
        "wait_for",
    }
    assert not any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id in forbidden_constructions
            or isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {"Condition", "Event", "Lock", "RLock", "Semaphore", "create_task"}
        )
        for node in ast.walk(tree)
    )


def test_all_ten_method_bodies_match_the_frozen_coordinator_baseline() -> None:
    owner = _class(node_lifecycle_runtime, "DurableNodeLifecycleRuntime")
    actual: dict[str, str] = {}
    for name in _MIGRATED_METHODS:
        method = copy.deepcopy(_method(owner, name))
        normalized = _NormalizeMigratedMethod().visit(method)
        material = ast.dump(
            normalized,
            annotate_fields=True,
            include_attributes=False,
        )
        actual[name] = hashlib.sha256(material.encode("utf-8")).hexdigest()
    assert actual == _MIGRATED_AST_DIGESTS


def test_coordinator_wires_the_same_instances_and_only_calls_the_service() -> None:
    owner = _class(coordinator, "GovernedTaskCoordinator")
    initializer = _method(owner, "__init__")
    assignments = _self_assignments(initializer, "_node_lifecycle")
    assert len(assignments) == 1
    construction = assignments[0].value
    assert isinstance(construction, ast.Call)
    assert isinstance(construction.func, ast.Name)
    assert construction.func.id == "DurableNodeLifecycleRuntime"

    keywords = {keyword.arg: keyword.value for keyword in construction.keywords}
    assert set(keywords) == {
        "task_store",
        "budget_runtime",
        "runtime_outbox",
        "owner_id",
    }
    assert isinstance(keywords["task_store"], ast.Name)
    assert keywords["task_store"].id == "task_store"
    for keyword, field in (
        ("budget_runtime", "_budgets"),
        ("runtime_outbox", "_runtime_outbox"),
        ("owner_id", "_owner_id"),
    ):
        value = keywords[keyword]
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "self"
        assert value.attr == field

    coordinator_methods = {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {f"_{name}" for name in _MIGRATED_METHODS}.intersection(
        coordinator_methods
    )

    coordinator_calls = _self_dependency_calls(owner, "_node_lifecycle")
    assert coordinator_calls == []

    dag_owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    dag_calls = _self_dependency_calls(dag_owner, "_node_lifecycle")
    dag_matrix = Counter(call.func.attr for call in dag_calls)
    assert dag_matrix == Counter(_DAG_SCHEDULING_CALL_MATRIX)
    dag_finalizer_calls = _self_dependency_calls(
        dag_owner,
        "_accepted_graph_finalization",
    )
    assert Counter(call.func.attr for call in dag_finalizer_calls) == Counter(
        {"complete": 1}
    )

    node_execution_owner = _class(
        node_execution_runtime,
        "DurableNodeExecutionRuntime",
    )
    node_execution_calls = _self_dependency_calls(
        node_execution_owner,
        "_node_lifecycle",
    )
    node_execution_matrix = Counter(
        call.func.attr for call in node_execution_calls
    )
    assert node_execution_matrix == Counter(_NODE_EXECUTION_CALL_MATRIX)
    node_finalizer_calls = _self_dependency_calls(
        node_execution_owner,
        "_graph_finalizer",
    )
    assert Counter(call.func.attr for call in node_finalizer_calls) == Counter(
        {"complete": 1}
    )

    finalizer_owner = _class(
        accepted_graph_finalization,
        "AcceptedGraphFinalizationRuntime",
    )
    finalizer_calls = _self_dependency_calls(finalizer_owner, "_node_lifecycle")
    finalizer_matrix = Counter(call.func.attr for call in finalizer_calls)
    assert finalizer_matrix == Counter(_ACCEPTED_GRAPH_FINALIZATION_CALL_MATRIX)
    assert dag_matrix + node_execution_matrix + finalizer_matrix == Counter(
        _COORDINATOR_CALL_MATRIX
    )
    assert len(dag_calls) + len(node_execution_calls) + len(finalizer_calls) == 50


def test_store_matrix_and_exact_failure_replay_are_frozen() -> None:
    owner = _class(node_lifecycle_runtime, "DurableNodeLifecycleRuntime")
    store_calls = _self_dependency_calls(owner, "_store")
    assert Counter(call.func.attr for call in store_calls) == Counter(
        {
            "transition_graph": 1,
            "transition_node": 1,
            "commit_node_acceptance": 1,
            "transition_graph_and_stage_acceptance_effects": 1,
            "request_cancel_node": 1,
            "transition_attempt": 1,
            "get_attempt": 1,
            "get_node": 1,
            "get_graph": 1,
        }
    )
    assert len(store_calls) == 9

    failure = _method(owner, "mark_pre_reservation_failure")
    selectors = [
        node.value
        for node in ast.walk(failure)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "fail_attempt" for target in node.targets)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Attribute)
        and isinstance(node.value.value.value, ast.Name)
        and node.value.value.value.id == "self"
        and node.value.value.attr == "_store"
    ]
    assert Counter(selector.attr for selector in selectors) == Counter(
        {
            "fail_running_attempt_node_bundle": 1,
            "fail_running_attempt_bundle": 1,
        }
    )

    replay_calls = [
        node
        for node in ast.walk(failure)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fail_attempt"
    ]
    assert len(replay_calls) == 2
    assert len({ast.dump(call, include_attributes=False) for call in replay_calls}) == 1
    for call in replay_calls:
        assert len(call.args) == 1
        assert isinstance(call.args[0], ast.Name)
        assert call.args[0].id == "attempt_id"
        assert len(call.keywords) == 1
        assert call.keywords[0].arg is None
        assert isinstance(call.keywords[0].value, ast.Name)
        assert call.keywords[0].value.id == "arguments"

    outer_try = next(
        node
        for node in failure.body
        if isinstance(node, ast.Try) and _contains(node, replay_calls[0])
    )
    assert len(outer_try.handlers) == 1
    first_handler = outer_try.handlers[0]
    assert isinstance(first_handler.type, ast.Name)
    assert first_handler.type.id == "Exception"
    nested_try = next(node for node in first_handler.body if isinstance(node, ast.Try))
    assert _contains(nested_try, replay_calls[1])
    assert len(nested_try.handlers) == 1
    replay_handler = nested_try.handlers[0]
    assert isinstance(replay_handler.type, ast.Name)
    assert replay_handler.type.id == "Exception"


def test_outbox_matrix_and_best_effort_boundary_are_frozen() -> None:
    owner = _class(node_lifecycle_runtime, "DurableNodeLifecycleRuntime")
    outbox_calls = _self_dependency_calls(owner, "_runtime_outbox")
    assert Counter(call.func.attr for call in outbox_calls) == Counter(
        {"build": 7, "publish_pending": 3}
    )

    publishes = [
        call for call in outbox_calls if call.func.attr == "publish_pending"
    ]
    best_effort = [
        call
        for call in publishes
        if any(
            keyword.arg == "best_effort"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
    ]
    assert len(best_effort) == 1
    worker_failure = _method(owner, "mark_worker_failure")
    assert _contains(worker_failure, best_effort[0])
    assert sum(
        keyword.arg == "best_effort"
        for call in publishes
        for keyword in call.keywords
    ) == 1
    assert any(
        isinstance(node, ast.Try)
        and any(_contains(statement, best_effort[0]) for statement in node.finalbody)
        for node in ast.walk(worker_failure)
    )
