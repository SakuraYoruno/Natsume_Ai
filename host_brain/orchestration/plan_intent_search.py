"""Pure migration adapter from one approved Plan intent to PlanningSearch.

``planned_steps``/``steps`` are accepted only as an upstream description of
an already-approved graph.  This module never dispatches a Skill, Agent, or
chat worker.  It converts that description into compiler-ready, JSON-only
``SearchCandidate`` values and lets PlanningSearch choose a complete
topological order through virtual transitions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BUDGET_POLICY_SCHEMA_VERSION,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import normalize_task_retry_policy
from host_brain.contracts.agent_runtime.work_order import (
    ALLOWED_AGENT_DOMAINS,
    ALLOWED_AGENT_RUN_MODES,
)
from host_brain.orchestration.governor_grant import (
    canonical_payload_digest,
    durable_governor_binding,
    governor_grant_ref,
)
from host_brain.planning_search import (
    CandidateScore,
    MCTSMode,
    PlanningSearchService,
    SearchBudget,
    SearchCandidate,
    SearchRequest,
    SearchResult,
    SearchStatus,
)


PLAN_INTENT_SEARCH_INVALID = "PLAN_INTENT_SEARCH_INVALID"
PLAN_INTENT_AUTHORITY_INVALID = "PLAN_INTENT_AUTHORITY_INVALID"
PLAN_INTENT_GRAPH_INVALID = "PLAN_INTENT_GRAPH_INVALID"
PLAN_INTENT_SENSITIVE_FIELD = "PLAN_INTENT_SENSITIVE_FIELD"
PLAN_INTENT_SEARCH_INCOMPLETE = "PLAN_INTENT_SEARCH_INCOMPLETE"

_SUPPORTED_ROUTES = frozenset({"skill", "delegate", "chat"})
_MAX_NODES = 64
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,239}$")
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "oauth",
        "password",
        "passwd",
        "private_key",
        "raw_auth",
        "rawauth",
        "secret",
        "token",
        "tokens",
    }
)
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
        "tokens",
    }
)
_SAFE_SENSITIVE_WORD_KEYS = frozenset({"token_count"})
_BUDGET_LIMIT_FIELDS = (
    "token_count",
    "tool_call_count",
    "time_ms",
    "concurrency",
    "retry_count",
    "model_cost_micros",
)


class PlanIntentSearchError(ValueError):
    """A Plan intent cannot be projected into a safe complete search."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or PLAN_INTENT_SEARCH_INVALID)
        self.detail = str(detail or "approved Plan intent search projection failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class _ApprovedNode:
    candidate_id: str
    ordinal: int
    route: str
    target_id: str
    objective: str
    dependencies: tuple[str, ...]
    args: dict[str, Any]
    risk_level: int
    acceptance_criteria: tuple[str, ...]
    capability_requirements: tuple[str, ...]
    input_refs: tuple[str, ...]
    output_contract: dict[str, Any]
    retry_policy: dict[str, Any]


class PlanDependencyExpander:
    """L1: expose only approved nodes whose dependencies are complete."""

    def __init__(
        self,
        *,
        candidates: Sequence[SearchCandidate],
        dependencies: Mapping[str, Sequence[str]],
    ) -> None:
        self._candidates = {item.candidate_id: item for item in candidates}
        self._dependencies = {
            str(candidate_id): tuple(str(value) for value in values)
            for candidate_id, values in dependencies.items()
        }

    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> tuple[SearchCandidate, ...]:
        del depth, context
        completed = _virtual_completed(state)
        path_ids = tuple(item.candidate_id for item in path)
        if path_ids and path_ids != completed:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                "virtual state does not exactly match the selected search path",
            )
        ready = [
            candidate
            for candidate_id, candidate in self._candidates.items()
            if candidate_id not in completed
            and set(self._dependencies[candidate_id]).issubset(completed)
        ]
        ready.sort(key=lambda item: (-item.prior_score, item.candidate_id))
        return tuple(ready[: max(0, int(limit))])


