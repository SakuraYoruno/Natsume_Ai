"""Governor 之后的动作仲裁层：只保留或收紧既有授权，绝不扩张它。

它不签发令牌也不执行副作用，而是把已提交的 Governor 决定、令牌绑定、场景限制和
依赖降级状态合成为最后一道可执行性判断。任何输入不完整或彼此矛盾时均应失败关闭。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from host_brain.governor.route_token import validate_governor_route_token
from host_brain.observability.degradation_state import infer_degradation_capability
from host_brain.runtime.degradation_authority import (
    DegradationCapabilityPort,
    evaluate_degradation_capability,
)


ACTION_ARBITER_SCHEMA_VERSION = "Natsume.action_arbiter.decision.v1"
GOVERNED_ACTION_BINDING_SCHEMA_VERSION = "Natsume.governed_action.binding.v1"

ARBITER_ALLOW = "ALLOW"
ARBITER_ALLOW_WITH_RESTRICTIONS = "ALLOW_WITH_RESTRICTIONS"
ARBITER_BLOCK = "BLOCK"

ARBITER_GOVERNOR_BLOCKED = "ARBITER_GOVERNOR_BLOCKED"
ARBITER_AIRLOCK_REVIEW_REQUIRED = "ARBITER_AIRLOCK_REVIEW_REQUIRED"
ARBITER_ROUTE_TOKEN_INVALID = "ARBITER_ROUTE_TOKEN_INVALID"
ARBITER_AUTHORITY_BINDING_INVALID = "ARBITER_AUTHORITY_BINDING_INVALID"
ARBITER_ACTION_MISMATCH = "ARBITER_ACTION_MISMATCH"
ARBITER_SCENE_RESTRICTED = "ARBITER_SCENE_RESTRICTED"
ARBITER_TOKEN_REQUIRED = "ARBITER_TOKEN_REQUIRED"
ARBITER_SCOPE_MISMATCH = "ARBITER_SCOPE_MISMATCH"
ARBITER_USER_GOAL_MISMATCH = "ARBITER_USER_GOAL_MISMATCH"
ARBITER_EXPLICIT_USER_GOAL_PRESERVED = "ARBITER_EXPLICIT_USER_GOAL_PRESERVED"
ARBITER_DEGRADATION_BLOCKED = "ARBITER_DEGRADATION_BLOCKED"

_EXECUTABLE_ROUTE_TOKENS = frozenset({"GOV_OK_RENDER", "GOV_OK_CACHED", "GOV_OK_RULE"})
_PAUSED_ROUTE_TOKENS = frozenset({"GOV_AIRLOCK_REVIEW"})


def _map(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        resolved = as_dict()
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    if is_dataclass(value):
        resolved = asdict(value)
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    return {}


def _risk(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 5


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _token_digest(token_ref: str) -> str:
    return f"sha256:{hashlib.sha256(token_ref.encode('utf-8')).hexdigest()}"


def _canonical_intent(intent_envelope: Any) -> dict[str, Any]:
    intent = _map(intent_envelope)
    # 授权绑定只哈希规范化后的最小意图坐标；忽略外部对象形态差异，但保留动作、
    # scope、参数摘要和主体，防止下游用同一 token 替换实际要执行的目标。
    return {
        "schema_version": str(intent.get("schema_version") or "").strip(),
        "id": str(intent.get("id") or intent.get("intent_id") or "").strip(),
        "ts_ms": intent.get("ts_ms"),
        "session_id": str(intent.get("session_id") or "").strip(),
        "seq": intent.get("seq"),
        "type": str(intent.get("type") or "").strip(),
        "action": str(intent.get("action") or intent.get("action_kind") or "").strip(),
        "target": str(intent.get("target") or intent.get("scope") or "").strip().lower(),
        "args": dict(intent.get("args") or {}) if isinstance(intent.get("args"), Mapping) else {},
        "nonce": str(intent.get("nonce") or "").strip(),
        "person_id": str(intent.get("person_id") or "").strip() or None,
        "principal_id": str(intent.get("principal_id") or "").strip() or None,
    }


def build_governed_action_binding(
    *,
    intent_envelope: Any,
    approval_token_ref: str,
    risk_level: int,
    issued_at_ms: int,
    expires_at_ms: int,
) -> dict[str, Any]:
    """构造可信 Governor 产出的脱敏授权绑定。

    绑定把一次批准限定到特定意图、风险等级、令牌摘要与有效期；返回值故意不含
    bearer token 和原始参数，以便它可进入审计和下游状态而不扩大凭据暴露面。
    """
    intent = _canonical_intent(intent_envelope)
    token_ref = str(approval_token_ref or "").strip()
    issued = int(issued_at_ms)
    expires = int(expires_at_ms)
    if not intent["id"] or not intent["action"] or not intent["target"]:
        raise ValueError("governed action binding requires intent id/action/target")
    if not token_ref:
        raise ValueError("governed action binding requires approval token")
    if issued <= 0 or expires <= issued:
        raise ValueError("governed action binding expiry must be after issuance")
    # 返回值只携带 token 摘要，绝不把 bearer token 写进审计/下游动作；绑定摘要
    # 同时覆盖意图、风险和过期时间，令任何单字段篡改都失去可验证性。
    intent_digest = f"sha256:{_digest(intent)}"
    token_digest = _token_digest(token_ref)
    person_id = str(intent.get("person_id") or "").strip()
    # HTTP/客户端参数不可信，不能自行指定授权主体。只有 Secure Ingress 完成认证并
    # 冻结服务身份后，生产入口才能设置 principal_id；否则沿用服务端推导的会话人物，
    # 防止调用方借改主体窃取已批准操作的身份上下文。
    principal_id = str(intent.get("principal_id") or person_id).strip()
    binding_digest = _digest(
        {
            "intent_digest": intent_digest,
            "risk_level": _risk(risk_level),
            "token_digest": token_digest,
            "expires_at_ms": expires,
        }
    )
    return {
        "schema_version": GOVERNED_ACTION_BINDING_SCHEMA_VERSION,
        "binding_id": f"governed_action_{binding_digest[:24]}",
        "binding_ref": f"governed-action://sha256/{binding_digest}",
        "intent_id": intent["id"],
        "session_id": intent["session_id"],
        "seq": intent["seq"],
        "action_kind": intent["action"],
        "scope": intent["target"],
        "person_id": person_id or None,
        "principal_id": principal_id or None,
        "risk_level": _risk(risk_level),
        "intent_digest": intent_digest,
        "intent_args_digest": f"sha256:{_digest(intent['args'])}",
        "approval_token_digest": token_digest,
        "issued_at_ms": issued,
        "expires_at_ms": expires,
        "read_only": True,
    }


def _token_ref(governor: Mapping[str, Any]) -> str:
    tokens = governor.get("tokens")
    token_map = dict(tokens) if isinstance(tokens, Mapping) else {}
    return str(token_map.get("approval_token_ref") or "").strip()


def _route_token(governor: Mapping[str, Any]) -> tuple[str, bool]:
    validation = validate_governor_route_token(governor)
    return str(validation.token or "").strip().upper(), bool(validation.consistent)


def _scene_expired(scene: Mapping[str, Any], now_ms: int) -> bool:
    if bool(scene.get("expired")):
        return True
    try:
        expires = int(scene.get("expires_at_ms") or 0)
    except (TypeError, ValueError):
        # A malformed expiry must not disable an otherwise restrictive scene.
        return False
    return bool(expires and now_ms >= expires)


def _restrictive_scene_policy(scene: Mapping[str, Any], constraints: Mapping[str, Any]) -> str:
    rank = {"ALLOW": 0, "CONTEXTUAL": 1, "BLOCK": 2}
    raw_values = [
        str(scene.get("proactive_policy") or "ALLOW").strip().upper(),
        str(constraints.get("proactive_policy") or "ALLOW").strip().upper(),
    ]
    values = [value if value in rank else "BLOCK" for value in raw_values]
    return max(values, key=rank.__getitem__)


def _binding_status(
    *,
    binding: Mapping[str, Any],
    intent_envelope: Any,
    token_ref: str,
    now_ms: int,
) -> tuple[bool, str]:
    if binding.get("schema_version") != GOVERNED_ACTION_BINDING_SCHEMA_VERSION:
        return False, "schema"
    try:
        expires_at_ms = int(binding.get("expires_at_ms") or 0)
    except (TypeError, ValueError):
        return False, "expiry"
    if expires_at_ms <= now_ms:
        return False, "expired"
    if not token_ref or binding.get("approval_token_digest") != _token_digest(token_ref):
        return False, "token"
    intent = _canonical_intent(intent_envelope)
    # token 本身正确仍不够：它必须绑定当前完整规范意图且未过期，不能作为同类
    # 动作的通用通行证。
    if not intent["id"] or binding.get("intent_digest") != f"sha256:{_digest(intent)}":
        return False, "intent"
    return True, "valid"


def arbitrate_action(
    *,
    governor_decision: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    intent_envelope: Any = None,
    scene_decision: Any = None,
    user_intent: Mapping[str, Any] | None = None,
    now_ms: int | None = None,
    enabled: bool = True,
    degradation_capability_port: DegradationCapabilityPort | None = None,
) -> dict[str, Any]:
    """在已提交且绑定意图的 Governor 决定之后施加只收紧的执行约束。

    返回的是可审计的裁决描述，不会实际调用工具。即使此前批准，令牌闭合验证、绑定
    过期、动作/范围替换、场景限制或关键依赖降级中的任一失败都可把结果收紧为阻断。
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    governor = dict(governor_decision or {})
    action = dict(proposed_action or {})
    scene = _map(scene_decision)
    intent = dict(user_intent or {})
    envelope = intent_envelope if intent_envelope is not None else action.get("intent_envelope")

    governor_status = str(governor.get("decision") or "REJECT").strip().upper()
    route_token, route_consistent = _route_token(governor)
    binding = _map(governor.get("approved_action_binding"))
    approval_token_ref = _token_ref(governor)
    binding_valid, binding_reason = _binding_status(
        binding=binding,
        intent_envelope=envelope,
        token_ref=approval_token_ref,
        now_ms=now,
    )
    # 后置裁决器只能保留或收紧 Governor 已提交的权限：所有后续检查围绕该 binding
    # 的动作、范围与风险运行，不能凭 proposed_action 自行提升执行能力。

    action_kind = str(action.get("action_kind") or action.get("action") or "noop").strip() or "noop"
    requested_scope = str(action.get("scope") or action.get("target") or "").strip().lower()
    approved_action_kind = str(binding.get("action_kind") or "").strip()
    approved_scope = str(binding.get("scope") or "").strip().lower()
    approved_risk = _risk(binding.get("risk_level"))
    original_risk = _risk(action.get("risk_level", approved_risk))
    effective_risk = max(original_risk, approved_risk)
    token_required = governor_status == "APPROVE" or bool(action.get("requires_token"))
    explicit_user_goal = bool(intent.get("explicit") or intent.get("explicit_user_intent"))
    user_intent_id = str(intent.get("intent_id") or "").strip()
    user_action_kind = str(intent.get("action_kind") or "").strip()
    user_goal_matches = (
        not enabled
        or not explicit_user_goal
        or (
            binding_valid
            and (not user_intent_id or user_intent_id == str(binding.get("intent_id") or ""))
            and (not user_action_kind or user_action_kind == approved_action_kind)
        )
    )
    action_matches = bool(approved_action_kind and action_kind == approved_action_kind)
    scope_matches = bool(approved_scope and requested_scope == approved_scope)
    is_proactive = bool(action.get("is_proactive")) and not explicit_user_goal
    scene_expired = _scene_expired(scene, now)
    scene_constraints = _map(scene.get("constraints"))
    scene_policy = (
        "ALLOW"
        if scene_expired or not enabled
        else _restrictive_scene_policy(scene, scene_constraints)
    )

    decision = ARBITER_ALLOW
    execution_state = "EXECUTABLE"
    reasons: list[str] = []
    restrictions: list[str] = []
    if governor_status != "APPROVE":
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_GOVERNOR_BLOCKED)
    elif not route_consistent:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_ROUTE_TOKEN_INVALID)
    elif route_token in _PAUSED_ROUTE_TOKENS:
        decision = ARBITER_BLOCK
        execution_state = "PAUSED_REVIEW"
        reasons.append(ARBITER_AIRLOCK_REVIEW_REQUIRED)
    elif route_token not in _EXECUTABLE_ROUTE_TOKENS:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_ROUTE_TOKEN_INVALID)
    elif token_required and not approval_token_ref:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_TOKEN_REQUIRED)
    elif enabled and not binding_valid:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.extend([ARBITER_AUTHORITY_BINDING_INVALID, f"ARBITER_BINDING_{binding_reason.upper()}"])
    elif enabled and not action_matches:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_ACTION_MISMATCH)
    elif enabled and not scope_matches:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_SCOPE_MISMATCH)
    elif not user_goal_matches:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_USER_GOAL_MISMATCH)
    elif scene_policy == "BLOCK" and is_proactive:
        decision = ARBITER_BLOCK
        execution_state = "BLOCKED"
        reasons.append(ARBITER_SCENE_RESTRICTED)
    elif scene_policy in {"BLOCK", "CONTEXTUAL"}:
        # 场景限制不得消解明确的用户前台目标；对主动行为可完全阻断，对当前用户
        # 请求则以受限允许保留目标并禁止额外跟进。
        decision = ARBITER_ALLOW_WITH_RESTRICTIONS
        reasons.append(ARBITER_SCENE_RESTRICTED)
        restrictions.extend(
            [
                "preserve_user_goal",
                f"expression_mode:{scene.get('expression_mode') or 'restrained'}",
                "no_unsolicited_followup",
            ]
        )
        if explicit_user_goal:
            reasons.append(ARBITER_EXPLICIT_USER_GOAL_PRESERVED)

    if not enabled:
        reasons.append("ACTION_ARBITER_DISABLED_GOVERNOR_PRESERVED")
    elif scene_expired:
        reasons.append("SCENE_DECISION_EXPIRED_IGNORED")

    effective_action_kind = approved_action_kind if binding_valid and approved_action_kind else action_kind
    effective_scope = approved_scope if binding_valid and approved_scope else requested_scope
    capability_id = infer_degradation_capability(
        action.get("capability_id"),
        effective_action_kind,
        effective_scope,
        action.get("capability_tags"),
        default=("high_risk_execution" if effective_risk >= 3 else "general_execution"),
    )
    capability_policy_decision = action.get("capability_policy_decision")
    if not isinstance(capability_policy_decision, Mapping):
        capability_policy_decision = governor.get("capability_policy_decision")
    degradation_decision = evaluate_degradation_capability(
        degradation_capability_port,
        capability_id=capability_id,
        static_risk_level=effective_risk,
        capability_policy_decision=(
            capability_policy_decision
            if isinstance(capability_policy_decision, Mapping)
            else None
        ),
    )
    degradation_allowed = bool(
        degradation_decision.get(
            "allowed",
            degradation_decision.get("allowed_by_degradation", False),
        )
    )
    if not degradation_allowed:
        # 依赖降级是最终 fail-closed 覆盖层：即使 Governor/binding 已允许，也不能
        # 在关键授权来源不可确认时继续执行高风险动作。
        was_already_blocked = decision == ARBITER_BLOCK
        decision = ARBITER_BLOCK
        if not was_already_blocked:
            execution_state = "BLOCKED"
        reasons.extend(
            [
                ARBITER_DEGRADATION_BLOCKED,
                str(
                    degradation_decision.get("reason_code")
                    or "DEGRADATION_STATE_UNAVAILABLE"
                ),
            ]
        )
        restrictions.append("dependency_degradation_fail_closed")
    if not reasons:
        reasons.append("ARBITER_NO_ADDITIONAL_RESTRICTION")

    user_goal_preserved = bool(user_goal_matches and (not enabled or (action_matches and scope_matches)))
    seed_payload = {
        "action_kind": effective_action_kind,
        "binding_ref": binding.get("binding_ref"),
        "decision": decision,
        "degradation_decision": degradation_decision.get("decision"),
        "governor": governor_status,
        "risk": effective_risk,
        "route_token": route_token,
        "scene_id": scene.get("decision_id"),
        "scope": effective_scope,
        "ts_ms": now,
    }
    digest = _digest(seed_payload)
    output = {
        "schema_version": ACTION_ARBITER_SCHEMA_VERSION,
        "decision_id": f"action_arbiter_{digest[:24]}",
        "decision": decision,
        "execution_state": execution_state,
        "reason_codes": list(dict.fromkeys(reasons)),
        "action_kind": action_kind,
        "effective_action_kind": effective_action_kind,
        "original_scope": requested_scope,
        "effective_scope": effective_scope,
        "original_risk_level": original_risk,
        "approved_risk_level": approved_risk,
        "effective_risk_level": effective_risk,
        "token_required": token_required,
        "token_present": bool(approval_token_ref),
        "token_ref_preserved": bool(approval_token_ref and (binding_valid or not enabled)),
        "authority_binding_ref": str(binding.get("binding_ref") or ""),
        "authority_binding_valid": binding_valid,
        "authority_binding_reason": binding_reason,
        "governor_decision": governor_status,
        "governor_reason_code": governor.get("reason_code"),
        "committed_route_token": route_token,
        "scene_decision_ref": (
            f"scene-decision://{scene.get('decision_id')}" if scene.get("decision_id") else ""
        ),
        "scene_expired": scene_expired,
        "restrictions": restrictions,
        "degradation_capability_id": capability_id,
        "degradation_decision": degradation_decision,
        "explicit_user_goal": explicit_user_goal,
        "user_goal_ref": str(intent.get("goal_ref") or scene.get("user_goal_ref") or "").strip(),
        "user_goal_preserved": user_goal_preserved,
        "may_relax_governor": False,
        "may_expand_scope": False,
        "may_reduce_risk": False,
        "may_bypass_token": False,
        "side_effects_executed": False,
        "decided_at_ms": now,
    }
    _assert_restrict_only(output, governor_status=governor_status, enabled=enabled)
    return output


