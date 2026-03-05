"""Tool execution single-entry broker with WRITE token gating."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CONFIRM_REQUIRED, TOKEN_INVALID
from host_brain.core.ids import new_event_id, now_ms
from host_brain.governor.tokens import TokenService
from host_brain.tools.catalog import ToolCatalog


ToolHandler = Callable[[dict[str, Any], dict[str, Any] | None], Any]

RESULT_SUCCESS = "SUCCESS"
RESULT_REJECTED = "REJECTED"
RESULT_FAILED = "FAILED"

TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
TOOL_HANDLER_MISSING = "TOOL_HANDLER_MISSING"
TOOL_EXECUTED = "TOOL_EXECUTED"
TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"


@dataclass(frozen=True)
class ToolExecutionResult:
    status: str
    result_code: str
    tool_id: str
    intent_id: str
    evidence_ref: str | None
    detail: dict[str, Any]
    output: Any = None


class ToolBroker:
    """Single legal entrypoint for managed tool execution."""

    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        token_service: TokenService,
        ledger: SQLiteLedger,
    ) -> None:
        self.catalog = catalog
        self.token_service = token_service
        self.ledger = ledger
        self._handlers: dict[str, ToolHandler] = {}

    def register_handler(self, tool_id: str, handler: ToolHandler) -> None:
        if not callable(handler):
            raise ValueError("handler must be callable")
        self._handlers[str(tool_id)] = handler

    def execute(
        self,
        *,
        request_id: str | None,
        intent_id: str,
        session_id: str,
        seq: int,
        tool_id: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        confirm_token_ref: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        ts_ms: int | None = None,
    ) -> ToolExecutionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        safe_args = args if isinstance(args, dict) else {}

        try:
            meta = self.catalog.get(tool_id)
        except KeyError:
            return self._log_and_return(
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                intent_id=intent_id,
                tool_id=tool_id,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                status=RESULT_REJECTED,
                result_code=TOOL_NOT_FOUND,
                result_digest=f"tool not found: {tool_id}",
                evidence_ref=None,
                ts_ms=now_value,
                output=None,
            )

        handler = self._handlers.get(tool_id)
        if handler is None:
            return self._log_and_return(
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                intent_id=intent_id,
                tool_id=tool_id,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                status=RESULT_REJECTED,
                result_code=TOOL_HANDLER_MISSING,
                result_digest=f"tool handler missing: {tool_id}",
                evidence_ref=None,
                ts_ms=now_value,
                output=None,
            )

        if meta.side_effect_level == "WRITE":
            require_confirm = meta.auth_level == "CONFIRM"
            validation = self.token_service.validate_execution_tokens(
                intent_id=intent_id,
                skill_name=tool_id,
                session_id=session_id,
                seq=int(seq),
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                require_confirm=require_confirm,
                now_ts_ms=now_value,
            )
            if not validation.valid:
                code = validation.reason_code or TOKEN_INVALID
                detail = validation.detail or "write token validation failed"
                if code == CONFIRM_REQUIRED and not confirm_token_ref:
                    detail = "confirm token required for write tool"
                return self._log_and_return(
                    request_id=request_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    intent_id=intent_id,
                    tool_id=tool_id,
                    approval_token_ref=approval_token_ref,
                    confirm_token_ref=confirm_token_ref,
                    status=RESULT_REJECTED,
                    result_code=code,
                    result_digest=detail,
                    evidence_ref=None,
                    ts_ms=now_value,
                    output=None,
                )

        try:
            # M27-J：context 注入情感字段（OPT-38）
            exec_context = dict(context or {})
            if "emotion_tag" not in exec_context:
                exec_context["emotion_tag"] = "NEUTRAL"
            if "mood_state" not in exec_context:
                exec_context["mood_state"] = "steady"
            output = handler(safe_args, exec_context)
        except Exception as exc:  # pragma: no cover - defensive guard
            return self._log_and_return(
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                intent_id=intent_id,
                tool_id=tool_id,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                status=RESULT_FAILED,
                result_code=TOOL_EXECUTION_ERROR,
                result_digest=str(exc),
                evidence_ref=None,
                ts_ms=now_value,
                output=None,
            )

        evidence_ref = f"evidence::{new_event_id()}"
        digest = _result_digest(output)
        return self._log_and_return(
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            status=RESULT_SUCCESS,
            result_code=TOOL_EXECUTED,
            result_digest=digest,
            evidence_ref=evidence_ref,
            ts_ms=now_value,
            output=output,
        )

    def _log_and_return(
        self,
        *,
        request_id: str | None,
        thread_id: str | None,
        turn_id: str | None,
        intent_id: str,
        tool_id: str,
        approval_token_ref: str | None,
        confirm_token_ref: str | None,
        status: str,
        result_code: str,
        result_digest: str,
        evidence_ref: str | None,
        ts_ms: int,
        output: Any,
    ) -> ToolExecutionResult:
        row = {
            "request_id": request_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "intent_id": intent_id,
            "skill_name": tool_id,
            "approval_token_ref": approval_token_ref,
            "confirm_token_ref": confirm_token_ref,
            "result_status": status,
            "result_code": result_code,
            "result_digest": result_digest,
            "evidence_ref": evidence_ref,
            "manifest_ref": None,
            "undo_token_ref": None,
            "ts_ms": ts_ms,
        }
        self.ledger.log_skill_exec(row)
        return ToolExecutionResult(
            status=status,
            result_code=result_code,
            tool_id=tool_id,
            intent_id=intent_id,
            evidence_ref=evidence_ref,
            detail=row,
            output=output,
        )


def _result_digest(output: Any) -> str:
    canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
