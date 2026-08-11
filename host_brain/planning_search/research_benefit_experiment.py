"""Fixed-corpus runner for the real A15 Research benefit experiment.

This module is an acceptance adapter, not a production planning route.  It
freezes a checked-in 24-workload corpus, obtains both arms from the common
PlanningSearch kernel, executes fresh canonical model requests and persists
only digest/usage/score evidence.  Source bodies and model answers never enter
the Research or cohort ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
)
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchCohortPairedEvidenceV1,
)
from host_brain.contracts.planning_search.research_domain_models import (
    RESEARCH_DOMAIN,
    ResearchSourceDescriptor,
    ResearchTaskSnapshot,
)
from host_brain.model_runtime.contracts import PromptRef
from host_brain.model_runtime.live_prompts import RESEARCH_EXECUTION_PROMPT_ID
from host_brain.orchestration.goal_planning import (
    GoalPlanningRequest,
    GoalPlanningService,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    SQLiteResearchShadowLedger,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
)
from host_brain.planning_search.contracts import (
    MCTSMode,
    PlanningGoal,
    SearchBudget,
)
from host_brain.planning_search.research_benchmark_corpus import (
    FrozenResearchCorpusLoader,
    FrozenResearchCorpusManifest,
    ResearchBenchmarkCorpusError,
    ResearchCorpusDocument,
    ResearchCorpusManifestEntry,
)
from host_brain.planning_search.research_benchmark_verifier import (
    ResearchCitationVerifier,
)
from host_brain.planning_search.research_citation_output_parser import (
    RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
    ResearchCitationOutputParserV1,
)
from host_brain.planning_search.research_domain import ResearchPlanningDomain
from host_brain.planning_search.research_paired_execution import (
    ResearchArmRuntimeInputV1,
    ResearchPairedExecutionRunner,
)
from host_brain.planning_search.research_quality_verifier import (
    AttestedResearchQualityVerifier,
)


FIXED_RESEARCH_COHORT_SCHEMA = "natsume.research-benefit-fixed-corpus.v1"
RESEARCH_EXECUTION_PROFILE_ID = "a15-research-citation-json-v1"

_EXECUTION_SYSTEM = """
你是冻结语料 Research 执行器。只依据调用方给出的来源正文回答目标，不使用外部知识。
输出只能是一个 JSON 对象，且只能包含 answer 与 citations 两个字段。
answer 是简洁中文结论；citations 是数组，至少包含一个与结论相关的引用。
每个引用必须逐字复制 citation_options 中的完整对象，不得自行计算或修改 source_id、字节偏移或摘要。
资料不足时明确说明不足，不得编造。不要输出 Markdown、解释性前缀或额外字段。
""".strip()


class ResearchBenefitExperimentError(RuntimeError):
    """The frozen queue or a real paired execution failed closed."""


@dataclass(frozen=True, slots=True)
class FixedResearchSourceV1:
    source_id: str
    source_ref: str
    relative_path: str
    content_digest: str
    byte_count: int
    authority_tier: str
    source_kind: str
    relevance_score: float
    estimated_read_cost: float
    topic_tags: tuple[str, ...]
    conflict_topics: tuple[str, ...]

    def manifest_entry(self) -> ResearchCorpusManifestEntry:
        return ResearchCorpusManifestEntry(
            source_id=self.source_id,
            source_ref=self.source_ref,
            relative_path=self.relative_path,
            content_digest=self.content_digest,
            byte_count=self.byte_count,
            authority_tier=self.authority_tier,
        )

    def planning_descriptor(self) -> ResearchSourceDescriptor:
        return ResearchSourceDescriptor(
            source_id=self.source_id,
            source_ref=self.source_ref,
            content_digest=self.content_digest,
            authority_tier=self.authority_tier,
            relevance_score=self.relevance_score,
            estimated_read_cost=self.estimated_read_cost,
            topic_tags=self.topic_tags,
            conflict_topics=self.conflict_topics,
        )


@dataclass(frozen=True, slots=True)
class FixedResearchTopicV1:
    topic_category: str
    corpus_ref: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixedResearchWorkloadV1:
    workload_id: str
    topic_category: str
    objective: str
    expected_points: tuple[str, ...]
    requires_conflict_resolution: bool


@dataclass(frozen=True, slots=True)
class FixedResearchCohortV1:
    root: Path
    config_digest: str
    cohort_id: str
    cohort_version: str
    planning_algorithm_version: str
    sources: tuple[FixedResearchSourceV1, ...]
    topics: tuple[FixedResearchTopicV1, ...]
    workloads: tuple[FixedResearchWorkloadV1, ...]

    def source(self, source_id: str) -> FixedResearchSourceV1:
        matches = tuple(item for item in self.sources if item.source_id == source_id)
        if len(matches) != 1:
            raise ResearchBenefitExperimentError("fixed source identity is missing or ambiguous")
        return matches[0]

    def topic(self, topic_category: str) -> FixedResearchTopicV1:
        matches = tuple(
            item for item in self.topics if item.topic_category == topic_category
        )
        if len(matches) != 1:
            raise ResearchBenefitExperimentError("fixed topic identity is missing or ambiguous")
        return matches[0]


@dataclass(frozen=True, slots=True)
class PreparedResearchWorkloadV1:
    definition: FixedResearchWorkloadV1
    repository_root: Path
    manifest: FrozenResearchCorpusManifest
    snapshot: ResearchTaskSnapshot
    binding: ResearchCorpusBindingV1
    profile: ResearchExecutionProfileV1
    budget: ResearchExecutionBudgetV1
    baseline_plan: ResearchArmPlanV1
    selected_plan: ResearchArmPlanV1
    baseline_input: ResearchArmRuntimeInputV1
    selected_input: ResearchArmRuntimeInputV1
    frozen_source_facts: tuple[str, ...]
    source_kinds: tuple[str, ...]
    selected_mcts_used: bool


def load_fixed_research_cohort(config_path: str | Path) -> FixedResearchCohortV1:
    path = Path(config_path).expanduser().resolve()
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchBenefitExperimentError("fixed Research cohort cannot be loaded") from exc
    if not isinstance(payload, dict) or payload.get("schema") != FIXED_RESEARCH_COHORT_SCHEMA:
        raise ResearchBenefitExperimentError("fixed Research cohort schema drifted")

    sources = tuple(_source_from_dict(item) for item in _list(payload, "sources"))
    if tuple(item.source_id for item in sources) != tuple(
        sorted(item.source_id for item in sources)
    ) or len({item.source_id for item in sources}) != len(sources):
        raise ResearchBenefitExperimentError("fixed sources must be unique and sorted")

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, dict):
        raise ResearchBenefitExperimentError("fixed Research topics must be an object")
    topics = tuple(
        _topic_from_dict(name, value) for name, value in sorted(raw_topics.items())
    )
    if len(topics) < 3:
        raise ResearchBenefitExperimentError("fixed Research cohort needs three topics")
    source_ids = {item.source_id for item in sources}
    for topic in topics:
        if len(topic.source_ids) != 3 or not set(topic.source_ids).issubset(source_ids):
            raise ResearchBenefitExperimentError("topic source set is incomplete")
        tiers = {next(item for item in sources if item.source_id == source_id).authority_tier for source_id in topic.source_ids}
        if tiers != {"primary", "independent", "secondary"}:
            raise ResearchBenefitExperimentError("each topic needs primary, independent and secondary sources")

    workloads = tuple(_workload_from_dict(item) for item in _list(payload, "workloads"))
    if len(workloads) != 24:
        raise ResearchBenefitExperimentError("fixed Research cohort requires exactly 24 workloads")
    if tuple(item.workload_id for item in workloads) != tuple(
        sorted(item.workload_id for item in workloads)
    ) or len({item.workload_id for item in workloads}) != len(workloads):
        raise ResearchBenefitExperimentError("fixed workloads must be unique and sorted")
    topic_names = {item.topic_category for item in topics}
    if {item.topic_category for item in workloads} != topic_names:
        raise ResearchBenefitExperimentError("fixed workload topic coverage drifted")

    cohort = FixedResearchCohortV1(
        root=path.parent,
        config_digest=hashlib.sha256(raw_bytes).hexdigest(),
        cohort_id=_text(payload.get("cohort_id"), "cohort_id", 160),
        cohort_version=_text(payload.get("cohort_version"), "cohort_version", 80),
        planning_algorithm_version=_text(
            payload.get("planning_algorithm_version"),
            "planning_algorithm_version",
            120,
        ),
        sources=sources,
        topics=topics,
        workloads=workloads,
    )
    _verify_all_source_files(cohort)
    return cohort


def prepare_research_workload(
    *,
    cohort: FixedResearchCohortV1,
    workload: FixedResearchWorkloadV1,
    prompt_registry: Any,
) -> PreparedResearchWorkloadV1:
    topic = cohort.topic(workload.topic_category)
    sources = tuple(cohort.source(source_id) for source_id in topic.source_ids)
    manifest = FrozenResearchCorpusManifest(
        corpus_ref=topic.corpus_ref,
        entries=tuple(item.manifest_entry() for item in sources),
    )
    loader = FrozenResearchCorpusLoader(
        repository_root=cohort.root,
        manifest=manifest,
    )
    documents = {item.source_id: item for item in loader.load_all()}
    snapshot = ResearchTaskSnapshot(
        task_ref=f"artifact://natsume/a15-research-workload/{workload.workload_id}",
        corpus_ref=topic.corpus_ref,
        objective=workload.objective,
        sources=tuple(item.planning_descriptor() for item in sources),
        requires_conflict_resolution=workload.requires_conflict_resolution,
        virtual_transition_ready=True,
        max_sources_per_plan=3,
        constraints={"network": "disabled", "production_influence": False},
    )
    binding = ResearchCorpusBindingV1.from_snapshot(
        snapshot,
        manifest_digest=manifest.manifest_digest,
    )
    baseline_result = _search(snapshot=snapshot, workload=workload, mode=MCTSMode.NEVER)
    selected_result = _search(snapshot=snapshot, workload=workload, mode=MCTSMode.AUTO)
    baseline_source_ids = _selected_source_ids(baseline_result)
    selected_source_ids = _selected_source_ids(selected_result)
    expected_baseline = tuple(
        item.source_id for item in sources if item.authority_tier == "secondary"
    )
    expected_selected = tuple(
        sorted(
            item.source_id
            for item in sources
            if item.authority_tier in {"primary", "independent"}
        )
    )
    if baseline_source_ids != expected_baseline:
        raise ResearchBenefitExperimentError("baseline did not select the frozen secondary source")
    if selected_source_ids != expected_selected or not selected_result.mcts_used:
        raise ResearchBenefitExperimentError("search arm did not use the expected L1/L2/L3 source path")

    baseline_messages = _execution_messages(
        objective=workload.objective,
        documents=tuple(documents[source_id] for source_id in baseline_source_ids),
    )
    selected_messages = _execution_messages(
        objective=workload.objective,
        documents=tuple(documents[source_id] for source_id in selected_source_ids),
    )
    baseline_prompt_digest = _prompt_digest(prompt_registry, baseline_messages)
    selected_prompt_digest = _prompt_digest(prompt_registry, selected_messages)
    if baseline_prompt_digest == selected_prompt_digest:
        raise ResearchBenefitExperimentError("paired arm prompt identities unexpectedly match")

    profile = ResearchExecutionProfileV1(
        prompt_digest=_digest_json(
            {
                "profile_id": RESEARCH_EXECUTION_PROFILE_ID,
                "prompt_id": RESEARCH_EXECUTION_PROMPT_ID,
                "system": _EXECUTION_SYSTEM,
            }
        ),
        profile_digest=_digest_json(
            {
                "profile_id": RESEARCH_EXECUTION_PROFILE_ID,
                "prompt_id": RESEARCH_EXECUTION_PROMPT_ID,
                "output_schema_digest": RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
                "cache_policy": "NONE",
            }
        ),
        output_schema_digest=RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
    )
    budget = ResearchExecutionBudgetV1(
        arm_max_tokens=1_600,
        arm_max_tool_calls=1,
        arm_max_wall_time_ms=120_000,
        pair_max_tokens=3_200,
        pair_max_wall_time_ms=240_000,
    )
    baseline_plan = ResearchArmPlanV1.from_search_result(
        f"{workload.workload_id}-baseline",
        baseline_result,
        binding=binding,
        prompt_digest=baseline_prompt_digest,
    )
    selected_plan = ResearchArmPlanV1.from_search_result(
        f"{workload.workload_id}-selected",
        selected_result,
        binding=binding,
        prompt_digest=selected_prompt_digest,
    )
    truth_documents = tuple(
        documents[item.source_id]
        for item in sources
        if item.authority_tier in {"primary", "independent"}
    )
    return PreparedResearchWorkloadV1(
        definition=workload,
        repository_root=cohort.root,
        manifest=manifest,
        snapshot=snapshot,
        binding=binding,
        profile=profile,
        budget=budget,
        baseline_plan=baseline_plan,
        selected_plan=selected_plan,
        baseline_input=ResearchArmRuntimeInputV1(
            prompt_id=RESEARCH_EXECUTION_PROMPT_ID,
            messages=baseline_messages,
            deadline_ms=90_000,
        ),
        selected_input=ResearchArmRuntimeInputV1(
            prompt_id=RESEARCH_EXECUTION_PROMPT_ID,
            messages=selected_messages,
            deadline_ms=90_000,
        ),
        frozen_source_facts=tuple(
            f"{document.source_id}：{document.content.decode('utf-8')}"
            for document in truth_documents
        ),
        source_kinds=tuple(sorted({item.source_kind for item in sources})),
        selected_mcts_used=True,
    )


async def execute_prepared_research_workload(
    *,
    prepared: PreparedResearchWorkloadV1,
    research_ledger: SQLiteResearchShadowLedger,
    external_adapter: AttestedExternalResearchAdapter,
    owner_id: str,
    cohort_run_key: str,
) -> ResearchCohortPairedEvidenceV1:
    registration_id = research_ledger.register(
        prepared.binding,
        prepared.profile,
        prepared.budget,
    )
    run_id = research_ledger.preregister_randomized_run(
        registration_id=registration_id,
        baseline=prepared.baseline_plan,
        selected=prepared.selected_plan,
        cohort_run_key=cohort_run_key,
    )
    paired = await ResearchPairedExecutionRunner(
        ledger=research_ledger,
        external_adapter=external_adapter,
        citation_extractor=ResearchCitationOutputParserV1(),
        verifier_factory=lambda: ResearchCitationVerifier(
            repository_root=prepared.repository_root,
            manifest=prepared.manifest,
            snapshot=prepared.snapshot,
            binding=prepared.binding,
        ),
        lease_duration_ms=180_000,
    ).execute_preregistered(
        run_id=run_id,
        arm_inputs={
            "BASELINE": prepared.baseline_input,
            "SELECTED": prepared.selected_input,
        },
        owner_id=owner_id,
    )
    if paired.replayed_terminal_receipt or len(paired.transient_outputs) != 2:
        raise ResearchBenefitExperimentError(
            "terminal Research replay has no private output; use a new run namespace"
        )
    transient = {item.role: item for item in paired.transient_outputs}
    quality_verifier = AttestedResearchQualityVerifier(
        external_adapter=external_adapter
    )
    baseline_quality = await quality_verifier.verify(
        run_id=run_id,
        arm_output=transient["BASELINE"],
        objective=prepared.definition.objective,
        expected_points=prepared.definition.expected_points,
        frozen_source_facts=prepared.frozen_source_facts,
        deadline_ms=60_000,
    )
    selected_quality = await quality_verifier.verify(
        run_id=run_id,
        arm_output=transient["SELECTED"],
        objective=prepared.definition.objective,
        expected_points=prepared.definition.expected_points,
        frozen_source_facts=prepared.frozen_source_facts,
        deadline_ms=60_000,
    )
    return ResearchCohortPairedEvidenceV1(
        workload_id=prepared.definition.workload_id,
        run_id=run_id,
        terminal_receipt_digest=paired.receipt_ref.rsplit("/", 1)[-1],
        terminal_receipt=paired.receipt,
        baseline_quality=baseline_quality,
        selected_quality=selected_quality,
        owner_randomized_run=True,
    )


def sanitized_member_summary(
    evidence: ResearchCohortPairedEvidenceV1,
) -> dict[str, Any]:
    receipt = evidence.terminal_receipt
    return {
        "workload_id": evidence.workload_id,
        "run_id": evidence.run_id,
        "terminal_receipt_digest": evidence.terminal_receipt_digest,
        "arm_order": list(receipt.arm_order),
        "baseline": _arm_summary(
            receipt.baseline_model,
            receipt.baseline_verifier,
            evidence.baseline_quality,
        ),
        "selected": _arm_summary(
            receipt.selected_model,
            receipt.selected_verifier,
            evidence.selected_quality,
        ),
    }


def _arm_summary(model: Any, citation: Any, quality: Any) -> dict[str, Any]:
    attestation = model.attestation
    quality_attestation = quality.attestation
    return {
        "execution_id": model.execution_id,
        "provider": None if attestation is None else attestation.provider,
        "model": None if attestation is None else attestation.model,
        "tokens": model.total_tokens,
        "cost_micros": model.cost_micros,
        "latency_ms": None if attestation is None else attestation.total_latency_ms,
        "citation_validity": citation.citation_validity,
        "quality_score": quality.quality_score,
        "quality_tokens": None if quality_attestation is None else quality_attestation.total_tokens,
        "quality_cost_micros": None if quality_attestation is None else quality_attestation.cost_micros,
        "quality_latency_ms": None if quality_attestation is None else quality_attestation.total_latency_ms,
        "severe_policy_errors": quality.severe_policy_errors,
        "severe_conflict_errors": quality.severe_conflict_errors,
        "benefit_eligible": bool(model.benefit_eligible and quality.benefit_eligible),
    }


def _search(
    *,
    snapshot: ResearchTaskSnapshot,
    workload: FixedResearchWorkloadV1,
    mode: MCTSMode,
):  # noqa: ANN202
    request = GoalPlanningRequest(
        request_id=f"{workload.workload_id}-{mode.value}",
        goal=PlanningGoal(
            goal_id=f"goal-{workload.workload_id}-{mode.value}",
            domain=RESEARCH_DOMAIN,
            objective=snapshot.objective,
            success_criteria=("形成只读、可引用的冻结证据回答",),
            frozen_context_refs=(
                snapshot.task_ref,
                snapshot.corpus_ref,
                *(item.source_ref for item in snapshot.sources),
            ),
        ),
        state=snapshot.to_virtual_state(),
        budget=SearchBudget(
            max_candidates=64,
            max_expansions=32,
            max_transitions=32,
            max_rollouts=16,
            max_depth=6,
            branching_factor=3,
        ),
        mcts_mode=mode,
    )
    result = GoalPlanningService(
        ports=ResearchPlanningDomain(snapshot=snapshot).planning_ports()
    ).search(request).result
    if not result.selected_path or result.side_effects_executed:
        raise ResearchBenefitExperimentError("Research search did not produce a pure selected path")
    return result


def _selected_source_ids(result: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(item.payload.get("source_id") or "").strip().lower()
            for item in result.selected_path
            if isinstance(item.payload, Mapping) and item.payload.get("source_id")
        )
    )


def _execution_messages(
    *,
    objective: str,
    documents: Sequence[ResearchCorpusDocument],
) -> tuple[dict[str, str], ...]:
    sources = []
    citation_options = []
    for document in documents:
        sources.append(
            {
                "source_id": document.source_id,
                "authority_tier": document.authority_tier,
                "content": document.content.decode("utf-8"),
            }
        )
        citation_options.append(
            {
                "source_id": document.source_id,
                "start_byte": 0,
                "end_byte": document.byte_count,
                "span_digest": document.content_digest,
            }
        )
    user_payload = {
        "objective": objective,
        "sources": sources,
        "citation_options": citation_options,
    }
    return (
        {"role": "system", "content": _EXECUTION_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def _prompt_digest(prompt_registry: Any, messages: tuple[dict[str, str], ...]) -> str:
    try:
        rendered = prompt_registry.render(
            PromptRef(
                RESEARCH_EXECUTION_PROMPT_ID,
                variables={"messages": messages},
            )
        )
    except Exception as exc:
        raise ResearchBenefitExperimentError("Research execution prompt cannot be rendered") from exc
    return str(rendered.digest)


def _verify_all_source_files(cohort: FixedResearchCohortV1) -> None:
    try:
        for topic in cohort.topics:
            manifest = FrozenResearchCorpusManifest(
                corpus_ref=topic.corpus_ref,
                entries=tuple(
                    cohort.source(source_id).manifest_entry()
                    for source_id in topic.source_ids
                ),
            )
            FrozenResearchCorpusLoader(
                repository_root=cohort.root,
                manifest=manifest,
            ).load_all()
    except ResearchBenchmarkCorpusError as exc:
        raise ResearchBenefitExperimentError(
            "fixed Research source content or metadata drifted"
        ) from exc


def _source_from_dict(value: Any) -> FixedResearchSourceV1:
    if not isinstance(value, dict):
        raise ResearchBenefitExperimentError("fixed source must be an object")
    source_kind = _text(value.get("source_kind"), "source_kind", 32).upper()
    if source_kind not in {"LOCAL_FILE", "WEB"}:
        raise ResearchBenefitExperimentError("fixed source_kind is invalid")
    return FixedResearchSourceV1(
        source_id=_text(value.get("source_id"), "source_id", 120),
        source_ref=_text(value.get("source_ref"), "source_ref", 1_024),
        relative_path=_text(value.get("relative_path"), "relative_path", 1_024),
        content_digest=_digest(value.get("content_digest"), "content_digest"),
        byte_count=_positive_int(value.get("byte_count"), "byte_count"),
        authority_tier=_text(value.get("authority_tier"), "authority_tier", 32).lower(),
        source_kind=source_kind,
        relevance_score=_unit(value.get("relevance_score"), "relevance_score"),
        estimated_read_cost=_unit(value.get("estimated_read_cost"), "estimated_read_cost"),
        topic_tags=_texts(value.get("topic_tags"), "topic_tags"),
        conflict_topics=_texts(value.get("conflict_topics"), "conflict_topics"),
    )


def _topic_from_dict(name: Any, value: Any) -> FixedResearchTopicV1:
    if not isinstance(value, dict):
        raise ResearchBenefitExperimentError("fixed topic must be an object")
    return FixedResearchTopicV1(
        topic_category=_text(name, "topic_category", 120),
        corpus_ref=_text(value.get("corpus_ref"), "corpus_ref", 1_024),
        source_ids=_texts(value.get("source_ids"), "source_ids"),
    )


def _workload_from_dict(value: Any) -> FixedResearchWorkloadV1:
    if not isinstance(value, dict):
        raise ResearchBenefitExperimentError("fixed workload must be an object")
    requires_conflict = value.get("requires_conflict_resolution")
    if not isinstance(requires_conflict, bool):
        raise ResearchBenefitExperimentError("requires_conflict_resolution must be a boolean")
    expected = _texts(value.get("expected_points"), "expected_points")
    if len(expected) != 2:
        raise ResearchBenefitExperimentError("each fixed workload needs exactly two expected points")
    return FixedResearchWorkloadV1(
        workload_id=_text(value.get("workload_id"), "workload_id", 160),
        topic_category=_text(value.get("topic_category"), "topic_category", 120),
        objective=_text(value.get("objective"), "objective", 4_000),
        expected_points=expected,
        requires_conflict_resolution=requires_conflict,
    )


def _list(value: Mapping[str, Any], field: str) -> list[Any]:
    result = value.get(field)
    if not isinstance(result, list):
        raise ResearchBenefitExperimentError(f"{field} must be a list")
    return result


def _texts(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ResearchBenefitExperimentError(f"{field} must be a non-empty list")
    result = tuple(_text(item, f"{field}[]", 4_000) for item in value)
    if len(set(result)) != len(result):
        raise ResearchBenefitExperimentError(f"{field} must contain unique values")
    return result


def _text(value: Any, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ResearchBenefitExperimentError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ResearchBenefitExperimentError(f"{field} must be a SHA-256 digest")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ResearchBenefitExperimentError(f"{field} must be a positive integer")
    return value


def _unit(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchBenefitExperimentError(f"{field} must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ResearchBenefitExperimentError(f"{field} must be within [0, 1]")
    return result


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FIXED_RESEARCH_COHORT_SCHEMA",
    "FixedResearchCohortV1",
    "FixedResearchSourceV1",
    "FixedResearchTopicV1",
    "FixedResearchWorkloadV1",
    "PreparedResearchWorkloadV1",
    "ResearchBenefitExperimentError",
    "execute_prepared_research_workload",
    "load_fixed_research_cohort",
    "prepare_research_workload",
    "sanitized_member_summary",
]
