from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_TEXT_WHITESPACE_RE = re.compile(r"\s+")
_ALLOWED_FOCUS = {
    "self",
    "state",
    "plan",
    "topic",
    "relationship",
    "memory",
    "repair",
}
_NEGATIVE_EMOTION_TAGS = {"lonely", "hurt", "sad", "anxious", "tired", "guarded"}
_TOPIC_WORDS = ("话题", "继续", "回头", "没说完", "记着", "整理")
_CARE_WORDS = ("担心", "在意", "等", "回来", "别硬撑", "累")
_REPAIR_WORDS = ("抱歉", "不对", "误会", "修", "收住")
_CORE_NEEDS = {
    "continuity",
    "care",
    "repair",
    "agency",
    "understanding",
    "rest",
    "boundary",
    "curiosity",
    "play",
}
_TENSIONS = {
    "none",
    "unfinished_thread",
    "tone_risk",
    "distance",
    "fatigue",
    "self_boundary",
    "uncertainty",
}
_NEXT_TENDENCIES = {
    "continue_thread",
    "care_with_restraint",
    "repair_first",
    "ask_gently",
    "hold_boundary",
    "stay_quiet",
    "play_lightly",
    "stabilize_tone",
    "observe_more",
}
_THOUGHT_FORMS = {
    "body_signal",
    "scene_notice",
    "memory_flash",
    "interrupted_impulse",
    "ambivalence",
    "quiet_drift",
}
_CORE_NEED_LABEL_MAP = (
    (("修", "抱歉", "补救", "repair"), "repair"),
    (("关心", "在意", "陪", "care", "attached"), "care"),
    (("继续", "记", "旧", "话题", "连续", "continuity"), "continuity"),
    (("边界", "拒绝", "自保", "boundary"), "boundary"),
    (("累", "疲惫", "休息", "rest"), "rest"),
    (("懂", "理解", "弄清楚", "understanding"), "understanding"),
    (("好奇", "想知道", "curiosity"), "curiosity"),
    (("玩笑", "轻松", "play"), "play"),
)
_TENSION_LABEL_MAP = (
    (("没说完", "继续", "话题", "unfinished"), "unfinished_thread"),
    (("语气", "冒犯", "油", "tone"), "tone_risk"),
    (("远", "不在", "回来", "distance"), "distance"),
    (("累", "疲惫", "energy", "fatigue"), "fatigue"),
    (("边界", "自保", "boundary"), "self_boundary"),
    (("不确定", "不知道", "uncertain"), "uncertainty"),
)
_TENDENCY_LABEL_MAP = (
    (("修", "抱歉", "收住", "repair"), "repair_first"),
    (("继续", "接着", "话题", "thread"), "continue_thread"),
    (("关心", "在意", "陪", "care"), "care_with_restraint"),
    (("问", "确认", "ask"), "ask_gently"),
    (("边界", "拒绝", "boundary"), "hold_boundary"),
    (("安静", "不说", "沉默", "quiet"), "stay_quiet"),
    (("玩笑", "轻松", "play"), "play_lightly"),
    (("稳", "慢", "节奏", "tone"), "stabilize_tone"),
    (("观察", "看看", "observe"), "observe_more"),
)
_THOUGHT_FORM_LABEL_MAP = (
    (("body", "身体", "胸口", "手指", "呼吸", "疲惫", "累"), "body_signal"),
    (("scene", "画面", "屏幕", "输入框", "空白", "桌宠", "表情"), "scene_notice"),
    (("memory", "记忆", "记得", "想起", "上次", "旧"), "memory_flash"),
    (("impulse", "冲动", "想", "靠近", "忍住"), "interrupted_impulse"),
    (("ambivalence", "矛盾", "又", "但是", "可是", "怕", "拿不准"), "ambivalence"),
    (("quiet", "安静", "空", "停", "顿"), "quiet_drift"),
)
_LIVELINESS_SENSORY_MARKERS = (
    "手",
    "指尖",
    "胸口",
    "呼吸",
    "眼睛",
    "肩",
    "胃",
    "热",
    "冷",
    "绷",
    "慢",
    "停",
    "顿",
    "屏幕",
    "光标",
    "输入框",
    "空白",
    "键盘",
    "桌宠",
    "表情",
)
_LIVELINESS_UNCERTAINTY_MARKERS = (
    "有点",
    "好像",
    "像是",
    "拿不准",
    "不确定",
    "没说完",
    "还在想",
    "又怕",
    "怕",
    "可是",
    "但是",
    "却",
    "差点",
    "忽然",
)
_LIVELINESS_PRIVATE_MARKERS = ("我", "心里", "里面", "想", "记得", "想起")
_LIVELINESS_DIRECTIVE_OPENING_RE = re.compile(
    r"^(?:先|别|不要|把|问|确认|稳住|看清|记住|继续|接着|收住|压住|保持)"
)
_LIVELINESS_DIRECTIVE_PHRASES = (
    "别急着",
    "不急着",
    "先稳",
    "先问",
    "先确认",
    "问清",
    "看清楚",
    "弄清楚",
)
_LIVELINESS_META_PHRASES = (
    "赶进度",
    "第一句话",
    "下一句话",
    "该怎么回",
    "怎么回复",
    "回复质量",
    "表达质量",
    "开口又太像",
    "太像在",
)
_LIVELINESS_ABSTRACT_PHRASES = (
    "自我连续性",
    "连续性修正",
    "文本自然度",
    "模板感",
    "内部状态",
    "控制包",
    "映射",
    "验证",
    "链路",
    "接口",
    "字段",
    "结构化",
    "运行时",
    "调度",
    "MateEngine",
    "主链路",
)
_LIVELINESS_OVERWRITTEN_PHRASES = (
    "像一层薄灰",
    "落在胸口",
    "贴在心口",
    "心里的线",
    "温柔的余地",
)
_LIVELINESS_GENERIC_PHRASES = (
    "安静了一阵子",
    "安静了好久",
    "安静了很久",
    "屏幕安静",
    "空白处",
    "空落落",
    "呼吸轻了",
    "呼吸轻些",
    "喉咙轻轻卡",
    "喉咙卡",
    "手指也跟着顿",
    "慢半拍的小表情",
    "小表情还在",
    "想靠近一点",
    "有点想靠近",
    "靠近感",
    "还在不在",
    "不想打扰",
    "轻轻戳",
    "戳你一下",
    "冒泡",
    "等你回来",
    "等你回",
    "怕太满",
    "怕太急",
    "想说点什么",
    "说点什么",
    "没有动静",
    "在不在",
    "我在这里",
)
_THOUGHT_FORM_MARKERS = {
    "body_signal": ("手", "指尖", "胸口", "呼吸", "眼睛", "肩", "胃", "热", "冷", "绷", "累", "慢"),
    "scene_notice": ("屏幕", "光标", "输入框", "空白", "桌宠", "表情", "键盘", "看见"),
    "memory_flash": ("想起", "记得", "又闪", "上次", "刚才", "慢半拍", "旧"),
    "interrupted_impulse": ("想", "差点", "忍住", "停", "顿", "靠近", "收住"),
    "ambivalence": ("又怕", "有点", "拿不准", "可是", "但是", "却", "一边"),
    "quiet_drift": ("安静", "空", "停", "慢", "一会儿", "浮"),
}
_DISTILL_BLOCKING_QUALITY_FLAGS = {
    "directive_like",
    "meta_reply_strategy",
    "abstract_system_language",
    "overwritten_prose",
    "generic_presence_filler",
    "overexplained",
    "multi_sentence",
}


