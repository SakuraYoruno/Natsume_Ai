"""SQLite UoW for the AW04-B Digital Life command foundation."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from natsume_contracts.life import (
    LIFE_AGGREGATE_CLAIM_BUSY,
    LIFE_AGGREGATE_CLAIM_FENCED,
    LIFE_COMMAND_BASELINE_REQUIRED,
    LIFE_COMMAND_CLAIM_BUSY,
    LIFE_COMMAND_CLAIM_FENCED,
    LIFE_COMMAND_DURABLE_DATA_INVALID,
    LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
    LIFE_COMMAND_INVALID,
    LIFE_COMMAND_SCHEMA_NOT_READY,
    LIFE_DELIVERY_CLAIM_FENCED,
    LIFE_PROJECTION_CLAIM_FENCED,
    LifeAggregateClaim,
    LifeCommand,
    LifeCommandClaim,
    LifeCommandPreview,
    LifeCommandReceipt,
    LifeCommandStatus,
    LifeDeliveryAuthorization,
    LifeDeliveryReceipt,
    LifeDeliveryStatus,
    LifeEvent,
    LifeIntentKind,
    LifeOutboxMessage,
    LifeProjectionIntent,
    LifeProjectionMessage,
    LifeProjectionReceipt,
    LifeSnapshot,
    LifeCommandRepositoryError,
    canonical_json,
    digest_json,
)


LIFE_COMMAND_MIGRATION_ASSET_KEY = "0079#life_command_foundation"
LIFE_COMMAND_MIGRATION_SHA256 = (
    "f2cc0b206b0d23c68e9bac97bde164039ecad16f61441f1f32b3c16115b573ca"
)
_ALLOWED_DISPOSITIONS = frozenset({"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"})
_REQUIRED_TABLES = frozenset(
    {
        "digital_life_state",
        "life_aggregate_claim",
        "life_command",
        "life_command_claim",
        "life_event_journal",
        "life_projection_intent",
        "life_projection_claim",
        "life_projection_receipt",
        "life_delivery_outbox",
        "life_delivery_receipt",
        "life_command_receipt",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_life_command_person_created",
        "idx_life_command_claim_expiry",
        "idx_life_aggregate_claim_expiry",
        "idx_life_event_person_sequence",
        "idx_life_projection_intent_event",
        "idx_life_projection_claim_pending",
        "idx_life_delivery_pending",
        "idx_life_delivery_command",
        "idx_life_delivery_authority",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "life_command_no_update",
        "life_command_no_delete",
        "life_event_journal_no_update",
        "life_event_journal_no_delete",
        "life_projection_intent_no_update",
        "life_projection_intent_no_delete",
        "life_projection_receipt_no_update",
        "life_projection_receipt_no_delete",
        "life_projection_receipt_insert_guard",
        "life_delivery_outbox_identity_no_update",
        "life_delivery_outbox_no_delete",
        "life_delivery_receipt_insert_guard",
        "life_delivery_receipt_no_update",
        "life_delivery_receipt_no_delete",
        "life_delivery_outbox_receipt_status_guard",
        "life_command_receipt_no_update",
        "life_command_receipt_no_delete",
        "life_command_state_projection_insert_guard",
        "life_command_state_projection_update_guard",
    }
)
_REQUIRED_COLUMNS = {
    "life_delivery_outbox": frozenset(
        {
            "authorization_request_id",
            "governor_grant_ref",
            "authority_kind",
            "authority_principal_id",
            "authorization_digest",
        }
    ),
    "life_delivery_receipt": frozenset(
        {
            "outbox_id",
            "delivery_key",
            "payload_digest",
            "authorization_digest",
            "result_json",
            "completed_at_ms",
        }
    ),
}


class LifeCommandRepository:
    """Operation-scoped, migration-first persistence boundary."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_INVALID,
                "LifeCommandRepository requires a durable SQLite path",
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
        if verify_schema:
            self.assert_schema_ready()

    @classmethod
    def from_ledger(cls, ledger: Any) -> "LifeCommandRepository":
        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path or db_path == ":memory:":
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_INVALID,
                "ledger must expose a durable canonical_db_path",
            )
        return cls(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.create_function(
            "natsume_sha256_hex",
            1,
            _sqlite_sha256_hex,
            deterministic=True,
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _operation(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_SCHEMA_NOT_READY,
                f"Digital Life database does not exist: {self.db_path}",
            )
        try:
            with self._operation(write=False) as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (LIFE_COMMAND_MIGRATION_ASSET_KEY,),
                ).fetchone()
                if (
                    asset is None
                    or str(asset["checksum"]) != LIFE_COMMAND_MIGRATION_SHA256
                    or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
                ):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_SCHEMA_NOT_READY,
                        "0079 Digital Life command migration is absent or drifted",
                    )
                tables = _catalog_names(conn, "table")
                indexes = _catalog_names(conn, "index")
                triggers = _catalog_names(conn, "trigger")
                if not _REQUIRED_TABLES.issubset(tables):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_SCHEMA_NOT_READY,
                        "Digital Life command tables are incomplete",
                    )
                if not _REQUIRED_INDEXES.issubset(indexes):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_SCHEMA_NOT_READY,
                        "Digital Life command indexes are incomplete",
                    )
                if not _REQUIRED_TRIGGERS.issubset(triggers):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_SCHEMA_NOT_READY,
                        "Digital Life immutable journal triggers are incomplete",
                    )
                for table_name, required_columns in _REQUIRED_COLUMNS.items():
                    if not required_columns.issubset(
                        _table_columns(conn, table_name)
                    ):
                        raise LifeCommandRepositoryError(
                            LIFE_COMMAND_SCHEMA_NOT_READY,
                            f"Digital Life table {table_name} has incomplete authority columns",
                        )
        except LifeCommandRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_SCHEMA_NOT_READY,
                str(exc),
            ) from exc

    # -- Command claim and atomic commit -----------------------------

    def try_claim_command(
        self,
        command: LifeCommand,
        *,
        owner_token: str,
        lease_ms: int,
        now_ms: int,
    ) -> LifeCommandReceipt | LifeCommandClaim | None:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            receipt = self._receipt_row(conn, command.command_id)
            if receipt is not None:
                self._assert_command_hash(command.command_hash, receipt["command_hash"])
                return _receipt_from_row(receipt).as_replayed()
            committed = conn.execute(
                "SELECT command_hash FROM life_command WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if committed is not None:
                self._assert_command_hash(command.command_hash, committed["command_hash"])
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "command exists without an atomic receipt",
                )
            claim = conn.execute(
                "SELECT * FROM life_command_claim WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if claim is None:
                conn.execute(
                    """
                    INSERT INTO life_command_claim(
                        command_id, command_hash, owner_token, fencing_token,
                        claim_expires_at_ms, attempt_count, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, 1, ?, 1, ?, ?)
                    """,
                    (
                        command.command_id,
                        command.command_hash,
                        owner,
                        expires,
                        now_value,
                        now_value,
                    ),
                )
                return LifeCommandClaim(
                    command_id=command.command_id,
                    command_hash=command.command_hash,
                    owner_token=owner,
                    fencing_token=1,
                    claim_expires_at_ms=expires,
                    attempt_count=1,
                )
            self._assert_command_hash(command.command_hash, claim["command_hash"])
            if int(claim["claim_expires_at_ms"]) > now_value:
                return None
            fencing_token = int(claim["fencing_token"]) + 1
            attempt_count = int(claim["attempt_count"]) + 1
            conn.execute(
                """
                UPDATE life_command_claim
                SET owner_token = ?, fencing_token = ?, claim_expires_at_ms = ?,
                    attempt_count = ?, updated_at_ms = ?
                WHERE command_id = ?
                """,
                (
                    owner,
                    fencing_token,
                    expires,
                    attempt_count,
                    now_value,
                    command.command_id,
                ),
            )
            return LifeCommandClaim(
                command_id=command.command_id,
                command_hash=command.command_hash,
                owner_token=owner,
                fencing_token=fencing_token,
                claim_expires_at_ms=expires,
                attempt_count=attempt_count,
            )

    def release_command_claim(self, claim: LifeCommandClaim, *, now_ms: int) -> None:
        with self._operation(write=True) as conn:
            conn.execute(
                """
                UPDATE life_command_claim
                SET claim_expires_at_ms = 0, updated_at_ms = ?
                WHERE command_id = ? AND owner_token = ? AND fencing_token = ?
                """,
                (
                    max(0, int(now_ms)),
                    claim.command_id,
                    claim.owner_token,
                    claim.fencing_token,
                ),
            )

    def acquire_aggregate_claim(
        self,
        person_id: str,
        *,
        owner_token: str,
        lease_ms: int,
        now_ms: int,
    ) -> LifeAggregateClaim | None:
        person = _required_text(person_id, "person_id")
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM life_aggregate_claim WHERE person_id = ?",
                (person,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO life_aggregate_claim(
                        person_id, owner_token, fencing_token,
                        lease_expires_at_ms, attempt_count,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, ?, 1, ?, 1, ?, ?)
                    """,
                    (person, owner, expires, now_value, now_value),
                )
                return LifeAggregateClaim(
                    person_id=person,
                    owner_token=owner,
                    fencing_token=1,
                    lease_expires_at_ms=expires,
                    attempt_count=1,
                )
            if int(row["lease_expires_at_ms"]) > now_value:
                return None
            old_fence = int(row["fencing_token"])
            fencing_token = old_fence + 1
            attempt_count = int(row["attempt_count"]) + 1
            cursor = conn.execute(
                """
                UPDATE life_aggregate_claim
                SET owner_token = ?, fencing_token = ?, lease_expires_at_ms = ?,
                    attempt_count = ?, updated_at_ms = ?
                WHERE person_id = ? AND fencing_token = ?
                  AND lease_expires_at_ms <= ?
                """,
                (
                    owner,
                    fencing_token,
                    expires,
                    attempt_count,
                    now_value,
                    person,
                    old_fence,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            return LifeAggregateClaim(
                person_id=person,
                owner_token=owner,
                fencing_token=fencing_token,
                lease_expires_at_ms=expires,
                attempt_count=attempt_count,
            )

    def release_aggregate_claim(
        self,
        claim: LifeAggregateClaim,
        *,
        now_ms: int,
    ) -> bool:
        with self._operation(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE life_aggregate_claim
                SET lease_expires_at_ms = 0, updated_at_ms = ?
                WHERE person_id = ? AND owner_token = ? AND fencing_token = ?
                """,
                (
                    max(0, int(now_ms)),
                    claim.person_id,
                    claim.owner_token,
                    claim.fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def commit_command(
        self,
        preview: LifeCommandPreview,
        *,
        claim: LifeCommandClaim,
        now_ms: int,
        aggregate_claim: LifeAggregateClaim | None = None,
    ) -> LifeCommandReceipt:
        command = preview.command
        now_value = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            existing = self._receipt_row(conn, command.command_id)
            if existing is not None:
                self._assert_command_hash(command.command_hash, existing["command_hash"])
                return _receipt_from_row(existing).as_replayed()
            self._assert_live_claim(conn, claim, now_ms=now_value)
            if aggregate_claim is not None:
                self._assert_live_aggregate_claim(
                    conn,
                    aggregate_claim,
                    person_id=command.person_id,
                    now_ms=now_value,
                )
            current = self._snapshot_from_connection(conn, command.person_id)
            if current.version > 0 and current.last_event_id is None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_BASELINE_REQUIRED,
                    "legacy state projection requires explicit baseline adoption",
                )
            if current.version != command.expected_version:
                receipt = self._commit_conflict(
                    conn,
                    command=command,
                    current=current,
                    reason_code="LIFE_COMMAND_VERSION_CONFLICT",
                )
                conn.execute(
                    "DELETE FROM life_command_claim WHERE command_id = ?",
                    (command.command_id,),
                )
                return receipt
            if (
                preview.before.state_digest != current.state_digest
                or preview.before.version != current.version
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_CLAIM_FENCED,
                    "snapshot changed after command reduction",
                )

            self._insert_command(conn, command)
            self._fault("after_command")
            self._insert_event(conn, preview.event)
            self._fault("after_event")
            self._write_projection(conn, preview.after, expected_version=current.version)
            self._fault("after_state_projection")
            for intent in preview.projection_intents:
                conn.execute(
                    """
                    INSERT INTO life_projection_intent(
                        intent_id, event_id, command_id, person_id, intent_kind,
                        target, payload_json, payload_digest, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.intent_id,
                        intent.event_id,
                        intent.command_id,
                        intent.person_id,
                        intent.intent_kind.value,
                        intent.target,
                        canonical_json(dict(intent.payload)),
                        intent.payload_digest,
                        intent.created_at_ms,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO life_projection_claim(
                        intent_id, status, available_at_ms, attempt_count,
                        fencing_token, created_at_ms, updated_at_ms
                    ) VALUES (?, 'PENDING', ?, 0, 0, ?, ?)
                    """,
                    (
                        intent.intent_id,
                        intent.created_at_ms,
                        intent.created_at_ms,
                        intent.created_at_ms,
                    ),
                )
            self._fault("after_projection_intents")
            outbox_count = self._insert_outbox(conn, preview)
            self._fault("after_outbox")
            receipt = LifeCommandReceipt(
                command_id=command.command_id,
                command_hash=command.command_hash,
                person_id=command.person_id,
                status=LifeCommandStatus.APPLIED,
                reason_code=None,
                event_id=preview.event.event_id,
                state_version=preview.after.version,
                state_digest=preview.after.state_digest,
                outbox_count=outbox_count,
                created_at_ms=preview.event.created_at_ms,
            )
            self._insert_receipt(conn, receipt)
            self._fault("after_receipt")
            conn.execute(
                "DELETE FROM life_command_claim WHERE command_id = ?",
                (command.command_id,),
            )
            return receipt

    def adopt_legacy_projection(
        self,
        *,
        command: LifeCommand,
        event: LifeEvent,
        receipt: LifeCommandReceipt,
    ) -> LifeCommandReceipt:
        """Atomically anchor one existing projection without changing it."""

        with self._operation(write=True) as conn:
            existing = self._receipt_row(conn, command.command_id)
            if existing is not None:
                self._assert_command_hash(command.command_hash, existing["command_hash"])
                return _receipt_from_row(existing).as_replayed()
            current = self._snapshot_from_connection(conn, command.person_id)
            if current.version <= 0:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_INVALID,
                    "only a non-empty legacy projection can be adopted",
                )
            if current.last_event_id is not None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "projection already has a command journal baseline",
                )
            state_after = event.payload.get("state_after")
            if (
                command.command_type != "baseline_adopted"
                or event.event_type != "baseline_adopted"
                or event.sequence != current.version
                or event.state_before_digest != current.state_digest
                or event.state_after_digest != current.state_digest
                or not isinstance(state_after, Mapping)
                or digest_json(dict(state_after)) != current.state_digest
                or receipt.event_id != event.event_id
                or receipt.state_version != current.version
                or receipt.state_digest != current.state_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_INVALID,
                    "baseline adoption records do not match the durable projection",
                )
            self._insert_command(conn, command)
            self._fault("after_baseline_command")
            self._insert_event(conn, event)
            self._fault("after_baseline_event")
            self._insert_receipt(conn, receipt)
            self._fault("after_baseline_receipt")
            return receipt

    def _commit_conflict(
        self,
        conn: sqlite3.Connection,
        *,
        command: LifeCommand,
        current: LifeSnapshot,
        reason_code: str,
    ) -> LifeCommandReceipt:
        self._insert_command(conn, command)
        receipt = LifeCommandReceipt(
            command_id=command.command_id,
            command_hash=command.command_hash,
            person_id=command.person_id,
            status=LifeCommandStatus.CONFLICT,
            reason_code=reason_code,
            event_id=None,
            state_version=current.version,
            state_digest=current.state_digest,
            outbox_count=0,
            created_at_ms=command.created_at_ms,
        )
        self._insert_receipt(conn, receipt)
        return receipt

    # -- Pure reads and projection rebuild ---------------------------

    def get_snapshot(self, person_id: str) -> LifeSnapshot:
        with self._operation(write=False) as conn:
            return self._snapshot_from_connection(conn, person_id)

    def get_receipt(self, command_id: str) -> LifeCommandReceipt | None:
        with self._operation(write=False) as conn:
            row = self._receipt_row(conn, command_id)
            return None if row is None else _receipt_from_row(row)

    def list_unadopted_projection_person_ids(self, *, limit: int = 1_000) -> list[str]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT state.person_id
                FROM digital_life_state AS state
                WHERE state.version > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM life_event_journal AS event
                      WHERE event.person_id = state.person_id
                  )
                ORDER BY state.person_id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 10_000)),),
            ).fetchall()
            return [str(row["person_id"]) for row in rows]

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[LifeEvent]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_event_journal
                WHERE person_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (
                    _required_text(person_id, "person_id"),
                    max(0, int(after_sequence)),
                    max(1, min(int(limit), 1_000)),
                ),
            ).fetchall()
            return [_event_from_row(row) for row in rows]

    def list_affective_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the DL-01 journal projection in a stable compound cursor."""

        cursor_ms = max(0, int(after_created_at_ms))
        cursor_event_id = str(after_event_id or "")
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT event_id, person_id, self_id, relationship_subject_id,
                       affective_kind, appraisal_id, episode_id, state_revision,
                       event_digest, payload_digest, created_at_ms
                FROM affective_life_event_v1
                WHERE person_id = ?
                  AND (
                      created_at_ms > ?
                      OR (created_at_ms = ? AND event_id > ?)
                  )
                ORDER BY created_at_ms ASC, event_id ASC
                LIMIT ?
                """,
                (
                    _required_text(person_id, "person_id"),
                    cursor_ms,
                    cursor_ms,
                    cursor_event_id,
                    max(1, min(int(limit), 1_000)),
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_relationship_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the DL-03 projection in a stable compound cursor."""

        cursor_ms = max(0, int(after_created_at_ms))
        cursor_event_id = str(after_event_id or "")
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT event_id, person_id, self_id, workspace_id,
                       relationship_subject_id, relationship_kind, episode_id,
                       aftereffect_id, state_revision, event_digest,
                       payload_digest, created_at_ms
                FROM relationship_life_event_v1
                WHERE person_id = ?
                  AND (
                      created_at_ms > ?
                      OR (created_at_ms = ? AND event_id > ?)
                  )
                ORDER BY created_at_ms ASC, event_id ASC
                LIMIT ?
                """,
                (
                    _required_text(person_id, "person_id"),
                    cursor_ms,
                    cursor_ms,
                    cursor_event_id,
                    max(1, min(int(limit), 1_000)),
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_companion_proactive_targets(
        self,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        """Read the canonical target registry from durable Life projections.

        Target registration is part of the immutable accepted-response event;
        this is intentionally a read-only registry view instead of a second
        mutable scheduler queue.  Invalid or stale compatibility projections
        are skipped fail-closed and cannot become outbound candidates.
        """

        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT person_id, version, state_json, updated_at_ms
                FROM digital_life_state
                ORDER BY person_id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 10_000)),),
            ).fetchall()
        targets: list[dict[str, Any]] = []
        for row in rows:
            state = _json_object(
                row["state_json"],
                "digital_life_state.state_json",
            )
            relationship_raw = state.get("relationship_v2")
            target_raw = state.get("companion_proactive_target")
            affective_raw = state.get("affective")
            if not all(
                isinstance(value, Mapping)
                for value in (relationship_raw, target_raw, affective_raw)
            ):
                continue
            relationship = dict(relationship_raw)
            target = dict(target_raw)
            affective = dict(affective_raw)
            workspace_id = str(relationship.get("workspace_id") or "").strip()
            subject_id = str(
                relationship.get("relationship_subject_id") or ""
            ).strip()
            if (
                not workspace_id
                or not subject_id
                or str(target.get("workspace_id") or "").strip() != workspace_id
                or str(target.get("relationship_subject_id") or "").strip()
                != subject_id
                or str(relationship.get("proactive_permission") or "").upper()
                != "ALLOWED"
                or bool(relationship.get("repair_required"))
                or bool(list(relationship.get("active_episodes") or []))
            ):
                continue
            target_id = str(target.get("target_id") or "").strip()
            platform = str(target.get("platform") or "").strip().lower()
            conversation_id = str(target.get("conversation_id") or "").strip()
            session_id = str(target.get("session_id") or "").strip()
            if not target_id or not platform or not conversation_id or not session_id:
                continue
            targets.append(
                {
                    "aggregate_id": str(row["person_id"]),
                    "state_version": int(row["version"]),
                    "updated_at_ms": int(row["updated_at_ms"]),
                    "target_id": target_id,
                    "workspace_id": workspace_id,
                    "relationship_subject_id": subject_id,
                    "session_id": session_id,
                    "platform": platform,
                    "conversation_id": conversation_id,
                    "registered_at_ms": max(
                        0, int(target.get("registered_at_ms") or 0)
                    ),
                    "relationship_revision": max(
                        0, int(relationship.get("revision") or 0)
                    ),
                    "self_affect_revision": max(
                        0, int(affective.get("revision") or 0)
                    ),
                }
            )
        return targets

    def list_affective_turn_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read DL-04 plan/sentence delivery identity without exposing text."""

        cursor_ms = max(0, int(after_created_at_ms))
        cursor_event_id = str(after_event_id or "")
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT event_id, person_id, self_id, workspace_id,
                       relationship_subject_id, session_id, delivery_kind,
                       plan_id, synchronization_id, self_affect_revision,
                       sentence_index, sentence_digest, plan_digest,
                       event_digest, payload_digest, created_at_ms
                FROM affective_turn_delivery_event_v1
                WHERE person_id = ?
                  AND (
                      created_at_ms > ?
                      OR (created_at_ms = ? AND event_id > ?)
                  )
                ORDER BY created_at_ms ASC, event_id ASC
                LIMIT ?
                """,
                (
                    _required_text(person_id, "person_id"),
                    cursor_ms,
                    cursor_ms,
                    cursor_event_id,
                    max(1, min(int(limit), 1_000)),
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_affective_expression_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        after_delivery_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read the DL-05 Host expression protocol without raw media/text."""

        cursor_ms = max(0, int(after_created_at_ms))
        cursor_event_id = str(after_event_id or "")
        cursor_delivery_id = str(after_delivery_id or "")
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT event_id, delivery_id, person_id, self_id, workspace_id,
                       relationship_subject_id, session_id, delivery_kind,
                       channel, action, plan_id, synchronization_id,
                       self_affect_revision, sentence_index,
                       avatar_expression, previous_avatar_expression,
                       previous_expression_source, receipt_status,
                       event_digest, payload_digest, created_at_ms
                FROM affective_expression_delivery_event_v1
                WHERE person_id = ?
                  AND (
                      created_at_ms > ?
                      OR (
                          created_at_ms = ?
                          AND (
                              event_id > ?
                              OR (event_id = ? AND delivery_id > ?)
                          )
                      )
                  )
                ORDER BY created_at_ms ASC, event_id ASC, delivery_id ASC
                LIMIT ?
                """,
                (
                    _required_text(person_id, "person_id"),
                    cursor_ms,
                    cursor_ms,
                    cursor_event_id,
                    cursor_event_id,
                    cursor_delivery_id,
                    max(1, min(int(limit), 1_000)),
                ),
            ).fetchall()
            return [dict(row) for row in rows]

    def last_affective_expression_delivery_head(
        self,
        person_id: str,
        *,
        workspace_id: str,
        relationship_subject_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Return the last Host-staged avatar transition in an exact scope.

        This is not an Edge-applied state.  It gives the next immutable
        expression plan a deterministic prior transition only when the
        canonical Life journal already accepted it.
        """

        with self._operation(write=False) as conn:
            row = conn.execute(
                """
                SELECT event_id, delivery_id, person_id, self_id, workspace_id,
                       relationship_subject_id, session_id, delivery_kind,
                       channel, action, plan_id, synchronization_id,
                       self_affect_revision, sentence_index,
                       avatar_expression, previous_avatar_expression,
                       previous_expression_source, receipt_status,
                       event_digest, payload_digest, created_at_ms
                FROM affective_expression_delivery_event_v1
                WHERE person_id = ?
                  AND workspace_id = ?
                  AND relationship_subject_id = ?
                  AND session_id = ?
                  AND channel = 'AVATAR'
                  AND action = 'TRANSITION'
                  AND delivery_kind = 'INTENT'
                  AND receipt_status = 'HOST_PROTOCOL_STAGED'
                  AND avatar_expression <> ''
                ORDER BY created_at_ms DESC, event_id DESC, delivery_id DESC
                LIMIT 1
                """,
                (
                    _required_text(person_id, "person_id"),
                    _required_text(workspace_id, "workspace_id"),
                    _required_text(
                        relationship_subject_id,
                        "relationship_subject_id",
                    ),
                    _required_text(session_id, "session_id"),
                ),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_replay_rows(self, person_id: str) -> list[dict[str, Any]]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT c.command_id, c.command_hash, c.person_id, c.command_type,
                       c.payload_json, c.expected_version, c.correlation_id,
                       c.causation_id, c.metadata_json, c.created_at_ms,
                       e.event_id, e.sequence, e.event_type, e.event_payload_json,
                       e.state_before_digest, e.state_after_digest,
                       e.previous_event_digest, e.event_digest
                FROM life_event_journal e
                JOIN life_command c ON c.command_id = e.command_id
                WHERE e.person_id = ? ORDER BY e.sequence ASC
                """,
                (_required_text(person_id, "person_id"),),
            ).fetchall()
            return [dict(row) for row in rows]

    def rebuild_projection(self, snapshot: LifeSnapshot) -> None:
        with self._operation(write=True) as conn:
            latest = conn.execute(
                """
                SELECT sequence, event_digest FROM life_event_journal
                WHERE person_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (snapshot.person_id,),
            ).fetchone()
            expected_version = int(latest["sequence"]) if latest is not None else 0
            expected_digest = str(latest["event_digest"]) if latest is not None else None
            if (
                snapshot.version != expected_version
                or snapshot.last_event_digest != expected_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "rebuild snapshot does not match journal head",
                )
            conn.execute(
                """
                INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    version = excluded.version,
                    state_json = excluded.state_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    snapshot.person_id,
                    snapshot.version,
                    canonical_json(dict(snapshot.state)),
                    snapshot.updated_at_ms,
                ),
            )

    # -- Projection intent dispatcher --------------------------------

    def list_projection_intents(
        self,
        command_id: str,
    ) -> list[LifeProjectionIntent]:
        command = _required_text(command_id, "command_id")
        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_projection_intent
                WHERE command_id = ?
                ORDER BY created_at_ms ASC, intent_id ASC
                """,
                (command,),
            ).fetchall()
            return [_projection_intent_from_row(row) for row in rows]

    def claim_projection_intents(
        self,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
        limit: int,
        intent_id: str | None = None,
    ) -> list[LifeProjectionMessage]:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        exact_intent = (
            _required_text(intent_id, "intent_id")
            if intent_id is not None
            else None
        )
        exact_filter = (
            "AND intent.intent_id = ?" if exact_intent is not None else ""
        )
        parameters: list[Any] = [now_value, now_value]
        if exact_intent is not None:
            parameters.append(exact_intent)
        parameters.append(max(1, min(int(limit), 1_000)))
        with self._operation(write=True) as conn:
            rows = conn.execute(
                f"""
                SELECT intent.*, claim.status, claim.available_at_ms,
                       claim.attempt_count, claim.fencing_token,
                       claim.claim_expires_at_ms
                FROM life_projection_claim AS claim
                JOIN life_projection_intent AS intent
                  ON intent.intent_id = claim.intent_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM life_projection_receipt AS receipt
                    WHERE receipt.intent_id = intent.intent_id
                )
                  AND (
                    claim.status IN ('PENDING', 'FAILED')
                    AND claim.available_at_ms <= ?
                    OR claim.status = 'CLAIMED'
                    AND COALESCE(claim.claim_expires_at_ms, 0) <= ?
                  )
                  {exact_filter}
                ORDER BY claim.available_at_ms ASC,
                         intent.created_at_ms ASC, intent.intent_id ASC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            claimed: list[LifeProjectionMessage] = []
            for row in rows:
                old_fence = int(row["fencing_token"] or 0)
                fencing_token = old_fence + 1
                attempt_count = int(row["attempt_count"] or 0) + 1
                cursor = conn.execute(
                    """
                    UPDATE life_projection_claim
                    SET status = 'CLAIMED', claim_owner = ?, fencing_token = ?,
                        claim_expires_at_ms = ?, attempt_count = ?, updated_at_ms = ?
                    WHERE intent_id = ? AND fencing_token = ?
                    """,
                    (
                        owner,
                        fencing_token,
                        expires,
                        attempt_count,
                        now_value,
                        row["intent_id"],
                        old_fence,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append(
                    LifeProjectionMessage(
                        intent_id=str(row["intent_id"]),
                        event_id=str(row["event_id"]),
                        command_id=str(row["command_id"]),
                        person_id=str(row["person_id"]),
                        intent_kind=LifeIntentKind(str(row["intent_kind"])),
                        target=str(row["target"]),
                        payload=_json_object(row["payload_json"], "payload_json"),
                        payload_digest=str(row["payload_digest"]),
                        attempt_count=attempt_count,
                        fencing_token=fencing_token,
                    )
                )
            return claimed

    def complete_projection(
        self,
        message: LifeProjectionMessage,
        *,
        owner_token: str,
        result: Mapping[str, Any],
        now_ms: int,
    ) -> LifeProjectionReceipt:
        completed_at = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            self._assert_projection_message(conn, message)
            existing = conn.execute(
                "SELECT * FROM life_projection_receipt WHERE intent_id = ?",
                (message.intent_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_digest"]) != message.payload_digest:
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_DURABLE_DATA_INVALID,
                        "projection receipt payload digest drift",
                    )
                return _projection_receipt_from_row(existing)
            claim = conn.execute(
                "SELECT * FROM life_projection_claim WHERE intent_id = ?",
                (message.intent_id,),
            ).fetchone()
            if (
                claim is None
                or str(claim["status"]) != "CLAIMED"
                or str(claim["claim_owner"] or "")
                != _required_text(owner_token, "owner_token")
                or int(claim["fencing_token"]) != message.fencing_token
                or int(claim["claim_expires_at_ms"] or 0) <= completed_at
            ):
                raise LifeCommandRepositoryError(
                    LIFE_PROJECTION_CLAIM_FENCED,
                    "projection claim is no longer owned by this dispatcher",
                )
            receipt = LifeProjectionReceipt(
                intent_id=message.intent_id,
                payload_digest=message.payload_digest,
                result=dict(result),
                completed_at_ms=completed_at,
            )
            conn.execute(
                """
                INSERT INTO life_projection_receipt(
                    intent_id, payload_digest, result_json, completed_at_ms
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.intent_id,
                    receipt.payload_digest,
                    canonical_json(dict(receipt.result)),
                    receipt.completed_at_ms,
                ),
            )
            conn.execute(
                "DELETE FROM life_projection_claim WHERE intent_id = ?",
                (message.intent_id,),
            )
            return receipt

    def renew_projection_claim(
        self,
        message: LifeProjectionMessage,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
    ) -> None:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            self._assert_projection_message(conn, message)
            cursor = conn.execute(
                """
                UPDATE life_projection_claim
                SET claim_expires_at_ms = ?, updated_at_ms = ?
                WHERE intent_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                  AND COALESCE(claim_expires_at_ms, 0) > ?
                """,
                (
                    expires,
                    now_value,
                    message.intent_id,
                    owner,
                    message.fencing_token,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_PROJECTION_CLAIM_FENCED,
                    "projection claim expired or is no longer owned",
                )

    def fail_projection(
        self,
        message: LifeProjectionMessage,
        *,
        owner_token: str,
        error: str,
        retry_at_ms: int,
        now_ms: int,
    ) -> None:
        now_value = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            self._assert_projection_message(conn, message)
            cursor = conn.execute(
                """
                UPDATE life_projection_claim
                SET status = 'FAILED', available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, last_error = ?, updated_at_ms = ?
                WHERE intent_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                  AND COALESCE(claim_expires_at_ms, 0) > ?
                """,
                (
                    max(0, int(retry_at_ms)),
                    str(error or "projection failed")[:2_000],
                    now_value,
                    message.intent_id,
                    _required_text(owner_token, "owner_token"),
                    message.fencing_token,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_PROJECTION_CLAIM_FENCED,
                    "projection claim is no longer owned by this dispatcher",
                )

    def retry_projection(self, intent_id: str, *, now_ms: int) -> bool:
        with self._operation(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE life_projection_claim
                SET status = 'PENDING', available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, last_error = NULL, updated_at_ms = ?
                WHERE intent_id = ? AND status = 'FAILED'
                """,
                (max(0, int(now_ms)), max(0, int(now_ms)), str(intent_id)),
            )
            return cursor.rowcount == 1

    def get_projection_receipt(
        self,
        intent_id: str,
    ) -> LifeProjectionReceipt | None:
        with self._operation(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM life_projection_receipt WHERE intent_id = ?",
                (str(intent_id),),
            ).fetchone()
            return None if row is None else _projection_receipt_from_row(row)

    def list_projection_claims(self) -> list[dict[str, Any]]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM life_projection_claim ORDER BY intent_id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    # -- Delivery outbox ---------------------------------------------

    def claim_deliveries(
        self,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
        limit: int,
    ) -> list[LifeOutboxMessage]:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_delivery_outbox
                WHERE NOT EXISTS (
                    SELECT 1 FROM life_delivery_receipt
                    WHERE life_delivery_receipt.outbox_id = life_delivery_outbox.outbox_id
                ) AND (
                    (
                        status IN ('PENDING', 'FAILED') AND available_at_ms <= ?
                    ) OR (
                        status = 'CLAIMED' AND COALESCE(claim_expires_at_ms, 0) <= ?
                    )
                )
                ORDER BY available_at_ms ASC, created_at_ms ASC, outbox_id ASC
                LIMIT ?
                """,
                (now_value, now_value, max(1, min(int(limit), 1_000))),
            ).fetchall()
            claimed: list[LifeOutboxMessage] = []
            for row in rows:
                fencing_token = int(row["fencing_token"] or 0) + 1
                attempt_count = int(row["attempt_count"] or 0) + 1
                cursor = conn.execute(
                    """
                    UPDATE life_delivery_outbox
                    SET status = 'CLAIMED', claim_owner = ?, fencing_token = ?,
                        claim_expires_at_ms = ?, attempt_count = ?, updated_at_ms = ?
                    WHERE outbox_id = ? AND fencing_token = ?
                    """,
                    (
                        owner,
                        fencing_token,
                        expires,
                        attempt_count,
                        now_value,
                        row["outbox_id"],
                        int(row["fencing_token"] or 0),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append(
                    _outbox_from_row(
                        {
                            **dict(row),
                            "status": LifeDeliveryStatus.CLAIMED.value,
                            "attempt_count": attempt_count,
                            "fencing_token": fencing_token,
                        }
                    )
                )
            return claimed

    def complete_delivery(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        result: Mapping[str, Any],
        now_ms: int,
    ) -> LifeDeliveryReceipt:
        owner = _required_text(owner_token, "owner_token")
        completed_at = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            existing = conn.execute(
                "SELECT * FROM life_delivery_receipt WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if existing is not None:
                receipt = _delivery_receipt_from_row(existing)
                _assert_delivery_receipt_matches(message, receipt)
                return receipt
            row = conn.execute(
                "SELECT * FROM life_delivery_outbox WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != LifeDeliveryStatus.CLAIMED.value
                or str(row["claim_owner"] or "") != owner
                or int(row["fencing_token"]) != message.fencing_token
                or int(row["claim_expires_at_ms"] or 0) <= completed_at
            ):
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim is no longer owned by this dispatcher",
                )
            if (
                str(row["payload_digest"]) != message.payload_digest
                or str(row["authorization_digest"])
                != message.authorization_digest
                or str(row["delivery_key"]) != message.delivery_key
                or digest_json(dict(message.payload)) != message.payload_digest
                or digest_json(message.authorization.as_dict())
                != message.authorization_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery claim payload or authorization digest drift",
                )
            receipt = LifeDeliveryReceipt(
                outbox_id=message.outbox_id,
                delivery_key=message.delivery_key,
                payload_digest=message.payload_digest,
                authorization_digest=message.authorization_digest,
                result=dict(result),
                completed_at_ms=completed_at,
            )
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET status = 'DELIVERED', result_json = ?, last_error = NULL,
                    available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, updated_at_ms = ?,
                    delivered_at_ms = ?
                WHERE outbox_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                """,
                (
                    canonical_json(dict(receipt.result)),
                    completed_at,
                    completed_at,
                    completed_at,
                    message.outbox_id,
                    owner,
                    message.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim is no longer owned by this dispatcher",
                )
            conn.execute(
                """
                INSERT INTO life_delivery_receipt(
                    outbox_id, delivery_key, payload_digest,
                    authorization_digest, result_json, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.outbox_id,
                    receipt.delivery_key,
                    receipt.payload_digest,
                    receipt.authorization_digest,
                    canonical_json(dict(receipt.result)),
                    receipt.completed_at_ms,
                ),
            )
            return receipt

    def renew_delivery_claim(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
    ) -> None:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM life_delivery_outbox WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if row is None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery outbox row is missing",
                )
            durable = _outbox_from_row(row)
            if (
                durable.event_id != message.event_id
                or durable.command_id != message.command_id
                or durable.person_id != message.person_id
                or durable.destination != message.destination
                or durable.delivery_key != message.delivery_key
                or durable.payload_digest != message.payload_digest
                or durable.authorization_digest != message.authorization_digest
                or durable.authorization != message.authorization
                or digest_json(dict(message.payload)) != message.payload_digest
                or digest_json(message.authorization.as_dict())
                != message.authorization_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery message differs from durable outbox identity",
                )
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET claim_expires_at_ms = ?, updated_at_ms = ?
                WHERE outbox_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                  AND COALESCE(claim_expires_at_ms, 0) > ?
                """,
                (
                    expires,
                    now_value,
                    message.outbox_id,
                    owner,
                    message.fencing_token,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim expired or is no longer owned",
                )

    def fail_delivery(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        error: str,
        retry_at_ms: int,
        now_ms: int,
    ) -> None:
        self._finish_delivery(
            message,
            owner_token=owner_token,
            status=LifeDeliveryStatus.FAILED,
            result={},
            error=str(error or "delivery failed")[:2_000],
            available_at_ms=max(0, int(retry_at_ms)),
            now_ms=now_ms,
        )

    def retry_delivery(self, outbox_id: str, *, now_ms: int) -> bool:
        with self._operation(write=True) as conn:
            receipt = conn.execute(
                "SELECT 1 FROM life_delivery_receipt WHERE outbox_id = ?",
                (str(outbox_id),),
            ).fetchone()
            if receipt is not None:
                return False
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET status = 'PENDING', available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, last_error = NULL, updated_at_ms = ?
                WHERE outbox_id = ? AND status = 'FAILED'
                """,
                (max(0, int(now_ms)), max(0, int(now_ms)), str(outbox_id)),
            )
            return cursor.rowcount == 1

    def get_delivery_receipt(
        self,
        outbox_id: str,
    ) -> LifeDeliveryReceipt | None:
        with self._operation(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM life_delivery_receipt WHERE outbox_id = ?",
                (str(outbox_id),),
            ).fetchone()
            return None if row is None else _delivery_receipt_from_row(row)

    def list_delivery_receipts(self) -> list[LifeDeliveryReceipt]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM life_delivery_receipt ORDER BY outbox_id ASC"
            ).fetchall()
            return [_delivery_receipt_from_row(row) for row in rows]

    def list_outbox(self, *, command_id: str | None = None) -> list[dict[str, Any]]:
        with self._operation(write=False) as conn:
            if command_id:
                rows = conn.execute(
                    """
                    SELECT * FROM life_delivery_outbox
                    WHERE command_id = ? ORDER BY outbox_id ASC
                    """,
                    (str(command_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM life_delivery_outbox ORDER BY outbox_id ASC"
                ).fetchall()
            return [dict(row) for row in rows]

    # -- Internal SQL helpers ----------------------------------------

    def _snapshot_from_connection(
        self,
        conn: sqlite3.Connection,
        person_id: str,
    ) -> LifeSnapshot:
        person = _required_text(person_id, "person_id")
        state_row = conn.execute(
            """
            SELECT version, state_json, updated_at_ms FROM digital_life_state
            WHERE person_id = ?
            """,
            (person,),
        ).fetchone()
        event_row = conn.execute(
            """
            SELECT event_id, sequence, event_digest FROM life_event_journal
            WHERE person_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (person,),
        ).fetchone()
        if state_row is None:
            if event_row is not None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "journal exists without a state projection",
                )
            return LifeSnapshot.empty(person)
        state = _json_object(state_row["state_json"], "digital_life_state.state_json")
        version = int(state_row["version"])
        if int(state.get("version") or -1) != version:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_DURABLE_DATA_INVALID,
                "state projection version drift",
            )
        if event_row is not None and int(event_row["sequence"]) != version:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_DURABLE_DATA_INVALID,
                "state projection and event journal head differ",
            )
        return LifeSnapshot(
            person_id=person,
            version=version,
            state=state,
            state_digest=digest_json(state),
            last_event_id=str(event_row["event_id"]) if event_row is not None else None,
            last_event_digest=(
                str(event_row["event_digest"]) if event_row is not None else None
            ),
            updated_at_ms=int(state_row["updated_at_ms"]),
        )

    def _assert_live_claim(
        self,
        conn: sqlite3.Connection,
        claim: LifeCommandClaim,
        *,
        now_ms: int,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM life_command_claim WHERE command_id = ?",
            (claim.command_id,),
        ).fetchone()
        if (
            row is None
            or str(row["command_hash"]) != claim.command_hash
            or str(row["owner_token"]) != claim.owner_token
            or int(row["fencing_token"]) != claim.fencing_token
            or int(row["claim_expires_at_ms"]) < int(now_ms)
        ):
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_CLAIM_FENCED,
                "command claim is expired or fenced",
            )

    def _assert_live_aggregate_claim(
        self,
        conn: sqlite3.Connection,
        claim: LifeAggregateClaim,
        *,
        person_id: str,
        now_ms: int,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM life_aggregate_claim WHERE person_id = ?",
            (str(person_id),),
        ).fetchone()
        if (
            claim.person_id != str(person_id)
            or row is None
            or str(row["owner_token"]) != claim.owner_token
            or int(row["fencing_token"]) != claim.fencing_token
            or int(row["lease_expires_at_ms"]) <= int(now_ms)
        ):
            raise LifeCommandRepositoryError(
                LIFE_AGGREGATE_CLAIM_FENCED,
                "aggregate claim is expired or fenced",
            )

    def _insert_command(self, conn: sqlite3.Connection, command: LifeCommand) -> None:
        conn.execute(
            """
            INSERT INTO life_command(
                command_id, command_hash, person_id, command_type, payload_json,
                expected_version, correlation_id, causation_id, metadata_json,
                created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.command_id,
                command.command_hash,
                command.person_id,
                command.command_type,
                canonical_json(dict(command.payload)),
                command.expected_version,
                command.correlation_id,
                command.causation_id,
                canonical_json(dict(command.metadata)),
                command.created_at_ms,
            ),
        )

    def _insert_event(self, conn: sqlite3.Connection, event: LifeEvent) -> None:
        conn.execute(
            """
            INSERT INTO life_event_journal(
                event_id, command_id, person_id, sequence, event_type,
                event_payload_json, state_before_digest, state_after_digest,
                previous_event_digest, event_digest, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.command_id,
                event.person_id,
                event.sequence,
                event.event_type,
                canonical_json(dict(event.payload)),
                event.state_before_digest,
                event.state_after_digest,
                event.previous_event_digest,
                event.event_digest,
                event.created_at_ms,
            ),
        )

    def _write_projection(
        self,
        conn: sqlite3.Connection,
        snapshot: LifeSnapshot,
        *,
        expected_version: int,
    ) -> None:
        if expected_version == 0:
            conn.execute(
                """
                INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.person_id,
                    snapshot.version,
                    canonical_json(dict(snapshot.state)),
                    snapshot.updated_at_ms,
                ),
            )
            return
        cursor = conn.execute(
            """
            UPDATE digital_life_state
            SET version = ?, state_json = ?, updated_at_ms = ?
            WHERE person_id = ? AND version = ?
            """,
            (
                snapshot.version,
                canonical_json(dict(snapshot.state)),
                snapshot.updated_at_ms,
                snapshot.person_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_CLAIM_FENCED,
                "state projection CAS failed",
            )

    def _insert_outbox(
        self,
        conn: sqlite3.Connection,
        preview: LifeCommandPreview,
    ) -> int:
        for index, delivery in enumerate(preview.deliveries):
            payload = dict(delivery.payload)
            payload_digest = digest_json(payload)
            authorization = delivery.authorization.as_dict()
            authorization_digest = digest_json(authorization)
            outbox_identity = {
                "event_id": preview.event.event_id,
                "destination": delivery.destination,
                "index": index,
                "payload": payload,
                "authorization": authorization,
            }
            outbox_id = f"life_out_{digest_json(outbox_identity)[:32]}"
            conn.execute(
                """
                INSERT INTO life_delivery_outbox(
                    outbox_id, event_id, command_id, person_id, destination,
                    delivery_key, payload_json, payload_digest, status,
                    authorization_request_id, governor_grant_ref,
                    authority_kind, authority_principal_id, authorization_digest,
                    available_at_ms, attempt_count, fencing_token,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    outbox_id,
                    preview.event.event_id,
                    preview.command.command_id,
                    preview.command.person_id,
                    delivery.destination,
                    outbox_id,
                    canonical_json(payload),
                    payload_digest,
                    authorization["request_id"],
                    authorization["governor_grant_ref"],
                    authorization["authority_kind"],
                    authorization["authority_principal_id"],
                    authorization_digest,
                    preview.event.created_at_ms,
                    preview.event.created_at_ms,
                    preview.event.created_at_ms,
                ),
            )
        return len(preview.deliveries)

    def _insert_receipt(
        self,
        conn: sqlite3.Connection,
        receipt: LifeCommandReceipt,
    ) -> None:
        conn.execute(
            """
            INSERT INTO life_command_receipt(
                command_id, command_hash, person_id, status, reason_code,
                event_id, state_version, state_digest, outbox_count, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.command_id,
                receipt.command_hash,
                receipt.person_id,
                receipt.status.value,
                receipt.reason_code,
                receipt.event_id,
                receipt.state_version,
                receipt.state_digest,
                receipt.outbox_count,
                receipt.created_at_ms,
            ),
        )

    def _receipt_row(
        self,
        conn: sqlite3.Connection,
        command_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM life_command_receipt WHERE command_id = ?",
            (str(command_id),),
        ).fetchone()

    def _assert_command_hash(self, expected: Any, actual: Any) -> None:
        if str(expected) != str(actual):
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
                "command_id was reused with a different canonical payload",
            )

    def _assert_projection_message(
        self,
        conn: sqlite3.Connection,
        message: LifeProjectionMessage,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM life_projection_intent WHERE intent_id = ?",
            (message.intent_id,),
        ).fetchone()
        if row is None:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_DURABLE_DATA_INVALID,
                "projection intent is missing",
            )
        if (
            str(row["event_id"]) != message.event_id
            or str(row["command_id"]) != message.command_id
            or str(row["person_id"]) != message.person_id
            or str(row["intent_kind"]) != message.intent_kind.value
            or str(row["target"]) != message.target
            or str(row["payload_digest"]) != message.payload_digest
            or digest_json(dict(message.payload)) != str(row["payload_digest"])
        ):
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_DURABLE_DATA_INVALID,
                "projection message differs from durable intent identity",
            )

    def _finish_delivery(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        status: LifeDeliveryStatus,
        result: Mapping[str, Any],
        error: str | None,
        available_at_ms: int,
        now_ms: int,
    ) -> None:
        now_value = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM life_delivery_outbox WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if row is None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery outbox row is missing",
                )
            durable = _outbox_from_row(row)
            if (
                durable.event_id != message.event_id
                or durable.command_id != message.command_id
                or durable.person_id != message.person_id
                or durable.destination != message.destination
                or durable.delivery_key != message.delivery_key
                or durable.payload_digest != message.payload_digest
                or durable.authorization_digest != message.authorization_digest
                or durable.authorization != message.authorization
                or digest_json(dict(message.payload)) != message.payload_digest
                or digest_json(message.authorization.as_dict())
                != message.authorization_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery message differs from durable outbox identity",
                )
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET status = ?, result_json = ?, last_error = ?,
                    available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, updated_at_ms = ?,
                    delivered_at_ms = CASE WHEN ? = 'DELIVERED' THEN ? ELSE NULL END
                WHERE outbox_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                  AND COALESCE(claim_expires_at_ms, 0) > ?
                """,
                (
                    status.value,
                    canonical_json(dict(result)),
                    error,
                    int(available_at_ms),
                    now_value,
                    status.value,
                    now_value,
                    message.outbox_id,
                    _required_text(owner_token, "owner_token"),
                    message.fencing_token,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim is no longer owned by this dispatcher",
                )

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)


