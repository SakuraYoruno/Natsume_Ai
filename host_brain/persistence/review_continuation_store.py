"""Durable delivery coordination for terminal TaskNode reviews.

The coordination row is intentionally not a second Review or Task state.  A
claim may be completed only after the exact, version-bound TaskNode has left
the waiting status selected by the Review resume phase.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hmac
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping, Sequence
import uuid

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.review_cancellation import (
    cancelled_review_target_status,
)
from host_brain.core.ids import now_ms
from host_brain.persistence.review_continuation_binding import (
    REVIEW_CONTINUATION_CLAIM_LOST,
    REVIEW_CONTINUATION_CONFLICT,
    REVIEW_CONTINUATION_INCOMPLETE,
    REVIEW_CONTINUATION_INVALID,
    REVIEW_CONTINUATION_SCHEMA_NOT_READY,
    REVIEW_CONTINUATION_TABLE,
    ReviewContinuationStoreError,
    TaskReviewSubjectBinding,
    intent_admission_initial_status as _intent_admission_initial_status,
    parse_task_review_subject,
    require_intent_admission_receipt_in_transaction as _require_intent_admission_receipt_in_transaction,
)
from host_brain.persistence.review_continuation_codec import (
    _AUTHORITY_FIELDS,
    _CHECKPOINT_INDEX,
    _READY_INDEX,
    _REQUIRED_COLUMNS,
    _REQUIRED_COLUMN_METADATA,
    _REQUIRED_FOREIGN_KEYS,
    _REQUIRED_INDEX_COLUMNS,
    _REQUIRED_SQL_FRAGMENTS,
    _TERMINAL_DELIVERY_STATUSES,
    _assert_candidate_binding,
    _assert_row_binding,
    _authority_projection,
    _claim_token_digest,
    _non_negative_int,
    _positive_int,
    _review_item_from_row,
    _required_text,
    _row_dict,
    _waiting_status,
)
from host_brain.persistence.review_continuation_schema import (
    continuation_table_exists,
)
from host_brain.persistence.task_record_codec import (
    _graph_cancellation_intent_record,
)


def enqueue_task_review_continuation_in_transaction(
    conn: sqlite3.Connection,
    *,
    review: ReviewItem,
    task_graph_id: Any,
    task_node_id: Any,
    ts_ms: int,
    require_schema: bool = False,
) -> dict[str, Any] | None:
    """Install one exact coordination row in the caller's Review transaction.

    Historical 0055-only stores keep their manual resume semantics unless the
    caller explicitly requires the 0096 production schema.
    """

    if review.status.value not in _TERMINAL_DELIVERY_STATUSES:
        return None
    binding = validate_task_review_admission_in_transaction(
        conn,
        review=review,
        task_graph_id=task_graph_id,
        task_node_id=task_node_id,
        require_schema=require_schema,
    )
    if binding is None:
        return None
    if not continuation_table_exists(conn):
        if require_schema:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_SCHEMA_NOT_READY,
                "review continuation migration 0096 is not applied",
            )
        return None
    now = _non_negative_int(ts_ms, field_name="ts_ms")
    existing = conn.execute(
        f"SELECT * FROM {REVIEW_CONTINUATION_TABLE} WHERE review_id = ?",
        (review.review_id,),
    ).fetchone()
    if existing is not None:
        _assert_row_binding(existing, review=review, binding=binding)
        return _row_dict(existing)
    try:
        conn.execute(
            f"""
            INSERT INTO {REVIEW_CONTINUATION_TABLE}(
                review_id, graph_id, graph_revision, node_id,
                subject_version, resume_phase, claim_owner, claim_token,
                claim_expires_ts_ms, claim_fencing_token, attempt_count,
                available_ts_ms, last_error, status_version,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, ?, NULL, 0, ?, ?)
            """,
            (
                review.review_id,
                binding.graph_id,
                binding.graph_revision,
                binding.node_id,
                int(review.subject_version),
                review.resume_phase.value,
                now,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation checkpoint identity conflicts",
        ) from exc
    inserted = conn.execute(
        f"SELECT * FROM {REVIEW_CONTINUATION_TABLE} WHERE review_id = ?",
        (review.review_id,),
    ).fetchone()
    if inserted is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation insert was not durable",
        )
    return _row_dict(inserted)


def _validate_cancelled_task_review_checkpoint(
    conn: sqlite3.Connection,
    *,
    review: ReviewItem,
    binding: TaskReviewSubjectBinding,
    node: sqlite3.Row,
) -> None:
    """Bind a crossed Review checkpoint to its exact cancellation intent."""

    intent_row = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_intent
        WHERE graph_id = ? AND graph_revision = ?
        """,
        (binding.graph_id, binding.graph_revision),
    ).fetchone()
    if intent_row is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "cancelled Review has no durable Graph cancellation intent",
        )
    try:
        cancellation = _graph_cancellation_intent_record(intent_row)
    except Exception as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "cancelled Review Graph cancellation intent is invalid",
        ) from exc
    payload = dict(cancellation.get("payload") or {})
    before_states = dict(payload.get("node_states_before") or {})
    after_states = dict(payload.get("node_states_after") or {})
    before = dict(before_states.get(binding.node_id) or {})
    after = dict(after_states.get(binding.node_id) or {})
    intent_initial = _intent_admission_initial_status(review.subject_ref)
    expected_waiting = intent_initial or _waiting_status(review.resume_phase.value)
    expected_before = {
        "status": expected_waiting,
        "status_version": int(review.subject_version),
    }
    try:
        expected_after_status = cancelled_review_target_status(expected_waiting)
    except ValueError as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "cancelled Review checkpoint has no legal Graph cancellation transition",
        ) from exc
    expected_after = {
        "status": expected_after_status,
        "status_version": int(review.subject_version) + 1,
    }
    if (
        str(cancellation.get("status") or "") not in {"REQUESTED", "FINALIZED"}
        or before != expected_before
        or after != expected_after
        or str(node["status"]) != expected_after_status
        or int(node["status_version"]) != expected_after["status_version"]
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "cancelled Review is not bound to the exact crossed checkpoint",
        )
    if intent_initial is not None:
        _require_intent_admission_receipt_in_transaction(
            conn,
            review=review,
            binding=binding,
        )
    _assert_phase_attempt(
        conn,
        binding=binding,
        resume_phase=review.resume_phase.value,
        checkpoint_crossed=True,
    )
