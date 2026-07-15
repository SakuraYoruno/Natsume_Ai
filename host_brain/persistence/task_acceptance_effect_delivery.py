"""Acceptance-effect durable queue and fenced delivery lifecycle.

The mixin uses only the owning ``DurableTaskStore`` `_connect()` and
`_transaction()` primitives.  It never creates a connection or transaction
authority of its own.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.persistence.task_record_codec import (
    _acceptance_effect_fence,
    _acceptance_effect_record,
    _acceptance_effect_text,
)
from host_brain.persistence.task_store_types import (
    AcceptanceEffectStatus,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)


_ACCEPTANCE_EFFECT_INTENT_REQUIRED_COLUMNS = frozenset(
    {
        "effect_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "commit_scope",
        "effect_type",
        "payload_ref",
        "payload_json",
        "payload_sha256",
        "idempotency_key",
        "status",
        "available_ts_ms",
        "lease_owner",
        "lease_expires_ts_ms",
        "fencing_token",
        "attempt_count",
        "status_version",
        "receipt_ref",
        "reason_code",
        "last_error",
        "created_ts_ms",
        "updated_ts_ms",
        "committed_ts_ms",
        "failed_ts_ms",
    }
)


class TaskAcceptanceEffectDeliveryMixin:
    """Read, claim and settle acceptance effects with durable fencing."""

    def assert_acceptance_effect_intent_ready(self) -> None:
        """Require the GR-06 Acceptance Effect Intent queue schema."""

        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_acceptance_effect_intent)"
                ).fetchall()
            }
        finally:
            conn.close()
        missing = sorted(_ACCEPTANCE_EFFECT_INTENT_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime acceptance effect migration 0087 is not applied: "
                f"{missing}",
            )

    def get_acceptance_effect_intent(self, effect_id: str) -> dict[str, Any]:
        self.assert_acceptance_effect_intent_ready()
        effect_ref = _acceptance_effect_text(effect_id, field_name="effect_id", maximum=512)
        conn = self._connect()
        try:
            return _acceptance_effect_record(
                self._require_acceptance_effect_intent(conn, effect_ref)
            )
        finally:
            conn.close()

    def list_acceptance_effect_intents(
        self,
        *,
        status: AcceptanceEffectStatus | str | None = None,
        graph_id: str | None = None,
        graph_revision: int | None = None,
        node_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.assert_acceptance_effect_intent_ready()
        clauses: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            try:
                normalized_status = AcceptanceEffectStatus(
                    str(status).strip().upper()
                ).value
            except ValueError as exc:
                raise TaskStoreError(TASK_STORE_INVALID, "invalid acceptance effect status") from exc
            clauses.append("status = ?")
            parameters.append(normalized_status)
        if graph_id is not None:
            clauses.append("graph_id = ?")
            parameters.append(
                _acceptance_effect_text(graph_id, field_name="graph_id", maximum=255)
            )
        if graph_revision is not None:
            if graph_id is None or isinstance(graph_revision, bool) or int(graph_revision) < 1:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "graph_revision filtering requires graph_id and a positive revision",
                )
            clauses.append("graph_revision = ?")
            parameters.append(int(graph_revision))
        if node_id is not None:
            if graph_id is None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "node_id filtering requires graph_id",
                )
            clauses.append("node_id = ?")
            parameters.append(
                _acceptance_effect_text(node_id, field_name="node_id", maximum=255)
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(min(1_000, max(1, int(limit))))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM task_runtime_acceptance_effect_intent"
                + where
                + " ORDER BY created_ts_ms, effect_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            return [_acceptance_effect_record(row) for row in rows]
        finally:
            conn.close()

    def claim_acceptance_effect_intent(
        self,
        *,
        owner_id: str,
        lease_ms: int = 30_000,
        effect_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim the oldest effect whose durable acceptance scope is committed."""

        self.assert_acceptance_effect_intent_ready()
        owner = _acceptance_effect_text(owner_id, field_name="owner_id", maximum=255)
        if isinstance(lease_ms, bool):
            raise TaskStoreError(TASK_STORE_INVALID, "lease_ms must be a positive integer")
        lease_duration = min(3_600_000, max(1, int(lease_ms)))
        selected_effect = None
        if effect_id is not None:
            selected_effect = _acceptance_effect_text(
                effect_id,
                field_name="effect_id",
                maximum=512,
            )
        ts_ms = self._now()
        expires_ts_ms = ts_ms + lease_duration
        with self._transaction() as conn:
            if selected_effect is not None:
                cancellation_fence = conn.execute(
                    """
                    SELECT resolution_status
                    FROM task_runtime_graph_cancellation_effect_fence
                    WHERE effect_id = ?
                    """,
                    (selected_effect,),
                ).fetchone()
                if cancellation_fence is not None:
                    raise TaskStoreError(
                        TASK_STORE_CANCELLATION_CONFLICT,
                        f"acceptance effect is fenced by Graph cancellation: {selected_effect}",
                    )
                existing_claim = conn.execute(
                    """
                    SELECT * FROM task_runtime_acceptance_effect_intent
                    WHERE effect_id = ?
                    """,
                    (selected_effect,),
                ).fetchone()
                if (
                    existing_claim is not None
                    and str(existing_claim["status"]) == AcceptanceEffectStatus.CLAIMED.value
                    and str(existing_claim["lease_owner"] or "") == owner
                    and int(existing_claim["lease_expires_ts_ms"] or 0) > ts_ms
                ):
                    replay = _acceptance_effect_record(existing_claim)
                    replay["replayed"] = True
                    return replay
            selected_clause = " AND effect.effect_id = ?" if selected_effect is not None else ""
            parameters: list[Any] = [ts_ms, ts_ms]
            if selected_effect is not None:
                parameters.append(selected_effect)
            row = conn.execute(
                """
                SELECT effect.*
                FROM task_runtime_acceptance_effect_intent AS effect
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = effect.attempt_id
                 AND attempt.graph_id = effect.graph_id
                 AND attempt.graph_revision = effect.graph_revision
                 AND attempt.node_id = effect.node_id
                JOIN task_runtime_node AS node
                  ON node.graph_id = effect.graph_id
                 AND node.graph_revision = effect.graph_revision
                 AND node.node_id = effect.node_id
                JOIN task_runtime_graph AS graph_row
                  ON graph_row.graph_id = effect.graph_id
                 AND graph_row.revision = effect.graph_revision
                WHERE (
                    (effect.status = 'PENDING' AND effect.available_ts_ms <= ?)
                    OR
                    (effect.status = 'CLAIMED' AND effect.lease_expires_ts_ms <= ?)
                  )
                  AND attempt.status = 'SUCCEEDED'
                  AND node.status = 'ACCEPTED'
                  AND (
                    effect.commit_scope = 'NODE'
                    OR
                    (effect.commit_scope = 'GRAPH' AND graph_row.status = 'COMPLETED')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM task_runtime_graph_cancellation_effect_fence AS cancel_fence
                    WHERE cancel_fence.effect_id = effect.effect_id
                  )
                """
                + selected_clause
                + " ORDER BY effect.created_ts_ms, effect.effect_id LIMIT 1",
                tuple(parameters),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'CLAIMED', lease_owner = ?, lease_expires_ts_ms = ?,
                    fencing_token = fencing_token + 1,
                    attempt_count = attempt_count + 1,
                    status_version = status_version + 1,
                    reason_code = NULL, last_error = NULL, updated_ts_ms = ?
                WHERE effect_id = ? AND status_version = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM task_runtime_graph_cancellation_effect_fence AS cancel_fence
                    WHERE cancel_fence.effect_id = task_runtime_acceptance_effect_intent.effect_id
                  )
                  AND (
                    (status = 'PENDING' AND available_ts_ms <= ?)
                    OR
                    (status = 'CLAIMED' AND lease_expires_ts_ms <= ?)
                  )
                """,
                (
                    owner,
                    expires_ts_ms,
                    ts_ms,
                    row["effect_id"],
                    int(row["status_version"]),
                    ts_ms,
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"acceptance effect claim lost its fence: {row['effect_id']}",
                )
            return _acceptance_effect_record(
                self._require_acceptance_effect_intent(conn, str(row["effect_id"]))
            )

    def commit_acceptance_effect_intent(
        self,
        effect_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        receipt_ref: str,
    ) -> dict[str, Any]:
        self.assert_acceptance_effect_intent_ready()
        effect_ref = _acceptance_effect_text(effect_id, field_name="effect_id", maximum=512)
        owner = _acceptance_effect_text(owner_id, field_name="owner_id", maximum=255)
        receipt = _acceptance_effect_text(
            receipt_ref,
            field_name="receipt_ref",
            maximum=512,
        )
        if not _DURABLE_REFERENCE_PATTERN.fullmatch(receipt):
            raise TaskStoreError(TASK_STORE_INVALID, "receipt_ref must be a durable reference")
        fence = _acceptance_effect_fence(fencing_token)
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_acceptance_effect_intent(conn, effect_ref)
            status = AcceptanceEffectStatus(str(row["status"]))
            if status is AcceptanceEffectStatus.COMMITTED:
                if (
                    str(row["lease_owner"]) == owner
                    and int(row["fencing_token"]) == fence
                    and str(row["receipt_ref"]) == receipt
                ):
                    replay = _acceptance_effect_record(row)
                    replay["replayed"] = True
                    return replay
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    f"acceptance effect commit replay drift: {effect_ref}",
                )
            self._assert_acceptance_effect_claim(
                row,
                owner_id=owner,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'COMMITTED', lease_expires_ts_ms = NULL,
                    receipt_ref = ?, reason_code = NULL, last_error = NULL,
                    status_version = status_version + 1, updated_ts_ms = ?,
                    committed_ts_ms = ?, failed_ts_ms = NULL
                WHERE effect_id = ? AND status = 'CLAIMED'
                  AND lease_owner = ? AND fencing_token = ?
                  AND status_version = ? AND lease_expires_ts_ms > ?
                """,
                (
                    receipt,
                    ts_ms,
                    ts_ms,
                    effect_ref,
                    owner,
                    fence,
                    int(row["status_version"]),
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"acceptance effect commit lost its fence: {effect_ref}",
                )
            committed = _acceptance_effect_record(
                self._require_acceptance_effect_intent(conn, effect_ref)
            )
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_effect_fence
                SET resolution_status = 'COMMITTED_EVIDENCE', resolved_ts_ms = ?
                WHERE effect_id = ?
                  AND resolution_status = 'RECONCILIATION_REQUIRED'
                """,
                (ts_ms, effect_ref),
            )
            committed["replayed"] = False
            return committed

    def fail_acceptance_effect_intent(
        self,
        effect_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        self.assert_acceptance_effect_intent_ready()
        effect_ref = _acceptance_effect_text(effect_id, field_name="effect_id", maximum=512)
        owner = _acceptance_effect_text(owner_id, field_name="owner_id", maximum=255)
        reason = _acceptance_effect_text(
            reason_code,
            field_name="reason_code",
            maximum=128,
        )
        error_detail = _acceptance_effect_text(detail, field_name="detail", maximum=2_000)
        fence = _acceptance_effect_fence(fencing_token)
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_acceptance_effect_intent(conn, effect_ref)
            status = AcceptanceEffectStatus(str(row["status"]))
            if status is AcceptanceEffectStatus.FAILED:
                if (
                    str(row["lease_owner"]) == owner
                    and int(row["fencing_token"]) == fence
                    and str(row["reason_code"]) == reason
                    and str(row["last_error"]) == error_detail
                ):
                    replay = _acceptance_effect_record(row)
                    replay["replayed"] = True
                    return replay
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    f"acceptance effect failure replay drift: {effect_ref}",
                )
            self._assert_acceptance_effect_claim(
                row,
                owner_id=owner,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'FAILED', lease_expires_ts_ms = NULL,
                    receipt_ref = NULL, reason_code = ?, last_error = ?,
                    status_version = status_version + 1, updated_ts_ms = ?,
                    committed_ts_ms = NULL, failed_ts_ms = ?
                WHERE effect_id = ? AND status = 'CLAIMED'
                  AND lease_owner = ? AND fencing_token = ?
                  AND status_version = ? AND lease_expires_ts_ms > ?
                """,
                (
                    reason,
                    error_detail,
                    ts_ms,
                    ts_ms,
                    effect_ref,
                    owner,
                    fence,
                    int(row["status_version"]),
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"acceptance effect failure lost its fence: {effect_ref}",
                )
            failed = _acceptance_effect_record(
                self._require_acceptance_effect_intent(conn, effect_ref)
            )
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_effect_fence
                SET resolution_status = 'FAILED_EVIDENCE', resolved_ts_ms = ?
                WHERE effect_id = ?
                  AND resolution_status = 'RECONCILIATION_REQUIRED'
                """,
                (ts_ms, effect_ref),
            )
            failed["replayed"] = False
            return failed

    def reschedule_acceptance_effect_intent(
        self,
        effect_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        reason_code: str,
        detail: str,
        delay_ms: int,
    ) -> dict[str, Any]:
        """Return one fenced handler failure to the durable pending queue.

        The attempt counter and fencing token remain monotonic.  Only the
        current live lease owner may reschedule, so a stale handler can never
        overwrite a newer dispatch attempt.
        """

        self.assert_acceptance_effect_intent_ready()
        effect_ref = _acceptance_effect_text(
            effect_id,
            field_name="effect_id",
            maximum=512,
        )
        owner = _acceptance_effect_text(owner_id, field_name="owner_id", maximum=255)
        reason = _acceptance_effect_text(
            reason_code,
            field_name="reason_code",
            maximum=128,
        )
        error_detail = _acceptance_effect_text(
            detail,
            field_name="detail",
            maximum=2_000,
        )
        if isinstance(delay_ms, bool):
            raise TaskStoreError(TASK_STORE_INVALID, "delay_ms must be a non-negative integer")
        retry_delay = min(3_600_000, max(0, int(delay_ms)))
        fence = _acceptance_effect_fence(fencing_token)
        ts_ms = self._now()
        available_ts_ms = ts_ms + retry_delay
        with self._transaction() as conn:
            row = self._require_acceptance_effect_intent(conn, effect_ref)
            cancellation_fence = conn.execute(
                """
                SELECT resolution_status
                FROM task_runtime_graph_cancellation_effect_fence
                WHERE effect_id = ?
                """,
                (effect_ref,),
            ).fetchone()
            if cancellation_fence is not None:
                raise TaskStoreError(
                    TASK_STORE_CANCELLATION_CONFLICT,
                    f"cancelled Graph effect cannot be rescheduled: {effect_ref}",
                )
            self._assert_acceptance_effect_claim(
                row,
                owner_id=owner,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'PENDING', available_ts_ms = ?,
                    lease_owner = NULL, lease_expires_ts_ms = NULL,
                    receipt_ref = NULL, reason_code = ?, last_error = ?,
                    status_version = status_version + 1, updated_ts_ms = ?,
                    committed_ts_ms = NULL, failed_ts_ms = NULL
                WHERE effect_id = ? AND status = 'CLAIMED'
                  AND lease_owner = ? AND fencing_token = ?
                  AND status_version = ? AND lease_expires_ts_ms > ?
                """,
                (
                    available_ts_ms,
                    reason,
                    error_detail,
                    ts_ms,
                    effect_ref,
                    owner,
                    fence,
                    int(row["status_version"]),
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"acceptance effect reschedule lost its fence: {effect_ref}",
                )
            rescheduled = _acceptance_effect_record(
                self._require_acceptance_effect_intent(conn, effect_ref)
            )
            rescheduled["replayed"] = False
            return rescheduled

    @staticmethod
    def _require_acceptance_effect_intent(
        conn: sqlite3.Connection,
        effect_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_acceptance_effect_intent WHERE effect_id = ?",
            (str(effect_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"AcceptanceEffectIntent not found: {effect_id}",
            )
        return row

    @staticmethod
    def _assert_acceptance_effect_claim(
        row: sqlite3.Row,
        *,
        owner_id: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> None:
        if str(row["status"]) != AcceptanceEffectStatus.CLAIMED.value:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"acceptance effect {row['effect_id']} has no active claim",
            )
        if int(row["fencing_token"]) != int(fencing_token):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"acceptance effect {row['effect_id']} fencing token is stale",
            )
        if str(row["lease_owner"] or "") != str(owner_id):
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                f"acceptance effect {row['effect_id']} is owned by another dispatcher",
            )
        if int(row["lease_expires_ts_ms"] or 0) <= int(now_ts_ms):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"acceptance effect {row['effect_id']} claim has expired",
            )