def _catalog_names(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?",
            (str(kind),),
        ).fetchall()
    }


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    trusted_name = str(table_name).replace('"', '""')
    return {
        str(row["name"])
        for row in conn.execute(
            f'PRAGMA table_info("{trusted_name}")'
        ).fetchall()
    }


def _receipt_from_row(row: Mapping[str, Any]) -> LifeCommandReceipt:
    return LifeCommandReceipt(
        command_id=str(row["command_id"]),
        command_hash=str(row["command_hash"]),
        person_id=str(row["person_id"]),
        status=LifeCommandStatus(str(row["status"])),
        reason_code=str(row["reason_code"]) if row["reason_code"] is not None else None,
        event_id=str(row["event_id"]) if row["event_id"] is not None else None,
        state_version=int(row["state_version"]),
        state_digest=str(row["state_digest"]),
        outbox_count=int(row["outbox_count"]),
        created_at_ms=int(row["created_at_ms"]),
    )


def _event_from_row(row: Mapping[str, Any]) -> LifeEvent:
    return LifeEvent(
        event_id=str(row["event_id"]),
        command_id=str(row["command_id"]),
        person_id=str(row["person_id"]),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        payload=_json_object(row["event_payload_json"], "event_payload_json"),
        state_before_digest=str(row["state_before_digest"]),
        state_after_digest=str(row["state_after_digest"]),
        previous_event_digest=(
            str(row["previous_event_digest"])
            if row["previous_event_digest"] is not None
            else None
        ),
        event_digest=str(row["event_digest"]),
        created_at_ms=int(row["created_at_ms"]),
    )


