"""SQLite authority for the Runtime Event journal and Task projection delivery.

Only this persistence adapter owns SQLite connections for the Runtime Event
slice.  ``runtime_event_timeline`` is the canonical append-only journal;
0068 atomically derives a delivery outbox row for every Task Runtime event.
Projection state is disposable and can be rebuilt from that journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Iterator

from host_brain.core.ids import now_ms


TASK_RUNTIME_PROJECTION = "task_runtime_latest_v1"

RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY = "RUNTIME_EVENT_SCHEMA_NOT_READY"
RUNTIME_EVENT_REPOSITORY_INVALID = "RUNTIME_EVENT_INVALID"
RUNTIME_EVENT_DELIVERY_NOT_FOUND = "RUNTIME_EVENT_DELIVERY_NOT_FOUND"
RUNTIME_EVENT_DELIVERY_CLAIM_LOST = "RUNTIME_EVENT_DELIVERY_CLAIM_LOST"
RUNTIME_EVENT_DELIVERY_CONFLICT = "RUNTIME_EVENT_DELIVERY_CONFLICT"
RUNTIME_EVENT_DURABLE_DATA_INVALID = "RUNTIME_EVENT_DURABLE_DATA_INVALID"

RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY = (
    "0112#runtime_context_authority"
)
RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256 = (
    "1ce9bd319094e965a91d958e23b3a034a6e9a74dc4429d533d8f0efe68960f7f"
)

RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY = "0068#runtime_event_delivery"
RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256 = (
    "91f114e2d0f199a9d9fa772f207e9154aba577f8469a0be0ff59dcbe854d63e2"
)
TASK_GRAPH_OUTBOX_REVISION_MIGRATION_ASSET_KEY = (
    "0095#task_runtime_revisioned_graph_outbox"
)
TASK_GRAPH_OUTBOX_REVISION_MIGRATION_SHA256 = (
    "db39c2af0c497322c18d767b74139b5cba635c33e947cba5191f440aad534fa1"
)

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "runtime_event_timeline": frozenset(
        {
            "cursor",
            "event_id",
            "schema_version",
            "event_type",
            "source_kind",
            "source_ref",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "payload_ref",
            "payload_json",
            "payload_digest",
            "evidence_refs_json",
            "dedupe_key",
            "redaction_level",
            "ts_ms",
            "created_ts_ms",
            "published_ts_ms",
        }
    ),
    "runtime_event_projection_outbox": frozenset(
        {
            "delivery_id",
            "projection_name",
            "event_cursor",
            "event_id",
            "status",
            "available_ts_ms",
            "attempt_count",
            "claim_owner",
            "claim_token",
            "claim_expires_ts_ms",
            "status_version",
            "last_error",
            "last_operator_ref",
            "created_ts_ms",
            "updated_ts_ms",
            "completed_ts_ms",
        }
    ),
    "runtime_event_projection_inbox": frozenset(
        {
            "projection_name",
            "event_cursor",
            "event_id",
            "delivery_id",
            "payload_digest",
            "applied_ts_ms",
        }
    ),
    "runtime_event_task_projection": frozenset(
        {
            "projection_name",
            "aggregate_type",
            "aggregate_id",
            "last_event_cursor",
            "last_event_id",
            "aggregate_version",
            "event_type",
            "payload_ref",
            "state_json",
            "state_digest",
            "updated_ts_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_runtime_event_projection_outbox_ready",
        "idx_runtime_event_projection_outbox_claim_expiry",
        "idx_runtime_event_projection_inbox_event",
        "idx_runtime_event_task_projection_cursor",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_runtime_event_task_projection_enqueue",
        "trg_runtime_event_journal_no_update",
        "trg_runtime_event_journal_no_delete",
    }
)


class RuntimeEventRepositoryError(RuntimeError):
    """Fail-closed persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or RUNTIME_EVENT_REPOSITORY_INVALID)
        self.detail = str(detail or "runtime event persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field_name: str, *, max_length: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeEventRepositoryError(
            RUNTIME_EVENT_REPOSITORY_INVALID,
            f"{field_name} is required",
        )
    if len(normalized) > max_length:
        raise RuntimeEventRepositoryError(
            RUNTIME_EVENT_REPOSITORY_INVALID,
            f"{field_name} exceeds {max_length} chars",
        )
    return normalized


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _revisioned_task_graph_ref(value: str) -> tuple[str, int] | None:
    graph_id, separator, raw_revision = str(value or "").rpartition("@")
    if not separator or not graph_id or not raw_revision.isdigit():
        return None
    revision = int(raw_revision)
    return (graph_id, revision) if revision >= 1 else None


