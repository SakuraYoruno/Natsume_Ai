"""Append-only SQLite owner for fixed PlanningSearch cohort evidence."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Sequence

from host_brain.contracts.planning_search.code_benefit_cohort import (
    CodeBenefitCohortEvaluator,
    CodeBenefitCohortReportV1,
    CodeBenefitCohortSpecV1,
    CodeCohortPairedEvidenceV1,
)
from host_brain.contracts.planning_search.cohort_ledger import (
    PLANNING_SEARCH_COHORT_SIZES,
    CohortAuthorityProofV1,
    CohortReportSnapshotV1,
    PlanningSearchCohortAuthorityResolver,
    canonical_json,
    cohort_domain,
    json_digest,
    required_text,
)
from host_brain.contracts.planning_search.pentest_calibration_cohort import (
    PentestCalibrationCohortEvaluator,
    PentestCalibrationCohortReportV1,
    PentestCalibrationCohortSpecV1,
    PentestExperimentRunEvidenceV1,
)
from host_brain.contracts.planning_search.promotion_gate import (
    PlanningSearchPromotionGate,
    PlanningSearchPromotionProposalV1,
)
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchBenefitCohortEvaluator,
    ResearchBenefitCohortReportV1,
    ResearchBenefitCohortSpecV1,
    ResearchCohortPairedEvidenceV1,
)
from host_brain.core.ids import now_ms
from host_brain.persistence.planning_search_cohort_ledger_support import (
    ALLOWED_DISPOSITIONS as _ALLOWED_DISPOSITIONS,
    COHORT_INVALID,
    COHORT_NOT_FOUND,
    COHORT_PROMOTION_NOT_ELIGIBLE,
    COHORT_REPLAY_DRIFT,
    COHORT_SCHEMA_NOT_READY,
    COHORT_SOURCE_AUTHORITY_REJECTED,
    COHORT_STORE_UNAVAILABLE,
    PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY,
    PLANNING_SEARCH_COHORT_MIGRATION_SHA256,
    REQUIRED_TABLES as _REQUIRED_TABLES,
    REQUIRED_TRIGGERS as _REQUIRED_TRIGGERS,
    PlanningSearchCohortLedgerError,
    PlanningSearchCohortLedgerSupport,
    canonical_object as _canonical_object,
    report_ref as _report_ref,
)


class SQLitePlanningSearchCohortLedger(PlanningSearchCohortLedgerSupport):
    """Single writer for fixed cohort registrations, members and reports."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        authority_resolver: PlanningSearchCohortAuthorityResolver,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise PlanningSearchCohortLedgerError(
                COHORT_INVALID,
                "cohort ledger requires a durable SQLite file path",
            )
        if authority_resolver is None:
            raise PlanningSearchCohortLedgerError(
                COHORT_INVALID,
                "cohort ledger requires an explicit source authority resolver",
            )
        if not callable(clock):
            raise PlanningSearchCohortLedgerError(
                COHORT_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._authority_resolver = authority_resolver
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise PlanningSearchCohortLedgerError(
                COHORT_SCHEMA_NOT_READY,
                "cohort ledger database does not exist",
            )
        try:
            with self._connection() as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY,),
                ).fetchone()
                rows = conn.execute(
                    """
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE type IN ('table', 'trigger')
                    """
                ).fetchall()
                foreign_keys = int(
                    conn.execute("PRAGMA foreign_keys").fetchone()[0]
                )
        except sqlite3.DatabaseError as exc:
            raise PlanningSearchCohortLedgerError(
                COHORT_SCHEMA_NOT_READY,
                "cohort schema cannot be inspected",
            ) from exc
        tables = {
            str(row["name"])
            for row in rows
            if str(row["type"]) == "table"
        }
        triggers = {
            str(row["name"])
            for row in rows
            if str(row["type"]) == "trigger"
        }
        proposal_trigger = next(
            (
                str(row["sql"] or "")
                for row in rows
                if str(row["name"])
                == "planning_promotion_proposal_evidence_complete"
            ),
            "",
        )
        normalized_trigger = "".join(proposal_trigger.upper().split())
        if (
            asset is None
            or str(asset["checksum"])
            != PLANNING_SEARCH_COHORT_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
            or not _REQUIRED_TABLES.issubset(tables)
            or not _REQUIRED_TRIGGERS.issubset(triggers)
            or foreign_keys != 1
            or "REPORT.CALIBRATION_ELIGIBLE=1" not in normalized_trigger
            or "REPORT.BENEFIT_ELIGIBLE=1" not in normalized_trigger
        ):
            raise PlanningSearchCohortLedgerError(
                COHORT_SCHEMA_NOT_READY,
                "migration 0136 cohort authority is missing or drifted",
            )

    def register_research(
        self,
        spec: ResearchBenefitCohortSpecV1,
    ) -> str:
        if not isinstance(spec, ResearchBenefitCohortSpecV1):
            raise self._invalid("Research cohort spec is invalid")
        return self._register("RESEARCH", spec)

    def register_code(self, spec: CodeBenefitCohortSpecV1) -> str:
        if not isinstance(spec, CodeBenefitCohortSpecV1):
            raise self._invalid("Code cohort spec is invalid")
        return self._register("CODE", spec)

    def register_pentest(
        self,
        spec: PentestCalibrationCohortSpecV1,
    ) -> str:
        if not isinstance(spec, PentestCalibrationCohortSpecV1):
            raise self._invalid("Pentest cohort spec is invalid")
        return self._register("PENTEST", spec)

    def append_research_member(
        self,
        *,
        spec: ResearchBenefitCohortSpecV1,
        evidence: ResearchCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1:
        if not isinstance(spec, ResearchBenefitCohortSpecV1) or not isinstance(
            evidence,
            ResearchCohortPairedEvidenceV1,
        ):
            raise self._invalid("Research cohort member is invalid")
        return self._append_member("RESEARCH", spec, evidence)

    def append_code_member(
        self,
        *,
        spec: CodeBenefitCohortSpecV1,
        evidence: CodeCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1:
        if not isinstance(spec, CodeBenefitCohortSpecV1) or not isinstance(
            evidence,
            CodeCohortPairedEvidenceV1,
        ):
            raise self._invalid("Code cohort member is invalid")
        return self._append_member("CODE", spec, evidence)

    def append_pentest_member(
        self,
        *,
        spec: PentestCalibrationCohortSpecV1,
        evidence: PentestExperimentRunEvidenceV1,
    ) -> CohortAuthorityProofV1:
        if not isinstance(spec, PentestCalibrationCohortSpecV1) or not isinstance(
            evidence,
            PentestExperimentRunEvidenceV1,
        ):
            raise self._invalid("Pentest cohort member is invalid")
        return self._append_member("PENTEST", spec, evidence)

    def seal_research(
        self,
        *,
        spec: ResearchBenefitCohortSpecV1,
        evidence: Sequence[ResearchCohortPairedEvidenceV1],
    ) -> ResearchBenefitCohortReportV1:
        report = ResearchBenefitCohortEvaluator().evaluate(
            spec=spec,
            evidence=evidence,
        )
        self._seal("RESEARCH", spec, evidence, report)
        return report

    def seal_code(
        self,
        *,
        spec: CodeBenefitCohortSpecV1,
        evidence: Sequence[CodeCohortPairedEvidenceV1],
    ) -> CodeBenefitCohortReportV1:
        report = CodeBenefitCohortEvaluator().evaluate(
            spec=spec,
            evidence=evidence,
        )
        self._seal("CODE", spec, evidence, report)
        return report

    def seal_pentest(
        self,
        *,
        spec: PentestCalibrationCohortSpecV1,
        evidence: Sequence[PentestExperimentRunEvidenceV1],
    ) -> PentestCalibrationCohortReportV1:
        report = PentestCalibrationCohortEvaluator().evaluate(
            spec=spec,
            evidence=evidence,
        )
        self._seal("PENTEST", spec, evidence, report)
        return report

    def get_report(
        self,
        *,
        domain: str,
        cohort_id: str,
        cohort_version: str,
    ) -> CohortReportSnapshotV1 | None:
        normalized_domain = cohort_domain(domain)
        normalized_id = required_text(cohort_id, "cohort_id", 160)
        normalized_version = required_text(
            cohort_version,
            "cohort_version",
            80,
        )
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM planning_search_cohort_report
                    WHERE domain = ? AND cohort_id = ? AND cohort_version = ?
                    """,
                    (normalized_domain, normalized_id, normalized_version),
                ).fetchone()
                if row is None:
                    return None
                self._verify_evidence_set(conn, row)
                report = _canonical_object(
                    row["report_json"],
                    "cohort report",
                )
                return CohortReportSnapshotV1(
                    domain=normalized_domain,
                    cohort_id=normalized_id,
                    cohort_version=normalized_version,
                    report_ref=(
                        "planning-search-report://sha256/"
                        + str(row["report_digest"])
                    ),
                    report_digest=str(row["report_digest"]),
                    spec_digest=str(row["spec_digest"]),
                    evidence_set_digest=str(row["evidence_set_digest"]),
                    benefit_eligible=bool(row["benefit_eligible"]),
                    calibration_eligible=bool(row["calibration_eligible"]),
                    proposal_eligible=bool(row["proposal_eligible"]),
                    report=report,
                    created_at_ms=int(row["created_at_ms"]),
                )
        except PlanningSearchCohortLedgerError:
            raise
        except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
            raise self._store_error("cohort report cannot be reopened", exc)

    def append_promotion_proposal(
        self,
        *,
        target_algorithm_version: str,
        pentest_report: PentestCalibrationCohortReportV1,
        research_report: ResearchBenefitCohortReportV1 | None = None,
        code_report: CodeBenefitCohortReportV1 | None = None,
    ) -> PlanningSearchPromotionProposalV1:
        proposal = PlanningSearchPromotionGate().evaluate(
            target_algorithm_version=target_algorithm_version,
            pentest_report=pentest_report,
            pentest_report_ref=_report_ref(pentest_report.report_digest),
            research_report=research_report,
            research_report_ref=(
                None
                if research_report is None
                else _report_ref(research_report.report_digest)
            ),
            code_report=code_report,
            code_report_ref=(
                None
                if code_report is None
                else _report_ref(code_report.report_digest)
            ),
        )
        if not proposal.proposal_eligible:
            raise PlanningSearchCohortLedgerError(
                COHORT_PROMOTION_NOT_ELIGIBLE,
                "cross-domain reports do not satisfy the promotion proposal gate",
            )
        reports: list[tuple[str, Any]] = [("PENTEST", pentest_report)]
        if research_report is not None:
            reports.append(("RESEARCH", research_report))
        if code_report is not None:
            reports.append(("CODE", code_report))
        reports.sort(key=lambda item: item[0])
        proposal_json = canonical_json(proposal.to_dict())
        proposal_digest = json_digest(proposal.to_dict())
        evidence_payload = [
            {
                "domain": domain,
                "report_digest": report.report_digest,
                "report_ref": _report_ref(report.report_digest),
            }
            for domain, report in reports
        ]
        evidence_set_digest = json_digest(evidence_payload)
        timestamp = self._timestamp()
        try:
            with self._transaction() as conn:
                for domain, report in reports:
                    self._require_report_exact(conn, domain, report)
                existing = conn.execute(
                    """
                    SELECT proposal_digest, evidence_set_digest, proposal_json
                    FROM planning_search_promotion_proposal
                    WHERE proposal_id = ?
                    """,
                    (proposal.proposal_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["proposal_digest"]) != proposal_digest
                        or str(existing["evidence_set_digest"])
                        != evidence_set_digest
                        or str(existing["proposal_json"]) != proposal_json
                        or self._promotion_evidence(conn, proposal.proposal_id)
                        != evidence_payload
                    ):
                        raise PlanningSearchCohortLedgerError(
                            COHORT_REPLAY_DRIFT,
                            "promotion proposal replay drift",
                        )
                    return proposal
                for item in evidence_payload:
                    conn.execute(
                        """
                        INSERT INTO planning_search_promotion_evidence(
                            proposal_id, domain, report_digest,
                            report_ref, created_at_ms
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            proposal.proposal_id,
                            item["domain"],
                            item["report_digest"],
                            item["report_ref"],
                            timestamp,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO planning_search_promotion_proposal(
                        proposal_id, proposal_digest, target_algorithm_version,
                        evidence_set_digest, proposal_json, review_required,
                        activation_authority, production_influence_enabled,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, 0, ?)
                    """,
                    (
                        proposal.proposal_id,
                        proposal_digest,
                        proposal.target_algorithm_version,
                        evidence_set_digest,
                        proposal_json,
                        timestamp,
                    ),
                )
                return proposal
        except PlanningSearchCohortLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise self._store_error(
                "promotion proposal cannot be persisted",
                exc,
            )

    def _register(self, domain: str, spec: Any) -> str:
        spec_json = canonical_json(spec.to_dict())
        timestamp = self._timestamp()
        try:
            with self._transaction() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM planning_search_cohort_registration
                    WHERE (
                        domain = ? AND cohort_id = ? AND cohort_version = ?
                    ) OR spec_digest = ?
                    """,
                    (
                        domain,
                        spec.cohort_id,
                        spec.cohort_version,
                        spec.spec_digest,
                    ),
                ).fetchall()
                if rows:
                    if len(rows) != 1 or not self._registration_matches(
                        rows[0],
                        domain,
                        spec,
                        spec_json,
                    ):
                        raise PlanningSearchCohortLedgerError(
                            COHORT_REPLAY_DRIFT,
                            "cohort registration replay drift",
                        )
                    return spec.spec_digest
                conn.execute(
                    """
                    INSERT INTO planning_search_cohort_registration(
                        domain, cohort_id, cohort_version, spec_digest,
                        expected_member_count, spec_json,
                        production_influence_enabled, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        domain,
                        spec.cohort_id,
                        spec.cohort_version,
                        spec.spec_digest,
                        PLANNING_SEARCH_COHORT_SIZES[domain],
                        spec_json,
                        timestamp,
                    ),
                )
                return spec.spec_digest
        except PlanningSearchCohortLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise self._store_error(
                "cohort registration cannot be persisted",
                exc,
            )

    def _append_member(
        self,
        domain: str,
        spec: Any,
        evidence: Any,
    ) -> CohortAuthorityProofV1:
        self._require_spec_member(spec, evidence)
        evidence_json = canonical_json(evidence.to_dict())
        evidence_digest = json_digest(evidence.to_dict())
        existing = self._get_member(
            domain=domain,
            cohort_id=spec.cohort_id,
            cohort_version=spec.cohort_version,
            workload_id=evidence.workload_id,
        )
        if existing is not None:
            return self._existing_member_proof(
                existing,
                evidence,
                evidence_digest,
                evidence_json,
            )
        try:
            resolver = getattr(
                self._authority_resolver,
                f"resolve_{domain.lower()}",
            )
            proof = resolver(
                cohort_id=spec.cohort_id,
                cohort_version=spec.cohort_version,
                evidence=evidence,
            )
        except Exception as exc:
            raise PlanningSearchCohortLedgerError(
                COHORT_SOURCE_AUTHORITY_REJECTED,
                "source authority rejected cohort evidence",
            ) from exc
        self._require_proof(
            proof=proof,
            domain=domain,
            spec=spec,
            evidence=evidence,
            evidence_digest=evidence_digest,
        )
        proof_json = canonical_json(proof.to_dict())
        timestamp = self._timestamp()
        try:
            with self._transaction() as conn:
                self._require_registration_exact(conn, domain, spec)
                current = conn.execute(
                    """
                    SELECT *
                    FROM planning_search_cohort_member_evidence
                    WHERE domain = ? AND cohort_id = ?
                      AND cohort_version = ? AND workload_id = ?
                    """,
                    (
                        domain,
                        spec.cohort_id,
                        spec.cohort_version,
                        evidence.workload_id,
                    ),
                ).fetchone()
                if current is not None:
                    return self._existing_member_proof(
                        current,
                        evidence,
                        evidence_digest,
                        evidence_json,
                    )
                conn.execute(
                    """
                    INSERT INTO planning_search_cohort_member_evidence(
                        domain, cohort_id, cohort_version, workload_id, run_id,
                        evidence_digest, evidence_json, authority_proof_digest,
                        authority_proof_json, production_influence_enabled,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        domain,
                        spec.cohort_id,
                        spec.cohort_version,
                        evidence.workload_id,
                        evidence.run_id,
                        evidence_digest,
                        evidence_json,
                        proof.proof_digest,
                        proof_json,
                        timestamp,
                    ),
                )
                return proof
        except PlanningSearchCohortLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise self._store_error(
                "cohort member cannot be persisted",
                exc,
            )

    def _seal(
        self,
        domain: str,
        spec: Any,
        evidence: Sequence[Any],
        report: Any,
    ) -> None:
        by_workload = {item.workload_id: item for item in evidence}
        report_json = canonical_json(report.to_dict())
        report_digest = report.report_digest
        flags = self._report_flags(domain, report)
        timestamp = self._timestamp()
        try:
            with self._transaction() as conn:
                self._require_registration_exact(conn, domain, spec)
                evidence_payload: list[dict[str, str]] = []
                for workload in spec.workloads:
                    item = by_workload[workload.workload_id]
                    item_json = canonical_json(item.to_dict())
                    item_digest = json_digest(item.to_dict())
                    row = conn.execute(
                        """
                        SELECT *
                        FROM planning_search_cohort_member_evidence
                        WHERE domain = ? AND cohort_id = ?
                          AND cohort_version = ? AND workload_id = ?
                        """,
                        (
                            domain,
                            spec.cohort_id,
                            spec.cohort_version,
                            workload.workload_id,
                        ),
                    ).fetchone()
                    if row is None or not self._member_matches(
                        row,
                        item,
                        item_digest,
                        item_json,
                    ):
                        raise PlanningSearchCohortLedgerError(
                            COHORT_NOT_FOUND,
                            "cohort report lacks exact durable member evidence",
                        )
                    evidence_payload.append(
                        {
                            "workload_id": workload.workload_id,
                            "run_id": item.run_id,
                            "evidence_digest": item_digest,
                            "authority_proof_digest": str(
                                row["authority_proof_digest"]
                            ),
                        }
                    )
                evidence_set_digest = json_digest(evidence_payload)
                existing = conn.execute(
                    """
                    SELECT *
                    FROM planning_search_cohort_report
                    WHERE domain = ? AND cohort_id = ? AND cohort_version = ?
                    """,
                    (domain, spec.cohort_id, spec.cohort_version),
                ).fetchone()
                if existing is not None:
                    if not self._report_matches(
                        existing,
                        report,
                        report_json,
                        evidence_set_digest,
                        flags,
                    ):
                        raise PlanningSearchCohortLedgerError(
                            COHORT_REPLAY_DRIFT,
                            "cohort report replay drift",
                        )
                    return
                conn.execute(
                    """
                    INSERT INTO planning_search_cohort_report(
                        domain, cohort_id, cohort_version, report_digest,
                        spec_digest, evidence_set_digest, report_json,
                        benefit_eligible, calibration_eligible,
                        proposal_eligible, production_influence_enabled,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        domain,
                        spec.cohort_id,
                        spec.cohort_version,
                        report_digest,
                        spec.spec_digest,
                        evidence_set_digest,
                        report_json,
                        int(flags["benefit_eligible"]),
                        int(flags["calibration_eligible"]),
                        int(flags["proposal_eligible"]),
                        timestamp,
                    ),
                )
        except PlanningSearchCohortLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise self._store_error("cohort report cannot be sealed", exc)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1_000.0,
        )
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

__all__ = [
    "COHORT_INVALID",
    "COHORT_NOT_FOUND",
    "COHORT_PROMOTION_NOT_ELIGIBLE",
    "COHORT_REPLAY_DRIFT",
    "COHORT_SCHEMA_NOT_READY",
    "COHORT_SOURCE_AUTHORITY_REJECTED",
    "COHORT_STORE_UNAVAILABLE",
    "PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY",
    "PLANNING_SEARCH_COHORT_MIGRATION_SHA256",
    "PlanningSearchCohortLedgerError",
    "SQLitePlanningSearchCohortLedger",
]
