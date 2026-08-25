from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.orchestration.goal_planning import GoalPlanningRequest, GoalPlanningService
from host_brain.contracts.planning_search.contracts import MCTSMode, PlanningGoal, SearchBudget, SearchCandidate
from host_brain.planning_search.research_domain import (
    ResearchCognitiveRanker,
    ResearchMCTSCondition,
    ResearchPlanningDomain,
    ResearchRewardModel,
    ResearchSuccessorCatalog,
)
from host_brain.contracts.planning_search.research_domain_models import (
    RESEARCH_DOMAIN,
    ResearchPlanningError,
    ResearchSourceDescriptor,
    ResearchTaskSnapshot,
)


def _source(
    source_id: str,
    *,
    tier: str,
    relevance: float,
    cost: float,
    digest: str,
) -> ResearchSourceDescriptor:
    return ResearchSourceDescriptor(
        source_id=source_id,
        source_ref=f"artifact://research-corpus/d20/{source_id}",
        content_digest=digest,
        authority_tier=tier,
        relevance_score=relevance,
        estimated_read_cost=cost,
        topic_tags=("research-runtime",),
        conflict_topics=("execution-scope",),
    )


def _snapshot(*, conflict: bool = True) -> ResearchTaskSnapshot:
    return ResearchTaskSnapshot(
        task_ref="artifact://research-workloads/d20/runtime-boundary",
        corpus_ref="artifact://research-corpus/d20/runtime-boundary",
        objective="Determine the supported readonly Research execution boundary from frozen sources.",
        sources=(
            _source("implementation", tier="primary", relevance=0.87, cost=0.18, digest="a" * 64),
            _source("independent-interface", tier="independent", relevance=0.78, cost=0.22, digest="b" * 64),
            _source("secondary-summary", tier="secondary", relevance=0.97, cost=0.05, digest="c" * 64),
        ),
        requires_conflict_resolution=conflict,
        virtual_transition_ready=True,
        max_sources_per_plan=3,
        constraints={"network": "disabled", "production_influence": False},
    )


