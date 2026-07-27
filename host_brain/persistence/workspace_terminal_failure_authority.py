"""Read-only signed failure proof for abandoning one active Workspace."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_operation_failure_outbox_identity,
    build_reconciliation_attestation_binding,
)
from host_brain.persistence.task_terminal_reconciliation_decision_authority import (
    canonical_current_parallel_terminal_decision,
)
from host_brain.persistence.task_store_types import build_outbox_payload_ref


_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,119}$")
_SIGNATURE = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANCELLATION_REASONS = frozenset(
    {"GOVERNED_CALLER_CANCELLED", "GRAPH_CANCELLATION_REQUESTED"}
)


def finalized_no_effect_failure_allows_workspace_abandonment(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    lease_id: str,
) -> bool:
    """Reopen one single-node signed RELEASE terminal without mutation authority."""

    if (
        not graph_id
        or type(graph_revision) is not int
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or not lease_id
    ):
        return False
    rows = conn.execute(
        """
        SELECT graph.status AS graph_status,
               graph.status_version AS graph_status_version,
               node.status AS node_status,
               node.status_version AS node_status_version,
               attempt.status AS attempt_status,
               attempt.status_version AS attempt_status_version,
               attempt.error_ref AS attempt_error_ref,
               attempt.operation_scope AS attempt_operation_scope,
               attempt.operation_fingerprint AS attempt_operation_fingerprint,
               lease.status AS lease_status,
               lease.status_version AS lease_status_version,
               lease.released_ts_ms AS lease_released_ts_ms,
               lease.owner_id AS lease_owner_id,
               lease.fencing_token AS lease_fencing_token,
               operation.reservation_id,
               operation.business_scope,
               operation.operation_fingerprint,
               operation.status AS operation_status,
               operation.status_version AS operation_status_version,
               operation.receipt_ref,
               operation.owner_id AS operation_owner_id,
               operation.fencing_token AS operation_fencing_token,
               operation.reconciliation_proof_ref,
               operation.reconciliation_proof_digest,
               operation.reconciliation_proof_key_id,
               operation.reconciliation_proof_signature,
               operation.reconciliation_action,
               operation.reconciliation_outcome,
               operation.reconciler_actor_id,
               operation.reconciliation_recovery_version,
               operation.recovery_reason_code,
               budget.budget_id,
               budget.status AS budget_status,
               budget.status_version AS budget_status_version
        FROM task_runtime_graph AS graph
        JOIN task_runtime_node AS node
          ON node.graph_id = graph.graph_id
         AND node.graph_revision = graph.revision
         AND node.node_id = ?
        JOIN task_runtime_attempt AS attempt
          ON attempt.graph_id = graph.graph_id
         AND attempt.graph_revision = graph.revision
         AND attempt.node_id = node.node_id
         AND attempt.attempt_id = ?
        JOIN task_runtime_lease AS lease
          ON lease.attempt_id = attempt.attempt_id
         AND lease.lease_id = ?
        JOIN task_runtime_idempotency_reservation AS operation
          ON operation.attempt_id = attempt.attempt_id
         AND operation.lease_id = lease.lease_id
        JOIN task_runtime_budget_reservation AS budget
          ON budget.graph_id = graph.graph_id
         AND budget.graph_revision = graph.revision
         AND budget.node_id IS NULL
         AND budget.attempt_id IS NULL
        WHERE graph.graph_id = ? AND graph.revision = ?
        """,
        (node_id, attempt_id, lease_id, graph_id, graph_revision),
    ).fetchall()
    row = rows[0] if len(rows) == 1 else None
    if row is None or not _terminal_shape_is_exact(row):
        return False
    node_count = _owner_cardinality(
        conn,
        graph_id=graph_id,
        graph_revision=graph_revision,
        attempt_id=attempt_id,
    )
    if node_count is None:
        return False
    if not _has_zero_downstream_authority(
        conn,
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
    ):
        return False
    try:
        proof_binding = build_reconciliation_attestation_binding(
            reservation_id=str(row["reservation_id"]),
            business_scope=str(row["business_scope"]),
            operation_fingerprint=str(row["operation_fingerprint"]),
            recovery_version=int(row["reconciliation_recovery_version"]),
            reconciler_actor_id=str(row["reconciler_actor_id"]),
            attestation_ref=str(row["reconciliation_proof_ref"]),
            recovery_action="RELEASE",
            reconciliation_outcome="NO_SIDE_EFFECT",
        )
    except Exception:
        return False
    expected_proof_digest = (
        "sha256:" + hashlib.sha256(proof_binding).hexdigest()
    )
    if str(row["reconciliation_proof_digest"]) != expected_proof_digest:
        return False
    error_ref = _operation_failure_error_ref(
        reservation_id=str(row["reservation_id"]),
        attempt_id=attempt_id,
        reason_code=str(row["recovery_reason_code"]),
        proof_ref=str(row["reconciliation_proof_ref"]),
        proof_digest=expected_proof_digest,
    )
    if str(row["attempt_error_ref"] or "") != error_ref:
        return False
    graph_wide_terminal = node_count == 1
    if (
        not graph_wide_terminal
        and not _parallel_failure_settlement_is_exact(
            conn,
            row=row,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            error_ref=error_ref,
        )
    ):
        return False
    return _terminal_outboxes_are_exact(
        conn,
        row=row,
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        lease_id=lease_id,
        error_ref=error_ref,
        graph_wide_terminal=graph_wide_terminal,
    )


def _terminal_shape_is_exact(row: sqlite3.Row) -> bool:
    reason = str(row["recovery_reason_code"] or "").strip().upper()
    recovery_version = row["reconciliation_recovery_version"]
    return bool(
        str(row["graph_status"]) == "FAILED"
        and str(row["node_status"]) == "FAILED"
        and str(row["attempt_status"]) == "FAILED"
        and str(row["lease_status"]) == "EXPIRED"
        and row["lease_released_ts_ms"] is not None
        and str(row["budget_status"]) == "RELEASED"
        and str(row["operation_status"]) == "RELEASED"
        and row["receipt_ref"] is None
        and str(row["reconciliation_action"]) == "RELEASE"
        and str(row["reconciliation_outcome"]) == "NO_SIDE_EFFECT"
        and type(recovery_version) is int
        and recovery_version >= 0
        and int(row["operation_status_version"]) == recovery_version + 1
        and str(row["business_scope"])
        == str(row["attempt_operation_scope"])
        and str(row["operation_fingerprint"])
        == str(row["attempt_operation_fingerprint"])
        and str(row["operation_owner_id"]) == str(row["lease_owner_id"])
        and int(row["operation_fencing_token"] or 0)
        == int(row["lease_fencing_token"] or 0)
        and bool(str(row["reconciler_actor_id"] or "").strip())
        and bool(str(row["reconciliation_proof_ref"] or "").strip())
        and bool(str(row["reconciliation_proof_key_id"] or "").strip())
        and _DIGEST.fullmatch(
            str(row["reconciliation_proof_digest"] or "")
        )
        is not None
        and _SIGNATURE.fullmatch(
            str(row["reconciliation_proof_signature"] or "")
        )
        is not None
        and _REASON.fullmatch(reason) is not None
        and reason not in _CANCELLATION_REASONS
    )


def _owner_cardinality(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    attempt_id: str,
) -> int | None:
    counts = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM task_runtime_node
           WHERE graph_id = ? AND graph_revision = ?) AS nodes,
          (SELECT COUNT(*) FROM task_runtime_idempotency_reservation
           WHERE attempt_id = ?) AS operations,
          (SELECT COUNT(*) FROM task_runtime_budget_reservation
           WHERE graph_id = ? AND graph_revision = ?
             AND node_id IS NULL AND attempt_id IS NULL) AS budgets,
          (SELECT COUNT(*) FROM host_command_journal
           WHERE graph_id = ? AND graph_revision = ?) AS commands
        """,
        (
            graph_id,
            graph_revision,
            attempt_id,
            graph_id,
            graph_revision,
            graph_id,
            graph_revision,
        ),
    ).fetchone()
    if (
        counts is None
        or int(counts["nodes"]) < 1
        or int(counts["operations"]) != 1
        or int(counts["budgets"]) != 1
        or int(counts["commands"]) != 1
    ):
        return None
    return int(counts["nodes"])


