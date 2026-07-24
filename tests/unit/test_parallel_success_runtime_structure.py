from __future__ import annotations

import ast
import copy
import hashlib
import inspect
from collections import Counter

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import dag_scheduling_runtime
from host_brain.task_runtime import parallel_success_records
from host_brain.task_runtime import parallel_success_runtime
from host_brain.task_runtime.parallel_success_runtime import ParallelSuccessRuntime


# Frozen from fa03e0e71 GovernedTaskCoordinator._complete_parallel_success.
# The fixture intentionally survives removal of the source Coordinator method.
_COMPLETE_BASELINE_SHA256 = (
    "37512557f3e7ef077e7610b7a7269598aedf30d8730233e1f37895764e9ad5cc"
)
_ROUTE_AUTHORITY_BASELINE_SHA256 = (
    "7ac394f0ca329bec9cc25a78c30fa296ccd6fa57cf093d3d8fb430e8fcdd5fcd"
)
_DEPENDENCY_FIELDS = {
    "_store": "STORE",
    "_runtime_outbox": "OUTBOX",
    "_budgets": "BUDGETS",
    "_projection_reader": "PROJECTION_READER",
    "_node_execution_projection": "NODE_PROJECTION",
    "_complete_parallel_success": "COMPLETE",
    "complete": "COMPLETE",
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


def _function(module: object, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in _tree(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


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


def _sha256(node: ast.AST) -> str:
    material = ast.dump(
        node,
        annotate_fields=True,
        include_attributes=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class _NormalizeComplete(ast.NodeTransformer):
    """Normalize only extraction names, not executable semantics."""

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> ast.AsyncFunctionDef:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.AsyncFunctionDef)
        projected.name = "COMPLETE"
        return projected

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.Attribute)
        if isinstance(projected.value, ast.Name) and projected.value.id == "self":
            projected.attr = _DEPENDENCY_FIELDS.get(
                projected.attr,
                projected.attr,
            )
        return projected


def test_parallel_success_runtime_and_records_are_narrow_boundaries() -> None:
    runtime_tree = _tree(parallel_success_runtime)
    records_tree = _tree(parallel_success_records)
    runtime_classes = [
        node for node in runtime_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in runtime_classes] == ["ParallelSuccessRuntime"]
    assert not [node for node in records_tree.body if isinstance(node, ast.ClassDef)]
    assert {
        node.name
        for node in records_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == {"_projection_route_authority"}
    assert not any(
        isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith))
        for node in ast.walk(records_tree)
    )

    assert len(inspect.getsource(parallel_success_runtime).splitlines()) < 800
    assert len(inspect.getsource(parallel_success_records).splitlines()) < 200
    assert tuple(inspect.signature(ParallelSuccessRuntime).parameters) == (
        "task_store",
        "runtime_outbox",
        "budget_runtime",
        "projection_reader",
        "node_execution_projection",
    )
    assert set(ParallelSuccessRuntime.__slots__) == {
        "_store",
        "_runtime_outbox",
        "_budgets",
        "_projection_reader",
        "_node_execution_projection",
    }


def test_complete_matches_the_frozen_coordinator_semantics() -> None:
    owner = _class(parallel_success_runtime, "ParallelSuccessRuntime")
    method = copy.deepcopy(_method(owner, "complete"))
    normalized = _NormalizeComplete().visit(copy.deepcopy(method))
    assert _sha256(normalized) == _COMPLETE_BASELINE_SHA256

    # Exact replay remains an internal service re-entry, never a Coordinator callback.
    recursive_calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == "complete"
    ]
    assert len(recursive_calls) == 1
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_complete_parallel_success"
        for node in ast.walk(method)
    )


def test_route_authority_helper_matches_the_exact_frozen_ast() -> None:
    helper = _function(parallel_success_records, "_projection_route_authority")
    assert _sha256(helper) == _ROUTE_AUTHORITY_BASELINE_SHA256


def test_parallel_success_dependency_call_matrices_are_frozen() -> None:
    owner = _class(parallel_success_runtime, "ParallelSuccessRuntime")
    expected = {
        "_store": {
            "get_graph": 2,
            "complete_parallel_graph_success": 2,
            "read_scheduler_snapshot": 1,
            "get_latest_parallel_success_decision": 1,
            "get_node": 1,
        },
        "_runtime_outbox": {"build": 4, "publish_pending": 2},
        "_budgets": {"refresh": 1},
        "_projection_reader": {"load": 1},
        "_node_execution_projection": {"try_operation": 1},
    }
    for dependency, matrix in expected.items():
        assert Counter(
            call.func.attr for call in _self_dependency_calls(owner, dependency)
        ) == Counter(matrix)

    outbox_calls = _self_dependency_calls(owner, "_runtime_outbox")
    assert sum(
        keyword.arg == "best_effort"
        for call in outbox_calls
        for keyword in call.keywords
    ) == 0


def test_runtime_has_no_coordinator_or_adjacent_execution_authority() -> None:
    runtime_source = inspect.getsource(parallel_success_runtime)
    records_source = inspect.getsource(parallel_success_records)
    assert "callback" not in runtime_source.lower()
    assert "callback" not in records_source.lower()

    runtime_tree = _tree(parallel_success_runtime)
    import_tokens = {
        token
        for node in runtime_tree.body
        if isinstance(node, ast.ImportFrom) and node.module
        for token in node.module.split(".")
    }
    assert not {
        "coordinator",
        "parallel_settlement_runtime",
        "parallel_settlement_projection",
        "graph_cancellation",
        "node_lifecycle_runtime",
    } & import_tokens

    forbidden_fields = {
        "_coordinator",
        "_parallel_settlement",
        "_graph_cancellations",
        "_cancellation",
        "_node_lifecycle",
        "_workers",
        "_worker_resolver",
        "_worker",
    }
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr in forbidden_fields
        for node in ast.walk(runtime_tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id
            in {
                "GovernedTaskCoordinator",
                "ParallelSettlementRuntime",
                "GraphCancellationRuntime",
                "DurableNodeLifecycleRuntime",
                "Lock",
                "RLock",
                "Semaphore",
                "create_task",
            }
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"Lock", "RLock", "Semaphore", "create_task"}
        )
        for node in ast.walk(runtime_tree)
    )


def test_coordinator_wires_the_shared_runtime_without_legacy_wrapper() -> None:
    owner = _class(coordinator, "GovernedTaskCoordinator")
    coordinator_methods = {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_complete_parallel_success" not in coordinator_methods

    construction = _constructor(owner, "_parallel_success")
    assert isinstance(construction.func, ast.Name)
    assert construction.func.id == "ParallelSuccessRuntime"
    keywords = {item.arg: item.value for item in construction.keywords}
    assert set(keywords) == set(inspect.signature(ParallelSuccessRuntime).parameters)
    assert isinstance(keywords["task_store"], ast.Name)
    assert keywords["task_store"].id == "task_store"
    assert isinstance(keywords["projection_reader"], ast.Name)
    assert keywords["projection_reader"].id == "projection_reader"
    expected_fields = {
        "runtime_outbox": "_runtime_outbox",
        "budget_runtime": "_budgets",
        "node_execution_projection": "_node_execution_projection",
    }
    for keyword, field in expected_fields.items():
        value = keywords[keyword]
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "self"
        assert value.attr == field

    dag_owner = _class(dag_scheduling_runtime, "DurableDagSchedulingRuntime")
    delegates = _self_dependency_calls(dag_owner, "_parallel_success")
    assert Counter(call.func.attr for call in delegates) == Counter({"complete": 1})
