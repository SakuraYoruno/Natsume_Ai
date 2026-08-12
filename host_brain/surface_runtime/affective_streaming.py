"""DL-04 原生情感逐句流的纯本地门控。

冻结的 ``AffectiveTurnPlanV1`` 决定 Natsume 的感受及允许的陪伴方式；本模块没有模型、
数据库、传输或全局运行时依赖，只判断一条已完成句子能否安全暂存到实时连接。
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
# 被拒绝时句子清空而非回显，避免调用链下游把危险文本误当作可展示内容。
    """逐句门控结果；拒绝时不回传原句，避免下游误将其作为可显示内容。"""
    accepted: bool
    sentence: str
    reason_code: str = ""


def affective_turn_plan_from_trace(
    trace_stage_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    # 只接受完整且版本匹配的已冻结计划；不从零散 trace 字段拼凑情绪或陪伴决策。
    """从追踪元数据提取最小可用的冻结计划；缺少关键身份即返回空计划。"""
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
        # 身份/同步锚点缺失时退回普通路径，绝不以“部分计划”驱动低延迟输出。
        return {}
    return plan


def should_use_native_sentence_stream(
    trace_stage_meta: Mapping[str, Any] | None,
    *,
    has_governed_model_budget: bool,
    has_governed_delivery_authority: bool = False,
) -> bool:
    # 模型预算不等于可见投递权；两项授权缺一时不能开启绕过完整交付链的低延迟流。
    """仅在授权链完整时选择低延迟路径。

    有受治理模型预算时，调用方还必须提供 Task Runtime 绑定节点/尝试/租约/grant 的
    可见性权威；provider 的最终模型结果本身仍不是用户可见收据。
    """

    if has_governed_model_budget and not has_governed_delivery_authority:
        return False
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    return bool(plan) and str(plan.get("generation_mode") or "").upper() == _STREAMABLE_MODE


def native_stream_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    # 系统提示只限制表达格式，不提供修改情感、关系或治理状态的任何新输入。
    """覆盖候选 JSON 输出形式，但不向模型授予新的情感、关系或投递权威。"""

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
    # 逐句闸门是最终显示前的防线：拒绝内部元数据、过大输出和关系施压话术。
    """对模型信封和明显操控性措辞闭合失败，防止内部状态或关系施压进入实时流。"""

    text = str(sentence or "").strip()
    if not text:
        # 空句不占用流位置，调用方可安全继续等待后续完整句。
        return SentenceGateResult(False, "", "SURFACE_STREAM_EMPTY_SENTENCE")
    if len(text) > 2_000:
        # 限长防止单个 chunk 绕过逐句节流或把整段内部内容一次性塞入界面。
        return SentenceGateResult(False, "", "SURFACE_STREAM_SENTENCE_TOO_LARGE")
    normalized = text.lower()
    if any(marker in normalized for marker in _FORBIDDEN_META_MARKERS):
        # 命中协议/计划标记表明模型泄露了控制面，不进行部分清洗而是整句拒绝。
        return SentenceGateResult(False, "", "SURFACE_STREAM_INTERNAL_META")
    if any(marker in text for marker in _MANIPULATION_MARKERS):
        # 对明确操控性措辞 fail closed，避免低延迟流成为施压关系表达的旁路。
        return SentenceGateResult(False, "", "SURFACE_STREAM_MANIPULATION_BLOCKED")
    return SentenceGateResult(True, text)


__all__ = [
    "SentenceGateResult",
    "affective_turn_plan_from_trace",
    "gate_stream_sentence",
    "native_stream_messages",
    "should_use_native_sentence_stream",
]
