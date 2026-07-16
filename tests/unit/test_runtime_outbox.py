from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
from typing import Any

import pytest

from host_brain.task_runtime import coordinator as coordinator_module
from host_brain.task_runtime import (
    graph_cancellation_runtime as graph_cancellation_runtime_module,
)
from host_brain.task_runtime import (
    parallel_settlement_runtime as parallel_settlement_runtime_module,
)
from host_brain.task_runtime import (
    node_lifecycle_runtime as node_lifecycle_runtime_module,
)
from host_brain.task_runtime import (
    node_execution_runtime as node_execution_runtime_module,
)
from host_brain.task_runtime import runtime_outbox as runtime_outbox_module
from host_brain.task_runtime.runtime_outbox import RuntimeOutbox


def _outbox_owners() -> tuple[ast.ClassDef, ...]:
    modules_and_classes = (
        (
            graph_cancellation_runtime_module,
            "GraphCancellationRuntime",
        ),
        (
            parallel_settlement_runtime_module,
            "ParallelSettlementRuntime",
        ),
        (
            node_lifecycle_runtime_module,
            "DurableNodeLifecycleRuntime",
        ),
        (
            node_execution_runtime_module,
            "DurableNodeExecutionRuntime",
        ),
        (coordinator_module, "GovernedTaskCoordinator"),
    )
    return tuple(
        next(
            node
            for node in ast.parse(inspect.getsource(module)).body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        for module, class_name in modules_and_classes
    )


def _runtime_outbox_calls(owner: ast.ClassDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_runtime_outbox"
    ]


class _Factory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        message = dict(kwargs)
        self.calls.append(message)
        return message


class _SyncPublisher:
    def __init__(self, result: Any = None) -> None:
        self.result = {"status": "PUBLISHED"} if result is None else result
        self.limits: list[int] = []

    def publish_pending(self, *, limit: int) -> Any:
        self.limits.append(limit)
        return self.result


def _runtime(
    *,
    publisher: Any | None = None,
    factory: _Factory | None = None,
    publish_limit: int = 200,
) -> tuple[RuntimeOutbox, _Factory]:
    selected_factory = factory or _Factory()
    return (
        RuntimeOutbox(
            publisher=publisher or _SyncPublisher(),
            factory=selected_factory,
            publish_limit=publish_limit,
        ),
        selected_factory,
    )


def test_build_preserves_frozen_digest_identity_and_evidence_normalization() -> None:
    runtime, factory = _runtime()
    arguments = {
        "command_id": "command-one",
        "aggregate_type": "TASK_NODE",
        "aggregate_id": "node-one",
        "aggregate_version": 3,
        "event_type": "TASK_NODE_VERIFIED",
        "attempt_id": "attempt-one",
    }

    seed = runtime.build(**arguments)
    final = runtime.build(
        **arguments,
        evidence_refs=(" evidence://one ", "evidence://one", "", "evidence://two"),
    )

    digest = hashlib.sha256(
        b"command-one\nTASK_NODE\nnode-one\n3\nTASK_NODE_VERIFIED"
    ).hexdigest()
    assert seed["outbox_id"] == final["outbox_id"] == f"outbox::governed::{digest}"
    assert seed["idempotency_key"] == final["idempotency_key"] == f"governed:{digest}"
    assert final == {
        "aggregate_type": "TASK_NODE",
        "aggregate_id": "node-one",
        "aggregate_version": 3,
        "event_type": "TASK_NODE_VERIFIED",
        "payload_ref": "taskstore://event/TASK_NODE/node-one/3",
        "idempotency_key": f"governed:{digest}",
        "attempt_id": "attempt-one",
        "evidence_refs": ("evidence://one", "evidence://two"),
        "outbox_id": f"outbox::governed::{digest}",
    }
    assert factory.calls == [seed, final]


@pytest.mark.asyncio
async def test_publish_pending_supports_sync_publishers_and_normalizes_limit() -> None:
    publisher = _SyncPublisher({"status": "PUBLISHED", "count": 2})
    runtime, _ = _runtime(publisher=publisher, publish_limit=0)

    result = await runtime.publish_pending()

    assert result == {"status": "PUBLISHED", "count": 2}
    assert result is not publisher.result
    assert publisher.limits == [1]


@pytest.mark.asyncio
async def test_one_runtime_outbox_serializes_async_publisher_calls() -> None:
    class _ConcurrentPublisher:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.calls = 0

        async def publish_pending(self, *, limit: int) -> dict[str, Any]:
            assert limit == 7
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {"status": "PUBLISHED", "call": self.calls}

    publisher = _ConcurrentPublisher()
    runtime, _ = _runtime(publisher=publisher, publish_limit=7)

    results = await asyncio.gather(
        runtime.publish_pending(),
        runtime.publish_pending(),
        runtime.publish_pending(),
    )

    assert len(results) == 3
    assert publisher.calls == 3
    assert publisher.max_active == 1


@pytest.mark.asyncio
async def test_best_effort_degrades_regular_failure_but_never_swallows_cancelled_error() -> None:
    class _FailingPublisher:
        def publish_pending(self, *, limit: int) -> dict[str, Any]:
            del limit
            raise RuntimeError("publisher unavailable")

    runtime, _ = _runtime(publisher=_FailingPublisher())
    with pytest.raises(RuntimeError, match="publisher unavailable"):
        await runtime.publish_pending()
    assert await runtime.publish_pending(best_effort=True) == {"status": "DEGRADED"}

    class _CancelledPublisher:
        async def publish_pending(self, *, limit: int) -> dict[str, Any]:
            del limit
            raise asyncio.CancelledError("shutdown")

    cancelled_runtime, _ = _runtime(publisher=_CancelledPublisher())
    with pytest.raises(asyncio.CancelledError, match="shutdown"):
        await cancelled_runtime.publish_pending(best_effort=True)


@pytest.mark.asyncio
async def test_non_mapping_publisher_result_keeps_frozen_ok_fallback() -> None:
    runtime, _ = _runtime(publisher=_SyncPublisher(object()))

    assert await runtime.publish_pending() == {"status": "OK"}


def test_runtime_outbox_is_a_leaf_without_coordinator_callback_or_store_state() -> None:
    tree = ast.parse(inspect.getsource(runtime_outbox_module))
    imported_tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_tokens.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_tokens.update(node.module.split("."))
            for alias in node.names:
                imported_tokens.update(alias.name.split("."))
    assert not {"coordinator", "persistence", "Store"} & imported_tokens

    parameters = tuple(inspect.signature(RuntimeOutbox).parameters)
    assert parameters == ("publisher", "factory", "publish_limit")
    runtime, _ = _runtime()
    assert set(vars(runtime)) == {
        "_publisher",
        "_factory",
        "_publish_limit",
        "_publish_lock",
    }
    assert isinstance(runtime._publish_lock, asyncio.Lock)  # noqa: SLF001
    assert not any(inspect.ismethod(value) for value in vars(runtime).values())


def test_coordinator_wires_one_runtime_outbox_and_keeps_budget_factory_binding() -> None:
    tree = ast.parse(inspect.getsource(coordinator_module))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GovernedTaskCoordinator"
    )
    initializer = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert tuple(inspect.signature(coordinator_module.GovernedTaskCoordinator).parameters) == (
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

    runtime_assignments = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_runtime_outbox"
            for target in node.targets
        )
    ]
    assert len(runtime_assignments) == 1
    runtime_call = runtime_assignments[0].value
    assert isinstance(runtime_call, ast.Call)
    assert isinstance(runtime_call.func, ast.Name)
    assert runtime_call.func.id == "RuntimeOutbox"
    runtime_keywords = {item.arg: item.value for item in runtime_call.keywords}
    assert {
        key: value.id if isinstance(value, ast.Name) else None
        for key, value in runtime_keywords.items()
    } == {
        "publisher": "outbox_publisher",
        "factory": "outbox_factory",
        "publish_limit": "publish_limit",
    }

    budget_calls = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "DurableBudgetRuntime"
    ]
    assert len(budget_calls) == 1
    budget_factory = next(
        item.value for item in budget_calls[0].keywords if item.arg == "outbox_factory"
    )
    assert isinstance(budget_factory, ast.Name)
    assert budget_factory.id == "outbox_factory"

    cancellation_calls = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GraphCancellationRuntime"
    ]
    assert len(cancellation_calls) == 1
    cancellation_outbox = next(
        item.value
        for item in cancellation_calls[0].keywords
        if item.arg == "runtime_outbox"
    )
    assert isinstance(cancellation_outbox, ast.Attribute)
    assert isinstance(cancellation_outbox.value, ast.Name)
    assert cancellation_outbox.value.id == "self"
    assert cancellation_outbox.attr == "_runtime_outbox"

    forbidden_fields = {
        "_publisher",
        "_outbox_factory",
        "_publish_limit",
        "_outbox_publish_lock",
    }
    assert not forbidden_fields & {
        node.attr
        for node in ast.walk(owner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert not {"_outbox", "_publish_outbox"} & {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_every_runtime_outbox_call_uses_the_shared_instance_and_explicit_command() -> None:
    calls = [
        call
        for owner in _outbox_owners()
        for call in _runtime_outbox_calls(owner)
    ]
    builds = [node for node in calls if node.func.attr == "build"]
    publishes = [node for node in calls if node.func.attr == "publish_pending"]
    assert len(builds) == 27
    assert len(publishes) == 31
    assert not [node for node in calls if node.func.attr not in {"build", "publish_pending"}]
    assert all(not node.args for node in builds)
    for build in builds:
        command_values = [item.value for item in build.keywords if item.arg == "command_id"]
        assert len(command_values) == 1
        command_value = command_values[0]
        assert isinstance(command_value, ast.Attribute)
        assert command_value.attr == "command_id"
        assert isinstance(command_value.value, ast.Name)
        assert command_value.value.id in {"admitted", "node_admission"}


def test_runtime_outbox_call_matrix_is_frozen() -> None:
    rows: list[str] = []
    best_effort_count = 0
    for owner in _outbox_owners():
        owner_call_ids = {
            id(call) for call in _runtime_outbox_calls(owner)
        }
        for method in owner.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [
                node
                for node in ast.walk(method)
                if id(node) in owner_call_ids
            ]
            for call in sorted(
                calls,
                key=lambda node: (node.lineno, node.col_offset),
            ):
                rows.append(
                    f"{method.name}:"
                    + ast.dump(
                        call,
                        annotate_fields=True,
                        include_attributes=False,
                    )
                )
                if call.func.attr == "publish_pending" and any(
                    keyword.arg == "best_effort"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in call.keywords
                ):
                    best_effort_count += 1

    assert len(rows) == 58
    assert best_effort_count == 5
    assert hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest() == (
        "26a1d181a473eb17d48b5cc01d9782e202d4fcf47e09c198f74d406514db95b6"
    )