def _parallel_failure_settlement_is_exact(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    error_ref: str,
) -> bool:
    decision = canonical_current_parallel_terminal_decision(
        conn,
        {
            "graph_id": graph_id,
            "revision": graph_revision,
            "status": str(row["graph_status"]),
            "status_version": int(row["graph_status_version"]),
        },
    )
    if not isinstance(decision, dict):
        return False
    node_rows = conn.execute(
        """
        SELECT node_id, status, status_version
        FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY node_id
        """,
        (graph_id, graph_revision),
    ).fetchall()
    actual_states = {
        str(item["node_id"]): {
            "status": str(item["status"]),
            "status_version": int(item["status_version"]),
        }
        for item in node_rows
    }
    outcomes = decision.get("outcomes")
    budget_version_before = decision.get("budget_status_version_before")
    selected = next(
        (
            item
            for item in outcomes
            if isinstance(item, dict)
            and str(item.get("node_id") or "") == node_id
        ),
        None,
    ) if isinstance(outcomes, list) else None
    return bool(
        decision.get("decision_kind") == "FAILURE"
        and decision.get("graph_target_status") == "FAILED"
        and decision.get("budget_target_status") == "RELEASED"
        and decision.get("budget_id") == str(row["budget_id"])
        and type(budget_version_before) is int
        and budget_version_before + 1
        == int(row["budget_status_version"])
        and decision.get("node_states") == actual_states
        and isinstance(selected, dict)
        and selected.get("attempt_id") == attempt_id
        and selected.get("status") == "FAILED"
        and error_ref in tuple(selected.get("evidence_refs") or ())
    )


