"""Durable Scheduler clock-retry ownership and fencing transactions.

This mixin deliberately owns no connection factory or repository state.  It
borrows the concrete Scheduler repository's single SQLite connection boundary;
the DUE-to-OCCURRENCE transfer additionally receives the admission UoW's exact
connection so no second transaction or writer can appear during hand-off.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.persistence.scheduler_repository_primitives import (
    SCHEDULER_CLOCK_RETRY_CONFLICT,
    SCHEDULER_CLOCK_RETRY_INVALID,
    SchedulerRepositoryError,
    _clock_retry_required_int,
    _clock_retry_required_reference,
    _clock_retry_required_task_id,
    _clock_retry_required_text,
)


class SchedulerClockRetryStoreMixin:
    """Focused clock-retry persistence operations for the canonical store."""

    def claim_clock_retry(
        self,
        *,
        retry_key: str,
        retry_kind: str,
        task_id: str,
        occurrence_id: str | None,
        owner_id: str,
        at_ms: int,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        """Claim one clock dispatch/retry key with a durable fencing token."""

        key = _clock_retry_required_text(
            retry_key,
            field_name="retry_key",
            limit=512,
        )
        kind = str(retry_kind or "").strip().upper()
        if kind not in {"DUE", "OCCURRENCE"}:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_INVALID,
                "retry_kind must be DUE or OCCURRENCE",
            )
        task_ref = _clock_retry_required_task_id(task_id)
        occurrence_ref = None
        if occurrence_id is not None:
            occurrence_ref = _clock_retry_required_reference(
                occurrence_id,
                field_name="occurrence_id",
            )
        if (kind == "DUE") != (occurrence_ref is None):
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_INVALID,
                "DUE retries cannot bind an occurrence and OCCURRENCE retries must bind one",
            )
        owner = _clock_retry_required_text(
            owner_id,
            field_name="owner_id",
            limit=240,
        )
        now_value = _clock_retry_required_int(
            at_ms,
            field_name="at_ms",
            minimum=0,
        )
        duration = _clock_retry_required_int(
            lease_duration_ms,
            field_name="lease_duration_ms",
            minimum=1,
        )
        expires = now_value + duration
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO scheduler_clock_retry_state(
                        retry_key, retry_kind, task_id, occurrence_id,
                        failure_count, next_retry_at_ms, last_reason_code,
                        lease_owner_id, lease_fencing_token,
                        lease_expires_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, 0, 0, NULL, ?, 1, ?, ?)
                    """,
                    (key, kind, task_ref, occurrence_ref, owner, expires, now_value),
                )
                claimed = True
            else:
                current = self._clock_retry_record(row)
                if (
                    current["retry_kind"] != kind
                    or current["task_id"] != task_ref
                    or current["occurrence_id"] != occurrence_ref
                ):
                    raise SchedulerRepositoryError(
                        SCHEDULER_CLOCK_RETRY_CONFLICT,
                        "clock retry key identity drifted",
                    )
                active_lease = (
                    current["lease_owner_id"] is not None
                    and int(current["lease_expires_at_ms"] or 0) > now_value
                )
                if current["next_retry_at_ms"] > now_value or active_lease:
                    conn.commit()
                    return {**current, "claimed": False}
                updated = conn.execute(
                    """
                    UPDATE scheduler_clock_retry_state
                    SET lease_owner_id = ?,
                        lease_fencing_token = lease_fencing_token + 1,
                        lease_expires_at_ms = ?, updated_at_ms = ?
                    WHERE retry_key = ? AND lease_fencing_token = ?
                    """,
                    (
                        owner,
                        expires,
                        now_value,
                        key,
                        current["lease_fencing_token"],
                    ),
                )
                if updated.rowcount != 1:
                    raise SchedulerRepositoryError(
                        SCHEDULER_CLOCK_RETRY_CONFLICT,
                        "clock retry lease CAS was lost",
                    )
                claimed = True
            claimed_row = conn.execute(
                "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
                (key,),
            ).fetchone()
            if claimed_row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "claimed clock retry state cannot be read back",
                )
            record = self._clock_retry_record(claimed_row)
            conn.commit()
            return {**record, "claimed": claimed}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_CONFLICT,
                "clock retry identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        """Extend only a still-live lease owned by the exact fencing token.

        An expired owner is deliberately unable to revive its lease.  It must
        compete for a new fencing token through :meth:`claim_clock_retry`.
        """

        key = _clock_retry_required_text(
            retry_key,
            field_name="retry_key",
            limit=512,
        )
        owner = _clock_retry_required_text(
            owner_id,
            field_name="owner_id",
            limit=240,
        )
        fence = _clock_retry_required_int(
            fencing_token,
            field_name="fencing_token",
            minimum=1,
        )
        now_value = _clock_retry_required_int(
            at_ms,
            field_name="at_ms",
            minimum=0,
        )
        duration = _clock_retry_required_int(
            lease_duration_ms,
            field_name="lease_duration_ms",
            minimum=1,
        )
        expires = now_value + duration
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            renewed = conn.execute(
                """
                UPDATE scheduler_clock_retry_state
                SET lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE retry_key = ? AND lease_owner_id = ?
                  AND lease_fencing_token = ?
                  AND lease_expires_at_ms > ?
                """,
                (expires, now_value, key, owner, fence, now_value),
            )
            if renewed.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "stale or expired clock retry lease cannot be renewed",
                )
            row = conn.execute(
                "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "renewed clock retry state cannot be read back",
                )
            record = self._clock_retry_record(row)
            conn.commit()
            return record
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def defer_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
        next_retry_at_ms: int,
        reason_code: str,
    ) -> dict[str, Any]:
        key = _clock_retry_required_text(
            retry_key,
            field_name="retry_key",
            limit=512,
        )
        owner = _clock_retry_required_text(
            owner_id,
            field_name="owner_id",
            limit=240,
        )
        fence = _clock_retry_required_int(
            fencing_token,
            field_name="fencing_token",
            minimum=1,
        )
        now_value = _clock_retry_required_int(
            at_ms,
            field_name="at_ms",
            minimum=0,
        )
        next_at = _clock_retry_required_int(
            next_retry_at_ms,
            field_name="next_retry_at_ms",
            minimum=0,
        )
        reason = _clock_retry_required_text(
            reason_code,
            field_name="reason_code",
            limit=128,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE scheduler_clock_retry_state
                SET failure_count = failure_count + 1,
                    next_retry_at_ms = ?, last_reason_code = ?,
                    lease_owner_id = NULL, lease_expires_at_ms = NULL,
                    updated_at_ms = ?
                WHERE retry_key = ? AND lease_owner_id = ?
                  AND lease_fencing_token = ?
                  AND lease_expires_at_ms > ?
                """,
                (next_at, reason, now_value, key, owner, fence, now_value),
            )
            if updated.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "stale clock retry lease cannot defer work",
                )
            row = conn.execute(
                "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
                (key,),
            ).fetchone()
            record = self._clock_retry_record(row)
            conn.commit()
            return record
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def clear_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
    ) -> bool:
        key = _clock_retry_required_text(
            retry_key,
            field_name="retry_key",
            limit=512,
        )
        owner = _clock_retry_required_text(
            owner_id,
            field_name="owner_id",
            limit=240,
        )
        fence = _clock_retry_required_int(
            fencing_token,
            field_name="fencing_token",
            minimum=1,
        )
        now_value = _clock_retry_required_int(
            at_ms,
            field_name="at_ms",
            minimum=0,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cleared = conn.execute(
                """
                UPDATE scheduler_clock_retry_state
                SET failure_count = 0, next_retry_at_ms = 0,
                    last_reason_code = NULL, lease_owner_id = NULL,
                    lease_expires_at_ms = NULL, updated_at_ms = ?
                WHERE retry_key = ? AND lease_owner_id = ?
                  AND lease_fencing_token = ?
                  AND lease_expires_at_ms > ?
                """,
                (now_value, key, owner, fence, now_value),
            )
            if cleared.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "stale clock retry lease cannot clear work",
                )
            conn.commit()
            return True
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def clock_retry_summary(self) -> dict[str, int | None]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS retry_count,
                       MIN(next_retry_at_ms) AS next_retry_at_ms
                FROM scheduler_clock_retry_state
                WHERE failure_count > 0
                """
            ).fetchone()
            return {
                "retry_count": int(row["retry_count"] or 0),
                "next_retry_at_ms": (
                    int(row["next_retry_at_ms"])
                    if row["next_retry_at_ms"] is not None
                    else None
                ),
            }
        finally:
            conn.close()

    @staticmethod
    def _clock_retry_record(row: sqlite3.Row) -> dict[str, Any]:
        if row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_CONFLICT,
                "clock retry state is missing",
            )
        return {
            "retry_key": str(row["retry_key"]),
            "retry_kind": str(row["retry_kind"]),
            "task_id": str(row["task_id"]),
            "occurrence_id": (
                str(row["occurrence_id"])
                if row["occurrence_id"] is not None
                else None
            ),
            "failure_count": int(row["failure_count"]),
            "next_retry_at_ms": int(row["next_retry_at_ms"]),
            "last_reason_code": (
                str(row["last_reason_code"])
                if row["last_reason_code"] is not None
                else None
            ),
            "lease_owner_id": (
                str(row["lease_owner_id"])
                if row["lease_owner_id"] is not None
                else None
            ),
            "lease_fencing_token": int(row["lease_fencing_token"]),
            "lease_expires_at_ms": (
                int(row["lease_expires_at_ms"])
                if row["lease_expires_at_ms"] is not None
                else None
            ),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def _transfer_due_retry_claim(
        self,
        conn: sqlite3.Connection,
        *,
        due_retry_key: str,
        occurrence_id: str,
        task_id: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        """Move DUE ownership to OCCURRENCE inside the admission UoW."""

        if not due_retry_key or not owner_id or fencing_token < 1 or lease_duration_ms < 1:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_INVALID,
                "clock admission requires an exact live DUE lease",
            )
        expires = at_ms + lease_duration_ms
        occurrence_row = conn.execute(
            "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
            (occurrence_id,),
        ).fetchone()
        if occurrence_row is not None:
            current = self._clock_retry_record(occurrence_row)
            if (
                current["retry_kind"] != "OCCURRENCE"
                or current["task_id"] != task_id
                or current["occurrence_id"] != occurrence_id
                or current["lease_owner_id"] != owner_id
                or current["lease_fencing_token"] != fencing_token
                or int(current["lease_expires_at_ms"] or 0) <= at_ms
            ):
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "clock occurrence replay does not own the live occurrence fence",
                )
            conn.execute(
                """
                UPDATE scheduler_clock_retry_state
                SET lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE retry_key = ? AND lease_owner_id = ?
                  AND lease_fencing_token = ? AND lease_expires_at_ms > ?
                """,
                (expires, at_ms, occurrence_id, owner_id, fencing_token, at_ms),
            )
        else:
            transferred = conn.execute(
                """
                UPDATE scheduler_clock_retry_state
                SET retry_key = ?, retry_kind = 'OCCURRENCE',
                    occurrence_id = ?, lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE retry_key = ? AND retry_kind = 'DUE'
                  AND task_id = ? AND occurrence_id IS NULL
                  AND lease_owner_id = ? AND lease_fencing_token = ?
                  AND lease_expires_at_ms > ?
                """,
                (
                    occurrence_id,
                    occurrence_id,
                    expires,
                    at_ms,
                    due_retry_key,
                    task_id,
                    owner_id,
                    fencing_token,
                    at_ms,
                ),
            )
            if transferred.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_CLOCK_RETRY_CONFLICT,
                    "clock admission lost its DUE owner or fencing token",
                )
        row = conn.execute(
            "SELECT * FROM scheduler_clock_retry_state WHERE retry_key = ?",
            (occurrence_id,),
        ).fetchone()
        if row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_CONFLICT,
                "transferred occurrence claim cannot be read back",
            )
        record = self._clock_retry_record(row)
        if (
            record["retry_kind"] != "OCCURRENCE"
            or record["occurrence_id"] != occurrence_id
            or record["lease_owner_id"] != owner_id
            or record["lease_fencing_token"] != fencing_token
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_CONFLICT,
                "transferred occurrence claim identity drifted",
            )
        return {**record, "claimed": True}


__all__ = ["SchedulerClockRetryStoreMixin"]
