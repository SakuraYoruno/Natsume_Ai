"""Small policy adapters that issue request-bound grants and never invoke capabilities."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from .broker import CapabilityDescriptor
from .contracts import CapabilityGrant, CapabilityRequest, GrantDecision


def issue_grant(request: CapabilityRequest, *, now_ms: int, approved: bool, reason_code: str, authority_ref: str = "", ttl_ms: int = 30_000, constraints: Mapping[str, Any] | None = None) -> CapabilityGrant:
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
    """Issue a grant only when an injected platform policy returns explicit authority."""

    def __init__(self, evaluator: Callable[[CapabilityRequest, CapabilityDescriptor], tuple[bool, str, str]]) -> None:
        self._evaluator = evaluator

    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int) -> CapabilityGrant:
        allowed, reason, authority_ref = self._evaluator(request, descriptor)
        return issue_grant(request, now_ms=now_ms, approved=allowed, reason_code=reason, authority_ref=authority_ref)


class GovernorToolCapabilityPolicy:
    def __init__(self, governor: Any, *, rules_provider: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self._governor = governor
        self._rules_provider = rules_provider or (lambda: {})

    def authorize(self, request: CapabilityRequest, descriptor: CapabilityDescriptor, *, now_ms: int) -> CapabilityGrant:
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


__all__ = ["GovernorToolCapabilityPolicy", "PredicateCapabilityPolicy", "issue_grant"]
