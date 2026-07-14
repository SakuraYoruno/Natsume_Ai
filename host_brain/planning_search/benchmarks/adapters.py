from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import CandidateScore, SearchCandidate
from .contracts import FrozenAction, FrozenScenario, ReplayOutcome


class FrozenScenarioAdapter:
    """Pure-data adapter implementing every PlanningSearch benchmark port."""

    domain = ""

    def __init__(self, scenario: FrozenScenario) -> None:
        if scenario.domain != self.domain:
            raise ValueError(f"{type(self).__name__} cannot run {scenario.domain!r} scenarios")
        self.scenario = scenario

    @property
    def initial_virtual_state(self) -> dict[str, Any]:
        return {
            "node_id": self.scenario.initial_state,
            "valid_so_far": True,
            "action_ids": [],
        }

    def expand(
        self,
        *,
        state: Mapping[str, Any],
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]:
        del path, depth, context
        node_id = str(state.get("node_id") or "")
        return tuple(
            self._candidate(self.scenario.actions[action_id])
            for action_id in self.scenario.expansions.get(node_id, ())[:limit]
        )

    def rank(
        self,
        *,
        state: Mapping[str, Any],
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Sequence[CandidateScore]:
        del state, context
        return tuple(
            CandidateScore(
                candidate_id=candidate.candidate_id,
                score=self.scenario.actions[candidate.candidate_id].cognitive_score,
                reason_code="frozen_shadow_cognitive_score",
            )
            for candidate in candidates
        )

    def transition(
        self,
        *,
        state: Mapping[str, Any],
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del path, context
        action = self.scenario.actions[candidate.candidate_id]
        if str(state.get("node_id") or "") != action.from_state:
            raise ValueError("candidate is not valid for the virtual state")
        return {
            "node_id": action.to_state,
            "valid_so_far": bool(state.get("valid_so_far", True)) and action.valid,
            "action_ids": [*state.get("action_ids", []), action.action_id],
        }

    def evaluate(
        self,
        *,
        initial_state: Mapping[str, Any],
        state: Mapping[str, Any],
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, path, context
        state_spec = self.scenario.states[str(state.get("node_id") or "")]
        reward = float(state_spec.get("reward", 0.0))
        if not bool(state.get("valid_so_far", True)):
            reward = min(reward, -1.0)
        return reward

    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool:
        del candidates, scores, context
        return self.scenario.requires_lookahead

    def replay(self, path: Sequence[SearchCandidate]) -> ReplayOutcome:
        state: Mapping[str, Any] = self.initial_virtual_state
        invalid = 0
        for index, candidate in enumerate(path, start=1):
            action = self.scenario.actions.get(candidate.candidate_id)
            if action is None or action.from_state != str(state.get("node_id") or ""):
                invalid += 1
                return ReplayOutcome(False, False, invalid, index, "__invalid_path__")
            if not action.valid:
                invalid += 1
            state = self.transition(state=state, candidate=candidate, path=tuple(path[:index]), context={})
        final_state = str(state.get("node_id") or "")
        state_spec = self.scenario.states[final_state]
        return ReplayOutcome(
            success=bool(state_spec.get("success")) and bool(state.get("valid_so_far", True)),
            validation_passed=bool(state_spec.get("validation_passed")) and bool(state.get("valid_so_far", True)),
            invalid_action_count=invalid,
            selected_action_count=len(path),
            final_state=final_state,
        )

    def _candidate(self, action: FrozenAction) -> SearchCandidate:
        return SearchCandidate(
            candidate_id=action.action_id,
            prior_score=action.prior_score,
            payload={
                "domain": self.domain,
                "action_summary": action.explanation,
            },
            labels=(self.domain, "simulation_shadow"),
            simulation_only=True,
        )


class CodeShadowBenchmarkAdapter(FrozenScenarioAdapter):
    domain = "code"


class ResearchShadowBenchmarkAdapter(FrozenScenarioAdapter):
    domain = "research"


def adapter_for(scenario: FrozenScenario) -> FrozenScenarioAdapter:
    if scenario.domain == "code":
        return CodeShadowBenchmarkAdapter(scenario)
    if scenario.domain == "research":
        return ResearchShadowBenchmarkAdapter(scenario)
    raise ValueError(f"unsupported benchmark domain: {scenario.domain}")
