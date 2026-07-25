from __future__ import annotations

import ast
import copy
import hashlib
import inspect
from collections import Counter

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import internal_agent_sealed_checkpoint_recovery
from host_brain.task_runtime import node_execution_projection
from host_brain.task_runtime import node_execution_preparation
from host_brain.task_runtime import node_execution_records
from host_brain.task_runtime import node_execution_runtime
from host_brain.task_runtime import node_worker_stream
from host_brain.task_runtime import operation_checkpoint_budget_recovery
from host_brain.task_runtime import operation_checkpoint_continuation
from host_brain.task_runtime import operation_recovery_checkpoint_loader
from host_brain.task_runtime import operation_recovery_runtime
from host_brain.task_runtime import preoperation_attempt_recovery
from host_brain.task_runtime.node_execution_projection import NodeExecutionProjection
from host_brain.task_runtime.node_execution_runtime import DurableNodeExecutionRuntime
from host_brain.task_runtime.operation_recovery_runtime import (
    DurableOperationRecoveryRuntime,
)


_PROJECTION_METHODS = {
    "matching_parallel_decision",
    "selected_parallel_decision_outcome",
    "terminal_replay",
    "attempts_for_node",
    "try_operation",
}
_RECORD_FUNCTIONS = {
    "build_review_context",
    "_retry_attempt_available",
    "_retry_usage_id",
    "_payload_digest",
    "_is_terminal_commit",
    "_heartbeat_failure_phase",
    "_precharged_tool_call_count",
    "_ref_token",
}
_MIGRATED_COORDINATOR_METHODS = {
    "_execute_node_stream",
    "_review_context",
    "_matching_parallel_decision",
    "_selected_parallel_decision_outcome",
    "_terminal_replay",
    "_attempts_for_node",
    "_try_operation",
}
_EXECUTE_STREAM_BASELINE_SHA256 = (
    "c127d601adb775146bafc9bebf5572b8bde97eb16826a7c6651ef79e5e5c29d2"
)
_PROJECTION_BASELINE_SHA256 = {
    "matching_parallel_decision": "d07db84d3b4cda033da5bc10b8f247156bec437b02b24ec2fdd62539a80b72a9",
    "selected_parallel_decision_outcome": "f601f3cc6506170720737b6e739492328d6707d8b53890500762da1ce038b453",
    "terminal_replay": "bc06a51a2ea908f588fb32d0a7c42cb6cc176224cf722f6fe756b2a08a677e29",
    "attempts_for_node": "003bb661287f2e0b56314f00adffcb4af68cd79a8e85329cbc443ee8ac3fab00",
    "try_operation": "6896d47dd8bef7b3ac99547aabe3be258af6f74ab434b3dfd68f250f368714e6",
}
_RECORD_BASELINE_SHA256 = {
    "build_review_context": "b3399d6f775c82ee0b9ca05833f2bc76b1b248990264d7bea77bd561b9e97f3d",
    "_retry_attempt_available": "b2c446b13cadb9bdb91e2668bc0cd58aa16b938f96366d4bab69ea632c508d44",
    "_retry_usage_id": "690d02038aef3385e2f478787ed624458c2ae7b4d75d04176d06654f92788261",
    "_payload_digest": "866a85d838ae44c0ffbd6ca0ddd33042734b9cdbc2c0de7e3c602b4cab7d3cd0",
    "_is_terminal_commit": "5c8610e835360c6c971a83c9969c58a3a3d4656c9285dd91ae1d7da28f602b0c",
    "_heartbeat_failure_phase": "03dc95808a81082205e02b3a0148c29a325e41b56642661394967dcbe2519908",
    "_precharged_tool_call_count": "75316853f38cbd6a879bd9019e543a9844f7a15d97c5ff61d72367b46e0e087b",
    "_ref_token": "0cebe503435dbc651e1552602579b4132a842c4c23e981b4bd82b7a5bda01b71",
}


