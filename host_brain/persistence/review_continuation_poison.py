"""Bounded poison-row coordination for task-bound Review continuations.

This helper owns no connection and no Review or Task business state.  Its
updates are limited to the existing migration-0096 delivery coordination row.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping

from host_brain.persistence.review_continuation_binding import (
    REVIEW_CONTINUATION_CLAIM_LOST,
    REVIEW_CONTINUATION_INVALID,
    REVIEW_CONTINUATION_TABLE,
    ReviewContinuationStoreError,
)


_POISON_ERROR_PREFIX = "REVIEW_CONTINUATION_POISON_"
_POISON_ERROR_PATTERN = re.compile(
    r"^REVIEW_CONTINUATION_POISON_[A-Z][A-Z0-9_]{0,159}$"
)
_POISON_BACKOFF_BASE_MS = 1_000
_POISON_BACKOFF_MAX_MS = 300_000
_POISON_HEALTH_SAMPLE_LIMIT = 128


class ReviewContinuationCandidateBusy(ReviewContinuationStoreError):
    """A valid continuation that cannot be claimed during this scan."""


def defer_poison_candidate_in_transaction(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    now: int,
    reason_code: Any,
) -> None:
    """Durably move one corrupt coordination row behind the current ready set."""

    error = _poison_error_code(reason_code)
    exponent = min(8, max(0, int(row["status_version"])))
    delay_ms = min(
        _POISON_BACKOFF_MAX_MS,
        _POISON_BACKOFF_BASE_MS * (2**exponent),
    )
    cursor = conn.execute(
        f"""
        UPDATE {REVIEW_CONTINUATION_TABLE}
        SET claim_owner = NULL, claim_token = NULL,
            claim_expires_ts_ms = NULL,
            available_ts_ms = ?, last_error = ?,
            status_version = status_version + 1,
            updated_ts_ms = ?
        WHERE review_id = ? AND status_version = ?
          AND (claim_owner IS NULL OR claim_expires_ts_ms <= ?)
        """,
        (
            now + delay_ms,
            error,
            now,
            str(row["review_id"]),
            int(row["status_version"]),
            now,
        ),
    )
    if cursor.rowcount != 1:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "poison review continuation changed before durable defer",
        )


def read_review_continuation_failure_snapshot(
    conn: sqlite3.Connection,
) -> Mapping[str, Any]:
    """Build a bounded, redacted health projection from coordination rows."""

    count_row = conn.execute(
        f"""
        SELECT COUNT(*) AS poisoned_count,
               MAX(updated_ts_ms) AS last_failure_ts_ms
        FROM {REVIEW_CONTINUATION_TABLE}
        WHERE last_error LIKE ?
        """,
        (f"{_POISON_ERROR_PREFIX}%",),
    ).fetchone()
    poisoned_count = int(count_row["poisoned_count"] or 0)
    last_failure_ts_ms = (
        None
        if count_row["last_failure_ts_ms"] is None
        else int(count_row["last_failure_ts_ms"])
    )
    sampled = conn.execute(
        f"""
        SELECT review_id, last_error
        FROM {REVIEW_CONTINUATION_TABLE}
        WHERE last_error LIKE ?
        ORDER BY updated_ts_ms, review_id
        LIMIT ?
        """,
        (f"{_POISON_ERROR_PREFIX}%", _POISON_HEALTH_SAMPLE_LIMIT),
    ).fetchall()
    fingerprint_payload = {
        "poisoned_count": poisoned_count,
        "rows": [
            {
                "review_id": str(row["review_id"]),
                "last_error": str(row["last_error"]),
            }
            for row in sampled
        ],
    }
    incident_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    return {
        "schema": "natsume.review_continuation.failure_snapshot.v1",
        "status": "FAILED" if poisoned_count else "RECOVERED",
        "poisoned_count": poisoned_count,
        "sampled_count": len(sampled),
        "truncated": poisoned_count > len(sampled),
        "last_failure_ts_ms": last_failure_ts_ms,
        "incident_digest": incident_digest,
    }


def _poison_error_code(value: Any) -> str:
    normalized = str(value or REVIEW_CONTINUATION_INVALID).strip().upper()
    normalized = re.sub(r"[^A-Z0-9_]+", "_", normalized).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = REVIEW_CONTINUATION_INVALID
    error = f"{_POISON_ERROR_PREFIX}{normalized}"[:192]
    if _POISON_ERROR_PATTERN.fullmatch(error) is None:
        return f"{_POISON_ERROR_PREFIX}REVIEW_CONTINUATION_INVALID"
    return error


__all__ = [
    "ReviewContinuationCandidateBusy",
    "defer_poison_candidate_in_transaction",
    "read_review_continuation_failure_snapshot",
]
