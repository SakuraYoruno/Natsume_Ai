from __future__ import annotations

import ast
import inspect

import host_brain.task_runtime as task_runtime
from host_brain.task_runtime import coordinator
from host_brain.task_runtime import coordinator_receipts
from host_brain.task_runtime import coordinator_records
from host_brain.task_runtime import graph_cancellation_projection
from host_brain.task_runtime import graph_cancellation_records
from host_brain.task_runtime import graph_cancellation_runtime
from host_brain.task_runtime import node_execution_runtime


RECORD_NAMES = (
    "_ParallelNodeOutcome",
    "_attempt_id",
    "_assert_durable_identity",
    "_evidence_refs",
    "_exception_reason",
    "_mapping",
    "_nested_mapping",
    "_node_input_refs",
    "_optional_record_non_negative_int",
    "_record_evidence_refs",
    "_record_non_negative_int",
    "_record_positive_int",
    "_required_record_text",
    "_reservation_result",
    "_review_evidence_refs",
    "_status",
    "_text",
    "_version",
)
RECEIPT_NAMES = (
    "GovernedExecutionReceipt",
    "_digest_from_receipt_ref",
    "_worker_receipt_ref",
    "approved_commit_event",
    "build_execution_receipt",
    "terminal_event",
)
CANCELLATION_RECORD_NAMES = (
    "_cancellation_budget_projection",
    "_cancellation_budget_target",
    "_cancellation_final_node_states",
    "_cancellation_node_states",
    "_cancellation_requested_node_states",
    "_validated_graph_cancellation_intent",
)


def _module_tree(module: object) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _top_level_definitions(module: object) -> set[str]:
    return {
        node.name
        for node in _module_tree(module).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _runtime_import_tokens(tree: ast.Module) -> set[str]:
    tokens: set[str] = set()

    def visit(node: ast.AST) -> None:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                tokens.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.update(node.module.split("."))
            for alias in node.names:
                tokens.update(alias.name.split("."))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return tokens


def test_coordinator_reexports_the_exact_extracted_value_objects_and_helpers() -> None:
    assert (
        coordinator.GovernedExecutionReceipt
        is coordinator_receipts.GovernedExecutionReceipt
    )
    assert (
        task_runtime.GovernedExecutionReceipt
        is coordinator_receipts.GovernedExecutionReceipt
    )
    for name in RECORD_NAMES:
        assert getattr(coordinator, name) is getattr(coordinator_records, name)
    for name in RECEIPT_NAMES:
        assert getattr(coordinator, name) is getattr(coordinator_receipts, name)
    runtime_record_names = {
        "_cancellation_budget_target",
        "_cancellation_final_node_states",
        "_cancellation_node_states",
        "_cancellation_requested_node_states",
        "_validated_graph_cancellation_intent",
    }
    projection_record_names = {
        "_cancellation_budget_projection",
        "_validated_graph_cancellation_intent",
    }
    assert runtime_record_names | projection_record_names == set(
        CANCELLATION_RECORD_NAMES
    )
    for name in runtime_record_names:
        assert getattr(graph_cancellation_runtime, name) is getattr(
            graph_cancellation_records,
            name,
        )
    for name in projection_record_names:
        assert getattr(graph_cancellation_projection, name) is getattr(
            graph_cancellation_records,
            name,
        )
    assert (
        graph_cancellation_runtime._exception_reason
        is coordinator_records._exception_reason
    )
    assert (
        graph_cancellation_projection._attempt_id
        is coordinator_records._attempt_id
    )


def test_extracted_modules_are_pure_and_never_reverse_import_coordinator() -> None:
    allowed_classes = {
        coordinator_records: {"_ParallelNodeOutcome"},
        coordinator_receipts: {"GovernedExecutionReceipt"},
        graph_cancellation_records: set(),
    }
    for module, expected_classes in allowed_classes.items():
        source = inspect.getsource(module)
        assert len(source.splitlines()) < 800
        tree = ast.parse(source)
        runtime_forbidden = {
            "asyncio",
            "coordinator",
            "heartbeat",
            "persistence",
            "ports",
            "review_runtime",
            "threading",
        }
        assert not runtime_forbidden & _runtime_import_tokens(tree)
        classes = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }
        assert classes == expected_classes
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__init__"
            for node in ast.walk(tree)
        )
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr
            in {
                "Lock",
                "create_task",
                "publish_pending",
                "request_graph_cancellation",
            }
        }

        assert not any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(getattr(node, "value", None), (ast.Dict, ast.List, ast.Set))
            and not (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                )
            )
            for node in tree.body
        )


def test_coordinator_uses_free_functions_instead_of_pure_bound_methods() -> None:
    migrated_methods = {
        "_assert_durable_identity",
        "_validated_graph_cancellation_intent",
        "_receipt",
        "_approved_commit_event",
        "_terminal_event",
    }
    assert not migrated_methods & coordinator.GovernedTaskCoordinator.__dict__.keys()
    coordinator_tree = _module_tree(coordinator)
    owner = next(
        node
        for node in coordinator_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GovernedTaskCoordinator"
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr in migrated_methods
        for node in ast.walk(owner)
    )
    free_calls = {
        node.func.id
        for node in ast.walk(owner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {
        "_assert_durable_identity",
        "approved_commit_event",
        "build_execution_receipt",
        "terminal_event",
    } <= free_calls

    identity_call_owners = {
        method.name
        for method in owner.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_durable_identity"
            for node in ast.walk(method)
        )
    }
    assert identity_call_owners == {"_execute_stream_impl"}
    node_execution_owner = next(
        node
        for node in _module_tree(node_execution_runtime).body
        if isinstance(node, ast.ClassDef)
        and node.name == "DurableNodeExecutionRuntime"
    )
    assert {
        method.name
        for method in node_execution_owner.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_durable_identity"
            for node in ast.walk(method)
        )
    } == {"execute_stream"}
    projection_tree = _module_tree(graph_cancellation_projection)
    projection_owner = next(
        node
        for node in projection_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "GraphCancellationProjection"
    )
    assert {
        method.name
        for method in projection_owner.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_durable_identity"
            for node in ast.walk(method)
        )
    } == {"nodes"}


