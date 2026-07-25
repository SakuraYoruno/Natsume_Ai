"""Focused validation and loading for a committed Operation checkpoint."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution
from .coordinator_receipts import _digest_from_receipt_ref, _worker_receipt_ref
from .coordinator_records import _status, _text
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .node_execution_records import _is_terminal_commit
from .operation_checkpoint_validation import (
    WorkerBudgetUsageCheckpointState,
    assert_active_checkpoint_budget,
    assert_canonical_projection_identity,
    assert_exact_committed_checkpoint,
    classify_worker_budget_usage,
    expected_route_authority,
)
from .operation_takeover_lease import acquire_checkpoint_takeover_lease
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .runtime_outbox import RuntimeOutbox


async def takeover_committed_checkpoint(
    *,
    task_store: TaskStorePort,
    projection_reader: ExecutionProjectionReaderPort | None,
    runtime_outbox: RuntimeOutbox,
    owner_id: str,
    lease_ttl_ms: int,
    checkpoint_factory: Callable[..., Any],
    admitted: AdmittedExecution,
    command: HostCommand,
    reservation: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    node_grant_ref: str | None,
    budget_id: str,
    durable_elapsed_ms_override: int | None = None,
    allow_multinode_checkpoint_continuation: bool = False,
) -> Any:
    """Fence the former owner and load, but never replay, its worker result."""

    assert_existing_operation_lineage(
        admitted=admitted,
        reservation=reservation,
        latest_attempt=latest_attempt,
    )
    if (
        len(admitted.nodes) != 1
        and not allow_multinode_checkpoint_continuation
    ):
        _blocked(
            "multi-node committed checkpoint requires scheduler-owned batch recovery"
        )
    if _status(reservation) != "COMMITTED" or not _text(
        reservation.get("receipt_ref")
    ):
        _blocked("operation has no committed continuation checkpoint")
    receipt_ref = _required_text(reservation.get("receipt_ref"), "receipt_ref")
    receipt_digest = _digest_from_receipt_ref(receipt_ref)
    if receipt_digest is None:
        _blocked(
            "committed checkpoint receipt has no canonical hash identity"
        )
    checkpoint_digest_ref = (
        f"sha256://execution/{receipt_digest.split(':', 1)[1]}"
    )
    checkpoint = assert_exact_committed_checkpoint(
        await _maybe_await(
            task_store.get_committed_operation_checkpoint(
                admitted.reservation_id
            )
        ),
        reservation_id=admitted.reservation_id,
        business_scope=admitted.business_scope,
        operation_fingerprint=admitted.operation_fingerprint,
        attempt_id=_required_text(latest_attempt.get("attempt_id"), "attempt_id"),
        lease_id=_required_text(reservation.get("lease_id"), "lease_id"),
        owner_id=_required_text(reservation.get("owner_id"), "owner_id"),
        fencing_token=_required_positive_int(
            reservation.get("fencing_token"),
            "fencing_token",
        ),
        expected_version=(
            _required_positive_int(
                reservation.get("status_version"),
                "status_version",
            )
            - 1
        ),
        receipt_ref=receipt_ref,
        checkpoint_digest_ref=checkpoint_digest_ref,
    )
    reservation = checkpoint["reservation"]
    latest_attempt = checkpoint["attempt"]
    historical_lease = checkpoint["lease"]
    assert_existing_operation_lineage(
        admitted=admitted,
        reservation=reservation,
        latest_attempt=latest_attempt,
    )
    if projection_reader is None:
        _blocked("committed operation has no durable projection reader")
    loaded = await _maybe_await(projection_reader.load(receipt_ref))
    if not isinstance(loaded, Mapping):
        _blocked("committed operation receipt has no durable worker projection")
    immutable_commit = dict(loaded)
    if not _is_terminal_commit(immutable_commit):
        _blocked("committed worker projection is not one exact COMMIT checkpoint")

    expected_route, expected_target = expected_route_authority(
        admitted=admitted,
        command=command,
        node_grant_ref=node_grant_ref,
    )

    direct_receipt = _worker_receipt_ref(immutable_commit)
    projection_receipt = _text(immutable_commit.get("projection_id"))
    projected_receipt = direct_receipt or projection_receipt
    if not projected_receipt:
        _blocked(
            "durable worker projection has no receipt binding for its checkpoint"
        )
    if projected_receipt != receipt_ref:
        _blocked("durable worker projection differs from the committed receipt")
    if projection_receipt:
        try:
            canonical_projection = validate_worker_projection_payload(
                immutable_commit,
                expected_projection_id=receipt_ref,
            )
        except WorkerProjectionStoreError as exc:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed worker projection failed its durable digest binding",
            ) from exc
        if _text(canonical_projection.get("payload_digest")) != receipt_digest:
            _blocked(
                "worker projection digest differs from its receipt identity"
            )
        payload_digest = receipt_digest
        payload_digest_source = "receipt_identity_digest"
        assert_canonical_projection_identity(
            canonical_projection,
            admitted=admitted,
            command=command,
            latest_attempt=latest_attempt,
            expected_route=expected_route,
            expected_target=expected_target,
            node_grant_ref=node_grant_ref,
        )
        # The canonical persisted projection intentionally has no
        # ``receipt_ref`` field.  Add it only to the verifier-facing copy;
        # never hash this augmented wrapper as if it were the original
        # worker COMMIT payload.
        worker_commit = {**canonical_projection, "receipt_ref": receipt_ref}
    else:
        _blocked(
            "non-canonical public projection cannot prove frozen Attempt lineage"
        )

    snapshot = await _maybe_await(
        task_store.read_scheduler_snapshot(
            admitted.graph_id,
            admitted.graph_revision,
        )
    )
    checkpoint_budget = assert_active_checkpoint_budget(
        snapshot,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        budget_id=_required_text(budget_id, "budget_id"),
        allowed_statuses=frozenset({"ACTIVE", "EXCEEDED"}),
    )
    budget_usage = classify_worker_budget_usage(
        snapshot,
        attempt_id=_required_text(latest_attempt.get("attempt_id"), "attempt_id"),
        budget_id=_required_text(budget_id, "budget_id"),
        worker_commit=worker_commit,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
    )
    if budget_usage.state is WorkerBudgetUsageCheckpointState.CONFLICT:
        _blocked("committed checkpoint worker BudgetUsage conflicts")
    budget_status = _status(checkpoint_budget)
    if (
        budget_usage.state
        is WorkerBudgetUsageCheckpointState.EXACT_REJECTED
        and budget_status != "EXCEEDED"
    ):
        _blocked(
            "committed checkpoint BudgetUsage outcome and reservation status differ"
        )
    if (
        budget_status == "EXCEEDED"
        and budget_usage.state
        is not WorkerBudgetUsageCheckpointState.EXACT_REJECTED
    ):
        if (
            len(admitted.nodes) == 1
            or budget_usage.state
            is not WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED
        ):
            _blocked(
                "committed checkpoint BudgetUsage outcome and reservation status differ"
            )
        _assert_exact_sibling_budget_rejection(
            snapshot,
            admitted=admitted,
            budget_id=_required_text(budget_id, "budget_id"),
            current_attempt_id=_required_text(
                latest_attempt.get("attempt_id"),
                "attempt_id",
            ),
        )
    durable_elapsed_ms = (
        _checkpoint_elapsed_ms(
            override=durable_elapsed_ms_override,
            reservation=reservation,
            attempt=latest_attempt,
        )
        if budget_usage.state is WorkerBudgetUsageCheckpointState.MISSING
        else None
    )

    attempt, lease = await acquire_checkpoint_takeover_lease(
        task_store=task_store,
        runtime_outbox=runtime_outbox,
        admitted=admitted,
        attempt=latest_attempt,
        historical_lease=historical_lease,
        owner_id=owner_id,
        lease_ttl_ms=lease_ttl_ms,
    )
    if (
        budget_usage.state is WorkerBudgetUsageCheckpointState.MISSING
        and lease is None
    ):
        _blocked(
            "missing worker BudgetUsage requires a new fenced takeover Lease"
        )

    return checkpoint_factory(
        reservation=dict(reservation),
        attempt=attempt,
        worker_commit=worker_commit,
        receipt_ref=receipt_ref,
        payload_digest=payload_digest,
        payload_digest_source=payload_digest_source,
        budget_usage_state=budget_usage.state,
        budget_usage=(
            dict(budget_usage.usage)
            if budget_usage.usage is not None
            else None
        ),
        durable_elapsed_ms=durable_elapsed_ms,
        lease=lease,
    )


def assert_existing_operation_lineage(
    *,
    admitted: AdmittedExecution,
    reservation: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
) -> None:
    if (
        _text(reservation.get("reservation_id")) != admitted.reservation_id
        or _text(reservation.get("business_scope")) != admitted.business_scope
        or _text(reservation.get("operation_fingerprint"))
        != admitted.operation_fingerprint
        or _text(reservation.get("attempt_id"))
        != _text(latest_attempt.get("attempt_id"))
        or _text(latest_attempt.get("graph_id")) != admitted.graph_id
        or int(latest_attempt.get("graph_revision") or 0)
        != admitted.graph_revision
        or _text(latest_attempt.get("node_id")) != admitted.node_id
        or _text(latest_attempt.get("operation_scope"))
        != admitted.business_scope
        or _text(latest_attempt.get("operation_fingerprint"))
        != admitted.operation_fingerprint
    ):
        _blocked("existing operation reservation lineage is invalid")


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _required_text(value: Any, field_name: str) -> str:
    normalized = _text(value)
    if not normalized:
        _blocked(f"committed checkpoint has no {field_name}")
    return normalized


def _required_positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 1:
        _blocked(f"committed checkpoint has invalid {field_name}")
    return value


def _durable_checkpoint_elapsed_ms(
    *,
    reservation: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> int:
    started_ts_ms = attempt.get("started_ts_ms")
    committed_ts_ms = reservation.get("updated_ts_ms")
    if (
        type(started_ts_ms) is not int
        or type(committed_ts_ms) is not int
        or started_ts_ms < 0
        or committed_ts_ms < started_ts_ms
    ):
        _blocked("committed checkpoint durable elapsed time is invalid")
    elapsed_ms = committed_ts_ms - started_ts_ms
    if elapsed_ms == 0:
        _blocked("committed checkpoint durable elapsed time cannot be zero")
    return elapsed_ms


def _checkpoint_elapsed_ms(
    *,
    override: int | None,
    reservation: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> int:
    if override is None:
        return _durable_checkpoint_elapsed_ms(
            reservation=reservation,
            attempt=attempt,
        )
    if type(override) is not int or override <= 0:
        _blocked("sealed checkpoint elapsed time is invalid")
    return override


def _assert_exact_sibling_budget_rejection(
    snapshot: Mapping[str, Any],
    *,
    admitted: AdmittedExecution,
    budget_id: str,
    current_attempt_id: str,
) -> None:
    """Prove why an accepted node may coexist with an EXCEEDED graph Budget."""

    raw_attempts = snapshot.get("attempts")
    raw_usages = snapshot.get("budget_usage")
    if not isinstance(raw_attempts, list) or not isinstance(raw_usages, list):
        _blocked(
            "EXCEEDED multi-node checkpoint lacks a canonical scheduler snapshot"
        )
    attempts: dict[str, Mapping[str, Any]] = {}
    for raw in raw_attempts:
        if not isinstance(raw, Mapping):
            _blocked(
                "EXCEEDED multi-node checkpoint Attempt journal is malformed"
            )
        attempt_id = _text(raw.get("attempt_id"))
        if not attempt_id or attempt_id in attempts:
            _blocked(
                "EXCEEDED multi-node checkpoint Attempt identity is ambiguous"
            )
        attempts[attempt_id] = raw

    admitted_node_ids = {binding.node_id for binding in admitted.nodes}
    for raw in raw_usages:
        if not isinstance(raw, Mapping):
            _blocked(
                "EXCEEDED multi-node checkpoint BudgetUsage journal is malformed"
            )
        attempt_id = _text(raw.get("attempt_id"))
        node_id = _text(raw.get("node_id"))
        attempt = attempts.get(attempt_id)
        version_before = raw.get("reservation_version_before")
        version_after = raw.get("reservation_version_after")
        exceeded_dimensions = raw.get("exceeded_dimensions")
        if (
            attempt_id
            and attempt_id != current_attempt_id
            and node_id in admitted_node_ids
            and node_id != admitted.node_id
            and _text(raw.get("budget_id")) == budget_id
            and _text(raw.get("graph_id")) == admitted.graph_id
            and type(raw.get("graph_revision")) is int
            and raw.get("graph_revision") == admitted.graph_revision
            and raw.get("accepted") is False
            and _text(raw.get("reason_code"))
            == "TASK_STORE_BUDGET_EXCEEDED"
            and (
                _text(raw.get("charge_kind")).startswith("WORKER_USAGE")
                or _text(raw.get("charge_kind")) == "WORKER_ELAPSED"
            )
            and isinstance(exceeded_dimensions, list)
            and bool(exceeded_dimensions)
            and type(version_before) is int
            and type(version_after) is int
            and version_before >= 1
            and version_after == version_before + 1
            and isinstance(attempt, Mapping)
            and _text(attempt.get("attempt_id")) == attempt_id
            and _text(attempt.get("graph_id")) == admitted.graph_id
            and type(attempt.get("graph_revision")) is int
            and attempt.get("graph_revision") == admitted.graph_revision
            and _text(attempt.get("node_id")) == node_id
        ):
            return
    _blocked(
        "accepted checkpoint under an EXCEEDED graph Budget has no exact "
        "sibling rejection"
    )


def _blocked(detail: str) -> None:
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


__all__ = [
    "assert_existing_operation_lineage",
    "takeover_committed_checkpoint",
]
