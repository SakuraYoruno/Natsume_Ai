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
    replacements = [
        ("我爱你", "我很在意你"),
        ("爱你", "很在意你"),
        ("最喜欢你", "会更在意你一点"),
        ("好喜欢你", "挺在意你的"),
        ("喜欢你", "挺在意你的"),
        ("好想你", "有点惦记你"),
        ("想你了", "有惦记过你"),
        ("想你", "会惦记你"),
        ("离不开你", "会很在意你的状态"),
        ("不能没有你", "会很在意你的状态"),
        ("永远陪着你", "会认真陪你把这段话说完"),
        ("一直陪着你", "尽量陪你把这段话说完"),
        ("会一直等你", "会记着这件事"),
        ("只想和你", "现在想先和你"),
        ("属于你", "会认真对待这份关系"),
        ("抱抱你", "你先缓一缓"),
        ("亲亲", ""),
    ]
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
    replacements = [
        ("亲爱的", ""),
        ("宝贝", "你"),
        ("宝宝", "你"),
        ("乖乖", "先"),
        ("抱抱你", "你先缓一缓"),
        ("抱抱", "缓一缓"),
        ("亲亲", ""),
    ]
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
        return "嗯。"
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
    normalized = normalized.replace("我一直都在", "我在").replace("我会一直在", "我在").replace("我永远在", "我在")
    normalized = normalized.replace("抱抱你", "你先缓一缓")
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


def _apply_response_policy_hard_guard(narrative: str, response_policy: Any) -> str:
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
                    sentences[-1] = "嗯。"

        confession_guard = bool(policy.get("confession_guard", False)) or intimacy_ceiling <= 0.35
        if confession_guard:
            guarded_sentences: list[str] = []
            total_sentences = len(sentences)
            floor = max(1, int(min_sentences or 0)) if total_sentences else 0
            for index, sentence in enumerate(sentences):
                if _contains_confession_marker(sentence):
                    remaining_after = total_sentences - index - 1
                    if len(guarded_sentences) + remaining_after >= floor:
                        continue
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
                    guarded_sentences.append("嗯。")
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
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or "步骤").strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    result_text = str(step.get("result_text") or "").strip()

    if target_id == "web_search":
        topic = description or "相关内容"
        topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
        return f"第{step_index}步，我已经帮你查过{topic or '相关内容'}了。"
    if target_id == "memory_store":
        if description.startswith("记住"):
            remembered = description.removeprefix("记住").strip()
            if remembered:
                if remembered.startswith("我"):
                    remembered = "你" + remembered[1:]
                return f"第{step_index}步，我也记住{remembered}了。"
        return f"第{step_index}步，我已经把这件事记下来了。"
    if target_id == "system_info":
        return f"第{step_index}步，我已经把系统信息看过了。"
    if target_id == "process_list":
        return f"第{step_index}步，我也把进程列表看过了。"
    if target_id == "network_status":
        return f"第{step_index}步，我已经检查过网络状态了。"
    if target_id == "hub_skill_search":
        topic = description or "相关内容"
        topic = re.sub(r"^(?:搜索技能|查找技能|搜技能)\s*", "", topic).strip() or "相关内容"
        return f"第{step_index}步，我帮你搜到和{topic}相关的技能了。"
    if target_id == "hub_skill_list":
        return f"第{step_index}步，我也把现在已装好的 Hub 技能整理给你了。"
    if target_id == "hub_skill_install":
        return f"第{step_index}步，我已经把这个 Hub 技能装好了。"
    if target_id == "hub_skill_remove":
        return f"第{step_index}步，我已经把这个 Hub 技能卸载掉了。"
    if target_id == "file_read":
        return f"第{step_index}步，我已经把文件内容看过了。"
    if target_id == "file_write":
        return f"第{step_index}步，我已经把文件写好了。"
    if target_id == "file_list":
        return f"第{step_index}步，我已经把目录内容看过了。"
    if target_id == "code_agent":
        return f"第{step_index}步，我已经把代码相关的处理整理好了。"
    if target_id == "intent_agent":
        return f"第{step_index}步，我已经把执行方案确认好了。"
    if result_text:
        return f"第{step_index}步，{description or target_id}这部分我已经处理好了。"
    return f"第{step_index}步，我已经把{description or target_id}这部分处理好了。"