class PlanDeterministicCognitiveRanker:
    """L2: deterministic dependency/ordinal scoring with no model call."""

    def __init__(
        self,
        *,
        ordinals: Mapping[str, int],
        dependencies: Mapping[str, Sequence[str]],
    ) -> None:
        self._ordinals = {str(key): int(value) for key, value in ordinals.items()}
        self._descendants = _descendant_counts(dependencies)
        self._size = max(1, len(self._ordinals))

    def rank(
        self,
        *,
        state: Any,
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> tuple[CandidateScore, ...]:
        del state, context
        scores = []
        for candidate in candidates:
            identifier = candidate.candidate_id
            downstream = self._descendants.get(identifier, 0)
            ordinal = self._ordinals[identifier]
            score = (
                float(candidate.prior_score)
                + downstream / (100.0 * self._size)
                - ordinal / (10_000.0 * self._size)
            )
            scores.append(
                CandidateScore(
                    candidate_id=identifier,
                    score=score,
                    reason_code="approved_plan_dependency_rank",
                )
            )
        scores.sort(key=lambda item: item.candidate_id)
        return tuple(scores)


class PlanVirtualTransition:
    """Pure transition that marks one approved node complete in virtual state."""

    def __init__(self, *, dependencies: Mapping[str, Sequence[str]]) -> None:
        self._dependencies = {
            str(candidate_id): tuple(str(value) for value in values)
            for candidate_id, values in dependencies.items()
        }

    def transition(
        self,
        *,
        state: Any,
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del context
        identifier = candidate.candidate_id
        if identifier not in self._dependencies:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"transition references an unapproved candidate: {identifier}",
            )
        completed = _virtual_completed(state)
        if identifier in completed:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"candidate was selected twice: {identifier}",
            )
        missing = tuple(
            dependency
            for dependency in self._dependencies[identifier]
            if dependency not in completed
        )
        if missing:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"candidate {identifier} has incomplete dependencies: {','.join(missing)}",
            )
        next_completed = completed + (identifier,)
        if tuple(item.candidate_id for item in path) != next_completed:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                "transition path differs from the virtual completion order",
            )
        return {
            "approved_candidate_ids": sorted(self._dependencies),
            "completed_candidate_ids": list(next_completed),
        }


class PlanCoverageReward:
    """Reward only virtual, complete, topologically valid approved paths."""

    def __init__(self, *, dependencies: Mapping[str, Sequence[str]]) -> None:
        self._dependencies = {
            str(candidate_id): tuple(str(value) for value in values)
            for candidate_id, values in dependencies.items()
        }
        self._approved = frozenset(self._dependencies)

    def evaluate(
        self,
        *,
        initial_state: Any,
        state: Any,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, context
        completed = _virtual_completed(state)
        path_ids = tuple(item.candidate_id for item in path)
        if completed != path_ids or not _is_topological(path_ids, self._dependencies):
            return -1.0
        coverage = len(set(completed) & self._approved) / max(1, len(self._approved))
        if len(completed) == len(self._approved) and set(completed) == self._approved:
            return 1.0
        return -1.0 + (0.5 * coverage)


class PlanConditionalMCTS:
    """Use look-ahead exactly when a complete path needs multiple nodes."""

    def __init__(self, *, approved_candidate_ids: Sequence[str]) -> None:
        self._approved = tuple(str(value) for value in approved_candidate_ids)

    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool:
        del scores, context
        return len(self._approved) > 1 and bool(candidates)


@dataclass(frozen=True, slots=True)
class PlanIntentSearchAdapter:
    """Compiler-facing, validating PlanningSearch facade for one Plan intent."""

    request: SearchRequest
    candidates: tuple[SearchCandidate, ...]
    approved_candidate_ids: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]
    _expander: PlanDependencyExpander
    _ranker: PlanDeterministicCognitiveRanker
    _transition: PlanVirtualTransition
    _reward: PlanCoverageReward
    _condition: PlanConditionalMCTS

    def search(self, request: SearchRequest) -> SearchResult:
        """Run a pure search and reject every incomplete/invalid selection."""

        if not _same_request(request, self.request):
            raise PlanIntentSearchError(
                PLAN_INTENT_SEARCH_INVALID,
                "search request differs from the approved Plan projection",
            )
        service = PlanningSearchService(
            expander=self._expander,
            cognitive_ranker=self._ranker,
            transition=self._transition,
            reward_model=self._reward,
            mcts_condition=self._condition,
        )
        result = service.search(request)
        _require_complete_result(
            result,
            approved_candidate_ids=self.approved_candidate_ids,
            dependencies=self.dependencies,
        )
        return result

    def run(self) -> SearchResult:
        return self.search(self.request)


