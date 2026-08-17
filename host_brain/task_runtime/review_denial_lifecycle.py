"""一次原子终态 Review 结算的运行时适配器。

预执行拒绝取消节点与图，后执行验收拒绝则失败节点与图；两种情况都在同一 UoW 中
释放图预算、记录结算引用和 outbox，避免审查决定与执行资源留下半状态。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from host_brain.persistence.task_review_denial_common import (
    TERMINAL_REVIEW_STATUSES,
    build_review_denial_settlement_ref,
)

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .coordinator_receipts import (
    _digest_from_receipt_ref,
    build_execution_receipt,
    terminal_event,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_REVIEW_DENIED,
    GovernedRuntimeError,
)
from .ports import TaskStorePort
from .node_execution_projection import NodeExecutionProjection
from .review_runtime import TaskNodeReviewGate
from .runtime_outbox import RuntimeOutbox


async def settle_denied_review(
    *,
    store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    node: Mapping[str, Any],
    graph: Mapping[str, Any],
    budget: BudgetRuntimeContext,
    gate: TaskNodeReviewGate,
    evidence_refs: Sequence[str],
    acceptance_decision: Mapping[str, Any] | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """在一个 SQLite UoW 中提交节点、图、共享预算和终态回执谱系。"""

    review_id = _text(gate.review_id)
    review_status = _text(gate.review_status).upper()
    phase = _text(gate.resume_phase).upper()
    review_status_version = gate.review_status_version
    if (
        not review_id
        or review_status not in TERMINAL_REVIEW_STATUSES
        or isinstance(review_status_version, bool)
        or not isinstance(review_status_version, int)
        or review_status_version < 1
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review lacks an exact terminal decision version",
        )
    # 审查阶段决定终态含义：尚未执行可取消；已执行但验收拒绝必须保留失败事实。
    if phase == "PRE_EXECUTION":
        node_target = "CANCELLED"
        graph_target = "CANCELLED"
        acceptance_ref = None
        attempt_ref = None
    elif phase == "POST_EXECUTION_ACCEPTANCE":
        node_target = "FAILED"
        graph_target = "FAILED"
        decision = _mapping(
            acceptance_decision,
            field_name="post-execution Acceptance decision",
        )
        acceptance_ref = _required_record_text(decision, "acceptance_ref")
        attempt_ref = _text(attempt_id)
        if (
            not attempt_ref
            or _required_record_text(decision, "attempt_id") != attempt_ref
            or _required_record_text(decision, "review_id") != review_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "denied Review lost its Acceptance/Attempt lineage",
            )
    else:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review resume phase is unsupported",
        )

    node_version = _version(node)
    graph_version = _version(graph)
    graph_status = _status(graph)
    if graph_status not in {"ACTIVE", "BLOCKED"}:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review Graph is no longer terminalizable",
        )
    settlement_ref = build_review_denial_settlement_ref(
        review_id=review_id,
        review_status=review_status,
        review_status_version=review_status_version,
        resume_phase=phase,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
        node_version_before=node_version,
        node_target_status=node_target,
        graph_status_before=graph_status,
        graph_version_before=graph_version,
        graph_target_status=graph_target,
        budget_id=budget.budget_id,
        budget_version_before=budget.version,
        acceptance_ref=acceptance_ref,
        attempt_id=attempt_ref,
    )
    # 结算引用与审查 checkpoint 进入所有 outbox 证据，异步消费者无需依赖进程内日志。
    bound_evidence = tuple(
        dict.fromkeys(
            (
                *(_text(ref) for ref in evidence_refs if _text(ref)),
                f"review://{review_id}",
                *(
                    (_text(gate.checkpoint_id),)
                    if _text(gate.checkpoint_id)
                    else ()
                ),
                settlement_ref,
            )
        )
    )
    node_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_NODE",
        aggregate_id=(
            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
        ),
        aggregate_version=node_version + 1,
        event_type=f"TASK_NODE_{node_target}",
        attempt_id=attempt_ref,
        evidence_refs=bound_evidence,
    )
    graph_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_GRAPH",
        aggregate_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        aggregate_version=graph_version + 1,
        event_type=f"TASK_GRAPH_{graph_target}",
        attempt_id=attempt_ref,
        evidence_refs=bound_evidence,
    )
    budget_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="BUDGET_RESERVATION",
        aggregate_id=budget.budget_id,
        aggregate_version=budget.version + 1,
        event_type="BUDGET_RESERVATION_RELEASED",
        evidence_refs=bound_evidence,
    )
    arguments = {
        "settlement_ref": settlement_ref,
        "review_id": review_id,
        "expected_node_version": node_version,
        "expected_graph_version": graph_version,
        "budget_id": budget.budget_id,
        "expected_budget_version": budget.version,
        "node_outbox": node_outbox,
        "graph_outbox": graph_outbox,
        "budget_outbox": budget_outbox,
        "acceptance_ref": acceptance_ref,
        "attempt_id": attempt_ref,
    }

    async def commit() -> Any:
        return await _maybe_await(
            store.settle_review_denial(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
                **arguments,
            )
        )

    result = _mapping(
        await _response_loss_exact_replay(commit),
        field_name="atomic Review denial settlement",
    )
    terminal_node = _mapping(result.get("node"), field_name="terminal Review node")
    terminal_graph = _mapping(result.get("graph"), field_name="terminal Review graph")
    terminal_budget = _mapping(
        result.get("budget"),
        field_name="terminal Review budget",
    )
    settlement = _mapping(
        result.get("settlement"),
        field_name="terminal Review settlement",
    )
    if (
        _status(terminal_node) != node_target
        or _status(terminal_graph) != graph_target
        or _status(terminal_budget) != "RELEASED"
        or _required_record_text(settlement, "settlement_ref") != settlement_ref
        or _required_record_text(settlement, "review_id") != review_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "atomic Review denial returned different terminal lineage",
        )
    return {
        "node": terminal_node,
        "graph": terminal_graph,
        "budget": budget.refreshed(terminal_budget),
        "settlement": settlement,
        "settlement_ref": settlement_ref,
        "evidence_refs": bound_evidence,
        "replayed": bool(result.get("replayed")),
    }


async def reopen_denied_review_terminal(
    *,
    store: TaskStorePort,
    projection: NodeExecutionProjection,
    admitted: AdmittedExecution,
    budget: BudgetRuntimeContext,
) -> dict[str, Any]:
    """调度器响应丢失后重新打开已提交结算，并重建不可重放副作用的拒绝回执。"""

    try:
        settlement = _mapping(
            await _maybe_await(
                store.get_review_denial_settlement_for_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                )
            ),
            field_name="durable Review denial settlement",
        )
    except Exception as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "terminal Review continuation has no durable settlement",
        ) from exc
    if (
        _required_record_text(settlement, "graph_id") != admitted.graph_id
        or int(settlement.get("graph_revision") or 0)
        != admitted.graph_revision
        or _required_record_text(settlement, "node_id") != admitted.node_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "terminal Review settlement targets a different TaskNode",
        )
    attempt_id = _text(settlement.get("attempt_id")) or None
    receipt_ref = None
    if attempt_id is not None:
        operation = await projection.try_operation(admitted.reservation_id)
        receipt_ref = _text((operation or {}).get("receipt_ref")) or None
        if (
            _status(operation or {}) != "COMMITTED"
            or not receipt_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "post-execution Review settlement lost its worker receipt",
            )
    settlement_ref = _required_record_text(settlement, "settlement_ref")
    review_id = _required_record_text(settlement, "review_id")
    evidence_refs = tuple(
        dict.fromkeys(
            _text(ref)
            for ref in (
                settlement_ref,
                f"review://{review_id}",
                settlement.get("acceptance_ref"),
                settlement.get("blocking_decision_ref"),
                receipt_ref,
            )
            if _text(ref)
        )
    )
    receipt = build_execution_receipt(
        admitted,
        attempt_id=attempt_id,
        receipt_ref=receipt_ref,
        payload_digest=_digest_from_receipt_ref(receipt_ref),
        status="REVIEW_DENIED",
        verification_status=("PASS" if attempt_id else "NOT_RUN"),
        accepted=False,
        replayed=True,
        reason_code=GOVERNED_REVIEW_DENIED,
        evidence_refs=evidence_refs,
        projection={
            "review": {
                "disposition": "REVIEW_DENIED",
                "allowed": False,
                "review_id": review_id,
                "review_status": settlement.get("review_status"),
                "review_status_version": settlement.get(
                    "review_status_version"
                ),
                "resume_phase": settlement.get("resume_phase"),
                "replay_side_effects": False,
            },
            "budget": budget.execution_payload(),
            "review_denial_settlement": settlement,
        },
    )
    return terminal_event(receipt)


async def _response_loss_exact_replay(
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """响应丢失时仅以完全相同操作重试一次；底层 UoW 负责精确重放校验。"""
    try:
        return await operation()
    except Exception:
        return await operation()


async def _maybe_await(value: Any) -> Any:
    """统一同步与异步存储端口返回值。"""
    return await value if inspect.isawaitable(value) else value


__all__ = ["reopen_denied_review_terminal", "settle_denied_review"]
