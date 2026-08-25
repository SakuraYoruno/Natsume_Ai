"""Pure PlanningSearch selected-path compiler for governed task admission.

The compiler is deliberately outside ``planning_search``: search proposes and
selects data-only candidates, while this module freezes one selected path into
canonical execution contracts.  It never persists, schedules, calls a worker,
or reads alternate/ranked candidates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.budget_policy import (
    BUDGET_POLICY_SCHEMA_VERSION,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    TASK_GRAPH_INVALID,
    ContractViolation,
)
from host_brain.contracts.agent_runtime.node_artifact import (
    build_task_result_artifact_ref,
    is_task_result_artifact_ref,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
    normalize_task_retry_policy,
    validate_task_graph_admission_state,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.agent_runtime.review_policy import (
    NODE_REVIEW_POLICIES_KEY,
    NODE_REVIEW_POLICY_SCHEMA_VERSION,
)
from host_brain.contracts.planning_search.contracts import (
    PlanningGoal,
    SearchCandidate,
    SearchResult,
    SearchStatus,
    VirtualStateSnapshot,
    _freeze_json,
    _thaw_json,
)


from host_brain.orchestration.planning_graph_codec import (
    SHA256_RE as _SHA256_RE,
    _assert_secret_free,
    _canonical_json,
    _compilation_digest,
    _dedupe_text,
    _exact_non_negative_int,
    _initial_lineage,
    _json_copy,
    _node_id,
    _normalize_evidence_refs,
    _planner_ref,
    _required_text,
    _required_text_list,
    _sha256_ref,
    _validate_virtual_state_provenance,
    _virtual_state_provenance_dict,
)

_ROUTE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

_NODE_REQUIRED_FIELDS = frozenset(
    {
        "worker_route",
        "target_id",
        "objective",
        "acceptance_criteria",
        "input_refs",
        "output_contract",
        "retry_policy",
        "risk_level",
        "capability_requirements",
    }
)
_ROOT_REQUIRED_FIELDS = frozenset(
    {
        "domain",
        "run_mode",
        "allowed_actions",
        "forbidden_actions",
        "verification_plan",
        "approval_policy",
        "review_policy",
        "budget_policy",
        "business_idempotency_scope",
        "approval_ref",
        "review_ref",
    }
)
_COMPILER_OWNED_FIELDS = frozenset(
    {
        "node_id",
        "dependencies",
        "status",
        "status_version",
        "graph_id",
        "graph_revision",
        "root_work_order_id",
        "task_graph_id",
        "task_node_id",
        "planner_ref",
        "operation_fingerprint",
        "failure_evidence_refs",
    }
)
_BUDGET_LIMIT_FIELDS = frozenset(
    {
        "token_count",
        "tool_call_count",
        "time_ms",
        "concurrency",
        "retry_count",
        "model_cost_micros",
    }
)
_DEPENDENCY_CANDIDATE_FIELD = "depends_on_candidate_ids"
_CANDIDATE_REVIEW_POLICIES_KEY = "candidate_review_policies"
_CONTEXT_OWNED_FIELDS = _ROOT_REQUIRED_FIELDS | frozenset(
    {"workspace", "input_pack", "metadata", "plan_objective"}
)
@dataclass(frozen=True, slots=True)
class CompiledPlanningGraph:
    """One deep-immutable compilation product; persistence remains caller-owned.

    The repository's canonical contracts contain mutable dictionaries for
    compatibility.  This product therefore stores canonical JSON snapshots and
    returns a fresh typed projection on access; callers cannot mutate the
    frozen revision by modifying a returned ``WorkOrderV3`` or ``TaskGraph``.
    """

    _work_order_json: str = field(repr=False)
    _task_graph_json: str = field(repr=False)
    trace_digest: str
    selected_candidate_ids: tuple[str, ...]
    compilation_digest: str
    virtual_state_schema: str = ""
    virtual_state_digest: str = ""
    previous_revision: int | None = None
    failure_evidence_refs: tuple[str, ...] = ()

    @classmethod
    def freeze(
        cls,
        *,
        work_order: WorkOrderV3,
        task_graph: TaskGraph,
        trace_digest: str,
        selected_candidate_ids: tuple[str, ...],
        virtual_state_schema: str,
        virtual_state_digest: str,
        previous_revision: int | None,
        failure_evidence_refs: tuple[str, ...],
    ) -> "CompiledPlanningGraph":
        compilation_digest = _compilation_digest(
            work_order=work_order,
            task_graph=task_graph,
            trace_digest=trace_digest,
            selected_candidate_ids=selected_candidate_ids,
            virtual_state_schema=virtual_state_schema,
            virtual_state_digest=virtual_state_digest,
            previous_revision=previous_revision,
            failure_evidence_refs=failure_evidence_refs,
        )
        product = cls(
            _work_order_json=_canonical_json(work_order.to_dict()),
            _task_graph_json=_canonical_json(task_graph.to_dict()),
            trace_digest=trace_digest,
            selected_candidate_ids=selected_candidate_ids,
            compilation_digest=compilation_digest,
            virtual_state_schema=virtual_state_schema,
            virtual_state_digest=virtual_state_digest,
            previous_revision=previous_revision,
            failure_evidence_refs=failure_evidence_refs,
        )
        product.validate()
        return product

    @property
    def work_order(self) -> WorkOrderV3:
        return WorkOrderV3.from_mapping(json.loads(self._work_order_json))

    @property
    def task_graph(self) -> TaskGraph:
        return TaskGraph.from_mapping(json.loads(self._task_graph_json))

    @property
    def root_work_order(self) -> WorkOrderV3:
        return self.work_order

    def validate(self) -> None:
        work_order = self.work_order
        task_graph = self.task_graph
        work_order.validate()
        validate_task_graph_admission_state(task_graph)
        if not _SHA256_RE.fullmatch(self.trace_digest):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "planning trace_digest must be a lowercase sha256 digest",
            )
        _validate_virtual_state_provenance(
            schema=self.virtual_state_schema,
            digest=self.virtual_state_digest,
        )
        if not self.selected_candidate_ids:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "compiled planning graph requires selected candidate lineage",
            )
        expected_compilation_digest = _compilation_digest(
            work_order=work_order,
            task_graph=task_graph,
            trace_digest=self.trace_digest,
            selected_candidate_ids=self.selected_candidate_ids,
            virtual_state_schema=self.virtual_state_schema,
            virtual_state_digest=self.virtual_state_digest,
            previous_revision=self.previous_revision,
            failure_evidence_refs=self.failure_evidence_refs,
        )
        if self.compilation_digest != expected_compilation_digest:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "compiled planning graph was mutated after its revision was frozen",
            )
        if work_order.budget_policy is None:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "compiled WorkOrderV3 requires an explicit finite BudgetPolicy",
            )
        graph = task_graph
        order = work_order
        if (
            order.work_order_id != graph.root_work_order_id
            or order.root_work_order_id != graph.root_work_order_id
            or order.task_graph_id != graph.graph_id
            or order.graph_revision != graph.revision
            or order.task_node_id != graph.nodes[0].node_id
        ):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "compiled WorkOrderV3 graph/root/node lineage is inconsistent",
            )
        if not order.approval_ref or not order.review_ref:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "compiled WorkOrderV3 requires approval and review lineage refs",
            )
        if (
            not order.business_idempotency_scope
            or not order.operation_fingerprint.startswith("sha256:")
            or not _SHA256_RE.fullmatch(order.operation_fingerprint.removeprefix("sha256:"))
        ):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "compiled WorkOrderV3 requires deterministic idempotency bindings",
            )
        if self.trace_digest not in graph.planner_ref:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "TaskGraph.planner_ref is not bound to the planning trace_digest",
            )
        expected_state = _virtual_state_provenance_dict(
            schema=self.virtual_state_schema,
            digest=self.virtual_state_digest,
        )
        planning_input = dict(order.input_pack).get("planning_search")
        planning_lineage = dict(order.metadata).get("planning_lineage")
        if (
            not isinstance(planning_input, Mapping)
            or not isinstance(planning_lineage, Mapping)
            or planning_input.get("virtual_state") != expected_state
            or planning_lineage.get("virtual_state") != expected_state
        ):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "compiled planning graph virtual state provenance drifted",
            )
        state_marker = f"/state/sha256:{self.virtual_state_digest}"
        if self.virtual_state_digest and state_marker not in graph.planner_ref:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "TaskGraph.planner_ref is not bound to the virtual state digest",
            )
        if not self.virtual_state_digest and "/state/sha256:" in graph.planner_ref:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "TaskGraph.planner_ref carries unexpected virtual state provenance",
            )
        if self.previous_revision is None:
            if graph.revision != 1 or self.failure_evidence_refs:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "initial planning compilation must be revision 1 without failure evidence",
                )
        elif (
            graph.revision != self.previous_revision + 1
            or not self.failure_evidence_refs
        ):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "replan compilation requires previous revision + 1 and failure evidence",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "work_order": json.loads(self._work_order_json),
            "task_graph": json.loads(self._task_graph_json),
            "trace_digest": self.trace_digest,
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "compilation_digest": self.compilation_digest,
            "virtual_state_schema": self.virtual_state_schema or None,
            "virtual_state_digest": self.virtual_state_digest or None,
            "previous_revision": self.previous_revision,
            "failure_evidence_refs": list(self.failure_evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class PlanningCompilationContext:
    """Governor-bound graph policy kept outside dynamic search candidates.

    Goal-driven successor catalogs may propose node descriptors but must never
    carry graph-wide budget, approval, review, workspace, or idempotency
    authority.  The compiler copies this immutable context into the canonical
    WorkOrder only after one selected path has been chosen.
    """

    domain: str
    run_mode: str
    plan_objective: str
    allowed_actions: Sequence[str]
    forbidden_actions: Sequence[str]
    verification_plan: Mapping[str, Any]
    approval_policy: Mapping[str, Any]
    review_policy: Mapping[str, Any]
    budget_policy: Mapping[str, Any]
    business_idempotency_scope: str
    approval_ref: str
    review_ref: str
    workspace: Mapping[str, Any] = field(default_factory=dict)
    input_pack: Mapping[str, Any] = field(default_factory=dict)
    input_refs: Sequence[str] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    goal: PlanningGoal | None = None
    virtual_state: VirtualStateSnapshot | None = None

    def __post_init__(self) -> None:
        domain = _required_text(self.domain, field_name="context.domain").lower()
        run_mode = _required_text(self.run_mode, field_name="context.run_mode").lower()
        objective = _required_text(
            self.plan_objective,
            field_name="context.plan_objective",
            max_length=4_000,
        )
        allowed = _context_text_list(
            self.allowed_actions, field_name="context.allowed_actions", allow_empty=True
        )
        forbidden = _context_text_list(
            self.forbidden_actions, field_name="context.forbidden_actions", allow_empty=True
        )
        input_refs = _context_text_list(
            self.input_refs, field_name="context.input_refs", allow_empty=True
        )
        for name, value in (
            ("verification_plan", self.verification_plan),
            ("approval_policy", self.approval_policy),
            ("review_policy", self.review_policy),
        ):
            if not isinstance(value, Mapping) or not value:
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"context.{name} must be a non-empty object",
                )
        for name, value in (
            ("workspace", self.workspace),
            ("input_pack", self.input_pack),
            ("metadata", self.metadata),
        ):
            if not isinstance(value, Mapping):
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"context.{name} must be an object",
                )
        _parse_explicit_budget_policy(self.budget_policy)
        scope = _required_text(
            self.business_idempotency_scope,
            field_name="context.business_idempotency_scope",
            max_length=240,
        )
        approval_ref = _required_text(
            self.approval_ref, field_name="context.approval_ref", max_length=1_000
        )
        review_ref = _required_text(
            self.review_ref, field_name="context.review_ref", max_length=1_000
        )
        if self.goal is not None and not isinstance(self.goal, PlanningGoal):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "context.goal is invalid")
        if self.virtual_state is not None and not isinstance(
            self.virtual_state, VirtualStateSnapshot
        ):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID, "context.virtual_state is invalid"
            )
        if self.goal is not None and self.goal.domain != domain:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "context.goal domain differs from context.domain",
            )
        if self.virtual_state is not None and self.virtual_state.domain != domain:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "context.virtual_state domain differs from context.domain",
            )
        for name, value in (
            ("context.workspace", self.workspace),
            ("context.input_pack", self.input_pack),
            ("context.metadata", self.metadata),
        ):
            _assert_secret_free(value, path=name)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "run_mode", run_mode)
        object.__setattr__(self, "plan_objective", objective)
        object.__setattr__(self, "allowed_actions", allowed)
        object.__setattr__(self, "forbidden_actions", forbidden)
        object.__setattr__(self, "verification_plan", _freeze_json(_json_copy(dict(self.verification_plan))))
        object.__setattr__(self, "approval_policy", _freeze_json(_json_copy(dict(self.approval_policy))))
        object.__setattr__(self, "review_policy", _freeze_json(_json_copy(dict(self.review_policy))))
        object.__setattr__(self, "budget_policy", _freeze_json(_json_copy(dict(self.budget_policy))))
        object.__setattr__(self, "business_idempotency_scope", scope)
        object.__setattr__(self, "approval_ref", approval_ref)
        object.__setattr__(self, "review_ref", review_ref)
        object.__setattr__(self, "workspace", _freeze_json(_json_copy(dict(self.workspace))))
        object.__setattr__(self, "input_pack", _freeze_json(_json_copy(dict(self.input_pack))))
        object.__setattr__(self, "input_refs", input_refs)
        object.__setattr__(self, "metadata", _freeze_json(_json_copy(dict(self.metadata))))

    def root_policy_payload(self) -> dict[str, Any]:
        """Return a detached canonical payload for compiler-internal merging."""

        input_pack = _thaw_json(self.input_pack)
        metadata = _thaw_json(self.metadata)
        metadata["planning_context"] = {
            "domain": self.domain,
            "goal": self.goal.to_dict() if self.goal is not None else None,
            "virtual_state": (
                self.virtual_state.to_dict() if self.virtual_state is not None else None
            ),
        }
        return {
            "domain": self.domain,
            "run_mode": self.run_mode,
            "plan_objective": self.plan_objective,
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "verification_plan": _thaw_json(self.verification_plan),
            "approval_policy": _thaw_json(self.approval_policy),
            "review_policy": _thaw_json(self.review_policy),
            "budget_policy": _thaw_json(self.budget_policy),
            "business_idempotency_scope": self.business_idempotency_scope,
            "approval_ref": self.approval_ref,
            "review_ref": self.review_ref,
            "workspace": _thaw_json(self.workspace),
            "input_pack": input_pack,
            "metadata": metadata,
        }


class PlanningGraphCompiler:
    """Compile only a successful ``SearchResult.selected_path``.

    Search alternatives, scores, counters, selected-candidate aliases, and any
    executor capability are intentionally outside this boundary.  Replanning
    is expressed by passing a terminal prior graph; the prior object is only
    validated/read and a new ``revision == previous + 1`` is constructed.
    """

    def compile(
        self,
        result: SearchResult,
        *,
        created_at: int = 0,
        previous_graph: TaskGraph | None = None,
        failure_evidence_refs: Sequence[str] = (),
        completion_policy: TaskGraphCompletionPolicy | str | None = None,
        compilation_context: PlanningCompilationContext | None = None,
    ) -> CompiledPlanningGraph:
        if not isinstance(result, SearchResult):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "planning compiler requires a SearchResult",
            )
        if result.status is not SearchStatus.SELECTED:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "only a SELECTED PlanningSearch result can be compiled",
            )
        selected_path = tuple(result.selected_path)
        if not selected_path:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "selected PlanningSearch result has an empty selected_path",
            )
        request_id = _required_text(result.request_id, field_name="request_id")
        trace_digest = str(result.trace_digest).strip().lower()
        if not _SHA256_RE.fullmatch(trace_digest):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "PlanningSearch trace_digest is invalid",
            )
        virtual_state_schema = str(result.virtual_state_schema or "").strip()
        virtual_state_digest = str(result.virtual_state_digest or "").strip().lower()
        _validate_virtual_state_provenance(
            schema=virtual_state_schema,
            digest=virtual_state_digest,
        )
        resolved_created_at = _exact_non_negative_int(created_at, field_name="created_at")
        evidence_refs = _normalize_evidence_refs(failure_evidence_refs)
        try:
            resolved_completion_policy = (
                TaskGraphCompletionPolicy(
                    str(getattr(completion_policy, "value", completion_policy)).strip()
                )
                if completion_policy is not None
                else (
                    previous_graph.completion_policy
                    if previous_graph is not None
                    else TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
                )
            )
        except ValueError as exc:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "planning compiler completion_policy is unsupported",
            ) from exc

        if compilation_context is not None and not isinstance(
            compilation_context, PlanningCompilationContext
        ):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "compilation_context must be PlanningCompilationContext",
            )
        if (
            compilation_context is not None
            and compilation_context.virtual_state is not None
            and (
                virtual_state_schema != compilation_context.virtual_state.schema_version
                or virtual_state_digest != compilation_context.virtual_state.state_digest
            )
        ):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "search result virtual state differs from compilation context",
            )
        payloads: list[dict[str, Any]] = []
        for index, candidate in enumerate(selected_path):
            if not isinstance(candidate, SearchCandidate):
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"selected_path[{index}] is not a SearchCandidate",
                )
            if candidate.simulation_only is not True:
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"selected_path[{index}] is not simulation-only",
                )
            payload = _json_copy(dict(candidate.payload))
            _assert_secret_free(payload, path=f"selected_path[{index}].payload")
            if compilation_context is not None:
                payload = _apply_compilation_context(
                    payload,
                    context=compilation_context,
                    index=index,
                )
            _validate_node_payload(payload, index=index)
            payloads.append(payload)
        _validate_root_payload(payloads[0])
        _validate_root_policy_consistency(payloads)
        selected_candidate_ids = tuple(candidate.candidate_id for candidate in selected_path)
        if len(set(selected_candidate_ids)) != len(selected_candidate_ids):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "selected PlanningSearch path contains duplicate candidate ids",
            )
        dependency_candidate_ids = _resolve_candidate_dependencies(
            payloads=payloads,
            selected_candidate_ids=selected_candidate_ids,
        )
        selected_plan_digest = _sha256_ref(
            {
                "selected_path": [
                    {"candidate_id": candidate_id, "payload": payload}
                    for candidate_id, payload in zip(
                        selected_candidate_ids,
                        payloads,
                        strict=True,
                    )
                ],
                "trace_digest": trace_digest,
                "virtual_state": _virtual_state_provenance_dict(
                    schema=virtual_state_schema,
                    digest=virtual_state_digest,
                ),
            }
        )

        if previous_graph is None:
            if evidence_refs:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "initial compilation cannot carry failure evidence",
                )
            revision = 1
            graph_id, root_work_order_id = _initial_lineage(
                request_id=request_id,
                selected_plan_digest=selected_plan_digest,
            )
            previous_revision = None
            previous_status = None
            previous_planner_ref = None
        else:
            if not isinstance(previous_graph, TaskGraph):
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "previous_graph must be a canonical TaskGraph",
                )
            previous_graph.validate()
            if previous_graph.status not in {
                TaskGraphStatus.FAILED,
                TaskGraphStatus.BLOCKED,
            }:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "failure replan requires a FAILED or BLOCKED previous graph",
                )
            if not evidence_refs:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "failure replan requires durable failure evidence refs",
                )
            graph_id = previous_graph.graph_id
            root_work_order_id = previous_graph.root_work_order_id
            revision = previous_graph.revision + 1
            previous_revision = previous_graph.revision
            previous_status = previous_graph.status.value
            previous_planner_ref = previous_graph.planner_ref

        planner_ref = _planner_ref(
            request_id=request_id,
            graph_id=graph_id,
            revision=revision,
            trace_digest=trace_digest,
            virtual_state_digest=virtual_state_digest,
        )
        node_ids = tuple(
            _node_id(
                graph_id=graph_id,
                position=index,
                candidate_id=candidate.candidate_id,
            )
            for index, candidate in enumerate(selected_path)
        )
        node_id_by_candidate = dict(zip(selected_candidate_ids, node_ids, strict=True))
        nodes = tuple(
            _compile_node(
                payload=payload,
                node_id=node_ids[index],
                dependency_node_ids=tuple(
                    node_id_by_candidate[candidate_id]
                    for candidate_id in dependency_candidate_ids[index]
                ),
                graph_id=graph_id,
                revision=revision,
                planner_ref=planner_ref,
                failure_evidence_refs=evidence_refs if index == 0 else (),
            )
            for index, payload in enumerate(payloads)
        )
        depended_on = {
            dependency for node in nodes for dependency in node.dependencies
        }
        sink_node_ids = tuple(
            node.node_id for node in nodes if node.node_id not in depended_on
        )
        result_node_id = (
            node_ids[-1]
            if len(sink_node_ids) == 1
            or resolved_completion_policy
            is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
            else None
        )
        graph = TaskGraph(
            graph_id=graph_id,
            root_work_order_id=root_work_order_id,
            revision=revision,
            planner_ref=planner_ref,
            status=TaskGraphStatus.CREATED,
            created_at=resolved_created_at,
            result_node_id=result_node_id,
            completion_policy=resolved_completion_policy,
            nodes=nodes,
        )
        validate_task_graph_admission_state(graph)

        operation_fingerprint = _sha256_ref(
            {
                "schema": "Natsume.planning_search.compilation.v1",
                "graph_id": graph_id,
                "root_work_order_id": root_work_order_id,
                "revision": revision,
                "trace_digest": trace_digest,
                "virtual_state": _virtual_state_provenance_dict(
                    schema=virtual_state_schema,
                    digest=virtual_state_digest,
                ),
                "selected_path": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "payload": payload,
                    }
                    for candidate, payload in zip(selected_path, payloads, strict=True)
                ],
                "failure_evidence_refs": list(evidence_refs),
                "previous_planner_ref": previous_planner_ref,
            }
        )
        work_order = _compile_work_order(
            payloads=payloads,
            selected_candidate_ids=selected_candidate_ids,
            graph=graph,
            node_ids=node_ids,
            planner_ref=planner_ref,
            trace_digest=trace_digest,
            virtual_state_schema=virtual_state_schema,
            virtual_state_digest=virtual_state_digest,
            operation_fingerprint=operation_fingerprint,
            failure_evidence_refs=evidence_refs,
            previous_revision=previous_revision,
            previous_status=previous_status,
            previous_planner_ref=previous_planner_ref,
        )
        compiled = CompiledPlanningGraph.freeze(
            work_order=work_order,
            task_graph=graph,
            trace_digest=trace_digest,
            selected_candidate_ids=selected_candidate_ids,
            virtual_state_schema=virtual_state_schema,
            virtual_state_digest=virtual_state_digest,
            previous_revision=previous_revision,
            failure_evidence_refs=evidence_refs,
        )
        return compiled

    def replan(
        self,
        result: SearchResult,
        *,
        previous_graph: TaskGraph,
        failure_evidence_refs: Sequence[str],
        created_at: int = 0,
        compilation_context: PlanningCompilationContext | None = None,
    ) -> CompiledPlanningGraph:
        """Explicit failure-only spelling of ``compile(..., previous_graph=...)``."""

        return self.compile(
            result,
            created_at=created_at,
            previous_graph=previous_graph,
            failure_evidence_refs=failure_evidence_refs,
            compilation_context=compilation_context,
        )


def compile_planning_search_result(
    result: SearchResult,
    *,
    created_at: int = 0,
    previous_graph: TaskGraph | None = None,
    failure_evidence_refs: Sequence[str] = (),
    compilation_context: PlanningCompilationContext | None = None,
) -> CompiledPlanningGraph:
    """Stateless convenience entry point for the canonical compiler."""

    return PlanningGraphCompiler().compile(
        result,
        created_at=created_at,
        previous_graph=previous_graph,
        failure_evidence_refs=failure_evidence_refs,
        compilation_context=compilation_context,
    )


def _apply_compilation_context(
    payload: Mapping[str, Any],
    *,
    context: PlanningCompilationContext,
    index: int,
) -> dict[str, Any]:
    forbidden = sorted(_CONTEXT_OWNED_FIELDS & set(payload))
    if forbidden:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] attempts to set context-owned fields: "
            + ",".join(forbidden),
        )
    merged = context.root_policy_payload()
    merged.update(_json_copy(dict(payload)))
    candidate_refs = _required_text_list(
        merged.get("input_refs"),
        field_name=f"selected_path[{index}].input_refs",
        allow_empty=True,
    )
    merged["input_refs"] = list(_dedupe_text(context.input_refs, candidate_refs))
    return merged


def _context_text_list(
    value: Sequence[str], *, field_name: str, allow_empty: bool
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an explicit list")
    return _required_text_list(list(value), field_name=field_name, allow_empty=allow_empty)


def _validate_node_payload(payload: Mapping[str, Any], *, index: int) -> None:
    missing = sorted(_NODE_REQUIRED_FIELDS - set(payload))
    if missing:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] payload is missing production fields: {','.join(missing)}",
        )
    owned = sorted(_COMPILER_OWNED_FIELDS & set(payload))
    if owned:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] payload sets compiler-owned fields: {','.join(owned)}",
        )
    route = _required_text(payload.get("worker_route"), field_name="worker_route").lower()
    if not _ROUTE_RE.fullmatch(route):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] worker_route is invalid",
        )
    _required_text(payload.get("target_id"), field_name="target_id", max_length=240)
    _required_text(payload.get("objective"), field_name="objective", max_length=4_000)
    _required_text_list(
        payload.get("acceptance_criteria"),
        field_name="acceptance_criteria",
        allow_empty=False,
    )
    input_refs = _required_text_list(
        payload.get("input_refs"),
        field_name="input_refs",
        allow_empty=True,
    )
    if any(is_task_result_artifact_ref(ref) for ref in input_refs):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] cannot set compiler-owned task-result refs",
        )
    _required_text_list(
        payload.get("capability_requirements"),
        field_name="capability_requirements",
        allow_empty=True,
    )
    output_contract = payload.get("output_contract")
    if not isinstance(output_contract, Mapping) or not output_contract:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] output_contract must be a non-empty object",
        )
    normalize_task_retry_policy(
        payload.get("retry_policy"),
        field_name=f"selected_path[{index}].retry_policy",
    )
    risk_level = payload.get("risk_level")
    if isinstance(risk_level, bool) or not isinstance(risk_level, int) or not 1 <= risk_level <= 5:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"selected_path[{index}] risk_level must be an integer between 1 and 5",
        )
    if _DEPENDENCY_CANDIDATE_FIELD in payload:
        _required_text_list(
            payload.get(_DEPENDENCY_CANDIDATE_FIELD),
            field_name=_DEPENDENCY_CANDIDATE_FIELD,
            allow_empty=True,
        )


def _resolve_candidate_dependencies(
    *,
    payloads: Sequence[Mapping[str, Any]],
    selected_candidate_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """Resolve an explicit approved DAG or preserve the linear compatibility path.

    Explicit dependency ids must point backward in ``selected_path``.  That
    makes the selected path itself a deterministic topological ordering and
    rejects unknown, self, forward, and cyclic references before TaskGraph
    construction.
    """

    # 显式依赖只能指向已出现的候选项，使 selected_path 自身成为稳定拓扑序，编译前即阻断环和前向引用。
    positions = {candidate_id: index for index, candidate_id in enumerate(selected_candidate_ids)}
    resolved: list[tuple[str, ...]] = []
    for index, payload in enumerate(payloads):
        if _DEPENDENCY_CANDIDATE_FIELD not in payload:
            resolved.append((selected_candidate_ids[index - 1],) if index else ())
            continue
        raw_value = payload.get(_DEPENDENCY_CANDIDATE_FIELD)
        raw_text_values = (
            tuple(str(value or "").strip() for value in raw_value)
            if isinstance(raw_value, Sequence)
            and not isinstance(raw_value, (str, bytes, bytearray))
            else ()
        )
        if raw_text_values and len(set(raw_text_values)) != len(raw_text_values):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"selected_path[{index}] contains duplicate dependency candidate ids",
            )
        raw_dependencies = _required_text_list(
            payload.get(_DEPENDENCY_CANDIDATE_FIELD),
            field_name=_DEPENDENCY_CANDIDATE_FIELD,
            allow_empty=True,
        )
        dependencies = tuple(raw_dependencies)
        invalid = tuple(
            candidate_id
            for candidate_id in dependencies
            if candidate_id not in positions or positions[candidate_id] >= index
        )
        if invalid:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"selected_path[{index}] has unknown/self/forward dependency candidates: "
                + ",".join(invalid),
            )
        resolved.append(dependencies)
    return tuple(resolved)


def _validate_root_payload(payload: Mapping[str, Any]) -> None:
    missing = sorted(_ROOT_REQUIRED_FIELDS - set(payload))
    if missing:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "selected_path[0] payload is missing WorkOrderV3 fields: " + ",".join(missing),
        )
    _required_text(payload.get("domain"), field_name="domain")
    _required_text(payload.get("run_mode"), field_name="run_mode")
    _required_text_list(payload.get("allowed_actions"), field_name="allowed_actions", allow_empty=True)
    _required_text_list(
        payload.get("forbidden_actions"),
        field_name="forbidden_actions",
        allow_empty=True,
    )
    for field_name in (
        "verification_plan",
        "approval_policy",
        "review_policy",
    ):
        value = payload.get(field_name)
        if not isinstance(value, Mapping) or not value:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"selected_path[0] {field_name} must be a non-empty object",
            )
    _required_text(
        payload.get("business_idempotency_scope"),
        field_name="business_idempotency_scope",
        max_length=240,
    )
    _required_text(payload.get("approval_ref"), field_name="approval_ref", max_length=1_000)
    _required_text(payload.get("review_ref"), field_name="review_ref", max_length=1_000)
    _parse_explicit_budget_policy(payload.get("budget_policy"))
    for field_name in ("workspace", "input_pack", "metadata"):
        value = payload.get(field_name, {})
        if not isinstance(value, Mapping):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"selected_path[0] {field_name} must be an object when provided",
            )


def _validate_root_policy_consistency(payloads: Sequence[Mapping[str, Any]]) -> None:
    """Reject a later candidate that tries to redefine graph-wide authority."""

    root = payloads[0]
    for index, payload in enumerate(payloads[1:], start=1):
        drift = sorted(
            field_name
            for field_name in _ROOT_REQUIRED_FIELDS
            if field_name in payload
            and _canonical_json(payload[field_name]) != _canonical_json(root[field_name])
        )
        if drift:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"selected_path[{index}] redefines graph-wide WorkOrder policy: "
                + ",".join(drift),
            )


def _parse_explicit_budget_policy(value: Any) -> BudgetPolicy:
    if not isinstance(value, Mapping):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "selected_path[0] budget_policy must be an explicit object",
        )
    policy_payload = dict(value)
    for field_name in ("schema_version", "scope", "limits", "on_exceeded"):
        if field_name not in policy_payload:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"budget_policy.{field_name} must be explicit",
            )
    if policy_payload.get("schema_version") != BUDGET_POLICY_SCHEMA_VERSION:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "budget_policy.schema_version is invalid",
        )
    limits = policy_payload.get("limits")
    if not isinstance(limits, Mapping):
        raise ContractViolation(CONTRACT_FIELD_INVALID, "budget_policy.limits must be an object")
    missing = sorted(_BUDGET_LIMIT_FIELDS - set(limits))
    if missing:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "budget_policy.limits must explicitly bound: " + ",".join(missing),
        )
    for field_name in _BUDGET_LIMIT_FIELDS:
        amount = limits.get(field_name)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"budget_policy.limits.{field_name} must be a finite non-negative integer",
            )
    if int(limits["time_ms"]) < 1 or int(limits["concurrency"]) < 1:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "budget_policy must bound positive time_ms and concurrency",
        )
    return BudgetPolicy.from_mapping(policy_payload)


def _compile_node(
    *,
    payload: Mapping[str, Any],
    node_id: str,
    dependency_node_ids: tuple[str, ...],
    graph_id: str,
    revision: int,
    planner_ref: str,
    failure_evidence_refs: tuple[str, ...],
) -> TaskNode:
    # 下游输入引用由编译器从依赖节点结果和失败证据拼装，候选 payload 无法伪造这些运行时拥有字段。
    dependencies = tuple(dependency_node_ids)
    dependency_refs = tuple(
        build_task_result_artifact_ref(graph_id, revision, dependency_node_id)
        for dependency_node_id in dependencies
    )
    input_refs = _dedupe_text(
        _required_text_list(payload.get("input_refs"), field_name="input_refs", allow_empty=True),
        dependency_refs,
        failure_evidence_refs,
        (planner_ref,) if not dependencies else (),
    )
    route = str(payload["worker_route"]).strip().lower()
    target_id = str(payload["target_id"]).strip()
    node = TaskNode(
        node_id=node_id,
        dependencies=dependencies,
        route=f"{route}:{quote(target_id, safe='')}",
        input_refs=input_refs,
        output_contract=deepcopy(dict(payload["output_contract"])),
        retry_policy=normalize_task_retry_policy(
            payload["retry_policy"],
            field_name="selected_path.retry_policy",
        ),
        risk_level=int(payload["risk_level"]),
        acceptance_criteria=_required_text_list(
            payload.get("acceptance_criteria"),
            field_name="acceptance_criteria",
            allow_empty=False,
        ),
        status=(TaskNodeStatus.BLOCKED if dependencies else TaskNodeStatus.READY),
        status_version=0,
    )
    node.validate()
    return node


def _compile_work_order(
    *,
    payloads: Sequence[Mapping[str, Any]],
    selected_candidate_ids: tuple[str, ...],
    graph: TaskGraph,
    node_ids: tuple[str, ...],
    planner_ref: str,
    trace_digest: str,
    virtual_state_schema: str,
    virtual_state_digest: str,
    operation_fingerprint: str,
    failure_evidence_refs: tuple[str, ...],
    previous_revision: int | None,
    previous_status: str | None,
    previous_planner_ref: str | None,
) -> WorkOrderV3:
    # 图级策略从根候选一次性冻结，节点绑定和规划来源写入 metadata 供后续授权与恢复逐项闭合。
    root = payloads[0]
    budget_policy = _parse_explicit_budget_policy(root.get("budget_policy"))
    all_acceptance = _dedupe_text(
        *(
            _required_text_list(
                payload.get("acceptance_criteria"),
                field_name="acceptance_criteria",
                allow_empty=False,
            )
            for payload in payloads
        )
    )
    all_capabilities = _dedupe_text(
        *(
            _required_text_list(
                payload.get("capability_requirements"),
                field_name="capability_requirements",
                allow_empty=True,
            )
            for payload in payloads
        )
    )
    all_input_refs = _dedupe_text(
        *(
            _required_text_list(
                payload.get("input_refs"),
                field_name="input_refs",
                allow_empty=True,
            )
            for payload in payloads
        ),
        failure_evidence_refs,
        (planner_ref,),
    )
    node_bindings = [
        {
            "candidate_id": candidate_id,
            "node_id": node_id,
            "objective": str(payload["objective"]).strip(),
            "worker_route": str(payload["worker_route"]).strip().lower(),
            "target_id": str(payload["target_id"]).strip(),
            "depends_on_candidate_ids": [
                selected_candidate_ids[node_ids.index(dependency_node_id)]
                for dependency_node_id in graph.nodes[index].dependencies
            ],
            "depends_on_node_ids": list(graph.nodes[index].dependencies),
        }
        for index, (candidate_id, node_id, payload) in enumerate(
            zip(selected_candidate_ids, node_ids, payloads, strict=True)
        )
    ]
    input_pack = deepcopy(dict(root.get("input_pack") or {}))
    input_pack["planning_search"] = {
        "trace_digest": trace_digest,
        "selected_candidate_ids": list(selected_candidate_ids),
        "virtual_state": _virtual_state_provenance_dict(
            schema=virtual_state_schema,
            digest=virtual_state_digest,
        ),
    }
    metadata = deepcopy(dict(root.get("metadata") or {}))
    metadata["planning_lineage"] = {
        "graph_id": graph.graph_id,
        "root_work_order_id": graph.root_work_order_id,
        "revision": graph.revision,
        "parent_revision": previous_revision,
        "parent_status": previous_status,
        "parent_planner_ref": previous_planner_ref,
        "planner_ref": planner_ref,
        "result_node_id": graph.result_node_id,
        "completion_policy": (
            graph.completion_policy.value
            if graph.completion_policy is not None
            else None
        ),
        "trace_digest": trace_digest,
        "virtual_state": _virtual_state_provenance_dict(
            schema=virtual_state_schema,
            digest=virtual_state_digest,
        ),
        "failure_evidence_refs": list(failure_evidence_refs),
        "approval_ref": str(root["approval_ref"]).strip(),
        "review_ref": str(root["review_ref"]).strip(),
        "node_bindings": node_bindings,
    }
    metadata["planning_lineage"]["failure_evidence_binding"] = (
        _sha256_ref(
            {
                "graph_id": graph.graph_id,
                "root_work_order_id": graph.root_work_order_id,
                "parent_revision": previous_revision,
                "parent_planner_ref": previous_planner_ref,
                "failure_evidence_refs": list(failure_evidence_refs),
            }
        )
        if previous_revision is not None
        else None
    )
    objective = str(root.get("plan_objective") or root["objective"]).strip()
    review_policy = _compile_node_review_policies(
        root.get("review_policy"),
        selected_candidate_ids=selected_candidate_ids,
        node_ids=node_ids,
    )
    order = WorkOrderV3(
        work_order_id=graph.root_work_order_id,
        domain=str(root["domain"]).strip().lower(),
        run_mode=str(root["run_mode"]).strip().lower(),
        objective=objective,
        workspace=deepcopy(dict(root.get("workspace") or {})),
        input_pack=input_pack,
        allowed_actions=_required_text_list(
            root.get("allowed_actions"),
            field_name="allowed_actions",
            allow_empty=True,
        ),
        forbidden_actions=_required_text_list(
            root.get("forbidden_actions"),
            field_name="forbidden_actions",
            allow_empty=True,
        ),
        acceptance_criteria=all_acceptance,
        verification_plan=deepcopy(dict(root["verification_plan"])),
        approval_policy=deepcopy(dict(root["approval_policy"])),
        review_policy=review_policy,
        metadata=metadata,
        root_work_order_id=graph.root_work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph.graph_id,
        task_node_id=graph.nodes[0].node_id,
        graph_revision=graph.revision,
        attempt=0,
        capability_requirements=all_capabilities,
        risk_level=max(int(payload["risk_level"]) for payload in payloads),
        input_refs=all_input_refs,
        output_contract=deepcopy(dict(payloads[-1]["output_contract"])),
        budget_policy=budget_policy,
        business_idempotency_scope=str(root["business_idempotency_scope"]).strip(),
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=str(root["approval_ref"]).strip(),
        review_ref=str(root["review_ref"]).strip(),
    )
    order.validate()
    return order


def _compile_node_review_policies(
    raw_policy: Any,
    *,
    selected_candidate_ids: tuple[str, ...],
    node_ids: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(raw_policy, Mapping) or not raw_policy:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "planning Review policy must be a non-empty object",
        )
    policy = deepcopy(dict(raw_policy))
    raw_candidates = policy.pop(_CANDIDATE_REVIEW_POLICIES_KEY, None)
    if raw_candidates is None:
        return policy
    if NODE_REVIEW_POLICIES_KEY in policy:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "planning context cannot provide compiler-owned node Review policies",
        )
    if not isinstance(raw_candidates, Mapping) or set(raw_candidates) != set(
        selected_candidate_ids
    ):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "candidate Review policies must cover the selected path exactly",
        )
    node_policies: dict[str, dict[str, Any]] = {}
    for candidate_id, node_id in zip(
        selected_candidate_ids,
        node_ids,
        strict=True,
    ):
        raw_entry = raw_candidates.get(candidate_id)
        if not isinstance(raw_entry, Mapping):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"candidate Review policy is invalid: {candidate_id}",
            )
        entry = dict(raw_entry)
        required = entry.get("review_required")
        reason_code = str(entry.get("reason_code") or "").strip()
        source_ref = str(entry.get("source_ref") or "").strip()
        if (
            not isinstance(required, bool)
            or not reason_code
            or "://" not in source_ref
        ):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"candidate Review policy lacks immutable provenance: {candidate_id}",
            )
        node_policies[node_id] = {
            "schema_version": NODE_REVIEW_POLICY_SCHEMA_VERSION,
            "review_required": required,
            "reason_code": reason_code,
            "source_ref": source_ref,
        }
    policy[NODE_REVIEW_POLICIES_KEY] = node_policies
    return policy




__all__ = [
    "CompiledPlanningGraph",
    "PlanningCompilationContext",
    "PlanningGraphCompiler",
    "compile_planning_search_result",
]
