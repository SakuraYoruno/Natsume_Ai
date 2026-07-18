"""Durable NE-06 proposal, knowledge and identity lifecycle owner."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.core.ids import now_ms
from host_brain.evolution import FeedbackProposalV1, IdentityMigrationPlanV1, KnowledgeEntryV1


EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY = "0110#evolution_governance_ledger"
_REF_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,1024}\Z")


class EvolutionGovernanceLedgerError(RuntimeError):
    """The proposal/evidence ledger failed closed."""


class SQLiteEvolutionGovernanceLedger:
    """Append-only owner for Feedback, Knowledge and Identity transitions."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], int] = now_ms) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_DURABLE_PATH_REQUIRED")
        self.db_path = Path(raw).expanduser().resolve()
        if not callable(clock):
            raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_CLOCK_INVALID")
        self._clock = clock
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_SCHEMA_UNAVAILABLE")
        with self._connection() as conn:
            try:
                asset = conn.execute(
                    "SELECT disposition FROM schema_migration_assets WHERE asset_key = ?",
                    (EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_MIGRATION_DRIFT") from exc
            if asset is None or str(asset[0]) not in {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}:
                raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_MIGRATION_DRIFT")
            required_columns = {
                "evolution_feedback_proposal": {"proposal_id", "proposal_digest", "trace_digest", "outcome_digest", "proposal_json", "created_at_ms"},
                "evolution_feedback_event": {"sequence", "event_id", "proposal_id", "event", "lab_report_ref", "review_ref", "rollback_ref", "event_json", "created_at_ms"},
                "evolution_knowledge_entry": {"entry_id", "entry_digest", "namespace", "workspace_id", "content_digest", "provenance_ref", "version", "validity", "confidence", "conflict_group", "supersedes", "deprecated", "entry_json", "created_at_ms"},
                "evolution_knowledge_event": {"sequence", "event_id", "entry_id", "event", "lab_report_ref", "review_ref", "event_json", "created_at_ms"},
                "evolution_identity_plan": {"migration_id", "plan_digest", "previous_snapshot_digest", "proposed_snapshot_digest", "diff_digest", "rollback_snapshot_digest", "activation_version", "plan_json", "created_at_ms"},
                "evolution_identity_event": {"sequence", "event_id", "migration_id", "event", "regression_report_ref", "review_ref", "event_json", "created_at_ms"},
            }
            for table, expected in required_columns.items():
                actual = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if actual != expected:
                    raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_SCHEMA_DRIFT")
            triggers = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            required_triggers = {
                "evolution_feedback_proposal_no_update", "evolution_feedback_proposal_no_delete",
                "evolution_feedback_event_no_update", "evolution_feedback_event_no_delete",
                "evolution_knowledge_entry_no_update", "evolution_knowledge_entry_no_delete",
                "evolution_knowledge_event_no_update", "evolution_knowledge_event_no_delete",
                "evolution_identity_plan_no_update", "evolution_identity_plan_no_delete",
                "evolution_identity_event_no_update", "evolution_identity_event_no_delete",
            }
            if not required_triggers.issubset(triggers):
                raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_SCHEMA_DRIFT")

    def register_feedback(self, proposal: FeedbackProposalV1) -> str:
        if not isinstance(proposal, FeedbackProposalV1):
            raise EvolutionGovernanceLedgerError("EVOLUTION_FEEDBACK_PROPOSAL_INVALID")
        encoded = _canonical(proposal.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT proposal_json FROM evolution_feedback_proposal WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionGovernanceLedgerError("EVOLUTION_FEEDBACK_REPLAY_DRIFT")
                return proposal.proposal_digest
            created_at_ms = int(self._clock())
            conn.execute(
                "INSERT INTO evolution_feedback_proposal(proposal_id, proposal_digest, trace_digest, outcome_digest, proposal_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)",
                (proposal.proposal_id, proposal.proposal_digest, proposal.trace_digest, proposal.outcome_digest, encoded, created_at_ms),
            )
            self._insert_feedback_event(
                conn,
                {"proposal_id": proposal.proposal_id, "proposal_digest": proposal.proposal_digest, "created_at_ms": created_at_ms},
                event="STAGED",
                lab_report_ref=None,
                review_ref=None,
            )
        return proposal.proposal_digest

    def apply_feedback(self, *, proposal_id: str, lab_report_ref: str, review_ref: str) -> dict[str, Any]:
        proposal = self._feedback_required(proposal_id)
        lab = _ref(lab_report_ref, "lab_report_ref")
        review = _ref(review_ref, "review_ref")
        with self._transaction() as conn:
            event = self._insert_feedback_event(conn, proposal, event="APPLIED", lab_report_ref=lab, review_ref=review)
        return event

    def rollback_feedback(
        self,
        *,
        proposal_id: str,
        rollback_ref: str,
        lab_report_ref: str,
        review_ref: str,
    ) -> dict[str, Any]:
        proposal = self._feedback_required(proposal_id)
        rollback = _ref(rollback_ref, "rollback_ref")
        lab = _ref(lab_report_ref, "lab_report_ref")
        review = _ref(review_ref, "review_ref")
        with self._transaction() as conn:
            return self._insert_feedback_event(conn, proposal, event="ROLLED_BACK", lab_report_ref=lab, review_ref=review, rollback_ref=rollback)

    def register_knowledge(self, entry: KnowledgeEntryV1) -> str:
        if not isinstance(entry, KnowledgeEntryV1):
            raise EvolutionGovernanceLedgerError("EVOLUTION_KNOWLEDGE_ENTRY_INVALID")
        encoded = _canonical(entry.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT entry_json FROM evolution_knowledge_entry WHERE entry_id = ?",
                (entry.entry_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionGovernanceLedgerError("EVOLUTION_KNOWLEDGE_REPLAY_DRIFT")
                return entry.entry_digest
            created_at_ms = int(self._clock())
            conn.execute(
                """
                INSERT INTO evolution_knowledge_entry(
                    entry_id, entry_digest, namespace, workspace_id, content_digest,
                    provenance_ref, version, validity, confidence, conflict_group,
                    supersedes, deprecated, entry_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id, entry.entry_digest, entry.namespace, entry.workspace_id,
                    entry.content_digest, entry.provenance_ref, entry.version, entry.validity,
                    entry.confidence, entry.conflict_group, entry.supersedes, int(entry.deprecated),
                    encoded, created_at_ms,
                ),
            )
            self._insert_knowledge_event(
                conn,
                {"entry_id": entry.entry_id, "entry_digest": entry.entry_digest, "created_at_ms": created_at_ms},
                event="PROPOSED",
                lab_report_ref=None,
                review_ref=None,
            )
        return entry.entry_digest

    def activate_knowledge(self, *, entry_id: str, lab_report_ref: str, review_ref: str) -> dict[str, Any]:
        entry = self._knowledge_required(entry_id)
        with self._transaction() as conn:
            return self._insert_knowledge_event(
                conn,
                entry,
                event="ACTIVATED",
                lab_report_ref=_ref(lab_report_ref, "lab_report_ref"),
                review_ref=_ref(review_ref, "review_ref"),
            )

    def deprecate_knowledge(self, *, entry_id: str, review_ref: str) -> dict[str, Any]:
        entry = self._knowledge_required(entry_id)
        with self._transaction() as conn:
            return self._insert_knowledge_event(
                conn,
                entry,
                event="DEPRECATED",
                lab_report_ref=None,
                review_ref=_ref(review_ref, "review_ref"),
            )

    def active_knowledge(self, *, namespace: str, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        bounded = int(limit)
        if bounded < 1 or bounded > 500:
            raise EvolutionGovernanceLedgerError("EVOLUTION_KNOWLEDGE_QUERY_LIMIT_INVALID")
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT entry_json FROM evolution_knowledge_entry AS entry
                WHERE entry.namespace = ? AND entry.workspace_id = ?
                  AND EXISTS (
                      SELECT 1 FROM evolution_knowledge_event AS activated
                      WHERE activated.entry_id = entry.entry_id AND activated.event = 'ACTIVATED'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM evolution_knowledge_event AS later
                      WHERE later.entry_id = entry.entry_id AND later.event IN ('DEPRECATED', 'REJECTED')
                        AND later.sequence > (
                            SELECT MAX(active.sequence) FROM evolution_knowledge_event AS active
                            WHERE active.entry_id = entry.entry_id AND active.event = 'ACTIVATED'
                        )
                  )
                ORDER BY entry.created_at_ms ASC, entry.entry_id ASC LIMIT ?
                """,
                (str(namespace), str(workspace_id), bounded),
            ).fetchall()
            return [json.loads(str(row[0])) for row in rows]

    def register_identity(self, plan: IdentityMigrationPlanV1) -> str:
        if not isinstance(plan, IdentityMigrationPlanV1):
            raise EvolutionGovernanceLedgerError("EVOLUTION_IDENTITY_PLAN_INVALID")
        encoded = _canonical(plan.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT plan_json FROM evolution_identity_plan WHERE migration_id = ?",
                (plan.migration_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionGovernanceLedgerError("EVOLUTION_IDENTITY_REPLAY_DRIFT")
                return plan.plan_digest
            created_at_ms = int(self._clock())
            conn.execute(
                """
                INSERT INTO evolution_identity_plan(
                    migration_id, plan_digest, previous_snapshot_digest,
                    proposed_snapshot_digest, diff_digest, rollback_snapshot_digest,
                    activation_version, plan_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.migration_id, plan.plan_digest, plan.previous_snapshot_digest,
                    plan.proposed_snapshot_digest, plan.diff_digest, plan.rollback_snapshot_digest,
                    plan.activation_version, encoded, created_at_ms,
                ),
            )
            self._insert_identity_event(
                conn,
                {"migration_id": plan.migration_id, "plan_digest": plan.plan_digest, "created_at_ms": created_at_ms},
                event="PROPOSED",
                regression_report_ref=None,
                review_ref=None,
            )
        return plan.plan_digest

    def activate_identity(self, *, migration_id: str, regression_report_ref: str, review_ref: str) -> dict[str, Any]:
        plan = self._identity_required(migration_id)
        with self._transaction() as conn:
            return self._insert_identity_event(
                conn,
                plan,
                event="ACTIVE",
                regression_report_ref=_ref(regression_report_ref, "regression_report_ref"),
                review_ref=_ref(review_ref, "review_ref"),
            )

    def rollback_identity(self, *, migration_id: str, regression_report_ref: str, review_ref: str) -> dict[str, Any]:
        plan = self._identity_required(migration_id)
        with self._transaction() as conn:
            return self._insert_identity_event(
                conn,
                plan,
                event="ROLLED_BACK",
                regression_report_ref=_ref(regression_report_ref, "regression_report_ref"),
                review_ref=_ref(review_ref, "review_ref"),
            )

    def list_events(self, *, subject_kind: str, subject_id: str, limit: int = 100) -> list[dict[str, Any]]:
        bounded = int(limit)
        if bounded < 1 or bounded > 500:
            raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_QUERY_LIMIT_INVALID")
        if subject_kind == "feedback":
            table, column = "evolution_feedback_event", "proposal_id"
        elif subject_kind == "knowledge":
            table, column = "evolution_knowledge_event", "entry_id"
        elif subject_kind == "identity":
            table, column = "evolution_identity_event", "migration_id"
        else:
            raise EvolutionGovernanceLedgerError("EVOLUTION_GOVERNANCE_SUBJECT_KIND_INVALID")
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT event_json FROM {table} WHERE {column} = ? ORDER BY sequence ASC LIMIT ?",
                (str(subject_id), bounded),
            ).fetchall()
            return [json.loads(str(row[0])) for row in rows]

    def _feedback_required(self, proposal_id: str) -> Mapping[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM evolution_feedback_proposal WHERE proposal_id = ?", (str(proposal_id),)).fetchone()
            if row is None:
                raise EvolutionGovernanceLedgerError("EVOLUTION_FEEDBACK_PROPOSAL_NOT_FOUND")
            return dict(row)

    def _knowledge_required(self, entry_id: str) -> Mapping[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM evolution_knowledge_entry WHERE entry_id = ?", (str(entry_id),)).fetchone()
            if row is None:
                raise EvolutionGovernanceLedgerError("EVOLUTION_KNOWLEDGE_ENTRY_NOT_FOUND")
            return dict(row)

    def _identity_required(self, migration_id: str) -> Mapping[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM evolution_identity_plan WHERE migration_id = ?", (str(migration_id),)).fetchone()
            if row is None:
                raise EvolutionGovernanceLedgerError("EVOLUTION_IDENTITY_PLAN_NOT_FOUND")
            return dict(row)

    @staticmethod
    def _insert_feedback_event(
        conn: sqlite3.Connection,
        proposal: Mapping[str, Any],
        *,
        event: str,
        lab_report_ref: str | None,
        review_ref: str | None,
        rollback_ref: str | None = None,
    ) -> dict[str, Any]:
        digest = str(proposal["proposal_digest"])
        event_id = f"evolution-feedback-event://{event.lower()}/{digest}"
        payload = {
            "schema": "natsume.feedback-event.v1",
            "event_id": event_id,
            "proposal_id": str(proposal["proposal_id"]),
            "proposal_digest": digest,
            "event": event,
            "lab_report_ref": lab_report_ref,
            "review_ref": review_ref,
            "rollback_ref": rollback_ref,
        }
        encoded = _canonical(payload)
        existing = conn.execute("SELECT event_json FROM evolution_feedback_event WHERE event_id = ?", (event_id,)).fetchone()
        if existing is not None:
            if str(existing[0]) != encoded:
                raise EvolutionGovernanceLedgerError("EVOLUTION_FEEDBACK_EVENT_REPLAY_DRIFT")
            return payload
        conn.execute(
            "INSERT INTO evolution_feedback_event(event_id, proposal_id, event, lab_report_ref, review_ref, rollback_ref, event_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, str(proposal["proposal_id"]), event, lab_report_ref, review_ref, rollback_ref, encoded, int(proposal.get("created_at_ms", 0))),
        )
        return payload

    @staticmethod
    def _insert_knowledge_event(
        conn: sqlite3.Connection,
        entry: Mapping[str, Any],
        *,
        event: str,
        lab_report_ref: str | None,
        review_ref: str | None,
    ) -> dict[str, Any]:
        digest = str(entry["entry_digest"])
        event_id = f"evolution-knowledge-event://{event.lower()}/{digest}"
        payload = {
            "schema": "natsume.knowledge-event.v1",
            "event_id": event_id,
            "entry_id": str(entry["entry_id"]),
            "entry_digest": digest,
            "event": event,
            "lab_report_ref": lab_report_ref,
            "review_ref": review_ref,
        }
        encoded = _canonical(payload)
        existing = conn.execute("SELECT event_json FROM evolution_knowledge_event WHERE event_id = ?", (event_id,)).fetchone()
        if existing is not None:
            if str(existing[0]) != encoded:
                raise EvolutionGovernanceLedgerError("EVOLUTION_KNOWLEDGE_EVENT_REPLAY_DRIFT")
            return payload
        conn.execute(
            "INSERT INTO evolution_knowledge_event(event_id, entry_id, event, lab_report_ref, review_ref, event_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, str(entry["entry_id"]), event, lab_report_ref, review_ref, encoded, int(entry.get("created_at_ms", 0))),
        )
        return payload

    @staticmethod
    def _insert_identity_event(
        conn: sqlite3.Connection,
        plan: Mapping[str, Any],
        *,
        event: str,
        regression_report_ref: str | None,
        review_ref: str | None,
    ) -> dict[str, Any]:
        digest = str(plan["plan_digest"])
        event_id = f"evolution-identity-event://{event.lower()}/{digest}"
        payload = {
            "schema": "natsume.identity-event.v1",
            "event_id": event_id,
            "migration_id": str(plan["migration_id"]),
            "plan_digest": digest,
            "event": event,
            "regression_report_ref": regression_report_ref,
            "review_ref": review_ref,
        }
        encoded = _canonical(payload)
        existing = conn.execute("SELECT event_json FROM evolution_identity_event WHERE event_id = ?", (event_id,)).fetchone()
        if existing is not None:
            if str(existing[0]) != encoded:
                raise EvolutionGovernanceLedgerError("EVOLUTION_IDENTITY_EVENT_REPLAY_DRIFT")
            return payload
        conn.execute(
            "INSERT INTO evolution_identity_event(event_id, migration_id, event, regression_report_ref, review_ref, event_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, str(plan["migration_id"]), event, regression_report_ref, review_ref, encoded, int(plan.get("created_at_ms", 0))),
        )
        return payload

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def _ref(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if _REF_RE.fullmatch(normalized) is None:
        raise EvolutionGovernanceLedgerError(f"EVOLUTION_{field.upper()}_INVALID")
    return normalized


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "EVOLUTION_GOVERNANCE_MIGRATION_ASSET_KEY",
    "EvolutionGovernanceLedgerError",
    "SQLiteEvolutionGovernanceLedger",
]
