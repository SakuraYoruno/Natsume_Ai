from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from host_brain.planning_search import (
    CandidateScore,
    MCTSMode,
    PlanningSearchService,
    SearchBudget,
    SearchCandidate,
    SearchRequest,
    SearchStatus,
)


class _StaticExpander:
    def __init__(self, candidates: Sequence[Any]) -> None:
        self.candidates = tuple(candidates)
        self.calls = 0

    def expand(self, **kwargs: Any) -> Sequence[Any]:
        self.calls += 1
        return self.candidates


def test_foundation_has_no_pentest_dependency() -> None:
    package = Path(__file__).parents[2] / "host_brain" / "planning_search"
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py")))

    assert "host_brain.pentest" not in source
    assert "..pentest" not in source
    assert ".pentest" not in source


def test_l1_expansion_is_stable_deduplicated_and_simulation_only() -> None:
    expander = _StaticExpander(
        [
            SearchCandidate("b", prior_score=0.6),
            SearchCandidate("a", prior_score=0.6),
            SearchCandidate("a", prior_score=0.2),
            SearchCandidate("unsafe", prior_score=1.0, simulation_only=False),
            object(),
        ]
    )
    result = PlanningSearchService(expander=expander).search(
        SearchRequest("l1", {}, mcts_mode=MCTSMode.NEVER)
    )

    assert result.status is SearchStatus.SELECTED
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_id == "a"
    assert [item.candidate.candidate_id for item in result.ranked] == ["a", "b"]
    assert result.counters.candidates == 2
    assert result.side_effects_executed is False
    l1_event = next(item for item in result.trace if item.event == "candidates_expanded")
    assert l1_event.details["rejected"] == [
        "duplicate_candidate:a",
        "invalid_candidate_type",
        "non_simulation_candidate:unsafe",
    ]


def test_l2_is_an_injected_ranking_port_with_safe_fallback() -> None:
    class Ranker:
        def rank(self, **kwargs: Any) -> Sequence[CandidateScore]:
            return [
                CandidateScore("slow", 0.95),
                CandidateScore("slow", 0.90),
                CandidateScore("unknown", 1.0),
            ]

    expander = _StaticExpander(
        [
            SearchCandidate("fast", prior_score=0.8),
            SearchCandidate("slow", prior_score=0.2),
        ]
    )
    result = PlanningSearchService(expander=expander, cognitive_ranker=Ranker()).search(
        SearchRequest("l2", {}, mcts_mode=MCTSMode.NEVER)
    )

    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_id == "slow"
    assert result.ranked[0].source == "l2_cognitive"
    assert result.ranked[0].score == 0.95
    rank_event = next(item for item in result.trace if item.event == "candidates_ranked")
    assert rank_event.details == {"ignored": 1, "scores": {"slow": 0.95}}


class _GraphExpander:
    def expand(
        self,
        *,
        state: Mapping[str, Any],
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]:
        del state, limit, context
        if depth == 0:
            return [
                SearchCandidate("short", prior_score=0.52),
                SearchCandidate("long", prior_score=0.50),
            ]
        if depth == 1 and path[0].candidate_id == "short":
            return [SearchCandidate("dead_end", prior_score=0.8)]
        if depth == 1 and path[0].candidate_id == "long":
            return [SearchCandidate("goal", prior_score=0.7)]
        return ()


class _VirtualTransition:
    def transition(
        self,
        *,
        state: Mapping[str, Any],
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del path, context
        return {**state, "last": candidate.candidate_id}


class _GoalReward:
    def evaluate(
        self,
        *,
        initial_state: Mapping[str, Any],
        state: Mapping[str, Any],
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, state, context
        return 1.0 if path[-1].candidate_id == "goal" else 0.0


def test_conditional_mcts_rollout_backprop_and_selected_path() -> None:
    budget = SearchBudget(
        max_candidates=20,
        max_expansions=20,
        max_transitions=20,
        max_rollouts=6,
        max_depth=2,
        branching_factor=3,
        mcts_score_gap=0.05,
    )
    result = PlanningSearchService(
        expander=_GraphExpander(),
        transition=_VirtualTransition(),
        reward_model=_GoalReward(),
    ).search(SearchRequest("mcts", {}, budget=budget, mcts_mode=MCTSMode.AUTO))

    assert result.status is SearchStatus.SELECTED
    assert result.mcts_used is True
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_id == "long"
    assert [item.candidate_id for item in result.selected_path] == ["long", "goal"]
    assert result.ranked[0].visits > 0
    assert result.ranked[0].mean_reward == 1.0
    assert result.counters.rollouts == 6
    assert any(item.event == "rollout_backpropagated" for item in result.trace)


def test_auto_mcts_skips_when_top_score_is_clear() -> None:
    expander = _StaticExpander(
        [SearchCandidate("clear", 0.9), SearchCandidate("other", 0.1)]
    )
    result = PlanningSearchService(
        expander=expander,
        transition=_VirtualTransition(),
        reward_model=_GoalReward(),
    ).search(
        SearchRequest(
            "skip",
            {},
            budget=SearchBudget(max_rollouts=3, mcts_score_gap=0.1),
            mcts_mode=MCTSMode.AUTO,
        )
    )

    assert result.mcts_used is False
    assert result.counters.rollouts == 0
    skipped = next(item for item in result.trace if item.event == "mcts_skipped")
    assert skipped.details["reason"] == "score_gap_clear"


def test_deadline_and_all_layer_budgets_are_enforced() -> None:
    expired_expander = _StaticExpander([SearchCandidate("never")])
    expired = PlanningSearchService(expander=expired_expander, clock_ms=lambda: 100.0).search(
        SearchRequest(
            "expired",
            {},
            budget=SearchBudget(deadline_ms=100.0),
            mcts_mode=MCTSMode.NEVER,
        )
    )
    assert expired.status is SearchStatus.DEADLINE_REACHED
    assert expired_expander.calls == 0
    assert expired.counters.to_dict() == {
        "candidates": 0,
        "expansions": 0,
        "transitions": 0,
        "rollouts": 0,
    }

    bounded = PlanningSearchService(expander=_GraphExpander()).search(
        SearchRequest(
            "bounded",
            {},
            budget=SearchBudget(
                max_candidates=1,
                max_expansions=1,
                max_transitions=0,
                max_rollouts=0,
                max_depth=1,
                branching_factor=1,
            ),
            mcts_mode=MCTSMode.NEVER,
        )
    )
    assert bounded.status is SearchStatus.SELECTED
    assert bounded.counters.candidates == 1
    assert bounded.counters.expansions == 1
    assert bounded.stop_reason == "candidate_budget_exhausted"


def test_trace_and_result_are_deterministic() -> None:
    def run() -> dict[str, Any]:
        budget = SearchBudget(
            max_candidates=20,
            max_expansions=20,
            max_transitions=20,
            max_rollouts=4,
            max_depth=2,
            branching_factor=3,
        )
        return PlanningSearchService(
            expander=_GraphExpander(),
            transition=_VirtualTransition(),
            reward_model=_GoalReward(),
            clock_ms=lambda: 10.0,
        ).search(
            SearchRequest("deterministic", {}, budget=budget, mcts_mode=MCTSMode.ALWAYS)
        ).to_dict()

    first = run()
    second = run()
    assert first == second
    assert first["trace_digest"] == second["trace_digest"]
    assert all("timestamp" not in event["details"] for event in first["trace"])


def test_candidate_payload_rejects_executable_objects() -> None:
    with pytest.raises(ValueError, match="JSON-safe"):
        SearchCandidate("callable", payload={"execute": lambda: None})
