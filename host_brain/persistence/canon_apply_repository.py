"""SQLite authority and operation-scoped UoW for governed Canon apply.

Domain validation and filesystem mutation belong to ``CanonApplyService``.
This adapter owns migration 0053 readiness, connections, SQL, CAS operations,
and the transaction joining subject preparation, token burn, and journaling.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


CANON_APPLY_MIGRATION_ASSET = "0053_canon_apply.sql"
CANON_APPLY_MIGRATION_SHA256 = (
    "5c712b40f7184f5f47bcc50be3e3516f59d4a5ec6342e9a4f07c64f13f54e54b"
)
CANON_APPLY_SCHEMA_MANIFEST_SHA256 = (
    "dfa8f618f721ec87edc7af5097702279577d30b0b06eb1ee8b6879b92b160c09"
)


class CanonApplyRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code if not self.detail else f"{self.reason_code}:{self.detail}"
        )


def canon_apply_migration_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "audit"
        / "migrations"
        / CANON_APPLY_MIGRATION_ASSET
    )


def canon_apply_schema_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = ("canon_apply_token", "canon_apply_journal")
    master_rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE tbl_name IN ('canon_apply_token', 'canon_apply_journal')
          AND type IN ('table', 'index', 'trigger')
        ORDER BY type, name
        """
    ).fetchall()
    master = [
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3] or "").split()),
        )
        for row in master_rows
    ]
    table_info: dict[str, list[tuple[Any, ...]]] = {}
    foreign_keys: dict[str, list[tuple[Any, ...]]] = {}
    indexes: dict[str, list[tuple[Any, ...]]] = {}
    index_columns: dict[str, list[tuple[Any, ...]]] = {}
    for table in tables:
        table_info[table] = [
            tuple(row) for row in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()
        ]
        foreign_keys[table] = sorted(
            (
                tuple(row)
                for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            ),
            key=repr,
        )
        index_rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
        indexes[table] = sorted(
            (
                (str(row[1]), int(row[2]), str(row[3]), int(row[4]))
                for row in index_rows
            ),
            key=repr,
        )
        for row in index_rows:
            name = str(row[1])
            index_columns[name] = [
                tuple(value)
                for value in conn.execute(f"PRAGMA index_xinfo({name})").fetchall()
            ]
    return {
        "master": master,
        "table_info": table_info,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
        "index_columns": index_columns,
    }


