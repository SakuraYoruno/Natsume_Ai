"""SQLite-enforced query-only authority for terminal reconciliation."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from host_brain.persistence.reconciliation_proof_verifier import (
    ReconciliationProofVerifier,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_INVALID,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)

from .task_terminal_reconciliation_read_store import (
    TaskTerminalReconciliationReadMixin,
)


class TerminalReconciliationQueryRepository(
    TaskTerminalReconciliationReadMixin
):
    """Narrow reader over the canonical Task Runtime database; it cannot write."""

    def __init__(
        self,
        canonical_db_path: str | Path,
        *,
        timeout_ms: int = 3_000,
        reconciliation_proof_verifier: ReconciliationProofVerifier | None = None,
    ) -> None:
        raw_path = str(canonical_db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "a durable canonical database path is required for terminal reconciliation reads",
            )
        self.canonical_db_path = Path(raw_path).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        self._reconciliation_proof_verifier = reconciliation_proof_verifier
        self._terminal_reconciliation_schema_ready = False
        if not self.canonical_db_path.is_file():
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"canonical Task Runtime database does not exist: {self.canonical_db_path}",
            )
        try:
            self.assert_terminal_reconciliation_ready()
        except TaskStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "terminal reconciliation query schema is unavailable",
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                self.canonical_db_path.as_uri() + "?mode=ro",
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            return conn
        except sqlite3.DatabaseError as exc:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "canonical Task Runtime database could not be opened read-only",
            ) from exc


__all__ = ["TerminalReconciliationQueryRepository"]
