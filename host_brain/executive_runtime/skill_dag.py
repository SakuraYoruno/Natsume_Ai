"""Skill DAG planner utilities for Executive routing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


_SAFE_NODE_TOKEN_RE = re.compile(r"[^a-z0-9_]+")


@dataclass
class SkillDagNode:
    node_id: str
    step_index: int
    route: str
    target_id: str
    description: str
    task_description: str
    route_reason: str
    allow_parallel: bool
    skill_family: str | None
    risk_level: int
    capability_tags: tuple[str, ...] = ()
    args_payload: dict[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    retry_limit: int = 0
    retry_backoff_ms: int = 0
    idempotency_key: str = ""
    fallback_policy: str = "default"

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "step_index": self.step_index,
            "route": self.route,
            "target_id": self.target_id,
            "description": self.description,
            "task_description": self.task_description,
            "route_reason": self.route_reason,
            "allow_parallel": self.allow_parallel,
            "skill_family": self.skill_family,
            "risk_level": self.risk_level,
            "capability_tags": list(self.capability_tags),
            "args_payload": dict(self.args_payload or {}),
            "dependencies": list(self.dependencies),
            "retry_limit": self.retry_limit,
            "retry_backoff_ms": self.retry_backoff_ms,
            "idempotency_key": self.idempotency_key,
            "fallback_policy": self.fallback_policy,
        }


@dataclass
class SkillDagPlan:
    plan_id: str
    nodes: list[SkillDagNode] = field(default_factory=list)
    execution_batches: list[list[str]] = field(default_factory=list)
    parallel_window: int = 1
    # Retained field name for serialized compatibility. ``True`` means this
    # plan is non-executable: callers must block admission with PLAN_CYCLE
    # rather than discard dependencies and serialize the nodes one-by-one.
    has_cycle_warning: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "parallel_window": self.parallel_window,
            "execution_batches": [list(batch) for batch in self.execution_batches],
            "nodes": [node.as_dict() for node in self.nodes],
            "parallel_enabled": any(len(batch) > 1 for batch in self.execution_batches),
            "has_cycle_warning": self.has_cycle_warning,
        }


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _sanitize_token(value: str) -> str:
    normalized = _SAFE_NODE_TOKEN_RE.sub("_", _normalize_text(value, lower=True)).strip("_")
    return normalized or "unknown"


def _safe_int(value: Any, *, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _resolve_parallel_window(value: Any | None = None) -> int:
    if value is not None:
        return _safe_int(value, default=4, minimum=1, maximum=16)
    # Application-configured runtime paths pass an explicit value.  Keeping a
    # deterministic default makes this pure planner usable by offline tools.
    return 4


def _coerce_dependencies(
    raw_value: Any,
    *,
    node_id_by_step_index: dict[int, str],
    known_node_ids: set[str],
) -> list[str]:
    if raw_value is None:
        return []

    items: list[Any]
    if isinstance(raw_value, (list, tuple, set)):
        items = list(raw_value)
    elif isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return []
        if "," in stripped:
            items = [part.strip() for part in stripped.split(",")]
        else:
            items = [stripped]
    else:
        items = [raw_value]

    dependencies: list[str] = []
    for item in items:
        node_id = ""
        if isinstance(item, int):
            node_id = node_id_by_step_index.get(item, "")
        else:
            as_text = _normalize_text(item)
            if as_text.isdigit():
                node_id = node_id_by_step_index.get(int(as_text), "")
            elif as_text in known_node_ids:
                node_id = as_text
        if node_id and node_id not in dependencies:
            dependencies.append(node_id)
    return dependencies


def _resolve_retry_policy(
    *,
    route: str,
    risk_level: int,
    fallback_policy: str,
    step: dict[str, Any],
) -> tuple[int, int]:
    # 显式重试策略优先于启发式默认值，但仍被上限夹住；规划器只表达可重试意图，
    # 真实执行是否安全重放仍由受治理的任务运行时根据副作用状态判定。
    explicit_retry = step.get("retry_limit")
    explicit_backoff = step.get("retry_backoff_ms")
    if explicit_retry is not None:
        retry_limit = _safe_int(explicit_retry, default=0, minimum=0, maximum=3)
        retry_backoff_ms = _safe_int(explicit_backoff, default=200 if retry_limit > 0 else 0, minimum=0, maximum=5000)
        return retry_limit, retry_backoff_ms

    policy = _normalize_text(fallback_policy, lower=True)
    if policy in {"no_retry", "fail_fast"}:
        return 0, 0
    if policy in {"retry_once", "retry_1"}:
        return 1, 200
    if policy in {"retry_twice", "retry_2"}:
        return 2, 250

    normalized_route = _normalize_text(route, lower=True)
    if risk_level <= 2 and normalized_route in {"skill", "delegate"}:
        return 1, 180
    return 0, 0


def _make_idempotency_key(
    *,
    plan_id: str,
    node_id: str,
    route: str,
    target_id: str,
    task_description: str,
) -> str:
    # 键只由计划中冻结的节点语义派生，不使用当前时间或随机数；同一离线规划输入
    # 重建后会得到同一键，供下游在持久命令层识别重复提交。
    payload = "|".join(
        [
            _normalize_text(plan_id, lower=True),
            _normalize_text(node_id, lower=True),
            _normalize_text(route, lower=True),
            _normalize_text(target_id, lower=True),
            _normalize_text(task_description, lower=True),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"idem::{digest}"


def _build_execution_batches(*, nodes: list[SkillDagNode], parallel_window: int) -> tuple[list[list[str]], bool]:
    """构建执行批次。

    Returns:
        (batches, has_cycle_warning) — has_cycle_warning 为 True 表示检测到
        cycle；此时 ``batches`` 必须为空，调用方只能以 ``PLAN_CYCLE``
        阻断 admission，不能忽略依赖后串行执行。
    """
    if not nodes:
        return [], False

    node_by_id = {node.node_id: node for node in nodes}
    indegree: dict[str, int] = {node.node_id: 0 for node in nodes}
    successors: dict[str, list[str]] = {node.node_id: [] for node in nodes}

    for node in nodes:
        # 不存在的依赖不会被悄悄转为可执行前置条件；它们不计入本纯函数的拓扑边，
        # 而完整计划校验应在准入层拒绝该结构，避免此处凭空猜测修复方式。
        valid_deps = [dep for dep in node.dependencies if dep in node_by_id and dep != node.node_id]
        indegree[node.node_id] = len(valid_deps)
        for dep in valid_deps:
            successors.setdefault(dep, []).append(node.node_id)

    ready: list[SkillDagNode] = [
        node for node in sorted(nodes, key=lambda item: (item.step_index, item.node_id))
        if indegree.get(node.node_id, 0) == 0
    ]
    pending_ids = {node.node_id for node in nodes}
    batches: list[list[str]] = []
    while pending_ids:
        ready = sorted(
            [node for node in ready if node.node_id in pending_ids],
            key=lambda item: (item.step_index, item.node_id),
        )
        if not ready:
            # 检测到环时返回空批次而非把剩余节点串行化；串行化会改变依赖语义，
            # 调用方必须把 has_cycle_warning 作为不可准入的 PLAN_CYCLE 处理。
            # A cycle has no safe topological order. Returning an empty
            # execution projection makes old callers fail closed instead of
            # converting an invalid plan into a different serial plan.
            return [], True
        else:
            first = ready[0]
            if first.allow_parallel:
                current_batch = [node for node in ready if node.allow_parallel][:parallel_window]
            else:
                current_batch = [first]

        batch_ids = [node.node_id for node in current_batch]
        batches.append(batch_ids)
        for node in current_batch:
            if node.node_id not in pending_ids:
                continue
            pending_ids.remove(node.node_id)
            for successor_id in successors.get(node.node_id, []):
                indegree[successor_id] = max(0, int(indegree.get(successor_id, 0)) - 1)
                if indegree[successor_id] == 0:
                    successor = node_by_id.get(successor_id)
                    if successor is not None:
                        ready.append(successor)

    return batches, False


def build_skill_dag_plan(
    *,
    plan_id: str,
    steps: list[dict[str, Any]],
    parallel_window: int | None = None,
) -> SkillDagPlan:
    normalized_plan_id = _normalize_text(plan_id) or "plan::runtime"
    normalized_steps = [dict(item or {}) for item in list(steps or [])]
    if not normalized_steps:
        return SkillDagPlan(plan_id=normalized_plan_id, nodes=[], execution_batches=[], parallel_window=1)

    window = _resolve_parallel_window(parallel_window)
    nodes: list[SkillDagNode] = []
    node_id_by_step_index: dict[int, str] = {}
    used_node_ids: set[str] = set()

    for position, step in enumerate(normalized_steps, start=1):
        route = _normalize_text(step.get("route"), lower=True)
        target_id = _normalize_text(step.get("target_id"))
        description = _normalize_text(step.get("description"))
        task_description = _normalize_text(step.get("task_description")) or description
        route_reason = _normalize_text(step.get("route_reason")) or "plan_step"
        allow_parallel = bool(step.get("allow_parallel", False))
        skill_family = _normalize_text(step.get("skill_family"), lower=True) or None
        risk_level = _safe_int(step.get("risk_level"), default=2, minimum=1, maximum=5)
        capability_tags = tuple(
            _normalize_text(tag, lower=True)
            for tag in list(step.get("capability_tags") or [])
            if _normalize_text(tag)
        )
        fallback_policy = _normalize_text(step.get("fallback_policy"), lower=True) or "default"
        step_index = _safe_int(step.get("step_index"), default=position, minimum=1, maximum=9999)

        requested_node_id = _normalize_text(step.get("node_id"))
        if requested_node_id:
            base_node_id = _sanitize_token(requested_node_id)
        else:
            base_node_id = f"n{step_index:02d}_{_sanitize_token(route or 'route')}_{_sanitize_token(target_id or description or 'task')}"
        node_id = base_node_id
        suffix = 1
        while node_id in used_node_ids:
            # 对相同展示名追加确定性后缀，保证每条依赖和幂等键都有唯一节点锚点，
            # 而不因字典覆盖丢失一个规划步骤。
            suffix += 1
            node_id = f"{base_node_id}_{suffix}"

        retry_limit, retry_backoff_ms = _resolve_retry_policy(
            route=route,
            risk_level=risk_level,
            fallback_policy=fallback_policy,
            step=step,
        )
        idempotency_key = _normalize_text(step.get("idempotency_key")) or _make_idempotency_key(
            plan_id=normalized_plan_id,
            node_id=node_id,
            route=route,
            target_id=target_id,
            task_description=task_description,
        )

        node = SkillDagNode(
            node_id=node_id,
            step_index=step_index,
            route=route,
            target_id=target_id,
            description=description,
            task_description=task_description,
            route_reason=route_reason,
            allow_parallel=allow_parallel,
            skill_family=skill_family,
            risk_level=risk_level,
            capability_tags=capability_tags,
            args_payload=dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {},
            dependencies=(),
            retry_limit=retry_limit,
            retry_backoff_ms=retry_backoff_ms,
            idempotency_key=idempotency_key,
            fallback_policy=fallback_policy,
        )
        nodes.append(node)
        node_id_by_step_index[step_index] = node_id
        used_node_ids.add(node_id)

    open_parallel_nodes: list[str] = []
    last_node_id = ""
    last_barrier_id = ""

    for index, node in enumerate(nodes):
        step = normalized_steps[index]
        explicit_dependencies = _coerce_dependencies(
            step.get("dependencies", step.get("depends_on")),
            node_id_by_step_index=node_id_by_step_index,
            known_node_ids=set(used_node_ids),
        )
        if explicit_dependencies:
            dependencies = [dep for dep in explicit_dependencies if dep != node.node_id]
        elif node.allow_parallel:
            dependencies = [last_barrier_id] if last_barrier_id else []
        else:
            if open_parallel_nodes:
                dependencies = list(open_parallel_nodes)
            elif last_node_id:
                dependencies = [last_node_id]
            else:
                dependencies = []

        nodes[index].dependencies = tuple(dict.fromkeys(dep for dep in dependencies if dep in used_node_ids and dep != node.node_id))

        if node.allow_parallel:
            if node.node_id not in open_parallel_nodes:
                open_parallel_nodes.append(node.node_id)
        else:
            open_parallel_nodes = []
            last_barrier_id = node.node_id
        last_node_id = node.node_id

    execution_batches, has_cycle_warning = _build_execution_batches(nodes=nodes, parallel_window=window)
    return SkillDagPlan(
        plan_id=normalized_plan_id,
        nodes=nodes,
        execution_batches=execution_batches,
        parallel_window=window,
        has_cycle_warning=has_cycle_warning,
    )


def merge_plan_steps_with_dag(*, steps: list[dict[str, Any]], dag_plan: SkillDagPlan) -> list[dict[str, Any]]:
    if not steps:
        return []
    node_by_step_index = {node.step_index: node for node in dag_plan.nodes}
    merged_steps: list[dict[str, Any]] = []
    for position, raw_step in enumerate(list(steps or []), start=1):
        step = dict(raw_step or {})
        step_index = _safe_int(step.get("step_index"), default=position, minimum=1, maximum=9999)
        node = node_by_step_index.get(step_index)
        if node is None:
            merged_steps.append(step)
            continue
        merged = dict(step)
        merged.update(node.as_dict())
        merged_steps.append(merged)
    return merged_steps


__all__ = [
    "SkillDagNode",
    "SkillDagPlan",
    "build_skill_dag_plan",
    "merge_plan_steps_with_dag",
]
