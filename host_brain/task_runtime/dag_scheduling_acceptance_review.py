"""Read-only reopen of a durable post-execution Acceptance Review."""

from __future__ import annotations

import inspect
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
    """Reopen the exact decision before consulting the Review projection."""

    attempts = await projection.attempts_for_node(admitted)
    if not attempts:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "post-execution review has no durable TaskAttempt",
        )
    attempt_id = _required_record_text(attempts[-1], "attempt_id")
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


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["reopen_post_execution_acceptance_review"]