def build_plan_intent_search(
    intent: Mapping[str, Any],
    *,
    governor_result: Mapping[str, Any],
    request_id: str | None = None,
    approval_ref: str | None = None,
    review_ref: str | None = None,
    budget_policy: Mapping[str, Any] | None = None,
    business_idempotency_scope: str | None = None,
    input_refs: Sequence[str] = (),
    search_budget: SearchBudget | None = None,
    frozen_context: Mapping[str, Any] | None = None,
) -> PlanIntentSearchAdapter:
    """Build a side-effect-free search facade from one approved Plan intent."""

    raw_intent = _mapping_copy(intent, field_name="intent")
    governor = _mapping_copy(governor_result, field_name="governor_result")
    frozen = _mapping_copy(frozen_context or {}, field_name="frozen_context")
    _assert_secret_free(raw_intent, path="intent")
    _assert_secret_free(frozen, path="frozen_context")
    request_ref = _required_identifier(
        request_id
        or governor.get("request_id")
        or raw_intent.get("request_id")
        or raw_intent.get("id"),
        field_name="request_id",
    )
    intent_id = _required_identifier(
        raw_intent.get("id") or raw_intent.get("intent_id"),
        field_name="intent.id",
    )
    if _text(raw_intent.get("route")).lower() != "plan":
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "only route=plan intents can enter Plan intent search",
        )
    _require_governor_authority(
        intent=raw_intent,
        governor=governor,
        request_id=request_ref,
        intent_id=intent_id,
    )
    canonical_approval_ref = governor_grant_ref(request_ref, governor)
    explicit_approval_ref = _text(
        approval_ref or raw_intent.get("approval_ref")
    )
    if explicit_approval_ref and explicit_approval_ref != canonical_approval_ref:
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "approval_ref differs from the approved Governor decision",
        )

    raw_steps = raw_intent.get("planned_steps")
    if not isinstance(raw_steps, list):
        raw_steps = raw_intent.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "approved Plan intent has no planned_steps/steps",
        )
    if len(raw_steps) > _MAX_NODES:
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            f"approved Plan intent exceeds {_MAX_NODES} nodes",
        )

    step_mappings = [
        _mapping_copy(value, field_name=f"steps[{index}]")
        for index, value in enumerate(raw_steps)
    ]
    nodes = _normalize_nodes(step_mappings)
    topo_ids = _topological_order(nodes)
    node_by_id = {node.candidate_id: node for node in nodes}
    ordered_nodes = tuple(node_by_id[candidate_id] for candidate_id in topo_ids)

    scope = _resolve_business_scope(
        explicit=business_idempotency_scope,
        intent=raw_intent,
        request_id=request_ref,
        intent_id=intent_id,
    )
    normalized_budget = _resolve_budget_policy(
        explicit=budget_policy,
        intent=raw_intent,
        scope=scope,
        node_count=len(ordered_nodes),
    )
    resolved_review_ref = _resolve_review_ref(
        explicit=review_ref,
        intent=raw_intent,
        governor=governor,
        approval_ref=canonical_approval_ref,
    )
    global_input_refs = _dedupe_text(
        input_refs,
        raw_intent.get("input_refs"),
        (canonical_approval_ref,),
    )
    graph_fields = _graph_policy_fields(
        intent=raw_intent,
        nodes=ordered_nodes,
        request_id=request_ref,
        intent_id=intent_id,
        approval_ref=canonical_approval_ref,
        review_ref=resolved_review_ref,
        budget_policy=normalized_budget,
        business_scope=scope,
        input_refs=global_input_refs,
        frozen_context=frozen,
    )
    candidate_count = len(ordered_nodes)
    candidates = tuple(
        _candidate_from_node(
            node,
            graph_fields=graph_fields,
            total_nodes=candidate_count,
        )
        for node in ordered_nodes
    )
    dependencies = {
        node.candidate_id: node.dependencies for node in ordered_nodes
    }
    ordinals = {node.candidate_id: node.ordinal for node in ordered_nodes}
    budget = search_budget or _default_search_budget(candidate_count)
    request = SearchRequest(
        request_id=request_ref,
        initial_state={
            "approved_candidate_ids": list(topo_ids),
            "completed_candidate_ids": [],
        },
        budget=budget,
        context={
            "schema_version": "Natsume.plan_intent_search.context.v1",
            "intent_id": intent_id,
            "approved_candidate_ids": list(topo_ids),
            "dependencies": {
                key: list(value) for key, value in sorted(dependencies.items())
            },
        },
        mcts_mode=MCTSMode.AUTO,
    )
    return PlanIntentSearchAdapter(
        request=request,
        candidates=candidates,
        approved_candidate_ids=topo_ids,
        dependencies=dependencies,
        _expander=PlanDependencyExpander(
            candidates=candidates,
            dependencies=dependencies,
        ),
        _ranker=PlanDeterministicCognitiveRanker(
            ordinals=ordinals,
            dependencies=dependencies,
        ),
        _transition=PlanVirtualTransition(dependencies=dependencies),
        _reward=PlanCoverageReward(dependencies=dependencies),
        _condition=PlanConditionalMCTS(approved_candidate_ids=topo_ids),
    )


