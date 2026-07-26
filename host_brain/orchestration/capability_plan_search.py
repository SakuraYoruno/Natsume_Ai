"""Host entry adapter for registry-derived PlanningSearch candidates.

This is intentionally separate from :mod:`plan_intent_search`: that legacy
adapter projects one already-approved ``planned_steps`` graph.  This adapter
freezes a capability inventory from the composed SkillRegistry and lets the
generic L1/L2/L3 service choose a path from competing registered capabilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from host_brain.orchestration.goal_planning import (
    GoalPlanningDecision,
    GoalPlanningRequest,
    GoalPlanningService,
)
from host_brain.orchestration.governor_grant import (
    durable_governor_binding,
    governor_grant_ref,
)
from host_brain.orchestration.plan_intent_search import (
    PlanIntentSearchError,
    require_governed_plan_authority,
    resolve_governed_plan_budget_policy,
    resolve_governed_plan_business_scope,
    resolve_governed_plan_review_ref,
)
from host_brain.orchestration.planning_graph_compiler import PlanningCompilationContext
from host_brain.planning_search.capability_domain import (
    CapabilityPlanningDomain,
    bind_selected_capability_inputs,
)
from host_brain.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_DOMAIN,
    CAPABILITY_PLANNING_EXECUTION_STEPS,
    CAPABILITY_PLANNING_MODE,
    CAPABILITY_PLANNING_TARGET_ID,
    CapabilityPlanningError,
    CapabilityPlanningSnapshot,
    PlanningCapability,
)
from host_brain.planning_search.contracts import (
    MCTSMode,
    PlanningGoal,
    SearchBudget,
    SearchRequest,
    SearchResult,
)


CAPABILITY_PLAN_SEARCH_INVALID = "CAPABILITY_PLAN_SEARCH_INVALID"
CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID = "CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID"
CAPABILITY_PLAN_SEARCH_NO_COMPETITION = "CAPABILITY_PLAN_SEARCH_NO_COMPETITION"

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_SAFE_SENSITIVE_WORD_KEYS = frozenset({"token_count"})
_TAG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class CapabilityPlanSearchError(ValueError):
    """A Host capability plan cannot enter generic PlanningSearch."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CAPABILITY_PLAN_SEARCH_INVALID)
        self.detail = str(detail or "capability plan search is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class CapabilityPlanSearchAdapter:
    """One immutable Host planning request backed by a frozen registry view."""

    request: SearchRequest
    goal_request: GoalPlanningRequest
    snapshot: CapabilityPlanningSnapshot
    base_context: PlanningCompilationContext

    def search(self, request: SearchRequest) -> SearchResult:
        if not _same_request(request, self.request):
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                "search request differs from the frozen capability plan request",
            )
        decision = GoalPlanningService(
            ports=CapabilityPlanningDomain(snapshot=self.snapshot).planning_ports()
        ).search(self.goal_request)
        return decision.result

    def compilation_context_for(self, result: SearchResult) -> PlanningCompilationContext:
        if not isinstance(result, SearchResult):
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                "compilation context requires a SearchResult",
            )
        if result.request_id != self.request.request_id:
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                "search result request_id differs from the frozen request",
            )
        try:
            decision = GoalPlanningDecision(request=self.goal_request, result=result)
            return bind_selected_capability_inputs(
                self.base_context,
                snapshot=self.snapshot,
                decision=decision,
            )
        except CapabilityPlanningError as exc:
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                str(exc),
            ) from exc


def is_capability_plan_intent(intent: Mapping[str, Any] | None) -> bool:
    """Return true only for the explicit registry-derived plan mode."""

    payload = dict(intent or {})
    args = payload.get("args")
    return (
        str(payload.get("route") or "").strip().lower() == "plan"
        and str(payload.get("target_id") or "").strip()
        == CAPABILITY_PLANNING_TARGET_ID
        and isinstance(args, Mapping)
        and str(args.get("planning_mode") or "").strip()
        == CAPABILITY_PLANNING_MODE
        and not _has_legacy_plan_steps(payload)
    )


