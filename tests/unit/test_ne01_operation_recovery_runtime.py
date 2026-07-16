from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.internal_task_runtime import _AgentProjectionReader
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
)
from host_brain.task_runtime.admission import AdmittedExecution, AdmittedTaskNode
from host_brain.task_runtime.errors import GovernedRuntimeError
from host_brain.task_runtime.node_execution_records import _payload_digest
from host_brain.task_runtime.operation_recovery_runtime import (
    DurableOperationRecoveryRuntime,
)
from host_brain.task_runtime.operation_checkpoint_validation import (
    accepted_worker_budget_usage,
)


class _Store:
    def __init__(
        self,
        reservation: dict[str, Any] | None = None,
        *,
        budget_usage: list[dict[str, Any]] | None = None,
    ) -> None:
        self.reservation = reservation
        self.budget_usage = (
            list(budget_usage)
            if budget_usage is not None
            else [
                {
                    "budget_id": "budget-1",
                    "usage_id": "budget-usage://worker/attempt-1",
                    "graph_id": "graph-1",
                    "graph_revision": 1,
                    "node_id": "node-1",
                    "attempt_id": "attempt-1",
                    "charge_kind": "WORKER_ELAPSED",
                    "accepted": True,
                }
            ]
        )

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        assert reservation_id == "reservation-1"
        return dict(self.reservation) if self.reservation is not None else None

    def reconcile_operation_reservation_state(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        value = self.get_operation_reservation(reservation_id)
        assert value is not None
        return value

    def get_committed_operation_checkpoint(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        reservation = dict(self.reservation or _exact_commit())
        assert reservation_id == reservation["reservation_id"]
        receipt_ref = reservation["receipt_ref"]
        tail = receipt_ref.rsplit("/", 1)[-1]
        digest_ref = (
            f"sha256://execution/{tail}"
            if len(tail) == 64
            else "sha256://execution/" + "f" * 64
        )
        return {
            "reservation": reservation,
            "attempt": {
                **_attempt(),
                "attempt_id": reservation["attempt_id"],
            },
            "lease": {
                "lease_id": reservation["lease_id"],
                "attempt_id": reservation["attempt_id"],
                "owner_id": reservation["owner_id"],
                "fencing_token": reservation["fencing_token"],
            },
            "outbox": {
                "aggregate_type": "OPERATION_RESERVATION",
                "aggregate_id": reservation_id,
                "aggregate_version": reservation["status_version"],
                "event_type": "OPERATION_COMMITTED",
                "attempt_id": reservation["attempt_id"],
                "evidence_refs": [receipt_ref, digest_ref],
            },
            "checkpoint_digest_ref": digest_ref,
        }

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        assert (graph_id, revision) == ("graph-1", 1)
        return {
            "budget_usage": [dict(item) for item in self.budget_usage]
        }


class _Reader:
    def __init__(self, projection: dict[str, Any]) -> None:
        self.projection = projection

    def load(self, receipt_ref: str) -> dict[str, Any]:
        return dict(self.projection)


def _admitted(*, worker_route: str = "skill:demo") -> AdmittedExecution:
    return AdmittedExecution(
        command_id="command-1",
        work_order_id="work-order-1",
        assignment_id="assignment-1",
        executor="canonical",
        graph_id="graph-1",
        graph_revision=1,
        nodes=(
            AdmittedTaskNode(
                node_id="node-1",
                worker_route=worker_route,
                dependencies=(),
                acceptance_criteria=("verified",),
                operation_fingerprint="fingerprint-1",
                reservation_id="reservation-1",
            ),
        ),
        business_scope="scope-1",
    )


def _attempt() -> dict[str, Any]:
    return {
        "attempt_id": "attempt-1",
        "graph_id": "graph-1",
        "graph_revision": 1,
        "node_id": "node-1",
        "operation_scope": "scope-1",
        "operation_fingerprint": "fingerprint-1",
        "status": "SUCCEEDED",
        "status_version": 4,
    }


def _command() -> Any:
    return SimpleNamespace(
        command_id="command-1",
        request_id="request-1",
        route_type="skill",
        target_id="demo",
    )


def _reservation(receipt_ref: str) -> dict[str, Any]:
    return {
        "reservation_id": "reservation-1",
        "business_scope": "scope-1",
        "operation_fingerprint": "fingerprint-1",
        "attempt_id": "attempt-1",
        "lease_id": "lease-1",
        "owner_id": "owner-old",
        "fencing_token": 7,
        "status": "COMMITTED",
        "status_version": 2,
        "receipt_ref": receipt_ref,
    }


def _runtime(
    *,
    store: Any | None = None,
    projection: dict[str, Any] | None = None,
) -> DurableOperationRecoveryRuntime:
    if store is None and projection is not None:
        projection_receipt = str(
            projection.get("receipt_ref") or projection.get("projection_id") or ""
        )
        store = _Store(_reservation(projection_receipt))
    return DurableOperationRecoveryRuntime(
        task_store=store or _Store(),
        projection_reader=_Reader(projection) if projection is not None else None,
        runtime_outbox=object(),  # not reached by terminal-attempt projection tests
        owner_id="owner-new",
        lease_ttl_ms=100,
    )


def _exact_commit(**overrides: Any) -> dict[str, Any]:
    value = {
        "reservation_id": "reservation-1",
        "business_scope": "scope-1",
        "operation_fingerprint": "fingerprint-1",
        "attempt_id": "attempt-1",
        "lease_id": "lease-1",
        "owner_id": "owner-old",
        "fencing_token": 7,
        "status": "COMMITTED",
        "status_version": 2,
        "receipt_ref": "receipt://exact",
    }
    value.update(overrides)
    return value


async def _commit(runtime: DurableOperationRecoveryRuntime, commit: Any) -> dict[str, Any]:
    return await runtime.commit_or_reconcile(
        commit=commit,
        reservation_id="reservation-1",
        business_scope="scope-1",
        operation_fingerprint="fingerprint-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        owner_id="owner-old",
        fencing_token=7,
        expected_version=1,
        receipt_ref="receipt://exact",
        checkpoint_digest_ref="sha256://execution/" + "f" * 64,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("reservation_id", "reservation-other"),
        ("receipt_ref", "receipt://other"),
        ("status_version", 9),
        ("fencing_token", "7"),
    ),
)
async def test_successful_commit_response_must_match_exact_checkpoint(
    field: str,
    wrong: Any,
) -> None:
    runtime = _runtime()

    with pytest.raises(GovernedRuntimeError):
        await _commit(runtime, lambda: _exact_commit(**{field: wrong}))


@pytest.mark.asyncio
async def test_response_loss_rejects_mismatched_read_after_write() -> None:
    runtime = _runtime(
        store=_Store(_exact_commit(receipt_ref="receipt://wrong")),
    )

    def _lost() -> None:
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError, match="response lost"):
        await _commit(runtime, _lost)


