"""Read-only compatibility access to the historical NE-06 V1 ledger."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from host_brain.contracts.evolution import (
    FeedbackProposalV1,
    IdentityMigrationPlanV1,
    KnowledgeEntryV1,
)
from host_brain.core.ids import now_ms


EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY = (
    "0110#evolution_governance_ledger"
)
EVOLUTION_GOVERNANCE_V2_MIGRATION_ASSET_KEY = (
    "0142#evolution_activation_authority"
)
_READ_ONLY_TRIGGERS = {
    "evolution_feedback_proposal_v1_read_only",
    "evolution_feedback_event_v1_read_only",
    "evolution_knowledge_entry_v1_read_only",
    "evolution_knowledge_event_v1_read_only",
    "evolution_identity_plan_v1_read_only",
    "evolution_identity_event_v1_read_only",
}


class EvolutionGovernanceLedgerError(RuntimeError):
    """The V1 history cannot satisfy an activation mutation."""


class SQLiteEvolutionGovernanceLedger:
    """Historical V1 query surface; every mutation fails closed."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_DURABLE_PATH_REQUIRED"
            )
        if not callable(clock):
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_CLOCK_INVALID"
            )
        self.db_path = Path(raw).expanduser().resolve()
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_SCHEMA_UNAVAILABLE"
            )
        try:
            with self._connection() as conn:
                assets = {
                    str(row[0]): str(row[1])
                    for row in conn.execute(
                        """
                        SELECT asset_key, disposition
                        FROM schema_migration_assets
                        WHERE asset_key IN (?, ?)
                        """,
                        (
                            EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY,
                            EVOLUTION_GOVERNANCE_V2_MIGRATION_ASSET_KEY,
                        ),
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'trigger'
                        """
                    ).fetchall()
                }
        except sqlite3.Error as exc:
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_MIGRATION_DRIFT"
            ) from exc
        accepted = {
            "APPLIED",
            "APPLIED_WITH_COMPAT",
            "LEGACY_BACKFILL",
        }
        if (
            set(assets) != {
                EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY,
                EVOLUTION_GOVERNANCE_V2_MIGRATION_ASSET_KEY,
            }
            or any(value not in accepted for value in assets.values())
            or not _READ_ONLY_TRIGGERS.issubset(triggers)
        ):
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_V1_NOT_READ_ONLY"
            )

    def register_feedback(self, proposal: FeedbackProposalV1) -> str:
        del proposal
        self._read_only()

    def apply_feedback(
        self,
        *,
        proposal_id: str,
        lab_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del proposal_id, lab_report_ref, review_ref
        self._read_only()

    def rollback_feedback(
        self,
        *,
        proposal_id: str,
        rollback_ref: str,
        lab_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del proposal_id, rollback_ref, lab_report_ref, review_ref
        self._read_only()

    def register_knowledge(self, entry: KnowledgeEntryV1) -> str:
        del entry
        self._read_only()

    def activate_knowledge(
        self,
        *,
        entry_id: str,
        lab_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del entry_id, lab_report_ref, review_ref
        self._read_only()

    def deprecate_knowledge(
        self,
        *,
        entry_id: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del entry_id, review_ref
        self._read_only()

    def register_identity(self, plan: IdentityMigrationPlanV1) -> str:
        del plan
        self._read_only()

    def activate_identity(
        self,
        *,
        migration_id: str,
        regression_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del migration_id, regression_report_ref, review_ref
        self._read_only()

    def rollback_identity(
        self,
        *,
        migration_id: str,
        regression_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        del migration_id, regression_report_ref, review_ref
        self._read_only()

    def active_knowledge(
        self,
        *,
        namespace: str,
        workspace_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = int(limit)
        if bounded_limit < 1 or bounded_limit > 500:
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_KNOWLEDGE_QUERY_LIMIT_INVALID"
            )
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT entry.entry_json
                FROM evolution_knowledge_entry AS entry
                WHERE entry.namespace = ? AND entry.workspace_id = ?
                  AND EXISTS (
                      SELECT 1
                      FROM evolution_knowledge_event AS event
                      WHERE event.entry_id = entry.entry_id
                        AND event.event = 'ACTIVATED'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM evolution_knowledge_event AS event
                      WHERE event.entry_id = entry.entry_id
                        AND event.event = 'DEPRECATED'
                  )
                ORDER BY entry.version DESC, entry.created_at_ms DESC
                LIMIT ?
                """,
                (
                    str(namespace or "").strip(),
                    str(workspace_id or "").strip(),
                    bounded_limit,
                ),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def list_events(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = int(limit)
        if bounded_limit < 1 or bounded_limit > 500:
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_QUERY_LIMIT_INVALID"
            )
        normalized = str(subject_kind or "").strip().lower()
        table_and_column = {
            "feedback": ("evolution_feedback_event", "proposal_id"),
            "knowledge": ("evolution_knowledge_event", "entry_id"),
            "identity": ("evolution_identity_event", "migration_id"),
        }.get(normalized)
        if table_and_column is None:
            raise EvolutionGovernanceLedgerError(
                "EVOLUTION_GOVERNANCE_SUBJECT_KIND_INVALID"
            )
        table, column = table_and_column
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT event_json
                FROM {table}
                WHERE {column} = ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (str(subject_id or "").strip(), bounded_limit),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _read_only() -> None:
        raise EvolutionGovernanceLedgerError(
            "EVOLUTION_GOVERNANCE_V1_READ_ONLY"
        )


__all__ = [
    "EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY",
    "EVOLUTION_GOVERNANCE_V2_MIGRATION_ASSET_KEY",
    "EvolutionGovernanceLedgerError",
    "SQLiteEvolutionGovernanceLedger",
]