def validate_task_review_admission_in_transaction(
    conn: sqlite3.Connection,
    *,
    review: ReviewItem,
    task_graph_id: Any,
    task_node_id: Any,
    require_schema: bool,
) -> TaskReviewSubjectBinding | None:
    """Validate a task-bound Review before it becomes durable poison."""

    binding = parse_task_review_subject(review.subject_ref)
    outer_graph = str(task_graph_id or "").strip()
    outer_node = str(task_node_id or "").strip()
    if binding is None:
        if outer_graph or outer_node:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                "Task-bound Review outer lineage requires a canonical task-node subject",
            )
        return None
    if outer_graph != binding.graph_id or outer_node != binding.node_id:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "TaskNode Review outer lineage differs from subject_ref",
        )
    if not continuation_table_exists(conn):
        if require_schema:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_SCHEMA_NOT_READY,
                "review continuation migration 0096 is not applied",
            )
        return binding
    node = _find_node(conn, binding)
    if node is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "TaskNode Review binding has no exact durable TaskNode",
        )
    if review.status is ReviewStatus.CANCELLED:
        _validate_cancelled_task_review_checkpoint(
            conn,
            review=review,
            binding=binding,
            node=node,
        )
        return binding
    intent_initial = _intent_admission_initial_status(review.subject_ref)
    if intent_initial is not None:
        _require_intent_admission_receipt_in_transaction(
            conn,
            review=review,
            binding=binding,
        )
        if (
            review.review_type is not ReviewType.PRE_EXECUTION_APPROVAL
            or review.resume_phase is not ReviewResumePhase.PRE_EXECUTION
            or binding.attempt_id is not None
            or str(node["status"]) != intent_initial
        ):
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                "Intent admission ReviewItem is not at its exact pre-execution checkpoint",
            )
    else:
        expected_waiting = _waiting_status(review.resume_phase.value)
        if str(node["status"]) != expected_waiting:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                "TaskNode is not at the ReviewItem waiting checkpoint",
            )
    if int(node["status_version"]) != int(review.subject_version):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "TaskNode status_version differs from ReviewItem subject_version",
        )
    _assert_phase_attempt(
        conn,
        binding=binding,
        resume_phase=review.resume_phase.value,
    )
    return binding


def validate_review_continuation_attempt_claim_in_transaction(
    conn: sqlite3.Connection,
    *,
    authority: Mapping[str, Any] | None,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    now_ts_ms: int,
) -> None:
    """Fence a resumed PRE Attempt to the live 0096 claim in the same UoW."""

    if authority is None:
        return
    candidate = dict(authority)
    if (
        frozenset(candidate) != _AUTHORITY_FIELDS
        or str(candidate.get("schema_version") or "")
        != "Natsume.task.review_continuation_authority.v1"
        or str(candidate.get("review_status") or "").strip().upper()
        != ReviewStatus.CONSUMED.value
        or str(candidate.get("graph_id") or "") != str(graph_id)
        or int(candidate.get("graph_revision") or 0) != int(graph_revision)
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "READY claim Review continuation authority is not exact",
        )
    row = _require_continuation(
        conn,
        _required_text(candidate.get("review_id"), field_name="review_id"),
    )
    stored_token = _required_text(row["claim_token"], field_name="stored claim_token")
    if (
        str(row["claim_owner"] or "")
        != _required_text(candidate.get("claim_owner"), field_name="claim_owner")
        or int(row["claim_fencing_token"])
        != _positive_int(
            candidate.get("claim_fencing_token"),
            field_name="claim_fencing_token",
        )
        or not hmac.compare_digest(
            _required_text(
                candidate.get("claim_token_sha256"),
                field_name="claim_token_sha256",
            ),
            _claim_token_digest(stored_token),
        )
        or int(row["claim_expires_ts_ms"] or -1)
        <= _non_negative_int(now_ts_ms, field_name="now_ts_ms")
        or _non_negative_int(
            candidate.get("claim_expires_ts_ms"),
            field_name="claim_expires_ts_ms",
        )
        > int(row["claim_expires_ts_ms"] or -1)
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "READY claim lost its live Review continuation fence",
        )
    bundle = _bundle(conn, row)
    _assert_candidate_binding(candidate, bundle)
    review = bundle.get("review")
    if (
        not isinstance(review, Mapping)
        or str(review.get("status") or "").strip().upper()
        != ReviewStatus.CONSUMED.value
        or int(bundle["review_status_version"])
        != int(candidate.get("review_status_version") or -1)
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "READY claim Review decision authority drifted",
        )