def test_cancellation_orchestration_is_owned_by_leaf_services() -> None:
    assert {
        "request_graph_cancellation",
        "continue_requested_graph_cancellation",
        "continue_graph_cancellation",
    } <= graph_cancellation_runtime.GraphCancellationRuntime.__dict__.keys()
    assert {
        "cancellation_for_graph",
        "graph",
        "nodes",
        "budget_record",
        "decision_for_intent",
        "terminal_event",
    } <= graph_cancellation_projection.GraphCancellationProjection.__dict__.keys()
    assert "request_graph_cancellation" in (
        coordinator.GovernedTaskCoordinator.__dict__
    )
    assert not {
        "_continue_graph_cancellation",
        "_cancellation_nodes",
        "_cancellation_budget_record",
        "_cancellation_decision_for_intent",
        "_graph_cancellation_terminal_event",
    } & coordinator.GovernedTaskCoordinator.__dict__.keys()
    assert not {"_attempt_id", "_exception_reason"} & {
        node.name
        for node in _module_tree(coordinator).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {
        "request_graph_cancellation",
        "_continue_graph_cancellation",
    } & _top_level_definitions(graph_cancellation_records)

    coordinator_owner = next(
        node
        for node in _module_tree(coordinator).body
        if isinstance(node, ast.ClassDef)
        and node.name == "GovernedTaskCoordinator"
    )
    request = next(
        node
        for node in coordinator_owner.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "request_graph_cancellation"
    )
    request_calls = [
        node
        for node in ast.walk(request)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]
    assert len(request_calls) == 1
    assert request_calls[0].func.attr == "request_graph_cancellation"
    assert isinstance(request_calls[0].func.value, ast.Attribute)
    assert request_calls[0].func.value.attr == "_graph_cancellations"

    execute_impl = next(
        node
        for node in coordinator_owner.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_execute_stream_impl"
    )
    reentry_calls = [
        node
        for node in ast.walk(execute_impl)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "continue_requested_graph_cancellation"
    ]
    assert len(reentry_calls) == 1
    assert isinstance(reentry_calls[0].func.value, ast.Attribute)
    assert reentry_calls[0].func.value.attr == "_graph_cancellations"


def test_cancellation_services_are_small_and_hold_no_coordinator_callback() -> None:
    modules = (
        graph_cancellation_runtime,
        graph_cancellation_projection,
    )
    for module in modules:
        source = inspect.getsource(module)
        assert len(source.splitlines()) < 800
        assert "callback" not in source.lower()
        assert "coordinator" not in _runtime_import_tokens(ast.parse(source))

    assert tuple(
        inspect.signature(
            graph_cancellation_runtime.GraphCancellationRuntime
        ).parameters
    ) == (
        "task_store",
        "runtime_outbox",
        "execution_registry",
        "lease_ttl_ms",
    )
    assert tuple(
        inspect.signature(
            graph_cancellation_projection.GraphCancellationProjection
        ).parameters
    ) == ("task_store",)
    assert set(graph_cancellation_runtime.GraphCancellationRuntime.__slots__) == {
        "_store",
        "_runtime_outbox",
        "_execution_registry",
        "_lease_ttl_ms",
        "_projection",
    }
    assert graph_cancellation_projection.GraphCancellationProjection.__slots__ == (
        "_store",
    )

    runtime_tree = _module_tree(graph_cancellation_runtime)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"RuntimeOutbox", "LocalGraphExecutionRegistry"}
        for node in ast.walk(runtime_tree)
    )
    projection_tree = _module_tree(graph_cancellation_projection)
    projection_store_calls = {
        node.func.attr
        for node in ast.walk(projection_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_store"
    }
    assert projection_store_calls == {
        "get_graph_cancellation_for_graph",
        "get_graph",
        "get_node",
        "read_scheduler_snapshot",
        "get_graph_cancellation_decision_for_intent",
    }


def test_extracted_callables_have_no_coordinator_or_self_parameter() -> None:
    for function in (
        coordinator_records._assert_durable_identity,
        coordinator_records._attempt_id,
        coordinator_records._exception_reason,
        coordinator_receipts.build_execution_receipt,
        coordinator_receipts.approved_commit_event,
        coordinator_receipts.terminal_event,
        graph_cancellation_records._validated_graph_cancellation_intent,
        graph_cancellation_records._cancellation_node_states,
        graph_cancellation_records._cancellation_requested_node_states,
        graph_cancellation_records._cancellation_final_node_states,
        graph_cancellation_records._cancellation_budget_target,
        graph_cancellation_records._cancellation_budget_projection,
    ):
        parameters = tuple(inspect.signature(function).parameters)
        assert "self" not in parameters
        assert "coordinator" not in parameters


def test_extracted_records_remain_frozen_slotted_dataclasses() -> None:
    receipt = coordinator_receipts.GovernedExecutionReceipt
    outcome = coordinator_records._ParallelNodeOutcome
    assert receipt.__dataclass_params__.frozen is True
    assert outcome.__dataclass_params__.frozen is True
    assert receipt.__slots__
    assert outcome.__slots__
