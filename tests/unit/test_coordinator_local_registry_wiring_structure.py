from __future__ import annotations

import ast
import inspect

from host_brain.task_runtime import coordinator
from host_brain.task_runtime import graph_cancellation_runtime
from host_brain.task_runtime.graph_cancellation_runtime import (
    GraphCancellationRuntime,
)
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
)


def _coordinator_ast() -> ast.ClassDef:
    tree = ast.parse(inspect.getsource(coordinator))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "GovernedTaskCoordinator"
    )


def _method(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in _coordinator_ast().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _cancellation_runtime_ast() -> ast.ClassDef:
    tree = ast.parse(inspect.getsource(graph_cancellation_runtime))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == GraphCancellationRuntime.__name__
    )


def _cancellation_method(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in _cancellation_runtime_ast().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _attribute_calls(
    owner: ast.AST,
    name: str,
) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


def test_public_constructor_signature_is_unchanged_by_registry_wiring() -> None:
    assert tuple(inspect.signature(coordinator.GovernedTaskCoordinator).parameters) == (
        "task_store",
        "outbox_publisher",
        "outbox_factory",
        "worker_resolver",
        "verifier",
        "acceptance",
        "projection_reader",
        "review_runtime",
        "acceptance_effects_enabled",
        "bounded_fanout_enabled",
        "atomic_graph_success_enabled",
        "fanout_event_buffer_size",
        "owner_id",
        "lease_ttl_ms",
        "operation_ttl_ms",
        "publish_limit",
    )


def test_coordinator_owns_one_registry_without_raw_lock_or_task_map() -> None:
    owner = _coordinator_ast()
    init = _method("__init__")
    registry_assignments = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_execution_registry"
            for target in node.targets
        )
    ]
    assert len(registry_assignments) == 1
    value = registry_assignments[0].value
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name)
    assert value.func.id == LocalGraphExecutionRegistry.__name__
    assert value.args == []
    assert value.keywords == []

    source = inspect.getsource(coordinator)
    assert "_execution_registry_lock" not in source
    assert "_execution_tasks" not in source
    assert "_cancel_local_graph_tasks" not in {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    cancellation_assignments = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_graph_cancellations"
            for target in node.targets
        )
    ]
    assert len(cancellation_assignments) == 1
    cancellation_call = cancellation_assignments[0].value
    assert isinstance(cancellation_call, ast.Call)
    assert isinstance(cancellation_call.func, ast.Name)
    assert cancellation_call.func.id == GraphCancellationRuntime.__name__
    cancellation_keywords = {
        keyword.arg: keyword.value for keyword in cancellation_call.keywords
    }
    assert set(cancellation_keywords) == {
        "task_store",
        "runtime_outbox",
        "execution_registry",
        "lease_ttl_ms",
    }
    task_store_binding = cancellation_keywords["task_store"]
    assert isinstance(task_store_binding, ast.Name)
    assert task_store_binding.id == "task_store"
    outbox_binding = cancellation_keywords["runtime_outbox"]
    assert isinstance(outbox_binding, ast.Attribute)
    assert isinstance(outbox_binding.value, ast.Name)
    assert outbox_binding.value.id == "self"
    assert outbox_binding.attr == "_runtime_outbox"
    registry_binding = cancellation_keywords["execution_registry"]
    assert isinstance(registry_binding, ast.Attribute)
    assert isinstance(registry_binding.value, ast.Name)
    assert registry_binding.value.id == "self"
    assert registry_binding.attr == "_execution_registry"
    ttl_binding = cancellation_keywords["lease_ttl_ms"]
    assert isinstance(ttl_binding, ast.Attribute)
    assert isinstance(ttl_binding.value, ast.Name)
    assert ttl_binding.value.id == "self"
    assert ttl_binding.attr == "_lease_ttl_ms"


def test_execute_stream_tracks_the_complete_inner_async_iteration() -> None:
    execute_stream = _method("execute_stream")
    track_blocks = [
        node
        for node in ast.walk(execute_stream)
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "track"
            and isinstance(item.context_expr.func.value, ast.Attribute)
            and isinstance(item.context_expr.func.value.value, ast.Name)
            and item.context_expr.func.value.value.id == "self"
            and item.context_expr.func.value.attr == "_execution_registry"
            for item in node.items
        )
    ]
    assert len(track_blocks) == 1
    track = track_blocks[0]
    track_nodes = {id(node) for node in ast.walk(track)}
    inner_loops = [
        node
        for node in ast.walk(execute_stream)
        if isinstance(node, ast.AsyncFor)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and node.iter.func.attr == "execute_stream"
        and isinstance(node.iter.func.value, ast.Attribute)
        and isinstance(node.iter.func.value.value, ast.Name)
        and node.iter.func.value.value.id == "self"
        and node.iter.func.value.attr == "_dag_scheduling"
    ]
    assert len(inner_loops) == 1
    assert id(inner_loops[0]) in track_nodes
    track_call = track.items[0].context_expr
    assert isinstance(track_call, ast.Call)
    assert {
        keyword.arg for keyword in track_call.keywords
    } == {"graph_id", "graph_revision"}


def test_durable_intent_publish_finally_cancel_then_continue_order_is_preserved() -> None:
    request = _cancellation_method("request_graph_cancellation")
    publish_calls = _attribute_calls(request, "publish_pending")
    cancel_calls = _attribute_calls(request, "cancel_and_join")
    continue_calls = _attribute_calls(request, "continue_graph_cancellation")
    assert len(publish_calls) == 1
    assert len(cancel_calls) == 1
    assert len(continue_calls) == 1
    publish = publish_calls[0]
    cancel = cancel_calls[0]
    continuation = continue_calls[0]
    assert publish.lineno < cancel.lineno < continuation.lineno

    owning_try = next(
        node
        for node in ast.walk(request)
        if isinstance(node, ast.Try)
        and id(publish) in {id(item) for item in ast.walk(node)}
        and id(cancel)
        in {
            id(item)
            for statement in node.finalbody
            for item in ast.walk(statement)
        }
    )
    assert owning_try.finalbody
    assert id(cancel) not in {
        id(item)
        for statement in owning_try.body
        for item in ast.walk(statement)
    }
    assert isinstance(publish.func, ast.Attribute)
    assert isinstance(publish.func.value, ast.Attribute)
    assert isinstance(publish.func.value.value, ast.Name)
    assert publish.func.value.value.id == "self"
    assert publish.func.value.attr == "_runtime_outbox"
    assert isinstance(cancel.func, ast.Attribute)
    assert isinstance(cancel.func.value, ast.Attribute)
    assert isinstance(cancel.func.value.value, ast.Name)
    assert cancel.func.value.value.id == "self"
    assert cancel.func.value.attr == "_execution_registry"
    assert {
        keyword.arg for keyword in cancel.keywords
    } == {
        "graph_id",
        "graph_revision",
        "intent_ref",
        "timeout_seconds",
    }
