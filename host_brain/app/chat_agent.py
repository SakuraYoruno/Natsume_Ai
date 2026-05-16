"""chat_agent.py — ChatAgent Soul 渲染管线（Phase XV）。

当前 CompiledSoul 的"说话"模块。
职责：拿到上下文→构建 prompt→流式生成→反思链→净化→COMMIT。
调用者：Executive Runtime（通过 chat_agent_sse_generator 函数）。

架构原则：
  - ChatAgent 只负责「说出来」，不做路由/调度决策。
  - 所有输入通过 ChatAgentInput 结构体传入（sub_agent_context 可带子 Agent 结果）。
  - 所有输出经完整人格管线：角色人设 + 情感 + Bond + 风格蒸馏 + 反思链。
  - 不调用 SkillLoader，不做委托判断，不感知 sys_mode 路由。

关系：
  Executive Runtime  ─────────▶  ChatAgent
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

from host_brain.codex_runtime.skill_ids import is_codex_runtime_skill
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch C：业务路径统一降级
_logger = logging.getLogger("Natsume.chat_agent")

_FALLBACK_GREETING_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_FALLBACK_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_FALLBACK_AFFECTION_RE = re.compile(r"(想你|好想你|我想你了|想你啦|惦记你)")
_FALLBACK_STRENGTH_RE = re.compile(r"(特长|擅长|你会什么|你能做什么|你会做什么)")
_OPEN_APP_VERB_RE = re.compile(r"(打开|启动|运行|开一下|帮我开|替我开|launch|open)", re.IGNORECASE)
_OPEN_APP_HINT_RE = re.compile(
    r"(本地|应用|软件|程序|app|酷狗|kugou|qq音乐|网易云|spotify|浏览器|微信|qq|音乐)",
    re.IGNORECASE,
)

_DELEGATE_MARKER_RE = re.compile(r"\[DELEGATE:[^\]]+\]", re.IGNORECASE)
_ACK_CLOSE_STAGES = {"COMPANION", "INTIMATE", "FAMILY"}
_ACK_WARM_STAGES = {"ALLY", "TRUSTED", "FRIEND"}
_ACK_LOW_ENERGY_TAGS = {"SAD", "HURT", "ANGRY", "FEAR", "EXHAUSTED", "NUMB"}


def _contains_cjk_or_kana(text: str) -> bool:
    return any(
        ("\u4e00" <= ch <= "\u9fff")
        or ("\u3040" <= ch <= "\u30ff")
        for ch in str(text or "")
    )


def repair_utf8_latin1_mojibake(text: str) -> str:
    raw = str(text or "")
    if not raw or _contains_cjk_or_kana(raw):
        return raw
    try:
        repaired = raw.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return raw
    return repaired if _contains_cjk_or_kana(repaired) else raw


def _build_prefrontal_context_block(*, task: str, tone_hint: str) -> str:
    _ = task, tone_hint
    return ""


def _resolve_minimal_fallback_text(*, kind: str, render_target_lang: str) -> str:
    _ = kind, render_target_lang
    return ""



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
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})

    # ── 子 Agent 执行结果（执行路径时由 Executive Runtime 填入）──
    sub_agent_context: Optional[SubAgentContext] = None

    # ── 通道参数 ──
    render_target_lang: str = "zh"   # legacy language hint；真实渲染由 render_profile/response_form 决定
    render_profile: str = ""         # zh_text=中文文字；ja_voice=日语语音
    response_form: str = ""          # canonical output form; render_profile is derived from this
    reply_channel: Optional[str] = None   # GATEWAY / ASR / WEB 等
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    reply_depth_policy: str = "normal"    # legacy metadata only; transport source must not alter text generation

    # ── 链路追踪 ──
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)
    trace_stage_meta: Dict[str, Any] = field(default_factory=dict)

    # ── LLM 运行时（由调用者 resolve 后传入，避免重复 resolve）──
    llm_runtime: Optional[Dict[str, Any]] = None

    # ── 行为约束策略（可由上游直接传入，缺省时从 prompt runtime_payload 回填）──
    render_policy: Dict[str, Any] = field(default_factory=dict)
    surface_task_control: Dict[str, Any] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    multimodal_observations: Dict[str, Any] = field(default_factory=dict)
    attachment_summary: str = ""
    eval_history_turns: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数（从 cortex_orchestrator 链路复用）
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _surface_main_no_reply_event(
    *,
    request_id: str,
    reason_code: str,
    ts_ms: int,
    error: str = "",
) -> str:
    audit_refs: dict[str, Any] = {
        "request_id": request_id,
        "path": "chat_agent:surface_main_chain",
        "surface_main_chain_required": True,
        "surface_main_chain_used": False,
        "reply_source": "surface_main_chain",
        "latency_ms": max(0, int(time.time() * 1000) - int(ts_ms or 0)),
    }
    if error:
        audit_refs["error"] = error
    return _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "NO_REPLY",
            "reason_code": reason_code,
            "narrative": "",
            "request_id": request_id,
            "audit_refs": audit_refs,
        }
    )


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _should_cut_stream_after_policy_cap(
    *,
    render_policy: Any,
    rendered_text: str,
    visible_text: str,
) -> bool:
    policy, max_chars, max_sentences, _min_sentences = _extract_render_policy_limits(render_policy)
    if not policy:
        return False
    if not rendered_text or not visible_text:
        return False
    if visible_text != rendered_text:
        return False

    if max_chars > 0 and len(visible_text) >= max_chars:
        return True

    if max_sentences > 0:
        visible_sentences = _split_text_into_sentences(visible_text)
        if len(visible_sentences) >= max_sentences:
            return True

    return False


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


def _should_schedule_commit_tts_fallback(
    *,
    narrative: str,
    tts_sentence_index: int,
    suppress_tts: bool,
    reply_channel: str | None,
    voice_outputs_enabled: bool,
) -> bool:
    """Decide whether ChatAgent should schedule COMMIT-time TTS fallback.

    Gateway requests already have a dedicated voice delivery path in input_routes,
    so scheduling the legacy commit fallback there will replay the same reply twice.
    """
    if not str(narrative or "").strip():
        return False
    if not voice_outputs_enabled:
        return False

    channel = str(reply_channel or "").strip().upper()
    if suppress_tts and channel:
        return False

    return bool(suppress_tts) or int(tts_sentence_index) == 0


def _should_enable_edge_voice_outputs(*, reply_channel: str | None) -> bool:
    channel = str(reply_channel or "").strip().upper()
    if channel:
        return channel not in {"CHAT_API", "WEB", "HTTP", "API"}
    enabled_raw = str(os.environ.get("HOST_CHAT_API_EDGE_VOICE_ENABLED") or "false").strip().lower()
    return enabled_raw in {"1", "true", "yes", "on"}


def _coerce_render_policy(raw_policy: Any) -> Dict[str, Any]:
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


def _extract_render_policy_limits(render_policy: Any) -> tuple[Dict[str, Any], int, int, int]:
    policy = _coerce_render_policy(render_policy)
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


def _coerce_render_policy_score(policy: Mapping[str, Any], key: str, *, default: float = 1.0) -> float:
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
    _ = user_message, sub_agent_ctx, render_target_lang
    return ""


def _load_template_replacement_pairs(
    template_id: str,
    *,
    render_target_lang: str = "zh",
) -> list[tuple[str, str]]:
    _ = template_id, render_target_lang
    return []


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
    with soft_fail(
        "chat_agent.template_format_map",
        fallback=text,
        expected_exc=(KeyError, IndexError, ValueError),
    ) as _fmt_box:
        _fmt_box[0] = text.format_map(payload)
    return _fmt_box[0]


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    _ = template_id
    return {}


def _normalize_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _resolve_localized_template_value(value: Any, *, render_target_lang: str | None = None) -> Any:
    if isinstance(value, Mapping):
        keys = {str(k).strip() for k in value.keys()}
        if keys.issubset({"zh", "ja", "default"}) and ("zh" in keys or "ja" in keys):
            lang_key = _normalize_template_lang(render_target_lang)
            return value.get(lang_key) or value.get("zh") or value.get("ja") or value.get("default")
    return value


def _resolve_template_text_localized(template_id: str, *, render_target_lang: str | None = None) -> str:
    _ = template_id, render_target_lang
    return ""


def _mapping_section(mapping: Mapping[str, Any], key: str, *, render_target_lang: str | None = None) -> dict[str, Any]:
    raw = _resolve_localized_template_value(mapping.get(key), render_target_lang=render_target_lang)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    render_target_lang: str | None = None,
    **kwargs: Any,
) -> str:
    raw = _resolve_localized_template_value(mapping.get(key, default), render_target_lang=render_target_lang)
    return _format_template_text(raw, **kwargs)


def _runtime_text(
    key: str,
    *,
    render_target_lang: str | None = None,
    **kwargs: Any,
) -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
    return _mapping_text(templates, key, render_target_lang=render_target_lang, **kwargs)


def _looks_like_open_app_request(user_message: str) -> bool:
    normalized = re.sub(r"\s+", "", str(user_message or "")).strip()
    if not normalized:
        return False
    return bool(_OPEN_APP_VERB_RE.search(normalized) and _OPEN_APP_HINT_RE.search(normalized))


def render_quick_gateway_ack_text(
    *,
    user_message: str = "",
    bond_stage: str = "FRIEND",
    emotion_tag: str = "NEUTRAL",
    sender_name: str | None = None,
) -> str:
    """Render a low-latency persona ACK for gateway immediate feedback."""

    templates = _load_template_mapping("chat_agent.runtime_texts")
    stage = str(bond_stage or "FRIEND").strip().upper() or "FRIEND"
    etag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    clean_sender = str(sender_name or "").strip()

    if _looks_like_open_app_request(user_message):
        text = _mapping_text(templates, "quick_ack_open_app", sender_name=clean_sender)
    elif etag in _ACK_LOW_ENERGY_TAGS:
        text = _mapping_text(templates, "quick_ack_low_energy", sender_name=clean_sender)
    elif stage in _ACK_CLOSE_STAGES:
        text = _mapping_text(templates, "quick_ack_close", sender_name=clean_sender)
    elif stage in _ACK_WARM_STAGES:
        text = _mapping_text(templates, "quick_ack_warm", sender_name=clean_sender)
    else:
        text = _mapping_text(templates, "quick_ack_default", sender_name=clean_sender)

    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_ack_default", sender_name=clean_sender)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_ack_fallback", sender_name=clean_sender)

    normalized = re.sub(r"\s+", " ", str(text)).strip()
    if len(normalized) > 60:
        normalized = normalized[:57].rstrip() + "..."
    _ = user_message
    return normalized


def _format_elapsed_compact(elapsed_ms: int) -> str:
    total_seconds = max(0, int(elapsed_ms // 1000))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def render_quick_gateway_progress_text(
    *,
    status: str = "UNKNOWN",
    target_id: str = "",
    progress_message: str = "",
    result_text: str = "",
    elapsed_ms: int = 0,
    bond_stage: str = "FRIEND",
    emotion_tag: str = "NEUTRAL",
    sender_name: str | None = None,
) -> str:
    """Render a low-latency persona progress reply for gateway status query."""

    templates = _load_template_mapping("chat_agent.runtime_texts")
    normalized_status = str(status or "UNKNOWN").strip().upper() or "UNKNOWN"
    _ = str(bond_stage or "FRIEND").strip().upper()
    _ = str(emotion_tag or "NEUTRAL").strip().upper()
    elapsed = _format_elapsed_compact(int(elapsed_ms or 0))
    target = str(target_id or "").strip() or _mapping_text(templates, "default_task_target")
    progress = str(progress_message or "").strip()
    result = str(result_text or "").strip()
    clean_sender = str(sender_name or "").strip()

    if normalized_status in {"RUNNING", "RECOVERING"}:
        text = _mapping_text(
            templates,
            "quick_progress_running",
            sender_name=clean_sender,
            target_id=target,
            elapsed=elapsed,
            progress_message=progress,
            result_text=result,
        )
    elif normalized_status == "COMPLETED":
        text = _mapping_text(
            templates,
            "quick_progress_completed",
            sender_name=clean_sender,
            target_id=target,
            elapsed=elapsed,
            progress_message=progress,
            result_text=result,
        )
    elif normalized_status == "FAILED":
        text = _mapping_text(
            templates,
            "quick_progress_failed",
            sender_name=clean_sender,
            target_id=target,
            elapsed=elapsed,
            progress_message=progress,
            result_text=result,
        )
    elif normalized_status in {"NONE", "EMPTY"}:
        text = _mapping_text(templates, "quick_progress_missing", sender_name=clean_sender)
    else:
        text = _mapping_text(
            templates,
            "quick_progress_default",
            sender_name=clean_sender,
            target_id=target,
            elapsed=elapsed,
            progress_message=progress,
            result_text=result,
        )

    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_progress_fallback", sender_name=clean_sender)
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    if len(normalized) > 120:
        normalized = normalized[:117].rstrip() + "..."
    return normalized


def render_quick_gateway_realign_text(
    *,
    sender_name: str | None = None,
    bond_stage: str = "FRIEND",
    emotion_tag: str = "NEUTRAL",
) -> str:
    """Render a low-latency persona notice when execution needs realignment."""

    templates = _load_template_mapping("chat_agent.runtime_texts")
    _ = str(bond_stage or "FRIEND").strip().upper()
    _ = str(emotion_tag or "NEUTRAL").strip().upper()
    clean_sender = str(sender_name or "").strip()

    text = _mapping_text(templates, "quick_realign_notice", sender_name=clean_sender)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_realign_default", sender_name=clean_sender)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_realign_fallback", sender_name=clean_sender)
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    if len(normalized) > 80:
        normalized = normalized[:77].rstrip() + "..."
    return normalized


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
        "愛してる", "好き", "大好き", "会いたい", "抱きしめたい", "ずっと一緒", "そばにいたい",
    )
    return any(marker in normalized for marker in markers)


def _contains_intimacy_marker(sentence: str) -> bool:
    normalized = str(sentence or "")
    if _contains_confession_marker(normalized):
        return True
    markers = (
        "亲爱的", "宝贝", "宝宝", "乖乖", "抱抱", "亲亲",
        "喜欢你", "好喜欢你", "最喜欢你", "想你", "好想你", "想你了",
        "愛してる", "好き", "大好き", "会いたい", "抱きしめる", "ぎゅっ", "かわいい子",
    )
    return any(marker in normalized for marker in markers)


def _soften_confession_sentence(sentence: str, *, render_target_lang: str = "zh") -> str:
    softened = str(sentence or "").strip()
    if not softened:
        return ""
    replacements = _load_template_replacement_pairs(
        "chat_agent.render_policy.confession_replacements",
        render_target_lang=render_target_lang,
    )
    for source, target in replacements:
        softened = softened.replace(source, target)
    softened = re.sub(r"\s+", " ", softened).strip()
    softened = softened.rstrip("，,、；;：: ").strip()
    if softened and softened[-1] not in ".。！？!?":
        softened += "。"
    return softened


def _soften_intimacy_sentence(sentence: str, *, render_target_lang: str = "zh") -> str:
    softened = str(sentence or "").strip()
    if not softened:
        return ""
    if _contains_confession_marker(softened):
        softened = _soften_confession_sentence(softened, render_target_lang=render_target_lang)
    replacements = _load_template_replacement_pairs(
        "chat_agent.render_policy.intimacy_replacements",
        render_target_lang=render_target_lang,
    )
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


def _soften_humor_tone(text: str, *, render_target_lang: str = "zh") -> str:
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
        return _resolve_template_text_localized(
            "chat_agent.render_policy.min_sentence_placeholder",
            render_target_lang=render_target_lang,
        )
    if normalized[-1] not in ".。！？!?":
        normalized += "。"
    return normalized


def _soften_withdrawn_tone(text: str, *, render_target_lang: str = "zh") -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"[~～]+", "", normalized)
    normalized = re.sub(r"(?:!|！){2,}", "。", normalized)
    normalized = re.sub(r"(?:\?|？){2,}", "？", normalized)
    for source, target in _load_template_replacement_pairs(
        "chat_agent.render_policy.withdrawn_replacements",
        render_target_lang=render_target_lang,
    ):
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


def _apply_render_policy_stream_guard(
    text: str,
    render_policy: Any,
    *,
    render_target_lang: str = "zh",
) -> str:
    normalized = str(text or "").strip()
    policy, max_chars, max_sentences, _min_sentences = _extract_render_policy_limits(render_policy)
    if not normalized or not policy:
        return normalized
    if max_sentences > 0:
        normalized = _truncate_text_to_sentence_limit(normalized, max_sentences=max_sentences)
    if max_chars > 0:
        normalized = _truncate_text_to_char_limit(normalized, max_chars=max_chars)
    return normalized.strip()


def _render_policy_explicit_buffering_decision(render_policy: Any) -> bool | None:
    policy = _coerce_render_policy(render_policy)
    if not policy:
        return None
    has_explicit = "force_buffering" in policy or "requires_validation" in policy
    if not has_explicit:
        return None
    if bool(policy.get("force_buffering") or False):
        return True
    if bool(policy.get("requires_validation") or False):
        return True
    return False


def _render_policy_requires_buffering(render_policy: Any) -> bool:
    explicit_decision = _render_policy_explicit_buffering_decision(render_policy)
    if explicit_decision is not None:
        return explicit_decision
    policy, _max_chars, _max_sentences, _min_sentences = _extract_render_policy_limits(render_policy)
    if not policy:
        return False
    intimacy_ceiling = _coerce_render_policy_score(policy, "intimacy_ceiling")
    humor_ceiling = _coerce_render_policy_score(policy, "humor_ceiling")
    return any([
        not bool(policy.get("allow_followup", True)),
        not bool(policy.get("allow_topic_shift", True)),
        bool(policy.get("confession_guard", False)),
        bool(policy.get("withdrawal_guard", False)),
        bool(policy.get("should_be_withdrawn", False)),
        intimacy_ceiling <= 0.55,
        humor_ceiling <= 0.35,
    ])


def _apply_render_policy_hard_guard(
    narrative: str,
    render_policy: Any,
    *,
    user_message: str = "",
    bond_stage: str = "",
    render_target_lang: str = "zh",
) -> str:
    normalized = str(narrative or "").strip()
    policy, max_chars, max_sentences, min_sentences = _extract_render_policy_limits(render_policy)
    if not normalized or not policy:
        return normalized

    intimacy_ceiling = _coerce_render_policy_score(policy, "intimacy_ceiling")
    humor_ceiling = _coerce_render_policy_score(policy, "humor_ceiling")

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
                    sentences[-1] = _resolve_template_text_localized(
                        "chat_agent.render_policy.min_sentence_placeholder",
                        render_target_lang=render_target_lang,
                    )

        confession_guard = bool(policy.get("confession_guard", False)) or intimacy_ceiling <= 0.35
        if confession_guard:
            guarded_sentences: list[str] = []
            for sentence in sentences:
                if _contains_confession_marker(sentence):
                    sentence = _soften_confession_sentence(
                        sentence,
                        render_target_lang=render_target_lang,
                    )
                if sentence:
                    guarded_sentences.append(sentence)
            sentences = guarded_sentences or sentences

        if intimacy_ceiling <= 0.55:
            guarded_sentences = []
            total_sentences = len(sentences)
            floor = max(1, int(min_sentences or 0)) if total_sentences else 0
            for index, sentence in enumerate(sentences):
                if _contains_intimacy_marker(sentence):
                    sentence = _soften_intimacy_sentence(
                        sentence,
                        render_target_lang=render_target_lang,
                    )
                if sentence:
                    guarded_sentences.append(sentence)
                    continue
                remaining_after = total_sentences - index - 1
                if len(guarded_sentences) + remaining_after < floor:
                    guarded_sentences.append(
                        _resolve_template_text_localized(
                            "chat_agent.render_policy.min_sentence_placeholder",
                            render_target_lang=render_target_lang,
                        )
                    )
            sentences = guarded_sentences or sentences

        normalized = "".join(str(sentence).strip() for sentence in sentences if str(sentence).strip()).strip()

    if humor_ceiling <= 0.35:
        normalized = _soften_humor_tone(
            normalized,
            render_target_lang=render_target_lang,
        )

    if bool(policy.get("withdrawal_guard", False)) or bool(policy.get("should_be_withdrawn", False)):
        normalized = _soften_withdrawn_tone(
            normalized,
            render_target_lang=render_target_lang,
        )

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


def _extract_visible_text(raw: str, *, render_target_lang: str = "zh") -> str:
    """从 LLM 原始输出中提取可见文本（支持 JSON / fenced JSON 包装格式）。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?is)<think\b[^>]*>.*?</think>", "", text).strip()
    text = re.sub(r"(?is)^<think\b[^>]*>.*", "", text).strip()
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
                    lang = _normalize_template_lang(render_target_lang)
                    if lang == "ja":
                        return str(narrative.get("text_ja") or narrative.get("text") or narrative.get("text_zh") or "").strip()
                    return str(narrative.get("text_zh") or narrative.get("text") or narrative.get("text_ja") or "").strip()
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


