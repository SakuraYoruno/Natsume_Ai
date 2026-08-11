"""Canonical contract for one durable whole-graph success decision.

The user-visible result remains bound to one explicit result node.  Deferred
graph effects may belong to any accepted node, but each must bind the exact
Attempt and receipt already frozen in ``accepted_outcomes``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy


PARALLEL_SUCCESS_SCHEMA_VERSION = "Natsume.task_runtime.parallel_success.v1"
MAX_PARALLEL_SUCCESS_PAYLOAD_BYTES = 262_144
MAX_PARALLEL_SUCCESS_EFFECTS = 30

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "graph_id",
        "graph_revision",
        "graph_status_before",
        "graph_status_version_before",
        "graph_target_status",
        "completion_policy",
        "budget_id",
        "budget_status_before",
        "budget_status_version_before",
        "budget_target_status",
        "result_node_id",
        "result_attempt_id",
        "result_receipt_ref",
        "node_states",
        "accepted_outcomes",
        "effect_intents",
        "graph_outbox_id",
        "budget_outbox_id",
    }
)
_OUTCOME_FIELDS = frozenset(
    {"node_id", "attempt_id", "receipt_ref", "payload_sha256"}
)
_EFFECT_FIELDS = frozenset(
    {
        "effect_id",
        "node_id",
        "attempt_id",
        "commit_scope",
        "effect_type",
        "payload_ref",
        "payload_sha256",
        "idempotency_key",
    }
)


class ParallelSuccessContractError(ValueError):
    """Raised when a whole-graph success decision is not canonical."""


@dataclass(frozen=True, slots=True)
class CanonicalParallelSuccessDecision:
    payload: dict[str, Any]
    payload_json: str
    payload_sha256: str
    decision_ref: str


def _text(value: Any, *, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ParallelSuccessContractError(f"{field_name} is required")
    return result


def _non_negative_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ParallelSuccessContractError(
            f"{field_name} must be an integer >= {minimum}"
        )
    return value


def _digest(value: Any, *, field_name: str) -> str:
    result = _text(value, field_name=field_name)
    if _SHA256.fullmatch(result) is None:
        raise ParallelSuccessContractError(f"{field_name} must be a sha256 digest")
    return result


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ParallelSuccessContractError("non-finite JSON number is forbidden")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(key)
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def _normalize_node_states(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise ParallelSuccessContractError("node_states must be a non-empty object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_state in value.items():
        node_id = _text(raw_node_id, field_name="node_states.node_id")
        if not isinstance(raw_state, Mapping) or set(raw_state) != {
            "status",
            "status_version",
        }:
            raise ParallelSuccessContractError(
                f"node_states.{node_id} must contain status and status_version"
            )
        status = _text(raw_state.get("status"), field_name=f"{node_id}.status").upper()
        if status != "ACCEPTED":
            raise ParallelSuccessContractError(
                f"whole-graph success requires ACCEPTED node: {node_id}"
            )
        normalized[node_id] = {
            "status": status,
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
        raise ParallelSuccessContractError("accepted_outcomes must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _OUTCOME_FIELDS:
            raise ParallelSuccessContractError(
                "each accepted outcome must contain only the canonical fields"
            )
        node_id = _text(raw.get("node_id"), field_name="accepted_outcome.node_id")
        if node_id in seen or node_id not in node_states:
            raise ParallelSuccessContractError(
                f"accepted outcome node is duplicate or unknown: {node_id}"
            )
        seen.add(node_id)
        normalized.append(
            {
                "node_id": node_id,
                "attempt_id": _text(
                    raw.get("attempt_id"), field_name=f"{node_id}.attempt_id"
                ),
                "receipt_ref": _text(
                    raw.get("receipt_ref"), field_name=f"{node_id}.receipt_ref"
                ),
                "payload_sha256": _digest(
                    raw.get("payload_sha256"),
                    field_name=f"{node_id}.payload_sha256",
                ),
            }
        )
    if seen != set(node_states):
        raise ParallelSuccessContractError(
            "accepted_outcomes must bind every durable node exactly once"
        )
    normalized.sort(key=lambda item: item["node_id"])
    return normalized


def _normalize_effects(
    value: Any,
    *,
    accepted_outcomes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ParallelSuccessContractError("effect_intents must be a non-empty array")
    if len(value) > MAX_PARALLEL_SUCCESS_EFFECTS:
        raise ParallelSuccessContractError(
            f"effect_intents may contain at most {MAX_PARALLEL_SUCCESS_EFFECTS} items"
        )
    normalized: list[dict[str, Any]] = []
    accepted_attempts = {
        str(outcome["node_id"]): str(outcome["attempt_id"])
        for outcome in accepted_outcomes
    }
    effect_ids: set[str] = set()
    idempotency_keys: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _EFFECT_FIELDS:
            raise ParallelSuccessContractError(
                "each effect intent must contain only the canonical fields"
            )
        effect_id = _text(raw.get("effect_id"), field_name="effect.effect_id")
        idempotency_key = _text(
            raw.get("idempotency_key"), field_name=f"effect.{effect_id}.idempotency_key"
        )
        if effect_id in effect_ids or idempotency_key in idempotency_keys:
            raise ParallelSuccessContractError("effect identities must be unique")
        effect_ids.add(effect_id)
        idempotency_keys.add(idempotency_key)
        node_id = _text(raw.get("node_id"), field_name=f"effect.{effect_id}.node_id")
        attempt_id = _text(
            raw.get("attempt_id"), field_name=f"effect.{effect_id}.attempt_id"
        )
        if accepted_attempts.get(node_id) != attempt_id:
            raise ParallelSuccessContractError(
                "graph effects must bind one exact accepted node Attempt"
            )
        normalized.append(
            {
                "effect_id": effect_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "commit_scope": _text(
                    raw.get("commit_scope"),
                    field_name=f"effect.{effect_id}.commit_scope",
                ).upper(),
                "effect_type": _text(
                    raw.get("effect_type"), field_name=f"effect.{effect_id}.effect_type"
                ),
                "payload_ref": _text(
                    raw.get("payload_ref"),
                    field_name=f"effect.{effect_id}.payload_ref",
                ),
                "payload_sha256": _digest(
                    raw.get("payload_sha256"),
                    field_name=f"effect.{effect_id}.payload_sha256",
                ),
                "idempotency_key": idempotency_key,
            }
        )
        if normalized[-1]["commit_scope"] != "GRAPH":
            raise ParallelSuccessContractError(
                "parallel success effect commit_scope must be GRAPH"
            )
    normalized.sort(key=lambda item: item["effect_id"])
    return normalized


def normalize_parallel_success_decision(
    payload: Mapping[str, Any],
) -> CanonicalParallelSuccessDecision:
    """Validate, normalize, and digest an all-accepted Graph decision."""

    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
        raise ParallelSuccessContractError(
            "parallel success payload has missing or unknown fields"
        )
    _assert_finite(payload)
    schema_version = _text(payload.get("schema_version"), field_name="schema_version")
    if schema_version != PARALLEL_SUCCESS_SCHEMA_VERSION:
        raise ParallelSuccessContractError("parallel success schema version is invalid")

    graph_status_before = _text(
        payload.get("graph_status_before"), field_name="graph_status_before"
    ).upper()
    graph_target_status = _text(
        payload.get("graph_target_status"), field_name="graph_target_status"
    ).upper()
    budget_status_before = _text(
        payload.get("budget_status_before"), field_name="budget_status_before"
    ).upper()
    budget_target_status = _text(
        payload.get("budget_target_status"), field_name="budget_target_status"
    ).upper()
    if graph_status_before != "ACTIVE" or graph_target_status != "COMPLETED":
        raise ParallelSuccessContractError("parallel success Graph states are invalid")
    if budget_status_before != "ACTIVE" or budget_target_status != "CONSUMED":
        raise ParallelSuccessContractError("parallel success Budget states are invalid")
    completion_policy = _text(
        payload.get("completion_policy"), field_name="completion_policy"
    )
    if completion_policy != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value:
        raise ParallelSuccessContractError(
            "parallel success requires the frozen atomic Graph completion policy"
        )

    result_node_id = _text(payload.get("result_node_id"), field_name="result_node_id")
    result_attempt_id = _text(
        payload.get("result_attempt_id"), field_name="result_attempt_id"
    )
    result_receipt_ref = _text(
        payload.get("result_receipt_ref"), field_name="result_receipt_ref"
    )
    node_states = _normalize_node_states(payload.get("node_states"))
    if result_node_id not in node_states:
        raise ParallelSuccessContractError("result_node_id is not present in node_states")
    outcomes = _normalize_outcomes(
        payload.get("accepted_outcomes"), node_states=node_states
    )
    result_outcome = next(
        item for item in outcomes if item["node_id"] == result_node_id
    )
    if (
        result_outcome["attempt_id"] != result_attempt_id
        or result_outcome["receipt_ref"] != result_receipt_ref
    ):
        raise ParallelSuccessContractError(
            "result authority conflicts with its accepted outcome"
        )
    effects = _normalize_effects(
        payload.get("effect_intents"),
        accepted_outcomes=outcomes,
    )

    graph_outbox_id = _text(
        payload.get("graph_outbox_id"), field_name="graph_outbox_id"
    )
    budget_outbox_id = _text(
        payload.get("budget_outbox_id"), field_name="budget_outbox_id"
    )
    if graph_outbox_id == budget_outbox_id:
        raise ParallelSuccessContractError(
            "Graph and Budget success Outboxes must be distinct"
        )
    normalized = {
        "schema_version": schema_version,
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
        "completion_policy": completion_policy,
        "budget_id": _text(payload.get("budget_id"), field_name="budget_id"),
        "budget_status_before": budget_status_before,
        "budget_status_version_before": _non_negative_int(
            payload.get("budget_status_version_before"),
            field_name="budget_status_version_before",
        ),
        "budget_target_status": budget_target_status,
        "result_node_id": result_node_id,
        "result_attempt_id": result_attempt_id,
        "result_receipt_ref": result_receipt_ref,
        "node_states": node_states,
        "accepted_outcomes": outcomes,
        "effect_intents": effects,
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
    if len(payload_json.encode("utf-8")) > MAX_PARALLEL_SUCCESS_PAYLOAD_BYTES:
        raise ParallelSuccessContractError("parallel success payload is too large")
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return CanonicalParallelSuccessDecision(
        payload=normalized,
        payload_json=payload_json,
        payload_sha256=f"sha256:{digest}",
        decision_ref=f"tasksuccess://sha256/{digest}",
    )


__all__ = [
    "CanonicalParallelSuccessDecision",
    "MAX_PARALLEL_SUCCESS_PAYLOAD_BYTES",
    "MAX_PARALLEL_SUCCESS_EFFECTS",
    "PARALLEL_SUCCESS_SCHEMA_VERSION",
    "ParallelSuccessContractError",
    "normalize_parallel_success_decision",
]