def search_approved_plan_intent(
    intent: Mapping[str, Any],
    **kwargs: Any,
) -> SearchResult:
    """Convenience wrapper that always applies complete-path validation."""

    return build_plan_intent_search(intent, **kwargs).run()


def _normalize_nodes(steps: Sequence[Mapping[str, Any]]) -> tuple[_ApprovedNode, ...]:
    identities: list[tuple[str, int, Mapping[str, Any]]] = []
    alias_to_id: dict[str, str] = {}
    step_index_to_id: dict[int, str] = {}
    for position, step in enumerate(steps, start=1):
        _assert_secret_free(step, path=f"steps[{position - 1}]")
        ordinal = _positive_int(step.get("step_index", position), field_name="step_index")
        candidate_id = _required_identifier(
            step.get("candidate_id")
            or step.get("node_id")
            or f"step-{ordinal:04d}",
            field_name=f"steps[{position - 1}].candidate_id",
        )
        if candidate_id in {value[0] for value in identities}:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"duplicate approved candidate id: {candidate_id}",
            )
        if ordinal in step_index_to_id:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"duplicate approved step_index: {ordinal}",
            )
        identities.append((candidate_id, ordinal, step))
        step_index_to_id[ordinal] = candidate_id
        for alias in (candidate_id, _text(step.get("node_id"))):
            if not alias:
                continue
            previous = alias_to_id.setdefault(alias, candidate_id)
            if previous != candidate_id:
                raise PlanIntentSearchError(
                    PLAN_INTENT_GRAPH_INVALID,
                    f"ambiguous approved node alias: {alias}",
                )

    nodes: list[_ApprovedNode] = []
    for position, (candidate_id, ordinal, step) in enumerate(identities):
        route = _text(step.get("route") or step.get("worker_route")).lower()
        if route not in _SUPPORTED_ROUTES:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"unsupported approved node route: {route or '<empty>'}",
            )
        target = _text(step.get("target_id") or step.get("agent_id"))
        if route == "chat":
            target = target or "surface_reply"
            if target != "surface_reply":
                raise PlanIntentSearchError(
                    PLAN_INTENT_GRAPH_INVALID,
                    "chat Plan nodes must target surface_reply",
                )
        target = _required_identifier(
            target,
            field_name=f"steps[{position}].target_id",
        )
        objective = _required_text(
            step.get("task")
            or step.get("task_description")
            or step.get("description"),
            field_name=f"steps[{position}].objective",
            max_length=4_000,
        )
        raw_dependencies = step.get("dependencies", step.get("depends_on", ()))
        dependency_values = _sequence(raw_dependencies, field_name="dependencies")
        dependencies: list[str] = []
        for raw_dependency in dependency_values:
            dependency = _resolve_dependency(
                raw_dependency,
                alias_to_id=alias_to_id,
                step_index_to_id=step_index_to_id,
            )
            if dependency == candidate_id:
                raise PlanIntentSearchError(
                    PLAN_INTENT_GRAPH_INVALID,
                    f"approved node depends on itself: {candidate_id}",
                )
            if dependency in dependencies:
                raise PlanIntentSearchError(
                    PLAN_INTENT_GRAPH_INVALID,
                    f"duplicate dependency for {candidate_id}: {dependency}",
                )
            dependencies.append(dependency)
        if not dependency_values and position > 0 and not bool(step.get("allow_parallel")):
            dependencies.append(identities[position - 1][0])

        args = _mapping_copy(step.get("args") or {}, field_name=f"steps[{position}].args")
        risk_level = _bounded_risk(step.get("risk_level", 2))
        acceptance = _text_tuple(
            step.get("acceptance_criteria")
            or (f"{candidate_id} returns a durable accepted result",),
            field_name="acceptance_criteria",
            allow_empty=False,
        )
        capabilities = _text_tuple(
            step.get("capability_requirements") or (target,),
            field_name="capability_requirements",
            allow_empty=False,
        )
        node_input_refs = _text_tuple(
            step.get("input_refs") or (),
            field_name="input_refs",
            allow_empty=True,
        )
        output_contract = _mapping_copy(
            step.get("output_contract")
            or {"type": "plan_node_result", "required": ["receipt_ref"]},
            field_name="output_contract",
        )
        if not output_contract:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                f"approved node {candidate_id} has an empty output_contract",
            )
        raw_retry = step.get("retry_policy")
        raw_retry_policy = _mapping_copy(
            raw_retry
            if raw_retry is not None
            else {
                "max_attempts": _positive_int(
                    step.get("retry_limit", 1),
                    field_name="retry_limit",
                ),
                "retry_backoff_ms": _non_negative_int(
                    step.get("retry_backoff_ms", 0),
                    field_name="retry_backoff_ms",
                ),
            },
            field_name="retry_policy",
        )
        try:
            retry_policy = normalize_task_retry_policy(
                raw_retry_policy,
                field_name=f"steps[{position}].retry_policy",
            )
        except ContractViolation as exc:
            raise PlanIntentSearchError(
                PLAN_INTENT_GRAPH_INVALID,
                exc.detail,
            ) from exc
        nodes.append(
            _ApprovedNode(
                candidate_id=candidate_id,
                ordinal=ordinal,
                route=route,
                target_id=target,
                objective=objective,
                dependencies=tuple(dependencies),
                args=args,
                risk_level=risk_level,
                acceptance_criteria=acceptance,
                capability_requirements=capabilities,
                input_refs=node_input_refs,
                output_contract=output_contract,
                retry_policy=retry_policy,
            )
        )
    return tuple(nodes)


