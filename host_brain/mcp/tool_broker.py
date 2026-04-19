"""MCP ToolBroker: single-entry-point for MCP tool invocation.

Call chain:
  intent_draft.mcp_call → MCPToolBroker.call()
    → registry check (must be registered)
    → Governor gate (WRITE requires token)
    → MCP SDK invoke (via handler)
    → Audit (skill_exec_log via log_skill_exec)

Error codes:
  MCP_TOOL_NOT_REGISTERED   - tool not in registry
  MCP_WRITE_TOKEN_REQUIRED  - WRITE tool called without approval token
  MCP_PROVIDER_UNAVAILABLE  - provider handler raised exception / not found
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MCP_PROVIDER_UNAVAILABLE,
    MCP_TOOL_NOT_REGISTERED,
    MCP_WRITE_TOKEN_REQUIRED,
)
from host_brain.core.ids import now_ms
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MCPCallResult:
    success: bool
    decision: str          # APPROVE | REJECT
    reason_code: str | None
    exec_id: str
    provider_id: str
    tool_id: str
    output: Any
    detail: str
    ts_ms: int


# ---------------------------------------------------------------------------
# MCPToolBroker
# ---------------------------------------------------------------------------

MCPHandler = Callable[[dict[str, Any]], Any]


class MCPToolBroker:
    """Single-entry-point for governed MCP tool invocation with full audit."""

    def __init__(
        self,
        *,
        registry: MCPToolRegistry,
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self._handlers: dict[str, MCPHandler] = {}

    def register_handler(self, provider_id: str, tool_id: str, handler: MCPHandler) -> None:
        """Register a callable handler for a provider+tool pair."""
        key = _handler_key(provider_id, tool_id)
        self._handlers[key] = handler

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
        """Execute a governed MCP tool call."""
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        exec_id = f"mcp_exec_{uuid.uuid4().hex[:12]}"
        req_id = request_id or f"req_{uuid.uuid4().hex[:8]}"

        # 1. Registry check
        meta = self.registry.get(provider_id=provider_id, tool_id=tool_id)
        if meta is None:
            return self._reject(
                exec_id=exec_id,
                request_id=req_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                provider_id=provider_id,
                tool_id=tool_id,
                reason_code=MCP_TOOL_NOT_REGISTERED,
                detail=f"tool not registered: {provider_id}/{tool_id}",
                ts_ms=now_value,
            )

        # 2. Governor gate: WRITE requires approval token
        is_write = _is_write(meta)
        if is_write and not approval_token_ref:
            return self._reject(
                exec_id=exec_id,
                request_id=req_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                provider_id=provider_id,
                tool_id=tool_id,
                reason_code=MCP_WRITE_TOKEN_REQUIRED,
                detail=f"WRITE tool {provider_id}/{tool_id} requires approval_token_ref",
                ts_ms=now_value,
            )

        # 3. Invoke handler
        handler = self._handlers.get(_handler_key(provider_id, tool_id))
        if handler is None:
            return self._reject(
                exec_id=exec_id,
                request_id=req_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                provider_id=provider_id,
                tool_id=tool_id,
                reason_code=MCP_PROVIDER_UNAVAILABLE,
                detail=f"no handler registered for {provider_id}/{tool_id}",
                ts_ms=now_value,
            )

        try:
            raw_output = handler(args or {})
            # 支持 async handler：若返回 coroutine，则在当前事件循环中执行
            if inspect.isawaitable(raw_output):
                try:
                    loop = asyncio.get_event_loop()
                    output = loop.run_until_complete(raw_output)
                except RuntimeError:
                    # 已在事件循环中（如 FastAPI 路由），无法 run_until_complete
                    # 此时 handler 应注册为同步版本，直接拒绝并记录错误
                    import logging as _logging
                    _logging.getLogger("Natsume.mcp.tool_broker").error(
                        "async handler %s/%s 在事件循环中被同步调用，无法 await，拒绝本次调用",
                        provider_id, tool_id,
                    )
                    return self._reject(
                        exec_id=exec_id,
                        request_id=req_id,
                        intent_id=intent_id,
                        session_id=session_id,
                        seq=seq,
                        provider_id=provider_id,
                        tool_id=tool_id,
                        reason_code=MCP_PROVIDER_UNAVAILABLE,
                        detail=f"async handler {provider_id}/{tool_id} 在事件循环中被同步调用，无法执行",
                        ts_ms=now_value,
                    )
            else:
                output = raw_output
        except Exception as exc:
            return self._reject(
                exec_id=exec_id,
                request_id=req_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                provider_id=provider_id,
                tool_id=tool_id,
                reason_code=MCP_PROVIDER_UNAVAILABLE,
                detail=f"handler raised exception: {exc}",
                ts_ms=now_value,
            )

        # 4. Audit
        self._log_exec(
            exec_id=exec_id,
            request_id=req_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            provider_id=provider_id,
            tool_id=tool_id,
            decision="APPROVE",
            reason_code=None,
            approval_token_ref=approval_token_ref,
            args=args,
            output=output,
            detail="approved by broker",
            ts_ms=now_value,
        )
        return MCPCallResult(
            success=True,
            decision="APPROVE",
            reason_code=None,
            exec_id=exec_id,
            provider_id=provider_id,
            tool_id=tool_id,
            output=output,
            detail="approved by broker",
            ts_ms=now_value,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reject(
        self,
        *,
        exec_id: str,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        provider_id: str,
        tool_id: str,
        reason_code: str,
        detail: str,
        ts_ms: int,
    ) -> MCPCallResult:
        self._log_exec(
            exec_id=exec_id,
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            provider_id=provider_id,
            tool_id=tool_id,
            decision="REJECT",
            reason_code=reason_code,
            approval_token_ref=None,
            args=None,
            output=None,
            detail=detail,
            ts_ms=ts_ms,
        )
        return MCPCallResult(
            success=False,
            decision="REJECT",
            reason_code=reason_code,
            exec_id=exec_id,
            provider_id=provider_id,
            tool_id=tool_id,
            output=None,
            detail=detail,
            ts_ms=ts_ms,
        )

    def _log_exec(
        self,
        *,
        exec_id: str,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        provider_id: str,
        tool_id: str,
        decision: str,
        reason_code: str | None,
        approval_token_ref: str | None,
        args: dict[str, Any] | None,
        output: Any,
        detail: str,
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        import json as _json
        try:
            output_str = _json.dumps(output, ensure_ascii=False, default=str) if output is not None else None
        except Exception:
            output_str = str(output)
        self.ledger.log_skill_exec({
            "request_id": request_id,
            "thread_id": None,
            "turn_id": None,
            "intent_id": intent_id,
            "event_id": exec_id,
            "skill_name": f"mcp::{provider_id}::{tool_id}",
            "approval_token_ref": approval_token_ref,
            "confirm_token_ref": None,
            "undo_token_ref": None,
            "result_status": "SUCCESS" if decision == "APPROVE" else "REJECTED",
            "result_code": reason_code,
            "result_digest": None,
            "evidence_ref": f"mcp_exec::{exec_id}",
            "manifest_ref": None,
            "ts_ms": ts_ms,
        })


def _handler_key(provider_id: str, tool_id: str) -> str:
    return f"{provider_id}::{tool_id}"


def _is_write(meta: ToolMeta) -> bool:
    """Determine if a tool should be treated as WRITE based on risk/reversibility."""
    return meta.risk_level in {"MEDIUM", "HIGH"} or not meta.reversible
