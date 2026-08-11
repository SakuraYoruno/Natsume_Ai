"""Committed route token definitions for Governed Runtime Contract (M34-C).

Host Governor 将裁决结果编译为有限令牌（CommittedRouteDecision），
LangGraph 条件边只读取 committed_route_token，不直接消费 LLM 原始输出。

设计原则（ModelPlane.md §10 Route Commitment Rule）：
- LLM 只能输出 proposed_* 字段（提案）
- Host Governor 才能写入 committed_* 字段（裁决）
- 条件边只能基于 committed_route_token 跳转
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RouteToken(str, Enum):
    """Governor 裁决结果的有限令牌枚举。

    每个令牌代表一种不可歧义的路由决策，由 Host Governor 编译后写入
    graph_state.committed_route_token，LangGraph 条件边只读取此字段。
    """

    # 裁决通过，允许进入渲染阶段（S2）
    GOV_OK_RENDER = "GOV_OK_RENDER"

    # 裁决失败，降级到 fallback 链路（core_reasoner_secondary 或模板）
    GOV_FAIL_FALLBACK = "GOV_FAIL_FALLBACK"

    # 需要 AIRLOCK 人工审核，暂停执行
    GOV_AIRLOCK_REVIEW = "GOV_AIRLOCK_REVIEW"

    # 降级到 secondary reasoner（primary 超时/格式漂移时）
    GOV_SECONDARY_REASONER = "GOV_SECONDARY_REASONER"

    # M42：分层治理令牌
    # Policy Cache 命中直接放行（risk_level=1，~0ms）
    GOV_OK_CACHED = "GOV_OK_CACHED"

    # 规则引擎审批通过（risk_level=2，~5ms，不调用 LLM）
    GOV_OK_RULE = "GOV_OK_RULE"


class RouteTokenDisposition(str, Enum):
    """Execution disposition produced by the trusted route-token validator."""

    ALLOW = "ALLOW"
    PAUSE = "PAUSE"
    BLOCK = "BLOCK"


ROUTE_TOKEN_EXECUTABLE = "ROUTE_TOKEN_EXECUTABLE"
ROUTE_TOKEN_AIRLOCK_REVIEW = "ROUTE_TOKEN_AIRLOCK_REVIEW"
ROUTE_TOKEN_REF_MISSING = "ROUTE_TOKEN_REF_MISSING"
ROUTE_TOKEN_DETAIL_MISSING = "ROUTE_TOKEN_DETAIL_MISSING"
ROUTE_TOKEN_DETAIL_TOKEN_MISSING = "ROUTE_TOKEN_DETAIL_TOKEN_MISSING"
ROUTE_TOKEN_REF_DETAIL_MISMATCH = "ROUTE_TOKEN_REF_DETAIL_MISMATCH"
ROUTE_TOKEN_NOT_EXECUTABLE = "ROUTE_TOKEN_NOT_EXECUTABLE"

_EXECUTABLE_ROUTE_TOKENS = frozenset(
    {
        RouteToken.GOV_OK_RENDER.value,
        RouteToken.GOV_OK_CACHED.value,
        RouteToken.GOV_OK_RULE.value,
    }
)


@dataclass(frozen=True)
class RouteTokenValidation:
    """Fail-closed validation result for a committed route-token pair."""

    disposition: RouteTokenDisposition
    reason_code: str
    token: str
    token_ref: str
    detail_token: str
    consistent: bool

    @property
    def executable(self) -> bool:
        return self.disposition == RouteTokenDisposition.ALLOW

    @property
    def paused(self) -> bool:
        return self.disposition == RouteTokenDisposition.PAUSE

    @property
    def blocked(self) -> bool:
        return self.disposition == RouteTokenDisposition.BLOCK

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "reason_code": self.reason_code,
            "token": self.token,
            "token_ref": self.token_ref,
            "detail_token": self.detail_token,
            "consistent": self.consistent,
            "executable": self.executable,
        }


@dataclass(frozen=True)
class CommittedRouteDecision:
    """Governor 已提交的路由裁决（不可变）。

    只有 Host Governor 可以创建此对象并写入 graph_state.committed_route_token。
    LLM 节点不可写入此字段（权限约束由 Host 端点层强制执行）。

    字段说明：
    - token: 路由令牌（RouteToken 枚举值）
    - reason_code: 裁决原因代码（APPROVE/REJECT 的具体原因）
    - committed_at_ms: 编译时间戳（毫秒，单调时钟）
    - proposer_node_id: 提案节点 ID（通常为 Governor 节点）
    """

    token: RouteToken
    reason_code: str
    committed_at_ms: int
    proposer_node_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.token, RouteToken):
            raise TypeError(f"token 必须是 RouteToken 枚举，got {type(self.token).__name__}")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("reason_code 不能为空")
        if not isinstance(self.committed_at_ms, int) or self.committed_at_ms <= 0:
            raise ValueError(f"committed_at_ms 必须为正整数，got {self.committed_at_ms!r}")
        if not isinstance(self.proposer_node_id, str) or not self.proposer_node_id.strip():
            raise ValueError("proposer_node_id 不能为空")


def compile_route_decision(
    *,
    approved: bool,
    reason_code: str | None,
    committed_at_ms: int,
    proposer_node_id: str = "governor",
    tier: str = "full",
) -> CommittedRouteDecision:
    """将 Governor evaluate_intent 的裁决结果编译为 CommittedRouteDecision。

    映射规则（TASK-0316 M34-D；M42 扩展分层令牌）：
    - approved=True, tier="cache"            → GOV_OK_CACHED（Policy Cache 命中）
    - approved=True, tier="rule"             → GOV_OK_RULE（规则引擎审批通过）
    - approved=True, tier="full"（默认）      → GOV_OK_RENDER
    - approved=True, tier="airlock"          → GOV_AIRLOCK_REVIEW（高风险暂停）
    - approved=False, reason=AIRLOCK*        → GOV_AIRLOCK_REVIEW
    - approved=False, reason=其他            → GOV_FAIL_FALLBACK

    Args:
        approved: Governor 是否批准
        reason_code: 拒绝原因代码（approved=True 时可为 None）
        committed_at_ms: 编译时间戳（毫秒）
        proposer_node_id: 提案节点 ID
        tier: 审批通道（"cache" / "rule" / "full" / "airlock"），默认 "full"

    Returns:
        CommittedRouteDecision（不可变）
    """
    if approved:
        tier_lower = str(tier).strip().lower()
        if tier_lower == "cache":
            token = RouteToken.GOV_OK_CACHED
        elif tier_lower == "rule":
            token = RouteToken.GOV_OK_RULE
        elif tier_lower == "airlock":
            token = RouteToken.GOV_AIRLOCK_REVIEW
        else:
            token = RouteToken.GOV_OK_RENDER
        return CommittedRouteDecision(
            token=token,
            reason_code=reason_code or "APPROVE",
            committed_at_ms=committed_at_ms,
            proposer_node_id=proposer_node_id,
        )

    rc = str(reason_code or "REJECT").strip().upper()
    # AIRLOCK 相关原因代码
    if "AIRLOCK" in rc or rc in ("EMOTION_GATE_BLOCKED",):
        token = RouteToken.GOV_AIRLOCK_REVIEW
    else:
        token = RouteToken.GOV_FAIL_FALLBACK

    return CommittedRouteDecision(
        token=token,
        reason_code=rc,
        committed_at_ms=committed_at_ms,
        proposer_node_id=proposer_node_id,
    )


def _normalize_route_token(value: Any) -> str:
    if isinstance(value, RouteToken):
        return value.value
    return str(value or "").strip().upper()


def validate_committed_route_token(
    *,
    token_ref: Any,
    token_detail: Mapping[str, Any] | None,
) -> RouteTokenValidation:
    """Validate the redundant committed route-token representation.

    The Governor publishes both ``committed_route_token_ref`` and the token in
    ``committed_route_token``.  Execution is allowed only when both values are
    present, agree, and name one of the three explicit executable tokens.
    AIRLOCK is a paused review state; every other missing, malformed, unknown,
    fallback, or secondary token is blocked.
    """
    ref_token = _normalize_route_token(token_ref)
    detail_is_mapping = isinstance(token_detail, Mapping)
    detail_token = (
        _normalize_route_token(token_detail.get("token"))
        if detail_is_mapping
        else ""
    )
    resolved_token = ref_token or detail_token

    def _result(
        disposition: RouteTokenDisposition,
        reason_code: str,
        *,
        consistent: bool,
    ) -> RouteTokenValidation:
        return RouteTokenValidation(
            disposition=disposition,
            reason_code=reason_code,
            token=resolved_token,
            token_ref=ref_token,
            detail_token=detail_token,
            consistent=consistent,
        )

    if not ref_token:
        return _result(
            RouteTokenDisposition.BLOCK,
            ROUTE_TOKEN_REF_MISSING,
            consistent=False,
        )
    if not detail_is_mapping:
        return _result(
            RouteTokenDisposition.BLOCK,
            ROUTE_TOKEN_DETAIL_MISSING,
            consistent=False,
        )
    if not detail_token:
        return _result(
            RouteTokenDisposition.BLOCK,
            ROUTE_TOKEN_DETAIL_TOKEN_MISSING,
            consistent=False,
        )
    if ref_token != detail_token:
        return _result(
            RouteTokenDisposition.BLOCK,
            ROUTE_TOKEN_REF_DETAIL_MISMATCH,
            consistent=False,
        )
    if ref_token in _EXECUTABLE_ROUTE_TOKENS:
        return _result(
            RouteTokenDisposition.ALLOW,
            ROUTE_TOKEN_EXECUTABLE,
            consistent=True,
        )
    if ref_token == RouteToken.GOV_AIRLOCK_REVIEW.value:
        return _result(
            RouteTokenDisposition.PAUSE,
            ROUTE_TOKEN_AIRLOCK_REVIEW,
            consistent=True,
        )
    return _result(
        RouteTokenDisposition.BLOCK,
        ROUTE_TOKEN_NOT_EXECUTABLE,
        consistent=True,
    )


def validate_governor_route_token(
    governor_result: Mapping[str, Any] | None,
) -> RouteTokenValidation:
    """Validate route-token fields from a Governor output mapping."""
    result = governor_result if isinstance(governor_result, Mapping) else {}
    detail = result.get("committed_route_token")
    return validate_committed_route_token(
        token_ref=result.get("committed_route_token_ref"),
        token_detail=detail if isinstance(detail, Mapping) else None,
    )


__all__ = [
    "CommittedRouteDecision",
    "RouteToken",
    "RouteTokenDisposition",
    "RouteTokenValidation",
    "ROUTE_TOKEN_AIRLOCK_REVIEW",
    "ROUTE_TOKEN_DETAIL_MISSING",
    "ROUTE_TOKEN_DETAIL_TOKEN_MISSING",
    "ROUTE_TOKEN_EXECUTABLE",
    "ROUTE_TOKEN_NOT_EXECUTABLE",
    "ROUTE_TOKEN_REF_DETAIL_MISMATCH",
    "ROUTE_TOKEN_REF_MISSING",
    "compile_route_decision",
    "validate_committed_route_token",
    "validate_governor_route_token",
]
