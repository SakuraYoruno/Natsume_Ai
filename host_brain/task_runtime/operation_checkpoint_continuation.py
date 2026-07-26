"""Continue verification and acceptance from a durable COMMITTED checkpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.node_artifact import is_task_result_artifact_ref

from .accepted_graph_finalization import AcceptedGraphFinalizationRuntime
from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import (
    approved_commit_event,
    build_execution_receipt,
    terminal_event,
)
from .coordinator_records import (
    _evidence_refs,
    _exception_reason,
    _mapping,
    _node_input_refs,
    _record_positive_int,
    _required_record_text,
    _review_evidence_refs,
    _status,
    _text,
)
from .errors import (
    GOVERNED_ACCEPTANCE_FAILED,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_REVIEW_REQUIRED,
    GOVERNED_VERIFICATION_EVIDENCE_MISSING,
    GOVERNED_VERIFICATION_FAILED,
    GovernedRuntimeError,
)
from .node_execution_records import build_review_context
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .internal_checkpoint_budget_rejection import (
    InternalCheckpointBudgetRejectionRuntime,
    is_internal_agent_checkpoint_authority,
    requires_internal_terminal_bundle,
)
from .node_worker_stream import _maybe_await
from .operation_checkpoint_budget_recovery import recover_checkpoint_budget
from .operation_checkpoint_records import build_checkpoint_execution_context
from .operation_checkpoint_validation import WorkerBudgetUsageCheckpointState
from .operation_checkpoint_verification import reopen_durable_verification
from .operation_recovery_runtime import CommittedOperationCheckpoint
from .ports import AcceptancePort, InternalAgentCheckpointUnitOfWorkPort
from .ports import TaskStorePort, VerificationPort
from .review_runtime import TaskNodeReviewGate, TaskNodeReviewRuntime
from .runtime_outbox import RuntimeOutbox


class CommittedCheckpointContinuation:
    """Advance state after a worker checkpoint without invoking that worker."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
        runtime_outbox: RuntimeOutbox,
        budget_runtime: DurableBudgetRuntime,
        node_lifecycle: DurableNodeLifecycleRuntime,
        verifier: VerificationPort,
        acceptance: AcceptancePort,
        review_runtime: TaskNodeReviewRuntime | None,
        owner_id: str,
        graph_finalizer: AcceptedGraphFinalizationRuntime,
    ) -> None:
        self._store = task_store
        self._runtime_outbox = runtime_outbox
        self._budgets = budget_runtime
        self._node_lifecycle = node_lifecycle
        self._verifier = verifier
        self._acceptance = acceptance
        self._reviews = review_runtime
        self._owner_id = str(owner_id)
        self._graph_finalizer = graph_finalizer
        self._internal_budget_rejection = (
            InternalCheckpointBudgetRejectionRuntime(
                checkpoint_uow=checkpoint_uow,
                runtime_outbox=runtime_outbox,
                owner_id=owner_id,
            )
        )

    async def resume(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        checkpoint: CommittedOperationCheckpoint,
        node_grant_ref: str | None,
        defer_shared_terminal: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        worker_commit = checkpoint.worker_commit
        attempt = checkpoint.attempt
        attempt_id = checkpoint.attempt_id
        payload_digest = checkpoint.payload_digest
        receipt_ref = checkpoint.receipt_ref
        digest_namespace = {
            "receipt_identity_digest": "receipt-identity",
        }.get(checkpoint.payload_digest_source, "recovery-projection")
        digest_ref = (
            f"sha256://{digest_namespace}/{payload_digest.split(':', 1)[1]}"
        )
        evidence_refs = (receipt_ref, digest_ref)
        recovery_checkpoint_projection = {
            "worker_replayed": False,
            "payload_digest": payload_digest,
            "payload_digest_source": checkpoint.payload_digest_source,
            "budget_usage_state": checkpoint.budget_usage_state.value,
            "budget_usage_reconstructed": (
                checkpoint.budget_usage_state
                is WorkerBudgetUsageCheckpointState.MISSING
            ),
            "durable_elapsed_ms": checkpoint.durable_elapsed_ms,
        }
        receipt_projection = {
            **worker_commit,
            "recovery_checkpoint": recovery_checkpoint_projection,
        }
        active_lease = checkpoint.lease

        attempt_status = _status(attempt)
        if attempt_status not in {"LEASED", "RUNNING", "SUCCEEDED"}:
            self._blocked(
                f"committed checkpoint Attempt cannot resume from {attempt_status}"
            )

        current_node = dict(node)
        node_status = _status(current_node)
        charge, recovery_hold = await recover_checkpoint_budget(
            budget_runtime=self._budgets,
            runtime_outbox=self._runtime_outbox,
            admitted=admitted,
            budget=budget,
            checkpoint=checkpoint,
            worker_commit=worker_commit,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            evidence_refs=evidence_refs,
        )
        if recovery_hold is not None:
            yield recovery_hold
            return
        if charge is None:
            self._blocked("checkpoint budget recovery returned no decision")
        budget = charge.context

        # Only an authoritative budget result may advance the Attempt/Node.
        # A denied result still enters RUNNING first so the Store-owned atomic
        # rejection bundle can close Attempt/Lease/Node/Graph/Budget together.
        if attempt_status == "LEASED":
            lease_id = _required_record_text(active_lease or {}, "lease_id")
            fence = _record_positive_int(active_lease or {}, "fencing_token")
            attempt = await self._node_lifecycle.transition_attempt(
                admitted,
                attempt,
                lease_id=lease_id,
                fencing_token=fence,
                target_status="RUNNING",
                evidence_refs=evidence_refs,
            )
            attempt_status = _status(attempt)
        if attempt_status not in {"RUNNING", "SUCCEEDED"}:
            self._blocked(
                f"committed checkpoint Attempt cannot resume from {attempt_status}"
            )
        if node_status == "LEASED":
            current_node = await self._node_lifecycle.transition_node(
                admitted,
                current_node,
                "RUNNING",
                evidence_refs=evidence_refs,
            )
            node_status = _status(current_node)
        if not charge.accepted:
            rejection_fence = (
                _record_positive_int(active_lease, "fencing_token")
                if active_lease is not None
                else _record_positive_int(
                    checkpoint.reservation,
                    "fencing_token",
                )
            )
            internal_agent_checkpoint = is_internal_agent_checkpoint_authority(
                command,
            )
            if not defer_shared_terminal or requires_internal_terminal_bundle(admitted, command):
                receipt = await self._internal_budget_rejection.reject(
                    admitted=admitted,
                    charge=charge,
                    attempt=attempt,
                    node=current_node,
                    graph=graph,
                    reservation=checkpoint.reservation,
                    lease=_mapping(
                        checkpoint.lease,
                        field_name="Internal Agent budget rejection Lease",
                    ),
                    fencing_token=rejection_fence,
                    evidence_refs=evidence_refs,
                    require_internal_agent=internal_agent_checkpoint,
                )
            else:
                receipt = await self._node_lifecycle.reject_budget_charge(
                    admitted,
                    charge,
                    attempt_id=attempt_id,
                    fencing_token=rejection_fence,
                    defer_shared_terminal=defer_shared_terminal,
                )
            yield terminal_event(receipt)
            return

        if attempt_status == "RUNNING":
            lease_id = _required_record_text(active_lease or {}, "lease_id")
            fence = _record_positive_int(active_lease or {}, "fencing_token")
            attempt = await self._node_lifecycle.transition_attempt(
                admitted,
                attempt,
                lease_id=lease_id,
                fencing_token=fence,
                target_status="SUCCEEDED",
                evidence_refs=evidence_refs,
            )

        input_artifacts: list[dict[str, Any]] = []
        if any(
            is_task_result_artifact_ref(ref)
            for ref in _node_input_refs(current_node)
        ):
            resolved = await _maybe_await(
                self._store.resolve_node_input_artifacts(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                )
            )
            if not isinstance(resolved, Sequence) or isinstance(
                resolved,
                (str, bytes, bytearray),
            ) or any(not isinstance(item, Mapping) for item in resolved):
                self._blocked("committed checkpoint input artifacts are invalid")
            input_artifacts = [dict(item) for item in resolved]

        execution_context = build_checkpoint_execution_context(
            admitted=admitted,
            command=command,
            budget=budget,
            node=node,
            checkpoint=checkpoint,
            node_grant_ref=node_grant_ref,
            owner_id=self._owner_id,
            input_artifacts=input_artifacts,
        )

        if node_status == "RUNNING":
            current_node = await self._node_lifecycle.transition_node(
                admitted,
                current_node,
                "EXECUTED",
                evidence_refs=evidence_refs,
            )
            node_status = _status(current_node)
        if node_status == "EXECUTED":
            current_node = await self._node_lifecycle.transition_node(
                admitted,
                current_node,
                "VERIFYING",
                evidence_refs=evidence_refs,
            )
            node_status = _status(current_node)

        if node_status == "VERIFYING":
            verification = _mapping(
                await _maybe_await(
                    self._verifier.verify(
                        command=command,
                        work_order=work_order,
                        execution_context=execution_context,
                        worker_commit=worker_commit,
                        evidence_refs=evidence_refs,
                    )
                ),
                field_name="verification result",
            )
            verification_refs = _evidence_refs(verification)
            verification_status = _text(verification.get("status")).upper()
            missing_refs = tuple(
                ref for ref in evidence_refs if ref not in verification_refs
            )
            if verification_status == "PASS" and missing_refs:
                verification = {
                    **verification,
                    "status": "FAIL",
                    "reason_code": GOVERNED_VERIFICATION_EVIDENCE_MISSING,
                    "evidence_refs": list(
                        dict.fromkeys((*verification_refs, *evidence_refs))
                    ),
                }
                verification_refs = _evidence_refs(verification)
                verification_status = "FAIL"
            if verification_status != "PASS":
                current_node, graph, budget = (
                    await self._node_lifecycle.finalize_verification_failure(
                        admitted,
                        node=current_node,
                        graph=graph,
                        budget=budget,
                        attempt_id=attempt_id,
                        reservation_id=admitted.reservation_id,
                        evidence_refs=verification_refs or evidence_refs,
                        verification_result=verification,
                        defer_shared_terminal=defer_shared_terminal,
                    )
                )
                await self._runtime_outbox.publish_pending()
                yield terminal_event(
                    build_execution_receipt(
                        admitted,
                        attempt_id=attempt_id,
                        receipt_ref=receipt_ref,
                        payload_digest=payload_digest,
                        status="VERIFICATION_FAILED",
                        verification_status=verification_status or "ERROR",
                        accepted=False,
                        reason_code=(
                            _text(verification.get("reason_code"))
                            or GOVERNED_VERIFICATION_FAILED
                        ),
                        evidence_refs=verification_refs or evidence_refs,
                        projection=receipt_projection,
                    )
                )
                return
            current_node = await self._node_lifecycle.commit_node_verification(
                admitted,
                current_node,
                attempt_id=attempt_id,
                reservation_id=admitted.reservation_id,
                target_status="VERIFIED",
                evidence_refs=verification_refs,
                verification_result=verification,
            )
        elif node_status == "VERIFIED":
            verification = await reopen_durable_verification(
                task_store=self._store,
                admitted=admitted,
                current_node=current_node,
            )
            verification_refs = _evidence_refs(verification)
            if (
                _text(verification.get("status")).upper() != "PASS"
                or any(ref not in verification_refs for ref in evidence_refs)
            ):
                self._blocked("durable verification checkpoint is incomplete")
        else:
            self._blocked(
                f"committed checkpoint TaskNode cannot resume from {node_status}"
            )

        acceptance = _mapping(
            await _maybe_await(
                self._acceptance.judge(
                    command=command,
                    work_order=work_order,
                    execution_context=execution_context,
                    worker_commit=worker_commit,
                    verification_result=verification,
                    evidence_refs=verification_refs,
                )
            ),
            field_name="acceptance judgement",
        )
        acceptance_refs = _evidence_refs(acceptance)
        accepted = _text(acceptance.get("decision")).upper() == "ACCEPT"
        missing_acceptance_refs = tuple(
            ref for ref in evidence_refs if ref not in acceptance_refs
        )
        if accepted and missing_acceptance_refs:
            accepted = False
            acceptance = {
                **acceptance,
                "decision": "REVIEW",
                "reason_code": GOVERNED_ACCEPTANCE_FAILED,
                "evidence_refs": list(
                    dict.fromkeys((*acceptance_refs, *evidence_refs))
                ),
            }
            acceptance_refs = _evidence_refs(acceptance)

        if not accepted:
            current_node = await self._node_lifecycle.transition_node(
                admitted,
                current_node,
                "WAITING_ACCEPTANCE_REVIEW",
                evidence_refs=acceptance_refs or verification_refs,
            )
            if not defer_shared_terminal:
                graph = await self._node_lifecycle.transition_graph(
                    admitted,
                    graph,
                    "BLOCKED",
                )
            review_gate: TaskNodeReviewGate | None = None
            review_refs: tuple[str, ...] = ()
            if self._reviews is not None:
                review_gate = self._reviews.post_execution_acceptance_gate(
                    context=build_review_context(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        node=current_node,
                        attempt_id=attempt_id,
                        reason=(
                            "Acceptance policy requires durable "
                            "post-execution review"
                        ),
                    )
                )
                review_refs = _review_evidence_refs(review_gate)
            await self._runtime_outbox.publish_pending()
            projection = dict(receipt_projection)
            if review_gate is not None:
                projection["review"] = review_gate.to_dict()
            yield terminal_event(
                build_execution_receipt(
                    admitted,
                    attempt_id=attempt_id,
                    receipt_ref=receipt_ref,
                    payload_digest=payload_digest,
                    status="WAITING_ACCEPTANCE_REVIEW",
                    verification_status="PASS",
                    accepted=False,
                    reason_code=(
                        GOVERNED_REVIEW_REQUIRED
                        if review_gate is not None
                        else _text(acceptance.get("reason_code"))
                        or GOVERNED_ACCEPTANCE_FAILED
                    ),
                    evidence_refs=tuple(
                        dict.fromkeys(
                            (*(acceptance_refs or verification_refs), *review_refs)
                        )
                    ),
                    projection=projection,
                )
            )
            return

        current_node = await self._node_lifecycle.accept_node_output(
            admitted,
            current_node,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            worker_output=worker_commit,
            evidence_refs=acceptance_refs,
            acceptance_judgement=acceptance,
        )
        all_nodes_accepted = not defer_shared_terminal
        if all_nodes_accepted:
            for binding in admitted.nodes:
                durable_node = await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                )
                if _status(durable_node) != "ACCEPTED":
                    all_nodes_accepted = False
                    break
        if all_nodes_accepted:
            budget = await self._budgets.finalize(
                budget,
                target_status=BudgetReservationStatus.CONSUMED,
            )
            graph = await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            )
            if _status(graph) == "ACTIVE":
                try:
                    graph = await self._graph_finalizer.complete(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        graph=graph,
                        node_grant_ref=node_grant_ref,
                        worker_commit=worker_commit,
                        attempt_id=attempt_id,
                    )
                except Exception as exc:
                    # Preserve the legacy ACTIVE/CONSUMED checkpoint for exact
                    # re-entry.  Marking Graph FAILED after Budget was consumed
                    # would create an unrecoverable false terminal.  New
                    # production Graphs never use this split boundary.
                    await self._runtime_outbox.publish_pending(best_effort=True)
                    yield terminal_event(
                        build_execution_receipt(
                            admitted,
                            attempt_id=attempt_id,
                            receipt_ref=receipt_ref,
                            payload_digest=payload_digest,
                            status="RECOVERY_REQUIRED",
                            verification_status="PASS",
                            accepted=False,
                            reason_code=_exception_reason(
                                exc,
                                "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
                            ),
                            evidence_refs=acceptance_refs,
                            projection=receipt_projection,
                        )
                    )
                    return
        await self._runtime_outbox.publish_pending()
        receipt = build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status="ACCEPTED",
            verification_status="PASS",
            accepted=True,
            evidence_refs=acceptance_refs,
            projection=receipt_projection,
        )
        yield approved_commit_event(worker_commit, receipt)

    @staticmethod
    def _blocked(detail: str) -> None:
        raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)

__all__ = ["CommittedCheckpointContinuation"]
