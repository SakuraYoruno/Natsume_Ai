"""Side-effect-free preparation shared by first execution and recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.contracts.agent_runtime.node_artifact import (
    is_task_result_artifact_ref,
)

from .admission import AdmittedExecution
from .coordinator_records import _node_input_refs
from .errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .node_worker_stream import _maybe_await
from .ports import TaskStorePort
from .preoperation_attempt_recovery import (
    PreOperationRecoveryCandidate,
    classify_preoperation_recovery_candidates,
)
from .worker_registry import GovernedWorkerRoute


async def resolve_node_input_artifacts(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
    node: Mapping[str, Any],
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Resolve accepted predecessor artifacts without mutating runtime state."""

    node_input_refs = _node_input_refs(node)
    if not any(is_task_result_artifact_ref(ref) for ref in node_input_refs):
        return node_input_refs, []
    resolved = await _maybe_await(
        task_store.resolve_node_input_artifacts(
            admitted.graph_id,
            admitted.graph_revision,
            admitted.node_id,
        )
    )
    if (
        not isinstance(resolved, Sequence)
        or isinstance(resolved, (str, bytes, bytearray))
        or any(not isinstance(item, Mapping) for item in resolved)
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "TaskNode input artifact resolution is invalid",
        )
    return node_input_refs, [dict(item) for item in resolved]


async def preverify_node_claim_authority(
    *,
    verifier: Any | None,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    node_grant_ref: str | None,
) -> dict[str, Any] | None:
    """Verify the immutable node Grant before claim or recovery mutation."""

    if node_grant_ref is None:
        return None
    if verifier is None:
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            "node grant verifier is required when a grant ref is present",
        )
    verify_for_claim = getattr(verifier, "verify_for_claim", None)
    if not callable(verify_for_claim):
        verify_for_claim = getattr(verifier, "verify", None)
    if not callable(verify_for_claim):
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            "node grant has no pre-claim authority verifier",
        )
    frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
    target_id = frozen_route.target_id or command.target_id
    preverified = await _maybe_await(
        verify_for_claim(
            grant_ref=node_grant_ref,
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
            graph_id=admitted.graph_id,
            root_work_order_id=str(
                getattr(work_order, "root_work_order_id", "")
                or command.root_work_order_id
                or ""
            ).strip(),
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
            route=frozen_route.kind,
            target_id=str(target_id or "").strip(),
            parent_grant_ref=str(command.governor_grant_ref or "").strip(),
        )
    )
    authority = getattr(preverified, "review_continuation_authority", None)
    if authority is None:
        return None
    if not isinstance(authority, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            "pre-claim Review continuation authority is malformed",
        )
    return dict(authority)


async def load_preoperation_recovery_candidate(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
) -> PreOperationRecoveryCandidate | None:
    """Read the exact scheduler view after the caller proved Operation absence."""

    snapshot = await _maybe_await(
        task_store.read_scheduler_snapshot(
            admitted.graph_id,
            admitted.graph_revision,
        )
    )
    if not isinstance(snapshot, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-operation scheduler snapshot is malformed",
        )
    candidates = classify_preoperation_recovery_candidates(
        snapshot,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        operation_free_node_ids=(admitted.node_id,),
    )
    return next(
        (item for item in candidates if item.node_id == admitted.node_id),
        None,
    )


def bind_preoperation_recovery_attempt(
    candidate: PreOperationRecoveryCandidate,
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind the classifier result back to the exact previously read journal."""

    if not attempts or not isinstance(attempts[-1], Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-operation recovery has no latest durable Attempt",
        )
    latest = dict(attempts[-1])
    if (
        str(latest.get("attempt_id") or "").strip() != candidate.attempt_id
        or latest.get("attempt_no") != candidate.attempt_no
        or latest.get("status_version") != candidate.attempt_version
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-operation recovery Attempt changed across durable reads",
        )
    return latest


__all__ = [
    "bind_preoperation_recovery_attempt",
    "load_preoperation_recovery_candidate",
    "preverify_node_claim_authority",
    "resolve_node_input_artifacts",
]