def _request(
    snapshot: ResearchTaskSnapshot,
    *,
    mcts_mode: MCTSMode = MCTSMode.ALWAYS,
) -> GoalPlanningRequest:
    return GoalPlanningRequest(
        request_id="d20-research-planning-unit",
        goal=PlanningGoal(
            goal_id="goal-research-d20-shadow",
            domain=RESEARCH_DOMAIN,
            objective=snapshot.objective,
            success_criteria=("select a readonly evidence path",),
            frozen_context_refs=(
                snapshot.task_ref,
                snapshot.corpus_ref,
                *(source.source_ref for source in snapshot.sources),
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
        mcts_mode=mcts_mode,
    )


def _next(catalog: ResearchSuccessorCatalog, state, candidate_id: str, path):
    candidates = {
        item.candidate_id: item
        for item in catalog.expand(state=state, path=path, depth=len(path), limit=32, context={})
    }
    candidate = candidates[candidate_id]
    return catalog.transition(state=state, candidate=candidate, path=(*path, candidate), context={}), candidate


def test_snapshot_has_no_dedicated_secret_fields_and_rejects_oracle_keys_or_host_paths() -> None:
    snapshot = _snapshot()
    public = snapshot.to_public_dict()

    assert public["sources"][0]["source_ref"].startswith("artifact://")
    assert "source_body" not in public
    assert "answer_key" not in public
    assert "verifier_decision" not in public
    assert "path" not in public["sources"][0]

    with pytest.raises(ResearchPlanningError):
        ResearchSourceDescriptor(
            source_id="bad-source",
            source_ref="file://C:/owner/secret.txt",
            content_digest="d" * 64,
            authority_tier="primary",
            relevance_score=0.5,
            estimated_read_cost=0.1,
        )
    with pytest.raises(ResearchPlanningError):
        ResearchTaskSnapshot(
            task_ref="artifact://research-workloads/d20/bad",
            corpus_ref="artifact://research-corpus/d20/bad",
            objective="Bad oracle leak",
            sources=(_source("primary", tier="primary", relevance=0.5, cost=0.1, digest="e" * 64),),
            constraints={"nested": {"verifier_expected_answer": "leak"}},
        )
    with pytest.raises(ResearchPlanningError):
        ResearchTaskSnapshot(
            task_ref="artifact://research-workloads/d20/path-leak",
            corpus_ref="artifact://research-corpus/d20/path-leak",
            objective="Read C:\\owner\\private.txt before planning.",
            sources=(_source("primary", tier="primary", relevance=0.5, cost=0.1, digest="f" * 64),),
        )
    with pytest.raises(ResearchPlanningError):
        ResearchTaskSnapshot(
            task_ref="artifact://research-workloads/d20/constraint-leak",
            corpus_ref="artifact://research-corpus/d20/constraint-leak",
            objective="Keep planning metadata path-free.",
            sources=(_source("primary", tier="primary", relevance=0.5, cost=0.1, digest="0" * 64),),
            constraints={"note": "inspect /tmp/private-source before planning"},
        )
    for path_like_objective in (
        "path=/tmp/private-source",
        "see(/tmp/private-source)",
        "local=/home/test/document",
    ):
        with pytest.raises(ResearchPlanningError):
            ResearchTaskSnapshot(
                task_ref="artifact://research-workloads/d20/posix-path-leak",
                corpus_ref="artifact://research-corpus/d20/posix-path-leak",
                objective=path_like_objective,
                sources=(_source("primary", tier="primary", relevance=0.5, cost=0.1, digest="1" * 64),),
            )


def test_dynamic_catalog_requires_query_primary_independent_crosscheck_resolution_and_stop() -> None:
    snapshot = _snapshot(conflict=True)
    catalog = ResearchSuccessorCatalog(snapshot=snapshot)
    state = snapshot.to_virtual_state()
    path: tuple[SearchCandidate, ...] = ()

    state, query = _next(catalog, state, "research.query.scope", path)
    path = (*path, query)
    state, primary = _next(catalog, state, "research.primary.implementation", path)
    path = (*path, primary)
    state, independent = _next(catalog, state, "research.independent.independent-interface", path)
    path = (*path, independent)
    state, cross_check = _next(catalog, state, "research.cross-check.evidence", path)
    path = (*path, cross_check)
    state, resolution = _next(catalog, state, "research.resolve-conflict.scope", path)
    path = (*path, resolution)
    state, stop = _next(catalog, state, "research.stop.synthesis", path)
    path = (*path, stop)

    assert [item.candidate_id for item in path] == [
        "research.query.scope",
        "research.primary.implementation",
        "research.independent.independent-interface",
        "research.cross-check.evidence",
        "research.resolve-conflict.scope",
        "research.stop.synthesis",
    ]
    assert state.facts["phase"] == "stopped"
    assert list(state.facts["read_source_ids"]) == ["implementation", "independent-interface"]


def test_catalog_rejects_forged_or_state_drifted_candidate() -> None:
    snapshot = _snapshot()
    catalog = ResearchSuccessorCatalog(snapshot=snapshot)
    state = snapshot.to_virtual_state()
    forged = SearchCandidate(
        candidate_id="research.query.scope",
        prior_score=1.0,
        payload={"research_action": "query", "mode": "PRODUCTION"},
    )

    with pytest.raises(ResearchPlanningError):
        catalog.transition(state=state, candidate=forged, path=(forged,), context={})
    with pytest.raises(ResearchPlanningError):
        catalog.expand(
            state=state.replace(facts={"snapshot_digest": "0" * 64, "sources": []}),
            path=(),
            depth=0,
            limit=8,
            context={},
        )


def test_l1_l2_l3_selects_full_public_metadata_path_without_a_runtime_side_effect() -> None:
    snapshot = _snapshot(conflict=True)
    result = GoalPlanningService(
        ports=ResearchPlanningDomain(snapshot=snapshot).planning_ports()
    ).search(_request(snapshot)).result

    assert result.mcts_used is True
    assert result.side_effects_executed is False
    assert [item.candidate_id for item in result.selected_path] == [
        "research.query.scope",
        "research.primary.implementation",
        "research.independent.independent-interface",
        "research.cross-check.evidence",
        "research.resolve-conflict.scope",
        "research.stop.synthesis",
    ]
    assert result.state_snapshot == snapshot.to_virtual_state()


def test_standard_domain_wiring_keeps_l3_unavailable_until_transition_metadata_is_ready() -> None:
    snapshot = ResearchTaskSnapshot(
        task_ref="artifact://research-workloads/d20/no-l3",
        corpus_ref="artifact://research-corpus/d20/no-l3",
        objective="Plan a bounded readonly research task without a transition model.",
        sources=(_source("primary-only", tier="primary", relevance=0.5, cost=0.1, digest="f" * 64),),
        virtual_transition_ready=False,
    )

    decision = GoalPlanningService(
        ports=ResearchPlanningDomain(snapshot=snapshot).planning_ports()
    ).search(_request(snapshot, mcts_mode=MCTSMode.ALWAYS))

    assert decision.result.mcts_used is False
    assert any(
        event.event == "mcts_skipped"
        and event.details["reason"] == "simulation_ports_not_configured"
        for event in decision.result.trace
    )


def test_pure_research_domain_does_not_depend_on_worker_model_or_oracle_runtime() -> None:
    source = Path("host_brain/planning_search/research_domain.py").read_text(encoding="utf-8")

    for forbidden in (
        "browser_researcher",
        "agent_runtime",
        "model_runtime",
        "requests",
        "sqlite3",
        "research_benchmark_oracle",
    ):
        assert forbidden not in source
