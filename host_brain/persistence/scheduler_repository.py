"""Durable Scheduler definition, occurrence, and result repositories.

The clock admits exactly one immutable occurrence for a definition revision and
scheduled instant.  Execution retries are intentionally absent here: Task
Runtime owns attempts, leases, retries, and terminal command state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.persistence.scheduler_clock_batch_store import (
    SchedulerClockBatchStoreMixin,
)
from host_brain.persistence.scheduler_clock_retry_store import (
    SchedulerClockRetryStoreMixin,
)
from host_brain.persistence.scheduler_repository_primitives import (
    SCHEDULER_CLOCK_RETRY_CONFLICT,
    SCHEDULER_CLOCK_RETRY_INVALID,
    SCHEDULER_DEFINITION_INVALID,
    SchedulerRepositoryError,
    _required_int,
    _required_reference,
    _required_task_id,
    _required_text,
)


SCHEDULER_SCHEMA_NOT_READY = "SCHEDULER_SCHEMA_NOT_READY"
SCHEDULER_DEFINITION_CONFLICT = "SCHEDULER_DEFINITION_CONFLICT"
SCHEDULER_DEFINITION_NOT_FOUND = "SCHEDULER_DEFINITION_NOT_FOUND"
SCHEDULER_OCCURRENCE_INVALID = "SCHEDULER_OCCURRENCE_INVALID"
SCHEDULER_OCCURRENCE_CONFLICT = "SCHEDULER_OCCURRENCE_CONFLICT"
SCHEDULER_EXECUTION_RESULT_INVALID = "SCHEDULER_EXECUTION_RESULT_INVALID"
SCHEDULER_EXECUTION_RESULT_CONFLICT = "SCHEDULER_EXECUTION_RESULT_CONFLICT"
SCHEDULER_EXECUTION_RESULT_NOT_FOUND = "SCHEDULER_EXECUTION_RESULT_NOT_FOUND"
SCHEDULER_TERMINAL_OUTCOME_INVALID = "SCHEDULER_TERMINAL_OUTCOME_INVALID"
SCHEDULER_TERMINAL_OUTCOME_CONFLICT = "SCHEDULER_TERMINAL_OUTCOME_CONFLICT"
SCHEDULER_ADMIN_OPERATION_INVALID = "SCHEDULER_ADMIN_OPERATION_INVALID"
SCHEDULER_ADMIN_OPERATION_CONFLICT = "SCHEDULER_ADMIN_OPERATION_CONFLICT"

SCHEDULER_MIGRATION_ASSET_KEY = "0065#scheduler_runtime"
# Frozen to host_brain/audit/migrations/0065_scheduler_runtime.sql.
SCHEDULER_MIGRATION_SHA256 = "447387f6a5b0e085d3e3155baaa25e2164070c6a5875d894903dfd9acdb11c8c"
SCHEDULER_UOW_MIGRATION_ASSET_KEY = "0066#scheduler_durable_uow"
# Frozen to host_brain/audit/migrations/0066_scheduler_durable_uow.sql.
SCHEDULER_UOW_MIGRATION_SHA256 = "c91a8941b5a9abe182399e9408789218cd2fc4614e279ff4a35a578087007c1b"
SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY = "0117#scheduler_clock_retry_authority"
# Frozen to host_brain/audit/migrations/0117_scheduler_clock_retry_authority.sql.
SCHEDULER_CLOCK_RETRY_MIGRATION_SHA256 = "a15ab6660d9daf5b7c6c97321699f3410676772df2e248a31a24a37446d113ed"

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_ALLOWED_TRIGGER_MODES = frozenset({"cron", "interval", "once"})
_ALLOWED_RESULT_STATUSES = frozenset(
    {"EXECUTED", "SUCCEEDED", "FAILED", "BLOCKED", "DEGRADED", "SKIPPED"}
)
_ALLOWED_ADMIN_OPERATIONS = frozenset({"PUBLISH", "SET_ENABLED", "DELETE"})
_ALLOWED_TERMINAL_OUTCOMES = frozenset({"GOVERNOR_REJECTED"})
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|secret|signature|(?:access|refresh|auth|raw)_?token)(?:$|_)|^token$",
    re.IGNORECASE,
)
_REQUIRED_TABLE_COLUMNS = {
    "scheduler_definition_revision": frozenset(
        {
            "task_id",
            "definition_revision",
            "definition_json",
            "definition_digest",
            "trigger_mode",
            "enabled_at_publish",
            "created_at_ms",
        }
    ),
    "scheduler_definition_head": frozenset(
        {
            "task_id",
            "definition_revision",
            "definition_digest",
            "head_version",
            "enabled",
            "retired",
            "next_scheduled_at_ms",
            "updated_at_ms",
        }
    ),
    "scheduler_occurrence": frozenset(
        {
            "occurrence_id",
            "task_id",
            "definition_revision",
            "source",
            "request_id",
            "principal_id",
            "ingress_claim_ref",
            "ingress_audit_ref",
            "body_sha256",
            "scheduled_at_ms",
            "next_scheduled_at_ms",
            "payload_json",
            "payload_digest",
            "created_at_ms",
        }
    ),
    "scheduler_execution_result": frozenset(
        {
            "receipt_ref",
            "occurrence_id",
            "command_id",
            "work_order_id",
            "result_status",
            "result_json",
            "result_digest",
            "created_at_ms",
        }
    ),
    "scheduler_occurrence_terminal_outcome": frozenset(
        {
            "receipt_ref",
            "occurrence_id",
            "outcome_kind",
            "reason_code",
            "outcome_json",
            "outcome_digest",
            "created_at_ms",
        }
    ),
    "scheduler_admin_operation_result": frozenset(
        {
            "receipt_ref",
            "request_id",
            "operation_kind",
            "task_id",
            "principal_id",
            "ingress_claim_ref",
            "ingress_audit_ref",
            "body_sha256",
            "request_json",
            "request_digest",
            "result_json",
            "result_digest",
            "created_at_ms",
        }
    ),
    "scheduler_clock_retry_state": frozenset(
        {
            "retry_key",
            "retry_kind",
            "task_id",
            "occurrence_id",
            "failure_count",
            "next_retry_at_ms",
            "last_reason_code",
            "lease_owner_id",
            "lease_fencing_token",
            "lease_expires_at_ms",
            "updated_at_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_scheduler_definition_head_due",
        "idx_scheduler_occurrence_task_schedule",
        "idx_scheduler_execution_result_status",
        "uq_scheduler_clock_occurrence_fence",
        "idx_scheduler_terminal_outcome_created",
        "idx_scheduler_admin_operation_task",
        "idx_scheduler_clock_retry_due",
        "uq_scheduler_clock_retry_occurrence",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_scheduler_definition_revision_no_update",
        "trg_scheduler_definition_revision_no_delete",
        "trg_scheduler_occurrence_no_update",
        "trg_scheduler_occurrence_no_delete",
        "trg_scheduler_execution_result_no_update",
        "trg_scheduler_execution_result_no_delete",
        "trg_scheduler_terminal_outcome_no_update",
        "trg_scheduler_terminal_outcome_no_delete",
        "trg_scheduler_admin_operation_no_update",
        "trg_scheduler_admin_operation_no_delete",
        "trg_scheduler_terminal_outcome_excludes_result",
        "trg_scheduler_result_excludes_terminal_outcome",
    }
)


class _SchedulerSQLiteRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int],
        timeout_ms: int,
        verify_schema: bool,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_INVALID,
                "scheduler repositories require a durable SQLite file path",
            )
        if not callable(clock):
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
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

    def _now(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_INVALID,
                "clock returned an invalid value",
            ) from exc
        if value < 0:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_INVALID,
                "clock must be non-negative",
            )
        return value

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise SchedulerRepositoryError(
                SCHEDULER_SCHEMA_NOT_READY,
                f"scheduler database does not exist: {self.db_path}",
            )
        try:
            conn = self._connect()
            try:
                assets = conn.execute(
                    """
                    SELECT asset_key, checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key IN (?, ?, ?)
                    """,
                    (
                        SCHEDULER_MIGRATION_ASSET_KEY,
                        SCHEDULER_UOW_MIGRATION_ASSET_KEY,
                        SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY,
                    ),
                ).fetchall()
                columns = {
                    table: {
                        str(row[1])
                        for row in conn.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    }
                    for table in _REQUIRED_TABLE_COLUMNS
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
                foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise SchedulerRepositoryError(
                SCHEDULER_SCHEMA_NOT_READY,
                "scheduler schema cannot be verified",
            ) from exc
        missing_columns = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_TABLE_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        asset_records = {str(row["asset_key"]): row for row in assets}
        expected_assets = {
            SCHEDULER_MIGRATION_ASSET_KEY: SCHEDULER_MIGRATION_SHA256,
            SCHEDULER_UOW_MIGRATION_ASSET_KEY: SCHEDULER_UOW_MIGRATION_SHA256,
            SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY: (
                SCHEDULER_CLOCK_RETRY_MIGRATION_SHA256
            ),
        }
        assets_ready = all(
            key in asset_records
            and str(asset_records[key]["checksum"]) == checksum
            and str(asset_records[key]["disposition"])
            in _ALLOWED_MIGRATION_DISPOSITIONS
            for key, checksum in expected_assets.items()
        )
        if (
            not assets_ready
            or missing_columns
            or not _REQUIRED_INDEXES <= indexes
            or not _REQUIRED_TRIGGERS <= triggers
            or foreign_keys != 1
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_SCHEMA_NOT_READY,
                "scheduler migrations 0065/0066/0117 are missing, stale, or checksum-drifted",
            )


class SchedulerDefinitionRepository(
    SchedulerClockBatchStoreMixin,
    SchedulerClockRetryStoreMixin,
    _SchedulerSQLiteRepository,
):
    """Immutable definition revisions plus one CAS-protected mutable head."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._fault_injector = fault_injector
        super().__init__(
            db_path,
            clock=clock,
            timeout_ms=timeout_ms,
            verify_schema=verify_schema,
        )

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(str(stage))

    def publish_definition(
        self,
        *,
        task_id: str,
        definition: Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
    ) -> dict[str, Any]:
        """Create/revise a definition; an exact current payload is a restart replay."""
        prepared = self._prepare_publish(
            task_id=task_id,
            definition=definition,
            expected_head_version=expected_head_version,
            next_scheduled_at_ms=next_scheduled_at_ms,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self._publish_definition_in_transaction(
                conn,
                prepared=prepared,
                now_value=self._now(),
            )
            conn.commit()
            return result
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "scheduler definition identity or revision conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _prepare_publish(
        *,
        task_id: str,
        definition: Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=0,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        next_due = _optional_ms(
            next_scheduled_at_ms,
            field_name="next_scheduled_at_ms",
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        normalized, trigger_mode, enabled = _normalize_definition(task_ref, definition)
        definition_json = _canonical_json(
            normalized,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        return {
            "task_id": task_ref,
            "expected_head_version": expected,
            "next_scheduled_at_ms": next_due,
            "definition": normalized,
            "definition_json": definition_json,
            "definition_digest": _sha256(definition_json),
            "trigger_mode": trigger_mode,
            "enabled": enabled,
        }

    def _publish_definition_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        prepared: Mapping[str, Any],
        now_value: int,
    ) -> dict[str, Any]:
        task_ref = str(prepared["task_id"])
        expected = int(prepared["expected_head_version"])
        next_due = prepared["next_scheduled_at_ms"]
        definition_json = str(prepared["definition_json"])
        definition_digest = str(prepared["definition_digest"])
        trigger_mode = str(prepared["trigger_mode"])
        enabled = bool(prepared["enabled"])
        existing = self._select_head(conn, task_ref)
        if existing is not None:
            record = self._head_record(existing)
            if record["definition_digest"] == definition_digest:
                if record["next_scheduled_at_ms"] == next_due:
                    return {**record, "created": False, "replayed": True}
                if record["head_version"] != expected:
                    raise SchedulerRepositoryError(
                        SCHEDULER_DEFINITION_CONFLICT,
                        "definition head CAS version is stale",
                    )
                updated = conn.execute(
                    """
                    UPDATE scheduler_definition_head
                    SET head_version = head_version + 1,
                        next_scheduled_at_ms = ?, updated_at_ms = ?
                    WHERE task_id = ? AND definition_revision = ?
                      AND definition_digest = ? AND head_version = ?
                    """,
                    (
                        next_due,
                        now_value,
                        task_ref,
                        int(record["definition_revision"]),
                        definition_digest,
                        expected,
                    ),
                )
                if updated.rowcount != 1:
                    raise SchedulerRepositoryError(
                        SCHEDULER_DEFINITION_CONFLICT,
                        "definition due-pointer CAS update lost",
                    )
                self._fault("after_definition_head")
                row = self._select_head(conn, task_ref)
                if row is None:
                    raise SchedulerRepositoryError(
                        SCHEDULER_DEFINITION_NOT_FOUND,
                        "rescheduled definition head cannot be read back",
                    )
                return {
                    **self._head_record(row),
                    "created": False,
                    "updated": True,
                    "rescheduled": True,
                    "replayed": False,
                }
            if record["head_version"] != expected:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "definition head CAS version is stale",
                )
            revision = int(record["definition_revision"]) + 1
            conn.execute(
                """
                INSERT INTO scheduler_definition_revision(
                    task_id, definition_revision, definition_json,
                    definition_digest, trigger_mode, enabled_at_publish,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_ref,
                    revision,
                    definition_json,
                    definition_digest,
                    trigger_mode,
                    int(enabled),
                    now_value,
                ),
            )
            self._fault("after_definition_revision")
            updated = conn.execute(
                """
                UPDATE scheduler_definition_head
                SET definition_revision = ?, definition_digest = ?,
                    head_version = head_version + 1, enabled = ?, retired = 0,
                    next_scheduled_at_ms = ?, updated_at_ms = ?
                WHERE task_id = ? AND head_version = ?
                """,
                (
                    revision,
                    definition_digest,
                    int(enabled),
                    next_due,
                    now_value,
                    task_ref,
                    expected,
                ),
            )
            if updated.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "definition head CAS update lost",
                )
        else:
            if expected != 0:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "new definition requires expected_head_version=0",
                )
            revision = 1
            conn.execute(
                """
                INSERT INTO scheduler_definition_revision(
                    task_id, definition_revision, definition_json,
                    definition_digest, trigger_mode, enabled_at_publish,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_ref,
                    revision,
                    definition_json,
                    definition_digest,
                    trigger_mode,
                    int(enabled),
                    now_value,
                ),
            )
            self._fault("after_definition_revision")
            conn.execute(
                """
                INSERT INTO scheduler_definition_head(
                    task_id, definition_revision, definition_digest,
                    head_version, enabled, retired,
                    next_scheduled_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, 1, ?, 0, ?, ?)
                """,
                (
                    task_ref,
                    revision,
                    definition_digest,
                    int(enabled),
                    next_due,
                    now_value,
                ),
            )
        self._fault("after_definition_head")
        row = self._select_head(conn, task_ref)
        if row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "committed scheduler definition cannot be read back",
            )
        return {**self._head_record(row), "created": True, "replayed": False}

    def get(self, task_id: str) -> dict[str, Any] | None:
        task_ref = _required_task_id(task_id)
        conn = self._connect()
        try:
            row = self._select_head(conn, task_ref)
            return None if row is None else self._head_record(row)
        finally:
            conn.close()

    def get_revision(self, task_id: str, revision: int) -> dict[str, Any] | None:
        task_ref = _required_task_id(task_id)
        revision_value = _required_int(
            revision,
            field_name="definition_revision",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM scheduler_definition_revision
                WHERE task_id = ? AND definition_revision = ?
                """,
                (task_ref, revision_value),
            ).fetchone()
            return None if row is None else self._revision_record(row)
        finally:
            conn.close()

    def list_definitions(
        self,
        *,
        limit: int = 100,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]:
        limit_value = _bounded_limit(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                self._head_select_sql()
                + (" WHERE h.retired = 0" if not include_retired else "")
                + " ORDER BY h.task_id LIMIT ?",
                (limit_value,),
            ).fetchall()
            return [self._head_record(row) for row in rows]
        finally:
            conn.close()

    def list_due(
        self,
        *,
        due_at_ms: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        due_at = _required_int(
            due_at_ms,
            field_name="due_at_ms",
            minimum=0,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        limit_value = _bounded_limit(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                self._head_select_sql()
                + """
                  WHERE h.retired = 0 AND h.enabled = 1
                    AND h.next_scheduled_at_ms IS NOT NULL
                    AND h.next_scheduled_at_ms <= ?
                  ORDER BY h.next_scheduled_at_ms, h.task_id
                  LIMIT ?
                """,
                (due_at, limit_value),
            ).fetchall()
            return [self._head_record(row) for row in rows]
        finally:
            conn.close()

    def list_pending_occurrences(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return CLOCK occurrences needing recovery before newly-due clock work.

        Manual occurrences are request-owned and must be recovered by the
        manual command lane.  Filtering them after ``LIMIT`` can permanently
        hide an older clock occurrence behind a manual backlog whose head has
        already advanced.
        """

        limit_value = _bounded_limit(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
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
                (limit_value,),
            ).fetchall()
            records: list[dict[str, Any]] = []
            for row in rows:
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
                records.append(record)
            return records
        finally:
            conn.close()

    def compare_and_set_head(
        self,
        *,
        task_id: str,
        expected_head_version: int,
        enabled: bool,
        next_scheduled_at_ms: int | None,
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        enabled_value = _required_bool(enabled, field_name="enabled")
        next_due = _optional_ms(
            next_scheduled_at_ms,
            field_name="next_scheduled_at_ms",
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_head(conn, task_ref)
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_NOT_FOUND,
                    "scheduler definition does not exist",
                )
            current = self._head_record(row)
            if current["retired"]:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "retired scheduler definition cannot be mutated",
                )
            if (
                current["enabled"] is enabled_value
                and current["next_scheduled_at_ms"] == next_due
            ):
                conn.commit()
                return {**current, "updated": False, "replayed": True}
            if current["head_version"] != expected:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "definition head CAS version is stale",
                )
            updated = conn.execute(
                """
                UPDATE scheduler_definition_head
                SET head_version = head_version + 1, enabled = ?,
                    next_scheduled_at_ms = ?, updated_at_ms = ?
                WHERE task_id = ? AND head_version = ? AND retired = 0
                """,
                (int(enabled_value), next_due, now_value, task_ref, expected),
            )
            if updated.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "definition head CAS update lost",
                )
            result = self._head_record(self._select_head(conn, task_ref))
            conn.commit()
            return {**result, "updated": True, "replayed": False}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def set_enabled(
        self,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        enabled_value = _required_bool(enabled, field_name="enabled")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self._set_enabled_in_transaction(
                conn,
                task_id=task_ref,
                enabled=enabled_value,
                expected_head_version=expected,
                now_value=self._now(),
            )
            conn.commit()
            return result
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _set_enabled_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
        now_value: int,
    ) -> dict[str, Any]:
        row = self._select_head(conn, task_id)
        if row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "scheduler definition does not exist",
            )
        current = self._head_record(row)
        if current["retired"]:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "retired scheduler definition cannot be mutated",
            )
        if current["enabled"] is enabled:
            return {**current, "updated": False, "replayed": True}
        if current["head_version"] != expected_head_version:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "definition head CAS version is stale",
            )
        updated = conn.execute(
            """
            UPDATE scheduler_definition_head
            SET head_version = head_version + 1, enabled = ?, updated_at_ms = ?
            WHERE task_id = ? AND head_version = ? AND retired = 0
            """,
            (int(enabled), now_value, task_id, expected_head_version),
        )
        if updated.rowcount != 1:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "definition enabled-state CAS update lost",
            )
        result_row = self._select_head(conn, task_id)
        if result_row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "updated scheduler definition cannot be read back",
            )
        return {**self._head_record(result_row), "updated": True, "replayed": False}

    def delete_definition(
        self,
        *,
        task_id: str,
        expected_head_version: int,
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self._delete_definition_in_transaction(
                conn,
                task_id=task_ref,
                expected_head_version=expected,
                now_value=self._now(),
            )
            conn.commit()
            return result
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _delete_definition_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        expected_head_version: int,
        now_value: int,
    ) -> dict[str, Any]:
        row = self._select_head(conn, task_id)
        if row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "scheduler definition does not exist",
            )
        current = self._head_record(row)
        if current["retired"]:
            return {**current, "deleted": False, "replayed": True}
        if current["head_version"] != expected_head_version:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "definition head CAS version is stale",
            )
        updated = conn.execute(
            """
            UPDATE scheduler_definition_head
            SET head_version = head_version + 1, enabled = 0, retired = 1,
                next_scheduled_at_ms = NULL, updated_at_ms = ?
            WHERE task_id = ? AND head_version = ? AND retired = 0
            """,
            (now_value, task_id, expected_head_version),
        )
        if updated.rowcount != 1:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_CONFLICT,
                "definition delete CAS update lost",
            )
        result_row = self._select_head(conn, task_id)
        if result_row is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "retired scheduler definition cannot be read back",
            )
        return {**self._head_record(result_row), "deleted": True, "replayed": False}

    def resolve_admin_operation(
        self,
        *,
        operation_kind: str,
        task_id: str,
        operation_request: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve and validate an immutable admin result without mutating state."""

        operation, task_ref, request_value, request_json, request_digest, auth = (
            self._prepare_admin_operation(
                operation_kind=operation_kind,
                task_id=task_id,
                operation_request=operation_request,
                authority=authority,
            )
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = self._select_admin_operation(conn, authority=auth)
            if row is None:
                conn.commit()
                return None
            record = self._admin_operation_record(row)
            self._assert_admin_operation_replay(
                record,
                operation_kind=operation,
                task_id=task_ref,
                request_value=request_value,
                request_json=request_json,
                request_digest=request_digest,
                authority=auth,
            )
            self._validate_ingress_authority(
                conn,
                authority=auth,
                request_id=auth["request_id"],
            )
            conn.commit()
            return {**record, "created": False, "replayed": True}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def publish_definition_operation(
        self,
        *,
        task_id: str,
        definition: Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        prepared = self._prepare_publish(
            task_id=task_id,
            definition=definition,
            expected_head_version=expected_head_version,
            next_scheduled_at_ms=next_scheduled_at_ms,
        )
        request_value = {
            "task_id": prepared["task_id"],
            "definition": prepared["definition"],
            "expected_head_version": prepared["expected_head_version"],
            "next_scheduled_at_ms": prepared["next_scheduled_at_ms"],
        }
        return self._run_admin_operation(
            operation_kind="PUBLISH",
            task_id=str(prepared["task_id"]),
            operation_request=request_value,
            authority=authority,
            mutate=lambda conn, now_value: self._publish_definition_in_transaction(
                conn,
                prepared=prepared,
                now_value=now_value,
            ),
        )

    def set_enabled_operation(
        self,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        enabled_value = _required_bool(enabled, field_name="enabled")
        request_value = {
            "task_id": task_ref,
            "enabled": enabled_value,
            "expected_head_version": expected,
        }
        return self._run_admin_operation(
            operation_kind="SET_ENABLED",
            task_id=task_ref,
            operation_request=request_value,
            authority=authority,
            mutate=lambda conn, now_value: self._set_enabled_in_transaction(
                conn,
                task_id=task_ref,
                enabled=enabled_value,
                expected_head_version=expected,
                now_value=now_value,
            ),
        )

    def delete_definition_operation(
        self,
        *,
        task_id: str,
        expected_head_version: int,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        task_ref = _required_task_id(task_id)
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        )
        request_value = {
            "task_id": task_ref,
            "expected_head_version": expected,
        }
        return self._run_admin_operation(
            operation_kind="DELETE",
            task_id=task_ref,
            operation_request=request_value,
            authority=authority,
            mutate=lambda conn, now_value: self._delete_definition_in_transaction(
                conn,
                task_id=task_ref,
                expected_head_version=expected,
                now_value=now_value,
            ),
        )

    def _run_admin_operation(
        self,
        *,
        operation_kind: str,
        task_id: str,
        operation_request: Mapping[str, Any],
        authority: Mapping[str, Any],
        mutate: Callable[[sqlite3.Connection, int], Mapping[str, Any]],
    ) -> dict[str, Any]:
        operation, task_ref, request_value, request_json, request_digest, auth = (
            self._prepare_admin_operation(
                operation_kind=operation_kind,
                task_id=task_id,
                operation_request=operation_request,
                authority=authority,
            )
        )
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._select_admin_operation(conn, authority=auth)
            if existing is not None:
                record = self._admin_operation_record(existing)
                self._assert_admin_operation_replay(
                    record,
                    operation_kind=operation,
                    task_id=task_ref,
                    request_value=request_value,
                    request_json=request_json,
                    request_digest=request_digest,
                    authority=auth,
                )
                self._validate_ingress_authority(
                    conn,
                    authority=auth,
                    request_id=auth["request_id"],
                )
                conn.commit()
                return {**record, "created": False, "replayed": True}

            self._validate_ingress_authority(
                conn,
                authority=auth,
                request_id=auth["request_id"],
            )
            raw_result = mutate(conn, now_value)
            result_value = _safe_mapping(
                raw_result,
                path="admin_operation.result",
                reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
            )
            result_json = _canonical_json(
                result_value,
                reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
            )
            result_digest = _sha256(result_json)
            receipt_ref = _admin_operation_receipt_ref(
                request_id=auth["request_id"],
                operation_kind=operation,
                task_id=task_ref,
                request_digest=request_digest,
                result_digest=result_digest,
            )
            self._fault("after_admin_mutation_before_journal")
            conn.execute(
                """
                INSERT INTO scheduler_admin_operation_result(
                    receipt_ref, request_id, operation_kind, task_id,
                    principal_id, ingress_claim_ref, ingress_audit_ref,
                    body_sha256, request_json, request_digest,
                    result_json, result_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_ref,
                    auth["request_id"],
                    operation,
                    task_ref,
                    auth["principal_id"],
                    auth["claim_ref"],
                    auth["audit_ref"],
                    auth["body_sha256"],
                    request_json,
                    request_digest,
                    result_json,
                    result_digest,
                    now_value,
                ),
            )
            self._fault("after_admin_operation_journal")
            row = conn.execute(
                "SELECT * FROM scheduler_admin_operation_result WHERE receipt_ref = ?",
                (receipt_ref,),
            ).fetchone()
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_ADMIN_OPERATION_CONFLICT,
                    "committed scheduler admin operation cannot be read back",
                )
            record = self._admin_operation_record(row)
            conn.commit()
            return {**record, "created": True, "replayed": False}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_CONFLICT,
                "scheduler admin operation identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _prepare_admin_operation(
        *,
        operation_kind: str,
        task_id: str,
        operation_request: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> tuple[str, str, dict[str, Any], str, str, dict[str, str]]:
        operation = str(operation_kind or "").strip().upper()
        if operation not in _ALLOWED_ADMIN_OPERATIONS:
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_INVALID,
                "scheduler admin operation kind is invalid",
            )
        task_ref = _required_task_id(task_id)
        request_value = _safe_mapping(
            operation_request,
            path="admin_operation.request",
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        )
        if request_value.get("task_id") != task_ref:
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_INVALID,
                "scheduler admin operation task binding differs",
            )
        request_json = _canonical_json(
            request_value,
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        )
        auth = _normalize_admin_authority(authority)
        return (
            operation,
            task_ref,
            request_value,
            request_json,
            _sha256(request_json),
            auth,
        )

    @staticmethod
    def _select_admin_operation(
        conn: sqlite3.Connection,
        *,
        authority: Mapping[str, str],
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            """
            SELECT * FROM scheduler_admin_operation_result
            WHERE request_id = ? OR ingress_claim_ref = ? OR ingress_audit_ref = ?
            ORDER BY created_at_ms, receipt_ref
            LIMIT 2
            """,
            (
                authority["request_id"],
                authority["claim_ref"],
                authority["audit_ref"],
            ),
        ).fetchall()
        if len(rows) > 1:
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_CONFLICT,
                "scheduler admin authority resolves to multiple operation results",
            )
        return rows[0] if rows else None

    @staticmethod
    def _assert_admin_operation_replay(
        record: Mapping[str, Any],
        *,
        operation_kind: str,
        task_id: str,
        request_value: Mapping[str, Any],
        request_json: str,
        request_digest: str,
        authority: Mapping[str, str],
    ) -> None:
        expected = {
            "operation_kind": operation_kind,
            "task_id": task_id,
            "request_id": authority["request_id"],
            "principal_id": authority["principal_id"],
            "ingress_claim_ref": authority["claim_ref"],
            "ingress_audit_ref": authority["audit_ref"],
            "body_sha256": authority["body_sha256"],
            "request": dict(request_value),
            "request_digest": request_digest,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_CONFLICT,
                "scheduler admin operation replay semantics drifted",
            )
        if _canonical_json(
            record["request"],
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ) != request_json:
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_CONFLICT,
                "scheduler admin operation replay encoding drifted",
            )

    @staticmethod
    def _admin_operation_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        request_value = _decode_canonical(
            record["request_json"],
            record["request_digest"],
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
            field_name="admin operation request",
        )
        result_value = _decode_canonical(
            record["result_json"],
            record["result_digest"],
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
            field_name="admin operation result",
        )
        operation = str(record["operation_kind"])
        if operation not in _ALLOWED_ADMIN_OPERATIONS:
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_INVALID,
                "durable scheduler admin operation kind drifted",
            )
        expected_receipt = _admin_operation_receipt_ref(
            request_id=str(record["request_id"]),
            operation_kind=operation,
            task_id=str(record["task_id"]),
            request_digest=str(record["request_digest"]),
            result_digest=str(record["result_digest"]),
        )
        if expected_receipt != str(record["receipt_ref"]):
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_INVALID,
                "durable scheduler admin operation receipt binding drifted",
            )
        return {
            "receipt_ref": str(record["receipt_ref"]),
            "request_id": str(record["request_id"]),
            "operation_kind": operation,
            "task_id": str(record["task_id"]),
            "principal_id": str(record["principal_id"]),
            "ingress_claim_ref": str(record["ingress_claim_ref"]),
            "ingress_audit_ref": str(record["ingress_audit_ref"]),
            "body_sha256": str(record["body_sha256"]),
            "request": request_value,
            "request_digest": str(record["request_digest"]),
            "result": result_value,
            "result_digest": str(record["result_digest"]),
            "created_at_ms": int(record["created_at_ms"]),
        }

    def admit_due_occurrence(
        self,
        *,
        task_id: str,
        definition_revision: int,
        expected_head_version: int,
        scheduled_at_ms: int,
        next_scheduled_at_ms: int | None,
        payload: Mapping[str, Any],
        authority: Mapping[str, Any],
        due_retry_key: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        """Admit a clock occurrence and atomically transfer its DUE lease.

        The returned ``clock_retry_claim`` is already keyed by the immutable
        occurrence id.  Callers must never dispatch with the former DUE claim.
        """

        task_ref = _required_task_id(task_id)
        revision = _required_int(
            definition_revision,
            field_name="definition_revision",
            minimum=1,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        expected = _required_int(
            expected_head_version,
            field_name="expected_head_version",
            minimum=1,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        scheduled_at = _required_int(
            scheduled_at_ms,
            field_name="scheduled_at_ms",
            minimum=0,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        next_due = _optional_ms(
            next_scheduled_at_ms,
            field_name="next_scheduled_at_ms",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        if next_due is not None and next_due <= scheduled_at:
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "next_scheduled_at_ms must advance beyond scheduled_at_ms",
            )
        safe_payload = _safe_mapping(
            payload,
            path="payload",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        payload_json = _canonical_json(
            safe_payload,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        payload_digest = _sha256(payload_json)
        occurrence_id = _occurrence_id(
            source="CLOCK",
            task_id=task_ref,
            definition_revision=revision,
            identity=str(scheduled_at),
        )
        request_id = _clock_request_id(task_ref, revision, scheduled_at)
        authority_value = _normalize_authority(authority, request_id=request_id)
        due_key = _required_text(
            due_retry_key,
            field_name="due_retry_key",
            limit=512,
            reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
        )
        expected_due_key = f"due:{task_ref}:{revision}:{scheduled_at}"
        if due_key != expected_due_key:
            raise SchedulerRepositoryError(
                SCHEDULER_CLOCK_RETRY_CONFLICT,
                "clock admission DUE retry identity drifted",
            )
        lease_owner = _required_text(
            lease_owner_id,
            field_name="lease_owner_id",
            limit=240,
            reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
        )
        lease_fence = _required_int(
            lease_fencing_token,
            field_name="lease_fencing_token",
            minimum=1,
            reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
        )
        lease_duration = _required_int(
            lease_duration_ms,
            field_name="lease_duration_ms",
            minimum=1,
            reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
        )
        return self._admit_occurrence(
            occurrence_id=occurrence_id,
            task_id=task_ref,
            definition_revision=revision,
            source="CLOCK",
            request_id=request_id,
            scheduled_at_ms=scheduled_at,
            next_scheduled_at_ms=next_due,
            payload=safe_payload,
            payload_json=payload_json,
            payload_digest=payload_digest,
            expected_head_version=expected,
            authority=authority_value,
            due_retry_key=due_key,
            lease_owner_id=lease_owner,
            lease_fencing_token=lease_fence,
            lease_duration_ms=lease_duration,
        )

    def admit_manual_occurrence(
        self,
        *,
        task_id: str,
        definition_revision: int,
        request_id: str,
        scheduled_at_ms: int,
        payload: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one idempotent manual occurrence without changing clock state."""

        task_ref = _required_task_id(task_id)
        revision = _required_int(
            definition_revision,
            field_name="definition_revision",
            minimum=1,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        request_ref = _required_text(
            request_id,
            field_name="request_id",
            limit=512,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        scheduled_at = _required_int(
            scheduled_at_ms,
            field_name="scheduled_at_ms",
            minimum=0,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        safe_payload = _safe_mapping(
            payload,
            path="payload",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        payload_json = _canonical_json(
            safe_payload,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        payload_digest = _sha256(payload_json)
        occurrence_id = _occurrence_id(
            source="MANUAL",
            task_id=task_ref,
            definition_revision=revision,
            identity=request_ref,
        )
        authority_value = _normalize_authority(authority, request_id=request_ref)
        return self._admit_occurrence(
            occurrence_id=occurrence_id,
            task_id=task_ref,
            definition_revision=revision,
            source="MANUAL",
            request_id=request_ref,
            scheduled_at_ms=scheduled_at,
            next_scheduled_at_ms=None,
            payload=safe_payload,
            payload_json=payload_json,
            payload_digest=payload_digest,
            expected_head_version=None,
            authority=authority_value,
            due_retry_key=None,
            lease_owner_id=None,
            lease_fencing_token=None,
            lease_duration_ms=None,
        )

    def get_occurrence(self, occurrence_id: str) -> dict[str, Any] | None:
        occurrence_ref = _required_reference(
            occurrence_id,
            field_name="occurrence_id",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM scheduler_occurrence WHERE occurrence_id = ?",
                (occurrence_ref,),
            ).fetchone()
            return None if row is None else self._occurrence_record(row)
        finally:
            conn.close()

    def get_occurrence_by_request(
        self,
        request_id: str,
        *,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        """Load the canonical occurrence before resolving the current revision."""

        request_ref = _required_text(
            request_id,
            field_name="request_id",
            limit=512,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        source_value: str | None = None
        if source is not None:
            source_value = str(source or "").strip().upper()
            if source_value not in {"CLOCK", "MANUAL"}:
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_INVALID,
                    "source must be CLOCK or MANUAL",
                )
        conn = self._connect()
        try:
            if source_value is None:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduler_occurrence
                    WHERE request_id = ?
                    ORDER BY created_at_ms, occurrence_id
                    LIMIT 2
                    """,
                    (request_ref,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduler_occurrence
                    WHERE request_id = ? AND source = ?
                    ORDER BY created_at_ms, occurrence_id
                    LIMIT 2
                    """,
                    (request_ref, source_value),
                ).fetchall()
            if not rows:
                return None
            if len(rows) > 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_CONFLICT,
                    "request_id is ambiguous across scheduler occurrence sources",
                )
            return self._occurrence_record(rows[0])
        finally:
            conn.close()

    def append_terminal_outcome(
        self,
        *,
        occurrence_id: str,
        outcome_kind: str,
        reason_code: str,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        occurrence_ref = _required_reference(
            occurrence_id,
            field_name="occurrence_id",
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
        )
        kind = str(outcome_kind or "").strip().upper()
        if kind not in _ALLOWED_TERMINAL_OUTCOMES:
            raise SchedulerRepositoryError(
                SCHEDULER_TERMINAL_OUTCOME_INVALID,
                "scheduler terminal outcome kind is invalid",
            )
        reason = _required_text(
            reason_code,
            field_name="reason_code",
            limit=160,
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
        )
        outcome_value = _safe_mapping(
            outcome,
            path="terminal_outcome",
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
        )
        if (
            str(outcome_value.get("decision") or "").strip().upper() != "REJECT"
            or str(outcome_value.get("reason_code") or "").strip() != reason
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_TERMINAL_OUTCOME_INVALID,
                "scheduler terminal outcome decision binding is invalid",
            )
        outcome_value["decision"] = "REJECT"
        outcome_value["reason_code"] = reason
        outcome_json = _canonical_json(
            outcome_value,
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
        )
        outcome_digest = _sha256(outcome_json)
        receipt_ref = _terminal_outcome_receipt_ref(
            occurrence_id=occurrence_ref,
            outcome_kind=kind,
            reason_code=reason,
            outcome_digest=outcome_digest,
        )
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            occurrence = conn.execute(
                "SELECT occurrence_id FROM scheduler_occurrence WHERE occurrence_id = ?",
                (occurrence_ref,),
            ).fetchone()
            if occurrence is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_TERMINAL_OUTCOME_INVALID,
                    "scheduler terminal outcome references a missing occurrence",
                )
            existing_result = conn.execute(
                "SELECT receipt_ref FROM scheduler_execution_result WHERE occurrence_id = ?",
                (occurrence_ref,),
            ).fetchone()
            if existing_result is not None:
                raise SchedulerRepositoryError(
                    SCHEDULER_TERMINAL_OUTCOME_CONFLICT,
                    "scheduler occurrence already has an execution result",
                )
            existing = conn.execute(
                """
                SELECT * FROM scheduler_occurrence_terminal_outcome
                WHERE occurrence_id = ? OR receipt_ref = ?
                ORDER BY created_at_ms, receipt_ref
                LIMIT 1
                """,
                (occurrence_ref, receipt_ref),
            ).fetchone()
            if existing is not None:
                record = self._terminal_outcome_record(existing)
                expected = {
                    "receipt_ref": receipt_ref,
                    "occurrence_id": occurrence_ref,
                    "outcome_kind": kind,
                    "reason_code": reason,
                    "outcome": outcome_value,
                    "outcome_digest": outcome_digest,
                }
                if any(record.get(key) != value for key, value in expected.items()):
                    raise SchedulerRepositoryError(
                        SCHEDULER_TERMINAL_OUTCOME_CONFLICT,
                        "scheduler occurrence already owns another terminal outcome",
                    )
                conn.commit()
                return {**record, "created": False, "replayed": True}
            conn.execute(
                """
                INSERT INTO scheduler_occurrence_terminal_outcome(
                    receipt_ref, occurrence_id, outcome_kind, reason_code,
                    outcome_json, outcome_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_ref,
                    occurrence_ref,
                    kind,
                    reason,
                    outcome_json,
                    outcome_digest,
                    now_value,
                ),
            )
            row = conn.execute(
                "SELECT * FROM scheduler_occurrence_terminal_outcome WHERE receipt_ref = ?",
                (receipt_ref,),
            ).fetchone()
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_TERMINAL_OUTCOME_CONFLICT,
                    "committed scheduler terminal outcome cannot be read back",
                )
            record = self._terminal_outcome_record(row)
            conn.commit()
            return {**record, "created": True, "replayed": False}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_TERMINAL_OUTCOME_CONFLICT,
                "scheduler terminal outcome identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_terminal_outcome(self, occurrence_id: str) -> dict[str, Any] | None:
        occurrence_ref = _required_reference(
            occurrence_id,
            field_name="occurrence_id",
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
        )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM scheduler_occurrence_terminal_outcome
                WHERE occurrence_id = ?
                """,
                (occurrence_ref,),
            ).fetchone()
            return None if row is None else self._terminal_outcome_record(row)
        finally:
            conn.close()

    @staticmethod
    def _terminal_outcome_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        outcome = _decode_canonical(
            record["outcome_json"],
            record["outcome_digest"],
            reason_code=SCHEDULER_TERMINAL_OUTCOME_INVALID,
            field_name="terminal outcome",
        )
        kind = str(record["outcome_kind"])
        reason = str(record["reason_code"])
        if (
            kind not in _ALLOWED_TERMINAL_OUTCOMES
            or str(outcome.get("decision") or "").strip().upper() != "REJECT"
            or str(outcome.get("reason_code") or "") != reason
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_TERMINAL_OUTCOME_INVALID,
                "durable scheduler terminal outcome binding drifted",
            )
        expected_receipt = _terminal_outcome_receipt_ref(
            occurrence_id=str(record["occurrence_id"]),
            outcome_kind=kind,
            reason_code=reason,
            outcome_digest=str(record["outcome_digest"]),
        )
        if expected_receipt != str(record["receipt_ref"]):
            raise SchedulerRepositoryError(
                SCHEDULER_TERMINAL_OUTCOME_INVALID,
                "durable scheduler terminal outcome receipt binding drifted",
            )
        return {
            "receipt_ref": str(record["receipt_ref"]),
            "occurrence_id": str(record["occurrence_id"]),
            "outcome_kind": kind,
            "reason_code": reason,
            "outcome": outcome,
            "outcome_digest": str(record["outcome_digest"]),
            "created_at_ms": int(record["created_at_ms"]),
        }

    def _admit_occurrence(
        self,
        *,
        occurrence_id: str,
        task_id: str,
        definition_revision: int,
        source: str,
        request_id: str,
        scheduled_at_ms: int,
        next_scheduled_at_ms: int | None,
        payload: dict[str, Any],
        payload_json: str,
        payload_digest: str,
        expected_head_version: int | None,
        authority: dict[str, str],
        due_retry_key: str | None,
        lease_owner_id: str | None,
        lease_fencing_token: int | None,
        lease_duration_ms: int | None,
    ) -> dict[str, Any]:
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM scheduler_occurrence
                WHERE occurrence_id = ? OR (source = ? AND request_id = ?)
                   OR (
                       task_id = ? AND definition_revision = ?
                       AND scheduled_at_ms = ? AND source = ?
                   )
                ORDER BY created_at_ms, occurrence_id
                LIMIT 1
                """,
                (
                    occurrence_id,
                    source,
                    request_id,
                    task_id,
                    definition_revision,
                    scheduled_at_ms,
                    source,
                ),
            ).fetchone()
            if existing is not None:
                record = self._occurrence_record(existing)
                expected = {
                    "occurrence_id": occurrence_id,
                    "task_id": task_id,
                    "definition_revision": definition_revision,
                    "source": source,
                    "request_id": request_id,
                    "principal_id": authority["principal_id"],
                    "ingress_claim_ref": authority["claim_ref"],
                    "ingress_audit_ref": authority["audit_ref"],
                    "body_sha256": authority["body_sha256"],
                    "scheduled_at_ms": scheduled_at_ms,
                    "next_scheduled_at_ms": next_scheduled_at_ms,
                    "payload_digest": payload_digest,
                    "payload": payload,
                }
                if any(record[key] != value for key, value in expected.items()):
                    raise SchedulerRepositoryError(
                        SCHEDULER_OCCURRENCE_CONFLICT,
                        "scheduler occurrence replay payload drifted",
                    )
                self._validate_ingress_authority(
                    conn,
                    authority=authority,
                    request_id=request_id,
                )
                replay_head = self._select_head(conn, task_id)
                if replay_head is None:
                    raise SchedulerRepositoryError(
                        SCHEDULER_DEFINITION_NOT_FOUND,
                        "scheduler occurrence lost its durable definition head",
                    )
                replay_head_version = self._head_record(replay_head)["head_version"]
                retry_claim = None
                if source == "CLOCK":
                    retry_claim = self._transfer_due_retry_claim(
                        conn,
                        due_retry_key=str(due_retry_key),
                        occurrence_id=occurrence_id,
                        task_id=task_id,
                        owner_id=str(lease_owner_id),
                        fencing_token=int(lease_fencing_token or 0),
                        at_ms=now_value,
                        lease_duration_ms=int(lease_duration_ms or 0),
                    )
                conn.commit()
                replay_result = {
                    **record,
                    "head_version": replay_head_version,
                    "created": False,
                    "replayed": True,
                }
                if retry_claim is not None:
                    replay_result["clock_retry_claim"] = retry_claim
                return replay_result

            self._validate_ingress_authority(
                conn,
                authority=authority,
                request_id=request_id,
            )
            head_row = self._select_head(conn, task_id)
            if head_row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_NOT_FOUND,
                    "scheduler occurrence references a missing definition",
                )
            head = self._head_record(head_row)
            if head["retired"] or not head["enabled"]:
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_CONFLICT,
                    "scheduler definition is not active",
                )
            if head["definition_revision"] != definition_revision:
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_CONFLICT,
                    "scheduler occurrence targets a stale definition revision",
                )
            if source == "CLOCK" and (
                head["head_version"] != expected_head_version
                or head["next_scheduled_at_ms"] != scheduled_at_ms
            ):
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_CONFLICT,
                    "clock occurrence lost its head CAS or due-time fence",
                )

            conn.execute(
                """
                INSERT INTO scheduler_occurrence(
                    occurrence_id, task_id, definition_revision, source,
                    request_id, principal_id, ingress_claim_ref,
                    ingress_audit_ref, body_sha256, scheduled_at_ms,
                    next_scheduled_at_ms, payload_json, payload_digest,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurrence_id,
                    task_id,
                    definition_revision,
                    source,
                    request_id,
                    authority["principal_id"],
                    authority["claim_ref"],
                    authority["audit_ref"],
                    authority["body_sha256"],
                    scheduled_at_ms,
                    next_scheduled_at_ms,
                    payload_json,
                    payload_digest,
                    now_value,
                ),
            )
            self._fault("after_occurrence")
            new_head_version = int(head["head_version"])
            if source == "CLOCK":
                updated = conn.execute(
                    """
                    UPDATE scheduler_definition_head
                    SET head_version = head_version + 1,
                        next_scheduled_at_ms = ?, updated_at_ms = ?
                    WHERE task_id = ? AND definition_revision = ?
                      AND head_version = ? AND enabled = 1 AND retired = 0
                      AND next_scheduled_at_ms = ?
                    """,
                    (
                        next_scheduled_at_ms,
                        now_value,
                        task_id,
                        definition_revision,
                        expected_head_version,
                        scheduled_at_ms,
                    ),
                )
                if updated.rowcount != 1:
                    raise SchedulerRepositoryError(
                        SCHEDULER_OCCURRENCE_CONFLICT,
                        "clock occurrence reschedule CAS update lost",
                    )
                new_head_version += 1
                self._fault("after_reschedule")
            retry_claim = None
            if source == "CLOCK":
                retry_claim = self._transfer_due_retry_claim(
                    conn,
                    due_retry_key=str(due_retry_key),
                    occurrence_id=occurrence_id,
                    task_id=task_id,
                    owner_id=str(lease_owner_id),
                    fencing_token=int(lease_fencing_token or 0),
                    at_ms=now_value,
                    lease_duration_ms=int(lease_duration_ms or 0),
                )
            row = conn.execute(
                "SELECT * FROM scheduler_occurrence WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_OCCURRENCE_CONFLICT,
                    "committed scheduler occurrence cannot be read back",
                )
            result = self._occurrence_record(row)
            conn.commit()
            admitted_result = {
                **result,
                "head_version": new_head_version,
                "created": True,
                "replayed": False,
            }
            if retry_claim is not None:
                admitted_result["clock_retry_claim"] = retry_claim
            return admitted_result
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_CONFLICT,
                "scheduler occurrence identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _validate_ingress_authority(
        conn: sqlite3.Connection,
        *,
        authority: Mapping[str, Any],
        request_id: str,
    ) -> None:
        try:
            claim = conn.execute(
                """
                SELECT principal_id, surface, request_id, body_sha256,
                       claim_status, action, required_scopes_json
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (authority["claim_ref"],),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT request_id, surface, principal_id, body_sha256,
                       decision, reason_code, claim_ref, side_effects_executed,
                       action, required_scopes_json
                FROM secure_ingress_audit
                WHERE audit_ref = ?
                """,
                (authority["audit_ref"],),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise SchedulerRepositoryError(
                SCHEDULER_SCHEMA_NOT_READY,
                "Secure Ingress schema is unavailable for scheduler admission",
            ) from exc
        if claim is None or audit is None:
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "scheduler authority claim or audit is missing",
            )
        command_surface = str(
            authority.get("command_surface") or "scheduler"
        ).strip().lower()
        expected_surface = (
            "obs.command" if command_surface == "operator" else "scheduler.admin"
        )
        expected = (
            authority["principal_id"],
            expected_surface,
            request_id,
            authority["body_sha256"],
        )
        claim_binding = (
            str(claim["principal_id"]),
            str(claim["surface"]),
            str(claim["request_id"]),
            str(claim["body_sha256"]).removeprefix("sha256:"),
        )
        audit_binding = (
            str(audit["principal_id"]),
            str(audit["surface"]),
            str(audit["request_id"]),
            str(audit["body_sha256"]).removeprefix("sha256:"),
        )
        operator_policy_valid = True
        if command_surface == "operator":
            command_id = str(
                authority.get("operator_command_id") or ""
            ).strip()
            expected_action = str(authority.get("action") or "").strip()
            expected_scopes = tuple(
                sorted(
                    str(item).strip()
                    for item in authority.get("required_scopes", ())
                    if str(item).strip()
                )
            )
            try:
                claim_scopes_raw = json.loads(str(claim["required_scopes_json"]))
                audit_scopes_raw = json.loads(str(audit["required_scopes_json"]))
                claim_scopes = tuple(sorted(str(item) for item in claim_scopes_raw))
                audit_scopes = tuple(sorted(str(item) for item in audit_scopes_raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                operator_policy_valid = False
            else:
                operator_policy_valid = (
                    command_id
                    in {
                        "scheduler.task.enable",
                        "scheduler.task.disable",
                        "scheduler.task.trigger",
                    }
                    and expected_action == f"obs:command:{command_id}"
                    and str(claim["action"]) == expected_action
                    and str(audit["action"]) == expected_action
                    and expected_scopes
                    == ("obs:write", "scheduler:write")
                    and claim_scopes == expected_scopes
                    and audit_scopes == expected_scopes
                )
        elif command_surface != "scheduler":
            operator_policy_valid = False
        if (
            claim_binding != expected
            or audit_binding != expected
            or not operator_policy_valid
            or str(claim["claim_status"]) != "COMMITTED"
            or str(audit["decision"]) != "ALLOW"
            or str(audit["reason_code"]) != "SECURE_INGRESS_ALLOWED"
            or str(audit["claim_ref"] or "") != authority["claim_ref"]
            or int(audit["side_effects_executed"]) != 0
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "scheduler authority binding is not an admissible committed claim",
            )

    @staticmethod
    def _head_select_sql() -> str:
        return """
            SELECT r.task_id, r.definition_revision, r.definition_json,
                   r.definition_digest, r.trigger_mode,
                   r.enabled_at_publish, r.created_at_ms,
                   h.head_version, h.enabled, h.retired,
                   h.next_scheduled_at_ms, h.updated_at_ms
            FROM scheduler_definition_head AS h
            JOIN scheduler_definition_revision AS r
              ON r.task_id = h.task_id
             AND r.definition_revision = h.definition_revision
             AND r.definition_digest = h.definition_digest
        """

    @classmethod
    def _select_head(cls, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        return conn.execute(
            cls._head_select_sql() + " WHERE h.task_id = ?",
            (task_id,),
        ).fetchone()

    @staticmethod
    def _revision_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        definition = _decode_canonical(
            record["definition_json"],
            record["definition_digest"],
            reason_code=SCHEDULER_DEFINITION_INVALID,
            field_name="definition",
        )
        task_id = str(record["task_id"])
        if definition.get("task_id") != task_id:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_INVALID,
                "durable definition task binding drifted",
            )
        return {
            "task_id": task_id,
            "definition_revision": int(record["definition_revision"]),
            "definition": definition,
            "definition_digest": str(record["definition_digest"]),
            "trigger_mode": str(record["trigger_mode"]),
            "enabled_at_publish": bool(record["enabled_at_publish"]),
            "created_at_ms": int(record["created_at_ms"]),
        }

    @classmethod
    def _head_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        revision = cls._revision_record(row)
        return {
            **revision,
            "head_version": int(row["head_version"]),
            "enabled": bool(row["enabled"]),
            "retired": bool(row["retired"]),
            "next_scheduled_at_ms": (
                None
                if row["next_scheduled_at_ms"] is None
                else int(row["next_scheduled_at_ms"])
            ),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _occurrence_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        payload = _decode_canonical(
            record["payload_json"],
            record["payload_digest"],
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
            field_name="payload",
        )
        source = str(record["source"])
        task_id = str(record["task_id"])
        revision = int(record["definition_revision"])
        request_id = str(record["request_id"])
        scheduled_at = int(record["scheduled_at_ms"])
        identity = str(scheduled_at) if source == "CLOCK" else request_id
        expected_id = _occurrence_id(
            source=source,
            task_id=task_id,
            definition_revision=revision,
            identity=identity,
        )
        if expected_id != str(record["occurrence_id"]):
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "durable scheduler occurrence identity drifted",
            )
        if source == "CLOCK" and request_id != _clock_request_id(
            task_id,
            revision,
            scheduled_at,
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "durable clock request binding drifted",
            )
        return {
            "occurrence_id": str(record["occurrence_id"]),
            "task_id": task_id,
            "definition_revision": revision,
            "source": source,
            "request_id": request_id,
            "principal_id": _required_text(
                record["principal_id"],
                field_name="principal_id",
                limit=240,
                reason_code=SCHEDULER_OCCURRENCE_INVALID,
            ),
            "ingress_claim_ref": _required_reference(
                record["ingress_claim_ref"],
                field_name="ingress_claim_ref",
                reason_code=SCHEDULER_OCCURRENCE_INVALID,
            ),
            "ingress_audit_ref": _required_reference(
                record["ingress_audit_ref"],
                field_name="ingress_audit_ref",
                reason_code=SCHEDULER_OCCURRENCE_INVALID,
            ),
            "body_sha256": _normalized_sha256(
                record["body_sha256"],
                field_name="body_sha256",
                reason_code=SCHEDULER_OCCURRENCE_INVALID,
            ),
            "authority": {
                "allowed": True,
                "duplicate": False,
                "reason_code": "SECURE_INGRESS_ALLOWED",
                "request_id": request_id,
                "principal_id": str(record["principal_id"]),
                "claim_ref": str(record["ingress_claim_ref"]),
                "audit_ref": str(record["ingress_audit_ref"]),
                "body_sha256": str(record["body_sha256"]),
                "existing_result_ref": None,
            },
            "scheduled_at_ms": scheduled_at,
            "next_scheduled_at_ms": (
                None
                if record["next_scheduled_at_ms"] is None
                else int(record["next_scheduled_at_ms"])
            ),
            "payload": payload,
            "payload_digest": str(record["payload_digest"]),
            "created_at_ms": int(record["created_at_ms"]),
        }


class SchedulerExecutionResultRepository(_SchedulerSQLiteRepository):
    """One immutable result projection per governed Scheduler occurrence."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        super().__init__(
            db_path,
            clock=clock,
            timeout_ms=timeout_ms,
            verify_schema=verify_schema,
        )

    def append(
        self,
        *,
        occurrence_id: str,
        command_id: str,
        work_order_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        occurrence_ref = _required_reference(
            occurrence_id,
            field_name="occurrence_id",
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        command_ref = _required_text(
            command_id,
            field_name="command_id",
            limit=512,
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        work_ref = _required_text(
            work_order_id,
            field_name="work_order_id",
            limit=240,
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        safe_result = _safe_mapping(
            result,
            path="result",
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        status = str(safe_result.get("status") or "").strip().upper()
        if status not in _ALLOWED_RESULT_STATUSES:
            raise SchedulerRepositoryError(
                SCHEDULER_EXECUTION_RESULT_INVALID,
                f"unsupported scheduler result status: {status or 'missing'}",
            )
        safe_result["status"] = status
        result_json = _canonical_json(
            safe_result,
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        if len(result_json.encode("utf-8")) > 16_777_216:
            raise SchedulerRepositoryError(
                SCHEDULER_EXECUTION_RESULT_INVALID,
                "scheduler result exceeds the durable size limit",
            )
        result_digest = _sha256(result_json)
        receipt_ref = _result_receipt_ref(
            occurrence_ref,
            command_ref,
            result_digest,
        )
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM scheduler_execution_result
                WHERE receipt_ref = ? OR occurrence_id = ?
                   OR command_id = ? OR work_order_id = ?
                ORDER BY created_at_ms, receipt_ref
                LIMIT 1
                """,
                (receipt_ref, occurrence_ref, command_ref, work_ref),
            ).fetchone()
            if existing is not None:
                record = self._result_record(existing)
                expected = {
                    "receipt_ref": receipt_ref,
                    "occurrence_id": occurrence_ref,
                    "command_id": command_ref,
                    "work_order_id": work_ref,
                    "result_status": status,
                    "result_digest": result_digest,
                    "result": safe_result,
                }
                if any(record[key] != value for key, value in expected.items()):
                    raise SchedulerRepositoryError(
                        SCHEDULER_EXECUTION_RESULT_CONFLICT,
                        "scheduler occurrence or command already owns another result",
                    )
                conn.commit()
                return {**record, "created": False, "replayed": True}
            conn.execute(
                """
                INSERT INTO scheduler_execution_result(
                    receipt_ref, occurrence_id, command_id, work_order_id,
                    result_status, result_json, result_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_ref,
                    occurrence_ref,
                    command_ref,
                    work_ref,
                    status,
                    result_json,
                    result_digest,
                    now_value,
                ),
            )
            row = conn.execute(
                "SELECT * FROM scheduler_execution_result WHERE receipt_ref = ?",
                (receipt_ref,),
            ).fetchone()
            if row is None:
                raise SchedulerRepositoryError(
                    SCHEDULER_EXECUTION_RESULT_NOT_FOUND,
                    "committed scheduler result cannot be read back",
                )
            record = self._result_record(row)
            conn.commit()
            return {**record, "created": True, "replayed": False}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SchedulerRepositoryError(
                SCHEDULER_EXECUTION_RESULT_CONFLICT,
                "scheduler result identity or foreign key conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, receipt_ref: str) -> dict[str, Any] | None:
        receipt = _required_reference(
            receipt_ref,
            field_name="receipt_ref",
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        return self._get_by("receipt_ref", receipt)

    def get_by_occurrence(self, occurrence_id: str) -> dict[str, Any] | None:
        occurrence = _required_reference(
            occurrence_id,
            field_name="occurrence_id",
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        return self._get_by("occurrence_id", occurrence)

    def get_by_command(self, command_id: str) -> dict[str, Any] | None:
        command = _required_text(
            command_id,
            field_name="command_id",
            limit=512,
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
        )
        return self._get_by("command_id", command)

    def _get_by(self, column: str, value: str) -> dict[str, Any] | None:
        if column not in {"receipt_ref", "occurrence_id", "command_id"}:
            raise AssertionError("invalid scheduler result lookup column")
        conn = self._connect()
        try:
            row = conn.execute(
                f'SELECT * FROM scheduler_execution_result WHERE "{column}" = ?',
                (value,),
            ).fetchone()
            return None if row is None else self._result_record(row)
        finally:
            conn.close()

    @staticmethod
    def _result_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        result = _decode_canonical(
            record["result_json"],
            record["result_digest"],
            reason_code=SCHEDULER_EXECUTION_RESULT_INVALID,
            field_name="result",
        )
        expected_receipt = _result_receipt_ref(
            str(record["occurrence_id"]),
            str(record["command_id"]),
            str(record["result_digest"]),
        )
        if expected_receipt != str(record["receipt_ref"]):
            raise SchedulerRepositoryError(
                SCHEDULER_EXECUTION_RESULT_INVALID,
                "durable scheduler result receipt binding drifted",
            )
        status = str(record["result_status"])
        if str(result.get("status") or "").upper() != status:
            raise SchedulerRepositoryError(
                SCHEDULER_EXECUTION_RESULT_INVALID,
                "durable scheduler result status binding drifted",
            )
        return {
            "receipt_ref": str(record["receipt_ref"]),
            "occurrence_id": str(record["occurrence_id"]),
            "command_id": str(record["command_id"]),
            "work_order_id": str(record["work_order_id"]),
            "result_status": status,
            "result": result,
            "result_digest": str(record["result_digest"]),
            "created_at_ms": int(record["created_at_ms"]),
        }


def _normalize_authority(
    authority: Mapping[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    if not isinstance(authority, Mapping):
        raise SchedulerRepositoryError(
            SCHEDULER_OCCURRENCE_INVALID,
            "admissible secret-free scheduler authority is required",
        )
    data = dict(authority)
    if not bool(data.get("allowed") or data.get("duplicate")):
        raise SchedulerRepositoryError(
            SCHEDULER_OCCURRENCE_INVALID,
            "scheduler authority is not admissible",
        )
    authority_request = _required_text(
        data.get("request_id"),
        field_name="authority.request_id",
        limit=512,
        reason_code=SCHEDULER_OCCURRENCE_INVALID,
    )
    if authority_request != request_id:
        raise SchedulerRepositoryError(
            SCHEDULER_OCCURRENCE_INVALID,
            "scheduler authority request_id differs from the occurrence",
        )
    normalized: dict[str, Any] = {
        "request_id": authority_request,
        "principal_id": _required_text(
            data.get("principal_id"),
            field_name="authority.principal_id",
            limit=240,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        ),
        "claim_ref": _required_reference(
            data.get("claim_ref", data.get("ingress_claim_ref")),
            field_name="authority.claim_ref",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        ),
        "audit_ref": _required_reference(
            data.get("audit_ref", data.get("ingress_audit_ref")),
            field_name="authority.audit_ref",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        ),
        "body_sha256": _normalized_sha256(
            data.get("body_sha256"),
            field_name="authority.body_sha256",
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        ),
    }
    command_surface = str(data.get("command_surface") or "").strip().lower()
    if command_surface:
        normalized["command_surface"] = command_surface
    if command_surface == "operator":
        normalized["operator_command_id"] = _required_text(
            data.get("operator_command_id"),
            field_name="authority.operator_command_id",
            limit=160,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        normalized["action"] = _required_text(
            data.get("action"),
            field_name="authority.action",
            limit=240,
            reason_code=SCHEDULER_OCCURRENCE_INVALID,
        )
        raw_scopes = data.get("required_scopes")
        if not isinstance(raw_scopes, (tuple, list, set, frozenset)):
            raise SchedulerRepositoryError(
                SCHEDULER_OCCURRENCE_INVALID,
                "Operator scheduler scopes are invalid",
            )
        normalized["required_scopes"] = tuple(
            sorted(
                _required_text(
                    item,
                    field_name="authority.required_scope",
                    limit=160,
                    reason_code=SCHEDULER_OCCURRENCE_INVALID,
                )
                for item in raw_scopes
            )
        )
    return normalized


def _normalize_admin_authority(authority: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(authority, Mapping):
        raise SchedulerRepositoryError(
            SCHEDULER_ADMIN_OPERATION_INVALID,
            "admissible secret-free scheduler admin authority is required",
        )
    data = dict(authority)
    if not bool(data.get("allowed") or data.get("duplicate")):
        raise SchedulerRepositoryError(
            SCHEDULER_ADMIN_OPERATION_INVALID,
            "scheduler admin authority is not admissible",
        )
    normalized: dict[str, Any] = {
        "request_id": _required_text(
            data.get("request_id"),
            field_name="authority.request_id",
            limit=512,
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ),
        "principal_id": _required_text(
            data.get("principal_id"),
            field_name="authority.principal_id",
            limit=240,
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ),
        "claim_ref": _required_reference(
            data.get("claim_ref", data.get("ingress_claim_ref")),
            field_name="authority.claim_ref",
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ),
        "audit_ref": _required_reference(
            data.get("audit_ref", data.get("ingress_audit_ref")),
            field_name="authority.audit_ref",
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ),
        "body_sha256": _normalized_sha256(
            data.get("body_sha256"),
            field_name="authority.body_sha256",
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        ),
    }
    command_surface = str(data.get("command_surface") or "").strip().lower()
    if command_surface:
        normalized["command_surface"] = command_surface
    if command_surface == "operator":
        normalized["operator_command_id"] = _required_text(
            data.get("operator_command_id"),
            field_name="authority.operator_command_id",
            limit=160,
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        )
        normalized["action"] = _required_text(
            data.get("action"),
            field_name="authority.action",
            limit=240,
            reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
        )
        raw_scopes = data.get("required_scopes")
        if not isinstance(raw_scopes, (tuple, list, set, frozenset)):
            raise SchedulerRepositoryError(
                SCHEDULER_ADMIN_OPERATION_INVALID,
                "Operator scheduler admin scopes are invalid",
            )
        normalized["required_scopes"] = tuple(
            sorted(
                _required_text(
                    item,
                    field_name="authority.required_scope",
                    limit=160,
                    reason_code=SCHEDULER_ADMIN_OPERATION_INVALID,
                )
                for item in raw_scopes
            )
        )
    return normalized


def _normalize_definition(
    task_id: str,
    definition: Mapping[str, Any],
) -> tuple[dict[str, Any], str, bool]:
    normalized = _safe_mapping(
        definition,
        path="definition",
        reason_code=SCHEDULER_DEFINITION_INVALID,
    )
    supplied_task = normalized.get("task_id")
    if supplied_task is not None and str(supplied_task).strip() != task_id:
        raise SchedulerRepositoryError(
            SCHEDULER_DEFINITION_INVALID,
            "definition.task_id does not match task_id",
        )
    trigger_mode = str(normalized.get("trigger_mode") or "").strip().lower()
    if trigger_mode not in _ALLOWED_TRIGGER_MODES:
        raise SchedulerRepositoryError(
            SCHEDULER_DEFINITION_INVALID,
            "definition.trigger_mode must be cron, interval, or once",
        )
    enabled = _required_bool(normalized.get("enabled", True), field_name="definition.enabled")
    normalized["task_id"] = task_id
    normalized["trigger_mode"] = trigger_mode
    normalized["enabled"] = enabled
    return normalized, trigger_mode, enabled


def _safe_mapping(
    value: Mapping[str, Any],
    *,
    path: str,
    reason_code: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SchedulerRepositoryError(reason_code, f"{path} must be a mapping")
    safe = _safe_value(value, path=path, reason_code=reason_code)
    if not isinstance(safe, dict):
        raise SchedulerRepositoryError(reason_code, f"{path} must be a mapping")
    return safe


def _safe_value(value: Any, *, path: str, reason_code: str) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise SchedulerRepositoryError(
                    reason_code,
                    f"{path} contains an invalid key",
                )
            key = raw_key.strip()
            if _FORBIDDEN_KEY_RE.search(key) and not key.lower().endswith("_ref"):
                raise SchedulerRepositoryError(
                    reason_code,
                    f"secret-like field is forbidden: {path}.{key}",
                )
            safe[key] = _safe_value(
                raw_value,
                path=f"{path}.{key}",
                reason_code=reason_code,
            )
        return safe
    if isinstance(value, (list, tuple)):
        return [
            _safe_value(item, path=f"{path}[]", reason_code=reason_code)
            for item in value
        ]
    if isinstance(value, Enum):
        return _safe_value(value.value, path=path, reason_code=reason_code)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise SchedulerRepositoryError(
        reason_code,
        f"{path} is not canonical JSON data",
    )


def _canonical_json(value: Mapping[str, Any], *, reason_code: str) -> str:
    try:
        return json.dumps(
            deepcopy(dict(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SchedulerRepositoryError(
            reason_code,
            "payload is not finite canonical JSON",
        ) from exc


def _decode_canonical(
    raw_json: Any,
    digest: Any,
    *,
    reason_code: str,
    field_name: str,
) -> dict[str, Any]:
    try:
        decoded = json.loads(str(raw_json))
    except (TypeError, json.JSONDecodeError) as exc:
        raise SchedulerRepositoryError(
            reason_code,
            f"durable {field_name} JSON is invalid",
        ) from exc
    if not isinstance(decoded, dict):
        raise SchedulerRepositoryError(
            reason_code,
            f"durable {field_name} must be an object",
        )
    canonical = _canonical_json(decoded, reason_code=reason_code)
    if _sha256(canonical) != str(digest):
        raise SchedulerRepositoryError(
            reason_code,
            f"durable {field_name} digest mismatch",
        )
    return decoded


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _occurrence_id(
    *,
    source: str,
    task_id: str,
    definition_revision: int,
    identity: str,
) -> str:
    material = json.dumps(
        {
            "definition_revision": definition_revision,
            "identity": identity,
            "source": source,
            "task_id": task_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"scheduleroccurrence://sha256/{digest}"


def _clock_request_id(task_id: str, revision: int, scheduled_at_ms: int) -> str:
    material = json.dumps(
        {
            "definition_revision": revision,
            "scheduled_at_epoch_ms": scheduled_at_ms,
            "schema_version": "natsume.scheduler.clock-authority.v1",
            "task_id": task_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]
    return f"scheduler.clock.request.{digest}"


def _result_receipt_ref(
    occurrence_id: str,
    command_id: str,
    result_digest: str,
) -> str:
    digest = hashlib.sha256(
        f"{occurrence_id}\n{command_id}\n{result_digest}".encode("utf-8")
    ).hexdigest()
    return f"schedulerresult://sha256/{digest}"


def _terminal_outcome_receipt_ref(
    *,
    occurrence_id: str,
    outcome_kind: str,
    reason_code: str,
    outcome_digest: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{occurrence_id}\n{outcome_kind}\n{reason_code}\n"
            f"{outcome_digest}"
        ).encode("utf-8")
    ).hexdigest()
    return f"schedulerterminal://sha256/{digest}"


def _admin_operation_receipt_ref(
    *,
    request_id: str,
    operation_kind: str,
    task_id: str,
    request_digest: str,
    result_digest: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{request_id}\n{operation_kind}\n{task_id}\n"
            f"{request_digest}\n{result_digest}"
        ).encode("utf-8")
    ).hexdigest()
    return f"scheduleradminresult://sha256/{digest}"


def _normalized_sha256(
    value: Any,
    *,
    field_name: str,
    reason_code: str,
) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} must be a SHA-256 digest",
        )
    return normalized.removeprefix("sha256:")


def _required_bool(value: Any, *, field_name: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise SchedulerRepositoryError(
        SCHEDULER_DEFINITION_INVALID,
        f"{field_name} must be boolean",
    )


def _optional_ms(value: Any, *, field_name: str, reason_code: str) -> int | None:
    if value is None:
        return None
    return _required_int(
        value,
        field_name=field_name,
        minimum=0,
        reason_code=reason_code,
    )


def _bounded_limit(value: Any) -> int:
    return min(
        10_000,
        _required_int(
            value,
            field_name="limit",
            minimum=1,
            reason_code=SCHEDULER_DEFINITION_INVALID,
        ),
    )


__all__ = [
    "SCHEDULER_ADMIN_OPERATION_CONFLICT",
    "SCHEDULER_ADMIN_OPERATION_INVALID",
    "SCHEDULER_CLOCK_RETRY_CONFLICT",
    "SCHEDULER_CLOCK_RETRY_INVALID",
    "SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY",
    "SCHEDULER_CLOCK_RETRY_MIGRATION_SHA256",
    "SCHEDULER_DEFINITION_CONFLICT",
    "SCHEDULER_DEFINITION_INVALID",
    "SCHEDULER_DEFINITION_NOT_FOUND",
    "SCHEDULER_EXECUTION_RESULT_CONFLICT",
    "SCHEDULER_EXECUTION_RESULT_INVALID",
    "SCHEDULER_EXECUTION_RESULT_NOT_FOUND",
    "SCHEDULER_MIGRATION_ASSET_KEY",
    "SCHEDULER_MIGRATION_SHA256",
    "SCHEDULER_UOW_MIGRATION_ASSET_KEY",
    "SCHEDULER_UOW_MIGRATION_SHA256",
    "SCHEDULER_OCCURRENCE_CONFLICT",
    "SCHEDULER_OCCURRENCE_INVALID",
    "SCHEDULER_SCHEMA_NOT_READY",
    "SCHEDULER_TERMINAL_OUTCOME_CONFLICT",
    "SCHEDULER_TERMINAL_OUTCOME_INVALID",
    "SchedulerDefinitionRepository",
    "SchedulerExecutionResultRepository",
    "SchedulerRepositoryError",
]
