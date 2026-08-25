from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_preference_repository import PreferenceRepository


_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_INDEX_RE = re.compile(r"(?:第\s*)?([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:本|个|项|部|条|张|个结果)?")
_EXPLICIT_INDEX_RE = re.compile(r"(?:第\s*)?([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:本|个|项|部|条|张|个结果)")
_DETAIL_RE = re.compile(r"(详情|详细内容|详细|内容|简介|介绍|讲什么|说什么|具体|展开)")
_COMPARE_RE = re.compile(r"(哪本|哪个好|哪一个|比较|对比|值不值得|好看|推荐哪|更推荐)")
_EPUB_RE = re.compile(r"(epub|电子书|打包|下载|封装|购买|买下来|买)")
_RETRY_RE = re.compile(r"(重试|重新跑|再跑|重来|重新执行|再执行)")
_FAILURE_RE = re.compile(r"(为什么失败|失败原因|报错|怎么失败|哪里错|什么问题|哪里有问题)")
_STATUS_RE = re.compile(r"(状态|进度|怎么样|现在呢|现在|完了吗|好了没|跑完|完成|结果|效果|耗时)")
_SHOW_RE = re.compile(r"(查看|看下|看看|展示|打开|发我|发给我|截图|效果|拉取|取回)")
_PUSH_RE = re.compile(r"(推送|发到|发给|同步到|发送到|传到)")
_CANCEL_RE = re.compile(r"(取消|停止|停掉|中断|别跑|不要跑)")
_ADJUST_RE = re.compile(r"(调整|修复|改掉|处理|优化|完善|接入|整合)")
_CONTINUE_RE = re.compile(r"^(继续|然后呢|接着|往下|下一步|后面呢)[。！？!\s]*$")
_PROACTIVE_CONTEXT_RE = re.compile(r"^(怎么了|咋了|为什么这么说|刚才说什么|刚才怎么了)[。！？?\s]*$")
_POSITIVE_FEEDBACK_RE = re.compile(r"(喜欢|不错|挺好|可以|就这个|继续这种|合适|好看)")
_NEGATIVE_FEEDBACK_RE = re.compile(r"(不喜欢|不要|别推|不行|换一个|雷|避雷|难看)")

_EVENT_FAMILY_SOURCE_TOKENS = {
    "browser_automation": ("browser", "web", "crawl", "scrape", "screenshot"),
    "artifact": ("artifact", "file", "output", "report"),
    "epub": ("epub", "ebook", "lightnovel_epub"),
    "background_work": ("work", "task", "job", "codex", "agent", "production"),
    "digital_life": ("life", "organ", "embodiment", "vts", "mate", "proactive"),
    "multimodal": ("multimodal", "vision", "image", "attachment", "screenshot"),
    "voice": ("asr", "tts", "voice", "audio", "vad"),
    "memory": ("memory", "semantic", "preference", "profile", "recommendation_feedback"),
    "skill": ("skill",),
}


