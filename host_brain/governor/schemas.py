"""Intent parsing and schema validation contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    EXPIRED_INTENT,
    GOVERNOR_OUTPUT_INVALID,
    INTENT_ARGS_INVALID,
    INTENT_PARSE_FAILED,
    INTENT_SCHEMA_INVALID,
    REPLAY_DETECTED,
    SEQ_INVALID,
    SKILL_TOKEN_MISSING,
)

PARSE_LENIENT_OK = "LENIENT_OK"
PARSE_LENIENT_FAIL = "LENIENT_FAIL"

TYPE_ALLOWLIST = {"skill_call", "query", "control"}
ACTION_ALLOWLIST = {
    "read_only_status",
    "narrative_only",
    "memory_recall",
    "noop",
    # M45：场景路由 action（由 chat_handler._action_map 注入，Governor 需感知真实意图类型）
    "exec_intent",
    # Server-owned Operator control intent.  It is not a discoverable Skill;
    # the action exists only so Governor can bind an exact typed payload.
    "operator_sys_mode_transition",
    "operator_edge_command_push",
    "operator_task_terminal_reconciliation",
    "operator_task_operation_reconciliation",
    "operator_task_effect_reconciliation",
    "operator_task_graph_cancellation",
    "operator_task_review_control",
    "operator_scheduler_state_control",
    "operator_host_mode_policy_control",
    "operator_long_term_goal_control",
    "work_code",
    "work_translate",
}
TARGET_ALLOWLIST = {
    "edge",
    "host",
    "memory",
    "system",
    "task_runtime",
}
ACTION_TARGET_RULES = {
    "operator_edge_command_push": "edge",
    "operator_task_operation_reconciliation": "task_runtime",
    "operator_task_effect_reconciliation": "task_runtime",
    "operator_task_graph_cancellation": "task_runtime",
    "operator_task_review_control": "task_runtime",
    "operator_scheduler_state_control": "system",
    "operator_host_mode_policy_control": "system",
    "operator_long_term_goal_control": "system",
}

REQUIRED_FIELDS: dict[str, type] = {
    "schema_version": str,
    "id": str,
    "ts_ms": int,
    "session_id": str,
    "seq": int,
    "type": str,
    "action": str,
    "target": str,
    "args": dict,
    "nonce": str,
}

ACTION_ARGS_RULES: dict[str, dict[str, dict[str, type]]] = {
    "read_only_status": {"required": {"scope": str}, "optional": {}},
    "narrative_only": {
        "required": {"text": str},
        "optional": {
            "style": str,
            "semantic_intent": dict,
            "pragmatic_frame": dict,
            "conversation_understanding": dict,
        },
    },
    "memory_recall": {"required": {"query": str}, "optional": {"k": int}},
    "noop": {"required": {}, "optional": {}},
    # M45：场景路由 action — args 只要包含 text 字段即可；
    # 允许计划路由把显式步骤和路由提示作为扩展参数透传给下游执行层。
    # CAPABILITY_SEARCH_V1 uses the same Governor-bound envelope: the Host
    # later re-derives candidates from its composed registry and never trusts
    # a caller-supplied candidate list.
    "exec_intent": {
        "required": {"text": str},
        "optional": {
            "planned_steps": list,
            "steps": list,
            "task": str,
            "planning_mode": str,
            "planning_capability_tags": list,
            "planning_max_risk_level": int,
            "route": str,
            "target_id": str,
            "target": str,
            "occurrence_ref": str,
            "sys_mode": str,
            # Thalamus attaches this canonical understanding field to every
            # execution intent before Governor evaluation.
            "semantic_intent": dict,
            "pragmatic_frame": dict,
            "conversation_understanding": dict,
        },
    },
    "operator_sys_mode_transition": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "target_mode": str,
            "source": str,
            "reason": str,
        },
        "optional": {},
    },
    "operator_edge_command_push": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "command_type": str,
            "target_agent": str,
            "payload": dict,
            "embodiment_runtime_scope_id": str,
        },
        "optional": {},
    },
    "operator_task_terminal_reconciliation": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "case_id": str,
            "signed_command_ref": str,
            "signed_command_sha256": str,
        },
        "optional": {},
    },
    "operator_task_operation_reconciliation": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "evidence_ref": str,
            "evidence_digest": str,
            "reservation_id": str,
            "expected_version": int,
            "action": str,
            "reconciler_actor_id": str,
        },
        "optional": {
            "recovery_case_ref": str,
            "recovery_case_subject_sha256": str,
        },
    },
    "operator_task_effect_reconciliation": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "evidence_ref": str,
            "evidence_digest": str,
            "effect_id": str,
            "case_id": str,
            "purpose": str,
            "outcome": str,
            "reconciler_actor_id": str,
        },
        "optional": {},
    },
    "operator_task_graph_cancellation": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "request_id": str,
            "operator_actor_id": str,
            "graph_id": str,
            "graph_revision": int,
            "reason_code": str,
        },
        "optional": {},
    },
    "operator_task_review_control": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "request_id": str,
            "operator_actor_id": str,
            "review_id": str,
        },
        "optional": {
            "actor_id": str,
            "subject_ref": str,
            "subject_version": int,
            "resume_phase": str,
            "checkpoint_id": str,
            "resume_binding_sha256": str,
            "resume_result_ref": str,
        },
    },
    "operator_scheduler_state_control": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "request_id": str,
            "operator_actor_id": str,
            "observed_at_ms": int,
            "task_id": str,
            "expected_head_version": int,
        },
        "optional": {
            "enabled": bool,
            "definition": dict,
            "next_scheduled_at_ms": object,
            "scheduler_ingress_method": str,
            "scheduler_ingress_path": str,
        },
    },
    "operator_host_mode_policy_control": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "request_id": str,
            "operator_actor_id": str,
            "observed_at_ms": int,
            "expected_policy_revision": int,
        },
        "optional": {
            "rules": dict,
            "event_type": str,
        },
    },
    "operator_long_term_goal_control": {
        "required": {
            "text": str,
            "route": str,
            "target_id": str,
            "operator_command_id": str,
            "request_id": str,
            "operator_actor_id": str,
            "observed_at_ms": int,
            "workspace_id": str,
            "subject_id": str,
            "goal_id": str,
            "expected_version": int,
        },
        "optional": {
            "constraints": list,
        },
    },
    "work_code": {"required": {"text": str}, "optional": {}},
    "work_translate": {"required": {"text": str}, "optional": {}},
}

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"


@dataclass(frozen=True)
class IntentEnvelope:
    schema_version: str
    id: str
    ts_ms: int
    session_id: str
    seq: int
    type: str
    action: str
    target: str
    args: dict[str, Any]
    nonce: str
    person_id: str | None = None
    principal_id: str | None = None


@dataclass(frozen=True)
class IntentParseResult:
    normalized_json: dict[str, Any] | None
    parse_stage: str
    reason_code: str | None
    lint_result: dict[str, Any]


@dataclass(frozen=True)
class GovernorOutputValidationResult:
    valid: bool
    reason_code: str | None
    violations: list[str]


def lenient_parse_intent(raw_input: str | dict[str, Any]) -> IntentParseResult:
    """Parse raw intent without adding semantic fields."""

    if isinstance(raw_input, dict):
        return IntentParseResult(
            normalized_json=raw_input,
            parse_stage=PARSE_LENIENT_OK,
            reason_code=None,
            lint_result={"parse_error": None},
        )

    if not isinstance(raw_input, str):
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": f"input type must be str|dict, got {type(raw_input).__name__}"},
        )

    text = raw_input.strip()
    if not text:
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": "input is empty"},
        )

    start = text.find("{")
    if start < 0:
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": "no json object found"},
        )

    candidate = text[start:]
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": f"json decode error at pos={exc.pos}: {exc.msg}"},
        )

    trailing = candidate[end:].strip()
    if trailing and "{" in trailing:
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": "multiple json objects detected"},
        )

    if not isinstance(parsed, dict):
        return IntentParseResult(
            normalized_json=None,
            parse_stage=PARSE_LENIENT_FAIL,
            reason_code=INTENT_PARSE_FAILED,
            lint_result={"parse_error": f"json root must be object, got {type(parsed).__name__}"},
        )

    return IntentParseResult(
        normalized_json=parsed,
        parse_stage=PARSE_LENIENT_OK,
        reason_code=None,
        lint_result={"parse_error": None},
    )


def strict_validate_intent(
    normalized_json: dict[str, Any],
    extra_action_allowlist: set[str] | None = None,
) -> tuple[IntentEnvelope | None, str | None, dict[str, Any]]:
    """严格校验 intent 格式。

    Args:
        normalized_json: 已解析的 intent 字典
        extra_action_allowlist: M42 扩展 — 动态注册的 Skill action 白名单（来自 SkillRegistry）。
            若提供，则允许 action 为 ACTION_ALLOWLIST ∪ extra_action_allowlist 中的任意值。
            对于 extra_action_allowlist 中的 action，args 校验规则宽松（允许任意 dict）。
    """
    lint: dict[str, Any] = {"schema": [], "args": []}

    for field_name, expected_type in REQUIRED_FIELDS.items():
        if field_name not in normalized_json:
            lint["schema"].append(f"missing field: {field_name}")
            continue
        if not isinstance(normalized_json[field_name], expected_type):
            lint["schema"].append(
                (
                    f"invalid type for {field_name}: expected "
                    f"{expected_type.__name__}, got {type(normalized_json[field_name]).__name__}"
                )
            )

    if lint["schema"]:
        return None, INTENT_SCHEMA_INVALID, lint

    for identity_field in ("person_id", "principal_id"):
        if identity_field in normalized_json and (
            not isinstance(normalized_json[identity_field], str)
            or not normalized_json[identity_field].strip()
        ):
            lint["schema"].append(f"invalid trusted identity field: {identity_field}")
    if lint["schema"]:
        return None, INTENT_SCHEMA_INVALID, lint

    # M42：合并静态白名单和动态 Skill 白名单
    effective_action_allowlist = ACTION_ALLOWLIST | (extra_action_allowlist or set())

    if normalized_json["type"] not in TYPE_ALLOWLIST:
        lint["schema"].append(f"type not allowed: {normalized_json['type']}")
    if normalized_json["action"] not in effective_action_allowlist:
        lint["schema"].append(f"action not allowed: {normalized_json['action']}")
    if normalized_json["target"] not in TARGET_ALLOWLIST:
        lint["schema"].append(f"target not allowed: {normalized_json['target']}")
    required_target = ACTION_TARGET_RULES.get(str(normalized_json["action"]))
    if required_target is not None and normalized_json["target"] != required_target:
        lint["schema"].append(
            f"target for {normalized_json['action']} must be {required_target}"
        )
    if (
        normalized_json["target"] == "edge"
        and normalized_json["action"] != "operator_edge_command_push"
    ):
        lint["schema"].append(
            "edge target is reserved for operator_edge_command_push"
        )
    if normalized_json["schema_version"] != "Natsume.intent.v1":
        lint["schema"].append(
            f"schema_version not allowed: {normalized_json['schema_version']}"
        )

    if lint["schema"]:
        return None, INTENT_SCHEMA_INVALID, lint

    # M42：动态 Skill 的 args 校验宽松（允许任意 dict，不要求特定字段）
    action = normalized_json["action"]
    if extra_action_allowlist and action in extra_action_allowlist and action not in ACTION_ARGS_RULES:
        # 动态注册的 Skill：args 只要是 dict 即可（已在 REQUIRED_FIELDS 中校验）
        pass
    else:
        args_reason = _validate_args(action, normalized_json["args"], lint)
        if args_reason is not None:
            return None, args_reason, lint

    # NOTE:
    # normalized_json may carry extension fields (e.g. route/planned_steps) that
    # are intentionally consumed by downstream runtime routers. Governor strict
    # validation only binds the canonical envelope fields and ignores unknown
    # top-level extension keys to preserve forward compatibility.
    envelope_payload: dict[str, Any] = {
        key: normalized_json[key]
        for key in REQUIRED_FIELDS
    }
    if "person_id" in normalized_json:
        envelope_payload["person_id"] = normalized_json.get("person_id")
    if "principal_id" in normalized_json:
        envelope_payload["principal_id"] = normalized_json.get("principal_id")
    return IntentEnvelope(**envelope_payload), None, lint


def _validate_args(action: str, args: dict[str, Any], lint: dict[str, Any]) -> str | None:
    rules = ACTION_ARGS_RULES.get(action)
    if rules is None:
        lint["schema"].append(f"missing args rule for action: {action}")
        return INTENT_SCHEMA_INVALID

    required = rules["required"]
    optional = rules["optional"]
    allowed_keys = set(required.keys()) | set(optional.keys())

    for key, expected_type in required.items():
        if key not in args:
            lint["args"].append(f"missing required arg for {action}: {key}")
            continue
        if not isinstance(args[key], expected_type):
            lint["args"].append(
                f"invalid arg type {key}: expected {expected_type.__name__}, got {type(args[key]).__name__}"
            )

    for key, value in args.items():
        if key not in allowed_keys:
            lint["args"].append(f"unknown arg for {action}: {key}")
            continue

        expected_type = required.get(key) or optional.get(key)
        if expected_type and not isinstance(value, expected_type):
            lint["args"].append(
                f"invalid arg type {key}: expected {expected_type.__name__}, got {type(value).__name__}"
            )

    if lint["args"]:
        return INTENT_ARGS_INVALID
    return None


def validate_governor_output(payload: dict[str, Any]) -> GovernorOutputValidationResult:
    violations: list[str] = []

    if not isinstance(payload, dict):
        return GovernorOutputValidationResult(
            valid=False,
            reason_code=GOVERNOR_OUTPUT_INVALID,
            violations=["payload must be object"],
        )

    decision = payload.get("decision")
    if decision not in {DECISION_APPROVE, DECISION_REJECT}:
        violations.append(f"decision must be APPROVE|REJECT, got {decision!r}")

    rule_trace = payload.get("rule_trace")
    if not isinstance(rule_trace, list) or not rule_trace:
        violations.append("rule_trace must be non-empty list")

    if decision == DECISION_REJECT:
        reason_code = payload.get("reason_code")
        if not isinstance(reason_code, str) or not reason_code.strip():
            violations.append("REJECT must include non-empty reason_code")
            return GovernorOutputValidationResult(
                valid=False,
                reason_code=GOVERNOR_OUTPUT_INVALID,
                violations=violations,
            )

    if decision == DECISION_APPROVE:
        tokens = payload.get("tokens")
        approval_token_ref = None
        if isinstance(tokens, dict):
            approval_token_ref = tokens.get("approval_token_ref")
        if not isinstance(approval_token_ref, str) or not approval_token_ref.strip():
            violations.append("APPROVE must include tokens.approval_token_ref")
            return GovernorOutputValidationResult(
                valid=False,
                reason_code=SKILL_TOKEN_MISSING,
                violations=violations,
            )

    if violations:
        return GovernorOutputValidationResult(
            valid=False,
            reason_code=GOVERNOR_OUTPUT_INVALID,
            violations=violations,
        )

    return GovernorOutputValidationResult(valid=True, reason_code=None, violations=[])