@pytest.mark.asyncio
async def test_response_loss_accepts_only_exact_read_after_write() -> None:
    exact = _exact_commit()
    runtime = _runtime(store=_Store(exact))

    def _lost() -> None:
        raise TimeoutError("response lost")

    assert await _commit(runtime, _lost) == exact


@pytest.mark.asyncio
async def test_malformed_checkpoint_numeric_binding_fails_as_governed_error() -> None:
    exact = _exact_commit()

    class _MalformedCheckpointStore(_Store):
        def get_committed_operation_checkpoint(
            self,
            reservation_id: str,
        ) -> dict[str, Any]:
            checkpoint = super().get_committed_operation_checkpoint(reservation_id)
            checkpoint["outbox"]["aggregate_version"] = "2"
            return checkpoint

    runtime = _runtime(store=_MalformedCheckpointStore(exact))

    with pytest.raises(GovernedRuntimeError, match="Outbox evidence drifted"):
        await _commit(runtime, lambda: exact)


def _worker_projection(projection_id: str) -> dict[str, Any]:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": "command-1",
        "request_id": "request-1",
        "narrative": "done",
        "route_type": "skill",
        "target_id": "demo",
        "graph_id": "graph-1",
        "graph_revision": 1,
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "node_grant_ref": "grant://node/1",
        "audit_refs": {"result": "audit://result/1"},
        "budget_usage": {
            "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
            "usage_id": "budget-usage://worker/attempt-1",
            "measurements": {
                "token_count": 1,
                "retry_count": 0,
                "model_cost_micros": 1,
            },
            "evidence_refs": ["model-budget://worker/attempt-1"],
        },
    }
    return {
        **body,
        "projection_id": projection_id,
        "payload_digest": _payload_digest(body),
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


@pytest.mark.asyncio
async def test_worker_projection_preserves_its_store_bound_payload_digest() -> None:
    body = _worker_projection("worker-projection://test/pending")
    digest = body["payload_digest"]
    receipt_ref = f"worker-projection://test/{digest.removeprefix('sha256:')}"
    body["projection_id"] = receipt_ref
    runtime = _runtime(projection=body)

    checkpoint = await runtime.takeover_committed(
        admitted=_admitted(),
        command=_command(),
        reservation=_reservation(receipt_ref),
        latest_attempt=_attempt(),
        node_grant_ref=None,
        budget_id="budget-1",
    )

    assert checkpoint.payload_digest == digest
    assert checkpoint.payload_digest_source == "receipt_identity_digest"
    assert checkpoint.worker_commit["receipt_ref"] == receipt_ref
    assert _payload_digest(checkpoint.worker_commit) != checkpoint.payload_digest


@pytest.mark.asyncio
async def test_multiple_accepted_worker_budget_usages_block_takeover() -> None:
    projection = _worker_projection("worker-projection://test/pending")
    receipt_ref = (
        "worker-projection://test/"
        + projection["payload_digest"].removeprefix("sha256:")
    )
    projection["projection_id"] = receipt_ref
    primary_usage = {
        "budget_id": "budget-1",
        "usage_id": "budget-usage://worker/attempt-1",
        "graph_id": "graph-1",
        "graph_revision": 1,
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "charge_kind": "WORKER_ELAPSED",
        "accepted": True,
    }
    duplicate_usage = {
        **primary_usage,
        "usage_id": "budget-usage://worker/attempt-1/duplicate",
    }
    runtime = DurableOperationRecoveryRuntime(
        task_store=_Store(
            _reservation(receipt_ref),
            budget_usage=[primary_usage, duplicate_usage],
        ),
        projection_reader=_Reader(projection),
        runtime_outbox=object(),
        owner_id="owner-new",
        lease_ttl_ms=100,
    )

    with pytest.raises(
        GovernedRuntimeError,
        match="lacks one accepted worker BudgetUsage",
    ):
        await runtime.takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


def test_projection_without_worker_budget_usage_identity_is_not_recoverable() -> None:
    with pytest.raises(
        GovernedRuntimeError,
        match="projection lacks worker BudgetUsage identity",
    ):
        accepted_worker_budget_usage(
            {
                "budget_usage": [
                    {
                        "budget_id": "budget-1",
                        "usage_id": "budget-usage://worker/attempt-1",
                        "attempt_id": "attempt-1",
                        "charge_kind": "WORKER_ELAPSED",
                        "accepted": True,
                    }
                ]
            },
            attempt_id="attempt-1",
            budget_id="budget-1",
            worker_commit={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("command_id", "command-other"),
        ("attempt_id", "attempt-other"),
        ("route_type", "delegate"),
    ),
)
async def test_digest_valid_worker_projection_cannot_drift_execution_identity(
    field: str,
    wrong: str,
) -> None:
    receipt_ref = "worker-projection://test/" + "c" * 64
    projection = _worker_projection(receipt_ref)
    projection[field] = wrong
    body = {
        key: value
        for key, value in projection.items()
        if key not in {"projection_id", "payload_digest", "schema_version"}
    }
    projection["payload_digest"] = _payload_digest(body)
    receipt_ref = (
        "worker-projection://test/"
        + projection["payload_digest"].removeprefix("sha256:")
    )
    projection["projection_id"] = receipt_ref

    with pytest.raises(GovernedRuntimeError, match="execution identity drifted"):
        await _runtime(projection=projection).takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "receipt_ref"),
    (
        (
            "scheduler",
            "schedulerresult://sha256/" + "a" * 64,
        ),
        (
            "internal",
            "agentresult://sha256/" + "b" * 64,
        ),
    ),
)
async def test_noncanonical_public_reader_cannot_resume_from_partial_identity(
    kind: str,
    receipt_ref: str,
) -> None:
    projection = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": "request-1",
        "narrative": kind,
        "route_type": "skill",
        "target_id": "demo",
        "receipt_ref": receipt_ref,
    }
    with pytest.raises(GovernedRuntimeError, match="non-canonical public projection"):
        await _runtime(projection=projection).takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