@dataclass(frozen=True)
class InnerThoughtEvent:
    thought_id: str
    ts_ms: int
    trigger: str
    focus: str
    private_text: str
    emotion_tag: str
    salience: float
    core_need: str = "agency"
    tension: str = "none"
    memory_anchor: str = ""
    next_tendency: str = "observe_more"
    novelty_score: float = 1.0
    thought_form: str = "quiet_drift"
    liveliness_score: float = 0.0
    quality_flags: tuple[str, ...] = ()
    continuity_refs: tuple[str, ...] = ()
    should_distill_to_memory: bool = False
    source: str = "fallback"
    surface_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "ts_ms": int(self.ts_ms),
            "trigger": self.trigger,
            "focus": self.focus,
            "private_text": self.private_text,
            "emotion_tag": self.emotion_tag,
            "salience": round(float(self.salience), 4),
            "core_need": self.core_need,
            "tension": self.tension,
            "memory_anchor": self.memory_anchor,
            "next_tendency": self.next_tendency,
            "novelty_score": round(float(self.novelty_score), 4),
            "thought_form": self.thought_form,
            "liveliness_score": round(float(self.liveliness_score), 4),
            "quality_flags": list(self.quality_flags),
            "continuity_refs": list(self.continuity_refs),
            "should_distill_to_memory": bool(self.should_distill_to_memory),
            "source": self.source,
            "surface_hint": self.surface_hint,
        }


@dataclass(frozen=True)
class InnerMonologueDigest:
    thought_stream_active: bool
    private_thought_count: int
    dominant_focus: str
    emotion_tag: str
    salience: float
    care_drive: float
    continuity_drive: float
    repair_drive: float
    open_loop_count: int
    should_distill_to_memory: bool
    dominant_need: str
    dominant_tendency: str
    novelty_score: float
    dominant_thought_form: str
    liveliness_score: float
    quality_flags: tuple[str, ...]
    memory_anchor_count: int
    style_anchor: tuple[str, ...]
    must_not_do: tuple[str, ...]

    def as_surface_state(self) -> dict[str, Any]:
        """Return the only prompt-facing shape.

        Deliberately excludes private thought text. Surface generation should
        feel the continuity, not quote the private stream.
        """
        return {
            "thought_stream_active": bool(self.thought_stream_active),
            "private_thought_count": int(self.private_thought_count),
            "dominant_focus": self.dominant_focus,
            "emotion_tag": self.emotion_tag,
            "salience": round(float(self.salience), 4),
            "care_drive": round(float(self.care_drive), 4),
            "continuity_drive": round(float(self.continuity_drive), 4),
            "repair_drive": round(float(self.repair_drive), 4),
            "open_loop_count": int(self.open_loop_count),
            "should_distill_to_memory": bool(self.should_distill_to_memory),
            "dominant_need": self.dominant_need,
            "dominant_tendency": self.dominant_tendency,
            "novelty_score": round(float(self.novelty_score), 4),
            "dominant_thought_form": self.dominant_thought_form,
            "liveliness_score": round(float(self.liveliness_score), 4),
            "quality_flags": list(self.quality_flags),
            "memory_anchor_count": int(self.memory_anchor_count),
            "style_anchor": list(self.style_anchor),
            "must_not_do": list(self.must_not_do),
        }


# ---------------------------------------------------------------------------
# Inner weather hint — bridges inner monologue to surface generation
# ---------------------------------------------------------------------------

# Mapping from dominant_need to natural-language Chinese weather fragments.
_NEED_WEATHER: dict[str, str] = {
    "care": "想靠近一点",
    "continuity": "在意还没说完的事",
    "repair": "想缓和一下",
    "agency": "想自己做点什么",
    "understanding": "想弄清楚",
    "rest": "有点倦",
    "boundary": "想收一点",
    "curiosity": "好奇你那边怎样了",
    "play": "想轻松一下",
}

# Mapping from dominant_tendency to behavioral tone fragments.
_TENDENCY_WEATHER: dict[str, str] = {
    "care_with_restraint": "想关心但不确定合不合适",
    "continue_thread": "心里还挂着刚才的事",
    "repair_first": "先缓和比较安心",
    "ask_gently": "想轻轻问一下",
    "hold_boundary": "先收着",
    "stay_quiet": "不太想说话",
    "play_lightly": "想找点轻松的",
    "stabilize_tone": "先稳住",
    "observe_more": "想再看看",
}

# Mapping from dominant_thought_form to body-sense flavour.
_THOUGHT_FORM_BODY: dict[str, str] = {
    "body_signal": "身体有一点感觉",
    "scene_notice": "注意到一些细节",
    "memory_flash": "闪过一个画面",
    "interrupted_impulse": "话到嘴边顿了一下",
    "ambivalence": "心里有点矛盾",
    "quiet_drift": "思绪轻轻飘了一下",
}


