"""Intent hypothesis generation.

The goal is not to pick a single brittle label. The engine proposes several
plausible user intents with evidence so downstream code can choose a human-like
response strategy and repair mistakes when the user corrects us.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


_QUALITY_RE = re.compile(r"(不像人话|不像人类|人类的思考|思考方式|不够聪明|不聪明|太笨|助手味|机械|呆|僵硬|模板|硬规则|质量|意图识别)")
_REPAIR_RE = re.compile(r"(没接上|接不上|承接不上|上下文断了|跑偏|误解|理解错|不是这个|我说的是|我问的是|牛头不对马嘴|不对吧|不太对)")
_RECOMMENDATION_QUERY_RE = re.compile(r"(第\s*(?:[一二两三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?|这本|那本|哪本|小说|轻小说|书).{0,24}(好看|怎么样|值得|推荐|能看|先看|适合)")
_PREFERENCE_QUERY_RE = re.compile(r"(我.*(?:喜欢|偏好|爱看|口味).*(?:什么|哪种|哪类|吗|嘛|么)|我的.{0,8}(?:偏好|口味|喜好))")
_FEEDBACK_RE = re.compile(r"((?:喜欢|不喜欢|讨厌|不错|不行|对味|没意思|别推)|(?:这个|现在|刚才).{0,16}(没用|不行|不好|不对))")
_VALUE_CHALLENGE_RE = re.compile(r"(有意义吗|有什么用|合理吗|值吗|必要吗|为什么要做|不如不做)")
_ACTION_RE = re.compile(r"^\s*(开始|开始吧|继续|继续吧|做吧|修复|实现|处理|重启|验证|提交|推送|拉取|启动)\b")


@dataclass(slots=True)
class IntentHypothesis:
    intent: str
    user_goal: str
    target_kind: str
    target_label: str
    confidence: float
    evidence: dict[str, Any]
    response_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "user_goal": self.user_goal,
            "target_kind": self.target_kind,
            "target_label": self.target_label,
            "confidence": round(float(self.confidence), 4),
            "evidence": dict(self.evidence or {}),
            "response_policy": self.response_policy,
        }


def _text(value: Any, *, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _top_reference(references: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    refs = [_mapping(item) for item in _list(references) if isinstance(item, Mapping)]
    refs.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return refs[0] if refs else {}


def _append_unique(items: list[IntentHypothesis], item: IntentHypothesis) -> None:
    for old in items:
        if old.intent == item.intent and old.target_label == item.target_label:
            if item.confidence > old.confidence:
                old.confidence = item.confidence
                old.evidence.update(item.evidence)
            return
    items.append(item)


def build_intent_hypotheses(
    *,
    text: str,
    references: Sequence[Mapping[str, Any]] | None = None,
    semantic_intent: Mapping[str, Any] | None = None,
    pragmatic_frame: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    semantic = _mapping(semantic_intent)
    pragmatic = _mapping(pragmatic_frame)
    ref = _top_reference(references)
    target_kind = _text(ref.get("target_kind"), limit=80) or "current_message"
    target_label = _text(ref.get("target_label"), limit=180) or normalized
    hypotheses: list[IntentHypothesis] = []

    if target_kind == "previous_life_proactive_message":
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="ask_life_proactive_reason",
                user_goal="想知道 Natsume 刚才为什么主动冒泡、状态怎么了",
                target_kind=target_kind,
                target_label=target_label,
                confidence=0.9,
                evidence={"source": "life_proactive_followup", "reference_bound": True},
                response_policy="接住上一条主动消息，解释是当前生命状态带来的靠近或关心，不要切到旧推荐/旧任务话题",
            ),
        )

    semantic_type = _text(semantic.get("type"), limit=80)
    if semantic_type:
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent=semantic_type,
                user_goal=_text(pragmatic.get("implied_need"), limit=180) or _text(semantic.get("answer_strategy"), limit=160),
                target_kind=target_kind,
                target_label=target_label,
                confidence=float(semantic.get("confidence") or 0.7),
                evidence={"source": "semantic_intent", "dialogue_act": semantic.get("dialogue_act")},
                response_policy=_text(pragmatic.get("expected_response"), limit=180) or _text(semantic.get("answer_strategy"), limit=160),
            ),
        )

    if _RECOMMENDATION_QUERY_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="ask_recommendation_item_judgement",
                user_goal="想知道被指代的推荐作品值不值得看",
                target_kind=target_kind,
                target_label=target_label,
                confidence=0.9 if ref else 0.68,
                evidence={"pattern": "recommendation_followup", "reference_bound": bool(ref)},
                response_policy="先绑定作品，再直接给判断和适合/不适合的理由",
            ),
        )

    if _PREFERENCE_QUERY_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="ask_user_preference_profile",
                user_goal="想确认 Natsume 是否真的理解自己的偏好",
                target_kind="user_profile",
                target_label="用户偏好画像",
                confidence=0.88,
                evidence={"pattern": "preference_query"},
                response_policy="基于证据说确定点和不确定点，不要机械免责声明",
            ),
        )

    if _REPAIR_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="repair_misunderstanding",
                user_goal="指出上一轮理解错了，要求重新绑定上下文",
                target_kind="previous_assistant_turn",
                target_label="上一轮理解或回复",
                confidence=0.9,
                evidence={"pattern": "repair"},
                response_policy="先承认错位，再具体说明改绑到哪里",
            ),
        )

    if _QUALITY_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="criticize_reasoning_quality",
                user_goal="指出系统不像人在思考，希望升级推理方式",
                target_kind="conversation_system",
                target_label="意图理解与对话推理",
                confidence=0.88,
                evidence={"pattern": "quality_meta_feedback"},
                response_policy="诊断思考缺口并给出或执行结构性修复",
            ),
        )

    if _VALUE_CHALLENGE_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="challenge_value_or_direction",
                user_goal="质疑当前方向是否有价值，要求重新判断优先级",
                target_kind=target_kind,
                target_label=target_label,
                confidence=0.82,
                evidence={"pattern": "value_challenge"},
                response_policy="不要辩解，解释价值缺口并重选更有效方向",
            ),
        )

    if _FEEDBACK_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="give_feedback",
                user_goal="表达评价或偏好，希望系统吸收这次反馈",
                target_kind=target_kind,
                target_label=target_label,
                confidence=0.68,
                evidence={"pattern": "feedback"},
                response_policy="确认具体反馈对象，不要过度泛化",
            ),
        )

    if _ACTION_RE.search(normalized):
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="request_action_or_continue",
                user_goal="希望开始、继续或执行当前任务",
                target_kind=target_kind if ref else "active_task",
                target_label=target_label if ref else "当前激活任务",
                confidence=0.74,
                evidence={"pattern": "action_or_continue"},
                response_policy="接上当前任务，不要重新问开放问题",
            ),
        )

    if not hypotheses:
        _append_unique(
            hypotheses,
            IntentHypothesis(
                intent="open_chat",
                user_goal="自然对话或轻量询问",
                target_kind=target_kind,
                target_label=target_label,
                confidence=0.48,
                evidence={"source": "fallback"},
                response_policy="结合最近上下文自然回应",
            ),
        )

    hypotheses.sort(key=lambda item: item.confidence, reverse=True)
    return [item.as_dict() for item in hypotheses[:5]]


__all__ = ["IntentHypothesis", "build_intent_hypotheses"]