def build_capability_plan_search(
    intent: Mapping[str, Any],
    *,
    governor_result: Mapping[str, Any],
    skill_registry: Any,
    request_id: str | None = None,
    review_ref: str | None = None,
    budget_policy: Mapping[str, Any] | None = None,
    business_idempotency_scope: str | None = None,
    input_refs: Sequence[str] = (),
    frozen_context: Mapping[str, Any] | None = None,
) -> CapabilityPlanSearchAdapter:
    """Build a true capability-search request without accepting ``planned_steps``.

    The caller supplies only Governor-bound task arguments and the composed
    registry.  Candidate targets, risk metadata and costs are reconstructed
    from the registry; a caller cannot promote an arbitrary target by naming
    it in the incoming intent.
    """

    raw_intent = _mapping_copy(intent, field_name="intent")
    governor = _mapping_copy(governor_result, field_name="governor_result")
    context = _mapping_copy(frozen_context or {}, field_name="frozen_context")
    _assert_secret_free(raw_intent, path="intent")
    _assert_secret_free(context, path="frozen_context")
    if _has_legacy_plan_steps(raw_intent):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "capability planning refuses legacy planned_steps/steps input at the plan boundary",
        )
    if not is_capability_plan_intent(raw_intent):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "capability planning requires route=plan, target_id=planning_search and explicit planning_mode",
        )
    if not callable(getattr(skill_registry, "all", None)):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "capability planning requires the composed SkillRegistry",
        )

    request_ref = _required_text(
        request_id or governor.get("request_id") or raw_intent.get("request_id"),
        field_name="request_id",
        max_length=240,
    )
    intent_id = _required_text(
        raw_intent.get("id") or raw_intent.get("intent_id"),
        field_name="intent.id",
        max_length=240,
    )
    try:
        require_governed_plan_authority(
            intent=raw_intent,
            governor=governor,
            request_id=request_ref,
            intent_id=intent_id,
        )
    except PlanIntentSearchError as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
            exc.detail,
        ) from exc

    args = _mapping_copy(raw_intent.get("args") or {}, field_name="intent.args")
    objective = _required_text(
        args.get("task") or raw_intent.get("task") or raw_intent.get("objective"),
        field_name="intent.args.task",
        max_length=4_000,
    )
    top_level_task = str(raw_intent.get("task") or raw_intent.get("objective") or "").strip()
    if top_level_task and top_level_task != objective:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
            "top-level task differs from Governor-bound args.task",
        )
    requested_tags = _tags(
        args.get("planning_capability_tags"),
        field_name="intent.args.planning_capability_tags",
    )
    requested_max_risk_level = _max_risk(
        args.get("planning_max_risk_level", 3)
    )
    approval_ref = governor_grant_ref(request_ref, governor)
    governor_max_risk_level = _governor_risk_ceiling(governor)
    # The intent describes a requested constraint, never a permission grant.
    # A selected capability must therefore satisfy both the frozen request and
    # the Governor's approved authority ceiling.
    max_risk_level = min(requested_max_risk_level, governor_max_risk_level)
    task_ref = _governed_ref_from_input_refs(input_refs)
    capabilities = _registry_capabilities(
        skill_registry,
        requested_tags=requested_tags,
        max_risk_level=max_risk_level,
    )
    if len(capabilities) < 2:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_NO_COMPETITION,
            "frozen registry has fewer than two eligible capability alternatives",
        )
    try:
        snapshot = CapabilityPlanningSnapshot(
            task_ref=task_ref,
            objective=objective,
            requested_tags=requested_tags,
            max_risk_level=max_risk_level,
            capabilities=capabilities,
            source_refs=(approval_ref,),
        )
    except CapabilityPlanningError as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            str(exc),
        ) from exc

    try:
        resolved_scope = resolve_governed_plan_business_scope(
            explicit=business_idempotency_scope,
            intent=raw_intent,
            request_id=request_ref,
            intent_id=intent_id,
        )
        resolved_budget = resolve_governed_plan_budget_policy(
            explicit=budget_policy,
            intent=raw_intent,
            scope=resolved_scope,
            node_count=CAPABILITY_PLANNING_EXECUTION_STEPS + 1,
        )
        resolved_review_ref = resolve_governed_plan_review_ref(
            explicit=review_ref,
            intent=raw_intent,
            governor=governor,
            approval_ref=approval_ref,
        )
    except PlanIntentSearchError as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            exc.detail,
        ) from exc

    state = snapshot.to_virtual_state()
    goal = PlanningGoal(
        goal_id="capability-plan:" + _sha256_text(
            {"request_id": request_ref, "intent_id": intent_id, "catalog": snapshot.catalog_digest}
        )[:40],
        domain=CAPABILITY_PLANNING_DOMAIN,
        objective=objective,
        success_criteria=(
            "select a bounded distinct registry-proven capability path",
            "return a governed synthesized result",
        ),
        frozen_context_refs=(task_ref, snapshot.catalog_ref),
    )
    search_budget = SearchBudget(
        max_candidates=min(48, max(12, len(capabilities) * 3)),
        max_expansions=min(48, max(12, len(capabilities) * 3)),
        max_transitions=min(72, max(18, len(capabilities) * 5)),
        max_rollouts=min(32, max(12, len(capabilities) * 6)),
        max_depth=CAPABILITY_PLANNING_EXECUTION_STEPS + 1,
        branching_factor=min(8, max(2, len(capabilities))),
    )
    goal_request = GoalPlanningRequest(
        request_id=request_ref,
        goal=goal,
        state=state,
        budget=search_budget,
        context={
            "schema_version": "natsume.capability-planning.context.v1",
            "catalog_ref": snapshot.catalog_ref,
            "catalog_digest": snapshot.catalog_digest,
        },
        mcts_mode=MCTSMode.AUTO,
    )
    search_request = goal_request.to_search_request()
    base_context = PlanningCompilationContext(
        domain=CAPABILITY_PLANNING_DOMAIN,
        run_mode="worker",
        plan_objective=objective,
        allowed_actions=(),
        forbidden_actions=("reroute", "replan", "cross_executor_resume"),
        verification_plan={
            "required": True,
            "policy": "durable_worker_projection",
        },
        approval_policy={
            "required": True,
            "mode": "governor_grant",
            "grant_ref": approval_ref,
        },
        review_policy={
            "review_required": not resolved_review_ref.startswith("review-waiver://"),
            "mode": "governed",
            "review_ref": resolved_review_ref,
        },
        budget_policy=resolved_budget,
        business_idempotency_scope=resolved_scope,
        approval_ref=approval_ref,
        review_ref=resolved_review_ref,
        workspace=_mapping_copy(raw_intent.get("workspace") or {}, field_name="workspace"),
        input_pack={
            **context,
            "request_id": request_ref,
            "intent_id": intent_id,
            "capability_plan_request": {
                "route": "plan",
                "planning_mode": CAPABILITY_PLANNING_MODE,
                "task": objective,
                "requested_tags": list(requested_tags),
                "requested_max_risk_level": requested_max_risk_level,
                "governor_max_risk_level": governor_max_risk_level,
                "effective_max_risk_level": max_risk_level,
                "execution_steps": CAPABILITY_PLANNING_EXECUTION_STEPS,
            },
        },
        input_refs=_dedupe_refs((*input_refs, approval_ref, snapshot.catalog_ref)),
        metadata={
            "source": "registry_capability_planning_search",
            "intent_id": intent_id,
            "catalog_ref": snapshot.catalog_ref,
            "execution_steps": CAPABILITY_PLANNING_EXECUTION_STEPS,
        },
        goal=goal,
        virtual_state=state,
    )
    return CapabilityPlanSearchAdapter(
        request=search_request,
        goal_request=goal_request,
        snapshot=snapshot,
        base_context=base_context,
    )


