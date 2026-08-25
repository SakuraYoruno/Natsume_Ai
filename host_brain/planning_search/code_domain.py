"""Pure Code-domain adapters for the shared PlanningSearch kernel.

The immutable evidence boundary lives in :mod:`code_domain_models`.  This
module only derives virtual candidates, ranks them and binds a selected,
catalog-validated path into the existing graph compiler.  It never opens a
repository, applies a patch, invokes an Agent or calls a verifier.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any

from host_brain.planning_search.code_domain_models import (
    CODE_DOMAIN,
    CODE_NODE_OUTPUT_SCHEMA,
    CODE_SHADOW_MODE,
    CodePatchSpec,
    CodePlanningError,
    CodeTaskSnapshot,
    _input_refs_for,
    _objective_for,
    _rounded,
    _thaw_json,
)
from host_brain.contracts.planning_search.contracts import (
    CandidateScore,
    SearchCandidate,
    VirtualStateSnapshot,
)

if TYPE_CHECKING:
    from host_brain.orchestration.goal_planning import GoalPlanningDecision
    from host_brain.orchestration.planning_graph_compiler import (
        PlanningCompilationContext,
    )


_CODE_SHADOW_ALLOWED_ACTIONS = frozenset({"generate_report"})
_CODE_SHADOW_REQUIRED_FORBIDDEN_ACTIONS = frozenset({"write_files", "network"})
_CODE_SHADOW_WORKSPACE = {"strategy": "temp_workspace"}


class CodeSuccessorCatalog:
    """Dynamic L1 Code successors derived only from virtual state/path."""

    def __init__(self, *, snapshot: CodeTaskSnapshot) -> None:
        if not isinstance(snapshot, CodeTaskSnapshot):
            raise TypeError("snapshot must be CodeTaskSnapshot")
        self._snapshot = snapshot
        self._patches = {item.patch_id: item for item in snapshot.patch_catalog}

    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]:
        del context, depth
        snapshot = _code_state(state, expected=self._snapshot)
        if int(limit) <= 0:
            return ()
        facts = _facts(snapshot)
        phase = str(facts.get("phase") or "root").strip().lower()
        if phase == "root":
            candidates = self._inspection_candidates()
        elif phase == "inspected":
            candidates = self._patch_candidates(path=path)
        elif phase == "patched":
            candidates = self._verification_candidates(facts=facts, path=path)
        else:
            candidates = ()
        return tuple(candidates[: max(0, int(limit))])

    def transition(
        self,
        *,
        state: Any,
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> VirtualStateSnapshot:
        del context
        current = _code_state(state, expected=self._snapshot)
        candidate_id = str(candidate.candidate_id or "").strip()
        expected = {
            item.candidate_id: item
            for item in self.expand(
                state=current,
                path=path[:-1],
                depth=max(0, len(path) - 1),
                limit=128,
                context={},
            )
        }.get(candidate_id)
        if expected is None or expected.to_dict() != candidate.to_dict():
            raise CodePlanningError(
                "candidate is not a legal successor of the current Code state"
            )
        facts = _thaw_json(current.facts)
        action = str(candidate.payload.get("code_action") or "").strip().lower()
        if action.startswith("inspect"):
            facts["phase"] = "inspected"
            facts["inspection_strategy"] = action.removeprefix("inspect_")
        elif action == "patch":
            patch_id = str(candidate.payload.get("patch_id") or "").strip()
            patch = self._patches.get(patch_id)
            if patch is None:
                raise CodePlanningError("patch candidate is not pre-registered")
            facts["phase"] = "patched"
            facts["selected_patch_id"] = patch.patch_id
            facts["estimated_pass_probability"] = _rounded(
                patch.estimated_pass_probability
            )
            facts["estimated_risk"] = _rounded(patch.estimated_risk)
            facts["estimated_cost"] = _rounded(patch.estimated_cost)
        elif action.startswith("verify_"):
            facts["phase"] = "verified"
            facts["verification_status"] = "simulated_predicted_pass"
            facts["verification_kind"] = action.removeprefix("verify_")
        elif action == "rollback":
            facts["phase"] = "rolled_back"
            facts["verification_status"] = "rollback_selected"
        else:
            raise CodePlanningError("unsupported Code virtual action")
        return current.replace(facts=facts)

    def _inspection_candidates(self) -> tuple[SearchCandidate, ...]:
        return (
            _candidate(
                candidate_id="code.inspect.targeted",
                action="inspect_targeted",
                objective=_objective_for("inspect_targeted", self._snapshot),
                input_refs=_input_refs_for("inspect_targeted", self._snapshot),
                prior_score=0.58,
                risk_level=1,
                test_coverage=0.82,
                estimated_cost=0.12,
                requires_lookahead=self._snapshot.virtual_model_ready,
                virtual_model_ready=self._snapshot.virtual_model_ready,
            ),
            _candidate(
                candidate_id="code.inspect.broad",
                action="inspect_broad",
                objective=_objective_for("inspect_broad", self._snapshot),
                input_refs=_input_refs_for("inspect_broad", self._snapshot),
                prior_score=0.42,
                risk_level=1,
                test_coverage=0.55,
                estimated_cost=0.28,
                requires_lookahead=self._snapshot.virtual_model_ready,
                virtual_model_ready=self._snapshot.virtual_model_ready,
            ),
        )

    def _patch_candidates(
        self,
        *,
        path: tuple[SearchCandidate, ...],
    ) -> tuple[SearchCandidate, ...]:
        values: list[SearchCandidate] = []
        dependency_id = (
            path[-1].candidate_id
            if path and str(path[-1].candidate_id).startswith("code.inspect.")
            else ""
        )
        if not dependency_id:
            raise CodePlanningError("Code patch successors require an inspected path")
        for patch in self._snapshot.patch_catalog:
            values.append(
                _candidate(
                    candidate_id=f"code.patch.{patch.patch_id}",
                    action="patch",
                    objective=_objective_for("patch", self._snapshot, patch=patch),
                    input_refs=_input_refs_for("patch", self._snapshot, patch=patch),
                    prior_score=(
                        0.65 * patch.estimated_pass_probability
                        - 0.20 * patch.estimated_risk
                        - 0.15 * patch.estimated_cost
                    ),
                    risk_level=1 + int(round(4 * patch.estimated_risk)),
                    test_coverage=min(
                        1.0,
                        len(patch.test_ids)
                        / max(1, len(self._snapshot.failing_test_ids)),
                    ),
                    estimated_cost=patch.estimated_cost,
                    requires_lookahead=patch.requires_lookahead,
                    virtual_model_ready=self._snapshot.virtual_model_ready,
                    patch=patch,
                    depends_on_candidate_ids=(dependency_id,),
                )
            )
        return tuple(values)

    def _verification_candidates(
        self,
        *,
        facts: Mapping[str, Any],
        path: tuple[SearchCandidate, ...],
    ) -> tuple[SearchCandidate, ...]:
        patch_id = str(facts.get("selected_patch_id") or "").strip()
        patch = self._patches.get(patch_id)
        if patch is None:
            return ()
        patch_candidate_id = f"code.patch.{patch.patch_id}"
        for item in reversed(path):
            if str(item.candidate_id).startswith("code.patch."):
                patch_candidate_id = item.candidate_id
                break
        return (
            _candidate(
                candidate_id=f"code.verify.unit.{patch.patch_id}",
                action="verify_unit",
                objective=_objective_for("verify_unit", self._snapshot, patch=patch),
                input_refs=_input_refs_for(
                    "verify_unit", self._snapshot, patch=patch
                ),
                prior_score=patch.estimated_pass_probability,
                risk_level=1,
                test_coverage=1.0,
                estimated_cost=0.08,
                requires_lookahead=False,
                virtual_model_ready=self._snapshot.virtual_model_ready,
                patch=patch,
                depends_on_candidate_ids=(patch_candidate_id,),
            ),
            _candidate(
                candidate_id=f"code.rollback.{patch.patch_id}",
                action="rollback",
                objective=_objective_for("rollback", self._snapshot, patch=patch),
                input_refs=_input_refs_for("rollback", self._snapshot, patch=patch),
                prior_score=-0.35,
                risk_level=1,
                test_coverage=0.0,
                estimated_cost=0.03,
                requires_lookahead=False,
                virtual_model_ready=self._snapshot.virtual_model_ready,
                patch=patch,
                depends_on_candidate_ids=(patch_candidate_id,),
            ),
        )


class CodeCognitiveRanker:
    """Deterministic L2 ordering from bounded virtual Code evidence."""

    def rank(
        self,
        *,
        state: Any,
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Sequence[CandidateScore]:
        del state, context
        scores: list[CandidateScore] = []
        for candidate in candidates:
            payload = candidate.payload
            coverage = _payload_unit(payload, "estimated_test_coverage")
            risk = _payload_unit(payload, "estimated_risk")
            cost = _payload_unit(payload, "estimated_cost")
            score = (
                float(candidate.prior_score)
                + 0.40 * coverage
                - 0.24 * risk
                - 0.16 * cost
                + (0.08 if payload.get("requires_lookahead") is True else 0.0)
            )
            scores.append(
                CandidateScore(
                    candidate_id=candidate.candidate_id,
                    score=score,
                    reason_code="code_virtual_evidence_rank",
                )
            )
        return tuple(scores)


class CodeRewardModel:
    """L3 reward over estimates only; actual verifier outcomes remain external."""

    def __init__(self, *, snapshot: CodeTaskSnapshot) -> None:
        self._snapshot = snapshot
        self._patches = {item.patch_id: item for item in snapshot.patch_catalog}

    def evaluate(
        self,
        *,
        initial_state: Any,
        state: Any,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, path, context
        snapshot = _code_state(state, expected=self._snapshot)
        facts = _facts(snapshot)
        phase = str(facts.get("phase") or "").strip().lower()
        patch = self._patches.get(str(facts.get("selected_patch_id") or "").strip())
        if phase == "rolled_back":
            return -0.85
        if patch is None:
            return -0.30 if phase != "inspected" else 0.05
        value = (
            1.25 * patch.estimated_pass_probability
            - 0.55 * patch.estimated_risk
            - 0.30 * patch.estimated_cost
        )
        if phase == "verified":
            return value + 0.35
        if phase == "patched":
            return value
        return -0.10


class CodeMCTSCondition:
    """Use L3 only when competing Code paths need look-ahead."""

    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool:
        del context
        if not candidates or not all(
            item.payload.get("virtual_model_ready") is True for item in candidates
        ):
            return False
        if any(item.payload.get("requires_lookahead") is True for item in candidates):
            return True
        if len(candidates) < 2:
            return False
        ordered = sorted(
            (float(scores.get(item.candidate_id, item.prior_score)) for item in candidates),
            reverse=True,
        )
        return abs(ordered[0] - ordered[1]) <= 0.20


@dataclass(frozen=True, slots=True)
class CodePlanningDomain:
    """One immutable bundle of pure Code planning adapters."""

    snapshot: CodeTaskSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, CodeTaskSnapshot):
            raise TypeError("snapshot must be CodeTaskSnapshot")

    def planning_ports(self):  # noqa: ANN201
        """Return D14's public domain-port bundle without a second kernel."""

        from host_brain.orchestration.goal_planning import (  # noqa: PLC0415
            PlanningDomainPorts,
        )

        catalog = CodeSuccessorCatalog(snapshot=self.snapshot)
        # ``MCTSMode.ALWAYS`` bypasses a condition port in the generic kernel.
        # Do not expose simulation ports until the frozen snapshot records that
        # the virtual model is ready; an external caller cannot override this
        # Code-domain gate.
        transition = catalog if self.snapshot.virtual_model_ready else None
        reward = (
            CodeRewardModel(snapshot=self.snapshot) if transition is not None else None
        )
        return PlanningDomainPorts(
            successors=catalog,
            ranker=CodeCognitiveRanker(),
            transition=transition,
            reward=reward,
            mcts_condition=CodeMCTSCondition(),
        )


