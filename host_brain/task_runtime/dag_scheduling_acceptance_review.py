"""以只读方式重开执行后已耐久化的验收审查。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .node_execution_projection import NodeExecutionProjection
from .node_execution_records import build_review_context
from .ports import TaskStorePort
from .review_runtime import TaskNodeReviewGate, TaskNodeReviewRuntime
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


async def reopen_post_execution_acceptance_review(
    *,
    store: TaskStorePort,
    reviews: TaskNodeReviewRuntime,
    projection: NodeExecutionProjection,
    admitted: AdmittedExecution,
    node: Mapping[str, Any],
    command: Mapping[str, Any],
    work_order: Mapping[str, Any],
) -> tuple[str, dict[str, Any], TaskNodeReviewGate]:
    """先重开精确验收决策，再构造 Review 投影门禁。

    恢复路径不能仅根据当前节点状态猜测审查存在：必须从同一节点版本的耐久
    决策绑定最后一次 Attempt，避免旧审查项在 DAG 续跑后批准了新的执行结果。
    """

    # Attempt 序列由执行投影按持久化顺序返回；审查只允许针对最后一项，而非
    # worker 或调用方可指定的任意历史 attempt。
    attempts = await projection.attempts_for_node(admitted)
    if not attempts:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "post-execution review has no durable TaskAttempt",
        )
    attempt_id = _required_record_text(attempts[-1], "attempt_id")
    # ``node_version_after`` 将读取钉在进入 WAITING 状态后的版本，防止并发续跑
    # 已经推进节点时，仍取回一份外观正确但已失效的审查决定。
    decision = _mapping(
        await _maybe_await(
            store.get_node_acceptance_decision(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
                node_version_after=_version(node),
            )
        ),
        field_name="durable acceptance review decision",
    )
    if (
        _required_record_text(decision, "target_status")
        != "WAITING_ACCEPTANCE_REVIEW"
        or _required_record_text(decision, "attempt_id") != attempt_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "post-execution Review lost its Acceptance decision",
        )
    gate = reviews.post_execution_acceptance_gate(
        context=build_review_context(
            admitted=admitted,
            command=command,
            work_order=work_order,
            node=node,
            attempt_id=attempt_id,
            reason="Acceptance policy requires durable post-execution review",
            additional_evidence_refs=tuple(decision.get("evidence_refs") or ()),
        )
    )
    return attempt_id, dict(decision), gate




__all__ = ["reopen_post_execution_acceptance_review"]
