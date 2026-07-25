from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import sqlite3
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_store_types import (
    TASK_STORE_INVALID,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)
from host_brain.task_runtime import coordinator as coordinator_module
from host_brain.task_runtime import (
    dag_scheduling_runtime as dag_scheduling_runtime_module,
)
from host_brain.task_runtime import (
    graph_cancellation_runtime as graph_cancellation_runtime_module,
)
from host_brain.task_runtime import (
    parallel_settlement_runtime as parallel_settlement_runtime_module,
)
from host_brain.task_runtime import (
    parallel_success_runtime as parallel_success_runtime_module,
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
            parallel_success_runtime_module,
            "ParallelSuccessRuntime",
        ),
        (
            node_lifecycle_runtime_module,
            "DurableNodeLifecycleRuntime",
        ),
        (
            node_execution_runtime_module,
            "DurableNodeExecutionRuntime",
        ),
        (
            dag_scheduling_runtime_module,
            "DurableDagSchedulingRuntime",
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
        "graph_revision": None,
        "attempt_id": "attempt-one",
        "evidence_refs": ("evidence://one", "evidence://two"),
        "outbox_id": f"outbox::governed::{digest}",
    }
    assert factory.calls == [seed, final]


def test_graph_outbox_uses_command_identity_and_revisioned_payload_ref() -> None:
    runtime, _ = _runtime()

    revision_one = runtime.build(
        command_id="command-graph-revision-1",
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-one",
        aggregate_version=2,
        event_type="TASK_GRAPH_ACTIVE",
        graph_revision=1,
    )
    revision_two = runtime.build(
        command_id="command-graph-revision-2",
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-one",
        aggregate_version=2,
        event_type="TASK_GRAPH_ACTIVE",
        graph_revision=2,
    )

    assert revision_one["graph_revision"] == 1
    assert revision_two["graph_revision"] == 2
    assert revision_one["outbox_id"] != revision_two["outbox_id"]
    assert revision_one["idempotency_key"] != revision_two["idempotency_key"]
    assert revision_one["payload_ref"] == (
        "taskstore://event/TASK_GRAPH/graph-one@1/2"
    )
    assert revision_two["payload_ref"] == (
        "taskstore://event/TASK_GRAPH/graph-one@2/2"
    )


def test_same_command_keeps_legacy_identity_but_revision_conflict_fails_closed(
    tmp_path,
) -> None:
    runtime, _ = _runtime()
    revision_one = runtime.build(
        command_id="command-graph-stable",
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-stable-command",
        aggregate_version=0,
        event_type="TASK_GRAPH_CREATED",
        graph_revision=1,
    )
    revision_two = runtime.build(
        command_id="command-graph-stable",
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-stable-command",
        aggregate_version=0,
        event_type="TASK_GRAPH_CREATED",
        graph_revision=2,
    )

    assert revision_one["outbox_id"] == revision_two["outbox_id"]
    assert revision_one["idempotency_key"] == revision_two["idempotency_key"]
    assert revision_one["graph_revision"] == 1
    assert revision_two["graph_revision"] == 2
    assert revision_one["payload_ref"].endswith("graph-stable-command@1/0")
    assert revision_two["payload_ref"].endswith("graph-stable-command@2/0")

    db_path = tmp_path / "runtime-outbox-stable-command.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="runtime-outbox-stable-command",
        ts_ms=1,
    )
    assert migrated.success is True, migrated.as_dict()
    with sqlite3.connect(db_path) as conn:
        register_task_graph_admission_authority(conn)
        for revision in (1, 2):
            with canonical_task_graph_admission_scope(
                "graph-stable-command",
                revision,
            ):
                conn.execute(
                    """
                    INSERT INTO task_runtime_graph(
                        graph_id, revision, root_work_order_id, planner_ref,
                        status, status_version, graph_json, source_kind,
                        source_ref, legacy_snapshot_json, created_ts_ms,
                        updated_ts_ms
                    ) VALUES ('graph-stable-command', ?, ?, ?, 'CREATED', 0,
                              '{}', 'CANONICAL', NULL, NULL, 1, 1)
                    """,
                    (
                        revision,
                        f"work-order-stable-{revision}",
                        f"planner://stable/{revision}",
                    ),
                )

        def insert(message: dict[str, Any]) -> None:
            conn.execute(
                """
                INSERT INTO task_runtime_outbox(
                    outbox_id, aggregate_type, aggregate_id,
                    graph_revision, aggregate_version, event_type,
                    payload_ref, evidence_refs_json, attempt_id,
                    idempotency_key, status, available_ts_ms,
                    created_ts_ms, published_ts_ms, status_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?, 'PENDING',
                          1, 1, NULL, 0)
                """,
                (
                    message["outbox_id"],
                    message["aggregate_type"],
                    message["aggregate_id"],
                    message["graph_revision"],
                    message["aggregate_version"],
                    message["event_type"],
                    message["payload_ref"],
                    message["idempotency_key"],
                ),
            )

        insert(revision_one)
        with pytest.raises(sqlite3.IntegrityError):
            insert(revision_two)
        assert conn.execute(
            """
            SELECT graph_revision, payload_ref
            FROM task_runtime_outbox
            WHERE outbox_id = ?
            """,
            (revision_one["outbox_id"],),
        ).fetchone() == (1, revision_one["payload_ref"])


def test_outbox_message_enforces_exact_graph_revision_contract() -> None:
    def message(*, aggregate_type: str, graph_revision: object) -> OutboxMessage:
        payload_aggregate_id = "aggregate-one"
        if aggregate_type == "TASK_GRAPH" and type(graph_revision) is int:
            payload_aggregate_id = f"aggregate-one@{graph_revision}"
        return OutboxMessage.create(
            aggregate_type=aggregate_type,
            aggregate_id="aggregate-one",
            aggregate_version=1,
            event_type=f"{aggregate_type}_CHANGED",
            payload_ref=(
                f"taskstore://event/{aggregate_type}/{payload_aggregate_id}/1"
            ),
            graph_revision=graph_revision,  # type: ignore[arg-type]
        )

    message(aggregate_type="TASK_GRAPH", graph_revision=1).validate()
    message(aggregate_type="TASK_NODE", graph_revision=None).validate()
    for invalid in (None, 0, -1, True, "1"):
        with pytest.raises(TaskStoreError) as graph_error:
            message(aggregate_type="TASK_GRAPH", graph_revision=invalid).validate()
        assert graph_error.value.reason_code == TASK_STORE_INVALID
    with pytest.raises(TaskStoreError) as non_graph_error:
        message(aggregate_type="TASK_NODE", graph_revision=1).validate()
    assert non_graph_error.value.reason_code == TASK_STORE_INVALID


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
        "internal_agent_checkpoint_uow",
        "outbox_publisher",
        "outbox_factory",
        "worker_resolver",
        "verifier",
        "acceptance",
        "projection_reader",
        "review_runtime",
        "node_grant_verifier",
        "acceptance_effects_enabled",
        "bounded_fanout_enabled",
        "atomic_graph_success_enabled",
        "fanout_event_buffer_size",
        "execution_registry",
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
    assert len(builds) == 29
    assert len(publishes) == 34
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

    assert len(rows) == 63
    assert best_effort_count == 8
    assert hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest() == (
        "0e90542ce684f1281e514a3f6dda50d34765babe87a0d7ce84f9bde7e4007772"
    )
