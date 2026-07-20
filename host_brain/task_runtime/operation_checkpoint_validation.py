"""Pure identity and BudgetUsage validation for Operation checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .worker_budget_usage import WorkerBudgetUsage, WorkerBudgetUsageError
from .worker_registry import GovernedWorkerRegistryError, GovernedWorkerRoute


class WorkerBudgetUsageCheckpointState(str, Enum):
    """Classification of the one worker settlement bound to a checkpoint."""

    EXACT_ACCEPTED = "EXACT_ACCEPTED"
    EXACT_REJECTED = "EXACT_REJECTED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class WorkerBudgetUsageCheckpoint:
    state: WorkerBudgetUsageCheckpointState
    expected_usage_id: str
    usage: dict[str, Any] | None = None


def classify_worker_budget_usage(
    snapshot: Any,
    *,
    attempt_id: str,
    budget_id: str,
    worker_commit: Mapping[str, Any],
    graph_id: str | None = None,
    graph_revision: int | None = None,
    node_id: str | None = None,
) -> WorkerBudgetUsageCheckpoint:
    """Classify, without mutating, the worker settlement for one checkpoint.

    Precharge and retry rows are deliberately outside this classification.
    Any worker-kind row with a different identity, a rejected result, or
    drifted lineage is a conflict rather than evidence that settlement is
    absent.
    """

    if not isinstance(snapshot, Mapping):
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.CONFLICT,
            "",
        )
    records = snapshot.get("budget_usage")
    if not isinstance(records, list) or any(
        not isinstance(item, Mapping) for item in records
    ):
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.CONFLICT,
            "",
        )
    payload = worker_commit.get("budget_usage")
    if payload is None:
        expected_usage_id = _worker_elapsed_usage_id(
            budget_id=budget_id,
            node_id=node_id,
            attempt_id=attempt_id,
        )
        expected_kind = "WORKER_ELAPSED"
        expected_delta = BudgetAmount().to_dict()
    else:
        if not isinstance(payload, Mapping):
            _blocked("committed checkpoint projection has malformed worker BudgetUsage")
        try:
            typed_usage = WorkerBudgetUsage.from_payload(payload)
        except WorkerBudgetUsageError as exc:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed checkpoint projection has invalid worker BudgetUsage",
            ) from exc
        expected_usage_id = typed_usage.usage_id
        expected_kind = _typed_worker_charge_kind(typed_usage)
        expected_delta = typed_usage.to_budget_amount(elapsed_ms=0).to_dict()

    worker_rows: list[dict[str, Any]] = []
    expected_id_rows: list[dict[str, Any]] = []
    for raw in records:
        record = dict(raw)
        usage_id = _text(record.get("usage_id"))
        charge_kind = _text(record.get("charge_kind")).upper()
        is_worker_kind = charge_kind.startswith("WORKER_USAGE") or (
            charge_kind == "WORKER_ELAPSED"
        )
        if usage_id == expected_usage_id:
            expected_id_rows.append(record)
        if (
            _text(record.get("attempt_id")) == attempt_id
            and _text(record.get("budget_id")) == budget_id
            and is_worker_kind
        ):
            worker_rows.append(record)

    if not worker_rows and not expected_id_rows:
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.MISSING,
            expected_usage_id,
        )
    if len(worker_rows) != 1 or len(expected_id_rows) != 1:
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.CONFLICT,
            expected_usage_id,
        )
    candidate = worker_rows[0]
    if candidate != expected_id_rows[0] or not _exact_worker_usage_record(
        candidate,
        expected_usage_id=expected_usage_id,
        expected_kind=expected_kind,
        expected_delta=expected_delta,
        attempt_id=attempt_id,
        budget_id=budget_id,
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
    ):
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.CONFLICT,
            expected_usage_id,
        )
    if candidate.get("accepted") is True:
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED,
            expected_usage_id,
            candidate,
        )
    if (
        candidate.get("accepted") is False
        and _text(candidate.get("reason_code")) == "TASK_STORE_BUDGET_EXCEEDED"
        and isinstance(candidate.get("exceeded_dimensions"), list)
        and bool(candidate.get("exceeded_dimensions"))
        and type(candidate.get("reservation_version_before")) is int
        and type(candidate.get("reservation_version_after")) is int
        and int(candidate["reservation_version_after"])
        == int(candidate["reservation_version_before"]) + 1
    ):
        return WorkerBudgetUsageCheckpoint(
            WorkerBudgetUsageCheckpointState.EXACT_REJECTED,
            expected_usage_id,
            candidate,
        )
    return WorkerBudgetUsageCheckpoint(
        WorkerBudgetUsageCheckpointState.CONFLICT,
        expected_usage_id,
    )


def assert_active_checkpoint_budget(
    snapshot: Any,
    *,
    graph_id: str,
    graph_revision: int,
    budget_id: str,
    allowed_statuses: frozenset[str] = frozenset({"ACTIVE"}),
) -> dict[str, Any]:
    """Require one ACTIVE graph-scoped BudgetReservation in the snapshot."""

    budgets = snapshot.get("budgets") if isinstance(snapshot, Mapping) else None
    if not isinstance(budgets, list) or len(budgets) != 1:
        _blocked("committed checkpoint lacks one exact graph BudgetReservation")
    budget = budgets[0]
    if not isinstance(budget, Mapping):
        _blocked("committed checkpoint graph BudgetReservation is malformed")
    record = dict(budget)
    version = record.get("status_version")
    if (
        _text(record.get("budget_id")) != budget_id
        or _text(record.get("graph_id")) != graph_id
        or not _exact_int(record.get("graph_revision"), graph_revision)
        or record.get("node_id") is not None
        or record.get("attempt_id") is not None
        or _status(record) not in allowed_statuses
        or type(version) is not int
        or version < 1
    ):
        _blocked("committed checkpoint graph BudgetReservation is not exact or continuable")
    return record


def accepted_worker_budget_usage(
    snapshot: Any,
    *,
    attempt_id: str,
    budget_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any]:
    classified = classify_worker_budget_usage(
        snapshot,
        attempt_id=attempt_id,
        budget_id=budget_id,
        worker_commit=worker_commit,
    )
    if classified.state is not WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED:
        _blocked("committed checkpoint lacks one accepted worker BudgetUsage")
    return dict(classified.usage or {})


def _typed_worker_charge_kind(usage: WorkerBudgetUsage) -> str:
    labels = {
        "token_count": "TOKEN",
        "tool_call_count": "TOOL",
        "time_ms": "TIME",
        "retry_count": "RETRY",
        "model_cost_micros": "COST",
    }
    return "WORKER_USAGE:" + "+".join(
        labels[item] for item in usage.measured_dimensions()
    )


def _worker_elapsed_usage_id(
    *,
    budget_id: str,
    node_id: str | None,
    attempt_id: str,
) -> str:
    node_ref = _text(node_id)
    if not node_ref:
        _blocked("committed checkpoint lacks node identity for worker elapsed usage")
    digest = hashlib.sha256(
        f"{budget_id}\n{node_ref}\n{attempt_id}\nworker-elapsed".encode("utf-8")
    ).hexdigest()
    return f"budget-usage://{digest}"


def _exact_worker_usage_record(
    record: Mapping[str, Any],
    *,
    expected_usage_id: str,
    expected_kind: str,
    expected_delta: Mapping[str, int],
    attempt_id: str,
    budget_id: str,
    graph_id: str | None,
    graph_revision: int | None,
    node_id: str | None,
) -> bool:
    delta = record.get("delta")
    if not isinstance(delta, Mapping) or set(delta) != set(expected_delta):
        return False
    if any(
        type(delta.get(field)) is not int or int(delta[field]) < 0
        for field in expected_delta
    ):
        return False
    if any(
        int(delta[field]) != int(expected_delta[field])
        for field in expected_delta
        if field != "time_ms"
    ):
        return False
    if (
        _text(record.get("usage_id")) != expected_usage_id
        or _text(record.get("budget_id")) != budget_id
        or _text(record.get("attempt_id")) != attempt_id
        or _text(record.get("charge_kind")).upper() != expected_kind
        or type(record.get("accepted")) is not bool
    ):
        return False
    if graph_id is not None and _text(record.get("graph_id")) != graph_id:
        return False
    if graph_revision is not None and not _exact_int(
        record.get("graph_revision"), graph_revision
    ):
        return False
    if node_id is not None and _text(record.get("node_id")) != node_id:
        return False
    return True


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
    "WorkerBudgetUsageCheckpoint",
    "WorkerBudgetUsageCheckpointState",
    "accepted_worker_budget_usage",
    "assert_active_checkpoint_budget",
    "assert_exact_committed_checkpoint",
    "assert_canonical_projection_identity",
    "classify_worker_budget_usage",
    "expected_route_authority",
    "is_exact_operation_commit",
]