def _outbox_from_row(row: Mapping[str, Any]) -> LifeOutboxMessage:
    return LifeOutboxMessage(
        outbox_id=str(row["outbox_id"]),
        event_id=str(row["event_id"]),
        command_id=str(row["command_id"]),
        person_id=str(row["person_id"]),
        destination=str(row["destination"]),
        delivery_key=str(row["delivery_key"]),
        payload=_json_object(row["payload_json"], "payload_json"),
        payload_digest=str(row["payload_digest"]),
        authorization=LifeDeliveryAuthorization(
            request_id=str(row["authorization_request_id"]),
            governor_grant_ref=str(row["governor_grant_ref"]),
            authority_kind=str(row["authority_kind"]),
            authority_principal_id=str(row["authority_principal_id"]),
        ),
        authorization_digest=str(row["authorization_digest"]),
        status=LifeDeliveryStatus(str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        fencing_token=int(row["fencing_token"]),
    )


def _delivery_receipt_from_row(row: Mapping[str, Any]) -> LifeDeliveryReceipt:
    return LifeDeliveryReceipt(
        outbox_id=str(row["outbox_id"]),
        delivery_key=str(row["delivery_key"]),
        payload_digest=str(row["payload_digest"]),
        authorization_digest=str(row["authorization_digest"]),
        result=_json_object(row["result_json"], "result_json"),
        completed_at_ms=int(row["completed_at_ms"]),
    )


def _assert_delivery_receipt_matches(
    message: LifeOutboxMessage,
    receipt: LifeDeliveryReceipt,
) -> None:
    if (
        receipt.delivery_key != message.delivery_key
        or receipt.payload_digest != message.payload_digest
        or receipt.authorization_digest != message.authorization_digest
    ):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "delivery receipt payload or authorization digest drift",
        )


