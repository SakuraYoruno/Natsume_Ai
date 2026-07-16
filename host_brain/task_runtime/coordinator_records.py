"""Pure record codecs and value objects used by the governed coordinator.

This module owns no runtime ports, locks, tasks, or Coordinator reference.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .admission import AdmittedExecution
from .errors import (
    GOVERNED_ASSIGNMENT_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)

if TYPE_CHECKING:
    from .review_runtime import TaskNodeReviewGate


@dataclass(frozen=True, slots=True)
class _ParallelNodeOutcome:
    node_id: str
    attempt_id: str | None
    status: str
    accepted: bool
    reason_code: str | None
    event: dict[str, Any]

    @classmethod
    def from_terminal_event(
        cls,
        *,
        node_id: str,
        event: Mapping[str, Any],
    ) -> "_ParallelNodeOutcome":
        receipt = event.get("execution_receipt")
        if not isinstance(receipt, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} has no execution receipt",
            )
        receipt_node_id = _text(receipt.get("node_id"))
        status = _text(receipt.get("status")).upper()
        raw_attempt_id = receipt.get("attempt_id")
        attempt_id = _text(raw_attempt_id) or None
        accepted = receipt.get("accepted")
        if (
            receipt_node_id != node_id
            or not status
            or not isinstance(accepted, bool)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} returned an invalid terminal identity",
            )
        if status == "WAITING_APPROVAL":
            if raw_attempt_id is not None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {node_id} pre-execution review bound a TaskAttempt",
                )
        elif attempt_id is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} terminal identity has no TaskAttempt",
            )
        if accepted != (status == "ACCEPTED"):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} returned an inconsistent acceptance status",
            )
        return cls(
            node_id=node_id,
            attempt_id=attempt_id,
            status=status,
            accepted=accepted,
            reason_code=_text(receipt.get("reason_code")) or None,
            event=dict(event),
        )

def _assert_durable_identity(
    admitted: AdmittedExecution,
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
) -> None:
    graph_payload = _nested_mapping(graph, "graph")
    durable_graph_id = _text(graph.get("graph_id") or graph_payload.get("graph_id"))
    durable_revision = int(graph.get("revision") or graph_payload.get("revision") or 0)
    durable_root = _text(graph.get("root_work_order_id") or graph_payload.get("root_work_order_id"))
    raw_durable_result_node_id = graph_payload.get("result_node_id")
    durable_result_node_id = (
        _text(raw_durable_result_node_id)
        if raw_durable_result_node_id is not None
        else None
    )
    raw_completion_policy = graph_payload.get("completion_policy")
    durable_completion_policy = (
        _text(raw_completion_policy)
        if raw_completion_policy is not None
        else None
    )
    node_payload = _nested_mapping(node, "node")
    durable_node_id = _text(node.get("node_id") or node_payload.get("node_id"))
    durable_route = _text(node.get("route") or node_payload.get("route"))
    raw_dependencies = node_payload.get("dependencies")
    durable_dependencies = tuple(
        _text(item)
        for item in (raw_dependencies if isinstance(raw_dependencies, list) else ())
        if _text(item)
    )
    if (
        durable_graph_id != admitted.graph_id
        or durable_revision != admitted.graph_revision
        or durable_root != admitted.work_order_id
        or durable_result_node_id != admitted.result_node_id
        or durable_completion_policy != admitted.completion_policy
        or durable_node_id != admitted.node_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable graph/node identity differs from admitted command",
        )
    if durable_route != admitted.worker_route:
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "durable TaskNode.route differs from the frozen worker route",
        )
    if durable_dependencies != admitted.dependencies:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable TaskNode.dependencies differ from the frozen DAG",
        )

def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        payload = serializer()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"{field_name} must be a mapping")

def _nested_mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    return dict(nested) if isinstance(nested, Mapping) else {}

def _node_input_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    raw = _nested_mapping(node, "node").get("input_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))

def _record_evidence_refs(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw = record.get("evidence_refs") if isinstance(record, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        raw = _nested_mapping(record, "node").get("evidence_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))

def _review_evidence_refs(gate: "TaskNodeReviewGate") -> tuple[str, ...]:
    refs: list[str] = []
    if _text(gate.review_id):
        refs.append(f"review://{_text(gate.review_id)}")
    if _text(gate.checkpoint_id):
        refs.append(_text(gate.checkpoint_id))
    return tuple(dict.fromkeys(refs))

def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()

def _status(record: Mapping[str, Any]) -> str:
    if not isinstance(record, Mapping):
        return ""
    nested = record.get("node") if isinstance(record.get("node"), Mapping) else record.get("graph")
    return _text(record.get("status") or (nested or {}).get("status")).upper()

def _version(record: Mapping[str, Any]) -> int:
    value = record.get("status_version") if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable record lacks an exact non-negative status_version",
        )
    return value

def _required_record_text(record: Mapping[str, Any], key: str) -> str:
    value = _text(record.get(key)) if isinstance(record, Mapping) else ""
    if not value:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} is required")
    return value

def _record_positive_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} must be positive")
    return value

def _record_non_negative_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"durable {key} must be a non-negative integer",
        )
    return value

def _optional_record_non_negative_int(
    record: Mapping[str, Any],
    key: str,
) -> int | None:
    value = record.get(key) if isinstance(record, Mapping) else None
    if value is None:
        return None
    return _record_non_negative_int(record, key)

def _reservation_result(value: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(value, Mapping):
        acquired = value.get("acquired")
        reservation = value.get("reservation")
    else:
        acquired = getattr(value, "acquired", None)
        reservation = getattr(value, "reservation", None)
    if not isinstance(acquired, bool) or not isinstance(reservation, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "reserve_operation returned an invalid result",
        )
    return acquired, dict(reservation)

def _evidence_refs(value: Mapping[str, Any]) -> tuple[str, ...]:
    raw = value.get("evidence_refs") if isinstance(value, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _attempt_id(admitted: AdmittedExecution, *, attempt_no: int) -> str:
    material = (
        f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}/"
        f"{admitted.assignment_id}/{int(attempt_no)}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _exception_reason(exc: BaseException, default: str) -> str:
    return (
        _text(getattr(exc, "reason_code", None))
        or _text(default)
        or exc.__class__.__name__.upper()
    )

__all__ = [
    "_ParallelNodeOutcome",
    "_attempt_id",
    "_assert_durable_identity",
    "_evidence_refs",
    "_exception_reason",
    "_mapping",
    "_nested_mapping",
    "_node_input_refs",
    "_optional_record_non_negative_int",
    "_record_evidence_refs",
    "_record_non_negative_int",
    "_record_positive_int",
    "_required_record_text",
    "_reservation_result",
    "_review_evidence_refs",
    "_status",
    "_text",
    "_version",
]
