"""Validated durable projections used by terminal reconciliation discovery.

This module owns row-codec validation and narrow classification helpers.  It
does not open connections or commit transactions; callers retain the single
``DurableTaskStore`` unit of work.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.review_policy import ReviewItem
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    is_signed_prebudget_cancellation_classifier_correction,
)
from host_brain.persistence.review_continuation_store import (
    ReviewContinuationStoreError,
    validate_task_review_admission_in_transaction,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _loads_list,
    _loads_object,
    _validated_budget_record,
    _validated_graph_payload,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)

from .task_terminal_reconciliation_common import (
    TERMINAL_RECONCILIATION_CASE_SCHEMA,
    _canonical_digest,
    _terminal_case_id,
    _value_digest,
)


_ACTIVE_REVIEW_STATUSES = frozenset({"PENDING", "APPROVED"})


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _bounded_limit(value: int, *, ceiling: int) -> int:
    if type(value) is not int or value < 1:
        raise TaskStoreError(TASK_STORE_INVALID, "limit must be a positive integer")
    return min(value, ceiling)


def _build_terminal_case(
    observation: Mapping[str, Any],
    *,
    case_kind: str,
    classification: str,
    reason_code: str,
    recommended_action: str,
    selected_budget: Mapping[str, Any] | None,
    selected_node: Mapping[str, Any] | None,
    selected_attempt: Mapping[str, Any] | None,
    failure_bundle: Mapping[str, Any] | None,
) -> dict[str, Any]:
    graph = dict(observation["graph"])
    payload = {
        "schema_version": TERMINAL_RECONCILIATION_CASE_SCHEMA,
        "case_kind": case_kind,
        "classification": classification,
        "reason_code": reason_code,
        "recommended_action": recommended_action,
        "graph": graph,
        "selected_budget": (
            dict(selected_budget) if selected_budget is not None else None
        ),
        "selected_node": dict(selected_node) if selected_node is not None else None,
        "selected_attempt": (
            dict(selected_attempt) if selected_attempt is not None else None
        ),
        "failure_bundle": (
            dict(failure_bundle) if failure_bundle is not None else None
        ),
        "observation": dict(observation),
    }
    snapshot_json, snapshot_sha256 = _canonical_digest(payload)
    case_id = _terminal_case_id(
        graph_id=graph["graph_id"],
        graph_revision=int(graph["revision"]),
        case_kind=case_kind,
        snapshot_sha256=snapshot_sha256,
    )
    return {
        "case_id": case_id,
        "schema_version": TERMINAL_RECONCILIATION_CASE_SCHEMA,
        "case_kind": case_kind,
        "classification": classification,
        "reason_code": reason_code,
        "graph_id": graph["graph_id"],
        "graph_revision": int(graph["revision"]),
        "graph_status": graph["status"],
        "graph_status_version": int(graph["status_version"]),
        "budget_id": selected_budget.get("budget_id") if selected_budget else None,
        "budget_status": selected_budget.get("status") if selected_budget else None,
        "budget_status_version": (
            int(selected_budget["status_version"]) if selected_budget else None
        ),
        "node_id": selected_node.get("node_id") if selected_node else None,
        "node_status": selected_node.get("status") if selected_node else None,
        "node_status_version": (
            int(selected_node["status_version"]) if selected_node else None
        ),
        "attempt_id": selected_attempt.get("attempt_id") if selected_attempt else None,
        "attempt_status": (
            selected_attempt.get("status") if selected_attempt else None
        ),
        "attempt_status_version": (
            int(selected_attempt["status_version"]) if selected_attempt else None
        ),
        "recommended_action": recommended_action,
        "snapshot": payload,
        "snapshot_json": snapshot_json,
        "snapshot_sha256": snapshot_sha256,
        "created_ts_ms": int(graph["updated_ts_ms"]),
    }


def _structural_terminal_observation(
    conn: sqlite3.Connection,
    graph: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze non-secret row identities when a canonical codec fails closed."""

    graph_id = str(graph["graph_id"])
    revision = int(graph["revision"])
    budget_rows = conn.execute(
        """
        SELECT budget_id, scope, graph_id, graph_revision, node_id,
               attempt_id, status,
               status_version, limits_json, reserved_json, consumed_json,
               attempted_consumed_json, exceeded_dimensions_json,
               updated_ts_ms
        FROM task_runtime_budget_reservation
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY CASE WHEN node_id IS NULL AND attempt_id IS NULL
                      THEN 0 ELSE 1 END,
                 node_id, attempt_id, budget_id
        """,
        (graph_id, revision),
    ).fetchall()
    node_rows = conn.execute(
        """
        SELECT node_id, status, status_version, node_json,
               retry_not_before_ms, updated_ts_ms
        FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
        """,
        (graph_id, revision),
    ).fetchall()
    return {
        "graph": {
            "graph_id": graph_id,
            "revision": revision,
            "status": str(graph["status"]),
            "status_version": int(graph["status_version"]),
            "graph_json_sha256": _value_digest(str(graph["graph_json"])),
            "source_kind": str(graph["source_kind"]),
            "source_ref": str(graph["source_ref"] or "").strip() or None,
            "created_ts_ms": int(graph["created_ts_ms"]),
            "updated_ts_ms": int(graph["updated_ts_ms"]),
        },
        "budgets": [
            {
                "budget_id": str(row["budget_id"]),
                "scope_sha256": _value_digest(str(row["scope"])),
                "graph_id": str(row["graph_id"] or "").strip() or None,
                "graph_revision": row["graph_revision"],
                "node_id": str(row["node_id"] or "").strip() or None,
                "attempt_id": str(row["attempt_id"] or "").strip() or None,
                "status": str(row["status"]),
                "status_version": int(row["status_version"]),
                "row_payload_sha256": _value_digest(
                    {
                        "limits_json": str(row["limits_json"]),
                        "reserved_json": str(row["reserved_json"]),
                        "consumed_json": str(row["consumed_json"]),
                        "attempted_consumed_json": str(
                            row["attempted_consumed_json"]
                        ),
                        "exceeded_dimensions_json": str(
                            row["exceeded_dimensions_json"]
                        ),
                    }
                ),
                "updated_ts_ms": int(row["updated_ts_ms"]),
            }
            for row in budget_rows
        ],
        "nodes": [
            {
                "node_id": str(row["node_id"]),
                "status": str(row["status"]),
                "status_version": int(row["status_version"]),
                "node_json_sha256": _value_digest(str(row["node_json"])),
                "retry_not_before_ms": row["retry_not_before_ms"],
                "updated_ts_ms": int(row["updated_ts_ms"]),
            }
            for row in node_rows
        ],
        "attempts": [],
        "leases": [],
        "operations": [],
        "reviews": [],
        "effects": [],
        "parallel_decisions": [],
        "success_decisions": [],
        "cancellations": [],
        "structural_error": "TERMINAL_DURABLE_PROJECTION_INVALID",
    }


