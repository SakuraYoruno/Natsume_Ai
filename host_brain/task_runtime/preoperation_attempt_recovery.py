"""Fail-closed recovery before an Operation reservation exists.

The classifier is a pure view over one durable scheduler snapshot.  The
takeover helper reuses the exact Attempt and delegates the fencing mutation to
``TaskStorePort.acquire_lease``.  Neither boundary makes a TaskNode READY or
starts a worker; the caller must continue the existing fenced Node/Attempt
lifecycle before reaching the Operation reservation boundary.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from .admission import AdmittedExecution
from .coordinator_records import _attempt_id
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_RECOVERABLE_NODE_STATUSES = frozenset({"LEASED", "RUNNING"})
_RECOVERABLE_ATTEMPT_STATUSES = frozenset({"LEASED", "RUNNING", "RECOVERABLE"})
_RECOVERABLE_GRAPH_BUDGET_STATUSES = frozenset({"ACTIVE", "EXCEEDED"})
_OPERATION_NOT_FOUND_REASONS = frozenset(
    {"TASK_STORE_NOT_FOUND", "NOT_FOUND", "OPERATION_NOT_FOUND"}
)
_LEASE_CONTENTION_REASONS = frozenset(
    {
        "TASK_STORE_CAS_CONFLICT",
        "TASK_STORE_LEASE_CONFLICT",
        "TASK_STORE_OWNERSHIP_CONFLICT",
    }
)


@dataclass(frozen=True, slots=True)
class PreOperationRecoveryCandidate:
    """One latest Attempt that is safe to offer for fenced takeover."""

    graph_id: str
    graph_revision: int
    node_id: str
    node_status: str
    attempt_id: str
    attempt_no: int
    attempt_status: str
    attempt_version: int
    previous_lease_id: str | None
    previous_fencing_token: int | None


def classify_preoperation_recovery_candidates(
    snapshot: Mapping[str, Any],
    *,
    graph_id: str,
    graph_revision: int,
    operation_free_node_ids: Collection[str],
) -> tuple[PreOperationRecoveryCandidate, ...]:
    """Return stable recovery candidates from one scheduler snapshot.

    ``operation_free_node_ids`` is an explicit proof boundary owned by the
    caller: every listed node must already have been checked through its exact
    deterministic reservation id.  Nodes omitted from that collection are
    never made recoverable by this classifier.
    """

    operation_free = _normalized_operation_free_ids(operation_free_node_ids)
    if not operation_free:
        # Existing scheduler projections may intentionally omit the heavier
        # Attempt/Graph sections when no Operation-free active node exists.
        # An empty authority set therefore has a strict no-read fast path.
        return ()
    if not isinstance(snapshot, Mapping):
        _conflict("pre-operation recovery snapshot is not a mapping")
    expected_graph_id = _required_text(graph_id, "graph_id")
    expected_revision = _exact_int(graph_revision, "graph_revision", minimum=1)
    observed_ts_ms = _exact_int(
        snapshot.get("observed_ts_ms"),
        "snapshot.observed_ts_ms",
        minimum=0,
    )
    graph = _required_mapping(snapshot.get("graph_record"), "snapshot.graph_record")
    if (
        _required_text(graph.get("graph_id"), "graph_record.graph_id")
        != expected_graph_id
        or _exact_int(graph.get("revision"), "graph_record.revision", minimum=1)
        != expected_revision
    ):
        _conflict("scheduler snapshot Graph identity drifted")

    nodes = _indexed_nodes(
        _required_records(graph.get("nodes"), "graph_record.nodes"),
        graph_id=expected_graph_id,
        graph_revision=expected_revision,
    )
    unknown_operation_free = sorted(operation_free - set(nodes))
    if unknown_operation_free:
        _conflict(
            "operation-free proof references unknown TaskNodes: "
            + ",".join(unknown_operation_free)
        )
    attempts_by_node, attempts_by_id = _indexed_attempts(
        _required_records(snapshot.get("attempts"), "snapshot.attempts"),
        graph_id=expected_graph_id,
        graph_revision=expected_revision,
        known_nodes=set(nodes),
    )
    active_leases = _indexed_active_leases(
        _required_records(snapshot.get("active_leases"), "snapshot.active_leases"),
        known_attempts=set(attempts_by_id),
    )

    if _status(graph) != "ACTIVE":
        return ()

    graph_budget: Mapping[str, Any] | None = None
    budget_usage_by_attempt: dict[str, tuple[Mapping[str, Any], ...]] | None = None
    candidates: list[PreOperationRecoveryCandidate] = []
    for node_id in sorted(operation_free):
        node = nodes[node_id]
        node_status = _status(node)
        if node_status not in _RECOVERABLE_NODE_STATUSES:
            continue
        attempts = attempts_by_node.get(node_id, ())
        if not attempts:
            _conflict(f"active TaskNode {node_id} has no durable Attempt")
        latest = attempts[-1]
        attempt_status = _status(latest)
        if attempt_status not in _RECOVERABLE_ATTEMPT_STATUSES:
            _conflict(
                f"active TaskNode {node_id} latest Attempt is not recoverable: "
                f"{attempt_status or 'UNKNOWN'}"
            )
        latest_attempt_id = _required_text(latest.get("attempt_id"), "attempt.attempt_id")
        node_attempt_ids = {
            _required_text(attempt.get("attempt_id"), "attempt.attempt_id")
            for attempt in attempts
        }
        foreign_leases = [
            lease
            for attempt_id, lease in active_leases.items()
            if attempt_id in node_attempt_ids and attempt_id != latest_attempt_id
        ]
        if foreign_leases:
            _conflict(f"TaskNode {node_id} has an ACTIVE Lease on a superseded Attempt")

        previous = active_leases.get(latest_attempt_id)
        if previous is not None:
            expires_ts_ms = _exact_int(
                previous.get("expires_ts_ms"),
                "lease.expires_ts_ms",
                minimum=0,
            )
            if expires_ts_ms > observed_ts_ms:
                continue
            previous_lease_id = _required_text(previous.get("lease_id"), "lease.lease_id")
            previous_fencing_token = _exact_int(
                previous.get("fencing_token"),
                "lease.fencing_token",
                minimum=1,
            )
        else:
            previous_lease_id = None
            previous_fencing_token = None

        if graph_budget is None or budget_usage_by_attempt is None:
            budgets_by_id, graph_budget = _indexed_budgets(
                _required_records(snapshot.get("budgets"), "snapshot.budgets"),
                graph_id=expected_graph_id,
                graph_revision=expected_revision,
            )
            budget_usage_by_attempt = _indexed_budget_usage(
                _required_records(
                    snapshot.get("budget_usage"),
                    "snapshot.budget_usage",
                ),
                budgets_by_id=budgets_by_id,
                attempts_by_id=attempts_by_id,
            )
        _assert_preoperation_budget_usage(
            budget=graph_budget,
            usages=budget_usage_by_attempt.get(latest_attempt_id, ()),
            node_id=node_id,
            attempt_id=latest_attempt_id,
            multi_node=len(nodes) > 1,
        )

        candidates.append(
            PreOperationRecoveryCandidate(
                graph_id=expected_graph_id,
                graph_revision=expected_revision,
                node_id=node_id,
                node_status=node_status,
                attempt_id=latest_attempt_id,
                attempt_no=_exact_int(latest.get("attempt_no"), "attempt.attempt_no", minimum=1),
                attempt_status=attempt_status,
                attempt_version=_exact_int(
                    latest.get("status_version"),
                    "attempt.status_version",
                    minimum=0,
                ),
                previous_lease_id=previous_lease_id,
                previous_fencing_token=previous_fencing_token,
            )
        )
    return tuple(candidates)


async def acquire_preoperation_takeover_lease(
    *,
    task_store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    attempt: Mapping[str, Any],
    owner_id: str,
    lease_ttl_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Acquire a higher fence for the exact pre-Operation Attempt.

    The returned Attempt is durably ``LEASED`` and belongs to ``owner_id``.
    The helper deliberately does not transition the TaskNode or Attempt to
    RUNNING; that remains the existing Node lifecycle authority.
    """

    if not isinstance(admitted, AdmittedExecution):
        _conflict("pre-operation takeover requires AdmittedExecution")
    admitted.validate()
    attempt_record = dict(_required_mapping(attempt, "attempt"))
    attempt_id = _required_text(attempt_record.get("attempt_id"), "attempt.attempt_id")
    attempt_version = _exact_int(
        attempt_record.get("status_version"),
        "attempt.status_version",
        minimum=0,
    )
    attempt_status = _status(attempt_record)
    if attempt_status not in _RECOVERABLE_ATTEMPT_STATUSES:
        _conflict(f"Attempt {attempt_id} cannot be taken over from {attempt_status or 'UNKNOWN'}")
    _assert_attempt_identity(attempt_record, admitted=admitted, attempt_id=attempt_id)
    owner = _required_text(owner_id, "owner_id")
    ttl_ms = _exact_int(lease_ttl_ms, "lease_ttl_ms", minimum=1)

    if await _try_operation(task_store, admitted.reservation_id) is not None:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "pre-operation takeover found an existing Operation reservation",
        )

    previous_raw = await _maybe_await(task_store.get_active_lease(attempt_id))
    previous = None
    if previous_raw is not None:
        previous = dict(_required_mapping(previous_raw, "active lease"))
        _assert_lease_identity(previous, attempt_id=attempt_id, require_active=True)

    lease_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_ATTEMPT",
        aggregate_id=attempt_id,
        aggregate_version=attempt_version + 1,
        event_type="TASK_ATTEMPT_LEASED",
        attempt_id=attempt_id,
    )

    async def _acquire() -> Any:
        return await _maybe_await(
            task_store.acquire_lease(
                attempt_id,
                owner_id=owner,
                ttl_ms=ttl_ms,
                expected_attempt_version=attempt_version,
                absent_operation_reservation_id=admitted.reservation_id,
                outbox=lease_outbox,
            )
        )

    try:
        claimed_raw = await _acquire()
    except Exception as exc:
        if _reason(exc):
            _raise_takeover_acquire_error(exc)
        try:
            claimed_raw = await _acquire()
        except Exception as replay_exc:
            _raise_takeover_acquire_error(replay_exc)

    lease = dict(_required_mapping(claimed_raw, "takeover lease"))
    _assert_lease_identity(lease, attempt_id=attempt_id, require_active=True)
    if _required_text(lease.get("owner_id"), "takeover lease.owner_id") != owner:
        _conflict("pre-operation takeover Lease owner drifted")
    new_fence = _exact_int(
        lease.get("fencing_token"),
        "takeover lease.fencing_token",
        minimum=1,
    )
    if previous is not None:
        previous_lease_id = _required_text(previous.get("lease_id"), "active lease.lease_id")
        previous_fence = _exact_int(
            previous.get("fencing_token"),
            "active lease.fencing_token",
            minimum=1,
        )
        if _required_text(lease.get("lease_id"), "takeover lease.lease_id") == previous_lease_id:
            raise GovernedRuntimeError(
                GOVERNED_EXECUTION_IN_PROGRESS,
                "pre-operation takeover did not replace the live Lease",
            )
        if new_fence <= previous_fence:
            _conflict("pre-operation takeover did not advance the fencing token")
    elif new_fence < 2:
        _conflict("pre-operation recovery has no historical fencing lineage")

    refreshed_raw = await _maybe_await(task_store.get_attempt(attempt_id))
    refreshed = dict(_required_mapping(refreshed_raw, "takeover Attempt"))
    _assert_attempt_identity(refreshed, admitted=admitted, attempt_id=attempt_id)
    if (
        _status(refreshed) != "LEASED"
        or _required_text(refreshed.get("owner_id"), "takeover Attempt.owner_id") != owner
        or _exact_int(
            refreshed.get("status_version"),
            "takeover Attempt.status_version",
            minimum=0,
        )
        != attempt_version + 1
    ):
        _conflict("pre-operation takeover Attempt binding is invalid")

    if await _try_operation(task_store, admitted.reservation_id) is not None:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "an Operation reservation appeared during pre-operation takeover",
        )
    return refreshed, lease


