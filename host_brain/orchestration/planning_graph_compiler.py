"""Pure PlanningSearch selected-path compiler for governed task admission.

The compiler is deliberately outside ``planning_search``: search proposes and
selects data-only candidates, while this module freezes one selected path into
canonical execution contracts.  It never persists, schedules, calls a worker,
or reads alternate/ranked candidates.
"""

from __future__ import annotations

import hashlib
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
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
    normalize_task_retry_policy,
    validate_task_graph_admission_state,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.planning_search.contracts import (
    SearchCandidate,
    SearchResult,
    SearchStatus,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
_SAFE_SENSITIVE_WORD_KEYS = frozenset({"token_count"})
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "auth",
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "authentication",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "raw_auth",
        "rawauth",
        "oauth",
        "secret",
        "token",
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
        previous_revision: int | None,
        failure_evidence_refs: tuple[str, ...],
    ) -> "CompiledPlanningGraph":
        compilation_digest = _compilation_digest(
            work_order=work_order,
            task_graph=task_graph,
            trace_digest=trace_digest,
            selected_candidate_ids=selected_candidate_ids,
            previous_revision=previous_revision,
            failure_evidence_refs=failure_evidence_refs,
        )
        product = cls(
            _work_order_json=_canonical_json(work_order.to_dict()),
            _task_graph_json=_canonical_json(task_graph.to_dict()),
            trace_digest=trace_digest,
            selected_candidate_ids=selected_candidate_ids,
            compilation_digest=compilation_digest,
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
    def graph(self) -> TaskGraph:
        """Compatibility alias that keeps the canonical field explicit."""

        return self.task_graph

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
            "previous_revision": self.previous_revision,
            "failure_evidence_refs": list(self.failure_evidence_refs),
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
        resolved_created_at = _exact_non_negative_int(created_at, field_name="created_at")
        evidence_refs = _normalize_evidence_refs(failure_evidence_refs)

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
        graph = TaskGraph(
            graph_id=graph_id,
            root_work_order_id=root_work_order_id,
            revision=revision,
            planner_ref=planner_ref,
            status=TaskGraphStatus.CREATED,
            created_at=resolved_created_at,
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
    ) -> CompiledPlanningGraph:
        """Explicit failure-only spelling of ``compile(..., previous_graph=...)``."""

        return self.compile(
            result,
            created_at=created_at,
            previous_graph=previous_graph,
            failure_evidence_refs=failure_evidence_refs,
        )


# The longer name is useful at composition sites without creating a second API.
PlanningSearchGraphCompiler = PlanningGraphCompiler


def compile_planning_search_result(
    result: SearchResult,
    *,
    created_at: int = 0,
    previous_graph: TaskGraph | None = None,
    failure_evidence_refs: Sequence[str] = (),
) -> CompiledPlanningGraph:
    """Stateless convenience entry point for the canonical compiler."""

    return PlanningGraphCompiler().compile(
        result,
        created_at=created_at,
        previous_graph=previous_graph,
        failure_evidence_refs=failure_evidence_refs,
    )


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
    _required_text_list(payload.get("input_refs"), field_name="input_refs", allow_empty=True)
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
    dependencies = tuple(dependency_node_ids)
    dependency_refs = tuple(
        _dependency_result_ref(graph_id, revision, dependency_node_id)
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
    operation_fingerprint: str,
    failure_evidence_refs: tuple[str, ...],
    previous_revision: int | None,
    previous_status: str | None,
    previous_planner_ref: str | None,
) -> WorkOrderV3:
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
        "trace_digest": trace_digest,
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
        review_policy=deepcopy(dict(root["review_policy"])),
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


def _assert_secret_free(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"sensitive key is forbidden in planning payload: {path}.{key}",
                )
            _assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    if normalized in _SAFE_SENSITIVE_WORD_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    parts = tuple(part for part in normalized.split("_") if part)
    if any(part in _SENSITIVE_KEY_PARTS for part in parts):
        return True
    compact = normalized.replace("_", "")
    if any(
        marker in compact
        for marker in (
            "apikey",
            "authheader",
            "authentication",
            "credential",
            "password",
            "passwd",
            "privatekey",
            "rawauth",
            "secret",
            "token",
        )
    ):
        return True
    return any(
        normalized.startswith(prefix) or normalized.endswith(suffix)
        for prefix, suffix in (
            ("secret", "secret"),
            ("token", "token"),
            ("credential", "credential"),
            ("rawauth", "rawauth"),
        )
    )


def _normalize_evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "failure_evidence_refs must be a sequence of refs",
        )
    result: list[str] = []
    for index, value in enumerate(values):
        ref = _required_text(
            value,
            field_name=f"failure_evidence_refs[{index}]",
            max_length=1_000,
        )
        if "://" not in ref:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"failure_evidence_refs[{index}] must be a governed ref",
            )
        if ref not in result:
            result.append(ref)
    return tuple(result)


def _initial_lineage(
    *,
    request_id: str,
    selected_plan_digest: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(
        (
            "Natsume.planning_search.lineage.v1\n"
            f"{request_id}\n{selected_plan_digest}"
        ).encode("utf-8")
    ).hexdigest()
    return f"graph-planning-{digest[:40]}", f"wo-planning-{digest[24:64]}"


def _node_id(*, graph_id: str, position: int, candidate_id: str) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "graph_id": graph_id,
                "position": position,
                "candidate_id": candidate_id,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"node-planning-{position + 1:04d}-{digest[:24]}"


def _planner_ref(
    *,
    request_id: str,
    graph_id: str,
    revision: int,
    trace_digest: str,
) -> str:
    request_digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
    return (
        f"planner://planning-search/{request_digest}/graphs/{quote(graph_id, safe='')}"
        f"/revisions/{revision}/trace/sha256:{trace_digest}"
    )


def _dependency_result_ref(graph_id: str, revision: int, node_id: str) -> str:
    return (
        "artifact://task-result/"
        f"{quote(graph_id, safe='')}/revisions/{revision}/nodes/{quote(node_id, safe='')}"
    )


def _sha256_ref(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _compilation_digest(
    *,
    work_order: WorkOrderV3,
    task_graph: TaskGraph,
    trace_digest: str,
    selected_candidate_ids: Sequence[str],
    previous_revision: int | None,
    failure_evidence_refs: Sequence[str],
) -> str:
    return _sha256_ref(
        {
            "schema": "Natsume.planning_search.compiled_graph.v1",
            "work_order": work_order.to_dict(),
            "task_graph": task_graph.to_dict(),
            "trace_digest": trace_digest,
            "selected_candidate_ids": list(selected_candidate_ids),
            "previous_revision": previous_revision,
            "failure_evidence_refs": list(failure_evidence_refs),
        }
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "planning payload must be JSON-safe") from exc


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _required_text(value: Any, *, field_name: str, max_length: int = 1_000) -> str:
    if not isinstance(value, str):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be text",
        )
    text = value.strip()
    if not text or len(text) > max_length:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be non-empty and at most {max_length} characters",
        )
    return text


def _required_text_list(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an explicit list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _required_text(item, field_name=f"{field_name}[{index}]", max_length=4_000)
        if text not in result:
            result.append(text)
    if not result and not allow_empty:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must not be empty")
    return tuple(result)


def _dedupe_text(*groups: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
    return tuple(result)


def _exact_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be an integer >= 0",
        )
    return value


__all__ = [
    "CompiledPlanningGraph",
    "PlanningGraphCompiler",
    "PlanningSearchGraphCompiler",
    "compile_planning_search_result",
]
