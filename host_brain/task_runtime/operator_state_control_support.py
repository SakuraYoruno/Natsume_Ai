"""Frozen contracts for governed Scheduler and Host mode-policy controls."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from host_brain.contracts.governed_operator import (
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
)
from host_brain.contracts.host_mode_policy import HOST_MODE_AUTO_RULES
from host_brain.task_runtime.governed_workers import FrozenNodeInput


OPERATOR_STATE_CONTROL_TARGET_ID = "operator_state_control"
OPERATOR_STATE_CONTROL_EFFECT_TYPE = "host.operator_state_control"
OPERATOR_STATE_CONTROL_COMMAND_IDS = frozenset(
    {
        "scheduler.task.enable",
        "scheduler.task.disable",
        "sys_mode.auto_rules.update",
        "sys_mode.event.submit",
    }
)
SCHEDULER_ADMIN_CONTROL_COMMAND_IDS = frozenset(
    {
        "scheduler.task.publish",
        "scheduler.task.delete",
        "scheduler.task.enable",
        "scheduler.task.disable",
    }
)
STATE_CONTROL_COMMAND_IDS = frozenset(
    OPERATOR_STATE_CONTROL_COMMAND_IDS | SCHEDULER_ADMIN_CONTROL_COMMAND_IDS
)
OPERATOR_STATE_EVENT_TYPES = frozenset(
    {
        "gaming_process_detected",
        "gaming_process_exited",
        "idle_check",
        "morning_check",
        "user_message_received",
        "voice_input_received",
        "wechat_message_received",
    }
)
_SET_ENABLED_COMMAND_IDS = frozenset(
    {"scheduler.task.enable", "scheduler.task.disable"}
)


def operator_state_control_ingress_requirements(
    command_id: str,
) -> tuple[str, str, tuple[str, ...]]:
    command = _command_id(command_id)
    if command not in OPERATOR_STATE_CONTROL_COMMAND_IDS:
        raise _invalid(
            "Scheduler HTTP controls do not carry OBS command authority"
        )
    domain_scope = (
        "scheduler:write"
        if command in _SET_ENABLED_COMMAND_IDS
        else "obs:sys-mode:write"
    )
    return (
        f"obs:command:{command}",
        f"/v1/host/obs-commands/{command}",
        tuple(sorted(("obs:write", domain_scope))),
    )


def operator_state_control_action_kind(command_id: str) -> str:
    command = _command_id(command_id)
    return (
        "operator_scheduler_state_control"
        if command in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS
        else "operator_host_mode_policy_control"
    )


def operator_state_control_target(command_id: str) -> str:
    _command_id(command_id)
    return "system"


def normalize_operator_state_control_args(
    payload: Mapping[str, Any],
    *,
    request_id: str,
    principal_id: str,
    observed_at_ms: int,
    scheduler_head_version: int | None = None,
    policy_revision: int | None = None,
) -> dict[str, Any]:
    value = dict(payload or {})
    request_ref = _text(request_id, "request_id")
    principal = _text(principal_id, "principal_id")
    observed = _non_negative_int(observed_at_ms, "observed_at_ms")
    authority_raw = value.get("_operator_authority")
    authority = dict(authority_raw) if isinstance(authority_raw, Mapping) else {}
    command_id = _command_id(authority.get("operator_command_id"))
    common = {
        "text": operator_state_control_message(command_id, value),
        "route": "delegate",
        "target_id": OPERATOR_STATE_CONTROL_TARGET_ID,
        "operator_command_id": command_id,
        "request_id": request_ref,
        "operator_actor_id": principal,
        "observed_at_ms": observed,
    }
    if command_id in _SET_ENABLED_COMMAND_IDS:
        current_version = _positive_int(
            scheduler_head_version,
            "scheduler_head_version",
        )
        supplied_version = value.get("expected_head_version")
        if supplied_version is not None and (
            _positive_int(supplied_version, "expected_head_version")
            != current_version
        ):
            raise _invalid("scheduler definition head version is stale")
        return {
            **common,
            "task_id": _text(value.get("task_id"), "task_id"),
            "enabled": command_id == "scheduler.task.enable",
            "expected_head_version": current_version,
        }
    revision = _positive_int(policy_revision, "policy_revision")
    if command_id == "sys_mode.auto_rules.update":
        raw_rules = value.get("rules")
        rules = dict(raw_rules) if isinstance(raw_rules, Mapping) else {}
        if (
            not rules
            or not set(rules).issubset(HOST_MODE_AUTO_RULES)
            or any(type(item) is not bool for item in rules.values())
        ):
            raise _invalid("Host mode auto rules are invalid")
        return {
            **common,
            "rules": {key: bool(rules[key]) for key in sorted(rules)},
            "expected_policy_revision": revision,
        }
    event_type = _text(value.get("event_type"), "event_type")
    if event_type not in OPERATOR_STATE_EVENT_TYPES:
        raise _invalid("Host mode event type is unsupported")
    return {
        **common,
        "event_type": event_type,
        "expected_policy_revision": revision,
    }


def normalize_scheduler_admin_control_args(
    payload: Mapping[str, Any],
    *,
    command_id: str,
    request_id: str,
    principal_id: str,
    observed_at_ms: int,
    ingress_method: str,
    ingress_path: str,
    scheduler_head_version: int | None,
) -> dict[str, Any]:
    """Freeze one authenticated Scheduler admin request for TaskGraph execution."""

    command = _command_id(command_id)
    if command not in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
        raise _invalid("unsupported Scheduler admin control command")
    value = dict(payload or {})
    request_ref = _text(request_id, "request_id")
    principal = _text(principal_id, "principal_id")
    observed = _non_negative_int(observed_at_ms, "observed_at_ms")
    method = _text(ingress_method, "scheduler_ingress_method").upper()
    path = _text(ingress_path, "scheduler_ingress_path")
    if method not in {"POST", "PATCH", "DELETE"} or not path.startswith("/"):
        raise _invalid("Scheduler admin HTTP binding is invalid")
    task_id = _text(value.get("task_id"), "task_id")
    common = {
        "text": operator_state_control_message(command, value),
        "route": "delegate",
        "target_id": OPERATOR_STATE_CONTROL_TARGET_ID,
        "operator_command_id": command,
        "request_id": request_ref,
        "operator_actor_id": principal,
        "observed_at_ms": observed,
        "scheduler_ingress_method": method,
        "scheduler_ingress_path": path,
        "task_id": task_id,
    }
    supplied_version = value.get("expected_head_version")
    if command == "scheduler.task.publish":
        expected = _non_negative_int(
            supplied_version,
            "expected_head_version",
        )
        current = (
            None
            if scheduler_head_version is None
            else _positive_int(
                scheduler_head_version,
                "scheduler_head_version",
            )
        )
        if (current is None and expected != 0) or (
            current is not None and expected != current
        ):
            raise _invalid("scheduler definition head version is stale")
        raw_definition = value.get("definition")
        if not isinstance(raw_definition, Mapping):
            raise _invalid("Scheduler definition is invalid")
        definition = _validate_scheduler_definition(
            task_id=task_id,
            definition=dict(raw_definition),
        )
        definition["task_id"] = task_id
        definition["trigger_mode"] = str(
            definition.get("trigger_mode") or ""
        ).strip().lower()
        definition["enabled"] = bool(definition.get("enabled", True))
        try:
            definition = json.loads(
                json.dumps(
                    definition,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise _invalid("Scheduler definition is not canonical JSON") from exc
        next_scheduled_at_ms = value.get("next_scheduled_at_ms")
        if next_scheduled_at_ms is not None:
            next_scheduled_at_ms = _non_negative_int(
                next_scheduled_at_ms,
                "next_scheduled_at_ms",
            )
        return {
            **common,
            "definition": definition,
            "expected_head_version": expected,
            "next_scheduled_at_ms": next_scheduled_at_ms,
        }
    current = _positive_int(
        scheduler_head_version,
        "scheduler_head_version",
    )
    if supplied_version is not None and (
        _positive_int(supplied_version, "expected_head_version") != current
    ):
        raise _invalid("scheduler definition head version is stale")
    if command in _SET_ENABLED_COMMAND_IDS:
        return {
            **common,
            "enabled": command == "scheduler.task.enable",
            "expected_head_version": current,
        }
    return {
        **common,
        "expected_head_version": current,
    }


def operator_state_control_message(
    command_id: str,
    payload: Mapping[str, Any],
) -> str:
    command = _command_id(command_id)
    if command == "scheduler.task.publish":
        return f"发布调度任务 {str(payload.get('task_id') or '').strip()}"
    if command == "scheduler.task.delete":
        return f"删除调度任务 {str(payload.get('task_id') or '').strip()}"
    if command in _SET_ENABLED_COMMAND_IDS:
        action = "启用" if command.endswith(".enable") else "停用"
        return f"{action}调度任务 {str(payload.get('task_id') or '').strip()}"
    if command == "sys_mode.auto_rules.update":
        return "更新系统模式自动切换规则"
    return f"提交系统模式事件 {str(payload.get('event_type') or '').strip()}"


def build_operator_state_control_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    normalized = dict(args or {})
    command_id = _command_id(normalized.get("operator_command_id"))
    request_ref = _text(request_id, "request_id")
    digest = hashlib.sha256(request_ref.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-state-control-{digest[:40]}",
        "ts_ms": _non_negative_int(ts_ms, "ts_ms"),
        "session_id": (
            _text(session_id, "session_id")
            if session_id is not None
            else f"operator-{request_ref}"
        ),
        "seq": int(ts_ms),
        "type": "control",
        "action": operator_state_control_action_kind(command_id),
        "target": operator_state_control_target(command_id),
        "args": normalized,
        "nonce": hashlib.sha256(
            f"operator-state-control\n{request_ref}\n{command_id}".encode(
                "utf-8"
            )
        ).hexdigest(),
        "person_id": _text(principal_id, "principal_id"),
        "principal_id": _text(principal_id, "principal_id"),
        "route": "delegate",
        "target_id": OPERATOR_STATE_CONTROL_TARGET_ID,
    }


def compile_frozen_operator_state_control(
    frozen: FrozenNodeInput,
) -> Mapping[str, Any]:
    if (
        frozen.route != "delegate"
        or frozen.target_id != OPERATOR_STATE_CONTROL_TARGET_ID
    ):
        raise _invalid("frozen Operator state-control route is invalid")
    pack = dict(frozen.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    command_id = _command_id(args.get("operator_command_id"))
    if (
        str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != OPERATOR_STATE_CONTROL_TARGET_ID
        or str(intent.get("target") or "").strip().lower()
        != operator_state_control_target(command_id)
        or str(intent.get("action") or "").strip()
        != operator_state_control_action_kind(command_id)
        or str(args.get("request_id") or "").strip() != frozen.request_id
    ):
        raise _invalid("frozen Operator state-control intent binding drifted")
    validate_operator_state_control_args(command_id, args)
    operation_id = operator_state_control_operation_id(
        request_id=frozen.request_id,
        command_id=command_id,
    )
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": f"操作员状态控制已通过执行验收：{command_id}",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": command_id,
            "proposed_effect_type": OPERATOR_STATE_CONTROL_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": OPERATOR_STATE_CONTROL_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": command_id,
                    "request_id": frozen.request_id,
                    "operation_id": operation_id,
                    "state_args": {
                        key: value
                        for key, value in args.items()
                        if key not in {"text", "route", "target_id"}
                    },
                },
            }
        ],
    }


def operator_state_control_operation_id(
    *,
    request_id: str,
    command_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{_command_id(command_id)}\n{_text(request_id, 'request_id')}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"operator-state-control://sha256/{digest}"


def validate_operator_state_control_args(
    command_id: str,
    args: Mapping[str, Any],
) -> None:
    command = _command_id(command_id)
    common = {
        "text",
        "route",
        "target_id",
        "operator_command_id",
        "request_id",
        "operator_actor_id",
        "observed_at_ms",
    }
    scheduler_http = "scheduler_ingress_method" in args
    if command in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
        scheduler_common = (
            common
            | {
                "scheduler_ingress_method",
                "scheduler_ingress_path",
                "task_id",
            }
            if scheduler_http
            else common | {"task_id"}
        )
        if command == "scheduler.task.publish":
            expected = scheduler_common | {
                "definition",
                "expected_head_version",
                "next_scheduled_at_ms",
            }
        elif command == "scheduler.task.delete":
            expected = scheduler_common | {"expected_head_version"}
        else:
            expected = scheduler_common | {
                "enabled",
                "expected_head_version",
            }
    else:
        expected = common | (
            {"rules", "expected_policy_revision"}
            if command == "sys_mode.auto_rules.update"
            else {"event_type", "expected_policy_revision"}
        )
    if set(args) != expected:
        raise _invalid("frozen Operator state-control fields drifted")
    for field in {
        "text",
        "route",
        "target_id",
        "operator_command_id",
        "request_id",
        "operator_actor_id",
    }:
        _text(args.get(field), field)
    _non_negative_int(args.get("observed_at_ms"), "observed_at_ms")
    if command in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
        _text(args.get("task_id"), "task_id")
        if scheduler_http:
            method = _text(
                args.get("scheduler_ingress_method"),
                "scheduler_ingress_method",
            ).upper()
            path = _text(
                args.get("scheduler_ingress_path"),
                "scheduler_ingress_path",
            )
            if (
                method not in {"POST", "PATCH", "DELETE"}
                or not path.startswith("/")
            ):
                raise _invalid("frozen Scheduler HTTP binding is invalid")
        if command == "scheduler.task.publish":
            _non_negative_int(
                args.get("expected_head_version"),
                "expected_head_version",
            )
            if not isinstance(args.get("definition"), Mapping):
                raise _invalid("frozen Scheduler definition is invalid")
            _validate_scheduler_definition(
                task_id=str(args["task_id"]),
                definition=dict(args["definition"]),
            )
            next_scheduled_at_ms = args.get("next_scheduled_at_ms")
            if next_scheduled_at_ms is not None:
                _non_negative_int(
                    next_scheduled_at_ms,
                    "next_scheduled_at_ms",
                )
            return
        _positive_int(args.get("expected_head_version"), "expected_head_version")
        if command in _SET_ENABLED_COMMAND_IDS:
            if type(args.get("enabled")) is not bool:
                raise _invalid("scheduler enabled flag is invalid")
            if args.get("enabled") != (command == "scheduler.task.enable"):
                raise _invalid("scheduler command and enabled flag differ")
        return
    _positive_int(args.get("expected_policy_revision"), "expected_policy_revision")
    if command == "sys_mode.auto_rules.update":
        raw = args.get("rules")
        rules = dict(raw) if isinstance(raw, Mapping) else {}
        if (
            not rules
            or not set(rules).issubset(HOST_MODE_AUTO_RULES)
            or any(type(item) is not bool for item in rules.values())
        ):
            raise _invalid("frozen Host mode rules are invalid")
        return
    if _text(args.get("event_type"), "event_type") not in OPERATOR_STATE_EVENT_TYPES:
        raise _invalid("frozen Host mode event type is unsupported")


def _command_id(value: Any) -> str:
    command = str(value or "").strip()
    if command not in STATE_CONTROL_COMMAND_IDS:
        raise _invalid("unsupported governed state-control command")
    return command


def _validate_scheduler_definition(
    *,
    task_id: str,
    definition: Mapping[str, Any],
) -> dict[str, Any]:
    # Lazy import prevents the Scheduler package from entering orchestration
    # while Task Runtime itself is still initializing.
    from host_brain.scheduler.contracts import (  # noqa: PLC0415
        validate_definition_payload,
    )

    return validate_definition_payload(
        task_id=task_id,
        definition=definition,
    )


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text.encode("utf-8")) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise _invalid(f"{field} is invalid")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise _invalid(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise _invalid(f"{field} must be a non-negative integer")
    return value


def _invalid(detail: str) -> GovernedOperatorRuntimeError:
    return GovernedOperatorRuntimeError(
        GOVERNED_OPERATOR_RUNTIME_INVALID,
        detail,
    )


__all__ = [
    "OPERATOR_STATE_CONTROL_COMMAND_IDS",
    "OPERATOR_STATE_CONTROL_EFFECT_TYPE",
    "OPERATOR_STATE_CONTROL_TARGET_ID",
    "OPERATOR_STATE_EVENT_TYPES",
    "SCHEDULER_ADMIN_CONTROL_COMMAND_IDS",
    "STATE_CONTROL_COMMAND_IDS",
    "build_operator_state_control_governor_intent",
    "compile_frozen_operator_state_control",
    "normalize_operator_state_control_args",
    "normalize_scheduler_admin_control_args",
    "operator_state_control_action_kind",
    "operator_state_control_ingress_requirements",
    "operator_state_control_operation_id",
    "operator_state_control_target",
    "validate_operator_state_control_args",
]