def _assert_restrict_only(
    output: Mapping[str, Any],
    *,
    governor_status: str,
    enabled: bool,
) -> None:
    if governor_status != "APPROVE" and output.get("decision") != ARBITER_BLOCK:
        raise RuntimeError("ActionArbiter cannot relax a Governor rejection")
    if int(output.get("effective_risk_level") or 0) < int(output.get("original_risk_level") or 0):
        raise RuntimeError("ActionArbiter cannot reduce risk")
    if bool(output.get("may_relax_governor")) or bool(output.get("may_expand_scope")):
        raise RuntimeError("ActionArbiter restrict-only invariant failed")
    if bool(output.get("may_bypass_token")):
        raise RuntimeError("ActionArbiter cannot bypass a token")
    if enabled and output.get("decision") != ARBITER_BLOCK:
        if not bool(output.get("authority_binding_valid")):
            raise RuntimeError("ActionArbiter cannot execute without a valid authority binding")
        if output.get("committed_route_token") not in _EXECUTABLE_ROUTE_TOKENS:
            raise RuntimeError("ActionArbiter cannot execute a non-executable route token")
        if output.get("effective_action_kind") != output.get("action_kind"):
            raise RuntimeError("ActionArbiter cannot substitute the approved action")
        if output.get("effective_scope") != output.get("original_scope"):
            raise RuntimeError("ActionArbiter cannot substitute the approved scope")


class ActionArbiter:
    """为调用方固定仲裁开关和降级能力端口的轻量门面。

    每次 ``decide`` 仍无状态地重算裁决，避免把上一请求的令牌、场景或授权绑定复用到
    当前请求；共享的仅是明确注入、可替换的降级观测端口。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        degradation_capability_port: DegradationCapabilityPort | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._degradation_capability_port = degradation_capability_port

    def decide(self, **kwargs: Any) -> dict[str, Any]:
        """使用实例默认安全配置委托一次独立的动作仲裁。"""
        kwargs.setdefault("enabled", self._enabled)
        kwargs.setdefault(
            "degradation_capability_port",
            self._degradation_capability_port,
        )
        return arbitrate_action(**kwargs)


__all__ = [
    "ACTION_ARBITER_SCHEMA_VERSION",
    "GOVERNED_ACTION_BINDING_SCHEMA_VERSION",
    "ARBITER_ALLOW",
    "ARBITER_ALLOW_WITH_RESTRICTIONS",
    "ARBITER_BLOCK",
    "ActionArbiter",
    "arbitrate_action",
    "build_governed_action_binding",
]
