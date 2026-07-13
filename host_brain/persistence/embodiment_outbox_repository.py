"""Canonical Host durable outbox for physical embodiment commands."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4


class EmbodimentOutboxError(RuntimeError):
    pass


class EmbodimentOutboxRepository:
    def __init__(self, canonical_db_path: str | Path, *, timeout_ms: int = 5_000) -> None:
        raw = str(canonical_db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_DURABLE_PATH_REQUIRED")
        self._db_path = Path(raw).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))

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

    def enqueue(self, command: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps(
            dict(command), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        command_id = str(command.get("command_id") or "").strip()
        idempotency_key = str(command.get("idempotency_key") or "").strip()
        if not command_id or not idempotency_key:
            raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_COMMAND_INVALID")
        now = int(time.time() * 1000)
        outbox_id = f"embodiment_outbox_{uuid4().hex}"
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM host_embodiment_outbox WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if str(existing["command_digest"]) != digest:
                    raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_IDEMPOTENCY_CONFLICT")
                return dict(existing)
            connection.execute(
                "INSERT INTO host_embodiment_outbox "
                "(outbox_id, command_id, idempotency_key, command_digest, command_json, "
                "status, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (outbox_id, command_id, idempotency_key, digest, payload, now, now),
            )
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def claim_next(self, *, worker_id: str, lease_ms: int = 30_000) -> dict[str, Any] | None:
        now = int(time.time() * 1000)
        token = f"{worker_id}:{uuid4().hex}"
        with self._transaction() as connection:
            connection.execute(
                "UPDATE host_embodiment_outbox SET status = 'IN_DOUBT', "
                "reason_code = 'EMBODIMENT_DISPATCH_OUTCOME_UNKNOWN', updated_at_ms = ?, "
                "completed_at_ms = ? WHERE status = 'DISPATCHING' "
                "AND claim_expires_at_ms IS NOT NULL AND claim_expires_at_ms <= ?",
                (now, now, now),
            )
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox WHERE status = 'PENDING' "
                "ORDER BY created_at_ms, outbox_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                "UPDATE host_embodiment_outbox SET status = 'DISPATCHING', claim_token = ?, "
                "claim_expires_at_ms = ?, attempt_count = attempt_count + 1, updated_at_ms = ? "
                "WHERE outbox_id = ? AND status = 'PENDING'",
                (token, now + max(1_000, int(lease_ms)), now, str(row["outbox_id"])),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM host_embodiment_outbox WHERE outbox_id = ?",
                (str(row["outbox_id"]),),
            ).fetchone()
        assert claimed is not None
        result = dict(claimed)
        result["command"] = json.loads(str(result["command_json"]))
        return result

    def complete(self, *, outbox_id: str, claim_token: str, receipt: Mapping[str, Any]) -> None:
        payload = json.dumps(
            dict(receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        accepted = bool(receipt.get("accepted"))
        raw_status = str(receipt.get("status") or "").strip().upper()
        status = (
            "APPLIED"
            if accepted and raw_status in {"APPLIED", "COMPLETED", "SUCCEEDED"}
            else "IN_DOUBT"
            if raw_status == "IN_DOUBT"
            else "REJECTED"
        )
        now = int(time.time() * 1000)
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE host_embodiment_outbox SET status = ?, receipt_json = ?, "
                "receipt_digest = ?, reason_code = ?, updated_at_ms = ?, completed_at_ms = ? "
                "WHERE outbox_id = ? AND status = 'DISPATCHING' AND claim_token = ?",
                (
                    status,
                    payload,
                    digest,
                    receipt.get("reason_code"),
                    now,
                    now,
                    str(outbox_id),
                    str(claim_token),
                ),
            )
            if updated.rowcount != 1:
                raise EmbodimentOutboxError("EMBODIMENT_OUTBOX_CLAIM_LOST")

    def get(self, outbox_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_embodiment_outbox WHERE outbox_id = ?", (str(outbox_id),)
            ).fetchone()
        return dict(row) if row is not None else None


__all__ = ["EmbodimentOutboxError", "EmbodimentOutboxRepository"]
