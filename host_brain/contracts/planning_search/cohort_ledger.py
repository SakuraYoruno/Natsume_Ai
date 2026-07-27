"""Stable contracts for the append-only PlanningSearch cohort authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from .code_benefit_cohort_models import CodeCohortPairedEvidenceV1
from .pentest_calibration_cohort_models import (
    PentestExperimentRunEvidenceV1,
)
from .research_benefit_cohort_models import ResearchCohortPairedEvidenceV1


PLANNING_SEARCH_COHORT_LEDGER_SCHEMA = (
    "natsume.planning-search-cohort-ledger.v1"
)
PLANNING_SEARCH_COHORT_DOMAINS = frozenset(
    {"RESEARCH", "CODE", "PENTEST"}
)
PLANNING_SEARCH_COHORT_SIZES = {
    "RESEARCH": 24,
    "CODE": 20,
    "PENTEST": 30,
}

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class PlanningSearchCohortContractError(ValueError):
    """A cohort authority proof or durable projection is malformed."""


def cohort_domain(value: Any) -> str:
    normalized = required_text(value, "domain", 32).upper()
    if normalized not in PLANNING_SEARCH_COHORT_DOMAINS:
        raise PlanningSearchCohortContractError(
            "domain must be RESEARCH, CODE or PENTEST"
        )
    return normalized


def required_text(value: Any, field: str, limit: int = 1_024) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise PlanningSearchCohortContractError(
            f"{field} must be non-empty and bounded"
        )
    return normalized


def sha256_digest(value: Any, field: str) -> str:
    normalized = required_text(value, field, 64).lower()
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise PlanningSearchCohortContractError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PlanningSearchCohortContractError(
            "value is not canonical JSON"
        ) from exc


def json_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CohortAuthorityProofV1:
    """Evidence that an injected authority reopened one cohort member."""

    resolver_id: str
    domain: str
    cohort_id: str
    cohort_version: str
    workload_id: str
    run_id: str
    evidence_digest: str
    authority_refs: tuple[str, ...]
    authority_digests: tuple[str, ...]
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resolver_id",
            required_text(self.resolver_id, "resolver_id", 160),
        )
        object.__setattr__(self, "domain", cohort_domain(self.domain))
        for field, limit in (
            ("cohort_id", 160),
            ("cohort_version", 80),
            ("workload_id", 160),
            ("run_id", 255),
        ):
            object.__setattr__(
                self,
                field,
                required_text(getattr(self, field), field, limit),
            )
        object.__setattr__(
            self,
            "evidence_digest",
            sha256_digest(self.evidence_digest, "evidence_digest"),
        )
        if isinstance(self.authority_refs, (str, bytes, bytearray)):
            raise PlanningSearchCohortContractError(
                "authority_refs must be a sequence"
            )
        if isinstance(self.authority_digests, (str, bytes, bytearray)):
            raise PlanningSearchCohortContractError(
                "authority_digests must be a sequence"
            )
        refs = tuple(
            required_text(value, "authority_refs[]", 1_024)
            for value in self.authority_refs
        )
        digests = tuple(
            sha256_digest(value, "authority_digests[]")
            for value in self.authority_digests
        )
        if (
            not refs
            or len(refs) != len(digests)
            or len(refs) > 16
            or len(set(refs)) != len(refs)
            or refs != tuple(sorted(refs))
        ):
            raise PlanningSearchCohortContractError(
                "authority refs/digests must be aligned, unique and sorted"
            )
        if any(
            not ref.endswith(f"/sha256/{digest}")
            for ref, digest in zip(refs, digests, strict=True)
        ):
            raise PlanningSearchCohortContractError(
                "authority refs must content-address their digests"
            )
        if self.production_influence_enabled is not False:
            raise PlanningSearchCohortContractError(
                "cohort authority proof cannot influence production"
            )
        object.__setattr__(self, "authority_refs", refs)
        object.__setattr__(self, "authority_digests", digests)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def proof_digest(self) -> str:
        return json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.planning-search-cohort-authority-proof.v1",
            "resolver_id": self.resolver_id,
            "domain": self.domain,
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "evidence_digest": self.evidence_digest,
            "authority_refs": list(self.authority_refs),
            "authority_digests": list(self.authority_digests),
            "production_influence_enabled": False,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CohortAuthorityProofV1":
        if (
            not isinstance(value, Mapping)
            or value.get("schema")
            != "natsume.planning-search-cohort-authority-proof.v1"
        ):
            raise PlanningSearchCohortContractError(
                "unsupported cohort authority proof"
            )
        return cls(
            resolver_id=str(value.get("resolver_id") or ""),
            domain=str(value.get("domain") or ""),
            cohort_id=str(value.get("cohort_id") or ""),
            cohort_version=str(value.get("cohort_version") or ""),
            workload_id=str(value.get("workload_id") or ""),
            run_id=str(value.get("run_id") or ""),
            evidence_digest=str(value.get("evidence_digest") or ""),
            authority_refs=tuple(value.get("authority_refs") or ()),
            authority_digests=tuple(value.get("authority_digests") or ()),
            production_influence_enabled=bool(
                value.get("production_influence_enabled")
            ),
        )


@dataclass(frozen=True, slots=True)
class CohortReportSnapshotV1:
    domain: str
    cohort_id: str
    cohort_version: str
    report_ref: str
    report_digest: str
    spec_digest: str
    evidence_set_digest: str
    benefit_eligible: bool
    calibration_eligible: bool
    proposal_eligible: bool
    report: Mapping[str, Any]
    created_at_ms: int
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", cohort_domain(self.domain))
        for field, limit in (("cohort_id", 160), ("cohort_version", 80)):
            object.__setattr__(
                self,
                field,
                required_text(getattr(self, field), field, limit),
            )
        for field in ("report_digest", "spec_digest", "evidence_set_digest"):
            object.__setattr__(
                self,
                field,
                sha256_digest(getattr(self, field), field),
            )
        expected_ref = (
            f"planning-search-report://sha256/{self.report_digest}"
        )
        if self.report_ref != expected_ref:
            raise PlanningSearchCohortContractError(
                "report_ref does not bind report_digest"
            )
        report = json.loads(canonical_json(self.report))
        if json_digest(report) != self.report_digest:
            raise PlanningSearchCohortContractError(
                "report payload digest drift"
            )
        for field in (
            "benefit_eligible",
            "calibration_eligible",
            "proposal_eligible",
        ):
            if not isinstance(getattr(self, field), bool):
                raise PlanningSearchCohortContractError(
                    f"{field} must be boolean"
                )
        created_at_ms = int(self.created_at_ms)
        if isinstance(self.created_at_ms, bool) or created_at_ms < 0:
            raise PlanningSearchCohortContractError(
                "created_at_ms must be non-negative"
            )
        if self.production_influence_enabled is not False:
            raise PlanningSearchCohortContractError(
                "cohort report cannot influence production"
            )
        object.__setattr__(self, "report", report)
        object.__setattr__(self, "created_at_ms", created_at_ms)
        object.__setattr__(self, "production_influence_enabled", False)


class PlanningSearchCohortAuthorityResolver(Protocol):
    """Required source-authority port; no permissive default is allowed."""

    def resolve_research(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: ResearchCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1: ...

    def resolve_code(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: CodeCohortPairedEvidenceV1,
    ) -> CohortAuthorityProofV1: ...

    def resolve_pentest(
        self,
        *,
        cohort_id: str,
        cohort_version: str,
        evidence: PentestExperimentRunEvidenceV1,
    ) -> CohortAuthorityProofV1: ...


class PentestExperimentReceiptAuthority(Protocol):
    """Read-only authority for the three content-addressed Pentest receipts."""

    def resolve_receipt_digest(
        self,
        *,
        receipt_ref: str,
    ) -> str | None: ...


__all__ = [
    "PLANNING_SEARCH_COHORT_DOMAINS",
    "PLANNING_SEARCH_COHORT_LEDGER_SCHEMA",
    "PLANNING_SEARCH_COHORT_SIZES",
    "CohortAuthorityProofV1",
    "CohortReportSnapshotV1",
    "PentestExperimentReceiptAuthority",
    "PlanningSearchCohortAuthorityResolver",
    "PlanningSearchCohortContractError",
    "canonical_json",
    "cohort_domain",
    "json_digest",
    "required_text",
    "sha256_digest",
]
