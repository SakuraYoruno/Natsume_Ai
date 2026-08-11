"""Inert compatibility projection for the retired MCP execution facade.

MCP provider metadata remains available through ``MCPToolRegistry``.  Provider
execution must be represented by a governed Tool Task node; this module keeps
the old public shape only so stale callers fail closed without invoking a
handler, consuming a token, or registering a CapabilityBroker adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.broker import CapabilityBroker
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.core.ids import now_ms
from host_brain.governor.tokens import TokenService
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.runtime.degradation_authority import DegradationCapabilityPort


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
    """Legacy API with no execution authority.

    ``CapabilityBroker`` and ``TokenService`` are accepted for constructor
    compatibility but deliberately not retained.  No method in this class can
    register an executable capability or call a provider handler.
    """

    def __init__(
        self,
        *,
        registry: MCPToolRegistry,
        capability_broker: CapabilityBroker,
        ledger: SQLiteLedger | None = None,
        token_service: TokenService | None = None,
        degradation_capability_port: DegradationCapabilityPort | None = None,
    ) -> None:
        if capability_broker is None:
            raise ValueError("MCPToolBroker requires the composed CapabilityBroker")
        del capability_broker, token_service
        self.registry = registry
        self.ledger = ledger
        self._degradation_capability_port = degradation_capability_port

    def register_handler(
        self,
        provider_id: str,
        tool_id: str,
        handler: MCPHandler,
    ) -> None:
        """Reject legacy callback registration without retaining the handler."""

        if not callable(handler):
            raise ValueError("MCP handler must be callable")
        del provider_id, tool_id, handler
        raise PermissionError(MCP_TASK_AUTHORITY_REQUIRED)

    def bind_degradation_capability_port(
        self,
        port: DegradationCapabilityPort,
    ) -> None:
        """Preserve exact-owner lifecycle semantics for compatibility users."""

        if not isinstance(port, DegradationCapabilityPort):
            raise TypeError("MCPToolBroker requires DegradationCapabilityPort")
        current = self._degradation_capability_port
        if current is not None and current is not port:
            raise RuntimeError("MCP_DEGRADATION_AUTHORITY_ALREADY_BOUND")
        self._degradation_capability_port = port

    def unbind_degradation_capability_port(
        self,
        port: DegradationCapabilityPort,
    ) -> None:
        current = self._degradation_capability_port
        if current is None:
            return
        if current is not port:
            raise RuntimeError("MCP_DEGRADATION_AUTHORITY_OWNER_MISMATCH")
        self._degradation_capability_port = None

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
        """Reject before registry lookup, token consumption, or handler access."""

        timestamp = int(ts_ms if ts_ms is not None else now_ms())
        req_id = str(
            request_id
            or f"mcp-retired-request::{provider_id}::{tool_id}::{timestamp}"
        )
        identity = "\n".join(
            (
                req_id,
                str(intent_id or ""),
                str(session_id or ""),
                str(int(seq)),
                str(provider_id or ""),
                str(tool_id or ""),
            )
        )
        exec_id = "mcp-retired://sha256/" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        del args
        self._log_rejection(
            request_id=req_id,
            intent_id=intent_id,
            provider_id=provider_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            exec_id=exec_id,
            ts_ms=timestamp,
        )
        return MCPCallResult(
            success=False,
            decision="REJECT",
            reason_code=MCP_TASK_AUTHORITY_REQUIRED,
            exec_id=exec_id,
            provider_id=str(provider_id),
            tool_id=str(tool_id),
            output=None,
            detail=(
                "MCP provider execution requires a governed Tool Task node; "
                "the legacy broker has no execution authority"
            ),
            ts_ms=timestamp,
        )

    def _log_rejection(
        self,
        *,
        request_id: str,
        intent_id: str,
        provider_id: str,
        tool_id: str,
        approval_token_ref: str | None,
        exec_id: str,
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        approval_audit_ref = None
        if approval_token_ref:
            approval_audit_ref = "approval-token://sha256/" + hashlib.sha256(
                str(approval_token_ref).encode("utf-8")
            ).hexdigest()
        self.ledger.log_skill_exec({
            "request_id": request_id,
            "thread_id": None,
            "turn_id": None,
            "intent_id": intent_id,
            "event_id": exec_id,
            "skill_name": f"mcp::{provider_id}::{tool_id}",
            "approval_token_ref": approval_audit_ref,
            "confirm_token_ref": None,
            "undo_token_ref": None,
            "result_status": "REJECTED",
            "result_code": MCP_TASK_AUTHORITY_REQUIRED,
            "result_digest": None,
            "evidence_ref": None,
            "manifest_ref": None,
            "ts_ms": ts_ms,
        })


__all__ = ["MCPCallResult", "MCPToolBroker"]