def _projection_receipt_from_row(row: Mapping[str, Any]) -> LifeProjectionReceipt:
    return LifeProjectionReceipt(
        intent_id=str(row["intent_id"]),
        payload_digest=str(row["payload_digest"]),
        result=_json_object(row["result_json"], "result_json"),
        completed_at_ms=int(row["completed_at_ms"]),
    )


def _projection_intent_from_row(row: Mapping[str, Any]) -> LifeProjectionIntent:
    payload = _json_object(row["payload_json"], "payload_json")
    payload_digest = str(row["payload_digest"])
    if digest_json(payload) != payload_digest:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "projection intent payload digest drift",
        )
    try:
        intent_kind = LifeIntentKind(str(row["intent_kind"]))
    except ValueError as exc:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "projection intent kind is invalid",
        ) from exc
    return LifeProjectionIntent(
        intent_id=str(row["intent_id"]),
        event_id=str(row["event_id"]),
        command_id=str(row["command_id"]),
        person_id=str(row["person_id"]),
        intent_kind=intent_kind,
        target=str(row["target"]),
        payload=payload,
        payload_digest=payload_digest,
        created_at_ms=int(row["created_at_ms"]),
    )


def _sqlite_sha256_hex(value: Any) -> str:
    if value is None:
        raise ValueError("natsume_sha256_hex requires non-null input")
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} must contain a JSON object",
        )
    return decoded


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_INVALID,
            f"{field_name} is required",
        )
    return text


__all__ = [
    "LIFE_AGGREGATE_CLAIM_BUSY",
    "LIFE_AGGREGATE_CLAIM_FENCED",
    "LIFE_COMMAND_BASELINE_REQUIRED",
    "LIFE_COMMAND_CLAIM_BUSY",
    "LIFE_COMMAND_CLAIM_FENCED",
    "LIFE_COMMAND_DURABLE_DATA_INVALID",
    "LIFE_COMMAND_IDEMPOTENCY_CONFLICT",
    "LIFE_COMMAND_INVALID",
    "LIFE_COMMAND_MIGRATION_ASSET_KEY",
    "LIFE_COMMAND_MIGRATION_SHA256",
    "LIFE_COMMAND_SCHEMA_NOT_READY",
    "LIFE_DELIVERY_CLAIM_FENCED",
    "LIFE_PROJECTION_CLAIM_FENCED",
    "LifeCommandRepository",
    "LifeCommandRepositoryError",
]