@pytest.mark.asyncio
async def test_projection_without_receipt_or_projection_id_is_not_rebound_synthetically() -> None:
    receipt_ref = "receipt://reserved/" + "9" * 64
    runtime = _runtime(
        store=_Store(_reservation(receipt_ref)),
        projection={
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
        }
    )

    with pytest.raises(GovernedRuntimeError, match="no receipt binding"):
        await runtime.takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


@pytest.mark.asyncio
async def test_non_hash_receipt_is_not_given_a_reconstructed_recovery_digest() -> None:
    receipt_ref = "skill-ledger://event/non-hash-identity"
    runtime = _runtime(
        projection={
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": "request-1",
            "receipt_ref": receipt_ref,
        }
    )

    with pytest.raises(GovernedRuntimeError, match="no canonical hash identity"):
        await runtime.takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


@pytest.mark.asyncio
async def test_real_internal_agent_public_reader_is_not_auto_resumed() -> None:
    receipt_ref = "agentresult://sha256/" + "d" * 64

    class _AgentResults:
        def get(self, requested: str) -> dict[str, Any] | None:
            if requested != receipt_ref:
                return None
            return {
                "command_id": "command-1",
                "work_order_id": "work-order-1",
                "receipt_ref": receipt_ref,
                "result_status": "EXECUTED",
                "result_digest": "sha256:" + "e" * 64,
                "result": {"status": "EXECUTED", "result_text": "done"},
            }

    runtime = DurableOperationRecoveryRuntime(
        task_store=_Store(_reservation(receipt_ref)),
        projection_reader=_AgentProjectionReader(_AgentResults()),
        runtime_outbox=object(),
        owner_id="owner-new",
        lease_ttl_ms=100,
    )
    command = SimpleNamespace(
        command_id="command-1",
        request_id="request-1",
        route_type="delegate",
        target_id="agent_runtime",
    )

    with pytest.raises(GovernedRuntimeError, match="non-canonical public projection"):
        await runtime.takeover_committed(
            admitted=_admitted(worker_route="delegate:agent_runtime"),
            command=command,
            reservation=_reservation(receipt_ref),
            latest_attempt=_attempt(),
            node_grant_ref=None,
            budget_id="budget-1",
        )


