"""Pure, deterministic scheduling decisions for one durable TaskGraph revision.

This module deliberately performs no persistence, clock reads, leasing, or
worker dispatch.  B1 can apply one decision through an atomic TaskStore unit of
work without duplicating dependency, retry-due, or concurrency rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus


class DagScheduleInputError(ValueError):
    """A repository snapshot is not safe to schedule."""


@dataclass(frozen=True, slots=True)
class DagNodeScheduleCandidate:
    """Minimal immutable node projection required by the pure scheduler."""

    node_id: str
    dependencies: tuple[str, ...]
    status: TaskNodeStatus
    retry_not_before_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DagScheduleDecision:
    """One deterministic scheduling preview; applying it is a B1 concern."""

    now_ms: int
    concurrency_limit: int
    occupied_slots: int
    available_slots: int
    ready_node_ids: tuple[str, ...]
    dependency_ready_node_ids: tuple[str, ...]
    retry_due_node_ids: tuple[str, ...]
    retry_waiting_node_ids: tuple[str, ...]
    dependency_blocked_node_ids: tuple[str, ...]
    eligible_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    deferred_node_ids: tuple[str, ...]

    @property
    def transition_to_ready_node_ids(self) -> tuple[str, ...]:
        """Nodes B1 must persist through BLOCKED/RETRY_PENDING -> READY."""

        return tuple(
            sorted((*self.dependency_ready_node_ids, *self.retry_due_node_ids))
        )


def compute_dag_schedule(
    candidates: Sequence[DagNodeScheduleCandidate],
    *,
    now_ms: int,
    concurrency_limit: int,
    occupied_slots: int = 0,
) -> DagScheduleDecision:
    """Return the stable READY/retry/fan-in decision for one graph snapshot.

    A BLOCKED node whose dependencies are all ACCEPTED and a due
    RETRY_PENDING node are treated as *effectively READY*.  They remain listed
    separately so the B1 persistence layer can apply their legal READY
    transition before claiming them.  READY nodes with an unaccepted
    dependency fail closed and are never selected.
    """

    _require_non_negative_int(now_ms, field_name="now_ms")
    _require_non_negative_int(
        concurrency_limit,
        field_name="concurrency_limit",
        minimum=1,
    )
    _require_non_negative_int(occupied_slots, field_name="occupied_slots")

    by_id: dict[str, DagNodeScheduleCandidate] = {}
    for candidate in candidates:
        _validate_candidate(candidate)
        if candidate.node_id in by_id:
            raise DagScheduleInputError(
                f"duplicate DAG scheduling candidate: {candidate.node_id}"
            )
        by_id[candidate.node_id] = candidate

    known_node_ids = set(by_id)
    for candidate in by_id.values():
        missing = sorted(set(candidate.dependencies) - known_node_ids)
        if missing:
            raise DagScheduleInputError(
                f"candidate {candidate.node_id} has unknown dependencies: "
                + ",".join(missing)
            )

    ready: list[str] = []
    dependency_ready: list[str] = []
    retry_due: list[str] = []
    retry_waiting: list[str] = []
    dependency_blocked: list[str] = []

    for node_id in sorted(by_id):
        candidate = by_id[node_id]
        dependencies_accepted = all(
            by_id[dependency].status is TaskNodeStatus.ACCEPTED
            for dependency in candidate.dependencies
        )
        if candidate.status in {
            TaskNodeStatus.READY,
            TaskNodeStatus.BLOCKED,
            TaskNodeStatus.RETRY_PENDING,
        } and not dependencies_accepted:
            dependency_blocked.append(node_id)
            continue
        if candidate.status is TaskNodeStatus.READY:
            ready.append(node_id)
        elif candidate.status is TaskNodeStatus.BLOCKED:
            dependency_ready.append(node_id)
        elif candidate.status is TaskNodeStatus.RETRY_PENDING:
            retry_not_before_ms = candidate.retry_not_before_ms
            if retry_not_before_ms is None:
                raise DagScheduleInputError(
                    f"RETRY_PENDING candidate {node_id} requires retry_not_before_ms"
                )
            if retry_not_before_ms <= now_ms:
                retry_due.append(node_id)
            else:
                retry_waiting.append(node_id)

    eligible = tuple(sorted((*ready, *dependency_ready, *retry_due)))
    available_slots = max(0, concurrency_limit - occupied_slots)
    selected = eligible[:available_slots]
    deferred = eligible[available_slots:]
    return DagScheduleDecision(
        now_ms=now_ms,
        concurrency_limit=concurrency_limit,
        occupied_slots=occupied_slots,
        available_slots=available_slots,
        ready_node_ids=tuple(ready),
        dependency_ready_node_ids=tuple(dependency_ready),
        retry_due_node_ids=tuple(retry_due),
        retry_waiting_node_ids=tuple(retry_waiting),
        dependency_blocked_node_ids=tuple(dependency_blocked),
        eligible_node_ids=eligible,
        selected_node_ids=selected,
        deferred_node_ids=deferred,
    )


def _validate_candidate(candidate: DagNodeScheduleCandidate) -> None:
    if not isinstance(candidate, DagNodeScheduleCandidate):
        raise DagScheduleInputError(
            "candidates must contain DagNodeScheduleCandidate values"
        )
    if not candidate.node_id or candidate.node_id != candidate.node_id.strip():
        raise DagScheduleInputError("candidate.node_id must be non-empty and trimmed")
    if not isinstance(candidate.status, TaskNodeStatus):
        raise DagScheduleInputError(
            f"candidate {candidate.node_id} has an invalid TaskNodeStatus"
        )
    if len(set(candidate.dependencies)) != len(candidate.dependencies):
        raise DagScheduleInputError(
            f"candidate {candidate.node_id} dependencies must be unique"
        )
    if candidate.node_id in candidate.dependencies:
        raise DagScheduleInputError(
            f"candidate {candidate.node_id} cannot depend on itself"
        )
    if (
        candidate.status is TaskNodeStatus.RETRY_PENDING
        and candidate.retry_not_before_ms is None
    ):
        raise DagScheduleInputError(
            f"RETRY_PENDING candidate {candidate.node_id} requires retry_not_before_ms"
        )
    if candidate.retry_not_before_ms is not None:
        _require_non_negative_int(
            candidate.retry_not_before_ms,
            field_name=f"{candidate.node_id}.retry_not_before_ms",
        )
        if candidate.status is not TaskNodeStatus.RETRY_PENDING:
            raise DagScheduleInputError(
                f"candidate {candidate.node_id} sets retry_not_before_ms outside RETRY_PENDING"
            )


def _require_non_negative_int(
    value: object,
    *,
    field_name: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DagScheduleInputError(
            f"{field_name} must be an integer >= {minimum}"
        )
    return value


__all__ = [
    "DagNodeScheduleCandidate",
    "DagScheduleDecision",
    "DagScheduleInputError",
    "compute_dag_schedule",
]
