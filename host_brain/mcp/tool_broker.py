"""Compatibility facade routing every MCP invocation through CapabilityBroker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import json
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    CapabilityInvocationError,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import PredicateCapabilityPolicy
from host_brain.core.errors import (
    MCP_PROVIDER_UNAVAILABLE,
    MCP_TOOL_NOT_REGISTERED,
    MCP_WRITE_TOKEN_REQUIRED,
    MCP_WRITE_TOKEN_INVALID,
)
from host_brain.core.ids import now_ms
from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL, TokenService
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.observability.degradation_state import (
    evaluate_degradation_front_door,
    infer_degradation_capability,
)


@dataclass(frozen=True)
class MCPCallResult:
    success: bool
    decision: str
    reason_code: str | None
    exec_id: str
    provider_id: str
    tool_id: str
    output: Any
    detail: str
    ts_ms: int


MCPHandler = Callable[[dict[str, Any]], Any]


class MCPToolBroker:
    """Legacy API over the platform's one governed capability executor."""

    def __init__(
        self,
        *,
        registry: MCPToolRegistry,
        capability_broker: CapabilityBroker,
        ledger: SQLiteLedger | None = None,
        token_service: TokenService | None = None,
    ) -> None:
        if capability_broker is None:
            raise ValueError("MCPToolBroker requires the composed CapabilityBroker")
        self.registry = registry
        self.ledger = ledger
        self._token_service = token_service
        self._handlers: dict[str, MCPHandler] = {}
        self._broker = capability_broker
        self._policy = PredicateCapabilityPolicy(self._evaluate_policy)

    def register_handler(self, provider_id: str, tool_id: str, handler: MCPHandler) -> None:
        if not callable(handler):
            raise ValueError("MCP handler must be callable")
        self._handlers[_handler_key(provider_id, tool_id)] = handler

    def call(
        self,
        *,
        provider_id: str,
        tool_id: str,
        request_id: str | None = None,
        intent_id: str = "",
        session_id: str = "",
        seq: int = 0,
        args: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        ts_ms: int | None = None,
    ) -> MCPCallResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        req_id = str(request_id or f"mcp-request::{provider_id}::{tool_id}::{now_value}")
        capability_id = _handler_key(provider_id, tool_id)
        meta = self.registry.get(provider_id=provider_id, tool_id=tool_id)
        if meta is not None:
            self._ensure_registered(meta)
        else:
            # A shared broker can outlive a replaced MCP registry.  Remove any
            # stale binding before executing an identifier absent from the
            # current registry so it cannot reach an old provider handler.
            self._broker.unregister(kind=CapabilityKind.MCP, capability_id=capability_id)
        payload = dict(args or {})
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        request = CapabilityRequest(
            request_id=req_id,
            attempt_id=f"mcp-attempt::{intent_id or req_id}::{seq}::{provider_id}::{tool_id}",
            idempotency_key=f"mcp::{intent_id or req_id}::{seq}::{provider_id}::{tool_id}::{digest}",
            capability_id=capability_id,
            kind=CapabilityKind.MCP,
            args=payload,
            context={
                "provider_id": provider_id,
                "tool_id": tool_id,
                "intent_id": intent_id,
                "session_id": session_id,
                "seq": int(seq),
                "approval_token_ref": approval_token_ref or "",
            },
            created_at_ms=now_value,
        )
        receipt = self._broker.execute(request)
        reason_code = _legacy_reason(receipt.reason_code, registered=meta is not None)
        decision = "APPROVE" if receipt.success else "REJECT"
        detail = "approved by capability broker" if receipt.success else str(receipt.detail or reason_code)
        self._log_projection(
            request_id=req_id,
            intent_id=intent_id,
            provider_id=provider_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            receipt=receipt,
            reason_code=reason_code,
            ts_ms=now_value,
        )
        return MCPCallResult(
            success=receipt.success,
            decision=decision,
            reason_code=None if receipt.success else reason_code,
            exec_id=receipt.attempt_id,
            provider_id=provider_id,
            tool_id=tool_id,
            output=receipt.output if receipt.success else None,
            detail=detail,
            ts_ms=now_value,
        )

    def _ensure_registered(self, meta: ToolMeta) -> None:
        capability_id = _handler_key(meta.provider_id, meta.tool_id)

        def _adapter(request: CapabilityRequest, _grant: Any) -> Any:
            handler = self._handlers.get(capability_id)
            if handler is None:
                raise CapabilityInvocationError(
                    MCP_PROVIDER_UNAVAILABLE,
                    f"no handler registered for {meta.provider_id}/{meta.tool_id}",
                )
            try:
                output = handler(dict(request.args))
                if inspect.isawaitable(output):
                    try:
                        asyncio.get_running_loop()
                    except RuntimeError:
                        return asyncio.run(output)
                    raise CapabilityInvocationError(
                        MCP_PROVIDER_UNAVAILABLE,
                        "async MCP handler cannot be synchronously invoked from a running event loop",
                    )
                return output
            except CapabilityInvocationError:
                raise
            except Exception as exc:
                raise CapabilityInvocationError(
                    MCP_PROVIDER_UNAVAILABLE,
                    f"handler raised exception: {exc}",
                ) from exc

        self._broker.register(CapabilityDescriptor(
            capability_id=capability_id,
            kind=CapabilityKind.MCP,
            version=str(meta.version or "1"),
            risk_level={"LOW": 1, "MEDIUM": 3, "HIGH": 4}.get(str(meta.risk_level).upper(), 5),
            adapter=_adapter,
            authorization_policy=self._policy,
        ), replace_existing=True)

    def _evaluate_policy(self, request: CapabilityRequest, _descriptor: CapabilityDescriptor) -> tuple[bool, str, str]:
        provider_id = str(request.context.get("provider_id") or "")
        tool_id = str(request.context.get("tool_id") or "")
        meta = self.registry.get(provider_id=provider_id, tool_id=tool_id)
        if meta is None:
            return False, MCP_TOOL_NOT_REGISTERED, ""
        approval = str(request.context.get("approval_token_ref") or "").strip()
        if _is_write(meta) and not approval:
            return False, MCP_WRITE_TOKEN_REQUIRED, ""
        static_risk = {"LOW": 1, "MEDIUM": 3, "HIGH": 4}.get(str(meta.risk_level).upper(), 5)
        capability = infer_degradation_capability(
            provider_id, tool_id, meta.name, meta.description, meta.domain_tags,
            default="high_risk_execution" if static_risk >= 3 else "general_execution",
        )
        degradation = evaluate_degradation_front_door(
            capability_id=capability,
            static_risk_level=static_risk,
        )
        if not bool(degradation.get("allowed", degradation.get("allowed_by_degradation", False))):
            return False, str(degradation.get("reason_code") or "DEGRADATION_STATE_UNAVAILABLE"), ""
        if approval:
            if self._token_service is None:
                return False, MCP_WRITE_TOKEN_INVALID, ""
            validation = self._token_service.validate_bound_token(
                approval,
                expected_type=TOKEN_TYPE_APPROVAL,
                intent_id=str(request.context.get("intent_id") or ""),
                skill_name=f"mcp::{provider_id}::{tool_id}",
                session_id=str(request.context.get("session_id") or ""),
                seq=int(request.context.get("seq") or 0),
                now_ts_ms=int(request.created_at_ms),
                consume=True,
            )
            if not validation.valid:
                return False, MCP_WRITE_TOKEN_INVALID, ""
            token_digest = hashlib.sha256(approval.encode()).hexdigest()
            authority = f"approval-token://sha256/{token_digest}"
        else:
            authority = f"mcp-read-policy://{provider_id}/{tool_id}"
        return True, "MCP_CAPABILITY_APPROVED", authority

    def _log_projection(
        self,
        *,
        request_id: str,
        intent_id: str,
        provider_id: str,
        tool_id: str,
        approval_token_ref: str | None,
        receipt: Any,
        reason_code: str | None,
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        approval_audit_ref = None
        if approval_token_ref:
            approval_audit_ref = "approval-token://sha256/" + hashlib.sha256(
                str(approval_token_ref).encode()
            ).hexdigest()
        self.ledger.log_skill_exec({
            "request_id": request_id,
            "thread_id": None,
            "turn_id": None,
            "intent_id": intent_id,
            "event_id": receipt.attempt_id,
            "skill_name": f"mcp::{provider_id}::{tool_id}",
            "approval_token_ref": approval_audit_ref,
            "confirm_token_ref": None,
            "undo_token_ref": None,
            "result_status": "SUCCESS" if receipt.success else "REJECTED",
            "result_code": None if receipt.success else reason_code,
            "result_digest": None,
            "evidence_ref": receipt.evidence_ref,
            "manifest_ref": None,
            "ts_ms": ts_ms,
        })


def _legacy_reason(reason: str | None, *, registered: bool) -> str | None:
    if not registered or reason == "CAPABILITY_NOT_REGISTERED":
        return MCP_TOOL_NOT_REGISTERED
    if reason == "CAPABILITY_INVOCATION_FAILED":
        return MCP_PROVIDER_UNAVAILABLE
    return reason


def _handler_key(provider_id: str, tool_id: str) -> str:
    return f"{str(provider_id).strip()}::{str(tool_id).strip()}"


def _is_write(meta: ToolMeta) -> bool:
    return meta.risk_level in {"MEDIUM", "HIGH"} or not meta.reversible


__all__ = ["MCPCallResult", "MCPToolBroker"]