def bind_selected_code_inputs(
    base_context: "PlanningCompilationContext",
    *,
    snapshot: CodeTaskSnapshot,
    decision: "GoalPlanningDecision",
    state: VirtualStateSnapshot | None = None,
) -> "PlanningCompilationContext":
    """Bind catalog-derived Agent args into one immutable compilation context.

    The selected search path is replayed against this snapshot's dynamic
    catalog before it becomes ``approved_candidate_inputs``.  A similarly
    named candidate therefore cannot smuggle arbitrary Agent arguments into a
    graph compilation request.
    """

    from host_brain.orchestration.goal_planning import (  # noqa: PLC0415
        GoalPlanningDecision,
    )
    from host_brain.orchestration.planning_graph_compiler import (  # noqa: PLC0415
        PlanningCompilationContext,
    )

    if not isinstance(base_context, PlanningCompilationContext):
        raise TypeError("base_context must be PlanningCompilationContext")
    if not isinstance(snapshot, CodeTaskSnapshot):
        raise TypeError("snapshot must be CodeTaskSnapshot")
    if not isinstance(decision, GoalPlanningDecision):
        raise TypeError("decision must be GoalPlanningDecision")
    _validate_shadow_compilation_context(base_context)
    resolved_state = state or snapshot.to_virtual_state()
    _code_state(resolved_state, expected=snapshot)
    frozen_goal_refs = frozenset(decision.request.goal.frozen_context_refs)
    required_goal_refs = frozenset(
        (
            snapshot.task_ref,
            snapshot.workspace_snapshot_ref,
            snapshot.verification_profile_ref,
        )
    )
    if (
        decision.request.goal.domain != CODE_DOMAIN
        or decision.request.state != resolved_state
        or not required_goal_refs.issubset(frozen_goal_refs)
    ):
        raise CodePlanningError(
            "decision goal/state does not match the Code compilation state"
        )
    result = decision.result
    if result.state_snapshot != resolved_state:
        raise CodePlanningError("search result did not retain the exact Code state snapshot")
    if (
        base_context.domain != CODE_DOMAIN
        or not base_context.approval_ref
        or not base_context.review_ref
    ):
        raise CodePlanningError("base compilation context is not a governed Code context")
    if (
        base_context.goal != decision.request.goal
        or base_context.plan_objective != decision.request.goal.objective
    ):
        raise CodePlanningError(
            "base compilation context goal differs from selected decision"
        )
    if (
        base_context.virtual_state is not None
        and base_context.virtual_state != resolved_state
    ):
        raise CodePlanningError("base compilation context state is stale")
    if str(_facts(resolved_state).get("phase") or "").strip().lower() != "root":
        raise CodePlanningError(
            "Code compilation only accepts a fresh root snapshot; intermediate "
            "replan lineage is not implemented"
        )

    catalog = CodeSuccessorCatalog(snapshot=snapshot)
    virtual_state = resolved_state
    prefix: tuple[SearchCandidate, ...] = ()
    approved: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(result.selected_path):
        allowed = {
            item.candidate_id: item
            for item in catalog.expand(
                state=virtual_state,
                path=prefix,
                depth=index,
                limit=128,
                context={},
            )
        }
        expected = allowed.get(candidate.candidate_id)
        if expected is None or expected.to_dict() != candidate.to_dict():
            raise CodePlanningError("selected path contains a non-catalog Code candidate")
        approved[candidate.candidate_id] = snapshot.candidate_input(expected)
        prefix = (*prefix, expected)
        virtual_state = catalog.transition(
            state=virtual_state,
            candidate=expected,
            path=prefix,
            context={},
        )
    if not approved:
        raise CodePlanningError("selected Code path must not be empty")

    input_pack = _thaw_json(base_context.input_pack)
    if "intent_draft" in input_pack:
        raise CodePlanningError(
            "base Code compilation context must not carry legacy intent_draft"
        )
    input_pack["approved_candidate_inputs"] = approved
    input_pack["code_planning"] = {
        "schema_version": "natsume.code-planning.shadow.v1",
        "mode": CODE_SHADOW_MODE,
        "snapshot_digest": snapshot.snapshot_digest,
        "virtual_state_digest": resolved_state.state_digest,
        "selected_candidate_ids": list(approved),
    }
    metadata = _thaw_json(base_context.metadata)
    metadata["code_planning"] = {
        "mode": CODE_SHADOW_MODE,
        "snapshot_digest": snapshot.snapshot_digest,
        "workspace_snapshot_ref": snapshot.workspace_snapshot_ref,
    }
    return PlanningCompilationContext(
        domain=base_context.domain,
        run_mode=base_context.run_mode,
        plan_objective=base_context.plan_objective,
        allowed_actions=base_context.allowed_actions,
        forbidden_actions=base_context.forbidden_actions,
        verification_plan=_thaw_json(base_context.verification_plan),
        approval_policy=_thaw_json(base_context.approval_policy),
        review_policy=_thaw_json(base_context.review_policy),
        budget_policy=_thaw_json(base_context.budget_policy),
        business_idempotency_scope=base_context.business_idempotency_scope,
        approval_ref=base_context.approval_ref,
        review_ref=base_context.review_ref,
        workspace=_thaw_json(base_context.workspace),
        input_pack=input_pack,
        input_refs=base_context.input_refs,
        metadata=metadata,
        goal=decision.request.goal,
        virtual_state=resolved_state,
    )