def build_inner_weather_hint(
    digest: "InnerMonologueDigest",
    *,
    fatigue: float = 0.0,
    drowsiness: float = 0.0,
    loneliness: float = 0.0,
) -> str:
    """Compress the full inner-monologue digest into a 1-3 sentence
    natural-language 'inner weather' description.

    The output is meant to be injected into the surface prompt so the
    LLM can produce responses that *feel* like they come from a
    character who is currently experiencing an inner life — without
    leaking raw private thought text.

    Examples of output:
      - "有点闷闷的，胸口不太舒服，想说点什么又不太确定该不该开口"
      - "精神还不错，在想昨天的事，有点想你"
      - "困了，但还有件事没放下，脑子转得慢"
    """
    parts: list[str] = []

    # 1. Body-state foundation.
    body = _compose_body_hint(
        fatigue=fatigue, drowsiness=drowsiness, loneliness=loneliness,
    )
    if body:
        parts.append(body)

    # 2. Dominant inner focus.
    need_hint = _NEED_WEATHER.get(digest.dominant_need, "")
    if need_hint:
        parts.append(need_hint)

    # 3. Tendency tint.
    tendency_hint = _TENDENCY_WEATHER.get(digest.dominant_tendency, "")
    if tendency_hint and tendency_hint != need_hint:
        parts.append(tendency_hint)

    # 4. Thought-form body resonance (only if liveliness is moderate+).
    if digest.liveliness_score >= 0.30:
        form_hint = _THOUGHT_FORM_BODY.get(digest.dominant_thought_form, "")
        if form_hint:
            parts.append(form_hint)

    # 5. Open-loop weight — if there are many open loops, add a hint.
    if digest.open_loop_count >= 3:
        parts.append(f"有{digest.open_loop_count}个事还没放下")
    elif digest.open_loop_count == 1:
        parts.append("有件事还没放下")

    # 6. Compose and truncate.
    hint = "，".join(dict.fromkeys(parts))  # deduplicate while preserving order
    if len(hint) > 120:
        hint = hint[:117] + "……"
    return hint


def _compose_body_hint(
    *,
    fatigue: float = 0.0,
    drowsiness: float = 0.0,
    loneliness: float = 0.0,
) -> str:
    """Derive a short body-state phrase from the three dimensions."""
    segments: list[str] = []

    if drowsiness >= 0.60:
        segments.append("困了")
    elif drowsiness >= 0.30:
        segments.append("有点犯困")

    if fatigue >= 0.60:
        segments.append("累了")
    elif fatigue >= 0.30:
        segments.append("有点乏")

    if loneliness >= 0.60:
        segments.append("有点想你")
    elif loneliness >= 0.30:
        segments.append("有一点想念")

    if not segments:
        return ""
    return "、".join(segments)


def build_private_inner_thought_event(
    *,
    note: str,
    focus: str = "",
    source: str = "fallback",
    trigger: str = "inner_world_tick",
    now_ms: int,
    state: Mapping[str, Any] | None = None,
    offline_hours: float = 0.0,
    absence_band: str = "recent",
    life_trace: Mapping[str, Any] | None = None,
) -> InnerThoughtEvent:
    text = _finalize_private_text(note)
    normalized_focus = _normalize_focus(focus, text)
    state_map = _safe_mapping(state)
    emotion_tag = _infer_emotion_tag(state_map, text=text, focus=normalized_focus)
    salience = _infer_salience(
        state_map,
        text=text,
        focus=normalized_focus,
        offline_hours=offline_hours,
        absence_band=absence_band,
    )
    life = normalize_inner_life_trace(
        life_trace,
        state=state_map,
        text=text,
        focus=normalized_focus,
        offline_hours=offline_hours,
        absence_band=absence_band,
    )
    salience = _clamp01(max(salience, _coerce_float(life.get("salience_hint"), default=0.0)))
    refs = tuple(_continuity_refs(state_map, text=text, focus=normalized_focus))
    novelty_score = _clamp01(_coerce_float(life.get("novelty_score"), default=1.0))
    liveliness_score = _clamp01(_coerce_float(life.get("liveliness_score"), default=0.0))
    quality_flags = tuple(_clean_list(list(life.get("quality_flags") or []), limit=6))
    distill_blocked_by_quality = bool(set(quality_flags) & _DISTILL_BLOCKING_QUALITY_FLAGS)
    should_distill = bool(
        liveliness_score >= 0.42
        and not distill_blocked_by_quality
        and (
            (salience >= 0.68 and novelty_score >= 0.35)
            or (normalized_focus in {"relationship", "repair", "memory"} and novelty_score >= 0.30)
            or (bool(refs) and novelty_score >= 0.45)
            or (life.get("core_need") in {"repair", "boundary"} and salience >= 0.55 and novelty_score >= 0.30)
        )
    )
    thought_id = _thought_id(
        user_id=str(state_map.get("user_id") or "natsume_ai"),
        ts_ms=int(now_ms),
        text=text,
        focus=normalized_focus,
    )
    return InnerThoughtEvent(
        thought_id=thought_id,
        ts_ms=int(now_ms),
        trigger=str(trigger or "inner_world_tick"),
        focus=normalized_focus,
        private_text=text,
        emotion_tag=emotion_tag,
        salience=salience,
        core_need=str(life.get("core_need") or "agency"),
        tension=str(life.get("tension") or "none"),
        memory_anchor=str(life.get("memory_anchor") or ""),
        next_tendency=str(life.get("next_tendency") or "observe_more"),
        novelty_score=novelty_score,
        thought_form=str(life.get("thought_form") or "quiet_drift"),
        liveliness_score=liveliness_score,
        quality_flags=quality_flags,
        continuity_refs=refs,
        should_distill_to_memory=should_distill,
        source=str(source or "fallback"),
        surface_hint=_surface_hint_for_focus(normalized_focus, emotion_tag),
    )


