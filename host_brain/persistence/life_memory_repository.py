"""Atomic SQLite repository for governed LifeMemory chat admission.

This adapter owns its connection and transaction.  It never reaches through
``SQLiteLedger._conn`` and never creates schema at runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import sqlite3
from typing import Any

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
]
