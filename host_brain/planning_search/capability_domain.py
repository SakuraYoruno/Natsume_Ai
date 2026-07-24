"""Registry-derived Host capability adapters for the shared PlanningSearch.

Unlike the legacy Plan adapter, this domain never receives a pre-expanded
``planned_steps`` graph.  It exposes competing registered capabilities from a
frozen snapshot, selects one virtual execution path, and leaves all real work
to the immutable TaskGraph compiled afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from typing import TYPE_CHECKING, Any

from host_brain.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_DOMAIN,
    CAPABILITY_PLANNING_EXECUTION_STEPS,
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
        selected_ids = _selected_capability_ids(facts, expected=self._snapshot)
        if phase in {"root", "executing"}:
            values = self._execution_candidates(excluded=selected_ids)
        elif phase == "executed":
            values = (self._synthesis_candidate(selected_ids),)
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
        selected_ids = list(_selected_capability_ids(facts, expected=self._snapshot))
        action = str(candidate.payload.get("capability_action") or "").strip()
        if action == "execute":
            capability_id = str(
                candidate.payload.get("capability_id") or ""
            ).strip()
            if capability_id not in self._capabilities or capability_id in selected_ids:
                raise CapabilityPlanningError("selected capability is not registered")
            selected_ids.append(capability_id)
            if len(selected_ids) > CAPABILITY_PLANNING_EXECUTION_STEPS:
                raise CapabilityPlanningError("selected capability path exceeds its frozen step bound")
            facts["selected_capability_ids"] = selected_ids
            facts["phase"] = (
                "executed"
                if len(selected_ids) == CAPABILITY_PLANNING_EXECUTION_STEPS
                else "executing"
            )
        elif action == "synthesize":
            candidate_ids = _candidate_capability_ids(candidate.payload)
            if tuple(selected_ids) != candidate_ids:
                raise CapabilityPlanningError("synthesis candidate does not match selected capabilities")
            if len(selected_ids) != CAPABILITY_PLANNING_EXECUTION_STEPS:
                raise CapabilityPlanningError("synthesis requires the complete frozen capability path")
            facts["phase"] = "synthesized"
        else:
            raise CapabilityPlanningError("unsupported capability planning action")
        return current.replace(facts=facts)

    def _execution_candidates(
        self,
        *,
        excluded: Sequence[str] = (),
    ) -> tuple[SearchCandidate, ...]:
        excluded_ids = frozenset(str(item or "").strip() for item in excluded)
        dependencies = tuple(f"capability.execute.{item}" for item in excluded)
        values = [
            self._execution_candidate(
                capability,
                dependency_candidate_ids=dependencies,
            )
            for capability in self._snapshot.capabilities
            if capability.capability_id not in excluded_ids
        ]
        return tuple(sorted(values, key=lambda item: (-item.prior_score, item.candidate_id)))

    def _execution_candidate(
        self,
        capability: PlanningCapability,
        *,
        dependency_candidate_ids: Sequence[str] = (),
    ) -> SearchCandidate:
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
                    # Governed Skill workers expose their committed result
                    # through the common Task Runtime ``narrative`` field.
                    # Do not require a domain-only alias that no admitted
                    # worker writes; the durable receipt remains the exact
                    # artifact identity.
                    "required": ["receipt_ref", "narrative"],
                },
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                "risk_level": capability.risk_level,
                "capability_requirements": [capability.capability_id],
                "capability_action": "execute",
                "capability_id": capability.capability_id,
                "estimated_relevance": _rounded(relevance),
                "estimated_risk": _rounded(risk),
                "estimated_cost": _rounded(capability.estimated_cost),
                "depends_on_candidate_ids": list(dependency_candidate_ids),
                "requires_lookahead": True,
            },
            labels=("capability", "execute", capability.family),
            simulation_only=True,
        )

    def _synthesis_candidate(self, capability_ids: Sequence[str]) -> SearchCandidate:
        selected_ids = tuple(str(item or "").strip() for item in capability_ids)
        if len(selected_ids) != CAPABILITY_PLANNING_EXECUTION_STEPS:
            raise CapabilityPlanningError("synthesis requires exactly the frozen capability step count")
        if len(set(selected_ids)) != len(selected_ids) or any(
            item not in self._capabilities for item in selected_ids
        ):
            raise CapabilityPlanningError("synthesis has invalid selected capabilities")
        digest = hashlib.sha256("\x1f".join(selected_ids).encode("utf-8")).hexdigest()[:32]
        execution_ids = tuple(f"capability.execute.{item}" for item in selected_ids)
        return SearchCandidate(
            candidate_id=f"capability.synthesize.{digest}",
            prior_score=0.95
            - 0.04
            * (
                sum(
                    _risk_unit(self._capabilities[item].risk_level)
                    for item in selected_ids
                )
                / len(selected_ids)
            ),
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
                    # Frozen Chat uses the same canonical committed narrative
                    # field as a direct governed Chat node.
                    "required": ["receipt_ref", "narrative"],
                },
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                "risk_level": 1,
                "capability_requirements": ["surface_reply"],
                "capability_action": "synthesize",
                "capability_ids": list(selected_ids),
                "estimated_relevance": 1.0,
                "estimated_risk": 0.0,
                "estimated_cost": 0.04,
                "depends_on_candidate_ids": list(execution_ids),
            },
            labels=("capability", "synthesize", "multi_step"),
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
        selected_ids = _selected_capability_ids(facts, expected=self._snapshot)
        if len(selected_ids) != CAPABILITY_PLANNING_EXECUTION_STEPS:
            return -1.0
        capabilities = [self._capabilities.get(item) for item in selected_ids]
        if any(item is None for item in capabilities):
            return -1.0
        resolved = [item for item in capabilities if item is not None]
        relevance = sum(
            _relevance(item, requested_tags=self._snapshot.requested_tags)
            for item in resolved
        ) / len(resolved)
        risk = sum(_risk_unit(item.risk_level) for item in resolved) / len(resolved)
        cost = sum(item.estimated_cost for item in resolved) / len(resolved)
        diversity = len({item.family for item in resolved}) / len(resolved)
        return (
            1.0
            + 0.45 * relevance
            + 0.08 * diversity
            + 0.03 * sum(1 for item in resolved if item.reversible) / len(resolved)
            - 0.22 * risk
            - 0.16 * cost
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
        "execution_steps": CAPABILITY_PLANNING_EXECUTION_STEPS,
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
        dependencies = _candidate_dependency_ids(payload)
        return {
            "route": "skill",
            "target_id": capability_id,
            "objective": snapshot.objective,
            "args": {
                "planning_mode": CAPABILITY_PLANNING_MODE,
                "catalog_ref": snapshot.catalog_ref,
                "capability_id": capability_id,
            },
            "depends_on_candidate_ids": list(dependencies),
        }
    if action == "synthesize":
        capability_ids = _candidate_capability_ids(payload)
        dependencies = _candidate_dependency_ids(payload)
        expected_dependencies = tuple(
            f"capability.execute.{item}" for item in capability_ids
        )
        if dependencies != expected_dependencies:
            raise CapabilityPlanningError("synthesis dependencies differ from selected capabilities")
        return {
            "route": "chat",
            "target_id": "surface_reply",
            "objective": snapshot.objective,
            "args": {
                "planning_mode": CAPABILITY_PLANNING_MODE,
                "catalog_ref": snapshot.catalog_ref,
                "selected_capability_ids": list(capability_ids),
            },
            "depends_on_candidate_ids": list(dependencies),
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
        or constraints.get("execution_steps") != CAPABILITY_PLANNING_EXECUTION_STEPS
        or tuple(value.source_refs)
        != (expected.task_ref, expected.catalog_ref, *expected.source_refs)
    ):
        raise CapabilityPlanningError(
            "capability virtual state is not bound to its frozen registry snapshot"
        )
    phase = str(facts.get("phase") or "").strip().lower()
    selected_ids = _selected_capability_ids(facts, expected=expected)
    if phase not in {"root", "executing", "executed", "synthesized"}:
        raise CapabilityPlanningError("capability virtual state phase is invalid")
    if phase == "root" and selected_ids:
        raise CapabilityPlanningError("root capability state must not select a capability")
    if phase == "executing" and not 0 < len(selected_ids) < CAPABILITY_PLANNING_EXECUTION_STEPS:
        raise CapabilityPlanningError("executing capability state has an invalid selected step count")
    if phase in {"executed", "synthesized"} and len(selected_ids) != CAPABILITY_PLANNING_EXECUTION_STEPS:
        raise CapabilityPlanningError("terminal capability state has an incomplete selected path")
    return value


def _facts(state: VirtualStateSnapshot) -> dict[str, Any]:
    facts = _thaw(state.facts)
    if not isinstance(facts, dict):
        raise CapabilityPlanningError("capability state facts are invalid")
    return facts


def _selected_capability_ids(
    facts: Mapping[str, Any],
    *,
    expected: CapabilityPlanningSnapshot,
) -> tuple[str, ...]:
    raw = facts.get("selected_capability_ids", ())
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise CapabilityPlanningError("selected_capability_ids must be an explicit sequence")
    values = tuple(str(item or "").strip() for item in raw)
    if any(not item for item in values) or len(set(values)) != len(values):
        raise CapabilityPlanningError("selected_capability_ids are invalid")
    if any(item not in {capability.capability_id for capability in expected.capabilities} for item in values):
        raise CapabilityPlanningError("capability virtual state selected unknown capability")
    if len(values) > CAPABILITY_PLANNING_EXECUTION_STEPS:
        raise CapabilityPlanningError("selected capability path exceeds its frozen step bound")
    return values


def _candidate_capability_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("capability_ids", ())
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise CapabilityPlanningError("synthesis candidate capability_ids must be an explicit sequence")
    values = tuple(str(item or "").strip() for item in raw)
    if (
        len(values) != CAPABILITY_PLANNING_EXECUTION_STEPS
        or any(not item for item in values)
        or len(set(values)) != len(values)
    ):
        raise CapabilityPlanningError("synthesis candidate capability_ids are invalid")
    return values


def _candidate_dependency_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("depends_on_candidate_ids", ())
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise CapabilityPlanningError("candidate dependencies must be an explicit sequence")
    values = tuple(str(item or "").strip() for item in raw)
    if any(not item for item in values) or len(set(values)) != len(values):
        raise CapabilityPlanningError("candidate dependencies are invalid")
    return values


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
