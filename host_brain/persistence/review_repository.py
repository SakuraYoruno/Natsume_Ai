"""Durable review and one-time resume persistence for Agent runtime v2.

ReviewStore is deliberately small: it persists ReviewItem state, binds one-time
resume tokens to the reviewed subject version and checkpoint, and returns a
checkpoint-only resume decision. It does not execute work, replay side effects,
or implement a full review UI.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote, unquote

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    validate_review_transition,
)
from host_brain.core.ids import now_ms
from host_brain.contracts.agent_runtime.review_token_contract import (
    ReviewTokenService,
    review_resume_token_digest,
)


REVIEW_STORE_SCHEMA_NOT_READY = "REVIEW_STORE_SCHEMA_NOT_READY"
REVIEW_STORE_NOT_FOUND = "REVIEW_STORE_NOT_FOUND"
REVIEW_STORE_INVALID = "REVIEW_STORE_INVALID"
REVIEW_STATUS_CONFLICT = "REVIEW_STATUS_CONFLICT"
REVIEW_TOKEN_INVALID = "REVIEW_TOKEN_INVALID"
REVIEW_TOKEN_EXPIRED = "REVIEW_TOKEN_EXPIRED"
REVIEW_TOKEN_SUBJECT_MISMATCH = "REVIEW_TOKEN_SUBJECT_MISMATCH"
REVIEW_RESUME_ALREADY_CONSUMED = "REVIEW_RESUME_ALREADY_CONSUMED"
REVIEW_CHECKPOINT_REQUIRED = "REVIEW_CHECKPOINT_REQUIRED"
REVIEW_GRAPH_CANCELLATION_CONFLICT = "REVIEW_GRAPH_CANCELLATION_CONFLICT"

REVIEW_STORE_TABLES = frozenset(
    {
        "agent_runtime_review",
        "agent_runtime_review_token",
    }
)
_REQUIRED_COLUMNS = {
    "agent_runtime_review": frozenset(
        {
            "review_id",
            "schema_version",
            "review_type",
            "subject_ref",
            "subject_version",
            "status",
            "status_version",
            "checkpoint_id",
            "token_ref",
            "resume_count",
            "resume_result_ref",
            "review_json",
        }
    ),
    "agent_runtime_review_token": frozenset(
        {
            "token_digest",
            "token_ref_public",
            "review_id",
            "token_type",
            "subject_ref",
            "subject_version",
            "resume_phase",
            "checkpoint_id",
            "expires_ts_ms",
            "state",
            "consumed_ts_ms",
            "consumed_result_ref",
        }
    ),
}
_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_TASK_NODE_SUBJECT_PATTERN = re.compile(
    r"^task-node://(?P<graph_id>[^/?#]+)/"
    r"(?P<graph_revision>[1-9][0-9]*)/"
    r"(?P<node_id>[^/?#]+)(?:\?[^#]*)?$"
)

__all__ = [
    "REVIEW_CHECKPOINT_REQUIRED",
    "REVIEW_GRAPH_CANCELLATION_CONFLICT",
    "REVIEW_RESUME_ALREADY_CONSUMED",
    "REVIEW_STATUS_CONFLICT",
    "REVIEW_STORE_INVALID",
    "REVIEW_STORE_NOT_FOUND",
    "REVIEW_STORE_SCHEMA_NOT_READY",
    "REVIEW_TOKEN_EXPIRED",
    "REVIEW_TOKEN_INVALID",
    "REVIEW_TOKEN_SUBJECT_MISMATCH",
    "ReviewStore",
    "ReviewStoreError",
]


class ReviewStoreError(RuntimeError):
    """Fail-closed durable review error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or REVIEW_STORE_INVALID)
        self.detail = str(detail or "review store operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_object(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise ReviewStoreError(REVIEW_STORE_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ReviewStoreError(REVIEW_STORE_INVALID, f"{field_name} must be a JSON object")
    return parsed


def _loads_list(value: Any, *, field_name: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise ReviewStoreError(REVIEW_STORE_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, list):
        raise ReviewStoreError(REVIEW_STORE_INVALID, f"{field_name} must be a JSON list")
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _durable_ref(value: Any, *, field_name: str, required: bool = True) -> str | None:
    text = _text(value)
    if not text:
        if required:
            raise ReviewStoreError(REVIEW_CHECKPOINT_REQUIRED, f"{field_name} is required")
        return None
    if not _DURABLE_REFERENCE_PATTERN.fullmatch(text):
        raise ReviewStoreError(REVIEW_STORE_INVALID, f"{field_name} must be a durable reference")
    return text


def _task_node_graph_revision(value: Any) -> tuple[str, int] | None:
    match = _TASK_NODE_SUBJECT_PATTERN.fullmatch(_text(value))
    if match is None:
        return None
    graph_id = unquote(match.group("graph_id"))
    node_id = unquote(match.group("node_id"))
    if not graph_id or not node_id:
        return None
    return graph_id, int(match.group("graph_revision"))


class ReviewStore:
    """SQLite-backed durable review store with token-bound one-time resume."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        token_service: ReviewTokenService | None = None,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise ReviewStoreError(
                REVIEW_STORE_INVALID,
                "ReviewStore requires a durable sqlite file path",
            )
        self.db_path = Path(raw_path)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._token_service = token_service or ReviewTokenService(clock=clock)
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _assert_task_node_not_cancelling(
        conn: sqlite3.Connection,
        *,
        subject_ref: str,
    ) -> None:
        """Fence exact TaskNode review authority after Graph cancellation.

        ReviewStore predates migration 0092, so historical databases containing
        only the 0055 review tables remain valid.  When 0092 is present, this
        check runs inside the caller's ``BEGIN IMMEDIATE`` transaction.  It
        therefore serializes against the cancellation writer: a Review write
        that wins first is revoked by 0092, while a cancellation that wins
        first prevents any later create, approve, or resume authority.
        """

        binding = _task_node_graph_revision(subject_ref)
        if binding is None:
            return
        cancellation_table = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_graph_cancellation_intent'
            """
        ).fetchone()
        if cancellation_table is None:
            return
        graph_id, graph_revision = binding
        cancellation = conn.execute(
            """
            SELECT intent_ref
            FROM task_runtime_graph_cancellation_intent
            WHERE graph_id = ? AND graph_revision = ?
            LIMIT 1
            """,
            (graph_id, graph_revision),
        ).fetchone()
        if cancellation is not None:
            raise ReviewStoreError(
                REVIEW_GRAPH_CANCELLATION_CONFLICT,
                "TaskGraph cancellation permanently fences ReviewItem authority "
                f"for {graph_id}@{graph_revision}",
            )

    def assert_schema_ready(self) -> None:
        if not self.db_path.exists():
            raise ReviewStoreError(
                REVIEW_STORE_SCHEMA_NOT_READY,
                f"review runtime database does not exist: {self.db_path}",
            )
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            present = {str(row[0]) for row in rows}
            missing_tables = sorted(REVIEW_STORE_TABLES - present)
            if missing_tables:
                raise ReviewStoreError(
                    REVIEW_STORE_SCHEMA_NOT_READY,
                    f"review migration 0055 is not applied; missing tables: {missing_tables}",
                )
            missing_columns = {}
            for table, required in _REQUIRED_COLUMNS.items():
                columns = {
                    str(column[1])
                    for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                missing = sorted(required - columns)
                if missing:
                    missing_columns[table] = missing
            if missing_columns:
                raise ReviewStoreError(
                    REVIEW_STORE_SCHEMA_NOT_READY,
                    f"review schema is stale; missing columns: {missing_columns}",
                )
        finally:
            conn.close()

    def create_review(
        self,
        review: ReviewItem | Mapping[str, Any],
        *,
        work_order_id: str | None = None,
        task_graph_id: str | None = None,
        task_node_id: str | None = None,
    ) -> dict[str, Any]:
        item = review if isinstance(review, ReviewItem) else ReviewItem.from_mapping(review)
        item.validate()
        if item.status is not ReviewStatus.PENDING:
            raise ReviewStoreError(REVIEW_STATUS_CONFLICT, "new ReviewItem must be PENDING")
        checkpoint_id = _durable_ref(item.checkpoint_id, field_name="checkpoint_id")
        diff_ref = _durable_ref(item.diff_ref, field_name="diff_ref", required=False)
        ts_ms = self._now()
        if int(item.expires_at) <= ts_ms:
            raise ReviewStoreError(REVIEW_TOKEN_EXPIRED, "ReviewItem expires_at is already expired")
        with self._transaction() as conn:
            self._assert_task_node_not_cancelling(
                conn,
                subject_ref=item.subject_ref,
            )
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
                    item.review_id,
                    item.schema_version,
                    item.review_type.value,
                    item.subject_ref,
                    item.subject_version,
                    item.requester,
                    item.reason,
                    item.risk_level,
                    _dumps(list(item.side_effects)),
                    _dumps(list(item.evidence_refs)),
                    item.status.value,
                    item.expires_at,
                    item.resume_phase.value,
                    diff_ref,
                    checkpoint_id,
                    item.decision_actor,
                    item.token_ref,
                    item.resume_count,
                    item.resume_result_ref,
                    _text(work_order_id) or None,
                    _text(task_graph_id) or None,
                    _text(task_node_id) or None,
                    _dumps(item.to_dict()),
                    ts_ms,
                    ts_ms,
                ),
            )
            return self._record_from_row(self._require_review(conn, item.review_id))

    def get_review(self, review_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            return self._record_from_row(self._require_review(conn, review_id))
        finally:
            conn.close()

    def list_open_reviews(self, *, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM agent_runtime_review
                WHERE status IN ('PENDING', 'APPROVED')
                ORDER BY created_ts_ms, review_id
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            return [self._record_from_row(row) for row in rows]
        finally:
            conn.close()

    def list_reviews(
        self,
        *,
        task_graph_id: str | None = None,
        graph_revision: int | None = None,
        work_order_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return validated reviews without mutating expiry or token state."""

        clauses: list[str] = []
        params: list[Any] = []
        graph_ref = _text(task_graph_id)
        if graph_ref:
            clauses.append("task_graph_id = ?")
            params.append(graph_ref)
        if graph_revision is not None:
            if not graph_ref or isinstance(graph_revision, bool):
                raise ReviewStoreError(
                    REVIEW_STORE_INVALID,
                    "graph_revision requires one task_graph_id and a positive integer",
                )
            try:
                normalized_revision = int(graph_revision)
            except (TypeError, ValueError) as exc:
                raise ReviewStoreError(
                    REVIEW_STORE_INVALID,
                    "graph_revision must be a positive integer",
                ) from exc
            if normalized_revision < 1:
                raise ReviewStoreError(
                    REVIEW_STORE_INVALID,
                    "graph_revision must be a positive integer",
                )
            subject_prefix = (
                f"task-node://{quote(graph_ref, safe='')}/{normalized_revision}/"
            )
            clauses.append("substr(subject_ref, 1, length(?)) = ?")
            params.extend((subject_prefix, subject_prefix))
        if _text(work_order_id):
            clauses.append("work_order_id = ?")
            params.append(_text(work_order_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM agent_runtime_review
                {where}
                ORDER BY created_ts_ms, review_id
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [self._record_from_row(row) for row in rows]
        finally:
            conn.close()

    def approve_review(
        self,
        review_id: str,
        *,
        actor_id: str,
        expected_subject_ref: str | None = None,
        expected_subject_version: int | None = None,
        expected_resume_phase: ReviewResumePhase | str | None = None,
        expected_checkpoint_id: str | None = None,
        ttl_ms: int | None = None,
    ) -> dict[str, Any]:
        actor = _text(actor_id)
        if not actor:
            raise ReviewStoreError(REVIEW_STORE_INVALID, "actor_id is required")
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_review(conn, review_id)
            item = self._item_from_row(row)
            self._assert_task_node_not_cancelling(
                conn,
                subject_ref=item.subject_ref,
            )
            self._raise_if_subject_mismatch(
                item,
                expected_subject_ref=expected_subject_ref,
                expected_subject_version=expected_subject_version,
                expected_resume_phase=expected_resume_phase,
                expected_checkpoint_id=expected_checkpoint_id,
            )
            if int(item.expires_at) <= ts_ms:
                self._expire_review_row(conn, row, ts_ms=ts_ms)
                raise ReviewStoreError(REVIEW_TOKEN_EXPIRED, "ReviewItem is expired")
            if item.status is not ReviewStatus.PENDING:
                raise ReviewStoreError(
                    REVIEW_STATUS_CONFLICT,
                    f"ReviewItem must be PENDING before approve, got {item.status.value}",
                )
            validate_review_transition(item.status, ReviewStatus.APPROVED)
            ttl = int(ttl_ms) if ttl_ms is not None else self._token_service.default_ttl_ms
            remaining = int(item.expires_at) - ts_ms
            effective_ttl = min(ttl, remaining)
            if effective_ttl <= 0:
                self._expire_review_row(conn, row, ts_ms=ts_ms)
                raise ReviewStoreError(REVIEW_TOKEN_EXPIRED, "ReviewItem is expired")
            try:
                token = self._token_service.issue_resume_token(
                    review_id=item.review_id,
                    subject_ref=item.subject_ref,
                    subject_version=item.subject_version,
                    resume_phase=item.resume_phase.value,
                    checkpoint_id=str(item.checkpoint_id),
                    ttl_ms=effective_ttl,
                )
            except ValueError as exc:
                raise ReviewStoreError(REVIEW_TOKEN_INVALID, str(exc)) from exc
            conn.execute(
                """
                INSERT INTO agent_runtime_review_token(
                    token_digest, token_ref_public, review_id, token_type,
                    subject_ref, subject_version, resume_phase, checkpoint_id,
                    issued_ts_ms, expires_ts_ms, state, consumed_ts_ms,
                    consumed_result_ref, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', NULL, NULL, ?, ?)
                """,
                (
                    token.token_digest,
                    token.token_ref_public,
                    token.review_id,
                    token.token_type,
                    token.subject_ref,
                    token.subject_version,
                    token.resume_phase,
                    token.checkpoint_id,
                    token.issued_ts_ms,
                    token.expires_ts_ms,
                    ts_ms,
                    ts_ms,
                ),
            )
            approved = replace(
                item,
                status=ReviewStatus.APPROVED,
                decision_actor=actor,
                token_ref=token.token_ref_public,
            )
            self._update_review_item(conn, row, approved, ts_ms=ts_ms)
            return {
                "review": self._record_from_row(self._require_review(conn, item.review_id)),
                "resume_token_ref": token.token_ref,
                "resume_token": token.to_public_dict(),
            }

    def reject_review(self, review_id: str, *, actor_id: str) -> dict[str, Any]:
        actor = _text(actor_id)
        if not actor:
            raise ReviewStoreError(REVIEW_STORE_INVALID, "actor_id is required")
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_review(conn, review_id)
            item = self._item_from_row(row)
            if item.status is not ReviewStatus.PENDING:
                raise ReviewStoreError(
                    REVIEW_STATUS_CONFLICT,
                    f"ReviewItem must be PENDING before reject, got {item.status.value}",
                )
            validate_review_transition(item.status, ReviewStatus.REJECTED)
            rejected = replace(item, status=ReviewStatus.REJECTED, decision_actor=actor)
            self._update_review_item(conn, row, rejected, ts_ms=ts_ms)
            conn.execute(
                """
                UPDATE agent_runtime_review_token
                SET state = 'REVOKED', updated_ts_ms = ?
                WHERE review_id = ? AND state = 'AVAILABLE'
                """,
                (ts_ms, item.review_id),
            )
            return self._record_from_row(self._require_review(conn, item.review_id))

    def resume_review(
        self,
        review_id: str,
        *,
        resume_token_ref: str,
        subject_ref: str,
        subject_version: int,
        resume_phase: ReviewResumePhase | str,
        checkpoint_id: str,
        resume_result_ref: str | None = None,
    ) -> dict[str, Any]:
        try:
            token_digest = review_resume_token_digest(resume_token_ref)
        except ValueError as exc:
            raise ReviewStoreError(REVIEW_TOKEN_INVALID, "resume token is invalid") from exc
        phase = self._phase_value(resume_phase)
        checkpoint = _durable_ref(checkpoint_id, field_name="checkpoint_id")
        result_ref = _durable_ref(
            resume_result_ref or f"review-resume://{_text(review_id)}/{phase.lower()}/{self._now()}",
            field_name="resume_result_ref",
        )
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_review(conn, review_id)
            item = self._item_from_row(row)
            self._assert_task_node_not_cancelling(
                conn,
                subject_ref=item.subject_ref,
            )
            token_row = conn.execute(
                "SELECT * FROM agent_runtime_review_token WHERE token_digest = ?",
                (token_digest,),
            ).fetchone()
            if token_row is None:
                raise ReviewStoreError(REVIEW_TOKEN_INVALID, "resume token not found")
            if str(token_row["review_id"]) != item.review_id:
                raise ReviewStoreError(
                    REVIEW_TOKEN_SUBJECT_MISMATCH,
                    "resume token is bound to a different review",
                )
            if str(token_row["state"]) == "CONSUMED" or item.status is ReviewStatus.CONSUMED:
                raise ReviewStoreError(
                    REVIEW_RESUME_ALREADY_CONSUMED,
                    "ReviewItem resume token has already been consumed",
                )
            if str(token_row["state"]) == "EXPIRED" or int(token_row["expires_ts_ms"]) <= ts_ms:
                self._expire_review_row(conn, row, ts_ms=ts_ms)
                raise ReviewStoreError(REVIEW_TOKEN_EXPIRED, "resume token is expired")
            if str(token_row["state"]) != "AVAILABLE":
                raise ReviewStoreError(REVIEW_TOKEN_INVALID, f"resume token state is {token_row['state']}")
            if int(item.expires_at) <= ts_ms:
                self._expire_review_row(conn, row, ts_ms=ts_ms)
                raise ReviewStoreError(REVIEW_TOKEN_EXPIRED, "ReviewItem is expired")
            if item.status is not ReviewStatus.APPROVED:
                raise ReviewStoreError(
                    REVIEW_STATUS_CONFLICT,
                    f"ReviewItem must be APPROVED before resume, got {item.status.value}",
                )
            self._raise_if_subject_mismatch(
                item,
                expected_subject_ref=subject_ref,
                expected_subject_version=subject_version,
                expected_resume_phase=phase,
                expected_checkpoint_id=checkpoint,
            )
            self._raise_if_token_mismatch(token_row, item)
            cursor = conn.execute(
                """
                UPDATE agent_runtime_review_token
                SET state = 'CONSUMED', consumed_ts_ms = ?,
                    consumed_result_ref = ?, updated_ts_ms = ?
                WHERE token_digest = ? AND state = 'AVAILABLE'
                """,
                (ts_ms, result_ref, ts_ms, token_digest),
            )
            if cursor.rowcount != 1:
                raise ReviewStoreError(
                    REVIEW_RESUME_ALREADY_CONSUMED,
                    "ReviewItem resume token has already been consumed",
                )
            consumed = replace(
                item,
                status=ReviewStatus.CONSUMED,
                resume_count=int(item.resume_count) + 1,
                resume_result_ref=result_ref,
            )
            self._update_review_item(conn, row, consumed, ts_ms=ts_ms)
            return {
                "review": self._record_from_row(self._require_review(conn, item.review_id)),
                "resume_phase": phase,
                "checkpoint_id": checkpoint,
                "resume_action": (
                    "START_EXECUTION"
                    if phase == ReviewResumePhase.PRE_EXECUTION.value
                    else "RETURN_POST_EXECUTION_CHECKPOINT"
                ),
                "replay_side_effects": False,
                "resume_result_ref": result_ref,
            }

    def expire_due(self) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_runtime_review
                WHERE status IN ('PENDING', 'APPROVED') AND expires_at <= ?
                ORDER BY review_id
                """,
                (ts_ms,),
            ).fetchall()
            expired_ids: list[str] = []
            for row in rows:
                self._expire_review_row(conn, row, ts_ms=ts_ms)
                expired_ids.append(str(row["review_id"]))
            token_cursor = conn.execute(
                """
                UPDATE agent_runtime_review_token
                SET state = 'EXPIRED', updated_ts_ms = ?
                WHERE state = 'AVAILABLE' AND expires_ts_ms <= ?
                """,
                (ts_ms, ts_ms),
            )
            return {
                "expired_review_ids": tuple(expired_ids),
                "expired_token_count": int(token_cursor.rowcount if token_cursor.rowcount >= 0 else 0),
            }

    def _require_review(self, conn: sqlite3.Connection, review_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM agent_runtime_review WHERE review_id = ?",
            (_text(review_id),),
        ).fetchone()
        if row is None:
            raise ReviewStoreError(REVIEW_STORE_NOT_FOUND, f"ReviewItem not found: {review_id}")
        return row

    def _item_from_row(self, row: sqlite3.Row) -> ReviewItem:
        payload = _loads_object(row["review_json"], field_name="review_json")
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
        return ReviewItem.from_mapping(payload)

    def _record_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = self._item_from_row(row)
        return {
            "review": item.to_dict(),
            "status_version": int(row["status_version"]),
            "work_order_id": row["work_order_id"],
            "task_graph_id": row["task_graph_id"],
            "task_node_id": row["task_node_id"],
            "side_effects": _loads_list(row["side_effects_json"], field_name="side_effects_json"),
            "evidence_refs": _loads_list(row["evidence_refs_json"], field_name="evidence_refs_json"),
            "created_ts_ms": int(row["created_ts_ms"]),
            "updated_ts_ms": int(row["updated_ts_ms"]),
        }

    def _update_review_item(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        item: ReviewItem,
        *,
        ts_ms: int,
    ) -> None:
        current_version = int(row["status_version"])
        item.validate()
        cursor = conn.execute(
            """
            UPDATE agent_runtime_review
            SET status = ?, status_version = status_version + 1,
                decision_actor = ?, token_ref = ?, resume_count = ?,
                resume_result_ref = ?, review_json = ?, updated_ts_ms = ?
            WHERE review_id = ? AND status_version = ?
            """,
            (
                item.status.value,
                item.decision_actor,
                item.token_ref,
                int(item.resume_count),
                item.resume_result_ref,
                _dumps(item.to_dict()),
                ts_ms,
                item.review_id,
                current_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ReviewStoreError(
                REVIEW_STATUS_CONFLICT,
                f"stale ReviewItem version for {item.review_id}",
            )

    def _expire_review_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        ts_ms: int,
    ) -> None:
        item = self._item_from_row(row)
        if item.status not in {ReviewStatus.PENDING, ReviewStatus.APPROVED}:
            return
        validate_review_transition(item.status, ReviewStatus.EXPIRED)
        expired = replace(item, status=ReviewStatus.EXPIRED)
        self._update_review_item(conn, row, expired, ts_ms=ts_ms)
        conn.execute(
            """
            UPDATE agent_runtime_review_token
            SET state = 'EXPIRED', updated_ts_ms = ?
            WHERE review_id = ? AND state = 'AVAILABLE'
            """,
            (ts_ms, item.review_id),
        )

    def _raise_if_subject_mismatch(
        self,
        item: ReviewItem,
        *,
        expected_subject_ref: str | None,
        expected_subject_version: int | None,
        expected_resume_phase: ReviewResumePhase | str | None,
        expected_checkpoint_id: str | None,
    ) -> None:
        expected_ref = _text(expected_subject_ref)
        if expected_ref and expected_ref != item.subject_ref:
            raise ReviewStoreError(REVIEW_TOKEN_SUBJECT_MISMATCH, "subject_ref mismatch")
        if expected_subject_version is not None and int(expected_subject_version) != int(item.subject_version):
            raise ReviewStoreError(REVIEW_TOKEN_SUBJECT_MISMATCH, "subject_version mismatch")
        phase = self._phase_value(expected_resume_phase) if expected_resume_phase is not None else ""
        if phase and phase != item.resume_phase.value:
            raise ReviewStoreError(REVIEW_TOKEN_SUBJECT_MISMATCH, "resume_phase mismatch")
        checkpoint = _text(expected_checkpoint_id)
        if checkpoint and checkpoint != str(item.checkpoint_id):
            raise ReviewStoreError(REVIEW_TOKEN_SUBJECT_MISMATCH, "checkpoint_id mismatch")

    def _raise_if_token_mismatch(self, token_row: sqlite3.Row, item: ReviewItem) -> None:
        if (
            str(token_row["subject_ref"]) != item.subject_ref
            or int(token_row["subject_version"]) != int(item.subject_version)
            or str(token_row["resume_phase"]) != item.resume_phase.value
            or str(token_row["checkpoint_id"]) != str(item.checkpoint_id)
        ):
            raise ReviewStoreError(
                REVIEW_TOKEN_SUBJECT_MISMATCH,
                "stored resume token binding no longer matches ReviewItem",
            )

    @staticmethod
    def _phase_value(value: ReviewResumePhase | str | None) -> str:
        try:
            return ReviewResumePhase(str(value or "").strip().upper()).value
        except ValueError as exc:
            raise ReviewStoreError(REVIEW_STORE_INVALID, "resume_phase is invalid") from exc


__all__ = [
    "REVIEW_CHECKPOINT_REQUIRED",
    "REVIEW_GRAPH_CANCELLATION_CONFLICT",
    "REVIEW_RESUME_ALREADY_CONSUMED",
    "REVIEW_STATUS_CONFLICT",
    "REVIEW_STORE_INVALID",
    "REVIEW_STORE_NOT_FOUND",
    "REVIEW_STORE_SCHEMA_NOT_READY",
    "REVIEW_STORE_TABLES",
    "REVIEW_TOKEN_EXPIRED",
    "REVIEW_TOKEN_INVALID",
    "REVIEW_TOKEN_SUBJECT_MISMATCH",
    "ReviewStore",
    "ReviewStoreError",
]
