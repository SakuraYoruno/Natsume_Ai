"""Fail-closed source resolver for PlanningSearch cohort evidence."""

from __future__ import annotations

from typing import Any

from host_brain.contracts.planning_search.calibration import (
    CalibrationRepository,
)
from host_brain.contracts.planning_search.code_benchmark_ledger import (
    CodeHistoricalBenchmarkLedger,
)
from host_brain.contracts.planning_search.code_benefit_cohort import (
    CodeCohortPairedEvidenceV1,
)
from host_brain.contracts.planning_search.cohort_ledger import (
    CohortAuthorityProofV1,
    PentestExperimentReceiptAuthority,
    canonical_json,
    json_digest,
)
from host_brain.contracts.planning_search.pentest_calibration_cohort import (
    PentestExperimentRunEvidenceV1,
)
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchCohortPairedEvidenceV1,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    SQLiteResearchShadowLedger,
)


COHORT_AUTHORITY_RESOLVER_ID = (
    "sqlite-planning-search-source-authority-v1"
)


class PlanningSearchCohortAuthorityResolutionError(RuntimeError):
    """A caller-submitted cohort member differs from durable source truth."""


class SQLitePlanningSearchCohortAuthorityResolver:
    """Reopen lower ledgers before a member enters the cohort authority.

    Research and Code evidence must reproduce an already durable terminal
    receipt.  Pentest evidence must reproduce an immutable calibration record
    and three independently resolved task, authorization and verification
    receipts.  No method writes or grants production influence.
    """

    def __init__(
        self,
        *,
        research_ledger: SQLiteResearchShadowLedger,
        code_ledger: CodeHistoricalBenchmarkLedger,
        calibration_repository: CalibrationRepository,
        pentest_receipt_authority: PentestExperimentReceiptAuthority,
    ) -> None:
        for label, value in (
            ("research_ledger", research_ledger),
            ("code_ledger", code_ledger),
            ("calibration_repository", calibration_repository),
            ("pentest_receipt_authority", pentest_receipt_authority),
        ):
            if value is None:
                raise PlanningSearchCohortAuthorityResolutionError(
                    f"{label} is required"
                )
        self._research_ledger = research_ledger
        self._code_ledger = code_ledger
        self._calibration_repository = calibration_repository
        self._pentest_receipt_authority = pentest_receipt_authority

    def resolve_research(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: ResearchCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1:
        reopened = self._research_ledger.get_terminal_receipt(
            run_id=evidence.run_id
        )
        if (
            reopened is None
            or reopened.run_id != evidence.run_id
            or reopened.receipt_digest != evidence.terminal_receipt_digest
            or canonical_json(reopened.receipt.to_dict())
            != canonical_json(evidence.terminal_receipt.to_dict())
        ):
            raise PlanningSearchCohortAuthorityResolutionError(
                "Research terminal receipt is missing or drifted"
            )
        return self._proof(
            domain="RESEARCH",
            cohort_id=cohort_id,
            cohort_version=cohort_version,
            evidence=evidence,
            authority_pairs=(
                (reopened.receipt_ref, reopened.receipt_digest),
            ),
        )

    def resolve_code(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: CodeCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1:
        reopened = self._code_ledger.get_completed(evidence.run_id)
        if (
            reopened is None
            or reopened.spec.run_id != evidence.run_id
            or json_digest(reopened.to_dict())
            != evidence.terminal_receipt_digest
            or canonical_json(reopened.to_dict())
            != canonical_json(evidence.terminal_receipt.to_dict())
        ):
            raise PlanningSearchCohortAuthorityResolutionError(
                "Code terminal receipt is missing or drifted"
            )
        return self._proof(
            domain="CODE",
            cohort_id=cohort_id,
            cohort_version=cohort_version,
            evidence=evidence,
            authority_pairs=(
                (reopened.receipt_ref, reopened.receipt_digest),
            ),
        )

    def resolve_pentest(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: PentestExperimentRunEvidenceV1,
    ) -> CohortAuthorityProofV1:
        record = self._calibration_repository.get(
            evidence.prediction.prediction_id
        )
        if (
            record is None
            or record.outcome is None
            or canonical_json(record.prediction.to_dict())
            != canonical_json(evidence.prediction.to_dict())
            or canonical_json(record.outcome.to_dict())
            != canonical_json(evidence.outcome.to_dict())
        ):
            raise PlanningSearchCohortAuthorityResolutionError(
                "Pentest calibration record is missing or drifted"
            )
        receipt_pairs = (
            (
                evidence.task_receipt_ref,
                evidence.task_receipt_digest,
            ),
            (
                evidence.authorization_receipt_ref,
                evidence.authorization_receipt_digest,
            ),
            (
                evidence.verification_receipt_ref,
                evidence.verification_receipt_digest,
            ),
        )
        for receipt_ref, expected_digest in receipt_pairs:
            resolved = self._pentest_receipt_authority.resolve_receipt_digest(
                receipt_ref=receipt_ref
            )
            if resolved != expected_digest:
                raise PlanningSearchCohortAuthorityResolutionError(
                    "Pentest terminal authority receipt is missing or drifted"
                )
        calibration_pairs = (
            (
                "planning-calibration-prediction://sha256/"
                + record.prediction.identity_digest,
                record.prediction.identity_digest,
            ),
            (
                "planning-calibration-outcome://sha256/"
                + record.outcome.outcome_digest,
                record.outcome.outcome_digest,
            ),
        )
        return self._proof(
            domain="PENTEST",
            cohort_id=cohort_id,
            cohort_version=cohort_version,
            evidence=evidence,
            authority_pairs=(*receipt_pairs, *calibration_pairs),
        )

    @staticmethod
    def _proof(
        *,
        domain: str,
        cohort_id: str,
        cohort_version: str,
        evidence: Any,
        authority_pairs: tuple[tuple[str, str], ...],
    ) -> CohortAuthorityProofV1:
        ordered = tuple(sorted(authority_pairs, key=lambda item: item[0]))
        return CohortAuthorityProofV1(
            resolver_id=COHORT_AUTHORITY_RESOLVER_ID,
            domain=domain,
            cohort_id=cohort_id,
            cohort_version=cohort_version,
            workload_id=evidence.workload_id,
            run_id=evidence.run_id,
            evidence_digest=json_digest(evidence.to_dict()),
            authority_refs=tuple(item[0] for item in ordered),
            authority_digests=tuple(item[1] for item in ordered),
        )


__all__ = [
    "COHORT_AUTHORITY_RESOLVER_ID",
    "PlanningSearchCohortAuthorityResolutionError",
    "SQLitePlanningSearchCohortAuthorityResolver",
]
