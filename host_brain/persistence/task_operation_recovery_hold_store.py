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

    @staticmethod
    def _operation_recovery_hold_schema_available(
        conn: sqlite3.Connection,
    ) -> bool:
        """Keep the old 0052/0054 Store contract readable during upgrades."""

        return (
            conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (_TABLE,),
            ).fetchone()
            is not None
        )

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
            return self._append_operation_recovery_hold_in_transaction(
                conn,
                identity=identity,
                reason_code=reason_code,
                checkpoint_state=checkpoint_state,
            )

    def _record_operation_recovery_hold_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        reservation_id: str,
        reason_code: str,
        checkpoint_state: str | None = None,
    ) -> dict[str, Any] | None:
        """Append the hold inside the mutation that first fences the Operation.

        Databases that intentionally expose only the historical 0052/0054
        contract remain readable.  A current production schema has 0130 and
        therefore must provide one exact graph Budget and complete lineage.
        """

        if not self._operation_recovery_hold_schema_available(conn):
            return None
        reservation_ref = str(reservation_id or "").strip()
        operation = self._require_operation_reservation(conn, reservation_ref)
        attempt = conn.execute(
            """
            SELECT * FROM task_runtime_attempt
            WHERE attempt_id = ?
            """,
            (str(operation["attempt_id"] or ""),),
        ).fetchone()
        if attempt is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Operation recovery hold Attempt lineage was not found",
            )
        budgets = conn.execute(
            """
            SELECT budget_id
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
              AND node_id IS NULL AND attempt_id IS NULL
            ORDER BY budget_id
            """,
            (str(attempt["graph_id"]), int(attempt["graph_revision"])),
        ).fetchall()
        if len(budgets) != 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Operation recovery hold requires one exact graph Budget",
            )
        identity = normalize_hold_identity(
            graph_id=str(attempt["graph_id"]),
            graph_revision=int(attempt["graph_revision"]),
            node_id=str(attempt["node_id"]),
            attempt_id=str(attempt["attempt_id"]),
            reservation_id=reservation_ref,
            budget_id=str(budgets[0]["budget_id"]),
        )
        return self._append_operation_recovery_hold_in_transaction(
            conn,
            identity=identity,
            reason_code=reason_code,
            checkpoint_state=checkpoint_state,
        )

    def _append_operation_recovery_hold_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        identity: dict[str, Any],
        reason_code: str,
        checkpoint_state: str | None,
    ) -> dict[str, Any]:
        observations = load_hold_observations(conn, **identity)
        # A nonterminal Operation ambiguity belongs to the immutable
        # Operation status version.  Lease expiry and Budget settlement may
        # advance after the interruption, but they must not manufacture a
        # second simultaneously-current reconciliation authority for that
        # same version.
        if checkpoint_state is None:
            operation = observations["operation"]
            rows = conn.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE reservation_id = ? AND operation_status_version = ?
                ORDER BY created_ts_ms, case_ref
                """,
                (
                    identity["reservation_id"],
                    int(operation["status_version"]),
                ),
            ).fetchall()
            current = []
            for row in rows:
                record = self._validated_hold_record(conn, row)
                if hold_is_current(conn, record):
                    current.append(record)
            if len(current) > 1:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Operation version has multiple current recovery holds",
                )
            if current:
                existing = current[0]
                if (
                    str(existing["reason_code"])
                    != str(reason_code or "").strip().upper()
                    or str(existing["attempt_id"]) != identity["attempt_id"]
                    or str(existing["node_id"]) != identity["node_id"]
                    or str(existing["budget_id"]) != identity["budget_id"]
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "Operation recovery hold authority changed before replay",
                    )
                return {
                    **existing,
                    "exact_replay": True,
                    "current": True,
                }
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
            return {
                **record,
                "exact_replay": True,
                "current": hold_is_current(conn, record),
            }

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

    def get_current_operation_recovery_hold_for_reservation(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        """Read the one case atomically created with an interruption marker."""

        self.assert_operation_recovery_hold_ready()
        reservation_ref = str(reservation_id or "").strip()
        reason = str(reason_code or "").strip().upper()
        if (
            not reservation_ref
            or type(expected_version) is not int
            or expected_version < 1
            or not reason
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation recovery hold lookup identity is invalid",
            )
        with self._transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE reservation_id = ? AND operation_status_version = ?
                ORDER BY created_ts_ms, case_ref
                """,
                (reservation_ref, expected_version),
            ).fetchall()
            current = []
            for row in rows:
                record = self._validated_hold_record(conn, row)
                if hold_is_current(conn, record):
                    current.append(record)
            if (
                len(current) != 1
                or str(current[0]["reason_code"]) != reason
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Operation interruption is missing its one exact current "
                    "recovery hold",
                )
            return {**current[0], "current": True}

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
