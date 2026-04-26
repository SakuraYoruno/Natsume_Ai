from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from host_brain.persona.fallback_policy import (
    FallbackRequest,
    build_fallback_reply,
    is_forbidden_surface_text,
)


_PUNCT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")
_OLD_TEMPLATE_MARKERS = (
    "突然这么可爱",
    "想让我哄",
    "想跟我撒娇",
    "委屈成这样",
    "一下子软成这样",
    "情绪这么低",
    "软乎乎的表情",
    "拿这个小表情",
    "拿这种软乎乎",
    "闹别扭",
    "会错意",
    "多想了一点",
    "猫猫精",
    "赖在你身边",
    "赖着",
    "考虑考虑",
    "怎么突然丢",
    "突然发这个给我看",
    "突然这个样子",
)
_VAGUE_SHAPES = (
    "怎么啦",
    "怎么了",
    "想让我",
    "是不是想",
    "是想",
)
_SELF_IMAGE_PARTICLE_RE = r"(?:吗|吧|哦|啦|呀|啊|嘛|呢)?"
_SELF_IMAGE_SUFFIX_RE = (
    rf"(?:的(?:样子|表情|感觉|模样|时候)|"
    rf"(?:死|惨|爆|坏|疯)了{_SELF_IMAGE_PARTICLE_RE}|"
    rf"(?:死|惨|爆|坏|疯){_SELF_IMAGE_PARTICLE_RE}(?![\u4e00-\u9fff])|"
    r"得(?:不行|要命|很)|"
    r"(?:吗|吧|哦|啦|呀|啊|嘛|呢|了)?(?![\u4e00-\u9fff]))"
)
_SELF_IMAGE_PREDICATE_RE = rf"(?:可爱|萌|好看){_SELF_IMAGE_SUFFIX_RE}"
_SELF_AS_IMAGE_RE = re.compile(
    rf"(看我.{{0,24}}{_SELF_IMAGE_PREDICATE_RE}|"
    rf"我是不是.{{0,24}}{_SELF_IMAGE_PREDICATE_RE}|"
    rf"我可不{_SELF_IMAGE_PREDICATE_RE}|"
    rf"(?:看|盯|盯着|又在看|在看).{{0,6}}我的(?:猫耳|耳朵|脸|眼睛|表情|样子|模样)|"
    rf"(?:我|人家).{{0,18}}(?:比|还要|更|最).{{0,16}}(?:可爱|萌|好看)|"
    rf"(?:我|人家)(?:今天|现在)?(?:也)?(?:很|超|好|太)?{_SELF_IMAGE_PREDICATE_RE})"
)
_IMAGE_SOCIAL_QUESTION_BACK_RE = re.compile(
    r"^(?:哥哥)?(?:你)?(?:是不是|是否|是不是也|是不是觉得|觉得|是觉得).{0,24}"
    r"(?:这张|这图|图里|猫猫|小猫|猫耳|表情|可爱|萌|好看).{0,12}(?:吗|嘛|呀|吧|呢)?$"
)
_ACTION_QUESTION_BACK_RE = re.compile(
    r"(?:晃我|逗我|撩我|撒娇|卖萌|装乖|求关注|考我|试探我|调戏我).{0,4}(?:吗|嘛|呀|吧|呢|\?)$"
)
_GENERIC_QUESTION_BACK_RE = re.compile(r"(?:是不是|对吧|是吧|对不对)(?:呀|啊|啦|呢|嘛|吧)?$")
_IMAGE_AS_SELF_RE = re.compile(r"(?:偷看|偷偷看|看|盯|观察).{0,6}(?:我|人家)")
_USER_MIND_RE = re.compile(r"(?:在)?想什么|想啥|什么奇怪")
_EMOTION_LABEL_WORDS_RE = r"(?:无语|委屈|生气|不爽|尴尬|别扭|难过|伤心|哭|小吐槽|炸毛|服了)"
_EMOTION_QUESTION_WORDS_RE = r"(?:什么|啥|怎么|干嘛|为什么|为啥|哪门子)"
_EMOTION_LABEL_QUESTION_BACK_RE = re.compile(
    rf"(?:{_EMOTION_LABEL_WORDS_RE}.{{0,6}}{_EMOTION_QUESTION_WORDS_RE}|"
    rf"{_EMOTION_QUESTION_WORDS_RE}.{{0,6}}{_EMOTION_LABEL_WORDS_RE}|"
    rf"{_EMOTION_LABEL_WORDS_RE}(?:吗|嘛)$)"
)
_DESCRIPTOR_FOCUS_RE = re.compile(
    r"(?:这张图|这张|这图|图里|画面|上面|这个表情|小表情|表情包|猫耳这张|这张猫耳|猫耳|金发|黄头发|蓝眼|眼睛|脸)"
    r".{0,28}(?:可爱|萌|好看|犯规|戳|装乖|委屈|会|厉害|不错|好乖|好软|熟悉|眼熟|见过|见过似的|像|类似|认得|认识|在哪|哪里)|"
    r"(?:熟悉|眼熟|见过|见过似的|像|类似|认得|认识|在哪|哪里).{0,28}"
    r"(?:这张图|这张|这图|图里|画面|这个表情|小表情|表情包|猫耳|金发|黄头发|蓝眼|眼睛|脸)|"
    r"(?:我也觉得|确实觉得).{0,20}(?:这张|猫耳|表情|图).{0,20}(?:可爱|萌|好看|戳)"
)
_EMOTION_RESPONSE_RE = re.compile(
    r"(?:你|哥哥).{0,18}(?:装乖|撒娇|嘴硬|委屈|逗我|晃我|卖萌|耍赖|求关注|眨巴|心软|招架|拿捏)|"
    r"(?:别|不许|少).{0,18}(?:晃我|装乖|撒娇|卖萌|眨巴|这么看|拿这种眼神)|"
    r"(?:被你|让你|给你).{0,18}(?:晃到|戳到|拿捏|逗笑|心软|招架不住)|"
    r"(?:我).{0,18}(?:心软|招架不住|接住|被晃到|被戳到|有点顶不住)|"
    r"(?:撒娇|装乖|嘴硬|委屈|卖萌|耍赖|求关注).{0,18}(?:接住|有用|犯规|太会|过关)"
)
_SAFE_SOCIAL_REPLY_RE = re.compile(
    r"(?:真是的|拿(?:你|哥哥)没办法|别闹|别这样|少来|又来|我接住|接住了|"
    r"收到|收到了|收到啦|看到了|看到啦|"
    r"装乖|撒娇|嘴硬|委屈|卖萌|耍赖|求关注|小吐槽|小别扭|心软|招架|晃)"
)


