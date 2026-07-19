"""Durable append-only owner for Evolution Lab evidence."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator

from host_brain.contracts.evolution_lab import (
    EvolutionProposalV1,
    ExperimentReportV1,
    ExperimentRunReceiptV1,
    ExperimentSpecV1,
    RedactionReceiptV1,
)
from host_brain.core.ids import now_ms


EVOLUTION_LAB_MIGRATION_ASSET_KEY = "0108#evolution_lab_ledger"


class EvolutionLabLedgerError(RuntimeError):
    """Evolution Lab evidence failed closed."""


class SQLiteEvolutionLabLedger:
    """Persist specs, runs, reports and proposal-only outputs."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], int] = now_ms) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise EvolutionLabLedgerError("Evolution Lab ledger requires a durable SQLite path")
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise EvolutionLabLedgerError("Evolution Lab ledger database does not exist")
        with self._connection() as conn:
            names = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
                )
            }
            required = {
                "evolution_lab_experiment_spec",
                "evolution_lab_redaction_receipt",
                "evolution_lab_run_receipt",
                "evolution_lab_report",
                "evolution_lab_proposal",
                "evolution_lab_run_spec_bound",
                "evolution_lab_proposal_report_bound",
            }
            if not required.issubset(names):
                raise EvolutionLabLedgerError("migration 0108 Evolution Lab schema is not ready")

    def register_spec(self, spec: ExperimentSpecV1) -> str:
        if not isinstance(spec, ExperimentSpecV1):
            raise EvolutionLabLedgerError("experiment spec is invalid")
        encoded = _canonical(spec.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT spec_json FROM evolution_lab_experiment_spec WHERE experiment_id = ?",
                (spec.experiment_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabLedgerError("Evolution Lab spec replay drift")
                return spec.spec_digest
            conn.execute(
                """
                INSERT INTO evolution_lab_experiment_spec
                (experiment_id, spec_digest, spec_json, created_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (spec.experiment_id, spec.spec_digest, encoded, self._clock()),
            )
        return spec.spec_digest

    def append_redaction(self, receipt: RedactionReceiptV1) -> str:
        if not isinstance(receipt, RedactionReceiptV1):
            raise EvolutionLabLedgerError("redaction receipt is invalid")
        encoded = _canonical(receipt.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT receipt_json FROM evolution_lab_redaction_receipt WHERE receipt_id = ?",
                (receipt.receipt_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabLedgerError("Evolution Lab redaction replay drift")
                return receipt.receipt_digest
            conn.execute(
                """
                INSERT INTO evolution_lab_redaction_receipt
                (receipt_id, receipt_digest, source_digest, rules_version,
                 output_dataset_digest, receipt_json, production_side_effects, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.receipt_digest,
                    receipt.source_digest,
                    receipt.rules_version,
                    receipt.output_dataset_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return receipt.receipt_digest

    def append_run(self, receipt: ExperimentRunReceiptV1) -> str:
        if not isinstance(receipt, ExperimentRunReceiptV1):
            raise EvolutionLabLedgerError("experiment run receipt is invalid")
        encoded = _canonical(receipt.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT receipt_json FROM evolution_lab_run_receipt WHERE run_id = ?",
                (receipt.run_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabLedgerError("Evolution Lab run replay drift")
                return receipt.run_id
            if conn.execute(
                "SELECT 1 FROM evolution_lab_experiment_spec WHERE spec_digest = ?",
                (receipt.spec_digest,),
            ).fetchone() is None:
                raise EvolutionLabLedgerError("run receipt references an unregistered spec")
            conn.execute(
                """
                INSERT INTO evolution_lab_run_receipt
                (run_id, spec_digest, variant, evidence_digest, receipt_json,
                 production_side_effects, created_at_ms)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    receipt.run_id,
                    receipt.spec_digest,
                    receipt.variant,
                    receipt.evidence_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return receipt.run_id

    def append_report(self, report: ExperimentReportV1) -> str:
        if not isinstance(report, ExperimentReportV1):
            raise EvolutionLabLedgerError("experiment report is invalid")
        encoded = _canonical(report.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT report_json FROM evolution_lab_report WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabLedgerError("Evolution Lab report replay drift")
                return report.report_digest
            if conn.execute(
                "SELECT 1 FROM evolution_lab_experiment_spec WHERE spec_digest = ?",
                (report.spec.spec_digest,),
            ).fetchone() is None:
                raise EvolutionLabLedgerError("report references an unregistered spec")
            for run in report.runs:
                row = conn.execute(
                    "SELECT spec_digest, receipt_json FROM evolution_lab_run_receipt WHERE run_id = ?",
                    (run.run_id,),
                ).fetchone()
                if row is None or str(row[0]) != report.spec.spec_digest or str(row[1]) != _canonical(run.to_dict()):
                    raise EvolutionLabLedgerError("report run is not an exact durable receipt")
            conn.execute(
                """
                INSERT INTO evolution_lab_report
                (report_id, report_digest, spec_digest, report_json, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report.report_id, report.report_digest, report.spec.spec_digest, encoded, self._clock()),
            )
        return report.report_digest

    def append_proposal(self, proposal: EvolutionProposalV1) -> str:
        if not isinstance(proposal, EvolutionProposalV1):
            raise EvolutionLabLedgerError("Evolution proposal is invalid")
        encoded = _canonical(proposal.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT proposal_json FROM evolution_lab_proposal WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabLedgerError("Evolution proposal replay drift")
                return proposal.proposal_id
            if conn.execute(
                "SELECT 1 FROM evolution_lab_report WHERE report_digest = ?",
                (proposal.report_digest,),
            ).fetchone() is None:
                raise EvolutionLabLedgerError("proposal references an unregistered report")
            conn.execute(
                """
                INSERT INTO evolution_lab_proposal
                (proposal_id, report_digest, proposal_json, review_required,
                 production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, 1, 0, ?)
                """,
                (proposal.proposal_id, proposal.report_digest, encoded, self._clock()),
            )
        return proposal.proposal_id

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
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


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "EVOLUTION_LAB_MIGRATION_ASSET_KEY",
    "EvolutionLabLedgerError",
    "SQLiteEvolutionLabLedger",
]
