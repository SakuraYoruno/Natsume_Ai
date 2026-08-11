"""Frozen base-schema inventory for the canonical TaskStore."""

from __future__ import annotations


TASK_RUNTIME_TABLES = frozenset(
    {
        "task_runtime_graph",
        "task_runtime_node",
        "task_runtime_attempt",
        "task_runtime_lease",
        "task_runtime_heartbeat",
        "task_runtime_idempotency_reservation",
        "task_runtime_budget_reservation",
        "task_runtime_outbox",
    }
)
TASK_RUNTIME_REQUIRED_COLUMNS = {
    "task_runtime_idempotency_reservation": frozenset(
        {
            "attempt_id",
            "lease_id",
            "owner_id",
            "fencing_token",
            "expires_ts_ms",
            "legacy_receipt_raw",
            "reconciliation_proof_ref",
            "reconciliation_proof_digest",
            "reconciliation_proof_key_id",
            "reconciliation_proof_signature",
            "reconciliation_action",
            "reconciliation_outcome",
            "reconciler_actor_id",
            "reconciliation_recovery_version",
            "recovery_reason_code",
            "recovery_ts_ms",
        }
    ),
}
DAG_SCHEDULING_REQUIRED_COLUMNS = frozenset({"retry_not_before_ms"})
BUDGET_USAGE_REQUIRED_COLUMNS = frozenset(
    {
        "budget_id",
        "usage_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "charge_kind",
        "delta_json",
        "delta_digest",
        "accepted",
        "reason_code",
        "exceeded_dimensions_json",
        "reservation_version_before",
        "reservation_version_after",
        "outbox_id",
        "created_ts_ms",
    }
)
GRAPH_CANCELLATION_INTENT_REQUIRED_COLUMNS = frozenset(
    {
        "intent_ref",
        "schema_version",
        "request_id",
        "request_identity",
        "source",
        "reason_code",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_at_request",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "status",
        "status_version",
        "requested_ts_ms",
        "finalized_ts_ms",
    }
)
GRAPH_CANCELLATION_DECISION_REQUIRED_COLUMNS = frozenset(
    {
        "decision_ref",
        "schema_version",
        "intent_ref",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_before",
        "budget_version_after",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)
GRAPH_CANCELLATION_EFFECT_FENCE_REQUIRED_COLUMNS = frozenset(
    {
        "effect_id",
        "intent_ref",
        "observed_status",
        "resolution_status",
        "observed_status_version",
        "created_ts_ms",
        "resolved_ts_ms",
    }
)


__all__ = [
    "BUDGET_USAGE_REQUIRED_COLUMNS",
    "DAG_SCHEDULING_REQUIRED_COLUMNS",
    "GRAPH_CANCELLATION_DECISION_REQUIRED_COLUMNS",
    "GRAPH_CANCELLATION_EFFECT_FENCE_REQUIRED_COLUMNS",
    "GRAPH_CANCELLATION_INTENT_REQUIRED_COLUMNS",
    "TASK_RUNTIME_REQUIRED_COLUMNS",
    "TASK_RUNTIME_TABLES",
]