def _topological_order(nodes: Sequence[_ApprovedNode]) -> tuple[str, ...]:
    node_by_id = {node.candidate_id: node for node in nodes}
    indegree = {node.candidate_id: len(node.dependencies) for node in nodes}
    successors = {node.candidate_id: [] for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            if dependency not in node_by_id:
                raise PlanIntentSearchError(
                    PLAN_INTENT_GRAPH_INVALID,
                    f"unknown dependency {dependency} for {node.candidate_id}",
                )
            successors[dependency].append(node.candidate_id)
    ready = [node for node in nodes if indegree[node.candidate_id] == 0]
    ready.sort(key=lambda item: (item.ordinal, item.candidate_id))
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node.candidate_id)
        for successor_id in sorted(
            successors[node.candidate_id],
            key=lambda value: (node_by_id[value].ordinal, value),
        ):
            indegree[successor_id] -= 1
            if indegree[successor_id] == 0:
                ready.append(node_by_id[successor_id])
                ready.sort(key=lambda item: (item.ordinal, item.candidate_id))
    if len(ordered) != len(nodes):
        blocked = sorted(set(node_by_id) - set(ordered))
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "approved Plan graph contains a cycle: " + ",".join(blocked),
        )
    return tuple(ordered)


def _graph_policy_fields(
    *,
    intent: Mapping[str, Any],
    nodes: Sequence[_ApprovedNode],
    request_id: str,
    intent_id: str,
    approval_ref: str,
    review_ref: str,
    budget_policy: Mapping[str, Any],
    business_scope: str,
    input_refs: tuple[str, ...],
    frozen_context: Mapping[str, Any],
) -> dict[str, Any]:
    domain = _text(intent.get("domain")).lower() or "skill"
    run_mode = _text(intent.get("run_mode")).lower() or "worker"
    if domain not in ALLOWED_AGENT_DOMAINS:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"unsupported WorkOrder domain: {domain}",
        )
    if run_mode not in ALLOWED_AGENT_RUN_MODES:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"unsupported WorkOrder run_mode: {run_mode}",
        )
    allowed_actions = tuple(
        dict.fromkeys(
            "render_reply"
            if node.route == "chat"
            else f"{node.route}:{node.target_id}"
            for node in nodes
        )
    )
    forbidden_actions = _text_tuple(
        intent.get("forbidden_actions")
        or ("reroute", "replan", "cross_executor_resume"),
        field_name="forbidden_actions",
        allow_empty=True,
    )
    candidate_inputs = {
        node.candidate_id: {
            "route": node.route,
            "target_id": node.target_id,
            "objective": node.objective,
            "args": node.args,
            "depends_on_candidate_ids": list(node.dependencies),
        }
        for node in nodes
    }
    return {
        "domain": domain,
        "run_mode": run_mode,
        "plan_objective": _text(intent.get("task") or intent.get("objective"))
        or nodes[0].objective,
        "allowed_actions": list(allowed_actions),
        "forbidden_actions": list(forbidden_actions),
        "verification_plan": {
            "required": True,
            "policy": "durable_worker_projection",
        },
        "approval_policy": {
            "required": True,
            "mode": "governor_grant",
            "grant_ref": approval_ref,
        },
        "review_policy": {
            "review_required": not review_ref.startswith("review-waiver://"),
            "mode": "governed",
            "review_ref": review_ref,
        },
        "budget_policy": _json_copy(budget_policy, field_name="budget_policy"),
        "business_idempotency_scope": business_scope,
        "approval_ref": approval_ref,
        "review_ref": review_ref,
        "workspace": _mapping_copy(intent.get("workspace") or {}, field_name="workspace"),
        "input_pack": {
            **_json_copy(frozen_context, field_name="frozen_context"),
            "request_id": request_id,
            "intent_id": intent_id,
            "approved_candidate_inputs": candidate_inputs,
        },
        "metadata": {
            "source": "approved_plan_intent_search",
            "intent_id": intent_id,
            "approved_routes": [node.route for node in nodes],
        },
        "input_refs": list(input_refs),
    }