class ReviewContinuationStore:
    """SQLite claim/fence adapter for Review continuation delivery."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_INVALID,
                "ReviewContinuationStore requires a durable sqlite file path",
            )
        self.db_path = Path(raw)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_review_continuation_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1_000.0,
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
        return _non_negative_int(self._clock(), field_name="clock")

    def assert_review_continuation_ready(self, *, force_revalidate: bool = False) -> None:
        del force_revalidate
        if not self.db_path.is_file():
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_SCHEMA_NOT_READY,
                f"review continuation database does not exist: {self.db_path}",
            )
        conn = self._connect()
        try:
            if not continuation_table_exists(conn):
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_SCHEMA_NOT_READY,
                    "review continuation migration 0096 is not applied",
                )
            table = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (REVIEW_CONTINUATION_TABLE,),
            ).fetchone()
            column_metadata = {
                str(row[1]): (
                    str(row[2]).upper(),
                    int(row[3]),
                    None if row[4] is None else str(row[4]),
                    int(row[5]),
                )
                for row in conn.execute(
                    f"PRAGMA table_info({REVIEW_CONTINUATION_TABLE})"
                ).fetchall()
            }
            columns = set(column_metadata)
            indexes = {
                str(row[1]): bool(row[2])
                for row in conn.execute(
                    f"PRAGMA index_list({REVIEW_CONTINUATION_TABLE})"
                ).fetchall()
            }
            index_columns = {
                name: tuple(
                    str(row[2])
                    for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
                )
                for name in _REQUIRED_INDEX_COLUMNS
                if name in indexes
            }
            foreign_keys = tuple(
                (
                    int(row[0]),
                    int(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]).upper(),
                    str(row[6]).upper(),
                    str(row[7]).upper(),
                )
                for row in conn.execute(
                    f"PRAGMA foreign_key_list({REVIEW_CONTINUATION_TABLE})"
                ).fetchall()
            )
            missing_columns = sorted(_REQUIRED_COLUMNS - columns)
            unexpected_columns = sorted(columns - _REQUIRED_COLUMNS)
            invalid_columns = sorted(
                name
                for name, expected in _REQUIRED_COLUMN_METADATA.items()
                if column_metadata.get(name) != expected
            )
            missing_indexes = sorted(set(_REQUIRED_INDEX_COLUMNS) - set(indexes))
            invalid_indexes = sorted(
                name
                for name, expected in _REQUIRED_INDEX_COLUMNS.items()
                if index_columns.get(name) != expected
                or (name == _CHECKPOINT_INDEX and not indexes.get(name, False))
                or (name == _READY_INDEX and indexes.get(name, False))
            )
            normalized_sql = re.sub(
                r"\s+",
                "",
                str(table[0] if table is not None else "").lower(),
            )
            missing_guards = sorted(
                fragment
                for fragment in _REQUIRED_SQL_FRAGMENTS
                if fragment not in normalized_sql
            )
            invalid_data = None
            if not missing_columns:
                invalid_data = conn.execute(
                    f"""
                    SELECT review_id FROM {REVIEW_CONTINUATION_TABLE}
                    WHERE typeof(review_id) <> 'text'
                       OR length(trim(review_id)) = 0
                       OR typeof(graph_id) <> 'text'
                       OR length(trim(graph_id)) = 0
                       OR typeof(graph_revision) <> 'integer'
                       OR graph_revision < 1
                       OR typeof(node_id) <> 'text'
                       OR length(trim(node_id)) = 0
                       OR typeof(subject_version) <> 'integer'
                       OR subject_version < 0
                       OR resume_phase NOT IN (
                           'PRE_EXECUTION', 'POST_EXECUTION_ACCEPTANCE'
                       )
                       OR typeof(claim_fencing_token) <> 'integer'
                       OR claim_fencing_token < 0
                       OR typeof(attempt_count) <> 'integer'
                       OR attempt_count <> claim_fencing_token
                       OR typeof(available_ts_ms) <> 'integer'
                       OR available_ts_ms < 0
                       OR typeof(status_version) <> 'integer'
                       OR status_version < 0
                       OR typeof(created_ts_ms) <> 'integer'
                       OR created_ts_ms < 0
                       OR typeof(updated_ts_ms) <> 'integer'
                       OR updated_ts_ms < created_ts_ms
                       OR NOT (
                           (
                               claim_owner IS NULL
                               AND claim_token IS NULL
                               AND claim_expires_ts_ms IS NULL
                           )
                           OR (
                               typeof(claim_owner) = 'text'
                               AND length(trim(claim_owner)) > 0
                               AND length(claim_owner) <= 255
                               AND typeof(claim_token) = 'text'
                               AND length(trim(claim_token)) > 0
                               AND length(claim_token) <= 512
                               AND typeof(claim_expires_ts_ms) = 'integer'
                               AND claim_expires_ts_ms >= 0
                               AND claim_fencing_token > 0
                           )
                       )
                       OR (
                           last_error IS NOT NULL
                           AND (
                               typeof(last_error) <> 'text'
                               OR length(trim(last_error)) = 0
                               OR length(last_error) > 2000
                           )
                       )
                    LIMIT 1
                    """
                ).fetchone()
            if (
                table is None
                or missing_columns
                or unexpected_columns
                or invalid_columns
                or missing_indexes
                or invalid_indexes
                or missing_guards
                or foreign_keys != _REQUIRED_FOREIGN_KEYS
                or invalid_data is not None
            ):
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_SCHEMA_NOT_READY,
                    "review continuation schema is not the frozen 0096 shape: "
                    f"missing_columns={missing_columns}, "
                    f"unexpected_columns={unexpected_columns}, "
                    f"invalid_columns={invalid_columns}, "
                    f"missing_indexes={missing_indexes}, "
                    f"invalid_indexes={invalid_indexes}, "
                    f"missing_guards={missing_guards}, "
                    f"foreign_keys_valid={foreign_keys == _REQUIRED_FOREIGN_KEYS}, "
                    f"invalid_data={None if invalid_data is None else invalid_data[0]}",
                )
        finally:
            conn.close()

    def reconcile_terminal_reviews(self) -> dict[str, int]:
        """Install missing rows for historical terminal Reviews still waiting."""

        scanned = 0
        enqueued = 0
        already_crossed = 0
        failed = 0
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT review_id FROM agent_runtime_review
                WHERE status IN ('CONSUMED', 'REJECTED', 'EXPIRED', 'CANCELLED')
                  AND task_graph_id IS NOT NULL
                  AND task_node_id IS NOT NULL
                ORDER BY created_ts_ms, review_id
                """
            ).fetchall()
        finally:
            conn.close()
        for candidate in rows:
            scanned += 1
            try:
                with self._transaction() as conn:
                    row = conn.execute(
                        "SELECT * FROM agent_runtime_review WHERE review_id = ?",
                        (str(candidate["review_id"]),),
                    ).fetchone()
                    if row is None or str(row["status"]) not in {
                        "CONSUMED",
                        "REJECTED",
                        "EXPIRED",
                        "CANCELLED",
                    }:
                        continue
                    review = _review_item_from_row(row)
                    binding = parse_task_review_subject(review.subject_ref)
                    if binding is None:
                        raise ReviewContinuationStoreError(
                            REVIEW_CONTINUATION_CONFLICT,
                            "task-bound terminal ReviewItem lost its subject binding",
                        )
                    if (
                        str(row["task_graph_id"] or "") != binding.graph_id
                        or str(row["task_node_id"] or "") != binding.node_id
                    ):
                        raise ReviewContinuationStoreError(
                            REVIEW_CONTINUATION_CONFLICT,
                            "task-bound terminal ReviewItem lineage drifted",
                        )
                    node = _require_node(conn, binding)
                    intent_initial = _intent_admission_initial_status(
                        review.subject_ref
                    )
                    checkpoint_status = (
                        intent_initial
                        if intent_initial is not None
                        else _waiting_status(review.resume_phase.value)
                    )
                    if review.status is not ReviewStatus.CANCELLED and (
                        str(node["status"]) != checkpoint_status
                        or int(node["status_version"])
                        != int(review.subject_version)
                    ):
                        already_crossed += 1
                        continue
                    if review.status is not ReviewStatus.CANCELLED:
                        validate_task_review_admission_in_transaction(
                            conn,
                            review=review,
                            task_graph_id=row["task_graph_id"],
                            task_node_id=row["task_node_id"],
                            require_schema=True,
                        )
                    existed = conn.execute(
                        f"SELECT 1 FROM {REVIEW_CONTINUATION_TABLE} WHERE review_id = ?",
                        (review.review_id,),
                    ).fetchone()
                    enqueue_task_review_continuation_in_transaction(
                        conn,
                        review=review,
                        task_graph_id=row["task_graph_id"],
                        task_node_id=row["task_node_id"],
                        ts_ms=self._now(),
                        require_schema=True,
                    )
                    if existed is None:
                        enqueued += 1
            except ReviewContinuationStoreError:
                failed += 1
        return {
            "scanned_count": scanned,
            "enqueued_count": enqueued,
            "already_crossed_count": already_crossed,
            "failed_count": failed,
        }

    def claim_pending_review_continuations(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]:
        owner = _required_text(worker_id, field_name="worker_id")
        lease = _positive_int(lease_ms, field_name="lease_ms")
        bounded = max(1, min(int(limit), 500))
        claimed: list[dict[str, Any]] = []
        # Candidate discovery is advisory.  Every candidate is claimed in its
        # own writer transaction so one historical cross-table drift cannot
        # roll back or permanently starve every healthy continuation behind it.
        advisory_now = self._now()
        conn = self._connect()
        try:
            candidates = conn.execute(
                f"""
                SELECT review_id FROM {REVIEW_CONTINUATION_TABLE}
                WHERE available_ts_ms <= ?
                  AND (claim_owner IS NULL OR claim_expires_ts_ms <= ?)
                ORDER BY available_ts_ms, graph_id, graph_revision, node_id, review_id
                LIMIT 500
                """,
                (advisory_now, advisory_now),
            ).fetchall()
        finally:
            conn.close()
        for candidate in candidates:
            if len(claimed) >= bounded:
                break
            try:
                with self._transaction() as conn:
                    # Lease time begins only after this writer owns
                    # BEGIN IMMEDIATE; lock wait cannot consume the lease.
                    now = self._now()
                    row = _require_continuation(
                        conn,
                        str(candidate["review_id"]),
                    )
                    if (
                        int(row["available_ts_ms"]) > now
                        or (
                            row["claim_owner"] is not None
                            and int(row["claim_expires_ts_ms"] or -1) > now
                        )
                    ):
                        continue
                    # Validate the complete Review/Task binding before the
                    # mutation.  Any poison row rolls back only this candidate.
                    _bundle(
                        conn,
                        row,
                        allow_crossed_pre_execution=True,
                    )
                    token = f"review-continuation-claim-{uuid.uuid4().hex}"
                    cursor = conn.execute(
                        f"""
                        UPDATE {REVIEW_CONTINUATION_TABLE}
                        SET claim_owner = ?, claim_token = ?,
                            claim_expires_ts_ms = ?,
                            claim_fencing_token = claim_fencing_token + 1,
                            attempt_count = attempt_count + 1,
                            status_version = status_version + 1,
                            updated_ts_ms = ?
                        WHERE review_id = ? AND status_version = ?
                          AND available_ts_ms <= ?
                          AND (claim_owner IS NULL OR claim_expires_ts_ms <= ?)
                        """,
                        (
                            owner,
                            token,
                            now + lease,
                            now,
                            str(row["review_id"]),
                            int(row["status_version"]),
                            now,
                            now,
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                    refreshed = _require_continuation(
                        conn,
                        str(row["review_id"]),
                    )
                    claimed.append(
                        _bundle(
                            conn,
                            refreshed,
                            allow_crossed_pre_execution=True,
                        )
                    )
            except ReviewContinuationStoreError:
                continue
        return tuple(claimed)

    def heartbeat_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        lease_ms: int,
        expected_version: int,
    ) -> Mapping[str, Any]:
        with self._transaction() as conn:
            now = self._now()
            row = _require_continuation(conn, review_id)
            _assert_claim(
                row,
                worker_id=worker_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                expected_version=expected_version,
                now=now,
            )
            cursor = conn.execute(
                f"""
                UPDATE {REVIEW_CONTINUATION_TABLE}
                SET claim_expires_ts_ms = ?, status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE review_id = ? AND status_version = ?
                  AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ?
                """,
                (
                    now + _positive_int(lease_ms, field_name="lease_ms"),
                    now,
                    str(review_id),
                    int(expected_version),
                    _required_text(worker_id, field_name="worker_id"),
                    _required_text(claim_token, field_name="claim_token"),
                    _positive_int(fencing_token, field_name="fencing_token"),
                ),
            )
            if cursor.rowcount != 1:
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation heartbeat lost its claim",
                )
            return _bundle(
                conn,
                _require_continuation(conn, review_id),
                allow_crossed_pre_execution=True,
            )

    def defer_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        retry_delay_ms: int,
        last_error: str,
        expected_version: int,
    ) -> Mapping[str, Any]:
        error = _required_text(last_error, field_name="last_error")[:2000]
        delay = _non_negative_int(retry_delay_ms, field_name="retry_delay_ms")
        with self._transaction() as conn:
            now = self._now()
            row = _require_continuation(conn, review_id)
            _assert_claim(
                row,
                worker_id=worker_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                expected_version=expected_version,
                now=now,
                allow_expired=True,
            )
            cursor = conn.execute(
                f"""
                UPDATE {REVIEW_CONTINUATION_TABLE}
                SET claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL, available_ts_ms = ?,
                    last_error = ?, status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE review_id = ? AND status_version = ?
                  AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ?
                """,
                (
                    now + delay,
                    error,
                    now,
                    str(review_id),
                    int(expected_version),
                    _required_text(worker_id, field_name="worker_id"),
                    _required_text(claim_token, field_name="claim_token"),
                    _positive_int(fencing_token, field_name="fencing_token"),
                ),
            )
            if cursor.rowcount != 1:
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation defer lost its claim",
                )
            return _bundle(
                conn,
                _require_continuation(conn, review_id),
                allow_crossed_pre_execution=True,
            )

    def complete_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        expected_version: int,
    ) -> Mapping[str, Any]:
        with self._transaction() as conn:
            now = self._now()
            row = _require_continuation(conn, review_id)
            _assert_claim(
                row,
                worker_id=worker_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                expected_version=expected_version,
                now=now,
            )
            bundle = _bundle(conn, row, require_checkpoint_crossed=True)
            cursor = conn.execute(
                f"""
                DELETE FROM {REVIEW_CONTINUATION_TABLE}
                WHERE review_id = ? AND status_version = ?
                  AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ?
                """,
                (
                    str(review_id),
                    int(expected_version),
                    _required_text(worker_id, field_name="worker_id"),
                    _required_text(claim_token, field_name="claim_token"),
                    _positive_int(fencing_token, field_name="fencing_token"),
                ),
            )
            if cursor.rowcount != 1:
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation completion lost its claim",
                )
            return {**bundle, "completed": True}

    def get_review_continuation(self, review_id: str) -> Mapping[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {REVIEW_CONTINUATION_TABLE} WHERE review_id = ?",
                (str(review_id or "").strip(),),
            ).fetchone()
            return (
                _bundle(conn, row, allow_crossed_pre_execution=True)
                if row is not None
                else None
            )
        finally:
            conn.close()

    def validate_review_continuation_claim(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Revalidate a raw live claim and return only secret-free authority."""

        candidate = dict(claim or {})
        review_id = _required_text(candidate.get("review_id"), field_name="review_id")
        owner = _required_text(candidate.get("claim_owner"), field_name="claim_owner")
        token = _required_text(candidate.get("claim_token"), field_name="claim_token")
        fence = _positive_int(
            candidate.get("claim_fencing_token"),
            field_name="claim_fencing_token",
        )
        expected_version = _non_negative_int(
            candidate.get("status_version"),
            field_name="status_version",
        )
        conn = self._connect()
        try:
            row = _require_continuation(conn, review_id)
            _assert_claim(
                row,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                expected_version=expected_version,
                now=self._now(),
            )
            bundle = _bundle(
                conn,
                row,
                allow_crossed_pre_execution=True,
            )
            _assert_candidate_binding(candidate, bundle)
            return _authority_projection(bundle, claim_token=token)
        finally:
            conn.close()

    def validate_review_continuation_authority(
        self,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Revalidate secret-free digest/owner/fence authority against a live claim."""

        return self._validate_review_continuation_authority(
            authority,
        )

    def validate_review_continuation_execution_authority(
        self,
        authority: Mapping[str, Any],
        *,
        attempt_id: str,
        lease_id: str,
        lease_owner_id: str,
        fencing_token: int,
        node_id: str,
    ) -> Mapping[str, Any]:
        """Revalidate authority after the resumed PRE Attempt has been claimed."""

        return self._validate_review_continuation_authority(
            authority,
            execution={
                "attempt_id": _required_text(
                    attempt_id,
                    field_name="attempt_id",
                ),
                "lease_id": _required_text(lease_id, field_name="lease_id"),
                "lease_owner_id": _required_text(
                    lease_owner_id,
                    field_name="lease_owner_id",
                ),
                "fencing_token": _positive_int(
                    fencing_token,
                    field_name="fencing_token",
                ),
                "node_id": _required_text(node_id, field_name="node_id"),
            },
        )

    def _validate_review_continuation_authority(
        self,
        authority: Mapping[str, Any],
        *,
        execution: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:

        candidate = dict(authority or {})
        if (
            frozenset(candidate) != _AUTHORITY_FIELDS
            or str(candidate.get("schema_version") or "")
            != "Natsume.task.review_continuation_authority.v1"
        ):
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CLAIM_LOST,
                "review continuation authority schema is invalid",
            )
        review_id = _required_text(candidate.get("review_id"), field_name="review_id")
        owner = _required_text(candidate.get("claim_owner"), field_name="claim_owner")
        fence = _positive_int(
            candidate.get("claim_fencing_token"),
            field_name="claim_fencing_token",
        )
        token_digest = _required_text(
            candidate.get("claim_token_sha256"),
            field_name="claim_token_sha256",
        )
        authority_expires = _non_negative_int(
            candidate.get("claim_expires_ts_ms"),
            field_name="claim_expires_ts_ms",
        )
        conn = self._connect()
        try:
            row = _require_continuation(conn, review_id)
            stored_token = _required_text(row["claim_token"], field_name="stored claim_token")
            expected_digest = _claim_token_digest(stored_token)
            if not hmac.compare_digest(token_digest, expected_digest):
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation authority token digest is stale",
                )
            observed_now = self._now()
            durable_expires = int(row["claim_expires_ts_ms"] or -1)
            if durable_expires <= observed_now or authority_expires > durable_expires:
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation authority expiry is stale or unbound",
                )
            _assert_claim(
                row,
                worker_id=owner,
                claim_token=stored_token,
                fencing_token=fence,
                expected_version=int(row["status_version"]),
                now=observed_now,
            )
            bundle = _bundle(
                conn,
                row,
                allow_crossed_pre_execution=(execution is None),
                execution=(
                    None
                    if execution is None
                    else {**dict(execution), "observed_now_ms": observed_now}
                ),
            )
            _assert_candidate_binding(candidate, bundle)
            review = bundle.get("review")
            durable_review_status = (
                str(review.get("status") or "").strip().upper()
                if isinstance(review, Mapping)
                else ""
            )
            if (
                str(candidate.get("review_status") or "").strip().upper()
                != durable_review_status
            ):
                raise ReviewContinuationStoreError(
                    REVIEW_CONTINUATION_CLAIM_LOST,
                    "review continuation authority Review status drifted",
                )
            # Heartbeats may extend expiry/status_version while this authority
            # is in flight.  The candidate remains valid only inside its
            # original, shorter expiry and exact owner/token/fence; returning
            # the candidate avoids turning a harmless heartbeat into drift.
            return dict(candidate)
        finally:
            conn.close()


