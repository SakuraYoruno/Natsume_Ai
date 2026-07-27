"""Research-specific shadow-run contracts.

These contracts are deliberately separate from the generic benchmark report
and from ``ModelAttempt``.  They bind the frozen Research corpus, the exact
planner arm, and the verifier evidence without granting production influence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import SearchResult
from .research_domain_models import ResearchTaskSnapshot

RESEARCH_BENCHMARK_SCHEMA = "natsume.research-benchmark.shadow.v1"
RESEARCH_EVIDENCE_CLASSES = frozenset(
    {"SCRIPTED_REPLAY", "CANONICAL_ATTEMPT_UNATTESTED", "ATTESTED_EXTERNAL"}
)
_FORBIDDEN_PROVIDER_MARKERS = frozenset(
    {"localhost", "127.0.0.1", "mock", "disabled", "test", "placeholder", "cache"}
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ResearchBenchmarkContractError(ValueError):
    """Research evidence is malformed or not independently bound."""


def _text(value: Any, field: str, limit: int = 512) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ResearchBenchmarkContractError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise ResearchBenchmarkContractError(f"{field} must be a SHA-256 digest")
    return result


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _refs(values: Sequence[Any], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ResearchBenchmarkContractError(f"{field} must be a sequence")
    result = tuple(_text(item, f"{field}[]", 1_024) for item in values)
    if not result or len(set(result)) != len(result):
        raise ResearchBenchmarkContractError(f"{field} must contain unique values")
    return result


@dataclass(frozen=True, slots=True)
class ResearchCorpusBindingV1:
    """Exact snapshot/manifest/source handshake for one Research run."""

    snapshot_digest: str
    corpus_ref: str
    manifest_digest: str
    source_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    source_digests: tuple[str, ...]
    authority_tiers: tuple[str, ...]

    def __post_init__(self) -> None:
        snapshot = _digest(self.snapshot_digest, "snapshot_digest")
        corpus = _text(self.corpus_ref, "corpus_ref", 1_024)
        manifest = _digest(self.manifest_digest, "manifest_digest")
        ids = _refs(self.source_ids, "source_ids")
        refs = _refs(self.source_refs, "source_refs")
        digests = tuple(_digest(item, "source_digests[]") for item in self.source_digests)
        tiers = _refs(self.authority_tiers, "authority_tiers")
        if not len(ids) == len(refs) == len(digests) == len(tiers):
            raise ResearchBenchmarkContractError("corpus source fields must have equal lengths")
        if tuple(sorted(ids)) != ids:
            raise ResearchBenchmarkContractError("source_ids must be sorted")
        object.__setattr__(self, "snapshot_digest", snapshot)
        object.__setattr__(self, "corpus_ref", corpus)
        object.__setattr__(self, "manifest_digest", manifest)
        object.__setattr__(self, "source_ids", ids)
        object.__setattr__(self, "source_refs", refs)
        object.__setattr__(self, "source_digests", digests)
        object.__setattr__(self, "authority_tiers", tiers)

    @classmethod
    def from_snapshot(cls, snapshot: ResearchTaskSnapshot, *, manifest_digest: str) -> "ResearchCorpusBindingV1":
        if not isinstance(snapshot, ResearchTaskSnapshot):
            raise ResearchBenchmarkContractError("snapshot must be ResearchTaskSnapshot")
        sources = tuple(sorted(snapshot.sources, key=lambda item: item.source_id))
        return cls(
            snapshot_digest=snapshot.snapshot_digest,
            corpus_ref=snapshot.corpus_ref,
            manifest_digest=manifest_digest,
            source_ids=tuple(item.source_id for item in sources),
            source_refs=tuple(item.source_ref for item in sources),
            source_digests=tuple(item.content_digest for item in sources),
            authority_tiers=tuple(item.authority_tier for item in sources),
        )

    @property
    def binding_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_BENCHMARK_SCHEMA,
            "snapshot_digest": self.snapshot_digest,
            "corpus_ref": self.corpus_ref,
            "manifest_digest": self.manifest_digest,
            "source_ids": list(self.source_ids),
            "source_refs": list(self.source_refs),
            "source_digests": list(self.source_digests),
            "authority_tiers": list(self.authority_tiers),
        }


@dataclass(frozen=True, slots=True)
class ResearchExecutionProfileV1:
    prompt_digest: str
    profile_digest: str
    output_schema_digest: str
    cache_policy: str = "NONE"

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_digest", _digest(self.prompt_digest, "prompt_digest"))
        object.__setattr__(self, "profile_digest", _digest(self.profile_digest, "profile_digest"))
        object.__setattr__(self, "output_schema_digest", _digest(self.output_schema_digest, "output_schema_digest"))
        if str(self.cache_policy).upper() != "NONE":
            raise ResearchBenchmarkContractError("Research execution cache_policy must be NONE")
        object.__setattr__(self, "cache_policy", "NONE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_digest": self.prompt_digest,
            "profile_digest": self.profile_digest,
            "output_schema_digest": self.output_schema_digest,
            "cache_policy": self.cache_policy,
        }


@dataclass(frozen=True, slots=True)
class ResearchExecutionBudgetV1:
    arm_max_tokens: int
    arm_max_tool_calls: int
    arm_max_wall_time_ms: int
    pair_max_tokens: int
    pair_max_wall_time_ms: int
    declared_only_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = (
            "arm_max_tokens",
            "arm_max_tool_calls",
            "arm_max_wall_time_ms",
            "pair_max_tokens",
            "pair_max_wall_time_ms",
        )
        for name in names:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ResearchBenchmarkContractError(f"{name} must be a positive integer")
        if self.pair_max_tokens < self.arm_max_tokens:
            raise ResearchBenchmarkContractError("pair_max_tokens must cover one arm")
        if self.pair_max_wall_time_ms < self.arm_max_wall_time_ms:
            raise ResearchBenchmarkContractError("pair_max_wall_time_ms must cover one arm")
        fields = tuple(sorted(set(_text(item, "declared_only_fields[]", 80) for item in self.declared_only_fields)))
        if any(item not in names for item in fields):
            raise ResearchBenchmarkContractError("declared_only_fields contains an unknown budget field")
        object.__setattr__(self, "declared_only_fields", fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_max_tokens": self.arm_max_tokens,
            "arm_max_tool_calls": self.arm_max_tool_calls,
            "arm_max_wall_time_ms": self.arm_max_wall_time_ms,
            "pair_max_tokens": self.pair_max_tokens,
            "pair_max_wall_time_ms": self.pair_max_wall_time_ms,
            "declared_only_fields": list(self.declared_only_fields),
        }

    @property
    def budget_digest(self) -> str:
        return _json_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchArmPlanV1:
    arm_id: str
    candidate_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    graph_digest: str
    path_digest: str
    generated_by: str = "RESEARCH_SUCCESSOR_CATALOG"

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm_id", _text(self.arm_id, "arm_id", 128))
        object.__setattr__(self, "candidate_ids", _refs(self.candidate_ids, "candidate_ids"))
        object.__setattr__(self, "source_ids", tuple(sorted(set(_text(item, "source_ids[]", 120) for item in self.source_ids))))
        object.__setattr__(self, "graph_digest", _digest(self.graph_digest, "graph_digest"))
        object.__setattr__(self, "path_digest", _digest(self.path_digest, "path_digest"))
        if self.generated_by != "RESEARCH_SUCCESSOR_CATALOG":
            raise ResearchBenchmarkContractError("ResearchArmPlan must come from the Research successor catalog")

    @classmethod
    def from_search_result(
        cls,
        arm_id: str,
        result: SearchResult,
        *,
        binding: ResearchCorpusBindingV1,
    ) -> "ResearchArmPlanV1":
        if not isinstance(result, SearchResult) or not result.selected_path:
            raise ResearchBenchmarkContractError("Research arm requires a selected SearchResult path")
        candidate_ids = tuple(candidate.candidate_id for candidate in result.selected_path)
        source_ids = tuple(
            str(candidate.payload.get("source_id") or "").strip().lower()
            for candidate in result.selected_path
            if isinstance(candidate.payload, Mapping) and candidate.payload.get("source_id")
        )
        if any(source_id not in binding.source_ids for source_id in source_ids):
            raise ResearchBenchmarkContractError("Research arm contains a source outside its corpus binding")
        return cls(
            arm_id=arm_id,
            candidate_ids=candidate_ids,
            source_ids=source_ids,
            graph_digest=_json_digest({"virtual_state": result.virtual_state_digest, "path": candidate_ids}),
            path_digest=result.trace_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "candidate_ids": list(self.candidate_ids),
            "source_ids": list(self.source_ids),
            "graph_digest": self.graph_digest,
            "path_digest": self.path_digest,
            "generated_by": self.generated_by,
        }


@dataclass(frozen=True, slots=True)
class ResearchCitationSpanV1:
    source_id: str
    start_byte: int
    end_byte: int
    span_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id", 120))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (self.start_byte, self.end_byte)):
            raise ResearchBenchmarkContractError("citation byte offsets must be non-negative integers")
        if self.end_byte <= self.start_byte:
            raise ResearchBenchmarkContractError("citation end_byte must exceed start_byte")
        object.__setattr__(self, "span_digest", _digest(self.span_digest, "span_digest"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "span_digest": self.span_digest,
        }


@dataclass(frozen=True, slots=True)
class ResearchVerifierReceiptV1:
    execution_id: str
    citation_count: int
    cited_span_count: int
    citation_coverage: float
    citation_validity: float
    evidence_digest: str
    fact_truth_attested: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (self.citation_count, self.cited_span_count)):
            raise ResearchBenchmarkContractError("citation counts must be non-negative integers")
        if self.cited_span_count > self.citation_count:
            raise ResearchBenchmarkContractError("cited_span_count cannot exceed citation_count")
        for name in ("citation_coverage", "citation_validity"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ResearchBenchmarkContractError(f"{name} must be within [0, 1]")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "evidence_digest", _digest(self.evidence_digest, "evidence_digest"))
        if self.fact_truth_attested:
            raise ResearchBenchmarkContractError("D20b verifier receipt cannot attest factual truth")
        object.__setattr__(self, "fact_truth_attested", False)

    @property
    def structurally_eligible(self) -> bool:
        """Whether this receipt clears the minimum D20 citation evidence floor.

        A structurally well-formed zero-citation receipt is still useful as a
        failed experiment record, but it must never make an external paired
        run benefit-eligible.  The frozen Research acceptance protocol
        requires non-empty, fully accounted citations with at least 0.95
        coverage and validity before later cohort statistics can consider it.
        """

        return (
            self.citation_count > 0
            and self.cited_span_count == self.citation_count
            and self.citation_coverage >= 0.95
            and self.citation_validity >= 0.95
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "citation_count": self.citation_count,
            "cited_span_count": self.cited_span_count,
            "citation_coverage": self.citation_coverage,
            "citation_validity": self.citation_validity,
            "evidence_digest": self.evidence_digest,
            "fact_truth_attested": False,
        }


@dataclass(frozen=True, slots=True)
class ResearchModelExecutionReceiptV1:
    execution_id: str
    arm_id: str
    evidence_class: str
    attempt_ids: tuple[str, ...]
    prompt_digest: str | None = None
    tool_call_count: int | None = None
    total_tokens: int | None = None
    cost_micros: int | None = None
    attestation_ref: str | None = None
    attestation: "ExternalProviderRunAttestationV1 | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        object.__setattr__(self, "arm_id", _text(self.arm_id, "arm_id", 128))
        evidence_class = _text(self.evidence_class, "evidence_class", 64).upper()
        if evidence_class not in RESEARCH_EVIDENCE_CLASSES:
            raise ResearchBenchmarkContractError("unsupported Research evidence_class")
        object.__setattr__(self, "evidence_class", evidence_class)
        object.__setattr__(self, "attempt_ids", _refs(self.attempt_ids, "attempt_ids"))
        prompt_digest = (
            None
            if self.prompt_digest is None
            else _digest(self.prompt_digest, "prompt_digest")
        )
        for name in ("tool_call_count", "total_tokens", "cost_micros"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ResearchBenchmarkContractError(f"{name} must be a non-negative integer")
        attestation = None if self.attestation_ref is None else _text(self.attestation_ref, "attestation_ref", 1_024)
        external_attestation = self.attestation
        if external_attestation is not None and not isinstance(
            external_attestation,
            ExternalProviderRunAttestationV1,
        ):
            raise ResearchBenchmarkContractError(
                "attestation must be an ExternalProviderRunAttestationV1"
            )
        if evidence_class == "ATTESTED_EXTERNAL" and not attestation:
            raise ResearchBenchmarkContractError("attested external evidence requires attestation_ref")
        if evidence_class == "ATTESTED_EXTERNAL" and (self.total_tokens is None or self.cost_micros is None):
            raise ResearchBenchmarkContractError(
                "attested external evidence requires token and cost usage"
            )
        if evidence_class == "ATTESTED_EXTERNAL" and prompt_digest is None:
            raise ResearchBenchmarkContractError(
                "attested external evidence requires the rendered prompt digest"
            )
        if evidence_class == "ATTESTED_EXTERNAL" and self.tool_call_count is None:
            raise ResearchBenchmarkContractError(
                "attested external evidence requires tool-call usage"
            )
        if evidence_class == "ATTESTED_EXTERNAL" and external_attestation is None:
            raise ResearchBenchmarkContractError(
                "attested external evidence requires an exact provider attestation"
            )
        if evidence_class == "ATTESTED_EXTERNAL" and external_attestation is not None:
            if (
                external_attestation.execution_id != self.execution_id
                or external_attestation.attestation_ref != attestation
                or external_attestation.total_tokens != self.total_tokens
                or external_attestation.cost_micros != self.cost_micros
            ):
                raise ResearchBenchmarkContractError(
                    "provider attestation does not bind the Research model receipt"
                )
        if evidence_class != "ATTESTED_EXTERNAL" and attestation:
            raise ResearchBenchmarkContractError("non-attested evidence cannot carry attestation_ref")
        if evidence_class != "ATTESTED_EXTERNAL" and external_attestation is not None:
            raise ResearchBenchmarkContractError(
                "non-attested evidence cannot carry a provider attestation"
            )
        object.__setattr__(self, "attestation_ref", attestation)
        object.__setattr__(self, "attestation", external_attestation)
        object.__setattr__(self, "prompt_digest", prompt_digest)

    @property
    def benefit_eligible(self) -> bool:
        return (
            self.evidence_class == "ATTESTED_EXTERNAL"
            and bool(self.attempt_ids)
            and self.attestation_ref is not None
            and self.prompt_digest is not None
            and self.tool_call_count is not None
            and self.attestation is not None
            and self.attestation.transport_identity_digest is not None
            and self.attestation.transport_external is True
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "execution_id": self.execution_id,
            "arm_id": self.arm_id,
            "evidence_class": self.evidence_class,
            "attempt_ids": list(self.attempt_ids),
            "prompt_digest": self.prompt_digest,
            "tool_call_count": self.tool_call_count,
            "total_tokens": self.total_tokens,
            "cost_micros": self.cost_micros,
            "attestation_ref": self.attestation_ref,
            "benefit_eligible": self.benefit_eligible,
        }
        if self.attestation is not None:
            result["attestation"] = self.attestation.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class ExternalProviderRunAttestationV1:
    """Trusted transport evidence for one fresh external-model request.

    This is an attestation contract only; it does not call a provider and it
    cannot grant production influence.  Local, mock, disabled, test and cache
    routes are rejected so scripted replay cannot masquerade as real evidence.
    """

    execution_id: str
    provider: str
    model: str
    route_digest: str
    response_digest: str
    total_tokens: int
    cost_micros: int
    provider_latency_ms: int
    total_latency_ms: int
    transport_identity_digest: str | None = None
    transport_external: bool | None = None
    fresh_request: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        provider = _text(self.provider, "provider", 160).lower()
        if any(marker in provider for marker in _FORBIDDEN_PROVIDER_MARKERS):
            raise ResearchBenchmarkContractError("external attestation provider is not genuinely external")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", _text(self.model, "model", 240))
        for name in ("route_digest", "response_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in ("total_tokens", "cost_micros", "provider_latency_ms", "total_latency_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ResearchBenchmarkContractError(f"{name} must be a non-negative integer")
        transport_identity = (
            None
            if self.transport_identity_digest is None
            else _digest(
                self.transport_identity_digest,
                "transport_identity_digest",
            )
        )
        transport_external = self.transport_external
        if transport_external is not None and not isinstance(transport_external, bool):
            raise ResearchBenchmarkContractError(
                "transport_external must be a boolean when present"
            )
        if (transport_identity is None) != (transport_external is None):
            raise ResearchBenchmarkContractError(
                "transport identity and external decision must be supplied together"
            )
        if transport_external is False:
            raise ResearchBenchmarkContractError(
                "external provider attestation cannot certify a local transport"
            )
        object.__setattr__(
            self,
            "transport_identity_digest",
            transport_identity,
        )
        object.__setattr__(self, "transport_external", transport_external)
        if not self.fresh_request:
            raise ResearchBenchmarkContractError("external model attestation requires a fresh request")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": "natsume.research.external-provider-attestation.v1",
            "execution_id": self.execution_id,
            "provider": self.provider,
            "model": self.model,
            "route_digest": self.route_digest,
            "response_digest": self.response_digest,
            "total_tokens": self.total_tokens,
            "cost_micros": self.cost_micros,
            "provider_latency_ms": self.provider_latency_ms,
            "total_latency_ms": self.total_latency_ms,
            "fresh_request": True,
        }
        if self.transport_identity_digest is not None:
            result["transport_identity_digest"] = self.transport_identity_digest
            result["transport_external"] = True
        return result

    @property
    def attestation_digest(self) -> str:
        """Return the canonical digest that a model receipt must reference."""

        return _json_digest(self.to_dict())

    @property
    def attestation_ref(self) -> str:
        """Return the opaque durable reference for this exact provider call."""

        return f"research-provider-attestation://sha256/{self.attestation_digest}"


@dataclass(frozen=True, slots=True)
class PairedResearchRunReceiptV1:
    corpus_binding_digest: str
    profile_digest: str
    budget_digest: str
    baseline_execution_id: str
    selected_execution_id: str
    baseline_model: ResearchModelExecutionReceiptV1
    selected_model: ResearchModelExecutionReceiptV1
    baseline_verifier: ResearchVerifierReceiptV1
    selected_verifier: ResearchVerifierReceiptV1
    arm_order: tuple[str, str]
    randomized_arm_order: bool
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        for name in ("corpus_binding_digest", "profile_digest", "budget_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        baseline_id = _text(self.baseline_execution_id, "baseline_execution_id", 160)
        selected_id = _text(self.selected_execution_id, "selected_execution_id", 160)
        if baseline_id == selected_id:
            raise ResearchBenchmarkContractError("paired Research arms require independent execution ids")
        if self.baseline_model.execution_id != baseline_id or self.selected_model.execution_id != selected_id:
            raise ResearchBenchmarkContractError("model receipts do not bind paired execution ids")
        if self.baseline_verifier.execution_id != baseline_id or self.selected_verifier.execution_id != selected_id:
            raise ResearchBenchmarkContractError("verifier receipts do not bind paired execution ids")
        if isinstance(self.arm_order, (str, bytes, bytearray)):
            raise ResearchBenchmarkContractError("paired Research arm_order must be an explicit sequence")
        arm_order = tuple(str(value or "").strip().upper() for value in self.arm_order)
        if len(arm_order) != 2 or set(arm_order) != {"BASELINE", "SELECTED"}:
            raise ResearchBenchmarkContractError(
                "paired Research arm_order must contain BASELINE and SELECTED exactly once"
            )
        if not self.randomized_arm_order:
            raise ResearchBenchmarkContractError("paired Research receipt requires a randomized arm order")
        if self.production_influence_enabled:
            raise ResearchBenchmarkContractError("Research shadow receipt cannot enable production influence")
        object.__setattr__(self, "baseline_execution_id", baseline_id)
        object.__setattr__(self, "selected_execution_id", selected_id)
        object.__setattr__(self, "arm_order", arm_order)
        object.__setattr__(self, "randomized_arm_order", True)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def benefit_eligible(self) -> bool:
        return (
            bool(self.randomized_arm_order)
            and self.baseline_model.benefit_eligible
            and self.selected_model.benefit_eligible
            and self.baseline_verifier.structurally_eligible
            and self.selected_verifier.structurally_eligible
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_BENCHMARK_SCHEMA,
            "corpus_binding_digest": self.corpus_binding_digest,
            "profile_digest": self.profile_digest,
            "budget_digest": self.budget_digest,
            "baseline_execution_id": self.baseline_execution_id,
            "selected_execution_id": self.selected_execution_id,
            "baseline_model": self.baseline_model.to_dict(),
            "selected_model": self.selected_model.to_dict(),
            "baseline_verifier": self.baseline_verifier.to_dict(),
            "selected_verifier": self.selected_verifier.to_dict(),
            "arm_order": list(self.arm_order),
            "randomized_arm_order": self.randomized_arm_order,
            "benefit_eligible": self.benefit_eligible,
            "production_influence_enabled": False,
        }


__all__ = [
    "RESEARCH_BENCHMARK_SCHEMA",
    "RESEARCH_EVIDENCE_CLASSES",
    "PairedResearchRunReceiptV1",
    "ResearchArmPlanV1",
    "ResearchBenchmarkContractError",
    "ResearchCorpusBindingV1",
    "ResearchCitationSpanV1",
    "ResearchExecutionBudgetV1",
    "ResearchExecutionProfileV1",
    "ResearchModelExecutionReceiptV1",
    "ResearchVerifierReceiptV1",
    "ExternalProviderRunAttestationV1",
]
