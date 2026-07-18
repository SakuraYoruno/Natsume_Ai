"""Explicit durable failure-replanning entry for governed PlanningSearch.

This module is deliberately separate from the execution loop.  It can prepare
one replacement revision only after loading a canonical durable, quiescent
``FAILED`` graph snapshot and the state-event references that prove that
failure.  It never executes the replacement graph and exposes no worker
callback that could perform an implicit execution-time replan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import inspect
import re
from typing import Any

from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskGraphStatus
from host_brain.orchestration.planning_execution import (
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
    PreparedPlanningExecution,
)
from host_brain.orchestration.planning_graph_compiler import PlanningCompilationContext
from host_brain.persistence.task_repository import build_outbox_payload_ref
from host_brain.planning_search.contracts import SearchRequest


PLANNING_RECOVERY_INVALID = "PLANNING_RECOVERY_INVALID"
PLANNING_RECOVERY_NOT_REPLANNABLE = "PLANNING_RECOVERY_NOT_REPLANNABLE"
PLANNING_RECOVERY_DURABLE_DRIFT = "PLANNING_RECOVERY_DURABLE_DRIFT"

_REPLANNABLE_GRAPH_STATUSES = frozenset({TaskGraphStatus.FAILED})
_FAILURE_NODE_STATUSES = frozenset(
    {
        "FAILED",
        "VERIFICATION_FAILED",
    }
)
_FAILURE_ATTEMPT_STATUSES = frozenset({"FAILED"})
_CONTINUABLE_NODE_STATUSES = frozenset(
    {
        "WAITING_APPROVAL",
        "LEASED",
        "RUNNING",
        "EXECUTED",
        "VERIFYING",
        "VERIFIED",
        "WAITING_ACCEPTANCE_REVIEW",
        "RETRY_PENDING",
        "CANCEL_REQUESTED",
    }
)
_NON_TERMINAL_ATTEMPT_STATUSES = frozenset(
    {"CREATED", "LEASED", "RUNNING", "RECOVERABLE", "CANCEL_REQUESTED"}
)
_TERMINAL_ATTEMPT_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
_DURABLE_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,999}$"
)
_MAX_FAILURE_EVIDENCE_REFS = 64


class PlanningRecoveryError(RuntimeError):
    """Fail-closed error raised before a replacement revision is admitted."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or PLANNING_RECOVERY_INVALID)
        self.detail = str(detail or "governed planning recovery failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class DurablePlanningFailure:
    """One validated durable failure snapshot and its immutable evidence refs."""

    previous_graph: TaskGraph
    graph_status_version: int
    failure_evidence_refs: tuple[str, ...]

    def validate(self) -> None:
        self.previous_graph.validate()
        if self.previous_graph.status not in _REPLANNABLE_GRAPH_STATUSES:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_NOT_REPLANNABLE,
                "durable previous graph must be FAILED",
            )
        if self.graph_status_version < 1:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "FAILED graph has no durable state-transition version",
            )
        if not self.failure_evidence_refs:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable failure snapshot contains no failure evidence",
            )
        if len(self.failure_evidence_refs) > _MAX_FAILURE_EVIDENCE_REFS:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable failure snapshot exceeds the bounded evidence set",
            )
        if len(set(self.failure_evidence_refs)) != len(self.failure_evidence_refs):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable failure evidence refs must be unique",
            )
        if any(not _DURABLE_REF_RE.fullmatch(ref) for ref in self.failure_evidence_refs):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable failure evidence contains an invalid reference",
            )