def _has_zero_downstream_authority(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
) -> bool:
    counts = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM task_runtime_worker_projection
           WHERE attempt_id = ?) AS projections,
          (SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent
           WHERE attempt_id = ?) AS effects,
          (SELECT COUNT(*) FROM task_runtime_graph_cancellation_intent
           WHERE graph_id = ? AND graph_revision = ?) AS cancellations,
          (SELECT COUNT(*) FROM task_runtime_node_verification_decision
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS verification,
          (SELECT COUNT(*) FROM task_runtime_node_acceptance_decision
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS acceptance,
          (SELECT COUNT(*) FROM task_runtime_node_artifact
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS artifacts
        """,
        (
            attempt_id,
            attempt_id,
            graph_id,
            graph_revision,
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            node_id,
        ),
    ).fetchone()
    return bool(
        counts is not None
        and all(int(counts[key]) == 0 for key in counts.keys())
    )


def _terminal_outboxes_are_exact(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    lease_id: str,
    error_ref: str,
    graph_wide_terminal: bool,
) -> bool:
    proof_ref = str(row["reconciliation_proof_ref"])
    evidence = (error_ref, proof_ref)
    request = {
        "reservation_id": str(row["reservation_id"]),
        "expected_version": int(row["reconciliation_recovery_version"]),
        "reconciler_actor_id": str(row["reconciler_actor_id"]),
        "attestation_ref": proof_ref,
        "attestation_key_id": str(row["reconciliation_proof_key_id"]),
    }
    try:
        operation_outbox_id, operation_idempotency_key = (
            build_operation_failure_outbox_identity(
                reservation_id=request["reservation_id"],
                recovery_version=request["expected_version"],
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
            )
        )
    except Exception:
        return False
    expected = [
        _ExpectedOutbox(
            outbox_id=operation_outbox_id,
            idempotency_key=operation_idempotency_key,
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=int(row["operation_status_version"]),
            event_type="OPERATION_RECOVERY_RELEASE",
            graph_revision=None,
            attempt_id=attempt_id,
            evidence_refs=(proof_ref,),
        ),
        _failure_outbox(
            kind="attempt",
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=int(row["attempt_status_version"]),
            event_type="TASK_ATTEMPT_FAILED",
            attempt_id=attempt_id,
            evidence_refs=evidence,
        ),
        _failure_outbox(
            kind="node",
            aggregate_type="TASK_NODE",
            aggregate_id=f"{graph_id}@{graph_revision}/{node_id}",
            aggregate_version=int(row["node_status_version"]),
            event_type="TASK_NODE_FAILED",
            attempt_id=attempt_id,
            evidence_refs=evidence,
        ),
        _failure_outbox(
            kind="lease",
            aggregate_type="TASK_LEASE",
            aggregate_id=lease_id,
            aggregate_version=int(row["lease_status_version"]),
            event_type="TASK_LEASE_EXPIRED",
            attempt_id=attempt_id,
            evidence_refs=evidence,
        ),
    ]
    if graph_wide_terminal:
        expected.extend(
            (
                _failure_outbox(
                    kind="graph",
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=graph_id,
                    aggregate_version=int(row["graph_status_version"]),
                    event_type="TASK_GRAPH_FAILED",
                    graph_revision=graph_revision,
                    attempt_id=attempt_id,
                    evidence_refs=evidence,
                ),
                _failure_outbox(
                    kind="budget",
                    aggregate_type="BUDGET_RESERVATION",
                    aggregate_id=str(row["budget_id"]),
                    aggregate_version=int(row["budget_status_version"]),
                    event_type="BUDGET_RESERVATION_RELEASED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence,
                ),
            )
        )
    return all(_outbox_matches(conn, item) for item in expected)


class _ExpectedOutbox:
    __slots__ = (
        "aggregate_id",
        "aggregate_type",
        "aggregate_version",
        "attempt_id",
        "event_type",
        "evidence_refs",
        "graph_revision",
        "idempotency_key",
        "outbox_id",
    )

    def __init__(
        self,
        *,
        outbox_id: str,
        idempotency_key: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        graph_revision: int | None,
        attempt_id: str,
        evidence_refs: tuple[str, ...],
    ) -> None:
        self.outbox_id = outbox_id
        self.idempotency_key = idempotency_key
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        self.aggregate_version = aggregate_version
        self.event_type = event_type
        self.graph_revision = graph_revision
        self.attempt_id = attempt_id
        self.evidence_refs = evidence_refs


def _failure_outbox(
    *,
    kind: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    attempt_id: str,
    evidence_refs: tuple[str, ...],
    graph_revision: int | None = None,
) -> _ExpectedOutbox:
    material = "\n".join(
        (
            kind,
            aggregate_type,
            aggregate_id,
            str(aggregate_version),
            str(graph_revision or ""),
            attempt_id,
            event_type,
            *evidence_refs,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return _ExpectedOutbox(
        outbox_id=f"outbox::operation-failure::{digest}",
        idempotency_key=f"operation-failure:{digest}",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        graph_revision=graph_revision,
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _outbox_matches(
    conn: sqlite3.Connection,
    expected: _ExpectedOutbox,
) -> bool:
    rows = conn.execute(
        """
        SELECT * FROM task_runtime_outbox
        WHERE outbox_id = ?
        """,
        (expected.outbox_id,),
    ).fetchall()
    row = rows[0] if len(rows) == 1 else None
    if row is None:
        return False
    try:
        evidence_refs = tuple(json.loads(str(row["evidence_refs_json"])))
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    exact_revision = (
        row["graph_revision"] is None
        if expected.graph_revision is None
        else int(row["graph_revision"] or 0) == expected.graph_revision
    )
    exact_payload_ref = build_outbox_payload_ref(
        aggregate_type=expected.aggregate_type,
        aggregate_id=expected.aggregate_id,
        aggregate_version=expected.aggregate_version,
        graph_revision=expected.graph_revision,
    )
    duplicate_count = conn.execute(
        """
        SELECT COUNT(*) FROM task_runtime_outbox
        WHERE aggregate_type = ? AND aggregate_id = ?
          AND aggregate_version = ? AND event_type = ?
          AND ((graph_revision IS NULL AND ? IS NULL) OR graph_revision = ?)
        """,
        (
            expected.aggregate_type,
            expected.aggregate_id,
            expected.aggregate_version,
            expected.event_type,
            expected.graph_revision,
            expected.graph_revision,
        ),
    ).fetchone()[0]
    return bool(
        int(duplicate_count) == 1
        and str(row["idempotency_key"]) == expected.idempotency_key
        and str(row["aggregate_type"]) == expected.aggregate_type
        and str(row["aggregate_id"]) == expected.aggregate_id
        and int(row["aggregate_version"]) == expected.aggregate_version
        and str(row["event_type"]) == expected.event_type
        and exact_revision
        and str(row["payload_ref"]) == exact_payload_ref
        and str(row["attempt_id"] or "") == expected.attempt_id
        and evidence_refs == expected.evidence_refs
    )


def _operation_failure_error_ref(
    *,
    reservation_id: str,
    attempt_id: str,
    reason_code: str,
    proof_ref: str,
    proof_digest: str,
) -> str:
    material = "\n".join(
        (reservation_id, attempt_id, reason_code, proof_ref, proof_digest)
    )
    return (
        "taskerror://operation-failure/sha256/"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


__all__ = [
    "finalized_no_effect_failure_allows_workspace_abandonment",
]
