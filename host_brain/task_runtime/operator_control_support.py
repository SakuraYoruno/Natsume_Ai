"""Frozen contracts for governed Task cancellation and Review controls."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any

from host_brain.contracts.governed_operator import (
    _invalid_operator_runtime as _invalid,
    _operator_non_negative_int as _non_negative_int,
    _operator_positive_int as _positive_int,
    _operator_text as _text,
)
from host_brain.secure_ingress.graph_cancellation_authority import (
    TASK_GRAPH_CANCEL_ACTION,
    TASK_GRAPH_CANCEL_PATH,
    TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput


OPERATOR_CONTROL_TARGET_ID = "operator_task_control"
OPERATOR_CONTROL_EFFECT_TYPE = "task.operator_control"
OPERATOR_GRAPH_CANCEL_ACTION_KIND = "operator_task_graph_cancellation"
OPERATOR_REVIEW_CONTROL_ACTION_KIND = "operator_task_review_control"
OPERATOR_CONTROL_COMMAND_IDS = frozenset(
    {
        "task_graph.cancel",
        "task_review.approve",
        "task_review.reject",
        "task_review.resume",
    }
)
_REVIEW_REQUIRED_SCOPES = ("obs:write", "task-review:write")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PUBLIC_REVIEW_TOKEN = re.compile(r"^review-token://[0-9a-f]{64}$")


def operator_control_ingress_requirements(
    command_id: str,
) -> tuple[str, str, tuple[str, ...]]:
    command = _command_id(command_id)
    if command == "task_graph.cancel":
        return (
            TASK_GRAPH_CANCEL_ACTION,
            TASK_GRAPH_CANCEL_PATH,
            TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
        )
    return (
        f"obs:command:{command}",
        f"/v1/host/obs-commands/{command}",
        _REVIEW_REQUIRED_SCOPES,
    )


def operator_control_action_kind(command_id: str) -> str:
    return (
        OPERATOR_GRAPH_CANCEL_ACTION_KIND
        if _command_id(command_id) == "task_graph.cancel"
        else OPERATOR_REVIEW_CONTROL_ACTION_KIND
    )


def normalize_operator_control_args(
    payload: Mapping[str, Any],
    *,
    request_id: str,
    principal_id: str,
) -> dict[str, Any]:
    value = dict(payload or {})
    request_ref = _text(request_id, "request_id")
    principal = _text(principal_id, "principal_id")
    authority_raw = value.get("_operator_authority")
    authority = dict(authority_raw) if isinstance(authority_raw, Mapping) else {}
    command_id = _command_id(authority.get("operator_command_id"))
    common = {
        "text": operator_control_message(command_id, value),
        "route": "delegate",
        "target_id": OPERATOR_CONTROL_TARGET_ID,
        "operator_command_id": command_id,
        "request_id": request_ref,
        "operator_actor_id": principal,
    }
    if command_id == "task_graph.cancel":
        graph_id = _text(value.get("graph_id"), "graph_id")
        revision = _positive_int(value.get("graph_revision"), "graph_revision")
        reason = str(value.get("reason_code") or "").strip().upper()
        if _REASON_CODE.fullmatch(reason) is None:
            raise _invalid("task_graph.cancel reason_code is invalid")
        return {
            **common,
            "graph_id": graph_id,
            "graph_revision": revision,
            "reason_code": reason,
        }

    review_id = _text(value.get("review_id"), "review_id")
    if command_id == "task_review.reject":
        if _text(value.get("actor_id"), "actor_id") != principal:
            raise _invalid("task_review.reject actor differs from Secure Ingress")
        return {**common, "review_id": review_id, "actor_id": principal}

    subject_ref = _text(value.get("subject_ref"), "subject_ref")
    subject_version = _non_negative_int(
        value.get("subject_version"),
        "subject_version",
    )
    resume_phase = str(value.get("resume_phase") or "").strip().upper()
    if resume_phase not in {
        "PRE_EXECUTION",
        "POST_EXECUTION_ACCEPTANCE",
    }:
        raise _invalid("Review resume_phase is invalid")
    checkpoint_id = _durable_ref(value.get("checkpoint_id"), "checkpoint_id")
    if command_id == "task_review.approve":
        if _text(value.get("actor_id"), "actor_id") != principal:
            raise _invalid("task_review.approve actor differs from Secure Ingress")
        return {
            **common,
            "review_id": review_id,
            "actor_id": principal,
            "subject_ref": subject_ref,
            "subject_version": subject_version,
            "resume_phase": resume_phase,
            "checkpoint_id": checkpoint_id,
        }

    public_ref = str(value.get("resume_token_ref") or "").strip()
    if _PUBLIC_REVIEW_TOKEN.fullmatch(public_ref) is None:
        raise _invalid(
            "Operator Review resume requires the recoverable public token reference"
        )
    supplied_result = str(value.get("resume_result_ref") or "").strip()
    result_ref = operator_control_resume_result_ref(
        request_id=request_ref,
        review_id=review_id,
    )
    if supplied_result and (
        _durable_ref(supplied_result, "resume_result_ref") != result_ref
    ):
        raise _invalid("Review resume_result_ref differs from server identity")
    return {
        **common,
        "review_id": review_id,
        "subject_ref": subject_ref,
        "subject_version": subject_version,
        "resume_phase": resume_phase,
        "checkpoint_id": checkpoint_id,
        # The bearer never enters WorkOrder or receipts.  The handler reopens
        # ReviewItem.token_ref and proves this digest before consuming it.
        "resume_binding_sha256": "sha256:"
        + hashlib.sha256(public_ref.encode("utf-8")).hexdigest(),
        "resume_result_ref": result_ref,
    }


def operator_control_message(
    command_id: str,
    payload: Mapping[str, Any],
) -> str:
    command = _command_id(command_id)
    if command == "task_graph.cancel":
        return (
            "请求取消任务图 "
            f"{str(payload.get('graph_id') or '').strip()}@"
            f"{payload.get('graph_revision')}"
        )
    return (
        f"处理任务审核 {str(payload.get('review_id') or '').strip()}："
        f"{command.rsplit('.', 1)[-1]}"
    )


def build_operator_control_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(args or {})
    command_id = _command_id(normalized.get("operator_command_id"))
    digest = hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-control-{digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": operator_control_action_kind(command_id),
        "target": "task_runtime",
        "args": normalized,
        "nonce": hashlib.sha256(
            f"operator-control\n{request_id}\n{command_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": str(principal_id),
        "principal_id": str(principal_id),
        "route": "delegate",
        "target_id": OPERATOR_CONTROL_TARGET_ID,
    }


def compile_frozen_operator_control(
    frozen: FrozenNodeInput,
) -> Mapping[str, Any]:
    if (
        frozen.route != "delegate"
        or frozen.target_id != OPERATOR_CONTROL_TARGET_ID
    ):
        raise _invalid("frozen Operator control route is invalid")
    pack = dict(frozen.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    command_id = _command_id(args.get("operator_command_id"))
    expected_action = operator_control_action_kind(command_id)
    if (
        str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != OPERATOR_CONTROL_TARGET_ID
        or str(intent.get("target") or "").strip().lower() != "task_runtime"
        or str(intent.get("action") or "").strip() != expected_action
        or str(args.get("request_id") or "").strip() != frozen.request_id
    ):
        raise _invalid("frozen Operator control intent binding drifted")
    _validate_frozen_args(command_id, args)
    operation_id = operator_control_operation_id(
        request_id=frozen.request_id,
        command_id=command_id,
    )
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": f"操作员控制命令已通过执行验收：{command_id}",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": command_id,
            "proposed_effect_type": OPERATOR_CONTROL_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": OPERATOR_CONTROL_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": command_id,
                    "request_id": frozen.request_id,
                    "operation_id": operation_id,
                    "control_args": {
                        key: value
                        for key, value in args.items()
                        if key not in {"text", "route", "target_id"}
                    },
                },
            }
        ],
    }


def operator_control_operation_id(*, request_id: str, command_id: str) -> str:
    digest = hashlib.sha256(
        f"{_command_id(command_id)}\n{_text(request_id, 'request_id')}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"operator-control://sha256/{digest}"


def operator_control_resume_result_ref(*, request_id: str, review_id: str) -> str:
    digest = hashlib.sha256(
        f"{_text(request_id, 'request_id')}\n{_text(review_id, 'review_id')}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"review-resume://operator/{digest}"


def _validate_frozen_args(command_id: str, args: Mapping[str, Any]) -> None:
    common = {
        "text",
        "route",
        "target_id",
        "operator_command_id",
        "request_id",
        "operator_actor_id",
    }
    by_command = {
        "task_graph.cancel": {
            "graph_id",
            "graph_revision",
            "reason_code",
        },
        "task_review.approve": {
            "review_id",
            "actor_id",
            "subject_ref",
            "subject_version",
            "resume_phase",
            "checkpoint_id",
        },
        "task_review.reject": {"review_id", "actor_id"},
        "task_review.resume": {
            "review_id",
            "subject_ref",
            "subject_version",
            "resume_phase",
            "checkpoint_id",
            "resume_binding_sha256",
            "resume_result_ref",
        },
    }
    expected = common | by_command[command_id]
    if set(args) != expected:
        raise _invalid("frozen Operator control fields drifted")
    for field in expected - {
        "graph_revision",
        "subject_version",
    }:
        if not str(args.get(field) or "").strip():
            raise _invalid(f"frozen Operator control {field} is empty")
    if command_id == "task_graph.cancel":
        _positive_int(args.get("graph_revision"), "graph_revision")
        if _REASON_CODE.fullmatch(str(args.get("reason_code") or "")) is None:
            raise _invalid("frozen cancellation reason_code is invalid")
    if command_id in {"task_review.approve", "task_review.resume"}:
        _non_negative_int(args.get("subject_version"), "subject_version")
        _durable_ref(args.get("checkpoint_id"), "checkpoint_id")
    if command_id == "task_review.resume":
        if _DIGEST.fullmatch(
            str(args.get("resume_binding_sha256") or "")
        ) is None:
            raise _invalid("frozen Review resume binding digest is invalid")
        _durable_ref(args.get("resume_result_ref"), "resume_result_ref")


def _command_id(value: Any) -> str:
    command = str(value or "").strip()
    if command not in OPERATOR_CONTROL_COMMAND_IDS:
        raise _invalid("unsupported governed Operator control command")
    return command


def _durable_ref(value: Any, field: str) -> str:
    ref = _text(value, field)
    if not re.fullmatch(r"[a-z][a-z0-9+.-]{1,31}://\S+", ref):
        raise _invalid(f"{field} must be a durable reference")
    return ref


__all__ = [
    "OPERATOR_CONTROL_COMMAND_IDS",
    "OPERATOR_CONTROL_EFFECT_TYPE",
    "OPERATOR_CONTROL_TARGET_ID",
    "OPERATOR_GRAPH_CANCEL_ACTION_KIND",
    "OPERATOR_REVIEW_CONTROL_ACTION_KIND",
    "build_operator_control_governor_intent",
    "compile_frozen_operator_control",
    "normalize_operator_control_args",
    "operator_control_action_kind",
    "operator_control_ingress_requirements",
    "operator_control_message",
    "operator_control_operation_id",
    "operator_control_resume_result_ref",
]
