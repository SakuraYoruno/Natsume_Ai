"""Single SQLite writer for a new durable pending ReviewItem."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewStatus,
)
from host_brain.persistence.review_continuation_store import (
    ReviewContinuationStoreError,
    continuation_table_exists,
    parse_task_review_subject,
    validate_task_review_admission_in_transaction,
)


REVIEW_PENDING_UOW_INVALID = "REVIEW_STORE_INVALID"
REVIEW_PENDING_UOW_CONFLICT = "REVIEW_STATUS_CONFLICT"
REVIEW_PENDING_UOW_EXPIRED = "REVIEW_TOKEN_EXPIRED"
REVIEW_PENDING_UOW_CANCELLING = "REVIEW_GRAPH_CANCELLATION_CONFLICT"
_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


class ReviewPendingUowError(RuntimeError):
    """Repository-neutral failure mapped by ReviewStore and TaskStore."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or REVIEW_PENDING_UOW_INVALID)
        self.detail = str(detail or "pending review write failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def review_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(str(row["review_json"]))
        if not isinstance(payload, dict):
            raise ValueError("review_json must be an object")
        payload.update(
            {
                "status": str(row["status"]),
                "decision_actor": row["decision_actor"],
                "token_ref": row["token_ref"],
                "resume_count": int(row["resume_count"]),
                "resume_result_ref": row["resume_result_ref"],
                "checkpoint_id": row["checkpoint_id"],
                "diff_ref": row["diff_ref"],
            }
        )
        item = ReviewItem.from_mapping(payload)
        side_effects = json.loads(str(row["side_effects_json"]))
        evidence_refs = json.loads(str(row["evidence_refs_json"]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_INVALID,
            "durable ReviewItem payload is invalid",
        ) from exc
    if not isinstance(side_effects, list) or not isinstance(evidence_refs, list):
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_INVALID,
            "durable ReviewItem evidence columns are invalid",
        )
    expected_json = canonical_json(item.to_dict())
    if (
        str(row["schema_version"]) != item.schema_version
        or str(row["review_id"]) != item.review_id
        or str(row["review_type"]) != item.review_type.value
        or str(row["subject_ref"]) != item.subject_ref
        or int(row["subject_version"]) != item.subject_version
        or str(row["requester"]) != item.requester
        or str(row["reason"]) != item.reason
        or int(row["risk_level"]) != item.risk_level
        or tuple(side_effects) != tuple(item.side_effects)
        or tuple(evidence_refs) != tuple(item.evidence_refs)
        or str(row["status"]) != item.status.value
        or int(row["expires_at"]) != item.expires_at
        or str(row["resume_phase"]) != item.resume_phase.value
        or str(row["review_json"]) != expected_json
    ):
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_INVALID,
            "durable ReviewItem columns drifted from review_json",
        )
    return {
        "review": item.to_dict(),
        "status_version": int(row["status_version"]),
        "work_order_id": row["work_order_id"],
        "task_graph_id": row["task_graph_id"],
        "task_node_id": row["task_node_id"],
        "side_effects": side_effects,
        "evidence_refs": evidence_refs,
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def require_review_record(
    conn: sqlite3.Connection,
    review_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM agent_runtime_review WHERE review_id = ?",
        (str(review_id or "").strip(),),
    ).fetchone()
    if row is None:
        raise ReviewPendingUowError(
            "REVIEW_STORE_NOT_FOUND",
            f"ReviewItem not found: {review_id}",
        )
    return review_record_from_row(row)


def insert_pending_review_in_transaction(
    conn: sqlite3.Connection,
    *,
    review: ReviewItem,
    work_order_id: str | None,
    task_graph_id: str | None,
    task_node_id: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    """Insert exactly one PENDING ReviewItem using the caller's transaction."""

    try:
        review.validate()
    except ValueError as exc:
        raise ReviewPendingUowError(
            str(getattr(exc, "reason_code", "") or REVIEW_PENDING_UOW_INVALID),
            "ReviewItem violates its contract",
        ) from exc
    if review.status is not ReviewStatus.PENDING:
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_CONFLICT,
            "new ReviewItem must be PENDING",
        )
    now = int(ts_ms)
    if review.expires_at <= now:
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_EXPIRED,
            "ReviewItem expires_at is already expired",
        )
    checkpoint_id = _durable_ref(
        review.checkpoint_id,
        field_name="checkpoint_id",
    )
    diff_ref = _durable_ref(
        review.diff_ref,
        field_name="diff_ref",
        required=False,
    )
    binding = parse_task_review_subject(review.subject_ref)
    if binding is not None and _graph_is_cancelling(
        conn,
        graph_id=binding.graph_id,
        graph_revision=binding.graph_revision,
    ):
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_CANCELLING,
            "TaskGraph cancellation permanently fences ReviewItem authority "
            f"for {binding.graph_id}@{binding.graph_revision}",
        )
    try:
        validate_task_review_admission_in_transaction(
            conn,
            review=review,
            task_graph_id=task_graph_id,
            task_node_id=task_node_id,
            require_schema=continuation_table_exists(conn),
        )
    except ReviewContinuationStoreError as exc:
        raise ReviewPendingUowError(exc.reason_code, exc.detail) from exc
    try:
        conn.execute(
            """
            INSERT INTO agent_runtime_review(
                review_id, schema_version, review_type, subject_ref,
                subject_version, requester, reason, risk_level,
                side_effects_json, evidence_refs_json, status, status_version,
                expires_at, resume_phase, diff_ref, checkpoint_id,
                decision_actor, token_ref, resume_count, resume_result_ref,
                work_order_id, task_graph_id, task_node_id, review_json,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.review_id,
                review.schema_version,
                review.review_type.value,
                review.subject_ref,
                review.subject_version,
                review.requester,
                review.reason,
                review.risk_level,
                canonical_json(list(review.side_effects)),
                canonical_json(list(review.evidence_refs)),
                review.status.value,
                review.expires_at,
                review.resume_phase.value,
                diff_ref,
                checkpoint_id,
                review.decision_actor,
                review.token_ref,
                review.resume_count,
                review.resume_result_ref,
                str(work_order_id or "").strip() or None,
                str(task_graph_id or "").strip() or None,
                str(task_node_id or "").strip() or None,
                canonical_json(review.to_dict()),
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_CONFLICT,
            "ReviewItem identity already exists",
        ) from exc
    return require_review_record(conn, review.review_id)


def _durable_ref(
    value: Any,
    *,
    field_name: str,
    required: bool = True,
) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ReviewPendingUowError(
                REVIEW_PENDING_UOW_INVALID,
                f"{field_name} is required",
            )
        return None
    if not _DURABLE_REFERENCE_PATTERN.fullmatch(text):
        raise ReviewPendingUowError(
            REVIEW_PENDING_UOW_INVALID,
            f"{field_name} must be a durable reference",
        )
    return text


def _graph_is_cancelling(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
) -> bool:
    table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table'
          AND name = 'task_runtime_graph_cancellation_intent'
        """
    ).fetchone()
    if table is None:
        return False
    row = conn.execute(
        """
        SELECT 1 FROM task_runtime_graph_cancellation_intent
        WHERE graph_id = ? AND graph_revision = ?
        LIMIT 1
        """,
        (graph_id, int(graph_revision)),
    ).fetchone()
    return row is not None


__all__ = [
    "ReviewPendingUowError",
    "insert_pending_review_in_transaction",
    "require_review_record",
    "review_record_from_row",
]