def _has_legacy_plan_steps(payload: Mapping[str, Any]) -> bool:
    """Reject caller-owned execution lists in both supported intent locations.

    Legacy plan envelopes have historically carried ``planned_steps`` or
    ``steps`` at the top level and, for compatibility, duplicated them in
    ``args``.  A registry-derived plan cannot accept either form: allowing the
    nested copy would recreate the same caller-authorized execution path under
    the canonical capability mode.
    """

    if "planned_steps" in payload or "steps" in payload:
        return True
    args = payload.get("args")
    return isinstance(args, Mapping) and (
        "planned_steps" in args or "steps" in args
    )


def _registry_capabilities(
    skill_registry: Any,
    *,
    requested_tags: tuple[str, ...],
    max_risk_level: int,
) -> tuple[PlanningCapability, ...]:
    try:
        values = tuple(skill_registry.all())
    except Exception as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "SkillRegistry capability inventory could not be read",
        ) from exc
    resolved: list[PlanningCapability] = []
    for raw in values:
        capability_id = str(getattr(raw, "skill_name", "") or "").strip().lower()
        family = str(getattr(raw, "family", "") or "general").strip().lower()
        scope = str(getattr(raw, "scope", "") or "").strip().lower()
        risk_level = getattr(raw, "risk_level", None)
        require_confirm = getattr(raw, "require_confirm", None)
        tags = tuple(
            str(value or "").strip().lower()
            for value in tuple(getattr(raw, "capability_tags", ()) or ())
            if str(value or "").strip()
        )
        tags = tuple(dict.fromkeys((*tags, family)))
        if (
            scope != "host"
            or not isinstance(require_confirm, bool)
            or isinstance(risk_level, bool)
            or not isinstance(risk_level, int)
            or risk_level > max_risk_level
            or not set(tags).intersection(requested_tags)
        ):
            continue
        try:
            resolved.append(
                PlanningCapability(
                    capability_id=capability_id,
                    family=family,
                    capability_tags=tags,
                    risk_level=risk_level,
                    reversible=bool(getattr(raw, "reversible", False)),
                    parallelizable=bool(getattr(raw, "parallelizable", False)),
                    estimated_cost=_estimated_cost(
                        risk_level=risk_level,
                        reversible=bool(getattr(raw, "reversible", False)),
                        parallelizable=bool(getattr(raw, "parallelizable", False)),
                    ),
                    requires_review=require_confirm,
                )
            )
        except CapabilityPlanningError:
            # A malformed registry row cannot become a plan candidate.
            continue
    return tuple(sorted(resolved, key=lambda item: item.capability_id))