def _bundle(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    require_checkpoint_crossed: bool = False,
    allow_crossed_pre_execution: bool = False,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    review_row = conn.execute(
        "SELECT * FROM agent_runtime_review WHERE review_id = ?",
        (str(row["review_id"]),),
    ).fetchone()
    if review_row is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation lost its ReviewItem",
        )
    review = _review_item_from_row(review_row)
    binding = parse_task_review_subject(review.subject_ref)
    if binding is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation subject_ref is invalid",
        )
    _assert_row_binding(row, review=review, binding=binding)
    if review.status.value not in _TERMINAL_DELIVERY_STATUSES:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation is not backed by a terminal ReviewItem",
        )
    node = _require_node(conn, binding)
    intent_initial = _intent_admission_initial_status(review.subject_ref)
    if intent_initial is not None:
        _require_intent_admission_receipt_in_transaction(
            conn,
            review=review,
            binding=binding,
        )
        # Intent review happens before the ordinary DAG scheduler is allowed to
        # claim READY nodes.  Its checkpoint is crossed only after the node
        # leaves the exact admission status/version (or a TaskAttempt exists),
        # never merely because it was not WAITING_APPROVAL.
        attempt = conn.execute(
            """
            SELECT 1 FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            LIMIT 1
            """,
            (binding.graph_id, binding.graph_revision, binding.node_id),
        ).fetchone()
        crossed = (
            str(node["status"]) != intent_initial
            or int(node["status_version"]) != int(review.subject_version)
            or attempt is not None
        )
    else:
        expected_waiting = _waiting_status(review.resume_phase.value)
        crossed = str(node["status"]) != expected_waiting
    _assert_phase_attempt(
        conn,
        binding=binding,
        resume_phase=review.resume_phase.value,
        checkpoint_crossed=(allow_crossed_pre_execution and crossed),
        execution=execution,
    )
    if require_checkpoint_crossed and not crossed:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INCOMPLETE,
            "reviewed TaskNode has not left its bound waiting status",
        )
    return {
        **_row_dict(row),
        "review": review.to_dict(),
        "review_status_version": int(review_row["status_version"]),
        "node": {
            "graph_id": str(node["graph_id"]),
            "graph_revision": int(node["graph_revision"]),
            "node_id": str(node["node_id"]),
            "status": str(node["status"]),
            "status_version": int(node["status_version"]),
        },
        "checkpoint_crossed": crossed,
    }


