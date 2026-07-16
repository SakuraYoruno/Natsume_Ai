from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.task_runtime.graph_cancellation_continuation_worker import (
    GraphCancellationContinuationShutdownError,
    GraphCancellationContinuationWorker,
)
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


def _admission() -> dict[str, Any]:
    graph = TaskGraph(
        graph_id="graph-continuation",
        root_work_order_id="wo-continuation",
        revision=3,
        planner_ref="planner://cancellation/continuation",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        result_node_id="node-one",
        nodes=(
            TaskNode(
                node_id="node-one",
                dependencies=(),
                route="executive_runtime",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-continuation",
        session_id="session-continuation",
        principal_id="principal-continuation",
        input_ref="command-input://request-continuation",
        input_sha256="1" * 64,
        root_work_order_id=graph.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id="executive_runtime",
        business_scope="host-chat-command",
        operation_fingerprint="2" * 64,
        created_at_ms=1,
    )
    assignment = ExecutorAssignment(
        work_order_id=graph.root_work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://wo-continuation/canonical",
        assigned_at_ms=1,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return {
        "command": command,
        "work_order": {"work_order_id": graph.root_work_order_id},
        "assignment": assignment,
        "graph": graph,
    }


class _CommandRepository:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.calls: list[tuple[str, int]] = []

    def get_by_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any] | None:
        self.calls.append((graph_id, graph_revision))
        return dict(self.record)


class _Store:
    def __init__(self, *, with_claim: bool = True) -> None:
        self.intent = {
            "intent_ref": "cancellation-intent://graph-continuation/3",
            "graph_id": "graph-continuation",
            "graph_revision": 3,
            "status": "REQUESTED",
        }
        self.coordination_version = 1
        self.claimed = not with_claim
        self.readiness_calls = 0
        self.claim_calls = 0
        self.heartbeat_versions: list[int] = []
        self.deferred: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []

    def _record(self) -> dict[str, Any]:
        return {
            "intent_ref": self.intent["intent_ref"],
            "graph_id": self.intent["graph_id"],
            "graph_revision": self.intent["graph_revision"],
            "claim_owner": "continuation-owner",
            "claim_token": "claim-token-1",
            "claim_expires_ts_ms": 999_999,
            "claim_fencing_token": 7,
            "attempt_count": 7,
            "status_version": self.coordination_version,
            "available_ts_ms": 0,
            "last_error": None,
            "intent": deepcopy(self.intent),
        }

    def assert_graph_cancellation_continuation_ready(self) -> None:
        self.readiness_calls += 1

    def claim_pending_graph_cancellations(self, **binding: Any) -> list[dict[str, Any]]:
        assert binding["worker_id"] == "continuation-owner"
        assert int(binding["lease_ms"]) > 0
        assert int(binding["limit"]) > 0
        self.claim_calls += 1
        if self.claimed:
            return []
        self.claimed = True
        return [self._record()]

    def heartbeat_graph_cancellation(
        self,
        intent_ref: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(intent_ref, binding)
        self.heartbeat_versions.append(int(binding["expected_version"]))
        self.coordination_version += 1
        return self._record()

    def defer_graph_cancellation(
        self,
        intent_ref: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(intent_ref, binding)
        self.deferred.append(dict(binding))
        self.coordination_version += 1
        return self._record()

    def complete_graph_cancellation(
        self,
        intent_ref: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(intent_ref, binding)
        assert self.intent["status"] == "FINALIZED"
        self.completed.append(dict(binding))
        self.coordination_version += 1
        return self._record()

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        assert (graph_id, revision) == ("graph-continuation", 3)
        return deepcopy(self.intent)

    def _assert_claim(self, intent_ref: str, binding: dict[str, Any]) -> None:
        assert intent_ref == self.intent["intent_ref"]
        assert binding["worker_id"] == "continuation-owner"
        assert binding["claim_token"] == "claim-token-1"
        assert binding["fencing_token"] == 7
        assert binding["expected_version"] == self.coordination_version


class _Runtime:
    def __init__(
        self,
        store: _Store,
        *,
        finalize: bool = True,
        delay_seconds: float = 0.0,
        error: BaseException | None = None,
    ) -> None:
        self.store = store
        self.finalize = finalize
        self.delay_seconds = delay_seconds
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def continue_graph_cancellation(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        if self.finalize:
            self.store.intent["status"] = "FINALIZED"
        return {"done": True}


def _worker(
    store: _Store,
    runtime: _Runtime,
    *,
    claim_lease_ms: int = 30_000,
    poll_interval_ms: int = 60_000,
    shutdown_grace_ms: int = 5_000,
) -> tuple[GraphCancellationContinuationWorker, _CommandRepository]:
    commands = _CommandRepository(_admission())
    worker = GraphCancellationContinuationWorker(
        task_store=store,
        command_repository=commands,
        cancellation_runtime=runtime,
        worker_id="continuation-owner",
        claim_lease_ms=claim_lease_ms,
        poll_interval_ms=poll_interval_ms,
        retry_delay_ms=250,
        batch_limit=4,
        shutdown_grace_ms=shutdown_grace_ms,
    )
    return worker, commands


@pytest.mark.asyncio
async def test_claim_rebuilds_exact_admission_heartbeats_and_completes_only_finalized() -> None:
    store = _Store()
    runtime = _Runtime(store, delay_seconds=0.06)
    worker, commands = _worker(store, runtime, claim_lease_ms=30)

    assert await worker.run_once() == 1

    assert commands.calls == [("graph-continuation", 3)]
    assert runtime.calls[0]["admitted"].graph_id == "graph-continuation"
    assert runtime.calls[0]["admitted"].graph_revision == 3
    assert runtime.calls[0]["replayed"] is True
    assert store.heartbeat_versions
    assert len(store.completed) == 1
    assert store.completed[0]["expected_version"] == store.coordination_version - 1
    assert store.deferred == []


@pytest.mark.asyncio
async def test_nonfinal_reconciliation_is_durably_deferred_not_falsely_completed() -> None:
    store = _Store()
    runtime = _Runtime(store, finalize=False)
    worker, _ = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert store.completed == []
    assert len(store.deferred) == 1
    assert (
        store.deferred[0]["last_error"]
        == "GRAPH_CANCELLATION_RECONCILIATION_PENDING"
    )
    assert store.deferred[0]["retry_delay_ms"] == 250


@pytest.mark.asyncio
async def test_runtime_failure_releases_claim_with_stable_nonsecret_error_code() -> None:
    class TransientFailure(RuntimeError):
        reason_code = "TRANSIENT_RECONCILIATION_FAILURE"

    store = _Store()
    runtime = _Runtime(
        store,
        error=TransientFailure("password=must-not-enter-durable-state"),
    )
    worker, _ = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert store.completed == []
    assert len(store.deferred) == 1
    error_code = store.deferred[0]["last_error"]
    assert error_code == (
        "GRAPH_CANCELLATION_CONTINUATION_TRANSIENT_RECONCILIATION_FAILURE"
    )
    assert "password" not in error_code.lower()


@pytest.mark.asyncio
async def test_untrusted_reason_text_falls_back_to_exception_class_name() -> None:
    class UnsafeReason(RuntimeError):
        reason_code = "password=must-not-enter-durable-state"

    store = _Store()
    runtime = _Runtime(store, error=UnsafeReason("another private detail"))
    worker, _ = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert store.deferred[0]["last_error"] == (
        "GRAPH_CANCELLATION_CONTINUATION_UNSAFEREASON"
    )
    assert "password" not in store.deferred[0]["last_error"].lower()


@pytest.mark.asyncio
async def test_bounded_batch_starts_every_claim_before_waiting_for_a_peer() -> None:
    class BatchStore:
        def claim_pending_graph_cancellations(self, **binding: Any) -> list[dict[str, int]]:
            assert binding["limit"] == 2
            return [{"slot": 1}, {"slot": 2}]

    started: list[int] = []
    all_started = asyncio.Event()

    class ProbeWorker(GraphCancellationContinuationWorker):
        async def _process_claim(self, raw: dict[str, int]) -> None:
            started.append(raw["slot"])
            if len(started) == 2:
                all_started.set()
            await all_started.wait()

    worker = ProbeWorker(
        task_store=BatchStore(),  # type: ignore[arg-type]
        command_repository=object(),  # type: ignore[arg-type]
        cancellation_runtime=object(),  # type: ignore[arg-type]
        worker_id="continuation-owner",
        batch_limit=2,
    )

    assert await asyncio.wait_for(worker.run_once(), timeout=0.5) == 2
    assert sorted(started) == [1, 2]


@pytest.mark.asyncio
async def test_lifecycle_scans_immediately_wakes_early_and_joins_cleanly() -> None:
    store = _Store(with_claim=False)
    runtime = _Runtime(store)
    worker, _ = _worker(store, runtime)

    await worker.start()
    for _ in range(100):
        if store.claim_calls >= 1:
            break
        await asyncio.sleep(0)
    assert worker.running
    assert store.readiness_calls == 1
    assert store.claim_calls >= 1

    first_count = store.claim_calls
    worker.wake()
    for _ in range(100):
        if store.claim_calls > first_count:
            break
        await asyncio.sleep(0)
    assert store.claim_calls > first_count

    await worker.stop()
    await worker.join()
    assert not worker.running


@pytest.mark.parametrize("shutdown_grace_ms", [0, -1])
def test_shutdown_grace_must_be_positive(shutdown_grace_ms: int) -> None:
    store = _Store(with_claim=False)
    runtime = _Runtime(store)

    with pytest.raises(ValueError, match="timing and batch values"):
        GraphCancellationContinuationWorker(
            task_store=store,
            command_repository=_CommandRepository(_admission()),
            cancellation_runtime=runtime,
            worker_id="continuation-owner",
            shutdown_grace_ms=shutdown_grace_ms,
        )


@pytest.mark.asyncio
async def test_stop_timeout_cancels_stubborn_claim_without_complete_or_defer() -> None:
    class StubbornRuntime(_Runtime):
        def __init__(self, store: _Store) -> None:
            super().__init__(store)
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def continue_graph_cancellation(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            self.store.intent["status"] = "FINALIZED"
            return {"done": True}

    store = _Store()
    runtime = StubbornRuntime(store)
    worker, _ = _worker(
        store,
        runtime,
        claim_lease_ms=30_000,
        shutdown_grace_ms=1,
    )

    await worker.start()
    await asyncio.wait_for(runtime.entered.wait(), timeout=0.5)
    await asyncio.wait_for(worker.stop(), timeout=0.5)
    await worker.join()

    assert runtime.cancelled.is_set()
    assert store.completed == []
    assert store.deferred == []
    assert store.claimed is True
    assert not worker.running
    assert not any(
        task.get_name().startswith("graph-cancellation-heartbeat::")
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    )


@pytest.mark.asyncio
async def test_shutdown_is_bounded_when_runtime_suppresses_task_cancellation() -> None:
    class IgnoringCancellationRuntime(_Runtime):
        def __init__(self, store: _Store) -> None:
            super().__init__(store)
            self.entered = asyncio.Event()
            self.ignored = asyncio.Event()
            self.release = asyncio.Event()

        async def continue_graph_cancellation(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.ignored.set()
            self.store.intent["status"] = "FINALIZED"
            return {"done": True}

    store = _Store()
    runtime = IgnoringCancellationRuntime(store)
    worker, _ = _worker(store, runtime, shutdown_grace_ms=1)
    await worker.start()
    await asyncio.wait_for(runtime.entered.wait(), timeout=0.5)

    with pytest.raises(GraphCancellationContinuationShutdownError):
        await asyncio.wait_for(worker.stop(), timeout=0.5)

    assert runtime.ignored.is_set()
    assert worker.running
    assert store.completed == []
    assert store.deferred == []

    runtime.release.set()
    for _ in range(100):
        if not worker.running:
            break
        await asyncio.sleep(0)
    await worker.join()
    assert not worker.running


@pytest.mark.asyncio
async def test_external_join_cancellation_propagates_without_stopping_worker() -> None:
    store = _Store(with_claim=False)
    runtime = _Runtime(store)
    worker, _ = _worker(store, runtime, shutdown_grace_ms=100)
    await worker.start()

    join_task = asyncio.create_task(worker.join())
    await asyncio.sleep(0)
    join_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await join_task
    assert worker.running

    await worker.stop()
    await worker.join()
    assert not worker.running