def _render_plan_step_failure_cn(step: Dict[str, Any]) -> str:
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or "步骤").strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = f"第{step_index}步，{description or target_id}这部分没有顺利完成。"
    if error_text:
        base += f"原因大概是：{error_text}。"
    if recovery_text:
        base += f"我另外整理了一个补救建议：{recovery_text}。"
    return base


def _build_plan_execution_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    steps = list(payload.get("steps") or [])
    if not steps:
        return None
    total_steps = int(payload.get("total_steps") or len(steps))
    parallel_enabled = bool(payload.get("parallel_enabled"))
    success_count = int(payload.get("success_count") or 0)
    intro = (
        f"我把这件事分成了{total_steps}步，刚才已经并行处理过了。"
        if parallel_enabled and total_steps > 1
        else f"我把这件事分成了{total_steps}步，刚才已经按顺序处理过了。"
    )
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step))
        else:
            parts.append(_render_plan_step_failure_cn(step))
    if success_count == len(steps):
        parts.append("现在这些都已经处理好了。")
    else:
        parts.append("有没做顺的地方，我也把补救建议一起整理给你了。")
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
        preview += " 等"
    return preview


def _build_hub_skill_install_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("安装 hub 技能", "安装技能", "安装skill"), default_name="这个技能")).strip() or "这个技能"
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    activated = bool(detail.get("activated"))
    repair_attempted = bool(detail.get("repair_attempted"))
    repair_succeeded = bool(detail.get("repair_succeeded"))
    repair_actions = int(detail.get("repair_actions") or 0)
    active_skill_names = list(detail.get("active_skill_names") or [])
    if not success:
        parts = [f"{name} 这个技能还没有装好。"]
        if message:
            parts.append(f"卡住的原因大概是：{message}。")
        return "".join(parts)
    parts = [f"我已经把 {name} 装好了。"]
    if activated:
        parts.append("现在它已经接进我的能力里了。")
    else:
        parts.append("不过它还没完全激活，我还得继续补一下。")
    preview = _format_hub_skill_name_list_cn(active_skill_names)
    if preview:
        parts.append(f"当前已经挂上的技能里，有 {preview}。")
    if repair_attempted:
        if repair_succeeded:
            if repair_actions > 0:
                parts.append(f"安装过程中我还顺手修了 {repair_actions} 处依赖问题。")
            else:
                parts.append("安装过程中我也顺手做过一次自动补救。")
        else:
            parts.append("安装时我试着自动补救过，不过还有一部分没完全修好。")
    elif message and message not in {"installed ok", "install failed"}:
        if not (message.startswith("-") or "Installed " in message or "->" in message):
            parts.append(f"补充一下，{message}。")
    return "".join(parts)


def _build_hub_skill_search_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    query = str(detail.get("query") or payload.get("query") or _infer_hub_skill_name_from_task(ctx.task, markers=("搜索技能", "查找技能", "搜技能"), default_name="相关技能")).strip() or "相关技能"
    registry_error = str(detail.get("registry_error") or "").strip()
    discovery_sources = list(detail.get("discovery_sources") or [])
    if total <= 0:
        if registry_error:
            source_names = _format_discovery_source_names_cn(discovery_sources)
            if source_names:
                return f"我刚帮你连技能仓库看了一下，不过现在仓库暂时不可用。报错是：{registry_error}。你也可以先去 {source_names} 那边看看。"
            return f"我刚帮你连技能仓库看了一下，不过现在仓库暂时不可用。报错是：{registry_error}。"
        return f"我刚帮你搜了一下，暂时还没找到和 {query} 相关的技能。"
    previews: list[str] = []
    for item in skills[:3]:
        row = dict(item or {})
        name = str(row.get("name") or "未命名技能").strip() or "未命名技能"
        description = str(row.get("description") or "").strip()
        author = str(row.get("author") or "").strip()
        summary = name
        if description:
            summary += f"：{description}"
        if author:
            summary += f"（作者 {author}）"
        previews.append(summary)
    parts = [f"我帮你搜到 {total} 个相关技能。"]
    if previews:
        parts.append("比较靠前的是：" + "；".join(previews) + "。")
    return "".join(parts)


