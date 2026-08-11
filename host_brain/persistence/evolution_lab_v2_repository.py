"""Durable append-only owner for V2 Evolution Lab evidence.

V2 tables are deliberately parallel to the historical V1 ledger.  A V2
repository never reads V1 records as activation or promotion evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterator

from host_brain.contracts.evolution_lab import (
    EvolutionProposalV2,
    ExperimentReportV2,
    ExperimentRunReceiptV2,
    ExperimentSpecV2,
)
from host_brain.core.ids import now_ms


EVOLUTION_LAB_V2_MIGRATION_ASSET_KEY = "0122#evolution_lab_v2_evidence"


class EvolutionLabV2LedgerError(RuntimeError):
    """Raised when V2 Lab evidence is incomplete, unbound or replay-drifting."""


class SQLiteEvolutionLabV2Ledger:
    """Persist only immutable, no-effect V2 Lab registrations and receipts."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], int] = now_ms) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise EvolutionLabV2LedgerError("Evolution Lab V2 ledger requires a durable SQLite path")
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise EvolutionLabV2LedgerError("Evolution Lab V2 ledger database does not exist")
        with self._connection() as conn:
            names = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
                )
            }
            required = {
                "evolution_lab_v2_experiment_spec",
                "evolution_lab_v2_execution_evidence",
                "evolution_lab_v2_run_receipt",
                "evolution_lab_v2_report",
                "evolution_lab_v2_proposal",
                "evolution_lab_v2_evidence_spec_bound",
                "evolution_lab_v2_run_evidence_bound",
                "evolution_lab_v2_proposal_report_bound",
            }
            if not required.issubset(names):
                raise EvolutionLabV2LedgerError("migration 0122 Evolution Lab V2 schema is not ready")

    def register_spec_v2(self, spec: ExperimentSpecV2) -> str:
        if not isinstance(spec, ExperimentSpecV2):
            raise EvolutionLabV2LedgerError("V2 experiment spec is invalid")
        encoded = _canonical(spec.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT spec_json FROM evolution_lab_v2_experiment_spec WHERE experiment_id = ?",
                (spec.experiment_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabV2LedgerError("Evolution Lab V2 spec replay drift")
                return spec.spec_digest
            conn.execute(
                """
                INSERT INTO evolution_lab_v2_experiment_spec
                (experiment_id, spec_digest, workspace_id, dataset_manifest_digest, spec_json, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.experiment_id,
                    spec.spec_digest,
                    spec.workspace_id,
                    spec.dataset_manifest.manifest_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return spec.spec_digest

    def append_run_v2(self, receipt: ExperimentRunReceiptV2) -> str:
        return self.append_execution_and_run(receipt)

    def append_execution_and_run(self, receipt: ExperimentRunReceiptV2) -> str:
        """Atomically write execution evidence and its corresponding V2 run."""

        if not isinstance(receipt, ExperimentRunReceiptV2):
            raise EvolutionLabV2LedgerError("V2 experiment run receipt is invalid")
        receipt_json = _canonical(receipt.to_dict())
        sandbox_json = _canonical(receipt.sandbox_receipt.to_dict())
        verification_json = _canonical(receipt.verification_receipt.to_dict())
        with self._transaction() as conn:
            existing_run = conn.execute(
                "SELECT receipt_json FROM evolution_lab_v2_run_receipt WHERE run_id = ?",
                (receipt.run_id,),
            ).fetchone()
            if existing_run is not None:
                if str(existing_run[0]) != receipt_json:
                    raise EvolutionLabV2LedgerError("Evolution Lab V2 run replay drift")
                self._require_exact_evidence(conn, receipt, sandbox_json, verification_json)
                return receipt.run_id

            if conn.execute(
                """
                SELECT 1 FROM evolution_lab_v2_experiment_spec
                WHERE spec_digest = ? AND workspace_id = ? AND dataset_manifest_digest = ?
                """,
                (
                    receipt.spec_digest,
                    receipt.workspace_id,
                    receipt.dataset_manifest_digest,
                ),
            ).fetchone() is None:
                raise EvolutionLabV2LedgerError("V2 run receipt references an unregistered exact spec")

            evidence = conn.execute(
                """
                SELECT spec_digest, workspace_id, dataset_manifest_digest,
                       sandbox_receipt_json, verification_receipt_json, evidence_digest
                FROM evolution_lab_v2_execution_evidence WHERE execution_id = ?
                """,
                (receipt.execution_id,),
            ).fetchone()
            if evidence is not None:
                self._assert_evidence_row_matches(
                    evidence,
                    receipt,
                    sandbox_json,
                    verification_json,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO evolution_lab_v2_execution_evidence
                    (execution_id, spec_digest, workspace_id, dataset_manifest_digest,
                     sandbox_receipt_digest, verification_receipt_digest,
                     effect_audit_receipt_digest, evidence_digest,
                     sandbox_receipt_json, verification_receipt_json,
                     effect_count, production_side_effects, benefit_eligible, created_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)
                    """,
                    (
                        receipt.execution_id,
                        receipt.spec_digest,
                        receipt.workspace_id,
                        receipt.dataset_manifest_digest,
                        receipt.sandbox_receipt.receipt_digest,
                        receipt.verification_receipt.receipt_digest,
                        receipt.sandbox_receipt.effect_audit.receipt_digest,
                        receipt.evidence_digest,
                        sandbox_json,
                        verification_json,
                        self._clock(),
                    ),
                )

            conn.execute(
                """
                INSERT INTO evolution_lab_v2_run_receipt
                (run_id, execution_id, receipt_digest, spec_digest, workspace_id,
                 dataset_manifest_digest, variant_id, receipt_json,
                 production_side_effects, benefit_eligible, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    receipt.run_id,
                    receipt.execution_id,
                    receipt.receipt_digest,
                    receipt.spec_digest,
                    receipt.workspace_id,
                    receipt.dataset_manifest_digest,
                    receipt.variant_id,
                    receipt_json,
                    self._clock(),
                ),
            )
        return receipt.run_id

    def append_report_v2(self, report: ExperimentReportV2) -> str:
        if not isinstance(report, ExperimentReportV2):
            raise EvolutionLabV2LedgerError("V2 experiment report is invalid")
        encoded = _canonical(report.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT report_json FROM evolution_lab_v2_report WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabV2LedgerError("Evolution Lab V2 report replay drift")
                return report.report_digest
            if conn.execute(
                "SELECT 1 FROM evolution_lab_v2_experiment_spec WHERE spec_digest = ?",
                (report.spec.spec_digest,),
            ).fetchone() is None:
                raise EvolutionLabV2LedgerError("V2 report references an unregistered spec")
            for run in report.runs:
                row = conn.execute(
                    "SELECT spec_digest, receipt_json FROM evolution_lab_v2_run_receipt WHERE run_id = ?",
                    (run.run_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row[0]) != report.spec.spec_digest
                    or str(row[1]) != _canonical(run.to_dict())
                ):
                    raise EvolutionLabV2LedgerError("V2 report run is not an exact durable receipt")
            conn.execute(
                """
                INSERT INTO evolution_lab_v2_report
                (report_id, report_digest, spec_digest, report_json, promotion_eligible, created_at_ms)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    report.report_id,
                    report.report_digest,
                    report.spec.spec_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return report.report_digest

    def append_proposal_v2(self, proposal: EvolutionProposalV2) -> str:
        if not isinstance(proposal, EvolutionProposalV2):
            raise EvolutionLabV2LedgerError("V2 Evolution proposal is invalid")
        encoded = _canonical(proposal.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT proposal_json FROM evolution_lab_v2_proposal WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise EvolutionLabV2LedgerError("Evolution Lab V2 proposal replay drift")
                return proposal.proposal_id
            if conn.execute(
                "SELECT 1 FROM evolution_lab_v2_report WHERE report_digest = ?",
                (proposal.report_digest,),
            ).fetchone() is None:
                raise EvolutionLabV2LedgerError("V2 proposal references an unregistered report")
            conn.execute(
                """
                INSERT INTO evolution_lab_v2_proposal
                (proposal_id, proposal_digest, report_digest, proposal_json,
                 review_required, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.proposal_digest,
                    proposal.report_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return proposal.proposal_id

    def _require_exact_evidence(
        self,
        conn: sqlite3.Connection,
        receipt: ExperimentRunReceiptV2,
        sandbox_json: str,
        verification_json: str,
    ) -> None:
        evidence = conn.execute(
            """
            SELECT spec_digest, workspace_id, dataset_manifest_digest,
                   sandbox_receipt_json, verification_receipt_json, evidence_digest
            FROM evolution_lab_v2_execution_evidence WHERE execution_id = ?
            """,
            (receipt.execution_id,),
        ).fetchone()
        if evidence is None:
            raise EvolutionLabV2LedgerError("V2 run replay lacks durable execution evidence")
        self._assert_evidence_row_matches(evidence, receipt, sandbox_json, verification_json)

    @staticmethod
    def _assert_evidence_row_matches(
        row: sqlite3.Row,
        receipt: ExperimentRunReceiptV2,
        sandbox_json: str,
        verification_json: str,
    ) -> None:
        if (
            str(row["spec_digest"]) != receipt.spec_digest
            or str(row["workspace_id"]) != receipt.workspace_id
            or str(row["dataset_manifest_digest"]) != receipt.dataset_manifest_digest
            or str(row["sandbox_receipt_json"]) != sandbox_json
            or str(row["verification_receipt_json"]) != verification_json
            or str(row["evidence_digest"]) != receipt.evidence_digest
        ):
            raise EvolutionLabV2LedgerError("Evolution Lab V2 execution evidence replay drift")

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


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "EVOLUTION_LAB_V2_MIGRATION_ASSET_KEY",
    "EvolutionLabV2LedgerError",
    "SQLiteEvolutionLabV2Ledger",
]
