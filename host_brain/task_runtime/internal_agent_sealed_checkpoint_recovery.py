"""Exact sealed-checkpoint continuation for the Internal Agent route."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.contracts.agent_runtime.node_artifact import (
    is_task_result_artifact_ref,
)
from host_brain.contracts.agent_runtime.projection_recovery import (
    ProjectionRecoveryContractError,
    validate_worker_timing,
)

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_receipts import _worker_receipt_ref
from .coordinator_records import (
    _mapping,
    _node_input_refs,
    _record_positive_int,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .node_worker_stream import _maybe_await
from .operation_recovery_runtime import DurableOperationRecoveryRuntime
from .ports import (
    GovernedWorkerResolverPort,
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
)
from .runtime_outbox import RuntimeOutbox
from .worker_registry import GovernedWorkerRoute


async def recover_internal_agent_sealed_checkpoint(
    *,
    task_store: TaskStorePort,
    checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
    worker_resolver: GovernedWorkerResolverPort,
    operation_recovery: DurableOperationRecoveryRuntime,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    budget: BudgetRuntimeContext,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    reservation: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    node_grant_ref: str | None,
) -> tuple[dict[str, Any], int] | None:
    """Commit one exact Internal Agent seal without rerunning its worker."""

    try:
        frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
    except Exception:
        return None
    reservation_status = _status(reservation)
    if (
        frozen_route.kind != "delegate"
        or frozen_route.target_id != "agent_runtime"
        or _text(command.surface).lower() != "internal"
        or _text(command.route_type).lower() != "delegate"
        or _text(command.target_id) != "agent_runtime"
        or not str(node_grant_ref or "").strip()
        or reservation_status not in {"RECOVERY_REQUIRED", "COMMITTED"}
        or _status(latest_attempt) != "RECOVERABLE"
    ):
        return None

    recover_owner = _required_record_text(reservation, "owner_id")
    lease_id = _required_record_text(reservation, "lease_id")
    fencing_token = _record_positive_int(reservation, "fencing_token")
    historical_lease = _mapping(
        await _maybe_await(task_store.get_lease(lease_id)),
        field_name="Internal Agent historical Lease",
    )
    if (
        _status(historical_lease) != "EXPIRED"
        or _required_record_text(historical_lease, "attempt_id")
        != _required_record_text(latest_attempt, "attempt_id")
        or _required_record_text(historical_lease, "owner_id")
        != recover_owner
        or _record_positive_int(historical_lease, "fencing_token")
        != fencing_token
    ):
        return None

    worker = worker_resolver.resolve(
        executor=admitted.executor,
        worker_route=admitted.worker_route,
    )
    recover = getattr(worker, "recover_sealed_checkpoint", None)
    if not callable(recover):
        return None

    node_input_refs = _node_input_refs(node)
    input_artifacts: list[dict[str, Any]] = []
    if any(is_task_result_artifact_ref(ref) for ref in node_input_refs):
        resolved = await _maybe_await(
            task_store.resolve_node_input_artifacts(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
            )
        )
        if not isinstance(resolved, Sequence) or isinstance(
            resolved,
            (str, bytes, bytearray),
        ) or any(not isinstance(item, Mapping) for item in resolved):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "Internal Agent recovery input artifacts are invalid",
            )
        input_artifacts = [dict(item) for item in resolved]

    execution_context = {
        "execution_mode": "governed_task_runtime",
        "command_id": admitted.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "target_id": frozen_route.target_id,
        "command_target_id": command.target_id,
        "work_order_id": admitted.work_order_id,
        "assignment_id": admitted.assignment_id,
        "executor": admitted.executor,
        "worker_route": admitted.worker_route,
        "graph_id": admitted.graph_id,
        "graph_revision": admitted.graph_revision,
        "node_id": admitted.node_id,
        "attempt_id": _required_record_text(latest_attempt, "attempt_id"),
        "lease_id": lease_id,
        "lease_owner_id": recover_owner,
        "fencing_token": fencing_token,
        "reservation_id": admitted.reservation_id,
        "business_scope": admitted.business_scope,
        "operation_fingerprint": admitted.operation_fingerprint,
        "parent_grant_ref": str(command.governor_grant_ref or "").strip(),
        "budget": budget.execution_payload(),
        "node_input_refs": list(node_input_refs),
        "node_input_artifacts": input_artifacts,
        "node_acceptance_criteria": list(
            admitted.active_node.acceptance_criteria
        ),
        "node_risk_level": int(admitted.active_node.risk_level),
        "node_grant_ref": str(node_grant_ref).strip(),
    }
    recovered_raw = await _maybe_await(
        recover(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
        )
    )
    recovered = _mapping(
        recovered_raw,
        field_name="Internal Agent sealed checkpoint",
    )
    projection = _mapping(
        recovered.get("projection"),
        field_name="Internal Agent recovered projection",
    )
    worker_commit = _mapping(
        recovered.get("worker_commit"),
        field_name="Internal Agent recovered worker commit",
    )
    projection_id = _required_record_text(projection, "projection_id")
    payload_digest = _required_record_text(projection, "payload_digest")
    if (
        _worker_receipt_ref(worker_commit) != projection_id
        or not payload_digest.startswith("sha256:")
        or len(payload_digest) != 71
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Internal Agent recovered checkpoint identity drifted",
        )
    checkpoint_digest_ref = (
        "sha256://execution/" + payload_digest.split(":", 1)[1]
    )
    stage_ref = _required_record_text(recovered, "stage_ref")
    seal_digest = _required_record_text(recovered, "seal_digest")
    try:
        worker_timing = validate_worker_timing(
            recovered.get("worker_timing")
        )
    except ProjectionRecoveryContractError as exc:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Internal Agent seal has invalid worker timing authority",
        ) from exc
    worker_elapsed_ms = _record_positive_int(worker_timing, "elapsed_ms")
    expected_version = (
        _version(reservation) - 1
        if reservation_status == "COMMITTED"
        else _version(reservation)
    )
    if expected_version < 1:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Internal Agent recovery reservation version is invalid",
        )
    committed = await operation_recovery.commit_or_reconcile(
        commit=lambda: checkpoint_uow.commit_internal_agent_recovered_checkpoint(
            admitted.reservation_id,
            attempt_id=_required_record_text(latest_attempt, "attempt_id"),
            lease_id=lease_id,
            owner_id=recover_owner,
            fencing_token=fencing_token,
            expected_version=expected_version,
            projection=projection,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            outbox=runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="OPERATION_RESERVATION",
                aggregate_id=admitted.reservation_id,
                aggregate_version=expected_version + 1,
                event_type="OPERATION_COMMITTED",
                attempt_id=_required_record_text(
                    latest_attempt,
                    "attempt_id",
                ),
                evidence_refs=(projection_id, checkpoint_digest_ref),
            ),
        ),
        reservation_id=admitted.reservation_id,
        business_scope=admitted.business_scope,
        operation_fingerprint=admitted.operation_fingerprint,
        attempt_id=_required_record_text(latest_attempt, "attempt_id"),
        lease_id=lease_id,
        owner_id=recover_owner,
        fencing_token=fencing_token,
        expected_version=expected_version,
        receipt_ref=projection_id,
        checkpoint_digest_ref=checkpoint_digest_ref,
    )
    # Budget settlement remains in CommittedCheckpointContinuation so a
    # denied charge closes Attempt/Lease/Node through its canonical fenced
    # terminal bundle.  The seal-bound duration only replaces the unsafe
    # recovery-time timestamp estimate.
    return dict(committed), worker_elapsed_ms

__all__ = ["recover_internal_agent_sealed_checkpoint"]
