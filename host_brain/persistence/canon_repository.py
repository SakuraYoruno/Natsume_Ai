"""Single SQLite writer for Canon commands, journal and projections.

The repository owns operation-scoped connections and complete transactions.
It never creates schema at runtime and never reaches through ``SQLiteLedger``.
``canon_command`` and ``canon_journal`` are immutable; mutable read models can
be deleted and rebuilt deterministically from the journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.core.ids import now_ms


CANON_SCHEMA_NOT_READY = "CANON_SCHEMA_NOT_READY"
CANON_COMMAND_INVALID = "CANON_COMMAND_INVALID"
CANON_COMMAND_CONFLICT = "CANON_COMMAND_CONFLICT"
CANON_PROJECTION_CONFLICT = "CANON_PROJECTION_CONFLICT"
CANON_DURABLE_DATA_INVALID = "CANON_DURABLE_DATA_INVALID"

CANON_MIGRATION_ASSET_KEY = "0067#canon_command_spine"
# Updated after the SQL asset is frozen.
CANON_MIGRATION_SHA256 = "efa1fb7bca9735cbe5fd81c027bbd38a1b63f874b9afaba9487e95ca3b244116"

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_PROPOSAL_STATUSES = frozenset(
    {
        "pending",
        "rejected",
        "APPLY_PREPARED",
        "APPLIED",
        "ROLLBACK_PREPARED",
        "ROLLED_BACK",
    }
)
_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "canon_command": frozenset(
        {
            "command_id",
            "command_hash",
            "command_type",
            "aggregate_kind",
            "aggregate_id",
            "expected_version",
            "payload_json",
            "payload_digest",
            "actor_id",
            "trace_id",
            "created_ts_ms",
        }
    ),
    "canon_journal": frozenset(
        {
            "journal_id",
            "command_id",
            "aggregate_kind",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            "payload_json",
            "payload_digest",
            "previous_event_digest",
            "event_digest",
            "created_ts_ms",
        }
    ),
    "canon_projection_head": frozenset(
        {
            "aggregate_kind",
            "aggregate_id",
            "version",
            "last_journal_id",
            "last_event_digest",
            "updated_ts_ms",
        }
    ),
    "canon_proposal_projection": frozenset(
        {
            "proposal_id",
            "canon_kind",
            "target_id",
            "base_version",
            "proposed_version",
            "proposed_hash",
            "proposed_title",
            "proposed_tags_json",
            "proposed_body",
            "source_intent_id",
            "proposer_node_id",
            "summary",
            "status",
            "status_reason",
            "status_actor_id",
            "projection_version",
            "last_journal_id",
            "created_ts_ms",
            "updated_ts_ms",
        }
    ),
    "canon_document_projection": frozenset(
        {
            "doc_id",
            "canon_version",
            "canon_hash",
            "status",
            "title",
            "tags_json",
            "body",
            "updated_by",
            "canon_ts_ms",
            "source_path",
            "projection_version",
            "last_journal_id",
            "projected_ts_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "uq_canon_journal_aggregate_version",
        "idx_canon_journal_replay",
        "idx_canon_proposal_projection_status",
        "idx_canon_proposal_projection_target",
        "idx_canon_document_projection_status",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "canon_command_no_update",
        "canon_command_no_delete",
        "canon_journal_no_update",
        "canon_journal_no_delete",
    }
)


class CanonRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CANON_COMMAND_INVALID)
        self.detail = str(detail or "Canon persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class CanonRepository:
    """The sole writer/UoW for the AW-03 Canon aggregate and read models."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise CanonRepositoryError(
                CANON_COMMAND_INVALID,
                "CanonRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise CanonRepositoryError(CANON_COMMAND_INVALID, "clock must be callable")
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
            value = _exact_int(self._clock(), "clock")
        except (TypeError, ValueError) as exc:
            raise CanonRepositoryError(CANON_COMMAND_INVALID, str(exc)) from exc
        if value < 0:
            raise CanonRepositoryError(CANON_COMMAND_INVALID, "clock must be non-negative")
        return value

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise CanonRepositoryError(
                CANON_SCHEMA_NOT_READY,
                f"Canon database does not exist: {self.db_path}",
            )
        try:
            with self._connect() as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (CANON_MIGRATION_ASSET_KEY,),
                ).fetchone()
                if (
                    asset is None
                    or str(asset["checksum"]) != CANON_MIGRATION_SHA256
                    or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
                ):
                    raise CanonRepositoryError(
                        CANON_SCHEMA_NOT_READY,
                        "0067 Canon migration asset is absent or checksum drifted",
                    )
                for table, required in _REQUIRED_TABLE_COLUMNS.items():
                    columns = {
                        str(row["name"])
                        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    }
                    if not required.issubset(columns):
                        raise CanonRepositoryError(
                            CANON_SCHEMA_NOT_READY,
                            f"{table} schema is incomplete",
                        )
                indexes = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                triggers = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
                if not _REQUIRED_INDEXES.issubset(indexes):
                    raise CanonRepositoryError(
                        CANON_SCHEMA_NOT_READY,
                        "Canon indexes are incomplete",
                    )
                if not _REQUIRED_TRIGGERS.issubset(triggers):
                    raise CanonRepositoryError(
                        CANON_SCHEMA_NOT_READY,
                        "Canon immutability triggers are incomplete",
                    )
        except CanonRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise CanonRepositoryError(CANON_SCHEMA_NOT_READY, str(exc)) from exc

    def submit_proposal(
        self,
        proposal: Mapping[str, Any],
        *,
        actor_id: str,
        trace_id: str,
        command_id: str | None = None,
        created_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_proposal(proposal, require_pending=True)
        command_key = _text(
            command_id or f"canon-proposal-submit::{normalized['proposal_id']}",
            "command_id",
            limit=240,
        )
        existing = self._get_command(command_key)
        if existing is not None:
            stored = _decode_json(existing["payload_json"], existing["payload_digest"])
            if _proposal_semantics(stored) != _proposal_semantics(normalized):
                raise CanonRepositoryError(
                    CANON_COMMAND_CONFLICT,
                    f"command_id replay changed proposal: {command_key}",
                )
            replay = self.get_proposal(normalized["proposal_id"])
            if replay is None:
                raise CanonRepositoryError(
                    CANON_DURABLE_DATA_INVALID,
                    "command exists without proposal projection",
                )
            return replay
        ts_value = _timestamp(created_ts_ms, self._now())
        normalized["created_ts_ms"] = ts_value
        normalized["updated_ts_ms"] = ts_value
        normalized["status_reason"] = None
        normalized["status_actor_id"] = None
        return self._commit_event(
            command_id=command_key,
            command_type="SUBMIT_PROPOSAL",
            aggregate_kind="PROPOSAL",
            aggregate_id=normalized["proposal_id"],
            expected_version=0,
            event_type="PROPOSAL_SUBMITTED",
            payload=normalized,
            actor_id=actor_id,
            trace_id=trace_id,
            created_ts_ms=ts_value,
            write_compat=True,
        )

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reason: str,
        actor_id: str,
        trace_id: str,
        command_id: str | None = None,
        created_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        proposal_key = _text(proposal_id, "proposal_id", limit=240)
        command_key = _text(
            command_id or f"canon-proposal-reject::{proposal_key}",
            "command_id",
            limit=240,
        )
        existing = self._get_command(command_key)
        if existing is not None:
            stored = _decode_json(existing["payload_json"], existing["payload_digest"])
            if (
                str(stored.get("status_reason") or "") != str(reason or "")[:200]
                or str(stored.get("status_actor_id") or "") != _text(actor_id, "actor_id", limit=240)
            ):
                raise CanonRepositoryError(
                    CANON_COMMAND_CONFLICT,
                    f"command_id replay changed rejection: {command_key}",
                )
            replay = self.get_proposal(proposal_key)
            if replay is None:
                raise CanonRepositoryError(
                    CANON_DURABLE_DATA_INVALID,
                    "command exists without proposal projection",
                )
            return replay
        current = self.get_proposal(proposal_key)
        if current is None:
            raise KeyError(f"proposal not found: {proposal_key}")
        if current["status"] != "pending":
            raise PermissionError(
                f"proposal {proposal_key!r} cannot be rejected from {current['status']!r}"
            )
        ts_value = _timestamp(created_ts_ms, self._now())
        payload = {
            **current,
            "status": "rejected",
            "status_reason": str(reason or "")[:200],
            "status_actor_id": _text(actor_id, "actor_id", limit=240),
            "updated_ts_ms": ts_value,
        }
        payload.pop("projection_version", None)
        payload.pop("last_journal_id", None)
        return self._commit_event(
            command_id=command_key,
            command_type="REJECT_PROPOSAL",
            aggregate_kind="PROPOSAL",
            aggregate_id=proposal_key,
            expected_version=int(current["projection_version"]),
            event_type="PROPOSAL_REJECTED",
            payload=payload,
            actor_id=actor_id,
            trace_id=trace_id,
            created_ts_ms=ts_value,
            write_compat=True,
        )

    def project_proposal_status(
        self,
        proposal_id: str,
        *,
        status: str,
        actor_id: str,
        trace_id: str,
        command_id: str,
        reason: str = "",
        created_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        proposal_key = _text(proposal_id, "proposal_id", limit=240)
        status_value = str(status or "").strip()
        if status_value not in _PROPOSAL_STATUSES:
            raise CanonRepositoryError(
                CANON_COMMAND_INVALID,
                f"unsupported proposal status: {status_value}",
            )
        command_key = _text(command_id, "command_id", limit=240)
        existing = self._get_command(command_key)
        if existing is not None:
            stored = _decode_json(existing["payload_json"], existing["payload_digest"])
            if str(stored.get("status")) != status_value:
                raise CanonRepositoryError(
                    CANON_COMMAND_CONFLICT,
                    f"command_id replay changed proposal status: {command_key}",
                )
            replay = self.get_proposal(proposal_key)
            if replay is None:
                raise CanonRepositoryError(
                    CANON_DURABLE_DATA_INVALID,
                    "command exists without proposal projection",
                )
            return replay
        current = self.get_proposal(proposal_key)
        if current is None:
            raise KeyError(f"proposal not found: {proposal_key}")
        if current["status"] == status_value:
            return current
        ts_value = _timestamp(created_ts_ms, self._now())
        payload = {
            **current,
            "status": status_value,
            "status_reason": str(reason or "")[:200] or None,
            "status_actor_id": _text(actor_id, "actor_id", limit=240),
            "updated_ts_ms": ts_value,
        }
        payload.pop("projection_version", None)
        payload.pop("last_journal_id", None)
        return self._commit_event(
            command_id=command_key,
            command_type="PROJECT_PROPOSAL_STATUS",
            aggregate_kind="PROPOSAL",
            aggregate_id=proposal_key,
            expected_version=int(current["projection_version"]),
            event_type="PROPOSAL_STATUS_PROJECTED",
            payload=payload,
            actor_id=actor_id,
            trace_id=trace_id,
            created_ts_ms=ts_value,
            write_compat=False,
        )

    def project_document(
        self,
        document: Mapping[str, Any],
        *,
        actor_id: str,
        trace_id: str,
        command_id: str | None = None,
        projected_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_document(document)
        current = self.get_document(normalized["doc_id"])
        if current is not None and _document_semantics(current) == _document_semantics(normalized):
            return current
        expected = int(current["projection_version"]) if current is not None else 0
        semantic_digest = _sha256(_canonical_json(_document_semantics(normalized)))
        command_key = _text(
            command_id
            or f"canon-document-project::{normalized['doc_id']}::{semantic_digest[:32]}",
            "command_id",
            limit=240,
        )
        ts_value = _timestamp(projected_ts_ms, self._now())
        normalized["projected_ts_ms"] = ts_value
        return self._commit_event(
            command_id=command_key,
            command_type="PROJECT_DOCUMENT",
            aggregate_kind="DOCUMENT",
            aggregate_id=normalized["doc_id"],
            expected_version=expected,
            event_type="DOCUMENT_PROJECTED",
            payload=normalized,
            actor_id=actor_id,
            trace_id=trace_id,
            created_ts_ms=ts_value,
            write_compat=True,
        )

    def remove_document(
        self,
        doc_id: str,
        *,
        actor_id: str,
        trace_id: str,
        command_id: str | None = None,
        created_ts_ms: int | None = None,
    ) -> bool:
        doc_key = _text(doc_id, "doc_id", limit=160)
        current = self.get_document(doc_key)
        if current is None:
            return False
        semantic = {
            "doc_id": doc_key,
            "removed_version": int(current["version"]),
            "removed_hash": str(current["hash"]),
        }
        digest = _sha256(_canonical_json(semantic))
        command_key = _text(
            command_id or f"canon-document-remove::{doc_key}::{digest[:32]}",
            "command_id",
            limit=240,
        )
        ts_value = _timestamp(created_ts_ms, self._now())
        self._commit_event(
            command_id=command_key,
            command_type="REMOVE_DOCUMENT",
            aggregate_kind="DOCUMENT",
            aggregate_id=doc_key,
            expected_version=int(current["projection_version"]),
            event_type="DOCUMENT_REMOVED",
            payload=semantic,
            actor_id=actor_id,
            trace_id=trace_id,
            created_ts_ms=ts_value,
            write_compat=True,
        )
        return True

    def bootstrap_legacy_proposals(
        self,
        *,
        actor_id: str = "canon.startup.legacy-import",
        trace_id: str = "canon-startup-legacy-import",
    ) -> int:
        """Import pre-0067 reviewable proposals once through the journal."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM canon_proposal_log
                WHERE proposed_title IS NOT NULL AND trim(proposed_title) <> ''
                  AND proposed_body IS NOT NULL AND trim(proposed_body) <> ''
                ORDER BY id ASC
                """
            ).fetchall()
        imported = 0
        for row in rows:
            proposal_id = str(row["proposal_id"])
            if self.get_proposal(proposal_id) is not None:
                continue
            status = str(row["status"])
            if status not in _PROPOSAL_STATUSES:
                continue
            payload = _normalize_proposal(
                {
                    "proposal_id": proposal_id,
                    "canon_kind": row["canon_kind"],
                    "target_id": row["target_id"],
                    "base_version": row["base_version"],
                    "proposed_version": row["proposed_version"],
                    "proposed_hash": row["proposed_hash"],
                    "proposed_title": row["proposed_title"],
                    "proposed_tags": _parse_json_list(row["proposed_tags"]),
                    "proposed_body": row["proposed_body"],
                    "source_intent_id": row["source_intent_id"],
                    "proposer_node_id": row["proposer_node_id"] or "legacy.canon",
                    "summary": row["summary"] or "legacy Canon proposal",
                    "status": status,
                },
                require_pending=False,
            )
            ts_value = max(0, int(row["ts_ms"] or 0))
            payload.update(
                {
                    "created_ts_ms": ts_value,
                    "updated_ts_ms": ts_value,
                    "status_reason": None,
                    "status_actor_id": actor_id,
                }
            )
            digest = _sha256(_canonical_json(payload))
            self._commit_event(
                command_id=f"canon-legacy-import::{digest}",
                command_type="PROJECT_PROPOSAL_STATUS",
                aggregate_kind="PROPOSAL",
                aggregate_id=proposal_id,
                expected_version=0,
                event_type="PROPOSAL_STATUS_PROJECTED",
                payload=payload,
                actor_id=actor_id,
                trace_id=trace_id,
                created_ts_ms=ts_value,
                write_compat=False,
            )
            imported += 1
        return imported

    def reconcile_legacy_terminal_statuses(
        self,
        *,
        actor_id: str = "canon.startup.apply-reconcile",
        trace_id: str = "canon-startup-apply-reconcile",
    ) -> int:
        """Close the file-apply/projection crash gap from the governed 0053 port."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT proposal_id, status FROM canon_proposal_log
                WHERE status IN ('APPLIED', 'ROLLED_BACK')
                ORDER BY proposal_id ASC
                """
            ).fetchall()
        reconciled = 0
        for row in rows:
            proposal_id = str(row["proposal_id"])
            status = str(row["status"])
            current = self.get_proposal(proposal_id)
            if current is None or current["status"] == status:
                continue
            self.project_proposal_status(
                proposal_id,
                status=status,
                actor_id=actor_id,
                trace_id=trace_id,
                command_id=f"canon-apply-reconcile::{proposal_id}::{status}",
            )
            reconciled += 1
        return reconciled

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        key = _text(proposal_id, "proposal_id", limit=240)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM canon_proposal_projection WHERE proposal_id = ?",
                (key,),
            ).fetchone()
        return _proposal_row(row) if row is not None else None

    def list_proposals(
        self,
        *,
        status: str | None = None,
        canon_kind: str | None = None,
        target_doc_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status))
        if canon_kind is not None:
            clauses.append("canon_kind = ?")
            params.append(str(canon_kind))
        if target_doc_id is not None:
            clauses.append("target_id = ?")
            params.append(str(target_doc_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        page_size = max(1, min(int(limit), 1000))
        page_offset = max(0, int(offset))
        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM canon_proposal_projection {where}",
                    tuple(params),
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM canon_proposal_projection {where}
                ORDER BY updated_ts_ms DESC, proposal_id DESC LIMIT ? OFFSET ?
                """,
                (*params, page_size, page_offset),
            ).fetchall()
        return [_proposal_row(row) for row in rows], total

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        key = _text(doc_id, "doc_id", limit=160)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM canon_document_projection WHERE doc_id = ?",
                (key,),
            ).fetchone()
        return _document_row(row) if row is not None else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM canon_document_projection ORDER BY doc_id ASC"
            ).fetchall()
        return [_document_row(row) for row in rows]

    def rebuild_projections(self) -> dict[str, int]:
        """Rebuild every mutable Canon read model from the immutable journal."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM canon_journal
                    ORDER BY created_ts_ms ASC, aggregate_kind ASC,
                             aggregate_id ASC, aggregate_version ASC, journal_id ASC
                    """
                ).fetchall()
                conn.execute("DELETE FROM canon_proposal_projection")
                conn.execute("DELETE FROM canon_document_projection")
                conn.execute("DELETE FROM canon_projection_head")
                for row in rows:
                    payload = _decode_json(row["payload_json"], row["payload_digest"])
                    self._project_event(conn, row, payload, rebuilding=True)
                    conn.execute(
                        """
                        INSERT INTO canon_projection_head (
                            aggregate_kind, aggregate_id, version, last_journal_id,
                            last_event_digest, updated_ts_ms
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(aggregate_kind, aggregate_id) DO UPDATE SET
                            version = excluded.version,
                            last_journal_id = excluded.last_journal_id,
                            last_event_digest = excluded.last_event_digest,
                            updated_ts_ms = excluded.updated_ts_ms
                        """,
                        (
                            row["aggregate_kind"],
                            row["aggregate_id"],
                            row["aggregate_version"],
                            row["journal_id"],
                            row["event_digest"],
                            row["created_ts_ms"],
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {
            "journal_events": len(rows),
            "proposals": len(self.list_proposals(limit=1000)[0]),
            "documents": len(self.list_documents()),
        }

    def journal_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM canon_journal").fetchone()[0])

    def _get_command(self, command_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM canon_command WHERE command_id = ?",
                (str(command_id),),
            ).fetchone()

    def _commit_event(
        self,
        *,
        command_id: str,
        command_type: str,
        aggregate_kind: str,
        aggregate_id: str,
        expected_version: int,
        event_type: str,
        payload: Mapping[str, Any],
        actor_id: str,
        trace_id: str,
        created_ts_ms: int,
        write_compat: bool,
    ) -> dict[str, Any]:
        expected = _exact_int(expected_version, "expected_version")
        if expected < 0:
            raise CanonRepositoryError(CANON_COMMAND_INVALID, "expected_version is negative")
        canonical_payload = _canonical_json(payload)
        payload_digest = _sha256(canonical_payload)
        normalized_command = {
            "command_id": _text(command_id, "command_id", limit=240),
            "command_type": str(command_type),
            "aggregate_kind": str(aggregate_kind),
            "aggregate_id": _text(aggregate_id, "aggregate_id", limit=240),
            "expected_version": expected,
            "payload_digest": payload_digest,
            "actor_id": _text(actor_id, "actor_id", limit=240),
            "trace_id": _text(trace_id, "trace_id", limit=240),
        }
        command_hash = _sha256(_canonical_json(normalized_command))
        journal_id = f"{normalized_command['command_id']}::event"
        if len(journal_id) > 260:
            journal_id = f"canon-event::{command_hash}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT command_hash FROM canon_command WHERE command_id = ?",
                    (normalized_command["command_id"],),
                ).fetchone()
                if existing is not None:
                    if str(existing["command_hash"]) != command_hash:
                        raise CanonRepositoryError(
                            CANON_COMMAND_CONFLICT,
                            f"command replay changed: {normalized_command['command_id']}",
                        )
                    conn.execute("ROLLBACK")
                    result = (
                        self.get_proposal(aggregate_id)
                        if aggregate_kind == "PROPOSAL"
                        else self.get_document(aggregate_id)
                    )
                    return result or {"doc_id": aggregate_id, "removed": True}
                head = conn.execute(
                    """
                    SELECT version, last_event_digest FROM canon_projection_head
                    WHERE aggregate_kind = ? AND aggregate_id = ?
                    """,
                    (aggregate_kind, aggregate_id),
                ).fetchone()
                actual_version = int(head["version"]) if head is not None else 0
                if actual_version != expected:
                    raise CanonRepositoryError(
                        CANON_PROJECTION_CONFLICT,
                        f"expected={expected}, actual={actual_version}",
                    )
                next_version = expected + 1
                previous_digest = str(head["last_event_digest"]) if head is not None else None
                event_digest = _sha256(
                    _canonical_json(
                        {
                            "journal_id": journal_id,
                            "command_id": normalized_command["command_id"],
                            "aggregate_kind": aggregate_kind,
                            "aggregate_id": aggregate_id,
                            "aggregate_version": next_version,
                            "event_type": event_type,
                            "payload_digest": payload_digest,
                            "previous_event_digest": previous_digest,
                            "created_ts_ms": created_ts_ms,
                        }
                    )
                )
                conn.execute(
                    """
                    INSERT INTO canon_command (
                        command_id, command_hash, command_type, aggregate_kind,
                        aggregate_id, expected_version, payload_json, payload_digest,
                        actor_id, trace_id, created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_command["command_id"],
                        command_hash,
                        command_type,
                        aggregate_kind,
                        aggregate_id,
                        expected,
                        canonical_payload,
                        payload_digest,
                        normalized_command["actor_id"],
                        normalized_command["trace_id"],
                        created_ts_ms,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO canon_journal (
                        journal_id, command_id, aggregate_kind, aggregate_id,
                        aggregate_version, event_type, payload_json, payload_digest,
                        previous_event_digest, event_digest, created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        journal_id,
                        normalized_command["command_id"],
                        aggregate_kind,
                        aggregate_id,
                        next_version,
                        event_type,
                        canonical_payload,
                        payload_digest,
                        previous_digest,
                        event_digest,
                        created_ts_ms,
                    ),
                )
                event = {
                    "journal_id": journal_id,
                    "aggregate_kind": aggregate_kind,
                    "aggregate_id": aggregate_id,
                    "aggregate_version": next_version,
                    "event_type": event_type,
                    "event_digest": event_digest,
                    "created_ts_ms": created_ts_ms,
                }
                self._project_event(conn, event, dict(payload), rebuilding=False)
                if head is None:
                    conn.execute(
                        """
                        INSERT INTO canon_projection_head (
                            aggregate_kind, aggregate_id, version, last_journal_id,
                            last_event_digest, updated_ts_ms
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            aggregate_kind,
                            aggregate_id,
                            next_version,
                            journal_id,
                            event_digest,
                            created_ts_ms,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE canon_projection_head
                        SET version = ?, last_journal_id = ?, last_event_digest = ?,
                            updated_ts_ms = ?
                        WHERE aggregate_kind = ? AND aggregate_id = ?
                          AND version = ? AND last_event_digest = ?
                        """,
                        (
                            next_version,
                            journal_id,
                            event_digest,
                            created_ts_ms,
                            aggregate_kind,
                            aggregate_id,
                            expected,
                            previous_digest,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CanonRepositoryError(
                            CANON_PROJECTION_CONFLICT,
                            "projection head CAS failed",
                        )
                if write_compat:
                    self._write_compat_projection(conn, event_type, dict(payload))
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise CanonRepositoryError(CANON_COMMAND_CONFLICT, str(exc)) from exc
            except Exception:
                conn.execute("ROLLBACK")
                raise
        if aggregate_kind == "PROPOSAL":
            result = self.get_proposal(aggregate_id)
        else:
            result = self.get_document(aggregate_id)
        return result or {"doc_id": aggregate_id, "removed": True}

    def _project_event(
        self,
        conn: sqlite3.Connection,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        rebuilding: bool,
    ) -> None:
        event_type = str(event["event_type"])
        version = int(event["aggregate_version"])
        journal_id = str(event["journal_id"])
        if event_type in {
            "PROPOSAL_SUBMITTED",
            "PROPOSAL_REJECTED",
            "PROPOSAL_STATUS_PROJECTED",
        }:
            proposal = _normalize_proposal(payload, require_pending=False)
            created_ts = max(0, int(payload.get("created_ts_ms") or event["created_ts_ms"]))
            updated_ts = max(created_ts, int(payload.get("updated_ts_ms") or event["created_ts_ms"]))
            values = (
                proposal["proposal_id"],
                proposal["canon_kind"],
                proposal["target_id"],
                proposal["base_version"],
                proposal["proposed_version"],
                proposal["proposed_hash"],
                proposal["proposed_title"],
                _canonical_json(proposal["proposed_tags"]),
                proposal["proposed_body"],
                proposal["source_intent_id"],
                proposal["proposer_node_id"],
                proposal["summary"],
                proposal["status"],
                payload.get("status_reason"),
                payload.get("status_actor_id"),
                version,
                journal_id,
                created_ts,
                updated_ts,
            )
            if version == 1:
                conn.execute(
                    """
                    INSERT INTO canon_proposal_projection (
                        proposal_id, canon_kind, target_id, base_version,
                        proposed_version, proposed_hash, proposed_title,
                        proposed_tags_json, proposed_body, source_intent_id,
                        proposer_node_id, summary, status, status_reason,
                        status_actor_id, projection_version, last_journal_id,
                        created_ts_ms, updated_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE canon_proposal_projection SET
                        canon_kind = ?, target_id = ?, base_version = ?,
                        proposed_version = ?, proposed_hash = ?, proposed_title = ?,
                        proposed_tags_json = ?, proposed_body = ?, source_intent_id = ?,
                        proposer_node_id = ?, summary = ?, status = ?, status_reason = ?,
                        status_actor_id = ?, projection_version = ?, last_journal_id = ?,
                        created_ts_ms = ?, updated_ts_ms = ?
                    WHERE proposal_id = ? AND projection_version = ?
                    """,
                    (*values[1:], proposal["proposal_id"], version - 1),
                )
                if cursor.rowcount != 1:
                    raise CanonRepositoryError(
                        CANON_PROJECTION_CONFLICT,
                        "proposal projection CAS failed",
                    )
            return
        if event_type == "DOCUMENT_REMOVED":
            cursor = conn.execute(
                """
                DELETE FROM canon_document_projection
                WHERE doc_id = ? AND projection_version = ?
                """,
                (event["aggregate_id"], version - 1),
            )
            if cursor.rowcount != 1:
                raise CanonRepositoryError(
                    CANON_PROJECTION_CONFLICT,
                    "document removal CAS failed",
                )
            return
        if event_type != "DOCUMENT_PROJECTED":
            raise CanonRepositoryError(
                CANON_DURABLE_DATA_INVALID,
                f"unknown Canon event type: {event_type}",
            )
        document = _normalize_document(payload)
        values = (
            document["doc_id"],
            document["version"],
            document["hash"],
            document["status"],
            document["title"],
            _canonical_json(document["tags"]),
            document["body"],
            document["updated_by"],
            document["ts_ms"],
            document["source_path"],
            version,
            journal_id,
            max(0, int(payload.get("projected_ts_ms") or event["created_ts_ms"])),
        )
        if version == 1:
            conn.execute(
                """
                INSERT INTO canon_document_projection (
                    doc_id, canon_version, canon_hash, status, title, tags_json,
                    body, updated_by, canon_ts_ms, source_path, projection_version,
                    last_journal_id, projected_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        else:
            cursor = conn.execute(
                """
                UPDATE canon_document_projection SET
                    canon_version = ?, canon_hash = ?, status = ?, title = ?,
                    tags_json = ?, body = ?, updated_by = ?, canon_ts_ms = ?,
                    source_path = ?, projection_version = ?, last_journal_id = ?,
                    projected_ts_ms = ?
                WHERE doc_id = ? AND projection_version = ?
                """,
                (*values[1:], document["doc_id"], version - 1),
            )
            if cursor.rowcount != 1:
                raise CanonRepositoryError(
                    CANON_PROJECTION_CONFLICT,
                    "document projection CAS failed",
                )

    @staticmethod
    def _write_compat_projection(
        conn: sqlite3.Connection,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Maintain legacy read tables inside the same authoritative UoW.

        No application or route may write these tables after the cutover. They
        remain only because the already-governed 0053 file-apply boundary still
        consumes them during the current wave.
        """

        if event_type == "PROPOSAL_SUBMITTED":
            conn.execute(
                """
                INSERT INTO canon_proposal_log (
                    proposal_id, canon_kind, target_id, base_version,
                    proposed_version, proposed_hash, proposed_title, proposed_tags,
                    proposed_body, source_intent_id, proposer_node_id, summary,
                    status, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["proposal_id"],
                    payload["canon_kind"],
                    payload["target_id"],
                    payload["base_version"],
                    payload["proposed_version"],
                    payload["proposed_hash"],
                    payload["proposed_title"],
                    _canonical_json(payload["proposed_tags"]),
                    payload["proposed_body"],
                    payload.get("source_intent_id"),
                    payload["proposer_node_id"],
                    payload["summary"],
                    payload["status"],
                    payload["created_ts_ms"],
                ),
            )
        elif event_type == "PROPOSAL_REJECTED":
            cursor = conn.execute(
                """
                UPDATE canon_proposal_log SET status = ?, ts_ms = ?
                WHERE proposal_id = ? AND status = 'pending'
                """,
                (payload["status"], payload["updated_ts_ms"], payload["proposal_id"]),
            )
            if cursor.rowcount != 1:
                raise CanonRepositoryError(
                    CANON_PROJECTION_CONFLICT,
                    "legacy proposal projection CAS failed",
                )
        elif event_type == "DOCUMENT_PROJECTED":
            conn.execute(
                """
                INSERT INTO world_canon_doc (
                    doc_id, version, hash, status, source_path, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    version = excluded.version,
                    hash = excluded.hash,
                    status = excluded.status,
                    source_path = excluded.source_path,
                    updated_ts = excluded.updated_ts
                """,
                (
                    payload["doc_id"],
                    payload["version"],
                    payload["hash"],
                    payload["status"],
                    payload["source_path"],
                    payload["projected_ts_ms"],
                ),
            )
        elif event_type == "DOCUMENT_REMOVED":
            conn.execute("DELETE FROM world_canon_doc WHERE doc_id = ?", (payload["doc_id"],))


def _proposal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": str(row["proposal_id"]),
        "canon_kind": str(row["canon_kind"]),
        "target_id": str(row["target_id"]),
        "base_version": int(row["base_version"]),
        "proposed_version": int(row["proposed_version"]),
        "proposed_hash": str(row["proposed_hash"]),
        "proposed_title": str(row["proposed_title"]),
        "proposed_tags": _parse_json_list(row["proposed_tags_json"]),
        "proposed_body": str(row["proposed_body"]),
        "source_intent_id": row["source_intent_id"],
        "proposer_node_id": str(row["proposer_node_id"]),
        "summary": str(row["summary"]),
        "status": str(row["status"]),
        "status_reason": row["status_reason"],
        "status_actor_id": row["status_actor_id"],
        "projection_version": int(row["projection_version"]),
        "last_journal_id": str(row["last_journal_id"]),
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
        "ts_ms": int(row["updated_ts_ms"]),
    }


def _document_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(row["doc_id"]),
        "version": int(row["canon_version"]),
        "hash": str(row["canon_hash"]),
        "status": str(row["status"]),
        "title": str(row["title"]),
        "tags": _parse_json_list(row["tags_json"]),
        "body": str(row["body"]),
        "updated_by": str(row["updated_by"]),
        "ts_ms": int(row["canon_ts_ms"]),
        "source_path": str(row["source_path"]),
        "projection_version": int(row["projection_version"]),
        "last_journal_id": str(row["last_journal_id"]),
        "projected_ts_ms": int(row["projected_ts_ms"]),
    }


def _normalize_proposal(
    proposal: Mapping[str, Any],
    *,
    require_pending: bool,
) -> dict[str, Any]:
    status = str(proposal.get("status") or "pending").strip()
    if status not in _PROPOSAL_STATUSES or (require_pending and status != "pending"):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, f"invalid proposal status: {status}")
    base_version = _exact_int(proposal.get("base_version"), "base_version")
    proposed_version = _exact_int(proposal.get("proposed_version"), "proposed_version")
    if base_version < 0 or proposed_version != base_version + 1:
        raise CanonRepositoryError(
            CANON_COMMAND_INVALID,
            "proposal versions are invalid",
        )
    digest = str(proposal.get("proposed_hash") or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "proposed_hash is invalid")
    canon_kind = str(proposal.get("canon_kind") or "world").strip()
    if canon_kind != "world":
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "only world Canon is supported")
    return {
        "proposal_id": _text(proposal.get("proposal_id"), "proposal_id", limit=240),
        "canon_kind": canon_kind,
        "target_id": _text(proposal.get("target_id"), "target_id", limit=160),
        "base_version": base_version,
        "proposed_version": proposed_version,
        "proposed_hash": digest,
        "proposed_title": _text(proposal.get("proposed_title"), "proposed_title"),
        "proposed_tags": _normalize_tags(proposal.get("proposed_tags")),
        "proposed_body": _text(proposal.get("proposed_body"), "proposed_body"),
        "source_intent_id": _optional_text(proposal.get("source_intent_id"), limit=240),
        "proposer_node_id": _text(
            proposal.get("proposer_node_id"), "proposer_node_id", limit=240
        ),
        "summary": str(proposal.get("summary") or "")[:1000],
        "status": status,
    }


def _normalize_document(document: Mapping[str, Any]) -> dict[str, Any]:
    version = _exact_int(document.get("version"), "version")
    ts_ms = _exact_int(document.get("ts_ms"), "ts_ms")
    if version < 1 or ts_ms < 0:
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "document version/time is invalid")
    digest = str(document.get("hash") or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "document hash is invalid")
    return {
        "doc_id": _text(document.get("doc_id"), "doc_id", limit=160),
        "version": version,
        "hash": digest,
        "status": _text(document.get("status"), "status", limit=80),
        "title": _text(document.get("title"), "title"),
        "tags": _normalize_tags(document.get("tags")),
        "body": _text(document.get("body"), "body"),
        "updated_by": _text(document.get("updated_by"), "updated_by", limit=240),
        "ts_ms": ts_ms,
        "source_path": _text(document.get("source_path"), "source_path"),
    }


def _proposal_semantics(proposal: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_proposal(proposal, require_pending=False)
    return normalized


def _document_semantics(document: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_document(document)


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = _parse_json_list(value)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "tags must be a list")
    result: list[str] = []
    for item in value:
        tag = str(item).strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CanonRepositoryError(CANON_DURABLE_DATA_INVALID, "tags JSON is invalid") from exc
    if not isinstance(decoded, list):
        raise CanonRepositoryError(CANON_DURABLE_DATA_INVALID, "tags JSON is not a list")
    return [str(item) for item in decoded]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "payload is not canonical JSON") from exc


def _decode_json(value: Any, expected_digest: Any) -> dict[str, Any]:
    raw = str(value or "")
    if _sha256(raw) != str(expected_digest or ""):
        raise CanonRepositoryError(CANON_DURABLE_DATA_INVALID, "payload digest mismatch")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CanonRepositoryError(CANON_DURABLE_DATA_INVALID, "payload JSON is invalid") from exc
    if not isinstance(decoded, dict) or _canonical_json(decoded) != raw:
        raise CanonRepositoryError(CANON_DURABLE_DATA_INVALID, "payload is not canonical")
    return decoded


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, field_name: str, *, limit: int = 20_000) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit or any(ord(char) < 32 for char in result):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, f"{field_name} is invalid")
    return result


def _optional_text(value: Any, *, limit: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _text(value, "optional text", limit=limit)


def _exact_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonRepositoryError(CANON_COMMAND_INVALID, f"{field_name} must be an integer")
    return value


def _timestamp(value: int | None, fallback: int) -> int:
    result = fallback if value is None else _exact_int(value, "timestamp")
    if result < 0:
        raise CanonRepositoryError(CANON_COMMAND_INVALID, "timestamp is negative")
    return result


__all__ = [
    "CANON_COMMAND_CONFLICT",
    "CANON_COMMAND_INVALID",
    "CANON_DURABLE_DATA_INVALID",
    "CANON_MIGRATION_ASSET_KEY",
    "CANON_MIGRATION_SHA256",
    "CANON_PROJECTION_CONFLICT",
    "CANON_SCHEMA_NOT_READY",
    "CanonRepository",
    "CanonRepositoryError",
]