def resolve_conversation_event_action(
    *,
    ledger: Any,
    person_id: str = "default",
    session_id: str = "",
    current_user_message: str = "",
    now_ms: int | None = None,
    before_ms: int | None = None,
    limit: int = 16,
) -> dict[str, Any]:
    """Resolve the user's short follow-up into a concrete recent event/action.

    This module is intentionally not a reply generator. It only binds the
    current utterance to recent visible/system events and describes the action
    the normal dialogue chain should handle.
    """

    now_ms = int(now_ms or time.time() * 1000)
    text = (current_user_message or "").strip()
    session_id = (session_id or "").strip()
    person_id = (person_id or "default").strip() or "default"

    base = _empty_resolution(text, now_ms)
    if not text:
        return base

    try:
        memory_repository = MemoryEventRepository.from_ledger(ledger)
    except Exception:
        memory_repository = None
    try:
        preference_repository = PreferenceRepository.from_ledger(ledger)
    except Exception:
        preference_repository = None

    recent_visible = _recent_memory_events(
        repository=memory_repository,
        person_id=person_id,
        session_id=session_id,
        visibility="visible",
        before_ms=before_ms,
        limit=limit,
    )
    recent_visible = [
        event
        for event in recent_visible
        if not _looks_like_internal_leak(event.get("content") or event.get("content_summary"))
    ]
    recent_system = _recent_memory_events(
        repository=memory_repository,
        person_id=person_id,
        session_id=session_id,
        visibility="system",
        before_ms=before_ms,
        limit=limit,
    )
    latest_delivery = _latest_recommendation_delivery(
        repository=preference_repository,
        person_id=person_id,
        session_id=session_id,
        before_ms=before_ms,
    )

    rec_resolution = _resolve_recommendation_action(text, latest_delivery, recent_visible=recent_visible)
    if rec_resolution:
        return _merge_resolution(base, rec_resolution)

    recent_assistant = _first_event(recent_visible, role="assistant")
    if recent_assistant and _PROACTIVE_CONTEXT_RE.search(text):
        return _merge_resolution(
            base,
            {
                "status": "RESOLVED",
                "binding_kind": "recent_visible_assistant_event",
                "action_kind": "answer_recent_visible_event",
                "domain": _event_domain(recent_assistant),
                "confidence": 0.82,
                "source_event": _compact_event(recent_assistant),
                "target": {
                    "event_id": recent_assistant.get("event_id"),
                    "event_kind": recent_assistant.get("event_kind"),
                    "content_summary": recent_assistant.get("content_summary") or recent_assistant.get("content"),
                },
                "evidence_refs": _event_refs(recent_assistant),
                "reason_code": "BIND_RECENT_VISIBLE_ASSISTANT_EVENT",
                "answer_guidance": {
                    "goal": "解释上一条可见 assistant 消息的语境和动机。",
                    "avoid": "不要跳到旧推荐、旧任务或长期记忆。",
                },
            },
        )

    generic = _resolve_operational_action_from_events(text, recent_visible + recent_system)
    if generic:
        return _merge_resolution(base, generic)

    work_or_skill = _latest_work_or_skill_event(recent_visible + recent_system)
    if work_or_skill:
        if _FAILURE_RE.search(text):
            return _merge_resolution(
                base,
                _work_skill_resolution(
                    event=work_or_skill,
                    action_kind="explain_failure",
                    reason_code="BIND_RECENT_WORK_OR_SKILL_FAILURE_QUERY",
                    confidence=0.76,
                ),
            )
        if _RETRY_RE.search(text):
            return _merge_resolution(
                base,
                _work_skill_resolution(
                    event=work_or_skill,
                    action_kind="retry_skill_or_work_event",
                    reason_code="BIND_RECENT_WORK_OR_SKILL_RETRY",
                    confidence=0.76,
                ),
            )
        if _CONTINUE_RE.search(text) or _DETAIL_RE.search(text):
            return _merge_resolution(
                base,
                _work_skill_resolution(
                    event=work_or_skill,
                    action_kind="explain_or_continue_skill_result",
                    reason_code="BIND_RECENT_WORK_OR_SKILL_CONTINUATION",
                    confidence=0.68,
                ),
            )

    if recent_assistant and _CONTINUE_RE.search(text):
        return _merge_resolution(
            base,
            {
                "status": "RESOLVED",
                "binding_kind": "recent_visible_assistant_event",
                "action_kind": "continue_from_last_event",
                "domain": _event_domain(recent_assistant),
                "confidence": 0.62,
                "source_event": _compact_event(recent_assistant),
                "target": {
                    "event_id": recent_assistant.get("event_id"),
                    "event_kind": recent_assistant.get("event_kind"),
                    "content_summary": recent_assistant.get("content_summary") or recent_assistant.get("content"),
                },
                "evidence_refs": _event_refs(recent_assistant),
                "reason_code": "BIND_CONTINUE_TO_RECENT_VISIBLE_EVENT",
                "answer_guidance": {
                    "goal": "沿着最近一条可见事件继续，而不是重新召回长期话题。",
                },
            },
        )

    return base


def _empty_resolution(text: str, now_ms: int) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.conversation_event_resolution.v1",
        "status": "UNRESOLVED",
        "binding_kind": "",
        "action_kind": "",
        "domain": "",
        "confidence": 0.0,
        "source_event": {},
        "target": {},
        "target_item": {},
        "evidence_refs": [],
        "reason_code": "NO_RECENT_EVENT_BINDING",
        "must_not": [],
        "answer_guidance": {},
        "query": text,
        "resolved_at_ms": now_ms,
    }