def _assert_phase_attempt(
    conn: sqlite3.Connection,
    *,
    binding: TaskReviewSubjectBinding,
    resume_phase: str,
    checkpoint_crossed: bool = False,
    execution: Mapping[str, Any] | None = None,
) -> None:
    if resume_phase == ReviewResumePhase.PRE_EXECUTION.value:
        if binding.attempt_id is not None:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                "pre-execution ReviewItem must not bind a TaskAttempt",
            )
        if execution is not None:
            _assert_resumed_attempt_authority(
                conn,
                binding=binding,
                execution=execution,
            )
            return
        live = conn.execute(
            """
            SELECT 1 FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND status IN ('CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED')
            LIMIT 1
            """,
            (binding.graph_id, binding.graph_revision, binding.node_id),
        ).fetchone()
        if live is not None and not checkpoint_crossed:
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                "pre-execution waiting TaskNode has a live TaskAttempt",
            )
        return
    if resume_phase != ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "review continuation resume_phase is unsupported",
        )
    if binding.attempt_id is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "post-execution ReviewItem requires an exact TaskAttempt",
        )
    attempt = conn.execute(
        """
        SELECT status FROM task_runtime_attempt
        WHERE attempt_id = ? AND graph_id = ? AND graph_revision = ? AND node_id = ?
        """,
        (
            binding.attempt_id,
            binding.graph_id,
            binding.graph_revision,
            binding.node_id,
        ),
    ).fetchone()
    if attempt is None or str(attempt["status"]) != "SUCCEEDED":
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "post-execution ReviewItem lacks its exact SUCCEEDED TaskAttempt",
        )
    if execution is not None:
        _assert_resumed_attempt_authority(
            conn,
            binding=binding,
            execution=execution,
        )


