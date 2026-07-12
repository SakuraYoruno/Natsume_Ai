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


SCHEDULER_SCHEMA_NOT_READY = "SCHEDULER_SCHEMA_NOT_READY"
SCHEDULER_DEFINITION_INVALID = "SCHEDULER_DEFINITION_INVALID"
SCHEDULER_DEFINITION_CONFLICT = "SCHEDULER_DEFINITION_CONFLICT"
SCHEDULER_DEFINITION_NOT_FOUND = "SCHEDULER_DEFINITION_NOT_FOUND"
SCHEDULER_OCCURRENCE_INVALID = "SCHEDULER_OCCURRENCE_INVALID"
SCHEDULER_OCCURRENCE_CONFLICT = "SCHEDULER_OCCURRENCE_CONFLICT"
SCHEDULER_EXECUTION_RESULT_INVALID = "SCHEDULER_EXECUTION_RESULT_INVALID"
SCHEDULER_EXECUTION_RESULT_CONFLICT = "SCHEDULER_EXECUTION_RESULT_CONFLICT"
SCHEDULER_EXECUTION_RESULT_NOT_FOUND = "SCHEDULER_EXECUTION_RESULT_NOT_FOUND"

SCHEDULER_MIGRATION_ASSET_KEY = "0065#scheduler_runtime"
# Frozen to host_brain/audit/migrations/0065_scheduler_runtime.sql.
SCHEDULER_MIGRATION_SHA256 = "447387f6a5b0e085d3e3155baaa25e2164070c6a5875d894903dfd9acdb11c8c"

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_ALLOWED_TRIGGER_MODES = frozenset({"cron", "interval", "once"})
_ALLOWED_RESULT_STATUSES = frozenset(
    {"EXECUTED", "SUCCEEDED", "FAILED", "BLOCKED", "DEGRADED", "SKIPPED"}
)
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,475}$"
)
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
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_scheduler_definition_head_due",
        "idx_scheduler_occurrence_task_schedule",
        "idx_scheduler_execution_result_status",
        "uq_scheduler_clock_occurrence_fence",
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
    }
)


class SchedulerRepositoryError(RuntimeError):
    """Fail-closed Scheduler persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_DEFINITION_INVALID)
        self.detail = str(detail or "scheduler persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


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
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (SCHEDULER_MIGRATION_ASSET_KEY,),
                ).fetchone()
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
        if (
            asset is None
            or str(asset["checksum"]) != SCHEDULER_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or missing_columns
            or not _REQUIRED_INDEXES <= indexes
            or not _REQUIRED_TRIGGERS <= triggers
            or foreign_keys != 1
        ):
            raise SchedulerRepositoryError(
                SCHEDULER_SCHEMA_NOT_READY,
                "scheduler migration 0065 is missing, stale, or checksum-drifted",
            )


class SchedulerDefinitionRepository(_SchedulerSQLiteRepository):
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
        definition_digest = _sha256(definition_json)
        now_value = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._select_head(conn, task_ref)
            if existing is not None:
                record = self._head_record(existing)
                if record["definition_digest"] == definition_digest:
                    if record["next_scheduled_at_ms"] == next_due:
                        conn.commit()
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
                    result = self._head_record(row)
                    conn.commit()
                    return {
                        **result,
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
            result = self._head_record(row)
            conn.commit()
            return {**result, "created": True, "replayed": False}
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
                SELECT o.*, r.receipt_ref AS existing_result_ref,
                       c.result_ref AS claim_result_ref
                FROM scheduler_occurrence AS o
                LEFT JOIN scheduler_execution_result AS r
                  ON r.occurrence_id = o.occurrence_id
                LEFT JOIN secure_ingress_idempotency_claim AS c
                  ON c.claim_ref = o.ingress_claim_ref
                WHERE o.source = 'CLOCK'
                  AND (
                       r.receipt_ref IS NULL
                    OR c.result_ref IS NULL
                    OR c.result_ref != r.receipt_ref
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
        current = self.get(task_id)
        if current is None:
            raise SchedulerRepositoryError(
                SCHEDULER_DEFINITION_NOT_FOUND,
                "scheduler definition does not exist",
            )
        return self.compare_and_set_head(
            task_id=task_id,
            expected_head_version=expected_head_version,
            enabled=enabled,
            next_scheduled_at_ms=current["next_scheduled_at_ms"],
        )

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
                conn.commit()
                return {**current, "deleted": False, "replayed": True}
            if current["head_version"] != expected:
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
                (now_value, task_ref, expected),
            )
            if updated.rowcount != 1:
                raise SchedulerRepositoryError(
                    SCHEDULER_DEFINITION_CONFLICT,
                    "definition delete CAS update lost",
                )
            result = self._head_record(self._select_head(conn, task_ref))
            conn.commit()
            return {**result, "deleted": True, "replayed": False}
        except SchedulerRepositoryError:
            conn.rollback()
            raise
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

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
    ) -> dict[str, Any]:
        """Atomically append one clock occurrence and advance its due pointer."""

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
                conn.commit()
                return {
                    **record,
                    "head_version": replay_head_version,
                    "created": False,
                    "replayed": True,
                }

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
            return {
                **result,
                "head_version": new_head_version,
                "created": True,
                "replayed": False,
            }
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
        authority: Mapping[str, str],
        request_id: str,
    ) -> None:
        try:
            claim = conn.execute(
                """
                SELECT principal_id, surface, request_id, body_sha256,
                       claim_status
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (authority["claim_ref"],),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT request_id, surface, principal_id, body_sha256,
                       decision, reason_code, claim_ref, side_effects_executed
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
        expected = (
            authority["principal_id"],
            "scheduler.admin",
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
        if (
            claim_binding != expected
            or audit_binding != expected
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
) -> dict[str, str]:
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
    return {
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


def _required_task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    if _TASK_ID_RE.fullmatch(task_id) is None:
        raise SchedulerRepositoryError(
            SCHEDULER_DEFINITION_INVALID,
            "task_id is invalid",
        )
    return task_id


def _required_reference(
    value: Any,
    *,
    field_name: str,
    reason_code: str,
) -> str:
    normalized = _required_text(
        value,
        field_name=field_name,
        limit=512,
        reason_code=reason_code,
    )
    if _REFERENCE_RE.fullmatch(normalized) is None:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} is not a durable reference",
        )
    return normalized


def _required_text(
    value: Any,
    *,
    field_name: str,
    limit: int,
    reason_code: str,
) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} is missing or too long",
        )
    return normalized


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


def _required_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    reason_code: str,
) -> int:
    if type(value) is not int or value < minimum:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} must be an integer >= {minimum}",
        )
    return value


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
    "SCHEDULER_DEFINITION_CONFLICT",
    "SCHEDULER_DEFINITION_INVALID",
    "SCHEDULER_DEFINITION_NOT_FOUND",
    "SCHEDULER_EXECUTION_RESULT_CONFLICT",
    "SCHEDULER_EXECUTION_RESULT_INVALID",
    "SCHEDULER_EXECUTION_RESULT_NOT_FOUND",
    "SCHEDULER_MIGRATION_ASSET_KEY",
    "SCHEDULER_MIGRATION_SHA256",
    "SCHEDULER_OCCURRENCE_CONFLICT",
    "SCHEDULER_OCCURRENCE_INVALID",
    "SCHEDULER_SCHEMA_NOT_READY",
    "SchedulerDefinitionRepository",
    "SchedulerExecutionResultRepository",
    "SchedulerRepositoryError",
]