def _tree(module: object) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _class(module: object, name: str) -> ast.ClassDef:
    return next(
        node
        for node in _tree(module).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(owner: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
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


def _named_dependency_calls(owner: ast.AST, dependency: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == dependency
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


class _NormalizeExecuteStream(ast.NodeTransformer):
    """Project the service body back onto the frozen Coordinator method."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.AsyncFunctionDef)
        projected.name = "__MIGRATED__"
        return projected

    def visit_Call(self, node: ast.Call) -> ast.Call:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.Call)
        if isinstance(projected.func, ast.Name) and projected.func.id == "build_review_context":
            projected.func = ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr="_review_context",
                ctx=ast.Load(),
            )
        return projected

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.Attribute)
        if (
            isinstance(projected.value, ast.Attribute)
            and isinstance(projected.value.value, ast.Name)
            and projected.value.value.id == "self"
            and projected.value.attr == "_projection"
            and projected.attr
            in {"terminal_replay", "attempts_for_node", "try_operation"}
        ):
            return ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr=f"_{projected.attr}",
                ctx=projected.ctx,
            )
        return projected


class _NormalizeExtractedCallable(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.FunctionDef)
        projected.name = "X"
        return projected

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.AsyncFunctionDef)
        projected.name = "X"
        return projected

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        projected = self.generic_visit(node)
        assert isinstance(projected, ast.Attribute)
        if (
            isinstance(projected.value, ast.Name)
            and projected.value.id == "self"
            and projected.attr in _PROJECTION_METHODS
        ):
            projected.attr = f"_{projected.attr}"
        return projected


def test_projection_and_record_helpers_are_narrow_boundaries() -> None:
    projection_tree = _tree(node_execution_projection)
    projection_classes = [
        node for node in projection_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in projection_classes] == ["NodeExecutionProjection"]
    assert tuple(inspect.signature(NodeExecutionProjection).parameters) == (
        "task_store",
        "projection_reader",
    )
    assert set(NodeExecutionProjection.__slots__) == {"_store", "_projection_reader"}
    projection_owner = projection_classes[0]
    assert _PROJECTION_METHODS <= {
        node.name
        for node in projection_owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert Counter(
        call.func.attr for call in _self_dependency_calls(projection_owner, "_store")
    ) == Counter(
        {
            "get_latest_parallel_settlement_decision": 1,
                "read_scheduler_snapshot": 2,
            "get_operation_reservation": 1,
            "get_node_artifact": 1,
            "try_resolve_checkpoint_budget_rejection_terminal": 1,
        }
    )
    assert Counter(
        call.func.attr
        for call in _self_dependency_calls(projection_owner, "_projection_reader")
    ) == Counter({"load": 1})
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr.startswith(("insert", "update", "transition", "commit", "stage"))
        for node in ast.walk(projection_owner)
    )

    records_tree = _tree(node_execution_records)
    assert not [node for node in records_tree.body if isinstance(node, ast.ClassDef)]
    assert _RECORD_FUNCTIONS == {
        node.name
        for node in records_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_modules = {
        (node.module or "").split(".")[-1]
        for node in records_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not {
        "coordinator",
        "budget_runtime",
        "ports",
        "runtime_outbox",
        "persistence",
    } & imported_modules


def test_projection_and_record_bodies_match_the_frozen_baseline() -> None:
    projection_owner = _class(node_execution_projection, "NodeExecutionProjection")
    projection_actual: dict[str, str] = {}
    for name in _PROJECTION_METHODS:
        normalized = _NormalizeExtractedCallable().visit(
            copy.deepcopy(_method(projection_owner, name))
        )
        projection_actual[name] = hashlib.sha256(
            ast.dump(
                normalized,
                annotate_fields=True,
                include_attributes=False,
            ).encode("utf-8")
        ).hexdigest()
    assert projection_actual == _PROJECTION_BASELINE_SHA256

    records_tree = _tree(node_execution_records)
    record_nodes = {
        node.name: node
        for node in records_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record_actual: dict[str, str] = {}
    for name in _RECORD_FUNCTIONS:
        normalized = _NormalizeExtractedCallable().visit(
            copy.deepcopy(record_nodes[name])
        )
        record_actual[name] = hashlib.sha256(
            ast.dump(
                normalized,
                annotate_fields=True,
                include_attributes=False,
            ).encode("utf-8")
        ).hexdigest()
    assert record_actual == _RECORD_BASELINE_SHA256


def test_coordinator_wires_shared_instances_without_wrappers() -> None:
    owner = _class(coordinator, "GovernedTaskCoordinator")
    coordinator_methods = {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not _MIGRATED_COORDINATOR_METHODS & coordinator_methods

    projection_call = _constructor(owner, "_node_execution_projection")
    assert isinstance(projection_call.func, ast.Name)
    assert projection_call.func.id == "NodeExecutionProjection"
    projection_keywords = {item.arg: item.value for item in projection_call.keywords}
    assert set(projection_keywords) == {"task_store", "projection_reader"}
    assert isinstance(projection_keywords["task_store"], ast.Name)
    assert projection_keywords["task_store"].id == "task_store"
    assert isinstance(projection_keywords["projection_reader"], ast.Name)
    assert projection_keywords["projection_reader"].id == "projection_reader"

    runtime_call = _constructor(owner, "_node_execution")
    assert isinstance(runtime_call.func, ast.Name)
    assert runtime_call.func.id == "DurableNodeExecutionRuntime"
    keywords = {item.arg: item.value for item in runtime_call.keywords}
    assert set(keywords) == set(inspect.signature(DurableNodeExecutionRuntime).parameters)
    assert isinstance(keywords["task_store"], ast.Name)
    assert keywords["task_store"].id == "task_store"
    expected_fields = {
        "runtime_outbox": "_runtime_outbox",
        "budget_runtime": "_budgets",
        "node_lifecycle": "_node_lifecycle",
        "worker_resolver": "_workers",
        "verifier": "_verifier",
        "acceptance": "_acceptance",
        "review_runtime": "_reviews",
        "projection": "_node_execution_projection",
        "owner_id": "_owner_id",
        "lease_ttl_ms": "_lease_ttl_ms",
        "operation_ttl_ms": "_operation_ttl_ms",
        "graph_finalizer": "_accepted_graph_finalization",
    }
    for keyword, field in expected_fields.items():
        value = keywords[keyword]
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "self"
        assert value.attr == field
    for keyword in ("node_grant_verifier", "projection_reader"):
        value = keywords[keyword]
        assert isinstance(value, ast.Name)
        assert value.id == keyword

    delegates = _self_dependency_calls(owner, "_node_execution")
    assert not delegates
    dag_call = _constructor(owner, "_dag_scheduling")
    dag_keywords = {item.arg: item.value for item in dag_call.keywords}
    node_runtime = dag_keywords["node_execution"]
    assert isinstance(node_runtime, ast.Attribute)
    assert isinstance(node_runtime.value, ast.Name)
    assert node_runtime.value.id == "self"
    assert node_runtime.attr == "_node_execution"


def test_node_execution_runtime_dependency_and_call_matrices_are_frozen() -> None:
    tree = _tree(node_execution_runtime)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert [node.name for node in classes] == ["DurableNodeExecutionRuntime"]
    assert len(inspect.getsource(node_execution_runtime).splitlines()) < 1_500
    expected_parameters = (
        "task_store",
        "checkpoint_uow",
        "runtime_outbox",
        "budget_runtime",
        "node_lifecycle",
        "node_grant_verifier",
        "worker_resolver",
        "verifier",
        "acceptance",
        "review_runtime",
        "projection",
        "projection_reader",
        "graph_finalizer",
        "owner_id",
        "lease_ttl_ms",
        "operation_ttl_ms",
    )
    assert tuple(inspect.signature(DurableNodeExecutionRuntime).parameters) == expected_parameters
    assert set(DurableNodeExecutionRuntime.__slots__) == {
        "_store",
        "_runtime_outbox",
        "_budgets",
        "_checkpoint_uow",
        "_checkpoint_continuation",
        "_graph_finalizer",
        "_internal_budget_rejection",
        "_node_lifecycle",
        "_node_grant_verifier",
        "_operation_recovery",
        "_workers",
        "_verifier",
        "_acceptance",
        "_reviews",
        "_projection",
        "_scheduler_checkpoint_continuation_authority",
        "_owner_id",
        "_lease_ttl_ms",
        "_operation_ttl_ms",
    }
    execute_parameters = inspect.signature(
        DurableNodeExecutionRuntime.execute_stream
    ).parameters
    assert "allow_multinode_checkpoint_continuation" not in execute_parameters
    assert "retry_due_claim" in execute_parameters
    assert "scheduler_checkpoint_continuation_authority" in execute_parameters
    owner = classes[0]
    expected_matrices = {
        "_store": {
            "get_graph": 2,
            "get_node": 3,
            "claim_ready_node": 1,
            "get_attempt": 1,
            "reserve_operation": 1,
            "get_active_lease": 1,
            "commit_operation": 1,
        },
        "_runtime_outbox": {"build": 7, "publish_pending": 16},
        "_budgets": {
            "ensure_active": 1,
            "precharge": 1,
            "guard_execution_time": 1,
            "settle_worker": 1,
            "settle_interrupted_worker": 1,
            "finalize": 1,
            "refresh": 1,
        },
        "_node_lifecycle": {
                "transition_graph": 3,
            "transition_node": 6,
            "accept_node_output": 1,
            "transition_attempt": 2,
            "finalize_node_budget": 5,
            "reject_budget_charge": 3,
            "mark_pre_reservation_failure": 4,
        },
        "_workers": {"resolve": 1},
        "_verifier": {"verify": 1},
        "_acceptance": {"judge": 1},
        "_reviews": {"post_execution_acceptance_gate": 1},
        "_projection": {
            "terminal_replay": 1,
            "attempts_for_node": 1,
            "try_operation": 2,
        },
        "_operation_recovery": {
            "assert_existing_lineage": 1,
            "reconcile_reservation_state": 1,
            "takeover_committed": 1,
            "commit_or_reconcile": 1,
            "unresolved_projection": 1,
        },
        "_checkpoint_continuation": {"resume": 1},
        "_graph_finalizer": {"complete": 1},
        "_internal_budget_rejection": {"reject": 1},
    }
    for dependency, expected in expected_matrices.items():
        assert Counter(
            call.func.attr for call in _self_dependency_calls(owner, dependency)
        ) == Counter(expected)

    import_tokens = {
        token
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
        for token in node.module.split(".")
    }
    assert not {"coordinator", "graph_cancellation", "parallel_settlement"} & import_tokens
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"Lock", "RLock", "Semaphore", "create_task"}
        for node in ast.walk(tree)
    )


def test_preparation_and_preoperation_helpers_preserve_authority_boundaries() -> None:
    preparation_tree = _tree(node_execution_preparation)
    assert not [node for node in preparation_tree.body if isinstance(node, ast.ClassDef)]
    preparation_functions = {
        node.name: node
        for node in preparation_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert set(preparation_functions) == {
        "resolve_node_input_artifacts",
        "preverify_node_claim_authority",
        "load_preoperation_recovery_candidate",
        "bind_preoperation_recovery_attempt",
    }
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(preparation_tree, "task_store")
    ) == Counter(
        {
            "resolve_node_input_artifacts": 1,
            "read_scheduler_snapshot": 1,
        }
    )
    worker_execution_names = {
        "execute",
        "execute_stream",
        "worker_event_stream",
    }
    assert not any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in worker_execution_names
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id in worker_execution_names
            )
        )
        for node in ast.walk(preparation_tree)
    )

    grant_boundary = preparation_functions["preverify_node_claim_authority"]
    assert tuple(
        inspect.signature(
            node_execution_preparation.preverify_node_claim_authority
        ).parameters
    ) == ("verifier", "admitted", "command", "work_order", "node_grant_ref")
    verifier_lookups = [
        call.args[1].value
        for call in ast.walk(grant_boundary)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "getattr"
        and len(call.args) >= 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "verifier"
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    ]
    assert Counter(verifier_lookups) == Counter({"verify_for_claim": 1, "verify": 1})
    verifier_calls = [
        call
        for call in ast.walk(grant_boundary)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "verify_for_claim"
    ]
    assert len(verifier_calls) == 1
    assert {item.arg for item in verifier_calls[0].keywords} == {
        "grant_ref",
        "request_id",
        "session_id",
        "principal_id",
        "graph_id",
        "root_work_order_id",
        "graph_revision",
        "node_id",
        "route",
        "target_id",
        "parent_grant_ref",
    }

    recovery_tree = _tree(preoperation_attempt_recovery)
    recovery_classes = [
        node for node in recovery_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in recovery_classes] == [
        "PreOperationRecoveryCandidate"
    ]
    recovery_functions = {
        node.name: node
        for node in recovery_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "classify_preoperation_recovery_candidates",
        "acquire_preoperation_takeover_lease",
    } <= set(recovery_functions)
    assert not any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in worker_execution_names | {"resolve"}
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id in worker_execution_names
            )
        )
        for node in ast.walk(recovery_tree)
    )
    recovery_imports = {
        (node.module or "").split(".")[-1]
        for node in recovery_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not {"worker_registry", "node_worker_stream"} & recovery_imports

    takeover = recovery_functions["acquire_preoperation_takeover_lease"]
    assert Counter(
        call.func.attr for call in _named_dependency_calls(takeover, "task_store")
    ) == Counter({"get_active_lease": 1, "acquire_lease": 1, "get_attempt": 1})
    acquire_calls = [
        call
        for call in _named_dependency_calls(takeover, "task_store")
        if call.func.attr == "acquire_lease"
    ]
    assert len(acquire_calls) == 1
    acquire_keywords = {item.arg: item.value for item in acquire_calls[0].keywords}
    absence_binding = acquire_keywords["absent_operation_reservation_id"]
    assert isinstance(absence_binding, ast.Attribute)
    assert isinstance(absence_binding.value, ast.Name)
    assert absence_binding.value.id == "admitted"
    assert absence_binding.attr == "reservation_id"

    runtime_method = _method(
        _class(node_execution_runtime, "DurableNodeExecutionRuntime"),
        "execute_stream",
    )
    boundary_call_list = [
        call
        for call in ast.walk(runtime_method)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id
        in {
            "resolve_node_input_artifacts",
            "preverify_node_claim_authority",
            "load_preoperation_recovery_candidate",
            "bind_preoperation_recovery_attempt",
            "acquire_preoperation_takeover_lease",
        }
    ]
    assert Counter(call.func.id for call in boundary_call_list) == Counter(
        {
            "resolve_node_input_artifacts": 1,
            "preverify_node_claim_authority": 1,
            "load_preoperation_recovery_candidate": 1,
            "bind_preoperation_recovery_attempt": 1,
            "acquire_preoperation_takeover_lease": 1,
        }
    )
    boundary_calls = {call.func.id: call for call in boundary_call_list}
    grant_call = boundary_calls["preverify_node_claim_authority"]
    grant_keywords = {item.arg: item.value for item in grant_call.keywords}
    verifier_binding = grant_keywords["verifier"]
    assert isinstance(verifier_binding, ast.Attribute)
    assert isinstance(verifier_binding.value, ast.Name)
    assert verifier_binding.value.id == "self"
    assert verifier_binding.attr == "_node_grant_verifier"
    grant_ref_binding = grant_keywords["node_grant_ref"]
    assert isinstance(grant_ref_binding, ast.Name)
    assert grant_ref_binding.id == "node_grant_ref"

    mutation_lines = [
        call.lineno
        for call in ast.walk(runtime_method)
        if isinstance(call, ast.Call)
        and (
            (
                isinstance(call.func, ast.Name)
                and call.func.id == "acquire_preoperation_takeover_lease"
            )
            or (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "claim_ready_node"
            )
        )
    ]
    assert len(mutation_lines) == 2
    assert grant_call.lineno < min(mutation_lines)


def test_online_recovery_is_split_into_focused_non_worker_boundaries() -> None:
    recovery_tree = _tree(operation_recovery_runtime)
    recovery_classes = [
        node for node in recovery_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in recovery_classes] == [
        "CommittedOperationCheckpoint",
        "DurableOperationRecoveryRuntime",
    ]
    assert len(inspect.getsource(operation_recovery_runtime).splitlines()) < 400
    assert tuple(inspect.signature(DurableOperationRecoveryRuntime).parameters) == (
        "task_store",
        "projection_reader",
        "runtime_outbox",
        "owner_id",
        "lease_ttl_ms",
    )
    assert not hasattr(DurableOperationRecoveryRuntime, "execute_stream")

    continuation_tree = _tree(operation_checkpoint_continuation)
    continuation_classes = [
        node for node in continuation_tree.body if isinstance(node, ast.ClassDef)
    ]
    assert [node.name for node in continuation_classes] == [
        "CommittedCheckpointContinuation"
    ]
    assert len(inspect.getsource(operation_checkpoint_continuation).splitlines()) < 550
    continuation_owner = continuation_classes[0]
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "_workers"
        for node in ast.walk(continuation_owner)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"resolve", "execute_stream"}
        for node in ast.walk(continuation_owner)
    )


def test_extracted_recovery_helpers_keep_narrow_authority_boundaries() -> None:
    sealed_tree = _tree(internal_agent_sealed_checkpoint_recovery)
    assert not [node for node in sealed_tree.body if isinstance(node, ast.ClassDef)]
    assert {
        node.name
        for node in sealed_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == {"recover_internal_agent_sealed_checkpoint"}
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(sealed_tree, "task_store")
    ) == Counter(
        {
            "get_lease": 1,
            "resolve_node_input_artifacts": 1,
        }
    )
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(sealed_tree, "checkpoint_uow")
    ) == Counter({"commit_internal_agent_recovered_checkpoint": 1})
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(sealed_tree, "worker_resolver")
    ) == Counter({"resolve": 1})
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(sealed_tree, "operation_recovery")
    ) == Counter({"commit_or_reconcile": 1})
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(sealed_tree, "runtime_outbox")
    ) == Counter({"build": 1})
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr
        in {
            "transition_attempt",
            "transition_node",
            "transition_graph",
            "finalize_node_budget",
        }
        for node in ast.walk(sealed_tree)
    )

    stream_tree = _tree(node_worker_stream)
    assert not [node for node in stream_tree.body if isinstance(node, ast.ClassDef)]
    assert {
        node.name
        for node in stream_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == {
        "worker_event_stream",
        "bounded_worker_event_stream",
        "_worker_elapsed_ms",
        "_maybe_await",
        "stop_heartbeat_after_interruption",
    }
    stream_imports = {
        (node.module or "").split(".")[-1]
        for node in stream_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not {"ports", "runtime_outbox", "persistence"} & stream_imports

    loader_tree = _tree(operation_recovery_checkpoint_loader)
    assert not [node for node in loader_tree.body if isinstance(node, ast.ClassDef)]
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(loader_tree, "task_store")
    ) == Counter(
        {
            "get_committed_operation_checkpoint": 1,
            "read_scheduler_snapshot": 1,
        }
    )
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(loader_tree, "projection_reader")
    ) == Counter({"load": 1})
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"resolve", "execute", "execute_stream"}
        for node in ast.walk(loader_tree)
    )

    budget_tree = _tree(operation_checkpoint_budget_recovery)
    assert not [node for node in budget_tree.body if isinstance(node, ast.ClassDef)]
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(budget_tree, "budget_runtime")
    ) == Counter(
        {"reconcile_worker_checkpoint": 1, "settle_worker_checkpoint": 1}
    )
    assert Counter(
        call.func.attr
        for call in _named_dependency_calls(budget_tree, "runtime_outbox")
    ) == Counter({"publish_pending": 1})
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr
        in {
            "build",
            "transition_attempt",
            "transition_node",
            "transition_graph",
            "finalize_node_budget",
            "mark_worker_failure",
        }
        for node in ast.walk(budget_tree)
    )


def test_execute_stream_matches_the_frozen_method_and_exception_structure() -> None:
    owner = _class(node_execution_runtime, "DurableNodeExecutionRuntime")
    method = copy.deepcopy(_method(owner, "execute_stream"))
    normalized = _NormalizeExecuteStream().visit(method)
    material = ast.dump(
        normalized,
        annotate_fields=True,
        include_attributes=False,
    )
    assert hashlib.sha256(material.encode("utf-8")).hexdigest() == (
        _EXECUTE_STREAM_BASELINE_SHA256
    )

    handlers = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.ExceptHandler) and node.type is not None
    ]
    assert any(
        isinstance(handler.type, ast.Attribute)
        and handler.type.attr == "CancelledError"
        for handler in handlers
    )
    assert any(
        isinstance(handler.type, ast.Name) and handler.type.id == "BaseException"
        for handler in handlers
    )
    assert any(
        isinstance(handler.type, ast.Name)
        and handler.type.id == "RetryableWorkerPreparationError"
        for handler in handlers
    )
