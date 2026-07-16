"""Durable cross-process continuation lease for Graph cancellation.

The 0092 intent and decision remain the only cancellation business truth.
This mixin persists only scanner coordination: availability, owner, token,
expiry, fencing and CAS version.  It borrows the concrete ``DurableTaskStore``
transaction/clock helpers and owns neither another connection nor an in-memory
claim registry.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from host_brain.persistence.task_record_codec import (
    _graph_cancellation_continuation_record,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


_TABLE = "task_runtime_graph_cancellation_continuation"
_REQUIRED_COLUMN_METADATA = {
    "intent_ref": ("TEXT", 1, None, 1),
    "graph_id": ("TEXT", 1, None, 0),
    "graph_revision": ("INTEGER", 1, None, 0),
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
_REQUIRED_COLUMNS = frozenset(_REQUIRED_COLUMN_METADATA)
_REQUIRED_INDEX_COLUMNS = {
    "uq_task_runtime_graph_cancellation_continuation_graph": (
        "graph_id",
        "graph_revision",
    ),
    "idx_task_runtime_graph_cancellation_continuation_ready": (
        "available_ts_ms",
        "claim_expires_ts_ms",
        "graph_id",
        "graph_revision",
        "intent_ref",
    ),
}
_UNIQUE_INDEXES = frozenset(
    {"uq_task_runtime_graph_cancellation_continuation_graph"}
)
_TABLE_SQL_FRAGMENTS = (
    "check(length(trim(intent_ref))>0)",
    "check(length(trim(graph_id))>0)",
    "check(typeof(graph_revision)='integer'andgraph_revision>=1)",
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
_REQUIRED_FOREIGN_KEY_ROWS = (
    (
        0,
        0,
        "task_runtime_graph_cancellation_intent",
        "intent_ref",
        "intent_ref",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        0,
        1,
        "task_runtime_graph_cancellation_intent",
        "graph_id",
        "graph_id",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        0,
        2,
        "task_runtime_graph_cancellation_intent",
        "graph_revision",
        "graph_revision",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
)

_MODE_UNCHECKED = "UNCHECKED"
_MODE_LEGACY = "LEGACY"
_MODE_READY = "READY"
_MAX_LEASE_MS = 86_400_000


def _normalized_sql(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _required_text(value: Any, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} exceeds {maximum} characters",
        )
    return normalized


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    return normalized


def _positive_lease(value: Any) -> int:
    lease_ms = _nonnegative_int(value, field_name="lease_ms")
    if lease_ms < 1 or lease_ms > _MAX_LEASE_MS:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"lease_ms must be between 1 and {_MAX_LEASE_MS}",
        )
    return lease_ms


class TaskGraphCancellationContinuationStoreMixin:
    """Claim, heartbeat, defer and complete one pending cancellation scan."""

    def _initialize_graph_cancellation_continuation_capability(self) -> None:
        """Cache the 0094 schema mode once during store construction."""

        self._graph_cancellation_continuation_mode = _MODE_UNCHECKED
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_TABLE,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            self._graph_cancellation_continuation_mode = _MODE_LEGACY
            return
        self.assert_graph_cancellation_ready()
        self.assert_graph_cancellation_continuation_ready()

    def _require_graph_cancellation_continuation_ready(self) -> None:
        if (
            getattr(
                self,
                "_graph_cancellation_continuation_mode",
                _MODE_UNCHECKED,
            )
            != _MODE_READY
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime graph cancellation continuation migration 0094 "
                "was not verified when this store was constructed",
            )

    def assert_graph_cancellation_continuation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        """Verify the frozen 0094 shape once, unless explicitly revalidated."""

        if (
            not force_revalidate
            and getattr(
                self,
                "_graph_cancellation_continuation_mode",
                _MODE_UNCHECKED,
            )
            == _MODE_READY
        ):
            return

        self._graph_cancellation_continuation_mode = _MODE_UNCHECKED
        conn = self._connect()
        try:
            table_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_TABLE,),
            ).fetchone()
            column_metadata = {
                str(row[1]): (
                    str(row[2]).upper(),
                    int(row[3]),
                    None if row[4] is None else str(row[4]),
                    int(row[5]),
                )
                for row in conn.execute(f'PRAGMA table_info("{_TABLE}")').fetchall()
            }
            columns = set(column_metadata)
            index_rows = {
                str(row[1]): bool(row[2])
                for row in conn.execute(f'PRAGMA index_list("{_TABLE}")').fetchall()
            }
            index_columns = {
                name: tuple(
                    str(row[2])
                    for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
                )
                for name in _REQUIRED_INDEX_COLUMNS
                if name in index_rows
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
                for row in conn.execute(f'PRAGMA foreign_key_list("{_TABLE}")').fetchall()
            )
            invalid = None
            if _REQUIRED_COLUMNS <= columns:
                invalid = conn.execute(
                    f"""
                    SELECT intent_ref FROM {_TABLE}
                    WHERE typeof(intent_ref) <> 'text'
                       OR length(trim(intent_ref)) = 0
                       OR typeof(graph_id) <> 'text'
                       OR length(trim(graph_id)) = 0
                       OR typeof(graph_revision) <> 'integer'
                       OR graph_revision < 1
                       OR typeof(claim_fencing_token) <> 'integer'
                       OR claim_fencing_token < 0
                       OR typeof(attempt_count) <> 'integer'
                       OR attempt_count < 0
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
                               claim_owner IS NOT NULL
                               AND typeof(claim_owner) = 'text'
                               AND length(trim(claim_owner)) > 0
                               AND length(claim_owner) <= 255
                               AND claim_token IS NOT NULL
                               AND typeof(claim_token) = 'text'
                               AND length(trim(claim_token)) > 0
                               AND length(claim_token) <= 512
                               AND claim_expires_ts_ms IS NOT NULL
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
                    ORDER BY graph_id, graph_revision, intent_ref
                    LIMIT 1
                    """
                ).fetchone()
        finally:
            conn.close()

        missing_columns = sorted(_REQUIRED_COLUMNS - columns)
        unexpected_columns = sorted(columns - _REQUIRED_COLUMNS)
        invalid_columns = sorted(
            name
            for name, expected in _REQUIRED_COLUMN_METADATA.items()
            if column_metadata.get(name) != expected
        )
        missing_indexes = sorted(_REQUIRED_INDEX_COLUMNS.keys() - index_rows.keys())
        if (
            table_row is None
            or missing_columns
            or unexpected_columns
            or invalid_columns
            or missing_indexes
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime graph cancellation continuation migration 0094 "
                "is not applied: "
                f"table={table_row is not None}, missing={missing_columns}, "
                f"unexpected={unexpected_columns}, "
                f"invalid={invalid_columns}, "
                f"indexes={missing_indexes}",
            )
        invalid_indexes = sorted(
            name
            for name, expected in _REQUIRED_INDEX_COLUMNS.items()
            if index_columns.get(name) != expected
            or (name in _UNIQUE_INDEXES and not index_rows.get(name, False))
        )
        table_sql = _normalized_sql(table_row[0])
        missing_guards = sorted(
            fragment for fragment in _TABLE_SQL_FRAGMENTS if fragment not in table_sql
        )
        invalid_foreign_keys = foreign_keys != _REQUIRED_FOREIGN_KEY_ROWS
        if invalid_indexes or missing_guards or invalid_foreign_keys:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime graph cancellation continuation schema is not "
                "the frozen 0094 shape: "
                f"indexes={invalid_indexes}, guards={missing_guards}, "
                f"foreign_keys_valid={not invalid_foreign_keys}",
            )
        if invalid is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task runtime graph cancellation continuation has invalid "
                f"coordination state: {invalid['intent_ref']}",
            )
        self._graph_cancellation_continuation_mode = _MODE_READY

    def claim_pending_graph_cancellations(
        self,
        worker_id: str,
        lease_ms: int,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Claim pending 0092 intents with transaction-scoped durable fences."""

        self._require_graph_cancellation_continuation_ready()
        owner = _required_text(worker_id, field_name="worker_id", maximum=255)
        lease = _positive_lease(lease_ms)
        size = max(1, min(_nonnegative_int(limit, field_name="limit"), 500))
        claimed: list[dict[str, Any]] = []
        with self._transaction() as conn:
            # Lease time starts only after BEGIN IMMEDIATE owns the writer
            # transaction.  Sampling before this point would let lock wait
            # consume the lease or authorize a mutation with stale time.
            ts_ms = self._now()
            if ts_ms < 0:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "trusted clock returned a negative time",
                )
            expires_ts_ms = ts_ms + lease
            # A worker can commit the canonical 0092 decision and die before
            # releasing 0094 coordination.  Once that lease is absent or
            # expired, the scanner removes the coordination residue instead
            # of retaining an unbounded second completion journal.
            conn.execute(
                f"""
                DELETE FROM {_TABLE}
                WHERE (
                    (
                        claim_owner IS NULL AND claim_token IS NULL
                        AND claim_expires_ts_ms IS NULL
                    )
                    OR claim_expires_ts_ms <= ?
                )
                  AND EXISTS (
                      SELECT 1
                      FROM task_runtime_graph_cancellation_intent AS intent
                      JOIN task_runtime_graph_cancellation_decision AS decision
                        ON decision.intent_ref = intent.intent_ref
                      WHERE intent.intent_ref = {_TABLE}.intent_ref
                        AND intent.status = 'FINALIZED'
                  )
                """,
                (ts_ms,),
            )
            for _ in range(size):
                candidate = conn.execute(
                    f"""
                    SELECT
                        intent.intent_ref,
                        continuation.intent_ref AS continuation_intent_ref,
                        continuation.status_version AS continuation_status_version
                    FROM task_runtime_graph_cancellation_intent AS intent
                    LEFT JOIN task_runtime_graph_cancellation_decision AS decision
                      ON decision.intent_ref = intent.intent_ref
                    LEFT JOIN {_TABLE} AS continuation
                      ON continuation.intent_ref = intent.intent_ref
                    WHERE intent.status = 'REQUESTED'
                      AND decision.intent_ref IS NULL
                      AND (
                          continuation.intent_ref IS NULL
                          OR (
                              continuation.available_ts_ms <= ?
                              AND (
                                  (
                                      continuation.claim_owner IS NULL
                                      AND continuation.claim_token IS NULL
                                      AND continuation.claim_expires_ts_ms IS NULL
                                  )
                                  OR continuation.claim_expires_ts_ms <= ?
                              )
                          )
                      )
                    ORDER BY intent.requested_ts_ms, intent.graph_id,
                             intent.graph_revision, intent.intent_ref
                    LIMIT 1
                    """,
                    (ts_ms, ts_ms),
                ).fetchone()
                if candidate is None:
                    break
                intent_ref = str(candidate["intent_ref"])
                claim_token = secrets.token_urlsafe(32)
                if candidate["continuation_intent_ref"] is None:
                    cursor = conn.execute(
                        f"""
                        INSERT INTO {_TABLE}(
                            intent_ref, graph_id, graph_revision,
                            claim_owner, claim_token, claim_expires_ts_ms,
                            claim_fencing_token, attempt_count,
                            available_ts_ms, last_error, status_version,
                            created_ts_ms, updated_ts_ms
                        )
                        SELECT
                            intent_ref, graph_id, graph_revision,
                            ?, ?, ?, 1, 1, ?, NULL, 1, ?, ?
                        FROM task_runtime_graph_cancellation_intent AS intent
                        WHERE intent.intent_ref = ? AND intent.status = 'REQUESTED'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM task_runtime_graph_cancellation_decision AS decision
                              WHERE decision.intent_ref = intent.intent_ref
                          )
                        """,
                        (
                            owner,
                            claim_token,
                            expires_ts_ms,
                            ts_ms,
                            ts_ms,
                            ts_ms,
                            intent_ref,
                        ),
                    )
                else:
                    expected_version = int(candidate["continuation_status_version"])
                    cursor = conn.execute(
                        f"""
                        UPDATE {_TABLE}
                        SET claim_owner = ?, claim_token = ?,
                            claim_expires_ts_ms = ?,
                            claim_fencing_token = claim_fencing_token + 1,
                            attempt_count = attempt_count + 1,
                            last_error = NULL,
                            status_version = status_version + 1,
                            updated_ts_ms = ?
                        WHERE intent_ref = ? AND status_version = ?
                          AND available_ts_ms <= ?
                          AND (
                              (
                                  claim_owner IS NULL AND claim_token IS NULL
                                  AND claim_expires_ts_ms IS NULL
                              )
                              OR claim_expires_ts_ms <= ?
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM task_runtime_graph_cancellation_intent AS intent
                              WHERE intent.intent_ref = {_TABLE}.intent_ref
                                AND intent.status = 'REQUESTED'
                                AND NOT EXISTS (
                                    SELECT 1
                                    FROM task_runtime_graph_cancellation_decision AS decision
                                    WHERE decision.intent_ref = intent.intent_ref
                                )
                          )
                        """,
                        (
                            owner,
                            claim_token,
                            expires_ts_ms,
                            ts_ms,
                            intent_ref,
                            expected_version,
                            ts_ms,
                            ts_ms,
                        ),
                    )
                if cursor.rowcount != 1:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "graph cancellation continuation claim lost its durable "
                        f"authority: {intent_ref}",
                    )
                row = self._require_graph_cancellation_continuation(conn, intent_ref)
                claimed.append(self._continuation_with_intent(conn, row))
        return claimed

    def heartbeat_graph_cancellation(
        self,
        intent_ref: str,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        lease_ms: int,
        expected_version: int,
    ) -> dict[str, Any]:
        """Extend only the exact live pending-intent continuation claim."""

        self._require_graph_cancellation_continuation_ready()
        reference, owner, token, fence, version = self._claim_arguments(
            intent_ref,
            worker_id,
            claim_token,
            fencing_token,
            expected_version,
        )
        lease = _positive_lease(lease_ms)
        with self._transaction() as conn:
            ts_ms = self._now()
            if ts_ms < 0:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "trusted clock returned a negative time",
                )
            expires_ts_ms = ts_ms + lease
            row = self._require_graph_cancellation_continuation(conn, reference)
            self._require_continuation_version(row, version, reference)
            self._assert_live_continuation_claim(
                row,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            self._assert_pending_cancellation_authority(conn, reference)
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET claim_expires_ts_ms = ?, status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE intent_ref = ? AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ? AND status_version = ?
                  AND claim_expires_ts_ms > ?
                """,
                (expires_ts_ms, ts_ms, reference, owner, token, fence, version, ts_ms),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"graph cancellation heartbeat lost its durable claim: {reference}",
                )
            return self._continuation_with_intent(
                conn,
                self._require_graph_cancellation_continuation(conn, reference),
            )

    def defer_graph_cancellation(
        self,
        intent_ref: str,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        retry_delay_ms: int,
        last_error: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Release a live claim to a durable retry deadline."""

        self._require_graph_cancellation_continuation_ready()
        reference, owner, token, fence, version = self._claim_arguments(
            intent_ref,
            worker_id,
            claim_token,
            fencing_token,
            expected_version,
        )
        delay = _nonnegative_int(retry_delay_ms, field_name="retry_delay_ms")
        error = _required_text(last_error, field_name="last_error", maximum=2_000)
        with self._transaction() as conn:
            ts_ms = self._now()
            if ts_ms < 0:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "trusted clock returned a negative time",
                )
            available_ts_ms = ts_ms + delay
            row = self._require_graph_cancellation_continuation(conn, reference)
            self._require_continuation_version(row, version, reference)
            self._assert_live_continuation_claim(
                row,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            self._assert_pending_cancellation_authority(conn, reference)
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL, available_ts_ms = ?,
                    last_error = ?, status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE intent_ref = ? AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ? AND status_version = ?
                  AND claim_expires_ts_ms > ?
                """,
                (
                    available_ts_ms,
                    error,
                    ts_ms,
                    reference,
                    owner,
                    token,
                    fence,
                    version,
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"graph cancellation defer lost its durable claim: {reference}",
                )
            return self._continuation_with_intent(
                conn,
                self._require_graph_cancellation_continuation(conn, reference),
            )

    def complete_graph_cancellation(
        self,
        intent_ref: str,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        expected_version: int,
    ) -> dict[str, Any]:
        """Clear coordination only after the canonical 0092 decision commits."""

        self._require_graph_cancellation_continuation_ready()
        reference, owner, token, fence, version = self._claim_arguments(
            intent_ref,
            worker_id,
            claim_token,
            fencing_token,
            expected_version,
        )
        with self._transaction() as conn:
            ts_ms = self._now()
            if ts_ms < 0:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "trusted clock returned a negative time",
                )
            row = self._require_graph_cancellation_continuation(conn, reference)
            self._require_continuation_version(row, version, reference)
            self._assert_live_continuation_claim(
                row,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            intent_row = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_intent
                WHERE intent_ref = ?
                """,
                (reference,),
            ).fetchone()
            if intent_row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"continuation lost its canonical cancellation intent: {reference}",
                )
            intent = self._validated_graph_cancellation_intent_record(conn, intent_row)
            decision = conn.execute(
                """
                SELECT decision_ref FROM task_runtime_graph_cancellation_decision
                WHERE intent_ref = ?
                """,
                (reference,),
            ).fetchone()
            if str(intent["status"]) != "FINALIZED" or decision is None:
                raise TaskStoreError(
                    TASK_STORE_CANCELLATION_CONFLICT,
                    "graph cancellation continuation cannot complete before its "
                    f"canonical decision: {reference}",
                )
            current = _graph_cancellation_continuation_record(row)
            cursor = conn.execute(
                f"""
                DELETE FROM {_TABLE}
                WHERE intent_ref = ? AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ? AND status_version = ?
                  AND claim_expires_ts_ms > ?
                """,
                (reference, owner, token, fence, version, ts_ms),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"graph cancellation completion lost its durable claim: {reference}",
                )
            result = dict(current)
            result.update(
                claim_owner=None,
                claim_token=None,
                claim_expires_ts_ms=None,
                available_ts_ms=ts_ms,
                last_error=None,
                status_version=version + 1,
                updated_ts_ms=ts_ms,
            )
            result = _graph_cancellation_continuation_record(result)
            result["intent"] = intent
            result["decision_ref"] = str(decision["decision_ref"])
            return result

    def get_graph_cancellation_continuation(
        self,
        intent_ref: str,
    ) -> dict[str, Any] | None:
        """Read coordination metadata; absence is not cancellation status."""

        self._require_graph_cancellation_continuation_ready()
        reference = _required_text(intent_ref, field_name="intent_ref", maximum=512)
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE intent_ref = ?",
                (reference,),
            ).fetchone()
            return self._continuation_with_intent(conn, row) if row is not None else None
        finally:
            conn.close()

    @staticmethod
    def _require_graph_cancellation_continuation(conn: Any, intent_ref: str) -> Any:
        row = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE intent_ref = ?",
            (str(intent_ref),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"graph cancellation continuation not found: {intent_ref}",
            )
        return row

    def _continuation_with_intent(self, conn: Any, row: Any) -> dict[str, Any]:
        continuation = _graph_cancellation_continuation_record(row)
        intent_row = conn.execute(
            """
            SELECT * FROM task_runtime_graph_cancellation_intent
            WHERE intent_ref = ?
            """,
            (str(continuation["intent_ref"]),),
        ).fetchone()
        if intent_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "graph cancellation continuation lost its canonical intent",
            )
        intent = self._validated_graph_cancellation_intent_record(conn, intent_row)
        if (
            str(continuation["graph_id"]) != str(intent["graph_id"])
            or int(continuation["graph_revision"]) != int(intent["graph_revision"])
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "graph cancellation continuation intent binding drifted",
            )
        continuation["intent"] = intent
        return continuation

    @staticmethod
    def _assert_pending_cancellation_authority(conn: Any, intent_ref: str) -> None:
        row = conn.execute(
            """
            SELECT intent.intent_ref
            FROM task_runtime_graph_cancellation_intent AS intent
            LEFT JOIN task_runtime_graph_cancellation_decision AS decision
              ON decision.intent_ref = intent.intent_ref
            WHERE intent.intent_ref = ? AND intent.status = 'REQUESTED'
              AND decision.intent_ref IS NULL
            """,
            (str(intent_ref),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_CANCELLATION_CONFLICT,
                "graph cancellation continuation no longer binds a pending intent: "
                f"{intent_ref}",
            )

    @staticmethod
    def _require_continuation_version(row: Any, expected: int, intent_ref: str) -> None:
        actual = int(row["status_version"])
        if actual != int(expected):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "stale expected version for graph cancellation continuation "
                f"{intent_ref}: expected {expected}, actual {actual}",
            )

    @staticmethod
    def _assert_live_continuation_claim(
        row: Any,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> None:
        intent_ref = str(row["intent_ref"])
        if row["claim_token"] is None:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"graph cancellation continuation has no active claim: {intent_ref}",
            )
        if str(row["claim_owner"] or "") != worker_id:
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                f"graph cancellation continuation is owned by another worker: {intent_ref}",
            )
        if (
            str(row["claim_token"] or "") != claim_token
            or int(row["claim_fencing_token"]) != fencing_token
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"graph cancellation continuation fence is stale: {intent_ref}",
            )
        if int(row["claim_expires_ts_ms"] or 0) <= int(now_ts_ms):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"graph cancellation continuation claim has expired: {intent_ref}",
            )

    @staticmethod
    def _claim_arguments(
        intent_ref: Any,
        worker_id: Any,
        claim_token: Any,
        fencing_token: Any,
        expected_version: Any,
    ) -> tuple[str, str, str, int, int]:
        reference = _required_text(intent_ref, field_name="intent_ref", maximum=512)
        owner = _required_text(worker_id, field_name="worker_id", maximum=255)
        token = _required_text(claim_token, field_name="claim_token", maximum=512)
        fence = _nonnegative_int(fencing_token, field_name="fencing_token")
        version = _nonnegative_int(expected_version, field_name="expected_version")
        if fence < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "fencing_token must be positive")
        return reference, owner, token, fence, version


__all__ = ["TaskGraphCancellationContinuationStoreMixin"]