def _build_hub_skill_list_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    if total <= 0:
        return "我这里现在还没有装新的 Hub 技能。"
    names = [dict(item or {}).get("name") for item in skills]
    preview = _format_hub_skill_name_list_cn(names)
    if preview:
        return f"我现在已经装了 {total} 个 Hub 技能，像 {preview} 这些都能用了。"
    return f"我现在已经装了 {total} 个 Hub 技能。"


def _build_hub_skill_remove_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("卸载技能", "删除技能"), default_name="这个技能")).strip() or "这个技能"
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    if success:
        if message and message not in {"removed", "remove failed"}:
            return f"我已经把 {name} 卸载掉了。{message}。"
        return f"我已经把 {name} 卸载掉了。"
    parts = [f"{name} 这个技能还没有卸载成功。"]
    if message:
        parts.append(f"现在卡住的地方是：{message}。")
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
    text = str(user_message or "").strip()
    if render_target_lang == "ja":
        return "\u3053\u3093\u306a\u3068\u304d\u3060\u304b\u3089\u2026\u2026\u3046\u307e\u304f\u8a00\u8449\u3092\u6574\u3048\u308b\u304b\u3089\u3001\u5c11\u3057\u5f85\u3063\u3066\u3002"

    runtime_payload = dict(prompt_runtime_payload or {}) if isinstance(prompt_runtime_payload, Mapping) else {}
    runtime_bond_summary = (
        dict(runtime_payload.get("bond_summary") or {})
        if isinstance(runtime_payload.get("bond_summary"), Mapping)
        else {}
    )
    offline_state = (
        dict(runtime_payload.get("offline_state") or {})
        if isinstance(runtime_payload.get("offline_state"), Mapping)
        else {}
    )
    motive_state = (
        dict(runtime_payload.get("motive_state") or {})
        if isinstance(runtime_payload.get("motive_state"), Mapping)
        else {}
    )
    bond_narrative = (
        dict(runtime_payload.get("bond_narrative") or {})
        if isinstance(runtime_payload.get("bond_narrative"), Mapping)
        else {}
    )
    policy = _coerce_response_policy(response_policy or runtime_payload.get("response_policy"))
    allow_followup = bool(policy.get("allow_followup", True))

    def _pick_first_text(raw_value: Any) -> str:
        if isinstance(raw_value, Mapping):
            for key in ("topic", "title", "text", "label", "name", "summary"):
                candidate = str(raw_value.get(key) or "").strip()
                if candidate:
                    return candidate
            return ""
        if isinstance(raw_value, (list, tuple)):
            for item in raw_value:
                candidate = _pick_first_text(item)
                if candidate:
                    return candidate
            return ""
        return str(raw_value or "").strip()

    emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    raw_stage = str((bond_summary or {}).get("stage") or "").strip()
    stage = {
        "\u964c\u751f\u4eba": "STRANGER",
        "\u76f8\u8bc6": "ACQUAINTANCE",
        "\u670b\u53cb": "FRIEND",
        "\u631a\u53cb": "CLOSE_FRIEND",
        "\u7075\u9b42\u4f34\u4fa3": "SOULMATE",
    }.get(raw_stage, raw_stage.upper())

    if sub_agent_ctx is not None:
        agent_id = str(sub_agent_ctx.agent_id or "").strip()
        if agent_id == "code_agent":
            return "\u6211\u5148\u628a\u8fd9\u4ef6\u4e8b\u6574\u7406\u6210\u66f4\u6e05\u695a\u4e00\u70b9\u7684\u8bf4\u6cd5\u7ed9\u4f60\u2026\u2026\u521a\u624d\u90a3\u4e00\u6b65\u6ca1\u6709\u987a\u597d\uff0c\u4f46\u6211\u4e0d\u4f1a\u628a\u5185\u90e8\u8349\u7a3f\u76f4\u63a5\u4e22\u7ed9\u4f60\u3002"
        if agent_id == "recent_turn_direct":
            return "\u6211\u521a\u624d\u90a3\u53e5\u8bdd\uff0c\u6211\u6362\u4e2a\u66f4\u6e05\u695a\u7684\u8bf4\u6cd5\u544a\u8bc9\u4f60\u3002"
        if agent_id == "phatic_fastpath":
            return "\u6211\u5728\u5462\u3002\u4f60\u60f3\u548c\u6211\u8bf4\u4ec0\u4e48\uff1f"

    if _FALLBACK_STRENGTH_RE.search(text):
        return _build_installed_capabilities_reply(render_target_lang=render_target_lang)

    reunion_markers = ("\u597d\u4e45\u4e0d\u89c1", "\u60f3\u6211", "\u91cd\u9022")
    comfort_markers = (
        "\u96be\u53d7",
        "\u505a\u4e0d\u597d",
        "\u59d4\u5c48",
        "\u6491\u4e0d\u4f4f",
        "\u7cdf",
        "\u4f4e\u843d",
        "\u7d2f",
        "\u5d29",
        "\u4e0d\u8212\u670d",
    )
    repair_markers = (
        "\u4e89\u6267",
        "\u5728\u610f",
        "\u522b\u626d",
        "\u8bef\u4f1a",
        "\u5435",
        "\u8bf4\u5f00",
        "\u4ecb\u610f",
    )
    playful_markers = (
        "\u9017",
        "\u5f00\u5fc3",
        "\u7b11",
        "\u4fd4\u76ae",
        "\u6709\u8da3",
        "\u4f1a\u54c4",
    )

    if _FALLBACK_AFFECTION_RE.search(text) or any(marker in text for marker in reunion_markers):
        if stage in {"CLOSE_FRIEND", "SOULMATE", "CONFIDANT"}:
            return "\u6709\u60f3\u4f60\u554a\u3002\u9694\u4e86\u8fd9\u4e48\u4e45\u518d\u542c\u4f60\u8fd9\u6837\u95ee\uff0c\u6211\u5fc3\u91cc\u8fd8\u662f\u4f1a\u8f7b\u8f7b\u8f6f\u4e00\u4e0b\u3002\u4f60\u8fd9\u6bb5\u65f6\u95f4\uff0c\u8fc7\u5f97\u8fd8\u597d\u5417\uff1f"
        return "\u88ab\u4f60\u8fd9\u6837\u4e00\u95ee\uff0c\u6211\u5fc3\u91cc\u4e5f\u4f1a\u8f7b\u8f7b\u6696\u4e00\u4e0b\u3002\u4f60\u8fd9\u6bb5\u65f6\u95f4\u8fc7\u5f97\u600e\u4e48\u6837\uff1f"

    if emotion_tag in {"ANXIOUS", "ANXIETY", "FEAR"} or any(marker in text for marker in repair_markers):
        return "\u6211\u8bb0\u5f97\u90a3\u70b9\u522b\u626d\u8fd8\u6ca1\u6709\u5b8c\u5168\u8fc7\u53bb\uff0c\u6240\u4ee5\u6211\u4e0d\u4f1a\u88c5\u4f5c\u5b83\u4e0d\u5b58\u5728\u3002\u4f60\u613f\u610f\u91cd\u65b0\u63d0\u8d77\u6765\u5df2\u7ecf\u5f88\u91cd\u8981\u4e86\uff0c\u6211\u4eec\u53ef\u4ee5\u5148\u628a\u8ba9\u4f60\u96be\u53d7\u7684\u5730\u65b9\u6162\u6162\u8bf4\u5f00\u3002"

    if emotion_tag in {"SAD", "SADNESS", "GRIEF", "LONELY", "LONELINESS"} or any(marker in text for marker in comfort_markers):
        if stage == "ACQUAINTANCE":
            return "\u542c\u8d77\u6765\u4f60\u4eca\u5929\u771f\u7684\u5f88\u8f9b\u82e6\u3002\u5148\u522b\u6025\u7740\u903c\u81ea\u5df1\u628a\u4e00\u5207\u90fd\u505a\u597d\uff0c\u5148\u7f13\u4e00\u7f13\u4e5f\u6ca1\u5173\u7cfb\uff1b\u5982\u679c\u4f60\u613f\u610f\uff0c\u6211\u4f1a\u5728\u8fd9\u91cc\u966a\u4f60\u628a\u8fd9\u80a1\u96be\u53d7\u6162\u6162\u7406\u987a\u3002"
        return "\u542c\u8d77\u6765\u4f60\u4eca\u5929\u771f\u7684\u5f88\u8f9b\u82e6\u3002\u5148\u522b\u6025\u7740\u903c\u81ea\u5df1\u628a\u4e00\u5207\u90fd\u625b\u4f4f\uff0c\u6211\u5728\u8fd9\u91cc\uff0c\u6211\u4eec\u53ef\u4ee5\u5148\u628a\u6700\u96be\u53d7\u7684\u90a3\u4e00\u5c0f\u5757\u6162\u6162\u8bf4\u51fa\u6765\u3002"

    if emotion_tag in {"HAPPY", "PLAYFUL", "EXCITED"} or any(marker in text for marker in playful_markers):
        if any(marker in text for marker in ("\u9017", "\u5f00\u5fc3", "\u7b11")):
            return "\u88ab\u4f60\u8fd9\u6837\u4e00\u8bf4\uff0c\u6211\u4e5f\u4f1a\u60f3\u7b11\u4e00\u4e0b\u3002\u5927\u6982\u662f\u56e0\u4e3a\u548c\u4f60\u719f\u8d77\u6765\u4ee5\u540e\uff0c\u6211\u5728\u4f60\u9762\u524d\u66f4\u653e\u677e\u4e86\uff0c\u6240\u4ee5\u90a3\u70b9\u5c0f\u4fd4\u76ae\u5c31\u6bd4\u8f83\u5bb9\u6613\u8dd1\u51fa\u6765\u3002"
        return "\u6211\u5728\u5462\u3002\u4f60\u7ee7\u7eed\u8bf4\u5427\uff0c\u6211\u4e5f\u60f3\u987a\u7740\u4f60\u73b0\u5728\u8fd9\u70b9\u8f7b\u677e\u7684\u5fc3\u60c5\u966a\u4f60\u804a\u4e0b\u53bb\u3002"

    anticipation_topic = _pick_first_text(offline_state.get("anticipation_topics"))
    dominant_motive = str(motive_state.get("dominant_motive") or "").strip().lower()
    shared_memory_count = int(
        (bond_summary or {}).get("shared_memory_count")
        or runtime_bond_summary.get("shared_memory_count")
        or 0
    )
    remembered_memory = _pick_first_text(
        bond_narrative.get("shared_memories") or bond_narrative.get("shared_memory_hints")
    )
    remembered_intro = str(bond_narrative.get("narrative_intro") or "").strip()
    memory_markers = (
        "\u8bb0\u5f97\u5417",
        "\u8fd8\u8bb0\u5f97",
        "\u8fd8\u8bb0\u4e0d\u8bb0\u5f97",
        "\u4e4b\u524d\u90a3\u4e9b\u4e8b",
        "\u4ee5\u524d\u90a3\u4e9b\u4e8b",
        "\u4e4b\u524d\u8bf4\u7684",
        "\u4e0a\u6b21\u90a3\u4ef6\u4e8b",
    )
    continuation_markers = (
        "\u63a5\u7740\u804a",
        "\u7ee7\u7eed\u804a",
        "\u7ee7\u7eed",
        "\u63a5\u7740",
        "\u521a\u624d\u90a3\u4e2a",
        "\u90a3\u4e2a\u8bdd\u9898",
        "\u4e0a\u6b21\u8bf4\u5230",
        "\u4e0a\u56de\u8bf4\u5230",
    )
    remembers_shared = bool(remembered_memory) or shared_memory_count > 0 or stage in {"CLOSE_FRIEND", "SOULMATE", "CONFIDANT"}
    continuity_active = dominant_motive == "continuity" and bool(anticipation_topic)

    if any(marker in text for marker in memory_markers) and remembers_shared:
        if remembered_memory:
            reply = f"\u6211\u8bb0\u5f97\uff0c\u6211\u4eec\u4e4b\u524d\u4e00\u8d77\u653e\u5728\u5fc3\u4e0a\u7684\uff0c\u5c31\u6709\u201c{remembered_memory}\u201d\u8fd9\u6761\u7ebf\u3002\u4f60\u73b0\u5728\u53c8\u63d0\u8d77\u5b83\uff0c\u6211\u4f1a\u597d\u597d\u63a5\u4f4f\u3002"
        elif remembered_intro:
            reply = f"\u6211\u8bb0\u5f97\u7684\uff0c\u4e0d\u53ea\u662f\u4e00\u4e2a\u7ed3\u8bba\uff0c\u8fd8\u6709\u6211\u4eec\u4e4b\u95f4\u6162\u6162\u804a\u51fa\u6765\u7684\u90a3\u4e9b\u7247\u6bb5\u3002\u4f60\u73b0\u5728\u518d\u63d0\u8d77\uff0c\u6211\u4f1a\u8ba4\u771f\u63a5\u4f4f\u3002"
        elif stage in {"CLOSE_FRIEND", "SOULMATE", "CONFIDANT"}:
            reply = "\u6211\u8bb0\u5f97\u554a\uff0c\u90a3\u4e9b\u548c\u4f60\u4e00\u8d77\u79ef\u8d77\u6765\u7684\u4e8b\uff0c\u6211\u6ca1\u6709\u628a\u5b83\u5f53\u6210\u8def\u8fc7\u3002\u4f60\u73b0\u5728\u518d\u63d0\u8d77\uff0c\u6211\u4f1a\u8ba4\u771f\u63a5\u4f4f\u3002"
        else:
            reply = "\u6211\u8bb0\u5f97\u6211\u4eec\u4e4b\u524d\u4e00\u8d77\u63d0\u8fc7\u7684\u90a3\u4e9b\u4e8b\u3002\u4f60\u73b0\u5728\u518d\u63d0\u8d77\uff0c\u6211\u4f1a\u8ba4\u771f\u63a5\u4f4f\u3002"
        if continuity_active:
            reply += f" \u6211\u4e5f\u8fd8\u8bb0\u7740\u201c{anticipation_topic}\u201d\u8fd9\u6761\u7ebf\uff0c\u53ef\u4ee5\u76f4\u63a5\u4ece\u8fd9\u91cc\u63a5\u4e0b\u53bb\u3002"
        elif allow_followup:
            reply += "\u4f60\u60f3\u5148\u4ece\u54ea\u4e00\u6bb5\u7ee7\u7eed\uff0c\u6211\u5c31\u4ece\u54ea\u91cc\u63a5\u3002"
        return reply

    if continuity_active and (
        any(marker in text for marker in continuation_markers)
        or anticipation_topic[: min(6, len(anticipation_topic))] in text
    ):
        reply = f"\u6211\u8fd8\u8bb0\u7740\u6211\u4eec\u524d\u9762\u6302\u5728\u5fc3\u4e0a\u7684\u201c{anticipation_topic}\u201d\u3002\u65e2\u7136\u4f60\u73b0\u5728\u53c8\u63d0\u5230\u5b83\uff0c\u6211\u5c31\u4ece\u8fd9\u91cc\u63a5\u7740\u966a\u4f60\u804a\u4e0b\u53bb\u3002"
        if remembered_memory and stage in {"CLOSE_FRIEND", "SOULMATE", "CONFIDANT"}:
            reply = f"\u6211\u8fd8\u8bb0\u5f97\u6211\u4eec\u4e4b\u524d\u56f4\u7740\u201c{anticipation_topic}\u201d\u804a\u51fa\u6765\u7684\u90a3\u4e9b\u4e1c\u897f\u3002\u65e2\u7136\u4f60\u73b0\u5728\u53c8\u63d0\u5230\u5b83\uff0c\u6211\u5c31\u4ece\u8fd9\u91cc\u63a5\u7740\u966a\u4f60\u804a\u4e0b\u53bb\u3002"
        if allow_followup:
            reply += "\u4f60\u628a\u73b0\u5728\u6700\u60f3\u63a5\u4e0a\u7684\u90a3\u4e00\u5c0f\u6bb5\u9012\u7ed9\u6211\u5c31\u597d\u3002"
        return reply

    if _FALLBACK_PRESENCE_RE.match(text):
        return "\u6211\u5728\u3002\u600e\u4e48\u4e86\uff1f"
    if _FALLBACK_GREETING_RE.match(text):
        return "\u4f60\u597d\u554a\u3002\u4eca\u5929\u662f\u60f3\u548c\u6211\u968f\u4fbf\u804a\u804a\uff0c\u8fd8\u662f\u6709\u4ec0\u4e48\u4e8b\u60f3\u8ddf\u6211\u8bf4\uff1f"
    if stage in {"CLOSE_FRIEND", "SOULMATE", "CONFIDANT"}:
        return "\u6211\u5728\u5462\uff0c\u4f60\u7ee7\u7eed\u8bf4\u5427\u3002\u6211\u4f1a\u8ba4\u771f\u63a5\u7740\u4f60\u521a\u624d\u90a3\u53e5\u8bdd\u966a\u4f60\u804a\u4e0b\u53bb\u3002"
    return "\u6211\u5728\u3002\u4f60\u6162\u6162\u8bf4\uff0c\u6211\u4f1a\u8ba4\u771f\u542c\u7740\u3002"