def _assert_resumed_attempt_authority(
    conn: sqlite3.Connection,
    *,
    binding: TaskReviewSubjectBinding,
    execution: Mapping[str, Any],
) -> None:
    attempt_id = _required_text(execution.get("attempt_id"), field_name="attempt_id")
    lease_id = _required_text(execution.get("lease_id"), field_name="lease_id")
    owner = _required_text(
        execution.get("lease_owner_id"),
        field_name="lease_owner_id",
    )
    fence = _positive_int(
        execution.get("fencing_token"),
        field_name="fencing_token",
    )
    execution_node_id = _required_text(
        execution.get("node_id"),
        field_name="node_id",
    )
    if execution_node_id != binding.node_id:
        anchor = conn.execute(
            """
            SELECT node.status AS node_status,
                   attempt.status AS attempt_status
            FROM task_runtime_node AS node
            JOIN task_runtime_attempt AS attempt
              ON attempt.graph_id = node.graph_id
             AND attempt.graph_revision = node.graph_revision
             AND attempt.node_id = node.node_id
            WHERE node.graph_id = ? AND node.graph_revision = ?
              AND node.node_id = ?
            ORDER BY attempt.attempt_no DESC
            LIMIT 1
            """,
            (binding.graph_id, binding.graph_revision, binding.node_id),
        ).fetchone()
        if (
            anchor is None
            or str(anchor["node_status"]) != "ACCEPTED"
            or str(anchor["attempt_status"]) != "SUCCEEDED"
        ):
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CLAIM_LOST,
                "downstream continuation preceded acceptance of its reviewed node",
            )
    row = conn.execute(
        """
        SELECT attempt.status AS attempt_status,
               attempt.owner_id AS attempt_owner,
               lease.status AS lease_status,
               lease.owner_id AS lease_owner,
               lease.fencing_token AS lease_fence,
               lease.expires_ts_ms AS lease_expires
        FROM task_runtime_attempt AS attempt
        JOIN task_runtime_lease AS lease
          ON lease.attempt_id = attempt.attempt_id
        WHERE attempt.attempt_id = ?
          AND attempt.graph_id = ? AND attempt.graph_revision = ?
          AND attempt.node_id = ? AND lease.lease_id = ?
        """,
        (
            attempt_id,
            binding.graph_id,
            binding.graph_revision,
            execution_node_id,
            lease_id,
        ),
    ).fetchone()
    if (
        row is None
        or str(row["attempt_status"]) not in {"LEASED", "RUNNING"}
        or str(row["attempt_owner"] or "") != owner
        or str(row["lease_status"]) != "ACTIVE"
        or str(row["lease_owner"]) != owner
        or int(row["lease_fence"]) != fence
        or int(row["lease_expires"])
        <= _non_negative_int(
            execution.get("observed_now_ms"),
            field_name="observed_now_ms",
        )
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "Review continuation grant is not bound to the active resumed Attempt/Lease",
        )