def _candidate(
    *,
    candidate_id: str,
    action: str,
    objective: str,
    input_refs: Sequence[str],
    prior_score: float,
    risk_level: int,
    test_coverage: float,
    estimated_cost: float,
    requires_lookahead: bool,
    virtual_model_ready: bool,
    patch: CodePatchSpec | None = None,
    depends_on_candidate_ids: Sequence[str] = (),
) -> SearchCandidate:
    payload: dict[str, Any] = {
        "worker_route": "skill",
        "target_id": "agent_worker",
        "objective": objective,
        "acceptance_criteria": [
            "return a structured shadow-only evidence summary",
            "preserve the frozen task and verification references",
        ],
        "input_refs": list(input_refs),
        "output_contract": {
            "schema_version": CODE_NODE_OUTPUT_SCHEMA,
            "kind": action,
            "mode": CODE_SHADOW_MODE,
            "required": ["receipt_ref", "evidence_summary"],
        },
        "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
        "risk_level": int(max(1, min(5, risk_level))),
        "capability_requirements": ["agent_worker"],
        "code_action": action,
        "estimated_test_coverage": _rounded(test_coverage),
        "estimated_risk": _rounded((int(max(1, min(5, risk_level))) - 1) / 4),
        "estimated_cost": _rounded(estimated_cost),
        "requires_lookahead": requires_lookahead is True,
        "virtual_model_ready": virtual_model_ready is True,
    }
    if patch is not None:
        payload["patch_id"] = patch.patch_id
        payload["patch_ref"] = patch.patch_ref
    if depends_on_candidate_ids:
        payload["depends_on_candidate_ids"] = list(depends_on_candidate_ids)
    return SearchCandidate(
        candidate_id=candidate_id,
        prior_score=float(prior_score),
        payload=payload,
        labels=("code", action, CODE_SHADOW_MODE.lower()),
        simulation_only=True,
    )


