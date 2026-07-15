from __future__ import annotations

import ast
import inspect

import host_brain.task_runtime as task_runtime
from host_brain.task_runtime import coordinator
from host_brain.task_runtime import coordinator_receipts
from host_brain.task_runtime import coordinator_records
from host_brain.task_runtime import graph_cancellation_records


RECORD_NAMES = (
    "_ParallelNodeOutcome",
    "_assert_durable_identity",
    "_evidence_refs",
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
    for name in CANCELLATION_RECORD_NAMES:
        assert getattr(coordinator, name) is getattr(
            graph_cancellation_records,
            name,
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
        "_validated_graph_cancellation_intent",
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
    assert identity_call_owners == {
        "_cancellation_nodes",
        "_execute_node_stream",
        "_execute_stream_impl",
    }


def test_cancellation_orchestration_remains_owned_by_coordinator() -> None:
    assert {
        "request_graph_cancellation",
        "_continue_graph_cancellation",
        "_cancellation_nodes",
        "_cancellation_budget_record",
        "_cancellation_decision_for_intent",
        "_graph_cancellation_terminal_event",
    } <= coordinator.GovernedTaskCoordinator.__dict__.keys()
    assert not {
        "request_graph_cancellation",
        "_continue_graph_cancellation",
    } & _top_level_definitions(graph_cancellation_records)


def test_extracted_callables_have_no_coordinator_or_self_parameter() -> None:
    for function in (
        coordinator_records._assert_durable_identity,
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
