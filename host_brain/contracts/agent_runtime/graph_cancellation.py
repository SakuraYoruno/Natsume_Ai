"""Canonical contracts for durable graph-scoped cancellation.

Cancellation is a two-phase authority.  The intent freezes one exact eligible
Graph/Budget/node snapshot and moves the Graph behind a durable cancellation
fence.  The decision is created only after Attempts, Leases, operations,
effects, and reviews have converged, and binds the final terminal mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping


GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION = (
    "Natsume.task_runtime.graph_cancellation_intent.v1"
)
GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION = (
    "Natsume.task_runtime.graph_cancellation_decision.v1"
)
MAX_GRAPH_CANCELLATION_PAYLOAD_BYTES = 262_144

_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_AUTHORITY_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,511}$"
)
_CANCELLATION_SOURCES = frozenset({"USER", "OPERATOR", "SYSTEM"})
_TERMINAL_NODE_STATUSES = frozenset(
    {"ACCEPTED", "FAILED", "VERIFICATION_FAILED", "CANCELLED"}
)
_DIRECT_CANCEL_NODE_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW", "RETRY_PENDING"}
)
_REQUEST_CANCEL_NODE_STATUSES = frozenset(
    {"CREATED", "BLOCKED", "READY", "LEASED", "RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"}
)
_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "request_identity",
        "source",
        "reason_code",
        "graph_id",
        "graph_revision",
        "graph_status_before",
        "graph_status_version_before",
        "graph_target_status",
        "budget_id",
        "budget_status_before",
        "budget_status_version_before",
        "node_states_before",
        "node_states_after",
        "graph_outbox_id",
    }
)
_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "intent_ref",
        "request_id",
        "request_identity",
        "graph_id",
        "graph_revision",
        "graph_status_before",
        "graph_status_version_before",
        "graph_target_status",
        "budget_id",
        "budget_status_before",
        "budget_status_version_before",
        "budget_target_status",
        "node_states_before",
        "node_states_after",
        "reconciliation_evidence_refs",
        "graph_outbox_id",
        "budget_outbox_id",
    }
)


class GraphCancellationContractError(ValueError):
    """Raised when cancellation authority is incomplete or non-canonical."""


@dataclass(frozen=True, slots=True)
class CanonicalGraphCancellationIntent:
    payload: dict[str, Any]
    payload_json: str
    payload_sha256: str
    intent_ref: str


@dataclass(frozen=True, slots=True)
class CanonicalGraphCancellationDecision:
    payload: dict[str, Any]
    payload_json: str
    payload_sha256: str
    decision_ref: str


def _text(value: Any, *, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise GraphCancellationContractError(f"{field_name} is required")
    return result


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name=field_name)


def _integer(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GraphCancellationContractError(
            f"{field_name} must be an integer >= {minimum}"
        )
    return value


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise GraphCancellationContractError("non-finite JSON number is forbidden")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(key)
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def _states(value: Any, *, field_name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise GraphCancellationContractError(f"{field_name} must be non-empty")
    result: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_state in value.items():
        node_id = _text(raw_node_id, field_name=f"{field_name}.node_id")
        if not isinstance(raw_state, Mapping) or set(raw_state) != {
            "status",
            "status_version",
        }:
            raise GraphCancellationContractError(
                f"{field_name}.{node_id} must contain status and status_version"
            )
        result[node_id] = {
            "status": _text(
                raw_state.get("status"), field_name=f"{field_name}.{node_id}.status"
            ).upper(),
            "status_version": _integer(
                raw_state.get("status_version"),
                field_name=f"{field_name}.{node_id}.status_version",
            ),
        }
    return {node_id: result[node_id] for node_id in sorted(result)}


def _canonical(payload: dict[str, Any], *, prefix: str) -> tuple[str, str, str]:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_GRAPH_CANCELLATION_PAYLOAD_BYTES:
        raise GraphCancellationContractError("cancellation payload is too large")
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, f"sha256:{digest}", f"{prefix}://sha256/{digest}"


def normalize_graph_cancellation_intent(
    payload: Mapping[str, Any],
) -> CanonicalGraphCancellationIntent:
    if not isinstance(payload, Mapping) or set(payload) != _INTENT_FIELDS:
        raise GraphCancellationContractError(
            "graph cancellation intent has missing or unknown fields"
        )
    _assert_finite(payload)
    schema_version = _text(payload.get("schema_version"), field_name="schema_version")
    if schema_version != GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION:
        raise GraphCancellationContractError("cancellation intent schema is invalid")
    reason_code = _text(payload.get("reason_code"), field_name="reason_code").upper()
    if _REASON_CODE.fullmatch(reason_code) is None:
        raise GraphCancellationContractError("reason_code is invalid")
    before = _states(payload.get("node_states_before"), field_name="node_states_before")
    after = _states(payload.get("node_states_after"), field_name="node_states_after")
    if set(before) != set(after):
        raise GraphCancellationContractError("intent node snapshots differ")
    for node_id, old in before.items():
        new = after[node_id]
        if old["status"] in _TERMINAL_NODE_STATUSES or old["status"] == "CANCEL_REQUESTED":
            expected = old
        elif old["status"] in _DIRECT_CANCEL_NODE_STATUSES:
            expected = {
                "status": "CANCELLED",
                "status_version": old["status_version"] + 1,
            }
        elif old["status"] in _REQUEST_CANCEL_NODE_STATUSES:
            expected = {
                "status": "CANCEL_REQUESTED",
                "status_version": old["status_version"] + 1,
            }
        else:
            raise GraphCancellationContractError(
                f"unsupported cancellation-intent node status: {node_id}"
            )
        if new != expected:
            raise GraphCancellationContractError(
                f"invalid cancellation-intent node transition: {node_id}"
            )
    graph_before = _text(
        payload.get("graph_status_before"), field_name="graph_status_before"
    ).upper()
    graph_target = _text(
        payload.get("graph_target_status"), field_name="graph_target_status"
    ).upper()
    raw_budget_id = _optional_text(payload.get("budget_id"), field_name="budget_id")
    raw_budget_status = _optional_text(
        payload.get("budget_status_before"), field_name="budget_status_before"
    )
    raw_budget_version = payload.get("budget_status_version_before")
    if graph_before not in {"CREATED", "ACTIVE", "BLOCKED"} or graph_target != "BLOCKED":
        raise GraphCancellationContractError("intent Graph states are invalid")
    if raw_budget_id is None:
        if raw_budget_status is not None or raw_budget_version is not None:
            raise GraphCancellationContractError("absent intent Budget has partial fields")
        budget_before = None
        budget_version = None
    else:
        if raw_budget_status is None or raw_budget_version is None:
            raise GraphCancellationContractError("intent Budget binding is incomplete")
        budget_before = raw_budget_status.upper()
        if budget_before not in {"PENDING", "ACTIVE", "EXCEEDED", "RELEASED"}:
            raise GraphCancellationContractError("intent Budget state is invalid")
        budget_version = _integer(
            raw_budget_version, field_name="budget_status_version_before"
        )
    request_identity = _text(
        payload.get("request_identity"), field_name="request_identity"
    )
    if _AUTHORITY_REF.fullmatch(request_identity) is None:
        raise GraphCancellationContractError(
            "request_identity must be a durable verified authority reference"
        )
    source = _text(payload.get("source"), field_name="source").upper()
    if source not in _CANCELLATION_SOURCES:
        raise GraphCancellationContractError("cancellation source is not authorized")
    normalized = {
        "schema_version": schema_version,
        "request_id": _text(payload.get("request_id"), field_name="request_id"),
        # The Store binds this identity but does not authenticate it.  The
        # application authority boundary must verify it before this call.
        "request_identity": request_identity,
        "source": source,
        "reason_code": reason_code,
        "graph_id": _text(payload.get("graph_id"), field_name="graph_id"),
        "graph_revision": _integer(
            payload.get("graph_revision"), field_name="graph_revision", minimum=1
        ),
        "graph_status_before": graph_before,
        "graph_status_version_before": _integer(
            payload.get("graph_status_version_before"),
            field_name="graph_status_version_before",
        ),
        "graph_target_status": graph_target,
        "budget_id": raw_budget_id,
        "budget_status_before": budget_before,
        "budget_status_version_before": budget_version,
        "node_states_before": before,
        "node_states_after": after,
        "graph_outbox_id": _text(
            payload.get("graph_outbox_id"), field_name="graph_outbox_id"
        ),
    }
    payload_json, payload_sha256, intent_ref = _canonical(
        normalized, prefix="taskcancel-intent"
    )
    return CanonicalGraphCancellationIntent(
        payload=normalized,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        intent_ref=intent_ref,
    )


def normalize_graph_cancellation_decision(
    payload: Mapping[str, Any],
) -> CanonicalGraphCancellationDecision:
    if not isinstance(payload, Mapping) or set(payload) != _DECISION_FIELDS:
        raise GraphCancellationContractError(
            "graph cancellation decision has missing or unknown fields"
        )
    _assert_finite(payload)
    schema_version = _text(payload.get("schema_version"), field_name="schema_version")
    if schema_version != GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION:
        raise GraphCancellationContractError("cancellation decision schema is invalid")
    before = _states(payload.get("node_states_before"), field_name="node_states_before")
    after = _states(payload.get("node_states_after"), field_name="node_states_after")
    if set(before) != set(after):
        raise GraphCancellationContractError("decision node snapshots differ")
    for node_id, old in before.items():
        new = after[node_id]
        if old["status"] == "CANCEL_REQUESTED":
            expected = {
                "status": "CANCELLED",
                "status_version": old["status_version"] + 1,
            }
        elif old["status"] in _TERMINAL_NODE_STATUSES:
            expected = old
        else:
            raise GraphCancellationContractError(
                f"decision contains unstable node: {node_id}"
            )
        if new != expected:
            raise GraphCancellationContractError(
                f"invalid final cancellation node transition: {node_id}"
            )
    graph_before = _text(
        payload.get("graph_status_before"), field_name="graph_status_before"
    ).upper()
    graph_target = _text(
        payload.get("graph_target_status"), field_name="graph_target_status"
    ).upper()
    raw_budget_id = _optional_text(payload.get("budget_id"), field_name="budget_id")
    raw_budget_before = _optional_text(
        payload.get("budget_status_before"), field_name="budget_status_before"
    )
    raw_budget_version = payload.get("budget_status_version_before")
    raw_budget_target = _optional_text(
        payload.get("budget_target_status"), field_name="budget_target_status"
    )
    if graph_before != "BLOCKED" or graph_target != "CANCELLED":
        raise GraphCancellationContractError("decision Graph states are invalid")
    raw_budget_outbox = _optional_text(
        payload.get("budget_outbox_id"), field_name="budget_outbox_id"
    )
    if raw_budget_id is None:
        if any(
            value is not None
            for value in (
                raw_budget_before,
                raw_budget_version,
                raw_budget_target,
                raw_budget_outbox,
            )
        ):
            raise GraphCancellationContractError("absent decision Budget has partial fields")
        budget_before = budget_target = budget_version = None
    else:
        if raw_budget_before is None or raw_budget_version is None or raw_budget_target is None:
            raise GraphCancellationContractError("decision Budget binding is incomplete")
        budget_before = raw_budget_before.upper()
        budget_target = raw_budget_target.upper()
        budget_version = _integer(
            raw_budget_version, field_name="budget_status_version_before"
        )
        allowed = {
            ("ACTIVE", "RELEASED"),
            ("EXCEEDED", "RELEASED"),
            ("PENDING", "CANCELLED"),
            ("RELEASED", "RELEASED"),
        }
        if (budget_before, budget_target) not in allowed:
            raise GraphCancellationContractError("decision Budget states are invalid")
        changes_budget = budget_before != budget_target
        if changes_budget != (raw_budget_outbox is not None):
            raise GraphCancellationContractError(
                "budget Outbox is required exactly for a Budget transition"
            )
    graph_outbox_id = _text(
        payload.get("graph_outbox_id"), field_name="graph_outbox_id"
    )
    budget_outbox_id = raw_budget_outbox
    if budget_outbox_id is not None and graph_outbox_id == budget_outbox_id:
        raise GraphCancellationContractError("final Outboxes must be distinct")
    request_identity = _text(
        payload.get("request_identity"), field_name="request_identity"
    )
    if _AUTHORITY_REF.fullmatch(request_identity) is None:
        raise GraphCancellationContractError(
            "request_identity must be a durable verified authority reference"
        )
    raw_evidence = payload.get("reconciliation_evidence_refs")
    if not isinstance(raw_evidence, (list, tuple)) or not raw_evidence:
        raise GraphCancellationContractError(
            "reconciliation_evidence_refs must be a non-empty array"
        )
    evidence_refs = sorted(
        {
            _text(item, field_name="reconciliation_evidence_ref")
            for item in raw_evidence
        }
    )
    if any(_AUTHORITY_REF.fullmatch(item) is None for item in evidence_refs):
        raise GraphCancellationContractError(
            "reconciliation evidence must contain durable references"
        )
    intent_ref = _text(payload.get("intent_ref"), field_name="intent_ref")
    if intent_ref not in evidence_refs:
        raise GraphCancellationContractError(
            "reconciliation evidence must include the cancellation intent"
        )
    normalized = {
        "schema_version": schema_version,
        "intent_ref": intent_ref,
        "request_id": _text(payload.get("request_id"), field_name="request_id"),
        "request_identity": request_identity,
        "graph_id": _text(payload.get("graph_id"), field_name="graph_id"),
        "graph_revision": _integer(
            payload.get("graph_revision"), field_name="graph_revision", minimum=1
        ),
        "graph_status_before": graph_before,
        "graph_status_version_before": _integer(
            payload.get("graph_status_version_before"),
            field_name="graph_status_version_before",
        ),
        "graph_target_status": graph_target,
        "budget_id": raw_budget_id,
        "budget_status_before": budget_before,
        "budget_status_version_before": budget_version,
        "budget_target_status": budget_target,
        "node_states_before": before,
        "node_states_after": after,
        "reconciliation_evidence_refs": evidence_refs,
        "graph_outbox_id": graph_outbox_id,
        "budget_outbox_id": budget_outbox_id,
    }
    payload_json, payload_sha256, decision_ref = _canonical(
        normalized, prefix="taskcancel"
    )
    return CanonicalGraphCancellationDecision(
        payload=normalized,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        decision_ref=decision_ref,
    )


__all__ = [
    "CanonicalGraphCancellationDecision",
    "CanonicalGraphCancellationIntent",
    "GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION",
    "GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION",
    "GraphCancellationContractError",
    "MAX_GRAPH_CANCELLATION_PAYLOAD_BYTES",
    "normalize_graph_cancellation_decision",
    "normalize_graph_cancellation_intent",
]
