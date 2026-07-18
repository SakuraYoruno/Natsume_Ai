"""Schema metadata and pure row codecs for Review continuation persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
)
from host_brain.persistence.review_continuation_binding import (
    REVIEW_CONTINUATION_CLAIM_LOST,
    REVIEW_CONTINUATION_CONFLICT,
    REVIEW_CONTINUATION_INVALID,
    ReviewContinuationStoreError,
    TaskReviewSubjectBinding,
)


_READY_INDEX = "idx_task_runtime_review_continuation_ready"
_CHECKPOINT_INDEX = "uq_task_runtime_review_continuation_checkpoint"
_TERMINAL_DELIVERY_STATUSES = frozenset(
    {
        ReviewStatus.CONSUMED.value,
        ReviewStatus.REJECTED.value,
        ReviewStatus.EXPIRED.value,
    }
)
_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "review_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
        "review_status",
        "review_status_version",
        "claim_owner",
        "claim_fencing_token",
        "claim_expires_ts_ms",
        "claim_token_sha256",
        "status_version",
    }
)
_REQUIRED_COLUMNS = frozenset(
    {
        "review_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
        "claim_owner",
        "claim_token",
        "claim_expires_ts_ms",
        "claim_fencing_token",
        "attempt_count",
        "available_ts_ms",
        "last_error",
        "status_version",
        "created_ts_ms",
        "updated_ts_ms",
    }
)
_REQUIRED_COLUMN_METADATA = {
    "review_id": ("TEXT", 1, None, 1),
    "graph_id": ("TEXT", 1, None, 0),
    "graph_revision": ("INTEGER", 1, None, 0),
    "node_id": ("TEXT", 1, None, 0),
    "subject_version": ("INTEGER", 1, None, 0),
    "resume_phase": ("TEXT", 1, None, 0),
    "claim_owner": ("TEXT", 0, None, 0),
    "claim_token": ("TEXT", 0, None, 0),
    "claim_expires_ts_ms": ("INTEGER", 0, None, 0),
    "claim_fencing_token": ("INTEGER", 1, "0", 0),
    "attempt_count": ("INTEGER", 1, "0", 0),
    "available_ts_ms": ("INTEGER", 1, None, 0),
    "last_error": ("TEXT", 0, None, 0),
    "status_version": ("INTEGER", 1, "0", 0),
    "created_ts_ms": ("INTEGER", 1, None, 0),
    "updated_ts_ms": ("INTEGER", 1, None, 0),
}
_REQUIRED_INDEX_COLUMNS = {
    _CHECKPOINT_INDEX: (
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
    ),
    _READY_INDEX: (
        "available_ts_ms",
        "claim_expires_ts_ms",
        "graph_id",
        "graph_revision",
        "node_id",
        "review_id",
    ),
}
_REQUIRED_FOREIGN_KEYS = (
    (0, 0, "task_runtime_node", "graph_id", "graph_id", "NO ACTION", "RESTRICT", "NONE"),
    (0, 1, "task_runtime_node", "graph_revision", "graph_revision", "NO ACTION", "RESTRICT", "NONE"),
    (0, 2, "task_runtime_node", "node_id", "node_id", "NO ACTION", "RESTRICT", "NONE"),
    (1, 0, "agent_runtime_review", "review_id", "review_id", "NO ACTION", "RESTRICT", "NONE"),
)
_REQUIRED_SQL_FRAGMENTS = (
    "check(length(trim(review_id))>0)",
    "check(length(trim(graph_id))>0)",
    "check(typeof(graph_revision)='integer'andgraph_revision>=1)",
    "check(length(trim(node_id))>0)",
    "check(typeof(subject_version)='integer'andsubject_version>=0)",
    "check(resume_phasein('pre_execution','post_execution_acceptance'))",
    "check(typeof(claim_fencing_token)='integer'andclaim_fencing_token>=0)",
    "check(typeof(attempt_count)='integer'andattempt_count>=0andattempt_count=claim_fencing_token)",
    "check(typeof(available_ts_ms)='integer'andavailable_ts_ms>=0)",
    "check(last_errorisnullor(typeof(last_error)='text'andlength(trim(last_error))>0andlength(last_error)<=2000))",
    "check(typeof(status_version)='integer'andstatus_version>=0)",
    "check(typeof(created_ts_ms)='integer'andcreated_ts_ms>=0)",
    "check(typeof(updated_ts_ms)='integer'andupdated_ts_ms>=created_ts_ms)",
    "claim_ownerisnullandclaim_tokenisnullandclaim_expires_ts_msisnull",
    "typeof(claim_owner)='text'andlength(trim(claim_owner))>0andlength(claim_owner)<=255",
    "typeof(claim_token)='text'andlength(trim(claim_token))>0andlength(claim_token)<=512",
    "typeof(claim_expires_ts_ms)='integer'andclaim_expires_ts_ms>=0",
    "claim_fencing_token>0",
)


def _review_item_from_row(row: sqlite3.Row) -> ReviewItem:
    try:
        payload = json.loads(str(row["review_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "terminal ReviewItem JSON is invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "terminal ReviewItem JSON must be an object",
        )
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
    try:
        return ReviewItem.from_mapping(payload)
    except ValueError as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "terminal ReviewItem contract is invalid",
        ) from exc


def _assert_row_binding(
    row: Mapping[str, Any],
    *,
    review: ReviewItem,
    binding: TaskReviewSubjectBinding,
) -> None:
    expected = (
        review.review_id,
        binding.graph_id,
        binding.graph_revision,
        binding.node_id,
        int(review.subject_version),
        review.resume_phase.value,
    )
    actual = (
        str(row["review_id"]),
        str(row["graph_id"]),
        int(row["graph_revision"]),
        str(row["node_id"]),
        int(row["subject_version"]),
        str(row["resume_phase"]),
    )
    if actual != expected:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation binding drifted from ReviewItem",
        )


def _assert_candidate_binding(
    candidate: Mapping[str, Any],
    durable: Mapping[str, Any],
) -> None:
    fields = (
        "review_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
        "claim_owner",
        "claim_fencing_token",
        "review_status_version",
    )
    for field in fields:
        if field not in candidate:
            continue
        expected = durable.get(field)
        actual = candidate.get(field)
        if field in {
            "graph_revision",
            "subject_version",
            "claim_fencing_token",
            "review_status_version",
        }:
            try:
                matches = not isinstance(actual, bool) and int(actual) == int(expected)
            except (TypeError, ValueError):
                matches = False
        else:
            matches = str(actual or "").strip() == str(expected or "").strip()
        if not matches:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CLAIM_LOST,
                f"review continuation authority field drifted: {field}",
            )


def _authority_projection(
    durable: Mapping[str, Any],
    *,
    claim_token: str,
) -> dict[str, Any]:
    review = durable.get("review")
    review_status = (
        str(review.get("status") or "").strip()
        if isinstance(review, Mapping)
        else ""
    )
    return {
        "schema_version": "Natsume.task.review_continuation_authority.v1",
        "review_id": str(durable["review_id"]),
        "graph_id": str(durable["graph_id"]),
        "graph_revision": int(durable["graph_revision"]),
        "node_id": str(durable["node_id"]),
        "subject_version": int(durable["subject_version"]),
        "resume_phase": str(durable["resume_phase"]),
        "review_status": review_status,
        "review_status_version": int(durable["review_status_version"]),
        "claim_owner": str(durable["claim_owner"]),
        "claim_fencing_token": int(durable["claim_fencing_token"]),
        "claim_expires_ts_ms": int(durable["claim_expires_ts_ms"]),
        "claim_token_sha256": _claim_token_digest(claim_token),
        "status_version": int(durable["status_version"]),
    }


def _claim_token_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _waiting_status(resume_phase: str) -> str:
    if resume_phase == ReviewResumePhase.PRE_EXECUTION.value:
        return "WAITING_APPROVAL"
    if resume_phase == ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value:
        return "WAITING_ACCEPTANCE_REVIEW"
    raise ReviewContinuationStoreError(
        REVIEW_CONTINUATION_INVALID,
        "review continuation resume_phase is invalid",
    )


def _row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "review_id": str(row["review_id"]),
        "graph_id": str(row["graph_id"]),
        "graph_revision": int(row["graph_revision"]),
        "node_id": str(row["node_id"]),
        "subject_version": int(row["subject_version"]),
        "resume_phase": str(row["resume_phase"]),
        "claim_owner": row["claim_owner"],
        "claim_token": row["claim_token"],
        "claim_expires_ts_ms": (
            int(row["claim_expires_ts_ms"])
            if row["claim_expires_ts_ms"] is not None
            else None
        ),
        "claim_fencing_token": int(row["claim_fencing_token"]),
        "attempt_count": int(row["attempt_count"]),
        "available_ts_ms": int(row["available_ts_ms"]),
        "last_error": row["last_error"],
        "status_version": int(row["status_version"]),
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _required_text(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"{field_name} is required",
        )
    return normalized


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"{field_name} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    return normalized


def _positive_int(value: Any, *, field_name: str) -> int:
    normalized = _non_negative_int(value, field_name=field_name)
    if normalized < 1:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"{field_name} must be a positive integer",
        )
    return normalized


__all__ = [
    "_AUTHORITY_FIELDS",
    "_CHECKPOINT_INDEX",
    "_READY_INDEX",
    "_REQUIRED_COLUMNS",
    "_REQUIRED_COLUMN_METADATA",
    "_REQUIRED_FOREIGN_KEYS",
    "_REQUIRED_INDEX_COLUMNS",
    "_REQUIRED_SQL_FRAGMENTS",
    "_TERMINAL_DELIVERY_STATUSES",
    "_assert_candidate_binding",
    "_assert_row_binding",
    "_authority_projection",
    "_claim_token_digest",
    "_non_negative_int",
    "_positive_int",
    "_review_item_from_row",
    "_required_text",
    "_row_dict",
    "_waiting_status",
]