def _validate_shadow_compilation_context(
    context: "PlanningCompilationContext",
) -> None:
    """Fail closed when a generic root policy could escape Code shadow mode."""

    if context.run_mode != "inspect":
        raise CodePlanningError("Code shadow compilation requires run_mode=inspect")
    allowed_actions = frozenset(context.allowed_actions)
    if allowed_actions != _CODE_SHADOW_ALLOWED_ACTIONS:
        raise CodePlanningError(
            "Code shadow compilation allows only generate_report at the root"
        )
    forbidden_actions = frozenset(context.forbidden_actions)
    if not _CODE_SHADOW_REQUIRED_FORBIDDEN_ACTIONS.issubset(forbidden_actions):
        raise CodePlanningError(
            "Code shadow compilation must forbid write_files and network"
        )
    workspace = _thaw_json(context.workspace)
    if workspace != _CODE_SHADOW_WORKSPACE:
        raise CodePlanningError(
            "Code shadow compilation requires an empty temp_workspace policy"
        )


def _code_state(value: Any, *, expected: CodeTaskSnapshot) -> VirtualStateSnapshot:
    if not isinstance(value, VirtualStateSnapshot) or value.domain != CODE_DOMAIN:
        raise CodePlanningError("Code planning requires a Code VirtualStateSnapshot")
    facts = _facts(value)
    if (
        str(facts.get("workspace_digest") or "") != expected.workspace_digest
        or str(facts.get("task_ref") or "") != expected.task_ref
    ):
        raise CodePlanningError("virtual Code state is not bound to its frozen snapshot")
    constraints = _thaw_json(value.constraints)
    expected_catalog = [item.to_state_dict() for item in expected.patch_catalog]
    if (
        constraints.get("mode") != CODE_SHADOW_MODE
        or constraints.get("isolated_workspace") is not True
        or constraints.get("production_influence_enabled") is not False
        or constraints.get("virtual_model_ready") is not expected.virtual_model_ready
        or constraints.get("allowed_path_refs") != list(expected.allowed_path_refs)
        or constraints.get("forbidden_path_refs") != list(expected.forbidden_path_refs)
        or constraints.get("max_changed_files") != expected.max_changed_files
        or constraints.get("verification_profile_ref")
        != expected.verification_profile_ref
        or facts.get("failing_test_ids") != list(expected.failing_test_ids)
        or facts.get("target_artifact_digests")
        != dict(expected.target_artifact_digests)
        or facts.get("candidate_patch_catalog") != expected_catalog
    ):
        raise CodePlanningError("Code virtual state must remain isolated shadow-only")
    return value


def _facts(state: VirtualStateSnapshot) -> dict[str, Any]:
    facts = _thaw_json(state.facts)
    if not isinstance(facts, dict):
        raise CodePlanningError("Code virtual state facts are invalid")
    return facts


def _payload_unit(payload: Mapping[str, Any], field_name: str) -> float:
    try:
        value = float(payload.get(field_name, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


__all__ = (
    "CODE_DOMAIN",
    "CODE_NODE_OUTPUT_SCHEMA",
    "CODE_SHADOW_MODE",
    "CodeCognitiveRanker",
    "CodeMCTSCondition",
    "CodePatchSpec",
    "CodePlanningDomain",
    "CodePlanningError",
    "CodeRewardModel",
    "CodeSuccessorCatalog",
    "CodeTaskSnapshot",
    "bind_selected_code_inputs",
)
