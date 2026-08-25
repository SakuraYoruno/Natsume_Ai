"""TaskNode 验收落库的原子提交适配器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _status,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


async def commit_accepted_node_output(
    *,
    store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    node: Mapping[str, Any],
    attempt_id: str,
    receipt_ref: str,
    worker_output: Mapping[str, Any],
    evidence_refs: Sequence[str],
    acceptance_judgement: Mapping[str, Any],
    effect_intents: Sequence[Any],
) -> dict[str, Any]:
    """在一个提交单元中落决策、产物、效果及 ``ACCEPTED`` 节点。

    这些记录共享同一节点版本：若只先写入其中一项，恢复路径可能把未验收的
    worker 输出误当作已授权效果。返回值会再次核对谱系，防止存储端错误地把
    相邻尝试的重放结果交给当前调用方。
    """

    version = _version(node)
    # Outbox 与节点版本一起推进，使外部观察者只能在验收事实已耐久后收到事件。
    arguments = {
        "expected_version": version,
        "outbox": runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_NODE",
            aggregate_id=(
                f"{admitted.graph_id}@{admitted.graph_revision}/"
                f"{admitted.node_id}"
            ),
            aggregate_version=version + 1,
            event_type="TASK_NODE_ACCEPTED",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        ),
        "acceptance_judgement": acceptance_judgement,
        "attempt_id": attempt_id,
        "receipt_ref": receipt_ref,
        "worker_output": worker_output,
        "effect_intents": effect_intents,
    }

    async def commit() -> Any:
        return await _maybe_await(
            store.commit_node_acceptance(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
                **arguments,
            )
        )

    # 响应丢失并不等于事务失败；用完全相同的条件写重放一次，由存储端的幂等
    # 键/版本比较返回既有结果，而不是自行构造成功响应。
    result = _mapping(
        await _response_loss_exact_replay(commit),
        field_name="atomic node artifact acceptance",
    )
    accepted = result.get("node")
    artifact = result.get("artifact")
    decision = result.get("decision")
    if (
        not isinstance(accepted, Mapping)
        or not isinstance(artifact, Mapping)
        or not isinstance(decision, Mapping)
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "atomic node acceptance returned incomplete durable authority",
        )
    if (
        _status(accepted) != "ACCEPTED"
        or _required_record_text(artifact, "graph_id") != admitted.graph_id
        or _required_record_text(artifact, "node_id") != admitted.node_id
        or _required_record_text(artifact, "attempt_id") != attempt_id
        or _required_record_text(artifact, "receipt_ref") != receipt_ref
        or _required_record_text(decision, "target_status") != "ACCEPTED"
        or _required_record_text(decision, "attempt_id") != attempt_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "atomic node acceptance returned a different artifact lineage",
        )
    return dict(accepted)


async def commit_acceptance_review(
    *,
    store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    node: Mapping[str, Any],
    graph: Mapping[str, Any],
    attempt_id: str,
    evidence_refs: Sequence[str],
    acceptance_judgement: Mapping[str, Any],
    review_item: Any,
    defer_shared_terminal: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """原子提交验收决策、等待节点、ReviewItem 与可选的图阻塞。

    ``defer_shared_terminal`` 用于由上层协调共享图终态的场景：本函数仅冻结节点
    审查检查点，避免多个并发节点各自抢写同一个 TaskGraph 版本。
    """

    node_version = _version(node)
    graph_version = _version(graph)
    node_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_NODE",
        aggregate_id=(
            f"{admitted.graph_id}@{admitted.graph_revision}/"
            f"{admitted.node_id}"
        ),
        aggregate_version=node_version + 1,
        event_type="TASK_NODE_WAITING_ACCEPTANCE_REVIEW",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )
    # 非延后模式由本事务同时阻塞图，保证审查项可见时不会有后续节点继续执行。
    graph_outbox = None
    expected_graph_version = None
    if not defer_shared_terminal:
        expected_graph_version = graph_version
        graph_outbox = runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_BLOCKED",
        )
    arguments = {
        "attempt_id": attempt_id,
        "expected_node_version": node_version,
        "acceptance_judgement": acceptance_judgement,
        "node_outbox": node_outbox,
        "review_item": review_item,
        "work_order_id": admitted.work_order_id,
        "expected_graph_version": expected_graph_version,
        "graph_outbox": graph_outbox,
    }

    async def commit() -> Any:
        return await _maybe_await(
            store.commit_node_acceptance_review(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
                **arguments,
            )
        )

    result = _mapping(
        await _response_loss_exact_replay(commit),
        field_name="atomic node acceptance review",
    )
    waiting = result.get("node")
    current_graph = result.get("graph")
    review = result.get("review")
    decision = result.get("decision")
    if (
        not isinstance(waiting, Mapping)
        or not isinstance(current_graph, Mapping)
        or not isinstance(review, Mapping)
        or not isinstance(decision, Mapping)
        or _required_record_text(decision, "target_status")
        != "WAITING_ACCEPTANCE_REVIEW"
        or _required_record_text(decision, "attempt_id") != attempt_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "atomic acceptance review returned different lineage",
        )
    if (
        not bool(result.get("replayed"))
        and _status(waiting) != "WAITING_ACCEPTANCE_REVIEW"
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "new acceptance review did not reach its waiting checkpoint",
        )
    if not defer_shared_terminal and _status(current_graph) != "BLOCKED":
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "direct acceptance review did not block its TaskGraph",
        )
    return dict(waiting), dict(current_graph), dict(review)


async def _response_loss_exact_replay(
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """以相同请求重试一次可能已提交但响应丢失的原子写入。

    这里不区分第一次异常的类别，因为写入端才掌握是否已落库；第二次调用若仍
    失败会如实上抛，调用方不会把网络错误转换成假成功。
    """
    try:
        return await operation()
    except Exception:
        return await operation()




__all__ = [
    "commit_acceptance_review",
    "commit_accepted_node_output",
]
