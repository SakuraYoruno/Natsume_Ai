"""Registry-derived Host capability adapters for the shared PlanningSearch.

Unlike the legacy Plan adapter, this domain never receives a pre-expanded
``planned_steps`` graph.  It exposes competing registered capabilities from a
frozen snapshot, selects one virtual execution path, and leaves all real work
to the immutable TaskGraph compiled afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any

from host_brain.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_DOMAIN,
    CAPABILITY_PLANNING_MODE,
    CAPABILITY_PLANNING_NODE_OUTPUT_SCHEMA,
    CapabilityPlanningError,
    CapabilityPlanningSnapshot,
    PlanningCapability,
)
from host_brain.planning_search.contracts import (
    CandidateScore,
    SearchCandidate,
    VirtualStateSnapshot,
)

if TYPE_CHECKING:
    from host_brain.orchestration.goal_planning import GoalPlanningDecision
    from host_brain.orchestration.planning_graph_compiler import (
        PlanningCompilationContext,
    )


class CapabilitySuccessorCatalog:
    """L1 graph expansion from a frozen registry capability snapshot."""

    def __init__(self, *, snapshot: CapabilityPlanningSnapshot) -> None:
        if not isinstance(snapshot, CapabilityPlanningSnapshot):
            raise TypeError("snapshot must be CapabilityPlanningSnapshot")
        self._snapshot = snapshot
        self._capabilities = {
            item.capability_id: item for item in snapshot.capabilities
        }

    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]:
        del path, depth, context
        resolved = _capability_state(state, expected=self._snapshot)
        if int(limit) <= 0:
            return ()
        facts = _facts(resolved)
        phase = str(facts.get("phase") or "root").strip().lower()
        if phase == "root":
            values = self._execution_candidates()
        elif phase == "executed":
            selected = str(facts.get("selected_capability_id") or "").strip()
            capability = self._capabilities.get(selected)
            values = (self._synthesis_candidate(capability),) if capability else ()
        else:
            values = ()
        return tuple(values[: max(0, int(limit))])

    def transition(
        self,
        *,
        state: Any,
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> VirtualStateSnapshot:
        del context
        current = _capability_state(state, expected=self._snapshot)
        if not isinstance(candidate, SearchCandidate):
            raise CapabilityPlanningError("candidate must be a SearchCandidate")
        expected = {
            item.candidate_id: item
            for item in self.expand(
                state=current,
                path=path[:-1],
                depth=max(0, len(path) - 1),
                limit=128,
                context={},
            )
        }.get(candidate.candidate_id)
        if expected is None or expected.to_dict() != candidate.to_dict():
            raise CapabilityPlanningError(
                "candidate is not a legal successor of the frozen capability state"
            )
        facts = _facts(current)
        action = str(candidate.payload.get("capability_action") or "").strip()
        if action == "execute":
            capability_id = str(
                candidate.payload.get("capability_id") or ""
            ).strip()
            if capability_id not in self._capabilities:
                raise CapabilityPlanningError("selected capability is not registered")
            facts["phase"] = "executed"
            facts["selected_capability_id"] = capability_id
        elif action == "synthesize":
            selected = str(facts.get("selected_capability_id") or "").strip()
            if selected != str(candidate.payload.get("capability_id") or "").strip():
                raise CapabilityPlanningError("synthesis candidate does not match selected capability")
            facts["phase"] = "synthesized"
        else:
            raise CapabilityPlanningError("unsupported capability planning action")
        return current.replace(facts=facts)

    def _execution_candidates(self) -> tuple[SearchCandidate, ...]:
        values = [self._execution_candidate(capability) for capability in self._snapshot.capabilities]
        return tuple(sorted(values, key=lambda item: (-item.prior_score, item.candidate_id)))

    def _execution_candidate(self, capability: PlanningCapability) -> SearchCandidate:
        relevance = _relevance(capability, requested_tags=self._snapshot.requested_tags)
        risk = _risk_unit(capability.risk_level)
        prior = relevance + (0.06 if capability.reversible else 0.0)
        prior += 0.03 if capability.parallelizable else 0.0
        prior -= 0.22 * risk + 0.18 * capability.estimated_cost
        return SearchCandidate(
            candidate_id=f"capability.execute.{capability.capability_id}",
            prior_score=prior,
            payload={
                "worker_route": "skill",
                "target_id": capability.capability_id,
                "objective": self._snapshot.objective,
                "acceptance_criteria": [
                    "return a durable governed capability receipt",
                    "remain within the frozen capability catalog and task objective",
                ],
                "input_refs": [self._snapshot.task_ref, self._snapshot.catalog_ref],
                "output_contract": {
                    "schema_version": CAPABILITY_PLANNING_NODE_OUTPUT_SCHEMA,
                    "kind": "capability_execution",
                    "required": ["receipt_ref", "result_summary"],
                },
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                "risk_level": capability.risk_level,
                "capability_requirements": [capability.capability_id],
                "capability_action": "execute",
                "capability_id": capability.capability_id,
                "estimated_relevance": _rounded(relevance),
                "estimated_risk": _rounded(risk),
                "estimated_cost": _rounded(capability.estimated_cost),
                "requires_lookahead": True,
            },
            labels=("capability", "execute", capability.family),
            simulation_only=True,
        )

    def _synthesis_candidate(self, capability: PlanningCapability) -> SearchCandidate:
        execution_id = f"capability.execute.{capability.capability_id}"
        return SearchCandidate(
            candidate_id=f"capability.synthesize.{capability.capability_id}",
            prior_score=0.95 - 0.04 * _risk_unit(capability.risk_level),
            payload={
                "worker_route": "chat",
                "target_id": "surface_reply",
                "objective": self._snapshot.objective,
                "acceptance_criteria": [
                    "synthesize only the accepted dependency result",
                    "return one durable final response receipt",
                ],
                "input_refs": [self._snapshot.task_ref, self._snapshot.catalog_ref],
                "output_contract": {
                    "schema_version": CAPABILITY_PLANNING_NODE_OUTPUT_SCHEMA,
                    "kind": "capability_synthesis",
                    "required": ["receipt_ref", "response_summary"],
                },
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                "risk_level": 1,
                "capability_requirements": ["surface_reply"],
                "capability_action": "synthesize",
                "capability_id": capability.capability_id,
                "estimated_relevance": 1.0,
                "estimated_risk": 0.0,
                "estimated_cost": 0.04,
                "depends_on_candidate_ids": [execution_id],
            },
            labels=("capability", "synthesize", capability.family),
            simulation_only=True,
        )


class CapabilityCognitiveRanker:
    """L2 deterministic ranking over frozen relevance, risk, and cost."""

    def rank(
        self,
        *,
        state: Any,
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Sequence[CandidateScore]:
        del state, context
        values: list[CandidateScore] = []
        for candidate in candidates:
            payload = candidate.payload
            relevance = _payload_unit(payload, "estimated_relevance")
            risk = _payload_unit(payload, "estimated_risk")
            cost = _payload_unit(payload, "estimated_cost")
            score = float(candidate.prior_score) + 0.40 * relevance - 0.28 * risk - 0.20 * cost
            values.append(
                CandidateScore(
                    candidate_id=candidate.candidate_id,
                    score=score,
                    reason_code="registry_capability_risk_cost_rank",
                )
            )
        return tuple(values)


class CapabilityRewardModel:
    """L3 virtual reward for a complete selected capability path only."""

    def __init__(self, *, snapshot: CapabilityPlanningSnapshot) -> None:
        self._snapshot = snapshot
        self._capabilities = {
            item.capability_id: item for item in snapshot.capabilities
        }

    def evaluate(
        self,
        *,
        initial_state: Any,
        state: Any,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, path, context
        resolved = _capability_state(state, expected=self._snapshot)
        facts = _facts(resolved)
        if str(facts.get("phase") or "").strip().lower() != "synthesized":
            return -0.55
        capability = self._capabilities.get(
            str(facts.get("selected_capability_id") or "").strip()
        )
        if capability is None:
            return -1.0
        return (
            1.0
            + 0.45 * _relevance(capability, requested_tags=self._snapshot.requested_tags)
            + (0.06 if capability.reversible else 0.0)
            - 0.22 * _risk_unit(capability.risk_level)
            - 0.16 * capability.estimated_cost
        )


class CapabilityMCTSCondition:
    """L3 is available only for genuine competing registry alternatives."""

    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool:
        del context
        execution = [
            candidate
            for candidate in candidates
            if candidate.payload.get("capability_action") == "execute"
        ]
        if len(execution) < 2:
            return False
        ordered = sorted(
            (
                float(scores.get(candidate.candidate_id, candidate.prior_score))
                for candidate in execution
            ),
            reverse=True,
        )
        highest_risk = max(
            _payload_unit(candidate.payload, "estimated_risk") for candidate in execution
        )
        return highest_risk > 0.0 or abs(ordered[0] - ordered[1]) <= 0.25


@dataclass(frozen=True, slots=True)
class CapabilityPlanningDomain:
    """One immutable bundle of registry-derived domain adapters."""

    snapshot: CapabilityPlanningSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, CapabilityPlanningSnapshot):
            raise TypeError("snapshot must be CapabilityPlanningSnapshot")

    def planning_ports(self):  # noqa: ANN201
        """Return the shared L1/L2/L3 ports without an execution dependency."""

        from host_brain.orchestration.goal_planning import (  # noqa: PLC0415
            PlanningDomainPorts,
        )

        catalog = CapabilitySuccessorCatalog(snapshot=self.snapshot)
        return PlanningDomainPorts(
            successors=catalog,
            ranker=CapabilityCognitiveRanker(),
            transition=catalog,
            reward=CapabilityRewardModel(snapshot=self.snapshot),
            mcts_condition=CapabilityMCTSCondition(),
        )


def bind_selected_capability_inputs(
    base_context: "PlanningCompilationContext",
    *,
    snapshot: CapabilityPlanningSnapshot,
    decision: "GoalPlanningDecision",
    state: VirtualStateSnapshot | None = None,
) -> "PlanningCompilationContext":
    """Rebuild selected node inputs from the frozen catalog before compile."""

    from host_brain.orchestration.goal_planning import (  # noqa: PLC0415
        GoalPlanningDecision,
    )
    from host_brain.orchestration.planning_graph_compiler import (  # noqa: PLC0415
        PlanningCompilationContext,
    )

    if not isinstance(base_context, PlanningCompilationContext):
        raise TypeError("base_context must be PlanningCompilationContext")
    if not isinstance(snapshot, CapabilityPlanningSnapshot):
        raise TypeError("snapshot must be CapabilityPlanningSnapshot")
    if not isinstance(decision, GoalPlanningDecision):
        raise TypeError("decision must be GoalPlanningDecision")
    resolved_state = state or snapshot.to_virtual_state()
    _capability_state(resolved_state, expected=snapshot)
    if (
        decision.request.goal.domain != CAPABILITY_PLANNING_DOMAIN
        or decision.request.state != resolved_state
        or decision.result.state_snapshot != resolved_state
        or snapshot.task_ref not in decision.request.goal.frozen_context_refs
        or snapshot.catalog_ref not in decision.request.goal.frozen_context_refs
    ):
        raise CapabilityPlanningError(
            "decision goal/state does not match the capability compilation state"
        )
    if (
        base_context.domain != CAPABILITY_PLANNING_DOMAIN
        or base_context.goal != decision.request.goal
        or base_context.plan_objective != decision.request.goal.objective
        or (
            base_context.virtual_state is not None
            and base_context.virtual_state != resolved_state
        )
    ):
        raise CapabilityPlanningError("base compilation context is stale or mismatched")
    input_pack = _thaw(base_context.input_pack)
    if "intent_draft" in input_pack or "approved_candidate_inputs" in input_pack:
        raise CapabilityPlanningError(
            "capability planning context cannot carry a legacy plan or pre-approved nodes"
        )

    catalog = CapabilitySuccessorCatalog(snapshot=snapshot)
    current = resolved_state
    prefix: tuple[SearchCandidate, ...] = ()
    approved: dict[str, dict[str, Any]] = {}
    selected_actions: list[str] = []
    for index, candidate in enumerate(decision.result.selected_path):
        allowed = {
            item.candidate_id: item
            for item in catalog.expand(
                state=current,
                path=prefix,
                depth=index,
                limit=128,
                context={},
            )
        }
        expected = allowed.get(candidate.candidate_id)
        if expected is None or expected.to_dict() != candidate.to_dict():
            raise CapabilityPlanningError(
                "selected path contains a non-catalog capability candidate"
            )
        approved[expected.candidate_id] = _candidate_input(
            snapshot=snapshot,
            candidate=expected,
        )
        route = str(expected.payload.get("worker_route") or "").strip()
        target = str(expected.payload.get("target_id") or "").strip()
        selected_actions.append(
            "render_reply" if route == "chat" else f"{route}:{target}"
        )
        prefix = (*prefix, expected)
        current = catalog.transition(
            state=current,
            candidate=expected,
            path=prefix,
            context={},
        )
    if not approved or _facts(current).get("phase") != "synthesized":
        raise CapabilityPlanningError("selected capability path must end in synthesis")

    input_pack["approved_candidate_inputs"] = approved
    input_pack["capability_planning"] = {
        "schema_version": CAPABILITY_PLANNING_MODE,
        "catalog_ref": snapshot.catalog_ref,
        "catalog_digest": snapshot.catalog_digest,
        "selected_candidate_ids": list(approved),
    }
    metadata = _thaw(base_context.metadata)
    metadata["capability_planning"] = {
        "mode": CAPABILITY_PLANNING_MODE,
        "catalog_ref": snapshot.catalog_ref,
        "requested_tags": list(snapshot.requested_tags),
        "max_risk_level": snapshot.max_risk_level,
    }
    return PlanningCompilationContext(
        domain=base_context.domain,
        run_mode=base_context.run_mode,
        plan_objective=base_context.plan_objective,
        allowed_actions=tuple(dict.fromkeys(selected_actions)),
        forbidden_actions=base_context.forbidden_actions,
        verification_plan=_thaw(base_context.verification_plan),
        approval_policy=_thaw(base_context.approval_policy),
        review_policy=_thaw(base_context.review_policy),
        budget_policy=_thaw(base_context.budget_policy),
        business_idempotency_scope=base_context.business_idempotency_scope,
        approval_ref=base_context.approval_ref,
        review_ref=base_context.review_ref,
        workspace=_thaw(base_context.workspace),
        input_pack=input_pack,
        input_refs=base_context.input_refs,
        metadata=metadata,
        goal=decision.request.goal,
        virtual_state=resolved_state,
    )


def _candidate_input(
    *,
    snapshot: CapabilityPlanningSnapshot,
    candidate: SearchCandidate,
) -> dict[str, Any]:
    payload = candidate.payload
    capability_id = str(payload.get("capability_id") or "").strip()
    action = str(payload.get("capability_action") or "").strip()
    if action == "execute":
        return {
            "route": "skill",
            "target_id": capability_id,
            "objective": snapshot.objective,
            "args": {
                "planning_mode": CAPABILITY_PLANNING_MODE,
                "catalog_ref": snapshot.catalog_ref,
                "capability_id": capability_id,
            },
            "depends_on_candidate_ids": [],
        }
    if action == "synthesize":
        return {
            "route": "chat",
            "target_id": "surface_reply",
            "objective": snapshot.objective,
            "args": {
                "planning_mode": CAPABILITY_PLANNING_MODE,
                "catalog_ref": snapshot.catalog_ref,
                "selected_capability_id": capability_id,
            },
            "depends_on_candidate_ids": [f"capability.execute.{capability_id}"],
        }
    raise CapabilityPlanningError("candidate action is unsupported")


def _capability_state(
    value: Any,
    *,
    expected: CapabilityPlanningSnapshot,
) -> VirtualStateSnapshot:
    if (
        not isinstance(value, VirtualStateSnapshot)
        or value.domain != CAPABILITY_PLANNING_DOMAIN
    ):
        raise CapabilityPlanningError(
            "capability planning requires a skill VirtualStateSnapshot"
        )
    facts = _facts(value)
    constraints = _thaw(value.constraints)
    expected_capabilities = [item.to_state_dict() for item in expected.capabilities]
    if (
        str(facts.get("catalog_digest") or "") != expected.catalog_digest
        or facts.get("capabilities") != expected_capabilities
        or tuple(value.capabilities)
        != tuple(item.capability_id for item in expected.capabilities)
        or constraints.get("mode") != CAPABILITY_PLANNING_MODE
        or constraints.get("requested_tags") != list(expected.requested_tags)
        or constraints.get("max_risk_level") != expected.max_risk_level
        or constraints.get("candidate_count") != len(expected.capabilities)
        or tuple(value.source_refs)
        != (expected.task_ref, expected.catalog_ref, *expected.source_refs)
    ):
        raise CapabilityPlanningError(
            "capability virtual state is not bound to its frozen registry snapshot"
        )
    phase = str(facts.get("phase") or "").strip().lower()
    selected = str(facts.get("selected_capability_id") or "").strip()
    if phase not in {"root", "executed", "synthesized"}:
        raise CapabilityPlanningError("capability virtual state phase is invalid")
    if phase == "root" and selected:
        raise CapabilityPlanningError("root capability state must not select a capability")
    if phase != "root" and selected not in {
        item.capability_id for item in expected.capabilities
    }:
        raise CapabilityPlanningError("capability virtual state selected unknown capability")
    return value


def _facts(state: VirtualStateSnapshot) -> dict[str, Any]:
    facts = _thaw(state.facts)
    if not isinstance(facts, dict):
        raise CapabilityPlanningError("capability state facts are invalid")
    return facts


def _relevance(
    capability: PlanningCapability,
    *,
    requested_tags: Sequence[str],
) -> float:
    overlap = len(set(capability.capability_tags).intersection(requested_tags))
    return min(1.0, overlap / max(1, len(tuple(requested_tags))))


def _risk_unit(risk_level: int) -> float:
    return max(0.0, min(1.0, (int(risk_level) - 1) / 4))


def _payload_unit(payload: Mapping[str, Any], field_name: str) -> float:
    try:
        value = float(payload.get(field_name, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _rounded(value: float) -> float:
    return round(float(value), 10)


__all__ = [
    "CapabilityCognitiveRanker",
    "CapabilityMCTSCondition",
    "CapabilityPlanningDomain",
    "CapabilityRewardModel",
    "CapabilitySuccessorCatalog",
    "bind_selected_capability_inputs",
]