def _merge_resolution(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(dict(patch))
    if merged.get("status") == "RESOLVED":
        must_not = list(merged.get("must_not") or [])
        must_not.extend(
            [
                "不要忽略 conversation_event_resolution 已绑定的目标。",
                "不要在目标明确时反问用户是哪一个。",
                "不要让长期记忆抢走当前事件承接。",
            ]
        )
        merged["must_not"] = _dedupe(must_not)
    return merged


def _resolve_recommendation_action(
    text: str,
    delivery: Mapping[str, Any] | None,
    *,
    recent_visible: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not delivery:
        return None
    items = list(delivery.get("items") or [])
    if not items:
        return None

    index = _parse_index(text)
    item = _item_by_index(items, index) if index else None
    has_detail = bool(_DETAIL_RE.search(text))
    has_compare = bool(_COMPARE_RE.search(text))
    has_epub = bool(_EPUB_RE.search(text))
    has_positive = bool(_POSITIVE_FEEDBACK_RE.search(text))
    has_negative = bool(_NEGATIVE_FEEDBACK_RE.search(text))
    inherited_item = False
    if not item and (has_detail or has_epub or has_positive or has_negative):
        item = _infer_recent_recommendation_item(items=items, recent_visible=list(recent_visible or []), current_text=text)
        inherited_item = bool(item)

    if item and has_detail:
        return _recommendation_resolution(
            delivery=delivery,
            item=item,
            action_kind="fetch_item_detail",
            reason_code="BIND_RECOMMENDATION_ITEM_DETAIL_FROM_RECENT_REFERENCE" if inherited_item else "BIND_RECOMMENDATION_ITEM_DETAIL",
            confidence=0.84 if inherited_item else 0.92,
            guidance="继承上一轮已绑定的推荐项，继续给出详细内容；目标明确时不要反问“哪方面”。"
            if inherited_item
            else "围绕用户指定的推荐项给出详细内容；目标明确时不要反问“哪方面”。",
        )
    if item and has_epub:
        return _recommendation_resolution(
            delivery=delivery,
            item=item,
            action_kind="package_item_epub",
            reason_code="BIND_RECOMMENDATION_ITEM_EPUB_FROM_RECENT_REFERENCE" if inherited_item else "BIND_RECOMMENDATION_ITEM_EPUB",
            confidence=0.82 if inherited_item else 0.88,
            guidance="继承上一轮已绑定的推荐项，执行购买/下载/打包 EPUB 的后续动作；目标明确时不要反问是哪本。"
            if inherited_item
            else "围绕用户指定的推荐项执行购买/下载/打包 EPUB 的后续动作。",
        )
    if item and (has_positive or has_negative):
        return _recommendation_resolution(
            delivery=delivery,
            item=item,
            action_kind="record_recommendation_feedback",
            reason_code="BIND_RECOMMENDATION_ITEM_FEEDBACK",
            confidence=0.86,
            guidance="把反馈绑定到用户指定推荐项，再交给偏好画像更新；不要无对象写长期偏好。",
            polarity="positive" if has_positive and not has_negative else "negative",
        )
    if has_compare:
        return {
            "status": "RESOLVED",
            "binding_kind": "recent_recommendation_delivery",
            "action_kind": "compare_recommendation_items",
            "domain": delivery.get("domain") or "lightnovel_recommendation",
            "confidence": 0.74,
            "source_event": _delivery_source_event(delivery),
            "target": {
                "delivery_id": delivery.get("delivery_id"),
                "item_count": len(items),
                "items": [_compact_recommendation_item(it) for it in items[:5]],
            },
            "evidence_refs": _delivery_refs(delivery),
            "reason_code": "BIND_RECOMMENDATION_COMPARE",
            "answer_guidance": {
                "goal": "比较最近一次推荐集里的作品，结合已有证据说明区别。",
            },
        }
    return None


def _recommendation_resolution(
    *,
    delivery: Mapping[str, Any],
    item: Mapping[str, Any],
    action_kind: str,
    reason_code: str,
    confidence: float,
    guidance: str,
    polarity: str = "",
) -> dict[str, Any]:
    target_item = _compact_recommendation_item(item)
    payload = {
        "status": "RESOLVED",
        "binding_kind": "recommendation_item_reference",
        "action_kind": action_kind,
        "domain": delivery.get("domain") or "lightnovel_recommendation",
        "confidence": confidence,
        "source_event": _delivery_source_event(delivery),
        "target": {
            "delivery_id": delivery.get("delivery_id"),
            "recommendations_ref": delivery.get("recommendations_ref"),
            "ranked_recommendations_ref": delivery.get("ranked_recommendations_ref"),
        },
        "target_item": target_item,
        "evidence_refs": _delivery_refs(delivery),
        "reason_code": reason_code,
        "answer_guidance": {
            "goal": guidance,
            "target_item_title": target_item.get("title"),
            "target_item_rank": target_item.get("rank"),
        },
    }
    if polarity:
        payload["answer_guidance"]["feedback_polarity"] = polarity
    return payload


def _work_skill_resolution(
    *,
    event: Mapping[str, Any],
    action_kind: str,
    reason_code: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "status": "RESOLVED",
        "binding_kind": "recent_work_or_skill_event",
        "action_kind": action_kind,
        "domain": _event_domain(event) or "work",
        "confidence": confidence,
        "source_event": _compact_event(event),
        "target": {
            "event_id": event.get("event_id"),
            "event_kind": event.get("event_kind"),
            "source_kind": event.get("source_kind"),
            "content_summary": event.get("content_summary") or event.get("content"),
        },
        "evidence_refs": _event_refs(event),
        "reason_code": reason_code,
        "answer_guidance": {
            "goal": "围绕最近任务/skill 事件继续处理，不要跳到不相关记忆。",
        },
    }


def _resolve_operational_action(text: str, event: Mapping[str, Any]) -> dict[str, Any] | None:
    family = _event_family(event)
    if not family:
        return None
    action_kind = ""
    reason_code = ""
    confidence = 0.0
    if _FAILURE_RE.search(text):
        action_kind = "explain_recent_event_failure"
        reason_code = "BIND_RECENT_OPERATIONAL_FAILURE_QUERY"
        confidence = 0.78
    elif _RETRY_RE.search(text):
        action_kind = "retry_recent_operational_event"
        reason_code = "BIND_RECENT_OPERATIONAL_RETRY"
        confidence = 0.76
    elif _CANCEL_RE.search(text):
        action_kind = "cancel_recent_operational_event"
        reason_code = "BIND_RECENT_OPERATIONAL_CANCEL"
        confidence = 0.72
    elif _PUSH_RE.search(text):
        action_kind = "push_recent_artifact_or_result"
        reason_code = "BIND_RECENT_OPERATIONAL_PUSH"
        confidence = 0.72
    elif _SHOW_RE.search(text):
        action_kind = "present_recent_artifact_or_observation"
        reason_code = "BIND_RECENT_OPERATIONAL_PRESENT"
        confidence = 0.70
    elif _STATUS_RE.search(text):
        action_kind = "report_recent_operational_status"
        reason_code = "BIND_RECENT_OPERATIONAL_STATUS"
        confidence = 0.70
    elif _DETAIL_RE.search(text) or _CONTINUE_RE.search(text):
        action_kind = "continue_recent_operational_event"
        reason_code = "BIND_RECENT_OPERATIONAL_CONTINUATION"
        confidence = 0.66
    elif _ADJUST_RE.search(text) and family in {"digital_life", "memory", "voice", "browser_automation", "artifact"}:
        action_kind = "adjust_recent_operational_system"
        reason_code = "BIND_RECENT_OPERATIONAL_ADJUSTMENT"
        confidence = 0.62
    if not action_kind:
        return None
    return {
        "status": "RESOLVED",
        "binding_kind": f"recent_{family}_event",
        "action_kind": action_kind,
        "domain": family,
        "confidence": confidence,
        "source_event": _compact_event(event),
        "target": _operational_target(event, family=family),
        "evidence_refs": _event_refs(event),
        "reason_code": reason_code,
        "answer_guidance": {
            "goal": _operational_goal(action_kind=action_kind, family=family),
            "event_family": family,
        },
    }


def _resolve_operational_action_from_events(text: str, events: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    candidates = [event for event in events if _event_family(event)]
    if not candidates:
        return None
    preferred = _preferred_families_for_text(text)
    ordered: list[Mapping[str, Any]] = []
    if preferred:
        for family in preferred:
            ordered.extend([event for event in candidates if _event_family(event) == family and event not in ordered])
    ordered.extend([event for event in candidates if event not in ordered])
    for event in ordered:
        resolution = _resolve_operational_action(text, event)
        if resolution:
            return resolution
    return None


def _preferred_families_for_text(text: str) -> list[str]:
    value = str(text or "").lower()
    if _EPUB_RE.search(value):
        return ["epub", "artifact", "recommendation", "browser_automation", "background_work", "skill"]
    if _PUSH_RE.search(value):
        return ["artifact", "epub", "recommendation", "browser_automation", "multimodal", "background_work", "skill", "digital_life"]
    if re.search(r"(效果|封面|图片|图|截图|画面|照片|看图|附件)", value):
        return ["artifact", "recommendation", "epub", "multimodal", "browser_automation", "background_work", "skill"]
    if re.search(r"(表情|动作|模型|桌宠|vts|mate|身体|口型|姿态)", value):
        return ["digital_life", "voice", "artifact", "background_work", "skill"]
    if re.search(r"(语音|声音|asr|tts|vad|声纹|麦克风|录音)", value):
        return ["voice", "digital_life", "artifact", "background_work", "skill"]
    if re.search(r"(记忆|偏好|画像|召回|上下文|承接)", value):
        return ["memory", "recommendation", "background_work", "skill"]
    if _STATUS_RE.search(value):
        return ["artifact", "epub", "recommendation", "browser_automation", "background_work", "skill", "multimodal", "voice", "digital_life"]
    if _SHOW_RE.search(value):
        return ["artifact", "recommendation", "browser_automation", "epub", "multimodal", "background_work", "skill", "digital_life"]
    return []


def _parse_index(text: str) -> int | None:
    matches = list(_INDEX_RE.finditer(text or ""))
    if not matches:
        return None
    raw = matches[-1].group(1)
    if raw.isdigit():
        value = int(raw)
    else:
        value = _CN_NUM.get(raw)
    if value and 0 < value <= 50:
        return value
    return None


def _item_by_index(items: list[Any], index: int | None) -> Mapping[str, Any] | None:
    if not index:
        return None
    for item in items:
        if not isinstance(item, Mapping):
            continue
        rank = item.get("rank") or item.get("index") or item.get("display_index")
        try:
            if int(rank) == index:
                return item
        except Exception:
            pass
    pos = index - 1
    if 0 <= pos < len(items) and isinstance(items[pos], Mapping):
        return items[pos]
    return None


def _infer_recent_recommendation_item(
    *,
    items: list[Any],
    recent_visible: list[Mapping[str, Any]],
    current_text: str,
) -> Mapping[str, Any] | None:
    current = str(current_text or "").strip()
    title_index: list[tuple[str, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        if title:
            key = _normalize_reference_key(title)
            if key:
                title_index.append((key, item))
    for event in recent_visible[:12]:
        if str(event.get("role") or "") not in {"user", "assistant"}:
            continue
        content = str(event.get("content") or event.get("content_summary") or "").strip()
        if not content or content == current:
            continue
        previous_index = _parse_explicit_index(content) if str(event.get("role") or "") == "user" else None
        if previous_index:
            item = _item_by_index(items, previous_index)
            if item:
                return item
        content_key = _normalize_reference_key(content)
        if content_key:
            for title_key, item in title_index:
                if title_key and (title_key in content_key or _reference_key_overlap(title_key, content_key) >= 0.72):
                    return item
    return None


def _normalize_reference_key(value: Any) -> str:
    text = _repair_mojibake_text(value).lower().translate(
        str.maketrans(
            {
                "轉": "转",
                "學": "学",
                "園": "园",
                "戀": "恋",
                "愛": "爱",
                "異": "异",
                "體": "体",
                "後": "后",
                "與": "与",
                "為": "为",
                "歡": "欢",
                "說": "说",
                "輕": "轻",
                "麼": "么",
                "覺": "觉",
                "貴": "贵",
                "遊": "游",
                "戲": "戏",
            }
        )
    )
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)


def _repair_mojibake_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    markers = ("Ã", "Â", "ä", "å", "ç", "è", "é", "ï¼", "ã")
    if not any(marker in text for marker in markers):
        return text
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return text
    original_cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    repaired_cjk = len(re.findall(r"[\u4e00-\u9fff]", repaired))
    return repaired if repaired_cjk > original_cjk else text


def _looks_like_internal_leak(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[:：]\s*(?:表达评价|指出系统|希望系统|用户目标|意图|置信度)", text)
        or "user_goal" in text
        or "selected_intent" in text
        or "intent_hypoth" in text
    )


def _reference_key_overlap(left: str, right: str) -> float:
    left_text = str(left or "")
    right_text = str(right or "")
    if not left_text or not right_text:
        return 0.0
    previous = [0] * (len(right_text) + 1)
    best = 0
    for left_ch in left_text:
        current = [0] * (len(right_text) + 1)
        for idx, right_ch in enumerate(right_text, start=1):
            if left_ch == right_ch:
                current[idx] = previous[idx - 1] + 1
                if current[idx] > best:
                    best = current[idx]
        previous = current
    return best / max(1, len(left_text))


def _parse_explicit_index(text: str) -> int | None:
    matches = list(_EXPLICIT_INDEX_RE.finditer(text or ""))
    if not matches:
        return None
    raw = matches[-1].group(1)
    if raw.isdigit():
        value = int(raw)
    else:
        value = _CN_NUM.get(raw)
    if value and 0 < value <= 50:
        return value
    return None


def _recent_memory_events(
    *,
    repository: MemoryEventRepository | None,
    person_id: str,
    session_id: str,
    visibility: str,
    before_ms: int | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    if repository is None:
        return []
    try:
        return repository.list_events(
            person_id=person_id,
            session_id=session_id,
            conversation_id=session_id,
            visibility=visibility,
            created_before_ms=before_ms,
            match_any_identity=True,
            limit=limit,
        )
    except Exception:
        return []


def _latest_recommendation_delivery(
    *,
    repository: PreferenceRepository | None,
    person_id: str,
    session_id: str,
    before_ms: int | None = None,
) -> dict[str, Any] | None:
    if repository is None:
        return None
    try:
        return repository.get_latest_delivery(
            person_id=person_id,
            session_id=session_id,
            conversation_id=session_id,
            created_before_ms=before_ms,
        )
    except Exception:
        return None


def _latest_work_or_skill_event(events: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for event in events:
        source = str(event.get("source_kind") or "").lower()
        kind = str(event.get("event_kind") or "").lower()
        refs = _loads(event.get("refs_json"), {})
        ref_text = json.dumps(refs, ensure_ascii=False).lower() if refs else ""
        if any(token in source for token in ("skill", "work", "job", "agent", "codex", "browser", "production")):
            return event
        if any(token in kind for token in ("skill", "work", "job", "artifact", "browser", "delivery", "task")):
            return event
        if any(token in ref_text for token in ("skill", "work_id", "job_id", "artifact", "browser")):
            return event
    return None


def _event_family(event: Mapping[str, Any]) -> str:
    source = str(event.get("source_kind") or "").lower()
    kind = str(event.get("event_kind") or "").lower()
    visibility = str(event.get("visibility") or "").lower()
    role = str(event.get("role") or "").lower()
    if visibility == "visible" and source == "conversation_turn" and role in {"user", "assistant"}:
        return ""
    refs = _loads(event.get("refs_json"), {})
    causality = _loads(event.get("causality_json"), {})
    content = f"{event.get('content_summary') or ''} {event.get('content') or ''}".lower()
    ref_text = ""
    if refs:
        ref_text += json.dumps(refs, ensure_ascii=False, default=str).lower()
    if causality:
        ref_text += " " + json.dumps(causality, ensure_ascii=False, default=str).lower()
    haystack = f"{source} {kind} {ref_text} {content}"
    if "recommendation_delivery" in haystack or "lightnovel_recommendation" in haystack:
        return "recommendation"
    for family, tokens in _EVENT_FAMILY_SOURCE_TOKENS.items():
        if any(token in haystack for token in tokens):
            return family
    return ""


def _operational_target(event: Mapping[str, Any], *, family: str) -> dict[str, Any]:
    refs = _loads(event.get("refs_json"), {})
    causality = _loads(event.get("causality_json"), {})
    target = {
        "event_id": event.get("event_id"),
        "event_kind": event.get("event_kind"),
        "source_kind": event.get("source_kind"),
        "family": family,
        "content_summary": event.get("content_summary") or _trim(event.get("content"), 240),
    }
    if isinstance(refs, Mapping):
        for key in (
            "work_id",
            "job_id",
            "run_id",
            "artifact_ref",
            "artifact_path",
            "report_ref",
            "epub_ref",
            "file_name",
            "url",
            "screenshot_path",
            "dispatch_id",
            "outbox_id",
            "episode_id",
            "memory_event_id",
        ):
            if refs.get(key):
                target[key] = refs.get(key)
    if isinstance(causality, Mapping):
        for key in ("status", "reason_code", "summary", "result", "action_kind"):
            if causality.get(key):
                target[key] = causality.get(key)
    return target


def _operational_goal(*, action_kind: str, family: str) -> str:
    if action_kind == "explain_recent_event_failure":
        return f"解释最近 {family} 事件的失败原因，基于事件证据说明下一步。"
    if action_kind == "retry_recent_operational_event":
        return f"重试或重新触发最近 {family} 事件，必要时说明会沿用哪个目标。"
    if action_kind == "cancel_recent_operational_event":
        return f"取消或停止最近 {family} 事件，避免误停无关任务。"
    if action_kind == "push_recent_artifact_or_result":
        return f"把最近 {family} 事件的产物或结果推送到用户当前通道。"
    if action_kind == "present_recent_artifact_or_observation":
        return f"展示或解释最近 {family} 事件的产物/观察结果。"
    if action_kind == "report_recent_operational_status":
        return f"汇报最近 {family} 事件当前状态和可验证结果。"
    if action_kind == "adjust_recent_operational_system":
        return f"围绕最近 {family} 系统表现继续修复或调整。"
    return f"沿着最近 {family} 事件继续，不切换到无关长期记忆。"


def _first_event(events: list[Mapping[str, Any]], *, role: str) -> Mapping[str, Any] | None:
    for event in events:
        if str(event.get("role") or "").lower() == role:
            return event
    return None


def _compact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "person_id": event.get("person_id"),
        "session_id": event.get("session_id"),
        "conversation_id": event.get("conversation_id"),
        "source_kind": event.get("source_kind"),
        "event_kind": event.get("event_kind"),
        "visibility": event.get("visibility"),
        "role": event.get("role"),
        "content_summary": event.get("content_summary") or _trim(event.get("content"), 180),
        "created_at_ms": event.get("created_at_ms"),
    }


def _event_refs(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = _loads(event.get("refs_json"), {})
    if isinstance(refs, Mapping):
        compact = {str(k): v for k, v in refs.items() if v not in (None, "", [], {})}
        return [compact] if compact else []
    if isinstance(refs, list):
        return [r for r in refs if isinstance(r, Mapping)]
    return []


def _event_domain(event: Mapping[str, Any]) -> str:
    family = _event_family(event)
    if family:
        return family
    refs = _loads(event.get("refs_json"), {})
    if isinstance(refs, Mapping):
        domain = refs.get("domain") or refs.get("skill_domain") or refs.get("task_domain")
        if domain:
            return str(domain)
    source = str(event.get("source_kind") or "")
    kind = str(event.get("event_kind") or "")
    if "recommend" in source or "recommend" in kind:
        return "lightnovel_recommendation"
    if "browser" in source or "browser" in kind:
        return "browser_automation"
    if "skill" in source or "skill" in kind:
        return "skill"
    if "work" in source or "task" in kind:
        return "work"
    return ""


def _compact_recommendation_item(item: Mapping[str, Any]) -> dict[str, Any]:
    rank = item.get("rank") or item.get("index") or item.get("display_index")
    try:
        rank = int(rank)
    except Exception:
        rank = None
    return {
        "rank": rank,
        "title": _repair_mojibake_text(item.get("title") or item.get("name") or item.get("label")),
        "series_key": _repair_mojibake_text(item.get("series_key") or item.get("item_key") or item.get("id")),
        "detail_url": item.get("detail_url") or item.get("url"),
        "cover_url": item.get("cover_url") or item.get("cover") or item.get("image_url"),
        "selection_reason": _repair_mojibake_text(item.get("selection_reason") or item.get("reason")),
        "intro_snippet": _repair_mojibake_text(item.get("intro_snippet") or item.get("intro") or item.get("summary")),
        "preference_matches": item.get("preference_matches"),
        "diversity_bucket": item.get("diversity_bucket"),
    }


def _delivery_source_event(delivery: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "delivery_id": delivery.get("delivery_id"),
        "person_id": delivery.get("person_id"),
        "session_id": delivery.get("session_id"),
        "conversation_id": delivery.get("conversation_id"),
        "domain": delivery.get("domain"),
        "platform": delivery.get("platform"),
        "created_at_ms": delivery.get("created_at_ms"),
    }


def _delivery_refs(delivery: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = {
        "delivery_id": delivery.get("delivery_id"),
        "request_id": delivery.get("request_id"),
        "job_id": delivery.get("job_id"),
        "recommendations_ref": delivery.get("recommendations_ref"),
        "ranked_recommendations_ref": delivery.get("ranked_recommendations_ref"),
    }
    return [{k: v for k, v in refs.items() if v}]


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
