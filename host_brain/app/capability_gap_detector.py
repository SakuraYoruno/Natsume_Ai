"""Capability gap detection for Skill-First executive routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from host_brain.app.relational_chat_detector import is_relational_chat_message


_ACTION_INTENT_MARKERS = (
    "帮我",
    "请你",
    "请帮",
    "执行",
    "完成",
    "做一下",
    "处理",
    "创建",
    "生成",
    "实现",
    "修复",
    "排查",
    "部署",
    "安装",
    "配置",
    "翻译",
    "写个",
    "写一段",
    "打开",
    "查询",
    "搜索",
)

_QUESTION_ONLY_PATTERN = re.compile(r"(是什么|为什么|是啥|啥意思|解释一下|讲讲|介绍下)")

_RELATIONAL_NON_ACTION_MARKERS = (
    "我们不熟",
    "不熟吧",
    "很熟吗",
    "熟吗",
    "为什么要帮我",
    "为什么帮我",
    "为什么要管我",
    "为什么管我",
    "还会帮我吗",
    "还会理我吗",
    "还会管我吗",
    "我不喜欢这样",
    "你是谁",
)

_GENERIC_HELP_MARKERS = ("帮我", "请你", "请帮")

_CONCRETE_ACTION_MARKERS = tuple(
    marker for marker in _ACTION_INTENT_MARKERS if marker not in _GENERIC_HELP_MARKERS
)

_CAPABILITY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coding", ("代码", "脚本", "函数", "报错", "bug", "debug", "重构", "编程")),
    ("translation", ("翻译", "日语", "英语", "韩语", "德语", "法语")),
    ("browser", ("网页", "网站", "浏览器", "抓取", "检索", "搜索")),
    ("system", ("进程", "端口", "日志", "系统", "服务", "环境变量")),
    ("memory", ("记住", "回忆", "摘要", "总结会话")),
    ("vision", ("图片", "看图", "截图", "识图", "视频")),
)

_SUGGESTED_FAMILY_BY_TAG = {
    "coding": "code",
    "translation": "translation",
    "browser": "browser",
    "system": "system",
    "memory": "memory",
    "vision": "perception",
}


@dataclass(frozen=True)
class CapabilityGapDecision:
    detected: bool
    reason_code: str
    confidence: float
    missing_capability_tags: tuple[str, ...]
    suggested_family: str | None
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "missing_capability_tags": list(self.missing_capability_tags),
            "suggested_family": self.suggested_family,
            "details": dict(self.details or {}),
        }


def _normalize(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _looks_like_action_intent(user_message: str) -> bool:
    message = _normalize(user_message)
    if len(message) < 6:
        return False
    if is_relational_chat_message(message):
        return False
    lowered = _normalize(message, lower=True)
    has_concrete_action = any(marker in message for marker in _CONCRETE_ACTION_MARKERS)
    capability_tags = _extract_capability_tags(message)
    has_capability_hint = any(tag != "reasoning" for tag in capability_tags)
    if any(marker in lowered for marker in _RELATIONAL_NON_ACTION_MARKERS):
        if not has_concrete_action and not has_capability_hint:
            return False
    if _QUESTION_ONLY_PATTERN.search(message):
        if not has_concrete_action and not has_capability_hint:
            return False
    if any(marker in message for marker in _ACTION_INTENT_MARKERS):
        if any(marker in message for marker in _GENERIC_HELP_MARKERS):
            return has_concrete_action or has_capability_hint
        return True
    if len(message) > 18 and ("然后" in message or "并且" in message or "同时" in message):
        return True
    return False


def _extract_capability_tags(user_message: str) -> tuple[str, ...]:
    message = _normalize(user_message, lower=True)
    tags: list[str] = []
    for tag, keywords in _CAPABILITY_HINTS:
        if any(keyword.lower() in message for keyword in keywords):
            tags.append(tag)
    if not tags:
        tags.append("reasoning")
    return tuple(dict.fromkeys(tags))


def detect_capability_gap(
    *,
    user_message: str,
    route_type: str,
    target_id: str = "",
    reason_code: str = "",
    llm_route_used: bool = False,
) -> CapabilityGapDecision:
    normalized_route = _normalize(route_type, lower=True)
    normalized_target = _normalize(target_id, lower=True)
    normalized_reason = _normalize(reason_code, lower=True)
    message = _normalize(user_message)

    if normalized_route in {"skill", "delegate", "plan", "task_progress", "recent_turn", "gap"}:
        return CapabilityGapDecision(
            detected=False,
            reason_code="ROUTE_ALREADY_EXECUTABLE",
            confidence=0.0,
            missing_capability_tags=(),
            suggested_family=None,
            details={"route_type": normalized_route, "target_id": normalized_target},
        )

    if normalized_route != "chat":
        return CapabilityGapDecision(
            detected=False,
            reason_code="ROUTE_NOT_CHAT",
            confidence=0.0,
            missing_capability_tags=(),
            suggested_family=None,
            details={"route_type": normalized_route},
        )

    if not _looks_like_action_intent(message):
        return CapabilityGapDecision(
            detected=False,
            reason_code="NO_ACTION_INTENT",
            confidence=0.0,
            missing_capability_tags=(),
            suggested_family=None,
            details={"route_reason": normalized_reason, "message_len": len(message)},
        )

    tags = _extract_capability_tags(message)
    suggested_family = _SUGGESTED_FAMILY_BY_TAG.get(tags[0], "reasoning")
    confidence = 0.64
    if llm_route_used:
        confidence += 0.08
    if "default_chat" in normalized_reason or normalized_reason.startswith("llm_route:chat"):
        confidence += 0.1
    if len(tags) >= 2:
        confidence += 0.05
    confidence = max(0.0, min(0.98, confidence))

    return CapabilityGapDecision(
        detected=True,
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        confidence=confidence,
        missing_capability_tags=tags,
        suggested_family=suggested_family,
        details={
            "route_reason": normalized_reason,
            "llm_route_used": bool(llm_route_used),
            "message_len": len(message),
        },
    )


__all__ = ["CapabilityGapDecision", "detect_capability_gap"]
