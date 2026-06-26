"""Routing logic for the executive runtime.

Contains the two-layer routing decision (_hard_rule_route) and the
complex-plan forcing check (_should_force_complex_plan), plus the
task-progress sub-context builder.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from host_brain.app.executive.constants import _RouteSelection
from host_brain.app.executive.text_utils import _normalize_text, _parse_int_env
from host_brain.app.executive.message_classification import (
    _extract_delegate_target,
    _extract_skill_target,
    _looks_like_multi_step_request,
    _looks_like_recent_turn_query,
    _looks_like_simple_task_split_advice,
    _looks_like_simple_technical_advice,
    _looks_like_task_progress_query,
    _message_requests_memory_recall,
    _surface_control_requests_memory_recall,
)

_logger = logging.getLogger("Natsume.executive_runtime_core")


def _hard_rule_route(
    *,
    intent_draft: dict[str, Any],
    sanitized_message: str,
    skill_registry: Any,
    surface_task_control: Any | None = None,
    allow_plan_detection: bool = True,
) -> _RouteSelection:
    route_hint = _normalize_text(intent_draft.get("route"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id"))
    if _surface_control_requests_memory_recall(surface_task_control, intent_draft) or _message_requests_memory_recall(sanitized_message):
        return _RouteSelection("chat", "", "hard_rule:surface_memory_recall")

    if route_hint in {"recent_turn", "task_progress", "chat"}:
        return _RouteSelection(route_hint, target_hint, f"intent_draft:{route_hint}")

    from host_brain.app.relational_chat_detector import build_relational_chat_reason_code
    relational_reason = build_relational_chat_reason_code(sanitized_message)
    if relational_reason:
        return _RouteSelection("chat", "", relational_reason)

    if route_hint == "plan":
        return _RouteSelection("plan", target_hint or "skill_dag", "intent_draft:plan")
    if route_hint == "skill" and target_hint:
        return _RouteSelection("skill", target_hint, "intent_draft:skill")
    if route_hint == "delegate" and target_hint:
        return _RouteSelection("delegate", target_hint, "intent_draft:delegate")
    if _looks_like_simple_technical_advice(sanitized_message, intent_draft):
        return _RouteSelection("chat", "", "hard_rule:surface_technical_advice")
    if _looks_like_simple_task_split_advice(sanitized_message, intent_draft):
        return _RouteSelection("chat", "", "hard_rule:surface_task_split")

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

    try:
        from host_brain.agents.planning_agent import RULE_ROUTE_MAP  # noqa: PLC0415
        msg_lower = _normalize_text(sanitized_message, lower=True)
        for keyword, (rule_route_type, rule_target_id) in RULE_ROUTE_MAP.items():
            if keyword.lower() in msg_lower:
                if rule_route_type == "skill":
                    try:
                        if skill_registry.get(rule_target_id) is not None:
                            return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
                        else:
                            _logger.debug(
                                "_hard_rule_route: skill '%s' not in registry, downgrade to delegate (keyword=%s)",
                                rule_target_id,
                                keyword,
                            )
                            return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:skill_fallback")
                    except Exception:
                        return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:registry_error")
                else:
                    return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
    except ImportError:
        pass

    if allow_plan_detection and _looks_multi_step_request(sanitized_message, intent_draft):
        return _RouteSelection("plan", "skill_dag", "hard_rule:multi_step")

    return _RouteSelection("chat", "", "default:chat")


def _should_force_complex_plan(
    *,
    route: _RouteSelection,
    user_message: str,
    intent_draft: dict[str, Any],
    llm_route_used: bool,
) -> bool:
    if route.route_type in {"plan", "recent_turn", "task_progress"}:
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

    from host_brain.app.capability_gap_detector import detect_capability_gap  # noqa: PLC0415
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
    from host_brain.app.executive.text_utils import _format_elapsed
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