def _normalized_sql(value: object) -> str:
    compact = "".join(str(value or "").lower().split())
    return compact.translate(str.maketrans("", "", "'\"`[]"))


class RuntimeEventRepository:
    """One connection owner for journal, delivery, inbox, and projection UoWs."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "RuntimeEventRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
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
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "clock returned an invalid value",
            ) from exc
        if value < 0:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "clock must be non-negative",
            )
        return value

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(str(stage))

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                f"runtime event database does not exist: {self.db_path}",
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
                    (RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,),
                ).fetchone()
                revision_asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (TASK_GRAPH_OUTBOX_REVISION_MIGRATION_ASSET_KEY,),
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
                revision_index_row = conn.execute(
                    """
                    SELECT tbl_name, sql FROM sqlite_master
                    WHERE type = 'index'
                      AND name = 'uq_runtime_event_task_outbox_source'
                    """
                ).fetchone()
                revision_index_list = {
                    str(row[1]): row
                    for row in conn.execute(
                        "PRAGMA index_list(runtime_event_timeline)"
                    ).fetchall()
                }
                duplicate_task_outbox_source = conn.execute(
                    """
                    SELECT source_ref
                    FROM runtime_event_timeline
                    WHERE source_kind = 'TASK_STORE_OUTBOX'
                    GROUP BY source_ref
                    HAVING COUNT(*) <> 1
                    LIMIT 1
                    """
                ).fetchone()
                revision_outbox_columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(task_runtime_outbox)"
                    ).fetchall()
                }
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                "runtime event schema cannot be verified",
            ) from exc
        missing_columns = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_TABLE_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        revision_footprint = (
            revision_asset is not None
            or revision_index_row is not None
            or "graph_revision" in revision_outbox_columns
        )
        revision_schema_invalid = False
        if revision_footprint:
            normalized_revision_index = re.sub(
                r"\s+", "", str(revision_index_row[1] if revision_index_row else "").lower()
            )
            revision_index_meta = revision_index_list.get(
                "uq_runtime_event_task_outbox_source"
            )
            revision_schema_invalid = (
                revision_asset is None
                or str(revision_asset["checksum"])
                != TASK_GRAPH_OUTBOX_REVISION_MIGRATION_SHA256
                or str(revision_asset["disposition"])
                not in _ALLOWED_MIGRATION_DISPOSITIONS
                or revision_index_row is None
                or str(revision_index_row[0]) != "runtime_event_timeline"
                or normalized_revision_index
                != (
                    "createuniqueindexuq_runtime_event_task_outbox_source"
                    "onruntime_event_timeline(source_ref)"
                    "wheresource_kind='task_store_outbox'"
                )
                or revision_index_meta is None
                or int(revision_index_meta[2]) != 1
                or int(revision_index_meta[4]) != 1
                or duplicate_task_outbox_source is not None
            )
        if (
            asset is None
            or str(asset["checksum"]) != RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or missing_columns
            or not _REQUIRED_INDEXES <= indexes
            or not _REQUIRED_TRIGGERS <= triggers
            or foreign_keys != 1
            or revision_schema_invalid
        ):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                "runtime event migrations 0056/0068 are missing, stale, or checksum-drifted",
            )

    def assert_runtime_context_authority_schema(self) -> None:
        """Verify the exact 0112 CAS and Host-lease fencing authority.

        Runtime callers consume this as a readiness port. The repository is
        the sole owner of the SQLite connection and schema inspection SQL.
        """

        if not self.db_path.is_file():
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                "runtime context SQLite authority is unavailable",
            )
        try:
            conn = self._connect()
            try:
                asset = conn.execute(
                    "SELECT checksum, disposition FROM schema_migration_assets "
                    "WHERE asset_key = ?",
                    (RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY,),
                ).fetchone()
                rows = conn.execute(
                    "SELECT type, name, sql FROM sqlite_master "
                    "WHERE name IN (?, ?, ?)",
                    (
                        "ux_runtime_context_aggregate_revision",
                        "runtime_guardian_requires_active_host_lease",
                        "scene_state_requires_active_host_lease",
                    ),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                "runtime context authority schema cannot be inspected",
            ) from exc
        if (
            asset is None
            or str(asset["checksum"])
            != RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256
            or str(asset["disposition"])
            not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                "runtime context migration checksum drift",
            )
        objects = {
            str(row["name"]): (
                str(row["type"]),
                _normalized_sql(row["sql"]),
            )
            for row in rows
        }
        expected = {
            "ux_runtime_context_aggregate_revision": (
                "index",
                (
                    "uniqueindex",
                    "runtime_event_timeline(aggregate_type,aggregate_id,aggregate_version)",
                    "aggregate_typein('scene_state','runtime_guardian')",
                ),
            ),
            "runtime_guardian_requires_active_host_lease": (
                "trigger",
                (
                    "host_embodiment_runtime_lease",
                    "$.details.runtime_scope_id",
                    "$.details.runtime_owner_id",
                    "$.details.runtime_lease_id",
                    "$.details.fencing_token",
                    "lease.expires_at_ms>new.created_ts_ms",
                ),
            ),
            "scene_state_requires_active_host_lease": (
                "trigger",
                (
                    "host_embodiment_runtime_lease",
                    "$.runtime_scope_id",
                    "$.runtime_owner_id",
                    "$.runtime_lease_id",
                    "$.fencing_token",
                    "lease.expires_at_ms>new.created_ts_ms",
                ),
            ),
        }
        for name, (kind, fragments) in expected.items():
            actual = objects.get(name)
            if actual is None or actual[0] != kind:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                    f"runtime context schema object missing: {name}",
                )
            if any(
                _normalized_sql(fragment) not in actual[1]
                for fragment in fragments
            ):
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY,
                    f"runtime context schema object drift: {name}",
                )

    # -- Append-only journal -------------------------------------------------

    def append_journal(self, record: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(record)
        dedupe_key = _required_text(item.get("dedupe_key"), "dedupe_key")
        event_id = _required_text(item.get("event_id"), "event_id")
        with self._transaction() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO runtime_event_timeline(
                        event_id, schema_version, event_type, source_kind,
                        source_ref, aggregate_type, aggregate_id,
                        aggregate_version, payload_ref, payload_json,
                        payload_digest, evidence_refs_json, dedupe_key,
                        redaction_level, ts_ms, created_ts_ms, published_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        item["schema_version"],
                        item["event_type"],
                        item["source_kind"],
                        item["source_ref"],
                        item["aggregate_type"],
                        item["aggregate_id"],
                        int(item["aggregate_version"]),
                        item["payload_ref"],
                        item["payload_json"],
                        item["payload_digest"],
                        item["evidence_refs_json"],
                        dedupe_key,
                        item["redaction_level"],
                        int(item["ts_ms"]),
                        int(item["created_ts_ms"]),
                        int(item["published_ts_ms"]),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_REPOSITORY_INVALID,
                    f"runtime event identity conflict: {event_id}",
                ) from exc
            row = conn.execute(
                "SELECT * FROM runtime_event_timeline WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if row is None:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_REPOSITORY_INVALID,
                    "runtime event insert failed without a retrievable dedupe row",
                )
            if str(row["event_id"]) != event_id:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_REPOSITORY_INVALID,
                    "runtime event dedupe key conflicts with a different event id",
                )
            immutable_fields = (
                "schema_version",
                "event_type",
                "source_kind",
                "source_ref",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
                "payload_ref",
                "payload_digest",
                "evidence_refs_json",
                "dedupe_key",
                "redaction_level",
                "ts_ms",
            )
            mismatched = [
                field
                for field in immutable_fields
                if str(row[field]) != str(item[field])
            ]
            if mismatched:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "runtime event replay does not match journal authority: "
                    + ", ".join(mismatched),
                )
            # 0068's trigger is the sole delivery-intent writer.  Replayed
            # pre-0068 rows are backfilled if a migration was interrupted
            # between catalog application and normal processing.
            if str(row["source_kind"]) == "TASK_STORE_OUTBOX":
                delivery = conn.execute(
                    """
                    SELECT delivery_id FROM runtime_event_projection_outbox
                    WHERE projection_name = ? AND event_cursor = ?
                    """,
                    (TASK_RUNTIME_PROJECTION, int(row["cursor"])),
                ).fetchone()
                if delivery is None:
                    raise RuntimeEventRepositoryError(
                        RUNTIME_EVENT_DURABLE_DATA_INVALID,
                        "Task Runtime journal row has no transactional projection intent",
                    )
            return dict(row)

    def list_journal(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
        event_type: str | None = None,
        source_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["cursor > ?"]
        params: list[Any] = [max(0, int(after_cursor))]
        if str(event_type or "").strip():
            clauses.append("event_type = ?")
            params.append(str(event_type).strip())
        if str(source_kind or "").strip():
            clauses.append("source_kind = ?")
            params.append(str(source_kind).strip().upper())
        params.append(min(1000, max(1, int(limit))))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM runtime_event_timeline
                WHERE {' AND '.join(clauses)}
                ORDER BY cursor ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_journal_by_event_id(
        self,
        *,
        event_id: str,
    ) -> dict[str, Any] | None:
        """Return the canonical journal row for one exact Runtime Event id."""

        if not isinstance(event_id, str):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "event_id must be a string",
            )
        raw_event_id = event_id
        identity = _required_text(raw_event_id, "event_id")
        if raw_event_id != identity:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_REPOSITORY_INVALID,
                "event_id must use its exact canonical form",
            )
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM runtime_event_timeline WHERE event_id = ?",
                (identity,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_journal_for_aggregates(
        self,
        *,
        aggregate_refs: Sequence[tuple[str, str]],
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys((str(kind), str(identifier)) for kind, identifier in aggregate_refs))
        if not normalized:
            return []
        after = max(0, int(after_cursor))
        size = min(1000, max(1, int(limit)))
        conn = self._connect()
        try:
            records: list[dict[str, Any]] = []
            for offset in range(0, len(normalized), 400):
                chunk = normalized[offset : offset + 400]
                identity_sql = " OR ".join(
                    "(aggregate_type = ? AND aggregate_id = ?)" for _ in chunk
                )
                params: list[Any] = [after]
                for kind, identifier in chunk:
                    params.extend((kind, identifier))
                params.append(size)
                rows = conn.execute(
                    f"""
                    SELECT * FROM runtime_event_timeline
                    WHERE cursor > ? AND ({identity_sql})
                    ORDER BY cursor ASC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
                records.extend(dict(row) for row in rows)

            # 0095 makes future TaskGraph Runtime Events revision-addressed.
            # Preserve the immutable pre-0095 journal and resolve its legacy
            # ``aggregate_id=graph_id`` rows through the exact source Outbox
            # authority instead of guessing or rewriting historical evidence.
            outbox_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_outbox)"
                ).fetchall()
            }
            legacy_graph_refs = [
                resolved
                for kind, identifier in normalized
                if kind == "TASK_GRAPH"
                for resolved in [_revisioned_task_graph_ref(identifier)]
                if resolved is not None
            ]
            if "graph_revision" in outbox_columns:
                for offset in range(0, len(legacy_graph_refs), 200):
                    chunk = legacy_graph_refs[offset : offset + 200]
                    if not chunk:
                        continue
                    identity_sql = " OR ".join(
                        "(event.aggregate_id = ? AND outbox.aggregate_id = ? "
                        "AND outbox.graph_revision = ?)"
                        for _ in chunk
                    )
                    params = [after]
                    for graph_id, revision in chunk:
                        params.extend((graph_id, graph_id, revision))
                    params.append(size)
                    rows = conn.execute(
                        f"""
                        SELECT event.*
                        FROM runtime_event_timeline AS event
                        JOIN task_runtime_outbox AS outbox
                          ON event.source_kind = 'TASK_STORE_OUTBOX'
                         AND event.source_ref = 'taskstore://outbox/' || outbox.outbox_id
                         AND outbox.aggregate_type = 'TASK_GRAPH'
                        WHERE event.cursor > ?
                          AND event.aggregate_type = 'TASK_GRAPH'
                          AND ({identity_sql})
                        ORDER BY event.cursor ASC
                        LIMIT ?
                        """,
                        tuple(params),
                    ).fetchall()
                    records.extend(dict(row) for row in rows)
            deduped = {int(item["cursor"]): item for item in records}
            ordered = sorted(
                deduped.values(),
                key=lambda item: (int(item["cursor"]), str(item["event_id"])),
            )
            return ordered[:size]
        finally:
            conn.close()

    def get_task_outbox_journal(self, *, outbox_id: str) -> dict[str, Any] | None:
        """Resolve the sole journal row projected from one immutable Outbox."""

        reference = f"taskstore://outbox/{_required_text(outbox_id, 'outbox_id')}"
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM runtime_event_timeline
                WHERE source_kind = 'TASK_STORE_OUTBOX' AND source_ref = ?
                ORDER BY cursor
                LIMIT 2
                """,
                (reference,),
            ).fetchall()
            if len(rows) > 1:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "one TaskStore Outbox has multiple Runtime Event journal rows",
                )
            return dict(rows[0]) if rows else None
        finally:
            conn.close()

    # -- Durable delivery ----------------------------------------------------

    def claim_projection_delivery(
        self,
        *,
        worker_id: str,
        lease_ms: int = 30_000,
    ) -> dict[str, Any] | None:
        owner = _required_text(worker_id, "worker_id", max_length=240)
        lease = max(1, int(lease_ms))
        now = self._now()
        expires = now + lease
        token = secrets.token_urlsafe(32)
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM runtime_event_projection_outbox
                WHERE projection_name = ?
                  AND (
                    (status = 'PENDING' AND available_ts_ms <= ?)
                    OR
                    (status = 'CLAIMED' AND claim_expires_ts_ms <= ?)
                  )
                ORDER BY event_cursor ASC
                LIMIT 1
                """,
                (TASK_RUNTIME_PROJECTION, now, now),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """
                UPDATE runtime_event_projection_outbox
                SET status = 'CLAIMED', claim_owner = ?, claim_token = ?,
                    claim_expires_ts_ms = ?, attempt_count = attempt_count + 1,
                    status_version = status_version + 1, updated_ts_ms = ?,
                    last_error = NULL, completed_ts_ms = NULL
                WHERE delivery_id = ? AND status_version = ?
                  AND (
                    (status = 'PENDING' AND available_ts_ms <= ?)
                    OR
                    (status = 'CLAIMED' AND claim_expires_ts_ms <= ?)
                  )
                """,
                (
                    owner,
                    token,
                    expires,
                    now,
                    row["delivery_id"],
                    int(row["status_version"]),
                    now,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DELIVERY_CONFLICT,
                    "projection delivery claim lost its CAS fence",
                )
            claimed = conn.execute(
                """
                SELECT o.*, e.event_type, e.source_kind, e.aggregate_type,
                       e.aggregate_id, e.aggregate_version, e.payload_ref,
                       e.payload_json, e.payload_digest, e.ts_ms
                FROM runtime_event_projection_outbox AS o
                JOIN runtime_event_timeline AS e ON e.cursor = o.event_cursor
                WHERE o.delivery_id = ?
                """,
                (row["delivery_id"],),
            ).fetchone()
            if claimed is None:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "claimed projection delivery has no journal row",
                )
            return dict(claimed)

    def complete_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        delivery = _required_text(delivery_id, "delivery_id")
        owner = _required_text(worker_id, "worker_id", max_length=240)
        token = _required_text(claim_token, "claim_token")
        now = self._now()
        with self._transaction() as conn:
            outbox = self._require_delivery(conn, delivery)
            self._require_live_claim(outbox, worker_id=owner, claim_token=token, now=now)
            journal = conn.execute(
                "SELECT * FROM runtime_event_timeline WHERE cursor = ?",
                (int(outbox["event_cursor"]),),
            ).fetchone()
            if journal is None or str(journal["event_id"]) != str(outbox["event_id"]):
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "projection delivery journal authority is missing or mismatched",
                )
            try:
                payload = json.loads(str(journal["payload_json"]))
            except json.JSONDecodeError as exc:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "journal payload_json is invalid",
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "journal payload_json must be an object",
                )
            payload_digest = _sha256_json(payload)
            if payload_digest != str(journal["payload_digest"]):
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "journal payload digest mismatch",
                )
            inbox = conn.execute(
                """
                SELECT * FROM runtime_event_projection_inbox
                WHERE projection_name = ? AND event_cursor = ?
                """,
                (TASK_RUNTIME_PROJECTION, int(journal["cursor"])),
            ).fetchone()
            applied = inbox is None
            if inbox is not None:
                if (
                    str(inbox["event_id"]) != str(journal["event_id"])
                    or str(inbox["delivery_id"]) != delivery
                    or str(inbox["payload_digest"]) != payload_digest
                ):
                    raise RuntimeEventRepositoryError(
                        RUNTIME_EVENT_DURABLE_DATA_INVALID,
                        "projection inbox conflicts with the journal authority",
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO runtime_event_projection_inbox(
                        projection_name, event_cursor, event_id, delivery_id,
                        payload_digest, applied_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        TASK_RUNTIME_PROJECTION,
                        int(journal["cursor"]),
                        journal["event_id"],
                        delivery,
                        payload_digest,
                        now,
                    ),
                )
                self._fault("after_projection_inbox")
                state = {
                    "event_id": str(journal["event_id"]),
                    "event_cursor": int(journal["cursor"]),
                    "event_type": str(journal["event_type"]),
                    "aggregate_type": str(journal["aggregate_type"]),
                    "aggregate_id": str(journal["aggregate_id"]),
                    "aggregate_version": int(journal["aggregate_version"]),
                    "payload_ref": str(journal["payload_ref"]),
                    "payload": payload,
                    "ts_ms": int(journal["ts_ms"]),
                }
                state_json = _canonical_json(state)
                state_digest = _sha256_json(state)
                conn.execute(
                    """
                    INSERT INTO runtime_event_task_projection(
                        projection_name, aggregate_type, aggregate_id,
                        last_event_cursor, last_event_id, aggregate_version,
                        event_type, payload_ref, state_json, state_digest,
                        updated_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(projection_name, aggregate_type, aggregate_id)
                    DO UPDATE SET
                        last_event_cursor = excluded.last_event_cursor,
                        last_event_id = excluded.last_event_id,
                        aggregate_version = excluded.aggregate_version,
                        event_type = excluded.event_type,
                        payload_ref = excluded.payload_ref,
                        state_json = excluded.state_json,
                        state_digest = excluded.state_digest,
                        updated_ts_ms = excluded.updated_ts_ms
                    WHERE excluded.last_event_cursor > runtime_event_task_projection.last_event_cursor
                    """,
                    (
                        TASK_RUNTIME_PROJECTION,
                        journal["aggregate_type"],
                        journal["aggregate_id"],
                        int(journal["cursor"]),
                        journal["event_id"],
                        int(journal["aggregate_version"]),
                        journal["event_type"],
                        journal["payload_ref"],
                        state_json,
                        state_digest,
                        now,
                    ),
                )
                self._fault("after_task_projection")
            self._fault("before_projection_delivery_complete")
            updated = conn.execute(
                """
                UPDATE runtime_event_projection_outbox
                SET status = 'COMPLETED', claim_owner = NULL,
                    claim_token = NULL, claim_expires_ts_ms = NULL,
                    status_version = status_version + 1,
                    updated_ts_ms = ?, completed_ts_ms = ?, last_error = NULL
                WHERE delivery_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND claim_token = ?
                  AND status_version = ?
                """,
                (
                    now,
                    now,
                    delivery,
                    owner,
                    token,
                    int(outbox["status_version"]),
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
                    "projection delivery completion lost its fence",
                )
            completed = dict(self._require_delivery(conn, delivery))
            completed["applied"] = applied
            projection = conn.execute(
                """
                SELECT * FROM runtime_event_task_projection
                WHERE projection_name = ? AND aggregate_type = ? AND aggregate_id = ?
                """,
                (
                    TASK_RUNTIME_PROJECTION,
                    journal["aggregate_type"],
                    journal["aggregate_id"],
                ),
            ).fetchone()
            completed["projection"] = self._projection_record(projection)
            return completed

    def release_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
        detail: str,
        retry_delay_ms: int = 0,
    ) -> dict[str, Any]:
        delivery = _required_text(delivery_id, "delivery_id")
        owner = _required_text(worker_id, "worker_id", max_length=240)
        token = _required_text(claim_token, "claim_token")
        error = _required_text(detail, "detail", max_length=2_000)
        now = self._now()
        available = now + max(0, int(retry_delay_ms))
        with self._transaction() as conn:
            row = self._require_delivery(conn, delivery)
            self._require_live_claim(row, worker_id=owner, claim_token=token, now=now)
            updated = conn.execute(
                """
                UPDATE runtime_event_projection_outbox
                SET status = 'PENDING', available_ts_ms = ?,
                    claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL,
                    status_version = status_version + 1,
                    last_error = ?, updated_ts_ms = ?, completed_ts_ms = NULL
                WHERE delivery_id = ? AND status_version = ?
                """,
                (available, error, now, delivery, int(row["status_version"])),
            )
            if updated.rowcount != 1:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
                    "projection delivery release lost its fence",
                )
            return dict(self._require_delivery(conn, delivery))

    def replay_projection_delivery(
        self,
        *,
        delivery_id: str,
        operator_ref: str,
    ) -> dict[str, Any]:
        delivery = _required_text(delivery_id, "delivery_id")
        operator = _required_text(operator_ref, "operator_ref")
        now = self._now()
        with self._transaction() as conn:
            row = self._require_delivery(conn, delivery)
            if (
                str(row["status"]) == "CLAIMED"
                and int(row["claim_expires_ts_ms"] or 0) > now
            ):
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DELIVERY_CONFLICT,
                    "cannot replay a live claimed delivery",
                )
            conn.execute(
                """
                UPDATE runtime_event_projection_outbox
                SET status = 'PENDING', available_ts_ms = ?,
                    claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL,
                    status_version = status_version + 1,
                    last_error = NULL, last_operator_ref = ?,
                    updated_ts_ms = ?, completed_ts_ms = NULL
                WHERE delivery_id = ? AND status_version = ?
                """,
                (now, operator, now, delivery, int(row["status_version"])),
            )
            return dict(self._require_delivery(conn, delivery))

    def rebuild_task_projection(self, *, operator_ref: str) -> dict[str, Any]:
        """Atomically discard derived state and requeue the complete journal."""

        operator = _required_text(operator_ref, "operator_ref")
        now = self._now()
        with self._transaction() as conn:
            active = conn.execute(
                """
                SELECT COUNT(*) FROM runtime_event_projection_outbox
                WHERE projection_name = ? AND status = 'CLAIMED'
                  AND claim_expires_ts_ms > ?
                """,
                (TASK_RUNTIME_PROJECTION, now),
            ).fetchone()
            if active is not None and int(active[0]) > 0:
                raise RuntimeEventRepositoryError(
                    RUNTIME_EVENT_DELIVERY_CONFLICT,
                    "cannot rebuild while projection deliveries have live claims",
                )
            conn.execute(
                "DELETE FROM runtime_event_task_projection WHERE projection_name = ?",
                (TASK_RUNTIME_PROJECTION,),
            )
            conn.execute(
                "DELETE FROM runtime_event_projection_inbox WHERE projection_name = ?",
                (TASK_RUNTIME_PROJECTION,),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_event_projection_outbox(
                    delivery_id, projection_name, event_cursor, event_id,
                    status, available_ts_ms, attempt_count, status_version,
                    created_ts_ms, updated_ts_ms
                )
                SELECT
                    'runtime-event-delivery://task-runtime-latest-v1/' || cursor,
                    ?, cursor, event_id, 'PENDING', ?, 0, 0,
                    created_ts_ms, ?
                FROM runtime_event_timeline
                WHERE source_kind = 'TASK_STORE_OUTBOX'
                """,
                (TASK_RUNTIME_PROJECTION, now, now),
            )
            conn.execute(
                """
                UPDATE runtime_event_projection_outbox
                SET status = 'PENDING', available_ts_ms = ?,
                    claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL,
                    status_version = status_version + 1,
                    last_error = NULL, last_operator_ref = ?,
                    updated_ts_ms = ?, completed_ts_ms = NULL
                WHERE projection_name = ?
                """,
                (now, operator, now, TASK_RUNTIME_PROJECTION),
            )
            count = conn.execute(
                """
                SELECT COUNT(*) FROM runtime_event_projection_outbox
                WHERE projection_name = ?
                """,
                (TASK_RUNTIME_PROJECTION,),
            ).fetchone()
            return {
                "projection_name": TASK_RUNTIME_PROJECTION,
                "requeued_count": int(count[0]) if count is not None else 0,
                "operator_ref": operator,
            }

    def get_delivery(self, *, delivery_id: str) -> dict[str, Any]:
        delivery = _required_text(delivery_id, "delivery_id")
        conn = self._connect()
        try:
            return dict(self._require_delivery(conn, delivery))
        finally:
            conn.close()

    def get_task_projection(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
    ) -> dict[str, Any] | None:
        kind = _required_text(aggregate_type, "aggregate_type")
        identifier = _required_text(aggregate_id, "aggregate_id")
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM runtime_event_task_projection
                WHERE projection_name = ? AND aggregate_type = ? AND aggregate_id = ?
                """,
                (TASK_RUNTIME_PROJECTION, kind, identifier),
            ).fetchone()
            return self._projection_record(row)
        finally:
            conn.close()

    def list_task_projections(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM runtime_event_task_projection
                WHERE projection_name = ? AND last_event_cursor > ?
                ORDER BY last_event_cursor ASC
                LIMIT ?
                """,
                (
                    TASK_RUNTIME_PROJECTION,
                    max(0, int(after_cursor)),
                    min(1000, max(1, int(limit))),
                ),
            ).fetchall()
            return [self._projection_record(row) for row in rows]
        finally:
            conn.close()

    def projection_health(self) -> dict[str, Any]:
        now = self._now()
        conn = self._connect()
        try:
            counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'CLAIMED' AND claim_expires_ts_ms > ? THEN 1 ELSE 0 END) AS claimed,
                    SUM(CASE WHEN status = 'CLAIMED' AND claim_expires_ts_ms <= ? THEN 1 ELSE 0 END) AS expired,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
                    MAX(event_cursor) AS journal_cursor
                FROM runtime_event_projection_outbox
                WHERE projection_name = ?
                """,
                (now, now, TASK_RUNTIME_PROJECTION),
            ).fetchone()
            projected = conn.execute(
                """
                SELECT MAX(event_cursor), COUNT(*)
                FROM runtime_event_projection_inbox
                WHERE projection_name = ?
                """,
                (TASK_RUNTIME_PROJECTION,),
            ).fetchone()
            return {
                "projection_name": TASK_RUNTIME_PROJECTION,
                "pending": int(counts["pending"] or 0),
                "claimed": int(counts["claimed"] or 0),
                "expired": int(counts["expired"] or 0),
                "completed": int(counts["completed"] or 0),
                "journal_cursor": int(counts["journal_cursor"] or 0),
                "projected_cursor": int(projected[0] or 0),
                "inbox_count": int(projected[1] or 0),
            }
        finally:
            conn.close()

    @staticmethod
    def _require_delivery(conn: sqlite3.Connection, delivery_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM runtime_event_projection_outbox WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_DELIVERY_NOT_FOUND,
                f"projection delivery not found: {delivery_id}",
            )
        return row

    @staticmethod
    def _require_live_claim(
        row: sqlite3.Row,
        *,
        worker_id: str,
        claim_token: str,
        now: int,
    ) -> None:
        if (
            str(row["status"]) != "CLAIMED"
            or str(row["claim_owner"] or "") != worker_id
            or str(row["claim_token"] or "") != claim_token
            or int(row["claim_expires_ts_ms"] or 0) <= now
        ):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
                "projection delivery claim is missing, expired, or fenced",
            )

    @staticmethod
    def _projection_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        record = dict(row)
        try:
            state = json.loads(str(record.pop("state_json")))
        except json.JSONDecodeError as exc:
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_DURABLE_DATA_INVALID,
                "projection state_json is invalid",
            ) from exc
        if not isinstance(state, dict) or _sha256_json(state) != str(record["state_digest"]):
            raise RuntimeEventRepositoryError(
                RUNTIME_EVENT_DURABLE_DATA_INVALID,
                "projection state digest mismatch",
            )
        record["state"] = state
        return record


__all__ = [
    "RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY",
    "RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256",
    "RUNTIME_EVENT_DELIVERY_CLAIM_LOST",
    "RUNTIME_EVENT_DELIVERY_CONFLICT",
    "RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY",
    "RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256",
    "RUNTIME_EVENT_DELIVERY_NOT_FOUND",
    "RUNTIME_EVENT_DURABLE_DATA_INVALID",
    "RUNTIME_EVENT_REPOSITORY_INVALID",
    "RUNTIME_EVENT_REPOSITORY_SCHEMA_NOT_READY",
    "RuntimeEventRepository",
    "RuntimeEventRepositoryError",
    "TASK_RUNTIME_PROJECTION",
]
