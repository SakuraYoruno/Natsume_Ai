"""Dependency ports for the governed Task Runtime application layer.

The runtime owns orchestration only.  Concrete SQLite stores, command journals,
event publishers, worker implementations, and verification providers are wired
by composition and are intentionally absent from this package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol


class CommandRepositoryPort(Protocol):
    """Atomically admit a command and its frozen graph/assignment."""

    def get(self, command_id: str) -> Mapping[str, Any] | None: ...

    def admit(
        self,
        command: Any,
        work_order: Any,
        assignment: Any,
        graph: Any,
    ) -> Any: ...


class TaskStorePort(Protocol):
    """Subset of ``DurableTaskStore`` required by the DAG coordinator."""

    def get_graph(self, graph_id: str, revision: int) -> Mapping[str, Any]: ...

    def assert_budget_usage_ready(self) -> None: ...

    def assert_dag_scheduling_ready(self) -> None: ...

    def assert_parallel_settlement_decision_ready(self) -> None: ...

    def assert_parallel_success_decision_ready(self) -> None: ...

    def assert_graph_cancellation_ready(self) -> None: ...

    def get_node(self, graph_id: str, graph_revision: int, node_id: str) -> Mapping[str, Any]: ...

    def get_node_artifact(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> Mapping[str, Any]: ...

    def resolve_node_input_artifacts(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def get_attempt(self, attempt_id: str) -> Mapping[str, Any]: ...

    def get_lease(self, lease_id: str) -> Mapping[str, Any]: ...

    def get_active_lease(self, attempt_id: str) -> Mapping[str, Any] | None: ...

    def get_acceptance_effect_intent(
        self,
        effect_id: str,
    ) -> Mapping[str, Any]: ...

    def get_operation_reservation(self, reservation_id: str) -> Mapping[str, Any]: ...

    def resolve_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> Mapping[str, Any]: ...

    def try_resolve_checkpoint_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> Mapping[str, Any] | None: ...

    def resolve_internal_agent_completion_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
    ) -> Mapping[str, Any]: ...

    def resolve_internal_agent_takeover_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_graph_status: str,
        expected_node_status: str,
        expected_attempt_status: str,
    ) -> Mapping[str, Any]: ...

    def resolve_internal_agent_checkpoint_continuation_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_node_status: str,
    ) -> Mapping[str, Any]: ...

    def assert_live_node_execution_authority(
        self,
        *,
        reservation_id: str,
        business_scope: str,
        operation_fingerprint: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        lease_id: str,
        lease_owner_id: str,
        fencing_token: int,
    ) -> Mapping[str, Any]: ...

    def reconcile_operation_reservation_state(
        self,
        reservation_id: str,
    ) -> Mapping[str, Any]: ...

    def get_committed_operation_checkpoint(
        self,
        reservation_id: str,
    ) -> Mapping[str, Any]: ...

    def get_budget_usage(self, budget_id: str, usage_id: str) -> Mapping[str, Any]: ...

    def get_graph_cancellation_intent(self, intent_ref: str) -> Mapping[str, Any]: ...

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...

    def list_pending_graph_cancellations(
        self,
        *,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    def get_graph_cancellation_decision(self, decision_ref: str) -> Mapping[str, Any]: ...

    def get_graph_cancellation_decision_for_intent(
        self,
        intent_ref: str,
    ) -> Mapping[str, Any] | None: ...

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        revision: int,
        *,
        include_graph_outboxes: bool = False,
    ) -> Mapping[str, Any]: ...

    def transition_graph(
        self,
        graph_id: str,
        revision: int,
        *,
        expected_version: int,
        target_status: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def request_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str | None,
        expected_graph_version: int,
        expected_budget_version: int | None,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        intent_ref: str,
        intent_payload: Mapping[str, Any],
        graph_outbox: Any,
    ) -> Mapping[str, Any]: ...

    def advance_graph_cancellation(
        self,
        intent_ref: str,
    ) -> Mapping[str, Any]: ...

    def finalize_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str | None,
        expected_graph_version: int,
        expected_budget_version: int | None,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        intent_ref: str,
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: Any,
        budget_outbox: Any | None,
    ) -> Mapping[str, Any]: ...

    def transition_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        target_status: str,
        outbox: Any,
        verification_result: Mapping[str, Any] | Any | None = None,
        acceptance_judgement: Mapping[str, Any] | Any | None = None,
    ) -> Mapping[str, Any]: ...

    def transition_node_to_accepted_with_artifact(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Mapping[str, Any] | Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def commit_node_acceptance(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Mapping[str, Any] | Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Mapping[str, Any],
        effect_intents: Sequence[Mapping[str, Any] | Any] = (),
    ) -> Mapping[str, Any]: ...

    def transition_node_and_stage_acceptance_effects(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Mapping[str, Any] | Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Mapping[str, Any],
        effect_intents: Sequence[Mapping[str, Any] | Any],
    ) -> Mapping[str, Any]: ...

    def transition_graph_and_stage_acceptance_effects(
        self,
        graph_id: str,
        revision: int,
        *,
        expected_version: int,
        outbox: Any,
        effect_intents: Sequence[Mapping[str, Any] | Any],
    ) -> Mapping[str, Any]: ...

    def reschedule_acceptance_effect_intent(
        self,
        effect_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        reason_code: str,
        detail: str,
        delay_ms: int,
    ) -> Mapping[str, Any]: ...

    def request_cancel_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def create_attempt(
        self,
        *,
        attempt_id: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_no: int,
        expected_node_version: int,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def promote_retry_due(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def claim_ready_node(
        self,
        *,
        attempt_id: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_node_version: int,
        expected_attempt_count: int,
        owner_id: str,
        lease_ttl_ms: int,
        budget_id: str,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        attempt_created_outbox: Any,
        attempt_leased_outbox: Any,
        retry_ready_outbox: Any | None = None,
        review_continuation_authority: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def schedule_retry(
        self,
        attempt_id: str,
        *,
        budget_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        error_ref: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def schedule_retry_node_outcome(
        self,
        attempt_id: str,
        *,
        budget_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_graph_version: int,
        error_ref: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def fail_running_attempt_bundle(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        error_ref: str,
        graph_target_status: str,
        attempt_outbox: Any,
        budget_id: str | None = None,
        expected_budget_version: int | None = None,
        budget_outbox: Any | None = None,
    ) -> Mapping[str, Any]: ...

    def fail_running_attempt_node_bundle(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        error_ref: str,
        attempt_outbox: Any,
    ) -> Mapping[str, Any]: ...

    def acquire_lease(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        ttl_ms: int,
        expected_attempt_version: int,
        outbox: Any,
        absent_operation_reservation_id: str | None = None,
        existing_operation_reservation_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    def heartbeat_lease(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_lease_version: int,
        ttl_ms: int,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def reserve_operation(
        self,
        *,
        reservation_id: str,
        business_scope: str,
        operation_fingerprint: str,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        ttl_ms: int,
        outbox: Any,
    ) -> Any: ...

    def renew_operation_reservation(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        ttl_ms: int,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def transition_attempt(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        target_status: str,
        outbox: Any,
        error_ref: str | None = None,
    ) -> Mapping[str, Any]: ...

    def commit_operation(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        receipt_ref: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def create_budget_reservation(
        self,
        reservation: Any,
        *,
        graph_id: str | None,
        graph_revision: int | None,
        node_id: str | None,
        attempt_id: str | None,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def transition_budget(
        self,
        budget_id: str,
        *,
        expected_version: int,
        target_status: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def settle_parallel_graph_budget(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        graph_target_status: str,
        budget_target_status: str | None,
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: Any,
        budget_outbox: Any | None = None,
    ) -> Mapping[str, Any]: ...

    def get_parallel_settlement_decision(
        self,
        decision_ref: str,
    ) -> Mapping[str, Any]: ...

    def get_latest_parallel_settlement_decision(
        self,
        graph_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...

    def complete_parallel_graph_success(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: Any,
        budget_outbox: Any,
        effect_intents: Sequence[Mapping[str, Any] | Any],
    ) -> Mapping[str, Any]: ...

    def get_parallel_success_decision(
        self,
        decision_ref: str,
    ) -> Mapping[str, Any]: ...

    def get_latest_parallel_success_decision(
        self,
        graph_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...

    def consume_budget_once(
        self,
        budget_id: str,
        *,
        usage_id: str,
        charge_kind: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        delta: Any,
        expected_version: int,
        outbox: Any,
    ) -> Any: ...

    def reconcile_budget(self, budget_id: str) -> Mapping[str, Any]: ...


class InternalAgentCheckpointUnitOfWorkPort(Protocol):
    """Explicit UoW authority for exact committed checkpoint continuation."""

    def commit_internal_agent_recovered_checkpoint(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        projection: Mapping[str, Any],
        stage_ref: str,
        seal_digest: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...

    def reject_internal_agent_checkpoint_budget_bundle(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: Any,
    ) -> Mapping[str, Any]: ...

    def reject_committed_checkpoint_budget_bundle(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: Any,
    ) -> Mapping[str, Any]: ...

    def reopen_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: Any,
    ) -> Mapping[str, Any]: ...

    def reopen_committed_checkpoint_budget_rejection_terminal(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: Any,
    ) -> Mapping[str, Any]: ...


class RuntimeOutboxPublisherPort(Protocol):
    def publish_pending(self, *, limit: int = 100) -> Mapping[str, Any]: ...


class ExecutionProjectionReaderPort(Protocol):
    """Resolve a committed receipt into its durable public projection."""

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None: ...


class TaskOutboxFactoryPort(Protocol):
    """Factory compatible with ``OutboxMessage.create``."""

    def create(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        payload_ref: str,
        idempotency_key: str | None = None,
        graph_revision: int | None = None,
        attempt_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        outbox_id: str | None = None,
        available_ts_ms: int | None = None,
    ) -> Any: ...


class GraphCancellationOperationReconciliationStorePort(Protocol):
    """Narrow durable boundary for cancellation-bound operation settlement."""

    def get_operation_reservation(self, reservation_id: str) -> Mapping[str, Any]: ...

    def get_attempt(self, attempt_id: str) -> Mapping[str, Any]: ...

    def get_graph_cancellation_intent(self, intent_ref: str) -> Mapping[str, Any]: ...

    def reconcile_graph_cancellation_operation(
        self,
        intent_ref: str,
        reservation_id: str,
        *,
        action: str,
        expected_version: int,
        outbox: Any,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None = None,
        attestation_ref: str | None = None,
        attestation_key_id: str | None = None,
        attestation_signature: str | None = None,
    ) -> Mapping[str, Any]: ...


class GraphCancellationEffectReconciliationStorePort(Protocol):
    """Append-only proof boundary for one inventoried Effect uncertainty.

    The read returns one exact aggregate with ``case``, ``intent``, ``effect``,
    ``fence``, ``attempt`` and an optional ``proof``.  The mutation records a
    proof sidecar and Outbox only; it must not rewrite the 0092 fence, intent,
    decision, or a finalized cancellation.
    """

    def read_graph_cancellation_effect_reconciliation(
        self,
        intent_ref: str,
        effect_id: str,
    ) -> Mapping[str, Any]: ...

    def record_graph_cancellation_effect_reconciliation_proof(
        self,
        intent_ref: str,
        effect_id: str,
        *,
        purpose: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        expected_intent_status_version: int,
        expected_effect_status: str,
        expected_effect_status_version: int,
        expected_effect_fencing_token: int,
        expected_effect_attempt_count: int,
        expected_effect_lease_owner: str | None,
        expected_effect_lease_expires_ts_ms: int | None,
        expected_attempt_status: str,
        expected_attempt_status_version: int,
        expected_fence_observed_status: str,
        expected_fence_observed_status_version: int,
        expected_fence_resolution_status: str,
        outcome: str,
        target_resolution_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        issued_ts_ms: int,
        proof_id: str,
        outbox: Any,
    ) -> Mapping[str, Any]: ...


class TerminalReconciliationStorePort(Protocol):
    """Append-only inventory and exact Graph/Budget repair boundary."""

    def inventory_terminal_reconciliation_cases(
        self,
        *,
        limit: int = 500,
    ) -> Mapping[str, Any]: ...

    def assert_terminal_reconciliation_production_ready(self) -> None: ...

    def list_terminal_reconciliation_candidates(
        self,
        graph_id: str | None = None,
        graph_revision: int | None = None,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> tuple[Mapping[str, Any], ...]: ...

    def read_terminal_reconciliation_case(
        self,
        case_id: str,
    ) -> Mapping[str, Any]: ...

    def record_terminal_reconciliation_proof(
        self,
        case_id: str,
        *,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        action: str,
        target_graph_status: str | None,
        target_budget_status: str | None,
        authority_kind: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        proof_id: str,
        graph_outbox: Any | None,
        budget_outbox: Any | None,
        attestation_ref: str | None = None,
        attestation_key_id: str | None = None,
        attestation_signature: str | None = None,
    ) -> Mapping[str, Any]: ...


class GovernedWorkerPort(Protocol):
    """A worker selected by the frozen executor and node route."""

    def execute_stream(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[Mapping[str, Any]]: ...


class GovernedWorkerResolverPort(Protocol):
    """Side-effect-free worker preparation before operation reservation.

    A resolver may raise ``RetryableWorkerPreparationError`` to explicitly
    classify a transient pre-operation failure.  Returning a worker closes
    this retry boundary; execution occurs only after durable reservation.
    """

    def resolve(self, *, executor: str, worker_route: str) -> GovernedWorkerPort: ...


class VerificationPort(Protocol):
    def verify(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> Mapping[str, Any] | Any: ...


class AcceptancePort(Protocol):
    def judge(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> Mapping[str, Any] | Any: ...


__all__ = [
    "AcceptancePort",
    "CommandRepositoryPort",
    "ExecutionProjectionReaderPort",
    "GraphCancellationEffectReconciliationStorePort",
    "GraphCancellationOperationReconciliationStorePort",
    "GovernedWorkerPort",
    "GovernedWorkerResolverPort",
    "InternalAgentCheckpointUnitOfWorkPort",
    "RuntimeOutboxPublisherPort",
    "TerminalReconciliationStorePort",
    "TaskOutboxFactoryPort",
    "TaskStorePort",
    "VerificationPort",
]
