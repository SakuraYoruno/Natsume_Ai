"""Current-turn dialogue frame construction.

The dialogue frame is the foreground conversation anchor. It is intentionally
small: it binds deictic user messages such as "this type" to the latest visible
conversation context before long-term memory, lessons, or life-state hints can
influence generation.
"""

from __future__ import annotations

from host_brain.core.runtime_helpers import _copy_mapping as _mapping

from collections.abc import Mapping, Sequence
import re
from typing import Any


_TITLE_RE = re.compile(r"《([^》]{1,120})》")


def _text(value: Any, *, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _contains_any(text: str, tokens: Sequence[str]) -> bool:
    return any(token in text for token in tokens)


def _latest_visible_assistant_turn(recent_turns: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    for turn in reversed(_list(recent_turns)):
        item = _mapping(turn)
        content = _text(item.get("content"))
        if (
            str(item.get("role") or "").strip().lower() == "assistant"
            and content
            and not _looks_like_internal_leak(content)
        ):
            return item
    return {}


def _extract_title(text: str) -> str:
    match = _TITLE_RE.search(text or "")
    return _text(match.group(1), limit=120) if match else ""


def _looks_like_internal_leak(text: str) -> bool:
    compact = _text(text)
    if not compact:
        return False
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[:：]\s*(?:表达评价|指出系统|希望系统|用户目标|意图|置信度)", compact)
        or "user_goal" in compact
        or "selected_intent" in compact
        or "intent_hypoth" in compact
    )


def _recent_recommendation_target(recent_recommendation_context: Mapping[str, Any] | None) -> dict[str, Any]:
    context = _mapping(recent_recommendation_context)
    referenced = _mapping(context.get("referenced_item"))
    if referenced:
        title = _text(referenced.get("title"), limit=120)
        if title:
            return {
                "target_kind": "recommendation_item",
                "target_label": title,
                "target_title": title,
                "target_rank": referenced.get("rank") or referenced.get("index") or context.get("reference_index"),
                "detail_url": _text(referenced.get("detail_url"), limit=240),
                "traits": list(referenced.get("traits") or []),
                "source": "recent_recommendation_context.referenced_item",
            }
    return {}


def _assistant_turn_target(turn: Mapping[str, Any]) -> dict[str, Any]:
    content = _text(turn.get("content"), limit=420)
    if not content:
        return {}
    title = _extract_title(content)
    label = title or content
    return {
        "target_kind": "recent_assistant_visible_turn",
        "target_label": _text(label, limit=160),
        "target_title": title,
        "source_turn_id": _text(turn.get("turn_id"), limit=160),
        "source_request_id": _text(turn.get("request_id"), limit=160),
        "source": "recent_visible_assistant_turn",
        "evidence": content,
    }


def _looks_like_ask_natsume_taste_about_context(message: str) -> bool:
    compact = _text(message)
    if not compact:
        return False
    asks_natsume_view = _contains_any(
        compact,
        (
            "你喜欢",
            "你喜不喜欢",
            "你觉得",
            "你看",
            "你会喜欢",
            "你吃这套",
            "你吃这种",
            "你感兴趣",
        ),
    )
    deictic_context = _contains_any(
        compact,
        (
            "这种",
            "这类",
            "这个类型",
            "这种类型",
            "这种题材",
            "这个题材",
            "这本",
            "这部",
            "它",
            "这个",
        ),
    )
    return bool(asks_natsume_view and deictic_context)


def build_dialogue_frame(
    *,
    current_user_message: str,
    recent_turns: Sequence[Mapping[str, Any]] | None = None,
    recent_visible_events: Sequence[Mapping[str, Any]] | None = None,
    recent_recommendation_context: Mapping[str, Any] | None = None,
    conversation_event_resolution: Mapping[str, Any] | None = None,
    previous_belief: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a foreground dialogue frame for this user turn.

    This does not answer the user. It only decides what the current utterance is
    attached to, and what lower-priority context must not override.
    """

    message = _text(current_user_message, limit=240)
    resolved_event = _mapping(conversation_event_resolution)
    event_target = _mapping(resolved_event.get("target_item"))
    if str(resolved_event.get("status") or "") == "RESOLVED" and event_target:
        title = _text(event_target.get("title"), limit=120)
        return {
            "schema_version": "Natsume.dialogue_frame.v1",
            "status": "RESOLVED",
            "intent": str(resolved_event.get("action_kind") or "continue_bound_event"),
            "binding_kind": str(resolved_event.get("binding_kind") or "event_target"),
            "target": {
                "target_kind": "event_target_item",
                "target_label": title or _text(event_target.get("detail_url"), limit=180),
                "target_title": title,
                "target_rank": event_target.get("rank") or event_target.get("index"),
                "detail_url": _text(event_target.get("detail_url"), limit=240),
                "source": "conversation_event_resolution",
            },
            "user_goal": _text(_mapping(resolved_event.get("answer_guidance")).get("goal"), limit=180)
            or "承接当前已绑定事件回答",
            "confidence": float(resolved_event.get("confidence") or 0.8),
            "suppress_long_term_memory": True,
            "must_not": [
                "不要让长期记忆覆盖当前已绑定对象",
                "不要反问用户是哪一个，除非目标确实为空",
            ],
        }

    latest_assistant = _latest_visible_assistant_turn(recent_turns)
    if not latest_assistant and recent_visible_events:
        assistant_events = [
            _mapping(item)
            for item in _list(recent_visible_events)
            if str(_mapping(item).get("role") or "").strip().lower() == "assistant"
            and not _looks_like_internal_leak(_text(_mapping(item).get("content")))
        ]
        latest_assistant = assistant_events[-1] if assistant_events else {}

    recent_target = _recent_recommendation_target(recent_recommendation_context)
    turn_target = _assistant_turn_target(latest_assistant)
    target = recent_target or turn_target

    if target and _looks_like_ask_natsume_taste_about_context(message):
        label = _text(target.get("target_label"), limit=160)
        return {
            "schema_version": "Natsume.dialogue_frame.v1",
            "status": "RESOLVED",
            "intent": "ask_natsume_taste_about_recent_context",
            "binding_kind": "recent_visible_context",
            "target": target,
            "user_goal": f"想听 Natsume 对刚才提到的{label or '内容'}的个人看法",
            "confidence": 0.88,
            "suppress_long_term_memory": True,
            "must_not": [
                "不要把这句话当成用户在表达自己的偏好",
                "不要把内部意图标签、置信度或 user_goal 写进回复",
                "不要重新在多本推荐里排序，除非用户明确问哪本更好",
                "不要让长期偏好记忆抢走当前指代对象",
            ],
        }

    previous = _mapping(previous_belief)
    if previous and _contains_any(message, ("这种", "这类", "这个", "它", "刚才")):
        label = _text(previous.get("active_topic"), limit=160)
        if label:
            return {
                "schema_version": "Natsume.dialogue_frame.v1",
                "status": "RESOLVED",
                "intent": "continue_recent_session_topic",
                "binding_kind": "previous_session_belief",
                "target": {
                    "target_kind": "previous_active_topic",
                    "target_label": label,
                    "source": "previous_belief",
                },
                "user_goal": "承接当前会话里的上一话题继续回答",
                "confidence": 0.72,
                "suppress_long_term_memory": True,
                "must_not": [
                    "不要跳到旧推荐、旧 ASR 或长期语义记忆",
                    "不要输出内部意图标签",
                ],
            }

    return {
        "schema_version": "Natsume.dialogue_frame.v1",
        "status": "UNRESOLVED",
        "intent": "",
        "binding_kind": "",
        "target": {},
        "user_goal": "",
        "confidence": 0.0,
        "suppress_long_term_memory": False,
        "must_not": [],
    }


__all__ = ["build_dialogue_frame"]
