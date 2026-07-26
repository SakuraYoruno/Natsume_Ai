"""Append-only inventory for unresolved nonterminal Operation recovery."""

from __future__ import annotations

import sqlite3
from typing import Any

from .task_operation_recovery_hold_codec import (
    build_operation_recovery_hold_subject,
    validate_operation_recovery_hold_row,
)
from .task_operation_recovery_hold_support import (
    AGGREGATE,
    EVENT,
    build_hold_outbox,
    hold_is_current,
    hold_row_values,
    load_hold_observations,
    normalize_hold_identity,
)
from .task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


_TABLE = "task_runtime_operation_recovery_hold"


class TaskOperationRecoveryHoldStoreMixin:
    """Freeze exact recovery ambiguity without attributing a side effect."""

    def assert_operation_recovery_hold_ready(self) -> None:
        conn = self._connect()
        try:
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({_TABLE})")
            }
            indexes = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_list({_TABLE})")
            }
            triggers = {
                str(row["name"])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger' AND tbl_name = ?
                    """,
                    (_TABLE,),
                )
            }
        finally:
            conn.close()
        if (
            not {
                "case_ref",
                "case_kind",
                "classification",
                "reservation_id",
                "operation_status_version",
                "budget_id",
                "budget_status_version",
                "subject_json",
                "subject_sha256",
                "outbox_id",
            }
            <= columns
            or "idx_task_operation_recovery_hold_current" not in indexes
            or not {
                "trg_task_operation_recovery_hold_exact_insert",
                "trg_task_operation_recovery_hold_no_update",
                "trg_task_operation_recovery_hold_no_delete",
            }
            <= triggers
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "Operation recovery hold migration 0130 is not ready",
            )

    def record_operation_recovery_hold(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        reservation_id: str,
        budget_id: str,
        reason_code: str,
        checkpoint_state: str | None = None,
    ) -> dict[str, Any]:
        """Atomically append one exact hold and its unique Outbox event."""

        self.assert_operation_recovery_hold_ready()
        identity = normalize_hold_identity(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            budget_id=budget_id,
        )
        with self._transaction() as conn:
            observations = load_hold_observations(conn, **identity)
            _, subject_json, subject_sha256, case_ref = (
                build_operation_recovery_hold_subject(
                    **observations,
                    reason_code=reason_code,
                    checkpoint_state=checkpoint_state,
                )
            )
            outbox = build_hold_outbox(
                case_ref=case_ref,
                subject_sha256=subject_sha256,
                attempt_id=identity["attempt_id"],
            )
            existing = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE case_ref = ?",
                (case_ref,),
            ).fetchone()
            if existing is not None:
                record = self._validated_hold_record(conn, existing)
                return {**record, "exact_replay": True, "current": True}

            ts_ms = self._now()
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(AGGREGATE, case_ref, 1),
                expected_event_type=EVENT,
                expected_attempt_id=identity["attempt_id"],
            )
            values = hold_row_values(
                observations=observations,
                subject_json=subject_json,
                subject_sha256=subject_sha256,
                case_ref=case_ref,
                outbox_id=outbox.outbox_id,
                reason_code=reason_code,
                checkpoint_state=checkpoint_state,
                created_ts_ms=ts_ms,
            )
            try:
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}(
                        case_ref, schema_version, case_kind, classification,
                        reason_code, checkpoint_state,
                        graph_id, graph_revision, graph_status,
                        graph_status_version, node_id, node_status,
                        node_status_version, attempt_id, attempt_status,
                        attempt_status_version, reservation_id,
                        operation_status, operation_status_version,
                        budget_id, budget_status, budget_status_version,
                        cancellation_intent_ref, cancellation_status,
                        cancellation_status_version, subject_json,
                        subject_sha256, outbox_id, created_ts_ms
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    tuple(values),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "Operation recovery hold binding changed before commit",
                ) from exc
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE case_ref = ?",
                (case_ref,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Operation recovery hold disappeared after insert",
                )
            return {
                **self._validated_hold_record(conn, row),
                "exact_replay": False,
                "current": True,
            }

    def get_operation_recovery_hold(self, case_ref: str) -> dict[str, Any]:
        self.assert_operation_recovery_hold_ready()
        reference = str(case_ref or "").strip()
        if not reference.startswith("operation-recovery-hold://sha256/"):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation recovery hold case_ref is invalid",
            )
        with self._transaction() as conn:
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE case_ref = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Operation recovery hold was not found",
                )
            record = self._validated_hold_record(conn, row)
            return {
                **record,
                "current": hold_is_current(conn, record),
            }

    def list_current_operation_recovery_holds(
        self,
        *,
        graph_id: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        self.assert_operation_recovery_hold_ready()
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation recovery hold limit must be between 1 and 1000",
            )
        graph_ref = str(graph_id or "").strip() or None
        with self._transaction() as conn:
            query = f"SELECT * FROM {_TABLE}"
            parameters: tuple[Any, ...] = ()
            if graph_ref is not None:
                query += " WHERE graph_id = ?"
                parameters = (graph_ref,)
            query += " ORDER BY created_ts_ms, case_ref"
            rows = conn.execute(query, parameters).fetchall()
            current: list[dict[str, Any]] = []
            for row in rows:
                record = self._validated_hold_record(conn, row)
                if hold_is_current(conn, record):
                    current.append({**record, "current": True})
                    if len(current) >= limit:
                        break
            return tuple(current)

    def _validated_hold_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        record = validate_operation_recovery_hold_row(row)
        outbox = build_hold_outbox(
            case_ref=record["case_ref"],
            subject_sha256=record["subject_sha256"],
            attempt_id=record["attempt_id"],
        )
        if outbox.outbox_id != record["outbox_id"]:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Operation recovery hold Outbox identity drifted",
            )
        self._assert_mutation_outbox_replay(
            conn,
            outbox,
            expected_aggregate=(AGGREGATE, record["case_ref"], 1),
            expected_event_type=EVENT,
            expected_attempt_id=record["attempt_id"],
        )
        return record

__all__ = ["TaskOperationRecoveryHoldStoreMixin"]
