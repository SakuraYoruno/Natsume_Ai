"""Canonical contract for one durable parallel terminal decision."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence


PARALLEL_SETTLEMENT_SCHEMA_VERSION = (
    "Natsume.task_runtime.parallel_settlement.v2"
)
MAX_PARALLEL_SETTLEMENT_PAYLOAD_BYTES = 262_144

_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "decision_kind",
        "selected_node_id",
        "graph_id",
        "graph_revision",
        "graph_status_before",
        "graph_status_version_before",
        "graph_target_status",
        "budget_id",
        "budget_status_before",
        "budget_status_version_before",
        "budget_target_status",
        "node_states",
        "outcomes",
        "graph_outbox_id",
        "budget_outbox_id",
    }
)
_OUTCOME_FIELDS = frozenset(
    {
        "node_id",
        "attempt_id",
        "status",
        "accepted",
        "reason_code",
        "evidence_refs",
    }
)
_FAILURE_OUTCOMES = frozenset({"FAILED", "VERIFICATION_FAILED", "CANCELLED"})
_REVIEW_OUTCOMES = frozenset({"WAITING_ACCEPTANCE_REVIEW"})
_ALLOWED_OUTCOMES = frozenset(
    {
        "ACCEPTED",
        "BUDGET_BLOCKED",
        "RETRY_PENDING",
        *_FAILURE_OUTCOMES,
        *_REVIEW_OUTCOMES,
    }
)


class ParallelSettlementContractError(ValueError):
    """Raised when a decision is not canonical or semantically coherent."""


@dataclass(frozen=True, slots=True)
class CanonicalParallelSettlementDecision:
    payload: dict[str, Any]
    payload_json: str
    payload_sha256: str
    decision_ref: str


def _text(value: Any, *, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ParallelSettlementContractError(f"{field_name} is required")
    return result


def _non_negative_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ParallelSettlementContractError(
            f"{field_name} must be an integer >= {minimum}"
        )
    return value


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ParallelSettlementContractError("non-finite JSON number is forbidden")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(key)
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def _normalize_node_states(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise ParallelSettlementContractError("node_states must be a non-empty object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_state in value.items():
        node_id = _text(raw_node_id, field_name="node_states.node_id")
        if not isinstance(raw_state, Mapping) or set(raw_state) != {
            "status",
            "status_version",
        }:
            raise ParallelSettlementContractError(
                f"node_states.{node_id} must contain status and status_version"
            )
        normalized[node_id] = {
            "status": _text(raw_state.get("status"), field_name=f"{node_id}.status"),
            "status_version": _non_negative_int(
                raw_state.get("status_version"),
                field_name=f"{node_id}.status_version",
            ),
        }
    return {node_id: normalized[node_id] for node_id in sorted(normalized)}


def _normalize_outcomes(
    value: Any,
    *,
    node_states: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ParallelSettlementContractError("outcomes must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _OUTCOME_FIELDS:
            raise ParallelSettlementContractError(
                "each outcome must contain only the canonical outcome fields"
            )
        node_id = _text(raw.get("node_id"), field_name="outcome.node_id")
        if node_id in seen or node_id not in node_states:
            raise ParallelSettlementContractError(
                f"outcome node is duplicate or unknown: {node_id}"
            )
        seen.add(node_id)
        status = _text(raw.get("status"), field_name=f"outcome.{node_id}.status").upper()
        if status not in _ALLOWED_OUTCOMES:
            raise ParallelSettlementContractError(
                f"unsupported parallel outcome status: {status}"
            )
        accepted = raw.get("accepted")
        if not isinstance(accepted, bool) or accepted != (status == "ACCEPTED"):
            raise ParallelSettlementContractError(
                f"outcome accepted flag conflicts with status: {node_id}"
            )
        durable_status = str(node_states[node_id].get("status") or "").strip().upper()
        expected_durable = "FAILED" if status == "BUDGET_BLOCKED" else status
        if durable_status != expected_durable:
            raise ParallelSettlementContractError(
                f"outcome status conflicts with node snapshot: {node_id}"
            )
        raw_reason = raw.get("reason_code")
        reason_code = None if raw_reason is None else _text(
            raw_reason,
            field_name=f"outcome.{node_id}.reason_code",
        )
        raw_refs = raw.get("evidence_refs")
        if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes)):
            raise ParallelSettlementContractError(
                f"outcome evidence_refs must be an array: {node_id}"
            )
        evidence_refs = sorted(
            {
                _text(item, field_name=f"outcome.{node_id}.evidence_ref")
                for item in raw_refs
            }
        )
        normalized.append(
            {
                "node_id": node_id,
                "attempt_id": _text(
                    raw.get("attempt_id"),
                    field_name=f"outcome.{node_id}.attempt_id",
                ),
                "status": status,
                "accepted": accepted,
                "reason_code": reason_code,
                "evidence_refs": evidence_refs,
            }
        )
    normalized.sort(key=lambda item: item["node_id"])
    return normalized


def normalize_parallel_settlement_decision(
    payload: Mapping[str, Any],
) -> CanonicalParallelSettlementDecision:
    """Validate, normalize, and digest a complete settlement decision."""

    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
        raise ParallelSettlementContractError(
            "parallel settlement payload has missing or unknown fields"
        )
    _assert_finite(payload)
    schema_version = _text(payload.get("schema_version"), field_name="schema_version")
    if schema_version != PARALLEL_SETTLEMENT_SCHEMA_VERSION:
        raise ParallelSettlementContractError("parallel settlement schema version is invalid")
    decision_kind = _text(payload.get("decision_kind"), field_name="decision_kind").upper()
    if decision_kind not in {"BUDGET", "FAILURE", "REVIEW"}:
        raise ParallelSettlementContractError("parallel settlement decision_kind is invalid")
    graph_status_before = _text(
        payload.get("graph_status_before"), field_name="graph_status_before"
    ).upper()
    graph_target_status = _text(
        payload.get("graph_target_status"), field_name="graph_target_status"
    ).upper()
    budget_status_before = _text(
        payload.get("budget_status_before"), field_name="budget_status_before"
    ).upper()
    raw_budget_target = payload.get("budget_target_status")
    budget_target_status = (
        None
        if raw_budget_target is None
        else _text(raw_budget_target, field_name="budget_target_status").upper()
    )
    if graph_status_before != "ACTIVE" or graph_target_status not in {"FAILED", "BLOCKED"}:
        raise ParallelSettlementContractError("parallel settlement Graph states are invalid")
    if budget_status_before not in {"ACTIVE", "EXCEEDED"}:
        raise ParallelSettlementContractError("parallel settlement Budget source is invalid")
    if budget_target_status not in {None, "RELEASED"}:
        raise ParallelSettlementContractError("parallel settlement Budget target is invalid")

    node_states = _normalize_node_states(payload.get("node_states"))
    outcomes = _normalize_outcomes(payload.get("outcomes"), node_states=node_states)
    selected_node_id = _text(
        payload.get("selected_node_id"), field_name="selected_node_id"
    )
    selected = next(
        (item for item in outcomes if item["node_id"] == selected_node_id),
        None,
    )
    if selected is None:
        raise ParallelSettlementContractError("selected_node_id is not present in outcomes")
    outcome_statuses = {str(item["status"]) for item in outcomes}
    if decision_kind == "BUDGET":
        if (
            budget_status_before != "EXCEEDED"
            or graph_target_status != "BLOCKED"
            or budget_target_status != "RELEASED"
            or selected["status"] != "BUDGET_BLOCKED"
            or bool(selected["accepted"])
        ):
            raise ParallelSettlementContractError("Budget decision states are invalid")
    elif decision_kind == "FAILURE":
        if (
            selected["status"] not in _FAILURE_OUTCOMES
            or not outcome_statuses.intersection(_FAILURE_OUTCOMES)
            or budget_status_before != "ACTIVE"
            or budget_target_status != "RELEASED"
        ):
            raise ParallelSettlementContractError("failure decision states are invalid")
    elif (
        selected["status"] not in _REVIEW_OUTCOMES
        or not outcome_statuses.intersection(_REVIEW_OUTCOMES)
        or outcome_statuses.intersection(_FAILURE_OUTCOMES)
        or budget_status_before != "ACTIVE"
        or graph_target_status != "BLOCKED"
        or budget_target_status is not None
    ):
        raise ParallelSettlementContractError("review decision states are invalid")

    graph_outbox_id = _text(payload.get("graph_outbox_id"), field_name="graph_outbox_id")
    raw_budget_outbox_id = payload.get("budget_outbox_id")
    budget_outbox_id = (
        None
        if raw_budget_outbox_id is None
        else _text(raw_budget_outbox_id, field_name="budget_outbox_id")
    )
    if (budget_target_status is None) != (budget_outbox_id is None):
        raise ParallelSettlementContractError(
            "budget_outbox_id is required exactly when Budget is released"
        )

    normalized = {
        "schema_version": schema_version,
        "decision_kind": decision_kind,
        "selected_node_id": selected_node_id,
        "graph_id": _text(payload.get("graph_id"), field_name="graph_id"),
        "graph_revision": _non_negative_int(
            payload.get("graph_revision"), field_name="graph_revision", minimum=1
        ),
        "graph_status_before": graph_status_before,
        "graph_status_version_before": _non_negative_int(
            payload.get("graph_status_version_before"),
            field_name="graph_status_version_before",
        ),
        "graph_target_status": graph_target_status,
        "budget_id": _text(payload.get("budget_id"), field_name="budget_id"),
        "budget_status_before": budget_status_before,
        "budget_status_version_before": _non_negative_int(
            payload.get("budget_status_version_before"),
            field_name="budget_status_version_before",
        ),
        "budget_target_status": budget_target_status,
        "node_states": node_states,
        "outcomes": outcomes,
        "graph_outbox_id": graph_outbox_id,
        "budget_outbox_id": budget_outbox_id,
    }
    payload_json = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(payload_json.encode("utf-8")) > MAX_PARALLEL_SETTLEMENT_PAYLOAD_BYTES:
        raise ParallelSettlementContractError("parallel settlement payload is too large")
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return CanonicalParallelSettlementDecision(
        payload=normalized,
        payload_json=payload_json,
        payload_sha256=f"sha256:{digest}",
        decision_ref=f"taskbatch://sha256/{digest}",
    )


__all__ = [
    "CanonicalParallelSettlementDecision",
    "MAX_PARALLEL_SETTLEMENT_PAYLOAD_BYTES",
    "PARALLEL_SETTLEMENT_SCHEMA_VERSION",
    "ParallelSettlementContractError",
    "normalize_parallel_settlement_decision",
]