@pytest.mark.asyncio
async def test_takeover_advances_historical_fence_even_when_no_active_lease_is_visible() -> None:
    seed = _worker_projection("worker-projection://test/pending")
    receipt_ref = (
        "worker-projection://test/"
        + seed["payload_digest"].removeprefix("sha256:")
    )
    seed["projection_id"] = receipt_ref
    reservation = _reservation(receipt_ref)

    class _ReusedFenceStore(_Store):
        def get_committed_operation_checkpoint(
            self,
            reservation_id: str,
        ) -> dict[str, Any]:
            checkpoint = super().get_committed_operation_checkpoint(reservation_id)
            checkpoint["attempt"]["status"] = "RUNNING"
            return checkpoint

        def get_active_lease(self, attempt_id: str) -> None:
            assert attempt_id == "attempt-1"
            return None

        def acquire_lease(self, attempt_id: str, **_: Any) -> dict[str, Any]:
            return {
                "lease_id": "lease-1",
                "attempt_id": attempt_id,
                "owner_id": "owner-new",
                "fencing_token": 7,
                "status": "ACTIVE",
            }

    class _Outbox:
        def build(self, **_: Any) -> dict[str, Any]:
            return {}

    runtime = DurableOperationRecoveryRuntime(
        task_store=_ReusedFenceStore(reservation),
        projection_reader=_Reader(seed),
        runtime_outbox=_Outbox(),
        owner_id="owner-new",
        lease_ttl_ms=100,
    )
    running_attempt = {**_attempt(), "status": "RUNNING"}

    with pytest.raises(GovernedRuntimeError, match="historical owner fence"):
        await runtime.takeover_committed(
            admitted=_admitted(),
            command=_command(),
            reservation=reservation,
            latest_attempt=running_attempt,
            node_grant_ref=None,
            budget_id="budget-1",
        )
