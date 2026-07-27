"""Single-transaction Scheduler clock batch read authority."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.scheduler_clock_batch import (
    SchedulerClockBatchError,
    build_scheduler_clock_batch,
)
from host_brain.persistence.scheduler_repository_primitives import (
    SCHEDULER_CLOCK_RETRY_INVALID,
    SchedulerRepositoryError,
    _clock_retry_required_int,
)


class SchedulerClockBatchStoreMixin:
    """Read pending and due work from one stable SQLite snapshot."""

    def read_clock_batch(
        self,
        *,
        due_at_ms: int,
        limit: int = 100,
    ) -> dict[str, Any]:
        captured_at_ms = _clock_retry_required_int(
            due_at_ms,
            field_name="due_at_ms",
            minimum=0,
        )
        batch_limit = _clock_retry_required_int(
            limit,
            field_name="limit",
            minimum=1,
        )
        if batch_limit > 1_000:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_INVALID,
                "clock batch limit must not exceed 1000",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            pending_rows = conn.execute(
                """
                SELECT o.*, r.receipt_ref AS execution_result_ref,
                       t.receipt_ref AS terminal_outcome_ref,
                       COALESCE(r.receipt_ref, t.receipt_ref) AS existing_result_ref,
                       c.result_ref AS claim_result_ref
                FROM scheduler_occurrence AS o
                LEFT JOIN scheduler_execution_result AS r
                  ON r.occurrence_id = o.occurrence_id
                LEFT JOIN scheduler_occurrence_terminal_outcome AS t
                  ON t.occurrence_id = o.occurrence_id
                LEFT JOIN secure_ingress_idempotency_claim AS c
                  ON c.claim_ref = o.ingress_claim_ref
                WHERE o.source = 'CLOCK'
                  AND (
                       COALESCE(r.receipt_ref, t.receipt_ref) IS NULL
                    OR c.result_ref IS NULL
                    OR c.result_ref != COALESCE(r.receipt_ref, t.receipt_ref)
                  )
                ORDER BY o.created_at_ms, o.scheduled_at_ms, o.occurrence_id
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
            pending = [
                self._clock_batch_occurrence_record(row)
                for row in pending_rows
            ]
            remaining = max(0, batch_limit - len(pending))
            due: list[dict[str, Any]] = []
            if remaining:
                due_rows = conn.execute(
                    self._head_select_sql()
                    + """
                      WHERE h.retired = 0 AND h.enabled = 1
                        AND h.next_scheduled_at_ms IS NOT NULL
                        AND h.next_scheduled_at_ms <= ?
                      ORDER BY h.next_scheduled_at_ms, h.task_id
                      LIMIT ?
                    """,
                    (captured_at_ms, remaining),
                ).fetchall()
                due = [self._head_record(row) for row in due_rows]
            batch = build_scheduler_clock_batch(
                captured_at_ms=captured_at_ms,
                limit=batch_limit,
                pending_occurrences=pending,
                due_definitions=due,
            )
            conn.commit()
            return batch
        except SchedulerClockBatchError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_INVALID,
                exc.detail,
            ) from exc
        except sqlite3.Error:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _clock_batch_occurrence_record(
        self,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        record = self._occurrence_record(row)
        existing_result_ref = (
            str(row["existing_result_ref"])
            if row["existing_result_ref"] is not None
            else None
        )
        record["existing_result_ref"] = existing_result_ref
        record["claim_result_ref"] = (
            str(row["claim_result_ref"])
            if row["claim_result_ref"] is not None
            else None
        )
        record["terminal_outcome_ref"] = (
            str(row["terminal_outcome_ref"])
            if row["terminal_outcome_ref"] is not None
            else None
        )
        record["authority"] = {
            **record["authority"],
            "existing_result_ref": existing_result_ref,
        }
        return record


__all__ = ["SchedulerClockBatchStoreMixin"]