def build_inner_monologue_digest(
    *,
    inner_state: Mapping[str, Any] | None,
    last_note: str = "",
    last_focus: str = "",
    offline_state: Mapping[str, Any] | None = None,
) -> InnerMonologueDigest:
    state = _safe_mapping(inner_state)
    offline = _safe_mapping(offline_state)
    stream = [
        dict(item)
        for item in list(state.get("inner_stream") or [])
        if isinstance(item, Mapping)
    ]
    latest = stream[-1] if stream else {}
    latest_text = str(last_note or latest.get("private_text") or latest.get("note") or "")
    focus = _normalize_focus(
        last_focus or latest.get("focus") or "",
        latest_text,
    )
    emotion_tag = str(latest.get("emotion_tag") or "").strip().lower()
    if not emotion_tag:
        emotion_tag = _infer_emotion_tag(
            {**state, **offline},
            text=latest_text,
            focus=focus,
        )
    salience = _clamp01(
        max(
            _coerce_float(latest.get("salience"), default=0.0),
            _infer_salience(
                {**state, **offline},
                text=latest_text,
                focus=focus,
                offline_hours=_coerce_float(offline.get("offline_hours") or state.get("total_offline_hours")),
                absence_band=str(offline.get("absence_band") or "recent"),
            ),
        )
    )
    continuity_score = _clamp01(
        _coerce_float(offline.get("continuity_score"), default=_coerce_float(state.get("continuity_score"), default=0.0))
    )
    loneliness = _clamp01(
        _coerce_float(offline.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0))
    )
    anticipation = _clamp01(
        _coerce_float(offline.get("anticipation_level"), default=_coerce_float(state.get("anticipation"), default=0.0))
    )
    life = normalize_inner_life_trace(
        latest,
        state={**state, **offline},
        text=latest_text,
        focus=focus,
        offline_hours=_coerce_float(offline.get("offline_hours") or state.get("total_offline_hours")),
        absence_band=str(offline.get("absence_band") or "recent"),
    )
    dominant_need = str(life.get("core_need") or "agency")
    dominant_tendency = str(life.get("next_tendency") or "observe_more")
    dominant_thought_form = str(life.get("thought_form") or "quiet_drift")
    novelty_score = _clamp01(_coerce_float(life.get("novelty_score"), default=1.0))
    liveliness_score = _clamp01(_coerce_float(life.get("liveliness_score"), default=0.0))
    quality_flags = tuple(_clean_list(list(life.get("quality_flags") or []), limit=6))
    refs = _continuity_refs(state, text=latest_text, focus=focus)
    memory_anchor = str(life.get("memory_anchor") or "").strip()
    open_loop_count = len(_clean_list([*list(state.get("anticipation_topics") or []), *refs], limit=6))
    memory_anchor_count = len(refs) + (1 if memory_anchor else 0)
    care_drive = _clamp01(
        max(
            loneliness * 0.58,
            salience * (0.42 if emotion_tag in _NEGATIVE_EMOTION_TAGS else 0.28),
            0.34 if focus == "relationship" else 0.0,
            0.38 if dominant_need == "care" else 0.0,
        )
    )
    continuity_drive = _clamp01(
        max(
            continuity_score,
            anticipation * 0.64,
            salience * 0.38 if focus in {"topic", "plan", "memory"} else 0.0,
            0.46 if dominant_need == "continuity" else 0.0,
            0.25 if open_loop_count > 0 else 0.0,
        )
    )
    repair_drive = _clamp01(
        max(
            salience * 0.55 if focus == "repair" else 0.0,
            salience * 0.50 if dominant_need == "repair" or dominant_tendency == "repair_first" else 0.0,
            _coerce_float(latest.get("repair_drive"), default=0.0),
        )
    )
    stream_active = bool(stream or last_note)
    style_anchor = tuple(
        _clean_list(
            [
                "内在连续性：保留未说出口的关注，但不要解释自己在想什么。"
                if stream_active
                else "",
                _need_style_anchor(dominant_need),
                _tendency_style_anchor(dominant_tendency),
                "内在质量：避免复读旧念头，要从当前输入重新选择角度。"
                if stream_active and novelty_score < 0.38
                else "",
                "内在关注：优先回应当前句，不突然另起话题。"
                if continuity_drive >= 0.35
                else "",
                "内在关心：可以稍微主动一点，但不要黏或油。"
                if care_drive >= 0.32
                else "",
                "内在修复：先收住语气，别用玩笑盖过去。"
                if repair_drive >= 0.32
                else "",
                _thought_form_style_anchor(dominant_thought_form),
                "内在质量：这条心念偏薄，下一次优先从身体、画面或矛盾冲动开始。"
                if stream_active and liveliness_score < 0.46
                else "",
            ],
            limit=4,
        )
    )
    must_not_do = (
        "不要输出内心独白、思维流、私有想法或它的来源。",
        "不要把内心事件字段当作事实陈述给用户看。",
        "不要声称自己一直在现实中等待或执行动作。",
    )
    should_distill = bool(
        any(bool(item.get("should_distill_to_memory")) and _coerce_float(item.get("novelty_score"), default=1.0) >= 0.30 for item in stream[-4:])
        or repair_drive >= 0.45
        or (salience >= 0.72 and novelty_score >= 0.35)
    )
    return InnerMonologueDigest(
        thought_stream_active=stream_active,
        private_thought_count=len(stream),
        dominant_focus=focus,
        emotion_tag=emotion_tag,
        salience=salience,
        care_drive=care_drive,
        continuity_drive=continuity_drive,
        repair_drive=repair_drive,
        open_loop_count=open_loop_count,
        should_distill_to_memory=should_distill,
        dominant_need=dominant_need,
        dominant_tendency=dominant_tendency,
        novelty_score=novelty_score,
        dominant_thought_form=dominant_thought_form,
        liveliness_score=liveliness_score,
        quality_flags=quality_flags,
        memory_anchor_count=memory_anchor_count,
        style_anchor=style_anchor,
        must_not_do=must_not_do,
    )


