"""Message intent classification functions for the executive runtime.

Each public function inspects a user message (and optionally
additional context) to classify what kind of intent it represents,
returning a boolean or target-id string.
"""

from __future__ import annotations

from typing import Any

from host_brain.app.executive.constants import (
    _MULTI_STEP_MARKERS,
    _RECENT_TURN_PATTERNS,
    _SIMPLE_TASK_SPLIT_RE,
    _SIMPLE_TECH_ACTION_BLOCKERS,
    _SIMPLE_TECH_ADVICE_TERMS,
    _TASK_PROGRESS_PATTERNS,
)
from host_brain.app.executive.text_utils import _normalize_text


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
    candidate = _normalize_text(intent_draft.get("target_id"))
    if candidate:
        try:
            if skill_registry.get(candidate) is not None:
                return candidate
        except Exception:
            pass
    candidate = _normalize_text(intent_draft.get("action"))
    if candidate and candidate not in {"exec_intent", "narrative_only", "noop", "work_code", "work_translate", "task_progress", "recent_turn"}:
        try:
            if skill_registry.get(candidate) is not None:
                return candidate
        except Exception:
            pass
    return ""


def _extract_delegate_target(intent_draft: dict[str, Any]) -> str:
    candidate = _normalize_text(intent_draft.get("target_id"))
    if candidate.endswith("_agent"):
        return candidate
    candidate = _normalize_text(intent_draft.get("action"))
    if candidate.endswith("_agent"):
        return candidate
    return ""


def _surface_control_requests_memory_recall(surface_task_control: Any, intent_draft: dict[str, Any]) -> bool:
    if not isinstance(surface_task_control, dict) or not surface_task_control:
        return False
    task_mode = _normalize_text(surface_task_control.get("task_mode"), lower=True)
    response_move = _normalize_text(surface_task_control.get("response_move"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id") or intent_draft.get("skill_id"), lower=True)
    action_hint = _normalize_text(intent_draft.get("action"), lower=True)
    recall_markers = {
        "memory_recall",
        "memory_followup",
        "recall_memory",
        "recall_preference",
        "recall_relationship",
    }
    if task_mode in recall_markers or response_move in recall_markers:
        return True
    return target_hint == "memory_recall" or action_hint == "memory_recall"


def _message_requests_memory_recall(user_message: str) -> bool:
    message = _normalize_text(user_message)
    if message and (
        any(token in message for token in ("回忆检查", "记忆验收", "同会话验收", "新会话验收", "最终同会话验收"))
        or ("验收" in message and any(token in message for token in ("列出", "复述", "回答", "说出")))
        or (
            any(token in message for token in ("测试代号", "海边灯塔", "预热指标", "推荐格式"))
            and any(token in message for token in ("是什么", "指什么", "列出", "复述", "当前", "刚才", "刚刚"))
        )
    ):
        return True
    try:
        from host_brain.memory.life_memory_query import (  # noqa: PLC0415
            CONTRACT_CORRECTION,
            CONTRACT_EVIDENCE_REVIEW,
            CONTRACT_EXPLICIT_MEMORY,
            CONTRACT_FAILURE_DEBUG,
            CONTRACT_OPEN_LOOP,
            CONTRACT_PREFERENCE,
            CONTRACT_SELF_CHANGE,
            plan_life_memory_query,
        )

        plan = plan_life_memory_query(user_message)
    except Exception:
        return False
    if not bool(getattr(plan, "should_recall", False)):
        return False
    contract_id = str(getattr(plan, "recall_contract_id", "") or "")
    if contract_id == CONTRACT_FAILURE_DEBUG:
        message = _normalize_text(user_message)
        message_lower = message.lower()
        return any(
            marker in message or marker.lower() in message_lower
            for marker in (
                "失败原因",
                "为什么失败",
                "上次",
                "之前",
                "刚才",
                "那次",
                "那个",
                "DeepSeek",
                "Gemini",
                "Gateway",
                "网关",
                "状态库",
            )
        )
    return contract_id in {
        CONTRACT_CORRECTION,
        CONTRACT_EVIDENCE_REVIEW,
        CONTRACT_EXPLICIT_MEMORY,
        CONTRACT_OPEN_LOOP,
        CONTRACT_PREFERENCE,
        CONTRACT_SELF_CHANGE,
    }


def _looks_like_simple_technical_advice(user_message: str, intent_draft: dict[str, Any] | None = None) -> bool:
    text = _normalize_text(user_message, lower=True)
    if not text:
        return False
    if any(token in text for token in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    if not any(token in text for token in _SIMPLE_TECH_ADVICE_TERMS):
        return False
    if not any(token in text for token in ("怎么", "如何", "怎么办", "该", "排查", "查", "先看", "看什么", "?", "？")):
        return False
    draft = dict(intent_draft or {}) if isinstance(intent_draft, dict) else {}
    target_id = _normalize_text(draft.get("target_id") or draft.get("skill_id"), lower=True)
    if target_id and target_id not in {"", "agent_worker", "intent_agent"}:
        return False
    return True


def _looks_like_simple_task_split_advice(user_message: str, intent_draft: dict[str, Any] | None = None) -> bool:
    text = _normalize_text(user_message, lower=True)
    if not text or not _SIMPLE_TASK_SPLIT_RE.search(text):
        return False
    if any(token in text for token in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    draft = dict(intent_draft or {}) if isinstance(intent_draft, dict) else {}
    target_id = _normalize_text(draft.get("target_id") or draft.get("skill_id"), lower=True)
    if target_id and target_id not in {"", "intent_agent", "agent_worker"}:
        return False
    return True