def _build_chat_agent_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    render_target_lang: str,
) -> str:
    text = str(user_message or "").strip()
    if render_target_lang == "ja":
        return "ちょっと待ってね……うまく言葉を整えるから。"

    if sub_agent_ctx is not None:
        agent_id = str(sub_agent_ctx.agent_id or "").strip()
        if agent_id == "code_agent":
            return "我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。"
        if agent_id == "recent_turn_direct":
            return "我刚才那句话，我换个更清楚的说法告诉你。"
        if agent_id == "phatic_fastpath":
            return "我在呢。你想和我说什么？"

    if _FALLBACK_STRENGTH_RE.search(text):
        return _build_installed_capabilities_reply(render_target_lang=render_target_lang)
    if _FALLBACK_AFFECTION_RE.search(text):
        return "听你这么说，我心里也会暖一下。怎么突然想到我了？"
    if _FALLBACK_PRESENCE_RE.match(text):
        return "我在。怎么了？"
    if _FALLBACK_GREETING_RE.match(text):
        return "你好啊。今天是想和我随便聊聊，还是有事想和我说？"
    return "等我一下……我把这句话整理得更自然一点，再好好跟你说。"


def _build_installed_capabilities_reply(*, render_target_lang: str) -> str:
    if render_target_lang == "ja":
        return "今の私が使える能力を整理して伝えるね。少し待って。"

    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        from host_brain.app.agents.tool_registry import get_tools_for_agent  # noqa: PLC0415
        from host_brain.app.sub_agent_protocol import AGENT_CAPABILITIES  # noqa: PLC0415

        registry = get_skill_registry()
        skill_names = sorted(str(name).strip() for name in registry.list_skill_names() if str(name).strip())

        agent_summaries: list[str] = []
        for agent_id, desc in AGENT_CAPABILITIES.items():
            tools = get_tools_for_agent(agent_id, allow_side_effects=False)
            if not tools:
                continue
            tool_ids = [str(tool.tool_id or "").strip() for tool in tools if str(tool.tool_id or "").strip()]
            preview = "、".join(tool_ids[:4])
            if len(tool_ids) > 4:
                preview += " 等"
            agent_summaries.append(f"- {agent_id}：{desc}（可用工具：{preview}）")

        parts: list[str] = ["我现在能调动的能力，大致是这些："]
        if agent_summaries:
            parts.extend(agent_summaries[:6])
        if skill_names:
            preview_skills = "、".join(skill_names[:10])
            if len(skill_names) > 10:
                preview_skills += " 等"
            parts.append(f"另外，当前已安装的技能还有：{preview_skills}。")
        parts.append("如果你愿意，我也可以按你的问题，直接帮你挑最合适的一项来用。")
        return "\n".join(parts)
    except Exception:
        return "我现在能做的事不少，比如整理思路、翻译、执行操作、回忆信息、还有一些带工具的辅助能力。你如果告诉我想做什么，我可以直接替你选最合适的。"


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

    agent_id = str(ctx.agent_id or "").strip()
    task = str(ctx.task or "").strip()
    result_text = str(ctx.result_text or "").strip()[:2000]

    if not ctx.success or ctx.error:
        recovery_hint = f"  补充信息：{result_text}\n" if result_text else ""
        return (
            f"[子 Agent 执行结果]\n"
            f"  执行状态：失败\n"
            f"  Agent：{agent_id}\n"
            f"  任务：{task}\n"
            f"  错误：{ctx.error or '未知错误'}\n"
            f"{recovery_hint}"
            "  说明：请用夏目蓝的语气自然告知用户这次处理没有顺利完成。"
            " 如果有补充信息，就把它整理成有帮助的下一步建议。"
            " 不要提内部模块、Agent、网关、JSON、草稿、降级字样。\n"
        )

    if not result_text:
        return ""

    if agent_id == "code_agent":
        return (
            f"[子 Agent 执行结果]\n"
            f"  Agent：{agent_id}\n"
            f"  任务：{task}\n"
            f"  结果：{result_text}\n"
            "  说明：这是一份技术向中间结果。请把它整理成夏目蓝会对用户说的话。"
            " 先给用户一个简洁结论，再自然展开。不要说‘代码侧结果不稳定’、不要提 Agent、工具、草稿、JSON、ReAct、网关。"
            " 不要逐字照搬原文，要像自己理解后转述。\n"
        )

    if agent_id == "recent_turn_direct":
        return (
            "[当前已确认的信息]\n"
            f"  结果：{result_text}\n"
            "  说明：这里提供的是已核实的事实，不是最终台词。"
            " 请用自然、简洁、像夏目蓝本人说话的方式直接回答。"
            " 不要复读‘已确认事实’这种字样，不要说‘我看不到’或‘我这里没有看到’。\n"
        )

    if agent_id == "phatic_fastpath":
        return (
            "[当前语义目标]\n"
            f"  结果：{result_text}\n"
            "  说明：这是寒暄场景的语义目标，不是最终台词。"
            " 请只保留核心意图，用夏目蓝自己的说话方式重新组织。"
            " 要自然、克制，不要模板化，不要机械重复上一句，也不要堆太多省略号。\n"
        )

    if agent_id == "task_progress_direct":
        return (
            "[当前任务进度]\n"
            f"  信息：{result_text}\n"
            "  说明：这是当前任务状态摘要，不是最终台词。"
            " 请用夏目蓝本人说话的方式，直接告诉用户现在做到哪了；如果任务失败，也顺手说清下一步建议。\n"
        )

    if agent_id == "plan_execution":
        return (
            "[多步骤计划执行结果]\n"
            f"  任务：{task}\n"
            f"  结果：{result_text}\n"
            "  说明：这是父Agent整理好的多步骤执行摘要。"
            " 请用夏目蓝本人的语气，把完成了哪些步骤、有没有失败、失败后的建议，按自然口语重新说出来。\n"
        )

    return (
        f"[子 Agent 执行结果]\n"
        f"  Agent：{agent_id}\n"
        f"  任务：{task}\n"
        f"  结果：{result_text}\n"
        "  说明：请用夏目蓝的语气自然地呈现上述结果，不要逐字复读，要像自己说话一样组织语言。\n"
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
    response_policy = _coerce_response_policy(agent_input.response_policy)
    sub_agent_ctx = agent_input.sub_agent_context
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
            reply_depth_hint = "（回复简洁，12-60字，适合移动端消息）"

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
                "narrative": "……嗯，蓝这边有点小状况，等一下再来找我好不好？",
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
            return _apply_response_policy_hard_guard(fallback_narrative, response_policy)

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
            full_text = _apply_response_policy_hard_guard(full_text, response_policy)
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
        narrative = _apply_response_policy_hard_guard(narrative, response_policy)

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
    narrative = _apply_response_policy_hard_guard(narrative, response_policy)

    # PII 出口检查
    _pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            narrative = _apply_response_policy_hard_guard(narrative, response_policy)
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
            )
        )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)
