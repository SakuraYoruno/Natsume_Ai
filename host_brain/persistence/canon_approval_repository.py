"""Operation-scoped persistence adapter for offline Canon approval tokens."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.persistence.canon_apply_repository import (
    CanonApplySqliteAuthority,
)


class CanonApprovalTokenRepository:
    """Own Canon approval-token connections, transactions, and SQL."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        schema_validator: Callable[[sqlite3.Connection], None] | None = None,
        authority: CanonApplySqliteAuthority | None = None,
    ) -> None:
        self._authority = authority or CanonApplySqliteAuthority(db_path)
        self.db_path = self._authority.db_path
        self._schema_validator = schema_validator

    def assert_ready(self) -> None:
        self._authority.assert_ready()
        if self._schema_validator is not None:
            with self._authority.read() as conn:
                self._schema_validator(conn)

    def insert_available_token(self, values: Mapping[str, Any]) -> None:
        ordered = (
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
            "authority_key_id",
            "approval_attestation_json",
            "approval_attestation_digest",
            "approval_signature",
            "approval_nonce",
            "issued_ts_ms",
            "expiry_ts_ms",
        )
        payload = tuple(values[name] for name in ordered)
        with self._authority.write() as conn:
            conn.execute(
                """
                INSERT INTO canon_apply_token (
                    token_digest, token_type, operation, subject_type, subject_id,
                    subject_version, subject_hash, target_doc_id, base_version,
                    actor_id, session_id, trace_id, evidence_digest,
                    authority_key_id, approval_attestation_json,
                    approval_attestation_digest, approval_signature, approval_nonce,
                    issued_ts_ms, expiry_ts_ms, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE')
                """,
                payload,
            )

    def get_token_metadata(self, token_digest: str) -> dict[str, Any] | None:
        with self._authority.read() as conn:
            row = conn.execute(
                "SELECT * FROM canon_apply_token WHERE token_digest = ?",
                (str(token_digest),),
            ).fetchone()
        return dict(row) if row is not None else None
