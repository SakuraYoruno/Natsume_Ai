from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from typing import Any, Mapping

import pytest

from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)


class _Incomplete(RuntimeError):
    reason_code = "REVIEW_CONTINUATION_INCOMPLETE"


class _Store:
    def __init__(self, *, with_claim: bool = True) -> None:
        self.claimed = not with_claim
        self.version = 1
        self.ready_calls = 0
        self.reconcile_calls = 0
        self.heartbeat_versions: list[int] = []
        self.deferred: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.node_status = "WAITING_APPROVAL"
        self.failure_snapshot: dict[str, Any] = {
            "schema": "natsume.review_continuation.failure_snapshot.v1",
            "status": "RECOVERED",
            "poisoned_count": 0,
            "sampled_count": 0,
            "truncated": False,
            "last_failure_ts_ms": None,
            "incident_digest": "sha256:" + ("0" * 64),
        }

    def _record(self) -> dict[str, Any]:
        return {
            "review_id": "review-worker-1",
            "graph_id": "graph-worker",
            "graph_revision": 2,
            "node_id": "node-worker",
            "subject_version": 4,
            "resume_phase": "PRE_EXECUTION",
            "claim_owner": "review-worker",
            "claim_token": "secret-claim-token",
            "claim_expires_ts_ms": 999_999_999,
            "claim_fencing_token": 3,
            "attempt_count": 3,
            "available_ts_ms": 0,
            "last_error": None,
            "status_version": self.version,
            "review": {
                "review_id": "review-worker-1",
                "status": "CONSUMED",
                "subject_version": 4,
                "resume_phase": "PRE_EXECUTION",
            },
            "node": {
                "graph_id": "graph-worker",
                "graph_revision": 2,
                "node_id": "node-worker",
                "status": self.node_status,
                "status_version": 4,
            },
        }

    def _authority(self) -> dict[str, Any]:
        record = self._record()
        return {
            "schema_version": "Natsume.task.review_continuation_authority.v1",
            "review_id": record["review_id"],
            "graph_id": record["graph_id"],
            "graph_revision": record["graph_revision"],
            "node_id": record["node_id"],
            "subject_version": record["subject_version"],
            "resume_phase": record["resume_phase"],
            "review_status": "CONSUMED",
            "claim_owner": record["claim_owner"],
            "claim_fencing_token": record["claim_fencing_token"],
            "claim_expires_ts_ms": record["claim_expires_ts_ms"],
            "claim_token_sha256": "sha256:" + hashlib.sha256(
                record["claim_token"].encode("utf-8")
            ).hexdigest(),
            "status_version": record["status_version"],
        }

    def assert_review_continuation_ready(self, **_: Any) -> None:
        self.ready_calls += 1

    def reconcile_terminal_reviews(self) -> dict[str, int]:
        self.reconcile_calls += 1
        return {"scanned_count": 1, "enqueued_count": 0}

    def claim_pending_review_continuations(self, **binding: Any) -> list[dict[str, Any]]:
        assert binding["worker_id"] == "review-worker"
        if self.claimed:
            return []
        self.claimed = True
        return [self._record()]

    def review_continuation_failure_snapshot(self) -> dict[str, Any]:
        return dict(self.failure_snapshot)

    def validate_review_continuation_claim(
        self,
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert claim["claim_token"] == "secret-claim-token"
        return self._authority()

    def validate_review_continuation_authority(
        self,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert "claim_token" not in authority
        assert authority["claim_token_sha256"].startswith("sha256:")
        return self._authority()

    def heartbeat_review_continuation(
        self,
        review_id: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(review_id, binding)
        self.heartbeat_versions.append(binding["expected_version"])
        self.version += 1
        return self._record()

    def defer_review_continuation(
        self,
        review_id: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(review_id, binding)
        self.deferred.append(dict(binding))
        self.version += 1
        return self._record()

    def complete_review_continuation(
        self,
        review_id: str,
        **binding: Any,
    ) -> dict[str, Any]:
        self._assert_claim(review_id, binding)
        if self.node_status == "WAITING_APPROVAL":
            raise _Incomplete("checkpoint not crossed")
        self.completed.append(dict(binding))
        self.version += 1
        return {**self._record(), "completed": True}

    def _assert_claim(self, review_id: str, binding: Mapping[str, Any]) -> None:
        assert review_id == "review-worker-1"
        assert binding["worker_id"] == "review-worker"
        assert binding["claim_token"] == "secret-claim-token"
        assert binding["fencing_token"] == 3
        assert binding["expected_version"] == self.version


class _Runtime:
    def __init__(
        self,
        store: _Store,
        *,
        cross_checkpoint: bool = True,
        delay_s: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.store = store
        self.cross_checkpoint = cross_checkpoint
        self.delay_s = delay_s
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append(deepcopy(dict(claim)))
        assert "claim_token" not in claim
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        if self.cross_checkpoint:
            self.store.node_status = "READY"
        return {"accepted": True}


class _BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._release = asyncio.Event()

    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert "claim_token" not in claim
        self.started.set()
        try:
            await self._release.wait()
        finally:
            self.cancelled.set()
        return {"accepted": True}


class _Expirer:
    def __init__(self) -> None:
        self.calls = 0

    def expire_due(self) -> Mapping[str, Any]:
        self.calls += 1
        return {"expired_review_ids": (), "expired_token_count": 0}


def _worker(
    store: _Store,
    runtime: _Runtime | _BlockingRuntime,
    *,
    claim_lease_ms: int = 30_000,
    poll_interval_ms: int = 60_000,
    review_expirer: _Expirer | None = None,
    shutdown_grace_ms: int = 5_000,
    observation_sink=None,
) -> ReviewContinuationWorker:
    return ReviewContinuationWorker(
        store=store,
        runtime=runtime,
        worker_id="review-worker",
        review_expirer=review_expirer,
        claim_lease_ms=claim_lease_ms,
        poll_interval_ms=poll_interval_ms,
        retry_delay_ms=250,
        batch_limit=4,
        shutdown_grace_ms=shutdown_grace_ms,
        observation_sink=observation_sink,
    )


@pytest.mark.asyncio
async def test_worker_delivers_only_secret_free_authority_and_completes_after_crossing() -> None:
    store = _Store()
    runtime = _Runtime(store)
    worker = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert len(runtime.calls) == 1
    assert runtime.calls[0]["claim_token_sha256"].startswith("sha256:")
    assert store.completed
    assert store.deferred == []


@pytest.mark.asyncio
async def test_worker_defers_when_runtime_returns_before_review_checkpoint_crosses() -> None:
    store = _Store()
    runtime = _Runtime(store, cross_checkpoint=False)
    worker = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert store.completed == []
    assert store.deferred[0]["last_error"] == (
        "REVIEW_CONTINUATION_REVIEW_CONTINUATION_INCOMPLETE"
    )


@pytest.mark.asyncio
async def test_worker_heartbeats_slow_surface_runtime_before_completion() -> None:
    store = _Store()
    runtime = _Runtime(store, delay_s=0.06)
    worker = _worker(store, runtime, claim_lease_ms=30)

    assert await worker.run_once() == 1

    assert store.heartbeat_versions
    assert store.completed[0]["expected_version"] == store.version - 1


@pytest.mark.asyncio
async def test_worker_durably_defers_runtime_failure_without_leaking_error_text() -> None:
    class _Transient(RuntimeError):
        reason_code = "SURFACE_RUNTIME_UNAVAILABLE"

    store = _Store()
    runtime = _Runtime(store, error=_Transient("secret details"))
    worker = _worker(store, runtime)

    assert await worker.run_once() == 1

    assert store.completed == []
    assert store.deferred[0]["last_error"] == (
        "REVIEW_CONTINUATION_SURFACE_RUNTIME_UNAVAILABLE"
    )
    assert "secret" not in store.deferred[0]["last_error"].lower()


@pytest.mark.asyncio
async def test_start_asserts_0096_and_reconciles_before_background_scan() -> None:
    store = _Store(with_claim=False)
    runtime = _Runtime(store)
    worker = _worker(store, runtime, poll_interval_ms=10_000)

    await worker.start()
    await asyncio.sleep(0)
    await worker.aclose()

    assert store.ready_calls == 1
    assert store.reconcile_calls == 1


@pytest.mark.asyncio
async def test_worker_sweeps_expired_reviews_before_claiming() -> None:
    store = _Store(with_claim=False)
    runtime = _Runtime(store)
    expirer = _Expirer()
    worker = _worker(store, runtime, review_expirer=expirer)

    assert await worker.run_once() == 0

    assert expirer.calls == 1


@pytest.mark.asyncio
async def test_aclose_timeout_cancels_owned_children_and_stops_renewal() -> None:
    store = _Store()
    runtime = _BlockingRuntime()
    worker = _worker(
        store,
        runtime,
        claim_lease_ms=30,
        poll_interval_ms=60_000,
        shutdown_grace_ms=10,
    )
    current = asyncio.current_task()
    baseline = set(asyncio.all_tasks())

    await worker.start()
    await asyncio.wait_for(runtime.started.wait(), timeout=1)
    while not store.heartbeat_versions:
        await asyncio.sleep(0.005)

    await asyncio.wait_for(worker.aclose(), timeout=1)
    await asyncio.wait_for(runtime.cancelled.wait(), timeout=1)
    heartbeat_count = len(store.heartbeat_versions)
    await asyncio.sleep(0.05)

    assert len(store.heartbeat_versions) == heartbeat_count
    leaked = [
        task
        for task in asyncio.all_tasks()
        if task is not current
        and task not in baseline
        and not task.done()
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_worker_projects_poison_failure_and_recovery_into_health() -> None:
    store = _Store(with_claim=False)
    store.failure_snapshot = {
        **store.failure_snapshot,
        "status": "FAILED",
        "poisoned_count": 7,
        "sampled_count": 7,
        "last_failure_ts_ms": 1_234,
        "incident_digest": "sha256:" + ("a" * 64),
    }
    observations: list[dict[str, Any]] = []

    def observe(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        observations.append(dict(snapshot))
        return {"status": snapshot["status"]}

    worker = _worker(
        store,
        _Runtime(store),
        observation_sink=observe,
    )

    assert await worker.run_once() == 0
    assert worker.health_snapshot() == {
        "status": "DEGRADED",
        "running": False,
        "poisoned_count": 7,
        "sampled_count": 7,
        "truncated": False,
        "last_failure_ts_ms": 1_234,
        "incident_digest": "sha256:" + ("a" * 64),
        "guardian_projection_status": "READY",
        "guardian_projection_error_type": None,
    }

    store.failure_snapshot = {
        **store.failure_snapshot,
        "status": "RECOVERED",
        "poisoned_count": 0,
        "sampled_count": 0,
        "last_failure_ts_ms": None,
        "incident_digest": "sha256:" + ("b" * 64),
    }
    assert await worker.run_once() == 0
    assert worker.health_snapshot()["status"] == "HEALTHY"
    assert [item["status"] for item in observations] == [
        "FAILED",
        "RECOVERED",
    ]


@pytest.mark.asyncio
async def test_worker_fails_closed_when_guardian_projection_fails() -> None:
    store = _Store(with_claim=False)

    def broken_observer(_snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        raise OSError("private guardian database path")

    worker = _worker(
        store,
        _Runtime(store),
        observation_sink=broken_observer,
    )

    with pytest.raises(OSError, match="private guardian database path"):
        await worker.run_once()

    health = worker.health_snapshot()
    assert health["status"] == "DEGRADED"
    assert health["guardian_projection_status"] == "FAILED"
    assert health["guardian_projection_error_type"] == "OSError"
    assert "private guardian database path" not in str(health)
