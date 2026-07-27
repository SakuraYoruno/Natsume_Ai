"""Durable owner for trusted Evolution Lab sandbox operations and receipts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterator, Mapping

from host_brain.contracts.evolution_lab import (
    EvolutionProposalV2,
    ExperimentSpecV2,
    PolicyVariantV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    GoldenExperimentSuiteReceiptV1,
    RedactionReceiptV1,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
)
from host_brain.core.ids import now_ms


EVOLUTION_LAB_TRUSTED_MIGRATION_ASSET_KEY = (
    "0138#evolution_lab_trusted_sandbox"
)
_MIGRATION_FILE = "0138_evolution_lab_trusted_sandbox.sql"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_OPERATION_STATUSES = {
    "COMPLETED",
    "FAILED",
    "RECONCILIATION_REQUIRED",
    "RUNNING",
}


class TrustedEvolutionLabLedgerError(RuntimeError):
    """Raised when trusted Lab operation/evidence authority is ambiguous."""


@dataclass(frozen=True, slots=True)
class TrustedExecutionClaim:
    operation_id: str
    execution_id: str
    spec_digest: str
    workspace_id: str
    dataset_manifest_digest: str
    variant_id: str
    container_name: str
    owner_id: str
    fencing_token: int
    lease_expires_at_ms: int
    status: str
    replayed: bool = False
    receipt: TrustedExperimentRunReceiptV2 | None = None

    @property
    def execution_authorized(self) -> bool:
        """Only a newly reserved claim may start an external container."""

        return self.status == "RUNNING" and not self.replayed


def trusted_execution_id(
    spec: ExperimentSpecV2,
    variant: PolicyVariantV1,
) -> str:
    digest = canonical_evolution_lab_digest(
        {
            "schema": "natsume.evolution-lab.trusted-execution-identity.v1",
            "spec_digest": spec.spec_digest,
            "variant_id": variant.variant_id,
            "seed": spec.seed,
            "isolation_profile_digest": spec.isolation_profile_digest,
        }
    )
    return f"lab-trusted-exec-{digest[:48]}"


class SQLiteTrustedEvolutionLabLedger:
    """Append trusted evidence and fence each external sandbox execution."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.startswith("file:"):
            raise TrustedEvolutionLabLedgerError(
                "trusted Evolution Lab ledger requires a durable SQLite path"
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise TrustedEvolutionLabLedgerError(
                "trusted Evolution Lab database does not exist"
            )
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / _MIGRATION_FILE
        )
        try:
            migration_checksum = hashlib.sha256(
                migration_path.read_text(encoding="utf-8-sig").encode("utf-8")
            ).hexdigest()
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE type IN ('table', 'trigger', 'index')
                    """
                ).fetchall()
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (EVOLUTION_LAB_TRUSTED_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise TrustedEvolutionLabLedgerError(
                "migration 0138 trusted Evolution Lab schema is not ready"
            ) from exc
        names = {str(row["name"]) for row in rows}
        required = {
            "evolution_lab_trusted_operation_head",
            "evolution_lab_trusted_operation_event",
            "evolution_lab_trusted_run_receipt",
            "evolution_lab_trusted_report",
            "evolution_lab_trusted_proposal",
            "evolution_lab_redaction_receipt",
            "evolution_lab_golden_suite_receipt",
            "evolution_lab_trusted_event_chain_guard",
            "evolution_lab_trusted_head_matches_event",
            "evolution_lab_trusted_run_operation_bound",
        }
        if (
            not required.issubset(names)
            or asset is None
            or str(asset["checksum"]) != migration_checksum
            or str(asset["disposition"])
            not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise TrustedEvolutionLabLedgerError(
                "migration 0138 trusted Evolution Lab schema is not ready"
            )

    def reserve_execution(
        self,
        *,
        spec: ExperimentSpecV2,
        variant_id: str,
        owner_id: str,
        lease_ms: int = 120_000,
    ) -> TrustedExecutionClaim:
        if not isinstance(spec, ExperimentSpecV2):
            raise TrustedEvolutionLabLedgerError(
                "trusted execution spec is invalid"
            )
        try:
            variant = spec.variant(variant_id)
        except ValueError as exc:
            raise TrustedEvolutionLabLedgerError(
                "trusted execution variant is not registered"
            ) from exc
        owner = _text(owner_id, "owner_id", 160)
        if isinstance(lease_ms, bool) or not isinstance(lease_ms, int) or lease_ms <= 0:
            raise TrustedEvolutionLabLedgerError(
                "trusted execution lease must be positive"
            )
        execution_id = trusted_execution_id(spec, variant)
        identity_digest = _sha256_hex(execution_id)
        operation_id = f"lab-trusted-operation-{identity_digest[:48]}"
        container_name = f"natsume-lab-{identity_digest[:40]}"
        now_value = int(self._clock())
        lease_expires = now_value + lease_ms

        with self._transaction() as conn:
            self._require_registered_spec(conn, spec)
            row = conn.execute(
                """
                SELECT * FROM evolution_lab_trusted_operation_head
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is not None:
                self._assert_operation_identity(
                    row,
                    spec=spec,
                    variant=variant,
                    execution_id=execution_id,
                    container_name=container_name,
                )
                self._assert_operation_chain(conn, row)
                status = str(row["status"])
                if status == "COMPLETED":
                    receipt = self._load_receipt_for_operation(
                        conn, operation_id
                    )
                    return self._claim_from_row(
                        row, replayed=True, receipt=receipt
                    )
                if status in {"FAILED", "RECONCILIATION_REQUIRED"}:
                    return self._claim_from_row(row, replayed=True)
                if status != "RUNNING":
                    raise TrustedEvolutionLabLedgerError(
                        "trusted operation has an unsupported durable status"
                    )
                if int(row["lease_expires_at_ms"]) > now_value:
                    return self._claim_from_row(row, replayed=True)
                reconciled = self._transition_to_reconciliation(
                    conn,
                    row=row,
                    owner_id=owner,
                    reason_code="TRUSTED_SANDBOX_LEASE_EXPIRED",
                    now_value=now_value,
                )
                return TrustedExecutionClaim(
                    operation_id=reconciled.operation_id,
                    execution_id=reconciled.execution_id,
                    spec_digest=reconciled.spec_digest,
                    workspace_id=reconciled.workspace_id,
                    dataset_manifest_digest=(
                        reconciled.dataset_manifest_digest
                    ),
                    variant_id=reconciled.variant_id,
                    container_name=reconciled.container_name,
                    owner_id=reconciled.owner_id,
                    fencing_token=reconciled.fencing_token,
                    lease_expires_at_ms=reconciled.lease_expires_at_ms,
                    status=reconciled.status,
                    replayed=True,
                )

            payload = {
                "container_name": container_name,
                "fencing_token": 1,
                "lease_expires_at_ms": lease_expires,
                "owner_id": owner,
                "status": "RUNNING",
            }
            event = _event_material(
                operation_id=operation_id,
                sequence=1,
                event_type="RUNNING",
                payload=payload,
                previous_digest=None,
            )
            conn.execute(
                """
                INSERT INTO evolution_lab_trusted_operation_head
                (operation_id, execution_id, spec_digest, workspace_id,
                 dataset_manifest_digest, variant_id, status, owner_id,
                 fencing_token, lease_expires_at_ms, container_name,
                 receipt_digest, version, current_event_sequence,
                 current_event_digest, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, 1, ?, ?, NULL, 1, 1, ?, ?, ?)
                """,
                (
                    operation_id,
                    execution_id,
                    spec.spec_digest,
                    spec.workspace_id,
                    spec.dataset_manifest.manifest_digest,
                    variant.variant_id,
                    owner,
                    lease_expires,
                    container_name,
                    event["event_digest"],
                    now_value,
                    now_value,
                ),
            )
            self._insert_event(
                conn,
                operation_id=operation_id,
                event=event,
                created_at_ms=now_value,
            )
            row = conn.execute(
                """
                SELECT * FROM evolution_lab_trusted_operation_head
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise TrustedEvolutionLabLedgerError(
                    "trusted operation insert did not reopen"
                )
            return self._claim_from_row(row)

    def complete_execution(
        self,
        *,
        claim: TrustedExecutionClaim,
        receipt: TrustedExperimentRunReceiptV2,
    ) -> TrustedExperimentRunReceiptV2:
        if not isinstance(claim, TrustedExecutionClaim) or not isinstance(
            receipt, TrustedExperimentRunReceiptV2
        ):
            raise TrustedEvolutionLabLedgerError(
                "trusted completion requires claim and receipt contracts"
            )
        encoded = _canonical(receipt.to_dict())
        now_value = int(self._clock())
        with self._transaction() as conn:
            row = self._require_live_claim(conn, claim)
            if str(row["status"]) == "COMPLETED":
                existing = self._load_receipt_for_operation(
                    conn, claim.operation_id
                )
                if existing.receipt_digest != receipt.receipt_digest:
                    raise TrustedEvolutionLabLedgerError(
                        "trusted completion exact replay drift"
                    )
                return existing
            if str(row["status"]) != "RUNNING":
                raise TrustedEvolutionLabLedgerError(
                    "trusted completion requires a running operation"
                )
            if (
                receipt.execution_id != claim.execution_id
                or receipt.spec_digest != claim.spec_digest
                or receipt.workspace_id != claim.workspace_id
                or receipt.dataset_manifest_digest
                != claim.dataset_manifest_digest
                or receipt.variant_id != claim.variant_id
                or not receipt.benefit_eligible
            ):
                raise TrustedEvolutionLabLedgerError(
                    "trusted completion receipt identity or eligibility drift"
                )
            conn.execute(
                """
                INSERT INTO evolution_lab_trusted_run_receipt
                (run_id, operation_id, execution_id, spec_digest, workspace_id,
                 dataset_manifest_digest, variant_id, receipt_digest,
                 sandbox_attestation_digest, verification_receipt_digest,
                 effect_audit_receipt_digest, receipt_json,
                 production_side_effects, benefit_eligible,
                 production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?)
                """,
                (
                    receipt.run_id,
                    claim.operation_id,
                    receipt.execution_id,
                    receipt.spec_digest,
                    receipt.workspace_id,
                    receipt.dataset_manifest_digest,
                    receipt.variant_id,
                    receipt.receipt_digest,
                    receipt.sandbox_attestation.receipt_digest,
                    receipt.verification_receipt.receipt_digest,
                    receipt.sandbox_attestation.effect_audit.receipt_digest,
                    encoded,
                    now_value,
                ),
            )
            sequence = int(row["current_event_sequence"]) + 1
            payload = {
                "fencing_token": claim.fencing_token,
                "receipt_digest": receipt.receipt_digest,
                "status": "COMPLETED",
            }
            event = _event_material(
                operation_id=claim.operation_id,
                sequence=sequence,
                event_type="COMPLETED",
                payload=payload,
                previous_digest=str(row["current_event_digest"]),
            )
            self._insert_event(
                conn,
                operation_id=claim.operation_id,
                event=event,
                created_at_ms=now_value,
            )
            updated = conn.execute(
                """
                UPDATE evolution_lab_trusted_operation_head
                SET status = 'COMPLETED',
                    receipt_digest = ?,
                    version = version + 1,
                    current_event_sequence = ?,
                    current_event_digest = ?,
                    updated_at_ms = ?
                WHERE operation_id = ?
                  AND status = 'RUNNING'
                  AND owner_id = ?
                  AND fencing_token = ?
                  AND version = ?
                """,
                (
                    receipt.receipt_digest,
                    sequence,
                    event["event_digest"],
                    now_value,
                    claim.operation_id,
                    claim.owner_id,
                    claim.fencing_token,
                    int(row["version"]),
                ),
            )
            if updated.rowcount != 1:
                raise TrustedEvolutionLabLedgerError(
                    "trusted completion lost operation authority"
                )
        return receipt

    def mark_reconciliation_required(
        self,
        *,
        claim: TrustedExecutionClaim,
        reason_code: str,
    ) -> TrustedExecutionClaim:
        reason = _text(reason_code, "reason_code", 120).upper()
        now_value = int(self._clock())
        with self._transaction() as conn:
            row = self._require_live_claim(conn, claim)
            if str(row["status"]) == "RECONCILIATION_REQUIRED":
                return self._claim_from_row(row)
            if str(row["status"]) != "RUNNING":
                raise TrustedEvolutionLabLedgerError(
                    "only a running trusted operation can require reconciliation"
                )
            return self._transition_to_reconciliation(
                conn,
                row=row,
                owner_id=claim.owner_id,
                reason_code=reason,
                now_value=now_value,
            )

    def append_report(
        self,
        report: TrustedExperimentReportV2,
    ) -> str:
        if not isinstance(report, TrustedExperimentReportV2):
            raise TrustedEvolutionLabLedgerError(
                "trusted report contract is invalid"
            )
        encoded = _canonical(report.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT report_json FROM evolution_lab_trusted_report
                WHERE report_id = ?
                """,
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["report_json"]) != encoded:
                    raise TrustedEvolutionLabLedgerError(
                        "trusted report replay drift"
                    )
                return report.report_digest
            if not report.promotion_eligible:
                raise TrustedEvolutionLabLedgerError(
                    "trusted report is not promotion-evidence eligible"
                )
            for run in report.runs:
                row = conn.execute(
                    """
                    SELECT receipt_digest, receipt_json
                    FROM evolution_lab_trusted_run_receipt
                    WHERE run_id = ?
                    """,
                    (run.run_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row["receipt_digest"]) != run.receipt_digest
                    or str(row["receipt_json"]) != _canonical(run.to_dict())
                ):
                    raise TrustedEvolutionLabLedgerError(
                        "trusted report references a non-authoritative run"
                    )
            conn.execute(
                """
                INSERT INTO evolution_lab_trusted_report
                (report_id, experiment_kind, report_digest, spec_digest, workspace_id,
                 report_json, promotion_eligible, review_required,
                 production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0, ?)
                """,
                (
                    report.report_id,
                    report.experiment_kind,
                    report.report_digest,
                    report.spec.spec_digest,
                    report.spec.workspace_id,
                    encoded,
                    int(self._clock()),
                ),
            )
        return report.report_digest

    def append_proposal(self, proposal: EvolutionProposalV2) -> str:
        if not isinstance(proposal, EvolutionProposalV2):
            raise TrustedEvolutionLabLedgerError(
                "trusted proposal contract is invalid"
            )
        encoded = _canonical(proposal.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT proposal_json FROM evolution_lab_trusted_proposal
                WHERE proposal_id = ?
                """,
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["proposal_json"]) != encoded:
                    raise TrustedEvolutionLabLedgerError(
                        "trusted proposal replay drift"
                    )
                return proposal.proposal_id
            if conn.execute(
                """
                SELECT 1 FROM evolution_lab_trusted_report
                WHERE report_digest = ? AND promotion_eligible = 1
                """,
                (proposal.report_digest,),
            ).fetchone() is None:
                raise TrustedEvolutionLabLedgerError(
                    "trusted proposal references a non-authoritative report"
                )
            conn.execute(
                """
                INSERT INTO evolution_lab_trusted_proposal
                (proposal_id, proposal_digest, report_digest, target_kind,
                 target_version, proposal_json, review_required,
                 production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.proposal_digest,
                    proposal.report_digest,
                    proposal.target_kind,
                    proposal.target_version,
                    encoded,
                    int(self._clock()),
                ),
            )
        return proposal.proposal_id

    def append_redaction_receipt(self, receipt: RedactionReceiptV1) -> str:
        if not isinstance(receipt, RedactionReceiptV1):
            raise TrustedEvolutionLabLedgerError(
                "redaction receipt contract is invalid"
            )
        encoded = _canonical(receipt.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT receipt_json FROM evolution_lab_redaction_receipt
                WHERE redaction_id = ?
                """,
                (receipt.redaction_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["receipt_json"]) != encoded:
                    raise TrustedEvolutionLabLedgerError(
                        "redaction receipt replay drift"
                    )
                return receipt.receipt_digest
            conn.execute(
                """
                INSERT INTO evolution_lab_redaction_receipt
                (redaction_id, workspace_id, source_digest,
                 output_dataset_digest, receipt_digest, receipt_json,
                 created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.redaction_id,
                    receipt.workspace_id,
                    receipt.source_digest,
                    receipt.output_dataset_digest,
                    receipt.receipt_digest,
                    encoded,
                    receipt.created_at_ms,
                ),
            )
        return receipt.receipt_digest

    def append_golden_suite(
        self, receipt: GoldenExperimentSuiteReceiptV1
    ) -> str:
        if not isinstance(receipt, GoldenExperimentSuiteReceiptV1):
            raise TrustedEvolutionLabLedgerError(
                "golden suite receipt contract is invalid"
            )
        encoded = _canonical(receipt.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT receipt_json FROM evolution_lab_golden_suite_receipt
                WHERE suite_id = ?
                """,
                (receipt.suite_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["receipt_json"]) != encoded:
                    raise TrustedEvolutionLabLedgerError(
                        "golden suite receipt replay drift"
                    )
                return receipt.receipt_digest
            for report_digest in receipt.report_digests.values():
                if conn.execute(
                    """
                    SELECT 1 FROM evolution_lab_trusted_report
                    WHERE report_digest = ? AND promotion_eligible = 1
                    """,
                    (report_digest,),
                ).fetchone() is None:
                    raise TrustedEvolutionLabLedgerError(
                        "golden suite references a missing trusted report"
                    )
            authoritative_runs: set[str] = set()
            for experiment_kind, report_digest in receipt.report_digests.items():
                report_row = conn.execute(
                    """
                    SELECT report_json
                    FROM evolution_lab_trusted_report
                    WHERE experiment_kind = ? AND report_digest = ?
                    """,
                    (experiment_kind, report_digest),
                ).fetchone()
                if report_row is None:
                    raise TrustedEvolutionLabLedgerError(
                        "golden suite experiment kind/report binding drift"
                    )
                report_payload = json.loads(str(report_row["report_json"]))
                run_payloads = report_payload.get("runs", ())
                run_digests = report_payload.get("run_receipt_digests", ())
                if (
                    not isinstance(run_payloads, list)
                    or not isinstance(run_digests, list)
                    or len(run_payloads) != len(run_digests)
                ):
                    raise TrustedEvolutionLabLedgerError(
                        "golden suite report receipt index is corrupt"
                    )
                for run_payload, run_digest_value in zip(
                    run_payloads, run_digests, strict=True
                ):
                    if not isinstance(run_payload, Mapping):
                        raise TrustedEvolutionLabLedgerError(
                            "golden suite report run payload is corrupt"
                        )
                    run_digest = str(run_digest_value or "")
                    sandbox = run_payload.get("sandbox_attestation")
                    if (
                        not run_digest
                        or not isinstance(sandbox, Mapping)
                        or str(sandbox.get("engine_identity"))
                        != receipt.engine_identity
                        or str(sandbox.get("image_reference"))
                        != receipt.image_reference
                    ):
                        raise TrustedEvolutionLabLedgerError(
                            "golden suite runtime evidence binding drift"
                        )
                    authoritative_runs.add(run_digest)
            if authoritative_runs != set(receipt.run_receipt_digests):
                raise TrustedEvolutionLabLedgerError(
                    "golden suite arm receipt set drift"
                )
            conn.execute(
                """
                INSERT INTO evolution_lab_golden_suite_receipt
                (suite_id, commit_sha, migration_head, receipt_digest,
                 receipt_json, status, production_side_effects,
                 production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, 'PASS', 0, 0, ?)
                """,
                (
                    receipt.suite_id,
                    receipt.commit_sha,
                    receipt.migration_head,
                    receipt.receipt_digest,
                    encoded,
                    receipt.created_at_ms,
                ),
            )
        return receipt.receipt_digest

    def _transition_to_reconciliation(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        owner_id: str,
        reason_code: str,
        now_value: int,
    ) -> TrustedExecutionClaim:
        sequence = int(row["current_event_sequence"]) + 1
        fence = int(row["fencing_token"]) + 1
        payload = {
            "fencing_token": fence,
            "owner_id": owner_id,
            "reason_code": reason_code,
            "status": "RECONCILIATION_REQUIRED",
        }
        event = _event_material(
            operation_id=str(row["operation_id"]),
            sequence=sequence,
            event_type="RECONCILIATION_REQUIRED",
            payload=payload,
            previous_digest=str(row["current_event_digest"]),
        )
        self._insert_event(
            conn,
            operation_id=str(row["operation_id"]),
            event=event,
            created_at_ms=now_value,
        )
        updated = conn.execute(
            """
            UPDATE evolution_lab_trusted_operation_head
            SET status = 'RECONCILIATION_REQUIRED',
                owner_id = ?,
                fencing_token = ?,
                lease_expires_at_ms = ?,
                version = version + 1,
                current_event_sequence = ?,
                current_event_digest = ?,
                updated_at_ms = ?
            WHERE operation_id = ?
              AND version = ?
            """,
            (
                owner_id,
                fence,
                now_value,
                sequence,
                event["event_digest"],
                now_value,
                str(row["operation_id"]),
                int(row["version"]),
            ),
        )
        if updated.rowcount != 1:
            raise TrustedEvolutionLabLedgerError(
                "trusted reconciliation transition lost authority"
            )
        current = conn.execute(
            """
            SELECT * FROM evolution_lab_trusted_operation_head
            WHERE operation_id = ?
            """,
            (str(row["operation_id"]),),
        ).fetchone()
        if current is None:
            raise TrustedEvolutionLabLedgerError(
                "trusted reconciliation head did not reopen"
            )
        return self._claim_from_row(current)

    def _require_live_claim(
        self,
        conn: sqlite3.Connection,
        claim: TrustedExecutionClaim,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM evolution_lab_trusted_operation_head
            WHERE operation_id = ?
            """,
            (claim.operation_id,),
        ).fetchone()
        if row is None:
            raise TrustedEvolutionLabLedgerError(
                "trusted operation claim does not exist"
            )
        self._assert_operation_chain(conn, row)
        if (
            str(row["execution_id"]) != claim.execution_id
            or str(row["owner_id"]) != claim.owner_id
            or int(row["fencing_token"]) != claim.fencing_token
            or str(row["spec_digest"]) != claim.spec_digest
        ):
            raise TrustedEvolutionLabLedgerError(
                "trusted operation claim is stale or drifted"
            )
        return row

    @staticmethod
    def _require_registered_spec(
        conn: sqlite3.Connection,
        spec: ExperimentSpecV2,
    ) -> None:
        row = conn.execute(
            """
            SELECT spec_json, workspace_id, dataset_manifest_digest
            FROM evolution_lab_v2_experiment_spec
            WHERE spec_digest = ?
            """,
            (spec.spec_digest,),
        ).fetchone()
        if (
            row is None
            or str(row["spec_json"]) != _canonical(spec.to_dict())
            or str(row["workspace_id"]) != spec.workspace_id
            or str(row["dataset_manifest_digest"])
            != spec.dataset_manifest.manifest_digest
        ):
            raise TrustedEvolutionLabLedgerError(
                "trusted execution requires an exact registered V2 spec"
            )

    @staticmethod
    def _assert_operation_identity(
        row: sqlite3.Row,
        *,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        execution_id: str,
        container_name: str,
    ) -> None:
        if (
            str(row["execution_id"]) != execution_id
            or str(row["spec_digest"]) != spec.spec_digest
            or str(row["workspace_id"]) != spec.workspace_id
            or str(row["dataset_manifest_digest"])
            != spec.dataset_manifest.manifest_digest
            or str(row["variant_id"]) != variant.variant_id
            or str(row["container_name"]) != container_name
            or str(row["status"]) not in _OPERATION_STATUSES
        ):
            raise TrustedEvolutionLabLedgerError(
                "trusted operation exact identity drift"
            )

    def _assert_operation_chain(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> None:
        events = conn.execute(
            """
            SELECT * FROM evolution_lab_trusted_operation_event
            WHERE operation_id = ?
            ORDER BY event_sequence
            """,
            (str(row["operation_id"]),),
        ).fetchall()
        if not events:
            raise TrustedEvolutionLabLedgerError(
                "trusted operation event chain is missing"
            )
        previous: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            payload_json = str(event["payload_json"])
            payload_digest = _sha256_hex(payload_json)
            event_digest = _event_digest(
                operation_id=str(row["operation_id"]),
                sequence=expected_sequence,
                event_type=str(event["event_type"]),
                payload_digest=payload_digest,
                previous_digest=previous,
            )
            if (
                int(event["event_sequence"]) != expected_sequence
                or str(event["payload_digest"]) != payload_digest
                or (
                    None
                    if event["previous_event_digest"] is None
                    else str(event["previous_event_digest"])
                )
                != previous
                or str(event["event_digest"]) != event_digest
            ):
                raise TrustedEvolutionLabLedgerError(
                    "trusted operation event chain is corrupt"
                )
            previous = event_digest
        latest = events[-1]
        if (
            int(row["current_event_sequence"]) != len(events)
            or str(row["current_event_digest"]) != str(latest["event_digest"])
            or str(row["status"]) != str(latest["event_type"])
        ):
            raise TrustedEvolutionLabLedgerError(
                "trusted operation head/event identity drift"
            )

    @staticmethod
    def _claim_from_row(
        row: sqlite3.Row,
        *,
        replayed: bool = False,
        receipt: TrustedExperimentRunReceiptV2 | None = None,
    ) -> TrustedExecutionClaim:
        return TrustedExecutionClaim(
            operation_id=str(row["operation_id"]),
            execution_id=str(row["execution_id"]),
            spec_digest=str(row["spec_digest"]),
            workspace_id=str(row["workspace_id"]),
            dataset_manifest_digest=str(row["dataset_manifest_digest"]),
            variant_id=str(row["variant_id"]),
            container_name=str(row["container_name"]),
            owner_id=str(row["owner_id"]),
            fencing_token=int(row["fencing_token"]),
            lease_expires_at_ms=int(row["lease_expires_at_ms"]),
            status=str(row["status"]),
            replayed=replayed,
            receipt=receipt,
        )

    @staticmethod
    def _load_receipt_for_operation(
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> TrustedExperimentRunReceiptV2:
        row = conn.execute(
            """
            SELECT receipt_json, receipt_digest
            FROM evolution_lab_trusted_run_receipt
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            raise TrustedEvolutionLabLedgerError(
                "completed trusted operation lacks its receipt"
            )
        try:
            payload = json.loads(str(row["receipt_json"]))
        except (TypeError, ValueError) as exc:
            raise TrustedEvolutionLabLedgerError(
                "trusted receipt JSON is corrupt"
            ) from exc
        receipt = TrustedExperimentRunReceiptV2.from_dict(payload)
        if receipt.receipt_digest != str(row["receipt_digest"]):
            raise TrustedEvolutionLabLedgerError(
                "trusted receipt digest is corrupt"
            )
        return receipt

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        event: Mapping[str, object],
        created_at_ms: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO evolution_lab_trusted_operation_event
            (event_id, operation_id, event_sequence, event_type,
             payload_json, payload_digest, previous_event_digest,
             event_digest, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lab-trusted-event-" + str(event["event_digest"])[:48],
                operation_id,
                int(event["sequence"]),
                str(event["event_type"]),
                str(event["payload_json"]),
                str(event["payload_digest"]),
                event["previous_digest"],
                str(event["event_digest"]),
                created_at_ms,
            ),
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function(
            "natsume_sha256_hex", 1, _sha256_hex, deterministic=True
        )
        conn.create_function(
            "natsume_evolution_lab_write_authorized",
            0,
            lambda: 1,
        )
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


def _event_material(
    *,
    operation_id: str,
    sequence: int,
    event_type: str,
    payload: Mapping[str, object],
    previous_digest: str | None,
) -> dict[str, object]:
    payload_json = _canonical(payload)
    payload_digest = _sha256_hex(payload_json)
    return {
        "sequence": sequence,
        "event_type": event_type,
        "payload_json": payload_json,
        "payload_digest": payload_digest,
        "previous_digest": previous_digest,
        "event_digest": _event_digest(
            operation_id=operation_id,
            sequence=sequence,
            event_type=event_type,
            payload_digest=payload_digest,
            previous_digest=previous_digest,
        ),
    }


def _event_digest(
    *,
    operation_id: str,
    sequence: int,
    event_type: str,
    payload_digest: str,
    previous_digest: str | None,
) -> str:
    return _sha256_hex(
        "|".join(
            (
                operation_id,
                str(sequence),
                event_type,
                payload_digest,
                previous_digest or "",
            )
        )
    )


def _sha256_hex(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _text(value: object, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise TrustedEvolutionLabLedgerError(
            f"{field} must be non-empty and bounded"
        )
    return normalized


__all__ = [
    "EVOLUTION_LAB_TRUSTED_MIGRATION_ASSET_KEY",
    "SQLiteTrustedEvolutionLabLedger",
    "TrustedEvolutionLabLedgerError",
    "TrustedExecutionClaim",
    "trusted_execution_id",
]