def _require_node(
    conn: sqlite3.Connection,
    binding: TaskReviewSubjectBinding,
) -> sqlite3.Row:
    row = _find_node(conn, binding)
    if row is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "TaskNode Review binding has no exact durable TaskNode",
        )
    return row


def _find_node(
    conn: sqlite3.Connection,
    binding: TaskReviewSubjectBinding,
) -> sqlite3.Row | None:
    _assert_graph_revision_not_superseded(
        conn,
        graph_id=binding.graph_id,
        graph_revision=binding.graph_revision,
    )
    return conn.execute(
        """
        SELECT * FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
        """,
        (binding.graph_id, binding.graph_revision, binding.node_id),
    ).fetchone()


def _assert_graph_revision_not_superseded(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
) -> None:
    ready = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table'
          AND name = 'task_runtime_graph_revision_supersession'
        """
    ).fetchone()
    if ready is None:
        return
    row = conn.execute(
        """
        SELECT supersession_id, replacement_revision
        FROM task_runtime_graph_revision_supersession
        WHERE graph_id = ? AND previous_revision = ?
        """,
        (str(graph_id), int(graph_revision)),
    ).fetchone()
    if row is not None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "superseded Graph revision fences Review continuation: "
            f"{row['supersession_id']} -> revision "
            f"{int(row['replacement_revision'])}",
        )


def _require_continuation(conn: sqlite3.Connection, review_id: str) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT * FROM {REVIEW_CONTINUATION_TABLE} WHERE review_id = ?",
        (_required_text(review_id, field_name="review_id"),),
    ).fetchone()
    if row is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_INVALID,
            f"review continuation not found: {review_id}",
        )
    return row


def _assert_claim(
    row: sqlite3.Row,
    *,
    worker_id: str,
    claim_token: str,
    fencing_token: int,
    expected_version: int,
    now: int,
    allow_expired: bool = False,
) -> None:
    if (
        str(row["claim_owner"] or "") != _required_text(worker_id, field_name="worker_id")
        or str(row["claim_token"] or "") != _required_text(claim_token, field_name="claim_token")
        or int(row["claim_fencing_token"]) != _positive_int(
            fencing_token, field_name="fencing_token"
        )
        or int(row["status_version"]) != _non_negative_int(
            expected_version, field_name="expected_version"
        )
        or (
            not allow_expired
            and int(row["claim_expires_ts_ms"] or -1) <= int(now)
        )
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CLAIM_LOST,
            "review continuation claim is stale or belongs to another worker",
        )


__all__ = [
    "REVIEW_CONTINUATION_CLAIM_LOST",
    "REVIEW_CONTINUATION_CONFLICT",
    "REVIEW_CONTINUATION_INCOMPLETE",
    "REVIEW_CONTINUATION_INVALID",
    "REVIEW_CONTINUATION_SCHEMA_NOT_READY",
    "REVIEW_CONTINUATION_TABLE",
    "ReviewContinuationStore",
    "ReviewContinuationStoreError",
    "TaskReviewSubjectBinding",
    "continuation_table_exists",
    "enqueue_task_review_continuation_in_transaction",
    "parse_task_review_subject",
    "validate_review_continuation_attempt_claim_in_transaction",
    "validate_task_review_admission_in_transaction",
]
