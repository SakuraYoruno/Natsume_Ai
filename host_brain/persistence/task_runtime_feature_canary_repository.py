"""SQLite owner for signed Task Runtime feature-canary receipts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.task_runtime.feature_canary import (
    FeatureCanarySignatureVerifier,
    TASK_RUNTIME_FEATURE_CANARY_EXPIRED,
    TASK_RUNTIME_FEATURE_CANARY_INVALID,
    TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
    TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
    TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
    TaskRuntimeFeatureCanaryError,
    TaskRuntimeFeatureCanaryReceipt,
)


TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY = (
    "TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY"
)
_MIGRATION_ID = 120
_MIGRATION_NAME = "task_runtime_feature_canary_receipt"
_TABLE = "task_runtime_feature_canary_receipt"
_COLUMNS = (
    "receipt_ref",
    "schema_version",
    "feature_key",
    "build_commit_sha",
    "runtime_migration_version",
    "configuration_digest",
    "evidence_digest",
    "attestation_key_id",
    "attestation_signature",
    "issued_at_ms",
    "expires_at_ms",
)
_TRIGGERS = {
    "task_runtime_feature_canary_receipt_no_update",
    "task_runtime_feature_canary_receipt_no_delete",
}


class SQLiteTaskRuntimeFeatureCanaryReceiptStore:
    """Reopen only immutable, signature-verified feature-canary receipts."""

    def __init__(self, db_path: str | Path) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "a file-backed canonical database is required",
            )
        self._db_path = str(Path(raw).resolve())
        self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def assert_schema_ready(self) -> None:
        try:
            with self._connection() as connection:
                migration = connection.execute(
                    "SELECT name FROM schema_migrations WHERE migration_id = ?",
                    (_MIGRATION_ID,),
                ).fetchone()
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({_TABLE})"
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' AND tbl_name=?",
                        (_TABLE,),
                    ).fetchall()
                }
        except sqlite3.Error as exc:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY,
                "feature-canary schema cannot be inspected",
            ) from exc
        if (
            migration is None
            or str(migration[0]) != _MIGRATION_NAME
            or columns != set(_COLUMNS)
            or triggers != _TRIGGERS
        ):
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY,
                "migration 0120 or immutable feature-canary schema is unavailable",
            )

    def append_attested(
        self,
        receipt: TaskRuntimeFeatureCanaryReceipt,
        *,
        signature_verifier: FeatureCanarySignatureVerifier | None,
    ) -> tuple[TaskRuntimeFeatureCanaryReceipt, bool]:
        """Append one externally signed receipt, or reopen its exact replay."""

        if not isinstance(receipt, TaskRuntimeFeatureCanaryReceipt):
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "append requires a typed feature-canary receipt",
            )
        self._verify_signature(receipt, signature_verifier=signature_verifier)
        expected = receipt.to_dict()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT " + ",".join(_COLUMNS) + f" FROM {_TABLE} "
                    "WHERE receipt_ref = ?",
                    (receipt.receipt_ref,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        f"INSERT INTO {_TABLE} (" + ",".join(_COLUMNS) + ") "
                        "VALUES (" + ",".join("?" for _ in _COLUMNS) + ")",
                        tuple(expected[column] for column in _COLUMNS),
                    )
                    connection.commit()
                    return receipt, False
                connection.commit()
        except sqlite3.Error as exc:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "feature-canary receipt could not be appended",
            ) from exc
        reopened = self._receipt_from_row(existing)
        if reopened.to_dict() != expected:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
                "feature-canary receipt reference is already bound to other data",
            )
        self._verify_signature(reopened, signature_verifier=signature_verifier)
        return reopened, True

    def read(self, receipt_ref: str) -> TaskRuntimeFeatureCanaryReceipt | None:
        ref = str(receipt_ref or "").strip()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT " + ",".join(_COLUMNS) + f" FROM {_TABLE} "
                    "WHERE receipt_ref = ?",
                    (ref,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY,
                "feature-canary receipt could not be reopened",
            ) from exc
        return None if row is None else self._receipt_from_row(row)

    def require_active(
        self,
        *,
        receipt_ref: str,
        feature_key: str,
        build_commit_sha: str,
        runtime_migration_version: int,
        configuration_digest: str,
        signature_verifier: FeatureCanarySignatureVerifier | None,
        now_ms: int,
    ) -> TaskRuntimeFeatureCanaryReceipt:
        receipt = self.read(receipt_ref)
        if receipt is None:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
                "bounded fan-out receipt is not durable",
            )
        self._verify_signature(receipt, signature_verifier=signature_verifier)
        expected = {
            "feature_key": str(feature_key or "").strip(),
            "build_commit_sha": str(build_commit_sha or "").strip(),
            "runtime_migration_version": int(runtime_migration_version),
            "configuration_digest": str(configuration_digest or "").strip(),
        }
        actual = {
            "feature_key": receipt.feature_key,
            "build_commit_sha": receipt.build_commit_sha,
            "runtime_migration_version": receipt.runtime_migration_version,
            "configuration_digest": receipt.configuration_digest,
        }
        if actual != expected:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
                "feature-canary receipt differs from the running runtime",
            )
        observed = int(now_ms)
        if not receipt.issued_at_ms <= observed < receipt.expires_at_ms:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_EXPIRED,
                "feature-canary receipt is outside its active window",
            )
        return receipt

    @staticmethod
    def _receipt_from_row(raw: Mapping[str, Any]) -> TaskRuntimeFeatureCanaryReceipt:
        return TaskRuntimeFeatureCanaryReceipt.from_mapping(dict(raw))

    @staticmethod
    def _verify_signature(
        receipt: TaskRuntimeFeatureCanaryReceipt,
        *,
        signature_verifier: FeatureCanarySignatureVerifier | None,
    ) -> None:
        if signature_verifier is None:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
                "feature-canary signature verifier is unavailable",
            )
        try:
            valid = bool(
                signature_verifier.has_key(receipt.attestation_key_id)
                and signature_verifier.verify(
                    key_id=receipt.attestation_key_id,
                    message=receipt.signing_message,
                    signature=receipt.attestation_signature,
                )
            )
        except Exception as exc:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
                "feature-canary signature verification failed",
            ) from exc
        if not valid:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
                "feature-canary signature is not trusted",
            )


__all__ = [
    "SQLiteTaskRuntimeFeatureCanaryReceiptStore",
    "TASK_RUNTIME_FEATURE_CANARY_SCHEMA_NOT_READY",
]