def _candidate_from_node(
    node: _ApprovedNode,
    *,
    graph_fields: Mapping[str, Any],
    total_nodes: int,
) -> SearchCandidate:
    node_args_ref = "plan-node-input://sha256/" + _sha256_json(
        {
            "candidate_id": node.candidate_id,
            "args": node.args,
            "objective": node.objective,
        }
    )
    payload = {
        **_json_copy(graph_fields, field_name="graph_fields"),
        "worker_route": node.route,
        "target_id": node.target_id,
        "objective": node.objective,
        "acceptance_criteria": list(node.acceptance_criteria),
        "input_refs": list(
            _dedupe_text(
                graph_fields.get("input_refs"),
                node.input_refs,
                (node_args_ref,),
            )
        ),
        "output_contract": _json_copy(
            node.output_contract,
            field_name="output_contract",
        ),
        "retry_policy": _json_copy(node.retry_policy, field_name="retry_policy"),
        "risk_level": node.risk_level,
        "capability_requirements": list(node.capability_requirements),
        "depends_on_candidate_ids": list(node.dependencies),
        "candidate_idempotency_key": "plan-candidate://sha256/" + _sha256_json(
            {
                "business_scope": graph_fields["business_idempotency_scope"],
                "candidate_id": node.candidate_id,
                "route": node.route,
                "target_id": node.target_id,
            }
        ),
        "requires_lookahead": total_nodes > 1,
    }
    prior = 1.0 - (node.ordinal / (100.0 * max(1, total_nodes)))
    labels = ["approved_plan_node", node.route]
    if total_nodes > 1:
        labels.append("requires_lookahead")
    return SearchCandidate(
        candidate_id=node.candidate_id,
        prior_score=prior,
        payload=payload,
        labels=tuple(labels),
        simulation_only=True,
    )


def _require_governor_authority(
    *,
    intent: Mapping[str, Any],
    governor: Mapping[str, Any],
    request_id: str,
    intent_id: str,
) -> None:
    if _text(governor.get("decision")).upper() != "APPROVE":
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "Plan intent has no approved Governor decision",
        )
    if _text(governor.get("request_id")) not in {"", request_id}:
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "Governor request_id differs from the Plan request",
        )
    if _text(governor.get("intent_id")) != intent_id:
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "Governor intent_id differs from the Plan intent",
        )
    binding = durable_governor_binding(governor)
    args = dict(intent.get("args") or {}) if isinstance(intent.get("args"), Mapping) else {}
    try:
        binding_seq = int(binding.get("seq"))
        intent_seq = int(intent.get("seq"))
    except (TypeError, ValueError) as exc:
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "Plan intent/Governor sequence is invalid",
        ) from exc
    expected = {
        "intent_id": intent_id,
        "session_id": _text(intent.get("session_id")),
        "action_kind": _text(intent.get("action") or intent.get("action_kind")),
        "scope": _text(intent.get("target") or intent.get("scope")).lower(),
        "principal_id": _text(intent.get("principal_id") or intent.get("person_id")),
        "intent_args_digest": "sha256:" + canonical_payload_digest(args),
    }
    actual = {
        key: _text(binding.get(key)).lower() if key == "scope" else _text(binding.get(key))
        for key in expected
    }
    if (
        not binding
        or not all(expected.values())
        or actual != expected
        or binding_seq != intent_seq
    ):
        raise PlanIntentSearchError(
            PLAN_INTENT_AUTHORITY_INVALID,
            "Governor binding does not exactly authorize the Plan intent",
        )


def _resolve_budget_policy(
    *,
    explicit: Mapping[str, Any] | None,
    intent: Mapping[str, Any],
    scope: str,
    node_count: int,
) -> dict[str, Any]:
    candidate = explicit
    if candidate is None and isinstance(intent.get("budget_policy"), Mapping):
        candidate = intent.get("budget_policy")
    if candidate is None:
        candidate = {
            "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
            "scope": scope,
            "limits": {
                "token_count": 4_000 * node_count,
                "tool_call_count": 8 * node_count,
                "time_ms": 120_000 * node_count,
                "concurrency": min(4, node_count),
                "retry_count": node_count,
                "model_cost_micros": 250_000 * node_count,
            },
            "on_exceeded": "BLOCK",
            "metadata": {
                "policy": "approved_plan_intent_default",
                "fail_closed": True,
            },
        }
    payload = _mapping_copy(candidate, field_name="budget_policy")
    limits = payload.get("limits")
    if not isinstance(limits, Mapping):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "budget_policy.limits must be an object",
        )
    missing = [field for field in _BUDGET_LIMIT_FIELDS if field not in limits]
    if missing:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "budget policy is missing explicit limits: " + ",".join(missing),
        )
    normalized_limits = {
        field: _non_negative_int(limits.get(field), field_name=f"budget.{field}")
        for field in _BUDGET_LIMIT_FIELDS
    }
    if normalized_limits["time_ms"] < 1 or normalized_limits["concurrency"] < 1:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "budget time_ms and concurrency must be positive",
        )
    payload["limits"] = normalized_limits
    try:
        policy = BudgetPolicy.from_mapping(payload)
    except Exception as exc:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"budget policy is invalid: {exc}",
        ) from exc
    return policy.to_dict()


