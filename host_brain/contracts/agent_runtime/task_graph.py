"""Versioned TaskGraph, TaskNode, and TaskEvent contracts.

This module deliberately contains no persistence, scheduling, leasing, or
execution behavior. It freezes serialization and legal state transitions for
later TaskStore and scheduler work.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    PLAN_CYCLE,
    TASK_GRAPH_INVALID,
    TASK_TRANSITION_INVALID,
    ContractViolation,
    require_version,
)


TASK_GRAPH_SCHEMA_VERSION = "Natsume.agent.task_graph.v1"
TASK_NODE_SCHEMA_VERSION = "Natsume.agent.task_node.v1"
TASK_EVENT_SCHEMA_VERSION = "Natsume.agent.task_event.v1"

TASK_RETRY_POLICY_FIELDS = frozenset({"max_attempts", "retry_backoff_ms"})


class TaskGraphStatus(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskGraphCompletionPolicy(StrEnum):
    """Frozen whole-graph completion semantics.

    Absence of this compatible v1 extension preserves the historical
    node-terminal completion path.  The atomic policy is an explicit durable
    opt-in and must never be inferred from process-local feature flags.
    """

    LEGACY_NODE_TERMINAL_V1 = "LEGACY_NODE_TERMINAL_V1"
    ATOMIC_GRAPH_SUCCESS_V1 = "ATOMIC_GRAPH_SUCCESS_V1"


class TaskNodeStatus(StrEnum):
    CREATED = "CREATED"
    BLOCKED = "BLOCKED"
    READY = "READY"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    WAITING_ACCEPTANCE_REVIEW = "WAITING_ACCEPTANCE_REVIEW"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class EventActorType(StrEnum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    AGENT = "AGENT"
    WORKER = "WORKER"
    GOVERNOR = "GOVERNOR"
    REVIEWER = "REVIEWER"


class EventRedactionLevel(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"


_NODE_TRANSITIONS: dict[TaskNodeStatus, frozenset[TaskNodeStatus]] = {
    TaskNodeStatus.CREATED: frozenset(
        {TaskNodeStatus.BLOCKED, TaskNodeStatus.READY, TaskNodeStatus.CANCEL_REQUESTED}
    ),
    TaskNodeStatus.BLOCKED: frozenset({TaskNodeStatus.READY, TaskNodeStatus.CANCEL_REQUESTED}),
    TaskNodeStatus.READY: frozenset(
        {
            TaskNodeStatus.WAITING_APPROVAL,
            TaskNodeStatus.LEASED,
            TaskNodeStatus.CANCEL_REQUESTED,
        }
    ),
    TaskNodeStatus.WAITING_APPROVAL: frozenset(
        {TaskNodeStatus.READY, TaskNodeStatus.CANCELLED}
    ),
    TaskNodeStatus.LEASED: frozenset({TaskNodeStatus.RUNNING, TaskNodeStatus.CANCEL_REQUESTED}),
    TaskNodeStatus.RUNNING: frozenset(
        {
            TaskNodeStatus.EXECUTED,
            TaskNodeStatus.FAILED,
            TaskNodeStatus.RETRY_PENDING,
            TaskNodeStatus.CANCEL_REQUESTED,
        }
    ),
    TaskNodeStatus.EXECUTED: frozenset({TaskNodeStatus.VERIFYING}),
    TaskNodeStatus.VERIFYING: frozenset(
        {TaskNodeStatus.VERIFIED, TaskNodeStatus.VERIFICATION_FAILED}
    ),
    TaskNodeStatus.VERIFIED: frozenset(
        {TaskNodeStatus.ACCEPTED, TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW}
    ),
    TaskNodeStatus.VERIFICATION_FAILED: frozenset(),
    TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW: frozenset(
        {
            TaskNodeStatus.ACCEPTED,
            TaskNodeStatus.FAILED,
            TaskNodeStatus.RETRY_PENDING,
            TaskNodeStatus.CANCELLED,
        }
    ),
    TaskNodeStatus.ACCEPTED: frozenset(),
    TaskNodeStatus.FAILED: frozenset(),
    TaskNodeStatus.RETRY_PENDING: frozenset(
        {TaskNodeStatus.READY, TaskNodeStatus.CANCELLED}
    ),
    TaskNodeStatus.CANCEL_REQUESTED: frozenset({TaskNodeStatus.CANCELLED}),
    TaskNodeStatus.CANCELLED: frozenset(),
}


_GRAPH_TRANSITIONS: dict[TaskGraphStatus, frozenset[TaskGraphStatus]] = {
    TaskGraphStatus.CREATED: frozenset(
        {TaskGraphStatus.ACTIVE, TaskGraphStatus.BLOCKED, TaskGraphStatus.CANCELLED}
    ),
    TaskGraphStatus.ACTIVE: frozenset(
        {
            TaskGraphStatus.BLOCKED,
            TaskGraphStatus.COMPLETED,
            TaskGraphStatus.FAILED,
            TaskGraphStatus.CANCELLED,
        }
    ),
    TaskGraphStatus.BLOCKED: frozenset(
        {TaskGraphStatus.ACTIVE, TaskGraphStatus.FAILED, TaskGraphStatus.CANCELLED}
    ),
    TaskGraphStatus.COMPLETED: frozenset(),
    TaskGraphStatus.FAILED: frozenset(),
    TaskGraphStatus.CANCELLED: frozenset(),
}


def _enum_value(enum_type: type[StrEnum], value: Any, *, field_name: str) -> StrEnum:
    try:
        return enum_type(str(value or "").strip().upper())
    except ValueError as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"unsupported {field_name}: {value}") from exc


def _non_negative_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be at least {minimum}")
    return parsed


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def normalize_task_retry_policy(
    value: Any,
    *,
    field_name: str = "TaskNode.retry_policy",
) -> dict[str, int]:
    """Validate and freeze the canonical TaskNode retry-policy shape.

    ``retry_backoff_ms`` has a canonical zero default so v1 producers that
    only bounded ``max_attempts`` keep their existing meaning.  Legacy aliases
    are rejected instead of being silently interpreted at the execution
    boundary.
    """

    if not isinstance(value, Mapping) or not value:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be a non-empty object",
        )
    policy = dict(value)
    unknown = sorted(str(key) for key in set(policy) - TASK_RETRY_POLICY_FIELDS)
    if unknown:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} contains unsupported fields: {','.join(unknown)}",
        )

    max_attempts = policy.get("max_attempts")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name}.max_attempts must be an integer >= 1",
        )
    retry_backoff_ms = policy.get("retry_backoff_ms", 0)
    if (
        isinstance(retry_backoff_ms, bool)
        or not isinstance(retry_backoff_ms, int)
        or retry_backoff_ms < 0
    ):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name}.retry_backoff_ms must be an integer >= 0",
        )
    return {
        "max_attempts": max_attempts,
        "retry_backoff_ms": retry_backoff_ms,
    }


def validate_task_node_transition(
    current: TaskNodeStatus | str,
    target: TaskNodeStatus | str,
) -> TaskNodeStatus:
    try:
        current_status = TaskNodeStatus(str(current))
        target_status = TaskNodeStatus(str(target))
    except ValueError as exc:
        raise ContractViolation(TASK_TRANSITION_INVALID, "unknown TaskNode status") from exc
    if target_status not in _NODE_TRANSITIONS[current_status]:
        raise ContractViolation(
            TASK_TRANSITION_INVALID,
            f"illegal TaskNode transition: {current_status.value}->{target_status.value}",
        )
    return target_status


def validate_task_graph_transition(
    current: TaskGraphStatus | str,
    target: TaskGraphStatus | str,
) -> TaskGraphStatus:
    try:
        current_status = TaskGraphStatus(str(current))
        target_status = TaskGraphStatus(str(target))
    except ValueError as exc:
        raise ContractViolation(TASK_TRANSITION_INVALID, "unknown TaskGraph status") from exc
    if target_status not in _GRAPH_TRANSITIONS[current_status]:
        raise ContractViolation(
            TASK_TRANSITION_INVALID,
            f"illegal TaskGraph transition: {current_status.value}->{target_status.value}",
        )
    return target_status


def validate_task_graph_admission_state(graph: "TaskGraph") -> None:
    """Require the canonical CREATED DAG projection used for new admissions."""

    graph.validate()
    if graph.status is not TaskGraphStatus.CREATED or not graph.nodes:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "TaskGraph admission requires a non-empty CREATED graph",
        )
    invalid: list[str] = []
    for node in graph.nodes:
        expected = TaskNodeStatus.BLOCKED if node.dependencies else TaskNodeStatus.READY
        if node.status is not expected:
            invalid.append(f"{node.node_id}:{node.status.value}->{expected.value}")
    if invalid:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "TaskGraph admission node states are invalid: " + ",".join(invalid),
        )


@dataclass(frozen=True, slots=True)
class TaskNode:
    node_id: str
    dependencies: tuple[str, ...]
    route: str
    input_refs: tuple[str, ...]
    output_contract: dict[str, Any]
    retry_policy: dict[str, Any]
    risk_level: int
    acceptance_criteria: tuple[str, ...]
    status: TaskNodeStatus = TaskNodeStatus.CREATED
    status_version: int = 0
    schema_version: str = TASK_NODE_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TaskNode":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=TASK_NODE_SCHEMA_VERSION,
            contract_name="TaskNode",
        )
        node = cls(
            node_id=str(data.get("node_id") or "").strip(),
            dependencies=_text_tuple(data.get("dependencies")),
            route=str(data.get("route") or "").strip(),
            input_refs=_text_tuple(data.get("input_refs")),
            output_contract=(
                dict(data.get("output_contract") or {})
                if isinstance(data.get("output_contract"), Mapping)
                else {}
            ),
            retry_policy=normalize_task_retry_policy(data.get("retry_policy")),
            risk_level=_non_negative_int(data.get("risk_level", 1), field_name="risk_level", minimum=1),
            acceptance_criteria=_text_tuple(data.get("acceptance_criteria")),
            status=_enum_value(TaskNodeStatus, data.get("status") or "CREATED", field_name="TaskNode.status"),
            status_version=_non_negative_int(data.get("status_version", 0), field_name="status_version"),
        )
        node.validate()
        return node

    def validate(self) -> None:
        if self.schema_version != TASK_NODE_SCHEMA_VERSION:
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskNode schema version is invalid")
        if not self.node_id or not self.route:
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskNode.node_id and route are required")
        if self.node_id in self.dependencies:
            raise ContractViolation(TASK_GRAPH_INVALID, f"TaskNode cannot depend on itself: {self.node_id}")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ContractViolation(TASK_GRAPH_INVALID, f"TaskNode dependencies must be unique: {self.node_id}")
        if self.risk_level < 1 or self.risk_level > 5:
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskNode.risk_level must be between 1 and 5")
        normalize_task_retry_policy(self.retry_policy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "node_id": self.node_id,
            "dependencies": list(self.dependencies),
            "route": self.route,
            "input_refs": list(self.input_refs),
            "output_contract": deepcopy(self.output_contract),
            "retry_policy": normalize_task_retry_policy(self.retry_policy),
            "risk_level": self.risk_level,
            "acceptance_criteria": list(self.acceptance_criteria),
            "status": self.status.value,
            "status_version": self.status_version,
        }


@dataclass(frozen=True, slots=True)
class TaskGraph:
    graph_id: str
    root_work_order_id: str
    revision: int
    planner_ref: str
    status: TaskGraphStatus
    created_at: int
    nodes: tuple[TaskNode, ...] = ()
    schema_version: str = TASK_GRAPH_SCHEMA_VERSION
    result_node_id: str | None = None
    completion_policy: TaskGraphCompletionPolicy | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TaskGraph":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=TASK_GRAPH_SCHEMA_VERSION,
            contract_name="TaskGraph",
        )
        raw_nodes = data.get("nodes")
        nodes = tuple(
            TaskNode.from_mapping(item)
            for item in (raw_nodes if isinstance(raw_nodes, list) else [])
            if isinstance(item, Mapping)
        )
        graph = cls(
            graph_id=str(data.get("graph_id") or "").strip(),
            root_work_order_id=str(data.get("root_work_order_id") or "").strip(),
            revision=_non_negative_int(data.get("revision"), field_name="revision", minimum=1),
            planner_ref=str(data.get("planner_ref") or "").strip(),
            status=_enum_value(TaskGraphStatus, data.get("status") or "CREATED", field_name="TaskGraph.status"),
            created_at=_non_negative_int(data.get("created_at"), field_name="created_at"),
            result_node_id=(
                str(data.get("result_node_id") or "").strip()
                if data.get("result_node_id") is not None
                else None
            ),
            completion_policy=(
                _enum_value(
                    TaskGraphCompletionPolicy,
                    data.get("completion_policy"),
                    field_name="TaskGraph.completion_policy",
                )
                if data.get("completion_policy") is not None
                else None
            ),
            nodes=nodes,
        )
        graph.validate()
        return graph

    def validate(self) -> None:
        if self.schema_version != TASK_GRAPH_SCHEMA_VERSION:
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskGraph schema version is invalid")
        if not self.graph_id or not self.root_work_order_id or not self.planner_ref:
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "TaskGraph.graph_id, root_work_order_id, and planner_ref are required",
            )
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskGraph node_id values must be unique")
        known = set(node_ids)
        for node in self.nodes:
            node.validate()
            missing = sorted(set(node.dependencies) - known)
            if missing:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    f"TaskNode {node.node_id} has unknown dependencies: {','.join(missing)}",
                )
        completion_policy: TaskGraphCompletionPolicy | None = None
        if self.completion_policy is not None:
            try:
                completion_policy = TaskGraphCompletionPolicy(self.completion_policy)
            except (TypeError, ValueError) as exc:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "TaskGraph.completion_policy is invalid",
                ) from exc
        depended_on = {
            dependency
            for node in self.nodes
            for dependency in node.dependencies
        }
        sink_node_ids = tuple(
            node.node_id for node in self.nodes if node.node_id not in depended_on
        )
        if self.result_node_id is not None:
            result_node_id = str(self.result_node_id).strip()
            if not result_node_id or result_node_id not in known:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "TaskGraph.result_node_id must identify a node in the graph",
                )
            dependents = sorted(
                node.node_id
                for node in self.nodes
                if result_node_id in node.dependencies
            )
            if dependents:
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "TaskGraph.result_node_id must identify a sink node; "
                    f"dependents: {','.join(dependents)}",
                )
            if (
                completion_policy
                is not TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
                and len(sink_node_ids) > 1
            ):
                raise ContractViolation(
                    TASK_GRAPH_INVALID,
                    "legacy TaskGraph cannot declare one result authority across "
                    "multiple terminal sinks",
                )
        if (
            completion_policy is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
            and self.result_node_id is None
        ):
            raise ContractViolation(
                TASK_GRAPH_INVALID,
                "atomic Graph success requires an explicit result_node_id",
            )
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        dependencies = {node.node_id: tuple(node.dependencies) for node in self.nodes}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ContractViolation(PLAN_CYCLE, f"TaskGraph cycle detected at node: {node_id}")
            visiting.add(node_id)
            for dependency in dependencies.get(node_id, ()):
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in dependencies:
            visit(node_id)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "root_work_order_id": self.root_work_order_id,
            "revision": self.revision,
            "planner_ref": self.planner_ref,
            "status": self.status.value,
            "created_at": self.created_at,
            "nodes": [node.to_dict() for node in self.nodes],
        }
        # ``result_node_id`` is an optional compatible extension of the v1
        # wire shape.  Omitting ``None`` preserves byte-stable legacy journal
        # payloads and their existing digests/replay identities.
        if self.result_node_id is not None:
            payload["result_node_id"] = self.result_node_id
        if self.completion_policy is not None:
            payload["completion_policy"] = TaskGraphCompletionPolicy(
                self.completion_policy
            ).value
        return payload


@dataclass(frozen=True, slots=True)
class TaskEvent:
    event_id: str
    event_type: str
    ts_ms: int
    trace_id: str
    actor_type: EventActorType
    actor_id: str
    sequence_no: int
    cursor: str
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    work_order_id: str | None = None
    task_graph_id: str | None = None
    task_node_id: str | None = None
    attempt_id: str | None = None
    payload_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    redaction_level: EventRedactionLevel = EventRedactionLevel.INTERNAL
    schema_version: str = TASK_EVENT_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TaskEvent":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=TASK_EVENT_SCHEMA_VERSION,
            contract_name="TaskEvent",
        )

        def optional_text(key: str) -> str | None:
            value = str(data.get(key) or "").strip()
            return value or None

        event = cls(
            event_id=str(data.get("event_id") or "").strip(),
            event_type=str(data.get("event_type") or "").strip(),
            ts_ms=_non_negative_int(data.get("ts_ms"), field_name="ts_ms"),
            trace_id=str(data.get("trace_id") or "").strip(),
            actor_type=_enum_value(EventActorType, data.get("actor_type"), field_name="TaskEvent.actor_type"),
            actor_id=str(data.get("actor_id") or "").strip(),
            sequence_no=_non_negative_int(data.get("sequence_no"), field_name="sequence_no", minimum=1),
            cursor=str(data.get("cursor") or "").strip(),
            span_id=optional_text("span_id"),
            parent_span_id=optional_text("parent_span_id"),
            request_id=optional_text("request_id"),
            work_order_id=optional_text("work_order_id"),
            task_graph_id=optional_text("task_graph_id"),
            task_node_id=optional_text("task_node_id"),
            attempt_id=optional_text("attempt_id"),
            payload_ref=optional_text("payload_ref"),
            evidence_refs=_text_tuple(data.get("evidence_refs")),
            redaction_level=_enum_value(
                EventRedactionLevel,
                data.get("redaction_level") or "INTERNAL",
                field_name="TaskEvent.redaction_level",
            ),
        )
        event.validate()
        return event

    def validate(self) -> None:
        if self.schema_version != TASK_EVENT_SCHEMA_VERSION:
            raise ContractViolation(TASK_GRAPH_INVALID, "TaskEvent schema version is invalid")
        if not self.event_id or not self.event_type or not self.trace_id or not self.actor_id or not self.cursor:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "TaskEvent event_id, event_type, trace_id, actor_id, and cursor are required",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "ts_ms": self.ts_ms,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "request_id": self.request_id,
            "work_order_id": self.work_order_id,
            "task_graph_id": self.task_graph_id,
            "task_node_id": self.task_node_id,
            "attempt_id": self.attempt_id,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "payload_ref": self.payload_ref,
            "evidence_refs": list(self.evidence_refs),
            "redaction_level": self.redaction_level.value,
            "sequence_no": self.sequence_no,
            "cursor": self.cursor,
        }


__all__ = [
    "TASK_EVENT_SCHEMA_VERSION",
    "TASK_GRAPH_SCHEMA_VERSION",
    "TASK_NODE_SCHEMA_VERSION",
    "TASK_RETRY_POLICY_FIELDS",
    "EventActorType",
    "EventRedactionLevel",
    "TaskEvent",
    "TaskGraph",
    "TaskGraphCompletionPolicy",
    "TaskGraphStatus",
    "TaskNode",
    "TaskNodeStatus",
    "normalize_task_retry_policy",
    "validate_task_graph_transition",
    "validate_task_graph_admission_state",
    "validate_task_node_transition",
]