class DurablePlanningFailureSource:
    """Load one consistent graph/attempt snapshot from the canonical TaskStore."""

    def __init__(self, task_store: Any) -> None:
        if not callable(getattr(task_store, "read_scheduler_snapshot", None)):
            raise ValueError("task_store.read_scheduler_snapshot is required")
        self._store = task_store

    async def load(
        self,
        *,
        graph_id: str,
        previous_revision: int,
    ) -> DurablePlanningFailure:
        graph_ref = str(graph_id or "").strip()
        revision = _positive_int(previous_revision, field_name="previous_revision")
        if not graph_ref:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_INVALID,
                "graph_id is required for failure replanning",
            )
        try:
            raw = self._store.read_scheduler_snapshot(
                graph_ref,
                revision,
                include_graph_outboxes=True,
            )
            snapshot = await raw if inspect.isawaitable(raw) else raw
        except PlanningRecoveryError:
            raise
        except Exception as exc:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                f"failed to load canonical TaskStore snapshot: {exc}",
            ) from exc
        if not isinstance(snapshot, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "TaskStore returned a non-mapping recovery snapshot",
            )

        graph_record = snapshot.get("graph_record")
        if not isinstance(graph_record, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "recovery snapshot is missing graph_record",
            )
        previous_graph = _load_graph(
            graph_record,
            expected_graph_id=graph_ref,
            expected_revision=revision,
        )
        status_version = _non_negative_int(
            graph_record.get("status_version"),
            field_name="graph_record.status_version",
        )
        if previous_graph.status not in _REPLANNABLE_GRAPH_STATUSES:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_NOT_REPLANNABLE,
                "explicit replan rejected: durable graph status is "
                f"{previous_graph.status.value}, expected FAILED",
            )

        _validate_quiescent_failed_revision(
            graph_record=graph_record,
            attempts=snapshot.get("attempts"),
            active_leases=snapshot.get("active_leases"),
            graph=previous_graph,
        )

        evidence_refs = _failure_evidence_refs(
            graph_record=graph_record,
            attempts=snapshot.get("attempts"),
            graph_outboxes=snapshot.get("graph_outboxes"),
            graph=previous_graph,
            graph_status_version=status_version,
        )
        failure = DurablePlanningFailure(
            previous_graph=previous_graph,
            graph_status_version=status_version,
            failure_evidence_refs=evidence_refs,
        )
        failure.validate()
        return failure


class GovernedPlanningRecoveryService:
    """Prepare, but never execute, one explicit replacement graph revision."""

    def __init__(
        self,
        *,
        execution_service: GovernedPlanningExecutionService,
        task_store: Any,
    ) -> None:
        if not isinstance(execution_service, GovernedPlanningExecutionService):
            raise ValueError(
                "execution_service must be GovernedPlanningExecutionService"
            )
        self._execution = execution_service
        self._failures = DurablePlanningFailureSource(task_store)

    async def prepare_failure_replan(
        self,
        request: SearchRequest,
        *,
        authority: PlanningExecutionAuthority,
        graph_id: str,
        previous_revision: int,
        created_at: int,
        compilation_context: PlanningCompilationContext | None = None,
    ) -> PreparedPlanningExecution:
        """Admit revision ``previous + 1`` after an explicit recovery request.

        The method intentionally stops after durable admission.  The caller must
        make a separate governed decision to pass the returned preparation to
        ``GovernedPlanningExecutionService.execute_stream``.
        """

        failure = await self._failures.load(
            graph_id=graph_id,
            previous_revision=previous_revision,
        )
        created = _non_negative_int(created_at, field_name="created_at")
        if created < failure.previous_graph.created_at:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_INVALID,
                "replacement revision cannot predate the previous durable graph",
            )

        previous_snapshot = failure.previous_graph.to_dict()
        prepared = await self._execution.prepare_replan(
            request,
            authority=authority,
            previous_graph=failure.previous_graph,
            failure_evidence_refs=failure.failure_evidence_refs,
            created_at=created,
            compilation_context=compilation_context,
        )
        prepared.validate()
        replacement = prepared.admission.graph
        previous = failure.previous_graph
        if previous.to_dict() != previous_snapshot:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "previous durable graph was mutated during recovery planning",
            )
        if (
            replacement.graph_id != previous.graph_id
            or replacement.root_work_order_id != previous.root_work_order_id
            or replacement.revision != previous.revision + 1
            or prepared.compiled.previous_revision != previous.revision
            or prepared.compiled.failure_evidence_refs
            != failure.failure_evidence_refs
        ):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "replacement admission broke graph/root/revision/evidence lineage",
            )
        return prepared


