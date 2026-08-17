"""针对单个耐久 TaskGraph 修订版的纯函数、确定性调度决策。

模块刻意不做持久化、读时钟、租约或 worker 派发。B1 可在原子 TaskStore 工作单元中应用
一次决策，无需重复实现依赖、重试到期或并发规则，也避免调度预览本身产生副作用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus


class DagScheduleInputError(ValueError):
    """仓储快照不满足调度安全不变量，不能被转换为可执行节点。"""


@dataclass(frozen=True, slots=True)
class DagNodeScheduleCandidate:
    """纯调度器所需的最小不可变节点投影。"""

    node_id: str
    dependencies: tuple[str, ...]
    status: TaskNodeStatus
    retry_not_before_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DagScheduleDecision:
    """一次确定性调度预览；持久化应用及租约声明仍由 B1 负责。"""

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
        """B1 必须先持久化为 READY 的节点，防止直接跨状态派发。"""

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

    依赖均 ACCEPTED 的 BLOCKED 节点与到期 RETRY_PENDING 节点视作「有效 READY」。两者仍
    分开列出，使 B1 能在声明前先应用合法 READY 状态转换；依赖未验收的 READY 节点失败关闭，
    永不进入选择集。
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

    # 畸形耐久快照不能靠回退插入顺序或串行执行变成可执行。准入通常拒绝环，但恢复/重放
    # 可能读到旧行或人工构造行，因此调度边界再次验证图不变量。
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
    """以稳定、可操作的错误拒绝任意依赖环。"""

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
    """验证计数为非 bool 的下界整数，避免松散快照破坏并发或时间判断。"""
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