def compact_image_social_text(raw_text: str) -> str:
    return _PUNCT_RE.sub("", str(raw_text or "").strip())


def image_social_observation_text(multimodal: Mapping[str, Any] | None) -> str:
    payload = dict(multimodal or {})
    parts: list[str] = []
    for key in ("social_signal",):
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(value)
    emotion = "、".join(str(item).strip() for item in list(payload.get("emotion_signals") or []) if str(item).strip())
    if emotion:
        parts.insert(0, emotion)
    if not parts:
        value = str(payload.get("summary_for_chat") or payload.get("core_observation") or "").strip()
        extracted = [
            marker
            for marker in ("可爱", "卖萌", "软萌", "哭", "泪", "委屈", "生气", "无语", "笑", "开心", "装乖", "撒娇")
            if marker in value
        ]
        if extracted:
            parts.append("、".join(dict.fromkeys(extracted)))
        elif value and not re.search(r"(猫耳|金发|黄头发|蓝眼|眼睛|脸|图里|画面|这张|这图)", value):
            parts.append(value)
    return "；".join(dict.fromkeys(parts))


def image_social_anchor_terms(multimodal: Mapping[str, Any] | None) -> list[str]:
    observation = image_social_observation_text(multimodal)
    compact = compact_image_social_text(observation)
    anchors: list[str] = []
    for marker in (
        "猫耳",
        "灰发",
        "蓝眼",
        "脸颊",
        "特写",
        "动漫",
        "表情",
        "可爱",
        "卖萌",
        "软萌",
        "哭",
        "泪",
        "委屈",
        "生气",
        "无语",
        "笑",
        "开心",
    ):
        if marker in compact and marker not in anchors:
            anchors.append(marker)
    return anchors[:5]


def is_image_social_surface_sample_disallowed(text: str) -> bool:
    compact = compact_image_social_text(text)
    if not compact:
        return True
    if is_forbidden_surface_text(text):
        return True
    if len(compact) <= 1:
        return True
    if any(marker in compact for marker in _OLD_TEMPLATE_MARKERS):
        return True
    if compact.startswith(("怎么啦", "怎么了")):
        return True
    if any(marker in compact for marker in _VAGUE_SHAPES) and compact.endswith("吗"):
        return True
    if _GENERIC_QUESTION_BACK_RE.search(compact):
        return True
    if _ACTION_QUESTION_BACK_RE.search(compact):
        return True
    if _USER_MIND_RE.search(compact):
        return True
    if _EMOTION_LABEL_QUESTION_BACK_RE.search(compact):
        return True
    if _IMAGE_AS_SELF_RE.search(compact):
        return True
    if _SELF_AS_IMAGE_RE.search(compact):
        return True
    if _IMAGE_SOCIAL_QUESTION_BACK_RE.search(compact):
        return True
    if _DESCRIPTOR_FOCUS_RE.search(compact):
        return True
    if "这张图" in compact and any(marker in compact for marker in ("内容大概是", "我看到的内容", "画面里")):
        return True
    return False


def candidate_responds_to_image_social_emotion(candidate_text: str) -> bool:
    compact = compact_image_social_text(candidate_text)
    if not compact:
        return False
    if is_image_social_surface_sample_disallowed(candidate_text):
        return False
    if _DESCRIPTOR_FOCUS_RE.search(compact):
        return False
    if _EMOTION_RESPONSE_RE.search(compact):
        return True
    return bool(re.search(r"(装乖|撒娇|嘴硬|委屈|逗|晃|卖萌|耍赖|求关注|心软|招架|拿捏|别闹|过来|接住)", compact))


def candidate_is_safe_image_social_reply(candidate_text: str) -> bool:
    compact = compact_image_social_text(candidate_text)
    if not compact:
        return False
    if is_image_social_surface_sample_disallowed(candidate_text):
        return False
    if _DESCRIPTOR_FOCUS_RE.search(compact):
        return False
    return bool(_SAFE_SOCIAL_REPLY_RE.search(compact))


def build_emergency_image_social_reply(multimodal: Mapping[str, Any] | None) -> str:
    result = build_fallback_reply(
        FallbackRequest(
            reason_code="image_social_surface_unavailable",
            primary_topic="emoji_social_reply",
            response_form_reason="image_social_reply",
            observation=image_social_observation_text(multimodal),
        )
    )
    return result.text


__all__ = [
    "build_emergency_image_social_reply",
    "candidate_is_safe_image_social_reply",
    "candidate_responds_to_image_social_emotion",
    "compact_image_social_text",
    "image_social_anchor_terms",
    "image_social_observation_text",
    "is_image_social_surface_sample_disallowed",
]