def _resolve_business_scope(
    *,
    explicit: str | None,
    intent: Mapping[str, Any],
    request_id: str,
    intent_id: str,
) -> str:
    candidate = _text(
        explicit or intent.get("business_idempotency_scope")
    )
    if not candidate:
        candidate = "host-plan:" + _sha256_json(
            {"request_id": request_id, "intent_id": intent_id}
        )[:40]
    if len(candidate) > 240:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "business_idempotency_scope exceeds 240 characters",
        )
    return candidate


def _resolve_review_ref(
    *,
    explicit: str | None,
    intent: Mapping[str, Any],
    governor: Mapping[str, Any],
    approval_ref: str,
) -> str:
    snapshots = governor.get("snapshot_refs")
    snapshot_map = dict(snapshots) if isinstance(snapshots, Mapping) else {}
    candidate = _text(
        explicit
        or intent.get("review_ref")
        or governor.get("review_ref")
        or snapshot_map.get("review_ref")
    )
    if not candidate:
        candidate = "review-waiver://plan/" + hashlib.sha256(
            approval_ref.encode("utf-8")
        ).hexdigest()
    if len(candidate) > 1_000:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            "review_ref exceeds 1000 characters",
        )
    return candidate


def _default_search_budget(node_count: int) -> SearchBudget:
    rollouts = min(16, max(2, node_count * 2))
    return SearchBudget(
        max_candidates=max(64, node_count * node_count * rollouts),
        max_expansions=max(8, 1 + node_count * rollouts),
        max_transitions=max(8, node_count * rollouts),
        max_rollouts=rollouts,
        max_depth=node_count,
        branching_factor=max(1, node_count),
        mcts_score_gap=1.0,
    )


def _require_complete_result(
    result: SearchResult,
    *,
    approved_candidate_ids: Sequence[str],
    dependencies: Mapping[str, Sequence[str]],
) -> None:
    approved = tuple(str(value) for value in approved_candidate_ids)
    path = tuple(candidate.candidate_id for candidate in result.selected_path)
    if (
        result.status is not SearchStatus.SELECTED
        or result.side_effects_executed
        or len(path) != len(approved)
        or len(set(path)) != len(path)
        or set(path) != set(approved)
        or not _is_topological(path, dependencies)
        or (len(approved) > 1 and not result.mcts_used)
    ):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INCOMPLETE,
            "PlanningSearch did not select every approved node in topological order",
        )


def _is_topological(
    path: Sequence[str],
    dependencies: Mapping[str, Sequence[str]],
) -> bool:
    completed: set[str] = set()
    for identifier in path:
        if identifier not in dependencies:
            return False
        if not set(dependencies[identifier]).issubset(completed):
            return False
        completed.add(identifier)
    return True


def _descendant_counts(
    dependencies: Mapping[str, Sequence[str]],
) -> dict[str, int]:
    successors = {str(identifier): set() for identifier in dependencies}
    for identifier, parents in dependencies.items():
        for parent in parents:
            successors[str(parent)].add(str(identifier))
    counts: dict[str, int] = {}
    for identifier in sorted(successors):
        seen: set[str] = set()
        pending = list(successors[identifier])
        while pending:
            child = pending.pop()
            if child in seen:
                continue
            seen.add(child)
            pending.extend(successors[child])
        counts[identifier] = len(seen)
    return counts


def _resolve_dependency(
    value: Any,
    *,
    alias_to_id: Mapping[str, str],
    step_index_to_id: Mapping[int, str],
) -> str:
    if isinstance(value, bool):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "dependency references cannot be booleans",
        )
    if isinstance(value, int):
        resolved = step_index_to_id.get(value)
    else:
        text = _text(value)
        resolved = alias_to_id.get(text)
        if resolved is None and text.isdigit():
            resolved = step_index_to_id.get(int(text))
    if not resolved:
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            f"unknown approved dependency: {value}",
        )
    return resolved