def _load_graph(
    graph_record: Mapping[str, Any],
    *,
    expected_graph_id: str,
    expected_revision: int,
) -> TaskGraph:
    payload = graph_record.get("graph")
    if not isinstance(payload, Mapping):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "graph_record.graph is not a canonical TaskGraph mapping",
        )
    try:
        graph = TaskGraph.from_mapping(payload)
    except Exception as exc:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            f"durable previous TaskGraph violates its contract: {exc}",
        ) from exc
    record_identity = (
        str(graph_record.get("graph_id") or "").strip(),
        _positive_int(graph_record.get("revision"), field_name="graph_record.revision"),
        str(graph_record.get("root_work_order_id") or "").strip(),
        str(graph_record.get("status") or "").strip(),
        str(graph_record.get("source_kind") or "").strip(),
    )
    expected_identity = (
        expected_graph_id,
        expected_revision,
        graph.root_work_order_id,
        graph.status.value,
        "CANONICAL",
    )
    if record_identity != expected_identity or (
        graph.graph_id != expected_graph_id or graph.revision != expected_revision
    ):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "TaskStore SQL identity differs from the canonical previous TaskGraph",
        )
    return graph


def _failure_evidence_refs(
    *,
    graph_record: Mapping[str, Any],
    attempts: Any,
    graph_outboxes: Any = None,
    graph: TaskGraph,
    graph_status_version: int,
) -> tuple[str, ...]:
    if graph_status_version < 1:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "FAILED graph lacks a persisted state transition",
        )
    refs: list[str] = []

    def add(ref: str) -> None:
        normalized = str(ref or "").strip()
        if normalized and normalized not in refs:
            refs.append(normalized)

    if graph_outboxes is None:
        add(
            build_outbox_payload_ref(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph.graph_id,
                aggregate_version=graph_status_version,
                graph_revision=graph.revision,
            )
        )
    else:
        if not isinstance(graph_outboxes, Sequence) or isinstance(
            graph_outboxes, (str, bytes)
        ):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot graph_outboxes must be a sequence",
            )
        terminal_event = f"TASK_GRAPH_{graph.status.value}"
        matching_graph_outboxes = [
            item
            for item in graph_outboxes
            if isinstance(item, Mapping)
            and str(item.get("aggregate_type") or "") == "TASK_GRAPH"
            and str(item.get("aggregate_id") or "") == graph.graph_id
            and int(item.get("aggregate_version", -1)) == graph_status_version
            and str(item.get("event_type") or "") == terminal_event
            and item.get("graph_revision") in (None, graph.revision)
        ]
        if len(matching_graph_outboxes) != 1:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable terminal Graph transition lacks one exact Outbox evidence identity",
            )
        add(str(matching_graph_outboxes[0].get("payload_ref") or ""))

    raw_nodes = graph_record.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable recovery snapshot nodes must be a sequence",
        )
    node_records: list[Mapping[str, Any]] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot contains a non-mapping node",
            )
        if (
            str(raw_node.get("graph_id") or "") != graph.graph_id
            or _positive_int(
                raw_node.get("graph_revision"),
                field_name="node.graph_revision",
            )
            != graph.revision
        ):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery node belongs to another graph revision",
            )
        node_records.append(raw_node)
    for node in sorted(node_records, key=lambda item: str(item.get("node_id") or "")):
        status = str(node.get("status") or "").strip()
        version = _non_negative_int(
            node.get("status_version"),
            field_name="node.status_version",
        )
        node_id = str(node.get("node_id") or "").strip()
        if status in _FAILURE_NODE_STATUSES and version > 0 and node_id:
            add(
                build_outbox_payload_ref(
                    aggregate_type="TASK_NODE",
                    aggregate_id=f"{graph.graph_id}@{graph.revision}/{node_id}",
                    aggregate_version=version,
                )
            )

    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable recovery snapshot attempts must be a sequence",
        )
    attempt_records: list[Mapping[str, Any]] = []
    for raw_attempt in attempts:
        if not isinstance(raw_attempt, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot contains a non-mapping attempt",
            )
        if (
            str(raw_attempt.get("graph_id") or "") != graph.graph_id
            or _positive_int(
                raw_attempt.get("graph_revision"),
                field_name="attempt.graph_revision",
            )
            != graph.revision
        ):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery attempt belongs to another graph revision",
            )
        attempt_records.append(raw_attempt)
    for attempt in sorted(
        attempt_records,
        key=lambda item: str(item.get("attempt_id") or ""),
    ):
        status = str(attempt.get("status") or "").strip()
        version = _non_negative_int(
            attempt.get("status_version"),
            field_name="attempt.status_version",
        )
        attempt_id = str(attempt.get("attempt_id") or "").strip()
        if status not in _FAILURE_ATTEMPT_STATUSES or version < 1 or not attempt_id:
            continue
        add(
            build_outbox_payload_ref(
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=version,
            )
        )
        error_ref = str(attempt.get("error_ref") or "").strip()
        if error_ref and _DURABLE_REF_RE.fullmatch(error_ref):
            add(error_ref)

    if len(refs) > _MAX_FAILURE_EVIDENCE_REFS:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable failure evidence exceeds the bounded recovery set",
        )
    return tuple(refs)


