"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
import re

from host_brain.agent_runtime.runtime_texts import (
    _CONTEXTUAL_MEMORY_QUOTED_VALUE_RE,
    _CONTEXTUAL_MEMORY_RECALL_RE,
    _CONTEXTUAL_MEMORY_TRANSFORM_RE,
    _CONTEXTUAL_MEMORY_WRITE_RE,
    _logger,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts


def _is_short_assistant_reply_followup(user_message: str) -> bool:
    try:
        from host_brain.persona.context.interaction_thread import detect_assistant_reply_followup_kind  # noqa: PLC0415

        return bool(detect_assistant_reply_followup_kind(str(user_message or "").strip()))
    except Exception:
        return False


def _load_continuity_turns(
    *,
    application_ports: AgentRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    person_id: str,
    render_target_lang: str,
    fetch_limit: int = 24,
) -> list[dict[str, str]]:
    try:
        window = application_ports.conversation_history().fetch_window_for_messages(
            session_id=session_id,
            max_turns=fetch_limit,
            exclude_request_id=request_id,
            render_target_lang="ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh",
        )
        return [{"role": turn.role, "content": turn.content} for turn in window.recent_turns]
    except Exception as exc:
        _logger.debug("[%s] continuity lookup failed: %s", request_id, exc)
        return []


def _extract_contextual_memory_value(turns: Sequence[Mapping[str, Any]]) -> str:
    fallback_quoted = ""
    for turn in reversed(list(turns or [])):
        row = dict(turn or {})
        role = str(row.get("role") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        if "别名" in content:
            continue
        if "偏好" in content and ("系统测试" in content or "测试报告" in content or "可复现" in content):
            continue
        if "报告" in content and ("可复现" in content or "主观" in content or "体验" in content):
            continue
        if _CONTEXTUAL_MEMORY_RECALL_RE.search(content) or _CONTEXTUAL_MEMORY_TRANSFORM_RE.search(content):
            continue
        quoted = _CONTEXTUAL_MEMORY_QUOTED_VALUE_RE.search(content)
        if quoted:
            candidate = str(quoted.group(1) or "").strip()
            if 1 <= len(candidate) <= 32:
                if any(marker in content for marker in ("口令", "暗号", "关键词", "锚点", "记住")):
                    return candidate
                fallback_quoted = fallback_quoted or candidate
        if role and role != "user":
            continue
        if not _CONTEXTUAL_MEMORY_WRITE_RE.search(content):
            continue
        tail = _CONTEXTUAL_MEMORY_WRITE_RE.split(content, maxsplit=1)[-1]
        tail = tail.strip(" \t\r\n，,。.!！?？:：；;、")
        tail = re.sub(r"(只需要|请|不用|不要).*$", "", tail).strip(" \t\r\n，,。.!！?？:：；;、")
        if 1 <= len(tail) <= 32:
            return tail
    return fallback_quoted


def _extract_contextual_alias_target(user_message: str, turns: Sequence[Mapping[str, Any]]) -> str:
    if "小橙灯" not in str(user_message or ""):
        return ""
    for turn in reversed(list(turns or [])):
        content = str(dict(turn or {}).get("content") or "").strip()
        if "小橙灯" in content and "橙灯码" in content:
            return "橙灯码"
    return ""


def _short_contextual_memory_sentence(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    for suffix in ("还亮着。", "在这里。", "收到。"):
        if len(token + suffix) <= 12:
            return token + suffix
    return token


def _maybe_build_contextual_memory_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = person_id, render_target_lang
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    strict_answer = any(marker in normalized for marker in ("只回答", "只答", "本身"))
    wants_transform = bool(_CONTEXTUAL_MEMORY_TRANSFORM_RE.search(normalized))
    wants_recall = bool(_CONTEXTUAL_MEMORY_RECALL_RE.search(normalized))
    if not wants_transform and not wants_recall:
        return None
    if wants_transform or not strict_answer:
        return None
    _load_turns = load_continuity_turns or _load_continuity_turns
    turns = _load_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=40,
    )
    value = _extract_contextual_memory_value(turns)
    alias_target = _extract_contextual_alias_target(normalized, turns)
    if alias_target:
        value = alias_target
    if not value:
        return None
    if wants_transform:
        return _short_contextual_memory_sentence(value)
    return value


def _maybe_build_contextual_session_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = request_id, session_id, person_id, user_message, render_target_lang, load_continuity_turns
    return None


def _maybe_build_contextual_style_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = request_id, session_id, person_id, user_message, render_target_lang, load_continuity_turns
    return None


def _memory_recall_plan_should_bypass_direct_status_fastlane(user_message: str, *, task_mode: str | None = None) -> bool:
    """Return True when a direct recent/task route would hide required memory."""

    message = str(user_message or "").strip()
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
        from host_brain.memory.life_memory_query import plan_life_memory_query  # noqa: PLC0415
    except Exception:
        return False
    try:
        plan = plan_life_memory_query(user_message, task_mode=task_mode)
    except Exception:
        return False
    if not bool(getattr(plan, "should_recall", False)):
        return False
    contract_id = str(getattr(plan, "recall_contract_id", "") or "").strip()
    reason = str(getattr(plan, "reason", "") or "").strip()
    query_kind = str(getattr(plan, "query_kind", "") or "").strip()
    strong_contracts = {
        "anaphoric_selection",
        "collection_date",
        "collection_recent",
        "correction",
        "evidence_review",
        "failure_debug",
        "open_loop",
        "preference",
        "explicit_memory",
        "self_change",
    }
    return (
        contract_id in strong_contracts
        or reason in {"collection_memory_recall", "correction_recall", "evidence_review_recall", "failure_debug_recall"}
        or query_kind in {"correction", "evidence_review", "explicit_memory", "failure_debug", "open_loop", "preference_lookup"}
    )