def _estimated_cost(
    *,
    risk_level: int,
    reversible: bool,
    parallelizable: bool,
) -> float:
    value = 0.10 + 0.12 * (int(risk_level) - 1)
    if not reversible:
        value += 0.08
    if not parallelizable:
        value += 0.03
    return min(1.0, round(value, 10))


def _same_request(left: SearchRequest, right: SearchRequest) -> bool:
    return (
        isinstance(left, SearchRequest)
        and left.request_id == right.request_id
        and left.budget == right.budget
        and left.context == right.context
        and left.mcts_mode == right.mcts_mode
        and left.state_snapshot == right.state_snapshot
        and left.initial_state == right.initial_state
    )


def _mapping_copy(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} must be an object",
        )
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} must be JSON-safe",
        ) from exc
    if not isinstance(decoded, dict):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} must be an object",
        )
    return decoded


def _assert_secret_free(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key or "").strip().lower()
            if (
                key in _SENSITIVE_EXACT_KEYS
                or (
                    any(part in key for part in _SENSITIVE_EXACT_KEYS)
                    and key not in _SAFE_SENSITIVE_WORD_KEYS
                )
            ):
                raise CapabilityPlanSearchError(
                    CAPABILITY_PLAN_SEARCH_INVALID,
                    f"{path}.{key} is not allowed in planning input",
                )
            _assert_secret_free(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_secret_free(item, path=f"{path}[{index}]")


def _tags(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} must be an explicit non-empty list",
        )
    normalized: list[str] = []
    for index, raw in enumerate(value):
        tag = _required_text(raw, field_name=f"{field_name}[{index}]", max_length=64).lower()
        if _TAG_RE.fullmatch(tag) is None:
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                f"{field_name}[{index}] is invalid",
            )
        if tag not in normalized:
            normalized.append(tag)
    if not normalized:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} must not be empty",
        )
    return tuple(sorted(normalized))


def _max_risk(value: Any) -> int:
    if isinstance(value, bool):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "planning_max_risk_level must be an integer",
        )
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "planning_max_risk_level must be an integer",
        ) from exc
    if not 1 <= resolved <= 3:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            "planning_max_risk_level must be between 1 and 3",
        )
    return resolved


def _governor_risk_ceiling(governor: Mapping[str, Any]) -> int:
    """Read the exact approved authority ceiling for a capability plan.

    The generic Plan binding already proves that the Governor approved the
    complete intent payload.  This additional check ensures a bounded
    capability search cannot reinterpret a broad ``planning_max_risk_level``
    field as authority to select a capability riskier than that approval.
    """

    binding = durable_governor_binding(governor)
    raw = binding.get("risk_level")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
            "Governor binding has no valid integer risk ceiling",
        )
    resolved = int(raw)
    if not 1 <= resolved <= 5:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
            "Governor binding risk ceiling must be between 1 and 5",
        )
    return resolved


def _governed_ref_from_input_refs(input_refs: Sequence[str]) -> str:
    for raw in input_refs:
        value = str(raw or "").strip()
        if "://" in value:
            return value
    raise CapabilityPlanSearchError(
        CAPABILITY_PLAN_SEARCH_INVALID,
        "capability planning requires a governed ingress input_ref",
    )


def _dedupe_refs(values: Sequence[str]) -> tuple[str, ...]:
    resolved: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if "://" not in value:
            raise CapabilityPlanSearchError(
                CAPABILITY_PLAN_SEARCH_INVALID,
                "planning input refs must be governed references",
            )
        if value not in resolved:
            resolved.append(value)
    return tuple(resolved)


def _required_text(value: Any, *, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise CapabilityPlanSearchError(
            CAPABILITY_PLAN_SEARCH_INVALID,
            f"{field_name} is required",
        )
    return normalized


def _sha256_text(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID",
    "CAPABILITY_PLAN_SEARCH_INVALID",
    "CAPABILITY_PLAN_SEARCH_NO_COMPETITION",
    "CapabilityPlanSearchAdapter",
    "CapabilityPlanSearchError",
    "build_capability_plan_search",
    "is_capability_plan_intent",
]
