from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
from typing import Any

from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput
from host_brain.persona.reply_surface_memory import build_reply_surface_memory_key


SURFACE_REPLY_RECALL_SKIPPED = "SURFACE_REPLY_RECALL_SKIPPED"
SURFACE_REPLY_RECALL_FAILED = "SURFACE_REPLY_RECALL_FAILED"
SURFACE_REPLY_RECALL_EMPTY = "SURFACE_REPLY_RECALL_EMPTY"

_TASK_ROUTE_MAP: dict[str, tuple[str, str, str, str]] = {
    "daily_good_morning": ("greeting", "phatic_wave", "greeting", "phatic_greeting"),
    "daily_greeting": ("greeting", "phatic_wave", "greeting", "phatic_greeting"),
    "daily_greeting_cn": ("greeting", "phatic_wave", "greeting", "phatic_greeting"),
    "daily_greeting_en": ("greeting", "phatic_wave", "greeting", "phatic_greeting"),
    "greeting": ("greeting", "phatic_wave", "greeting", "phatic_greeting"),
    "presence": ("presence_ping", "phatic_wave", "presence", "default_text"),
    "short_ack": ("light_ack", "phatic_wave", "light_ack", "default_text"),
    "identity": ("identity", "identify_self", "name_only", "default_text"),
}
_LIGHT_TASKS = frozenset(_TASK_ROUTE_MAP)
_TECH_MARKERS = ("报错", "红字", "截图", "贴", "发", "看", "改", "写")
_TEASE_MARKERS = ("哼", "行啦", "好啦", "别", "才", "嘛", "呀", "呢")
_ADDRESS_MARKERS = ("尼尼", "哥哥", "老哥")
_PROCESS_WORDS = ("缓存", "历史回复", "样例", "召回", "检索", "命中")
_SAFE_TRACE_KEYS = frozenset(
    {
        "degraded",
        "error_type",
        "key_digest",
        "reason",
        "reason_code",
        "sample_chars",
        "sample_digest",
        "skipped",
        "style_feature_count",
    }
)


@dataclass(frozen=True)
class ReplySurfaceRecallPlan:
    should_recall: bool
    task_mode: str
    keys: tuple[str, ...] = ()
    primary_topic: str = ""
    tactic_family: str = ""
    tactic_variant: str = ""
    response_form_reason: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_recall": bool(self.should_recall),
            "task_mode": self.task_mode,
            "keys": list(self.keys),
            "primary_topic": self.primary_topic,
            "tactic_family": self.tactic_family,
            "tactic_variant": self.tactic_variant,
            "response_form_reason": self.response_form_reason,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReplySurfaceRecallPacket:
    """Compressed reply-surface memory allowed to cross into ReplyControlPacket."""

    style_anchor: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    confidence: float = 0.0
    key_digest: str = ""
    sample_digest: str = ""
    degraded: bool = False
    reason_code: str | None = None
    trace: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "style_anchor": list(self.style_anchor),
            "must_not_do": list(self.must_not_do),
            "confidence": round(float(self.confidence or 0.0), 6),
            "key_digest": self.key_digest,
            "sample_digest": self.sample_digest,
            "degraded": bool(self.degraded),
            "reason_code": self.reason_code,
            "trace": dict(self.trace or {}),
        }


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _clean_list(values: Sequence[Any] | None) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return tuple(cleaned)


def _coerce_sequence(value: Any) -> Sequence[Any]:
    if value is None or isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return ()
    return value


