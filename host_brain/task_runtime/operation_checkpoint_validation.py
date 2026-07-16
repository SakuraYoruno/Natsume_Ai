"""Pure identity and BudgetUsage validation for Operation checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .worker_registry import GovernedWorkerRegistryError, GovernedWorkerRoute


def accepted_worker_budget_usage(
    snapshot: Any,
    *,
    attempt_id: str,
    budget_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any]:
    records = snapshot.get("budget_usage") if isinstance(snapshot, Mapping) else None
    candidates = [
        dict(item)
        for item in (records or ())
        if isinstance(item, Mapping)
        and _text(item.get("attempt_id")) == attempt_id
        and _text(item.get("budget_id")) == budget_id
        and bool(item.get("accepted"))
        and (
            _text(item.get("charge_kind")).startswith("WORKER_USAGE")
            or _text(item.get("charge_kind")) == "WORKER_ELAPSED"
        )
    ]
    payload = worker_commit.get("budget_usage")
    expected_usage_id = (
        _text(payload.get("usage_id")) if isinstance(payload, Mapping) else ""
    )
    if not expected_usage_id:
        _blocked("committed checkpoint projection lacks worker BudgetUsage identity")
    if len(candidates) != 1:
        _blocked("committed checkpoint lacks one accepted worker BudgetUsage")
    candidate = candidates[0]
    if _text(candidate.get("usage_id")) != expected_usage_id:
        _blocked("committed checkpoint worker BudgetUsage identity drifted")
    return candidate


def expected_route_authority(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    node_grant_ref: str | None,
) -> tuple[str, str]:
    try:
        frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
    except GovernedWorkerRegistryError:
        if node_grant_ref is not None:
            _blocked("committed checkpoint has an invalid frozen worker route")
        route_type = _text(getattr(command, "route_type", None)).lower()
        target_id = _text(getattr(command, "target_id", None))
    else:
        route_type = frozen_route.kind
        target_id = frozen_route.target_id or _text(
            getattr(command, "target_id", None)
        )
    if not route_type or not target_id:
        _blocked("committed checkpoint route authority is incomplete")
    return route_type, target_id


def assert_canonical_projection_identity(
    projection: Mapping[str, Any],
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    latest_attempt: Mapping[str, Any],
    expected_route: str,
    expected_target: str,
    node_grant_ref: str | None,
) -> None:
    bindings: tuple[tuple[Any, Any], ...] = (
        (projection.get("command_id"), admitted.command_id),
        (projection.get("request_id"), getattr(command, "request_id", None)),
        (projection.get("graph_id"), admitted.graph_id),
        (projection.get("graph_revision"), admitted.graph_revision),
        (projection.get("node_id"), admitted.node_id),
        (projection.get("attempt_id"), latest_attempt.get("attempt_id")),
        (_text(projection.get("route_type")).lower(), expected_route),
        (projection.get("target_id"), expected_target),
    )
    if any(str(actual) != str(expected) for actual, expected in bindings):
        _blocked("canonical worker projection execution identity drifted")
    if node_grant_ref is not None and _text(
        projection.get("node_grant_ref")
    ) != _text(node_grant_ref):
        _blocked("canonical worker projection node grant drifted")


def is_exact_operation_commit(
    value: Any,
    *,
    reservation_id: str,
    business_scope: str,
    operation_fingerprint: str,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    expected_version: int,
    receipt_ref: str,
) -> bool:
    if (
        not isinstance(value, Mapping)
        or type(fencing_token) is not int
        or fencing_token < 1
        or type(expected_version) is not int
        or expected_version < 0
    ):
        return False
    return (
        _text(value.get("reservation_id")) == reservation_id
        and _text(value.get("business_scope")) == business_scope
        and _text(value.get("operation_fingerprint")) == operation_fingerprint
        and _text(value.get("attempt_id")) == attempt_id
        and _text(value.get("lease_id")) == lease_id
        and _text(value.get("owner_id")) == owner_id
        and _exact_int(value.get("fencing_token"), fencing_token)
        and _status(value) == "COMMITTED"
        and _exact_int(value.get("status_version"), expected_version + 1)
        and _text(value.get("receipt_ref")) == receipt_ref
    )


def assert_exact_committed_checkpoint(
    checkpoint: Any,
    *,
    reservation_id: str,
    business_scope: str,
    operation_fingerprint: str,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    expected_version: int,
    receipt_ref: str,
    checkpoint_digest_ref: str,
) -> dict[str, Any]:
    if (
        type(fencing_token) is not int
        or fencing_token < 1
        or type(expected_version) is not int
        or expected_version < 0
    ):
        _blocked("committed Operation checkpoint numeric binding is invalid")
    if not isinstance(checkpoint, Mapping):
        _blocked("Store returned no exact committed Operation checkpoint")
    reservation = checkpoint.get("reservation")
    attempt = checkpoint.get("attempt")
    lease = checkpoint.get("lease")
    outbox = checkpoint.get("outbox")
    if not is_exact_operation_commit(
        reservation,
        reservation_id=reservation_id,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        attempt_id=attempt_id,
        lease_id=lease_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        expected_version=expected_version,
        receipt_ref=receipt_ref,
    ):
        _blocked("committed Operation checkpoint reservation binding drifted")
    if (
        not isinstance(attempt, Mapping)
        or _text(attempt.get("attempt_id")) != attempt_id
        or not isinstance(lease, Mapping)
        or _text(lease.get("lease_id")) != lease_id
        or _text(lease.get("attempt_id")) != attempt_id
        or _text(lease.get("owner_id")) != owner_id
        or not _exact_int(lease.get("fencing_token"), fencing_token)
    ):
        _blocked("committed Operation checkpoint Attempt/Lease binding drifted")
    evidence_refs = (
        tuple(outbox.get("evidence_refs") or ())
        if isinstance(outbox, Mapping)
        else ()
    )
    if (
        not isinstance(outbox, Mapping)
        or _text(outbox.get("aggregate_type")) != "OPERATION_RESERVATION"
        or _text(outbox.get("aggregate_id")) != reservation_id
        or not _exact_int(
            outbox.get("aggregate_version"),
            expected_version + 1,
        )
        or _text(outbox.get("event_type")) != "OPERATION_COMMITTED"
        or _text(outbox.get("attempt_id")) != attempt_id
        or evidence_refs != (receipt_ref, checkpoint_digest_ref)
        or _text(checkpoint.get("checkpoint_digest_ref"))
        != checkpoint_digest_ref
    ):
        _blocked("committed Operation checkpoint Outbox evidence drifted")
    return {
        "reservation": dict(reservation),
        "attempt": dict(attempt),
        "lease": dict(lease),
        "outbox": dict(outbox),
        "checkpoint_digest_ref": checkpoint_digest_ref,
    }


def _blocked(detail: str) -> None:
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


__all__ = [
    "accepted_worker_budget_usage",
    "assert_exact_committed_checkpoint",
    "assert_canonical_projection_identity",
    "expected_route_authority",
    "is_exact_operation_commit",
]