def assert_canon_apply_schema_ready(
    conn: sqlite3.Connection, *, migration_path: Path | None = None
) -> None:
    asset = migration_path or canon_apply_migration_path()
    if not asset.is_file():
        raise RuntimeError(f"CANON_APPLY_SCHEMA_MISSING:{asset}")
    sql = asset.read_text(encoding="utf-8-sig")
    if "canon_apply_token" not in sql or "canon_apply_journal" not in sql:
        raise RuntimeError("CANON_APPLY_SCHEMA_INVALID")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if checksum != CANON_APPLY_MIGRATION_SHA256:
        raise RuntimeError("CANON_APPLY_SCHEMA_INVALID:asset_checksum")
    try:
        migration_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if migration_table is None:
            raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_missing")
        migrations = conn.execute(
            "SELECT name, checksum FROM schema_migrations WHERE migration_id=53"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_invalid") from exc
    if (
        len(migrations) != 1
        or str(migrations[0][0]) != "canon_apply"
        or str(migrations[0][1]) != CANON_APPLY_MIGRATION_SHA256
    ):
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:central_migration_mismatch")
    if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:foreign_keys_disabled")
    try:
        actual_manifest = canon_apply_schema_manifest(conn)
        referenced_objects = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"
        ).fetchall()
        referenced_objects.extend(
            conn.execute(
                "SELECT type, name, sql FROM sqlite_temp_master "
                "WHERE type IN ('trigger', 'view')"
            ).fetchall()
        )
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:schema_introspection") from exc
    for object_type, name, object_sql in referenced_objects:
        normalized = str(object_sql or "").lower()
        if "canon_apply_token" in normalized or "canon_apply_journal" in normalized:
            raise RuntimeError(
                "CANON_APPLY_SCHEMA_NOT_READY:unexpected_schema_object:"
                f"{object_type}:{name}"
            )
    manifest_digest = hashlib.sha256(
        json.dumps(
            actual_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if manifest_digest != CANON_APPLY_SCHEMA_MANIFEST_SHA256:
        raise RuntimeError("CANON_APPLY_SCHEMA_NOT_READY:schema_fingerprint")


class CanonApplySqliteAuthority:
    """Shared 0053 connection/readiness authority for apply and token repos."""

    def __init__(self, db_path: str | Path, *, timeout_ms: int = 30_000) -> None:
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.timeout_ms = max(1, int(timeout_ms))

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.timeout_ms}")
        return conn

    def assert_ready(self) -> None:
        with self.connect() as conn:
            assert_canon_apply_schema_ready(conn)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


_SUBJECT_COORDINATES = {
    "canon_proposal_log": "proposal_id",
    "pentest_knowledge_promotion_candidate": "candidate_id",
}


class CanonApplyUnitOfWork:
    """Semantic DB operations available inside one repository transaction."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_token(self, token_digest: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM canon_apply_token WHERE token_digest=?", (token_digest,)
            ).fetchone()
        )

    def get_journal(self, apply_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM canon_apply_journal WHERE apply_id=?", (apply_id,)
            ).fetchone()
        )

    def find_active_target(self, target_doc_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT apply_id FROM canon_apply_journal WHERE target_doc_id=? "
                "AND state IN ('PREPARED','RECOVERY_REQUIRED') LIMIT 1",
                (target_doc_id,),
            ).fetchone()
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM canon_proposal_log WHERE proposal_id=? "
                "ORDER BY id DESC LIMIT 1",
                (proposal_id,),
            ).fetchone()
        )

    def get_promotion_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM pentest_knowledge_promotion_candidate WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        )

    def get_pentest_entry(self, entry_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM pentest_knowledge_entry WHERE entry_id=?", (entry_id,)
            ).fetchone()
        )

    def latest_applied_subject(self, subject_id: str) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM canon_apply_journal WHERE operation='APPLY' "
                "AND subject_type='CANON_PROPOSAL' AND subject_id=? AND state='APPLIED' "
                "ORDER BY completed_ts_ms DESC, prepared_ts_ms DESC LIMIT 1",
                (subject_id,),
            ).fetchone()
        )

    def applied_rollback_source(
        self,
        *,
        subject_id: str,
        target_doc_id: str,
        after_version: int,
        after_hash: str,
    ) -> dict[str, Any] | None:
        return _dict(
            self._conn.execute(
                "SELECT * FROM canon_apply_journal WHERE operation='APPLY' "
                "AND subject_type='CANON_PROPOSAL' AND subject_id=? "
                "AND target_doc_id=? AND state='APPLIED' AND after_version=? "
                "AND after_hash=? ORDER BY completed_ts_ms DESC, prepared_ts_ms DESC "
                "LIMIT 1",
                (subject_id, target_doc_id, after_version, after_hash),
            ).fetchone()
        )

    def list_apply_predecessors(
        self,
        *,
        target_doc_id: str,
        after_version: int,
        after_hash: str,
        exclude_apply_id: str,
        completed_before_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM canon_apply_journal WHERE state='APPLIED' "
            "AND operation='APPLY' AND target_doc_id=? AND after_version=? "
            "AND after_hash=? AND apply_id<>? AND completed_ts_ms IS NOT NULL "
            "AND completed_ts_ms<=? ORDER BY completed_ts_ms DESC, "
            "prepared_ts_ms DESC, apply_id DESC LIMIT ?",
            (
                target_doc_id,
                after_version,
                after_hash,
                exclude_apply_id,
                completed_before_ms,
                limit,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def cas_subject_status(
        self,
        *,
        table: str,
        subject_id: str,
        expected_status: str,
        new_status: str,
    ) -> bool:
        key = _SUBJECT_COORDINATES.get(table)
        if key is None:
            raise CanonApplyRepositoryError(
                "CANON_APPLY_JOURNAL_SUBJECT_INVALID", table
            )
        cursor = self._conn.execute(
            f"UPDATE {table} SET status=? WHERE {key}=? AND status=?",
            (new_status, subject_id, expected_status),
        )
        return int(cursor.rowcount or 0) == 1

    def burn_token(self, values: Mapping[str, Any]) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE canon_apply_token
            SET state='BURNED', burned_apply_id=?, burned_ts_ms=?
            WHERE token_digest=? AND state='AVAILABLE' AND token_type=?
              AND operation=? AND subject_type=? AND subject_id=?
              AND subject_version=? AND subject_hash=? AND target_doc_id=?
              AND base_version=? AND actor_id=? AND session_id=? AND trace_id=?
              AND evidence_digest=? AND expiry_ts_ms>?
            """,
            tuple(
                values[key]
                for key in (
                    "apply_id",
                    "burned_ts_ms",
                    "token_digest",
                    "token_type",
                    "operation",
                    "subject_type",
                    "subject_id",
                    "subject_version",
                    "subject_hash",
                    "target_doc_id",
                    "base_version",
                    "actor_id",
                    "session_id",
                    "trace_id",
                    "evidence_digest",
                    "now_ts_ms",
                )
            ),
        )
        return int(cursor.rowcount or 0) == 1

    def insert_journal(self, values: Mapping[str, Any]) -> None:
        keys = (
            "apply_id",
            "command_hash",
            "operation",
            "subject_type",
            "subject_id",
            "subject_version",
            "subject_hash",
            "target_doc_id",
            "base_version",
            "token_digest",
            "actor_id",
            "session_id",
            "trace_id",
            "evidence_refs_json",
            "evidence_digest",
            "authority_key_id",
            "approval_attestation_digest",
            "subject_previous_status",
            "subject_prepared_status",
            "subject_final_status",
            "before_payload_json",
            "target_payload_json",
            "before_version",
            "before_hash",
            "after_version",
            "after_hash",
            "recovery_not_before_ts_ms",
            "lease_owner",
            "lease_expiry_ts_ms",
            "prepared_ts_ms",
            "updated_ts_ms",
        )
        try:
            self._conn.execute(
                """
                INSERT INTO canon_apply_journal(
                    apply_id, command_hash, operation, subject_type, subject_id,
                    subject_version, subject_hash, target_doc_id, base_version,
                    token_digest, actor_id, session_id, trace_id,
                    evidence_refs_json, evidence_digest, authority_key_id,
                    approval_attestation_digest, state, subject_previous_status,
                    subject_prepared_status, subject_final_status,
                    before_payload_json, target_payload_json, before_version,
                    before_hash, after_version, after_hash, fence_version,
                    file_replaced, recovery_not_before_ts_ms, lease_owner,
                    lease_expiry_ts_ms, prepared_ts_ms, updated_ts_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PREPARED',?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?)
                """,
                tuple(values[key] for key in keys),
            )
        except sqlite3.IntegrityError as exc:
            if "canon_apply_journal.target_doc_id" in str(exc):
                raise CanonApplyRepositoryError("CANON_APPLY_TARGET_BUSY") from exc
            raise

    def insert_change_log(self, values: Mapping[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO canon_change_log(
                change_id, proposal_id, canon_kind, target_id, before_version,
                after_version, before_hash, after_hash, before_body, after_body,
                source_path, ts_ms
            ) VALUES(?,?,'world',?,?,?,?,?,?,?,?,?)
            """,
            tuple(
                values[key]
                for key in (
                    "change_id",
                    "proposal_id",
                    "target_id",
                    "before_version",
                    "after_version",
                    "before_hash",
                    "after_hash",
                    "before_body",
                    "after_body",
                    "source_path",
                    "ts_ms",
                )
            ),
        )

    def project_world_document(
        self,
        target: Mapping[str, Any] | None,
        *,
        doc_id: str,
        source_path: str,
        now_ms: int,
    ) -> None:
        if target is None:
            self._conn.execute(
                "DELETE FROM world_canon_doc WHERE doc_id=?",
                (doc_id,),
            )
            return
        self._conn.execute(
            """
            INSERT INTO world_canon_doc(doc_id, version, hash, status, source_path, updated_ts)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(doc_id) DO UPDATE SET version=excluded.version,
                hash=excluded.hash, status=excluded.status,
                source_path=excluded.source_path, updated_ts=excluded.updated_ts
            """,
            (
                target["doc_id"],
                target["version"],
                target["hash"],
                target["status"],
                source_path,
                now_ms,
            ),
        )

    def finalize_journal(
        self,
        *,
        apply_id: str,
        fence: int,
        lease_owner: str,
        now_ms: int,
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE canon_apply_journal SET state='APPLIED', file_replaced=1, "
            "reason_code=NULL, detail=NULL, lease_owner=NULL, lease_expiry_ts_ms=?, "
            "updated_ts_ms=?, completed_ts_ms=? WHERE apply_id=? AND fence_version=? "
            "AND lease_owner=? AND state IN ('PREPARED','RECOVERY_REQUIRED')",
            (now_ms, now_ms, now_ms, apply_id, fence, lease_owner),
        )
        return int(cursor.rowcount or 0) == 1

    def record_failure(
        self,
        *,
        apply_id: str,
        fence: int,
        lease_owner: str,
        state: str,
        reason_code: str,
        detail: str,
        now_ms: int,
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE canon_apply_journal SET state=?, reason_code=?, detail=?, "
            "recovery_not_before_ts_ms=CASE WHEN ?='RECOVERY_REQUIRED' THEN ? "
            "ELSE recovery_not_before_ts_ms END, lease_owner=NULL, "
            "lease_expiry_ts_ms=?, updated_ts_ms=? WHERE apply_id=? "
            "AND fence_version=? AND lease_owner=? "
            "AND state IN ('PREPARED','RECOVERY_REQUIRED')",
            (
                state,
                reason_code[:120],
                detail[:1000],
                state,
                now_ms,
                now_ms,
                now_ms,
                apply_id,
                fence,
                lease_owner,
            ),
        )
        return int(cursor.rowcount or 0) == 1

    def quarantine_recovery(
        self,
        *,
        apply_id: str,
        detail: str,
        recovery_not_before_ms: int,
        now_ms: int,
        fence: int | None = None,
    ) -> None:
        if fence is None:
            self._conn.execute(
                "UPDATE canon_apply_journal SET state='RECOVERY_REQUIRED', "
                "reason_code='CANON_APPLY_JOURNAL_INTEGRITY_FAILED', detail=?, "
                "recovery_not_before_ts_ms=?, lease_owner=NULL, "
                "lease_expiry_ts_ms=?, updated_ts_ms=? WHERE apply_id=? "
                "AND state IN ('PREPARED','RECOVERY_REQUIRED')",
                (detail[:1000], recovery_not_before_ms, now_ms, now_ms, apply_id),
            )
        else:
            self._conn.execute(
                "UPDATE canon_apply_journal SET state='RECOVERY_REQUIRED', "
                "reason_code='CANON_APPLY_JOURNAL_INTEGRITY_FAILED', detail=?, "
                "recovery_not_before_ts_ms=?, lease_owner=NULL, "
                "lease_expiry_ts_ms=?, updated_ts_ms=? WHERE apply_id=? "
                "AND fence_version=? AND state IN ('PREPARED','RECOVERY_REQUIRED')",
                (
                    detail[:1000],
                    recovery_not_before_ms,
                    now_ms,
                    now_ms,
                    apply_id,
                    fence,
                ),
            )

    def claim_recovery(
        self,
        *,
        apply_id: str,
        old_fence: int,
        lease_owner: str,
        lease_expiry_ms: int,
        now_ms: int,
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE canon_apply_journal SET state='RECOVERY_REQUIRED', "
            "fence_version=fence_version+1, "
            "reason_code='CANON_APPLY_RECOVERY_CLAIMED', lease_owner=?, "
            "lease_expiry_ts_ms=?, updated_ts_ms=? WHERE apply_id=? "
            "AND fence_version=? AND state IN ('PREPARED','RECOVERY_REQUIRED') "
            "AND recovery_not_before_ts_ms<=? AND lease_expiry_ts_ms<=?",
            (
                lease_owner,
                lease_expiry_ms,
                now_ms,
                apply_id,
                old_fence,
                now_ms,
                now_ms,
            ),
        )
        return int(cursor.rowcount or 0) == 1


class CanonApplyRepository:
    """Application-facing persistence port for Canon apply/recovery."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        authority: CanonApplySqliteAuthority | None = None,
    ) -> None:
        self.authority = authority or CanonApplySqliteAuthority(db_path)
        self.db_path = self.authority.db_path

    def assert_ready(self) -> None:
        self.authority.assert_ready()

    @contextmanager
    def read_uow(self) -> Iterator[CanonApplyUnitOfWork]:
        with self.authority.read() as conn:
            yield CanonApplyUnitOfWork(conn)

    @contextmanager
    def transaction(self) -> Iterator[CanonApplyUnitOfWork]:
        with self.authority.write() as conn:
            yield CanonApplyUnitOfWork(conn)

    def get_journal(self, apply_id: str) -> dict[str, Any] | None:
        with self.read_uow() as uow:
            return uow.get_journal(apply_id)

    def get_promotion_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self.read_uow() as uow:
            return uow.get_promotion_candidate(candidate_id)

    def list_recoverable_apply_ids(
        self, *, now_ms: int, limit: int
    ) -> list[str]:
        with self.authority.read() as conn:
            rows = conn.execute(
                "SELECT apply_id FROM canon_apply_journal "
                "WHERE state IN ('PREPARED','RECOVERY_REQUIRED') "
                "AND recovery_not_before_ts_ms<=? AND lease_expiry_ts_ms<=? "
                "ORDER BY prepared_ts_ms ASC, apply_id ASC LIMIT ?",
                (now_ms, now_ms, limit),
            ).fetchall()
        return [str(row["apply_id"]) for row in rows]

    def mark_file_replaced(
        self, *, apply_id: str, fence: int, lease_owner: str, now_ms: int
    ) -> bool:
        with self.authority.write() as conn:
            cursor = conn.execute(
                "UPDATE canon_apply_journal SET file_replaced=1, updated_ts_ms=? "
                "WHERE apply_id=? AND fence_version=? AND lease_owner=? "
                "AND state IN ('PREPARED','RECOVERY_REQUIRED')",
                (now_ms, apply_id, fence, lease_owner),
            )
            return int(cursor.rowcount or 0) == 1

    def renew_lease(
        self,
        *,
        apply_id: str,
        fence: int,
        lease_owner: str,
        lease_expiry_ms: int,
        now_ms: int,
    ) -> bool:
        with self.authority.write() as conn:
            cursor = conn.execute(
                "UPDATE canon_apply_journal SET lease_expiry_ts_ms=?, updated_ts_ms=? "
                "WHERE apply_id=? AND fence_version=? AND lease_owner=? "
                "AND state IN ('PREPARED','RECOVERY_REQUIRED')",
                (lease_expiry_ms, now_ms, apply_id, fence, lease_owner),
            )
            return int(cursor.rowcount or 0) == 1


__all__ = [
    "CANON_APPLY_MIGRATION_ASSET",
    "CANON_APPLY_MIGRATION_SHA256",
    "CANON_APPLY_SCHEMA_MANIFEST_SHA256",
    "CanonApplyRepository",
    "CanonApplyRepositoryError",
    "CanonApplySqliteAuthority",
    "CanonApplyUnitOfWork",
    "assert_canon_apply_schema_ready",
    "canon_apply_migration_path",
    "canon_apply_schema_manifest",
]