def _digest(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _safe_trace(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    trace: dict[str, Any] = {}
    for key, value in raw.items():
        key_text = _clean_text(key)
        if key_text not in _SAFE_TRACE_KEYS:
            continue
        if isinstance(value, bool):
            trace[key_text] = value
        elif isinstance(value, int | float):
            trace[key_text] = value
        elif isinstance(value, str):
            trace[key_text] = _clean_text(value)[:80]
    return trace


def _task_mode(context: ThreeLayerContinuityContext) -> str:
    return _clean_text(context.task_mode_hint or context.response_move_hint).lower() or "daily_chat"


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker and marker in text for marker in markers)


def _current_chain_key(
    *,
    soul_id: str,
    task_mode: str,
    response_move: str,
    render_target_lang: str,
) -> str:
    return build_reply_surface_memory_key(
        soul_id=soul_id,
        primary_topic=f"isolated_surface:{task_mode}",
        tactic_family="reply_control_packet",
        tactic_variant=response_move or task_mode,
        response_form_reason="current_main_chain",
        render_target_lang=render_target_lang,
    )


def plan_reply_surface_recall(
    context: ThreeLayerContinuityContext,
    *,
    soul_id: str,
    render_target_lang: str = "zh",
) -> ReplySurfaceRecallPlan:
    task_mode = _task_mode(context)
    response_move = _clean_text(context.response_move_hint).lower() or task_mode
    if task_mode not in _LIGHT_TASKS:
        return ReplySurfaceRecallPlan(
            should_recall=False,
            task_mode=task_mode,
            reason="unsupported_surface_reply_recall_task",
        )

    primary_topic, tactic_family, tactic_variant, response_form_reason = _TASK_ROUTE_MAP[task_mode]
    legacy_key = build_reply_surface_memory_key(
        soul_id=soul_id,
        primary_topic=primary_topic,
        tactic_family=tactic_family,
        tactic_variant=tactic_variant,
        response_form_reason=response_form_reason,
        render_target_lang=render_target_lang,
    )
    current_key = _current_chain_key(
        soul_id=soul_id,
        task_mode=task_mode,
        response_move=response_move,
        render_target_lang=render_target_lang,
    )
    return ReplySurfaceRecallPlan(
        should_recall=True,
        task_mode=task_mode,
        keys=_clean_list([current_key, legacy_key]),
        primary_topic=primary_topic,
        tactic_family=tactic_family,
        tactic_variant=tactic_variant,
        response_form_reason=response_form_reason,
        reason="surface_reply_style_memory",
    )


def build_reply_surface_recall_packet(
    *,
    sample_text: str,
    plan: ReplySurfaceRecallPlan,
    key: str = "",
) -> ReplySurfaceRecallPacket:
    text = _clean_text(sample_text)
    if not text:
        return ReplySurfaceRecallPacket(
            degraded=True,
            reason_code=SURFACE_REPLY_RECALL_EMPTY,
            trace={"plan": plan.as_dict()},
        )

    anchors: list[str] = ["历史表层风格只作节奏锚定，不照抄原句。"]
    length = len(text)
    if length <= 12:
        anchors.append("历史表层风格：短句，少解释。")
    elif length <= 28:
        anchors.append("历史表层风格：一到两拍，别写成长段。")
    else:
        anchors.append("历史表层风格：压短后再用，避免长句模板。")
    if _contains_any(text, _TEASE_MARKERS):
        anchors.append("历史表层风格：可保留轻微熟人推拉，但不升级亲密。")
    if _contains_any(text, _ADDRESS_MARKERS):
        anchors.append("历史表层风格：称呼最多轻放一次，称呼必须服从 Soul。")
    if "？" in text or "?" in text:
        anchors.append("历史表层风格：问句要短，只在 question_budget 允许时使用。")
    if _contains_any(text, _TECH_MARKERS):
        anchors.append("历史表层风格：技术入口短而具体，优先一个可执行抓手。")

    return ReplySurfaceRecallPacket(
        style_anchor=_clean_list(anchors),
        must_not_do=(
            "不要照抄历史表层回复",
            "不要输出缓存/历史回复/样例/召回等过程词",
            "不要沿用历史样本里的越界称呼或关系",
        ),
        confidence=0.72,
        key_digest=_digest(key),
        sample_digest=_digest(text),
        trace={
            "plan": plan.as_dict(),
            "sample_chars": length,
            "style_feature_count": len(anchors),
        },
    )


def recall_reply_surface_for_context(
    *,
    memory_store: Any,
    context: ThreeLayerContinuityContext,
    soul_id: str,
    render_target_lang: str = "zh",
    exclude: Iterable[str] | None = None,
    fail_soft: bool = True,
) -> ReplySurfaceRecallPacket:
    plan = plan_reply_surface_recall(
        context,
        soul_id=soul_id,
        render_target_lang=render_target_lang,
    )
    if not plan.should_recall:
        return ReplySurfaceRecallPacket(
            degraded=False,
            reason_code=SURFACE_REPLY_RECALL_SKIPPED,
            trace={"skipped": True, "plan": plan.as_dict()},
        )
    try:
        for key in plan.keys:
            sample = _clean_text(memory_store.sample(key=key, exclude=exclude))
            if sample:
                packet = build_reply_surface_recall_packet(sample_text=sample, plan=plan, key=key)
                return replace(packet, trace={**dict(packet.trace or {}), "skipped": False})
    except Exception as exc:
        if not fail_soft:
            raise
        return ReplySurfaceRecallPacket(
            degraded=True,
            reason_code=SURFACE_REPLY_RECALL_FAILED,
            trace={
                "error_type": type(exc).__name__,
                "detail": _clean_text(exc)[:160],
                "plan": plan.as_dict(),
            },
        )
    return ReplySurfaceRecallPacket(
        degraded=False,
        reason_code=SURFACE_REPLY_RECALL_EMPTY,
        trace={"skipped": False, "plan": plan.as_dict()},
    )


def apply_reply_surface_recall_packet(
    context: ThreeLayerContinuityContext,
    packet: ReplySurfaceRecallPacket,
) -> ThreeLayerContinuityContext:
    style_anchor = _clean_list(
        [
            *context.style_anchor,
            *packet.style_anchor,
            "表层回复记忆压缩" if packet.style_anchor else "",
        ]
    )
    must_not_do = _clean_list([*context.must_not_do, *packet.must_not_do])
    return replace(
        context,
        style_anchor=style_anchor,
        must_not_do=must_not_do,
    )


def build_surface_input_with_reply_surface_recall(
    context: ThreeLayerContinuityContext,
    *,
    packet: ReplySurfaceRecallPacket,
    soul_md: str,
    case_id: str = "",
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> IsolatedSurfaceInput:
    return build_surface_input_from_three_layer_context(
        apply_reply_surface_recall_packet(context, packet),
        soul_md=soul_md,
        case_id=case_id,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )


def reply_surface_recall_packet_from_mapping(
    raw: Mapping[str, Any] | ReplySurfaceRecallPacket,
) -> ReplySurfaceRecallPacket:
    if isinstance(raw, ReplySurfaceRecallPacket):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("reply surface recall packet must be a mapping")
    return ReplySurfaceRecallPacket(
        style_anchor=_clean_list(_coerce_sequence(raw.get("style_anchor"))),
        must_not_do=_clean_list(_coerce_sequence(raw.get("must_not_do"))),
        confidence=_clamp01(raw.get("confidence")),
        key_digest=_clean_text(raw.get("key_digest")),
        sample_digest=_clean_text(raw.get("sample_digest")),
        degraded=bool(raw.get("degraded", False)),
        reason_code=_clean_text(raw.get("reason_code")) or None,
        trace=_safe_trace(raw.get("trace")),
    )


__all__ = [
    "ReplySurfaceRecallPacket",
    "ReplySurfaceRecallPlan",
    "SURFACE_REPLY_RECALL_EMPTY",
    "SURFACE_REPLY_RECALL_FAILED",
    "SURFACE_REPLY_RECALL_SKIPPED",
    "apply_reply_surface_recall_packet",
    "build_reply_surface_recall_packet",
    "build_surface_input_with_reply_surface_recall",
    "plan_reply_surface_recall",
    "recall_reply_surface_for_context",
    "reply_surface_recall_packet_from_mapping",
]
