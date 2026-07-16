"""Pure adapters that project legacy planner outputs onto one TaskGraph revision.

The adapters in this module do not persist, schedule, lease, retry, or execute
work.  A :class:`GraphRevisionIdentity` is created by the caller and reused by
every projection so PlanningAgent, SkillDag, and PlanGraph cannot silently
mint different graph identities for the same work order.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    PLAN_CYCLE,
    PLANNER_NEEDS_INPUT,
    SUB_AGENT_LINEAGE_INVALID,
    TASK_GRAPH_INVALID,
    ContractViolation,
    require_version,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TASK_GRAPH_SCHEMA_VERSION,
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    normalize_task_retry_policy,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


PLANNER_ADAPTER_SCHEMA_VERSION = "Natsume.agent.planner_adapter.v1"
PLANNER_SHADOW_SCHEMA_VERSION = "Natsume.agent.planner_shadow_comparison.v1"
PLANNER_SHADOW_INPUT_SCHEMA_VERSION = "Natsume.agent.planner_shadow_input.v1"
PLANNER_ROUTE_TYPE_METADATA_KEY = "planner_route_type"
PLANNER_TARGET_ID_METADATA_KEY = "planner_target_id"
PLANNER_TARGET_BINDING_METADATA_KEY = "planner_target_binding"
_MAX_PARALLEL_WINDOW = 16


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_text(value: Any) -> str:
    return _text(getattr(value, "value", value)).lower()


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    items = [_text(item) for item in value if _text(item)]
    if isinstance(value, (set, frozenset)):
        items.sort()
    return tuple(items)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any, *, field_name: str, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be an integer",
        ) from exc
    if parsed < minimum:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be at least {minimum}",
        )
    return parsed


def _parallel_window(value: Any, *, field_name: str = "parallel_window") -> int:
    parsed = _positive_int(value, field_name=field_name)
    if parsed > _MAX_PARALLEL_WINDOW:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be at most {_MAX_PARALLEL_WINDOW}",
        )
    return parsed


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
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "canonical planner payload must be finite JSON data",
        ) from exc


def _semantic_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _dedupe_text(*values: Sequence[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for group in values:
        for item in group:
            normalized = _text(item)
            if normalized and normalized not in result:
                result.append(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class GraphRevisionIdentity:
    """Identity allocated once by the orchestration boundary."""

    graph_id: str
    revision: int
    root_work_order_id: str

    def validate(self) -> None:
        if not _text(self.graph_id) or not _text(self.root_work_order_id):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "graph_id and root_work_order_id are required",
            )
        _positive_int(self.revision, field_name="revision")

    @classmethod
    def from_task_graph(cls, graph: TaskGraph) -> "GraphRevisionIdentity":
        graph.validate()
        return cls(
            graph_id=graph.graph_id,
            revision=graph.revision,
            root_work_order_id=graph.root_work_order_id,
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "graph_id": self.graph_id,
            "revision": self.revision,
            "root_work_order_id": self.root_work_order_id,
        }


def build_dependency_result_ref(
    identity: GraphRevisionIdentity,
    node_id: str,
) -> str:
    """Return the stable artifact ref a dependent node must consume."""

    identity.validate()
    normalized_node_id = _text(node_id)
    if not normalized_node_id:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "dependency node_id is required")
    return (
        "artifact://task-result/"
        f"{quote(identity.graph_id, safe='')}/revisions/{identity.revision}/nodes/"
        f"{quote(normalized_node_id, safe='')}"
    )


def build_planner_target_binding(
    *,
    child_work_order_id: str,
    root_work_order_id: str,
    parent_work_order_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    route_type: str,
    target_id: str,
) -> str:
    """Bind a child authorization to one route target on one graph revision."""

    payload = {
        "child_work_order_id": _text(child_work_order_id),
        "root_work_order_id": _text(root_work_order_id),
        "parent_work_order_id": _text(parent_work_order_id),
        "graph_id": _text(graph_id),
        "graph_revision": _positive_int(graph_revision, field_name="graph_revision"),
        "node_id": _text(node_id),
        "route_type": _text(route_type).lower(),
        "target_id": _text(target_id),
    }
    if not all(payload.values()):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "planner target binding requires complete child lineage, route, and target",
        )
    return _semantic_digest(payload)


def _task_route_binding(route_type: str, target_id: str) -> str:
    return f"{_text(route_type).lower()}:{quote(_text(target_id), safe='')}"


@dataclass(frozen=True, slots=True)
class PlannerNodeView:
    """Execution-neutral planner metadata paired with a canonical TaskNode."""

    node_id: str
    task_description: str
    route_type: str
    target_id: str
    dependencies: tuple[str, ...] = ()
    dependency_refs: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    output_contract: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(
        default_factory=lambda: {"max_attempts": 1, "retry_backoff_ms": 0}
    )
    risk_level: int = 1
    acceptance_criteria: tuple[str, ...] = ()
    allow_parallel: bool = True
    skill_family: str | None = None
    capability_requirements: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PlannerNodeView":
        data = dict(payload or {})
        view = cls(
            node_id=_text(data.get("node_id")),
            task_description=_text(data.get("task_description")),
            route_type=_text(data.get("route_type")).lower(),
            target_id=_text(data.get("target_id")),
            dependencies=_text_tuple(data.get("dependencies")),
            dependency_refs=_text_tuple(data.get("dependency_refs")),
            input_refs=_text_tuple(data.get("input_refs")),
            output_contract=_mapping(data.get("output_contract")),
            retry_policy=normalize_task_retry_policy(
                data.get("retry_policy")
                if data.get("retry_policy") is not None
                else {"max_attempts": 1, "retry_backoff_ms": 0},
                field_name="planner_node.retry_policy",
            ),
            risk_level=_positive_int(
                data.get("risk_level", 1),
                field_name="risk_level",
            ),
            acceptance_criteria=_text_tuple(data.get("acceptance_criteria")),
            allow_parallel=bool(data.get("allow_parallel", True)),
            skill_family=_text(data.get("skill_family")) or None,
            capability_requirements=_text_tuple(data.get("capability_requirements")),
        )
        view.validate()
        return view

    def validate(self) -> None:
        if not self.node_id or not self.task_description or not self.route_type:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "planner node_id, task_description, and route_type are required",
            )
        if self.route_type not in {"skill", "delegate", "chat"}:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"unsupported planner route_type: {self.route_type}",
            )
        if self.route_type in {"skill", "delegate", "chat"} and not self.target_id:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"target_id is required for {self.route_type} node {self.node_id}",
            )
        if self.node_id in self.dependencies:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"planner node cannot depend on itself: {self.node_id}",
            )
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"planner node dependencies must be unique: {self.node_id}",
            )
        if self.risk_level < 1 or self.risk_level > 5:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "planner node risk_level must be between 1 and 5",
            )
        normalize_task_retry_policy(
            self.retry_policy,
            field_name=f"planner node {self.node_id} retry_policy",
        )

    def to_task_node(self) -> TaskNode:
        self.validate()
        return TaskNode(
            node_id=self.node_id,
            dependencies=self.dependencies,
            route=_task_route_binding(self.route_type, self.target_id),
            input_refs=_dedupe_text(self.input_refs, self.dependency_refs),
            output_contract=deepcopy(self.output_contract),
            retry_policy=normalize_task_retry_policy(
                self.retry_policy,
                field_name=f"planner node {self.node_id} retry_policy",
            ),
            risk_level=self.risk_level,
            acceptance_criteria=self.acceptance_criteria,
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "node_id": self.node_id,
            "task_description": self.task_description,
            "route_type": self.route_type,
            "target_id": self.target_id,
            "dependencies": list(self.dependencies),
            "dependency_refs": list(self.dependency_refs),
            "input_refs": list(self.input_refs),
            "output_contract": deepcopy(self.output_contract),
            "retry_policy": normalize_task_retry_policy(
                self.retry_policy,
                field_name=f"planner node {self.node_id} retry_policy",
            ),
            "risk_level": self.risk_level,
            "acceptance_criteria": list(self.acceptance_criteria),
            "allow_parallel": self.allow_parallel,
            "skill_family": self.skill_family,
            "capability_requirements": list(self.capability_requirements),
        }


def _build_execution_batches(
    nodes: tuple[PlannerNodeView, ...],
    *,
    parallel_window: int,
) -> tuple[tuple[str, ...], ...]:
    window = _parallel_window(parallel_window)
    node_by_id = {node.node_id: node for node in nodes}
    pending = set(node_by_id)
    completed: set[str] = set()
    ordered_ids = [node.node_id for node in nodes]
    batches: list[tuple[str, ...]] = []

    while pending:
        ready = [
            node_by_id[node_id]
            for node_id in ordered_ids
            if node_id in pending
            and set(node_by_id[node_id].dependencies).issubset(completed)
        ]
        if not ready:
            raise ContractViolation(
                PLAN_CYCLE,
                f"planner dependency cycle detected: {','.join(sorted(pending))}",
            )

        first = ready[0]
        if first.allow_parallel:
            current = [node for node in ready if node.allow_parallel][:window]
        else:
            current = [first]
        batch = tuple(node.node_id for node in current)
        batches.append(batch)
        pending.difference_update(batch)
        completed.update(batch)

    return tuple(batches)


def _planner_result_node_id(
    nodes: Sequence[PlannerNodeView],
    *,
    completion_policy: TaskGraphCompletionPolicy | None,
) -> str | None:
    """Select a safe durable result authority from planner-declared order."""

    ordered = tuple(nodes)
    depended_on = {
        dependency
        for node in ordered
        for dependency in node.dependencies
    }
    sinks = tuple(node.node_id for node in ordered if node.node_id not in depended_on)
    if not sinks:
        raise ContractViolation(
            PLAN_CYCLE if ordered else TASK_GRAPH_INVALID,
            (
                "planner dependency cycle has no sink result node"
                if ordered
                else "planner output has no node for result authority"
            ),
        )
    # A legacy multi-sink Graph has no stable outer commit boundary.  Do not
    # invent one result authority that could disagree with the node that
    # happens to complete last.  Atomic Graph success freezes the outer
    # boundary and may therefore select the last planner-declared sink.
    if (
        len(sinks) == 1
        or completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    ):
        return sinks[-1]
    return None


def _planner_completion_policy(value: Any) -> TaskGraphCompletionPolicy | None:
    normalized = _text(getattr(value, "value", value)).upper()
    if not normalized:
        return None
    try:
        return TaskGraphCompletionPolicy(normalized)
    except ValueError as exc:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            f"planner completion_policy is unsupported: {normalized}",
        ) from exc


@dataclass(frozen=True, slots=True)
class CanonicalPlannerOutput:
    """One immutable planner projection suitable for persistence or shadow use."""

    identity: GraphRevisionIdentity
    source_kind: str
    source_plan_id: str
    planner_ref: str
    created_at: int
    parallel_window: int
    nodes: tuple[PlannerNodeView, ...]
    execution_batches: tuple[tuple[str, ...], ...]
    task_graph: TaskGraph
    schema_version: str = PLANNER_ADAPTER_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        identity: GraphRevisionIdentity,
        source_kind: str,
        source_plan_id: str,
        planner_ref: str,
        created_at: int,
        nodes: Sequence[PlannerNodeView],
        result_node_id: str | None = None,
        completion_policy: TaskGraphCompletionPolicy | str | None = None,
        parallel_window: int | None = None,
    ) -> "CanonicalPlannerOutput":
        identity.validate()
        normalized_nodes = tuple(nodes)
        for node in normalized_nodes:
            node.validate()
        resolved_parallel_window = _parallel_window(
            parallel_window
            if parallel_window is not None
            else min(_MAX_PARALLEL_WINDOW, max(1, len(normalized_nodes)))
        )
        resolved_completion_policy = _planner_completion_policy(completion_policy)
        resolved_result_node_id = _text(result_node_id) or _planner_result_node_id(
            normalized_nodes,
            completion_policy=resolved_completion_policy,
        )
        task_graph = TaskGraph(
            graph_id=identity.graph_id,
            root_work_order_id=identity.root_work_order_id,
            revision=identity.revision,
            planner_ref=_text(planner_ref),
            status=TaskGraphStatus.CREATED,
            created_at=max(0, int(created_at)),
            result_node_id=resolved_result_node_id,
            completion_policy=resolved_completion_policy,
            nodes=tuple(node.to_task_node() for node in normalized_nodes),
        )
        task_graph.validate()
        output = cls(
            identity=identity,
            source_kind=_text(source_kind),
            source_plan_id=_text(source_plan_id),
            planner_ref=_text(planner_ref),
            created_at=max(0, int(created_at)),
            parallel_window=resolved_parallel_window,
            nodes=normalized_nodes,
            execution_batches=_build_execution_batches(
                normalized_nodes,
                parallel_window=resolved_parallel_window,
            ),
            task_graph=task_graph,
        )
        output.validate()
        return output

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CanonicalPlannerOutput":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=PLANNER_ADAPTER_SCHEMA_VERSION,
            contract_name="CanonicalPlannerOutput",
        )
        identity_payload = _mapping(data.get("identity"))
        identity = GraphRevisionIdentity(
            graph_id=_text(identity_payload.get("graph_id")),
            revision=_positive_int(identity_payload.get("revision"), field_name="revision"),
            root_work_order_id=_text(identity_payload.get("root_work_order_id")),
        )
        raw_nodes = data.get("planner_nodes")
        nodes = tuple(
            PlannerNodeView.from_mapping(item)
            for item in (raw_nodes if isinstance(raw_nodes, list) else [])
            if isinstance(item, Mapping)
        )
        raw_batches = data.get("execution_batches")
        batches = tuple(
            _text_tuple(batch)
            for batch in (raw_batches if isinstance(raw_batches, list) else [])
            if isinstance(batch, (list, tuple))
        )
        graph_payload = data.get("task_graph")
        if not isinstance(graph_payload, Mapping):
            raise ContractViolation(TASK_GRAPH_INVALID, "canonical planner task_graph is required")
        inferred_window = max(
            (len(batch) for batch in batches),
            default=1,
        )
        output = cls(
            identity=identity,
            source_kind=_text(data.get("source_kind")),
            source_plan_id=_text(data.get("source_plan_id")),
            planner_ref=_text(data.get("planner_ref")),
            created_at=_positive_int(
                data.get("created_at", 0),
                field_name="created_at",
                minimum=0,
            ),
            parallel_window=_parallel_window(
                data.get("parallel_window", inferred_window),
            ),
            nodes=nodes,
            execution_batches=batches,
            task_graph=TaskGraph.from_mapping(graph_payload),
        )
        output.validate()
        return output

    def validate(self) -> None:
        if self.schema_version != PLANNER_ADAPTER_SCHEMA_VERSION:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "canonical planner adapter schema_version is invalid",
            )
        self.identity.validate()
        self.task_graph.validate()
        validate_same_graph_revision(self.identity, self.task_graph)
        _parallel_window(self.parallel_window)
        if not self.source_kind or not self.source_plan_id or not self.planner_ref:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "source_kind, source_plan_id, and planner_ref are required",
            )
        expected_graph = TaskGraph(
            graph_id=self.identity.graph_id,
            root_work_order_id=self.identity.root_work_order_id,
            revision=self.identity.revision,
            planner_ref=self.planner_ref,
            status=TaskGraphStatus.CREATED,
            created_at=self.created_at,
            result_node_id=self.task_graph.result_node_id,
            completion_policy=self.task_graph.completion_policy,
            nodes=tuple(node.to_task_node() for node in self.nodes),
        )
        expected_graph.validate()
        if self.task_graph != expected_graph:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "TaskGraph must be the exact semantic projection of planner nodes",
            )

        graph_nodes = {node.node_id: node for node in self.task_graph.nodes}
        view_nodes = {node.node_id: node for node in self.nodes}
        if set(graph_nodes) != set(view_nodes):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "planner view nodes must exactly match TaskGraph nodes",
            )
        for node_id, view in view_nodes.items():
            view.validate()
            graph_node = graph_nodes[node_id]
            if graph_node != view.to_task_node():
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    f"planner view drift for node: {node_id}",
                )
            expected_refs = tuple(
                build_dependency_result_ref(self.identity, dependency)
                for dependency in view.dependencies
            )
            if view.dependency_refs != expected_refs:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    f"dependency refs drift for node: {node_id}",
                )

        expected_batches = _build_execution_batches(
            self.nodes,
            parallel_window=self.parallel_window,
        )
        if self.execution_batches != expected_batches:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "execution batches must equal the deterministic planner projection",
            )

        flattened = [node_id for batch in self.execution_batches for node_id in batch]
        if len(flattened) != len(set(flattened)) or set(flattened) != set(view_nodes):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "execution batches must cover every planner node exactly once",
            )
        completed: set[str] = set()
        for batch in self.execution_batches:
            for node_id in batch:
                if not set(view_nodes[node_id].dependencies).issubset(completed):
                    raise ContractViolation(
                        TASK_GRAPH_INVALID,
                        f"execution batch violates dependencies for node: {node_id}",
                    )
            completed.update(batch)

    def node(self, node_id: str) -> PlannerNodeView:
        normalized = _text(node_id)
        for node in self.nodes:
            if node.node_id == normalized:
                return node
        raise ContractViolation(TASK_GRAPH_INVALID, f"unknown planner node: {normalized}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "source_kind": self.source_kind,
            "source_plan_id": self.source_plan_id,
            "planner_ref": self.planner_ref,
            "created_at": self.created_at,
            "parallel_window": self.parallel_window,
            "task_graph": self.task_graph.to_dict(),
            "planner_nodes": [node.to_dict() for node in self.nodes],
            "execution_batches": [list(batch) for batch in self.execution_batches],
        }

    def to_persistence_input(self) -> dict[str, Any]:
        """Return data for a future TaskStore without performing any write."""

        return self.to_dict()

    def to_semantic_projection(self) -> dict[str, Any]:
        """Return complete execution semantics without adapter provenance."""

        self.validate()
        payload = {
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "parallel_window": self.parallel_window,
            "planner_nodes": [node.to_dict() for node in self.nodes],
            "execution_batches": [list(batch) for batch in self.execution_batches],
        }
        if self.task_graph.result_node_id is not None:
            payload["result_node_id"] = self.task_graph.result_node_id
        if self.task_graph.completion_policy is not None:
            payload["completion_policy"] = self.task_graph.completion_policy.value
        return payload

    def to_shadow_projection(self) -> dict[str, Any]:
        """Return a trace-safe projection containing refs, lineage, and digests only."""

        self.validate()
        payload = self.to_dict()
        projection = {
            "schema_version": PLANNER_SHADOW_INPUT_SCHEMA_VERSION,
            **self.identity.to_dict(),
            "source_kind": self.source_kind,
            "source_plan_id": self.source_plan_id,
            "planner_ref": self.planner_ref,
            "plan_ref": (
                "artifact://canonical-plan/"
                f"{quote(self.identity.graph_id, safe='')}/revisions/{self.identity.revision}"
            ),
            "plan_digest": _semantic_digest(payload),
            "parallel_window": self.parallel_window,
            "execution_batches": [list(batch) for batch in self.execution_batches],
            "node_bindings": [
                {
                    "node_id": node.node_id,
                    "route_type": node.route_type,
                    "target_id": node.target_id,
                    "dependencies": list(node.dependencies),
                    "dependency_refs": list(node.dependency_refs),
                    "node_ref": build_dependency_result_ref(self.identity, node.node_id),
                    "semantic_digest": _semantic_digest(node.to_dict()),
                }
                for node in self.nodes
            ],
        }
        if self.task_graph.result_node_id is not None:
            projection["result_node_id"] = self.task_graph.result_node_id
        if self.task_graph.completion_policy is not None:
            projection["completion_policy"] = self.task_graph.completion_policy.value
        return projection


def _normalize_node_id(value: Any, *, fallback: str) -> str:
    raw = _text(value) or fallback
    normalized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in raw)
    return normalized.strip("_") or fallback


def adapt_planning_agent_plan(
    plan: Any,
    *,
    identity: GraphRevisionIdentity,
    planner_ref: str = "planner://planning-agent",
    created_at: int | None = None,
) -> CanonicalPlannerOutput:
    """Project a PlanningAgent TaskPlan without allocating a graph identity."""

    all_steps = list(getattr(plan, "steps", None) or [])
    source_plan_id = _text(getattr(plan, "task_id", ""))
    if not source_plan_id:
        raise ContractViolation(TASK_GRAPH_INVALID, "PlanningAgent plan.task_id is required")

    ask_steps = [
        step
        for step in all_steps
        if _text(getattr(step, "action_type", "")).lower() == "ask"
        or _status_text(getattr(step, "status", "")) == "needs_input"
    ]
    if ask_steps or _status_text(getattr(plan, "status", "")) == "needs_input":
        ask_ids = ",".join(
            str(getattr(step, "step_id", "") or "?")
            for step in ask_steps
        ) or "plan"
        raise ContractViolation(
            PLANNER_NEEDS_INPUT,
            f"PlanningAgent plan requires user input before execution: steps {ask_ids}",
        )

    all_step_ids = {
        int(getattr(step, "step_id"))
        for step in all_steps
        if str(getattr(step, "step_id", "")).strip().isdigit()
    }
    step_type_by_id = {
        int(getattr(step, "step_id")): _text(getattr(step, "action_type", "")).lower()
        for step in all_steps
        if str(getattr(step, "step_id", "")).strip().isdigit()
    }
    action_steps = [step for step in all_steps if _text(getattr(step, "action_type", "")).lower() == "act"]
    if not action_steps:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "PlanningAgent plan has no executable act steps",
        )
    action_node_by_step: dict[int, str] = {}
    for position, step in enumerate(action_steps, start=1):
        step_id = _positive_int(getattr(step, "step_id", position), field_name="step_id")
        action_node_by_step[step_id] = _normalize_node_id(
            getattr(step, "node_id", ""),
            fallback=f"step-{step_id}",
        )

    nodes: list[PlannerNodeView] = []
    for position, step in enumerate(action_steps, start=1):
        step_id = _positive_int(getattr(step, "step_id", position), field_name="step_id")
        raw_dependencies = list(getattr(step, "depends_on", None) or [])
        invalid_dependency_tokens = [
            dependency
            for dependency in raw_dependencies
            if not str(dependency).strip().isdigit()
        ]
        if invalid_dependency_tokens:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"PlanningAgent step {step_id} has invalid dependency tokens: "
                f"{invalid_dependency_tokens}",
            )
        unknown = [
            dependency
            for dependency in raw_dependencies
            if str(dependency).strip().isdigit() and int(dependency) not in all_step_ids
        ]
        if unknown:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"PlanningAgent step {step_id} has unknown dependencies: {unknown}",
            )
        unsupported_non_action_dependencies = [
            int(dependency)
            for dependency in raw_dependencies
            if str(dependency).strip().isdigit()
            and int(dependency) in all_step_ids
            and int(dependency) not in action_node_by_step
            and step_type_by_id.get(int(dependency)) != "reason"
        ]
        if unsupported_non_action_dependencies:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"PlanningAgent step {step_id} depends on non-executable steps: "
                f"{unsupported_non_action_dependencies}",
            )
        dependencies = tuple(
            action_node_by_step[int(dependency)]
            for dependency in raw_dependencies
            if str(dependency).strip().isdigit() and int(dependency) in action_node_by_step
        )
        scaffold_input_refs = tuple(
            "artifact://planner-context/"
            f"{quote(source_plan_id, safe='')}/steps/{int(dependency)}"
            for dependency in raw_dependencies
            if str(dependency).strip().isdigit()
            and step_type_by_id.get(int(dependency)) == "reason"
        )
        target_id = _text(getattr(step, "skill_name", "")) or "intent_agent"
        route_type = "delegate" if target_id.endswith("_agent") else "skill"
        node_id = action_node_by_step[step_id]
        dependency_refs = tuple(
            build_dependency_result_ref(identity, dependency)
            for dependency in dependencies
        )
        nodes.append(
            PlannerNodeView(
                node_id=node_id,
                task_description=_text(getattr(step, "description", "")) or f"execute step {step_id}",
                route_type=route_type,
                target_id=target_id,
                dependencies=dependencies,
                dependency_refs=dependency_refs,
                input_refs=scaffold_input_refs,
                output_contract={"type": "planner_step_result"},
                risk_level=1,
                allow_parallel=(
                    True
                    if getattr(step, "allow_parallel", None) is None
                    else bool(getattr(step, "allow_parallel"))
                ),
                capability_requirements=(target_id,),
            )
        )

    resolved_created_at = int(
        created_at
        if created_at is not None
        else (getattr(plan, "created_ts_ms", 0) or 0)
    )
    return CanonicalPlannerOutput.build(
        identity=identity,
        source_kind="planning_agent",
        source_plan_id=source_plan_id,
        planner_ref=planner_ref,
        created_at=resolved_created_at,
        nodes=nodes,
        result_node_id=(
            _text(getattr(plan, "result_node_id", "")) or None
        ),
        completion_policy=getattr(plan, "completion_policy", None),
    )


def adapt_skill_dag_plan(
    plan: Any,
    *,
    identity: GraphRevisionIdentity,
    planner_ref: str = "planner://skill-dag",
    created_at: int = 0,
) -> CanonicalPlannerOutput:
    """Project a SkillDagPlan while preserving routes, dependencies, and refs."""

    source_plan_id = _text(getattr(plan, "plan_id", ""))
    if not source_plan_id:
        raise ContractViolation(TASK_GRAPH_INVALID, "SkillDagPlan.plan_id is required")
    if bool(getattr(plan, "has_cycle_warning", False)):
        raise ContractViolation(
            PLAN_CYCLE,
            f"SkillDagPlan {source_plan_id} carries a cycle warning",
        )
    parallel_window = _parallel_window(
        getattr(plan, "parallel_window", None)
        if getattr(plan, "parallel_window", None) is not None
        else _MAX_PARALLEL_WINDOW,
        field_name="SkillDagPlan.parallel_window",
    )
    indexed_source_nodes = list(
        enumerate(list(getattr(plan, "nodes", None) or []), start=1)
    )
    indexed_source_nodes.sort(
        key=lambda item: (
            _positive_int(
                getattr(item[1], "step_index", item[0]),
                field_name="SkillDagNode.step_index",
            ),
            _normalize_node_id(
                getattr(item[1], "node_id", ""),
                fallback=f"node-{item[0]}",
            ),
        )
    )
    nodes: list[PlannerNodeView] = []
    for position, source_node in indexed_source_nodes:
        node_id = _normalize_node_id(
            getattr(source_node, "node_id", ""),
            fallback=f"node-{position}",
        )
        dependencies = _text_tuple(getattr(source_node, "dependencies", ()))
        dependency_refs = tuple(
            build_dependency_result_ref(identity, dependency)
            for dependency in dependencies
        )
        retry_limit = max(0, int(getattr(source_node, "retry_limit", 0) or 0))
        retry_backoff_ms = max(0, int(getattr(source_node, "retry_backoff_ms", 0) or 0))
        args_payload = _mapping(getattr(source_node, "args_payload", {}))
        opaque_arg_keys = sorted(
            key
            for key, value in args_payload.items()
            if key not in {"input_refs", "output_contract", "acceptance_criteria"}
            and value not in (None, "", [], {}, ())
        )
        if opaque_arg_keys:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                f"SkillDag node {node_id} has inline execution args without governed refs: "
                f"{','.join(opaque_arg_keys)}",
            )
        input_refs = _text_tuple(args_payload.get("input_refs"))
        output_contract = _mapping(args_payload.get("output_contract")) or {
            "type": "planner_step_result"
        }
        acceptance_criteria = _text_tuple(args_payload.get("acceptance_criteria"))
        nodes.append(
            PlannerNodeView(
                node_id=node_id,
                task_description=(
                    _text(getattr(source_node, "task_description", ""))
                    or _text(getattr(source_node, "description", ""))
                    or f"execute node {node_id}"
                ),
                route_type=_text(getattr(source_node, "route", "")).lower() or "delegate",
                target_id=_text(getattr(source_node, "target_id", "")) or "intent_agent",
                dependencies=dependencies,
                dependency_refs=dependency_refs,
                input_refs=input_refs,
                output_contract=output_contract,
                retry_policy={
                    "max_attempts": retry_limit + 1,
                    "retry_backoff_ms": retry_backoff_ms,
                },
                risk_level=max(1, min(5, int(getattr(source_node, "risk_level", 1) or 1))),
                acceptance_criteria=acceptance_criteria,
                allow_parallel=bool(getattr(source_node, "allow_parallel", True)),
                skill_family=_text(getattr(source_node, "skill_family", "")) or None,
                capability_requirements=_text_tuple(
                    getattr(source_node, "capability_tags", ())
                ),
            )
        )

    output = CanonicalPlannerOutput.build(
        identity=identity,
        source_kind="skill_dag",
        source_plan_id=source_plan_id,
        planner_ref=planner_ref,
        created_at=created_at,
        nodes=nodes,
        result_node_id=(
            _text(getattr(plan, "result_node_id", "")) or None
        ),
        completion_policy=getattr(plan, "completion_policy", None),
        parallel_window=parallel_window,
    )
    raw_source_batches = getattr(plan, "execution_batches", None)
    if raw_source_batches:
        if not isinstance(raw_source_batches, (list, tuple)):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "SkillDagPlan.execution_batches must be a sequence",
            )
        source_batches = tuple(
            _text_tuple(batch)
            for batch in raw_source_batches
            if isinstance(batch, (list, tuple))
        )
        if len(source_batches) != len(raw_source_batches) or source_batches != output.execution_batches:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "SkillDagPlan execution batches drift from dependencies or parallel_window",
            )
    return output


def _coerce_identity(value: GraphRevisionIdentity | TaskGraph | CanonicalPlannerOutput) -> GraphRevisionIdentity:
    if isinstance(value, GraphRevisionIdentity):
        value.validate()
        return value
    if isinstance(value, CanonicalPlannerOutput):
        value.validate()
        return value.identity
    if isinstance(value, TaskGraph):
        return GraphRevisionIdentity.from_task_graph(value)
    raise ContractViolation(TASK_GRAPH_INVALID, "unsupported graph revision identity source")


def validate_same_graph_revision(
    expected: GraphRevisionIdentity | TaskGraph | CanonicalPlannerOutput,
    observed: GraphRevisionIdentity | TaskGraph | CanonicalPlannerOutput,
) -> GraphRevisionIdentity:
    """Fail closed when a consumer observes a second graph ID or revision."""

    expected_identity = _coerce_identity(expected)
    observed_identity = _coerce_identity(observed)
    if expected_identity != observed_identity:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "expected "
            f"{expected_identity.graph_id}@{expected_identity.revision}/"
            f"{expected_identity.root_work_order_id}, observed "
            f"{observed_identity.graph_id}@{observed_identity.revision}/"
            f"{observed_identity.root_work_order_id}",
        )
    return observed_identity


def _dependency_edges(plan: CanonicalPlannerOutput) -> list[list[str]]:
    return sorted(
        [[dependency, node.node_id] for node in plan.nodes for dependency in node.dependencies]
    )


def _route_bindings(plan: CanonicalPlannerOutput) -> list[list[str]]:
    return [
        [node.node_id, node.route_type, node.target_id]
        for node in plan.nodes
    ]


def _dependency_ref_bindings(plan: CanonicalPlannerOutput) -> list[list[Any]]:
    return [
        [node.node_id, list(node.dependency_refs)]
        for node in plan.nodes
    ]


def build_planner_shadow_comparison(
    source: CanonicalPlannerOutput,
    consumer: CanonicalPlannerOutput,
) -> dict[str, Any]:
    """Build a read-only, serializable diff payload for shadow observation."""

    source.validate()
    consumer.validate()
    same_graph_id = source.identity.graph_id == consumer.identity.graph_id
    same_revision = source.identity.revision == consumer.identity.revision
    same_root = source.identity.root_work_order_id == consumer.identity.root_work_order_id
    source_node_ids = [node.node_id for node in source.nodes]
    consumer_node_ids = [node.node_id for node in consumer.nodes]
    source_edges = _dependency_edges(source)
    consumer_edges = _dependency_edges(consumer)
    source_routes = _route_bindings(source)
    consumer_routes = _route_bindings(consumer)
    source_dependency_refs = _dependency_ref_bindings(source)
    consumer_dependency_refs = _dependency_ref_bindings(consumer)
    same_nodes = source_node_ids == consumer_node_ids
    same_dependencies = source_edges == consumer_edges
    same_routes = source_routes == consumer_routes
    same_dependency_refs = source_dependency_refs == consumer_dependency_refs
    same_batches = source.execution_batches == consumer.execution_batches
    same_result_authority = (
        source.task_graph.result_node_id == consumer.task_graph.result_node_id
    )
    same_completion_policy = (
        source.task_graph.completion_policy == consumer.task_graph.completion_policy
    )
    source_semantic_digest = _semantic_digest(source.to_semantic_projection())
    consumer_semantic_digest = _semantic_digest(consumer.to_semantic_projection())
    same_semantics = source_semantic_digest == consumer_semantic_digest
    mismatch_reasons: list[str] = []
    for matched, reason in (
        (same_graph_id, "GRAPH_ID_MISMATCH"),
        (same_revision, "REVISION_MISMATCH"),
        (same_root, "ROOT_WORK_ORDER_MISMATCH"),
        (same_nodes, "NODE_SET_MISMATCH"),
        (same_dependencies, "DEPENDENCY_MISMATCH"),
        (same_routes, "ROUTE_BINDING_MISMATCH"),
        (same_dependency_refs, "DEPENDENCY_REF_MISMATCH"),
        (same_batches, "BATCH_MISMATCH"),
        (same_result_authority, "RESULT_AUTHORITY_MISMATCH"),
        (same_completion_policy, "COMPLETION_POLICY_MISMATCH"),
        (same_semantics, "SEMANTIC_MISMATCH"),
    ):
        if not matched:
            mismatch_reasons.append(reason)
    return {
        "schema_version": PLANNER_SHADOW_SCHEMA_VERSION,
        "source": {
            "source_kind": source.source_kind,
            "source_plan_id": source.source_plan_id,
            **source.identity.to_dict(),
            "node_ids": source_node_ids,
            "dependency_edges": source_edges,
            "route_bindings": source_routes,
            "dependency_refs": source_dependency_refs,
            "result_node_id": source.task_graph.result_node_id,
            "completion_policy": (
                source.task_graph.completion_policy.value
                if source.task_graph.completion_policy is not None
                else None
            ),
            "execution_batches": [list(batch) for batch in source.execution_batches],
            "semantic_digest": source_semantic_digest,
        },
        "consumer": {
            "source_kind": consumer.source_kind,
            "source_plan_id": consumer.source_plan_id,
            **consumer.identity.to_dict(),
            "node_ids": consumer_node_ids,
            "dependency_edges": consumer_edges,
            "route_bindings": consumer_routes,
            "dependency_refs": consumer_dependency_refs,
            "result_node_id": consumer.task_graph.result_node_id,
            "completion_policy": (
                consumer.task_graph.completion_policy.value
                if consumer.task_graph.completion_policy is not None
                else None
            ),
            "execution_batches": [list(batch) for batch in consumer.execution_batches],
            "semantic_digest": consumer_semantic_digest,
        },
        "same_graph_id": same_graph_id,
        "same_revision": same_revision,
        "same_root_work_order": same_root,
        "same_nodes": same_nodes,
        "same_dependencies": same_dependencies,
        "same_routes": same_routes,
        "same_dependency_refs": same_dependency_refs,
        "same_result_authority": same_result_authority,
        "same_completion_policy": same_completion_policy,
        "same_execution_batches": same_batches,
        "same_semantics": same_semantics,
        "compatible": not mismatch_reasons,
        "mismatch_reasons": mismatch_reasons,
    }


def build_child_work_order_for_plan_node(
    *,
    parent_work_order: WorkOrderV3,
    plan: CanonicalPlannerOutput,
    node_id: str,
    child_work_order_id: str | None = None,
) -> WorkOrderV3:
    """Construct the only valid SubAgent task source: a child WorkOrder."""

    parent_work_order.validate()
    plan.validate()
    if parent_work_order.root_work_order_id != plan.identity.root_work_order_id:
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "parent WorkOrder root does not match planner graph root",
        )
    if parent_work_order.task_graph_id is not None and (
        parent_work_order.task_graph_id != plan.identity.graph_id
        or parent_work_order.graph_revision != plan.identity.revision
    ):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "parent WorkOrder graph identity does not match planner graph revision",
        )
    node = plan.node(node_id)
    digest_input = "|".join(
        [
            parent_work_order.work_order_id,
            plan.identity.graph_id,
            str(plan.identity.revision),
            node.node_id,
        ]
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    resolved_child_id = _text(child_work_order_id) or f"wo_child_{digest[:24]}"
    target_binding = build_planner_target_binding(
        child_work_order_id=resolved_child_id,
        root_work_order_id=parent_work_order.root_work_order_id,
        parent_work_order_id=parent_work_order.work_order_id,
        graph_id=plan.identity.graph_id,
        graph_revision=plan.identity.revision,
        node_id=node.node_id,
        route_type=node.route_type,
        target_id=node.target_id,
    )
    operation_seed = "|".join(
        [
            parent_work_order.operation_fingerprint,
            plan.identity.graph_id,
            str(plan.identity.revision),
            node.node_id,
            node.task_description,
            node.route_type,
            node.target_id,
        ]
    )
    child = WorkOrderV3(
        work_order_id=resolved_child_id,
        domain=parent_work_order.domain,
        run_mode=parent_work_order.run_mode,
        objective=node.task_description,
        workspace=deepcopy(parent_work_order.workspace),
        input_pack=deepcopy(parent_work_order.input_pack),
        allowed_actions=tuple(parent_work_order.allowed_actions),
        forbidden_actions=tuple(parent_work_order.forbidden_actions),
        acceptance_criteria=(
            node.acceptance_criteria
            if node.acceptance_criteria
            else tuple(parent_work_order.acceptance_criteria)
        ),
        verification_plan=deepcopy(parent_work_order.verification_plan),
        approval_policy=deepcopy(parent_work_order.approval_policy),
        review_policy=deepcopy(parent_work_order.review_policy),
        metadata={
            **deepcopy(parent_work_order.metadata),
            "planner_source_kind": plan.source_kind,
            "planner_source_plan_id": plan.source_plan_id,
            "planner_ref": plan.planner_ref,
            PLANNER_ROUTE_TYPE_METADATA_KEY: node.route_type,
            PLANNER_TARGET_ID_METADATA_KEY: node.target_id,
            PLANNER_TARGET_BINDING_METADATA_KEY: target_binding,
        },
        root_work_order_id=parent_work_order.root_work_order_id,
        parent_work_order_id=parent_work_order.work_order_id,
        task_graph_id=plan.identity.graph_id,
        task_node_id=node.node_id,
        graph_revision=plan.identity.revision,
        attempt=0,
        capability_requirements=_dedupe_text(
            tuple(parent_work_order.capability_requirements),
            node.capability_requirements,
            (node.target_id,),
        ),
        risk_level=max(parent_work_order.risk_level, node.risk_level),
        input_refs=_dedupe_text(
            tuple(parent_work_order.input_refs),
            node.input_refs,
            node.dependency_refs,
        ),
        output_contract=deepcopy(node.output_contract),
        budget_policy=parent_work_order.budget_policy,
        business_idempotency_scope=parent_work_order.business_idempotency_scope,
        operation_fingerprint=f"sha256:{hashlib.sha256(operation_seed.encode('utf-8')).hexdigest()}",
        status_version=0,
        approval_ref=parent_work_order.approval_ref,
        review_ref=parent_work_order.review_ref,
    )
    child.validate()
    return child


__all__ = [
    "PLANNER_ADAPTER_SCHEMA_VERSION",
    "PLANNER_NEEDS_INPUT",
    "PLANNER_ROUTE_TYPE_METADATA_KEY",
    "PLANNER_SHADOW_SCHEMA_VERSION",
    "PLANNER_SHADOW_INPUT_SCHEMA_VERSION",
    "PLANNER_TARGET_BINDING_METADATA_KEY",
    "PLANNER_TARGET_ID_METADATA_KEY",
    "CanonicalPlannerOutput",
    "GraphRevisionIdentity",
    "PlannerNodeView",
    "adapt_planning_agent_plan",
    "adapt_skill_dag_plan",
    "build_child_work_order_for_plan_node",
    "build_dependency_result_ref",
    "build_planner_target_binding",
    "build_planner_shadow_comparison",
    "validate_same_graph_revision",
]
