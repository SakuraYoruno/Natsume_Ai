"""Focused contracts and durable projection checks for Operator Edge commands."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any

from host_brain.app.accepted_edge_embodiment_handler import (
    edge_embodiment_handler_receipt_ref,
)
from host_brain.contracts.governed_operator import (
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
    _operator_runtime_scope_id as _runtime_scope_id,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from natsume_contracts.task import HostCommand


EDGE_TARGET_ID = "operator_edge_command_push"
EDGE_EFFECT_TYPE = "edge.embodiment_enqueue"
EDGE_ACTION_KIND = "operator_edge_command_push"
EDGE_INGRESS_ACTION = "obs:command:edge.command.push"
EDGE_INGRESS_PATH = "/v1/host/obs-commands/edge.command.push"
EDGE_INGRESS_REQUIRED_SCOPES = ("device:edge:write", "obs:write")


def compile_frozen_edge_command(
    frozen: FrozenNodeInput,
    *,
    runtime_scope_id: str | None,
) -> dict[str, Any]:
    if runtime_scope_id is None:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "app-local embodiment runtime is unavailable",
        )
    pack = dict(frozen.input_pack or {})
    intent = pack.get("intent_draft")
    if not isinstance(intent, Mapping):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen Edge Operator WorkOrder lacks intent_draft",
        )
    args = intent.get("args")
    if not isinstance(args, Mapping):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen Edge Operator intent lacks typed args",
        )
    normalized = normalize_edge_command_args(args)
    if (
        str(intent.get("action") or "").strip() != EDGE_ACTION_KIND
        or str(intent.get("target") or "").strip().lower() != "edge"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != EDGE_TARGET_ID
        or str(args.get("embodiment_runtime_scope_id") or "").strip()
        != runtime_scope_id
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen Edge Operator intent binding drifted",
        )
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": "边缘表情指令已通过执行验收并等待持久投递",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": "edge.command.push",
            "proposed_effect_type": EDGE_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": EDGE_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": "edge.command.push",
                    "request_id": frozen.request_id,
                    "command_type": normalized["command_type"],
                    "target_agent": normalized["target_agent"],
                    "payload": normalized["payload"],
                    "runtime_scope_id": runtime_scope_id,
                },
            }
        ],
    }


def normalize_edge_command_args(value: Mapping[str, Any]) -> dict[str, Any]:
    command_type = str(value.get("command_type") or "").strip()
    target_agent = str(value.get("target_agent") or "").strip()
    operator_command_id = str(
        value.get("operator_command_id") or "edge.command.push"
    ).strip()
    raw_payload = value.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    if (
        operator_command_id != "edge.command.push"
        or command_type != "emotion_state_command"
        or target_agent != "edge.somatic"
        or set(payload) - {"emotion_tag", "intensity"}
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "only edge.somatic emotion_state_command is currently supported",
        )
    emotion_tag = str(payload.get("emotion_tag") or "").strip().upper()
    if (
        not emotion_tag
        or len(emotion_tag) > 64
        or any(
            not (character.isascii() and (character.isalnum() or character == "_"))
            for character in emotion_tag
        )
        or not emotion_tag[0].isalpha()
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator emotion_tag is invalid",
        )
    normalized_payload: dict[str, Any] = {"emotion_tag": emotion_tag}
    if "intensity" in payload:
        intensity = payload.get("intensity")
        if (
            isinstance(intensity, bool)
            or not isinstance(intensity, (int, float))
            or not math.isfinite(float(intensity))
            or not 0.0 <= float(intensity) <= 1.0
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Edge Operator intensity is invalid",
            )
        normalized_payload["intensity"] = float(intensity)
    return {
        "operator_command_id": "edge.command.push",
        "command_type": command_type,
        "target_agent": target_agent,
        "payload": normalized_payload,
    }


def build_edge_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-edge-{digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": EDGE_ACTION_KIND,
        "target": "edge",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-edge-command\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": EDGE_TARGET_ID,
    }


def require_durable_edge_outcome(
    *,
    task_store: Any,
    embodiment_runtime_provider: Any,
    execution_receipt: Mapping[str, Any],
    command: HostCommand,
    normalized_args: Mapping[str, Any],
) -> dict[str, Any]:
    from natsume_contracts.edge.embodiment_v1 import (  # noqa: PLC0415
        parse_embodiment_command,
    )

    if not callable(embodiment_runtime_provider):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "app-local embodiment runtime provider is unavailable",
        )
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    try:
        graph_revision = int(receipt.get("graph_revision") or 0)
    except (TypeError, ValueError) as exc:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator graph revision is invalid",
        ) from exc
    list_effects = getattr(task_store, "list_acceptance_effect_intents", None)
    if (
        not graph_id
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or not callable(list_effects)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator receipt cannot reopen durable Acceptance Effect",
        )
    effects = [
        dict(row)
        for row in tuple(
            list_effects(
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_id=node_id,
                limit=10,
            )
            or ()
        )
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "").strip() == EDGE_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator must bind one durable embodiment effect",
        )
    effect = effects[0]
    payload_raw = effect.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    business = {
        key: payload.get(key)
        for key in {
            "operator_command_id",
            "request_id",
            "command_type",
            "target_agent",
            "runtime_scope_id",
            "embodiment_command",
        }
    }
    runtime = embodiment_runtime_provider()
    runtime_scope_id = str(getattr(runtime, "runtime_scope_id", "") or "").strip()
    if (
        business["operator_command_id"] != "edge.command.push"
        or business["request_id"] != command.request_id
        or business["command_type"] != normalized_args["command_type"]
        or business["target_agent"] != normalized_args["target_agent"]
        or business["runtime_scope_id"] != runtime_scope_id
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator Acceptance Effect payload drifted",
        )
    try:
        embodiment_command = parse_embodiment_command(
            business["embodiment_command"]
        )
    except (TypeError, ValueError) as exc:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator embodiment command is invalid",
        ) from exc
    identity_digest = hashlib.sha256(
        json.dumps(
            {
                "graph_id": graph_id,
                "graph_revision": graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "request_id": command.request_id,
                "command_type": normalized_args["command_type"],
                "target_agent": normalized_args["target_agent"],
                "payload": dict(normalized_args["payload"]),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        embodiment_command.kind != "avatar"
        or embodiment_command.action != "set_expression"
        or embodiment_command.payload != dict(normalized_args["payload"])
        or embodiment_command.request_id != command.request_id
        or embodiment_command.issued_at_ms != int(command.created_at_ms)
        or embodiment_command.deadline_ms
        != int(command.created_at_ms) + 7 * 24 * 60 * 60 * 1000
        or embodiment_command.idempotency_key
        != f"task-edge-effect:{identity_digest}"
        or embodiment_command.authority_ref
        != f"authority://task-acceptance/sha256/{identity_digest}"
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator embodiment command identity drifted",
        )
    reopen = getattr(runtime, "reopen_outbox_command", None)
    if not callable(reopen):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator durable outbox reader is unavailable",
        )
    row_raw = reopen(embodiment_command)
    row = dict(row_raw) if isinstance(row_raw, Mapping) else {}
    outbox_id = str(row.get("outbox_id") or "").strip()
    delivery_status = str(row.get("status") or "").strip().upper()
    persisted_receipt = str(effect.get("receipt_ref") or "").strip()
    if (
        str(effect.get("status") or "").strip().upper() != "COMMITTED"
        or str(effect.get("graph_id") or "").strip() != graph_id
        or int(effect.get("graph_revision") or 0) != graph_revision
        or str(effect.get("node_id") or "").strip() != node_id
        or str(effect.get("attempt_id") or "").strip() != attempt_id
        or int(effect.get("committed_ts_ms") or 0) <= 0
        or not outbox_id
        or str(row.get("runtime_scope_id") or "").strip() != runtime_scope_id
        or str(row.get("command_id") or "").strip()
        != embodiment_command.command_id
        or str(row.get("idempotency_key") or "").strip()
        != embodiment_command.idempotency_key
        or persisted_receipt != edge_embodiment_handler_receipt_ref(outbox_id)
        or delivery_status
        not in {"PENDING", "DISPATCHING", "IN_DOUBT", "APPLIED", "REJECTED"}
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Edge Operator durable outbox receipt binding drifted",
        )
    physical_receipt = (
        dict(row.get("receipt"))
        if isinstance(row.get("receipt"), Mapping)
        else None
    )
    physical_effect_verified = delivery_status == "APPLIED"
    physical_receipt_ref: str | None = None
    if physical_effect_verified:
        if (
            physical_receipt is None
            or physical_receipt.get("accepted") is not True
            or str(physical_receipt.get("command_id") or "").strip()
            != embodiment_command.command_id
            or str(physical_receipt.get("idempotency_key") or "").strip()
            != embodiment_command.idempotency_key
            or str(physical_receipt.get("status") or "").strip().upper()
            not in {"APPLIED", "COMPLETED", "SUCCEEDED"}
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Edge Operator APPLIED row lacks its exact physical receipt",
            )
        physical_receipt_ref = (
            str(physical_receipt.get("receipt_ref") or "").strip() or None
        )
    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "effect_type": EDGE_EFFECT_TYPE,
        "handler_receipt_ref": persisted_receipt,
        "committed_ts_ms": int(effect["committed_ts_ms"]),
        "outbox_id": outbox_id,
        "command_id": embodiment_command.command_id,
        "idempotency_key": embodiment_command.idempotency_key,
        "delivery_status": delivery_status,
        "durable_admitted": delivery_status
        in {"PENDING", "DISPATCHING", "IN_DOUBT", "APPLIED"},
        "physical_effect_verified": physical_effect_verified,
        "physical_receipt_ref": physical_receipt_ref,
    }


def pending_operator_reviews(
    review_store: Any,
    *,
    graph_id: str,
    graph_revision: int,
) -> list[dict[str, Any]]:
    rows = review_store.list_reviews(
        task_graph_id=graph_id,
        graph_revision=graph_revision,
    )
    reviews: list[dict[str, Any]] = []
    for raw in tuple(rows or ()):
        record = dict(raw or {}) if isinstance(raw, Mapping) else {}
        review = record.get("review")
        review = dict(review) if isinstance(review, Mapping) else {}
        review_id = str(review.get("review_id") or "").strip()
        status = str(review.get("status") or "").strip().upper()
        if review_id and status in {"PENDING", "APPROVED"}:
            reviews.append(
                {
                    "review_id": review_id,
                    "status": status,
                    "subject_ref": review.get("subject_ref"),
                    "subject_version": review.get("subject_version"),
                    "resume_phase": review.get("resume_phase"),
                    "checkpoint_id": review.get("checkpoint_id"),
                }
            )
    if len(reviews) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator Review admission must expose one exact pending ReviewItem",
        )
    return reviews


def embodiment_runtime_scope_id(provider: Any | None) -> str | None:
    if provider is None:
        return None
    if not callable(provider):
        raise TypeError("embodiment_runtime_provider must be callable")
    runtime = provider()
    if runtime is None:
        raise TypeError("Operator runtime requires app-local embodiment runtime")
    return _runtime_scope_id(getattr(runtime, "runtime_scope_id", None))


__all__ = [
    "EDGE_ACTION_KIND",
    "EDGE_EFFECT_TYPE",
    "EDGE_INGRESS_ACTION",
    "EDGE_INGRESS_PATH",
    "EDGE_INGRESS_REQUIRED_SCOPES",
    "EDGE_TARGET_ID",
    "build_edge_governor_intent",
    "compile_frozen_edge_command",
    "embodiment_runtime_scope_id",
    "normalize_edge_command_args",
    "pending_operator_reviews",
    "require_durable_edge_outcome",
]
