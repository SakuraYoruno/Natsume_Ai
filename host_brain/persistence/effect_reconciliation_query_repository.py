"""Read-only canonical query authority for Effect reconciliation.

The dedicated observability process cannot share the Host process' in-memory
Task Runtime container.  It therefore opens the same canonical SQLite file
through a query-only URI instead of constructing another ``DurableTaskStore``
writer or maintaining a second task-state projection.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import time

from host_brain.persistence.reconciliation_proof_verifier import (
    ReconciliationProofVerifier,
)
from host_brain.persistence.task_effect_reconciliation_read_store import (
    TaskEffectReconciliationReadMixin,
)
from host_brain.persistence.task_acceptance_effect_reconciliation_store import (
    TaskAcceptanceEffectReconciliationStoreMixin,
)
from host_brain.persistence.task_acceptance_effect_reconciliation_evidence_store import (
    TaskAcceptanceEffectReconciliationEvidenceStoreMixin,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_INVALID,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


class EffectReconciliationQueryRepository(
    TaskAcceptanceEffectReconciliationEvidenceStoreMixin,
    TaskAcceptanceEffectReconciliationStoreMixin,
    TaskEffectReconciliationReadMixin,
):
    """Narrow, query-only view over the canonical Task Runtime database."""

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
                "a durable canonical database path is required for Effect reconciliation reads",
            )
        self.canonical_db_path = Path(raw_path).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        self._reconciliation_proof_verifier = reconciliation_proof_verifier
        self._effect_reconciliation_schema_ready = False
        if not self.canonical_db_path.is_file():
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"canonical Task Runtime database does not exist: {self.canonical_db_path}",
            )
        try:
            self.assert_effect_reconciliation_ready()
            self.assert_acceptance_effect_reconciliation_ready()
        except TaskStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "Effect reconciliation query schema is unavailable",
            ) from exc

    @staticmethod
    def _now() -> int:
        return time.time_ns() // 1_000_000

    def record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del effect_id, kwargs
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Effect reconciliation query repository is read-only",
        )

    def _connect(self) -> sqlite3.Connection:
        """Open one operation-scoped connection with SQLite-enforced read-only authority."""

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


__all__ = ["EffectReconciliationQueryRepository"]
