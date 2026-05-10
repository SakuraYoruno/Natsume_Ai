"""chat_agent_preprocessor.py — ChatAgent 消息预处理与工具函数。

拆分自 chat_agent.py（P2-12）：
  - 输入/输出结构体（SubAgentContext、ChatAgentInput）
  - 工具函数（SSE、净化、截断、策略守卫等）
  - 子 Agent 结果渲染（确定性回复、Hub Skill 回复、Plan 执行回复）
  - 快速 ACK/进度/纠偏文本渲染
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from host_brain.app.relational_chat_detector import is_relational_chat_message
from host_brain.codex_runtime.skill_ids import CODEX_RUNTIME_SKILL_IDS, is_codex_runtime_skill

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
    request_id: str = ""
    session_id: str = ""
    user_message: str = ""
    person_id: str = ""
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: Dict[str, Any] = field(default_factory=dict)
    bond_summary: Dict[str, Any] = field(default_factory=dict)
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})
    sub_agent_context: Optional[SubAgentContext] = None
    render_target_lang: str = "zh"
    reply_channel: Optional[str] = None
    response_form: str = ""
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    reply_depth_policy: str = "normal"  # legacy metadata only; transport source must not alter text generation
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)
    trace_stage_meta: Dict[str, Any] = field(default_factory=dict)
    llm_runtime: Optional[Dict[str, Any]] = None
    render_policy: Dict[str, Any] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    multimodal_observations: Dict[str, Any] = field(default_factory=dict)
    attachment_summary: str = ""
    eval_history_turns: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_output(text: str) -> str:
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
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _coerce_render_policy(raw_policy: Any) -> Dict[str, Any]:
    if isinstance(raw_policy, Mapping):
        return dict(raw_policy)
    return {}


def _split_text_into_sentences(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    from host_brain.app.sentence_splitter import SentenceSplitter
    splitter = SentenceSplitter()
    sentences = list(splitter.feed(normalized))
    remainder = splitter.flush()
    if remainder:
        sentences.append(remainder)
    return [str(s).strip() for s in sentences if str(s).strip()]


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
    payload = _SafeTemplateDict({str(key): "" if value is None else str(value) for key, value in kwargs.items()})
    try:
        return text.format_map(payload)
    except Exception:
        return text


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


def _normalize_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _resolve_localized_template_value(value: Any, *, render_target_lang: str | None = None) -> Any:
    if isinstance(value, Mapping):
        keys = {str(k).strip() for k in value.keys()}
        if keys.issubset({"zh", "ja", "default"}) and ("zh" in keys or "ja" in keys):
            lang_key = _normalize_template_lang(render_target_lang)
            return value.get(lang_key) or value.get("zh") or value.get("ja") or value.get("default")
    return value


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


def _runtime_text(key: str, *, render_target_lang: str | None = None, **kwargs: Any) -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
    return _mapping_text(templates, key, render_target_lang=render_target_lang, **kwargs)


# ---------------------------------------------------------------------------
# 快速 ACK / 进度 / 纠偏文本渲染
# ---------------------------------------------------------------------------
def _looks_like_open_app_request(user_message: str) -> bool:
    normalized = re.sub(r"\s+", "", str(user_message or "")).strip()
    if not normalized:
        return False
    return bool(_OPEN_APP_VERB_RE.search(normalized) and _OPEN_APP_HINT_RE.search(normalized))


def _format_elapsed_compact(elapsed_ms: int) -> str:
    total_seconds = max(0, int(elapsed_ms // 1000))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def render_quick_gateway_ack_text(*, user_message: str = "", bond_stage: str = "STRANGER", emotion_tag: str = "NEUTRAL", sender_name: str | None = None) -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
    stage = str(bond_stage or "STRANGER").strip().upper() or "STRANGER"
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
        text = _mapping_text(templates, "quick_ack_stranger", sender_name=clean_sender)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_ack_default", sender_name=clean_sender)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_ack_fallback", sender_name=clean_sender)
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    if len(normalized) > 60:
        normalized = normalized[:57].rstrip() + "..."
    _ = user_message
    return normalized


def render_quick_gateway_progress_text(*, status: str = "UNKNOWN", target_id: str = "", progress_message: str = "", result_text: str = "", elapsed_ms: int = 0, bond_stage: str = "STRANGER", emotion_tag: str = "NEUTRAL", sender_name: str | None = None) -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
    normalized_status = str(status or "UNKNOWN").strip().upper() or "UNKNOWN"
    elapsed = _format_elapsed_compact(int(elapsed_ms or 0))
    target = str(target_id or "").strip() or _mapping_text(templates, "default_task_target")
    progress = str(progress_message or "").strip()
    result = str(result_text or "").strip()
    clean_sender = str(sender_name or "").strip()
    if normalized_status in {"RUNNING", "RECOVERING"}:
        text = _mapping_text(templates, "quick_progress_running", sender_name=clean_sender, target_id=target, elapsed=elapsed, progress_message=progress, result_text=result)
    elif normalized_status == "COMPLETED":
        text = _mapping_text(templates, "quick_progress_completed", sender_name=clean_sender, target_id=target, elapsed=elapsed, progress_message=progress, result_text=result)
    elif normalized_status == "FAILED":
        text = _mapping_text(templates, "quick_progress_failed", sender_name=clean_sender, target_id=target, elapsed=elapsed, progress_message=progress, result_text=result)
    elif normalized_status in {"NONE", "EMPTY"}:
        text = _mapping_text(templates, "quick_progress_missing", sender_name=clean_sender)
    else:
        text = _mapping_text(templates, "quick_progress_default", sender_name=clean_sender, target_id=target, elapsed=elapsed, progress_message=progress, result_text=result)
    if not str(text or "").strip():
        text = _mapping_text(templates, "quick_progress_fallback", sender_name=clean_sender)
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    if len(normalized) > 120:
        normalized = normalized[:117].rstrip() + "..."
    return normalized


def render_quick_gateway_realign_text(*, sender_name: str | None = None, bond_stage: str = "STRANGER", emotion_tag: str = "NEUTRAL") -> str:
    templates = _load_template_mapping("chat_agent.runtime_texts")
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


# ---------------------------------------------------------------------------
# 响应策略守卫
# ---------------------------------------------------------------------------
def _looks_like_followup_sentence(sentence: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(sentence or "")).strip()
    if not normalized:
        return False
    followup_markers = ("你呢", "要不要", "想不想", "还要不要", "要继续吗", "如果你愿意", "方便的话", "可以告诉我", "能不能", "要不要我", "要我继续", "还想", "要聊聊吗", "愿意的话", "想继续", "要不要继续")
    if any(marker in normalized for marker in followup_markers):
        return True
    return bool(re.search(r"[?？]$", normalized))


def _soften_followup_sentence(sentence: str) -> str:
    normalized = str(sentence or "").strip()
    if not normalized:
        return ""
    patterns = [r"[，,、]?(?:你呢|你觉得呢|你怎么看呢|你怎么看|你觉得怎么样呢|你觉得怎么样)[。！？!?？]*$", r"[，,、]?(?:要不要|想不想|还要不要|要不要继续|要继续吗).*[。！？!?？]*$", r"[，,、]?(?:如果你愿意|方便的话|愿意的话|可以的话).*[。！？!?？]*$", r"[，,、]?(?:可以告诉我|能不能告诉我|要不要和我说说).*[。！？!?？]*$"]
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
        "我爱你", "爱你", "好喜欢你", "最喜欢你", "喜欢你", "好想你", "想你了", "想你",
        "离不开你", "不能没有你", "只想和你", "属于你", "亲亲", "抱抱你", "一直陪着你", "永远陪着你", "会一直等你",
        "愛してる", "好き", "大好き", "会いたい", "抱きしめたい", "ずっと一緒", "そばにいたい",
    )
    return any(marker in normalized for marker in markers)


def _contains_intimacy_marker(sentence: str) -> bool:
    normalized = str(sentence or "")
    if _contains_confession_marker(normalized):
        return True
    markers = (
        "亲爱的", "宝贝", "宝宝", "乖乖", "抱抱", "亲亲", "喜欢你", "好喜欢你", "最喜欢你", "想你", "好想你", "想你了",
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


def _drop_trailing_policy_sentences(sentences: list[str], *, min_sentences: int, predicate) -> list[str]:
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
    return any([not bool(policy.get("allow_followup", True)), not bool(policy.get("allow_topic_shift", True)), bool(policy.get("confession_guard", False)), bool(policy.get("withdrawal_guard", False)), bool(policy.get("should_be_withdrawn", False)), intimacy_ceiling <= 0.55, humor_ceiling <= 0.35])


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
            sentences = _drop_trailing_policy_sentences(sentences, min_sentences=min_sentences, predicate=_looks_like_topic_shift_sentence)
        if not bool(policy.get("allow_followup", True)):
            sentences = _drop_trailing_policy_sentences(sentences, min_sentences=min_sentences, predicate=_looks_like_followup_sentence)
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
        normalized = "".join(str(s).strip() for s in sentences if str(s).strip()).strip()
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

# ---------------------------------------------------------------------------
# 降级回复构建
# ---------------------------------------------------------------------------
def _build_minimal_chat_fallback_reply(*, user_message: str, sub_agent_ctx: SubAgentContext | None, render_target_lang: str) -> str:
    from host_brain.persona.fallback_policy import (  # noqa: PLC0415
        FallbackRequest,
        build_fallback_reply,
    )

    text = str(user_message or "").strip()
    if sub_agent_ctx is not None:
        agent_id = str(sub_agent_ctx.agent_id or "").strip()
        if is_codex_runtime_skill(agent_id):
            return _resolve_minimal_fallback_text(kind="codex_worker", render_target_lang=render_target_lang)
        if agent_id == "recent_turn_direct":
            return _resolve_minimal_fallback_text(kind="recent_turn_direct", render_target_lang=render_target_lang)
    if _FALLBACK_STRENGTH_RE.search(text):
        return _build_installed_capabilities_reply(render_target_lang=render_target_lang)
    return build_fallback_reply(
        FallbackRequest(
            reason_code="chat_agent_preprocessor_minimal_degraded_fallback",
            render_target_lang=render_target_lang,
            user_message=text,
        )
    ).text


def _build_chat_agent_quality_fallback_reply(*, user_message: str, sub_agent_ctx: SubAgentContext | None, bond_summary: Dict[str, Any] | None, emotion_context: Dict[str, Any] | None, render_target_lang: str, render_policy: Any = None, prompt_runtime_payload: Mapping[str, Any] | None = None) -> str:
    from host_brain.persona.fallback_policy import (  # noqa: PLC0415
        FallbackRequest,
        build_fallback_reply,
    )

    delegated_fallback = _build_sub_agent_result_preserving_fallback_reply(
        sub_agent_ctx,
        render_target_lang=render_target_lang,
        user_message=user_message,
    )
    if delegated_fallback:
        return delegated_fallback
    _ = bond_summary, emotion_context, render_policy, prompt_runtime_payload
    return build_fallback_reply(
        FallbackRequest(
            reason_code="chat_agent_preprocessor_degraded_fallback",
            render_target_lang=render_target_lang,
            user_message=user_message,
        )
    ).text


def _build_chat_agent_fallback_reply(*, user_message: str, sub_agent_ctx: SubAgentContext | None, bond_summary: Dict[str, Any] | None, render_target_lang: str) -> str:
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
        from host_brain.app.dependencies import get_skill_registry
        from host_brain.app.agents.tool_registry import get_tools_for_agent
        from host_brain.app.sub_agent_protocol import get_agent_capabilities
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
    except Exception:
        return _mapping_text(templates, "fallback", render_target_lang=render_target_lang)


def _should_buffer_stream_output(*, emotion_context: Any, bond_summary: Any, render_policy: Any = None) -> bool:
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
        from host_brain.app.dependencies import get_reflection_chain
        reflection = get_reflection_chain()
        return bool(reflection.should_reflect(emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""), intent=str((bond_summary or {}).get("intent") or ""), bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"), turn_count=int((emotion_context or {}).get("turn_count") or 1)))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染
# ---------------------------------------------------------------------------
def _normalize_plan_step_description(description: str, *, render_target_lang: str = "zh") -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:蓝蓝|夏目蓝|夏目藍|小蓝|阿蓝)[，,、:：\s]*", "", text)
    lang = _normalize_template_lang(render_target_lang)
    prefixes = ("先", "然后", "再", "接着", "顺便", "帮我", "给我", "请") if lang == "zh" else ("まず", "それから", "次に", "続けて", "ついでに", "私に", "どうか", "お願いします")
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
    target_id = str(step.get("target_id") or _runtime_text("default_step_target", render_target_lang=render_target_lang)).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""), render_target_lang=render_target_lang)
    result_text = str(step.get("result_text") or "").strip()
    description_or_target = description or target_id
    # 特殊 target_id 处理
    special_targets = {"web_search", "memory_store", "system_info", "process_list", "network_status", "hub_skill_search", "hub_skill_list", "hub_skill_install", "hub_skill_remove", "file_read", "file_write", "file_list", *CODEX_RUNTIME_SKILL_IDS, "intent_agent"}
    if target_id in special_targets:
        if target_id == "web_search":
            topic = description or _runtime_text("default_topic", render_target_lang=render_target_lang)
            if _normalize_template_lang(render_target_lang) == "ja":
                topic = re.sub(r"^(?:検索して|探して|調べて|見つけて)\s*", "", topic).strip() or _runtime_text("default_topic", render_target_lang=render_target_lang)
            else:
                topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
            return _mapping_text(success_templates, "web_search", render_target_lang=render_target_lang, step_index=step_index, topic=(topic or _runtime_text("default_topic", render_target_lang=render_target_lang)))
        if target_id == "memory_store":
            remember_prefix = "覚えて" if _normalize_template_lang(render_target_lang) == "ja" else "记住"
            if description.startswith(remember_prefix):
                remembered = description.removeprefix(remember_prefix).strip()
                if remembered:
                    if _normalize_template_lang(render_target_lang) == "zh" and remembered.startswith("我"):
                        remembered = "你" + remembered[1:]
                    return _mapping_text(success_templates, "memory_store.remembered", render_target_lang=render_target_lang, step_index=step_index, remembered=remembered)
            return _mapping_text(success_templates, "memory_store.default", render_target_lang=render_target_lang, step_index=step_index)
        if is_codex_runtime_skill(target_id):
            return _mapping_text(success_templates, "codex_worker", render_target_lang=render_target_lang, step_index=step_index)
        return _mapping_text(success_templates, target_id, render_target_lang=render_target_lang, step_index=step_index)
    if result_text:
        return _mapping_text(success_templates, "__with_result", render_target_lang=render_target_lang, step_index=step_index, description_or_target=description_or_target)
    return _mapping_text(success_templates, "__default", render_target_lang=render_target_lang, step_index=step_index, description_or_target=description_or_target)


def _render_plan_step_failure_cn(step: Dict[str, Any], *, render_target_lang: str = "zh") -> str:
    templates = _load_template_mapping("chat_agent.plan_execution.reply_templates")
    failure_templates = _mapping_section(templates, "step_failure", render_target_lang=render_target_lang)
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or _runtime_text("default_step_target", render_target_lang=render_target_lang)).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""), render_target_lang=render_target_lang)
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = _mapping_text(failure_templates, "base", render_target_lang=render_target_lang, step_index=step_index, description_or_target=(description or target_id))
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
    intro = (_mapping_text(summary_templates, "intro_parallel", render_target_lang=render_target_lang, total_steps=total_steps) if parallel_enabled and total_steps > 1 else _mapping_text(summary_templates, "intro_sequential", render_target_lang=render_target_lang, total_steps=total_steps))
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step, render_target_lang=render_target_lang))
        else:
            parts.append(_render_plan_step_failure_cn(step, render_target_lang=render_target_lang))
    parts.append(_mapping_text(summary_templates, "all_done", render_target_lang=render_target_lang) if success_count == len(steps) else _mapping_text(summary_templates, "partial_done", render_target_lang=render_target_lang))
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
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("安装 hub 技能", "安装技能", "安装skill"), default_name=_default_skill_name)).strip() or _default_skill_name
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
            parts.append(_mapping_text(templates, "install_repair_success_count", render_target_lang=render_target_lang, repair_actions=repair_actions) if repair_actions > 0 else _mapping_text(templates, "install_repair_success_generic", render_target_lang=render_target_lang))
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
    query = str(detail.get("query") or payload.get("query") or _infer_hub_skill_name_from_task(ctx.task, markers=("搜索技能", "查找技能", "搜技能"), default_name=_default_skill_query)).strip() or _default_skill_query
    registry_error = str(detail.get("registry_error") or "").strip()
    discovery_sources = list(detail.get("discovery_sources") or [])
    if total <= 0:
        if registry_error:
            source_names = "、".join([str(item.get("name") if isinstance(item, dict) else item or "").strip() for item in discovery_sources if str(item.get("name") if isinstance(item, dict) else item or "").strip()])
            if source_names:
                return _mapping_text(templates, "search_registry_unavailable_with_sources", render_target_lang=render_target_lang, registry_error=registry_error, source_names=source_names)
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
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("卸载技能", "删除技能"), default_name=_default_skill_name)).strip() or _default_skill_name
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


def _infer_open_app_name(task_text: str, *, render_target_lang: str) -> str:
    normalized = str(task_text or "").strip()
    if not normalized:
        return _mapping_text(
            _load_template_mapping("chat_agent.system_skill.reply_templates"),
            "default_app_name",
            render_target_lang=render_target_lang,
        )
    normalized = re.sub(r"^(?:蓝蓝|夏目蓝|夏目藍|natsume ai|natsume)[，,\s:：]*", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"(可以|可不可以|能不能|能否|帮我|替我|把|一下|一下子|本地的|本地|应用程序|应用|软件|程序)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(打开|启动|运行|launch|open)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip("，。；！？,.!? ：:　 ")
    return normalized or _mapping_text(
        _load_template_mapping("chat_agent.system_skill.reply_templates"),
        "default_app_name",
        render_target_lang=render_target_lang,
    )


def _build_system_skill_reply(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    agent_id = str(ctx.agent_id or "").strip()
    if agent_id != "open_app":
        return None
    templates = _load_template_mapping("chat_agent.system_skill.reply_templates")
    payload = dict(ctx.payload or {})
    result = dict(payload.get("result") or {})
    detail_source = result.get("detail") or payload.get("detail") or result
    detail = dict(detail_source or {}) if isinstance(detail_source, Mapping) else {}
    success = bool(detail.get("success", ctx.success))
    app_name = str(
        detail.get("app_name")
        or payload.get("app_name")
        or _infer_open_app_name(ctx.task, render_target_lang=render_target_lang)
    ).strip() or _mapping_text(templates, "default_app_name", render_target_lang=render_target_lang)
    message = str(
        detail.get("detail")
        or detail.get("message")
        or result.get("error")
        or ctx.error
        or ctx.result_text
        or ""
    ).strip()
    if success:
        return _mapping_text(templates, "open_app_success", render_target_lang=render_target_lang, name=app_name)
    reply = _mapping_text(templates, "open_app_failure", render_target_lang=render_target_lang, name=app_name)
    generic_failure_messages = {
        "抱歉，我这边没能把这个应用打开。",
        "I couldn't open the app.",
    }
    if message and message not in generic_failure_messages:
        reply += _mapping_text(
            templates,
            "open_app_failure_reason",
            render_target_lang=render_target_lang,
            message=str(message).rstrip("。.!！?？"),
        )
    return reply


def _looks_like_internal_sub_agent_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if normalized.startswith("[ReAct"):
        return True
    if normalized.startswith("ReAct 已达到最大步数限制"):
        return True
    if normalized.startswith("[RepairAgent"):
        return True
    return any(marker in normalized for marker in ("stopped_reason=", "agent_id=", "repair_agent_trace"))


def _should_preserve_sub_agent_result_for_user(*, user_message: str, agent_id: str) -> bool:
    normalized_agent_id = str(agent_id or "").strip()
    if normalized_agent_id != "intent_agent":
        return True
    return not is_relational_chat_message(user_message)


def _build_sub_agent_result_preserving_fallback_reply(ctx: SubAgentContext | None, *, render_target_lang: str, user_message: str = "") -> str | None:
    if ctx is None:
        return None
    if not bool(ctx.success) or str(ctx.error or "").strip():
        return None
    agent_id = str(ctx.agent_id or "").strip()
    if not _should_preserve_sub_agent_result_for_user(user_message=user_message, agent_id=agent_id):
        return None
    if agent_id == "open_app":
        return _build_system_skill_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id.startswith("skill:hub_skill_"):
        return _build_hub_skill_reply_cn(ctx, render_target_lang=render_target_lang)
    result_text = _strip_internal_delegate_markers(str(ctx.result_text or ""))
    result_text = _extract_visible_text(result_text, render_target_lang=render_target_lang)
    result_text = re.sub(r"\n{3,}", "\n\n", result_text).strip()
    if not result_text or _looks_like_internal_sub_agent_result(result_text):
        return None
    return result_text


def _build_deterministic_sub_agent_reply(ctx: SubAgentContext | None, *, render_target_lang: str, user_message: str = "") -> str | None:
    if ctx is None:
        return None
    agent_id = str(ctx.agent_id or "").strip()
    result_text = str(ctx.result_text or "").strip()
    if agent_id == "open_app":
        return _build_system_skill_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx, render_target_lang=render_target_lang)
    if agent_id.startswith("skill:hub_skill_"):
        return _build_hub_skill_reply_cn(ctx, render_target_lang=render_target_lang)
    if is_codex_runtime_skill(agent_id) or agent_id in {"translate_agent", "intent_agent"}:
        return _build_sub_agent_result_preserving_fallback_reply(ctx, render_target_lang=render_target_lang, user_message=user_message)
    if agent_id in {"recent_turn_direct", "task_progress_direct"}:
        return result_text or None
    return None


def _build_sub_agent_context_prompt(ctx: SubAgentContext, *, render_target_lang: str = "zh") -> str:
    """将子 Agent 结果转换为注入 ChatAgent prompt 的上下文块。"""
    if not ctx:
        return ""
    templates = _load_template_mapping("chat_agent.sub_agent_context.prompt_templates")
    agent_id = str(ctx.agent_id or "").strip()
    task = str(ctx.task or "").strip()
    result_text = str(ctx.result_text or "").strip()[:2000]
    if not ctx.success or ctx.error:
        recovery_hint = (_runtime_text("sub_agent_recovery_hint_line", render_target_lang=render_target_lang, result_text=result_text) if result_text else "")
        return _mapping_text(templates, "failure", render_target_lang=render_target_lang, agent_id=agent_id, task=task, error_text=(ctx.error or _runtime_text("unknown_error", render_target_lang=render_target_lang)), recovery_hint=recovery_hint)
    if not result_text:
        return ""
    if is_codex_runtime_skill(agent_id):
        return _mapping_text(templates, "codex_worker", render_target_lang=render_target_lang, agent_id=agent_id, task=task, result_text=result_text)
    if agent_id == "recent_turn_direct":
        return _mapping_text(templates, "recent_turn_direct", render_target_lang=render_target_lang, result_text=result_text)
    if agent_id == "task_progress_direct":
        return _mapping_text(templates, "task_progress_direct", render_target_lang=render_target_lang, result_text=result_text)
    if agent_id == "plan_execution":
        return _mapping_text(templates, "plan_execution", render_target_lang=render_target_lang, task=task, result_text=result_text)
    return _mapping_text(templates, "default", render_target_lang=render_target_lang, agent_id=agent_id, task=task, result_text=result_text)


# ---------------------------------------------------------------------------
# LLM 输出提取
# ---------------------------------------------------------------------------
def _extract_visible_text(raw: str, *, render_target_lang: str = "zh") -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    # Some upstream providers may leak chain-of-thought style blocks when they
    # degrade or retry. Strip them at the final visible-text boundary too.
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
                for key in ("final_answer", "result", "output", "text", "message"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text


def _extract_tts_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text_ja") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return ""


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
