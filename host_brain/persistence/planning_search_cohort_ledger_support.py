"""Pure validation helpers for the PlanningSearch cohort SQLite owner."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.planning_search.cohort_ledger import (
    PLANNING_SEARCH_COHORT_SIZES,
    CohortAuthorityProofV1,
    PlanningSearchCohortContractError,
    canonical_json,
    json_digest,
)


PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY = (
    "0136#planning_search_cohort_ledger"
)
PLANNING_SEARCH_COHORT_MIGRATION_SHA256 = (
    "e619c3a03d38067ac0b524e562606c805fefc1eec69877f16f28db22071c98c2"
)

COHORT_INVALID = "PLANNING_SEARCH_COHORT_INVALID"
COHORT_NOT_FOUND = "PLANNING_SEARCH_COHORT_NOT_FOUND"
COHORT_REPLAY_DRIFT = "PLANNING_SEARCH_COHORT_REPLAY_DRIFT"
COHORT_SCHEMA_NOT_READY = "PLANNING_SEARCH_COHORT_SCHEMA_NOT_READY"
COHORT_SOURCE_AUTHORITY_REJECTED = (
    "PLANNING_SEARCH_COHORT_SOURCE_AUTHORITY_REJECTED"
)
COHORT_PROMOTION_NOT_ELIGIBLE = (
    "PLANNING_SEARCH_COHORT_PROMOTION_NOT_ELIGIBLE"
)
COHORT_STORE_UNAVAILABLE = "PLANNING_SEARCH_COHORT_STORE_UNAVAILABLE"

ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
REQUIRED_TABLES = frozenset(
    {
        "planning_search_cohort_registration",
        "planning_search_cohort_member_evidence",
        "planning_search_cohort_report",
        "planning_search_promotion_evidence",
        "planning_search_promotion_proposal",
    }
)
REQUIRED_TRIGGERS = frozenset(
    {
        "planning_cohort_member_registration_bound",
        "planning_cohort_report_complete",
        "planning_promotion_proposal_evidence_complete",
        "planning_cohort_registration_no_update",
        "planning_cohort_registration_no_delete",
        "planning_cohort_member_no_update",
        "planning_cohort_member_no_delete",
        "planning_cohort_report_no_update",
        "planning_cohort_report_no_delete",
        "planning_promotion_evidence_no_update",
        "planning_promotion_evidence_no_delete",
        "planning_promotion_proposal_no_update",
        "planning_promotion_proposal_no_delete",
    }
)


class PlanningSearchCohortLedgerError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or COHORT_INVALID)
        self.detail = str(detail or "PlanningSearch cohort ledger failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class PlanningSearchCohortLedgerSupport:
    """Read validation only; the concrete repository retains every writer."""

    def _get_member(
        self,
        *,
        domain: str,
        cohort_id: str,
        cohort_version: str,
        workload_id: str,
    ) -> sqlite3.Row | None:
        try:
            with self._connection() as conn:
                return conn.execute(
                    """
                    SELECT *
                    FROM planning_search_cohort_member_evidence
                    WHERE domain = ? AND cohort_id = ?
                      AND cohort_version = ? AND workload_id = ?
                    """,
                    (
                        domain,
                        cohort_id,
                        cohort_version,
                        workload_id,
                    ),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise self._store_error("cohort member cannot be read", exc)

    def _existing_member_proof(
        self,
        row: sqlite3.Row,
        evidence: Any,
        evidence_digest: str,
        evidence_json: str,
    ) -> CohortAuthorityProofV1:
        if not self._member_matches(
            row,
            evidence,
            evidence_digest,
            evidence_json,
        ):
            raise PlanningSearchCohortLedgerError(
                COHORT_REPLAY_DRIFT,
                "cohort member replay drift",
            )
        proof_payload = canonical_object(
            row["authority_proof_json"],
            "cohort authority proof",
        )
        try:
            proof = CohortAuthorityProofV1.from_dict(proof_payload)
        except PlanningSearchCohortContractError as exc:
            raise PlanningSearchCohortLedgerError(
                COHORT_REPLAY_DRIFT,
                "cohort authority proof is invalid",
            ) from exc
        if proof.proof_digest != str(row["authority_proof_digest"]):
            raise PlanningSearchCohortLedgerError(
                COHORT_REPLAY_DRIFT,
                "cohort authority proof digest drift",
            )
        return proof

    @staticmethod
    def _member_matches(
        row: sqlite3.Row,
        evidence: Any,
        evidence_digest: str,
        evidence_json: str,
    ) -> bool:
        return (
            str(row["run_id"]) == evidence.run_id
            and str(row["evidence_digest"]) == evidence_digest
            and str(row["evidence_json"]) == evidence_json
            and int(row["production_influence_enabled"]) == 0
        )

    @staticmethod
    def _require_spec_member(spec: Any, evidence: Any) -> None:
        matches = [
            item
            for item in spec.workloads
            if item.workload_id == evidence.workload_id
        ]
        if len(matches) != 1 or matches[0].run_id != evidence.run_id:
            raise PlanningSearchCohortLedgerError(
                COHORT_INVALID,
                "cohort member is not in the exact preregistered spec",
            )

    @staticmethod
    def _require_proof(
        *,
        proof: CohortAuthorityProofV1,
        domain: str,
        spec: Any,
        evidence: Any,
        evidence_digest: str,
    ) -> None:
        if not isinstance(proof, CohortAuthorityProofV1) or (
            proof.domain != domain
            or proof.cohort_id != spec.cohort_id
            or proof.cohort_version != spec.cohort_version
            or proof.workload_id != evidence.workload_id
            or proof.run_id != evidence.run_id
            or proof.evidence_digest != evidence_digest
        ):
            raise PlanningSearchCohortLedgerError(
                COHORT_SOURCE_AUTHORITY_REJECTED,
                "source authority proof does not bind the cohort member",
            )

    @staticmethod
    def _registration_matches(
        row: sqlite3.Row,
        domain: str,
        spec: Any,
        spec_json: str,
    ) -> bool:
        return (
            str(row["domain"]) == domain
            and str(row["cohort_id"]) == spec.cohort_id
            and str(row["cohort_version"]) == spec.cohort_version
            and str(row["spec_digest"]) == spec.spec_digest
            and int(row["expected_member_count"])
            == PLANNING_SEARCH_COHORT_SIZES[domain]
            and str(row["spec_json"]) == spec_json
            and int(row["production_influence_enabled"]) == 0
        )

    def _require_registration_exact(
        self,
        conn: sqlite3.Connection,
        domain: str,
        spec: Any,
    ) -> None:
        row = conn.execute(
            """
            SELECT *
            FROM planning_search_cohort_registration
            WHERE domain = ? AND cohort_id = ? AND cohort_version = ?
            """,
            (domain, spec.cohort_id, spec.cohort_version),
        ).fetchone()
        if row is None or not self._registration_matches(
            row,
            domain,
            spec,
            canonical_json(spec.to_dict()),
        ):
            raise PlanningSearchCohortLedgerError(
                COHORT_NOT_FOUND,
                "exact cohort registration is missing",
            )

    @staticmethod
    def _report_flags(domain: str, report: Any) -> dict[str, bool]:
        if domain in {"RESEARCH", "CODE"}:
            return {
                "benefit_eligible": report.benefit_eligible,
                "calibration_eligible": False,
                "proposal_eligible": report.proposal_eligible,
            }
        return {
            "benefit_eligible": False,
            "calibration_eligible": report.calibration_eligible,
            "proposal_eligible": report.proposal_input_eligible,
        }

    @staticmethod
    def _report_matches(
        row: sqlite3.Row,
        report: Any,
        report_json: str,
        evidence_set_digest: str,
        flags: Mapping[str, bool],
    ) -> bool:
        return (
            str(row["report_digest"]) == report.report_digest
            and str(row["spec_digest"]) == report.spec_digest
            and str(row["evidence_set_digest"]) == evidence_set_digest
            and str(row["report_json"]) == report_json
            and bool(row["benefit_eligible"])
            is flags["benefit_eligible"]
            and bool(row["calibration_eligible"])
            is flags["calibration_eligible"]
            and bool(row["proposal_eligible"])
            is flags["proposal_eligible"]
            and int(row["production_influence_enabled"]) == 0
        )

    def _verify_evidence_set(
        self,
        conn: sqlite3.Connection,
        report_row: sqlite3.Row,
    ) -> None:
        rows = conn.execute(
            """
            SELECT workload_id, run_id, evidence_digest,
                   authority_proof_digest
            FROM planning_search_cohort_member_evidence
            WHERE domain = ? AND cohort_id = ? AND cohort_version = ?
            ORDER BY workload_id
            """,
            (
                report_row["domain"],
                report_row["cohort_id"],
                report_row["cohort_version"],
            ),
        ).fetchall()
        payload = [
            {
                "workload_id": str(row["workload_id"]),
                "run_id": str(row["run_id"]),
                "evidence_digest": str(row["evidence_digest"]),
                "authority_proof_digest": str(
                    row["authority_proof_digest"]
                ),
            }
            for row in rows
        ]
        if json_digest(payload) != str(report_row["evidence_set_digest"]):
            raise PlanningSearchCohortLedgerError(
                COHORT_REPLAY_DRIFT,
                "cohort report evidence-set digest drift",
            )

    def _require_report_exact(
        self,
        conn: sqlite3.Connection,
        domain: str,
        report: Any,
    ) -> None:
        row = conn.execute(
            """
            SELECT *
            FROM planning_search_cohort_report
            WHERE domain = ? AND report_digest = ?
            """,
            (domain, report.report_digest),
        ).fetchone()
        if row is None:
            raise PlanningSearchCohortLedgerError(
                COHORT_NOT_FOUND,
                f"{domain} promotion report is not durable",
            )
        self._verify_evidence_set(conn, row)
        if (
            str(row["report_json"]) != canonical_json(report.to_dict())
            or str(row["spec_digest"]) != report.spec_digest
            or int(row["production_influence_enabled"]) != 0
        ):
            raise PlanningSearchCohortLedgerError(
                COHORT_REPLAY_DRIFT,
                f"{domain} promotion report drift",
            )

    @staticmethod
    def _promotion_evidence(
        conn: sqlite3.Connection,
        proposal_id: str,
    ) -> list[dict[str, str]]:
        rows = conn.execute(
            """
            SELECT domain, report_digest, report_ref
            FROM planning_search_promotion_evidence
            WHERE proposal_id = ?
            ORDER BY domain
            """,
            (proposal_id,),
        ).fetchall()
        return [
            {
                "domain": str(row["domain"]),
                "report_digest": str(row["report_digest"]),
                "report_ref": str(row["report_ref"]),
            }
            for row in rows
        ]

    def _timestamp(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise self._invalid("clock returned an invalid timestamp")
        return value

    @staticmethod
    def _invalid(detail: str) -> PlanningSearchCohortLedgerError:
        return PlanningSearchCohortLedgerError(COHORT_INVALID, detail)

    @staticmethod
    def _store_error(
        detail: str,
        exc: BaseException,
    ) -> PlanningSearchCohortLedgerError:
        _ = exc
        return PlanningSearchCohortLedgerError(
            COHORT_STORE_UNAVAILABLE,
            detail,
        )


def canonical_object(value: Any, label: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PlanningSearchCohortLedgerError(
            COHORT_REPLAY_DRIFT,
            f"{label} is not valid JSON",
        ) from exc
    if not isinstance(parsed, Mapping) or canonical_json(parsed) != str(value):
        raise PlanningSearchCohortLedgerError(
            COHORT_REPLAY_DRIFT,
            f"{label} is not canonical",
        )
    return parsed


def report_ref(report_digest: str) -> str:
    return f"planning-search-report://sha256/{report_digest}"


__all__ = [
    "ALLOWED_DISPOSITIONS",
    "COHORT_INVALID",
    "COHORT_NOT_FOUND",
    "COHORT_PROMOTION_NOT_ELIGIBLE",
    "COHORT_REPLAY_DRIFT",
    "COHORT_SCHEMA_NOT_READY",
    "COHORT_SOURCE_AUTHORITY_REJECTED",
    "COHORT_STORE_UNAVAILABLE",
    "PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY",
    "PLANNING_SEARCH_COHORT_MIGRATION_SHA256",
    "REQUIRED_TABLES",
    "REQUIRED_TRIGGERS",
    "PlanningSearchCohortLedgerError",
    "PlanningSearchCohortLedgerSupport",
    "canonical_object",
    "report_ref",
]
