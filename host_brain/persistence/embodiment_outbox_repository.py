"""Canonical Host durable outbox and authority leases for embodiment commands.

The database is the authority boundary.  A repository instance is scoped to one
immutable ``runtime_scope_id``: it never claims or settles rows from another
runtime, and historical rows created before scope fencing remain intentionally
unclaimable.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4


class EmbodimentOutboxError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbodimentRuntimeAuthority:
    """The exact Host lease allowed to mutate one embodiment runtime scope.

    This is deliberately a separate value from the short-lived dispatcher
    claim.  Every outbox transition verifies it inside the same SQLite
    ``BEGIN IMMEDIATE`` transaction as the transition itself, so a stale Host
    cannot pass a precheck and write after a replacement acquired the scope.
    """

    worker_id: str
    lease_id: str
    fencing_token: int


class EmbodimentOutboxRepository:
    """Durable embodiment outbox with scope- and fencing-bound authority.

    ``runtime_scope_id`` defaults to a stable digest of the canonical SQLite
    path.  It can be supplied explicitly for an intentional logical scope, but
    no method ever falls back to an unscoped row.  This is deliberately stricter
    than the pre-0104 schema: old rows stay visible for forensic SQL only until
    a dedicated migration/reconciliation policy is approved.
    """

    def __init__(
        self,
        canonical_db_path: str | Path,
        *,
        runtime_scope_id: str | None = None,
        timeout_ms: int = 5_000,
    ) -> None:
        raw = str(canonical_db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_DURABLE_PATH_REQUIRED")
        self._db_path = Path(raw).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        self._runtime_scope_id = _normalise_identifier(
            runtime_scope_id,
            field="runtime_scope_id",
            default=_default_runtime_scope_id(self._db_path),
        )

    @property
    def canonical_db_path(self) -> Path:
        return self._db_path

    @property
    def runtime_scope_id(self) -> str:
        return self._runtime_scope_id

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self._db_path), timeout=self._timeout_ms / 1000.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Outbox records.  Every query includes runtime_scope_id.  Do not turn
    # this into a nullable/COALESCE predicate: legacy rows must fail closed.
    # ------------------------------------------------------------------
    def enqueue(
        self,
        command: Mapping[str, Any],
        *,
        authority: EmbodimentRuntimeAuthority,
    ) -> dict[str, Any]:
        return self.enqueue_many((command,), authority=authority)[0]

    def enqueue_many(
        self,
        commands: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
        *,
        authority: EmbodimentRuntimeAuthority,
    ) -> tuple[dict[str, Any], ...]:
        """Atomically admit one bounded ExpressionPlan command batch.

        Each command keeps its own idempotency identity and terminal physical
        receipt.  The transaction only prevents a Host crash from leaving half
        of one already-approved local expression plan in the outbox.
        """

        values = tuple(commands)
        if not values or len(values) > 66 or any(
            not isinstance(command, Mapping) for command in values
        ):
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_BATCH_INVALID")
        prepared: list[tuple[Mapping[str, Any], str, str, str]] = []
        for command in values:
            payload = _canonical_json(command)
            command_id = _normalise_identifier(
                command.get("command_id"), field="command_id"
            )
            idempotency_key = _normalise_identifier(
                command.get("idempotency_key"), field="idempotency_key"
            )
            prepared.append((command, payload, _digest(payload), idempotency_key))
        command_ids = [
            _normalise_identifier(item[0].get("command_id"), field="command_id")
            for item in prepared
        ]
        idempotency_keys = [item[3] for item in prepared]
        if len(set(command_ids)) != len(command_ids) or len(set(idempotency_keys)) != len(
            idempotency_keys
        ):
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_BATCH_IDENTITY_CONFLICT")

        now = _now_ms()
        output: list[dict[str, Any]] = []
        with self._transaction() as connection:
            self._require_runtime_authority(connection, authority=authority, now=now)
            for command, payload, digest, idempotency_key in prepared:
                command_id = _normalise_identifier(
                    command.get("command_id"), field="command_id"
                )
                key_row = connection.execute(
                    "SELECT * FROM host_embodiment_outbox WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                command_row = connection.execute(
                    "SELECT * FROM host_embodiment_outbox WHERE command_id = ?",
                    (command_id,),
                ).fetchone()
                existing = key_row or command_row
                if existing is not None:
                    if str(existing["runtime_scope_id"] or "") != self._runtime_scope_id:
                        raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_SCOPE_CONFLICT")
                    if (
                        str(existing["command_id"]) != command_id
                        or str(existing["idempotency_key"]) != idempotency_key
                        or str(existing["command_digest"]) != digest
                    ):
                        raise EmbodimentOutboxError(
                            "EMBODIMENT_OUTBOX_IDEMPOTENCY_CONFLICT"
                        )
                    output.append(dict(existing))
                    continue
                outbox_id = f"embodiment_outbox_{uuid4().hex}"
                connection.execute(
                    "INSERT INTO host_embodiment_outbox "
                    "(outbox_id, runtime_scope_id, command_id, idempotency_key, command_digest, "
                    "command_json, status, created_at_ms, updated_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                    (
                        outbox_id,
                        self._runtime_scope_id,
                        command_id,
                        idempotency_key,
                        digest,
                        payload,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM host_embodiment_outbox "
                    "WHERE outbox_id = ? AND runtime_scope_id = ?",
                    (outbox_id, self._runtime_scope_id),
                ).fetchone()
                assert row is not None
                output.append(dict(row))
        return tuple(output)

    def claim_next(
        self,
        *,
        worker_id: str,
        authority: EmbodimentRuntimeAuthority,
        lease_ms: int = 30_000,
    ) -> dict[str, Any] | None:
        worker = _normalise_identifier(worker_id, field="worker_id")
        now = _now_ms()
        token = f"{worker}:{uuid4().hex}"
        with self._transaction() as connection:
            self._require_runtime_authority(connection, authority=authority, now=now)
            self._expire_dispatching_claims(connection, now=now)
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox "
                "WHERE runtime_scope_id = ? AND status = 'PENDING' "
                "ORDER BY created_at_ms, "
                "CASE json_extract(command_json, '$.kind') "
                "WHEN 'voice' THEN 0 WHEN 'avatar' THEN 1 ELSE 2 END, "
                "outbox_id LIMIT 1",
                (self._runtime_scope_id,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                "UPDATE host_embodiment_outbox SET status = 'DISPATCHING', claim_token = ?, "
                "claim_expires_at_ms = ?, attempt_count = attempt_count + 1, updated_at_ms = ? "
                "WHERE outbox_id = ? AND runtime_scope_id = ? AND status = 'PENDING'",
                (
                    token,
                    now + max(1_000, int(lease_ms)),
                    now,
                    str(row["outbox_id"]),
                    self._runtime_scope_id,
                ),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM host_embodiment_outbox "
                "WHERE outbox_id = ? AND runtime_scope_id = ?",
                (str(row["outbox_id"]), self._runtime_scope_id),
            ).fetchone()
        assert claimed is not None
        result = dict(claimed)
        result["command"] = json.loads(str(result["command_json"]))
        return result

    def expire_dispatching_claims(
        self,
        *,
        authority: EmbodimentRuntimeAuthority,
    ) -> int:
        """Move expired *this-scope* Host claims to durable uncertainty."""

        now = _now_ms()
        with self._transaction() as connection:
            self._require_runtime_authority(connection, authority=authority, now=now)
            return self._expire_dispatching_claims(connection, now=now)

    def _expire_dispatching_claims(self, connection: sqlite3.Connection, *, now: int) -> int:
        updated = connection.execute(
            "UPDATE host_embodiment_outbox SET status = 'IN_DOUBT', "
            "claim_token = NULL, claim_expires_at_ms = NULL, "
            "reason_code = 'EMBODIMENT_DISPATCH_OUTCOME_UNKNOWN', updated_at_ms = ?, "
            "completed_at_ms = ? WHERE runtime_scope_id = ? AND status = 'DISPATCHING' "
            "AND claim_expires_at_ms IS NOT NULL AND claim_expires_at_ms <= ?",
            (now, now, self._runtime_scope_id, now),
        )
        return max(0, int(updated.rowcount or 0))

    def complete(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        receipt: Mapping[str, Any],
        authority: EmbodimentRuntimeAuthority,
        expected_command_id: str | None = None,
        expected_idempotency_key: str | None = None,
    ) -> None:
        """Settle exactly the row claimed by this scope and exact receipt identity."""

        resolved_outbox_id = _normalise_identifier(outbox_id, field="outbox_id")
        resolved_claim = _normalise_identifier(claim_token, field="claim_token")
        receipt_command_id = _normalise_identifier(
            receipt.get("command_id"), field="receipt.command_id"
        )
        receipt_key = _normalise_identifier(
            receipt.get("idempotency_key"), field="receipt.idempotency_key"
        )
        expected_command = _normalise_identifier(
            expected_command_id,
            field="expected_command_id",
            default=receipt_command_id,
        )
        expected_key = _normalise_identifier(
            expected_idempotency_key,
            field="expected_idempotency_key",
            default=receipt_key,
        )
        if expected_command != receipt_command_id or expected_key != receipt_key:
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_RECEIPT_IDENTITY_MISMATCH")
        payload = _canonical_json(receipt)
        digest = _digest(payload)
        status = _receipt_status(receipt)
        now = _now_ms()
        with self._transaction() as connection:
            self._require_runtime_authority(connection, authority=authority, now=now)
            row = connection.execute(
                "SELECT command_id, idempotency_key FROM host_embodiment_outbox "
                "WHERE outbox_id = ? AND runtime_scope_id = ?",
                (resolved_outbox_id, self._runtime_scope_id),
            ).fetchone()
            if row is None:
                raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_CLAIM_LOST")
            if (
                str(row["command_id"]) != expected_command
                or str(row["idempotency_key"]) != expected_key
            ):
                raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_RECEIPT_IDENTITY_MISMATCH")
            updated = connection.execute(
                "UPDATE host_embodiment_outbox SET status = ?, receipt_json = ?, "
                "receipt_digest = ?, claim_token = NULL, claim_expires_at_ms = NULL, "
                "reason_code = ?, updated_at_ms = ?, completed_at_ms = ? "
                "WHERE outbox_id = ? AND runtime_scope_id = ? "
                "AND command_id = ? AND idempotency_key = ? "
                "AND status = 'DISPATCHING' AND claim_token = ?",
                (
                    status,
                    payload,
                    digest,
                    receipt.get("reason_code"),
                    now,
                    now,
                    resolved_outbox_id,
                    self._runtime_scope_id,
                    expected_command,
                    expected_key,
                    resolved_claim,
                ),
            )
            if updated.rowcount != 1:
                raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_CLAIM_LOST")

    def list_in_doubt(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return only this scope's durable uncertainty records."""

        maximum = max(0, int(limit))
        if maximum == 0:
            return []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM host_embodiment_outbox "
                "WHERE runtime_scope_id = ? AND status = 'IN_DOUBT' "
                "ORDER BY created_at_ms, outbox_id LIMIT ?",
                (self._runtime_scope_id, maximum),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["command"] = json.loads(str(record["command_json"]))
            records.append(record)
        return records

    def reconcile_in_doubt(
        self,
        *,
        outbox_id: str,
        command_id: str,
        idempotency_key: str,
        expected_receipt_digest: str | None,
        receipt: Mapping[str, Any],
        authority: EmbodimentRuntimeAuthority,
    ) -> bool:
        """CAS one exact Edge receipt onto one scoped ``IN_DOUBT`` record.

        Re-observing the identical ``IN_DOUBT`` receipt is a no-op.  In
        particular, it must not advance ``completed_at_ms`` or cause callers
        to emit the same receipt observer event twice.
        """

        resolved_outbox_id = _normalise_identifier(outbox_id, field="outbox_id")
        resolved_command_id = _normalise_identifier(command_id, field="command_id")
        resolved_key = _normalise_identifier(idempotency_key, field="idempotency_key")
        receipt_command_id = _normalise_identifier(
            receipt.get("command_id"), field="receipt.command_id"
        )
        receipt_key = _normalise_identifier(
            receipt.get("idempotency_key"), field="receipt.idempotency_key"
        )
        if receipt_command_id != resolved_command_id or receipt_key != resolved_key:
            return False
        payload = _canonical_json(receipt)
        digest = _digest(payload)
        status = _receipt_status(receipt)
        expected_digest = str(expected_receipt_digest or "").strip() or None
        now = _now_ms()
        with self._transaction() as connection:
            self._require_runtime_authority(connection, authority=authority, now=now)
            row = connection.execute(
                "SELECT receipt_digest FROM host_embodiment_outbox "
                "WHERE outbox_id = ? AND runtime_scope_id = ? "
                "AND command_id = ? AND idempotency_key = ? AND status = 'IN_DOUBT'",
                (
                    resolved_outbox_id,
                    self._runtime_scope_id,
                    resolved_command_id,
                    resolved_key,
                ),
            ).fetchone()
            if row is None:
                return False
            stored_digest = str(row["receipt_digest"] or "").strip() or None
            if stored_digest != expected_digest:
                return False
            if stored_digest == digest:
                return False
            updated = connection.execute(
                "UPDATE host_embodiment_outbox SET status = ?, receipt_json = ?, "
                "receipt_digest = ?, claim_token = NULL, claim_expires_at_ms = NULL, "
                "reason_code = ?, updated_at_ms = ?, completed_at_ms = ? "
                "WHERE outbox_id = ? AND runtime_scope_id = ? "
                "AND command_id = ? AND idempotency_key = ? AND status = 'IN_DOUBT' "
                "AND ((receipt_digest IS NULL AND ? IS NULL) OR receipt_digest = ?)",
                (
                    status,
                    payload,
                    digest,
                    receipt.get("reason_code"),
                    now,
                    now,
                    resolved_outbox_id,
                    self._runtime_scope_id,
                    resolved_command_id,
                    resolved_key,
                    expected_digest,
                    expected_digest,
                ),
            )
        return updated.rowcount == 1

    def get(self, outbox_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox "
                "WHERE outbox_id = ? AND runtime_scope_id = ?",
                (str(outbox_id), self._runtime_scope_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_command_identity(
        self,
        *,
        command_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Reopen one exact scoped command without mutating or redispatching it."""

        resolved_command_id = _normalise_identifier(
            command_id,
            field="command_id",
        )
        resolved_key = _normalise_identifier(
            idempotency_key,
            field="idempotency_key",
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox "
                "WHERE runtime_scope_id = ? AND command_id = ? "
                "AND idempotency_key = ?",
                (
                    self._runtime_scope_id,
                    resolved_command_id,
                    resolved_key,
                ),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["command"] = json.loads(str(record["command_json"]))
        if record.get("receipt_json"):
            record["receipt"] = json.loads(str(record["receipt_json"]))
        else:
            record["receipt"] = None
        return record

    # ------------------------------------------------------------------
    # Runtime authority lease.  Same owner is not special: an active claim
    # must be renewed with its exact lease/fence, never reacquired by name.
    # ------------------------------------------------------------------
    def acquire_runtime_lease(
        self,
        *,
        worker_id: str,
        lease_ms: int = 30_000,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        owner = _normalise_identifier(worker_id, field="worker_id")
        now = _now_ms(now_ms)
        expires = now + _lease_duration(lease_ms)
        new_lease_id = f"host_embodiment_runtime_lease_{uuid4().hex}"
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_runtime_lease WHERE runtime_scope_id = ?",
                (self._runtime_scope_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO host_embodiment_runtime_lease "
                    "(runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, "
                    "expires_at_ms, released_at_ms) VALUES (?, ?, ?, 1, ?, ?, NULL)",
                    (self._runtime_scope_id, owner, new_lease_id, now, expires),
                )
            else:
                active = row["released_at_ms"] is None and int(row["expires_at_ms"]) > now
                if active:
                    return None
                prior_fence = int(row["fencing_token"])
                updated = connection.execute(
                    "UPDATE host_embodiment_runtime_lease SET owner_id = ?, lease_id = ?, "
                    "fencing_token = ?, issued_at_ms = ?, expires_at_ms = ?, released_at_ms = NULL "
                    "WHERE runtime_scope_id = ? AND fencing_token = ? "
                    "AND (released_at_ms IS NOT NULL OR expires_at_ms <= ?)",
                    (
                        owner,
                        new_lease_id,
                        prior_fence + 1,
                        now,
                        expires,
                        self._runtime_scope_id,
                        prior_fence,
                        now,
                    ),
                )
                if updated.rowcount != 1:
                    return None
            acquired = connection.execute(
                "SELECT * FROM host_embodiment_runtime_lease WHERE runtime_scope_id = ?",
                (self._runtime_scope_id,),
            ).fetchone()
        assert acquired is not None
        return dict(acquired)

    def renew_runtime_lease(
        self,
        *,
        worker_id: str,
        lease_id: str,
        fencing_token: int,
        lease_ms: int = 30_000,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        owner = _normalise_identifier(worker_id, field="worker_id")
        resolved_lease_id = _normalise_identifier(lease_id, field="lease_id")
        fence = _fencing_token(fencing_token)
        now = _now_ms(now_ms)
        requested_expiry = now + _lease_duration(lease_ms)
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE host_embodiment_runtime_lease SET expires_at_ms = "
                "CASE WHEN expires_at_ms > ? THEN expires_at_ms ELSE ? END "
                "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
                "AND fencing_token = ? AND released_at_ms IS NULL AND expires_at_ms > ?",
                (
                    requested_expiry,
                    requested_expiry,
                    self._runtime_scope_id,
                    owner,
                    resolved_lease_id,
                    fence,
                    now,
                ),
            )
            if updated.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM host_embodiment_runtime_lease WHERE runtime_scope_id = ?",
                (self._runtime_scope_id,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def validate_runtime_lease(
        self,
        *,
        worker_id: str,
        lease_id: str,
        fencing_token: int,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the exact live Host claim without extending its expiry."""

        owner = _normalise_identifier(worker_id, field="worker_id")
        resolved_lease_id = _normalise_identifier(lease_id, field="lease_id")
        fence = _fencing_token(fencing_token)
        now = _now_ms(now_ms)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_runtime_lease "
                "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
                "AND fencing_token = ? AND released_at_ms IS NULL "
                "AND expires_at_ms > ?",
                (
                    self._runtime_scope_id,
                    owner,
                    resolved_lease_id,
                    fence,
                    now,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def release_runtime_lease(
        self,
        *,
        worker_id: str,
        lease_id: str,
        fencing_token: int,
        now_ms: int | None = None,
    ) -> bool:
        owner = _normalise_identifier(worker_id, field="worker_id")
        resolved_lease_id = _normalise_identifier(lease_id, field="lease_id")
        fence = _fencing_token(fencing_token)
        now = _now_ms(now_ms)
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE host_embodiment_runtime_lease SET released_at_ms = ?, expires_at_ms = ? "
                "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
                "AND fencing_token = ? AND released_at_ms IS NULL AND expires_at_ms > ?",
                (
                    now,
                    now,
                    self._runtime_scope_id,
                    owner,
                    resolved_lease_id,
                    fence,
                    now,
                ),
            )
        return updated.rowcount == 1

    # ------------------------------------------------------------------
    # Edge session lease cache.  A live Host runtime claim fences every write
    # or release.  Reads are exact and may be used after a crash to perform a
    # reconnect with the previously issued Edge session/lease tuple.
    # ------------------------------------------------------------------
    def load_edge_session_lease(
        self,
        session_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None:
        session = _normalise_identifier(session_id, field="session_id")
        owner = _normalise_identifier(owner_id, field="owner_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_edge_session_lease "
                "WHERE runtime_scope_id = ? AND session_id = ? AND owner_id = ? "
                "AND released_at_ms IS NULL",
                (self._runtime_scope_id, session, owner),
            ).fetchone()
        return dict(row) if row is not None else None

    def persist_edge_session_lease(
        self,
        *,
        session_id: str,
        owner_id: str,
        lease_id: str,
        fencing_token: int,
        issued_at_ms: int,
        expires_at_ms: int,
        expected_runtime_lease_id: str,
        expected_runtime_fencing_token: int,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        session = _normalise_identifier(session_id, field="session_id")
        owner = _normalise_identifier(owner_id, field="owner_id")
        edge_lease_id = _normalise_identifier(lease_id, field="edge_lease_id")
        edge_fence = _fencing_token(fencing_token)
        issued = _timestamp(issued_at_ms, field="issued_at_ms")
        expires = _timestamp(expires_at_ms, field="expires_at_ms")
        if expires <= issued:
            raise EmbodimentOutboxError("EMBODIMENT_EDGE_LEASE_EXPIRY_INVALID")
        runtime_lease_id = _normalise_identifier(
            expected_runtime_lease_id, field="expected_runtime_lease_id"
        )
        runtime_fence = _fencing_token(expected_runtime_fencing_token)
        now = _now_ms(now_ms)
        with self._transaction() as connection:
            if not self._runtime_claim_is_active(
                connection,
                lease_id=runtime_lease_id,
                fencing_token=runtime_fence,
                now=now,
            ):
                return None
            existing = connection.execute(
                "SELECT * FROM host_embodiment_edge_session_lease WHERE runtime_scope_id = ?",
                (self._runtime_scope_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO host_embodiment_edge_session_lease "
                    "(runtime_scope_id, session_id, owner_id, lease_id, fencing_token, "
                    "issued_at_ms, expires_at_ms, runtime_lease_id, runtime_fencing_token, "
                    "updated_at_ms, released_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        self._runtime_scope_id,
                        session,
                        owner,
                        edge_lease_id,
                        edge_fence,
                        issued,
                        expires,
                        runtime_lease_id,
                        runtime_fence,
                        now,
                    ),
                )
            else:
                active = (
                    existing["released_at_ms"] is None
                    and int(existing["expires_at_ms"]) > now
                )
                if active:
                    same_authority = (
                        str(existing["session_id"]) == session
                        and str(existing["owner_id"]) == owner
                    )
                    existing_fence = int(existing["fencing_token"])
                    same_lease = str(existing["lease_id"]) == edge_lease_id
                    if not same_authority or edge_fence < existing_fence:
                        return None
                    if edge_fence == existing_fence and not same_lease:
                        return None
                updated = connection.execute(
                    "UPDATE host_embodiment_edge_session_lease SET session_id = ?, owner_id = ?, "
                    "lease_id = ?, fencing_token = ?, issued_at_ms = ?, expires_at_ms = ?, "
                    "runtime_lease_id = ?, runtime_fencing_token = ?, updated_at_ms = ?, "
                    "released_at_ms = NULL WHERE runtime_scope_id = ?",
                    (
                        session,
                        owner,
                        edge_lease_id,
                        edge_fence,
                        issued,
                        expires,
                        runtime_lease_id,
                        runtime_fence,
                        now,
                        self._runtime_scope_id,
                    ),
                )
                if updated.rowcount != 1:
                    return None
            stored = connection.execute(
                "SELECT * FROM host_embodiment_edge_session_lease WHERE runtime_scope_id = ?",
                (self._runtime_scope_id,),
            ).fetchone()
        assert stored is not None
        return dict(stored)

    def release_edge_session_lease(
        self,
        *,
        session_id: str,
        owner_id: str,
        lease_id: str,
        fencing_token: int,
        expected_runtime_lease_id: str,
        expected_runtime_fencing_token: int,
        now_ms: int | None = None,
    ) -> bool:
        session = _normalise_identifier(session_id, field="session_id")
        owner = _normalise_identifier(owner_id, field="owner_id")
        edge_lease_id = _normalise_identifier(lease_id, field="edge_lease_id")
        edge_fence = _fencing_token(fencing_token)
        runtime_lease_id = _normalise_identifier(
            expected_runtime_lease_id, field="expected_runtime_lease_id"
        )
        runtime_fence = _fencing_token(expected_runtime_fencing_token)
        now = _now_ms(now_ms)
        with self._transaction() as connection:
            if not self._runtime_claim_is_active(
                connection,
                lease_id=runtime_lease_id,
                fencing_token=runtime_fence,
                now=now,
            ):
                return False
            updated = connection.execute(
                "UPDATE host_embodiment_edge_session_lease SET released_at_ms = ?, "
                "updated_at_ms = ? WHERE runtime_scope_id = ? AND session_id = ? "
                "AND owner_id = ? AND lease_id = ? AND fencing_token = ? "
                "AND released_at_ms IS NULL AND runtime_lease_id = ? "
                "AND runtime_fencing_token = ?",
                (
                    now,
                    now,
                    self._runtime_scope_id,
                    session,
                    owner,
                    edge_lease_id,
                    edge_fence,
                    runtime_lease_id,
                    runtime_fence,
                ),
            )
        return updated.rowcount == 1

    def _runtime_claim_is_active(
        self,
        connection: sqlite3.Connection,
        *,
        lease_id: str,
        fencing_token: int,
        now: int,
    ) -> bool:
        row = connection.execute(
            "SELECT 1 FROM host_embodiment_runtime_lease "
            "WHERE runtime_scope_id = ? AND lease_id = ? AND fencing_token = ? "
            "AND released_at_ms IS NULL AND expires_at_ms > ?",
            (self._runtime_scope_id, lease_id, fencing_token, now),
        ).fetchone()
        return row is not None

    def _require_runtime_authority(
        self,
        connection: sqlite3.Connection,
        *,
        authority: EmbodimentRuntimeAuthority,
        now: int,
    ) -> None:
        if not isinstance(authority, EmbodimentRuntimeAuthority):
            raise EmbodimentOutboxError("EMBODIMENT_RUNTIME_AUTHORITY_REQUIRED")
        worker = _normalise_identifier(authority.worker_id, field="authority.worker_id")
        lease_id = _normalise_identifier(authority.lease_id, field="authority.lease_id")
        fence = _fencing_token(authority.fencing_token)
        row = connection.execute(
            "SELECT 1 FROM host_embodiment_runtime_lease "
            "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
            "AND fencing_token = ? AND released_at_ms IS NULL AND expires_at_ms > ?",
            (self._runtime_scope_id, worker, lease_id, fence, now),
        ).fetchone()
        if row is None:
            raise EmbodimentOutboxError("EMBODIMENT_RUNTIME_AUTHORITY_LOST")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _receipt_status(receipt: Mapping[str, Any]) -> str:
    accepted = bool(receipt.get("accepted"))
    raw_status = str(receipt.get("status") or "").strip().upper()
    if accepted and raw_status in {"APPLIED", "COMPLETED", "SUCCEEDED"}:
        return "APPLIED"
    # Edge may durably accept a queued TTS/Avatar command before the physical
    # renderer has produced its terminal receipt.  Treating that acknowledgement
    # as a rejection loses the only safe read-only reconciliation path and makes
    # a successfully queued utterance look failed forever.  ``IN_DOUBT`` here
    # means "accepted but not terminal yet"; the dispatcher never resubmits it
    # and may only settle it through the exact Edge receipt query.
    if accepted and raw_status in {"ACCEPTED", "QUEUED", "PENDING"}:
        return "IN_DOUBT"
    if raw_status == "IN_DOUBT":
        return "IN_DOUBT"
    return "REJECTED"


def _normalise_identifier(value: Any, *, field: str, default: str | None = None) -> str:
    text = str(default if value is None else value).strip()
    if not text or len(text) > 512:
        raise EmbodimentOutboxError(f"EMBODIMENT_OUTBOX_{field.upper()}_INVALID")
    return text


def _default_runtime_scope_id(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return "host-embodiment-db:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _now_ms(value: int | None = None) -> int:
    return _timestamp(int(time.time() * 1000) if value is None else value, field="now_ms")


def _timestamp(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EmbodimentOutboxError(f"EMBODIMENT_OUTBOX_{field.upper()}_INVALID") from exc
    if result < 0:
        raise EmbodimentOutboxError(f"EMBODIMENT_OUTBOX_{field.upper()}_INVALID")
    return result


def _fencing_token(value: Any) -> int:
    result = _timestamp(value, field="fencing_token")
    if result < 1:
        raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_FENCING_TOKEN_INVALID")
    return result


def _lease_duration(value: Any) -> int:
    try:
        return max(1_000, int(value))
    except (TypeError, ValueError) as exc:
        raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_LEASE_MS_INVALID") from exc


__all__ = ["EmbodimentOutboxError", "EmbodimentOutboxRepository"]
