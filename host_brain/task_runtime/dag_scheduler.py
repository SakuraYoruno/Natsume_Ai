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

    # 先建立不可变的按 ID 索引，随后所有选择都排序；这样同一持久快照无论
    # 在哪个进程恢复，都得出相同派发顺序，避免字典插入顺序成为隐式策略。
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

    # A malformed durable snapshot must never be made executable by falling
    # back to insertion order or serial execution.  Admission normally
    # rejects cycles, but recovery/replay can observe old or manually-created
    # rows, so the scheduler repeats the graph invariant at its boundary.
    _reject_cycles(by_id)

    ready: list[str] = []
    dependency_ready: list[str] = []
    retry_due: list[str] = []
    retry_waiting: list[str] = []
    dependency_blocked: list[str] = []

    for node_id in sorted(by_id):
        candidate = by_id[node_id]
        # 依赖必须是已验收的业务结果，而不只是 worker 已返回。这样下游不会
        # 读取尚待人工复核、验收效果或补偿处理的中间产物。
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
    # 并发槽位只截断本轮“可派发”集合；被延后的节点仍保留 READY 语义，
    # 由下一次基于新快照的调度决定，而不是在内存中永久排队。
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


def _reject_cycles(
    candidates: dict[str, DagNodeScheduleCandidate],
) -> None:
    """Reject every dependency cycle with a stable, actionable error."""

    indegree = {node_id: len(candidate.dependencies) for node_id, candidate in candidates.items()}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in candidates}
    for candidate in candidates.values():
        for dependency in candidate.dependencies:
            dependents[dependency].append(candidate.node_id)

    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        node_id = ready.pop(0)
        visited += 1
        for dependent in sorted(dependents[node_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    if visited != len(candidates):
        cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
        raise DagScheduleInputError(
            "PLAN_CYCLE: durable DAG contains a dependency cycle: "
            + ",".join(cyclic)
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