def _normalize_plan_step_description(description: str, *, render_target_lang: str = "zh") -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    prefixes = ("先", "然后", "再", "接着", "顺便", "帮我", "给我", "请")
    if _normalize_template_lang(render_target_lang) == "ja":
        prefixes = ("まず", "それから", "次に", "続けて", "ついでに", "手伝って", "見て", "お願い")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text.strip(" 　。！？!?，,；;：:")


def _render_plan_step_success_cn(step: Dict[str, Any], *, render_target_lang: str = "zh") -> str:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    success_templates = _mapping_section(templates, "step_success", render_target_lang=render_target_lang)
    step_index = int(step.get("step_index") or 0)
    target_id = str(
        step.get("target_id")
        or _runtime_text("default_step_target", render_target_lang=render_target_lang)
    ).strip()
    description = _normalize_plan_step_description(
        str(step.get("description") or step.get("task_description") or ""),
        render_target_lang=render_target_lang,
    )
    result_text = str(step.get("result_text") or "").strip()
    description_or_target = description or target_id

    if target_id == "web_search":
        topic = description or _runtime_text("default_topic", render_target_lang=render_target_lang)
        if _normalize_template_lang(render_target_lang) == "ja":
            topic = re.sub(r"^(?:検索(?:して)?|調べ(?:て)?|見(?:て)?)(?:\s+)?", "", topic)
        else:
            topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
        return _mapping_text(
            success_templates,
            "web_search",
            render_target_lang=render_target_lang,
            step_index=step_index,
            topic=(topic or _runtime_text("default_topic", render_target_lang=render_target_lang)),
        )
    if target_id == "memory_store":
        remember_prefix = "覚えて" if _normalize_template_lang(render_target_lang) == "ja" else "记住"
        if description.startswith(remember_prefix):
            remembered = description.removeprefix(remember_prefix).strip()
            if remembered:
                if _normalize_template_lang(render_target_lang) == "zh" and remembered.startswith("我"):
                    remembered = "你" + remembered[1:]
                return _mapping_text(
                    success_templates,
                    "memory_store.remembered",
                    render_target_lang=render_target_lang,
                    step_index=step_index,
                    remembered=remembered,
                )
        return _mapping_text(success_templates, "memory_store.default", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "system_info":
        return _mapping_text(success_templates, "system_info", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "process_list":
        return _mapping_text(success_templates, "process_list", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "network_status":
        return _mapping_text(success_templates, "network_status", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "hub_skill_search":
        topic = description or _runtime_text("default_topic", render_target_lang=render_target_lang)
        if _normalize_template_lang(render_target_lang) == "ja":
            topic = re.sub(r"^(?:技能を)?(?:探して|検索して|見つけて)\s*", "", topic).strip() or _runtime_text("default_topic", render_target_lang=render_target_lang)
        else:
            topic = re.sub(r"^(?:搜索技能|查找技能|搜技能)\s*", "", topic).strip() or _runtime_text("default_topic", render_target_lang=render_target_lang)
        return _mapping_text(success_templates, "hub_skill_search", render_target_lang=render_target_lang, step_index=step_index, topic=topic)
    if target_id == "hub_skill_list":
        return _mapping_text(success_templates, "hub_skill_list", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "hub_skill_install":
        return _mapping_text(success_templates, "hub_skill_install", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "hub_skill_remove":
        return _mapping_text(success_templates, "hub_skill_remove", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "file_read":
        return _mapping_text(success_templates, "file_read", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "file_write":
        return _mapping_text(success_templates, "file_write", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "file_list":
        return _mapping_text(success_templates, "file_list", render_target_lang=render_target_lang, step_index=step_index)
    if is_codex_runtime_skill(target_id):
        return _mapping_text(success_templates, "codex_worker", render_target_lang=render_target_lang, step_index=step_index)
    if target_id == "intent_agent":
        return _mapping_text(success_templates, "intent_agent", render_target_lang=render_target_lang, step_index=step_index)
    if result_text:
        return _mapping_text(
            success_templates,
            "__with_result",
            render_target_lang=render_target_lang,
            step_index=step_index,
            description_or_target=description_or_target,
        )
    return _mapping_text(
        success_templates,
        "__default",
        render_target_lang=render_target_lang,
        step_index=step_index,
        description_or_target=description_or_target,
    )


def _render_plan_step_failure_cn(step: Dict[str, Any], *, render_target_lang: str = "zh") -> str:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    failure_templates = _mapping_section(templates, "step_failure", render_target_lang=render_target_lang)
    step_index = int(step.get("step_index") or 0)
    target_id = str(
        step.get("target_id")
        or _runtime_text("default_step_target", render_target_lang=render_target_lang)
    ).strip()
    description = _normalize_plan_step_description(
        str(step.get("description") or step.get("task_description") or ""),
        render_target_lang=render_target_lang,
    )
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = _mapping_text(
        failure_templates,
        "base",
        render_target_lang=render_target_lang,
        step_index=step_index,
        description_or_target=(description or target_id),
    )
    if error_text:
        base += _mapping_text(failure_templates, "error_suffix", render_target_lang=render_target_lang, error_text=error_text)
    if recovery_text:
        base += _mapping_text(failure_templates, "recovery_suffix", render_target_lang=render_target_lang, recovery_text=recovery_text)
    return base


def _build_plan_execution_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    summary_templates = _mapping_section(templates, "summary", render_target_lang=render_target_lang)
    payload = dict(ctx.payload or {})
    steps = list(payload.get("steps") or [])
    if not steps:
        return None
    total_steps = int(payload.get("total_steps") or len(steps))
    parallel_enabled = bool(payload.get("parallel_enabled"))
    success_count = int(payload.get("success_count") or 0)
    intro = (
        _mapping_text(summary_templates, "intro_parallel", render_target_lang=render_target_lang, total_steps=total_steps)
        if parallel_enabled and total_steps > 1
        else _mapping_text(summary_templates, "intro_sequential", render_target_lang=render_target_lang, total_steps=total_steps)
    )
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step, render_target_lang=render_target_lang))
        else:
            parts.append(_render_plan_step_failure_cn(step, render_target_lang=render_target_lang))
    if success_count == len(steps):
        parts.append(_mapping_text(summary_templates, "all_done", render_target_lang=render_target_lang))
    else:
        parts.append(_mapping_text(summary_templates, "partial_done", render_target_lang=render_target_lang))
    return "".join(parts)


def _infer_hub_skill_name_from_task(task: str, *, markers: tuple[str, ...], default_name: str) -> str:
    normalized = str(task or "").strip()
    for marker in markers:
        if marker in normalized:
            normalized = normalized.split(marker, 1)[-1].strip(" ：:")
            break
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


def _format_hub_skill_name_list_cn(values: list[Any], *, limit: int = 6, render_target_lang: str = "zh") -> str:
    names = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not names:
        return ""
    preview = "、".join(names[:limit])
    if len(names) > limit:
        preview += _runtime_text("list_more_suffix", render_target_lang=render_target_lang)
    return preview


def _build_hub_skill_install_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    _default_skill_name = _runtime_text("default_skill_name", render_target_lang=render_target_lang)
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
        parts = [_mapping_text(templates, "install_failure_intro", render_target_lang=render_target_lang, name=name)]
        if message:
            parts.append(_mapping_text(templates, "install_failure_reason", render_target_lang=render_target_lang, message=message))
        return "".join(parts)
    parts = [_mapping_text(templates, "install_success_intro", render_target_lang=render_target_lang, name=name)]
    if activated:
        parts.append(_mapping_text(templates, "install_success_activated", render_target_lang=render_target_lang))
    else:
        parts.append(_mapping_text(templates, "install_success_pending_activation", render_target_lang=render_target_lang))
    preview = _format_hub_skill_name_list_cn(active_skill_names, render_target_lang=render_target_lang)
    if preview:
        parts.append(_mapping_text(templates, "install_success_active_skills", render_target_lang=render_target_lang, preview=preview))
    if repair_attempted:
        if repair_succeeded:
            if repair_actions > 0:
                parts.append(_mapping_text(templates, "install_repair_success_count", render_target_lang=render_target_lang, repair_actions=repair_actions))
            else:
                parts.append(_mapping_text(templates, "install_repair_success_generic", render_target_lang=render_target_lang))
        else:
            parts.append(_mapping_text(templates, "install_repair_failed", render_target_lang=render_target_lang))
    elif message and message not in {"installed ok", "install failed"}:
        if not (message.startswith("-") or "Installed " in message or "->" in message):
            parts.append(_mapping_text(templates, "install_message_suffix", render_target_lang=render_target_lang, message=message))
    return "".join(parts)


def _build_hub_skill_search_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    _default_skill_query = _runtime_text("default_skill_query", render_target_lang=render_target_lang)
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
                    render_target_lang=render_target_lang,
                    registry_error=registry_error,
                    source_names=source_names,
                )
            return _mapping_text(templates, "search_registry_unavailable", render_target_lang=render_target_lang, registry_error=registry_error)
        return _mapping_text(templates, "search_empty", render_target_lang=render_target_lang, query=query)
    previews: list[str] = []
    for item in skills[:3]:
        row = dict(item or {})
        _unnamed_skill_name = _runtime_text("unnamed_skill_name", render_target_lang=render_target_lang)
        name = str(row.get("name") or _unnamed_skill_name).strip() or _unnamed_skill_name
        description = str(row.get("description") or "").strip()
        author = str(row.get("author") or "").strip()
        summary = name
        if description:
            summary += f"：{description}"
        if author:
            summary += _runtime_text("author_suffix", render_target_lang=render_target_lang, author=author)
        previews.append(summary)
    parts = [_mapping_text(templates, "search_intro", render_target_lang=render_target_lang, total=total)]
    if previews:
        delimiter = "；" if _normalize_template_lang(render_target_lang) == "zh" else "、"
        parts.append(_mapping_text(templates, "search_preview_prefix", render_target_lang=render_target_lang) + delimiter.join(previews) + "。")
    return "".join(parts)


def _build_hub_skill_list_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    if total <= 0:
        return _mapping_text(templates, "list_empty", render_target_lang=render_target_lang)
    names = [dict(item or {}).get("name") for item in skills]
    preview = _format_hub_skill_name_list_cn(names, render_target_lang=render_target_lang)
    if preview:
        return _mapping_text(templates, "list_with_preview", render_target_lang=render_target_lang, total=total, preview=preview)
    return _mapping_text(templates, "list_total_only", render_target_lang=render_target_lang, total=total)


def _build_hub_skill_remove_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = _load_template_mapping("chat_agent.hub_skill.reply_templates")
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    _default_skill_name = _runtime_text("default_skill_name", render_target_lang=render_target_lang)
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
            return _mapping_text(templates, "remove_success_with_message", render_target_lang=render_target_lang, name=name, message=message)
        return _mapping_text(templates, "remove_success", render_target_lang=render_target_lang, name=name)
    parts = [_mapping_text(templates, "remove_failure_intro", render_target_lang=render_target_lang, name=name)]
    if message:
        parts.append(_mapping_text(templates, "remove_failure_reason", render_target_lang=render_target_lang, message=message))
    return "".join(parts)


def _build_hub_skill_reply_cn(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    agent_id = str(ctx.agent_id or "").strip()
    if agent_id == "skill:hub_skill_install":
        return _build_hub_skill_install_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id == "skill:hub_skill_search":
        return _build_hub_skill_search_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id == "skill:hub_skill_list":
        return _build_hub_skill_list_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id == "skill:hub_skill_remove":
        return _build_hub_skill_remove_reply_cn(ctx, render_target_lang=render_target_lang)
    return None


def _build_deterministic_sub_agent_reply(
    ctx: SubAgentContext | None,
    *,
    render_target_lang: str,
    user_message: str = "",
) -> str | None:
    _ = user_message
    if ctx is None:
        return None
    agent_id = str(ctx.agent_id or "").strip()
    result_text = str(ctx.result_text or "").strip()
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id.startswith("skill:hub_skill_"):
        return _build_hub_skill_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id in {"recent_turn_direct", "task_progress_direct"}:
        visible = _extract_visible_text(result_text, render_target_lang=render_target_lang)
        return visible or result_text or None
    return None


def _build_chat_agent_quality_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    emotion_context: Dict[str, Any] | None,
    render_target_lang: str,
    render_policy: Any = None,
    prompt_runtime_payload: Mapping[str, Any] | None = None,
) -> str:
    _ = bond_summary, emotion_context, render_policy, prompt_runtime_payload
    if sub_agent_ctx is not None:
        delegated_fallback = _build_sub_agent_result_preserving_fallback_reply(
            sub_agent_ctx,
            render_target_lang=render_target_lang,
            user_message=user_message,
        )
        if delegated_fallback:
            return delegated_fallback
    _ = user_message, render_target_lang
    return ""


def _build_chat_agent_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    render_target_lang: str,
) -> str:
    return _build_chat_agent_quality_fallback_reply(
        user_message=user_message,
        sub_agent_ctx=sub_agent_ctx,
        bond_summary=bond_summary,
        emotion_context=None,
        render_target_lang=render_target_lang,
    )


def _build_installed_capabilities_reply(*, render_target_lang: str) -> str:
    templates = _load_template_mapping("chat_agent.capabilities.reply_templates")
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
                preview += _runtime_text("list_more_suffix", render_target_lang=render_target_lang)
            agent_summaries.append(
                _mapping_text(
                    templates,
                    "agent_summary",
                    render_target_lang=render_target_lang,
                    agent_id=agent_id,
                    desc=desc,
                    preview=preview,
                )
            )

        parts: list[str] = [_mapping_text(templates, "intro", render_target_lang=render_target_lang)]
        if agent_summaries:
            parts.extend(agent_summaries[:6])
        if skill_names:
            preview_skills = "、".join(skill_names[:10])
            if len(skill_names) > 10:
                preview_skills += _runtime_text("list_more_suffix", render_target_lang=render_target_lang)
            parts.append(_mapping_text(templates, "skill_suffix", render_target_lang=render_target_lang, preview_skills=preview_skills))
        parts.append(_mapping_text(templates, "cta", render_target_lang=render_target_lang))
        return "\n".join(parts)
    except Exception as _cap_exc:
        _logger.debug("chat_agent.capability_tip build fallback: %s", _cap_exc)
        from host_brain.core.soft_fail import _emit_metric  # noqa: PLC0415

        _emit_metric(
            stage="chat_agent.capability_tip_build",
            exc_type=type(_cap_exc).__name__,
        )
        return _mapping_text(templates, "fallback", render_target_lang=render_target_lang)


def _should_buffer_stream_output(
    *,
    emotion_context: Any,
    bond_summary: Any,
    render_policy: Any = None,
) -> bool:
    """高风险对话缓冲判断。"""
    policy = _coerce_render_policy(render_policy)
    primary_topic = str(policy.get("primary_topic") or "").strip()
    explicit_decision = _render_policy_explicit_buffering_decision(render_policy)
    if explicit_decision is not None:
        return explicit_decision
    if primary_topic in {
        "assistant_reply_followup",
        "emoji_social_reply",
        "emoji_social_followup",
        "greeting",
        "presence_ping",
        "light_ack",
        "playful_ping",
        "identity",
    }:
        return False
    if _render_policy_requires_buffering(render_policy):
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
                bond_stage=str((bond_summary or {}).get("stage") or "FRIEND"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception as _sb_exc:
        _logger.debug("chat_agent.should_reflect soft fail: %s", _sb_exc)
        from host_brain.core.soft_fail import _emit_metric  # noqa: PLC0415

        _emit_metric(
            stage="chat_agent.should_reflect",
            exc_type=type(_sb_exc).__name__,
        )
        return False


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染（P1 改造：不再用正则拼接，统一走 ChatAgent 人格渲染）
# ---------------------------------------------------------------------------

def _build_sub_agent_context_prompt(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str:
    """将子 Agent 结果转换为注入 ChatAgent prompt 的上下文块。

    Phase XV 核心改造：子 Agent 结果不再直接输出给用户，
    而是作为 context 注入 ChatAgent，由当前 CompiledSoul 重新组织。
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
                render_target_lang=render_target_lang,
                result_text=result_text,
            )
            if result_text
            else ""
        )
        return _mapping_text(
            templates,
            "failure",
            render_target_lang=render_target_lang,
            agent_id=agent_id,
            task=task,
            error_text=(ctx.error or _runtime_text("unknown_error", render_target_lang=render_target_lang)),
            recovery_hint=recovery_hint,
        )

    if not result_text:
        return ""

    if is_codex_runtime_skill(agent_id):
        return _mapping_text(
            templates,
            "codex_worker",
            render_target_lang=render_target_lang,
            agent_id=agent_id,
            task=task,
            result_text=result_text,
        )

    if agent_id == "recent_turn_direct":
        return _mapping_text(templates, "recent_turn_direct", render_target_lang=render_target_lang, result_text=result_text)

    if agent_id == "task_progress_direct":
        return _mapping_text(templates, "task_progress_direct", render_target_lang=render_target_lang, result_text=result_text)

    if agent_id == "plan_execution":
        return _mapping_text(templates, "plan_execution", render_target_lang=render_target_lang, task=task, result_text=result_text)

    return _mapping_text(
        templates,
        "default",
        render_target_lang=render_target_lang,
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

职责：接收 ChatAgentInput → 构建 Soul prompt → LLM 生成
          → SentenceSplitter → 轻校验 → 净化 → COMMIT → 异步后处理。

    调用者：Executive Runtime（由 cortex_orchestrator 链路调用此函数）。
    """
    request_id = agent_input.request_id
    session_id = agent_input.session_id
    user_message = agent_input.user_message
    person_id = agent_input.person_id
    emotion_context = agent_input.emotion_context
    emotion_snapshot = agent_input.emotion_snapshot
    bond_summary = agent_input.bond_summary
    retrieval_evidence_items = agent_input.retrieval_evidence_items
    retrieval_summary = agent_input.retrieval_summary
    render_target_lang = agent_input.render_target_lang
    thread_id = agent_input.thread_id
    ts_ms = agent_input.ts_ms or int(time.time() * 1000)
    intent_draft = agent_input.intent_draft or {}
    trace_stage_meta = dict(agent_input.trace_stage_meta or {})
    reply_plan = _coerce_render_policy(getattr(agent_input, "reply_plan", None) or agent_input.render_policy)
    sub_agent_ctx = agent_input.sub_agent_context
    if "chat_agent_used" not in trace_stage_meta:
        trace_stage_meta["chat_agent_used"] = True
    if "chat_mode" not in trace_stage_meta:
        trace_stage_meta["chat_mode"] = "sub_agent" if sub_agent_ctx else "direct"
    if sub_agent_ctx and not str(trace_stage_meta.get("chat_sub_agent_id") or "").strip():
        trace_stage_meta["chat_sub_agent_id"] = sub_agent_ctx.agent_id
    if sub_agent_ctx and not _should_preserve_sub_agent_result_for_user(
        user_message=user_message,
        agent_id=sub_agent_ctx.agent_id,
    ):
        trace_stage_meta["chat_sub_agent_suppressed"] = sub_agent_ctx.agent_id
        sub_agent_ctx = None
    surface_required = False
    try:
        from host_brain.app.surface_main_chain_adapter import (  # noqa: PLC0415
            surface_main_chain_enabled,
            surface_main_chain_required,
            surface_main_chain_sse_generator,
        )

        surface_required = surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx)
        if surface_main_chain_enabled(agent_input, sub_agent_ctx=sub_agent_ctx):
            async for event in surface_main_chain_sse_generator(
                agent_input,
                trace_stage_meta=trace_stage_meta,
            ):
                yield event
            return
        if surface_required:
            yield _surface_main_no_reply_event(
                request_id=request_id,
                reason_code="SURFACE_MAIN_CHAIN_DISABLED",
                ts_ms=ts_ms,
            )
            return
    except Exception as _surface_main_exc:
        _logger.debug("[%s] surface_main_chain unavailable: %s", request_id, _surface_main_exc)
        if surface_required or sub_agent_ctx is None:
            yield _surface_main_no_reply_event(
                request_id=request_id,
                reason_code="SURFACE_MAIN_CHAIN_UNAVAILABLE",
                ts_ms=ts_ms,
                error=repr(_surface_main_exc),
            )
            return
    deterministic_reply = _build_deterministic_sub_agent_reply(
        sub_agent_ctx,
        render_target_lang=render_target_lang,
        user_message=user_message,
    )
    prompt_messages: list[dict[str, str]] = []
    gen_params: Dict[str, Any] = {}
    prompt_runtime_payload: Dict[str, Any] = {}
    prompt_build_started_ms = int(time.time() * 1000)
    prompt_build_ms = 0
    llm_runtime_resolve_ms = 0
    response_intelligence_ms = 0
    sanitize_egress_ms = 0
    precommit_log_ms = 0
    previous_assistant_text = ""
    from host_brain.persona.reply_plan import coerce_reply_plan  # noqa: PLC0415
    # 确定性子结果渲染不依赖 LLM，也不需要构建完整 prompt。
    if not deterministic_reply:
        # ── 1. 构建 prompt（复用完整 cortex_prompt_builder 管线）──────────────
        from host_brain.app.cortex_prompt_builder import build_cortex_prompt  # noqa: PLC0415

        # 如果有子 Agent 结果，注入到 user_message 作为额外上下文
        # Phase XV P1: 子 Agent 结果统一经 ChatAgent 人格渲染，不直接输出
        effective_user_message = user_message
        sub_agent_context_prompt = ""
        if sub_agent_ctx:
            sub_agent_context_prompt = _build_sub_agent_context_prompt(sub_agent_ctx, render_target_lang=render_target_lang)
            # 子 Agent 结果作为额外 context 注入到 intent_draft，让 prompt_builder 感知
            intent_draft = {
                **intent_draft,
                "sub_agent_context": sub_agent_context_prompt,
                "sub_agent_id": sub_agent_ctx.agent_id,
            }

        # Phase D: PrefrontalAgent 意图规划结果注入
        # prefrontal_task 由 executive_runtime_core 在 plan_response 后写入 intent_draft。
        # 这里收成受控的 turn_context_sections，由 cortex_prompt_builder 并入单一 system_prompt，
        # 不再额外创建第二条 system/context 平面。
        _prefrontal_task = str(intent_draft.get("prefrontal_task") or "").strip()
        _prefrontal_tone_hint = str(intent_draft.get("prefrontal_tone_hint") or "").strip()
        _prefrontal_context_block = ""
        if _prefrontal_task:
            _prefrontal_context_block = _build_prefrontal_context_block(
                task=_prefrontal_task,
                tone_hint=_prefrontal_tone_hint,
            )
            _logger.debug(
                "[%s] ChatAgent 接收 prefrontal_task: task=%s tone=%s",
                request_id, _prefrontal_task[:60], _prefrontal_tone_hint,
            )

        # 合并 turn-local context（子 Agent 结果 + prefrontal 规划）
        _turn_context_sections = [p for p in [sub_agent_context_prompt, _prefrontal_context_block] if p]

        try:
            prompt_result = build_cortex_prompt(
                user_message=effective_user_message,
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
                attachments=list(agent_input.attachments or []),
                multimodal_observations=dict(agent_input.multimodal_observations or {}),
                attachment_summary=str(agent_input.attachment_summary or "").strip(),
                eval_history_turns=list(agent_input.eval_history_turns or []),
                turn_context_sections=_turn_context_sections,
                include_runtime_payload=True,
            )
            if isinstance(prompt_result, tuple) and len(prompt_result) >= 3:
                prompt_messages, gen_params, raw_prompt_runtime_payload = prompt_result[:3]
                prompt_runtime_payload = dict(raw_prompt_runtime_payload or {}) if isinstance(raw_prompt_runtime_payload, Mapping) else {}
            else:
                prompt_messages, gen_params = prompt_result
                prompt_runtime_payload = {}
            conversation_history_meta = dict(prompt_runtime_payload.get("conversation_history") or {})
            if conversation_history_meta:
                trace_stage_meta["conversation_history"] = conversation_history_meta
            conversation_interpretation_meta = dict(prompt_runtime_payload.get("conversation_interpretation") or {})
            if conversation_interpretation_meta:
                trace_stage_meta["conversation_interpretation"] = conversation_interpretation_meta
            topic_ledger_meta = dict(prompt_runtime_payload.get("topic_ledger") or {})
            if topic_ledger_meta:
                trace_stage_meta["topic_ledger"] = topic_ledger_meta
            tactic_ledger_meta = dict(prompt_runtime_payload.get("tactic_ledger") or {})
            if tactic_ledger_meta:
                trace_stage_meta["tactic_ledger"] = tactic_ledger_meta
            affect_state_meta = dict(prompt_runtime_payload.get("affect_state") or {})
            if affect_state_meta:
                trace_stage_meta["affect_state"] = affect_state_meta
            relationship_state_meta = dict(prompt_runtime_payload.get("relationship_state") or {})
            if relationship_state_meta:
                trace_stage_meta["relationship_state"] = relationship_state_meta
            drive_state_meta = dict(prompt_runtime_payload.get("drive_state") or {})
            if drive_state_meta:
                trace_stage_meta["drive_state"] = drive_state_meta
            reply_plan_meta = dict(prompt_runtime_payload.get("reply_plan") or {})
            if reply_plan_meta:
                trace_stage_meta["reply_plan"] = reply_plan_meta
            prompt_build_ms = max(0, int(time.time() * 1000) - prompt_build_started_ms)
            if not reply_plan:
                reply_plan = _coerce_render_policy(prompt_runtime_payload.get("reply_plan"))
        except Exception as _pb_exc:
            _logger.warning("[%s] ChatAgent prompt_builder failed: %s", request_id, _pb_exc)
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "NO_REPLY",
                "reason_code": "PROMPT_BUILD_FAILED", "request_id": request_id,
                "narrative": "",
                "reply_envelope": {
                    "kind": "no_reply",
                    "sendable": False,
                    "persistable": False,
                    "cacheable": False,
                    "degraded": True,
                    "reason_code": "PROMPT_BUILD_FAILED",
                },
            })
            return
    reply_plan_decision = coerce_reply_plan(reply_plan)

    def _is_image_social_reply_plan(plan: Any) -> bool:
        if isinstance(plan, dict):
            topic = str(plan.get("primary_topic") or "").strip()
            form_reason = str(plan.get("response_form_reason") or "").strip()
        else:
            topic = str(getattr(plan, "primary_topic", "") or "").strip()
            form_reason = str(getattr(plan, "response_form_reason", "") or "").strip()
        return (
            topic in {"emoji_social_reply", "emoji_social_followup"}
            or form_reason in {"image_social_reply", "image_social_followup"}
        )

    image_social_reply_plan = _is_image_social_reply_plan(reply_plan_decision)
    social_reaction_form_only_plan = (
        str(getattr(reply_plan_decision, "primary_topic", "") or "").strip() == "emoji_social_reply"
        and str(getattr(reply_plan_decision, "response_form_reason", "") or "").strip() == "social_reaction"
        and bool(getattr(reply_plan_decision, "allow_form_only_reply", False))
    )
    if not deterministic_reply:
        previous_assistant_text = next(
            (
                str(item.get("content") or "").strip()
                for item in reversed(prompt_messages)
                if str(item.get("role") or "").strip().lower() == "assistant" and str(item.get("content") or "").strip()
            ),
            "",
        )
        trace_stage_meta["deterministic_primary_used"] = False
        trace_stage_meta["deterministic_primary_reason"] = "disabled:surface_renderer_or_full_model_only"

    if social_reaction_form_only_plan and not deterministic_reply:
        social_reaction_started_ms = int(time.time() * 1000)
        from host_brain.app.context.multimodal_act import coerce_multimodal_act  # noqa: PLC0415
        from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: PLC0415
        from host_brain.app.reaction_catalog import get_reaction_catalog  # noqa: PLC0415
        from host_brain.app.reply_envelope import ReplyEnvelope  # noqa: PLC0415

        multimodal_act = coerce_multimodal_act(
            dict(getattr(reply_plan_decision, "multimodal", {}) or {}).get("multimodal_act")
            or getattr(reply_plan_decision, "multimodal", {})
        )
        platform = str(getattr(agent_input, "reply_channel", "") or "").strip().lower() or "qq"
        soul_id = str(getattr(get_compiled_soul(), "soul_id", "") or "").strip()
        reaction_asset = get_reaction_catalog().select(
            platform=platform,
            emotion=str(multimodal_act.emotion or "unknown").strip().lower() or "unknown",
            intensity=float(multimodal_act.intensity or 0.0),
            soul_id=soul_id,
        )
        social_reaction_ms = max(0, int(time.time() * 1000) - social_reaction_started_ms)
        trace_stage_meta["social_reaction_decider"] = {
            "invoked": True,
            "latency_ms": social_reaction_ms,
            "affected_output": bool(reaction_asset),
            "source": "reaction_catalog",
            "emotion": multimodal_act.emotion,
            "intensity": multimodal_act.intensity,
            "platform": platform,
            "reason_code": "REACTION_ASSET_SELECTED" if reaction_asset else "SOCIAL_REACTION_ASSET_MISSING",
        }
        trace_stage_meta["image_social_full_chat_lane"] = {
            "status": "SKIPPED",
            "invoked": False,
            "affected_output": False,
            "source": "social_reaction_decider",
            "reason_code": "social_reaction_form_only",
        }
        trace_stage_meta["surface_renderer"] = {
            "status": "SKIPPED",
            "invoked": False,
            "affected_output": False,
            "source": "social_reaction_decider",
            "reason_code": "social_reaction_form_only",
        }
        timings_ms = {
            "prompt_build_ms": prompt_build_ms,
            "social_reaction_decider_ms": social_reaction_ms,
            "llm_runtime_resolve_ms": 0,
            "llm_latency_ms": 0,
            "first_token_ms": 0,
        }
        if reaction_asset is None:
            reply_envelope = ReplyEnvelope.no_reply(
                reason_code="SOCIAL_REACTION_ASSET_MISSING",
                source="reaction_catalog",
                audit={
                    "request_id": request_id,
                    "emotion": multimodal_act.emotion,
                    "platform": platform,
                    "response_form_reason": reply_plan_decision.response_form_reason,
                },
            )
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SOCIAL_REACTION_ASSET_MISSING",
                "narrative": "",
                "request_id": request_id,
                "reply_envelope": reply_envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "chat_agent:social_reaction",
                    "latency_ms": social_reaction_ms,
                    "first_token_ms": 0,
                    "llm_latency_ms": 0,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                    "stream_buffered": False,
                    "model_id": "reaction-catalog",
                    "render_target_lang": render_target_lang,
                    "tts_text_ja": None,
                    "response_form": str(reply_plan_decision.response_form or "sticker").strip() or "sticker",
                    "response_form_reason": str(reply_plan_decision.response_form_reason or "social_reaction").strip(),
                    "timings_ms": timings_ms,
                    "response_intelligence": {"reflection": {"status": "BYPASSED", "reason_code": "social_reaction_form_only"}},
                    "suppress_final_reply": True,
                    "suppress_final_reply_reason_code": "SOCIAL_REACTION_ASSET_MISSING",
                    "reply_envelope": reply_envelope.as_dict(),
                    "trace_stage_meta": trace_stage_meta,
                },
            })
            return

        response_form = str(reaction_asset.response_form or reply_plan_decision.response_form or "sticker").strip().lower() or "sticker"
        if response_form not in {"sticker", "image", "gif"}:
            response_form = "sticker"
        raw_extra = {
            **dict(reaction_asset.raw_extra or {}),
            "media_ref": reaction_asset.media_ref,
            "reaction_id": reaction_asset.reaction_id,
            "content": reaction_asset.content,
            "multimodal_act": multimodal_act.as_dict(),
        }
        reply_envelope = ReplyEnvelope.final_reply(
            text="",
            response_form=response_form,
            source="reaction_catalog",
            cacheable=False,
            media_ref=str(reaction_asset.media_ref or "").strip(),
            reaction_id=str(reaction_asset.reaction_id or "").strip(),
            raw_extra=raw_extra,
            audit={
                "request_id": request_id,
                "emotion": multimodal_act.emotion,
                "platform": platform,
                "response_form_reason": reply_plan_decision.response_form_reason,
            },
        )
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "narrative": reply_envelope.text,
            "request_id": request_id,
            "reply_envelope": reply_envelope.as_dict(),
            "audit_refs": {
                "request_id": request_id,
                "path": "chat_agent:social_reaction",
                "latency_ms": social_reaction_ms,
                "first_token_ms": 0,
                "llm_latency_ms": 0,
                "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                "stream_buffered": False,
                "model_id": "reaction-catalog",
                "render_target_lang": render_target_lang,
                "tts_text_ja": None,
                "response_form": response_form,
                "response_form_reason": str(reply_plan_decision.response_form_reason or "social_reaction").strip(),
                "timings_ms": timings_ms,
                "response_intelligence": {"reflection": {"status": "BYPASSED", "reason_code": "social_reaction_form_only"}},
                "pii_stats": {},
                "suppress_final_reply": False,
                "suppress_final_reply_reason_code": None,
                "media_ref": reply_envelope.media_ref,
                "reaction_id": reply_envelope.reaction_id,
                "raw_extra": raw_extra,
                "reply_envelope": reply_envelope.as_dict(),
                "trace_stage_meta": trace_stage_meta,
            },
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
        llm_runtime_resolve_started_ms = int(time.time() * 1000)

        def _schedule_llm_unavailable_post_process(
            fallback_narrative: str,
            *,
            provider_name: str = "fallback",
            model_id: str = "llm-unavailable-fallback",
        ) -> None:
            _ = fallback_narrative, provider_name, model_id
            trace_stage_meta["llm_unavailable_no_reply"] = {
                "kind": "no_reply",
                "reason_code": "LLM_UNAVAILABLE",
                "affected_output": False,
            }

        def _build_llm_unavailable_fallback_narrative() -> str:
            fallback_narrative = _build_chat_agent_quality_fallback_reply(
                user_message=user_message,
                sub_agent_ctx=sub_agent_ctx,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                render_policy=reply_plan,
                prompt_runtime_payload=prompt_runtime_payload,
            )
            return _apply_render_policy_hard_guard(
                fallback_narrative,
                reply_plan,
                user_message=user_message,
                bond_stage=str((bond_summary or {}).get("stage") or ""),
                render_target_lang=render_target_lang,
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
                llm_runtime = resolve_chat_agent_llm_runtime(
                    base_runtime=base_runtime,
                )
            except Exception as _rt_exc:
                _logger.warning("[%s] ChatAgent llm_runtime resolve failed: %s", request_id, _rt_exc)
                fallback_narrative = _build_llm_unavailable_fallback_narrative()
                _schedule_llm_unavailable_post_process(fallback_narrative)
                yield _sse({
                    "phase": "COMMIT", "done": True, "decision": "NO_REPLY",
                    "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                    "narrative": "",
                    "reply_envelope": {
                        "kind": "no_reply",
                        "sendable": False,
                        "persistable": False,
                        "cacheable": False,
                        "degraded": True,
                        "reason_code": "LLM_UNAVAILABLE",
                    },
                })
                return
        llm_runtime_resolve_ms = max(0, int(time.time() * 1000) - llm_runtime_resolve_started_ms)

        if not llm_runtime.get("available"):
            fallback_narrative = _build_llm_unavailable_fallback_narrative()
            _schedule_llm_unavailable_post_process(
                fallback_narrative,
                provider_name=str(llm_runtime.get("provider_name") or "fallback"),
                model_id=str(llm_runtime.get("model_id") or "llm-unavailable-fallback"),
            )
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "NO_REPLY",
                "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                "narrative": "",
                "reply_envelope": {
                    "kind": "no_reply",
                    "sendable": False,
                    "persistable": False,
                    "cacheable": False,
                    "degraded": True,
                    "reason_code": "LLM_UNAVAILABLE",
                },
            })
            return

    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    base_url = str(llm_runtime.get("base_url_override") or "").strip() or None

    # Runtime defaults provide the broad model personality; prompt builders can
    # still override per-lane budgets/temperature for stricter chat contracts.
    agent_gen_params = {}
    agent_overrides = llm_runtime.get("agent_gen_params") or {}
    agent_gen_params.update({
        k: v for k, v in agent_overrides.items()
        if v is not None
    })
    agent_gen_params.update({
        k: v for k, v in dict(gen_params or {}).items()
        if v is not None
    })
    if bool(agent_gen_params.pop("disable_token_limit", False)):
        for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
            agent_gen_params.pop(token_key, None)
    response_form_for_completion = str(
        getattr(reply_plan_decision, "response_form", "") or reply_plan.get("response_form") or "text"
    ).strip().lower()
    provider_model_fingerprint = f"{provider_name}:{model_id}".lower()
    use_nonstream_completion = (
        response_form_for_completion in {"", "text"}
        and (
            str(provider_name or "").strip().lower() == "gemini"
            or "gemini-" in provider_model_fingerprint
        )
        and str(os.environ.get("HOST_CHAT_GEMINI_NONSTREAM_TEXT") or "true").strip().lower()
        not in {"0", "false", "no", "off"}
    )
    if use_nonstream_completion:
        for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
            agent_gen_params.pop(token_key, None)
        trace_stage_meta["completion_mode"] = {
            "mode": "non_stream",
            "reason_code": "gemini_stream_first_chunk_truncation_guard",
            "provider": provider_name,
            "model": model_id,
        }
    if image_social_reply_plan and not deterministic_reply and not social_reaction_form_only_plan:
        trace_stage_meta["image_social_full_chat_lane"] = {
            "status": "ENABLED",
            "invoked": True,
            "affected_output": True,
            "source": "full_model",
            "reason_code": "image_social_text_uses_full_chat",
        }

    # ── 3. 流式生成 + SentenceSplitter ──────────────────────────────
    from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
        acomplete_llm_with_pool,
        astream_llm_chunks_with_pool,
    )
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415
    from host_brain.persona.reply_validator import (  # noqa: PLC0415
        build_correction_prompt,
        build_semantic_rescue_seed,
        build_soul_rescue_prompt,
        infer_reply_tactic_shape,
        validate_reply,
    )

    # 诊断日志：确认 system_prompt 是否包含人设（前 150 字）
    _logger.info(
        "[%s] ChatAgent system_prompt preview: %s",
        request_id,
        next((str(item.get("content") or "") for item in prompt_messages if str(item.get("role") or "").strip().lower() == "system"), "")[:150].replace("\n", "↵"),
    )
    _logger.info(
        "[%s] ChatAgent direct_response_injected=%s",
        request_id,
        any("【回应原则】" in str(item.get("content") or "") for item in prompt_messages),
    )

    splitter = SentenceSplitter()
    executed_tactic_family = str(reply_plan_decision.tactic_family or "").strip()
    executed_tactic_variant = str(reply_plan_decision.tactic_variant or "").strip()
    executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip()
    reply_used_fallback = False
    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    sentence_index = 0
    llm_started_ms = int(time.time() * 1000)

    buffer_stream = _should_buffer_stream_output(
        emotion_context=emotion_context,
        bond_summary=bond_summary or None,
        render_policy=reply_plan,
    )
    if bool(reply_plan.get("force_buffering")):
        buffer_stream = True
    if use_nonstream_completion:
        buffer_stream = True
    if deterministic_reply:
        buffer_stream = False
        provider_name = str(locals().get("deterministic_reply_provider_name") or "host")
        model_id = str(locals().get("deterministic_reply_model_id") or "deterministic-plan-renderer")
        timeout_ms = 0
        api_key = ""
        base_url = None

    _voice_outputs_enabled = _should_enable_edge_voice_outputs(
        reply_channel=agent_input.reply_channel,
    )
    # Phase XV P7：分句 TTS 开关
    # suppress_edge_tts=True 时（Gateway 模式），或入口本身不是语音通道时，本地 TTS 不触发
    _suppress_tts = bool(agent_input.suppress_edge_tts) or not _voice_outputs_enabled
    _tts_sentence_stream_enabled = (
        _voice_outputs_enabled
        and not _suppress_tts
        and str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # 分句 TTS 用的 SentenceSplitter（对 text_ja 分句）
    tts_splitter = SentenceSplitter() if _tts_sentence_stream_enabled else None  # type: ignore[assignment]
    tts_sentence_index = 0
    # 追踪已渲染的 text_ja（用于增量提取）
    _tts_rendered_text = ""

    async def _collect_buffered_generation(messages_for_call: list[dict[str, str]]) -> tuple[str, int | None]:
        buffered_text = ""
        buffered_first_token_ms: int | None = None
        _started_ms = int(time.time() * 1000)
        async for _chunk in astream_llm_chunks_with_pool(
            request_id=request_id,
            scene_type="CHAT",
            cognitive_tier="A",
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages_for_call,
            gen_params=agent_gen_params,
            base_url_override=base_url,
        ):
            if buffered_first_token_ms is None:
                buffered_first_token_ms = int(time.time() * 1000) - _started_ms
            buffered_text += _chunk
        return buffered_text, buffered_first_token_ms

    def _build_retry_messages(correction_prompt: str) -> list[dict[str, str]]:
        correction_block = f"【重试纠偏】\n{str(correction_prompt or '').strip()}".strip()
        retry_messages: list[dict[str, str]] = [{"role": "system", "content": correction_block}]
        for item in prompt_messages:
            role = str(item.get("role") or "").strip().lower() or "user"
            retry_messages.append({"role": role, "content": str(item.get("content") or "")})
        return retry_messages

    def _should_attempt_soul_rescue(plan: Any) -> bool:
        topic = str(getattr(plan, "primary_topic", "") or "").strip()
        if topic in {
            "assistant_reply_followup",
            "emoji_social_reply",
            "emoji_social_followup",
            "greeting",
            "presence_ping",
            "light_ack",
            "playful_ping",
            "identity",
        }:
            return False
        return topic in {
            "relationship_boundary",
            "motivation",
            "help_commitment",
            "rejection",
        }

    def _build_rescue_messages(
        semantic_seed_text: str,
        *,
        previous_candidate_text: str = "",
    ) -> list[dict[str, str]]:
        rescue_prompt = build_soul_rescue_prompt(
            plan=reply_plan_decision,
            semantic_seed_text=semantic_seed_text,
            previous_candidate_text=previous_candidate_text,
        )
        base_system_parts = [
            str(item.get("content") or "").strip()
            for item in prompt_messages
            if str(item.get("role") or "").strip().lower() == "system" and str(item.get("content") or "").strip()
        ]
        rescue_system = "\n\n".join(part for part in base_system_parts if part).strip()
        rescue_block = f"【自然化重写】\n{str(rescue_prompt or '').strip()}".strip()
        if rescue_system:
            rescue_system = f"{rescue_system}\n\n{rescue_block}".strip()
        else:
            rescue_system = rescue_block
        return [
            {"role": "system", "content": rescue_system},
            {"role": "user", "content": str(user_message or "").strip()},
        ]

    async def _attempt_soul_rescue(
        *,
        previous_candidate_text: str,
    ) -> tuple[str, int | None]:
        if not _should_attempt_soul_rescue(reply_plan_decision):
            return "", None
        semantic_seed_text = build_semantic_rescue_seed(plan=reply_plan_decision)
        try:
            return await _collect_buffered_generation(
                _build_rescue_messages(
                    semantic_seed_text,
                    previous_candidate_text=previous_candidate_text,
                )
            )
        except Exception as _rescue_exc:
            _logger.warning("[%s] ChatAgent soul rescue rewrite failed: %s", request_id, _rescue_exc)
            return "", None

    def _finalize_candidate_text(
        raw_text: str,
        *,
        allow_rewrite: bool,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        candidate = repair_utf8_latin1_mojibake(raw_text) or raw_text
        candidate = _extract_visible_text(candidate, render_target_lang=render_target_lang) or candidate
        _ri_meta = {
            "reflection": {
                "status": "BYPASSED",
                "reason_code": "normal_chat_pipeline_direct",
                "allow_rewrite": bool(allow_rewrite),
            }
        }
        candidate = _strip_internal_delegate_markers(candidate)
        candidate = _airlock_sanitize_output(candidate)
        candidate, _pii_stats = scrub_pii_egress(candidate)
        return candidate, _ri_meta, _pii_stats

    def _is_validation_acceptable(validation_candidate: Any) -> bool:
        if validation_candidate is None:
            return False
        reason_codes = {
            str(item or "").strip()
            for item in list(getattr(validation_candidate, "reason_codes", ()) or ())
            if str(item or "").strip()
        }
        blocking_reasons = {
            "empty_reply",
            "symbol_only_reply",
            "unfinished_tail",
            "prompt_leak",
            "process_term_echo",
            "unsafe_content",
            "wrong_form",
        }
        if reason_codes & blocking_reasons:
            return False
        return True

    def _trace_text_sample(raw: str, *, limit: int = 240) -> str:
        normalized = str(raw or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit] + "..."

    suppress_final_reply = False
    suppress_final_reply_reason_code: str | None = None

    def _apply_validation_fallback(
        *,
        reason_codes: Any,
        source: str,
    ) -> None:
        nonlocal full_text
        nonlocal narrative
        nonlocal response_intelligence_meta
        nonlocal _pii_egress_stats
        nonlocal tts_text
        nonlocal reply_used_fallback
        nonlocal executed_tactic_family
        nonlocal executed_tactic_variant
        nonlocal executed_trajectory_stage
        nonlocal suppress_final_reply
        nonlocal suppress_final_reply_reason_code

        normalized_reasons = [
            str(item or "").strip()
            for item in list(reason_codes or ())
            if str(item or "").strip()
        ]
        trace_stage_meta.setdefault("reply_validation", {})
        suppress_final_reply = True
        if image_social_reply_plan:
            suppress_final_reply_reason_code = "IMAGE_SOCIAL_NO_SAFE_REPLY"
        else:
            suppress_final_reply_reason_code = "VALIDATION_NO_SAFE_REPLY"
        reply_used_fallback = False
        executed_tactic_family = str(reply_plan_decision.tactic_family or "").strip() or "no_reply"
        executed_tactic_variant = str(reply_plan_decision.tactic_variant or "").strip() or "validation_suppressed"
        executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip() or "validation_suppressed"
        full_text = ""
        narrative = ""
        response_intelligence_meta = {
            "reflection": {
                "status": "BYPASSED",
                "reason_code": suppress_final_reply_reason_code,
                "allow_rewrite": False,
            },
            "suppressed_final_reply": {
                "reason_code": suppress_final_reply_reason_code,
                "source": source,
                "validation_reason_codes": list(normalized_reasons),
            },
        }
        _pii_egress_stats = {}
        tts_text = ""
        trace_stage_meta["reply_validation"]["fallback"] = {
            "used": False,
            "suppressed": True,
            "reason_code": suppress_final_reply_reason_code,
            "source": source,
            "reason_codes": list(normalized_reasons),
        }
        trace_stage_meta["fallback_suppressed"] = {
            "reason_code": suppress_final_reply_reason_code,
            "source": source,
            "primary_topic": str(getattr(reply_plan_decision, "primary_topic", "") or "").strip(),
            "response_form_reason": str(getattr(reply_plan_decision, "response_form_reason", "") or "").strip(),
        }
        trace_stage_meta["fallback_used"] = False
        trace_stage_meta["reply_source"] = "suppressed_no_reply"

    if not deterministic_reply:
        trace_stage_meta["surface_renderer"] = {
            "status": "SKIPPED",
            "invoked": False,
            "affected_output": False,
            "source": "full_model",
            "reason_code": "retired_from_normal_text_final_reply",
        }

    try:
        if deterministic_reply:
            full_text = str(deterministic_reply).strip()
            if image_social_reply_plan:
                _pre_render_narrative, _pre_render_ri_meta, _pre_render_pii = _finalize_candidate_text(
                    full_text,
                    allow_rewrite=False,
                )
                _pre_render_validation = validate_reply(
                    text=_pre_render_narrative,
                    plan=reply_plan_decision,
                    user_message=user_message,
                    previous_assistant_text=previous_assistant_text,
                )
                trace_stage_meta["image_social_pre_render_validation"] = (
                    _pre_render_validation.as_dict() if hasattr(_pre_render_validation, "as_dict") else {}
                )
                if not _is_validation_acceptable(_pre_render_validation):
                    _apply_validation_fallback(
                        reason_codes=getattr(_pre_render_validation, "reason_codes", ()) or (),
                        source="image_social_deterministic_pre_render_invalid",
                    )
            if suppress_final_reply:
                full_text = ""
                rendered_text = ""
                first_token_ms = 0 if first_token_ms is None else first_token_ms
            else:
                visible = _apply_render_policy_stream_guard(
                    full_text,
                    reply_plan,
                    render_target_lang=render_target_lang,
                )
                rendered_text = visible
                first_token_ms = 0
                if visible:
                    yield _sse({"phase": "RENDER", "chunk": visible})
                    await asyncio.sleep(0)
                    for sentence in splitter.feed(visible):
                        yield _sse({
                            "phase": "SENTENCE",
                            "sentence": sentence,
                            "sentence_index": sentence_index,
                        })
                        sentence_index += 1
                    await asyncio.sleep(0)
        elif suppress_final_reply:
            full_text = ""
            rendered_text = ""
            first_token_ms = 0 if first_token_ms is None else first_token_ms
        elif use_nonstream_completion:
            _complete_started_ms = int(time.time() * 1000)
            _complete_result = await acomplete_llm_with_pool(
                request_id=request_id,
                scene_type="CHAT",
                cognitive_tier="A",
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                messages=prompt_messages,
                gen_params=agent_gen_params,
                base_url_override=base_url,
            )
            full_text = str(getattr(_complete_result, "content", "") or "").strip()
            first_token_ms = max(0, int(time.time() * 1000) - _complete_started_ms)
            final_provider = str(getattr(_complete_result, "final_provider", "") or "").strip()
            final_model = str(getattr(_complete_result, "final_model", "") or "").strip()
            if final_provider:
                provider_name = final_provider
            if final_model:
                model_id = final_model
            trace_stage_meta["completion_mode"] = {
                **dict(trace_stage_meta.get("completion_mode") or {}),
                "latency_ms": first_token_ms,
                "final_provider": provider_name,
                "final_model": model_id,
                "pool_exhausted": bool(getattr(_complete_result, "pool_exhausted", False)),
                "raw_text_sample": _trace_text_sample(full_text),
            }
        else:
            async for chunk in astream_llm_chunks_with_pool(
                request_id=request_id,
                scene_type="CHAT",
                cognitive_tier="A",
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                messages=prompt_messages,
                gen_params=agent_gen_params,
                base_url_override=base_url,
            ):
                if first_token_ms is None:
                    first_token_ms = int(time.time() * 1000) - llm_started_ms
                    _logger.info(
                        "[%s] ChatAgent first_token_ms=%s model=%s buffered=%s",
                        request_id,
                        first_token_ms,
                        model_id,
                        buffer_stream,
                    )

                full_text += chunk
                visible = _apply_render_policy_stream_guard(
                    _extract_visible_text(full_text, render_target_lang=render_target_lang),
                    reply_plan,
                    render_target_lang=render_target_lang,
                )
                if not visible:
                    continue

                if visible.startswith(rendered_text):
                    new_chunk = visible[len(rendered_text):]
                else:
                    new_chunk = visible
                if not new_chunk:
                    if _should_cut_stream_after_policy_cap(
                        render_policy=reply_plan,
                        rendered_text=rendered_text,
                        visible_text=visible,
                    ):
                        full_text = visible
                        _logger.info(
                            "[%s] ChatAgent stream cut after render_policy cap",
                            request_id,
                        )
                        break
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
                render_policy=reply_plan,
                prompt_runtime_payload=prompt_runtime_payload,
            )
            full_text = _apply_render_policy_hard_guard(
                full_text,
                reply_plan,
                user_message=user_message,
                bond_stage=str((bond_summary or {}).get("stage") or ""),
                render_target_lang=render_target_lang,
            )
            if not buffer_stream:
                visible_fallback = _apply_render_policy_stream_guard(
                    full_text,
                    reply_plan,
                    render_target_lang=render_target_lang,
                )
                yield _sse({"phase": "RENDER", "chunk": visible_fallback})
                for sentence in splitter.feed(visible_fallback):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

    # ── 4. 组装 narrative + ReplyPlan 校验/纠偏 ─────────────────────
    response_intelligence_meta: dict[str, Any] = {}
    _pii_egress_stats: dict[str, Any] = {}
    _response_intelligence_started_ms = int(time.time() * 1000)
    narrative, response_intelligence_meta, _pii_egress_stats = _finalize_candidate_text(
        full_text,
        allow_rewrite=buffer_stream,
    )
    tts_text = _extract_tts_text(full_text)
    if not deterministic_reply:
        _validation = validate_reply(
            text=narrative,
            plan=reply_plan_decision,
            user_message=user_message,
            previous_assistant_text=previous_assistant_text,
        )
        trace_stage_meta["reply_validation"] = {
            "initial": _validation.as_dict(),
        }
        trace_stage_meta["reply_validation"]["initial_text"] = _trace_text_sample(narrative)
        if _is_validation_acceptable(_validation):
            trace_stage_meta["reply_validation"]["accepted_initial"] = True
        elif image_social_reply_plan:
            _apply_validation_fallback(
                reason_codes=getattr(_validation, "reason_codes", ()) or (),
                source="image_social_initial_invalid",
            )
        elif _validation.should_retry:
            if image_social_reply_plan:
                trace_stage_meta["reply_validation"]["image_social_retry_reason"] = "safe_initial_invalid"
            _correction_prompt = build_correction_prompt(
                plan=reply_plan_decision,
                validation=_validation,
                previous_assistant_text=previous_assistant_text,
            )
            try:
                _retry_raw_text, _retry_first_token_ms = await _collect_buffered_generation(
                    _build_retry_messages(_correction_prompt)
                )
                if first_token_ms is None and _retry_first_token_ms is not None:
                    first_token_ms = _retry_first_token_ms
            except Exception as _retry_exc:
                _logger.warning("[%s] ChatAgent correction retry failed: %s", request_id, _retry_exc)
                _retry_raw_text = ""
            if _retry_raw_text.strip():
                _retry_narrative, _retry_ri_meta, _retry_pii = _finalize_candidate_text(
                    _retry_raw_text,
                    allow_rewrite=True,
                )
                _retry_validation = validate_reply(
                    text=_retry_narrative,
                    plan=reply_plan_decision,
                    user_message=user_message,
                    previous_assistant_text=previous_assistant_text,
                )
                trace_stage_meta["reply_validation"]["retry"] = _retry_validation.as_dict()
                trace_stage_meta["reply_validation"]["retry_text"] = _trace_text_sample(_retry_narrative)
                if _is_validation_acceptable(_retry_validation):
                    executed_tactic_family = str(
                        _retry_validation.actual_tactic_family
                        or reply_plan_decision.tactic_family
                        or ""
                    ).strip()
                    executed_tactic_variant = str(
                        _retry_validation.actual_tactic_variant
                        or reply_plan_decision.tactic_variant
                        or ""
                    ).strip()
                    executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip()
                    full_text = _retry_narrative
                    narrative = _retry_narrative
                    response_intelligence_meta = _retry_ri_meta
                    _pii_egress_stats = _retry_pii
                    tts_text = _extract_tts_text(full_text)
                    if not _retry_validation.is_valid:
                        trace_stage_meta["reply_validation"]["accepted_with_warnings"] = {
                            "quality_score": float(getattr(_retry_validation, "quality_score", 0.0) or 0.0),
                            "reason_codes": list(getattr(_retry_validation, "reason_codes", ()) or ()),
                        }
                elif image_social_reply_plan:
                    _apply_validation_fallback(
                        reason_codes=getattr(_retry_validation, "reason_codes", ()) or (),
                        source="image_social_retry_invalid",
                    )
                else:
                    _rescue_raw_text, _rescue_first_token_ms = await _attempt_soul_rescue(
                        previous_candidate_text=_retry_narrative,
                    )
                    if first_token_ms is None and _rescue_first_token_ms is not None:
                        first_token_ms = _rescue_first_token_ms
                    if _rescue_raw_text.strip():
                        _rescue_narrative, _rescue_ri_meta, _rescue_pii = _finalize_candidate_text(
                            _rescue_raw_text,
                            allow_rewrite=True,
                        )
                        _rescue_validation = validate_reply(
                            text=_rescue_narrative,
                            plan=reply_plan_decision,
                            user_message=user_message,
                            previous_assistant_text=previous_assistant_text,
                        )
                        trace_stage_meta["reply_validation"]["rescue"] = _rescue_validation.as_dict()
                        trace_stage_meta["reply_validation"]["rescue_text"] = _trace_text_sample(_rescue_narrative)
                        if _is_validation_acceptable(_rescue_validation):
                            executed_tactic_family = str(
                                _rescue_validation.actual_tactic_family
                                or reply_plan_decision.tactic_family
                                or ""
                            ).strip()
                            executed_tactic_variant = str(
                                _rescue_validation.actual_tactic_variant
                                or reply_plan_decision.tactic_variant
                                or ""
                            ).strip()
                            executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip()
                            full_text = _rescue_narrative
                            narrative = _rescue_narrative
                            response_intelligence_meta = _rescue_ri_meta
                            _pii_egress_stats = _rescue_pii
                            tts_text = _extract_tts_text(full_text)
                            if not _rescue_validation.is_valid:
                                trace_stage_meta["reply_validation"]["accepted_with_warnings"] = {
                                    "quality_score": float(getattr(_rescue_validation, "quality_score", 0.0) or 0.0),
                                    "reason_codes": list(getattr(_rescue_validation, "reason_codes", ()) or ()),
                                }
                        else:
                            _apply_validation_fallback(
                                reason_codes=getattr(_rescue_validation, "reason_codes", ()) or (),
                                source="retry_rescue_invalid",
                            )
                    else:
                        _apply_validation_fallback(
                            reason_codes=getattr(_retry_validation, "reason_codes", ()) or (),
                            source="retry_rescue_empty",
                        )
            elif image_social_reply_plan:
                _apply_validation_fallback(
                    reason_codes=getattr(_validation, "reason_codes", ()) or (),
                    source="image_social_retry_empty",
                )
            else:
                _rescue_raw_text, _rescue_first_token_ms = await _attempt_soul_rescue(
                    previous_candidate_text=narrative,
                )
                if first_token_ms is None and _rescue_first_token_ms is not None:
                    first_token_ms = _rescue_first_token_ms
                if _rescue_raw_text.strip():
                    _rescue_narrative, _rescue_ri_meta, _rescue_pii = _finalize_candidate_text(
                        _rescue_raw_text,
                        allow_rewrite=True,
                    )
                    _rescue_validation = validate_reply(
                        text=_rescue_narrative,
                        plan=reply_plan_decision,
                        user_message=user_message,
                        previous_assistant_text=previous_assistant_text,
                    )
                    trace_stage_meta["reply_validation"]["rescue"] = _rescue_validation.as_dict()
                    trace_stage_meta["reply_validation"]["rescue_text"] = _trace_text_sample(_rescue_narrative)
                    if _is_validation_acceptable(_rescue_validation):
                        executed_tactic_family = str(
                            _rescue_validation.actual_tactic_family
                            or reply_plan_decision.tactic_family
                            or ""
                        ).strip()
                        executed_tactic_variant = str(
                            _rescue_validation.actual_tactic_variant
                            or reply_plan_decision.tactic_variant
                            or ""
                        ).strip()
                        executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip()
                        full_text = _rescue_narrative
                        narrative = _rescue_narrative
                        response_intelligence_meta = _rescue_ri_meta
                        _pii_egress_stats = _rescue_pii
                        tts_text = _extract_tts_text(full_text)
                        if not _rescue_validation.is_valid:
                            trace_stage_meta["reply_validation"]["accepted_with_warnings"] = {
                                "quality_score": float(getattr(_rescue_validation, "quality_score", 0.0) or 0.0),
                                "reason_codes": list(getattr(_rescue_validation, "reason_codes", ()) or ()),
                            }
                    else:
                        _apply_validation_fallback(
                            reason_codes=getattr(_rescue_validation, "reason_codes", ()) or (),
                            source="retry_empty_rescue_invalid",
                        )
                else:
                    _apply_validation_fallback(
                        reason_codes=getattr(_validation, "reason_codes", ()) or (),
                        source="retry_empty_rescue_empty",
                    )
        else:
            _rescue_raw_text, _rescue_first_token_ms = await _attempt_soul_rescue(
                previous_candidate_text=narrative,
            )
            if first_token_ms is None and _rescue_first_token_ms is not None:
                first_token_ms = _rescue_first_token_ms
            if _rescue_raw_text.strip():
                _rescue_narrative, _rescue_ri_meta, _rescue_pii = _finalize_candidate_text(
                    _rescue_raw_text,
                    allow_rewrite=True,
                )
                _rescue_validation = validate_reply(
                    text=_rescue_narrative,
                    plan=reply_plan_decision,
                    user_message=user_message,
                    previous_assistant_text=previous_assistant_text,
                )
                trace_stage_meta["reply_validation"]["rescue"] = _rescue_validation.as_dict()
                trace_stage_meta["reply_validation"]["rescue_text"] = _trace_text_sample(_rescue_narrative)
                if _is_validation_acceptable(_rescue_validation):
                    executed_tactic_family = str(
                        _rescue_validation.actual_tactic_family
                        or reply_plan_decision.tactic_family
                        or ""
                    ).strip()
                    executed_tactic_variant = str(
                        _rescue_validation.actual_tactic_variant
                        or reply_plan_decision.tactic_variant
                        or ""
                    ).strip()
                    executed_trajectory_stage = str(reply_plan_decision.trajectory_stage or "").strip()
                    full_text = _rescue_narrative
                    narrative = _rescue_narrative
                    response_intelligence_meta = _rescue_ri_meta
                    _pii_egress_stats = _rescue_pii
                    tts_text = _extract_tts_text(full_text)
                    if not _rescue_validation.is_valid:
                        trace_stage_meta["reply_validation"]["accepted_with_warnings"] = {
                            "quality_score": float(getattr(_rescue_validation, "quality_score", 0.0) or 0.0),
                            "reason_codes": list(getattr(_rescue_validation, "reason_codes", ()) or ()),
                        }
                else:
                    _apply_validation_fallback(
                        reason_codes=getattr(_rescue_validation, "reason_codes", ()) or (),
                        source="initial_rescue_invalid",
                    )
            else:
                _apply_validation_fallback(
                    reason_codes=getattr(_validation, "reason_codes", ()) or (),
                    source="initial_rescue_empty",
                )
    if not str(narrative or "").strip() and not suppress_final_reply:
        suppress_final_reply = True
        suppress_final_reply_reason_code = "EMPTY_REPLY"
        trace_stage_meta.setdefault("reply_validation", {})
        trace_stage_meta["reply_validation"]["empty_reply_suppressed"] = {
            "used": False,
            "suppressed": True,
            "reason_code": suppress_final_reply_reason_code,
        }
        trace_stage_meta["fallback_used"] = False
        trace_stage_meta["reply_source"] = "suppressed_no_reply"
    with soft_fail(
        "chat_agent.response_intelligence_ms",
        fallback=0,
        expected_exc=(TypeError, ValueError, OverflowError),
    ) as _rims_box:
        _rims_box[0] = max(0, int(time.time() * 1000) - _response_intelligence_started_ms)
    response_intelligence_ms = _rims_box[0]
    sanitize_egress_ms = response_intelligence_ms

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

    remainder = splitter.flush()
    if remainder:
        yield _sse({
            "phase": "SENTENCE",
            "sentence": remainder,
            "sentence_index": sentence_index,
        })
        sentence_index += 1

    # ── 7. INPUT_COMPLETED 同步写入 ──────────────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms
    llm_latency_ms = int(time.time() * 1000) - llm_started_ms
    _precommit_started_ms = int(time.time() * 1000)

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
    finally:
        precommit_log_ms = max(0, int(time.time() * 1000) - _precommit_started_ms)

    timings_ms = {
        "prompt_build_ms": prompt_build_ms,
        "llm_runtime_resolve_ms": llm_runtime_resolve_ms,
        "stream_after_first_token_ms": (
            max(0, llm_latency_ms - int(first_token_ms or 0))
            if first_token_ms is not None
            else llm_latency_ms
        ),
        "response_intelligence_ms": response_intelligence_ms,
        "sanitize_egress_ms": sanitize_egress_ms,
        "precommit_log_ms": precommit_log_ms,
    }
    _logger.info("[%s] ChatAgent timings_ms=%s", request_id, timings_ms)

    # ── 8. COMMIT ────────────────────────────────────────────────────
    commit_decision = "NO_REPLY" if suppress_final_reply else "APPROVE"
    from host_brain.app.reply_envelope import envelope_from_commit  # noqa: PLC0415

    reply_envelope = envelope_from_commit(
        decision=commit_decision,
        narrative=narrative,
        reason_code=suppress_final_reply_reason_code,
        response_form=str(reply_plan_decision.response_form or "text").strip() or "text",
        source=str(trace_stage_meta.get("reply_source") or ("surface" if deterministic_reply else "full_model")).strip(),
        degraded=bool(reply_used_fallback or suppress_final_reply),
        cacheable=bool(trace_stage_meta.get("reply_cacheable", True)),
        audit={
            "request_id": request_id,
            "suppress_final_reply": bool(suppress_final_reply),
            "reason_code": suppress_final_reply_reason_code,
        },
    )
    yield _sse({
        "phase": "COMMIT",
        "done": True,
        "decision": commit_decision,
        "reason_code": suppress_final_reply_reason_code,
        "narrative": narrative,
        "request_id": request_id,
        "reply_envelope": reply_envelope.as_dict(),
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
            "tts_text_ja": tts_text or None,
            "response_form": str(reply_plan_decision.response_form or "text").strip() or "text",
            "response_form_reason": str(reply_plan_decision.response_form_reason or "").strip(),
            "timings_ms": timings_ms,
            "response_intelligence": response_intelligence_meta,
            "pii_stats": {"egress": _pii_egress_stats} if _pii_egress_stats else {},
            "suppress_final_reply": bool(suppress_final_reply),
            "suppress_final_reply_reason_code": suppress_final_reply_reason_code,
            "reply_envelope": reply_envelope.as_dict(),
        },
    })

    # ── 9a. G1: COMMIT 后兜底触发 TTS（补发未被分句流式覆盖的 narrative）──
    # 条件：suppress_edge_tts=True 时分句 TTS 被跳过，或分句流式 TTS 未触发时补发完整 narrative。
    # Gateway 场景已有 input_routes 统一负责语音下发，这里跳过以避免重复播报。
    if _should_schedule_commit_tts_fallback(
        narrative=narrative,
        tts_sentence_index=tts_sentence_index,
        suppress_tts=_suppress_tts,
        reply_channel=agent_input.reply_channel,
        voice_outputs_enabled=_voice_outputs_enabled,
    ):
        _commit_emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        _commit_emotion_intensity: float | None = None
        with soft_fail("chat_agent.commit_tts_broca_params", fallback=None):
            from host_brain.app.chat_agent_postprocessor import broca_to_tts_params  # noqa: PLC0415
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415
            _lang_agent = get_language_system_agent()
            _last_broca = dict((_lang_agent.state_registry.get_state("language_system_agent") or {}).get("state", {}).get("last_broca") or {})
            if _last_broca:
                _tts_params = broca_to_tts_params(
                    tone=str(_last_broca.get("tone") or "balanced"),
                    warmth_weight=float(_last_broca.get("warmth_weight") or 0.5),
                    emotion_tag=_commit_emotion_tag,
                )
                _commit_emotion_tag = _tts_params["emotion_tag"]
                _commit_emotion_intensity = _tts_params["emotion_intensity"]
        try:
            from host_brain.app.chat_agent_postprocessor import trigger_tts_after_commit  # noqa: PLC0415
            asyncio.create_task(trigger_tts_after_commit(
                narrative=narrative,
                request_id=request_id,
                session_id=session_id,
                emotion_tag=_commit_emotion_tag,
                emotion_intensity=_commit_emotion_intensity,
                suppress_tts=False,   # 此处已判断条件，强制发送
                source="chat_commit_fallback",
            ))
        except Exception as _tts_exc:
            _logger.debug("[%s] G1 trigger_tts_after_commit schedule failed: %s", request_id, _tts_exc)

    # ── 9. 异步后处理（fire-and-forget）─────────────────────────────
    # 先走中性共享模块，保持 cortex_orchestrator 链路上的后处理收口一致
    try:
        _actual_tactic_signature = ""
        if narrative.strip() and str(reply_plan_decision.primary_topic or "").strip():
            _actual_tactic = infer_reply_tactic_shape(
                text=narrative,
                topic=str(reply_plan_decision.primary_topic or "").strip(),
            )
            _actual_family = str(_actual_tactic.get("family") or "").strip()
            _actual_variant = str(_actual_tactic.get("variant") or "").strip()
            _actual_tactic_signature = str(_actual_tactic.get("signature") or "").strip()
            if _actual_family:
                executed_tactic_family = _actual_family
            if _actual_variant:
                executed_tactic_variant = _actual_variant
        reply_plan_for_commit = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
        if executed_tactic_family:
            reply_plan_for_commit["tactic_family"] = executed_tactic_family
        if executed_tactic_variant:
            reply_plan_for_commit["tactic_variant"] = executed_tactic_variant
        if executed_trajectory_stage:
            reply_plan_for_commit["trajectory_stage"] = executed_trajectory_stage
        if reply_used_fallback:
            reply_plan_for_commit["fallback_used"] = True
        if suppress_final_reply:
            reply_plan_for_commit["suppress_final_reply"] = True
            reply_plan_for_commit["suppress_final_reply_reason_code"] = suppress_final_reply_reason_code
        _ab_eval_mode = str(os.environ.get("AB_EVAL_MODE") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        } or bool(trace_stage_meta.get("ab_eval"))
        if _ab_eval_mode:
            trace_stage_meta["post_process_skipped"] = "AB_EVAL_MODE"
        else:
            from host_brain.app.agent_runtime_shared import _async_post_process  # noqa: PLC0415
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
                    [{
                        "agent_id": sub_agent_ctx.agent_id,
                        "success": sub_agent_ctx.success,
                        "latency_ms": sub_agent_ctx.latency_ms,
                        "error": sub_agent_ctx.error,
                        "audit_refs": (
                            dict((sub_agent_ctx.payload or {}).get("audit_refs") or {})
                            if isinstance(sub_agent_ctx.payload, dict)
                            else {}
                        ),
                    }]
                    if sub_agent_ctx else []
                ),
                retrieval_summary=retrieval_summary,
                gov_decision={"decision": commit_decision},
                pii_ingress_stats={},
                pii_egress_stats=_pii_egress_stats,
                # Phase XV trace 增强：传入 LLM 输入摘要和召回证据
                llm_input_digest=json.dumps(prompt_messages, ensure_ascii=False)[:500] or None,
                retrieval_evidence_items=list(retrieval_evidence_items or []),
                motive_state=prompt_runtime_payload.get("motive_state"),
                reply_plan=reply_plan_for_commit,
                trace_stage_meta={
                    **dict(trace_stage_meta or {}),
                    "executed_tactic_family": executed_tactic_family,
                    "executed_tactic_variant": executed_tactic_variant,
                    "executed_tactic_signature": _actual_tactic_signature or None,
                    "reply_used_fallback": reply_used_fallback,
                    "suppress_final_reply": bool(suppress_final_reply),
                    "suppress_final_reply_reason_code": suppress_final_reply_reason_code,
                    "reply_envelope": reply_envelope.as_dict(),
                    "voice_outputs_enabled": _voice_outputs_enabled,
                },
            )
            )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)


# ---------------------------------------------------------------------------
# 聚合导入（P2-12 拆分后向后兼容）
# ---------------------------------------------------------------------------
# chat_agent owns final reply rendering. Do not import fallback/render/buffering
# functions from the preprocessor here, otherwise module globals are overwritten
# after definition and the normal chat path becomes split-brain again.
from host_brain.app.chat_agent_preprocessor import (  # noqa: F401, E402
    _build_sub_agent_result_preserving_fallback_reply,
    _should_preserve_sub_agent_result_for_user,
)

# 后处理函数已移至 chat_agent_postprocessor.py
from host_brain.app.chat_agent_postprocessor import (  # noqa: F401, E402
    _post_speak_sentence,
    scrub_pii_egress,
    log_input_completed,
    schedule_async_post_process,
)
