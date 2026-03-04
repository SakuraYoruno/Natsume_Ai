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
ACTION_ALLOWLIST = {"read_only_status", "narrative_only", "memory_recall", "noop"}
TARGET_ALLOWLIST = {"host", "memory", "system"}

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
    "narrative_only": {"required": {"text": str}, "optional": {"style": str}},
    "memory_recall": {"required": {"query": str}, "optional": {"k": int}},
    "noop": {"required": {}, "optional": {}},
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


def strict_validate_intent(normalized_json: dict[str, Any]) -> tuple[IntentEnvelope | None, str | None, dict[str, Any]]:
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

    if normalized_json["type"] not in TYPE_ALLOWLIST:
        lint["schema"].append(f"type not allowed: {normalized_json['type']}")
    if normalized_json["action"] not in ACTION_ALLOWLIST:
        lint["schema"].append(f"action not allowed: {normalized_json['action']}")
    if normalized_json["target"] not in TARGET_ALLOWLIST:
        lint["schema"].append(f"target not allowed: {normalized_json['target']}")
    if normalized_json["schema_version"] != "Natsume.intent.v1":
        lint["schema"].append(
            f"schema_version not allowed: {normalized_json['schema_version']}"
        )

    if lint["schema"]:
        return None, INTENT_SCHEMA_INVALID, lint

    args_reason = _validate_args(normalized_json["action"], normalized_json["args"], lint)
    if args_reason is not None:
        return None, args_reason, lint

    return IntentEnvelope(**normalized_json), None, lint


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