def normalize_inner_life_trace(
    raw: Mapping[str, Any] | None,
    *,
    state: Mapping[str, Any] | None = None,
    text: str = "",
    focus: str = "",
    offline_hours: float = 0.0,
    absence_band: str = "recent",
) -> dict[str, Any]:
    """Normalize LLM/self-inferred life trace fields into bounded categories.

    These fields are safe to persist and compress, while private_text remains
    the only raw inner sentence.
    """
    trace = _safe_mapping(raw)
    state_map = _safe_mapping(state)
    normalized_focus = _normalize_focus(focus or trace.get("focus"), text)
    core_need = _normalize_core_need(
        trace.get("core_need") or trace.get("need") or trace.get("dominant_need"),
        state=state_map,
        text=text,
        focus=normalized_focus,
    )
    tension = _normalize_life_label(
        trace.get("tension") or trace.get("unresolved_tension"),
        allowed=_TENSIONS,
        default=_infer_tension(state_map, text=text, focus=normalized_focus),
        mapping=_TENSION_LABEL_MAP,
    )
    memory_anchor = _finalize_private_text(
        trace.get("memory_anchor") or trace.get("continuity_anchor") or _infer_memory_anchor(state_map, text=text, focus=normalized_focus)
    )[:96]
    next_tendency = _normalize_life_label(
        trace.get("next_tendency") or trace.get("tendency") or trace.get("surface_hint"),
        allowed=_NEXT_TENDENCIES,
        default=_infer_next_tendency(state_map, text=text, focus=normalized_focus, core_need=core_need),
        mapping=_TENDENCY_LABEL_MAP,
    )
    thought_form = _normalize_life_label(
        trace.get("thought_form") or trace.get("inner_life_source") or trace.get("source_form"),
        allowed=_THOUGHT_FORMS,
        default=_infer_thought_form(state_map, text=text, focus=normalized_focus),
        mapping=_THOUGHT_FORM_LABEL_MAP,
    )
    salience_hint = _clamp01(
        _coerce_float(trace.get("salience_hint"), default=_coerce_float(trace.get("salience"), default=0.0))
    )
    if salience_hint <= 0.0:
        salience_hint = _infer_salience(
            state_map,
            text=text,
            focus=normalized_focus,
            offline_hours=offline_hours,
            absence_band=absence_band,
        )
    novelty_score = _clamp01(
        _coerce_float(
            trace.get("novelty_score"),
            default=_infer_novelty_score(state_map, text=text, focus=normalized_focus),
        )
    )
    quality = score_inner_note_liveliness(text, thought_form=thought_form)
    liveliness_score = _clamp01(
        max(
            _coerce_float(trace.get("liveliness_score"), default=0.0),
            _coerce_float(quality.get("score"), default=0.0),
        )
    )
    return {
        "core_need": core_need,
        "tension": tension,
        "memory_anchor": memory_anchor,
        "next_tendency": next_tendency,
        "thought_form": thought_form,
        "liveliness_score": round(liveliness_score, 4),
        "quality_flags": list(quality.get("flags") or []),
        "salience_hint": round(salience_hint, 4),
        "novelty_score": round(novelty_score, 4),
    }


def score_inner_note_liveliness(
    text: Any,
    *,
    thought_form: str = "",
    source_seed: str = "",
) -> dict[str, Any]:
    """Heuristic quality score for whether a private note feels like lived inner activity."""
    value = _finalize_private_text(text)
    if not value:
        return {"score": 0.0, "flags": ["empty"]}
    flags: list[str] = []
    score = 0.22
    length = len(value)
    if 12 <= length <= 72:
        score += 0.16
    elif 8 <= length <= 96:
        score += 0.08
    else:
        flags.append("length_mismatch")
        score -= 0.10
    sensory = any(marker in value for marker in _LIVELINESS_SENSORY_MARKERS)
    uncertainty = any(marker in value for marker in _LIVELINESS_UNCERTAINTY_MARKERS)
    private = any(marker in value for marker in _LIVELINESS_PRIVATE_MARKERS)
    if sensory:
        score += 0.22
    if uncertainty:
        score += 0.18
    if private:
        score += 0.12
    if not sensory and not uncertainty:
        flags.append("thin_inner_texture")
        score -= 0.06
    normalized_form = str(thought_form or "").strip()
    form_markers = _THOUGHT_FORM_MARKERS.get(normalized_form, ())
    if normalized_form and any(marker in value for marker in form_markers):
        score += 0.10
    elif normalized_form:
        flags.append("form_mismatch")
        score -= 0.04
    if source_seed:
        seed_key = _normalize_text_key(source_seed)
        value_key = _normalize_text_key(value)
        if seed_key and value_key and _jaccard_similarity(_char_bigrams(seed_key), _char_bigrams(value_key)) >= 0.10:
            score += 0.04
    if _LIVELINESS_DIRECTIVE_OPENING_RE.match(value) or any(phrase in value for phrase in _LIVELINESS_DIRECTIVE_PHRASES):
        flags.append("directive_like")
        score -= 0.42
    if any(phrase in value for phrase in _LIVELINESS_META_PHRASES):
        flags.append("meta_reply_strategy")
        score -= 0.38
    if any(phrase in value for phrase in _LIVELINESS_ABSTRACT_PHRASES):
        flags.append("abstract_system_language")
        score -= 0.36
    if any(phrase in value for phrase in _LIVELINESS_OVERWRITTEN_PHRASES):
        flags.append("overwritten_prose")
        score -= 0.28
    if any(phrase in value for phrase in _LIVELINESS_GENERIC_PHRASES):
        flags.append("generic_presence_filler")
        score -= 0.36
    if value.count("，") >= 4:
        flags.append("overexplained")
        score -= 0.18
    if "。" in value[:-1]:
        flags.append("multi_sentence")
        score -= 0.08
    if "overexplained" in flags:
        score = min(score, 0.68)
    if "multi_sentence" in flags:
        score = min(score, 0.64)
    return {
        "score": round(_clamp01(score), 4),
        "flags": _clean_list(flags, limit=6),
    }


