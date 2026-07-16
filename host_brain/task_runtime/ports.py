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

    def get_attempt(self, attempt_id: str) -> Mapping[str, Any]: ...

    def get_active_lease(self, attempt_id: str) -> Mapping[str, Any] | None: ...

    def get_operation_reservation(self, reservation_id: str) -> Mapping[str, Any]: ...

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

    def transition_node_and_stage_acceptance_effects(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Mapping[str, Any] | Any,
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
    "GovernedWorkerPort",
    "GovernedWorkerResolverPort",
    "RuntimeOutboxPublisherPort",
    "TaskOutboxFactoryPort",
    "TaskStorePort",
    "VerificationPort",
]
