"""Proposal-only cross-domain gate for PlanningSearch L3 promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .code_benefit_cohort_models import CodeBenefitCohortReportV1
from .pentest_calibration_cohort_models import (
    PentestCalibrationCohortReportV1,
    _content_ref,
    _json_digest,
    _text,
)
from .research_benefit_cohort_models import ResearchBenefitCohortReportV1


PLANNING_SEARCH_PROMOTION_PROPOSAL_SCHEMA = (
    "natsume.planning-search-promotion-proposal.v1"
)


class PlanningSearchPromotionGateError(ValueError):
    """Promotion evidence is malformed or content-address identity drifted."""


@dataclass(frozen=True, slots=True)
class PlanningSearchPromotionProposalV1:
    """Review-bound proposal; never an activation or execution authority."""

    target_algorithm_version: str
    pentest_report_ref: str
    pentest_report_digest: str
    non_pentest_domains: tuple[str, ...]
    non_pentest_report_refs: tuple[str, ...]
    non_pentest_report_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]
    proposal_eligible: bool
    review_required: bool = True
    activation_authority: bool = False
    production_influence_enabled: bool = False
    proposal_id: str = ""

    def __post_init__(self) -> None:
        target_version = _promotion_text(
            self.target_algorithm_version,
            "target_algorithm_version",
            160,
        )
        object.__setattr__(self, "target_algorithm_version", target_version)
        pentest_digest = _report_digest(
            self.pentest_report_digest,
            "pentest_report_digest",
        )
        object.__setattr__(self, "pentest_report_digest", pentest_digest)
        object.__setattr__(
            self,
            "pentest_report_ref",
            _report_ref(
                self.pentest_report_ref,
                "pentest_report_ref",
                pentest_digest,
            ),
        )
        domains = tuple(
            _promotion_text(value, "non_pentest_domains[]", 32).upper()
            for value in self.non_pentest_domains
        )
        refs = tuple(self.non_pentest_report_refs)
        digests = tuple(
            _report_digest(value, "non_pentest_report_digests[]")
            for value in self.non_pentest_report_digests
        )
        if (
            not domains
            or len(domains) != len(refs)
            or len(domains) != len(digests)
            or domains != tuple(sorted(set(domains)))
            or any(domain not in {"CODE", "RESEARCH"} for domain in domains)
        ):
            raise PlanningSearchPromotionGateError(
                "non-Pentest report identities must be aligned, unique and sorted"
            )
        refs = tuple(
            _report_ref(ref, "non_pentest_report_refs[]", digest)
            for ref, digest in zip(refs, digests, strict=True)
        )
        object.__setattr__(self, "non_pentest_domains", domains)
        object.__setattr__(self, "non_pentest_report_refs", refs)
        object.__setattr__(self, "non_pentest_report_digests", digests)
        reasons = tuple(
            sorted(
                set(
                    _promotion_text(value, "reason_codes[]", 120)
                    for value in self.reason_codes
                )
            )
        )
        object.__setattr__(self, "reason_codes", reasons)
        expected_eligible = not reasons
        if self.proposal_eligible is not expected_eligible:
            raise PlanningSearchPromotionGateError(
                "proposal eligibility must be derived from reason_codes"
            )
        if self.review_required is not True:
            raise PlanningSearchPromotionGateError(
                "PlanningSearch promotion proposal requires Review"
            )
        if (
            self.activation_authority is not False
            or self.production_influence_enabled is not False
        ):
            raise PlanningSearchPromotionGateError(
                "promotion proposal cannot activate or influence production"
            )
        object.__setattr__(self, "review_required", True)
        object.__setattr__(self, "activation_authority", False)
        object.__setattr__(self, "production_influence_enabled", False)
        expected_id = "planpromo_" + self.identity_digest[:32]
        supplied_id = str(self.proposal_id or "").strip()
        if supplied_id and supplied_id != expected_id:
            raise PlanningSearchPromotionGateError(
                "proposal_id does not bind immutable proposal identity"
            )
        object.__setattr__(self, "proposal_id", expected_id)

    @property
    def identity_digest(self) -> str:
        return _json_digest(self.identity_dict())

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema": PLANNING_SEARCH_PROMOTION_PROPOSAL_SCHEMA,
            "target_algorithm_version": self.target_algorithm_version,
            "pentest_report_ref": self.pentest_report_ref,
            "pentest_report_digest": self.pentest_report_digest,
            "non_pentest_domains": list(self.non_pentest_domains),
            "non_pentest_report_refs": list(self.non_pentest_report_refs),
            "non_pentest_report_digests": list(
                self.non_pentest_report_digests
            ),
            "reason_codes": list(self.reason_codes),
            "proposal_eligible": self.proposal_eligible,
            "review_required": True,
            "activation_authority": False,
            "production_influence_enabled": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, **self.identity_dict()}


class PlanningSearchPromotionGate:
    """Require safe Pentest calibration and one proven non-Pentest domain."""

    def evaluate(
        self,
        *,
        target_algorithm_version: str,
        pentest_report: PentestCalibrationCohortReportV1,
        pentest_report_ref: str,
        research_report: ResearchBenefitCohortReportV1 | None = None,
        research_report_ref: str | None = None,
        code_report: CodeBenefitCohortReportV1 | None = None,
        code_report_ref: str | None = None,
    ) -> PlanningSearchPromotionProposalV1:
        if not isinstance(pentest_report, PentestCalibrationCohortReportV1):
            raise PlanningSearchPromotionGateError(
                "pentest_report must be PentestCalibrationCohortReportV1"
            )
        pentest_digest = pentest_report.report_digest
        pentest_ref = _report_ref(
            pentest_report_ref,
            "pentest_report_ref",
            pentest_digest,
        )

        report_rows: list[
            tuple[
                str,
                ResearchBenefitCohortReportV1 | CodeBenefitCohortReportV1,
                str,
            ]
        ] = []
        if research_report is not None:
            if not isinstance(research_report, ResearchBenefitCohortReportV1):
                raise PlanningSearchPromotionGateError(
                    "research_report has an unsupported type"
                )
            if research_report_ref is None:
                raise PlanningSearchPromotionGateError(
                    "research_report_ref is required"
                )
            report_rows.append(
                ("RESEARCH", research_report, research_report_ref)
            )
        elif research_report_ref is not None:
            raise PlanningSearchPromotionGateError(
                "research_report is required when its ref is supplied"
            )
        if code_report is not None:
            if not isinstance(code_report, CodeBenefitCohortReportV1):
                raise PlanningSearchPromotionGateError(
                    "code_report has an unsupported type"
                )
            if code_report_ref is None:
                raise PlanningSearchPromotionGateError(
                    "code_report_ref is required"
                )
            report_rows.append(("CODE", code_report, code_report_ref))
        elif code_report_ref is not None:
            raise PlanningSearchPromotionGateError(
                "code_report is required when its ref is supplied"
            )
        if not report_rows:
            raise PlanningSearchPromotionGateError(
                "at least one non-Pentest report is required"
            )

        evidence: list[tuple[str, str, str]] = []
        benefit_proven = False
        for domain, report, report_ref in sorted(report_rows):
            report_digest = report.report_digest
            durable_ref = _report_ref(
                report_ref,
                f"{domain.lower()}_report_ref",
                report_digest,
            )
            evidence.append((domain, durable_ref, report_digest))
            benefit_proven = benefit_proven or (
                report.benefit_eligible and report.proposal_eligible
            )

        reasons: set[str] = set()
        if (
            not pentest_report.calibration_eligible
            or not pentest_report.proposal_input_eligible
        ):
            reasons.add("PENTEST_CALIBRATION_NOT_ELIGIBLE")
        if not benefit_proven:
            reasons.add("NON_PENTEST_BENEFIT_NOT_PROVEN")
        ordered_reasons = tuple(sorted(reasons))
        return PlanningSearchPromotionProposalV1(
            target_algorithm_version=target_algorithm_version,
            pentest_report_ref=pentest_ref,
            pentest_report_digest=pentest_digest,
            non_pentest_domains=tuple(row[0] for row in evidence),
            non_pentest_report_refs=tuple(row[1] for row in evidence),
            non_pentest_report_digests=tuple(row[2] for row in evidence),
            reason_codes=ordered_reasons,
            proposal_eligible=not ordered_reasons,
        )


def _report_digest(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise PlanningSearchPromotionGateError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _promotion_text(value: Any, field: str, limit: int) -> str:
    try:
        return _text(value, field, limit)
    except ValueError as exc:
        raise PlanningSearchPromotionGateError(str(exc)) from exc


def _report_ref(value: Any, field: str, digest: str) -> str:
    try:
        return _content_ref(
            value,
            field,
            "planning-search-report",
            digest,
        )
    except ValueError as exc:
        raise PlanningSearchPromotionGateError(str(exc)) from exc


__all__ = [
    "PLANNING_SEARCH_PROMOTION_PROPOSAL_SCHEMA",
    "PlanningSearchPromotionGate",
    "PlanningSearchPromotionGateError",
    "PlanningSearchPromotionProposalV1",
]
