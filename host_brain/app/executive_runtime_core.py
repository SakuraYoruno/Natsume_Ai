"""Skill-first executive runtime core.

This module owns the canonical execution path behind:
chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime.

The runtime only routes to skill families / executors. Legacy dispatch/top-level
entrypoints are intentionally not reintroduced here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncGenerator

from host_brain.app.capability_gap_detector import detect_capability_gap
from host_brain.app.runtime_capability_synthesizer import synthesize_runtime_capability
from host_brain.app.skill_dag_planner import SkillDagNode, SkillDagPlan, build_skill_dag_plan
from host_brain.app.skill_family_router import build_skill_route_decision
from host_brain.skills.executor_registry import dispatch_executor_payload


_logger = logging.getLogger("Natsume.executive_runtime_core")

_RECENT_TURN_PATTERNS = (
    "刚刚",
    "上句",
    "上一句",
    "上一条",
    "刚才说",
    "我刚才说",
    "你刚才说",
    "我们刚才",
)

_TASK_PROGRESS_PATTERNS = (
    "进度",
    "做到哪",
    "做完了吗",
    "还在跑",
    "执行到哪",
    "任务状态",
    "现在怎么样",
    "还没好",
)

_MULTI_STEP_MARKERS = (
    "然后",
    "并且",
    "同时",
    "接着",
    "再把",
    "之后",
)

_ACTION_SPLIT_RE = re.compile(r"(?:然后|并且|同时|接着|之后|再把|再去|再来)")
_SKILL_EXEC_FAIL_STATUS = {"failed", "rejected", "degraded", "error"}
_SKILL_EXEC_FAIL_CODES = {
    "SCHED_NO_CONTENT",
    "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR",
    "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
}
_SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available",
    "no memory store available",
    "no memory backend available",
    "skipping diary",
    "no conversations on",
)


@dataclass(frozen=True)
class _RouteSelection:
    route_type: str
    target_id: str
    reason_code: str


@dataclass(frozen=True)
class _ExecutionOutcome:
    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _parse_bool_env(name: str, *, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _iter_candidate_keys() -> tuple[str, ...]:
    return (
        "route",
        "route_type",
        "target_id",
        "skill_id",
        "skill_name",
        "skill",
        "action",
        "executor_target",
        "delegate_agent",
        "agent_id",
        "target_agent",
    )


def _looks_like_recent_turn_query(user_message: str) -> bool:
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _RECENT_TURN_PATTERNS)


def _looks_like_task_progress_query(user_message: str) -> bool:
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _TASK_PROGRESS_PATTERNS)


def _looks_multi_step_request(user_message: str, intent_draft: dict[str, Any]) -> bool:
    if isinstance(intent_draft.get("planned_steps"), list) and len(intent_draft.get("planned_steps") or []) > 1:
        return True
    if isinstance(intent_draft.get("steps"), list) and len(intent_draft.get("steps") or []) > 1:
        return True
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _MULTI_STEP_MARKERS)


def _extract_skill_target(intent_draft: dict[str, Any], skill_registry: Any) -> str:
    for key in ("skill_id", "skill_name", "skill", "action", "target_id"):
        candidate = _normalize_text(intent_draft.get(key))
        if not candidate:
            continue
        try:
            if skill_registry.get(candidate) is not None:
                return candidate
        except Exception:
            continue
    return ""


def _extract_delegate_target(intent_draft: dict[str, Any]) -> str:
    for key in ("delegate_agent", "agent_id", "target_agent", "executor_target"):
        candidate = _normalize_text(intent_draft.get(key))
        if candidate.endswith("_agent"):
            return candidate
    return ""


def _hard_rule_route(
    *,
    intent_draft: dict[str, Any],
    sanitized_message: str,
    skill_registry: Any,
    allow_plan_detection: bool = True,
) -> _RouteSelection:
    route_hint = _normalize_text(intent_draft.get("route"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id") or intent_draft.get("skill_id") or intent_draft.get("agent_id"))
    if route_hint in {"recent_turn", "task_progress", "chat"}:
        return _RouteSelection(route_hint, target_hint, f"intent_draft:{route_hint}")
    if route_hint == "skill" and target_hint:
        return _RouteSelection("skill", target_hint, "intent_draft:skill")
    if route_hint == "delegate" and target_hint:
        return _RouteSelection("delegate", target_hint, "intent_draft:delegate")

    if _looks_like_task_progress_query(sanitized_message):
        return _RouteSelection("task_progress", "", "hard_rule:task_progress")
    if _looks_like_recent_turn_query(sanitized_message):
        return _RouteSelection("recent_turn", "", "hard_rule:recent_turn")

    skill_target = _extract_skill_target(intent_draft, skill_registry)
    if skill_target:
        return _RouteSelection("skill", skill_target, "intent_draft:skill_target")

    delegate_target = _extract_delegate_target(intent_draft)
    if delegate_target:
        return _RouteSelection("delegate", delegate_target, "intent_draft:delegate_target")

    if allow_plan_detection and _looks_multi_step_request(sanitized_message, intent_draft):
        return _RouteSelection("plan", "skill_dag", "hard_rule:multi_step")

    return _RouteSelection("chat", "", "default:chat")


def _build_default_plan_steps(
    *,
    user_message: str,
    skill_registry: Any,
) -> list[dict[str, Any]]:
    segments = [segment.strip(" ，。；;") for segment in _ACTION_SPLIT_RE.split(_normalize_text(user_message)) if segment.strip(" ，。；;")]
    if len(segments) <= 1:
        return []
    steps: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        route = _hard_rule_route(
            intent_draft={},
            sanitized_message=segment,
            skill_registry=skill_registry,
            allow_plan_detection=False,
        )
        decision = build_skill_route_decision(
            route_type=route.route_type if route.route_type in {"skill", "delegate"} else "delegate",
            target_id=route.target_id if route.target_id else "intent_agent",
            reason_code=route.reason_code or "plan:segment",
            skill_registry=skill_registry,
        )
        steps.append(
            {
                "step_index": index,
                "route": route.route_type if route.route_type in {"skill", "delegate"} else "delegate",
                "target_id": route.target_id if route.target_id else "intent_agent",
                "description": segment,
                "task_description": segment,
                "route_reason": route.reason_code or "plan:segment",
                "allow_parallel": ("同时" in _normalize_text(user_message)) and index > 1,
                "skill_family": decision.skill_family,
                "risk_level": decision.risk_level,
            }
        )
    return steps


def _build_planning_agent_plan_steps(
    *,
    request_id: str,
    user_message: str,
    skill_registry: Any,
) -> list[dict[str, Any]]:
    from host_brain.app.dependencies import get_planning_agent  # noqa: PLC0415

    planning_agent = get_planning_agent()
    if not planning_agent.needs_planning(user_message):
        return []

    available_skills: list[str] = []
    try:
        available_skills = [
            str(meta.skill_name or "").strip()
            for meta in list(skill_registry.all() or [])
            if str(getattr(meta, "skill_name", "") or "").strip()
        ]
    except Exception:
        available_skills = []

    plan = planning_agent.create_plan(
        task_id=f"runtime_plan::{request_id}",
        request=user_message,
        available_skills=available_skills or None,
    )

    raw_steps = list(getattr(plan, "steps", []) or [])
    action_steps = [
        step
        for step in raw_steps
        if _normalize_text(getattr(step, "action_type", ""), lower=True) == "act"
        and _normalize_text(getattr(step, "description"))
    ]
    if len(action_steps) <= 1:
        return []

    source_to_index: dict[int, int] = {}
    for index, step in enumerate(action_steps, start=1):
        source_step_id = int(getattr(step, "step_id", index) or index)
        source_to_index[source_step_id] = index

    parallel_enabled = _parse_bool_env("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_PARALLEL_ENABLED", default=True)
    converted_steps: list[dict[str, Any]] = []
    for index, step in enumerate(action_steps, start=1):
        description = _normalize_text(getattr(step, "description")) or f"step-{index}"
        planned_skill = _normalize_text(getattr(step, "skill_name"))
        route = "delegate"
        target_id = "intent_agent"
        route_reason = "planning_agent:act"

        if planned_skill:
            try:
                if skill_registry.get(planned_skill) is not None:
                    route = "skill"
                    target_id = planned_skill
                    route_reason = "planning_agent:skill"
                elif planned_skill.endswith("_agent"):
                    route = "delegate"
                    target_id = planned_skill
                    route_reason = "planning_agent:delegate"
            except Exception:
                route = "delegate"
                target_id = "intent_agent"
                route_reason = "planning_agent:fallback"

        if target_id == "intent_agent":
            hard_route = _hard_rule_route(
                intent_draft={},
                sanitized_message=description,
                skill_registry=skill_registry,
                allow_plan_detection=False,
            )
            if hard_route.route_type in {"skill", "delegate"}:
                route = hard_route.route_type
                target_id = hard_route.target_id or "intent_agent"
                route_reason = f"planning_agent:{hard_route.reason_code}"

        decision = build_skill_route_decision(
            route_type=route,
            target_id=target_id,
            reason_code=route_reason,
            skill_registry=skill_registry,
        )
        raw_dependencies = list(getattr(step, "depends_on", []) or [])
        dependencies = [
            int(source_to_index[dep])
            for dep in raw_dependencies
            if int(dep) in source_to_index and int(source_to_index[dep]) < index
        ]
        if not dependencies and index > 1 and not parallel_enabled:
            dependencies = [index - 1]

        converted_steps.append(
            {
                "step_index": index,
                "route": route,
                "target_id": target_id,
                "description": description,
                "task_description": description,
                "route_reason": route_reason,
                "allow_parallel": bool(parallel_enabled and not dependencies),
                "skill_family": decision.skill_family,
                "risk_level": decision.risk_level,
                "dependencies": dependencies,
                "capability_tags": list(getattr(decision, "capability_tags", ()) or ()),
            }
        )
    return converted_steps


def _should_force_complex_plan(
    *,
    route: _RouteSelection,
    user_message: str,
    intent_draft: dict[str, Any],
    llm_route_used: bool,
) -> bool:
    if route.route_type in {"plan", "recent_turn", "task_progress"}:
        return False
    if not _parse_bool_env("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED", default=True):
        return False
    if bool(intent_draft.get("disable_auto_plan")):
        return False
    if bool(intent_draft.get("force_plan")):
        return True

    normalized_message = _normalize_text(user_message)
    min_chars = _parse_int_env(
        "HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_MIN_CHARS",
        default=24,
        minimum=8,
        maximum=400,
    )
    if len(normalized_message) < min_chars:
        return False

    if route.route_type == "chat":
        gap = detect_capability_gap(
            user_message=normalized_message,
            route_type=route.route_type,
            target_id=route.target_id,
            reason_code=route.reason_code,
            llm_route_used=llm_route_used,
        )
        if not gap.detected:
            return False

    try:
        from host_brain.app.dependencies import get_planning_agent  # noqa: PLC0415

        return bool(get_planning_agent().needs_planning(normalized_message))
    except Exception:
        return False


def _maybe_build_multi_step_plan(
    *,
    request_id: str,
    sanitized_message: str,
    intent_draft: dict[str, Any],
    skill_registry: Any,
) -> SkillDagPlan | None:
    explicit_steps = intent_draft.get("planned_steps")
    if not isinstance(explicit_steps, list):
        explicit_steps = intent_draft.get("steps")
    steps = [dict(item or {}) for item in explicit_steps] if isinstance(explicit_steps, list) else []
    if not steps:
        steps = _build_planning_agent_plan_steps(
            request_id=request_id,
            user_message=sanitized_message,
            skill_registry=skill_registry,
        )
    if not steps:
        steps = _build_default_plan_steps(user_message=sanitized_message, skill_registry=skill_registry)
    if len(steps) <= 1:
        return None
    return build_skill_dag_plan(plan_id=f"plan::{request_id}", steps=steps)


def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _build_task_progress_sub_context(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
) -> SimpleNamespace:
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    registry = get_runtime_task_registry()
    summary = registry.summarize_for_chat(
        person_id=person_id,
        session_id=session_id,
        exclude_request_id=request_id,
    )
    if not summary:
        return SimpleNamespace(
            success=False,
            result_text="当前没有可汇报的执行进度。",
            payload={},
        )

    status = _normalize_text(summary.get("status")) or "UNKNOWN"
    target_id = _normalize_text(summary.get("target_id")) or "task"
    progress = _normalize_text(summary.get("progress_message") or summary.get("detail"))
    elapsed = _format_elapsed(int(summary.get("elapsed_ms") or 0))
    result_text = f"当前任务 {target_id} 状态为 {status}，已运行 {elapsed}。"
    if progress:
        result_text += f" 当前进展：{progress}"
    result_value = _normalize_text(summary.get("result_text"))
    if result_value and status in {"COMPLETED", "FAILED"}:
        result_text += f" 结果摘要：{result_value}"
    return SimpleNamespace(
        success=True,
        result_text=result_text,
        payload=summary,
    )


def _extract_skill_args(intent_draft: dict[str, Any], request_body: Any, user_message: str) -> dict[str, Any]:
    if isinstance(intent_draft.get("args"), dict):
        return dict(intent_draft.get("args") or {})
    if isinstance(request_body, dict) and isinstance(request_body.get("args"), dict):
        return dict(request_body.get("args") or {})
    return {
        "query": user_message,
        "text": user_message,
        "user_message": user_message,
    }


def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def _is_effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False

    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False

    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True


async def _dispatch_to_sub_agent(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    target_id: str,
    task_text: str,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> _ExecutionOutcome:
    from host_brain.app.sub_agent_protocol import SubAgentRequest, get_sub_agent_dispatcher  # noqa: PLC0415

    started = time.time()
    dispatcher = get_sub_agent_dispatcher()
    timeout_ms = _parse_int_env(
        "HOST_SUBAGENT_DISPATCH_TIMEOUT_MS",
        default=90_000,
        minimum=5_000,
        maximum=300_000,
    )
    response = await dispatcher.dispatch(
        SubAgentRequest(
            agent_id=target_id,
            task=task_text,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
            context=dict(context or {}),
            timeout_ms=timeout_ms,
        )
    )
    latency_ms = int((time.time() - started) * 1000)
    return _ExecutionOutcome(
        route_type="delegate",
        target_id=target_id,
        skill_family=str(dict(context or {}).get("skill_family") or "general"),
        success=bool(response.success),
        result_text=_normalize_text(response.result),
        error_text=_normalize_text(response.error),
        latency_ms=max(latency_ms, int(response.latency_ms or 0)),
        payload={
            "audit_refs": dict(response.audit_refs or {}),
            "agent_id": response.agent_id,
        },
    )


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
    recall_slot_text: dict[str, Any],
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
    exec_context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "emotion_context": dict(emotion_context or {}),
        "recall_slot_text": dict(recall_slot_text or {}),
        "skill_family": str(skill_meta.family or "general"),
    }

    try:
        handler_payload = skill_meta.handler(args, exec_context)
        if isinstance(handler_payload, dict):
            payload = dict(handler_payload)
            payload.setdefault("_context", exec_context)
        else:
            payload = handler_payload
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


def _family_default_delegate(skill_family: str) -> str:
    normalized = _normalize_text(skill_family, lower=True)
    if normalized == "code":
        return "code_agent"
    if normalized == "translation":
        return "translate_agent"
    if normalized in {"perception", "vision"}:
        return "vision_agent"
    if normalized == "system":
        return "repair_agent"
    return "intent_agent"


def _maybe_synthesize_runtime_capability(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    route_type: str,
    target_id: str,
    reason_code: str,
    llm_route_used: bool,
) -> dict[str, Any] | None:
    gap = detect_capability_gap(
        user_message=user_message,
        route_type=route_type,
        target_id=target_id,
        reason_code=reason_code,
        llm_route_used=llm_route_used,
    )
    if not gap.detected:
        return None

    from host_brain.app.dependencies import (  # noqa: PLC0415
        get_capability_forge_service,
        get_capability_gap_telemetry,
        get_runtime_capability_registry,
    )

    forge_service = get_capability_forge_service()
    override = forge_service.get_runtime_route_override(
        request_id=request_id,
        suggested_family=str(gap.suggested_family or "reasoning"),
        missing_capability_tags=list(gap.missing_capability_tags),
        reason_code=gap.reason_code,
    )
    runtime_spec = synthesize_runtime_capability(
        request_id=request_id,
        session_id=session_id,
        user_message=user_message,
        gap_decision=gap,
        registry=get_runtime_capability_registry(),
    )

    override_target = _normalize_text(override.get("executor_target")) if isinstance(override, dict) else ""
    executor_route = runtime_spec.route_type
    executor_target = override_target or runtime_spec.target_id
    if override_target:
        executor_route = "delegate"

    get_capability_gap_telemetry().record_detected(
        request_id=request_id,
        session_id=session_id,
        runtime_capability_id=runtime_spec.runtime_capability_id,
        reason_code=gap.reason_code,
        suggested_family=gap.suggested_family,
        missing_capability_tags=gap.missing_capability_tags,
        confidence=gap.confidence,
        executor_route=executor_route,
        executor_target=executor_target,
    )

    auto_plan_result: dict[str, Any] = {}
    try:
        auto_plan_result = forge_service.maybe_auto_plan_for_gap(
            request_id=request_id,
            session_id=session_id,
            reason_code=gap.reason_code,
            suggested_family=str(gap.suggested_family or "reasoning"),
            missing_capability_tags=list(gap.missing_capability_tags),
            proposer="executive_runtime",
        )
    except Exception as exc:
        _logger.debug("[%s] capability forge auto-plan skipped: %s", request_id, exc)
        auto_plan_result = {
            "triggered": False,
            "reason": "AUTO_PLAN_EXCEPTION",
        }

    capability_forge_payload = dict(override or {})
    if auto_plan_result:
        capability_forge_payload["auto_plan"] = dict(auto_plan_result)

    return {
        "capability_gap": gap.as_dict(),
        "runtime_capability": runtime_spec.as_dict(),
        "capability_forge": capability_forge_payload,
        "executor_route": executor_route,
        "executor_target": executor_target,
        "task_text": runtime_spec.task,
    }


def _build_failure_recovery_plan(
    *,
    route_type: str,
    target_id: str,
    skill_family: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    details = dict(payload or {})
    if route_type == "skill":
        fallback_policy = _normalize_text(details.get("fallback_policy"), lower=True) or "chat_fallback"
        if fallback_policy in {"same_family", "retry_once"}:
            return {
                "route_type": "delegate",
                "target_id": _family_default_delegate(skill_family),
                "reason_code": f"skill_recovery:{fallback_policy}",
            }
        return {
            "route_type": "chat",
            "target_id": "",
            "reason_code": "skill_recovery:chat_fallback",
        }
    if route_type == "delegate" and target_id != "intent_agent":
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "reason_code": "delegate_recovery:intent_agent",
        }
    return None


def _build_chat_input(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    recall_slot_text: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
    sub_agent_context,
):
    from host_brain.app.chat_agent import ChatAgentInput  # noqa: PLC0415

    return ChatAgentInput(
        request_id=request_id,
        session_id=session_id,
        user_message=sanitized_message,
        person_id=person_id,
        emotion_context=dict(emotion_context or {}),
        emotion_snapshot=dict(emotion_snapshot or {}),
        bond_summary=dict(bond_summary or {}),
        recall_slot_text=dict(recall_slot_text or {}),
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        retrieval_summary=dict(retrieval_summary or {}),
        sub_agent_context=sub_agent_context,
        render_target_lang=render_target_lang,
        ts_ms=ts_ms,
        intent_draft=dict(intent_draft or {}),
        trace_stage_meta=dict(trace_stage_meta or {}),
        llm_runtime=dict(llm_runtime or {}) if isinstance(llm_runtime, dict) else llm_runtime,
    )


def _build_sub_agent_context(
    *,
    agent_id: str,
    task_text: str,
    outcome: _ExecutionOutcome,
):
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415

    return SubAgentContext(
        agent_id=agent_id,
        task=task_text,
        result_text=outcome.result_text,
        success=outcome.success,
        latency_ms=outcome.latency_ms,
        error=outcome.error_text,
        payload=dict(outcome.payload or {}),
    )


async def _execute_plan_node(
    *,
    node: SkillDagNode,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    recall_slot_text: dict[str, Any],
    render_target_lang: str,
    node_results: dict[str, _ExecutionOutcome],
) -> _ExecutionOutcome:
    dependency_context_parts: list[str] = []
    for dep_id in list(node.dependencies or ()):
        previous = node_results.get(dep_id)
        if previous and previous.result_text:
            dependency_context_parts.append(f"[{dep_id}] {previous.result_text}")
    task_text = _normalize_text(node.task_description or node.description)
    if dependency_context_parts:
        task_text = f"{task_text}\n\nDependency context:\n" + "\n".join(dependency_context_parts)

    if node.route == "skill":
        return await asyncio.to_thread(
            _execute_skill,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id=node.target_id,
            task_text=task_text,
            user_message=task_text,
            governor_result=governor_result,
            intent_draft={**dict(intent_draft or {}), "args": {"query": task_text, "text": task_text}},
            emotion_context=emotion_context,
            recall_slot_text=recall_slot_text,
            render_target_lang=render_target_lang,
        )
    return await _dispatch_to_sub_agent(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        target_id=node.target_id or "intent_agent",
        task_text=task_text,
        user_message=task_text,
        context={
            "skill_family": node.skill_family or "executive",
            "plan_node_id": node.node_id,
            "route_reason": node.route_reason,
        },
    )


async def _execute_skill_dag(
    *,
    plan: SkillDagPlan,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    recall_slot_text: dict[str, Any],
    render_target_lang: str,
) -> tuple[_ExecutionOutcome, dict[str, Any]]:
    node_results: dict[str, _ExecutionOutcome] = {}
    plan_trace: list[dict[str, Any]] = []

    for batch in list(plan.execution_batches or []):
        batch_nodes = [node for node in plan.nodes if node.node_id in batch]
        coros = [
            _execute_plan_node(
                node=node,
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                intent_id=intent_id,
                governor_result=governor_result,
                intent_draft=intent_draft,
                emotion_context=emotion_context,
                recall_slot_text=recall_slot_text,
                render_target_lang=render_target_lang,
                node_results=node_results,
            )
            for node in batch_nodes
        ]
        batch_results = await asyncio.gather(*coros)
        for node, outcome in zip(batch_nodes, batch_results):
            node_results[node.node_id] = outcome
            plan_trace.append(
                {
                    "node_id": node.node_id,
                    "route": node.route,
                    "target_id": node.target_id,
                    "success": outcome.success,
                    "latency_ms": outcome.latency_ms,
                    "result_text": outcome.result_text,
                    "error_text": outcome.error_text,
                }
            )

    ordered_results = [node_results[node.node_id] for node in plan.nodes if node.node_id in node_results]
    success = bool(ordered_results) and all(item.success for item in ordered_results)
    summary_lines: list[str] = []
    for node in plan.nodes:
        outcome = node_results.get(node.node_id)
        if outcome is None:
            continue
        body = outcome.result_text or outcome.error_text or "no output"
        summary_lines.append(f"[{node.step_index}] {node.description}: {body}")

    return (
        _ExecutionOutcome(
            route_type="plan",
            target_id=plan.plan_id,
            skill_family="executive",
            success=success,
            result_text="\n".join(summary_lines).strip(),
            error_text="" if success else "PLAN_NODE_FAILED",
            latency_ms=sum(int(item.latency_ms or 0) for item in ordered_results),
            payload={
                "plan": plan.as_dict(),
                "plan_trace": plan_trace,
            },
        ),
        {
            "plan_id": plan.plan_id,
            "plan_trace": plan_trace,
            "parallel_enabled": any(len(batch) > 1 for batch in list(plan.execution_batches or [])),
        },
    )


async def _stream_chat_with_context(
    *,
    route_reason: str,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    recall_slot_text: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
    sub_agent_context,
    runtime_capability_payload: dict[str, Any] | None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.chat_agent import chat_agent_sse_generator  # noqa: PLC0415
    from host_brain.app.executive_runtime import (  # noqa: PLC0415
        build_runtime_capability_handoff_extra,
        record_executive_handoff,
        stream_chat_route,
    )

    chat_input = _build_chat_input(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        recall_slot_text=recall_slot_text,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=sub_agent_context,
    )
    async for event in stream_chat_route(
        route="chat",
        request_id=request_id,
        route_reason=route_reason,
        skill_family=str(trace_stage_meta.get("skill_family") or "social"),
        runtime_capability_handoff_extra=build_runtime_capability_handoff_extra(runtime_capability_payload),
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
        chat_agent_sse_generator_fn=chat_agent_sse_generator,
        chat_input=chat_input,
    ):
        yield event


async def _executive_runtime_core_impl(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    person_id: str,
    sanitized_message: str,
    recall_slot_text: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.chat_agent import _sse  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry, get_skill_registry  # noqa: PLC0415
    from host_brain.app.executive_runtime import (  # noqa: PLC0415
        record_executive_handoff,
        record_runtime_capability_execution,
        try_handle_recent_turn_direct_route,
        try_handle_task_progress_direct_route,
    )

    del metrics

    skill_registry = get_skill_registry()
    llm_route_used = bool(
        dict(intent_draft or {}).get("llm_route_used")
        or dict(intent_draft or {}).get("route_source") == "llm"
    )
    route = _hard_rule_route(
        intent_draft=dict(intent_draft or {}),
        sanitized_message=sanitized_message,
        skill_registry=skill_registry,
    )
    if _should_force_complex_plan(
        route=route,
        user_message=sanitized_message,
        intent_draft=dict(intent_draft or {}),
        llm_route_used=llm_route_used,
    ):
        route = _RouteSelection("plan", "skill_dag", "auto_plan:complex_task")
    direct_recent = await try_handle_recent_turn_direct_route(
        route=route.route_type,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        render_target_lang=render_target_lang,
        route_reason=route.reason_code,
        ts_ms=ts_ms,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        governor_result=governor_result,
        pii_ingress_stats=pii_ingress_stats,
        sse_fn=_sse,
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
    )
    if direct_recent is not None:
        for event in direct_recent:
            yield event
        return

    direct_progress = await try_handle_task_progress_direct_route(
        route=route.route_type,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        route_reason=route.reason_code,
        ts_ms=ts_ms,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        governor_result=governor_result,
        pii_ingress_stats=pii_ingress_stats,
        render_target_lang=render_target_lang,
        fallback_no_summary_text="当前没有可汇报的执行进度。",
        build_task_progress_sub_context_fn=_build_task_progress_sub_context,
        sse_fn=_sse,
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
    )
    if direct_progress is not None:
        for event in direct_progress:
            yield event
        return

    runtime_task_registry = get_runtime_task_registry()
    runtime_task = runtime_task_registry.create_task(
        task_kind=route.route_type,
        target_id=route.target_id,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        source="executive_runtime",
        detail=route.reason_code,
        progress_message=f"route={route.route_type}",
        meta={
            "intent_id": intent_id,
            "render_target_lang": render_target_lang,
        },
    )

    runtime_capability_payload = None
    if route.route_type == "chat":
        runtime_capability_payload = _maybe_synthesize_runtime_capability(
            request_id=request_id,
            session_id=session_id,
            user_message=sanitized_message,
            route_type=route.route_type,
            target_id=route.target_id,
            reason_code=route.reason_code,
            llm_route_used=llm_route_used,
        )
        if runtime_capability_payload is not None:
            route = _RouteSelection(
                route_type=_normalize_text(runtime_capability_payload.get("executor_route"), lower=True) or "delegate",
                target_id=_normalize_text(runtime_capability_payload.get("executor_target")) or "intent_agent",
                reason_code="capability_gap:runtime_synthesized",
            )
            runtime_task_registry.update_task(
                runtime_task.task_id,
                task_kind=route.route_type,
                target_id=route.target_id,
                detail=route.reason_code,
                progress_message="runtime capability synthesized",
                meta={
                    **dict(runtime_task.meta or {}),
                    "runtime_capability_payload": runtime_capability_payload,
                },
            )

    plan = None
    if route.route_type == "plan":
        plan = _maybe_build_multi_step_plan(
            request_id=request_id,
            sanitized_message=sanitized_message,
            intent_draft=dict(intent_draft or {}),
            skill_registry=skill_registry,
        )
        if plan is None:
            route = _RouteSelection("delegate", "intent_agent", "plan_fallback:intent_agent")

    yield _sse(
        {
            "phase": "EXECUTIVE_ROUTE",
            "done": False,
            "request_id": request_id,
            "route_type": route.route_type,
            "target_id": route.target_id or None,
            "reason_code": route.reason_code,
            "runtime_task_id": runtime_task.task_id,
            "runtime_capability_id": dict(runtime_capability_payload or {}).get("runtime_capability", {}).get("runtime_capability_id"),
        }
    )

    if route.route_type == "chat":
        async for event in _stream_chat_with_context(
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            recall_slot_text=recall_slot_text,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta={
                "cortex_orchestrator_used": True,
                "executive_router_used": True,
                "skill_route_type": "chat",
                "skill_family": "social",
                "skill_route_reason": route.reason_code,
            },
            sub_agent_context=None,
            runtime_capability_payload=runtime_capability_payload,
        ):
            yield event
        runtime_task_registry.mark_completed(
            runtime_task.task_id,
            detail="chat route completed",
            result_text="chat stream completed",
            result_code="CHAT_COMPLETED",
        )
        return

    if plan is not None:
        runtime_task_registry.update_task(
            runtime_task.task_id,
            progress_message=f"executing plan {plan.plan_id}",
            meta={
                **dict(runtime_task.meta or {}),
                "plan": plan.as_dict(),
            },
        )
        plan_outcome, plan_meta = await _execute_skill_dag(
            plan=plan,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            recall_slot_text=recall_slot_text,
            render_target_lang=render_target_lang,
        )
        trace_stage_meta = {
            "cortex_orchestrator_used": True,
            "executive_router_used": True,
            "skill_route_type": "plan",
            "skill_family": "executive",
            "skill_route_reason": route.reason_code,
            "plan_id": plan.plan_id,
            "parallel_enabled": bool(plan_meta.get("parallel_enabled")),
        }
        if plan_outcome.success:
            runtime_task_registry.mark_completed(
                runtime_task.task_id,
                detail="plan completed",
                result_text=plan_outcome.result_text,
                result_code="PLAN_COMPLETED",
            )
        else:
            runtime_task_registry.mark_failed(
                runtime_task.task_id,
                detail="plan failed",
                error=plan_outcome.error_text,
                result_code="PLAN_FAILED",
                result_text=plan_outcome.result_text,
            )
        async for event in _stream_chat_with_context(
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            recall_slot_text=recall_slot_text,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta=trace_stage_meta,
            sub_agent_context=_build_sub_agent_context(
                agent_id="executive_planner",
                task_text=sanitized_message,
                outcome=plan_outcome,
            ),
            runtime_capability_payload=runtime_capability_payload,
        ):
            yield event
        return

    route_decision = build_skill_route_decision(
        route_type=route.route_type,
        target_id=route.target_id,
        reason_code=route.reason_code,
        skill_registry=skill_registry,
    )
    task_text = _normalize_text(dict(runtime_capability_payload or {}).get("task_text")) or sanitized_message
    record_executive_handoff(
        request_id=request_id,
        session_id=session_id,
        handoff_route=route.route_type,
        handoff_target=route.target_id or None,
        handoff_reason=route.reason_code,
        extra={
            "skill_family": route_decision.skill_family,
            "skill_id": route_decision.skill_id,
        },
    )

    if route.route_type == "skill":
        outcome = await asyncio.to_thread(
            _execute_skill,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id=route.target_id,
            task_text=task_text,
            user_message=sanitized_message,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            recall_slot_text=recall_slot_text,
            render_target_lang=render_target_lang,
        )
    else:
        outcome = await _dispatch_to_sub_agent(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            target_id=route.target_id or "intent_agent",
            task_text=task_text,
            user_message=sanitized_message,
            context={
                "skill_family": route_decision.skill_family or "general",
                "route_reason": route.reason_code,
            },
        )

    if runtime_capability_payload is not None:
        record_runtime_capability_execution(
            request_id=request_id,
            runtime_capability_payload=runtime_capability_payload,
            success=outcome.success,
            latency_ms=outcome.latency_ms,
            error_text=outcome.error_text or None,
            executor_route=route.route_type,
            executor_target=route.target_id or "",
            actor="executive_runtime",
        )

    if not outcome.success:
        recovery_plan = _build_failure_recovery_plan(
            route_type=route.route_type,
            target_id=route.target_id,
            skill_family=route_decision.skill_family or "general",
            payload=outcome.payload,
        )
        if recovery_plan:
            runtime_task_registry.mark_recovering(
                runtime_task.task_id,
                detail=recovery_plan["reason_code"],
                recovery_plan=recovery_plan,
            )
            if recovery_plan["route_type"] == "delegate":
                recovered = await _dispatch_to_sub_agent(
                    request_id=request_id,
                    session_id=session_id,
                    person_id=person_id,
                    target_id=recovery_plan["target_id"],
                    task_text=task_text,
                    user_message=sanitized_message,
                    context={
                        "skill_family": route_decision.skill_family or "general",
                        "route_reason": recovery_plan["reason_code"],
                    },
                )
                runtime_task_registry.attach_recovery_result(
                    runtime_task.task_id,
                    recovery_status="SUCCESS" if recovered.success else "FAILED",
                    recovery_result_text=recovered.result_text,
                    recovery_error=recovered.error_text,
                )
                if recovered.success:
                    outcome = recovered

    if outcome.success:
        runtime_task_registry.mark_completed(
            runtime_task.task_id,
            detail=f"{route.route_type} completed",
            result_text=outcome.result_text,
            result_code="EXECUTED",
        )
    else:
        runtime_task_registry.mark_failed(
            runtime_task.task_id,
            detail=f"{route.route_type} failed",
            error=outcome.error_text,
            result_code="EXECUTION_FAILED",
            result_text=outcome.result_text,
        )

    trace_stage_meta = {
        "cortex_orchestrator_used": True,
        "executive_router_used": True,
        "skill_route_type": route.route_type,
        "skill_family": route_decision.skill_family or "general",
        "skill_id": route_decision.skill_id,
        "skill_route_reason": route.reason_code,
    }
    async for event in _stream_chat_with_context(
        route_reason=route.reason_code,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        recall_slot_text=recall_slot_text,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=_build_sub_agent_context(
            agent_id=route.target_id or route.route_type,
            task_text=task_text,
            outcome=outcome,
        ),
        runtime_capability_payload=runtime_capability_payload,
    ):
        yield event


__all__ = [
    "_hard_rule_route",
    "_maybe_build_multi_step_plan",
    "_build_planning_agent_plan_steps",
    "_should_force_complex_plan",
    "_build_task_progress_sub_context",
    "_dispatch_to_sub_agent",
    "_execute_skill",
    "_maybe_synthesize_runtime_capability",
    "_build_failure_recovery_plan",
    "_executive_runtime_core_impl",
]
