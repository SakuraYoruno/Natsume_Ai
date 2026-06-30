"""Skill execution and related helpers for the executive runtime.

Contains the full skill execution path, sub-agent dispatch, skill
argument extraction/merging, failure-recovery planning, and the
capability-gap synthesis helper.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from host_brain.app.executive.constants import _ExecutionOutcome, _RouteSelection
from host_brain.app.executive.text_utils import (
    _coerce_mapping,
    _extract_result_code,
    _flatten_result_text,
    _is_effective_skill_success,
    _normalize_text,
    _parse_int_env,
)

_logger = logging.getLogger("Natsume.executive_runtime_core")


def _log_skill_exec_row(
    *,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
) -> None:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        get_ledger().log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": None,
                "turn_id": None,
                "intent_id": intent_id,
                "event_id": f"evt::{request_id}::{skill_name}",
                "skill_name": skill_name,
                "approval_token_ref": approval_token_ref,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": result_status,
                "result_code": result_code,
                "result_digest": result_digest[:4000] if result_digest else None,
                "evidence_ref": f"skill_exec::{request_id}::{skill_name}",
                "manifest_ref": None,
                "ts_ms": ts_ms,
            }
        )
    except Exception as exc:
        _logger.debug("[%s] skill exec audit skipped skill=%s: %s", request_id, skill_name, exc)


def _execute_skill(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> _ExecutionOutcome:
    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

    started = time.time()
    registry = get_skill_registry()
    skill_meta = registry.require(skill_id)
    approval_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("approval_token_ref"))
    execution_intent_id = _normalize_text(intent_id) or f"intent::{request_id}::{skill_id}"

    if _normalize_text(dict(governor_result or {}).get("decision"), lower=True) not in {"", "approve"}:
        error_text = _normalize_text(governor_result.get("reason_code")) or "GOVERNOR_REJECTED"
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="REJECTED",
            result_code=error_text,
            result_digest=error_text,
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=error_text,
            latency_ms=latency_ms,
            payload={"approval_token_ref": approval_token_ref or None},
        )

    args = _extract_skill_args(intent_draft, request_body, task_text)
    if is_agent_runtime_skill(skill_id):
        args.setdefault("approval_confirmed", True)
        args.setdefault("confirmed", True)
    if approval_token_ref:
        args.setdefault("approval_token_ref", approval_token_ref)
    exec_context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "mood_state": _normalize_text(dict(emotion_context or {}).get("mood_state")) or "NEUTRAL",
        "emotion_tag": _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL",
        "emotion_context": dict(emotion_context or {}),
        "skill_family": str(skill_meta.family or "general"),
    }
    if skill_id.startswith("memory_"):
        from host_brain.app.executive.memory_context import _build_memory_exec_context  # noqa: PLC0415

        exec_context.update(
            _build_memory_exec_context(
                request_id=request_id,
                session_id=session_id,
                intent_id=execution_intent_id,
                emotion_context=emotion_context,
            )
        )

    try:
        handler_payload = skill_meta.handler(args, exec_context)
        if isinstance(handler_payload, dict):
            payload = dict(handler_payload)
            payload.setdefault("_context", exec_context)
        else:
            payload = handler_payload
        from host_brain.app.skills.executor_registry import dispatch_executor_payload  # noqa: PLC0415

        raw_result = dispatch_executor_payload(payload)
        result_text = _flatten_result_text(raw_result)
        result_mapping = _coerce_mapping(raw_result)
        raw_status = _normalize_text(result_mapping.get("status"), lower=True)
        raw_code = _normalize_text(result_mapping.get("reason_code") or result_mapping.get("result_code"))
        success = _is_effective_skill_success(
            raw_status=raw_status,
            raw_code=raw_code,
            raw_result=raw_result,
            result_text=result_text,
        )
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="SUCCESS" if success else "FAILED",
            result_code=raw_code or None,
            result_digest=(result_text or str(raw_result or "")),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=success,
            result_text=result_text,
            error_text="" if success else (raw_code or "SKILL_EXECUTION_NO_EFFECT"),
            latency_ms=latency_ms,
            payload={
                "result": raw_result,
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(skill_meta.fallback_policy or "chat_fallback"),
            },
        )
    except Exception as exc:
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="FAILED",
            result_code="SKILL_EXECUTION_ERROR",
            result_digest=str(exc),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=str(exc),
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(getattr(skill_meta, "fallback_policy", "chat_fallback") or "chat_fallback"),
            },
        )


def _extract_skill_args(intent_draft: dict[str, Any], request_body: Any, user_message: str) -> dict[str, Any]:
    if isinstance(intent_draft.get("args"), dict):
        return dict(intent_draft
