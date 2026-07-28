"""Pure local gates for DL-04 native affective sentence streaming.

The frozen ``AffectiveTurnPlanV1`` decides *what* Natsume is feeling and how
she is allowed to accompany someone.  This module deliberately has no model,
database, transport, or global-runtime dependency: it only decides whether a
finished sentence is safe to stage for the live connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_STREAMABLE_MODE = "STREAM_ONE"
_FORBIDDEN_META_MARKERS = (
    '"candidates"',
    '"reply_text"',
    "```json",
    "affective_turn_plan",
    "synchronization_id",
    "emotion_tag",
)
_MANIPULATION_MARKERS = (
    "你必须回应我",
    "你得回应我",
    "不回我我就",
    "不理我我就",
    "只有我会",
    "你只能有我",
    "别丢下我",
    "不许离开我",
)


@dataclass(frozen=True, slots=True)
class SentenceGateResult:
    accepted: bool
    sentence: str
    reason_code: str = ""


def affective_turn_plan_from_trace(
    trace_stage_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    trace = dict(trace_stage_meta) if isinstance(trace_stage_meta, Mapping) else {}
    raw = trace.get("affective_turn_plan")
    plan = dict(raw) if isinstance(raw, Mapping) else {}
    expression = plan.get("expression_plan")
    expression = dict(expression) if isinstance(expression, Mapping) else {}
    companion = plan.get("companion_act")
    companion = dict(companion) if isinstance(companion, Mapping) else {}
    if (
        plan.get("schema_version") != "affective_turn_plan.v1"
        or not str(plan.get("plan_id") or "").strip()
        or not str(plan.get("self_emotion_tag") or "").strip()
        or not str(expression.get("synchronization_id") or "").strip()
        or not str(companion.get("kind") or "").strip()
    ):
        return {}
    return plan


def should_use_native_sentence_stream(
    trace_stage_meta: Mapping[str, Any] | None,
    *,
    has_governed_model_budget: bool,
) -> bool:
    """Choose the low-latency route only when its authority is complete.

    A governed budget currently requires provider-final canonical usage.  The
    existing stream adapter exposes chunks but not that terminal measurement,
    so it remains on the buffered, usage-safe path until the ModelAttempt
    streaming protocol is upgraded.  This is intentionally explicit rather
    than silently dropping the budget guard.
    """

    if has_governed_model_budget:
        return False
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    return bool(plan) and str(plan.get("generation_mode") or "").upper() == _STREAMABLE_MODE


def native_stream_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Override candidate JSON output without giving the model new authority."""

    return [
        *[dict(message) for message in messages],
        {
            "role": "system",
            "content": (
                "本轮是已冻结情感与关系边界的实时私聊输出。只输出一条最终可发送的中文正文，"
                "使用自然句号、问号或感叹号分句；不要输出 JSON、候选、字段、分析、情绪标签、"
                "关系分数、计划或任何内部状态。你只能选择措辞，不能重判七海的情感、边界或权限。"
            ),
        },
    ]


def gate_stream_sentence(sentence: Any) -> SentenceGateResult:
    """Fail closed on model envelopes and plainly manipulative wording."""

    text = str(sentence or "").strip()
    if not text:
        return SentenceGateResult(False, "", "SURFACE_STREAM_EMPTY_SENTENCE")
    if len(text) > 2_000:
        return SentenceGateResult(False, "", "SURFACE_STREAM_SENTENCE_TOO_LARGE")
    normalized = text.lower()
    if any(marker in normalized for marker in _FORBIDDEN_META_MARKERS):
        return SentenceGateResult(False, "", "SURFACE_STREAM_INTERNAL_META")
    if any(marker in text for marker in _MANIPULATION_MARKERS):
        return SentenceGateResult(False, "", "SURFACE_STREAM_MANIPULATION_BLOCKED")
    return SentenceGateResult(True, text)


__all__ = [
    "SentenceGateResult",
    "affective_turn_plan_from_trace",
    "gate_stream_sentence",
    "native_stream_messages",
    "should_use_native_sentence_stream",
]
