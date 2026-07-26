"""Focused runtime adapter for atomic TaskNode Acceptance persistence."""

from __future__ import annotations

import inspect
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
    """Commit decision, artifact, effects and ACCEPTED Node in one UoW."""

    version = _version(node)
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
    """Commit decision, waiting Node, ReviewItem and optional Graph block."""

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
    try:
        return await operation()
    except Exception:
        return await operation()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "commit_acceptance_review",
    "commit_accepted_node_output",
]
