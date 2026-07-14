"""Migration-first persistence for governed capability execution.

The repository is the only owner of SQLite mechanics for capability claims
and receipts.  Tables are provisioned by migration 0081; this module performs
only DML and fails closed when the canonical database is unavailable.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping


class CapabilityExecutionRepository:
    def __init__(self, canonical_db_path: str | Path, *, timeout_ms: int = 5_000) -> None:
        raw = str(canonical_db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise ValueError("capability execution requires a durable canonical database")
        self._db_path = Path(raw).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def lookup(self, idempotency_key: str) -> tuple[str, str] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT request_hash, receipt_json FROM capability_execution_receipt "
                "WHERE idempotency_key = ?",
                (str(idempotency_key),),
            ).fetchone()
        if row is None:
            return None
        return str(row["request_hash"]), str(row["receipt_json"])

    def claim(self, values: Mapping[str, Any]) -> str:
        with self._transaction() as connection:
            receipt = connection.execute(
                "SELECT request_hash FROM capability_execution_receipt WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if receipt is not None:
                return "REPLAY" if str(receipt["request_hash"]) == values["request_hash"] else "CONFLICT"
            claim = connection.execute(
                "SELECT request_hash, lease_expires_at_ms FROM capability_execution_claim "
                "WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if claim is not None:
                if str(claim["request_hash"]) != values["request_hash"]:
                    return "CONFLICT"
                return (
                    "IN_FLIGHT"
                    if int(claim["lease_expires_at_ms"]) > int(values["now_ms"])
                    else "ABANDONED"
                )
            connection.execute(
                "INSERT INTO capability_execution_claim "
                "(idempotency_key, request_hash, request_id, attempt_id, capability_id, "
                "kind, owner_token, state, lease_expires_at_ms, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?)",
                (
                    values["idempotency_key"],
                    values["request_hash"],
                    values["request_id"],
                    values["attempt_id"],
                    values["capability_id"],
                    values["kind"],
                    values["owner_token"],
                    int(values["lease_expires_at_ms"]),
                    int(values["now_ms"]),
                    int(values["now_ms"]),
                ),
            )
            return "ACQUIRED"

    def complete(self, values: Mapping[str, Any]) -> str:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT request_hash, receipt_json FROM capability_execution_receipt "
                "WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != values["request_hash"]:
                    raise RuntimeError("CAPABILITY_IDEMPOTENCY_CONFLICT")
                return str(existing["receipt_json"])
            claim = connection.execute(
                "SELECT request_hash, owner_token FROM capability_execution_claim "
                "WHERE idempotency_key = ?",
                (values["idempotency_key"],),
            ).fetchone()
            if (
                claim is None
                or str(claim["request_hash"]) != values["request_hash"]
                or str(claim["owner_token"]) != values["owner_token"]
            ):
                raise RuntimeError("CAPABILITY_CLAIM_OWNERSHIP_LOST")
            connection.execute(
                "INSERT INTO capability_execution_receipt "
                "(idempotency_key, request_hash, request_id, attempt_id, capability_id, kind, "
                "status, attempt_json, evidence_json, receipt_json, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    values["idempotency_key"],
                    values["request_hash"],
                    values["request_id"],
                    values["attempt_id"],
                    values["capability_id"],
                    values["kind"],
                    values["status"],
                    values["attempt_json"],
                    values["evidence_json"],
                    values["receipt_json"],
                    int(values["finished_at_ms"]),
                ),
            )
            connection.execute(
                "UPDATE capability_execution_claim SET state = ?, updated_at_ms = ? "
                "WHERE idempotency_key = ? AND owner_token = ?",
                (
                    values["claim_state"],
                    int(values["finished_at_ms"]),
                    values["idempotency_key"],
                    values["owner_token"],
                ),
            )
            return str(values["receipt_json"])

    def record_late_outcome(self, values: Mapping[str, Any]) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO capability_execution_late_outcome "
                "(idempotency_key, request_hash, owner_token, status, output_digest, "
                "error_type, observed_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    values["idempotency_key"],
                    values["request_hash"],
                    values["owner_token"],
                    values["status"],
                    values.get("output_digest"),
                    values.get("error_type"),
                    int(values["observed_at_ms"]),
                ),
            )


__all__ = ["CapabilityExecutionRepository"]
