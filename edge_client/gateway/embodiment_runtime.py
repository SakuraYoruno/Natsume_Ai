"""Edge-owned durable execution boundary for physical embodiment commands.

The journal intentionally never reclaims a command after dispatch starts.  A
process can crash after the device effect and before receipt commit; replaying
that command would duplicate the physical effect.  Reconnect therefore gets a
durable ``IN_DOUBT`` receipt and requires reconciliation instead of execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from edge_client.persistence.sqlite import EdgeSqliteDatabase

from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_CONFLICT,
    EMBODIMENT_COMMAND_EXPIRED,
    EMBODIMENT_COMMAND_IN_DOUBT,
    EMBODIMENT_FENCE_STALE,
    EMBODIMENT_LEASE_EXPIRED,
    EMBODIMENT_LEASE_INVALID,
    EMBODIMENT_LEASE_REQUIRED,
    EmbodimentCommand,
    EmbodimentReceipt,
)
from natsume_contracts.edge.transport_v1 import (
    EDGE_SESSION_BUSY,
    EDGE_SESSION_RECONNECT_INVALID,
    EdgeSessionLease,
    stable_lease_id,
)


EDGE_EMBODIMENT_TRANSPORT_SCHEMA_VERSION = 1
EDGE_EMBODIMENT_RECEIPT_CORRUPT = "EDGE_EMBODIMENT_RECEIPT_CORRUPT"
EDGE_EMBODIMENT_EXECUTOR_FAILED = "EDGE_EMBODIMENT_EXECUTOR_FAILED"


class EdgeEmbodimentTransportError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class CommandClaim:
    execute: bool
    receipt: EmbodimentReceipt | None


class SQLiteEdgeEmbodimentStore:
    """SQLite lease and command journal local to the Edge distribution."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] | None = None,
        timeout_ms: int = 5_000,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_DURABLE_PATH_REQUIRED")
        self.db_path = Path(raw_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._timeout_ms = max(1, int(timeout_ms))
        self._database = EdgeSqliteDatabase(self.db_path, timeout_ms=self._timeout_ms)
        self._verify_schema()

    def now_ms(self) -> int:
        value = int(self._clock())
        if value < 0:
            raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_CLOCK_INVALID")
        return value

    def _connect(self):  # noqa: ANN202
        return self._database.connection()

    def _transaction(self):  # noqa: ANN202
        return self._database.transaction()

    def _verify_schema(self) -> None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT schema_version FROM edge_transport_schema WHERE component = ?",
                    ("embodiment",),
                ).fetchone()
        except Exception as exc:
            raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_MIGRATION_REQUIRED") from exc
        if row is None:
            raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_MIGRATION_REQUIRED")
        if int(row["schema_version"]) != EDGE_EMBODIMENT_TRANSPORT_SCHEMA_VERSION:
            raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_SCHEMA_DRIFT")

    def open_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        ttl_ms: int = 30_000,
        reconnect_lease_id: str | None = None,
        reconnect_fencing_token: int | None = None,
    ) -> EdgeSessionLease:
        session = _text(session_id, EDGE_SESSION_RECONNECT_INVALID)
        owner = _text(owner_id, EDGE_SESSION_RECONNECT_INVALID)
        ttl = int(ttl_ms)
        if ttl < 1_000:
            raise EdgeEmbodimentTransportError(EDGE_SESSION_RECONNECT_INVALID)
        now = self.now_ms()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM edge_session_lease WHERE session_id = ?", (session,)
            ).fetchone()
            if row is not None and int(row["expires_at_ms"]) > now:
                exact_reconnect = (
                    reconnect_lease_id is not None
                    and reconnect_fencing_token is not None
                    and str(row["owner_id"]) == owner
                    and str(row["lease_id"]) == str(reconnect_lease_id)
                    and int(row["fencing_token"]) == int(reconnect_fencing_token)
                )
                if not exact_reconnect:
                    raise EdgeEmbodimentTransportError(EDGE_SESSION_BUSY)
                expires = now + ttl
                conn.execute(
                    "UPDATE edge_session_lease SET expires_at_ms = ?, updated_at_ms = ? "
                    "WHERE session_id = ? AND lease_id = ? AND fencing_token = ?",
                    (expires, now, session, str(row["lease_id"]), int(row["fencing_token"])),
                )
                return EdgeSessionLease(
                    session_id=session,
                    lease_id=str(row["lease_id"]),
                    owner_id=owner,
                    fencing_token=int(row["fencing_token"]),
                    issued_at_ms=int(row["issued_at_ms"]),
                    expires_at_ms=expires,
                )

            # A stale reconnect credential must not silently become a new lease.
            if reconnect_lease_id is not None or reconnect_fencing_token is not None:
                raise EdgeEmbodimentTransportError(EDGE_SESSION_RECONNECT_INVALID)
            fence = (int(row["fencing_token"]) + 1) if row is not None else 1
            lease_id = stable_lease_id(
                session_id=session, owner_id=owner, fencing_token=fence
            )
            expires = now + ttl
            conn.execute(
                """
                INSERT INTO edge_session_lease(
                    session_id, lease_id, owner_id, fencing_token,
                    issued_at_ms, expires_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    lease_id = excluded.lease_id,
                    owner_id = excluded.owner_id,
                    fencing_token = excluded.fencing_token,
                    issued_at_ms = excluded.issued_at_ms,
                    expires_at_ms = excluded.expires_at_ms,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (session, lease_id, owner, fence, now, expires, now),
            )
        return EdgeSessionLease(session, lease_id, owner, fence, now, expires)

    def claim_command(self, command: EmbodimentCommand) -> CommandClaim:
        now = self.now_ms()
        digest = _command_digest(command)
        with self._transaction() as conn:
            self._require_live_lease(conn, command=command, now=now)
            existing = conn.execute(
                "SELECT * FROM edge_embodiment_command "
                "WHERE command_id = ? OR idempotency_key = ?",
                (command.command_id, command.idempotency_key),
            ).fetchall()
            if existing:
                first_id = str(existing[0]["command_id"])
                if any(str(row["command_id"]) != first_id for row in existing[1:]):
                    raise EdgeEmbodimentTransportError(EMBODIMENT_COMMAND_CONFLICT)
                row = existing[0]
                if str(row["command_digest"]) != digest:
                    raise EdgeEmbodimentTransportError(EMBODIMENT_COMMAND_CONFLICT)
                receipt = self._receipt_from_row(row, now=now)
                return CommandClaim(False, receipt)

            if int(command.deadline_ms) <= now:
                receipt = _receipt(
                    command,
                    accepted=False,
                    status="REJECTED",
                    reason_code=EMBODIMENT_COMMAND_EXPIRED,
                    detail="embodiment command deadline elapsed before Edge execution",
                    received_at_ms=now,
                    completed_at_ms=now,
                )
                self._insert_terminal(conn, command, digest, receipt, now=now)
                return CommandClaim(False, receipt)

            conn.execute(
                """
                INSERT INTO edge_embodiment_command(
                    command_id, idempotency_key, command_digest, session_id,
                    lease_id, fencing_token, status, receipt_json,
                    receipt_sha256, created_at_ms, updated_at_ms, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'DISPATCHING', NULL, NULL, ?, ?, NULL)
                """,
                (
                    command.command_id, command.idempotency_key, digest,
                    command.session_id, command.lease_id, int(command.fencing_token), now, now,
                ),
            )
        return CommandClaim(True, None)

    def complete_command(
        self,
        command: EmbodimentCommand,
        receipt: EmbodimentReceipt,
    ) -> EmbodimentReceipt:
        now = self.now_ms()
        serialized, digest = _serialize_receipt(receipt)
        status = "APPLIED" if receipt.accepted else "REJECTED"
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM edge_embodiment_command WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if row is None or str(row["command_digest"]) != _command_digest(command):
                raise EdgeEmbodimentTransportError(EMBODIMENT_COMMAND_CONFLICT)
            if str(row["status"]) != "DISPATCHING":
                return self._receipt_from_row(row, now=now)
            conn.execute(
                "UPDATE edge_embodiment_command SET status = ?, receipt_json = ?, "
                "receipt_sha256 = ?, updated_at_ms = ?, completed_at_ms = ? "
                "WHERE command_id = ? AND status = 'DISPATCHING'",
                (status, serialized, digest, now, now, command.command_id),
            )
        return receipt

    def mark_in_doubt(self, command: EmbodimentCommand) -> EmbodimentReceipt:
        now = self.now_ms()
        receipt = _receipt(
            command,
            accepted=False,
            status="IN_DOUBT",
            reason_code=EMBODIMENT_COMMAND_IN_DOUBT,
            detail="device outcome is uncertain; command will not be replayed",
            received_at_ms=now,
            completed_at_ms=now,
        )
        serialized, digest = _serialize_receipt(receipt)
        with self._transaction() as conn:
            conn.execute(
                "UPDATE edge_embodiment_command SET status = 'IN_DOUBT', "
                "receipt_json = ?, receipt_sha256 = ?, updated_at_ms = ?, "
                "completed_at_ms = ? WHERE command_id = ? AND status = 'DISPATCHING'",
                (serialized, digest, now, now, command.command_id),
            )
        return receipt

    def get_receipt(self, *, command_id: str) -> EmbodimentReceipt | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM edge_embodiment_command WHERE command_id = ?",
                (str(command_id),),
            ).fetchone()
            return None if row is None else self._receipt_from_row(row, now=self.now_ms())
        finally:
            conn.close()

    def _require_live_lease(
        self,
        conn: sqlite3.Connection,
        *,
        command: EmbodimentCommand,
        now: int,
    ) -> None:
        if not command.session_id or not command.lease_id or int(command.fencing_token) < 1:
            raise EdgeEmbodimentTransportError(EMBODIMENT_LEASE_REQUIRED)
        row = conn.execute(
            "SELECT * FROM edge_session_lease WHERE session_id = ?",
            (command.session_id,),
        ).fetchone()
        if row is None:
            raise EdgeEmbodimentTransportError(EMBODIMENT_LEASE_INVALID)
        if int(row["expires_at_ms"]) <= now:
            raise EdgeEmbodimentTransportError(EMBODIMENT_LEASE_EXPIRED)
        if int(row["fencing_token"]) != int(command.fencing_token):
            raise EdgeEmbodimentTransportError(EMBODIMENT_FENCE_STALE)
        if str(row["lease_id"]) != command.lease_id:
            raise EdgeEmbodimentTransportError(EMBODIMENT_LEASE_INVALID)

    def _insert_terminal(
        self,
        conn: sqlite3.Connection,
        command: EmbodimentCommand,
        command_digest: str,
        receipt: EmbodimentReceipt,
        *,
        now: int,
    ) -> None:
        serialized, receipt_digest = _serialize_receipt(receipt)
        conn.execute(
            """
            INSERT INTO edge_embodiment_command(
                command_id, idempotency_key, command_digest, session_id,
                lease_id, fencing_token, status, receipt_json,
                receipt_sha256, created_at_ms, updated_at_ms, completed_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 'REJECTED', ?, ?, ?, ?, ?)
            """,
            (
                command.command_id, command.idempotency_key, command_digest,
                command.session_id, command.lease_id, int(command.fencing_token),
                serialized, receipt_digest, now, now, now,
            ),
        )

    def _receipt_from_row(self, row: sqlite3.Row, *, now: int) -> EmbodimentReceipt:
        raw = str(row["receipt_json"] or "")
        if raw:
            expected = str(row["receipt_sha256"] or "")
            actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if not expected or actual != expected:
                raise EdgeEmbodimentTransportError(EDGE_EMBODIMENT_RECEIPT_CORRUPT)
            return _parse_stored_receipt(raw)
        # DISPATCHING without a receipt means an effect may already have run.
        return EmbodimentReceipt(
            command_id=str(row["command_id"]),
            idempotency_key=str(row["idempotency_key"]),
            accepted=False,
            status="IN_DOUBT",
            reason_code=EMBODIMENT_COMMAND_IN_DOUBT,
            detail="command is already dispatching; replay denied",
            received_at_ms=int(row["created_at_ms"]),
            completed_at_ms=int(row["created_at_ms"]),
            event={"replay_denied": True},
            receipt_ref=_stable_receipt_ref(str(row["command_id"])),
        )


class DurableEmbodimentRuntime:
    """Claim, execute, and durably receipt an Edge physical effect exactly once."""

    def __init__(self, store: SQLiteEdgeEmbodimentStore) -> None:
        self.store = store

    def execute(
        self,
        command: EmbodimentCommand,
        executor: Callable[[EmbodimentCommand], Mapping[str, Any] | Any],
    ) -> EmbodimentReceipt:
        claim = self.store.claim_command(command)
        if not claim.execute:
            assert claim.receipt is not None
            return claim.receipt
        try:
            outcome = executor(command)
        except Exception:
            # The executor boundary cannot prove whether the physical side
            # effect happened before the error.  Persist uncertainty and deny
            # all reconnect/replay attempts.
            return self.store.mark_in_doubt(command)
        payload = _mapping(outcome)
        completed = self.store.now_ms()
        accepted = bool(payload.get("success", payload.get("accepted", False)))
        receipt = _receipt(
            command,
            accepted=accepted,
            status=str(payload.get("status") or ("COMPLETED" if accepted else "REJECTED")),
            reason_code=(str(payload.get("reason_code") or "").strip() or None),
            detail=str(payload.get("detail") or ("device effect completed" if accepted else "device effect rejected")),
            received_at_ms=completed,
            completed_at_ms=completed,
            event=payload,
        )
        return self.store.complete_command(command, receipt)


def _command_digest(command: EmbodimentCommand) -> str:
    encoded = json.dumps(
        command.as_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt(
    command: EmbodimentCommand,
    *,
    accepted: bool,
    status: str,
    reason_code: str | None,
    detail: str,
    received_at_ms: int,
    completed_at_ms: int,
    event: Mapping[str, Any] | None = None,
) -> EmbodimentReceipt:
    return EmbodimentReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        accepted=bool(accepted),
        status=str(status),
        reason_code=reason_code,
        detail=str(detail)[:500],
        received_at_ms=int(received_at_ms),
        completed_at_ms=int(completed_at_ms),
        event=dict(event or {}),
        receipt_ref=_stable_receipt_ref(command.command_id),
    )


def _serialize_receipt(receipt: EmbodimentReceipt) -> tuple[str, str]:
    try:
        serialized = json.dumps(
            receipt.as_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EdgeEmbodimentTransportError("EDGE_EMBODIMENT_RECEIPT_INVALID") from exc
    return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_stored_receipt(raw: str) -> EmbodimentReceipt:
    try:
        payload = json.loads(raw)
        return EmbodimentReceipt(
            command_id=_text(payload.get("command_id"), EDGE_EMBODIMENT_RECEIPT_CORRUPT),
            idempotency_key=_text(payload.get("idempotency_key"), EDGE_EMBODIMENT_RECEIPT_CORRUPT),
            accepted=bool(payload.get("accepted")),
            status=_text(payload.get("status"), EDGE_EMBODIMENT_RECEIPT_CORRUPT),
            reason_code=(str(payload.get("reason_code") or "").strip() or None),
            detail=str(payload.get("detail") or ""),
            received_at_ms=int(payload.get("received_at_ms") or 0),
            completed_at_ms=(int(payload["completed_at_ms"]) if payload.get("completed_at_ms") is not None else None),
            event=dict(payload.get("event") or {}),
            receipt_ref=(str(payload.get("receipt_ref") or "").strip() or None),
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EdgeEmbodimentTransportError(EDGE_EMBODIMENT_RECEIPT_CORRUPT) from exc


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        rendered = as_dict()
        if isinstance(rendered, Mapping):
            return dict(rendered)
    return {
        "success": bool(getattr(value, "success", False)),
        "status": str(getattr(value, "status", "REJECTED")),
        "reason_code": getattr(value, "reason_code", None),
        "detail": str(getattr(value, "detail", "device executor returned invalid outcome")),
        "event": dict(getattr(value, "event", {}) or {}),
    }


def _text(value: Any, reason_code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EdgeEmbodimentTransportError(reason_code)
    return text


def _stable_receipt_ref(command_id: str) -> str:
    digest = hashlib.sha256(str(command_id).encode("utf-8")).hexdigest()
    return f"edge-embodiment-receipt://{digest}"


__all__ = [
    "EDGE_EMBODIMENT_TRANSPORT_SCHEMA_VERSION", "EDGE_EMBODIMENT_RECEIPT_CORRUPT",
    "EDGE_EMBODIMENT_EXECUTOR_FAILED", "EdgeEmbodimentTransportError",
    "SQLiteEdgeEmbodimentStore", "DurableEmbodimentRuntime",
]
