"""Atomic SQLite repository for governed LifeMemory chat admission.

This adapter owns its connection and transaction.  It never reaches through
``SQLiteLedger._conn`` and never creates schema at runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
import uuid

from host_brain.core.ids import now_ms
from host_brain.memory.life_memory.contracts import (
    ChatMemoryCandidate,
    ChatMemoryWritePlan,
    MemoryAdmissionClaim,
    MemoryAdmissionDecision,
    MemoryAdmissionKind,
    MemoryWriteReceipt,
)


LIFE_MEMORY_SCHEMA_NOT_READY = "LIFE_MEMORY_SCHEMA_NOT_READY"
LIFE_MEMORY_ADMISSION_INVALID = "LIFE_MEMORY_ADMISSION_INVALID"
LIFE_MEMORY_ADMISSION_CONFLICT = "LIFE_MEMORY_ADMISSION_CONFLICT"
LIFE_MEMORY_ADMISSION_CLAIM_LOST = "LIFE_MEMORY_ADMISSION_CLAIM_LOST"
LIFE_MEMORY_DURABLE_DATA_INVALID = "LIFE_MEMORY_DURABLE_DATA_INVALID"

LIFE_MEMORY_MIGRATION_ASSET_KEY = "0063#life_memory_admission"
# Filled from the frozen SQL asset.  Schema verification also checks the exact
# table/index shape, so a stale or locally edited catalog fails closed.
LIFE_MEMORY_MIGRATION_SHA256 = "1b44c80975c275fae713344e1189efe57d43cec223c3931de122231dda82cb66"

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "life_episode": frozenset(
        {
            "episode_id",
            "episode_kind",
            "thread_id",
            "person_id",
            "session_id",
            "primary_ref",
            "summary",
            "salience",
            "truth_state",
            "status",
            "created_ts_ms",
            "last_touched_ts_ms",
            "metadata",
        }
    ),
    "life_evidence": frozenset(
        {
            "evidence_id",
            "episode_id",
            "evidence_kind",
            "source_ref",
            "request_id",
            "content",
            "payload",
            "trust_level",
            "created_ts_ms",
        }
    ),
    "life_memory_admission_receipt": frozenset(
        {
            "request_id",
            "command_hash",
            "decision",
            "status",
            "reason_code",
            "worth_score",
            "tags_json",
            "episode_id",
            "outbox_id",
            "created_ts_ms",
        }
    ),
    "life_memory_admission_claim": frozenset(
        {
            "request_id",
            "command_hash",
            "owner_token",
            "claim_expires_ts_ms",
            "attempt_count",
            "created_ts_ms",
            "updated_ts_ms",
        }
    ),
    "life_memory_projection_outbox": frozenset(
        {
            "outbox_id",
            "episode_id",
            "request_id",
            "event_type",
            "payload_json",
            "status",
            "available_ts_ms",
            "attempt_count",
            "claim_owner",
            "claim_expires_ts_ms",
            "last_error",
            "created_ts_ms",
            "updated_ts_ms",
            "published_ts_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "uq_life_memory_admission_episode",
        "uq_life_memory_projection_episode",
        "idx_life_memory_projection_pending",
        "idx_life_memory_projection_claim_expiry",
        "idx_life_memory_admission_claim_expiry",
    }
)


class LifeMemoryRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or LIFE_MEMORY_ADMISSION_INVALID)
        self.detail = str(detail or "life memory persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True)
class SemanticLineageBatch:
    """Typed, read-only-shaped snapshot for semantic-memory provenance.

    The mappings deliberately expose records rather than a SQLite handle so
    retrieval policy can resolve lineage without crossing the persistence
    boundary.  Each tuple retains the repository-defined source ordering.
    """

    episodes_by_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    evidence_by_episode_id: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    collection_items_by_collection_id: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    entity_mentions_by_entity_id: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    corrections_by_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


class LifeMemoryRepository:
    """One UoW for episode, evidence, receipt and projection intent."""

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
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "LifeMemoryRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
        self._shared_connection: sqlite3.Connection | None = None
        self._shared_write_lock: threading.RLock | None = None
        if verify_schema:
            self.assert_schema_ready()

    @classmethod
    def from_ledger(cls, ledger: Any) -> "LifeMemoryRepository":
        """Bind legacy LifeMemory callers to the canonical ledger database.

        Durable databases still use operation-scoped connections.  The shared
        connection branch exists only for SQLite's process-local ``:memory:``
        fixture databases, which cannot be reopened by path; transactions are
        nevertheless scoped and fenced by the repository-owned UoW.
        """

        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "ledger canonical_db_path is required",
            )
        if db_path != ":memory:":
            return cls(db_path, verify_schema=False)
        instance = cls.__new__(cls)
        instance.db_path = Path(":memory:")
        instance._clock = now_ms
        instance._timeout_ms = 5_000
        instance._fault_injector = None
        instance._shared_connection = getattr(ledger, "_conn")
        instance._shared_write_lock = getattr(ledger, "_write_lock")
        return instance

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
    def _read_operation(self) -> Iterator[sqlite3.Connection]:
        shared = self._shared_connection
        if shared is not None:
            lock = self._shared_write_lock
            if lock is None:
                raise LifeMemoryRepositoryError(
                    LIFE_MEMORY_DURABLE_DATA_INVALID,
                    "shared in-memory repository has no transaction lock",
                )
            with lock:
                yield shared
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _write_uow(self) -> Iterator[sqlite3.Connection]:
        shared = self._shared_connection
        if shared is not None:
            lock = self._shared_write_lock
            if lock is None:
                raise LifeMemoryRepositoryError(
                    LIFE_MEMORY_DURABLE_DATA_INVALID,
                    "shared in-memory repository has no transaction lock",
                )
            with lock:
                nested = shared.in_transaction
                savepoint = f"life_memory_uow_{uuid.uuid4().hex}"
                if nested:
                    shared.execute(f'SAVEPOINT "{savepoint}"')
                else:
                    shared.execute("BEGIN IMMEDIATE")
                try:
                    yield shared
                    if nested:
                        shared.execute(f'RELEASE SAVEPOINT "{savepoint}"')
                    else:
                        shared.commit()
                except BaseException:
                    if nested:
                        shared.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
                        shared.execute(f'RELEASE SAVEPOINT "{savepoint}"')
                    elif shared.in_transaction:
                        shared.rollback()
                    raise
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "clock returned an invalid value",
            ) from exc
        if value < 0:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "clock must be non-negative",
            )
        return value

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(str(stage))

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_SCHEMA_NOT_READY,
                f"life memory database does not exist: {self.db_path}",
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
                    (LIFE_MEMORY_MIGRATION_ASSET_KEY,),
                ).fetchone()
                missing_columns: dict[str, list[str]] = {}
                for table, required in _REQUIRED_TABLE_COLUMNS.items():
                    actual = {
                        str(row[1])
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    }
                    missing = sorted(required - actual)
                    if missing:
                        missing_columns[table] = missing
                indexes: set[str] = set()
                for table in (
                    "life_memory_admission_claim",
                    "life_memory_admission_receipt",
                    "life_memory_projection_outbox",
                ):
                    indexes.update(
                        str(row[1])
                        for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()
                    )
                foreign_keys_enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_SCHEMA_NOT_READY,
                "life memory schema cannot be verified",
            ) from exc

        expected_checksum = LIFE_MEMORY_MIGRATION_SHA256
        asset_invalid = (
            asset is None
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or str(asset["checksum"]) != expected_checksum
        )
        missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
        if asset_invalid or missing_columns or missing_indexes or foreign_keys_enabled != 1:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_SCHEMA_NOT_READY,
                "life memory schema is stale: "
                f"asset_valid={not asset_invalid}, missing_columns={missing_columns}, "
                f"missing_indexes={missing_indexes}, foreign_keys={foreign_keys_enabled}",
            )

    def get_receipt(
        self,
        request_id: str,
        *,
        expected_command_hash: str | None = None,
    ) -> MemoryWriteReceipt | None:
        request = str(request_id or "").strip()
        if not request:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "request_id is required",
            )
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM life_memory_admission_receipt WHERE request_id = ?",
                    (request,),
                ).fetchone()
                if row is None:
                    return None
                self._assert_replay_hash(row, expected_command_hash)
                return self._receipt_from_row(
                    row,
                    replayed=True,
                    projection_status=self._projection_status(conn, row["outbox_id"]),
                )
            finally:
                conn.close()
        except LifeMemoryRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "life memory receipt lookup failed",
            ) from exc

    def try_claim_chat_admission(
        self,
        candidate: ChatMemoryCandidate,
        *,
        owner_token: str,
        lease_ms: int = 60_000,
    ) -> MemoryAdmissionClaim | MemoryWriteReceipt | None:
        """Try to become the durable policy owner for one chat request.

        ``None`` means another live owner holds the lease.  A receipt means the
        request already committed.  The request hash is checked against both
        durable states so payload drift fails before policy evaluation.
        """

        if not isinstance(candidate, ChatMemoryCandidate):
            raise TypeError("candidate must be ChatMemoryCandidate")
        owner = str(owner_token or "").strip()
        if not owner or len(owner) > 240:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "owner_token is required and must not exceed 240 characters",
            )
        lease_duration = max(1, int(lease_ms))
        now_value = self._now()
        expires = now_value + lease_duration
        command_hash = candidate.command_hash
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                receipt_row = conn.execute(
                    "SELECT * FROM life_memory_admission_receipt WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if receipt_row is not None:
                    self._assert_replay_hash(receipt_row, command_hash)
                    receipt = self._receipt_from_row(
                        receipt_row,
                        replayed=True,
                        projection_status=self._projection_status(
                            conn,
                            receipt_row["outbox_id"],
                        ),
                    )
                    conn.commit()
                    return receipt

                claim_row = conn.execute(
                    "SELECT * FROM life_memory_admission_claim WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if claim_row is not None:
                    self._assert_replay_hash(claim_row, command_hash)
                    if (
                        str(claim_row["owner_token"]) != owner
                        and int(claim_row["claim_expires_ts_ms"]) > now_value
                    ):
                        conn.commit()
                        return None
                    conn.execute(
                        """
                        UPDATE life_memory_admission_claim
                        SET owner_token = ?, claim_expires_ts_ms = ?,
                            attempt_count = attempt_count + ?, updated_ts_ms = ?
                        WHERE request_id = ? AND command_hash = ?
                        """,
                        (
                            owner,
                            expires,
                            0 if str(claim_row["owner_token"]) == owner else 1,
                            now_value,
                            candidate.request_id,
                            command_hash,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO life_memory_admission_claim (
                            request_id, command_hash, owner_token,
                            claim_expires_ts_ms, attempt_count, created_ts_ms,
                            updated_ts_ms
                        ) VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            candidate.request_id,
                            command_hash,
                            owner,
                            expires,
                            now_value,
                            now_value,
                        ),
                    )
                owned_row = conn.execute(
                    "SELECT * FROM life_memory_admission_claim WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if owned_row is None or str(owned_row["owner_token"]) != owner:
                    raise LifeMemoryRepositoryError(
                        LIFE_MEMORY_DURABLE_DATA_INVALID,
                        "admission claim disappeared before ownership was recorded",
                    )
                claim = self._claim_from_row(owned_row)
                conn.commit()
                return claim
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()
        except LifeMemoryRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "life memory admission claim failed",
            ) from exc

    def release_chat_admission_claim(self, claim: MemoryAdmissionClaim) -> bool:
        """Release a live claim after a handled policy/commit failure."""

        if not isinstance(claim, MemoryAdmissionClaim):
            raise TypeError("claim must be MemoryAdmissionClaim")
        try:
            conn = self._connect()
            try:
                with conn:
                    changed = conn.execute(
                        """
                        DELETE FROM life_memory_admission_claim
                        WHERE request_id = ? AND command_hash = ? AND owner_token = ?
                        """,
                        (claim.request_id, claim.command_hash, claim.owner_token),
                    ).rowcount
                return changed == 1
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "life memory admission claim release failed",
            ) from exc

    def commit_chat_admission(
        self,
        *,
        candidate: ChatMemoryCandidate,
        claim: MemoryAdmissionClaim,
        decision: MemoryAdmissionDecision,
        plan: ChatMemoryWritePlan | None,
    ) -> MemoryWriteReceipt:
        if not isinstance(candidate, ChatMemoryCandidate):
            raise TypeError("candidate must be ChatMemoryCandidate")
        if not isinstance(decision, MemoryAdmissionDecision):
            raise TypeError("decision must be MemoryAdmissionDecision")
        if not isinstance(claim, MemoryAdmissionClaim):
            raise TypeError("claim must be MemoryAdmissionClaim")
        if decision.writes_episode != (plan is not None):
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "write plan does not match admission decision",
            )
        command_hash = candidate.command_hash
        created_ts_ms = self._now()
        outbox_id = (
            f"life-memory-projection::{plan.episode_id}"
            if decision.emits_projection and plan is not None
            else None
        )
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT * FROM life_memory_admission_receipt WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if existing is not None:
                    self._assert_replay_hash(existing, command_hash)
                    receipt = self._receipt_from_row(
                        existing,
                        replayed=True,
                        projection_status=self._projection_status(conn, existing["outbox_id"]),
                    )
                    conn.commit()
                    return receipt

                claim_row = conn.execute(
                    "SELECT * FROM life_memory_admission_claim WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if (
                    claim_row is None
                    or str(claim_row["command_hash"]) != command_hash
                    or str(claim_row["owner_token"]) != claim.owner_token
                    or int(claim_row["claim_expires_ts_ms"]) <= created_ts_ms
                ):
                    raise LifeMemoryRepositoryError(
                        LIFE_MEMORY_ADMISSION_CLAIM_LOST,
                        "admission policy owner lost its durable fencing lease",
                    )

                if plan is not None:
                    self._insert_episode(conn, plan)
                    self._fault("after_episode")
                    self._insert_evidence(conn, plan)
                    self._fault("after_evidence")

                status = "SKIPPED" if decision.kind is MemoryAdmissionKind.SKIP else "COMMITTED"
                conn.execute(
                    """
                    INSERT INTO life_memory_admission_receipt (
                        request_id, command_hash, decision, status, reason_code,
                        worth_score, tags_json, episode_id, outbox_id, created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.request_id,
                        command_hash,
                        decision.kind.value,
                        status,
                        decision.reason_code,
                        decision.worth_score,
                        _json_dumps(list(decision.tags)),
                        plan.episode_id if plan is not None else None,
                        outbox_id,
                        created_ts_ms,
                    ),
                )
                self._fault("after_receipt")

                if outbox_id is not None and plan is not None:
                    conn.execute(
                        """
                        INSERT INTO life_memory_projection_outbox (
                            outbox_id, episode_id, request_id, event_type, payload_json,
                            status, available_ts_ms, attempt_count, claim_owner,
                            claim_expires_ts_ms, last_error, created_ts_ms, updated_ts_ms,
                            published_ts_ms
                        ) VALUES (?, ?, ?, 'LIFE_MEMORY_CHAT_ADMITTED', ?, 'PENDING', ?, 0,
                                  NULL, NULL, NULL, ?, ?, NULL)
                        """,
                        (
                            outbox_id,
                            plan.episode_id,
                            candidate.request_id,
                            _json_dumps(
                                {
                                    "episode_id": plan.episode_id,
                                    "request_id": candidate.request_id,
                                    "evidence_refs": list(plan.evidence_refs),
                                    "decision": decision.as_dict(),
                                    "schema_version": candidate.schema_version,
                                }
                            ),
                            created_ts_ms,
                            created_ts_ms,
                            created_ts_ms,
                        ),
                    )
                    self._fault("after_outbox")

                deleted_claim = conn.execute(
                    """
                    DELETE FROM life_memory_admission_claim
                    WHERE request_id = ? AND command_hash = ? AND owner_token = ?
                    """,
                    (candidate.request_id, command_hash, claim.owner_token),
                ).rowcount
                if deleted_claim != 1:
                    raise LifeMemoryRepositoryError(
                        LIFE_MEMORY_ADMISSION_CLAIM_LOST,
                        "admission claim was lost before commit",
                    )

                row = conn.execute(
                    "SELECT * FROM life_memory_admission_receipt WHERE request_id = ?",
                    (candidate.request_id,),
                ).fetchone()
                if row is None:
                    raise LifeMemoryRepositoryError(
                        LIFE_MEMORY_DURABLE_DATA_INVALID,
                        "committed admission receipt disappeared",
                    )
                conn.commit()
                return self._receipt_from_row(
                    row,
                    replayed=False,
                    projection_status="PENDING" if outbox_id else None,
                )
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()
        except LifeMemoryRepositoryError:
            raise
        except sqlite3.IntegrityError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_CONFLICT,
                "life memory admission violated a durable uniqueness invariant",
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "life memory admission transaction failed",
            ) from exc

    def claim_projection(
        self,
        outbox_id: str,
        *,
        owner_id: str,
        lease_ms: int = 60_000,
    ) -> dict[str, Any] | None:
        outbox = str(outbox_id or "").strip()
        owner = str(owner_id or "").strip()
        if not outbox or not owner:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "outbox_id and owner_id are required",
            )
        now_value = self._now()
        lease_expiry = now_value + max(1, int(lease_ms))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM life_memory_projection_outbox WHERE outbox_id = ?",
                (outbox,),
            ).fetchone()
            if row is None or str(row["status"]) == "PUBLISHED":
                conn.commit()
                return None
            if (
                str(row["status"]) == "PROCESSING"
                and int(row["claim_expires_ts_ms"] or 0) > now_value
            ):
                conn.commit()
                return None
            changed = conn.execute(
                """
                UPDATE life_memory_projection_outbox
                SET status = 'PROCESSING', claim_owner = ?, claim_expires_ts_ms = ?,
                    attempt_count = attempt_count + 1, last_error = NULL, updated_ts_ms = ?
                WHERE outbox_id = ?
                  AND (
                    status IN ('PENDING', 'FAILED')
                    OR (status = 'PROCESSING' AND COALESCE(claim_expires_ts_ms, 0) <= ?)
                  )
                """,
                (owner, lease_expiry, now_value, outbox, now_value),
            ).rowcount
            if changed != 1:
                conn.commit()
                return None
            payload = self._load_projection_payload(conn, outbox)
            conn.commit()
            return payload
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def mark_projection_published(self, outbox_id: str, *, owner_id: str) -> None:
        self._finish_projection(outbox_id, owner_id=owner_id, success=True, detail=None)

    def mark_projection_failed(self, outbox_id: str, *, owner_id: str, detail: str) -> None:
        self._finish_projection(outbox_id, owner_id=owner_id, success=False, detail=detail)

    def projection_status(self, outbox_id: str | None) -> str | None:
        if not outbox_id:
            return None
        conn = self._connect()
        try:
            return self._projection_status(conn, outbox_id)
        finally:
            conn.close()

    def list_ready_projection_ids(self, *, limit: int = 100) -> list[str]:
        now_value = self._now()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT outbox_id
                FROM life_memory_projection_outbox
                WHERE (
                    status IN ('PENDING', 'FAILED')
                    OR (status = 'PROCESSING' AND COALESCE(claim_expires_ts_ms, 0) <= ?)
                )
                  AND available_ts_ms <= ?
                ORDER BY created_ts_ms ASC, outbox_id ASC
                LIMIT ?
                """,
                (now_value, now_value, max(1, min(int(limit), 1_000))),
            ).fetchall()
            return [str(row["outbox_id"]) for row in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Legacy LifeMemory read model.  These named queries keep SQL and
    # connection ownership inside persistence while the domain layer retains
    # ranking, classification and presentation policy.

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        return self._get_record("life_episode", "episode_id", episode_id)

    def get_collection(self, collection_id: str) -> dict[str, Any] | None:
        return self._get_record(
            "life_memory_collection",
            "collection_id",
            collection_id,
        )

    def get_collection_item(self, collection_item_id: str) -> dict[str, Any] | None:
        return self._get_record(
            "life_memory_collection_item",
            "collection_item_id",
            collection_item_id,
        )

    def _get_record(
        self,
        table: str,
        key_column: str,
        key_value: Any,
    ) -> dict[str, Any] | None:
        allowed = {
            ("life_episode", "episode_id"),
            ("life_memory_collection", "collection_id"),
            ("life_memory_collection_item", "collection_item_id"),
        }
        if (table, key_column) not in allowed:
            raise AssertionError("unsupported LifeMemory record lookup")
        with self._read_operation() as conn:
            row = conn.execute(
                f'SELECT * FROM "{table}" WHERE "{key_column}" = ? LIMIT 1',
                (str(key_value or "").strip(),),
            ).fetchone()
            return None if row is None else dict(row)

    def get_episodes_by_ids(
        self,
        episode_ids: Sequence[Any],
        *,
        truth_state: str | None = None,
        status: str | None = None,
        excluded_retention_state: str | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(item or "").strip() for item in episode_ids if str(item or "").strip()))
        if not ids:
            return []
        clauses = [f"episode_id IN ({', '.join('?' for _ in ids)})"]
        params: list[Any] = [*ids]
        if truth_state is not None:
            clauses.append("truth_state = ?")
            params.append(str(truth_state))
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status))
        if excluded_retention_state is not None:
            clauses.append("COALESCE(retention_state, '') != ?")
            params.append(str(excluded_retention_state))
        with self._read_operation() as conn:
            rows = conn.execute(
                f"SELECT * FROM life_episode WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_collections_by_ids(
        self,
        collection_ids: Sequence[Any],
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(item or "").strip() for item in collection_ids if str(item or "").strip()))
        if not ids:
            return []
        with self._read_operation() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM life_memory_collection
                WHERE collection_id IN ({', '.join('?' for _ in ids)})
                """,
                ids,
            ).fetchall()
            return [dict(row) for row in rows]

    def load_semantic_lineage_batch(
        self,
        *,
        episode_ids: Sequence[Any] = (),
        collection_ids: Sequence[Any] = (),
        entity_ids: Sequence[Any] = (),
        correction_ids: Sequence[Any] = (),
        evidence_limit_per_episode: int = 12,
        collection_item_limit_per_collection: int = 16,
        entity_mention_limit_per_entity: int = 16,
    ) -> SemanticLineageBatch:
        """Load all provenance records for one semantic-recall snapshot.

        Related episode/evidence rows discovered through collections, entity
        mentions and corrections are folded into the same operation-scoped
        read transaction.  Optional lineage tables fail soft independently so
        an older or partially migrated store can still return usable episode
        provenance.
        """

        normalized_episode_ids = _normalized_record_ids(episode_ids)
        normalized_collection_ids = _normalized_record_ids(collection_ids)
        normalized_entity_ids = _normalized_record_ids(entity_ids)
        normalized_correction_ids = _normalized_record_ids(correction_ids)
        evidence_limit = max(1, int(evidence_limit_per_episode))
        collection_limit = max(1, int(collection_item_limit_per_collection))
        entity_limit = max(1, int(entity_mention_limit_per_entity))

        collection_rows: list[dict[str, Any]] = []
        entity_rows: list[dict[str, Any]] = []
        correction_rows: list[dict[str, Any]] = []
        episode_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []

        with self._read_operation() as conn:
            for id_batch in _record_id_batches(normalized_collection_ids):
                collection_rows.extend(
                    _semantic_fetch_rows_fail_soft(
                        conn,
                        f"""
                        SELECT collection_id, collection_item_id, episode_id,
                               source_ref, evidence_ref, position, created_ts_ms
                        FROM life_memory_collection_item
                        WHERE collection_id IN ({', '.join('?' for _ in id_batch)})
                        ORDER BY collection_id ASC, position ASC,
                                 created_ts_ms ASC
                        """,
                        id_batch,
                    )
                )

            for id_batch in _record_id_batches(normalized_entity_ids):
                entity_rows.extend(
                    _semantic_fetch_rows_fail_soft(
                        conn,
                        f"""
                        SELECT m.entity_id, m.mention_id, m.episode_id,
                               m.collection_id, m.collection_item_id,
                               m.source_ref, m.evidence_ref, m.position,
                               m.created_ts_ms,
                               i.source_ref AS item_source_ref,
                               i.evidence_ref AS item_evidence_ref
                        FROM life_memory_entity_mention m
                        LEFT JOIN life_memory_collection_item i
                          ON i.collection_item_id = m.collection_item_id
                        WHERE m.entity_id IN ({', '.join('?' for _ in id_batch)})
                        ORDER BY m.entity_id ASC, m.created_ts_ms DESC,
                                 m.position ASC
                        """,
                        id_batch,
                    )
                )

            for id_batch in _record_id_batches(normalized_correction_ids):
                correction_rows.extend(
                    _semantic_fetch_rows_fail_soft(
                        conn,
                        f"""
                        SELECT correction_id, episode_id, correction_episode_id,
                               created_ts_ms
                        FROM life_memory_correction
                        WHERE correction_id IN ({', '.join('?' for _ in id_batch)})
                        """,
                        id_batch,
                    )
                )

            collection_items_by_id = _group_semantic_records(
                collection_rows,
                key="collection_id",
                per_key_limit=collection_limit,
            )
            entity_mentions_by_id = _group_semantic_records(
                entity_rows,
                key="entity_id",
                per_key_limit=entity_limit,
            )
            corrections_by_id = {
                str(row.get("correction_id") or "").strip(): row
                for row in correction_rows
                if str(row.get("correction_id") or "").strip()
            }

            related_episode_ids = list(normalized_episode_ids)

            def add_related_episode(value: Any) -> None:
                episode_id = str(value or "").strip()
                if episode_id and episode_id not in related_episode_ids:
                    related_episode_ids.append(episode_id)

            for items in collection_items_by_id.values():
                for row in items:
                    add_related_episode(row.get("episode_id"))
            for mentions in entity_mentions_by_id.values():
                for row in mentions:
                    add_related_episode(row.get("episode_id"))
            for row in correction_rows:
                add_related_episode(row.get("correction_episode_id"))
                add_related_episode(row.get("episode_id"))

            for id_batch in _record_id_batches(related_episode_ids):
                episode_rows.extend(
                    _semantic_fetch_rows_fail_soft(
                        conn,
                        f"""
                        SELECT episode_id, person_id, session_id, primary_ref
                        FROM life_episode
                        WHERE episode_id IN ({', '.join('?' for _ in id_batch)})
                        """,
                        id_batch,
                    )
                )
                evidence_rows.extend(
                    _semantic_fetch_rows_fail_soft(
                        conn,
                        f"""
                        SELECT evidence_id, episode_id, source_ref, created_ts_ms
                        FROM life_evidence
                        WHERE episode_id IN ({', '.join('?' for _ in id_batch)})
                        ORDER BY episode_id ASC, created_ts_ms ASC
                        """,
                        id_batch,
                    )
                )

        episodes_by_id = {
            str(row.get("episode_id") or "").strip(): row
            for row in episode_rows
            if str(row.get("episode_id") or "").strip()
        }
        evidence_by_id = _group_semantic_records(
            evidence_rows,
            key="episode_id",
            per_key_limit=evidence_limit,
        )
        return SemanticLineageBatch(
            episodes_by_id=episodes_by_id,
            evidence_by_episode_id=evidence_by_id,
            collection_items_by_collection_id=collection_items_by_id,
            entity_mentions_by_entity_id=entity_mentions_by_id,
            corrections_by_id=corrections_by_id,
        )

    def list_episode_records(
        self,
        *,
        truth_states: Sequence[str] = (),
        excluded_truth_states: Sequence[str] = (),
        statuses: Sequence[str] = (),
        excluded_statuses: Sequence[str] = (),
        retention_states: Sequence[str] = (),
        excluded_retention_states: Sequence[str] = (),
        action: str | None = None,
        person_or_global: str | None = None,
        session_or_global: str | None = None,
        uncompressed_only: bool = False,
        metadata_any_patterns: Sequence[str] = (),
        order: str = "strength",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, values in (
            ("truth_state", truth_states),
            ("status", statuses),
            ("retention_state", retention_states),
        ):
            normalized = [str(value) for value in values]
            if normalized:
                clauses.append(f"COALESCE({column}, '') IN ({', '.join('?' for _ in normalized)})")
                params.extend(normalized)
        excluded = [str(value) for value in excluded_retention_states]
        if excluded:
            clauses.append(
                f"COALESCE(retention_state, '') NOT IN ({', '.join('?' for _ in excluded)})"
            )
            params.extend(excluded)
        excluded_truth = [str(value) for value in excluded_truth_states]
        if excluded_truth:
            clauses.append(
                f"COALESCE(truth_state, '') NOT IN ({', '.join('?' for _ in excluded_truth)})"
            )
            params.extend(excluded_truth)
        excluded_status = [str(value) for value in excluded_statuses]
        if excluded_status:
            clauses.append(
                f"COALESCE(status, '') NOT IN ({', '.join('?' for _ in excluded_status)})"
            )
            params.extend(excluded_status)
        if action is not None:
            clauses.append("action = ?")
            params.append(str(action))
        if person_or_global is not None:
            clauses.append("(COALESCE(person_id, '') = ? OR COALESCE(person_id, '') = '')")
            params.append(str(person_or_global))
        if session_or_global is not None:
            clauses.append("(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '')")
            params.append(str(session_or_global))
        if uncompressed_only:
            clauses.append("compressed_into_episode_id IS NULL")
        patterns = [str(value) for value in metadata_any_patterns if str(value)]
        if patterns:
            clauses.append(
                "(" + " OR ".join("metadata LIKE ?" for _ in patterns) + ")"
            )
            params.extend(patterns)
        orders = {
            "strength": "memory_strength DESC, salience DESC, last_touched_ts_ms DESC",
            "recent": "last_touched_ts_ms DESC, memory_strength DESC, salience DESC",
            "created": "created_ts_ms ASC",
            "oldest": "last_touched_ts_ms ASC",
        }
        order_sql = orders.get(str(order), orders["strength"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._read_operation() as conn:
            rows = conn.execute(
                f"SELECT * FROM life_episode{where} ORDER BY {order_sql} LIMIT ?",
                [*params, max(1, int(limit))],
            ).fetchall()
            return [dict(row) for row in rows]

    def list_semantic_collection_records(self, *, limit: int) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_memory_collection
                WHERE COALESCE(retention_state, '') != 'GONE'
                  AND COALESCE(item_count, 0) > 0
                ORDER BY memory_strength DESC, updated_ts_ms DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_semantic_entity_records(self, *, limit: int) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_memory_entity
                WHERE COALESCE(retention_state, '') != 'GONE'
                  AND COALESCE(mention_count, 0) > 0
                ORDER BY memory_strength DESC, last_seen_ts_ms DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_correction_semantic_rows(self, *, limit: int) -> list[dict[str, Any]]:
        if not self.table_exists("life_memory_correction"):
            return []
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.correction_id, c.episode_id, c.correction_episode_id,
                    c.correction_kind, c.reason_code, c.detail, c.created_ts_ms,
                    c.metadata, source_ep.summary AS source_summary,
                    source_ep.primary_ref AS source_primary_ref,
                    source_ep.person_id AS source_person_id,
                    source_ep.session_id AS source_session_id,
                    source_ep.truth_state AS source_truth_state,
                    source_ep.status AS source_status,
                    source_ep.metadata AS source_metadata,
                    corr_ep.summary AS correction_summary,
                    corr_ep.primary_ref AS correction_primary_ref,
                    corr_ep.person_id AS correction_person_id,
                    corr_ep.session_id AS correction_session_id,
                    corr_ep.truth_state AS correction_truth_state,
                    corr_ep.status AS correction_status,
                    corr_ep.retention_state AS correction_retention_state,
                    corr_ep.metadata AS correction_metadata
                FROM life_memory_correction c
                LEFT JOIN life_episode source_ep ON source_ep.episode_id = c.episode_id
                LEFT JOIN life_episode corr_ep ON corr_ep.episode_id = c.correction_episode_id
                ORDER BY c.created_ts_ms DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def table_exists(self, table_name: str) -> bool:
        name = str(table_name or "").strip()
        if name not in {
            "life_memory_correction",
            "semantic_memory",
            "life_memory_collection",
            "life_memory_entity",
        }:
            return False
        with self._read_operation() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                (name,),
            ).fetchone()
            return row is not None

    def list_active_semantic_memory_rows(self, *, limit: int) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM semantic_memory
                WHERE deleted_at IS NULL
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_active_semantic_memories(self) -> int:
        with self._read_operation() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM semantic_memory WHERE deleted_at IS NULL"
            ).fetchone()
            return int(row["count"] or 0) if row is not None else 0

    def semantic_referenced_episode_ids(self) -> set[str]:
        try:
            with self._read_operation() as conn:
                rows = conn.execute(
                    "SELECT source_memory_ids FROM semantic_memory WHERE deleted_at IS NULL"
                ).fetchall()
        except sqlite3.DatabaseError:
            return set()
        episode_ids: set[str] = set()
        for row in rows:
            source_ids = _json_loads(row["source_memory_ids"], fallback=[])
            if not isinstance(source_ids, list):
                continue
            for source_id in source_ids:
                value = str(source_id or "").strip()
                if value.startswith("life_episode::"):
                    episode_id = value.split("::", 1)[1]
                    if episode_id:
                        episode_ids.add(episode_id)
        return episode_ids

    def list_open_loop_records(
        self,
        *,
        status: str | None,
        person_id: str | None,
        session_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if person_id:
            clauses.append("person_id = ?")
            params.append(str(person_id))
        if session_id:
            clauses.append("session_id = ?")
            params.append(str(session_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._read_operation() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM life_open_loop{where}
                ORDER BY memory_strength DESC, updated_ts_ms DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
            return [dict(row) for row in rows]

    def list_collection_index_rows(
        self,
        *,
        limit: int,
        include_non_lightnovel: bool,
    ) -> list[dict[str, Any]]:
        clauses = ["COALESCE(c.retention_state, '') != 'GONE'"]
        if not include_non_lightnovel:
            clauses.append("c.domain = 'lightnovel_recommendation'")
        where = "WHERE " + " AND ".join(clauses)
        with self._read_operation() as conn:
            rows = conn.execute(
                f"""
                SELECT i.*, c.domain AS collection_domain,
                       c.scope_key AS collection_scope_key
                FROM life_memory_collection_item i
                JOIN life_memory_collection c ON c.collection_id = i.collection_id
                {where}
                ORDER BY i.created_ts_ms ASC, i.position ASC, i.collection_item_id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def collection_item_has_entity_mention(
        self,
        *,
        entity_id: str,
        collection_item_id: str,
    ) -> bool:
        with self._read_operation() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM life_memory_entity_mention
                WHERE entity_id = ? AND collection_item_id = ? LIMIT 1
                """,
                (str(entity_id), str(collection_item_id)),
            ).fetchone()
            return row is not None

    def list_entity_records(
        self,
        *,
        domains: Sequence[str] = (),
        limit: int,
    ) -> list[dict[str, Any]]:
        domain_values = sorted({str(value) for value in domains if str(value)})
        clauses = ["COALESCE(retention_state, '') != 'GONE'"]
        params: list[Any] = []
        if domain_values:
            clauses.append(
                f"COALESCE(domain, '') IN ({', '.join('?' for _ in domain_values)})"
            )
            params.extend(domain_values)
        with self._read_operation() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM life_memory_entity
                WHERE {' AND '.join(clauses)}
                ORDER BY last_seen_ts_ms DESC, memory_strength DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
            return [dict(row) for row in rows]

    def list_policy_backfill_records(
        self,
        *,
        limit: int,
        include_existing: bool,
    ) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT ep.*, GROUP_CONCAT(DISTINCT ev.source_ref) AS evidence_source_refs
                FROM life_episode ep
                LEFT JOIN life_evidence ev ON ev.episode_id = ep.episode_id
                WHERE (? OR ep.metadata IS NULL OR ep.metadata NOT LIKE '%"memory_policy"%')
                GROUP BY ep.episode_id
                ORDER BY ep.created_ts_ms ASC
                LIMIT ?
                """,
                (1 if include_existing else 0, max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def recall_episode_candidates(
        self,
        *,
        include_suppressed: bool,
        like_terms: Sequence[str],
        memory_classes: Sequence[str],
        domains: Sequence[str],
        anaphoric_kinds: Sequence[str],
        anaphoric: bool,
        person_id: str | None,
        session_id: str | None,
        limit: int,
        significant_anaphoric: Callable[[Mapping[str, Any]], bool],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        base_clauses: list[str] = []
        base_params: list[Any] = []
        if not include_suppressed:
            base_clauses.extend(
                [
                    "status != 'SUPPRESSED'",
                    "truth_state NOT IN ('SUPPRESSED', 'CORRECTED')",
                    "COALESCE(retention_state, '') != 'GONE'",
                ]
            )
        rows_by_id: dict[str, dict[str, Any]] = {}
        anaphoric_ids: set[str] = set()

        def fetch(
            conn: sqlite3.Connection,
            extra_clause: str = "",
            extra_params: Sequence[Any] = (),
            *,
            row_limit: int,
        ) -> list[sqlite3.Row]:
            clauses = [*base_clauses]
            if extra_clause:
                clauses.append(f"({extra_clause})")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            return conn.execute(
                f"""
                SELECT * FROM life_episode{where}
                ORDER BY last_touched_ts_ms DESC
                LIMIT ?
                """,
                [*base_params, *extra_params, max(1, int(row_limit))],
            ).fetchall()

        with self._read_operation() as conn:
            for row in fetch(conn, row_limit=limit):
                rows_by_id[str(row["episode_id"])] = dict(row)
            terms = [str(term) for term in like_terms if str(term)]
            if terms:
                clauses: list[str] = []
                params: list[str] = []
                for term in terms:
                    clauses.append("(COALESCE(summary, '') LIKE ? OR COALESCE(primary_ref, '') LIKE ?)")
                    params.extend([f"%{term}%", f"%{term}%"])
                for row in fetch(conn, " OR ".join(clauses), params, row_limit=limit * 2):
                    rows_by_id[str(row["episode_id"])] = dict(row)
            classes = [str(value) for value in memory_classes if str(value)]
            if classes:
                placeholders = ", ".join("?" for _ in classes)
                clause = (
                    f"json_extract(metadata, '$.memory_class') IN ({placeholders}) "
                    f"OR json_extract(metadata, '$.memory_policy.memory_class') IN ({placeholders})"
                )
                for row in fetch(conn, clause, [*classes, *classes], row_limit=max(limit * 3, 48)):
                    rows_by_id[str(row["episode_id"])] = dict(row)
            domain_values = sorted({str(value) for value in domains if str(value)})
            if domain_values:
                clauses = []
                params = []
                for domain in domain_values:
                    clauses.append(
                        "(COALESCE(metadata, '') LIKE ? OR COALESCE(summary, '') LIKE ? OR COALESCE(primary_ref, '') LIKE ?)"
                    )
                    params.extend([f"%{domain}%"] * 3)
                for row in fetch(conn, " OR ".join(clauses), params, row_limit=max(limit * 3, 48)):
                    rows_by_id[str(row["episode_id"])] = dict(row)
            if anaphoric and (session_id or person_id):
                scope_clauses: list[str] = []
                scope_params: list[Any] = []
                if session_id:
                    scope_clauses.append("COALESCE(session_id, '') = ?")
                    scope_params.append(str(session_id))
                if person_id:
                    scope_clauses.append("COALESCE(person_id, '') = ?")
                    scope_params.append(str(person_id))
                kinds = [str(value) for value in anaphoric_kinds if str(value)]
                if kinds:
                    clause = (
                        f"episode_kind IN ({', '.join('?' for _ in kinds)}) "
                        f"AND ({' OR '.join(scope_clauses)})"
                    )
                    for row in fetch(
                        conn,
                        clause,
                        [*kinds, *scope_params],
                        row_limit=max(limit * 3, 40),
                    ):
                        record = dict(row)
                        episode_id = str(row["episode_id"])
                        if significant_anaphoric(record):
                            rows_by_id[episode_id] = record
                            anaphoric_ids.add(episode_id)
        return list(rows_by_id.values()), anaphoric_ids

    def find_collection_records(
        self,
        *,
        domains: Sequence[str],
        scope_keys: Sequence[str],
        delivery_day_ranges: Sequence[tuple[int, int]],
        person_id: str | None,
        session_id: str | None,
        owner_aliases: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        base_clauses = ["COALESCE(retention_state, '') != 'GONE'"]
        base_params: list[Any] = []
        domain_values = sorted({str(value) for value in domains if str(value)})
        if domain_values:
            base_clauses.append(
                f"COALESCE(domain, '') IN ({', '.join('?' for _ in domain_values)})"
            )
            base_params.extend(domain_values)
        keys = [str(value) for value in scope_keys if str(value)]
        ranges = [(int(start), int(end)) for start, end in delivery_day_ranges]
        if keys or ranges:
            scope_clauses: list[str] = []
            if keys:
                scope_clauses.append(
                    f"COALESCE(scope_key, '') IN ({', '.join('?' for _ in keys)})"
                )
                base_params.extend(keys)
            for start, end in ranges:
                scope_clauses.append(
                    "(COALESCE(scope_key, '') LIKE 'delivery:%' AND COALESCE(created_ts_ms, 0) >= ? AND COALESCE(created_ts_ms, 0) < ?)"
                )
                base_params.extend([start, end])
            base_clauses.append("(" + " OR ".join(scope_clauses) + ")")

        def query(
            conn: sqlite3.Connection,
            identity_clause: str = "",
            identity_params: Sequence[Any] = (),
        ) -> list[dict[str, Any]]:
            clauses = [*base_clauses]
            if identity_clause:
                clauses.append(identity_clause)
            rows = conn.execute(
                f"""
                SELECT * FROM life_memory_collection
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_ts_ms DESC, memory_strength DESC
                LIMIT ?
                """,
                [*base_params, *identity_params, max(1, int(limit))],
            ).fetchall()
            return [dict(row) for row in rows]

        aliases = [str(value) for value in owner_aliases if str(value)]
        identity_clause = ""
        identity_params: list[Any] = []
        if session_id:
            if person_id and aliases:
                identity_clause = (
                    "(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '' "
                    f"OR COALESCE(person_id, '') IN ({', '.join('?' for _ in aliases)}))"
                )
                identity_params = [str(session_id), *aliases]
            else:
                identity_clause = "(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '')"
                identity_params = [str(session_id)]
        elif person_id:
            identity_clause = "(COALESCE(person_id, '') = ? OR COALESCE(person_id, '') = '')"
            identity_params = [str(person_id)]
        with self._read_operation() as conn:
            rows = query(conn, identity_clause, identity_params)
            if rows or not (session_id or person_id) or not aliases:
                return rows
            return query(
                conn,
                f"COALESCE(person_id, '') IN ({', '.join('?' for _ in aliases)})",
                aliases,
            )

    def list_collection_items(
        self,
        *,
        collection_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_memory_collection_item
                WHERE collection_id = ?
                ORDER BY created_ts_ms ASC, position ASC, item_title ASC
                LIMIT ?
                """,
                (str(collection_id), max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_entity_mentions(
        self,
        *,
        entity_id: str,
        person_id: str | None,
        session_id: str | None,
        owner_aliases: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = ["m.entity_id = ?"]
        params: list[Any] = [str(entity_id)]
        aliases = [str(value) for value in owner_aliases if str(value)]
        if session_id:
            if person_id and aliases:
                clauses.append(
                    "(COALESCE(ep.session_id, '') = ? OR COALESCE(ep.session_id, '') = '' "
                    f"OR COALESCE(ep.person_id, '') IN ({', '.join('?' for _ in aliases)}))"
                )
                params.extend([str(session_id), *aliases])
            else:
                clauses.append("(COALESCE(ep.session_id, '') = ? OR COALESCE(ep.session_id, '') = '')")
                params.append(str(session_id))
        elif person_id and aliases:
            clauses.append(
                f"(COALESCE(ep.person_id, '') IN ({', '.join('?' for _ in aliases)}) OR COALESCE(ep.person_id, '') = '')"
            )
            params.extend(aliases)
        with self._read_operation() as conn:
            rows = conn.execute(
                f"""
                SELECT m.*, i.item_title, i.item_summary,
                       i.source_ref AS item_source_ref,
                       i.evidence_ref AS item_evidence_ref,
                       i.metadata AS item_metadata, c.scope_key,
                       c.domain AS collection_domain,
                       ep.person_id AS episode_person_id,
                       ep.session_id AS episode_session_id,
                       ep.created_ts_ms AS episode_created_ts_ms
                FROM life_memory_entity_mention m
                LEFT JOIN life_memory_collection_item i ON i.collection_item_id = m.collection_item_id
                LEFT JOIN life_memory_collection c ON c.collection_id = m.collection_id
                LEFT JOIN life_episode ep ON ep.episode_id = m.episode_id
                WHERE {' AND '.join(clauses)}
                ORDER BY m.created_ts_ms DESC, m.position ASC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
            return [dict(row) for row in rows]

    def list_evidence(self, *, episode_id: str) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_evidence
                WHERE episode_id = ? ORDER BY created_ts_ms ASC
                """,
                (str(episode_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_corrections(self, *, episode_id: str) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_memory_correction
                WHERE episode_id = ? ORDER BY created_ts_ms ASC
                """,
                (str(episode_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Legacy LifeMemory write UoWs.  Each method owns one complete mutation
    # boundary; callers provide normalized domain values, never SQL handles.

    def upsert_episode_record(
        self,
        record: Mapping[str, Any],
        *,
        merge_existing: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> bool:
        episode_id = str(record.get("episode_id") or "").strip()
        if not episode_id:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_INVALID,
                "episode_id is required",
            )
        with self._write_uow() as conn:
            existing = conn.execute(
                "SELECT * FROM life_episode WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO life_episode (
                        episode_id, episode_kind, thread_id, person_id, session_id,
                        actor_id, intent, action, result, primary_ref, summary,
                        salience, base_salience, memory_strength, decay_profile_id,
                        retention_state, last_recalled_ts_ms, recall_count,
                        reinforcement_count, compressed_into_episode_id,
                        retention_until_ts_ms, forget_reason, truth_state, status,
                        created_ts_ms, last_touched_ts_ms, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        record.get(key)
                        for key in (
                            "episode_id", "episode_kind", "thread_id", "person_id",
                            "session_id", "actor_id", "intent", "action", "result",
                            "primary_ref", "summary", "salience", "base_salience",
                            "memory_strength", "decay_profile_id", "retention_state",
                            "last_recalled_ts_ms", "recall_count", "reinforcement_count",
                            "compressed_into_episode_id", "retention_until_ts_ms",
                            "forget_reason", "truth_state", "status", "created_ts_ms",
                            "last_touched_ts_ms", "metadata",
                        )
                    ),
                )
                return True
            update = dict(merge_existing(dict(existing)))
            conn.execute(
                """
                UPDATE life_episode
                SET episode_kind = COALESCE(NULLIF(?, ''), episode_kind),
                    thread_id = COALESCE(?, thread_id),
                    person_id = COALESCE(?, person_id),
                    session_id = COALESCE(?, session_id),
                    actor_id = COALESCE(?, actor_id),
                    intent = COALESCE(?, intent), action = COALESCE(?, action),
                    result = COALESCE(?, result), primary_ref = COALESCE(?, primary_ref),
                    summary = COALESCE(?, summary),
                    salience = MAX(COALESCE(salience, 0.0), ?),
                    base_salience = MAX(COALESCE(base_salience, 0.0), ?),
                    memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                    decay_profile_id = COALESCE(NULLIF(?, ''), decay_profile_id),
                    retention_state = ?,
                    retention_until_ts_ms = COALESCE(?, retention_until_ts_ms),
                    forget_reason = COALESCE(?, forget_reason), truth_state = ?,
                    status = COALESCE(NULLIF(?, ''), status),
                    last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?),
                    metadata = ?
                WHERE episode_id = ?
                """,
                tuple(
                    update.get(key)
                    for key in (
                        "episode_kind", "thread_id", "person_id", "session_id",
                        "actor_id", "intent", "action", "result", "primary_ref",
                        "summary", "salience", "base_salience", "memory_strength",
                        "decay_profile_id", "retention_state", "retention_until_ts_ms",
                        "forget_reason", "truth_state", "status", "last_touched_ts_ms",
                        "metadata", "episode_id",
                    )
                ),
            )
            return False

    def attach_evidence_record(self, record: Mapping[str, Any]) -> None:
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO life_evidence (
                    evidence_id, episode_id, evidence_kind, source_ref, request_id,
                    content, payload, trust_level, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "evidence_id", "episode_id", "evidence_kind", "source_ref",
                        "request_id", "content", "payload", "trust_level",
                        "created_ts_ms",
                    )
                ),
            )
            conn.execute(
                """
                UPDATE life_episode
                SET last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                WHERE episode_id = ?
                """,
                (record.get("created_ts_ms"), record.get("episode_id")),
            )

    def upsert_open_loop_record(
        self,
        record: Mapping[str, Any],
        *,
        merge_existing: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        with self._write_uow() as conn:
            existing = conn.execute(
                "SELECT * FROM life_open_loop WHERE semantic_key = ? LIMIT 1",
                (record.get("semantic_key"),),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO life_open_loop (
                        open_loop_id, semantic_key, episode_id, person_id, session_id,
                        title, detail, status, source_ref, opened_ts_ms, updated_ts_ms,
                        closed_ts_ms, close_episode_id, close_reason, salience,
                        memory_strength, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        record.get(key)
                        for key in (
                            "open_loop_id", "semantic_key", "episode_id", "person_id",
                            "session_id", "title", "detail", "status", "source_ref",
                            "opened_ts_ms", "updated_ts_ms", "closed_ts_ms",
                            "close_episode_id", "close_reason", "salience",
                            "memory_strength", "metadata",
                        )
                    ),
                )
                return
            update = dict(merge_existing(dict(existing)))
            conn.execute(
                """
                UPDATE life_open_loop
                SET episode_id = COALESCE(NULLIF(?, ''), episode_id),
                    person_id = COALESCE(?, person_id), session_id = COALESCE(?, session_id),
                    title = COALESCE(?, title), detail = COALESCE(?, detail), status = ?,
                    source_ref = COALESCE(?, source_ref),
                    updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?),
                    closed_ts_ms = ?, close_episode_id = ?, close_reason = ?,
                    salience = MAX(COALESCE(salience, 0.0), ?),
                    memory_strength = MAX(COALESCE(memory_strength, 0.0), ?), metadata = ?
                WHERE semantic_key = ?
                """,
                tuple(
                    update.get(key)
                    for key in (
                        "episode_id", "person_id", "session_id", "title", "detail",
                        "status", "source_ref", "updated_ts_ms", "closed_ts_ms",
                        "close_episode_id", "close_reason", "salience",
                        "memory_strength", "metadata", "semantic_key",
                    )
                ),
            )

    def upsert_collection_record(self, record: Mapping[str, Any]) -> None:
        collection_id = str(record.get("collection_id") or "").strip()
        with self._write_uow() as conn:
            existing = conn.execute(
                "SELECT metadata FROM life_memory_collection WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO life_memory_collection (
                        collection_id, collection_kind, domain, scope_key, person_id,
                        session_id, title, summary, item_count, salience,
                        memory_strength, retention_state, created_ts_ms,
                        updated_ts_ms, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        record.get(key)
                        for key in (
                            "collection_id", "collection_kind", "domain", "scope_key",
                            "person_id", "session_id", "title", "summary", "item_count",
                            "salience", "memory_strength", "retention_state",
                            "created_ts_ms", "updated_ts_ms", "metadata",
                        )
                    ),
                )
                return
            current_metadata = _json_loads(existing["metadata"], fallback={})
            incoming_metadata = _json_loads(record.get("metadata"), fallback={})
            merged = _merge_json_objects(existing["metadata"], record.get("metadata"))
            for list_key in ("source_episode_ids",):
                old_list = (
                    current_metadata.get(list_key)
                    if isinstance(current_metadata, dict)
                    else None
                )
                new_list = (
                    incoming_metadata.get(list_key)
                    if isinstance(incoming_metadata, dict)
                    else None
                )
                values = [
                    str(item)
                    for item in (
                        list(old_list) if isinstance(old_list, list) else []
                    )
                    if str(item).strip()
                ]
                if isinstance(new_list, list):
                    values.extend(str(item) for item in new_list if str(item).strip())
                if values:
                    merged[list_key] = list(dict.fromkeys(values))
            conn.execute(
                """
                UPDATE life_memory_collection
                SET title = COALESCE(NULLIF(?, ''), title),
                    summary = COALESCE(NULLIF(?, ''), summary),
                    salience = MAX(COALESCE(salience, 0.0), ?),
                    memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                    retention_state = COALESCE(NULLIF(?, ''), retention_state),
                    updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?), metadata = ?
                WHERE collection_id = ?
                """,
                (
                    record.get("title"), record.get("summary"), record.get("salience"),
                    record.get("memory_strength"), record.get("retention_state"),
                    record.get("updated_ts_ms"), _json_dumps(merged), collection_id,
                ),
            )

    def attach_collection_item_record(
        self,
        record: Mapping[str, Any],
        *,
        scope_cutoff: Callable[[Any, int], int | None],
        summary_builder: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], str],
    ) -> None:
        collection_id = str(record.get("collection_id") or "").strip()
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT INTO life_memory_collection_item (
                    collection_item_id, collection_id, episode_id, item_key,
                    item_title, item_summary, source_ref, evidence_ref,
                    position, created_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, item_key) DO UPDATE SET
                    item_title = COALESCE(NULLIF(excluded.item_title, ''), item_title),
                    item_summary = COALESCE(NULLIF(excluded.item_summary, ''), item_summary),
                    source_ref = COALESCE(NULLIF(excluded.source_ref, ''), source_ref),
                    evidence_ref = COALESCE(NULLIF(excluded.evidence_ref, ''), evidence_ref),
                    position = excluded.position, metadata = excluded.metadata
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "collection_item_id", "collection_id", "episode_id", "item_key",
                        "item_title", "item_summary", "source_ref", "evidence_ref",
                        "position", "created_ts_ms", "metadata",
                    )
                ),
            )
            collection = conn.execute(
                "SELECT * FROM life_memory_collection WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            if collection is None:
                raise LifeMemoryRepositoryError(
                    LIFE_MEMORY_DURABLE_DATA_INVALID,
                    "collection item references a missing collection",
                )
            anchor = conn.execute(
                """
                SELECT MAX(created_ts_ms) AS max_ts
                FROM life_memory_collection_item WHERE collection_id = ?
                """,
                (collection_id,),
            ).fetchone()
            anchor_ts = int(anchor["max_ts"] or 0) if anchor is not None else 0
            cutoff = scope_cutoff(collection["scope_key"], anchor_ts)
            if cutoff is not None:
                conn.execute(
                    """
                    DELETE FROM life_memory_collection_item
                    WHERE collection_id = ? AND created_ts_ms < ?
                    """,
                    (collection_id, int(cutoff)),
                )
            items = conn.execute(
                """
                SELECT * FROM life_memory_collection_item
                WHERE collection_id = ?
                ORDER BY created_ts_ms ASC, position ASC, item_title ASC
                """,
                (collection_id,),
            ).fetchall()
            item_records = [dict(item) for item in items]
            summary = str(summary_builder(dict(collection), item_records) or "").strip()
            conn.execute(
                """
                UPDATE life_memory_collection
                SET item_count = ?, summary = COALESCE(NULLIF(?, ''), summary),
                    updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?)
                WHERE collection_id = ?
                """,
                (
                    len(item_records), summary, record.get("created_ts_ms"), collection_id,
                ),
            )

    def upsert_entity_record(self, record: Mapping[str, Any]) -> None:
        entity_id = str(record.get("entity_id") or "").strip()
        with self._write_uow() as conn:
            existing = conn.execute(
                "SELECT metadata FROM life_memory_entity WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO life_memory_entity (
                        entity_id, entity_kind, canonical_name, normalized_key, domain,
                        first_seen_ts_ms, last_seen_ts_ms, mention_count, salience,
                        memory_strength, retention_state, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    tuple(
                        record.get(key)
                        for key in (
                            "entity_id", "entity_kind", "canonical_name",
                            "normalized_key", "domain", "first_seen_ts_ms",
                            "last_seen_ts_ms", "salience", "memory_strength",
                            "retention_state", "metadata",
                        )
                    ),
                )
                return
            metadata = _merge_json_objects(existing["metadata"], record.get("metadata"))
            conn.execute(
                """
                UPDATE life_memory_entity
                SET canonical_name = COALESCE(NULLIF(?, ''), canonical_name),
                    last_seen_ts_ms = MAX(COALESCE(last_seen_ts_ms, 0), ?),
                    salience = MAX(COALESCE(salience, 0.0), ?),
                    memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                    retention_state = COALESCE(NULLIF(?, ''), retention_state),
                    metadata = ? WHERE entity_id = ?
                """,
                (
                    record.get("canonical_name"), record.get("last_seen_ts_ms"),
                    record.get("salience"), record.get("memory_strength"),
                    record.get("retention_state"), _json_dumps(metadata), entity_id,
                ),
            )

    def attach_entity_mention_record(self, record: Mapping[str, Any]) -> None:
        entity_id = str(record.get("entity_id") or "").strip()
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT INTO life_memory_entity_mention (
                    mention_id, entity_id, episode_id, collection_id,
                    collection_item_id, source_ref, evidence_ref, position,
                    created_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, episode_id, collection_id, collection_item_id)
                DO UPDATE SET
                    source_ref = COALESCE(NULLIF(excluded.source_ref, ''), source_ref),
                    evidence_ref = COALESCE(NULLIF(excluded.evidence_ref, ''), evidence_ref),
                    position = excluded.position,
                    created_ts_ms = MIN(created_ts_ms, excluded.created_ts_ms),
                    metadata = excluded.metadata
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "mention_id", "entity_id", "episode_id", "collection_id",
                        "collection_item_id", "source_ref", "evidence_ref", "position",
                        "created_ts_ms", "metadata",
                    )
                ),
            )
            self._refresh_entity_mention_count(conn, entity_id)

    def apply_collection_item_review(
        self,
        *,
        collection_item_id: str,
        evidence_ref: str,
        review_metadata: Mapping[str, Any],
        updated_ts_ms: int,
    ) -> dict[str, Any] | None:
        with self._write_uow() as conn:
            row = conn.execute(
                """
                SELECT * FROM life_memory_collection_item
                WHERE collection_item_id = ?
                """,
                (str(collection_item_id),),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            merged = _merge_json_objects(item.get("metadata"), review_metadata)
            conn.execute(
                """
                UPDATE life_memory_collection_item
                SET evidence_ref = COALESCE(NULLIF(?, ''), evidence_ref), metadata = ?
                WHERE collection_item_id = ?
                """,
                (evidence_ref, _json_dumps(merged), collection_item_id),
            )
            collection_id = str(item.get("collection_id") or "").strip()
            if collection_id:
                conn.execute(
                    """
                    UPDATE life_memory_collection
                    SET memory_strength = MAX(COALESCE(memory_strength, 0.0), 0.58),
                        retention_state = CASE WHEN COALESCE(retention_state, '') = 'COLD'
                                               THEN 'WARM' ELSE retention_state END,
                        updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?)
                    WHERE collection_id = ?
                    """,
                    (int(updated_ts_ms), collection_id),
                )
            entity_rows = conn.execute(
                """
                SELECT DISTINCT entity_id FROM life_memory_entity_mention
                WHERE collection_item_id = ?
                """,
                (collection_item_id,),
            ).fetchall()
            return {
                **item,
                "collection_id": collection_id,
                "entity_ids": [str(entity["entity_id"]) for entity in entity_rows],
            }

    def update_collection_item_metadata(
        self,
        *,
        collection_item_id: str,
        metadata_json: str,
    ) -> None:
        with self._write_uow() as conn:
            conn.execute(
                "UPDATE life_memory_collection_item SET metadata = ? WHERE collection_item_id = ?",
                (metadata_json, str(collection_item_id)),
            )

    def link_episode_record(self, record: Mapping[str, Any]) -> None:
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO life_episode_link (
                    link_id, source_episode_id, target_episode_id, link_kind,
                    weight, created_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "link_id", "source_episode_id", "target_episode_id",
                        "link_kind", "weight", "created_ts_ms", "metadata",
                    )
                ),
            )

    def record_correction(
        self,
        record: Mapping[str, Any],
        *,
        truth_state: str,
        status: str,
    ) -> None:
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO life_memory_correction (
                    correction_id, episode_id, correction_episode_id,
                    correction_kind, reason_code, detail, created_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "correction_id", "episode_id", "correction_episode_id",
                        "correction_kind", "reason_code", "detail", "created_ts_ms",
                        "metadata",
                    )
                ),
            )
            conn.execute(
                """
                UPDATE life_episode
                SET truth_state = ?, status = ?,
                    last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                WHERE episode_id = ?
                """,
                (truth_state, status, record.get("created_ts_ms"), record.get("episode_id")),
            )

    def reinforce_episode(
        self,
        *,
        episode_id: str,
        recalled_at_ms: int,
        calculate: Callable[[Mapping[str, Any]], tuple[float, str]],
    ) -> tuple[float, str] | None:
        with self._write_uow() as conn:
            row = conn.execute(
                """
                SELECT memory_strength, truth_state, retention_state
                FROM life_episode WHERE episode_id = ?
                """,
                (str(episode_id),),
            ).fetchone()
            if row is None:
                return None
            strength, retention_state = calculate(dict(row))
            conn.execute(
                """
                UPDATE life_episode
                SET last_recalled_ts_ms = ?, recall_count = COALESCE(recall_count, 0) + 1,
                    reinforcement_count = COALESCE(reinforcement_count, 0) + 1,
                    memory_strength = ?, retention_state = ?
                WHERE episode_id = ?
                """,
                (
                    int(recalled_at_ms), float(strength), str(retention_state),
                    str(episode_id),
                ),
            )
            return float(strength), str(retention_state)

    def record_compaction(
        self,
        record: Mapping[str, Any],
        *,
        source_episode_ids: Sequence[str],
        cold_state: str,
        gone_state: str,
    ) -> None:
        ids = [str(value) for value in source_episode_ids if str(value)]
        with self._write_uow() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO life_memory_compaction (
                    compaction_id, source_episode_ids, compressed_episode_id,
                    summary, dropped_details, kept_facts, created_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    record.get(key)
                    for key in (
                        "compaction_id", "source_episode_ids", "compressed_episode_id",
                        "summary", "dropped_details", "kept_facts", "created_ts_ms",
                        "metadata",
                    )
                ),
            )
            conn.executemany(
                """
                UPDATE life_episode
                SET compressed_into_episode_id = ?,
                    retention_state = CASE WHEN COALESCE(memory_strength, 0.0) < 0.18
                                           THEN ? ELSE ? END,
                    forget_reason = COALESCE(forget_reason, 'compressed_into_summary'),
                    last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                WHERE episode_id = ?
                """,
                [
                    (
                        record.get("compressed_episode_id"), gone_state, cold_state,
                        record.get("created_ts_ms"), value,
                    )
                    for value in ids
                ],
            )

    def apply_forgetting_updates(
        self,
        updates: Sequence[tuple[float, str, str | None, str]],
    ) -> None:
        if not updates:
            return
        with self._write_uow() as conn:
            conn.executemany(
                """
                UPDATE life_episode
                SET memory_strength = ?, retention_state = ?,
                    forget_reason = COALESCE(?, forget_reason)
                WHERE episode_id = ?
                """,
                list(updates),
            )

    def fill_missing_forget_reasons(self, *, retention_state: str, reason: str) -> int:
        with self._write_uow() as conn:
            cursor = conn.execute(
                """
                UPDATE life_episode SET forget_reason = ?
                WHERE COALESCE(retention_state, '') = ?
                  AND (forget_reason IS NULL OR TRIM(forget_reason) = '')
                """,
                (str(reason), str(retention_state)),
            )
            return int(cursor.rowcount or 0)

    def update_episode_policy_batch(
        self,
        updates: Sequence[tuple[str, str, str]],
    ) -> None:
        if not updates:
            return
        with self._write_uow() as conn:
            conn.executemany(
                """
                UPDATE life_episode SET metadata = ?, decay_profile_id = ?
                WHERE episode_id = ?
                """,
                list(updates),
            )

    def soft_delete_semantic_memories(
        self,
        semantic_ids: Sequence[str],
        *,
        deleted_at_ms: int,
    ) -> None:
        ids = [str(value) for value in semantic_ids if str(value)]
        if not ids:
            return
        with self._write_uow() as conn:
            conn.execute(
                f"""
                UPDATE semantic_memory SET deleted_at = ?
                WHERE semantic_id IN ({', '.join('?' for _ in ids)})
                """,
                (int(deleted_at_ms), *ids),
            )

    def update_semantic_memory(
        self,
        *,
        semantic_id: str,
        summary: str,
        source_memory_ids_json: str,
        confidence: float,
        tags_json: str,
        ts_ms: int,
        operator_node_id: str,
    ) -> None:
        with self._write_uow() as conn:
            conn.execute(
                """
                UPDATE semantic_memory
                SET summary = ?, source_memory_ids = ?, confidence = ?, tags = ?,
                    ts_ms = ?, operator_node_id = ?
                WHERE semantic_id = ? AND deleted_at IS NULL
                """,
                (
                    summary, source_memory_ids_json, float(confidence), tags_json,
                    int(ts_ms), operator_node_id, semantic_id,
                ),
            )

    @staticmethod
    def _refresh_entity_mention_count(conn: sqlite3.Connection, entity_id: str) -> None:
        row = conn.execute(
            """
            SELECT COUNT(1) AS mention_count, MAX(created_ts_ms) AS last_seen_ts_ms
            FROM life_memory_entity_mention WHERE entity_id = ?
            """,
            (entity_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE life_memory_entity SET mention_count = ?,
                last_seen_ts_ms = MAX(COALESCE(last_seen_ts_ms, 0), ?)
            WHERE entity_id = ?
            """,
            (
                int(row["mention_count"] or 0) if row is not None else 0,
                int(row["last_seen_ts_ms"] or 0) if row is not None else 0,
                entity_id,
            ),
        )

    def _finish_projection(
        self,
        outbox_id: str,
        *,
        owner_id: str,
        success: bool,
        detail: str | None,
    ) -> None:
        now_value = self._now()
        conn = self._connect()
        try:
            with conn:
                changed = conn.execute(
                    """
                    UPDATE life_memory_projection_outbox
                    SET status = ?, claim_owner = NULL, claim_expires_ts_ms = NULL,
                        last_error = ?, updated_ts_ms = ?, published_ts_ms = ?
                    WHERE outbox_id = ? AND status = 'PROCESSING' AND claim_owner = ?
                    """,
                    (
                        "PUBLISHED" if success else "FAILED",
                        None if success else str(detail or "projection failed")[:1000],
                        now_value,
                        now_value if success else None,
                        str(outbox_id),
                        str(owner_id),
                    ),
                ).rowcount
            if changed != 1:
                raise LifeMemoryRepositoryError(
                    LIFE_MEMORY_ADMISSION_CONFLICT,
                    "projection claim was lost before completion",
                )
        finally:
            conn.close()

    def _insert_episode(self, conn: sqlite3.Connection, plan: ChatMemoryWritePlan) -> None:
        episode_columns = {
            str(row[1])
            for row in conn.execute('PRAGMA table_info("life_episode")').fetchall()
        }
        extended = {
            "actor_id",
            "intent",
            "action",
            "result",
            "base_salience",
            "memory_strength",
            "decay_profile_id",
            "retention_state",
        }
        if extended.issubset(episode_columns):
            conn.execute(
                """
                INSERT INTO life_episode (
                    episode_id, episode_kind, thread_id, person_id, session_id,
                    actor_id, intent, action, result, primary_ref, summary,
                    salience, base_salience, memory_strength, decay_profile_id,
                    retention_state, truth_state, status, created_ts_ms,
                    last_touched_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO NOTHING
                """,
                (
                    plan.episode_id,
                    plan.episode_kind,
                    plan.thread_id,
                    plan.person_id,
                    plan.session_id,
                    plan.actor_id,
                    plan.intent,
                    plan.action,
                    plan.result,
                    plan.primary_ref,
                    plan.summary,
                    plan.salience,
                    plan.salience,
                    plan.memory_strength,
                    plan.decay_profile_id,
                    plan.retention_state,
                    plan.truth_state,
                    plan.status,
                    plan.created_ts_ms,
                    plan.created_ts_ms,
                    _json_dumps(dict(plan.metadata)),
                ),
            )
        else:
            # 0045 is the historical minimum schema.  Extended retention
            # columns remain optional until their separate central migration;
            # the complete policy still lives in metadata and the receipt.
            conn.execute(
                """
                INSERT INTO life_episode (
                    episode_id, episode_kind, thread_id, person_id, session_id,
                    primary_ref, summary, salience, truth_state, status,
                    created_ts_ms, last_touched_ts_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO NOTHING
                """,
                (
                    plan.episode_id,
                    plan.episode_kind,
                    plan.thread_id,
                    plan.person_id,
                    plan.session_id,
                    plan.primary_ref,
                    plan.summary,
                    plan.salience,
                    plan.truth_state,
                    plan.status,
                    plan.created_ts_ms,
                    plan.created_ts_ms,
                    _json_dumps(dict(plan.metadata)),
                ),
            )
        row = conn.execute(
            "SELECT primary_ref FROM life_episode WHERE episode_id = ?",
            (plan.episode_id,),
        ).fetchone()
        if row is None or str(row["primary_ref"] or "") != plan.primary_ref:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_CONFLICT,
                "episode identity is already bound to a different request",
            )

    @staticmethod
    def _insert_evidence(conn: sqlite3.Connection, plan: ChatMemoryWritePlan) -> None:
        for evidence in plan.evidence:
            conn.execute(
                """
                INSERT OR IGNORE INTO life_evidence (
                    evidence_id, episode_id, evidence_kind, source_ref, request_id,
                    content, payload, trust_level, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    plan.episode_id,
                    evidence.evidence_kind,
                    evidence.source_ref,
                    evidence.request_id,
                    evidence.content,
                    _json_dumps(dict(evidence.payload)),
                    evidence.trust_level,
                    evidence.created_ts_ms,
                ),
            )

    @staticmethod
    def _load_projection_payload(conn: sqlite3.Connection, outbox_id: str) -> dict[str, Any]:
        outbox = conn.execute(
            "SELECT * FROM life_memory_projection_outbox WHERE outbox_id = ?",
            (outbox_id,),
        ).fetchone()
        if outbox is None:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "claimed projection outbox disappeared",
            )
        episode = conn.execute(
            "SELECT * FROM life_episode WHERE episode_id = ?",
            (outbox["episode_id"],),
        ).fetchone()
        evidence = conn.execute(
            """
            SELECT * FROM life_evidence
            WHERE episode_id = ?
            ORDER BY created_ts_ms ASC, evidence_id ASC
            """,
            (outbox["episode_id"],),
        ).fetchall()
        if episode is None:
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "projection source episode is missing",
            )
        return {
            "outbox": dict(outbox),
            "episode": dict(episode),
            "evidence": [dict(row) for row in evidence],
        }

    @staticmethod
    def _projection_status(conn: sqlite3.Connection, outbox_id: Any) -> str | None:
        if not outbox_id:
            return None
        row = conn.execute(
            "SELECT status FROM life_memory_projection_outbox WHERE outbox_id = ?",
            (str(outbox_id),),
        ).fetchone()
        return str(row["status"]) if row is not None else None

    @staticmethod
    def _assert_replay_hash(row: Mapping[str, Any], expected: str | None) -> None:
        if expected is None:
            return
        if str(row["command_hash"]) != str(expected):
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_ADMISSION_CONFLICT,
                "request_id replay changed the canonical chat candidate",
            )

    @staticmethod
    def _claim_from_row(row: Mapping[str, Any]) -> MemoryAdmissionClaim:
        return MemoryAdmissionClaim(
            request_id=str(row["request_id"]),
            command_hash=str(row["command_hash"]),
            owner_token=str(row["owner_token"]),
            claim_expires_ts_ms=int(row["claim_expires_ts_ms"]),
            attempt_count=int(row["attempt_count"]),
        )

    @staticmethod
    def _receipt_from_row(
        row: Mapping[str, Any],
        *,
        replayed: bool,
        projection_status: str | None,
    ) -> MemoryWriteReceipt:
        tags_raw = _json_loads(row["tags_json"], fallback=[])
        if not isinstance(tags_raw, list):
            raise LifeMemoryRepositoryError(
                LIFE_MEMORY_DURABLE_DATA_INVALID,
                "admission tags_json is not a list",
            )
        return MemoryWriteReceipt(
            request_id=str(row["request_id"]),
            command_hash=str(row["command_hash"]),
            decision=MemoryAdmissionKind(str(row["decision"])),
            status=str(row["status"]),
            reason_code=str(row["reason_code"]),
            worth_score=float(row["worth_score"]),
            tags=tuple(str(tag) for tag in tags_raw),
            episode_id=str(row["episode_id"]) if row["episode_id"] is not None else None,
            outbox_id=str(row["outbox_id"]) if row["outbox_id"] is not None else None,
            created_ts_ms=int(row["created_ts_ms"]),
            replayed=bool(replayed),
            projection_status=projection_status,
        )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: Any, *, fallback: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _normalized_record_ids(values: Sequence[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            cleaned
            for value in values
            if (cleaned := str(value or "").strip())
        )
    )


def _record_id_batches(values: Sequence[str], *, size: int = 400) -> Iterator[list[str]]:
    batch_size = max(1, int(size))
    for offset in range(0, len(values), batch_size):
        yield list(values[offset : offset + batch_size])


def _semantic_fetch_rows_fail_soft(
    conn: sqlite3.Connection,
    statement: str,
    params: Sequence[Any],
) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(statement, list(params)).fetchall()]
    except sqlite3.DatabaseError:
        return []


def _group_semantic_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
    per_key_limit: int,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    limit = max(1, int(per_key_limit))
    for row in rows:
        group_id = str(row.get(key) or "").strip()
        if not group_id:
            continue
        group = grouped.setdefault(group_id, [])
        if len(group) < limit:
            group.append(row)
    return {group_id: tuple(records) for group_id, records in grouped.items()}


def _merge_json_objects(current: Any, incoming: Any) -> dict[str, Any]:
    current_value = _json_loads(current, fallback={})
    incoming_value = _json_loads(incoming, fallback={})
    merged = dict(current_value) if isinstance(current_value, dict) else {}
    if isinstance(incoming, Mapping):
        incoming_value = dict(incoming)
    if isinstance(incoming_value, dict):
        merged.update(incoming_value)
    return merged


__all__ = [
    "LIFE_MEMORY_ADMISSION_CLAIM_LOST",
    "LIFE_MEMORY_ADMISSION_CONFLICT",
    "LIFE_MEMORY_ADMISSION_INVALID",
    "LIFE_MEMORY_DURABLE_DATA_INVALID",
    "LIFE_MEMORY_MIGRATION_ASSET_KEY",
    "LIFE_MEMORY_MIGRATION_SHA256",
    "LIFE_MEMORY_SCHEMA_NOT_READY",
    "LifeMemoryRepository",
    "LifeMemoryRepositoryError",
    "SemanticLineageBatch",
]
