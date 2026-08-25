"""Goal-driven facade over the one domain-neutral PlanningSearch kernel.

This module is intentionally a planning boundary only.  It freezes a goal and
virtual state, delegates to ``PlanningSearchService``, and returns a pure
``SearchResult``.  It does not compile, admit, persist, dispatch, or execute
the selected path; those operations remain explicit downstream boundaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from host_brain.contracts.planning_search.contracts import (
    MCTSMode,
    PlanningGoal,
    SearchBudget,
    SearchRequest,
    SearchResult,
    VirtualStateSnapshot,
    _freeze_json,
    _thaw_json,
)
from host_brain.planning_search.ports import (
    CognitiveRankingPort,
    MCTSConditionPort,
    RewardModelPort,
    SuccessorCatalogPort,
    VirtualTransitionPort,
)
from host_brain.planning_search.service import PlanningSearchService


GOAL_PLANNING_CONTEXT_SCHEMA = "natsume.goal-planning.context.v1"


class GoalPlanningError(ValueError):
    """A goal-planning request is malformed before any search begins."""


@dataclass(frozen=True, slots=True)
class PlanningDomainPorts:
    """Pure domain adapters consumed by the shared L1/L2/L3 kernel."""

    successors: SuccessorCatalogPort
    ranker: CognitiveRankingPort | None = None
    transition: VirtualTransitionPort | None = None
    reward: RewardModelPort | None = None
    mcts_condition: MCTSConditionPort | None = None


@dataclass(frozen=True, slots=True)
class GoalPlanningRequest:
    """One immutable search request for a governor-approved planning goal."""

    request_id: str
    goal: PlanningGoal
    state: VirtualStateSnapshot
    budget: SearchBudget = field(default_factory=SearchBudget)
    context: Mapping[str, Any] = field(default_factory=dict)
    mcts_mode: MCTSMode = MCTSMode.AUTO

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        if not request_id:
            raise GoalPlanningError("request_id must not be empty")
        if not isinstance(self.goal, PlanningGoal):
            raise GoalPlanningError("goal must be a PlanningGoal")
        if not isinstance(self.state, VirtualStateSnapshot):
            raise GoalPlanningError("state must be a VirtualStateSnapshot")
        if self.goal.domain != self.state.domain:
            raise GoalPlanningError("goal and virtual state domains must match")
        try:
            context = _freeze_json(_json_copy(dict(self.context)))
        except (TypeError, ValueError) as exc:
            raise GoalPlanningError("context must be JSON-safe") from exc
        mode = self.mcts_mode if isinstance(self.mcts_mode, MCTSMode) else MCTSMode(str(self.mcts_mode))
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "mcts_mode", mode)

    def to_search_request(self) -> SearchRequest:
        """Build the sole kernel request without exposing any execution port."""

        context = _thaw_json(self.context)
        context["goal_planning"] = {
            "schema_version": GOAL_PLANNING_CONTEXT_SCHEMA,
            "goal": self.goal.to_dict(),
            "virtual_state": self.state.to_dict(),
        }
        return SearchRequest(
            request_id=self.request_id,
            initial_state=self.state,
            budget=self.budget,
            context=context,
            mcts_mode=self.mcts_mode,
            state_snapshot=self.state,
        )


@dataclass(frozen=True, slots=True)
class GoalPlanningDecision:
    """Pure selected/empty result bound to the exact goal and virtual state."""

    request: GoalPlanningRequest
    result: SearchResult

    def __post_init__(self) -> None:
        if self.result.request_id != self.request.request_id:
            raise GoalPlanningError("search result request_id differs from frozen request")
        if self.result.virtual_state_schema != self.request.state.schema_version:
            raise GoalPlanningError("search result virtual state schema drifted")
        if self.result.virtual_state_digest != self.request.state.state_digest:
            raise GoalPlanningError("search result virtual state digest drifted")


class GoalPlanningService:
    """Run a goal through the existing pure L1/L2/L3 PlanningSearch kernel."""

    def __init__(self, *, ports: PlanningDomainPorts) -> None:
        if not isinstance(ports, PlanningDomainPorts):
            raise TypeError("ports must be PlanningDomainPorts")
        self._ports = ports

    def search(self, request: GoalPlanningRequest) -> GoalPlanningDecision:
        if not isinstance(request, GoalPlanningRequest):
            raise TypeError("request must be GoalPlanningRequest")
        kernel = PlanningSearchService(
            expander=requested_successors(self._ports),
            cognitive_ranker=self._ports.ranker,
            transition=self._ports.transition,
            reward_model=self._ports.reward,
            mcts_condition=self._ports.mcts_condition,
        )
        result = kernel.search(request.to_search_request())
        return GoalPlanningDecision(request=request, result=result)


def build_replan_goal_request(
    request: GoalPlanningRequest,
    *,
    failure_evidence_refs: tuple[str, ...],
    accepted_artifact_refs: tuple[str, ...] = (),
) -> GoalPlanningRequest:
    """Freeze durable failure inputs into a *new* pure replan request.

    This helper neither validates a live task graph nor performs admission; a
    caller must obtain those refs from the durable recovery boundary.  The
    returned request can only describe the next search, which must later be
    compiled as a new immutable graph revision by ``PlanningGraphCompiler``.
    """

    if not isinstance(request, GoalPlanningRequest):
        raise TypeError("request must be GoalPlanningRequest")
    failures = _governed_refs(failure_evidence_refs, field_name="failure_evidence_refs")
    if not failures:
        raise GoalPlanningError("failure_evidence_refs must not be empty")
    accepted = _governed_refs(accepted_artifact_refs, field_name="accepted_artifact_refs")
    context = _thaw_json(request.context)
    context["replan"] = {
        "failure_evidence_refs": list(failures),
        "accepted_artifact_refs": list(accepted),
        "previous_virtual_state_digest": request.state.state_digest,
    }
    state_facts = _json_copy(request.state.identity_dict()["facts"])
    state_facts["replan_failure_evidence_refs"] = list(failures)
    state_facts["accepted_artifact_refs"] = list(accepted)
    replan_state = request.state.replace(facts=state_facts)
    return replace(request, state=replan_state, context=context)


def requested_successors(ports: PlanningDomainPorts) -> SuccessorCatalogPort:
    """Small explicit helper that documents the only L1 boundary supplied."""

    return ports.successors


def _governed_refs(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, tuple):
        raise GoalPlanningError(f"{field_name} must be a tuple of governed refs")
    normalized: list[str] = []
    for index, raw in enumerate(values):
        value = str(raw or "").strip()
        if not value or "://" not in value:
            raise GoalPlanningError(f"{field_name}[{index}] must be a governed ref")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _json_copy(value: Any) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return json.loads(encoded)


__all__ = [
    "GOAL_PLANNING_CONTEXT_SCHEMA",
    "GoalPlanningDecision",
    "GoalPlanningError",
    "GoalPlanningRequest",
    "GoalPlanningService",
    "PlanningDomainPorts",
    "build_replan_goal_request",
]