def _indexed_nodes(
    records: list[Mapping[str, Any]],
    *,
    graph_id: str,
    graph_revision: int,
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        node_id = _required_text(record.get("node_id"), "node.node_id")
        if (
            _required_text(record.get("graph_id"), "node.graph_id") != graph_id
            or _exact_int(record.get("graph_revision"), "node.graph_revision", minimum=1)
            != graph_revision
        ):
            _conflict(f"TaskNode {node_id} Graph identity drifted")
        if node_id in indexed:
            _conflict(f"duplicate TaskNode in scheduler snapshot: {node_id}")
        indexed[node_id] = record
    if not indexed:
        _conflict("scheduler snapshot Graph has no TaskNodes")
    return indexed


def _indexed_attempts(
    records: list[Mapping[str, Any]],
    *,
    graph_id: str,
    graph_revision: int,
    known_nodes: set[str],
) -> tuple[dict[str, tuple[Mapping[str, Any], ...]], dict[str, Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        attempt_id = _required_text(record.get("attempt_id"), "attempt.attempt_id")
        node_id = _required_text(record.get("node_id"), "attempt.node_id")
        if (
            _required_text(record.get("graph_id"), "attempt.graph_id") != graph_id
            or _exact_int(record.get("graph_revision"), "attempt.graph_revision", minimum=1)
            != graph_revision
            or node_id not in known_nodes
        ):
            _conflict(f"Attempt {attempt_id} execution identity drifted")
        _exact_int(record.get("attempt_no"), "attempt.attempt_no", minimum=1)
        _exact_int(record.get("status_version"), "attempt.status_version", minimum=0)
        if attempt_id in by_id:
            _conflict(f"duplicate Attempt in scheduler snapshot: {attempt_id}")
        by_id[attempt_id] = record
        grouped.setdefault(node_id, []).append(record)

    normalized: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for node_id, attempts in grouped.items():
        ordered = sorted(
            attempts,
            key=lambda item: (
                _exact_int(item.get("attempt_no"), "attempt.attempt_no", minimum=1),
                _required_text(item.get("attempt_id"), "attempt.attempt_id"),
            ),
        )
        numbers = [
            _exact_int(item.get("attempt_no"), "attempt.attempt_no", minimum=1)
            for item in ordered
        ]
        if numbers != list(range(1, len(numbers) + 1)):
            _conflict(f"TaskNode {node_id} Attempt journal is not contiguous")
        normalized[node_id] = tuple(ordered)
    return normalized, by_id


def _indexed_active_leases(
    records: list[Mapping[str, Any]],
    *,
    known_attempts: set[str],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    lease_ids: set[str] = set()
    for record in records:
        attempt_id = _required_text(record.get("attempt_id"), "lease.attempt_id")
        lease_id = _required_text(record.get("lease_id"), "lease.lease_id")
        if attempt_id not in known_attempts:
            _conflict(f"ACTIVE Lease {lease_id} references an unknown Attempt")
        if _status(record) != "ACTIVE":
            _conflict(f"scheduler active_leases contains non-ACTIVE Lease {lease_id}")
        _required_text(record.get("owner_id"), "lease.owner_id")
        _exact_int(record.get("fencing_token"), "lease.fencing_token", minimum=1)
        _exact_int(record.get("expires_ts_ms"), "lease.expires_ts_ms", minimum=0)
        if lease_id in lease_ids or attempt_id in indexed:
            _conflict(f"duplicate ACTIVE Lease binding for Attempt {attempt_id}")
        lease_ids.add(lease_id)
        indexed[attempt_id] = record
    return indexed


def _indexed_budgets(
    records: list[Mapping[str, Any]],
    *,
    graph_id: str,
    graph_revision: int,
) -> tuple[dict[str, Mapping[str, Any]], Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    graph_scoped: list[Mapping[str, Any]] = []
    for record in records:
        budget_id = _required_text(record.get("budget_id"), "budget.budget_id")
        if (
            _required_text(record.get("graph_id"), "budget.graph_id") != graph_id
            or _exact_int(
                record.get("graph_revision"),
                "budget.graph_revision",
                minimum=1,
            )
            != graph_revision
        ):
            _conflict(f"Budget {budget_id} Graph identity drifted")
        if budget_id in indexed:
            _conflict(f"duplicate Budget in scheduler snapshot: {budget_id}")
        indexed[budget_id] = record
        if record.get("node_id") is None and record.get("attempt_id") is None:
            graph_scoped.append(record)
    if len(graph_scoped) != 1:
        _conflict("pre-operation recovery requires exactly one graph-scoped Budget")
    graph_budget = graph_scoped[0]
    budget_status = _status(graph_budget)
    if budget_status not in _RECOVERABLE_GRAPH_BUDGET_STATUSES:
        _conflict(
            "pre-operation recovery requires an ACTIVE or EXCEEDED "
            "graph-scoped Budget"
        )
    return indexed, graph_budget


def _indexed_budget_usage(
    records: list[Mapping[str, Any]],
    *,
    budgets_by_id: Mapping[str, Mapping[str, Any]],
    attempts_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    usage_ids: set[str] = set()
    for record in records:
        usage_id = _required_text(record.get("usage_id"), "budget_usage.usage_id")
        budget_id = _required_text(record.get("budget_id"), "budget_usage.budget_id")
        attempt_id = _required_text(record.get("attempt_id"), "budget_usage.attempt_id")
        node_id = _required_text(record.get("node_id"), "budget_usage.node_id")
        charge_kind = _required_text(
            record.get("charge_kind"),
            "budget_usage.charge_kind",
        )
        accepted = record.get("accepted")
        if type(accepted) is not bool:
            _conflict(f"BudgetUsage {usage_id} accepted must be a boolean")
        if usage_id in usage_ids:
            _conflict(f"duplicate BudgetUsage in scheduler snapshot: {usage_id}")
        if budget_id not in budgets_by_id:
            _conflict(f"BudgetUsage {usage_id} references an unknown Budget")
        attempt = attempts_by_id.get(attempt_id)
        if attempt is None:
            _conflict(f"BudgetUsage {usage_id} references an unknown Attempt")
        if _required_text(attempt.get("node_id"), "attempt.node_id") != node_id:
            _conflict(f"BudgetUsage {usage_id} Node/Attempt identity drifted")
        # Retain exact durable spelling. Candidate validation below only accepts
        # the canonical upper-case PRECHARGE kind.
        if charge_kind != str(record.get("charge_kind")):
            _conflict(f"BudgetUsage {usage_id} charge_kind is not canonical")
        usage_ids.add(usage_id)
        grouped.setdefault(attempt_id, []).append(record)
    return {attempt_id: tuple(usages) for attempt_id, usages in grouped.items()}


def _assert_preoperation_budget_usage(
    *,
    budget: Mapping[str, Any],
    usages: tuple[Mapping[str, Any], ...],
    node_id: str,
    attempt_id: str,
    multi_node: bool,
) -> None:
    budget_status = _status(budget)
    if not usages:
        if budget_status == "ACTIVE":
            return
        if budget_status == "EXCEEDED" and multi_node:
            # A sibling may exhaust the graph-scoped Budget after this node
            # durably claimed its Attempt but before it wrote PRECHARGE.  The
            # resumed charge exact-rejects against EXCEEDED and closes only this
            # node; a single-node graph has no such sibling lineage.
            return
        _conflict(
            f"pre-operation Attempt {attempt_id} has an EXCEEDED Budget "
            "without a recoverable PRECHARGE outcome"
        )
    if len(usages) != 1:
        _conflict(
            f"pre-operation Attempt {attempt_id} has multiple BudgetUsage records"
        )
    usage = usages[0]
    budget_id = _required_text(budget.get("budget_id"), "budget.budget_id")
    usage_id = _required_text(usage.get("usage_id"), "budget_usage.usage_id")
    if (
        _required_text(usage.get("budget_id"), "budget_usage.budget_id")
        != budget_id
        or _required_text(usage.get("node_id"), "budget_usage.node_id") != node_id
        or _required_text(usage.get("attempt_id"), "budget_usage.attempt_id")
        != attempt_id
    ):
        _conflict(f"pre-operation BudgetUsage {usage_id} execution identity drifted")
    if _required_text(usage.get("charge_kind"), "budget_usage.charge_kind") != "PRECHARGE":
        _conflict(
            f"pre-operation Attempt {attempt_id} already crossed the PRECHARGE boundary"
        )
    expected_usage_id = _canonical_precharge_usage_id(
        budget_id=budget_id,
        node_id=node_id,
        attempt_id=attempt_id,
    )
    if usage_id != expected_usage_id:
        _conflict(f"pre-operation Attempt {attempt_id} PRECHARGE identity drifted")
    accepted = usage.get("accepted")
    if budget_status == "ACTIVE" and accepted is not True:
        _conflict(
            f"pre-operation Attempt {attempt_id} has a rejected PRECHARGE outcome"
        )


def _canonical_precharge_usage_id(
    *,
    budget_id: str,
    node_id: str,
    attempt_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{budget_id}\n{node_id}\n{attempt_id}\nworker-precharge".encode("utf-8")
    ).hexdigest()
    return f"budget-usage://{digest}"


def _normalized_operation_free_ids(values: Collection[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes, bytearray)):
        _conflict("operation_free_node_ids must be a collection of node ids")
    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "operation_free_node_ids is not iterable",
        ) from exc
    normalized = tuple(
        _required_text(value, "operation_free_node_id") for value in raw_values
    )
    return frozenset(normalized)


async def _try_operation(
    task_store: TaskStorePort,
    reservation_id: str,
) -> dict[str, Any] | None:
    try:
        value = await _maybe_await(task_store.get_operation_reservation(reservation_id))
    except Exception as exc:
        if _reason(exc) in _OPERATION_NOT_FOUND_REASONS:
            return None
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-operation reservation lookup failed",
        ) from exc
    if not isinstance(value, Mapping):
        _conflict("pre-operation reservation lookup returned an invalid record")
    return dict(value)


def _assert_attempt_identity(
    attempt: Mapping[str, Any],
    *,
    admitted: AdmittedExecution,
    attempt_id: str,
) -> None:
    attempt_no = _exact_int(attempt.get("attempt_no"), "attempt.attempt_no", minimum=1)
    expected_attempt_id = _attempt_id(admitted, attempt_no=attempt_no)
    if (
        _required_text(attempt.get("attempt_id"), "attempt.attempt_id") != attempt_id
        or attempt_id != expected_attempt_id
        or _required_text(attempt.get("graph_id"), "attempt.graph_id")
        != admitted.graph_id
        or _exact_int(attempt.get("graph_revision"), "attempt.graph_revision", minimum=1)
        != admitted.graph_revision
        or _required_text(attempt.get("node_id"), "attempt.node_id") != admitted.node_id
        or _required_text(attempt.get("operation_scope"), "attempt.operation_scope")
        != admitted.business_scope
        or _required_text(
            attempt.get("operation_fingerprint"),
            "attempt.operation_fingerprint",
        )
        != admitted.operation_fingerprint
    ):
        _conflict("pre-operation Attempt execution identity drifted")


def _assert_lease_identity(
    lease: Mapping[str, Any],
    *,
    attempt_id: str,
    require_active: bool,
) -> None:
    lease_id = _required_text(lease.get("lease_id"), "lease.lease_id")
    if _required_text(lease.get("attempt_id"), "lease.attempt_id") != attempt_id:
        _conflict(f"Lease {lease_id} Attempt identity drifted")
    if require_active and _status(lease) != "ACTIVE":
        _conflict(f"Lease {lease_id} is not ACTIVE")
    _required_text(lease.get("owner_id"), "lease.owner_id")
    _exact_int(lease.get("fencing_token"), "lease.fencing_token", minimum=1)


def _required_records(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        _conflict(f"{field_name} is not a record list")
    return list(value)


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _conflict(f"{field_name} is not a mapping")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _conflict(f"{field_name} must be non-empty and trimmed")
    return value


def _exact_int(value: Any, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _conflict(f"{field_name} must be an integer >= {minimum}")
    return value


def _status(record: Mapping[str, Any]) -> str:
    value = record.get("status")
    return str(getattr(value, "value", value) or "").strip().upper()


def _reason(exc: BaseException) -> str:
    return str(getattr(exc, "reason_code", "") or "").strip()


def _raise_takeover_acquire_error(exc: BaseException) -> NoReturn:
    reason = _reason(exc)
    if reason == "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED":
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "an Operation reservation appeared before Lease takeover",
        ) from exc
    if reason in _LEASE_CONTENTION_REASONS:
        raise GovernedRuntimeError(
            GOVERNED_EXECUTION_IN_PROGRESS,
            "pre-operation Attempt still has a live execution owner",
        ) from exc
    raise GovernedRuntimeError(
        GOVERNED_DURABLE_STATE_CONFLICT,
        f"pre-operation Attempt takeover failed: {reason or type(exc).__name__}",
    ) from exc


def _conflict(detail: str) -> None:
    raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, detail)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "PreOperationRecoveryCandidate",
    "acquire_preoperation_takeover_lease",
    "classify_preoperation_recovery_candidates",
]