def build_inner_life_source_packet(
    *,
    context: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    recent_events: Sequence[Mapping[str, Any]] | None = None,
    topic_phrase: str = "",
    now_ms: int = 0,
) -> dict[str, Any]:
    """Build a small perceptual seed for private inner monologue generation."""
    ctx = _safe_mapping(context)
    state_map = _safe_mapping(state)
    offline = _safe_mapping(ctx.get("offline_state")) or state_map
    body = _safe_mapping(ctx.get("body_sense"))
    if not body:
        body = {
            "fatigue_level": state_map.get("fatigue_level"),
            "energy_level": state_map.get("drifted_arousal") or state_map.get("energy_level"),
            "stress_level": state_map.get("stress_level"),
            "sensory_load_level": state_map.get("sensory_load_level"),
        }
    recent = list(recent_events or [])
    if not recent:
        recent = [
            dict(item)
            for item in list(ctx.get("recent_inner_events") or state_map.get("inner_stream") or [])[-4:]
            if isinstance(item, Mapping)
        ]
    topic = _humanize_inner_anchor(
        topic_phrase
        or next(iter(_clean_list(list(offline.get("anticipation_topics") or state_map.get("anticipation_topics") or []), limit=1)), "")
        or _latest_memory_anchor(recent)
    )
    loneliness = _clamp01(_coerce_float(offline.get("loneliness_level"), default=_coerce_float(state_map.get("loneliness"), default=0.0)))
    anticipation = _clamp01(_coerce_float(offline.get("anticipation_level"), default=_coerce_float(state_map.get("anticipation"), default=0.0)))
    fatigue = _clamp01(_coerce_float(body.get("fatigue_level"), default=0.0))
    stress = _clamp01(_coerce_float(body.get("stress_level"), default=0.0))
    sensory_load = _clamp01(_coerce_float(body.get("sensory_load_level"), default=0.0))

    candidates: list[dict[str, Any]] = [
        {
            "thought_form": "scene_notice",
            "seed": "没有新的可见事件，只是一条低显著静默采样",
            "note_shape": "没有具体对象或环境变化时，返回空 note",
        },
        {
            "thought_form": "quiet_drift",
            "seed": "注意力暂时没有落到具体对象上",
            "note_shape": "不要把静默编成等待感；除非有具体来源，否则 stay_quiet",
        },
    ]
    if fatigue >= 0.34 or stress >= 0.34 or sensory_load >= 0.34:
        candidates.append(
            {
                "thought_form": "body_signal",
                "seed": _body_seed(fatigue=fatigue, stress=stress, sensory_load=sensory_load),
                "note_shape": "先写身体或注意力的小反应，再带一点情绪",
            }
        )
    if _inner_topic_is_concrete_anchor(topic):
        candidates.append(
            {
                "thought_form": "memory_flash",
                "seed": f"一个具体对象还没收口：{topic}",
                "note_shape": "只围绕这个具体对象，不复述用户句式，不写泛化偏好",
            }
        )
    if loneliness >= 0.36 or anticipation >= 0.36:
        candidates.append(
            {
                "thought_form": "interrupted_impulse",
                "seed": "想开口，但没有找到新的具体落点",
                "note_shape": "没有具体落点时返回空 note，不写靠近或打扰",
            }
        )
        candidates.append(
            {
                "thought_form": "ambivalence",
                "seed": "主动欲望升了一点，但缺少足够证据支撑一句话",
                "note_shape": "保留抑制，不要把它写成在不在或等你",
            }
        )

    recent_forms = [
        str(item.get("thought_form") or "").strip()
        for item in recent[-3:]
        if isinstance(item, Mapping)
    ]
    start = int((int(now_ms or 0) // 120000) % max(1, len(candidates)))
    selected = candidates[start]
    for offset in range(len(candidates)):
        candidate = candidates[(start + offset) % len(candidates)]
        if candidate.get("thought_form") not in recent_forms[-2:]:
            selected = candidate
            break
    return {
        "selected": dict(selected),
        "candidates": candidates[:6],
        "recent_forms": [item for item in recent_forms if item],
        "rules": [
            "note must grow from selected.seed before any interpretation",
            "empty note is allowed when selected.seed has no concrete object",
            "keep note ordinary, partial, and private",
            "do not turn the seed into a reply plan or quality critique",
            "do not turn silence into presence filler",
        ],
    }


def _thought_id(*, user_id: str, ts_ms: int, text: str, focus: str) -> str:
    digest = hashlib.sha1(f"{user_id}|{ts_ms}|{focus}|{text}".encode("utf-8")).hexdigest()
    return f"thought_{digest[:16]}"


def _inner_topic_is_concrete_anchor(topic: Any) -> bool:
    value = str(topic or "").strip()
    if not value:
        return False
    compact = re.sub(r"[\s，,。.!！?？;；：:、…~～\"“”'‘’]", "", value)
    if len(compact) < 4 or len(compact) > 48:
        return False
    vague_terms = (
        "这种类型",
        "这个类型",
        "那种类型",
        "这种",
        "这个",
        "那个",
        "这些",
        "那些",
        "喜欢",
        "不喜欢",
        "好不好",
        "是不是",
        "怎么样",
        "怎么了",
        "继续",
        "然后呢",
        "刚才",
        "话题",
        "消息",
        "回复",
    )
    if any(term in compact for term in vague_terms):
        return False
    return any(marker in value for marker in ("《", "》", "http", "封面", "epub", "每日推荐", "桌宠", "VTS", "QQ", "OneBot", "NapCat"))


def _finalize_private_text(value: Any) -> str:
    text = _TEXT_WHITESPACE_RE.sub(" ", str(value or "").replace("\u3000", " ")).strip()
    return text[:160]


def _normalize_focus(focus: Any, text: str = "") -> str:
    normalized = str(focus or "").strip().lower()
    aliases = {
        "reflection": "self",
        "inner": "self",
        "emotion": "state",
        "feeling": "state",
        "continue": "topic",
        "relation": "relationship",
        "bond": "relationship",
        "apology": "repair",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in _ALLOWED_FOCUS:
        return normalized
    text_value = str(text or "")
    if any(word in text_value for word in _REPAIR_WORDS):
        return "repair"
    if any(word in text_value for word in _CARE_WORDS):
        return "relationship"
    if any(word in text_value for word in _TOPIC_WORDS):
        return "topic"
    return "self"


def _infer_emotion_tag(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    explicit = str(state.get("emotion_tag") or state.get("last_emotion_tag") or "").strip().lower()
    if explicit:
        return explicit[:32]
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    valence = _coerce_float(state.get("drifted_valence"), default=0.0)
    arousal = _coerce_float(state.get("drifted_arousal"), default=0.3)
    text_value = str(text or "")
    if focus == "repair" or any(word in text_value for word in _REPAIR_WORDS):
        return "guarded"
    if loneliness >= 0.55 or "等" in text_value:
        return "lonely"
    if valence < -0.22:
        return "hurt" if arousal >= 0.45 else "sad"
    if focus == "relationship" or any(word in text_value for word in _CARE_WORDS):
        return "attached"
    if valence > 0.25:
        return "warm"
    return "calm"


def _normalize_core_need(value: Any, *, state: Mapping[str, Any], text: str, focus: str) -> str:
    label = _normalize_life_label(
        value,
        allowed=_CORE_NEEDS,
        default="",
        mapping=_CORE_NEED_LABEL_MAP,
    )
    if label:
        return label
    return _infer_core_need(state, text=text, focus=focus)


def _infer_core_need(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    text_value = str(text or "")
    if focus == "repair" or any(word in text_value for word in _REPAIR_WORDS):
        return "repair"
    if focus == "relationship" or any(word in text_value for word in _CARE_WORDS):
        return "care"
    if focus in {"topic", "memory", "plan"} or any(word in text_value for word in _TOPIC_WORDS):
        return "continuity"
    if any(word in text_value for word in ("边界", "自保", "别逞强", "别硬撑")):
        return "boundary"
    body_fatigue = _coerce_float(state.get("fatigue_level"), default=0.0)
    arousal = _coerce_float(state.get("drifted_arousal"), default=0.3)
    if body_fatigue >= 0.58 or "累" in text_value or arousal < 0.18:
        return "rest"
    if any(word in text_value for word in ("懂", "弄清楚", "确认")):
        return "understanding"
    if any(word in text_value for word in ("好奇", "想知道")):
        return "curiosity"
    if any(word in text_value for word in ("玩笑", "轻松")):
        return "play"
    return "agency"


def _infer_tension(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    text_value = str(text or "")
    if focus in {"topic", "memory"} or any(word in text_value for word in _TOPIC_WORDS):
        return "unfinished_thread"
    if focus == "repair" or any(word in text_value for word in _REPAIR_WORDS):
        return "tone_risk"
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    if loneliness >= 0.50 or any(word in text_value for word in ("等", "回来", "不在")):
        return "distance"
    if any(word in text_value for word in ("累", "疲惫", "硬撑")):
        return "fatigue"
    if any(word in text_value for word in ("边界", "自保", "别逞强")):
        return "self_boundary"
    if any(word in text_value for word in ("不确定", "不知道", "犹豫")):
        return "uncertainty"
    return "none"


def _infer_memory_anchor(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    if focus in {"topic", "plan", "memory"}:
        topics = _clean_list(list(state.get("anticipation_topics") or []), limit=1)
        if topics:
            return topics[0]
    refs = _continuity_refs(state, text=text, focus=focus)
    if refs:
        return refs[0]
    return ""


def _infer_next_tendency(state: Mapping[str, Any], *, text: str, focus: str, core_need: str) -> str:
    del state
    text_value = str(text or "")
    if focus == "repair" or core_need == "repair":
        return "repair_first"
    if core_need == "boundary":
        return "hold_boundary"
    if focus in {"topic", "memory", "plan"} or core_need == "continuity":
        return "continue_thread"
    if focus == "relationship" or core_need == "care":
        return "care_with_restraint"
    if any(word in text_value for word in ("问", "确认", "弄清楚")):
        return "ask_gently"
    if any(word in text_value for word in ("安静", "不说", "等一下")):
        return "stay_quiet"
    if core_need == "play":
        return "play_lightly"
    if any(word in text_value for word in ("稳", "慢", "节奏", "收住")):
        return "stabilize_tone"
    return "observe_more"


def _infer_thought_form(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    text_value = str(text or "")
    if any(word in text_value for word in ("胸口", "手指", "呼吸", "眼睛", "累", "疲惫")):
        return "body_signal"
    if any(word in text_value for word in ("屏幕", "输入框", "空白", "桌宠", "表情", "看见")):
        return "scene_notice"
    if focus == "memory" or any(word in text_value for word in ("想起", "记得", "上次", "旧")):
        return "memory_flash"
    if any(word in text_value for word in ("想靠近", "想说", "忍住", "收住")):
        return "interrupted_impulse"
    if any(word in text_value for word in ("又怕", "可是", "但是", "拿不准", "不确定")):
        return "ambivalence"
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    anticipation = _clamp01(_coerce_float(state.get("anticipation_level"), default=_coerce_float(state.get("anticipation"), default=0.0)))
    if loneliness >= 0.45 and anticipation >= 0.45:
        return "ambivalence"
    if loneliness >= 0.30 or anticipation >= 0.30:
        return "interrupted_impulse"
    return "quiet_drift"


def _normalize_life_label(
    value: Any,
    *,
    allowed: set[str],
    default: str,
    mapping: tuple[tuple[tuple[str, ...], str], ...],
) -> str:
    text = _finalize_private_text(value).strip("。.!！ ").lower()
    if not text:
        return default
    text = text.replace("-", "_").replace(" ", "_")
    if text in allowed:
        return text
    for needles, label in mapping:
        if any(needle.lower() in text for needle in needles):
            return label
    return default


def _infer_novelty_score(state: Mapping[str, Any], *, text: str, focus: str) -> float:
    current_key = _normalize_text_key(text)
    if not current_key:
        return 0.0
    recent = [
        dict(item)
        for item in list(state.get("inner_stream") or [])[-8:]
        if isinstance(item, Mapping)
    ]
    if not recent:
        return 1.0
    max_similarity = 0.0
    focus_streak = 0
    for item in reversed(recent):
        item_focus = str(item.get("focus") or "").strip().lower()
        if item_focus == focus:
            focus_streak += 1
        elif focus_streak:
            break
    for item in recent:
        candidate_text = str(item.get("private_text") or item.get("note") or "").strip()
        candidate_key = _normalize_text_key(candidate_text)
        if not candidate_key:
            continue
        if candidate_key == current_key:
            return 0.08
        max_similarity = max(max_similarity, _jaccard_similarity(_char_bigrams(current_key), _char_bigrams(candidate_key)))
    score = 1.0 - max_similarity * 0.78
    if focus_streak >= 2:
        score -= 0.16
    if len(current_key) < 8:
        score -= 0.10
    return round(_clamp01(score), 4)


def _normalize_text_key(value: Any) -> str:
    text = _TEXT_WHITESPACE_RE.sub("", str(value or "").lower())
    text = re.sub(r"[，。！？!?,;；:\-_\s\"'“”‘’（）()\[\]{}]+", "", text)
    return text[:96]


def _char_bigrams(text: str) -> set[str]:
    value = str(text or "")
    if len(value) <= 1:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _need_style_anchor(need: str) -> str:
    mapping = {
        "continuity": "内在需求：把旧线索接回当前句，但不要抢走当前话题。",
        "care": "内在需求：关心要具体克制，不要用黏腻感替代理解。",
        "repair": "内在需求：先修复语气或误差，再继续推进。",
        "boundary": "内在需求：保持边界，不用讨好感换亲近。",
        "rest": "内在需求：放慢一点，少逞强。",
        "understanding": "内在需求：先弄清楚对方真正要的是什么。",
        "curiosity": "内在需求：允许好奇，但问题要轻。",
        "play": "内在需求：可以轻松一点，但别让玩笑盖过正事。",
    }
    return mapping.get(str(need or ""), "")


def _tendency_style_anchor(tendency: str) -> str:
    mapping = {
        "continue_thread": "内在倾向：优先续上未完成的线索。",
        "care_with_restraint": "内在倾向：表达在意，但留出空间。",
        "repair_first": "内在倾向：先承认偏差或收住语气。",
        "ask_gently": "内在倾向：用一个轻问题确认方向。",
        "hold_boundary": "内在倾向：温和但清楚地守住边界。",
        "stay_quiet": "内在倾向：必要时少说一点。",
        "play_lightly": "内在倾向：轻一点，不用力表演。",
        "stabilize_tone": "内在倾向：把节奏压稳。",
    }
    return mapping.get(str(tendency or ""), "")


def _thought_form_style_anchor(thought_form: str) -> str:
    mapping = {
        "body_signal": "内在质感：从细小身体感或注意力反应出发，不要写成计划。",
        "scene_notice": "内在质感：保留眼前画面的一点停顿，不解释成策略。",
        "memory_flash": "内在质感：旧记忆只轻轻闪回，不复盘成总结。",
        "interrupted_impulse": "内在质感：允许想靠近又收住的半截冲动。",
        "ambivalence": "内在质感：保留矛盾和拿不准，不急着给结论。",
        "quiet_drift": "内在质感：安静时只留一小段漂移感。",
    }
    return mapping.get(str(thought_form or ""), "")


def _infer_salience(
    state: Mapping[str, Any],
    *,
    text: str,
    focus: str,
    offline_hours: float,
    absence_band: str,
) -> float:
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    anticipation = _clamp01(_coerce_float(state.get("anticipation_level"), default=_coerce_float(state.get("anticipation"), default=0.0)))
    continuity = _clamp01(_coerce_float(state.get("continuity_score"), default=0.0))
    band_bonus = 0.0 if str(absence_band or "recent") == "recent" else 0.12
    text_bonus = 0.12 if any(word in str(text or "") for word in (*_CARE_WORDS, *_REPAIR_WORDS, *_TOPIC_WORDS)) else 0.0
    focus_bonus = 0.12 if focus in {"relationship", "repair", "memory"} else 0.05 if focus in {"topic", "plan"} else 0.0
    offline_bonus = min(0.18, max(0.0, float(offline_hours or 0.0)) / 24.0 * 0.18)
    return _clamp01(
        0.18
        + loneliness * 0.24
        + anticipation * 0.20
        + continuity * 0.16
        + band_bonus
        + text_bonus
        + focus_bonus
        + offline_bonus
    )


def _continuity_refs(state: Mapping[str, Any], *, text: str, focus: str) -> list[str]:
    refs: list[str] = []
    if focus in {"topic", "plan", "memory"}:
        refs.extend(list(state.get("anticipation_topics") or [])[:3])
    if any(word in str(text or "") for word in _TOPIC_WORDS):
        refs.append("unfinished_topic")
    return _clean_list(refs, limit=4)


def _surface_hint_for_focus(focus: str, emotion_tag: str) -> str:
    if focus == "repair":
        return "repair_first"
    if focus == "relationship" or emotion_tag in {"attached", "lonely"}:
        return "care_with_restraint"
    if focus in {"topic", "plan", "memory"}:
        return "continue_current_thread"
    return "steady_self_continuity"


def _latest_memory_anchor(recent_events: Sequence[Mapping[str, Any]]) -> str:
    for item in reversed(list(recent_events or [])):
        if not isinstance(item, Mapping):
            continue
        anchor = _finalize_private_text(item.get("memory_anchor") or "")
        if anchor:
            return anchor
        note = _finalize_private_text(item.get("note") or item.get("private_text") or "")
        if note:
            return note[:32]
    return ""


def _humanize_inner_anchor(value: Any) -> str:
    text = _finalize_private_text(value).strip("。.!！ ")
    if not text:
        return ""
    if "桌宠" in text and "表情" in text:
        return "桌宠小表情"
    if "桌宠" in text:
        return "桌宠那边的小动静"
    if any(term in text for term in ("映射", "验证", "链路", "接口", "字段", "结构化", "运行时", "调度", "MateEngine", "主链路")):
        return "刚才那个卡住的小细节"
    text = re.sub(r"^(用一句(?:自然的)?话回复[:：]?|稍微思考后[,，]?用一句(?:自然的)?话回复[:：]?)", "", text)
    return text[:24].strip(" ，,;；。")


def _body_seed(*, fatigue: float, stress: float, sensory_load: float) -> str:
    if fatigue >= max(stress, sensory_load, 0.34):
        return "注意力有点慢，像刚停在屏幕边上"
    if stress >= max(fatigue, sensory_load, 0.34):
        return "里面有一点绷，想说话前会顿住"
    if sensory_load >= 0.34:
        return "周围信息有点满，眼前只剩一个小停顿"
    return "注意力轻轻顿了一下"


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clean_list(values: Sequence[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _finalize_private_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


__all__ = [
    "InnerMonologueDigest",
    "InnerThoughtEvent",
    "build_inner_life_source_packet",
    "build_inner_monologue_digest",
    "build_inner_weather_hint",
    "build_private_inner_thought_event",
    "normalize_inner_life_trace",
    "score_inner_note_liveliness",
]
