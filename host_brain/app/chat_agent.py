"""chat_agent.py — ChatAgent 人格渲染管线（Phase XV）。

夏目蓝本人的"说话"模块。
职责：拿到上下文→构建 prompt→流式生成→反思链→净化→COMMIT。
调用者：DispatchAgent（通过 chat_agent_sse_generator 函数）。

架构原则：
  - ChatAgent 只负责「说出来」，不做路由/调度决策。
  - 所有输入通过 ChatAgentInput 结构体传入（sub_agent_context 可带子 Agent 结果）。
  - 所有输出经完整人格管线：角色人设 + 情感 + Bond + 风格蒸馏 + 反思链。
  - 不调用 SkillLoader，不做委托判断，不感知 sys_mode 路由。

关系：
  DispatchAgent  ──────────────▶  ChatAgent
   (路由决策/        ChatAgentInput   (人格渲染/
    Skill执行/        结构体传递       LLM流式生成/
    子Agent调度)                       COMMIT输出)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json,
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)

_logger = logging.getLogger("Natsume.chat_agent")

_FALLBACK_GREETING_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_FALLBACK_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_FALLBACK_AFFECTION_RE = re.compile(r"(想你|好想你|我想你了|想你啦|惦记你)")
_FALLBACK_STRENGTH_RE = re.compile(r"(特长|擅长|你会什么|你能做什么|你会做什么)")

_DELEGATE_MARKER_RE = re.compile(r"\[DELEGATE:[^\]]+\]", re.IGNORECASE)



# ---------------------------------------------------------------------------
# 输入/输出结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentContext:
    """子 Agent 执行结果，注入 ChatAgent prompt。"""
    agent_id: str = ""
    task: str = ""
    result_text: str = ""
    success: bool = True
    latency_ms: int = 0
    error: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatAgentInput:
    """ChatAgent 的完整输入上下文。"""
    # ── 必填 ──
    request_id: str = ""
    session_id: str = ""
    user_message: str = ""           # 净化后的用户消息
    person_id: str = ""

    # ── 情感 / Bond 上下文 ──
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: Dict[str, Any] = field(default_factory=dict)
    bond_summary: Dict[str, Any] = field(default_factory=dict)

    # ── 检索结果（RecallFusion 输出）──
    recall_slot_text: Dict[str, str] = field(default_factory=dict)
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})

    # ── 子 Agent 执行结果（执行路径时由 DispatchAgent 填入）──
    sub_agent_context: Optional[SubAgentContext] = None

    # ── 通道参数 ──
    render_target_lang: str = "zh"   # Phase XV: 双轨，文字通道永远 zh
    reply_channel: Optional[str] = None   # GATEWAY / ASR / WEB 等
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    reply_depth_policy: str = "normal"    # "normal" / "gateway"（QQ 要求更短）

    # ── 链路追踪 ──
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)
    trace_stage_meta: Dict[str, Any] = field(default_factory=dict)

    # ── LLM 运行时（由调用者 resolve 后传入，避免重复 resolve）──
    llm_runtime: Optional[Dict[str, Any]] = None

    # ── 行为约束策略（可由上游直接传入，缺省时从 prompt runtime_payload 回填）──
    response_policy: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具函数（从 top_agent.py 复用）
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


def _strip_internal_delegate_markers(text: str) -> str:
    normalized = str(text or "")
    normalized = _DELEGATE_MARKER_RE.sub("", normalized)
    normalized = normalized.replace("{agent_result}", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _coerce_response_policy(raw_policy: Any) -> Dict[str, Any]:
    if isinstance(raw_policy, Mapping):
        return dict(raw_policy)
    return {}


def _split_text_into_sentences(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

    splitter = SentenceSplitter()
    sentences = list(splitter.feed(normalized))
    remainder = splitter.flush()
    if remainder:
        sentences.append(remainder)
    return [str(sentence).strip() for sentence in sentences if str(sentence).strip()]


def _truncate_text_to_sentence_limit(text: str, *, max_sentences: int) -> str:
    normalized = str(text or "").strip()
    if max_sentences <= 0 or not normalized:
        return normalized

    sentences = _split_text_into_sentences(normalized)
    if len(sentences) <= max_sentences:
        return normalized

    search_start = 0
    taken_end = 0
    taken_count = 0
    for sentence in sentences:
        found_at = normalized.find(sentence, search_start)
        if found_at < 0:
            continue
        taken_end = found_at + len(sentence)
        search_start = taken_end
        taken_count += 1
        if taken_count >= max_sentences:
            break

    if taken_count >= max_sentences and taken_end > 0:
        return normalized[:taken_end].strip()
    return "".join(sentences[:max_sentences]).strip()


def _truncate_text_to_char_limit(text: str, *, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    candidate = normalized[:max_chars].rstrip()
    boundary_positions = [candidate.rfind(ch) for ch in "。！？!?…\n」）"]
    boundary_index = max(boundary_positions) if boundary_positions else -1
    if boundary_index >= max(0, max_chars // 2):
        natural = candidate[: boundary_index + 1].strip()
        if natural:
            return natural
    return candidate.rstrip("，,、；;：:-— ").strip()


def _extract_response_policy_limits(response_policy: Any) -> tuple[Dict[str, Any], int, int, int]:
    policy = _coerce_response_policy(response_policy)
    try:
        max_sentences = max(0, int(policy.get("max_sentences") or 0))
    except (TypeError, ValueError):
        max_sentences = 0
    try:
        max_chars = max(0, int(policy.get("max_chars") or 0))
    except (TypeError, ValueError):
        max_chars = 0
    try:
        min_sentences = max(0, int(policy.get("min_sentences") or 0))
    except (TypeError, ValueError):
        min_sentences = 0

    if bool(policy.get("should_be_brief")):
        if max_sentences <= 0:
            max_sentences = 3
        if max_chars <= 0:
            max_chars = 120

    return policy, max_chars, max_sentences, min_sentences


def _coerce_response_policy_score(policy: Mapping[str, Any], key: str, *, default: float = 1.0) -> float:
    try:
        value = float(policy.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, value))


def _build_minimal_chat_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    render_target_lang: str,
) -> str:
    text = str(user_message or "").strip()
    if render_target_lang == "ja":
        return resolve_prompt_template_text("chat_agent.minimal_fallback.ja_default")

    if sub_agent_ctx is not None:
        agent_id = str(sub_agent_ctx.agent_id or "").strip()
        if agent_id == "code_agent":
            return resolve_prompt_template_text("chat_agent.minimal_fallback.code_agent")
        if agent_id == "recent_turn_direct":
            return resolve_prompt_template_text("chat_agent.minimal_fallback.recent_turn_direct")
        if agent_id == "phatic_fastpath":
            return resolve_prompt_template_text("chat_agent.minimal_fallback.phatic_fastpath")

    if _FALLBACK_STRENGTH_RE.search(text):
        return _build_installed_capabilities_reply(render_target_lang=render_target_lang)
    if _FALLBACK_PRESENCE_RE.match(text):
        return resolve_prompt_template_text("chat_agent.minimal_fallback.presence")
    if _FALLBACK_GREETING_RE.match(text):
        return resolve_prompt_template_text("chat_agent.minimal_fallback.greeting")
    return resolve_prompt_template_text("chat_agent.minimal_fallback.default")


def _load_template_replacement_pairs(template_id: str) -> list[tuple[str, str]]:
    raw = resolve_prompt_template_json(template_id)
    pairs: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return pairs
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if source:
            pairs.append((source, target))
    return pairs


class _SafeTemplateDict(dict[str, str]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


def _runtime_text(
    key: str,
    **kwargs: Any,
) -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
    return _mapping_text(templates, key, **kwargs)


def _looks_like_followup_sentence(sentence: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(sentence or "")).strip()
    if not normalized:
        return False
    followup_markers = (
        "你呢", "要不要", "想不想", "还要不要", "要继续吗", "如果你愿意",
        "方便的话", "可以告诉我", "能不能", "要不要我", "要我继续", "还想",
        "要聊聊吗", "愿意的话", "想继续", "要不要继续",
    )
    if any(marker in normalized for marker in followup_markers):
        return True
    return bool(re.search(r"[?？]$", normalized))


def _soften_followup_sentence(sentence: str) -> str:
    normalized = str(sentence or "").strip()
    if not normalized:
        return ""
    patterns = [
        r"[，,、]?(?:你呢|你觉得呢|你怎么看呢|你怎么看|你觉得怎么样呢|你觉得怎么样)[。！？!?？]*$",
        r"[，,、]?(?:要不要|想不想|还要不要|要不要继续|要继续吗).*[。！？!?？]*$",
        r"[，,、]?(?:如果你愿意|方便的话|愿意的话|可以的话).*[。！？!?？]*$",
        r"[，,、]?(?:可以告诉我|能不能告诉我|要不要和我说说).*[。！？!?？]*$",
    ]
    softened = normalized
    for pattern in patterns:
        softened = re.sub(pattern, "", softened).strip()
    softened = softened.rstrip("，,、；;：: ").strip()
    if softened == normalized and _looks_like_followup_sentence(normalized):
        return ""
    if softened and softened[-1] not in ".。！？!?":
        softened += "。"
    return softened


def _looks_like_topic_shift_sentence(sentence: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(sentence or "")).strip()
    if not normalized:
        return False
    return bool(re.match(r"^(?:对了|另外|顺便|说起来|还有|题外话|再补一句|补充一句)", normalized))


def _contains_confession_marker(sentence: str) -> bool:
    normalized = str(sentence or "")
    markers = (
        "我爱你", "爱你", "好喜欢你", "最喜欢你", "喜欢你", "好想你", "想你了",
        "想你", "离不开你", "不能没有你", "只想和你", "属于你", "亲亲", "抱抱你",
        "一直陪着你", "永远陪着你", "会一直等你",
    )
    return any(marker in normalized for marker in markers)


def _contains_intimacy_marker(sentence: str) -> bool:
    normalized = str(sentence or "")
    if _contains_confession_marker(normalized):
        return True
    markers = (
        "亲爱的", "宝贝", "宝宝", "乖乖", "抱抱", "亲亲",
        "喜欢你", "好喜欢你", "最喜欢你", "想你", "好想你", "想你了",
    )
    return any(marker in normalized for marker in markers)


def _soften_confession_sentence(sentence: str) -> str:
    softened = str(sentence or "").strip()
    if not softened:
        return ""
    replacements = _load_template_replacement_pairs("chat_agent.response_policy.confession_replacements")
    for source, target in replacements:
        softened = softened.replace(source, target)
    softened = re.sub(r"\s+", " ", softened).strip()
    softened = softened.rstrip("，,、；;：: ").strip()
    if softened and softened[-1] not in ".。！？!?":
        softened += "。"
    return softened


def _soften_intimacy_sentence(sentence: str) -> str:
    softened = str(sentence or "").strip()
    if not softened:
        return ""
    if _contains_confession_marker(softened):
        softened = _soften_confession_sentence(softened)
    replacements = _load_template_replacement_pairs("chat_agent.response_policy.intimacy_replacements")
    for source, target in replacements:
        softened = softened.replace(source, target)
    softened = softened.replace("你你", "你").replace("先先", "先")
    softened = re.sub(r"\s+", " ", softened).strip()
    softened = softened.lstrip("，,、；;：: ").strip()
    softened = re.sub(r"([。！？!?])\1+", r"\1", softened)
    softened = softened.rstrip("，,、；;：: ").strip()
    if softened and softened[-1] not in ".。！？!?":
        softened += "。"
    return softened


def _soften_humor_tone(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"[~～]+", "", normalized)
    normalized = re.sub(r"哈{2,}", "", normalized)
    normalized = re.sub(r"呵{2,}", "", normalized)
    normalized = re.sub(r"嘿{2,}", "", normalized)
    normalized = re.sub(r"嘻{2,}", "", normalized)
    normalized = re.sub(r"([。！？!?])[，,、 ]+", r"\1", normalized)
    normalized = re.sub(r"[，,、 ]+([。！？!?])", r"\1", normalized)
    normalized = re.sub(r"([。！？!?])\1+", r"\1", normalized)
    normalized = re.sub(r"([，,、])\1+", r"\1", normalized)
    normalized = normalized.lstrip("，,、。！？!? ").strip()
    normalized = normalized.rstrip("，,、；;：: ").strip()
    if not normalized:
        return resolve_prompt_template_text("chat_agent.response_policy.min_sentence_placeholder")
    if normalized[-1] not in ".。！？!?":
        normalized += "。"
    return normalized


def _soften_withdrawn_tone(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"[~～]+", "", normalized)
    normalized = re.sub(r"(?:!|！){2,}", "。", normalized)
    normalized = re.sub(r"(?:\?|？){2,}", "？", normalized)
    for source, target in _load_template_replacement_pairs("chat_agent.response_policy.withdrawn_replacements"):
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"([。！？!?])\1+", r"\1", normalized)
    return normalized.strip()


def _drop_trailing_policy_sentences(
    sentences: list[str],
    *,
    min_sentences: int,
    predicate,
) -> list[str]:
    guarded = list(sentences or [])
    floor = max(1, int(min_sentences or 0)) if guarded else 0
    while len(guarded) > floor and predicate(guarded[-1]):
        guarded.pop()
    return guarded


def _apply_response_policy_stream_guard(text: str, response_policy: Any) -> str:
    normalized = str(text or "").strip()
    policy, max_chars, max_sentences, _min_sentences = _extract_response_policy_limits(response_policy)
    if not normalized or not policy:
        return normalized
    if max_sentences > 0:
        normalized = _truncate_text_to_sentence_limit(normalized, max_sentences=max_sentences)
    if max_chars > 0:
        normalized = _truncate_text_to_char_limit(normalized, max_chars=max_chars)
    return normalized.strip()


def _response_policy_requires_buffering(response_policy: Any) -> bool:
    policy, _max_chars, _max_sentences, _min_sentences = _extract_response_policy_limits(response_policy)
    if not policy:
        return False
    intimacy_ceiling = _coerce_response_policy_score(policy, "intimacy_ceiling")
    humor_ceiling = _coerce_response_policy_score(policy, "humor_ceiling")
    return any([
        not bool(policy.get("allow_followup", True)),
        not bool(policy.get("allow_topic_shift", True)),
        bool(policy.get("confession_guard", False)),
        bool(policy.get("withdrawal_guard", False)),
        bool(policy.get("should_be_withdrawn", False)),
        intimacy_ceiling <= 0.55,
        humor_ceiling <= 0.35,
    ])


def _apply_response_policy_hard_guard(
    narrative: str,
    response_policy: Any,
    *,
    user_message: str = "",
    bond_stage: str = "",
) -> str:
    normalized = str(narrative or "").strip()
    policy, max_chars, max_sentences, min_sentences = _extract_response_policy_limits(response_policy)
    if not normalized or not policy:
        return normalized

    intimacy_ceiling = _coerce_response_policy_score(policy, "intimacy_ceiling")
    humor_ceiling = _coerce_response_policy_score(policy, "humor_ceiling")

    sentences = _split_text_into_sentences(normalized)
    if sentences:
        if not bool(policy.get("allow_topic_shift", True)):
            sentences = _drop_trailing_policy_sentences(
                sentences,
                min_sentences=min_sentences,
                predicate=_looks_like_topic_shift_sentence,
            )
        if not bool(policy.get("allow_followup", True)):
            sentences = _drop_trailing_policy_sentences(
                sentences,
                min_sentences=min_sentences,
                predicate=_looks_like_followup_sentence,
            )
            if sentences and _looks_like_followup_sentence(sentences[-1]):
                softened = _soften_followup_sentence(sentences[-1])
                if softened:
                    sentences[-1] = softened
                elif len(sentences) > max(1, int(min_sentences or 0)):
                    sentences.pop()
                else:
                    sentences[-1] = resolve_prompt_template_text("chat_agent.response_policy.min_sentence_placeholder")

        confession_guard = bool(policy.get("confession_guard", False)) or intimacy_ceiling <= 0.35
        if confession_guard:
            guarded_sentences: list[str] = []
            for sentence in sentences:
                if _contains_confession_marker(sentence):
                    sentence = _soften_confession_sentence(sentence)
                if sentence:
                    guarded_sentences.append(sentence)
            sentences = guarded_sentences or sentences

        if intimacy_ceiling <= 0.55:
            guarded_sentences = []
            total_sentences = len(sentences)
            floor = max(1, int(min_sentences or 0)) if total_sentences else 0
            for index, sentence in enumerate(sentences):
                if _contains_intimacy_marker(sentence):
                    sentence = _soften_intimacy_sentence(sentence)
                if sentence:
                    guarded_sentences.append(sentence)
                    continue
                remaining_after = total_sentences - index - 1
                if len(guarded_sentences) + remaining_after < floor:
                    guarded_sentences.append(
                        resolve_prompt_template_text("chat_agent.response_policy.min_sentence_placeholder")
                    )
            sentences = guarded_sentences or sentences

        normalized = "".join(str(sentence).strip() for sentence in sentences if str(sentence).strip()).strip()

    if humor_ceiling <= 0.35:
        normalized = _soften_humor_tone(normalized)

    if bool(policy.get("withdrawal_guard", False)) or bool(policy.get("should_be_withdrawn", False)):
        normalized = _soften_withdrawn_tone(normalized)

    if max_sentences > 0:
        normalized = _truncate_text_to_sentence_limit(normalized, max_sentences=max_sentences)
    if max_chars > 0:
        normalized = _truncate_text_to_char_limit(normalized, max_chars=max_chars)
    return normalized.strip()


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    import re as _re
    _EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
        ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
        ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
        ("ANGRY", "生气|气死|烦|腹立|むかつく"),
        ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
        ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
    ]
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return tag
    return "NEUTRAL"


async def _post_speak_sentence(
    *,
    text: str,
    utterance_id: str,
    emotion_tag: str,
    request_id: str,
    session_id: str,
) -> None:
    """Phase XV P7：向 Edge 发送单句 TTS 请求（fire-and-forget）。

    语音通道永远使用日语（lang_hint="ja"），不受 render_target_lang 影响。
    """
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        await post_speak(
            text=text,
            utterance_id=utterance_id,
            request_id=request_id,
            session_id=session_id,
            emotion_tag=emotion_tag,
            lang_hint="ja",  # Phase XV: 语音通道永远日语
        )
    except Exception as exc:
        _logger.debug("[%s] P7 sentence TTS failed (non-critical): %s", request_id, exc)


def _extract_visible_text(raw: str) -> str:
    """从 LLM 原始输出中提取可见文本（支持 JSON / fenced JSON 包装格式）。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = str(fenced.group(1) or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text_zh") or narrative.get("text") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text

def _extract_tts_text(raw: str) -> str:
    """提取 TTS 语音通道文本（日语，text_ja 字段）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    # Phase XV P6：TTS 语音通道取 text_ja
                    return str(narrative.get("text_ja") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return ""


def _normalize_plan_step_description(description: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:蓝蓝|夏目蓝|夏目藍|小蓝|阿蓝)[，,、:：\s]*", "", text)
    prefixes = ("先", "然后", "再", "接着", "顺便", "帮我", "给我", "请")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text.strip(" 　。！？!?，,；;：:")


def _render_plan_step_success_cn(step: Dict[str, Any]) -> str:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    success_templates = _mapping_section(templates, "step_success")
    step_index = int(step.get("step_index") or 0)
    target_id = str(
        step.get("target_id")
        or _runtime_text("default_step_target")
    ).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    result_text = str(step.get("result_text") or "").strip()
    description_or_target = description or target_id

    if target_id == "web_search":
        topic = description or _runtime_text("default_topic")
        topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
        return _mapping_text(
            success_templates,
            "web_search",
            step_index=step_index,
            topic=(topic or _runtime_text("default_topic")),
        )
    if target_id == "memory_store":
        if description.startswith("记住"):
            remembered = description.removeprefix("记住").strip()
            if remembered:
                if remembered.startswith("我"):
                    remembered = "你" + remembered[1:]
                return _mapping_text(
                    success_templates,
                    "memory_store.remembered",
                    step_index=step_index,
                    remembered=remembered,
                )
        return _mapping_text(success_templates, "memory_store.default", step_index=step_index)
    if target_id == "system_info":
        return _mapping_text(success_templates, "system_info", step_index=step_index)
    if target_id == "process_list":
        return _mapping_text(success_templates, "process_list", step_index=step_index)
    if target_id == "network_status":
        return _mapping_text(success_templates, "network_status", step_index=step_index)
    if target_id == "hub_skill_search":
        topic = description or _runtime_text("default_topic")
        topic = re.sub(r"^(?:搜索技能|查找技能|搜技能)\s*", "", topic).strip() or _runtime_text("default_topic")
        return _mapping_text(success_templates, "hub_skill_search", step_index=step_index, topic=topic)
    if target_id == "hub_skill_list":
        return _mapping_text(success_templates, "hub_skill_list", step_index=step_index)
    if target_id == "hub_skill_install":
        return _mapping_text(success_templates, "hub_skill_install", step_index=step_index)
    if target_id == "hub_skill_remove":
        return _mapping_text(success_templates, "hub_skill_remove", step_index=step_index)
    if target_id == "file_read":
        return _mapping_text(success_templates, "file_read", step_index=step_index)
    if target_id == "file_write":
        return _mapping_text(success_templates, "file_write", step_index=step_index)
    if target_id == "file_list":
        return _mapping_text(success_templates, "file_list", step_index=step_index)
    if target_id == "code_agent":
        return _mapping_text(success_templates, "code_agent", step_index=step_index)
    if target_id == "intent_agent":
        return _mapping_text(success_templates, "intent_agent", step_index=step_index)
    if result_text:
        return _mapping_text(
            success_templates,
            "__with_result",
            step_index=step_index,
            description_or_target=description_or_target,
        )
    return _mapping_text(
        success_templates,
        "__default",
        step_index=step_index,
        description_or_target=description_or_target,
    )


def _render_plan_step_failure_cn(step: Dict[str, Any]) -> str:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    failure_templates = _mapping_section(templates, "step_failure")
    step_index = int(step.get("step_index") or 0)
    target_id = str(
        step.get("target_id")
        or _runtime_text("default_step_target")
    ).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = _mapping_text(
        failure_templates,
        "base",
        step_index=step_index,
        description_or_target=(description or target_id),
    )
    if error_text:
        base += _mapping_text(failure_templates, "error_suffix", error_text=error_text)
    if recovery_text:
        base += _mapping_text(failure_templates, "recovery_suffix", recovery_text=recovery_text)
    return base


def _build_plan_execution_reply_cn(ctx: SubAgentContext) -> str | None:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    summary_templates = _mapping_section(templates, "summary")
    payload = dict(ctx.payload or {})
    steps = list(payload.get("steps") or [])
    if not steps:
        return None
    total_steps = int(payload.get("total_steps") or len(steps))
    parallel_enabled = bool(payload.get("parallel_enabled"))
    success_count = int(payload.get("success_count") or 0)
    intro = (
        _mapping_text(summary_templates, "intro_parallel", total_steps=total_steps)
        if parallel_enabled and total_steps > 1
        else _mapping_text(summary_templates, "intro_sequential", total_steps=total_steps)
    )
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step))
        else:
            parts.append(_render_plan_step_failure_cn(step))
    if success_count == len(steps):
        parts.append(_mapping_text(summary_templates, "all_done"))
    else:
        parts.append(_mapping_text(summary_templates, "partial_done"))
    return "".join(parts)


def _infer_hub_skill_name_from_task(task: str, *, markers: tuple[str, ...], default_name: str) -> str:
    normalized = str(task or "").strip()
    for marker in markers:
        if marker in normalized:
            normalized = normalized.split(marker, 1)[-1].strip(" ：:")
            break
    normalized = re.sub(r"^(?:蓝蓝|夏目蓝|夏目蓝蓝|natsume ai|natsume)[，,\s:：]*", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"\s+(?:强制|覆盖|覆盖安装|立即激活|现在激活)$", "", normalized, flags=re.IGNORECASE).strip()
    normalized = normalized.strip("，。；！？,.!? ")
    return normalized or default_name


def _format_discovery_source_names_cn(values: list[Any]) -> str:
    names = []
    for item in values:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            names.append(name)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "、".join(names)


def _format_hub_skill_name_list_cn(values: list[Any], *, limit: int = 6) -> str:
    names = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not names:
        return ""
    preview = "、".join(names[:limit])
    if len(names) > limit:
        preview += _runtime_text("list_more_suffix")
    return preview


def _build_hub_skill_install_reply_cn(ctx: SubAgentContext) -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    _default_skill_name = _runtime_text("default_skill_name")
    name = str(
        detail.get("name")
        or _infer_hub_skill_name_from_task(
            ctx.task,
            markers=("安装 hub 技能", "安装技能", "安装skill"),
            default_name=_default_skill_name,
        )
    ).strip() or _default_skill_name
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    activated = bool(detail.get("activated"))
    repair_attempted = bool(detail.get("repair_attempted"))
    repair_succeeded = bool(detail.get("repair_succeeded"))
    repair_actions = int(detail.get("repair_actions") or 0)
    active_skill_names = list(detail.get("active_skill_names") or [])
    if not success:
        parts = [_mapping_text(templates, "install_failure_intro", name=name)]
        if message:
            parts.append(_mapping_text(templates, "install_failure_reason", message=message))
        return "".join(parts)
    parts = [_mapping_text(templates, "install_success_intro", name=name)]
    if activated:
        parts.append(_mapping_text(templates, "install_success_activated"))
    else:
        parts.append(_mapping_text(templates, "install_success_pending_activation"))
    preview = _format_hub_skill_name_list_cn(active_skill_names)
    if preview:
        parts.append(_mapping_text(templates, "install_success_active_skills", preview=preview))
    if repair_attempted:
        if repair_succeeded:
            if repair_actions > 0:
                parts.append(_mapping_text(templates, "install_repair_success_count", repair_actions=repair_actions))
            else:
                parts.append(_mapping_text(templates, "install_repair_success_generic"))
        else:
            parts.append(_mapping_text(templates, "install_repair_failed"))
    elif message and message not in {"installed ok", "install failed"}:
        if not (message.startswith("-") or "Installed " in message or "->" in message):
            parts.append(_mapping_text(templates, "install_message_suffix", message=message))
    return "".join(parts)


def _build_hub_skill_search_reply_cn(ctx: SubAgentContext) -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    _default_skill_query = _runtime_text("default_skill_query")
    query = str(
        detail.get("query")
        or payload.get("query")
        or _infer_hub_skill_name_from_task(
            ctx.task,
            markers=("搜索技能", "查找技能", "搜技能"),
            default_name=_default_skill_query,
        )
    ).strip() or _default_skill_query
    registry_error = str(detail.get("registry_error") or "").strip()
    discovery_sources = list(detail.get("discovery_sources") or [])
    if total <= 0:
        if registry_error:
            source_names = _format_discovery_source_names_cn(discovery_sources)
            if source_names:
                return _mapping_text(
                    templates,
                    "search_registry_unavailable_with_sources",
                    registry_error=registry_error,
                    source_names=source_names,
                )
            return _mapping_text(templates, "search_registry_unavailable", registry_error=registry_error)
        return _mapping_text(templates, "search_empty", query=query)
    previews: list[str] = []
    for item in skills[:3]:
        row = dict(item or {})
        _unnamed_skill_name = _runtime_text("unnamed_skill_name")
        name = str(row.get("name") or _unnamed_skill_name).strip() or _unnamed_skill_name
        description = str(row.get("description") or "").strip()
        author = str(row.get("author") or "").strip()
        summary = name
        if description:
            summary += f"：{description}"
        if author:
            summary += _runtime_text("author_suffix", author=author)
        previews.append(summary)
    parts = [_mapping_text(templates, "search_intro", total=total)]
    if previews:
        parts.append(_mapping_text(templates, "search_preview_prefix") + "；".join(previews) + "。")
    return "".join(parts)


def _build_hub_skill_list_reply_cn(ctx: SubAgentContext) -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    if total <= 0:
        return _mapping_text(templates, "list_empty")
    names = [dict(item or {}).get("name") for item in skills]
    preview = _format_hub_skill_name_list_cn(names)
    if preview:
        return _mapping_text(templates, "list_with_preview", total=total, preview=preview)
    return _mapping_text(templates, "list_total_only", total=total)


def _build_hub_skill_remove_reply_cn(ctx: SubAgentContext) -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    _default_skill_name = _runtime_text("default_skill_name")
    name = str(
        detail.get("name")
        or _infer_hub_skill_name_from_task(
            ctx.task,
            markers=("卸载技能", "删除技能"),
            default_name=_default_skill_name,
        )
    ).strip() or _default_skill_name
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    if success:
        if message and message not in {"removed", "remove failed"}:
            return _mapping_text(templates, "remove_success_with_message", name=name, message=message)
        return _mapping_text(templates, "remove_success", name=name)
    parts = [_mapping_text(templates, "remove_failure_intro", name=name)]
    if message:
        parts.append(_mapping_text(templates, "remove_failure_reason", message=message))
    return "".join(parts)


def _build_hub_skill_reply_cn(ctx: SubAgentContext) -> str | None:
    agent_id = str(ctx.agent_id or "").strip()
    if agent_id == "skill:hub_skill_install":
        return _build_hub_skill_install_reply_cn(ctx)
    if agent_id == "skill:hub_skill_search":
        return _build_hub_skill_search_reply_cn(ctx)
    if agent_id == "skill:hub_skill_list":
        return _build_hub_skill_list_reply_cn(ctx)
    if agent_id == "skill:hub_skill_remove":
        return _build_hub_skill_remove_reply_cn(ctx)
    return None


def _build_deterministic_sub_agent_reply(
    ctx: SubAgentContext | None,
    *,
    render_target_lang: str,
) -> str | None:
    if ctx is None:
        return None
    agent_id = str(ctx.agent_id or "").strip()
    if render_target_lang != "zh":
        return None
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx)
    if agent_id.startswith("skill:hub_skill_"):
        return _build_hub_skill_reply_cn(ctx)
    return None


def _build_chat_agent_quality_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    emotion_context: Dict[str, Any] | None,
    render_target_lang: str,
    response_policy: Any = None,
    prompt_runtime_payload: Mapping[str, Any] | None = None,
) -> str:
    return _build_minimal_chat_fallback_reply(
        user_message=user_message,
        sub_agent_ctx=sub_agent_ctx,
        render_target_lang=render_target_lang,
    )


def _build_chat_agent_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    render_target_lang: str,
) -> str:
    return _build_minimal_chat_fallback_reply(
        user_message=user_message,
        sub_agent_ctx=sub_agent_ctx,
        render_target_lang=render_target_lang,
    )


def _build_installed_capabilities_reply(*, render_target_lang: str) -> str:
    templates = _load_template_mapping("chat_agent.capabilities.reply_templates")
    if render_target_lang == "ja":
        return _mapping_text(templates, "ja_intro")

    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        from host_brain.app.agents.tool_registry import get_tools_for_agent  # noqa: PLC0415
        from host_brain.app.sub_agent_protocol import get_agent_capabilities  # noqa: PLC0415

        registry = get_skill_registry()
        skill_names = sorted(str(name).strip() for name in registry.list_skill_names() if str(name).strip())

        agent_summaries: list[str] = []
        for agent_id, desc in get_agent_capabilities().items():
            tools = get_tools_for_agent(agent_id, allow_side_effects=False)
            if not tools:
                continue
            tool_ids = [str(tool.tool_id or "").strip() for tool in tools if str(tool.tool_id or "").strip()]
            preview = "、".join(tool_ids[:4])
            if len(tool_ids) > 4:
                preview += _runtime_text("list_more_suffix")
            agent_summaries.append(
                _mapping_text(
                    templates,
                    "agent_summary",
                    agent_id=agent_id,
                    desc=desc,
                    preview=preview,
                )
            )

        parts: list[str] = [_mapping_text(templates, "zh_intro")]
        if agent_summaries:
            parts.extend(agent_summaries[:6])
        if skill_names:
            preview_skills = "、".join(skill_names[:10])
            if len(skill_names) > 10:
                preview_skills += _runtime_text("list_more_suffix")
            parts.append(_mapping_text(templates, "skill_suffix", preview_skills=preview_skills))
        parts.append(_mapping_text(templates, "cta"))
        return "\n".join(parts)
    except Exception:
        return _mapping_text(templates, "fallback")


def _should_buffer_stream_output(
    *,
    emotion_context: Any,
    bond_summary: Any,
    response_policy: Any = None,
) -> bool:
    """高风险对话缓冲判断。"""
    if _response_policy_requires_buffering(response_policy):
        return True
    env_key = "HOST_STREAM_REFLECTION_BUFFER_ENABLED"
    raw = str(os.environ.get(env_key, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415
        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染（P1 改造：不再用正则拼接，统一走 ChatAgent 人格渲染）
# ---------------------------------------------------------------------------

def _build_sub_agent_context_prompt(ctx: SubAgentContext) -> str:
    """将子 Agent 结果转换为注入 ChatAgent prompt 的上下文块。

    Phase XV 核心改造：子 Agent 结果不再直接输出给用户，
    而是作为 context 注入 ChatAgent，由 ChatAgent 用夏目蓝语气重新组织。
    """
    if not ctx:
        return ""

    templates = _load_template_mapping("chat_agent.sub_agent_context.prompt_templates")
    agent_id = str(ctx.agent_id or "").strip()
    task = str(ctx.task or "").strip()
    result_text = str(ctx.result_text or "").strip()[:2000]

    if not ctx.success or ctx.error:
        recovery_hint = (
            _runtime_text(
                "sub_agent_recovery_hint_line",
                result_text=result_text,
            )
            if result_text
            else ""
        )
        return _mapping_text(
            templates,
            "failure",
            agent_id=agent_id,
            task=task,
            error_text=(ctx.error or _runtime_text("unknown_error")),
            recovery_hint=recovery_hint,
        )

    if not result_text:
        return ""

    if agent_id == "code_agent":
        return _mapping_text(
            templates,
            "code_agent",
            agent_id=agent_id,
            task=task,
            result_text=result_text,
        )

    if agent_id == "recent_turn_direct":
        return _mapping_text(templates, "recent_turn_direct", result_text=result_text)

    if agent_id == "phatic_fastpath":
        return _mapping_text(templates, "phatic_fastpath", result_text=result_text)

    if agent_id == "task_progress_direct":
        return _mapping_text(templates, "task_progress_direct", result_text=result_text)

    if agent_id == "plan_execution":
        return _mapping_text(templates, "plan_execution", task=task, result_text=result_text)

    return _mapping_text(
        templates,
        "default",
        agent_id=agent_id,
        task=task,
        result_text=result_text,
    )


# ---------------------------------------------------------------------------
# ChatAgent 主生成器
# ---------------------------------------------------------------------------

async def chat_agent_sse_generator(
    agent_input: ChatAgentInput,
) -> AsyncGenerator[str, None]:
    """ChatAgent SSE 生成器（Phase XV 核心）。

    职责：接收 ChatAgentInput → 构建完整人格 prompt → LLM 流式生成
          → SentenceSplitter → 反思链 → 净化 → COMMIT → 异步后处理。

    调用者：DispatchAgent（top_agent.py 改造后调用此函数）。
    """
    request_id = agent_input.request_id
    session_id = agent_input.session_id
    user_message = agent_input.user_message
    person_id = agent_input.person_id
    emotion_context = agent_input.emotion_context
    emotion_snapshot = agent_input.emotion_snapshot
    bond_summary = agent_input.bond_summary
    recall_slot_text = agent_input.recall_slot_text
    retrieval_evidence_items = agent_input.retrieval_evidence_items
    retrieval_summary = agent_input.retrieval_summary
    render_target_lang = agent_input.render_target_lang  # Phase XV: 固定 "zh"
    thread_id = agent_input.thread_id
    ts_ms = agent_input.ts_ms or int(time.time() * 1000)
    intent_draft = agent_input.intent_draft or {}
    trace_stage_meta = dict(agent_input.trace_stage_meta or {})
    response_policy = _coerce_response_policy(agent_input.response_policy)
    sub_agent_ctx = agent_input.sub_agent_context
    if "chat_agent_used" not in trace_stage_meta:
        trace_stage_meta["chat_agent_used"] = True
    if "chat_mode" not in trace_stage_meta:
        trace_stage_meta["chat_mode"] = "sub_agent" if sub_agent_ctx else "direct"
    if sub_agent_ctx and not str(trace_stage_meta.get("chat_sub_agent_id") or "").strip():
        trace_stage_meta["chat_sub_agent_id"] = sub_agent_ctx.agent_id
    deterministic_reply = _build_deterministic_sub_agent_reply(sub_agent_ctx, render_target_lang=render_target_lang)

    prompt_text = ""
    system_prompt = ""
    gen_params: Dict[str, Any] = {}
    prompt_runtime_payload: Dict[str, Any] = {}

    # 确定性子结果渲染不依赖 LLM，也不需要构建完整 prompt。
    if not deterministic_reply:
        # ── 1. 构建 prompt（复用完整 top_agent_prompt 管线）──────────────
        from host_brain.app.top_agent_prompt import build_top_agent_prompt  # noqa: PLC0415

        # 如果有子 Agent 结果，注入到 user_message 作为额外上下文
        # Phase XV P1: 子 Agent 结果统一经 ChatAgent 人格渲染，不直接输出
        effective_user_message = user_message
        sub_agent_context_prompt = ""
        if sub_agent_ctx:
            sub_agent_context_prompt = _build_sub_agent_context_prompt(sub_agent_ctx)
            # 子 Agent 结果作为额外 context 注入到 intent_draft，让 prompt_builder 感知
            intent_draft = {
                **intent_draft,
                "sub_agent_context": sub_agent_context_prompt,
                "sub_agent_id": sub_agent_ctx.agent_id,
            }

        # 回复深度策略：Gateway 通道使用更短的回复
        reply_depth_hint = ""
        if agent_input.reply_depth_policy == "gateway":
            reply_depth_hint = _runtime_text(
                "gateway_reply_depth_hint",
            )

        try:
            prompt_result = build_top_agent_prompt(
                user_message=effective_user_message + reply_depth_hint,
                render_target_lang=render_target_lang,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                retrieval_summary=retrieval_summary,
                retrieval_evidence_items=retrieval_evidence_items,
                person_id=person_id,
                session_id=session_id,
                request_id=request_id,
                thread_id=thread_id,
                bond_summary=bond_summary,
                recall_slot_text=recall_slot_text if recall_slot_text else None,
                extra_context_prompt=sub_agent_context_prompt,
                include_runtime_payload=True,
            )
            if isinstance(prompt_result, tuple) and len(prompt_result) >= 4:
                prompt_text, system_prompt, gen_params, raw_prompt_runtime_payload = prompt_result[:4]
                prompt_runtime_payload = dict(raw_prompt_runtime_payload or {}) if isinstance(raw_prompt_runtime_payload, Mapping) else {}
            else:
                prompt_text, system_prompt, gen_params = prompt_result
                prompt_runtime_payload = {}
            if not response_policy:
                response_policy = _coerce_response_policy(prompt_runtime_payload.get("response_policy"))
        except Exception as _pb_exc:
            _logger.warning("[%s] ChatAgent prompt_builder failed: %s", request_id, _pb_exc)
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "REJECT",
                "reason_code": "PROMPT_BUILD_FAILED", "request_id": request_id,
                "narrative": resolve_prompt_template_text("chat_agent.minimal_fallback.prompt_build_failed"),
            })
            return

    # ── 2. 解析 LLM 运行时（ChatAgent 专用配置）─────────────────────
    if deterministic_reply:
        llm_runtime = {
            "available": True,
            "provider_name": "host",
            "model_id": "deterministic-plan-renderer",
            "timeout_ms": 0,
            "llm_api_key": "",
            "base_url_override": "",
        }
    else:
        llm_runtime = agent_input.llm_runtime

        def _schedule_llm_unavailable_post_process(
            fallback_narrative: str,
            *,
            provider_name: str = "fallback",
            model_id: str = "llm-unavailable-fallback",
        ) -> None:
            try:
                from host_brain.app.top_agent import _async_post_process  # noqa: PLC0415

                total_latency_ms = int(time.time() * 1000) - ts_ms
                asyncio.create_task(
                    _async_post_process(
                        request_id=request_id,
                        person_id=person_id,
                        session_id=session_id,
                        user_message=user_message,
                        narrative=fallback_narrative,
                        emotion_context=emotion_context,
                        bond_summary=bond_summary,
                        ts_ms=ts_ms,
                        render_target_lang=render_target_lang,
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                        first_token_ms=None,
                        provider_name=provider_name,
                        model_id=model_id,
                        buffer_stream=False,
                        delegated_agents=[],
                        retrieval_summary=(
                            agent_input.retrieval_summary
                            or {"status": "SKIPPED", "reason_code": "LLM_UNAVAILABLE"}
                        ),
                        gov_decision={"decision": "APPROVE"},
                        pii_ingress_stats={},
                        pii_egress_stats={},
                        retrieval_evidence_items=list(agent_input.retrieval_evidence_items or []),
                        motive_state=prompt_runtime_payload.get("motive_state"),
                        response_policy=response_policy,
                        trace_stage_meta=trace_stage_meta,
                    )
                )
            except Exception as _pp_exc:
                _logger.debug("[%s] ChatAgent llm unavailable post_process failed: %s", request_id, _pp_exc)

        def _build_llm_unavailable_fallback_narrative() -> str:
            fallback_narrative = _build_chat_agent_quality_fallback_reply(
                user_message=user_message,
                sub_agent_ctx=sub_agent_ctx,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                response_policy=response_policy,
                prompt_runtime_payload=prompt_runtime_payload,
            )
            return _apply_response_policy_hard_guard(
                fallback_narrative,
                response_policy,
                user_message=user_message,
                bond_stage=str((bond_summary or {}).get("stage") or ""),
            )

        if llm_runtime is None:
            try:
                from host_brain.app.graph_config_builder import (  # noqa: PLC0415
                    resolve_host_llm_runtime_async,
                    resolve_chat_agent_llm_runtime,
                )
                base_runtime = await resolve_host_llm_runtime_async(
                    rollout_key=session_id,
                    scene_type="CHAT",
                )
                llm_runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
            except Exception as _rt_exc:
                _logger.warning("[%s] ChatAgent llm_runtime resolve failed: %s", request_id, _rt_exc)
                fallback_narrative = _build_llm_unavailable_fallback_narrative()
                _schedule_llm_unavailable_post_process(fallback_narrative)
                yield _sse({
                    "phase": "COMMIT", "done": True, "decision": "REJECT",
                    "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                    "narrative": fallback_narrative,
                })
                return

        if not llm_runtime.get("available"):
            fallback_narrative = _build_llm_unavailable_fallback_narrative()
            _schedule_llm_unavailable_post_process(
                fallback_narrative,
                provider_name=str(llm_runtime.get("provider_name") or "fallback"),
                model_id=str(llm_runtime.get("model_id") or "llm-unavailable-fallback"),
            )
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                "narrative": fallback_narrative,
            })
            return

    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    base_url = str(llm_runtime.get("base_url_override") or "").strip() or None

    # ChatAgent 专属 gen_params（高温度，自然流畅）
    agent_gen_params = dict(gen_params or {})
    agent_overrides = llm_runtime.get("agent_gen_params") or {}
    agent_gen_params.update({
        k: v for k, v in agent_overrides.items()
        if v is not None
    })

    # ── 3. 流式生成 + SentenceSplitter ──────────────────────────────
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake  # noqa: PLC0415
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

    # 诊断日志：确认 system_prompt 是否包含人设（前 150 字）
    _logger.info(
        "[%s] ChatAgent system_prompt preview: %s",
        request_id,
        (system_prompt or "")[:150].replace("\n", "↵"),
    )

    splitter = SentenceSplitter()
    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    sentence_index = 0
    llm_started_ms = int(time.time() * 1000)

    buffer_stream = _should_buffer_stream_output(
        emotion_context=emotion_context,
        bond_summary=bond_summary or None,
        response_policy=response_policy,
    )
    if deterministic_reply:
        buffer_stream = False
        provider_name = "host"
        model_id = "deterministic-plan-renderer"
        timeout_ms = 0
        api_key = ""
        base_url = None

    # Phase XV P7：分句 TTS 开关
    # suppress_edge_tts=True 时（Gateway 模式），本地 TTS 不触发
    _suppress_tts = bool(agent_input.suppress_edge_tts)
    _tts_sentence_stream_enabled = (
        not _suppress_tts
        and str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # 分句 TTS 用的 SentenceSplitter（对 text_ja 分句）
    tts_splitter = SentenceSplitter() if _tts_sentence_stream_enabled else None  # type: ignore[assignment]
    tts_sentence_index = 0
    # 追踪已渲染的 text_ja（用于增量提取）
    _tts_rendered_text = ""

    try:
        if deterministic_reply:
            full_text = str(deterministic_reply).strip()
            visible = _apply_response_policy_stream_guard(full_text, response_policy)
            rendered_text = visible
            first_token_ms = 0
            if visible:
                yield _sse({"phase": "RENDER", "chunk": visible})
                for sentence in splitter.feed(visible):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
        else:
            async for chunk in astream_llm_chunks(
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                gen_params=agent_gen_params,
                base_url_override=base_url,
            ):
                if first_token_ms is None:
                    first_token_ms = int(time.time() * 1000) - llm_started_ms

                full_text += chunk
                visible = _apply_response_policy_stream_guard(_extract_visible_text(full_text), response_policy)
                if not visible:
                    continue

                if visible.startswith(rendered_text):
                    new_chunk = visible[len(rendered_text):]
                else:
                    new_chunk = visible
                if not new_chunk:
                    continue

                rendered_text = visible

                if buffer_stream:
                    continue

                yield _sse({"phase": "RENDER", "chunk": new_chunk})

                for sentence in splitter.feed(new_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

                if _tts_sentence_stream_enabled and tts_splitter is not None:
                    _tts_full = _extract_tts_text(full_text)
                    if _tts_full and _tts_full.startswith(_tts_rendered_text):
                        _tts_new = _tts_full[len(_tts_rendered_text):]
                        _tts_rendered_text = _tts_full
                        for _tts_sentence in tts_splitter.feed(_tts_new):
                            if _tts_sentence.strip():
                                _etag = _infer_emotion_tag_for_tts(
                                    _tts_sentence,
                                    str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                                )
                                asyncio.create_task(_post_speak_sentence(
                                    text=_tts_sentence,
                                    utterance_id=f"utt_{request_id}_{tts_sentence_index:03d}",
                                    emotion_tag=_etag,
                                    request_id=request_id,
                                    session_id=session_id,
                                ))
                                tts_sentence_index += 1

    except (TimeoutError, Exception) as _llm_exc:
        _logger.warning("[%s] ChatAgent LLM failed: %s", request_id, _llm_exc)
        if not full_text.strip():
            full_text = _build_chat_agent_quality_fallback_reply(
                user_message=user_message,
                sub_agent_ctx=sub_agent_ctx,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                response_policy=response_policy,
                prompt_runtime_payload=prompt_runtime_payload,
            )
            full_text = _apply_response_policy_hard_guard(
                full_text,
                response_policy,
                user_message=user_message,
                bond_stage=str((bond_summary or {}).get("stage") or ""),
            )
            if not buffer_stream:
                visible_fallback = _apply_response_policy_stream_guard(full_text, response_policy)
                yield _sse({"phase": "RENDER", "chunk": visible_fallback})
                for sentence in splitter.feed(visible_fallback):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

    # ── 4. 组装 narrative ────────────────────────────────────────────
    narrative = repair_utf8_latin1_mojibake(full_text) or full_text
    narrative = _extract_visible_text(narrative) or narrative

    # 提取 TTS 文本（Phase XV 双轨语言：text_ja 走语音通道）
    tts_text = _extract_tts_text(full_text)
    if _tts_sentence_stream_enabled and tts_text and tts_sentence_index == 0:
        tts_fallback_splitter = SentenceSplitter()
        for _tts_sentence in tts_fallback_splitter.feed(tts_text):
            if _tts_sentence.strip():
                _etag = _infer_emotion_tag_for_tts(
                    _tts_sentence,
                    str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                )
                asyncio.create_task(_post_speak_sentence(
                    text=_tts_sentence,
                    utterance_id=f"utt_{request_id}_{tts_sentence_index:03d}",
                    emotion_tag=_etag,
                    request_id=request_id,
                    session_id=session_id,
                ))
                tts_sentence_index += 1
    if _tts_sentence_stream_enabled and tts_sentence_index > 0:
        await asyncio.sleep(0)

    # ── 5. apply_response_intelligence（反思链 + 多模态后缀）─────────
    response_intelligence_meta: dict[str, Any] = {}
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

        narrative, response_intelligence_meta = apply_response_intelligence(
            request_id=request_id,
            user_message=user_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary or None,
            render_lang=render_target_lang,
            allow_rewrite=buffer_stream,
        )
        narrative = _apply_response_policy_hard_guard(
            narrative,
            response_policy,
            user_message=user_message,
            bond_stage=str((bond_summary or {}).get("stage") or ""),
        )

        if buffer_stream and narrative:
            rendered_text = narrative
            yield _sse({"phase": "RENDER", "chunk": narrative})
            for sentence in splitter.feed(narrative):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1
        elif narrative.startswith(rendered_text):
            postprocess_chunk = narrative[len(rendered_text):]
            if postprocess_chunk:
                rendered_text = narrative
                yield _sse({"phase": "RENDER", "chunk": postprocess_chunk})
                for sentence in splitter.feed(postprocess_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
    except Exception as _ri_exc:
        response_intelligence_meta = {
            "reflection": {"status": "DEGRADED", "reason_code": str(_ri_exc)[:100]},
        }
        _logger.debug("[%s] ChatAgent response_intelligence failed: %s", request_id, _ri_exc)

    # flush 剩余
    remainder = splitter.flush()
    if remainder:
        yield _sse({
            "phase": "SENTENCE",
            "sentence": remainder,
            "sentence_index": sentence_index,
        })
        sentence_index += 1

    # ── 6. 出口净化 ──────────────────────────────────────────────────
    narrative = _strip_internal_delegate_markers(narrative)
    narrative = _airlock_sanitize_output(narrative)
    narrative = _apply_response_policy_hard_guard(
        narrative,
        response_policy,
        user_message=user_message,
        bond_stage=str((bond_summary or {}).get("stage") or ""),
    )

    # PII 出口检查
    _pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            narrative = _apply_response_policy_hard_guard(
                narrative,
                response_policy,
                user_message=user_message,
                bond_stage=str((bond_summary or {}).get("stage") or ""),
            )
            if _pii_eg.has_pii:
                _pii_egress_stats = {
                    "detections": _pii_eg.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_eg.detections}),
                }
    except Exception:
        pass

    # ── 7. INPUT_COMPLETED 同步写入 ──────────────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms
    llm_latency_ms = int(time.time() * 1000) - llm_started_ms

    try:
        from host_brain.app.dependencies import get_ledger as _get_pre_ledger  # noqa: PLC0415
        _get_pre_ledger().log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
            "stage": "chat_agent.pre_commit",
            "detail": (
                f"source={'GATEWAY' if agent_input.reply_channel else 'CHAT_API'} "
                f"text_len={len(user_message)} reply_len={len(narrative)}"
            ),
            "payload": {
                "source": agent_input.reply_channel or "CHAT_API",
                "person_id": person_id,
                "input_preview": _build_reply_preview(user_message, limit=160),
                "reply_preview": _build_reply_preview(narrative, limit=160),
                "render_target_lang": render_target_lang,
                "sub_agent_id": sub_agent_ctx.agent_id if sub_agent_ctx else None,
            },
            "ts_ms": int(time.time() * 1000),
        })
    except Exception as _ve_exc:
        _logger.debug("[%s] pre-commit INPUT_COMPLETED failed: %s", request_id, _ve_exc)

    # ── 8. COMMIT ────────────────────────────────────────────────────
    yield _sse({
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "narrative": narrative,
        "request_id": request_id,
        "audit_refs": {
            "request_id": request_id,
            "path": f"chat_agent:sub={sub_agent_ctx.agent_id}" if sub_agent_ctx else "chat_agent:direct",
            "latency_ms": total_latency_ms,
            "first_token_ms": first_token_ms,
            "llm_latency_ms": llm_latency_ms,
            "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            "stream_buffered": buffer_stream,
            "model_id": model_id,
            "render_target_lang": render_target_lang,
            "pii_stats": {"egress": _pii_egress_stats} if _pii_egress_stats else {},
        },
    })

    # ── 9. 异步后处理（fire-and-forget）─────────────────────────────
    # 复用 top_agent._async_post_process（不动后处理逻辑）
    try:
        from host_brain.app.top_agent import _async_post_process  # noqa: PLC0415
        asyncio.create_task(
            _async_post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary=bond_summary,
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
                first_token_ms=first_token_ms,
                provider_name=provider_name,
                model_id=model_id,
                buffer_stream=buffer_stream,
                delegated_agents=(
                    [{"agent_id": sub_agent_ctx.agent_id,
                      "success": sub_agent_ctx.success,
                      "latency_ms": sub_agent_ctx.latency_ms}]
                    if sub_agent_ctx else []
                ),
                retrieval_summary=retrieval_summary,
                gov_decision={"decision": "APPROVE"},
                pii_ingress_stats={},
                pii_egress_stats=_pii_egress_stats,
                # Phase XV trace 增强：传入 LLM 输入摘要和召回证据
                llm_input_digest=(system_prompt or "")[:500] or None,
                retrieval_evidence_items=list(retrieval_evidence_items or []),
                motive_state=prompt_runtime_payload.get("motive_state"),
                response_policy=response_policy,
                trace_stage_meta=trace_stage_meta,
            )
        )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)