def _graph_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    graph_json = str(row["graph_json"])
    graph_payload = _validated_graph_payload(row)
    if _dumps(graph_payload) != graph_json:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "TaskGraph SQL/JSON projection drifted during terminal discovery",
        )
    return {
        "graph_id": str(row["graph_id"]),
        "revision": int(row["revision"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
        "graph_json_sha256": _value_digest(graph_payload),
        "source_kind": str(row["source_kind"]),
        "source_ref": str(row["source_ref"] or "").strip() or None,
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _budget_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validated_budget_record(row)
    return {
        "budget_id": str(validated["budget_id"]),
        "scope": str(validated["scope"]),
        "graph_id": str(validated.get("graph_id") or "").strip() or None,
        "graph_revision": (
            int(validated["graph_revision"])
            if validated.get("graph_revision") is not None
            else None
        ),
        "node_id": str(validated.get("node_id") or "").strip() or None,
        "attempt_id": str(validated.get("attempt_id") or "").strip() or None,
        "status": str(validated["status"]),
        "status_version": int(validated["status_version"]),
        "limits_sha256": _value_digest(validated["limits"]),
        "reserved_sha256": _value_digest(validated["reserved"]),
        "consumed_sha256": _value_digest(validated["consumed"]),
        "attempted_consumed_sha256": _value_digest(
            validated["attempted_consumed"]
        ),
        "exceeded_dimensions": list(validated["exceeded_dimensions"]),
        "exceeded_dimensions_sha256": _value_digest(
            validated["exceeded_dimensions"]
        ),
        "updated_ts_ms": int(validated["updated_ts_ms"]),
    }


def _node_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = _loads_object(
        str(row["node_json"]),
        field_name="terminal_reconciliation.node_json",
    )
    if (
        str(payload.get("status") or "") != str(row["status"])
        or int(payload.get("status_version", -1)) != int(row["status_version"])
        or _dumps(payload) != str(row["node_json"])
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "TaskNode SQL/JSON projection drifted during terminal discovery",
        )
    return {
        "node_id": str(row["node_id"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
        "node_json_sha256": _value_digest(payload),
        "retry_not_before_ms": row["retry_not_before_ms"],
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _attempt_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": str(row["attempt_id"]),
        "node_id": str(row["node_id"]),
        "attempt_no": int(row["attempt_no"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
        "owner_id": str(row["owner_id"] or "").strip() or None,
        "error_ref": str(row["error_ref"] or "").strip() or None,
        "completed_ts_ms": row["completed_ts_ms"],
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _lease_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": str(row["lease_id"]),
        "attempt_id": str(row["attempt_id"]),
        "owner_id": str(row["owner_id"]),
        "fencing_token": int(row["fencing_token"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
        "released_ts_ms": row["released_ts_ms"],
        "expires_ts_ms": int(row["expires_ts_ms"]),
    }


def _operation_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reservation_id": str(row["reservation_id"]),
        "attempt_id": str(row["attempt_id"] or "").strip() or None,
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
    }


def _terminal_execution_lineage_valid(
    attempts: list[Mapping[str, Any]],
    leases: list[Mapping[str, Any]],
) -> bool:
    """Validate terminal Attempt/Lease ownership without inventing recovery."""

    leases_by_attempt: dict[str, list[Mapping[str, Any]]] = {}
    for lease in leases:
        leases_by_attempt.setdefault(str(lease["attempt_id"]), []).append(lease)
        if (
            str(lease["status"]) == "ACTIVE"
            or lease.get("released_ts_ms") is None
            or int(lease.get("fencing_token") or 0) < 1
        ):
            return False
    for attempt in attempts:
        status = str(attempt["status"])
        if status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return False
        completed_ts = attempt.get("completed_ts_ms")
        if completed_ts is None or int(completed_ts) != int(attempt["updated_ts_ms"]):
            return False
        if status == "FAILED" and not str(attempt.get("error_ref") or "").strip():
            return False
        attempt_id = str(attempt["attempt_id"])
        bound = leases_by_attempt.get(attempt_id, [])
        if not bound:
            if status != "CANCELLED" or attempt.get("owner_id") is not None:
                return False
            continue
        latest = max(
            bound,
            key=lambda item: (int(item["fencing_token"]), str(item["lease_id"])),
        )
        owner = str(attempt.get("owner_id") or "").strip()
        if not owner or owner != str(latest["owner_id"]):
            return False
    return True


def _review_projection(
    conn: sqlite3.Connection,
    row: sqlite3.Row | Mapping[str, Any],
    *,
    graph_id: str,
    graph_revision: int,
) -> dict[str, Any]:
    """Validate one ReviewItem and its exact TaskNode subject binding."""

    payload = _loads_object(
        str(row["review_json"]),
        field_name="terminal_reconciliation.review_json",
    )
    if _dumps(payload) != str(row["review_json"]):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "ReviewItem JSON is not canonical during terminal discovery",
        )
    try:
        item = ReviewItem.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "ReviewItem violates its canonical contract during terminal discovery",
        ) from exc
    expected = {
        "schema_version": str(row["schema_version"]),
        "review_id": str(row["review_id"]),
        "review_type": str(row["review_type"]),
        "subject_ref": str(row["subject_ref"]),
        "subject_version": int(row["subject_version"]),
        "status": str(row["status"]),
        "checkpoint_id": str(row["checkpoint_id"] or "").strip() or None,
        "decision_actor": str(row["decision_actor"] or "").strip() or None,
        "token_ref": str(row["token_ref"] or "").strip() or None,
        "resume_phase": str(row["resume_phase"]),
        "resume_count": int(row["resume_count"]),
        "resume_result_ref": str(row["resume_result_ref"] or "").strip() or None,
        "diff_ref": str(row["diff_ref"] or "").strip() or None,
        "expires_at": int(row["expires_at"]),
    }
    actual_payload = item.to_dict()
    actual = {field: actual_payload.get(field) for field in expected}
    side_effects = _loads_list(
        str(row["side_effects_json"]),
        field_name="terminal_reconciliation.review.side_effects",
    )
    evidence_refs = _loads_list(
        str(row["evidence_refs_json"]),
        field_name="terminal_reconciliation.review.evidence_refs",
    )
    if (
        actual != expected
        or list(item.side_effects) != side_effects
        or list(item.evidence_refs) != evidence_refs
        or str(row["task_graph_id"] or "") != graph_id
        or str(row["task_node_id"] or "") == ""
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "ReviewItem SQL/JSON or outer lineage drifted during terminal discovery",
        )
    try:
        binding = validate_task_review_admission_in_transaction(
            conn,
            review=item,
            task_graph_id=row["task_graph_id"],
            task_node_id=row["task_node_id"],
            require_schema=True,
        )
    except ReviewContinuationStoreError as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "ReviewItem TaskNode checkpoint binding drifted during terminal discovery",
        ) from exc
    if binding is None or int(binding.graph_revision) != int(graph_revision):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "ReviewItem graph revision drifted during terminal discovery",
        )
    return {
        **actual_payload,
        "status_version": int(row["status_version"]),
        "task_graph_id": graph_id,
        "task_node_id": str(row["task_node_id"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _selected_failure_lineage(
    failure_nodes: list[Mapping[str, Any]],
    attempts: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if len(failure_nodes) != 1:
        return None, None
    node = failure_nodes[0]
    candidates = [row for row in attempts if row["node_id"] == node["node_id"]]
    if not candidates:
        return node, None
    latest = max(
        candidates,
        key=lambda row: (int(row["attempt_no"]), str(row["attempt_id"])),
    )
    if latest["status"] not in {"FAILED", "CANCELLED", "SUCCEEDED"}:
        return None, None
    return node, latest


def _has_exact_review_checkpoint(
    nodes: list[Mapping[str, Any]],
    reviews: list[Mapping[str, Any]],
) -> bool:
    waiting = [
        row
        for row in nodes
        if row["status"] in {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
    ]
    if len(waiting) != 1:
        return False
    node = waiting[0]
    expected_phase = (
        "PRE_EXECUTION"
        if node["status"] == "WAITING_APPROVAL"
        else "POST_EXECUTION_ACCEPTANCE"
    )
    expected_review_type = (
        "PRE_EXECUTION_APPROVAL"
        if node["status"] == "WAITING_APPROVAL"
        else "POST_EXECUTION_ACCEPTANCE"
    )
    matches = [
        row
        for row in reviews
        if str(row.get("task_node_id") or "") == node["node_id"]
        and str(row.get("review_type") or "") == expected_review_type
        and str(row.get("resume_phase") or "") == expected_phase
        and int(row.get("subject_version", -1)) == int(node["status_version"])
        and bool(str(row.get("checkpoint_id") or "").strip())
        and str(row.get("status") or "") in _ACTIVE_REVIEW_STATUSES
    ]
    return len(matches) == 1


def _one_outbox(
    conn: sqlite3.Connection,
    *,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    graph_revision: int | None,
) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT * FROM task_runtime_outbox
        WHERE aggregate_type = ? AND aggregate_id = ?
          AND aggregate_version = ?
          AND ((? IS NULL AND graph_revision IS NULL) OR graph_revision = ?)
        ORDER BY outbox_id
        """,
        (
            aggregate_type,
            aggregate_id,
            int(aggregate_version),
            graph_revision,
            graph_revision,
        ),
    ).fetchall()
    return (
        rows[0]
        if len(rows) == 1 and str(rows[0]["event_type"]) == event_type
        else None
    )


def _only_or_none(values: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return values[0] if len(values) == 1 else None


def _quarantine(
    reason_code: str,
    *,
    selected_budget: Mapping[str, Any] | None,
    selected_node: Mapping[str, Any] | None,
    selected_attempt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "case_kind": "TERMINAL_STRUCTURAL_ANOMALY",
        "classification": "QUARANTINED_STRUCTURAL",
        "reason_code": reason_code,
        "recommended_action": "NONE",
        "selected_budget": selected_budget,
        "selected_node": selected_node,
        "selected_attempt": selected_attempt,
        "failure_bundle": None,
    }


def _healthy_closure_if_required(
    conn: sqlite3.Connection,
    observation: Mapping[str, Any],
    *,
    classified: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Return a signed no-op closure only after a full healthy classification."""

    if classified is not None:
        return classified
    graph = observation["graph"]
    prior_rows = conn.execute(
        """
        SELECT *
        FROM task_runtime_terminal_reconciliation_case AS item
        WHERE item.graph_id = ? AND item.graph_revision = ?
          AND NOT EXISTS (
              SELECT 1
              FROM task_runtime_terminal_reconciliation_proof AS proof
              WHERE proof.case_id = item.case_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM task_runtime_terminal_reconciliation_supersession AS supersession
              WHERE supersession.prior_case_id = item.case_id
          )
        ORDER BY item.created_ts_ms, item.case_id
        """,
        (str(graph["graph_id"]), int(graph["revision"])),
    ).fetchall()
    if not prior_rows:
        return None
    graph_budgets = [
        row
        for row in observation["budgets"]
        if row.get("node_id") is None and row.get("attempt_id") is None
    ]
    if not graph_budgets and all(
        is_signed_prebudget_cancellation_classifier_correction(dict(row))
        for row in prior_rows
    ):
        # The historical case receives a signed no-op proof. Creating a
        # synthetic Budget or replacement case would change task truth only to
        # satisfy the superseded classifier assumption.
        return None
    if len(graph_budgets) != 1:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal healthy closure lacks one graph-scope Budget",
        )
    return {
        "case_kind": "TERMINAL_HEALTHY_CLOSURE",
        "classification": "SIGNED_REPAIR_REQUIRED",
        "reason_code": "CURRENT_TERMINAL_STATE_HEALTHY_REQUIRES_SIGNED_CLOSURE",
        "recommended_action": "NONE",
        "selected_budget": graph_budgets[0],
        "selected_node": None,
        "selected_attempt": None,
        "failure_bundle": None,
    }


__all__ = [
    "_ACTIVE_REVIEW_STATUSES",
    "_attempt_projection",
    "_bounded_limit",
    "_build_terminal_case",
    "_budget_projection",
    "_graph_projection",
    "_has_exact_review_checkpoint",
    "_healthy_closure_if_required",
    "_lease_projection",
    "_node_projection",
    "_one_outbox",
    "_only_or_none",
    "_operation_projection",
    "_quarantine",
    "_review_projection",
    "_selected_failure_lineage",
    "_structural_terminal_observation",
    "_table_columns",
    "_terminal_execution_lineage_valid",
]
