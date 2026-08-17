"""只签发请求绑定 Grant、绝不调用能力的轻量策略适配器。

策略将不同控制面给出的显式批准收敛为同一短时 Grant 契约；实际 Tool/Skill/MCP 调用仍
只能由 Broker 在验证 Grant 后发起，避免政策函数成为旁路执行入口。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from .broker import CapabilityDescriptor
from .contracts import CapabilityGrant, CapabilityRequest, GrantDecision


def issue_grant(request: CapabilityRequest, *, now_ms: int, approved: bool, reason_code: str, authority_ref: str = "", ttl_ms: int = 30_000, constraints: Mapping[str, Any] | None = None) -> CapabilityGrant:
    """为精确请求签发短时 Grant；批准必须携带可追溯的权威引用。"""
    # Grant ID 纳入请求摘要、决定、权威来源和签发时刻；授权不能脱离这次具体调用被复用。
    decision = GrantDecision.APPROVE if approved else GrantDecision.REJECT
    digest = hashlib.sha256(f"{request.request_hash}:{decision.value}:{authority_ref}:{now_ms}".encode()).hexdigest()
    return CapabilityGrant(
        grant_id=f"capability-grant://sha256/{digest}", request_hash=request.request_hash,
        capability_id=request.capability_id, kind=request.kind, decision=decision,
        reason_code=str(reason_code or ("CAPABILITY_APPROVED" if approved else "CAPABILITY_REJECTED")),
        authority_ref=str(authority_ref or ""), issued_at_ms=now_ms, expires_at_ms=now_ms + max(1, int(ttl_ms)),
        constraints=dict(constraints or {}),
    )


class PredicateCapabilityPolicy:
    """仅在注入的平台策略返回显式权威时签发 Grant。"""

    def __init__(self, evaluator: Callable[[CapabilityRequest, CapabilityDescriptor], tuple[bool, str, str]]) -> None:
        self._evaluator = evaluator

    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int) -> CapabilityGrant:
        """调用外部裁决并将其封装为本请求绑定的 Grant，不执行 descriptor。"""
        # 注入策略只返回决定及权威引用，实际适配器调用始终由 Broker 统一掌管。
        allowed, reason, authority_ref = self._evaluator(request, descriptor)
        return issue_grant(request, now_ms=now_ms, approved=allowed, reason_code=reason, authority_ref=authority_ref)


class DescriptorCapabilityPolicy:
    """Dispatch authorization through the policy explicitly bound to a descriptor.

    这是生产 Broker 策略：保持唯一耐久执行权威，同时允许 Graph、Skill、MCP 与 Domain
    能力保留各自失败关闭的准入规则。
    """

    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int) -> CapabilityGrant:
        """仅委托组合期绑定的策略；未绑定即拒绝而非采用默认放行。"""
        # 未在组合期绑定策略即拒绝，避免能力仅因被注册就获得默认放行。
        policy = descriptor.authorization_policy
        if policy is None:
            return issue_grant(
                request,
                now_ms=now_ms,
                approved=False,
                reason_code="CAPABILITY_POLICY_NOT_COMPOSED",
            )
        return policy.authorize(request, descriptor, now_ms=now_ms)


class GovernorToolCapabilityPolicy:
    """把 Governor 工具裁决翻译为能力 Grant，未知或异常令牌全部拒绝。"""

    def __init__(self, governor: Any, *, rules_provider: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self._governor = governor
        self._rules_provider = rules_provider or (lambda: {})

    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int) -> CapabilityGrant:
        """请求 Governor 审核单个工具调用，且只接受精确 ``GOV_APPROVE``。"""
        # Governor 仅提供显式的治理结论；缺失、异常或其他 token 全部按拒绝处理。
        if self._governor is None:
            return issue_grant(request, now_ms=now_ms, approved=False, reason_code="GOVERNOR_MISSING")
        try:
            result = self._governor.evaluate_tool_call(
                tool_calls=[{"tool": request.capability_id, "args": dict(request.args)}],
                tribe_params=dict(self._rules_provider() or {}),
            )
        except Exception as exc:
            return issue_grant(request, now_ms=now_ms, approved=False, reason_code=f"GOVERNOR_UNAVAILABLE:{type(exc).__name__}")
        token = str(getattr(result, "token", "") or "").strip()
        approved = token == "GOV_APPROVE"
        return issue_grant(
            request, now_ms=now_ms, approved=approved,
            reason_code="GOV_APPROVE" if approved else (token or "GOVERNOR_DECISION_MISSING"),
            authority_ref=f"governor-token://{token}" if approved else "",
        )


__all__ = [
    "DescriptorCapabilityPolicy",
    "GovernorToolCapabilityPolicy",
    "PredicateCapabilityPolicy",
    "issue_grant",
]