def _virtual_completed(state: Any) -> tuple[str, ...]:
    if not isinstance(state, Mapping):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "virtual search state must be an object",
        )
    raw = state.get("completed_candidate_ids", ())
    if not isinstance(raw, (list, tuple)):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "virtual completed_candidate_ids must be a sequence",
        )
    values = tuple(_text(value) for value in raw)
    if any(not value for value in values) or len(values) != len(set(values)):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "virtual completed_candidate_ids are invalid",
        )
    return values


def _same_request(candidate: SearchRequest, expected: SearchRequest) -> bool:
    return (
        isinstance(candidate, SearchRequest)
        and candidate.request_id == expected.request_id
        and candidate.initial_state == expected.initial_state
        and candidate.context == expected.context
        and candidate.budget == expected.budget
        and candidate.mcts_mode is expected.mcts_mode
        and _same_state_snapshot(candidate, expected)
    )


def _same_state_snapshot(candidate: SearchRequest, expected: SearchRequest) -> bool:
    left = candidate.state_snapshot
    right = expected.state_snapshot
    if left is None or right is None:
        return left is right
    return (
        left.schema_version == right.schema_version
        and left.state_digest == right.state_digest
    )


def _assert_secret_free(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                raise PlanIntentSearchError(
                    PLAN_INTENT_SENSITIVE_FIELD,
                    f"sensitive key is forbidden in approved Plan input: {path}.{key}",
                )
            _assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    if normalized in _SAFE_SENSITIVE_WORD_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return any(
        part in _SENSITIVE_KEY_PARTS
        for part in normalized.split("_")
        if part
    )


def _mapping_copy(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be an object",
        )
    copied = _json_copy(dict(value), field_name=field_name)
    if not isinstance(copied, dict):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be an object",
        )
    return copied


def _json_copy(value: Any, *, field_name: str) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be JSON-safe",
        ) from exc


def _sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            f"{field_name} must be a sequence",
        )
    return tuple(value)


def _text_tuple(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    values = _sequence(value, field_name=field_name)
    normalized = tuple(_required_text(item, field_name=field_name) for item in values)
    if not allow_empty and not normalized:
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            f"{field_name} must not be empty",
        )
    if len(normalized) != len(set(normalized)):
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            f"{field_name} contains duplicates",
        )
    return normalized


def _dedupe_text(*groups: Any) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            candidates = (group,)
        elif isinstance(group, Sequence):
            candidates = group
        else:
            raise PlanIntentSearchError(
                PLAN_INTENT_SEARCH_INVALID,
                "reference collection must be a sequence",
            )
        for raw in candidates:
            value = _required_text(raw, field_name="reference", max_length=1_000)
            if value not in values:
                values.append(value)
    return tuple(values)


def _required_identifier(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, max_length=240)
    if not _IDENTIFIER_RE.fullmatch(text):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} is not a valid identifier",
        )
    return text


def _required_text(
    value: Any,
    *,
    field_name: str,
    max_length: int = 1_000,
) -> str:
    text = _text(value)
    has_forbidden_control = any(
        ord(char) < 32 and char not in "\t\n\r" for char in text
    )
    if not text or len(text) > max_length or has_forbidden_control:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} is missing or invalid",
        )
    return text


def _bounded_risk(value: Any) -> int:
    risk = _positive_int(value, field_name="risk_level")
    if risk > 5:
        raise PlanIntentSearchError(
            PLAN_INTENT_GRAPH_INVALID,
            "risk_level must be between 1 and 5",
        )
    return risk


def _positive_int(value: Any, *, field_name: str) -> int:
    parsed = _non_negative_int(value, field_name=field_name)
    if parsed < 1:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be positive",
        )
    return parsed


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be an integer",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be an integer",
        ) from exc
    if parsed < 0:
        raise PlanIntentSearchError(
            PLAN_INTENT_SEARCH_INVALID,
            f"{field_name} must be non-negative",
        )
    return parsed


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "PLAN_INTENT_AUTHORITY_INVALID",
    "PLAN_INTENT_GRAPH_INVALID",
    "PLAN_INTENT_SEARCH_INCOMPLETE",
    "PLAN_INTENT_SEARCH_INVALID",
    "PLAN_INTENT_SENSITIVE_FIELD",
    "PlanConditionalMCTS",
    "PlanCoverageReward",
    "PlanDependencyExpander",
    "PlanDeterministicCognitiveRanker",
    "PlanIntentSearchAdapter",
    "PlanIntentSearchError",
    "PlanVirtualTransition",
    "build_plan_intent_search",
    "search_approved_plan_intent",
]