def _validate_quiescent_failed_revision(
    *,
    graph_record: Mapping[str, Any],
    attempts: Any,
    active_leases: Any,
    graph: TaskGraph,
) -> None:
    """Reject a predecessor that can still advance through an older runtime path.

    This is a fail-closed stopgap, not durable revision supersession.  CREATED,
    BLOCKED and READY nodes remain valid because a terminal FAILED graph prevents
    ordinary scheduler claims; terminal ACCEPTED/FAILED/CANCELLED nodes and
    terminal Attempts are also valid.  States with an independent continuation
    path are rejected before PlanningSearch or admission can run.
    """

    raw_nodes = graph_record.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable recovery snapshot nodes must be a sequence",
        )
    canonical_node_statuses = {
        node.node_id: node.status.value for node in graph.nodes
    }
    observed_node_ids: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot contains a non-mapping node",
            )
        node_id = str(raw_node.get("node_id") or "").strip()
        status = str(raw_node.get("status") or "").strip()
        if (
            node_id in observed_node_ids
            or canonical_node_statuses.get(node_id) != status
        ):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable SQL node status differs from the canonical TaskGraph",
            )
        observed_node_ids.add(node_id)
        if status in _CONTINUABLE_NODE_STATUSES:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_NOT_REPLANNABLE,
                "explicit replan rejected: predecessor node "
                f"{node_id} remains continuable in status {status}",
            )
    if observed_node_ids != set(canonical_node_statuses):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable SQL node set differs from the canonical TaskGraph",
        )

    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable recovery snapshot attempts must be a sequence",
        )
    for raw_attempt in attempts:
        if not isinstance(raw_attempt, Mapping):
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot contains a non-mapping attempt",
            )
        status = str(raw_attempt.get("status") or "").strip()
        if status in _NON_TERMINAL_ATTEMPT_STATUSES:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_NOT_REPLANNABLE,
                "explicit replan rejected: predecessor Attempt "
                f"{raw_attempt.get('attempt_id')!s} is non-terminal in status {status}",
            )
        if status not in _TERMINAL_ATTEMPT_STATUSES:
            raise PlanningRecoveryError(
                PLANNING_RECOVERY_DURABLE_DRIFT,
                "durable recovery snapshot contains an unknown Attempt status",
            )

    if not isinstance(active_leases, Sequence) or isinstance(
        active_leases, (str, bytes)
    ):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            "durable recovery snapshot active_leases must be a sequence",
        )
    if active_leases:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_NOT_REPLANNABLE,
            "explicit replan rejected: predecessor revision retains an active lease",
        )


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            f"{field_name} must be an integer",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            f"{field_name} must be an integer",
        ) from exc
    if parsed < 0:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_DURABLE_DRIFT,
            f"{field_name} must be non-negative",
        )
    return parsed


def _positive_int(value: Any, *, field_name: str) -> int:
    parsed = _non_negative_int(value, field_name=field_name)
    if parsed < 1:
        raise PlanningRecoveryError(
            PLANNING_RECOVERY_INVALID,
            f"{field_name} must be at least 1",
        )
    return parsed


__all__ = [
    "DurablePlanningFailure",
    "DurablePlanningFailureSource",
    "GovernedPlanningRecoveryService",
    "PLANNING_RECOVERY_DURABLE_DRIFT",
    "PLANNING_RECOVERY_INVALID",
    "PLANNING_RECOVERY_NOT_REPLANNABLE",
    "PlanningRecoveryError",
]
