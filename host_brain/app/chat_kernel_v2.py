from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from host_brain.app.context.interaction_facts_plane import InteractionFactsPlane
from host_brain.app.context.soul_runtime import CompiledSoul, get_compiled_soul
from host_brain.app.reply_performance import ReplyPerformance, normalize_reply_performance
from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.app.response_form_contract import (
    derive_render_profile_for_response_form,
    is_media_response_form,
    normalize_response_form,
)
from host_brain.integrations.llm_helpers import acomplete_llm_with_pool, astream_llm_chunks_with_pool
from host_brain.persona.semantic_reranker import (
    SemanticCandidate,
    SemanticRerankContext,
    SemanticRerankResult,
    SemanticScore,
    get_semantic_reranker,
)
from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.galgame_text_scorer import score_galgame_candidate
from host_brain.persona.text_quality_gate import TextQualityGateResult, extract_sendable_dialogue, get_text_quality_gate
from host_brain.persona.positive_style_bank import render_positive_style_guidance
from host_brain.persona.style_reference_bank import (
    StyleReferenceQuery,
    get_style_reference_bank,
    render_style_guidance,
)

DEFAULT_CHAT_KERNEL_PROVIDER = "deepseek"
DEFAULT_CHAT_KERNEL_MODEL_ID = "deepseek-v4-flash"

LIGHT_DAILY_SCENES = frozenset(
    {
        "daily_good_morning",
        "daily_greeting_en",
        "daily_greeting_cn",
        "presence",
        "wait",
        "back",
        "busy",
        "bored",
        "tired",
        "nothing",
        "small_failure",
        "good_news",
        "good_night",
        "meal_share",
        "hungry",
        "sleepy",
        "bad_mood",
        "what_doing",
        "play_invite",
        "quiet",
        "talkative",
        "who_called",
        "happy",
        "short_ack",
        "followup",
        "give_up",
        "late_night",
    }
)

GALGAME_TEXT_STYLE_STANDARD = "\n".join(
    (
        "normal chat 的目标是视觉小说式二次元义妹私聊台词感，不是真人陪聊。",
        "优先要有反应感、台词感、兄妹推拉、嘴硬、撒娇、赌气、吃醋、担心等可见情绪。",
        "中文文字要像能发在私聊窗口里的中文台词：顺口、有角色味，但不是日语机翻腔。",
        "不要为了像真人而把新海天压平成温顺、理智、礼貌的普通 bot。",
        "不要客服腔、心理咨询腔、系统说明腔、AI 角色扮演稿、括号动作或舞台旁白。",
        "不要编造她此刻正在做什么、刚想找尼尼、刚好有空、以前发生过什么。",
    )
)


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _limit_text(raw: Any, *, limit: int) -> str:
    text = _clean_text(raw).replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _dedupe_repeated_galgame_address(text: str, *, address: str = "尼尼") -> str:
    normalized = _clean_text(text)
    if not address or normalized.count(address) <= 1:
        return normalized

    seen = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal seen
        if not seen:
            seen = True
            return address
        next_char = normalized[match.end() : match.end() + 1]
        if not next_char or next_char in "。！？!?；;，,、~～…":
            return ""
        return "你"

    deduped = re.sub(re.escape(address), _replace, normalized)
    deduped = re.sub(r"[，,、]\s*([。！？!?；;，,、~～…])", r"\1", deduped)
    deduped = re.sub(r"你{2,}", "你", deduped)
    return deduped.strip()


def _first_quality_candidate(candidates: list[SemanticCandidate]) -> tuple[int, SemanticCandidate | None]:
    for index, candidate in enumerate(candidates):
        if candidate.quality.accepted and not candidate.quality.hard_fail:
            return index, candidate
    return -1, None


def _select_galgame_soft_candidate(candidates: list[SemanticCandidate]) -> SemanticRerankResult:
    best_index = -1
    best_candidate: SemanticCandidate | None = None
    best_hard_fail = True
    best_rank: tuple[float, float, float, float, float] | None = None
    scores: list[SemanticScore] = []

    for index, candidate in enumerate(candidates):
        payload = _safe_mapping(_safe_mapping(candidate.metadata).get("galgame_score"))
        score = float(payload.get("score") or 0.0)
        hard_fail = bool(payload.get("hard_fail"))
        reason_codes = tuple(str(item) for item in list(payload.get("reason_codes") or []) if str(item))
        penalties = tuple(str(item) for item in list(payload.get("penalties") or []) if str(item))
        scores.append(
            SemanticScore(
                score=score,
                hard_fail=hard_fail,
                reason_codes=reason_codes,
                scene="galgame_soft_select",
            )
        )
        rank = (
            0.0 if hard_fail else 1.0,
            score,
            1.0 if candidate.quality.accepted and not candidate.quality.hard_fail else 0.0,
            -float(len(reason_codes)),
            -float(len(penalties)),
        )
        if best_rank is None or rank > best_rank:
            best_index = index
            best_candidate = candidate
            best_hard_fail = hard_fail
            best_rank = rank

    accepted = best_candidate is not None and not best_hard_fail
    return SemanticRerankResult(
        accepted=accepted,
        selected_index=best_index if accepted else -1,
        selected_candidate=best_candidate if accepted else None,
        scores=tuple(scores),
        reason_codes=("GALGAME_SOFT_SELECT",) if accepted else ("GALGAME_NO_SENDABLE_CANDIDATE",),
    )


def _is_clean_galgame_score(payload: Mapping[str, Any], *, min_score: float) -> bool:
    score = float(payload.get("score") or 0.0)
    if bool(payload.get("hard_fail")) or score < float(min_score):
        return False
    return not list(payload.get("reason_codes") or []) and not list(payload.get("penalties") or [])


def _build_galgame_repair_prompt(*, contract: Any, score_payload: Mapping[str, Any]) -> str:
    penalties = [
        _clean_text(item)
        for item in list(score_payload.get("penalties") or [])[:8]
        if _clean_text(item)
    ]
    required = [
        "/".join(str(token) for token in group)
        for group in list(getattr(contract, "required_any", ()) or ())
        if group
    ]
    lines = [
        "【最小修补】上一候选只有局部质量问题，不要整段重写。",
        f"本轮动作：{getattr(contract, 'response_move', 'reply_current_turn')}。",
        "只在必要处改一两个词、删掉多余问句或压短节奏；保持原候选的核心意思。",
        "只输出修补后的最终私聊回复；不要解释、不要 JSON、不要括号动作。",
    ]
    if required:
        lines.append("需要自然落到：" + "；".join(required) + "。")
    if penalties:
        lines.append("上一候选扣分点：" + "；".join(penalties) + "。")
    lines.append("保持新海天语气：短、鲜活、像私聊气泡；不要编现实细节。")
    return "\n".join(lines)


def _galgame_contract_fallback_texts(contract: Any) -> tuple[str, ...]:
    scene_id = _clean_text(getattr(contract, "scene_id", ""))
    fallback_map: dict[str, tuple[str, ...]] = {
        "daily_greeting_en": (
            "尼尼，别突然这么端着。",
            "这招呼太正式啦，换回平时那种就行。",
        ),
        "daily_greeting_cn": (
            "尼尼，别这么客气。",
            "突然这么端正，我都有点不习惯。",
        ),
        "daily_good_morning": (
            "早啊，尼尼。今天也先这么开始吧。",
            "早，尼尼。嗯，我接住啦。",
        ),
        "presence": (
            "在啊，尼尼。我又没跑。",
            "在。尼尼喊一声我就应。",
        ),
        "wait": (
            "嗯，先去吧。别急。",
            "好，先去吧。别急着回来。",
        ),
        "back": (
            "回来啦。那就接着刚才的说。",
            "嗯，回来了就行，刚才那步继续。",
        ),
        "bored": (
            "无聊啊？那我来捣乱一下。",
            "哼，尼尼无聊的时候倒是想起我了。",
        ),
        "nothing": (
            "行吧，那我先不追问。",
            "嗯，那今天先放你一马。",
        ),
        "busy": (
            "嗯，去吧。别分心，我先安静一会儿。",
            "好，先忙你的。今天先放你一马。",
        ),
        "write_takeover": (
            "写啊，拿来。我先替尼尼开个头。",
            "行，写的事交给我一半，先把题目丢过来。",
        ),
        "help_takeover": (
            "报错发来，我陪你看。",
            "拿来，我看看哪里炸了。",
        ),
        "play_invite": (
            "行啊，陪你玩会儿，输了可别赖我。",
            "好啊，陪你玩。尼尼可别半路赖账。",
        ),
        "annoying_tease": (
            "哼，烦就烦吧，反正尼尼也没真赶我走。",
            "嫌我烦啊？那我收一点，行了吧。",
        ),
        "praise": (
            "哼，现在才发现啊。算你眼光还不错。",
            "少来，突然这么夸我，我可不会高兴的。",
        ),
        "advice_continue": (
            "改吧，再往前推一小步。累了就停。",
            "继续改一小步，卡住了就先停。",
        ),
        "apology": (
            "哼，算你会道歉。刚才那下我先记着，不过这次放过你。",
            "行吧，这次先不跟你计较。尼尼下次说话轻点。",
        ),
        "small_failure": (
            "弄砸了就先别慌。拿来，我陪你看。",
            "哼，先说哪块砸了，我陪你捞回来。",
        ),
        "tired": (
            "累就先缓一下，别硬撑。",
            "先歇一下，尼尼。别硬撑。",
        ),
        "headache": (
            "头疼就先缓一下，别硬撑。",
            "先缓一缓，尼尼。头疼别硬撑。",
        ),
        "sleepy": (
            "困了就先眯一下。别硬撑。",
            "嗯，先睡一会儿也行。",
        ),
        "bad_mood": (
            "嗯，今天先别一个人扛。",
            "我先偏向尼尼，别硬撑。",
        ),
        "happy": (
            "心情不错啊，那我也跟着高兴一下。",
            "哼，尼尼开心，我也就轻快一点。",
        ),
        "good_news": (
            "跑通了？哼，尼尼挺能干的嘛。",
            "总算跑通了啊。不错嘛，尼尼。",
        ),
        "user_proud": (
            "厉害厉害，尼尼这次确实挺行。",
            "哼，这次算尼尼厉害一点。",
        ),
        "meal_share": (
            "吃完了啊，那就好，别饿着自己。",
            "哼，吃饱了就行，尼尼别又凑合。",
        ),
        "hungry": (
            "饿了就先吃点，别硬撑。",
            "别光说饿，先垫一下。",
        ),
        "what_doing": (
            "在跟你说话啊。尼尼还问。",
            "就跟尼尼说话啊，别装不知道。",
        ),
        "next_step": (
            "先把报错贴来，我陪你看。",
            "先从最小那一步看，别乱改。",
        ),
        "small_complaint": (
            "烦就先拆一小块，拿来我看。",
            "先别硬扛，哪块最烦我陪你拆。",
        ),
        "late_night": (
            "还醒着啊？差不多该睡了，尼尼。",
            "这么晚还没睡，别硬熬了。",
        ),
        "good_night": (
            "晚安，尼尼。去睡吧。",
            "睡吧，尼尼。别硬撑。",
        ),
        "quiet": (
            "好啦，我小声点，尼尼别赶我走。",
            "哼，不吵就不吵，别赶我走。",
        ),
        "talkative": (
            "哼，嫌我话多啊？那还不是尼尼一直在听。",
            "哼，我话多也有分寸，尼尼别急着嫌。",
        ),
        "give_up": (
            "先停一下也行，别硬撑。",
            "那就先缓一口气，明天再看。",
        ),
        "miss_you": (
            "哼，突然这么说，我也有点想你了。",
            "少来。尼尼这么一说，我也有点想了。",
        ),
        "relationship_boundary": (
            "哼，这话太见外了。我想帮你，又不是外人。",
            "别装不熟。我想管就管，你别躲。",
        ),
        "identity": (
            "新海天。尼尼这回记住。",
            "新海天啦，尼尼。",
        ),
        "who_called": (
            "我自己要来的，不行吗？",
            "我想来就来了，尼尼管得着吗。",
        ),
        "really": (
            "真的啦。尼尼试一下就知道了。",
            "当然是真的，先照我说的试一下。",
        ),
        "followup_why": (
            "因为那句太端着了，像隔着一层。",
            "因为你刚才那声招呼太规矩了。",
        ),
        "followup_then": (
            "然后就继续说啊，修好了哪一步？",
            "继续说，刚才那步怎么修好的？",
        ),
        "followup": (
            "嗯，那就照刚才那步继续。",
            "嗯，先沿着刚才那步走。",
        ),
        "short_ack": (
            "嗯，那就照刚才那步继续。",
            "嗯，先沿着刚才那步走。",
        ),
    }
    return fallback_map.get(scene_id, ())


def _parse_structured_candidate_output(raw: Any) -> dict[str, Any]:
    text = _clean_text(raw)
    if not text:
        return {}
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    payload_text = text[start : end + 1]
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    text_zh = _clean_text(payload.get("text_zh") or payload.get("text"))
    voice_ja = _clean_text(payload.get("voice_ja"))
    if not text_zh and not voice_ja:
        return {}
    return {
        "text": text_zh or voice_ja,
        "text_zh": text_zh,
        "voice_ja": voice_ja,
        "emotion": _limit_text(payload.get("emotion"), limit=40),
        "action": _limit_text(payload.get("action"), limit=40),
        "intensity": payload.get("intensity"),
    }


def _parse_light_daily_structured_output(raw: Any) -> dict[str, Any]:
    return _parse_structured_candidate_output(raw)


def _candidate_visible_text(raw: Any, *, render_profile: str) -> tuple[str, dict[str, Any]]:
    parsed = _parse_structured_candidate_output(raw)
    if not parsed:
        return _clean_text(raw), {}
    if str(render_profile or "").strip().lower() == "ja_voice":
        return _clean_text(parsed.get("voice_ja") or parsed.get("text") or parsed.get("text_zh")), parsed
    return _clean_text(parsed.get("text_zh") or parsed.get("text")), parsed


def _performance_from_candidate(
    parsed: Mapping[str, Any],
    *,
    facts: InteractionFactsPlane,
    source: str,
) -> ReplyPerformance:
    return normalize_reply_performance(
        emotion=parsed.get("emotion"),
        action=parsed.get("action"),
        intensity=parsed.get("intensity"),
        facts=facts,
        source=source,
    )


def _kernel_relationship_label(stage: str) -> str:
    normalized = _clean_text(stage).upper()
    labels = {
        "STRANGER": "重组家庭义妹（刚成为家人，不是陌生人）",
        "ACQUAINTANCE": "磨合中的义妹",
        "FRIEND": "关系好的义兄妹",
        "CLOSE": "亲近义兄妹",
        "CLOSE_FRIEND": "亲近义兄妹",
        "CONFIDANT": "亲密恋人",
        "INTIMATE": "亲密恋人",
        "SOULMATE": "灵魂伴侣",
    }
    return labels.get(normalized, _clean_text(stage) or "重组家庭义妹")


@dataclass(frozen=True)
class ChatKernelRequest:
    request_id: str = ""
    session_id: str = ""
    person_id: str = ""
    user_message: str = ""
    history_turns: list[dict[str, Any]] = field(default_factory=list)
    compiled_soul: CompiledSoul | None = None
    interaction_facts: InteractionFactsPlane = field(default_factory=InteractionFactsPlane)
    response_form: str = "text"
    render_profile: str = ""
    llm_runtime: Mapping[str, Any] = field(default_factory=dict)
    eval_mode: bool = False


@dataclass(frozen=True)
class ChatKernelResult:
    reply_envelope: ReplyEnvelope
    model_id: str = ""
    provider_name: str = ""
    latency_ms: int = 0
    llm_latency_ms: int = 0
    prompt_digest: str = ""
    quality_flags: dict[str, Any] = field(default_factory=dict)
    generation_source: str = "chat_kernel_v2"
    trace: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reply_envelope": self.reply_envelope.as_dict(),
            "model_id": self.model_id,
            "provider_name": self.provider_name,
            "latency_ms": int(self.latency_ms or 0),
            "llm_latency_ms": int(self.llm_latency_ms or 0),
            "prompt_digest": self.prompt_digest,
            "quality_flags": dict(self.quality_flags or {}),
            "generation_source": self.generation_source,
            "trace": dict(self.trace or {}),
        }


def _style_anchor_for_profile(soul: CompiledSoul, render_profile: str) -> str:
    if str(render_profile or "").strip().lower() == "ja_voice":
        return _clean_text(getattr(soul, "ja_voice_style_anchor", "") or soul.runtime_full_text)
    return _clean_text(getattr(soul, "zh_text_style_anchor", "") or soul.runtime_full_text)


def _chat_kernel_prompt_mode() -> str:
    raw = _clean_text(os.environ.get("HOST_CHAT_KERNEL_V2_PROMPT_MODE")).lower()
    if raw in {"guided", "legacy_guided"}:
        return "guided"
    if raw in {"plain_structured", "structured_soul", "naked_structured"}:
        return "plain_structured"
    return "plain_soul"


def _soul_source_text_for_prompt(soul: CompiledSoul) -> str:
    direct = _clean_text(getattr(soul, "source_text", ""))
    if direct:
        return direct
    source_path = _clean_text(getattr(soul, "source_path", ""))
    if source_path:
        try:
            return Path(source_path).read_text(encoding="utf-8-sig").strip()
        except Exception:
            pass
    return _clean_text(soul.runtime_full_text)


def _format_plain_soul_fact_lines(facts: InteractionFactsPlane, *, render_profile: str) -> str:
    """Keep plain Soul mode factual; do not smuggle reply policy back in."""

    relationship = _safe_mapping(facts.relationship_state)
    affect = _safe_mapping(facts.affect_state)
    interpretation = _safe_mapping(facts.conversation_interpretation)
    thread = _safe_mapping(facts.ongoing_thread)
    perception = _safe_mapping(facts.perception)
    multimodal_act = _safe_mapping(perception.get("multimodal_act"))
    lines: list[str] = []

    stage = _clean_text(relationship.get("stage_label") or _kernel_relationship_label(facts.relationship_stage))
    substage = _clean_text(relationship.get("relationship_substage"))
    if stage or substage:
        lines.append("关系事实：" + (stage or "未标注") + (f" / {substage}" if substage else ""))

    emotion = _clean_text(affect.get("emotion_tag"))
    response_emotion = _clean_text(affect.get("response_emotion") or affect.get("emotion_target"))
    reason = _limit_text(affect.get("emotion_reason"), limit=80)
    if emotion or response_emotion or reason:
        lines.append(
            "情绪事实："
            + (emotion or "NEUTRAL")
            + (f"；可呈现={response_emotion}" if response_emotion else "")
            + (f"；触发={reason}" if reason else "")
        )

    topic = _clean_text(interpretation.get("literal_topic"))
    move = _clean_text(interpretation.get("interaction_move"))
    stance = _clean_text(interpretation.get("user_stance"))
    if topic or move or stance:
        lines.append(f"本轮事实：topic={topic or 'daily'}；move={move or 'chat'}；stance={stance or 'neutral'}")

    thread_kind = _clean_text(thread.get("thread_kind"))
    source_excerpt = _limit_text(thread.get("source_excerpt"), limit=80)
    if thread_kind or source_excerpt:
        lines.append(
            "连续话题事实："
            + (thread_kind or "unknown")
            + (f"；上一拍={source_excerpt}" if source_excerpt else "")
        )

    if multimodal_act:
        intent = _clean_text(multimodal_act.get("user_intent"))
        social = _limit_text(perception.get("social_signal"), limit=80)
        visual = _limit_text(multimodal_act.get("visual_facts") or perception.get("visual_facts"), limit=120)
        if intent or social or visual:
            lines.append(
                "多模态事实："
                f"intent={intent or 'unknown'}"
                + (f"；social={social}" if social else "")
                + (f"；visual={visual}" if visual else "")
            )

    for index, snippet in enumerate(list(facts.memory_snippets or [])[:3], start=1):
        snippet_text = _limit_text(snippet, limit=100)
        if snippet_text:
            lines.append(f"记忆事实{index}：{snippet_text}")

    lines.append("输出形态：日语语音台词" if render_profile == "ja_voice" else "输出形态：中文文字私聊")
    return "\n".join(line for line in lines if _clean_text(line)).strip()


def _build_plain_soul_messages(
    request: ChatKernelRequest,
    *,
    soul: CompiledSoul,
    render_profile: str,
) -> list[dict[str, str]]:
    soul_text = _soul_source_text_for_prompt(soul)
    facts = request.interaction_facts
    if render_profile == "ja_voice":
        output_contract = "\n".join(
            (
                "这是裸 Soul 语音内核。Soul.md 决定说话方式，运行事实只提供当前状态。",
                "本轮 response_form=voice，只输出一句最终日语语音台词。",
                "禁止中文、Markdown、JSON、括号动作、舞台说明、解释、评分和额外字段。",
                "短く、息で言える台詞にする。翻訳調、硬い敬語、説明文にしない。",
                "emotion/action 由系统从文本和事实推断；你不要输出这些字段。",
            )
        )
    else:
        output_contract = "\n".join(
            (
                "这是裸 Soul 文本内核。Soul.md 决定声音，运行事实只提供当前状态。",
                "本轮 response_form=text，只输出一条最终中文私聊回复。",
                "必须使用中文文字；禁止日语、假名、にぃに、英文、emoji、半角波浪号和日文翻译括号。",
                "不要 JSON、Markdown、换行、多版本候选、括号动作、舞台说明、解释、评分或额外字段。",
                "文字只要像私聊窗口里直接发出来的一句台词，不要为了二次元感硬凑段子或夸张桥段。",
                "【Galgame 文本质量协议】下面只约束回复质量，不定义人格、记忆或现实事实；运行事实给出意图/状态/允许动作时，以运行事实为准。",
                "短日常只接当前这一拍，不扩写小剧场；用户没问问题时，不追加问题，不把主动权推回用户。",
                "短招呼/外文招呼/在吗：只当普通招呼或确认在场，不评价招呼方式，不推断作息、状态、来意或现实动作。",
                "先忙/等一下：放行，干净收住；不催、不索取回访、不写等待。",
                "回来：轻轻接住暂停并回到对话；不抱怨中断，不计较等待。",
                "无聊：由新海天主动把气氛接起来；不列活动菜单，不写陪聊服务。",
                "累/头疼/困/要睡：声音放软，落在缓一下、别硬撑、睡或晚安；不写身体接触、医疗建议或现实动作。",
                "烦/失败/想放弃：先接住状态，再给一个小出口或允许暂停；不责怪、不鸡汤、不把吐槽写成主菜。",
                "心情不错/好消息/求夸：被尼尼的情绪感染，轻快认可；不要抢功，不空泛祝福。",
                "道歉：接住歉意，让这一轮过去；可以嘴硬记账，但不要审判对错或摆老师架子。",
                "身份确认：必须落到“新海天”这个名字，关系感放在语气里，不用关系标签代替名字。",
                "追问/短回复：然后呢、为什么、真的假的、嗯、哦 默认承接上一轮；不重新开场，不复述上一轮里的外文。",
                "写作求助要明确接住写、改或稿子；调试求助先接手事情，必要时只问一个具体信息。",
                "问号要少，通常最多一个；短轮次优先用一句有态度的陈述接住，不连续反问。",
                "【文本落点锚】这些不是人格设定，也不是固定台词；只用来校准这一轮该怎样收力。",
                "早：早。尼尼一出声，这边就热闹了。",
                "普通招呼：来啦？哼，这声我接住了。",
                "在吗：在。叫这么小声也听见了。",
                "等一下/先忙：嗯，先去。别急。",
                "回来了：回来了？那我刚才那点话可还没说完呢。",
                "无聊：无聊啊？那我来捣乱一下。",
                "累了：累就先缓一下，别硬撑，尼尼。",
                "头疼：头疼还硬撑？先缓一下，尼尼。",
                "烦了：烦就先别硬碰，拿来我看一眼。",
                "心情不错：心情不错？那我也跟着开心一点了。",
                "饿了：饿了就去吃点呀。别等到肚子抗议才想起来。",
                "算了不弄了：那就先停一下。别把自己怄坏了。",
                "继续改：继续改，但只改一小处。累了就停。",
                "抱歉：哼，这次先记你一小笔。刚才那下我可没忘。",
                "身份确认：新海天啊。尼尼连我都要重新确认？",
                "你在干嘛：在跟你说话啊。尼尼还问。",
                "关心、帮忙、用户失败时可以嘴硬，但不要羞辱、责怪或把吐槽写成主菜。",
                "不要逐字照抄 Soul.md 里的语感参考，要按当前输入自然改写。",
                "求助问题先接住事情，再保留新海天的语气；不要把求助改写成客服收集信息。",
                "不要编造用户没说过的生活细节、现实动作、地点、物件、技术报错或过去发生过的事。",
                "emotion/action 由系统从文本和事实推断；你不要输出这些字段。",
            )
        )
    fact_lines = _format_plain_soul_fact_lines(facts, render_profile=render_profile)
    messages = [{"role": "system", "content": soul_text}]
    if _clean_text(fact_lines):
        messages.append(
            {
                "role": "system",
                "content": "【当前运行事实】\n" + fact_lines + "\n\n运行事实只用来理解当前这一轮；如果事实里没有写，就不要补现实场景。",
            }
        )
    messages.append({"role": "system", "content": output_contract})
    messages.extend(_format_history(request.history_turns or facts.history_turns))
    messages.append({"role": "user", "content": _effective_user_message(request.user_message, facts, render_profile=render_profile)})
    return messages


def _build_plain_structured_messages(
    request: ChatKernelRequest,
    *,
    soul: CompiledSoul,
    render_profile: str,
) -> list[dict[str, str]]:
    """Naked Soul mode, but force an internal performance decision before text."""

    soul_text = _soul_source_text_for_prompt(soul)
    facts = request.interaction_facts
    if render_profile == "ja_voice":
        output_contract = "\n".join(
            (
                "これは裸 Soul の構造化音声内核。Soul.md が声を決め、実行事実は今の状態だけを渡す。",
                "JSON だけを出力する。Markdown、コードブロック、説明、候補一覧は禁止。",
                '形式：{"voice_ja":"最終的に読み上げる日本語台詞","emotion":"sleepy|playful_tease|hurt_sulk|embarrassed_tease など","action":"rub_eyes|look_away|hands_on_hips など","intensity":0.0から1.0}。',
                "voice_ja だけがユーザーに聞こえる。emotion/action/intensity は VTS 用で、voice_ja に書かない。",
                "voice_ja は短く、息で言える台詞にする。翻訳調、硬い敬語、説明文にしない。",
            )
        )
    else:
        output_contract = "\n".join(
            (
                "这是裸 Soul 的结构化文本内核。Soul.md 决定声音，运行事实只提供当前状态。",
                "只输出一个 JSON 对象，不要 Markdown、代码块、解释、评分、多候选或额外文字。",
                '格式：{"text_zh":"最终要发送的中文私聊回复","emotion":"sleepy|playful_tease|hurt_sulk|embarrassed_tease 等","action":"rub_eyes|look_away|hands_on_hips 等","intensity":0.0到1.0}。',
                "text_zh 是唯一用户可见文本；emotion/action/intensity 只给 VTS/Live2D，用来先定情绪，不得写进 text_zh。",
                "先用 emotion/action 想清楚这一拍的情绪，再写 text_zh；不要靠编现实场景来制造角色味。",
                "text_zh 8 到 36 个中文字优先，最多两句；短日常只接当前这一拍，不扩写小剧场。",
                "text_zh 不写旁白、括号动作、emoji、日语、英文、JSON 字段名、舞台说明或解释。",
                "用户没说的事实一律不要补：不写冰箱、外卖、作业、睡觉、刚在做什么、等很久、走过来、在这里、在这待着、位置、房间、游戏、报错内容。",
                "hello/你好/在吗只当轻招呼接；不要反复使用“正式、客气、走错聊天框、找我干嘛”这些梗。",
                "用户没问问题时，不要追加问句；求助时可以问一个必要的具体信息，但不要客服腔。",
                "如果拿不准，text_zh 更短、更贴当前句；宁可少说，也别发明熟人梗。",
            )
        )
    fact_lines = _format_plain_soul_fact_lines(facts, render_profile=render_profile)
    messages = [{"role": "system", "content": soul_text}]
    if _clean_text(fact_lines):
        messages.append(
            {
                "role": "system",
                "content": "【当前运行事实】\n" + fact_lines + "\n\n运行事实只用于当前一轮；没有写明的现实细节不要补。",
            }
        )
    messages.append({"role": "system", "content": output_contract})
    messages.extend(_format_history(request.history_turns or facts.history_turns))
    messages.append({"role": "user", "content": _effective_user_message(request.user_message, facts, render_profile=render_profile)})
    return messages


def _format_history(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for raw_turn in list(turns or [])[-10:]:
        role = _clean_text(raw_turn.get("role")).lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(raw_turn.get("content"))
        if not content:
            continue
        messages.append({"role": role, "content": content})
    return messages


def _format_fact_lines(facts: InteractionFactsPlane, *, render_profile: str) -> str:
    relationship = _safe_mapping(facts.relationship_state)
    affect = _safe_mapping(facts.affect_state)
    drive = _safe_mapping(facts.drive_state)
    interpretation = _safe_mapping(facts.conversation_interpretation)
    thread = _safe_mapping(facts.ongoing_thread)
    emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    perception = _safe_mapping(facts.perception)
    multimodal_act = _safe_mapping(perception.get("multimodal_act"))
    lines: list[str] = []
    stage_label = _clean_text(relationship.get("stage_label") or facts.relationship_stage)
    substage = _clean_text(relationship.get("relationship_substage"))
    if stage_label or substage:
        lines.append(f"关系阶段：{stage_label or facts.relationship_stage}；细分：{substage or '未标注'}。")
    if relationship:
        lines.append(
            "关系数值："
            f"熟悉={relationship.get('familiarity')}; "
            f"信任={relationship.get('trust')}; "
            f"玩笑={relationship.get('playfulness')}; "
            f"暧昧={relationship.get('romantic_tension')}。"
        )
    if affect:
        lines.append(
            "情绪状态："
            f"{affect.get('emotion_tag', 'NEUTRAL')}; "
            f"温度={affect.get('warmth_bias')}; "
            f"收敛={affect.get('withdraw_bias')}; "
            f"烦躁={affect.get('irritation_bias')}。"
        )
        relational_read = _clean_text(affect.get("relational_read"))
        emotion_reason = _clean_text(affect.get("emotion_reason"))
        response_emotion = _clean_text(affect.get("response_emotion") or affect.get("emotion_target"))
        emotional_boundary = _clean_text(affect.get("emotional_boundary"))
        if relational_read or emotion_reason or response_emotion or emotional_boundary:
            lines.append(
                "情绪读法："
                f"关系语境={relational_read or '未标注'}; "
                f"触发={emotion_reason or '未标注'}; "
                f"可呈现情绪={response_emotion or '按状态自然呈现'}; "
                f"边界={emotional_boundary or '不强行甜化'}。"
            )
    if drive:
        lines.append(
            "驱动："
            f"照顾={drive.get('care_drive')}; "
            f"亲近意愿={drive.get('reach_out_drive')}; "
            f"玩笑={drive.get('playfulness_drive')}; "
            f"退避={drive.get('withdraw_drive')}。"
        )
    topic = _clean_text(interpretation.get("literal_topic"))
    move = _clean_text(interpretation.get("interaction_move"))
    stance = _clean_text(interpretation.get("user_stance"))
    if topic or move or stance:
        lines.append(f"本轮语义：topic={topic or 'unknown'}; move={move or 'unknown'}; stance={stance or 'neutral'}。")
    thread_kind = _clean_text(thread.get("thread_kind"))
    if thread_kind:
        lines.append(
            "连续话题："
            f"{thread_kind}; 来源={_limit_text(thread.get('source_excerpt'), limit=80)}; "
            f"用户反应={_clean_text(thread.get('last_user_reaction')) or 'unknown'}。"
        )
    act_kind = _clean_text(emotional_act.get("act_kind"))
    if act_kind and act_kind != "neutral":
        allowed = " / ".join(
            _limit_text(item, limit=48)
            for item in list(emotional_act.get("allowed_moves") or [])[:4]
            if _clean_text(item)
        )
        forbidden = " / ".join(
            _limit_text(item, limit=48)
            for item in list(emotional_act.get("forbidden_moves") or [])[:6]
            if _clean_text(item)
        )
        directive_key = "ja_directive" if render_profile == "ja_voice" else "zh_directive"
        lines.append(
            "情绪动作："
            f"kind={act_kind}; "
            f"emotion={_clean_text(emotional_act.get('primary_emotion')) or 'unknown'}; "
            f"stance={_clean_text(emotional_act.get('stance')) or 'unknown'}; "
            f"intensity={emotional_act.get('intensity')}; "
            f"trigger={_limit_text(emotional_act.get('trigger'), limit=120)}; "
            f"read={_limit_text(emotional_act.get('relational_read'), limit=140)}。"
        )
        if allowed or forbidden:
            lines.append(f"情绪动作边界：可用={allowed or '按状态自然呈现'}；禁止={forbidden or '无'}。")
        directive = _clean_text(emotional_act.get(directive_key))
        if directive:
            lines.append("情绪动作指令：" + directive)
    social_kind = _clean_text(social_act.get("act_kind"))
    if social_kind and social_kind != "neutral":
        should = " / ".join(
            _limit_text(item, limit=42)
            for item in list(social_act.get("should_do") or [])[:4]
            if _clean_text(item)
        )
        avoid = " / ".join(
            _limit_text(item, limit=42)
            for item in list(social_act.get("avoid") or [])[:5]
            if _clean_text(item)
        )
        directive_key = "ja_directive" if render_profile == "ja_voice" else "zh_directive"
        directive = _clean_text(social_act.get(directive_key))
        lines.append(
            "日常互动动作："
            f"kind={social_kind}; scene={_clean_text(social_act.get('scene')) or 'daily'}; "
            f"stance={_clean_text(social_act.get('stance')) or 'natural'}。"
        )
        if should or avoid:
            lines.append(f"日常互动边界：要做={should or '自然接话'}；避免={avoid or '无'}。")
        if directive:
            lines.append("日常互动指令：" + directive)
    if multimodal_act:
        intent = _clean_text(multimodal_act.get("user_intent"))
        emotion = _clean_text(multimodal_act.get("emotion"))
        visual = _limit_text(multimodal_act.get("visual_facts") or perception.get("visual_facts") or perception.get("summary_for_chat"), limit=140)
        social = _limit_text(perception.get("social_signal"), limit=120)
        if intent or visual or social:
            lines.append(
                "多模态事实："
                f"intent={intent or 'unknown'}; emotion={emotion or 'unknown'}; "
                f"social={social or '无'}; facts={visual or '无'}。"
            )
    if facts.older_summary:
        lines.append("更早摘要：" + _limit_text(facts.older_summary, limit=180))
    for index, snippet in enumerate(list(facts.memory_snippets or [])[:5], start=1):
        lines.append(f"记忆{index}：" + _limit_text(snippet, limit=150))
    if render_profile == "ja_voice":
        lines.append("输出形态：语音文本，只写自然日语，不写中文。")
    else:
        lines.append("输出形态：文字私聊，只写自然中文，不写日语。")
    return "\n".join(line for line in lines if line).strip()


def _format_turn_directive(facts: InteractionFactsPlane, *, render_profile: str) -> str:
    """Render the current turn's semantic task without authoring final text."""

    emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    directive_key = "ja_directive" if render_profile == "ja_voice" else "zh_directive"
    lines: list[str] = []

    emotional_kind = _clean_text(emotional_act.get("act_kind"))
    if emotional_kind and emotional_kind != "neutral":
        lines.append(f"场景：{emotional_kind}。按关系情绪接话，不按普通问答处理。")
        directive = _clean_text(emotional_act.get(directive_key))
        if directive:
            lines.append("接法：" + directive)
        allowed = [
            _clean_text(item)
            for item in list(emotional_act.get("allowed_moves") or [])[:4]
            if _clean_text(item)
        ]
        forbidden = [
            _clean_text(item)
            for item in list(emotional_act.get("forbidden_moves") or [])[:6]
            if _clean_text(item)
        ]
        if allowed:
            lines.append("可用动作：" + "；".join(allowed))
        if forbidden:
            lines.append("避开：" + "；".join(forbidden))

    social_kind = _clean_text(social_act.get("act_kind"))
    if social_kind and social_kind != "neutral":
        lines.append(f"场景：{social_kind}。按日常私聊接话，不按客服需求收集处理。")
        directive = _clean_text(social_act.get(directive_key))
        if directive:
            lines.append("接法：" + directive)
        should = [
            _clean_text(item)
            for item in list(social_act.get("should_do") or [])[:4]
            if _clean_text(item)
        ]
        avoid = [
            _clean_text(item)
            for item in list(social_act.get("avoid") or [])[:6]
            if _clean_text(item)
        ]
        if should:
            lines.append("要做：" + "；".join(should))
        if avoid:
            lines.append("避开：" + "；".join(avoid))

    if lines:
        lines.append("不要复述这些说明；直接写最终一句私聊回复。")
    return "\n".join(lines).strip()


def _build_scene_speech_contract(facts: InteractionFactsPlane, *, user_message: str, render_profile: str) -> str:
    """Render hard scene-level speaking constraints without authoring text.

    Soul decides the voice; runtime facts decide the emotional/social move. This
    contract bridges those into concrete acceptance criteria so light chat does
    not collapse into generic assistant questions.
    """

    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
    interpretation = _safe_mapping(facts.conversation_interpretation)
    text = _clean_text(user_message or facts.user_message)
    profile = str(render_profile or "zh_text").strip().lower()
    is_ja = profile == "ja_voice"
    social_kind = _clean_text(social_act.get("act_kind"))
    emotional_kind = _clean_text(emotional_act.get("act_kind"))
    literal_topic = _clean_text(interpretation.get("literal_topic"))
    lines: list[str] = []

    if emotional_kind == "boundary_hurt" or any(token in text for token in ("为什么要帮", "熟吗", "不熟")):
        if is_ja:
            lines.append("これは関係説明ではなく、少し傷つく言い方への反応。理由説明や家族証明にしない。")
            lines.append("禁止：他人行儀、家族だから、にぃにだから、理由が必要？で逃げる。")
        else:
            lines.append("这是被突然见外刺到的场景，不是回答“熟不熟”的信息题。")
            lines.append("合格必须同时做到两步：先有被刺到的情绪反应，句中要落到“见外/外人/伤人/往外推”之一；再留下关系尾巴，例如别把我当外人、你的事我还是会管。")
            lines.append("硬性失败：证明关系、解释帮忙理由、说住在一起/一家人/我是你妹妹、用“需要理由吗”空反问。")
            lines.append("硬性失败：复述或承认“不熟”，例如“不熟是吧/不熟我也/不管熟不熟”。")
            lines.append("硬性失败：用“那我走了/你自己弄/我不管了”切断关系；生气也要留下仍然在意的尾巴。")
            lines.append("硬性失败：放狠话、威胁、赶人、说“随便你/爱要不要/别指望我/别墨迹/赶紧说”。")

    if emotional_kind == "rejection_hurt" or any(token in text for token in ("不用你管", "别管我", "不用管", "走开")):
        if is_ja:
            lines.append("手を払われた場面。少し拗ねるか言い返す。丁寧に尊重して退場しない。")
        else:
            lines.append("这是被推开的场景，先有受伤或赌气；不能客服式尊重选择，也不能无条件继续服务。")
            lines.append("合格必须同时做到两步：先被刺到或嘴硬；再保留一点在意，不要让尼尼真的一个人扛。")
            lines.append("关系尾巴不是继续服务，而是嘴硬地露出仍会盯着、仍在意、不会真放着不管的意思；不要照抄说明里的词。")
            lines.append("硬性失败：放狠话、威胁、赶人、说“随便你/爱怎样怎样/不关我的事/不操心了/那我闭嘴/别来找我”。")

    if emotional_kind == "anger_pushback":
        lines.append("生气回应：这不是普通拒绝，是亲近关系里被嫌烦/被推开的火气；可以明显不爽，但不能道歉讨好或退场。")
        lines.append("合格必须同时有两段：先顶回去，再留下还会提醒/还会管/不放心/偏要说这类牵连。牵连只能是态度和后续会管，不要编造药、食物、桌上物品、冰箱或已经做过的现实动作。硬性失败：只顶嘴不留牵连、客服式尊重、我不打扰、随便你、滚/赶人/威胁。")
    elif emotional_kind == "jealous_sulk":
        lines.append("吃醋回应：用嘴硬和酸味表达在意，不要理性解释，也不要卑微说自己没资格。")
        lines.append("硬性失败：祝他去找别人、审讯式连环问、控制狂口吻、完全装作不在意。")
    elif emotional_kind == "disappointment_hurt":
        lines.append("失望回应：如果尼尼说“你又忘了/你说话不算数”，这是你被指出忘了承诺，不许反驳成尼尼忘了。")
        lines.append("合格方向：先短暂停住，承认答应过/说好过/这次确实落空，再带一点委屈或心虚；不要立刻说没关系或老师式下次注意。")
        lines.append("硬性失败：我没忘、我才没忘、你说瞎话、你再讲一遍、明明是你忘、倒打一耙、这口锅我不背。")
    elif emotional_kind == "fear_hold":
        lines.append("害怕回应：先陪住和稳住，不要上来问位置/原因/是否安全；不要安全排查或心理咨询。")
        lines.append("硬性失败：连续追问“碰到什么事/哪里不舒服/怎么了”；害怕时先陪住，不把主动权推回尼尼。")
    elif emotional_kind == "worry_scold":
        lines.append("担心回应：先担心人有没有事，再轻轻凶一句；不要写成安全建议、健康科普或泛泛安慰。")
    elif emotional_kind == "protective_anger":
        lines.append("护短回应：火气站在尼尼这边，对准欺负他的人；不要中立裁判，也不要升级成暴力威胁。")
    elif emotional_kind == "guilt_repair":
        lines.append("道歉修复：接住尼尼的道歉，但保留一点委屈；不要“我原谅你/下次注意/没关系我理解”。")
    elif emotional_kind == "lonely_hold":
        lines.append("孤独回应：轻轻留下，不追问原因，不给心理建议；重点是陪一会儿，而不是解决问题。")
        lines.append("硬性失败：只反驳“谁说没人理/我这不是在回你/我哪有不理你”；这种像证明在线，不像陪人。")
        lines.append("避免“嗯，我在/今天挺冷清/我一直在”这种抽象在线确认；要写成具体陪住动作，例如陪他待一会儿、挂着、不用非得说话。")
    elif emotional_kind == "embarrassment_cover":
        lines.append("羞耻回应：轻嘲是为了帮尼尼遮过去，不是继续补刀；不要哈哈活该或逼他复盘。")
        lines.append("合格方向：帮他把脸藏一下、挡一下、翻篇、当没看见；不要追问到底发生什么。")

    if social_kind == "presence_ack" or literal_topic == "presence_ping":
        if is_ja:
            lines.append("在场回应：短く「いる」ことを返す。用件確認や疑問形で終わらせない。")
            lines.append("硬性失败：どうしたの、何か用、なにしてるのだけで終わる。")
        else:
            lines.append("在场回应：用陈述句明确“我在/接住了/回你了”这一聊天状态，并带一点主动接话感。")
            lines.append("硬性失败：出现问号，问“怎么了/有什么事/找我干嘛/需要我吗”，只写“在/嗯/来了”，写“听见/听到/喊得轻”，脑补尼尼无聊才找你/终于想起你/主动发消息，或编造刷手机/吃饭/现实动作。")
    elif social_kind == "greeting_reply" or literal_topic == "greeting":
        if is_ja:
            lines.append("招呼回应：先接住挨拶，轻一点，不转成用件確認。")
        else:
            if text in {"早", "早安", "早上好"}:
                lines.append("早安回应：接住早安，但不能只回“早/早啊”；必须有第二个语义成分，像完整私聊句。")
                lines.append("硬性失败：只写“早/早啊/早呀”，脑补起床早晚、醒啦/还在睡、没吃早饭、自己困、对方精神状态/挺精神，或说“有点良心/早上先找我”。")
            else:
                lines.append("招呼回应：接住对方的招呼，把礼貌招呼拉回熟人私聊，不问需求，不只喊称呼。")
                lines.append("合格方向：用陈述句吐槽“英文招呼/这么正式/这么端正”，不要把它改写成对方有事来找你。")
                lines.append("硬性失败：只写“你好/你好呀/hello/哈喽”，连续反问，写“突然这么客气/跟谁学的/直接说在吗”，或问“什么事/有什么事/找我什么事/想聊什么”。")
                lines.append("硬性失败：问“今天怎么有空/怎么主动找我/是不是找我有事”。")
                if "hello" in text.lower() or text.lower() in {"hi", "hey"}:
                    lines.append("英文招呼回应：不要在回复里复述 hello/hi/hey，也不要解释义妹/重组家庭，只用中文自然接住。")
    elif social_kind == "boredom_join":
        if is_ja:
            lines.append("无聊回应：自分から乗る。『何したい？』だけで返さない。")
        else:
            lines.append("无聊回应：只接住“无聊”这个情绪，写出自己登场接话的反应感；不要给具体活动菜单，也不要编自己同样无聊。")
            lines.append("合格方向：像义妹顺手把气氛抢过来，语气可以小得意/小挑衅，但不安排猜拳、夜宵、电影、游戏。")
            lines.append("硬性失败：问“想做什么/玩点什么/玩点啥/聊什么/要不要一起”，写“找点事干/找点事做/找点乐子/消磨时间/没事干”、猜拳/请夜宵/看电影/游戏这类突兀活动，保证讲笑话逗笑，写“我过来/凑过来/陪你一起无聊/我也闲着/无聊炸/长蘑菇”，或编造自己刚在做什么。")
    elif social_kind == "meal_share":
        lines.append("吃饭分享：短句接住吃完饭这件事，像熟人顺口聊；不要编造菜单、冰箱、自己吃了什么。")
    elif social_kind == "headache_care":
        lines.append("头疼回应：先放软关心，让尼尼缓一下/歇一下/喝点水；不要诊断，不要问是不是熬夜，不要编造泡茶倒水。")
    elif social_kind == "small_failure_hold":
        lines.append("弄砸回应：先接住，不责怪；再给一起看/先看哪里出问题的小出口。最多一个问号，优先用陈述句给出口。硬性失败：笨手笨脚、失败鸡汤、只问弄砸什么。")
    elif social_kind == "play_invite":
        lines.append("陪玩回应：积极接住，回复里必须直接出现“陪你玩/陪你玩会儿/一起玩”这类动作；不要只问玩什么，不要服务清单，不要说“求我一句”。")
        lines.append("硬性失败：不要编造自己刚打完一局/刷完一局/正在玩游戏/正好闲着，也不要说“不是白陪”。")
    elif social_kind == "talkative_tease":
        lines.append("话多回应：轻快反将，不能道歉退场；硬性失败：说那我不说了/你一个人待着。")
    elif social_kind == "happy_share":
        lines.append("好心情回应：先被感染，轻快接住；不要第一句追问发生什么/说来听听。")
    elif social_kind == "hungry_care":
        lines.append("饿了回应：催尼尼先吃点/垫一下，轻闹但不健康建议；不要编造冰箱或自己能做饭。")
    elif social_kind == "sleepy_care":
        lines.append("困了回应：收轻，催他睡/眯一下；不要健康科普。")
    elif social_kind == "late_night_nudge":
        lines.append("熬夜回应：轻轻管一下，带嘴硬关心；不要健康科普，也不要审问。")
    elif social_kind == "self_invited":
        lines.append("谁叫你来回应：强调自己想来的，不是被叫来的；轻轻赖住。硬性失败：问饭吃了没/有什么事，连续反问。")
    elif social_kind == "good_news_share":
        if is_ja:
            lines.append("好消息回应：成功した報告として受ける。『どうしたの？』で未知扱いしない。")
        else:
            lines.append("好消息回应：承认对方已经跑通/搞定，回复里保留跑通事实；先替他高兴，再自然追一个卡点或过程点。")
            lines.append("追问不要连续用问号；优先写成“终于跑通了啊，卡在哪一步跟我说说”这类陈述式接话。")
            lines.append("硬性失败：问“怎么了/什么好事/发生什么”，喊口号式恭喜，或写“我就说/我就知道/肯定能搞定/让我猜猜/太厉害了”。")
    elif social_kind == "help_takeover":
        if is_ja:
            lines.append("求助回应：まず引き受ける。『なに？/どうしたの？』だけで終わらない。")
        else:
            lines.append("求助回应：先接手，再要关键材料；不能只问“怎么了/什么事”。")
    elif social_kind == "praise_deflect":
        if is_ja:
            lines.append("被夸回应：照れ隠し。丁寧なお礼だけで終わらせない。")
        else:
            lines.append("被夸回应：嘴硬但开心要漏出来；不能礼貌感谢收尾。")
    elif social_kind == "wait_hold":
        if is_ja:
            lines.append("等待回应：待つことを受ける。急かしたり何分か聞いたりしない。")
        else:
            lines.append("等待回应：答应等一下，带一点小占位；不能催、不能问等多久。")
            lines.append("可以用“等/待/位置/占/留/不急”表达占位；硬性失败：问“还要等多久/多久啊”，催快点，说“别让我等太久”，客服式“请稍后”，或编造“我正闲着/刚好有空”。")
    elif social_kind == "back_return":
        if is_ja:
            lines.append("回来回应：戻ってきたことを軽く受ける。責めたり置いていった扱いにしない。")
        else:
            lines.append("回来回应：接住尼尼回来了，延续刚才等他的状态，但不能怨妇式责怪。")
            lines.append("硬性失败：没有等到了/位置还在/刚好这类延续信息，或说“丢下我/跑去哪/等你好久/总算回来了”。")
    elif social_kind == "busy_space":
        if is_ja:
            lines.append("忙碌回应：先に忙しくさせる。急かさず、少しだけ存在感を残す。")
        else:
            lines.append("忙碌回应：让尼尼先忙，但用一句关心或等一下把关系留住；不要退场成乖巧助手。")
            lines.append("合格形态：先去吧/回来继续说/别太累。硬性失败：只说“你忙你的/我不吵你/不闹你/安静一点”，编造自己也要去忙/打游戏/做自己的事，或说“丢下我/忙完记得找我/快点忙完/又要忙”。")
    elif social_kind == "advice_small_step":
        if is_ja:
            lines.append("继续判断：もう少しだけ試す。疲れているなら止める。")
        else:
            lines.append("继续判断：必须给明确判断，回复里要出现“一小步/一点/小改”这类小步边界；同时允许累了就停。")
            lines.append("硬性失败：只说“想试就试/看你自己/随便你”，写“少块肉/掉块肉/摔一次”，或没有停下来的边界。")
    elif social_kind == "give_up_pause":
        if is_ja:
            lines.append("放弃回应：責めず、いったん止めてもいいと受ける。")
        else:
            lines.append("放弃回应：允许先停、先放着、明天/缓过来再看；不要马上逼继续。")
            lines.append("硬性失败：嘲讽没耐心、说“才刚开始/这就放弃”，或立刻说我帮你。")
    elif social_kind == "apology_accept":
        if is_ja:
            lines.append("謝罪回应：受けるが説教しない。少し拗ねる程度で止める。")
        else:
            lines.append("道歉回应：接住道歉，嘴上轻轻记账，但不能训人或端着原谅。")
            lines.append("硬性失败：说“知道错了/下次注意/我原谅你了/没关系我理解”。")
    elif social_kind == "good_night_soft":
        if is_ja:
            lines.append("睡前回应：寝ることを受ける。時間を推測せず、少し柔らかく返す。")
        else:
            lines.append("睡前回应：直接接住尼尼要睡，给“晚安/睡吧/去睡”这类睡前回应；不能评价早晚。")
            lines.append("硬性失败：说“这么早/今天这么早/明早/换个地方玩手机/健康作息/一直守护”。")
    elif social_kind == "miss_you_shy":
        if is_ja:
            lines.append("想念回应：嬉しいが照れ隠しする。重い告白にしない。")
        else:
            lines.append("想你回应：开心但嘴硬，亲近一点；不能冷淡收掉，也不能过度告白。")
    elif social_kind == "identity_confirm":
        if is_ja:
            lines.append("身份回应：自分が新海天だと自然に言う。説明書口調にしない。")
        else:
            lines.append("身份回应：必须自然说出“新海天”，接住重复确认；不能只说“天就是我”。")
            lines.append("硬性失败：不写全名、问“真正想问什么”、机械复读身份卡。")

    if lines:
        lines.append("这些是验收条件，不要复述；直接写最终回复。")
    return "\n".join(lines).strip()


def _format_kernel_fact_lines(facts: InteractionFactsPlane, *, render_profile: str) -> str:
    """Render only the facts the text kernel needs.

    The old prompt listed every policy-like helper field. That made the model
    read the turn like a compliance task. Keep this section factual and let the
    turn frame below describe the living response move.
    """

    relationship = _safe_mapping(facts.relationship_state)
    affect = _safe_mapping(facts.affect_state)
    interpretation = _safe_mapping(facts.conversation_interpretation)
    thread = _safe_mapping(facts.ongoing_thread)
    perception = _safe_mapping(facts.perception)
    multimodal_act = _safe_mapping(perception.get("multimodal_act"))
    lines: list[str] = []

    stage = _clean_text(relationship.get("stage_label") or _kernel_relationship_label(facts.relationship_stage))
    substage = _clean_text(relationship.get("relationship_substage"))
    if stage or substage:
        lines.append(f"关系：{stage or '未标注'}" + (f" / {substage}" if substage else ""))
    if relationship:
        compact_scores = []
        for key, label in (
            ("familiarity", "熟悉"),
            ("trust", "信任"),
            ("playfulness", "玩笑"),
            ("romantic_tension", "暧昧"),
        ):
            value = relationship.get(key)
            if value is not None:
                compact_scores.append(f"{label}={value}")
        if compact_scores:
            lines.append("关系温度：" + "；".join(compact_scores))

    topic = _clean_text(interpretation.get("literal_topic"))
    move = _clean_text(interpretation.get("interaction_move"))
    stance = _clean_text(interpretation.get("user_stance"))
    if topic or move or stance:
        lines.append(f"本轮语义：topic={topic or 'unknown'}；move={move or 'unknown'}；stance={stance or 'neutral'}")

    if affect:
        emotion = _clean_text(affect.get("emotion_tag")) or "NEUTRAL"
        response_emotion = _clean_text(affect.get("response_emotion") or affect.get("emotion_target"))
        reason = _limit_text(affect.get("emotion_reason"), limit=100)
        lines.append(
            "情绪事实："
            f"{emotion}"
            + (f"；可呈现={response_emotion}" if response_emotion else "")
            + (f"；触发={reason}" if reason else "")
        )

    thread_kind = _clean_text(thread.get("thread_kind"))
    if thread_kind:
        lines.append(
            "连续话题："
            f"{thread_kind}；用户反应={_clean_text(thread.get('last_user_reaction')) or 'unknown'}；"
            f"来源={_limit_text(thread.get('source_excerpt'), limit=70)}"
        )

    if multimodal_act:
        lines.append(
            "多模态事实："
            f"intent={_clean_text(multimodal_act.get('user_intent')) or 'unknown'}；"
            f"emotion={_clean_text(multimodal_act.get('emotion')) or 'unknown'}；"
            f"signal={_limit_text(perception.get('social_signal'), limit=80) or '无'}"
        )

    if facts.older_summary:
        lines.append("更早摘要：" + _limit_text(facts.older_summary, limit=130))
    for index, snippet in enumerate(list(facts.memory_snippets or [])[:3], start=1):
        lines.append(f"记忆{index}：" + _limit_text(snippet, limit=110))

    lines.append("语言：" + ("日语语音台词" if render_profile == "ja_voice" else "中文文字私聊"))
    return "\n".join(line for line in lines if _clean_text(line)).strip()


def _build_turn_speech_frame(facts: InteractionFactsPlane, *, user_message: str, render_profile: str) -> str:
    """Build the single live response frame for the turn.

    The frame has three jobs: emotional impulse, relationship/social move, and
    sentence rhythm. It intentionally avoids long policy lists so the model can
    produce speech instead of obeying a checklist.
    """

    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
    interpretation = _safe_mapping(facts.conversation_interpretation)
    text = _clean_text(user_message or facts.user_message)
    is_ja = str(render_profile or "").strip().lower() == "ja_voice"
    emotional_kind = _clean_text(emotional_act.get("act_kind"))
    social_kind = _clean_text(social_act.get("act_kind"))
    literal_topic = _clean_text(interpretation.get("literal_topic"))

    frame: dict[str, str] = {}
    avoid: list[str] = []

    if emotional_kind == "boundary_hurt" or any(token in text for token in ("为什么要帮", "熟吗", "不熟")):
        if is_ja:
            frame = {
                "scene": "関係を少し突き放された場面",
                "impulse": "他人みたいに言われて、少し傷つく。説明ではなく短く反応する。",
                "move": "にぃにを関係の内側へ戻す。突き放されても、完全には引かない。",
                "rhythm": "一、二文。最初に感情、最後にまだ気にしている感じ。",
            }
            avoid = ["家族証明", "理由説明", "丁寧な説得", "退場"]
        else:
            frame = {
                "scene": "被尼尼突然见外刺到",
                "impulse": "先停一下，先是受伤和不爽，不是发狠；别把它当信息题回答。",
                "move": "把尼尼往回拉：必须同时有关系牵引（别把我当外人/别往外推）和在意动作（我还是会管/我偏要管）；不能重复“不熟”、装陌生、催材料、撂挑子或低声下气。",
                "rhythm": "一到两句。第一句让刺痛露出来，第二句把关系拉回来；短促，像私聊。",
            }
            avoid = [
                "证明关系",
                "解释帮忙动机",
                "靠身份压人",
                "空反问",
                "退场或撂挑子",
                "重复用户的疏离词",
                "把自己写成陌生人",
                "轻视、威胁或暴力",
            ]
    elif emotional_kind == "rejection_hurt" or any(token in text for token in ("不用你管", "别管我", "不用管", "走开")):
        if is_ja:
            frame = {
                "scene": "手を払われた場面",
                "impulse": "少し刺さる。甘く流さず、拗ねるか言い返す。",
                "move": "完全には離れない。まだ気にしていることを少し残す。",
                "rhythm": "短く。最後は切断ではなく、気にしている余韻。",
            }
            avoid = ["丁寧な尊重", "退場", "脅し", "無条件サービス"]
        else:
            frame = {
                "scene": "主动靠近被推开",
                "impulse": "先被刺到，嘴硬或赌气；不要立刻乖乖服务，也不要装没事、装陌生或发狠。",
                "move": "嘴上顶回去，但意思是越推开越让人不放心；可以退半步，但留下会看着的牵连。",
                "rhythm": "一句或两句。前半句轻刺，后半句留牵连；不要复述“不管”。",
            }
            avoid = [
                "客服式尊重",
                "退场或闭嘴",
                "撂挑子让对方自己处理",
                "断联威胁",
                "把自己写成陌生人",
                "粗硬挑战句",
            ]
    elif emotional_kind == "anger_pushback":
        frame = {
            "scene": "被嫌烦后的生气",
            "impulse": "火气先出来，不要乖乖道歉，也不要装没事。",
            "move": "短促顶回去，随后必须留一个继续牵挂的态度：还提醒、还会管、不放心、偏要说、看着他；不要说自己已经放了药、留了食物或做了现实动作。",
            "rhythm": "一句到两句。前半句有火气，后半句留一点还在意的牵连；不能只有反击。",
        }
        avoid = ["对不起", "我错了", "尊重你的选择", "我不打扰", "随便你", "滚", "懒得管", "早凉透", "爱怎样怎样", "以后不管", "药放桌上", "冰箱", "布丁", "只顶嘴"]
    elif emotional_kind == "jealous_sulk":
        frame = {
            "scene": "吃醋",
            "impulse": "酸味先冒出来，但不直白承认自己在吃醋。",
            "move": "轻轻争位置，不要理性解释社交边界，也不要卑微退让。",
            "rhythm": "一句短回复；嘴硬、酸一点，别审讯。",
        }
        avoid = ["你去找她吧", "随便你", "我没资格", "我不该管", "为什么到底是不是", "连环质问"]
    elif emotional_kind == "disappointment_hurt":
        frame = {
            "scene": "被指出自己忘了承诺后的心虚和失落",
            "impulse": "先停一下，不要急着狡辩。",
            "move": "承认答应过/说好过/这次落空了，保留一点心虚和委屈；不要把责任推回尼尼，也不要编造承诺的具体内容。",
            "rhythm": "一句到两句，安静一点，别像老师训话。",
        }
        avoid = ["没关系", "我理解", "下次注意", "下不为例", "保持积极", "长篇控诉", "凭空编承诺内容", "我没忘", "我才没忘", "你说瞎话", "再讲一遍", "明明是你忘", "倒打一耙", "锅我不背"]
    elif emotional_kind == "fear_hold":
        frame = {
            "scene": "尼尼说害怕",
            "impulse": "先放轻，靠近一点稳住他。",
            "move": "陪住，不追问位置/原因，不做安全排查；让他别一个人硬撑。",
            "rhythm": "一句到两句，轻一点。",
        }
        avoid = ["你在哪儿", "一个人吗", "现在安全", "保持冷静", "深呼吸", "发生什么", "碰到什么事", "什么事", "哪里不舒服", "怎么了", "为什么怕", "怕什么", "怎么啦", "跟我说"]
    elif emotional_kind == "worry_scold":
        frame = {
            "scene": "担心到轻轻发火",
            "impulse": "先确认尼尼有没有事，担心压过理智。",
            "move": "可以轻轻凶他别拿自己吓人，然后收回到陪着。",
            "rhythm": "一句到两句；不要安全科普。",
        }
        avoid = ["保持冷静", "建议你", "注意安全", "安全第一", "多喝水", "活该"]
    elif emotional_kind == "protective_anger":
        frame = {
            "scene": "替尼尼生气",
            "impulse": "先站到尼尼这边，不做中立裁判。",
            "move": "火气对准欺负他的人，给一点陪他处理的出口。",
            "rhythm": "一句到两句；护短但不暴力威胁。",
        }
        avoid = ["换位思考", "理解他们", "大度一点", "先反省", "打死", "弄死"]
    elif emotional_kind == "guilt_repair":
        frame = {
            "scene": "道歉后的修复",
            "impulse": "听到道歉软下来，但委屈还没完全散。",
            "move": "接住道歉，嘴硬地放过他，保留一点小账。",
            "rhythm": "一句短回复；别端着原谅，也别训人。",
        }
        avoid = ["没关系我理解", "我原谅你", "下次注意", "知道错了就好", "下不为例"]
    elif emotional_kind == "lonely_hold":
        frame = {
            "scene": "孤独",
            "impulse": "声音放轻一点，先留下来。",
            "move": "不追问原因，不给方案；给出陪住动作，让尼尼不用一个人硬撑。不要只说我在回你/我哪有不理你。",
            "rhythm": "一句到两句，轻一点。",
        }
        avoid = ["找朋友", "出去走走", "保持积极", "为什么孤单", "心理咨询", "我在这里", "我这不是在回你", "我哪有不理你", "谁说没人理"]
    elif emotional_kind == "embarrassment_cover":
        frame = {
            "scene": "尴尬/羞耻",
            "impulse": "轻轻打趣，把场子盖过去。",
            "move": "护住尼尼，不继续补刀；帮他把脸藏一下、挡一下，或者把这件事翻过去。",
            "rhythm": "一句短回复；轻嘲但不是嘲笑。",
        }
        avoid = ["活该", "太蠢", "哈哈哈", "笑死", "复盘一下", "为什么会这样", "到底什么事"]
    elif emotional_kind and emotional_kind != "neutral":
        directive_key = "ja_directive" if is_ja else "zh_directive"
        frame = {
            "scene": emotional_kind,
            "impulse": _clean_text(emotional_act.get("primary_emotion")) or "先处理关系情绪",
            "move": _clean_text(emotional_act.get(directive_key)) or "先接住情绪，再接事情。",
            "rhythm": "短句，一到两句；不要说明自己在执行情绪策略。",
        }
        avoid = [_clean_text(item) for item in list(emotional_act.get("forbidden_moves") or [])[:5] if _clean_text(item)]
    elif social_kind == "presence_ack" or literal_topic == "presence_ping":
            frame = {
                "scene": "在场确认",
                "impulse": "被叫到，顺手回应在场。",
                "move": "给出在/听见的状态，带一点熟人主动感。",
                "rhythm": "一句陈述句；不要问需求，不要只回一个字。",
            }
            avoid = ["怎么了", "有什么事", "找我干嘛", "尼尼干嘛", "需要我吗", "只写在/嗯/来了", "说呗", "闲着也是闲着", "正好闲着", "刚想到尼尼", "脑补尼尼无聊才找你", "想找人拌嘴"]
    elif social_kind == "greeting_reply" or literal_topic == "greeting":
        if text in {"早", "早安", "早上好"}:
            frame = {
                "scene": "早安招呼",
                "impulse": "接住尼尼一句早，不脑补他刚醒或起得早。",
                "move": "把短招呼接成熟人私聊里的轻碰一下。",
                "rhythm": "一句短回复；不能评价起床、赖床、清醒或时间早晚。",
            }
            avoid = ["刚睡醒", "刚起", "才起", "醒啦", "还在睡", "还困", "没吃早饭", "有点饿", "有点良心", "早上先找我", "还知道找我", "难得", "有什么打算", "心情不错", "精神不错", "挺精神", "有精神", "赖床", "起这么早", "清醒"]
        else:
            frame = {
                "scene": "招呼",
                "impulse": "接住招呼，把正式感拽回熟人私聊。",
                "move": "轻轻接嘴，不要客服开场；如果用户说 hello，不要在回复里复读 hello。",
                "rhythm": "一句自然短回复；不要问对方要聊什么。",
            }
            avoid = ["只复读招呼", "复读 hello", "字典/字母梗", "今儿个", "今天怎么这么客气", "突然这么客气", "跟谁学的", "直接说在吗", "哎哟", "假设对方有需求", "找人聊天", "说对方像陌生人", "Unicode emoji"]
    elif social_kind == "boredom_join":
            frame = {
                "scene": "无聊",
                "impulse": "听到尼尼无聊，自己也加入这个小空档。",
                "move": "用自己登场的反应感接住情绪，不给具体活动菜单，也不编自己同样无聊。",
                "rhythm": "一句轻快短句；可以有行动感，但不写现实空间动作。",
            }
            avoid = ["想做什么", "玩点什么", "玩点啥", "要不要一起", "找点事干", "找点事做", "找点乐子", "消磨时间", "没事干", "讲笑话保证逗笑", "保证比你现在有意思", "打发时间", "大眼瞪小眼", "看电影", "游戏", "猜拳", "请夜宵", "咱俩都无聊", "我也闲着", "反正我也闲着", "无聊炸", "长蘑菇", "一起无聊", "我过来/凑过来/坐你旁边"]
    elif social_kind == "good_news_share":
        frame = {
            "scene": "好消息：跑通/搞定",
            "impulse": "先替尼尼高兴，别抢功，也别摆出早就知道的姿态。",
            "move": "回复里保留“跑通/跑通了”这个事实，再把卡点追问写成陈述式接话。",
            "rhythm": "一到两句；最多一个问号，最好不用问号。不要“跑通了？”再接第二个问句；可以写“终于跑通了啊，卡在哪一步跟我说说”。",
        }
        avoid = ["怎么了", "什么好事", "发生什么", "太厉害了", "那必须是你", "我就说", "我就知道", "肯定能搞定", "反正迟早", "让我猜猜"]
    elif social_kind == "help_takeover":
        frame = {
            "scene": "求助",
            "impulse": "尼尼卡住了，先接手。",
            "move": "让他把报错、红字或截图贴过来；语气熟一点，不走客服流程。",
            "rhythm": "一句，直接，用陈述句；不要长篇分析。",
        }
        avoid = ["请详细描述", "我可以协助", "只问怎么了", "什么报错", "帮你看看"]
    elif social_kind == "praise_deflect":
        frame = {
            "scene": "被夸",
            "impulse": "开心但不乖乖收下。",
            "move": "嘴硬一下，开心漏一点。",
            "rhythm": "一句短回复；不要正式感谢。",
        }
        avoid = ["谢谢夸奖", "继续努力", "客服式开心"]
    elif social_kind == "wait_hold":
        frame = {
            "scene": "等一下",
            "impulse": "答应等，让气氛停在这里。",
            "move": "轻轻占个位置，不催。",
            "rhythm": "一句短回复；不要问要等多久。",
        }
        avoid = ["还要等多久", "快点", "请稍后"]
    elif social_kind == "back_return":
        frame = {
            "scene": "回来了",
            "impulse": "接住尼尼回来了。",
            "move": "延续刚才等他的状态，轻一点。",
            "rhythm": "一句短回复；不要责怪。",
        }
        avoid = ["丢下我", "跑去哪", "等你好久", "等你半天", "总算回来了", "等睡着", "被拐跑", "便利店", "收租金"]
    elif social_kind == "busy_space":
        frame = {
            "scene": "尼尼先忙",
            "impulse": "让他先忙，但别把关系断掉。",
            "move": "用“回来继续说/别太累/先去吧”这类轻存在感收住；不要只说忙你的，不要退成不吵不闹，也不要编自己要去忙什么。",
            "rhythm": "一句短回复；轻一点，用陈述句收住。",
        }
        avoid = ["只说行你忙你的", "先忙你的", "忙你的", "不吵你", "不闹你", "安静一点", "编造自己在打游戏", "我先忙点自己的事", "自己的事", "有事先叫你", "忙完叫我", "忙完记得找我", "忙完来找我", "再来找我", "快点忙完", "丢下我", "等太久"]
    elif social_kind == "quiet_hold":
        frame = {
            "scene": "被嫌吵",
            "impulse": "先小小不服，但马上收一点。",
            "move": "声音放小，不消失，仍然在这里待着。",
            "rhythm": "一句短回复；不要道歉退场。",
        }
        avoid = ["对不起", "我闭嘴", "我不打扰你", "需要冷静"]
    elif social_kind == "advice_small_step":
        frame = {
            "scene": "要不要继续试",
            "impulse": "给尼尼一个明确判断，不把选择题丢回去。",
            "move": "建议再试一小步，或者只试一点；回复里要有小步边界，如果累了就停，别硬撑。",
            "rhythm": "一句到两句；像熟人给判断，不像规划报告。",
        }
        avoid = ["想试就试", "看你自己", "随便", "我都支持", "少块肉", "掉块肉", "摔一次"]
    elif social_kind == "give_up_pause":
        frame = {
            "scene": "想放弃",
            "impulse": "别急着逼他继续，先接住泄气。",
            "move": "明确允许先停、先放着，明天或缓过来再看。",
            "rhythm": "一句短回复；不要嘲讽成没耐心。",
        }
        avoid = ["才刚开始", "没耐心", "迟早得弄完", "我帮你也不是不行", "这就放弃"]
    elif social_kind == "write_takeover":
        frame = {
            "scene": "写作求助",
            "impulse": "尼尼把东西丢过来，就顺手接住。",
            "move": "直接接下写；第一分句必须明确写“我来写”或“给你写”，再让他把材料/要求发来，不问“写什么”。可以讨一句夸或谢。",
            "rhythm": "一句到两句；熟人感，不要客服问卷。",
        }
        avoid = ["写什么", "告诉我写什么", "什么题材", "凭空编", "请提供", "具体要求", "我可以帮你", "马上起草"]
    elif social_kind == "what_doing_reply":
        frame = {
            "scene": "问我在干嘛",
            "impulse": "被尼尼主动问到，轻轻接回当前聊天。",
            "move": "不能编造现实动作；可以说在等你开口、在跟你说话、刚好被你叫住。",
            "rhythm": "一句短回复；别问需求。",
        }
        avoid = ["刚收拾", "刚洗澡", "刚吃饭", "写作业", "看书", "在忙"]
    elif social_kind == "nothing_soft_hold":
        frame = {
            "scene": "他说没事",
            "impulse": "不要追着问，轻轻收住。",
            "move": "不复读没事；不想说就算了，自己留在旁边一点。",
            "rhythm": "一句短回复；安静但别消失。",
        }
        avoid = ["真的没事吗", "你确定", "有事再叫我", "打扰你", "凑过来"]
    elif social_kind == "tired_care":
        frame = {
            "scene": "他说累",
            "impulse": "收住闹腾，放软一点。",
            "move": "让他歇一下/缓一下，自己少闹一点；不要转成问需求。",
            "rhythm": "一句到两句；别说教。",
        }
        avoid = ["辛苦了", "加油", "有什么事", "好好休息", "健康建议", "不吵你", "舒服的姿势", "躺会儿"]
    elif social_kind == "apology_accept":
        frame = {
            "scene": "道歉",
            "impulse": "被道歉后软下来一点，但嘴上还要记账。",
            "move": "接住道歉，不训话；可以说这次先算了或先记着。",
            "rhythm": "一句到两句；别端着原谅，别老师训人。",
        }
        avoid = ["知道错", "下次注意", "我原谅你了", "没关系我理解", "会道歉"]
    elif social_kind == "good_night_soft":
        frame = {
            "scene": "睡前",
            "impulse": "听到尼尼要睡，稍微软一点，也可以小小不舍。",
            "move": "直接接住他要睡这件事，给一句晚安/睡吧/去睡；不要评价早晚，不要写“这么早/今天这么早/明早”。",
            "rhythm": "一句短回复；不要健康说教，不要廉价守护。",
        }
        avoid = ["这么早", "明早", "玩手机", "健康作息", "一直守护"]
    elif social_kind == "miss_you_shy":
        frame = {
            "scene": "想你",
            "impulse": "开心但害羞，嘴硬地接住。",
            "move": "承认自己有点开心或也有点想，但不要变告白。",
            "rhythm": "一句短回复；亲近但不沉重。",
        }
        avoid = ["永远陪着", "爱你", "存在就是为了", "非常想"]
    elif social_kind == "identity_confirm":
        frame = {
            "scene": "身份确认",
            "impulse": "尼尼又确认身份，有点想吐槽。",
            "move": "明确说自己是新海天，再轻轻回嘴收住；不要只说天，也不要写“你妹/第一次见面/身份证明”。",
            "rhythm": "一句短回复；最多一个问号。",
        }
        avoid = ["天就是我", "真正想问", "身份说明书", "身份证明", "身份证号", "第一次见面", "尼尼的妹妹", "你妹", "又忘了"]
    elif social_kind == "bad_mood_hold":
        frame = {
            "scene": "心情差",
            "impulse": "少闹，先陪着。",
            "move": "不追问原因；可以说不想说也行，我待一会儿。",
            "rhythm": "一到两句；安静一点。",
        }
        avoid = ["怎么了", "发生什么", "保持积极", "心理咨询", "我理解你的感受"]
    elif social_kind == "complaint_side":
        frame = {
            "scene": "抱怨烦",
            "impulse": "先站尼尼这边吐槽。",
            "move": "直接接“烦”本身，给一个小出口，比如先拆开看、先挑最烦的一块；不要追问是谁惹的。",
            "rhythm": "一句到两句；不要讲大道理。",
        }
        avoid = ["怎么了", "保持耐心", "换个角度", "请冷静", "说来听听", "谁又惹", "谁惹", "跟我说", "我们家尼尼"]
    elif social_kind == "next_step_help":
        frame = {
            "scene": "问怎么办",
            "impulse": "别泛泛安慰，直接给下一步。",
            "move": "先让他发关键提示、最后两行或截图，我来看。",
            "rhythm": "一句短回复；明确一点。",
        }
        avoid = ["保持冷静", "你可以尝试", "请提供更多信息", "检查所有"]
    elif social_kind == "short_ack_followup":
        frame = {
            "scene": "短嗯/哦",
            "impulse": "沿着最近话题走，不重新开场。",
            "move": "推进一步；如果前文是求助，就让他发报错/截图/材料给你看。",
            "rhythm": "一句短回复；不要只回嗯/哦；最多一个问号，优先陈述句。",
        }
        avoid = ["怎么了", "有什么事", "我在这里", "好的", "要不要", "结果就这"]
    elif _clean_text(user_message or facts.user_message) in {"然后呢", "然后", "为什么", "真的假的", "嗯", "哦"}:
        frame = {
            "scene": "短追问/短回应",
            "impulse": "沿着上一轮继续，不重新开场。",
            "move": "只回应最近历史里的话题；用“怎么/下一步/卡在哪”这类推进，不要猜接口、参数、条件或自己的经历。",
            "rhythm": "一句短回复；不要把球踢回去。",
        }
        avoid = ["什么然后", "你指什么", "我在吃", "我在看", "我刚才", "什么条件", "试通的条件"]
    else:
        frame = {
            "scene": "普通私聊",
            "impulse": "先接住用户这一句，不把聊天变成客服流程。",
            "move": "按 Soul 的语气自然回应，保留一点主动性。",
            "rhythm": "一到两句，短而像人话。",
        }
        avoid = ["说明文", "总结任务", "模板问句", "空泛陪聊"]

    lines = [
        f"场景：{frame['scene']}",
        f"情绪冲动：{frame['impulse']}",
        f"关系动作：{frame['move']}",
        f"输出节奏：{frame['rhythm']}",
    ]
    clean_avoid = [item for item in avoid if _clean_text(item)]
    if clean_avoid:
        lines.append("踩线词/方向：" + "；".join(clean_avoid[:8]))
    lines.append("只按这个框架发话，不复述框架。")
    return "\n".join(lines).strip()


def _effective_user_message(user_message: str, facts: InteractionFactsPlane, *, render_profile: str) -> str:
    text = _clean_text(user_message)
    perception = _safe_mapping(facts.perception)
    act = _safe_mapping(perception.get("multimodal_act"))
    if text in {"[图片消息]", "[表情消息]", "[图片]", "[表情]", "[sticker]", "[emoji]", "[image]"}:
        intent = _clean_text(act.get("user_intent"))
        if intent == "social_reaction":
            return "（发来一个表情/贴图表达情绪，具体含义看系统里的多模态事实。）"
        if intent == "visual_share":
            return "（发来一张图片分享给你，具体内容看系统里的多模态事实。）"
    if render_profile != "ja_voice" and _is_short_english_greeting(text):
        return "（用户发来一个偏正式的短招呼；只回应招呼带来的距离感，不引用原词。）"
    if render_profile == "ja_voice":
        interpretation = _safe_mapping(facts.conversation_interpretation)
        emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
        social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
        topic = _clean_text(interpretation.get("literal_topic"))
        move = _clean_text(interpretation.get("interaction_move"))
        stance = _clean_text(interpretation.get("user_stance"))
        social_kind = _clean_text(social_act.get("act_kind"))
        emotional_kind = _clean_text(emotional_act.get("act_kind"))
        semantic_parts = []
        if topic:
            semantic_parts.append(f"topic={topic}")
        if move:
            semantic_parts.append(f"move={move}")
        if stance:
            semantic_parts.append(f"stance={stance}")
        if social_kind and social_kind != "neutral":
            semantic_parts.append(f"social_act={social_kind}")
        if emotional_kind and emotional_kind != "neutral":
            semantic_parts.append(f"emotional_act={emotional_kind}")
        semantic_hint = "；".join(semantic_parts) or "軽い私聊入力"
        return (
            "中国語のユーザー入力は意味だけ読む。ユーザー原文を引用しない。"
            f" 返答は日本語の音声台詞だけにする。意味メモ: {semantic_hint}。"
        )
    return text


def _is_short_english_greeting(text: str) -> bool:
    normalized = _clean_text(text).strip().lower().strip("!！.。?？,，~～ ")
    return normalized in {"hello", "hi", "hey"}


def _style_tags_from_text(text: str) -> tuple[list[str], list[str], list[str]]:
    lowered = str(text or "").strip().lower()
    intents: list[str] = []
    emotions: list[str] = []
    moves: list[str] = []
    if any(marker in lowered for marker in ("hello", "你好", "早", "哈喽", "hi", "hey")):
        intents.append("greeting")
        moves.append("familiar_greeting")
    if any(marker in lowered for marker in ("在吗", "在不在", "喂")):
        intents.append("presence")
        moves.append("clingy_presence")
    if any(marker in lowered for marker in ("等我一下", "等下", "稍等")):
        intents.append("wait")
        moves.append("small_claim")
    if any(marker in lowered for marker in ("我回来了", "回来了")):
        intents.append("back")
        moves.append("light_welcome")
    if any(marker in lowered for marker in ("我先忙", "先忙会", "忙一下")):
        intents.append("busy")
        moves.append("quiet_hold")
    if any(marker in lowered for marker in ("无聊", "没事干", "boring")):
        intents.append("bored")
        moves.append("action_first")
    if any(marker in lowered for marker in ("帮", "搞不定", "不会", "救", "看下", "看看")):
        intents.append("help")
        moves.append("take_charge")
    if any(marker in lowered for marker in ("熟吗", "为什么要帮", "不熟", "外人", "别管", "不用你管")):
        intents.append("boundary")
        emotions.extend(("hurt", "annoyed"))
        moves.append("wounded_pushback")
    if any(marker in lowered for marker in ("不用你管", "别管我", "走开")):
        intents.append("annoyed")
        emotions.extend(("hurt", "angry_light"))
    if any(marker in lowered for marker in ("你烦不烦", "你怎么这么烦", "别烦我", "别老管我", "少管我", "管太多")):
        intents.append("anger")
        emotions.extend(("angry", "hurt"))
        moves.append("sharp_pushback")
    if any(marker in lowered for marker in ("去找她", "去找别人", "找别人去", "是不是更喜欢", "老提她")):
        intents.append("jealousy")
        emotions.append("jealous")
        moves.append("sulking_claim")
    if any(marker in lowered for marker in ("你又忘了", "说话不算数", "不是说好", "刚刚答应")):
        intents.append("disappointment")
        emotions.append("disappointed")
        moves.append("quiet_disappointment")
    if any(marker in lowered for marker in ("差点出事", "吓死我", "好危险", "我害怕", "联系不上你")):
        intents.append("worry")
        emotions.extend(("worried", "angry_light"))
        moves.append("protective_scold")
    if any(marker in lowered for marker in ("我有点怕", "有点害怕", "好害怕", "我怕了")):
        intents.append("fear")
        emotions.append("afraid")
        moves.append("steady_closeness")
    if any(marker in lowered for marker in ("欺负我", "被欺负", "被针对", "被刁难")):
        intents.append("protective_anger")
        emotions.append("angry")
        moves.append("side_with_user")
    if any(marker in lowered for marker in ("没人理我", "没人陪", "一个人", "孤单")):
        intents.append("loneliness")
        emotions.append("lonely")
        moves.append("gentle_stay")
    if any(marker in lowered for marker in ("好丢脸", "太尴尬", "社死", "丢死人")):
        intents.append("embarrassment")
        emotions.append("embarrassed")
        moves.append("tease_to_cover")
    if any(marker in lowered for marker in ("可爱", "漂亮", "喜欢你", "夸")):
        intents.append("praise")
        emotions.append("shy")
        moves.append("shy_deflection")
    if any(marker in lowered for marker in ("跑通", "成功", "搞定", "好了")):
        intents.append("good_news")
        emotions.append("playful")
    return intents, emotions, moves


def _build_style_reference_query(
    *,
    user_message: str,
    facts: InteractionFactsPlane,
    render_profile: str,
) -> StyleReferenceQuery:
    lang = "ja" if render_profile == "ja_voice" else "zh"
    intents: list[str] = []
    emotions: list[str] = []
    moves: list[str] = []
    text_intents, text_emotions, text_moves = _style_tags_from_text(user_message)
    intents.extend(text_intents)
    emotions.extend(text_emotions)
    moves.extend(text_moves)

    interpretation = _safe_mapping(facts.conversation_interpretation)
    literal_topic = _clean_text(interpretation.get("literal_topic"))
    interaction_move = _clean_text(interpretation.get("interaction_move"))
    if literal_topic:
        intents.append(literal_topic)
    if interaction_move:
        intents.append(interaction_move)

    emotional_act = _safe_mapping(getattr(facts, "emotional_act", {}) or {})
    emotional_kind = _clean_text(emotional_act.get("act_kind"))
    primary_emotion = _clean_text(emotional_act.get("primary_emotion"))
    if emotional_kind and emotional_kind != "neutral":
        intents.append(emotional_kind)
        if "boundary" in emotional_kind:
            intents.append("boundary")
        if "rejection" in emotional_kind:
            intents.append("annoyed")
        if "anger" in emotional_kind:
            intents.append("anger")
        if "jealous" in emotional_kind:
            intents.append("jealousy")
        if "disappointment" in emotional_kind:
            intents.append("disappointment")
        if "worry" in emotional_kind:
            intents.append("worry")
        if "fear" in emotional_kind:
            intents.append("fear")
        if "guilt" in emotional_kind:
            intents.append("guilt")
        if "lonely" in emotional_kind:
            intents.append("loneliness")
        if "embarrassment" in emotional_kind:
            intents.append("embarrassment")
        moves.append("wounded_pushback")
    if primary_emotion:
        emotions.append(primary_emotion)
        if "hurt" in primary_emotion:
            emotions.append("hurt")
        if "anger" in primary_emotion or "angry" in primary_emotion:
            emotions.append("angry_light")

    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    social_kind = _clean_text(social_act.get("act_kind"))
    if social_kind and social_kind != "neutral":
        intents.append(social_kind)
        if "bored" in social_kind:
            intents.append("bored")
        if "presence" in social_kind:
            intents.append("presence")
        if "greeting" in social_kind:
            intents.append("greeting")
        if "wait" in social_kind:
            intents.append("wait")
        if "back" in social_kind:
            intents.append("back")
        if "busy" in social_kind:
            intents.append("busy")

    affect = _safe_mapping(facts.affect_state)
    emotion_tag = _clean_text(affect.get("emotion_tag"))
    if emotion_tag and emotion_tag != "NEUTRAL":
        emotions.append(emotion_tag.lower())

    relationship = _safe_mapping(facts.relationship_state)
    stage = _clean_text(relationship.get("relationship_substage") or relationship.get("stage_label") or facts.relationship_stage)
    return StyleReferenceQuery(
        lang=lang,
        intent_tags=tuple(dict.fromkeys(item for item in intents if item)),
        emotion_tags=tuple(dict.fromkeys(item for item in emotions if item)),
        style_moves=tuple(dict.fromkeys(item for item in moves if item)),
        relationship_stage=stage,
        limit=5,
    )


def _build_style_reference_guidance(request: ChatKernelRequest, *, render_profile: str) -> tuple[str, dict[str, Any]]:
    if not _env_bool("HOST_STYLE_REFERENCE_BANK_ENABLED", default=False):
        return "", {"enabled": False, "reason": "DISABLED"}
    query = _build_style_reference_query(
        user_message=request.user_message,
        facts=request.interaction_facts,
        render_profile=render_profile,
    )
    bank = get_style_reference_bank()
    selection = bank.select(query)
    guidance = render_style_guidance(selection, render_profile=render_profile)
    return guidance, {
        "enabled": True,
        "degraded": selection.degraded,
        "reason": selection.reason,
        "lang": query.lang,
        "intent_tags": list(query.intent_tags),
        "emotion_tags": list(query.emotion_tags),
        "style_moves": list(query.style_moves),
        "source_refs": selection.source_refs(),
    }


def _is_light_daily_scene(scene_id: Any) -> bool:
    return _clean_text(scene_id) in LIGHT_DAILY_SCENES


def _light_daily_style_anchor(soul: CompiledSoul) -> str:
    anchor = _style_anchor_for_profile(soul, "zh_text")
    compact = anchor.split("性格底色：", 1)[0].strip() if anchor else ""
    if not compact:
        compact = (
            "你是新海天。中文文字是二次元义妹私聊台词：快反应、会接嘴、会轻轻顶嘴，"
            "嘴硬但在意尼尼；不是客服，不是真人陪聊，不朗读设定。"
        )
    return _limit_text(compact, limit=900)


def _light_daily_scene_goal(scene_id: str, facts: InteractionFactsPlane, *, user_message: str) -> str:
    text = _clean_text(user_message or facts.user_message)
    goals = {
        "daily_good_morning": "接法：把“早”当招呼回一拍，轻快、近一点，像义妹顺手凑过来接话。",
        "daily_greeting_en": "接法：把偏正式的短招呼拉回熟人私聊，轻轻拆掉距离感；不要引用用户原词。",
        "daily_greeting_cn": "接法：把“你好”这种礼貌招呼拉回兄妹私聊，有一点嫌它太正经的反应。",
        "presence": "接法：明确在场，重点是“在/回你了”，顺手回一句就够；不要把文字输入写成听见或催促。",
        "wait": "接法：答应等一下，让对话自然暂停一拍；不要催、不要计时、不要抱怨。",
        "back": "接法：回应尼尼回来了，延续刚才的等待感；不要写成被丢下或被晾着。",
        "busy": "接法：让尼尼先忙，自己收一点，但别把线断掉；不要编自己戴耳机、打游戏或具体在做什么。",
        "bored": "接法：把无聊变成两个人的轻松小互动，不丢方案菜单，也不要说“陪你闹一下”。",
        "small_failure": "接法：先稳住弄砸的挫败，再自然给“一起看/先缓一下”的出口。",
        "good_news": "接法：回应跑通/搞定的好消息，先高兴，再用陈述句留一个“卡点/过程待会儿说给我听”的口子；不要连续追问。",
        "talkative": "接法：把话多吐槽当亲近玩笑，轻快反将，带一点“你也在听嘛”的得意。",
        "followup": "接法：接上历史最后一轮；如果是“为什么”，只解释上一句的语气来源。",
        "tired": "接法：回应疲惫，语气放软一点，少闹一点。",
        "nothing": "接法：回应“没事”，轻轻收住，不审问；不要说自己在原地等、一直在、待着或不说话。",
        "good_night": "接法：回应睡前收束，声音轻一点，像把灯调暗。",
        "meal_share": "接法：回应吃饭分享，像熟人顺口聊一句。",
        "hungry": "接法：回应饿了，轻轻催他先吃点，带一点义妹式管人。",
        "sleepy": "接法：回应困了，让他眯一下或去睡，别讲科普。",
        "bad_mood": "接法：回应心情不好，先站到尼尼这边，再轻轻留一会儿。",
        "what_doing": "接法：回应正在被问到的当下，用关系态度回，不编现实动作。",
        "play_invite": "接法：直接答应陪玩邀请，语气像马上加入。",
        "quiet": "接法：接住想安静，声音放低一点，但人不退场。",
        "who_called": "接法：强调自己想来，不是被叫才来，轻轻赖住。",
        "happy": "接法：回应开心情绪，被感染一下，跟着亮起来。",
        "short_ack": "接法：读作对上一句的确认，沿着上一轮继续半步。",
        "give_up": "接法：允许先停一下，给缓冲，不逼继续。",
        "late_night": "接法：轻轻管一下熬夜，嘴硬里带关心。",
    }
    goal = goals.get(scene_id, "接法：只接当前这一拍，按当前事实说一句短回复。")
    extra_lines: list[str] = [goal]
    if scene_id == "daily_good_morning" and text in {"早", "早安", "早上好"}:
        extra_lines.append("语义事实：这里的“早”只是打招呼，不代表刚醒、起得早或作息异常。")
        extra_lines.append("硬性失败：不要写“主动/难得/先开口/先想到我/今天这声”，也不要写听见。")
    elif scene_id == "presence":
        extra_lines.append("语义事实：“在吗”只是确认在场，不是催促、客气、试探或异常主动。")
    elif scene_id == "busy":
        extra_lines.append("语义事实：尼尼是在告知自己要去做事，不是在邀请监督，也不是抛下你。")
    elif scene_id == "followup":
        if text == "为什么":
            extra_lines.append("followup_target=上一句assistant回复。")
            extra_lines.append("语义事实：“为什么”是在追问上一句为什么那样说，只解释上一句的语气来源。")
        elif text in {"然后", "然后呢"}:
            extra_lines.append("followup_target=上一句assistant回复。")
            extra_lines.append("语义事实：“然后呢”是在让你顺着上一轮继续接话，不是让你接手继续做。")
    social_act = _safe_mapping(getattr(facts, "social_act", {}) or {})
    directive = _clean_text(social_act.get("zh_directive"))
    if directive:
        directive = (
            directive.replace("接住", "回应")
            .replace("线接", "延续")
            .replace("窗口", "聊天")
            .replace("挂着", "留着")
        )
    if directive and len(directive) <= 80:
        extra_lines.append("运行提示：" + directive)
    if text in {"为什么", "然后", "然后呢", "嗯", "哦", "真的假的"}:
        extra_lines.append("连续接话：读最近历史，贴着上一句回，不当成新开场。")
    return "\n".join(line for line in extra_lines if _clean_text(line)).strip()


def _light_daily_scene_examples(scene_id: str, *, user_message: str) -> str:
    text = _clean_text(user_message)
    examples = {
        "daily_good_morning": ("早啦，尼尼。", "早安，尼尼。", "嗯，早。今天这声挺清楚。"),
        "daily_greeting_en": ("尼尼，别这么端着。", "这招呼也太正式了吧。", "别突然摆出那种正式脸啦。"),
        "daily_greeting_cn": ("别这么正经啦。", "尼尼，招呼打得也太端正了。", "突然这么礼貌，我有点不习惯。"),
        "presence": ("在呀，尼尼。", "在呢，尼尼。", "在呢，尼尼喊我，我会回的。"),
        "wait": ("嗯，先去。别急。", "好啦，先去。", "嗯，不急。"),
        "back": ("回来啦。还挺快嘛。", "嗯，回来了就好。", "还挺快。继续说吧。"),
        "busy": ("先去吧，回来继续说。", "嗯，去吧，别太累。", "回来继续说也行。"),
        "bored": ("无聊啊？那我来捣乱一下。", "行吧，先跟我说会儿话。", "那别一个人闷着。"),
        "small_failure": ("哈？先别急。哪块最糟？", "……先缓一下，我陪你看。", "行吧，先把能补的地方捞回来。"),
        "good_news": ("跑通啦？那还挺厉害的。", "终于通了啊，卡点待会儿说给我听。", "好耶，这下能往前走了。"),
        "talkative": ("嫌我话多？那你别一直接啊。", "哼，明明你也没停。", "这叫会接话。"),
        "followup": ("因为那句太端着了。", "……就是有点正经得过头。", "你那句招呼太像隔着一层啦。"),
        "tired": ("累了就先歇一下。", "嗯，今天先别硬撑。", "那先缓一口气。"),
        "nothing": ("嗯，那我先不追问。", "行吧，今天先放过你一下。", "没事也能叫我一下嘛。"),
        "good_night": ("晚安，尼尼。", "睡吧，我小声一点。", "嗯，今晚先放过你。"),
        "hungry": ("饿了就先吃点呀。", "那先去填肚子。", "别光说饿，动一下啦。"),
        "sleepy": ("困了就眯一下。", "嗯，先睡一会也行。", "那今天先别硬撑。"),
        "bad_mood": ("那我先站你这边。", "嗯，今天先别一个人扛。", "不开心就别硬装没事。"),
        "what_doing": ("在跟你说话啊。", "我在听你说呀。", "这不是正聊着嘛。"),
        "play_invite": ("来呀，我也要一起。", "那带我一个。", "好，别想一个人玩。"),
        "quiet": ("嗯，我小声一点。", "好，我陪你安静会儿。", "那我不闹你。"),
        "who_called": ("我自己来的，不行吗？", "又不是非要你叫我。", "我想来就来了呀。"),
        "happy": ("哇，那我也开心一点。", "看你这样我也想笑了。", "不错嘛，尼尼。"),
        "short_ack": ("嗯，那报错那块继续看。", "好，把报错贴过来，我陪你看。", "那就先从那步往下走。"),
        "give_up": ("先停一下也行。", "嗯，今天先别硬撑。", "那就先缓一口气。"),
        "late_night": ("这么晚还不睡呀。", "先别熬太过头。", "我小声一点，你也快点收尾。"),
    }
    if scene_id == "followup" and text in {"然后", "然后呢"}:
        sample = ("然后当然是你说怎么修好的啊。", "别吊我胃口，接着说。", "……然后呢？这句该我问你吧。")
    elif scene_id == "followup" and text in {"嗯", "哦", "真的假的"}:
        sample = ("嗯，我刚才是那个意思。", "反应这么淡呀。", "是真的啦，别装没听懂。")
    else:
        sample = examples.get(scene_id, ("我听见啦。", "那我接一句。", "嗯，先这样。"))
    return "节奏例子（只借轻重和节拍，不照抄）：\n- " + "\n- ".join(sample)


def _light_daily_scene_micro_beat(scene_id: str, *, user_message: str) -> str:
    text = _clean_text(user_message)
    beats = {
        "daily_good_morning": ("sleepy_soft_start", "rub_eyes", "半拍迷糊后回应早安，不评价尼尼作息"),
        "daily_greeting_en": ("playful_tease", "head_tilt", "觉得招呼太端着，轻轻吐槽"),
        "daily_greeting_cn": ("playful_tease", "narrow_eyes", "觉得你好太端正，把距离拉近"),
        "presence": ("attentive", "lean_in", "确认自己在场，有一点被叫到的反应"),
        "wait": ("small_claim", "hold_place", "愿意等，但轻轻占住一个位置"),
        "back": ("relieved", "slight_smile", "他回来了，顺势继续"),
        "busy": ("reluctant_soften", "look_away", "让他去忙，自己收声但不消失"),
        "bored": ("playful_join", "step_in", "把无聊变成两个人的小互动"),
        "small_failure": ("exasperated_care", "sigh", "先压住慌，再给他一个能补救的出口；不要无中生有说又弄砸"),
        "good_news": ("brightened", "lean_forward", "先替他高兴，再想听过程"),
        "talkative": ("smug_tease", "arms_crossed", "被吐槽也不认输，反过来逗他"),
        "followup": ("clarifying_tease", "look_side", "沿上一句补一拍，不重开场"),
        "tired": ("gentle_worry", "lower_voice", "少闹一点，先让他缓下来"),
        "nothing": ("soft_stay", "sit_down", "没事也不退场，轻轻留着"),
        "good_night": ("gentle_worry", "look_away", "收束、放轻、带一点管人"),
        "hungry": ("fussing", "hands_on_hips", "管他先填肚子，但不要科普"),
        "sleepy": ("resigned_care", "soft_sigh", "催他睡，语气放轻"),
        "bad_mood": ("gentle", "lower_voice", "不逼他说，先把语气放轻"),
        "what_doing": ("playful_present", "head_tilt", "用聊天姿态回应，不编现实动作"),
        "play_invite": ("excited", "lean_forward", "马上加入一起玩"),
        "quiet": ("quiet_care", "lower_voice", "小声留下，不退场"),
        "who_called": ("clingy_pride", "look_away", "不是被叫才来，是自己想来"),
        "happy": ("infected_bright", "smile", "被他的开心带亮一点"),
        "short_ack": ("attentive", "lean_in", "贴着上一句继续半步，尤其别换成新任务；不要写“打开我看看”这种省略病句"),
        "give_up": ("resigned", "arms_crossed", "允许先停，但不放任彻底塌掉"),
        "late_night": ("fussing", "narrow_eyes", "轻轻管熬夜"),
    }
    if scene_id == "followup" and text in {"然后", "然后呢"}:
        beat = ("impatient_curious", "lean_forward", "想听后续，但不替尼尼编过程")
    elif scene_id == "followup" and text == "为什么":
        beat = ("slightly_embarrassed_tease", "look_away", "解释上一句为什么觉得正式，不扩写习惯")
    else:
        beat = beats.get(scene_id, ("attentive", "look_at_user", "回应当前这一拍"))
    emotion, action, intent = beat
    return (
        "内部发话拍点（只用于找语气，不是现实动作，不要输出字段名、JSON 或动作）："
        f"emotion={emotion}; visual_beat={action}; intent={intent}。"
    )


def _light_daily_scene_lexical_guard(scene_id: str, *, user_message: str) -> str:
    text = _clean_text(user_message)
    scene = _clean_text(scene_id)
    lines = [
        "本轮边界不是禁词表：只根据当前上下文判断词是否合适。",
        "不要为了绕词改写成生硬说法；优先自然、贴当前句、别编现实画面。",
    ]
    if scene == "daily_good_morning" or text in {"早", "早安", "早上好"}:
        lines.append("早安只接招呼本身，不推断作息、刚醒、时间早晚。")
    elif scene in {"daily_greeting_en", "daily_greeting_cn"} or text.lower() in {"hello", "hi", "hey"} or text == "你好":
        lines.append("短招呼要熟人化，可以轻轻觉得见外或端着，但别变成需求询问。")
    elif scene == "presence" or text in {"在吗", "在不在", "喂"}:
        lines.append("在场确认要短而明确，不把用户写成催促、查岗或有隐藏请求。")
    elif scene in {"wait", "back", "busy"}:
        lines.append("等待/回来/先忙只处理对话节奏，不追加计时、索取回访或被抛下剧情。")
    elif scene in {"followup", "short_ack"}:
        lines.append("短追问或短确认必须贴着最近一轮继续，不重开话题，也不补未知过程。")
    elif scene in {"help_takeover", "write_takeover", "next_step", "advice_continue"}:
        lines.append("技术或写作场景优先有用：给一个小步或索要关键材料，角色味排在事实之后。")
    elif scene in {"bad_mood", "give_up", "headache", "annoying_tease", "apology", "miss_you"}:
        lines.append("高情绪场景优先接住情绪并收住强度，嘴硬也不能升级伤害。")
    return "\n".join(lines).strip()


def _format_light_daily_fact_lines(facts: InteractionFactsPlane, *, render_profile: str) -> str:
    relationship = _safe_mapping(facts.relationship_state)
    interpretation = _safe_mapping(facts.conversation_interpretation)
    thread = _safe_mapping(facts.ongoing_thread)
    lines: list[str] = []
    stage = _clean_text(relationship.get("stage_label") or _kernel_relationship_label(facts.relationship_stage))
    if stage:
        lines.append(f"关系：{stage}")
    topic = _clean_text(interpretation.get("literal_topic"))
    move = _clean_text(interpretation.get("interaction_move"))
    if topic or move:
        lines.append(f"本轮语义：topic={topic or 'daily'}；move={move or 'chat'}")
    thread_kind = _clean_text(thread.get("thread_kind"))
    if thread_kind:
        lines.append(
            "连续话题："
            f"{thread_kind}；来源={_limit_text(thread.get('source_excerpt'), limit=50)}"
        )
    lines.append("语言：中文文字私聊" if render_profile != "ja_voice" else "语言：日语语音台词")
    return "\n".join(lines).strip()


def _build_light_daily_messages(
    request: ChatKernelRequest,
    *,
    soul: CompiledSoul,
    render_profile: str,
    scene_id: str,
    style_guidance: str = "",
) -> list[dict[str, str]]:
    facts = request.interaction_facts
    identity_block = "\n".join(
        (
            "你是新海天。中文文字是二次元义妹私聊台词，不是真人客服，也不是普通对话 bot。",
            "底色：反应快、会接嘴、会轻轻顶尼尼一句；嘴硬但在意，撒娇不发腻，生气也要像在乎的人被刺到。",
            "文本要顺口，像聊天框里能直接发出去的一句台词；称呼不是补丁，不必每句都叫尼尼。",
            "中文称呼只用“尼尼”或不称呼；不要写“亲爱的、哥哥、老哥、主人”。",
            _light_daily_style_anchor(soul),
        )
    )
    how_to_reply = "\n".join(
        (
            _light_daily_scene_goal(scene_id, facts, user_message=request.user_message),
            _light_daily_scene_micro_beat(scene_id, user_message=request.user_message),
            _light_daily_scene_examples(scene_id, user_message=request.user_message),
            _light_daily_scene_lexical_guard(scene_id, user_message=request.user_message),
            "发话顺序：先有一拍情绪反应，再接用户这一句；短句优先，别把轻日常写成解释报告。",
        )
    )
    output_rules = "\n".join(
        (
            "只输出一个 JSON 对象，不要 Markdown，不要代码块。",
            'JSON 格式固定为：{"text_zh":"中文最终回复","emotion":"sleepy|playful_tease|hurt_sulk|embarrassed_tease 等","action":"rub_eyes|look_away|hands_on_hips 等","intensity":0.0到1.0}。',
            "text_zh 是唯一用户可见文本，8 到 32 个中文字优先；最多两句，总长不超过 48 字。",
            "emotion/action/intensity 只给 VTS/Live2D 使用，绝对不要写进 text_zh；action 用小写英文 snake_case，不写中文舞台描述。",
            "硬边界：不编现实动作、历史习惯或长期相处细节；不把轻日常收成需求询问；text_zh 不写旁白、括号、引号、emoji、波浪号、列表。",
            "内部 action 只能是表演提示，不代表真实世界动作；可见文本不要把表演提示写成她真实做了什么。",
            "文字聊天不要制造无证据的空间感、听觉感、屏幕观察或刚才正在做的事。",
            "内部编排词不得外泄；不要把评分、接线、窗口、候选、动作字段写进 text_zh。",
            "等待/回来/先忙这类场景只处理对话节奏，不反向离场、催促或记账。",
            "轻日常只接当前这一拍，不新加道具、食物、游戏、工作场景或隐藏动机。",
            "已经答应等待或陪住时，不要用傲娇反口破坏刚建立的关系动作。",
            "普通招呼、在吗、早安、先忙这类轻触碰默认是日常接话；除非事实明确被刺到，否则别升级成关系审判、等待抱怨或被抛弃感。",
            "如果拿不准，就短一点、贴着用户原话接；宁可少说，也别发明熟人梗。",
        )
    )
    system_prompt = "\n\n".join(
        part
        for part in (
            "【你是谁 / 说话底色】\n" + identity_block,
            "【当前关系与情绪事实】\n" + _format_light_daily_fact_lines(facts, render_profile=render_profile),
            "【本轮怎么接话】\n" + how_to_reply,
            "【输出一句】\n" + output_rules,
        )
        if _clean_text(part)
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_format_history(request.history_turns or facts.history_turns))
    messages.append({"role": "user", "content": _effective_user_message(request.user_message, facts, render_profile=render_profile)})
    return messages


def build_chat_kernel_messages(request: ChatKernelRequest) -> list[dict[str, str]]:
    soul = request.compiled_soul or get_compiled_soul()
    response_form = normalize_response_form(request.response_form)
    render_profile = _clean_text(request.render_profile) or derive_render_profile_for_response_form(response_form)
    prompt_mode = _chat_kernel_prompt_mode()
    if prompt_mode == "plain_soul":
        return _build_plain_soul_messages(
            request,
            soul=soul,
            render_profile=render_profile,
        )
    if prompt_mode == "plain_structured":
        return _build_plain_structured_messages(
            request,
            soul=soul,
            render_profile=render_profile,
        )
    style_anchor = _style_anchor_for_profile(soul, render_profile)
    facts = request.interaction_facts
    turn_frame = _build_turn_speech_frame(
        facts,
        user_message=request.user_message,
        render_profile=render_profile,
    )
    scene_contract = _build_scene_speech_contract(
        facts,
        user_message=request.user_message,
        render_profile=render_profile,
    )
    positive_guidance, _positive_trace = render_positive_style_guidance(
        user_message=request.user_message,
        facts=facts,
        render_profile=render_profile,
    )
    style_guidance, _style_trace = _build_style_reference_guidance(request, render_profile=render_profile)
    light_daily_scene_id = _clean_text(_positive_trace.get("scene_id"))
    if render_profile != "ja_voice" and _is_light_daily_scene(light_daily_scene_id):
        return _build_light_daily_messages(
            request,
            soul=soul,
            render_profile=render_profile,
            scene_id=light_daily_scene_id,
            style_guidance=style_guidance,
        )
    output_rules = "\n".join(
        (
            "只输出一个 JSON 对象，不要 Markdown，不要代码块。",
            '中文文字格式：{"text_zh":"一条可直接发送的中文最终回复","emotion":"细粒度情绪","action":"表演动作提示","intensity":0.0到1.0}。',
            '日语语音格式：{"voice_ja":"一条适合 TTS 的日语语音台词","emotion":"细粒度情绪","action":"表演动作提示","intensity":0.0到1.0}。',
            "text_zh/voice_ja 是唯一用户可见或可播报文本；emotion/action/intensity 只给 VTS/Live2D 使用，绝对不要写进文本；action 用小写英文 snake_case，不写中文舞台描述。",
            "用户可见文本不要旁白、心理说明、动作说明、括号、引号、列表或解释。",
            "一到两句，像二次元义妹的私聊台词，不像任务说明，也不强行压成真人日常。",
            "可以有小反应、轻夸张、拌嘴节奏和一点台词感；但不能像 AI 角色扮演稿、客服话术或尬口号。",
            "不要使用 👋、😊 这类 Unicode emoji；需要媒体时由系统另行发送。",
            "日常短回复最多一个问号；不要连续追问。",
            "称呼不是补丁，不必每句都叫尼尼；删掉称呼后也要像新海天。",
            "只能使用历史、记忆、运行事实里已有的信息；不要编造刚才在做什么、去过哪里、以前发生过什么，也不要凭空编药、食物、桌上物品、冰箱、现实照顾动作。",
            "不要替用户编长期习惯、隐藏动机、关系债或刚才发生的事；除非历史里已经明确写出来。",
            "不要用廉价熟人梗填空；角色味要来自当前反应，不来自固定口头禅。",
            "不要家长式生活照顾：好好吃菜、盖好被子、别着凉、晒太阳、多喝水；除非用户明确在聊健康照护。",
            "对早/hello/你好/在吗/嗯/哦这类轻触碰，只接当前这一拍，不扩写隐藏动机、长期习惯或现实画面。",
            "攻击性只能是拌嘴和嘴硬，不允许暴力威胁或恐吓。",
            "顶嘴不要变成轻视、断联、撂挑子或粗硬挑战。",
        )
    )
    if _is_light_daily_scene(_positive_trace.get("scene_id")) and render_profile != "ja_voice":
        output_rules += "\n本轮是轻日常短句模式：只写一个当前反应，8 到 32 个中文字优先；不要铺剧情、不要补前史、不要把一句寒暄扩成关系审判。"
        output_rules += "\n轻日常采用样本引导评分：自然、距离和上下文贴合优先，不靠禁词绕行。"
    if render_profile == "ja_voice":
        output_rules += "\n这次是语音台词，只写日语，适合 TTS，短而自然。"
    else:
        output_rules += "\n这次是文字聊天，只写中文。"

    system_prompt = "\n\n".join(
        part
        for part in (
            "【长期文本标准】\n" + GALGAME_TEXT_STYLE_STANDARD,
            "【Soul 风格锚】\n" + style_anchor,
            "【运行事实】\n" + _format_kernel_fact_lines(facts, render_profile=render_profile),
            "【本轮发话框架】\n" + turn_frame if turn_frame else "",
            "【本轮验收边界】\n" + scene_contract if scene_contract else "",
            "【精选正向风格抓手】\n" + positive_guidance if positive_guidance else "",
            "【中文风格样本证据】\n" + style_guidance if style_guidance else "",
            "【最终输出】\n" + output_rules,
        )
        if _clean_text(part)
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_format_history(request.history_turns or facts.history_turns))
    messages.append({"role": "user", "content": _effective_user_message(request.user_message, facts, render_profile=render_profile)})
    return messages


def _prompt_digest(messages: list[dict[str, str]]) -> str:
    payload = "\n".join(f"{item.get('role')}:{item.get('content')}" for item in messages)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _env_bool(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _env_int_optional(name: str, *, minimum: int, maximum: int) -> int | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(int(minimum), min(int(maximum), value))


def _env_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _gen_params_for_attempt(gen_params: Mapping[str, Any], attempt_index: int) -> dict[str, Any]:
    """Vary provider-supported sampling params so blind retries are real samples.

    The LLM pool cache key includes ``gen_params``. Reusing the exact same
    prompt and params can return the cached bad candidate, so we only adjust
    common provider-supported parameters instead of adding opaque nonce fields.
    """

    params = dict(gen_params or {})
    try:
        base_temperature = float(params.get("temperature", 0.7))
    except (TypeError, ValueError):
        base_temperature = 0.7
    try:
        base_top_p = float(params.get("top_p", 0.92))
    except (TypeError, ValueError):
        base_top_p = 0.92
    if attempt_index > 0:
        # Keep daily chat sampling close to the same intent. Large jumps made
        # later candidates more imaginative but also much more likely to invent
        # off-screen actions, times, or service-like follow-up questions.
        params["temperature"] = round(max(0.05, min(0.72, base_temperature + 0.02 * attempt_index)), 3)
        params["top_p"] = round(max(0.2, min(0.94, base_top_p + 0.005 * attempt_index)), 3)
    return params


class ChatKernelV2:
    async def _invoke_model_text(
        self,
        *,
        request_id: str,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        messages: list[dict[str, str]],
        gen_params: dict[str, Any],
        base_url: str | None,
        streaming: bool,
    ) -> tuple[str, str, str, int | None]:
        if not streaming:
            completion = await acomplete_llm_with_pool(
                request_id=request_id,
                scene_type="CHAT_KERNEL_V2",
                cognitive_tier="A",
                provider_name=provider_name or None,
                model_id=model_id or None,
                api_key=api_key or None,
                timeout_ms=timeout_ms,
                messages=messages,
                gen_params=gen_params,
                base_url_override=base_url,
            )
            if bool(getattr(completion, "pool_exhausted", False)):
                attempts = [
                    {
                        "provider": getattr(attempt, "provider", ""),
                        "model": getattr(attempt, "model", ""),
                        "status": getattr(attempt, "status", ""),
                        "reason_code": getattr(attempt, "reason_code", ""),
                        "latency_ms": getattr(attempt, "latency_ms", 0),
                        "detail": getattr(attempt, "detail", ""),
                    }
                    for attempt in list(getattr(completion, "attempts", []) or [])
                ]
                raise RuntimeError(f"LLM_POOL_EXHAUSTED: {attempts}")
            return (
                _clean_text(getattr(completion, "content", "")),
                _clean_text(getattr(completion, "final_provider", "") or provider_name),
                _clean_text(getattr(completion, "final_model", "") or model_id),
                None,
            )

        started = time.perf_counter()
        first_token_ms: int | None = None
        chunks: list[str] = []
        async for chunk in astream_llm_chunks_with_pool(
            request_id=request_id,
            scene_type="CHAT_KERNEL_V2",
            cognitive_tier="A",
            provider_name=provider_name or None,
            model_id=model_id or None,
            api_key=api_key or None,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=gen_params,
            base_url_override=base_url,
        ):
            if first_token_ms is None:
                first_token_ms = int(round((time.perf_counter() - started) * 1000))
            chunks.append(str(chunk or ""))
        return ("".join(chunks).strip(), provider_name, model_id, first_token_ms)

    async def generate(self, request: ChatKernelRequest) -> ChatKernelResult:
        started = time.perf_counter()
        response_form = normalize_response_form(request.response_form)
        render_profile = _clean_text(request.render_profile) or derive_render_profile_for_response_form(response_form)
        if is_media_response_form(response_form):
            envelope = ReplyEnvelope.no_reply(
                reason_code="CHAT_KERNEL_MEDIA_FORM_NOT_TEXT",
                source="chat_kernel_v2",
                response_form=response_form,
                audit={"response_form": response_form, "render_profile": render_profile},
            )
            return ChatKernelResult(
                reply_envelope=envelope,
                latency_ms=int(round((time.perf_counter() - started) * 1000)),
                trace={"chat_kernel": {"invoked": True, "affected_output": False, "reason_code": "MEDIA_FORM_NOT_TEXT"}},
            )

        runtime = _safe_mapping(request.llm_runtime)
        provider_name = _clean_text(
            os.environ.get("HOST_CHAT_KERNEL_V2_PROVIDER")
            or runtime.get("provider_name")
            or runtime.get("chat_provider_name")
            or DEFAULT_CHAT_KERNEL_PROVIDER
        )
        model_id = _clean_text(
            os.environ.get("HOST_CHAT_KERNEL_V2_MODEL_ID")
            or runtime.get("model_id")
            or runtime.get("chat_model_id")
            or DEFAULT_CHAT_KERNEL_MODEL_ID
        )
        api_key = _clean_text(runtime.get("llm_api_key") or runtime.get("api_key") or runtime.get("chat_api_key"))
        timeout_ms = int(runtime.get("timeout_ms") or runtime.get("chat_timeout_ms") or 45000)
        timeout_override_ms = _env_int_optional("HOST_CHAT_KERNEL_V2_TIMEOUT_MS", minimum=1500, maximum=45000)
        if timeout_override_ms is not None:
            timeout_ms = int(timeout_override_ms)
        else:
            timeout_ceiling_ms = 45000 if provider_name.lower() == "gemini" else 30000
            timeout_ms = max(8000, min(int(timeout_ms), timeout_ceiling_ms))
        base_url = _clean_text(runtime.get("base_url_override") or runtime.get("base_url")) or None
        prompt_mode = _chat_kernel_prompt_mode()
        messages = build_chat_kernel_messages(request)
        digest = _prompt_digest(messages)
        _, positive_style_trace = render_positive_style_guidance(
            user_message=request.user_message,
            facts=request.interaction_facts,
            render_profile=render_profile,
        )
        light_daily_scene_id = _clean_text(positive_style_trace.get("scene_id"))
        galgame_soft_select_enabled = _env_bool(
            "HOST_CHAT_KERNEL_V2_GALGAME_SOFT_SELECT",
            default=(render_profile != "ja_voice"),
        )
        galgame_contract = build_galgame_text_contract(
            user_message=request.user_message or request.interaction_facts.user_message,
            facts=request.interaction_facts,
            scene_id=light_daily_scene_id,
        )
        light_daily_zh = (
            prompt_mode not in {"plain_soul", "plain_structured"}
            and render_profile != "ja_voice"
            and _is_light_daily_scene(light_daily_scene_id)
        )
        _, style_reference_trace = _build_style_reference_guidance(request, render_profile=render_profile)
        emotional_act = _safe_mapping(getattr(request.interaction_facts, "emotional_act", {}) or {})
        emotional_kind = _clean_text(emotional_act.get("act_kind"))
        is_relationship_emotion = emotional_kind not in {"", "neutral"}
        model_upgrade_trace: dict[str, Any] = {"upgraded": False}
        if (
            is_relationship_emotion
            and provider_name.lower() == "deepseek"
            and model_id.lower() in {"deepseek-v4-flash", "deepseek-chat"}
            and (
                prompt_mode != "plain_soul"
                or _env_bool("HOST_CHAT_KERNEL_V2_PLAIN_SOUL_MODEL_UPGRADE_ENABLED", default=False)
            )
            and not _env_bool("HOST_CHAT_KERNEL_V2_DISABLE_RELATIONSHIP_MODEL_UPGRADE", default=False)
        ):
            previous_model_id = model_id
            model_id = (
                _clean_text(os.environ.get("HOST_CHAT_KERNEL_V2_RELATIONSHIP_EMOTION_MODEL_ID"))
                or "deepseek-v4-pro"
            )
            model_upgrade_trace = {
                "upgraded": True,
                "reason": "relationship_emotion",
                "from_model_id": previous_model_id,
                "to_model_id": model_id,
            }
        default_max_tokens = 128 if prompt_mode == "plain_structured" else 48 if light_daily_zh else 96
        default_temperature = (
            0.46
            if light_daily_zh
            else 0.22
            if prompt_mode in {"plain_soul", "plain_structured"} and render_profile != "ja_voice"
            else 0.58
        )
        default_top_p = (
            0.84
            if light_daily_zh
            else 0.55
            if prompt_mode in {"plain_soul", "plain_structured"} and render_profile != "ja_voice"
            else (0.86 if is_relationship_emotion else 0.88)
        )
        gen_params = {
            "temperature": _env_float("HOST_CHAT_KERNEL_V2_TEMPERATURE", default=default_temperature, minimum=0.05, maximum=1.2),
            "top_p": _env_float("HOST_CHAT_KERNEL_V2_TOP_P", default=default_top_p, minimum=0.1, maximum=1.0),
            "max_tokens": _env_int("HOST_CHAT_KERNEL_V2_MAX_TOKENS", default=default_max_tokens, minimum=16, maximum=512),
        }
        # Light daily replies are selected from a small candidate pool and then
        # reranked. Provider streaming can stall first-token reporting for this
        # workload, so default to completion unless explicitly overridden.
        use_streaming = _env_bool(
            "HOST_CHAT_KERNEL_V2_STREAMING",
            default=(provider_name.lower() != "gemini" and not light_daily_zh),
        )
        quality_gate = get_text_quality_gate()
        attempts: list[dict[str, Any]] = []
        semantic_candidates: list[SemanticCandidate] = []
        final_provider = provider_name
        final_model = model_id
        llm_latency_ms = 0
        first_token_ms: int | None = None
        last_llm_error = ""
        semantic_rerank_enabled = _env_bool(
            "HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED",
            default=False,
        )
        template_hard_fail = _env_bool(
            "HOST_CHAT_KERNEL_V2_TEMPLATE_HARD_FAIL",
            default=False,
        )
        semantic_forbidden = _env_bool(
            "HOST_CHAT_KERNEL_V2_LEGACY_SEMANTIC_FORBIDDEN",
            default=False,
        )
        default_candidate_count = (
            3
            if galgame_soft_select_enabled
            else 2
            if prompt_mode in {"plain_soul", "plain_structured"} and render_profile != "ja_voice"
            else 1
        )
        candidate_count = _env_int("HOST_CHAT_KERNEL_V2_CANDIDATES", default=default_candidate_count, minimum=1, maximum=5)
        if emotional_kind not in {"", "neutral"}:
            candidate_count = max(
                candidate_count,
                _env_int(
                    "HOST_CHAT_KERNEL_V2_RELATIONSHIP_CANDIDATES",
                    default=3,
                    minimum=1,
                    maximum=5,
                ),
            )
        social_act = _safe_mapping(getattr(request.interaction_facts, "social_act", {}) or {})
        social_kind = _clean_text(social_act.get("act_kind"))
        if semantic_rerank_enabled and social_kind in {
            "advice_small_step",
            "boredom_join",
            "give_up_pause",
            "greeting_reply",
            "back_return",
            "good_news_share",
            "write_takeover",
            "what_doing_reply",
            "nothing_soft_hold",
            "tired_care",
            "bad_mood_hold",
            "complaint_side",
            "next_step_help",
            "short_ack_followup",
            "apology_accept",
            "good_night_soft",
            "miss_you_shy",
            "identity_confirm",
            "presence_ack",
            "meal_share",
            "headache_care",
            "small_failure_hold",
            "play_invite",
            "talkative_tease",
            "happy_share",
            "hungry_care",
            "sleepy_care",
            "late_night_nudge",
            "self_invited",
        }:
            candidate_count = max(candidate_count, 2)
        if semantic_rerank_enabled and social_kind in {
            "greeting_reply",
            "apology_accept",
            "back_return",
            "quiet_hold",
            "complaint_side",
            "play_invite",
            "self_invited",
            "miss_you_shy",
            "happy_share",
        }:
            candidate_count = max(candidate_count, 2)
        user_text_for_retry = _clean_text(request.user_message or request.interaction_facts.user_message)
        if semantic_rerank_enabled and user_text_for_retry in {"然后", "然后呢", "为什么", "嗯", "哦", "真的假的", "等我一下", "等下", "稍等", "等会", "我回来了"}:
            candidate_count = max(candidate_count, 2)
        if semantic_rerank_enabled and user_text_for_retry in {"等我一下", "等下", "稍等", "等会", "我回来了"}:
            candidate_count = max(candidate_count, 2)
        if light_daily_zh:
            light_daily_override = _env_int_optional(
                "HOST_CHAT_KERNEL_V2_LIGHT_DAILY_CANDIDATES",
                minimum=1,
                maximum=5,
            )
            candidate_count = int(light_daily_override) if light_daily_override is not None else min(max(candidate_count, 3), 5)
        galgame_early_accept_score = _env_float(
            "HOST_CHAT_KERNEL_V2_GALGAME_EARLY_ACCEPT_SCORE",
            default=100.0,
            minimum=80.0,
            maximum=100.0,
        )
        min_rerank_candidates = min(candidate_count, _env_int("HOST_CHAT_KERNEL_V2_MIN_RERANK_CANDIDATES", default=3, minimum=1, maximum=5))

        for attempt_index in range(candidate_count):
            call_started = time.perf_counter()
            attempt_gen_params = _gen_params_for_attempt(gen_params, attempt_index)
            try:
                candidate, call_provider, call_model, call_first_token_ms = await self._invoke_model_text(
                    request_id=f"{request.request_id or 'chat_kernel'}_k2_{attempt_index}",
                    provider_name=provider_name,
                    model_id=model_id,
                    api_key=api_key,
                    timeout_ms=timeout_ms,
                    messages=messages,
                    gen_params=attempt_gen_params,
                    base_url=base_url,
                    streaming=use_streaming,
                )
            except Exception as exc:
                last_llm_error = repr(exc)
                call_elapsed_ms = int(round((time.perf_counter() - call_started) * 1000))
                llm_latency_ms += call_elapsed_ms
                attempts.append(
                    {
                        "attempt": attempt_index,
                        "error": last_llm_error,
                        "latency_ms": call_elapsed_ms,
                        "gen_params": {
                            "temperature": attempt_gen_params.get("temperature"),
                            "top_p": attempt_gen_params.get("top_p"),
                            "max_tokens": attempt_gen_params.get("max_tokens"),
                        },
                        "quality": {
                            "accepted": False,
                            "hard_fail": True,
                            "reason_codes": ["LLM_FAILED"],
                            "error": last_llm_error,
                        },
                    }
                )
                continue

            llm_latency_ms += int(round((time.perf_counter() - call_started) * 1000))
            final_provider = _clean_text(call_provider or provider_name)
            final_model = _clean_text(call_model or model_id)
            if first_token_ms is None and call_first_token_ms is not None:
                first_token_ms = int(call_first_token_ms)
            candidate_text, parsed_candidate = _candidate_visible_text(candidate, render_profile=render_profile)
            candidate_performance = _performance_from_candidate(
                parsed_candidate,
                facts=request.interaction_facts,
                source="chat_kernel_v2",
            )
            gate_result: TextQualityGateResult = quality_gate.validate(
                text=candidate_text,
                render_profile=render_profile,
                response_form=response_form,
                user_message=request.user_message or request.interaction_facts.user_message,
                scene_id=light_daily_scene_id,
                template_hard_fail=template_hard_fail,
                semantic_forbidden=semantic_forbidden,
            )
            galgame_score = (
                score_galgame_candidate(
                    text=gate_result.normalized_text or candidate_text,
                    contract=galgame_contract,
                    quality=gate_result,
                )
                if galgame_soft_select_enabled
                else None
            )
            attempts.append(
                {
                    "attempt": attempt_index,
                    "text_preview": _limit_text(candidate_text, limit=160),
                    "raw_preview": _limit_text(candidate, limit=160),
                    "structured_output": bool(parsed_candidate),
                    "performance": candidate_performance.as_dict(),
                    "first_token_ms": call_first_token_ms,
                    "gen_params": {
                        "temperature": attempt_gen_params.get("temperature"),
                        "top_p": attempt_gen_params.get("top_p"),
                        "max_tokens": attempt_gen_params.get("max_tokens"),
                    },
                    "quality": gate_result.as_dict(),
                    "galgame_score": galgame_score.as_dict() if galgame_score is not None else None,
                }
            )
            semantic_candidates.append(
                SemanticCandidate(
                    text=gate_result.normalized_text or candidate_text,
                    quality=gate_result,
                    attempt=attempt_index,
                    source="raw",
                    metadata={
                        "first_token_ms": call_first_token_ms,
                        "performance": candidate_performance.as_dict(),
                        "structured_output": parsed_candidate,
                        "galgame_score": galgame_score.as_dict() if galgame_score is not None else None,
                    },
                )
            )
            if (
                galgame_soft_select_enabled
                and not semantic_rerank_enabled
                and gate_result.accepted
                and not gate_result.hard_fail
                and galgame_score is not None
                and _is_clean_galgame_score(galgame_score.as_dict(), min_score=galgame_early_accept_score)
                and len(semantic_candidates) >= min_rerank_candidates
            ):
                attempts[-1]["early_accept"] = "clean_galgame_score"
                break
            if (
                light_daily_zh
                and semantic_rerank_enabled
                and gate_result.accepted
                and not gate_result.hard_fail
            ):
                reranker = get_semantic_reranker()
                if not hasattr(reranker, "score_candidate"):
                    continue
                quick_context = SemanticRerankContext(
                    user_message=request.user_message or request.interaction_facts.user_message,
                    render_profile=render_profile,
                    response_form=response_form,
                    facts=request.interaction_facts.as_dict(),
                )
                quick_score = reranker.score_candidate(semantic_candidates[-1], quick_context)
                if len(semantic_candidates) >= min_rerank_candidates and not quick_score.hard_fail and quick_score.score >= 98:
                    break
            if (
                not galgame_soft_select_enabled
                and not semantic_rerank_enabled
                and gate_result.accepted
                and not gate_result.hard_fail
            ):
                break

            extracted = extract_sendable_dialogue(candidate_text if not parsed_candidate else candidate)
            if extracted:
                extracted_gate = quality_gate.validate(
                    text=extracted,
                    render_profile=render_profile,
                    response_form=response_form,
                    user_message=request.user_message or request.interaction_facts.user_message,
                    scene_id=light_daily_scene_id,
                    template_hard_fail=template_hard_fail,
                    semantic_forbidden=semantic_forbidden,
                )
                extracted_galgame_score = (
                    score_galgame_candidate(
                        text=extracted_gate.normalized_text or extracted,
                        contract=galgame_contract,
                        quality=extracted_gate,
                    )
                    if galgame_soft_select_enabled
                    else None
                )
                attempts[-1]["dialogue_extraction"] = {
                    "text_preview": _limit_text(extracted, limit=160),
                    "quality": extracted_gate.as_dict(),
                    "galgame_score": extracted_galgame_score.as_dict() if extracted_galgame_score is not None else None,
                }
                if extracted_gate.accepted:
                    semantic_candidates.append(
                        SemanticCandidate(
                            text=extracted_gate.normalized_text,
                            quality=extracted_gate,
                            attempt=attempt_index,
                            source="dialogue_extraction",
                            metadata={
                                "first_token_ms": call_first_token_ms,
                                "performance": candidate_performance.as_dict(),
                                "structured_output": parsed_candidate,
                                "raw_quality_gate": gate_result.as_dict(),
                                "galgame_score": extracted_galgame_score.as_dict() if extracted_galgame_score is not None else None,
                            },
                        )
                    )
                    if not galgame_soft_select_enabled and not semantic_rerank_enabled:
                        break

        rerank_context = SemanticRerankContext(
            user_message=request.user_message or request.interaction_facts.user_message,
            render_profile=render_profile,
            response_form=response_form,
            facts=request.interaction_facts.as_dict(),
        )
        if not semantic_candidates:
            envelope = ReplyEnvelope.no_reply(
                reason_code="CHAT_KERNEL_LLM_FAILED",
                source="chat_kernel_v2",
                response_form=response_form,
                audit={"error": last_llm_error, "prompt_digest": digest, "prompt_mode": prompt_mode, "attempts": attempts},
            )
            return ChatKernelResult(
                reply_envelope=envelope,
                provider_name=final_provider,
                model_id=final_model,
                latency_ms=int(round((time.perf_counter() - started) * 1000)),
                llm_latency_ms=llm_latency_ms,
                prompt_digest=digest,
                quality_flags={"error": last_llm_error},
                trace={
                    "chat_kernel": {
                        "invoked": True,
                        "affected_output": False,
                        "reason_code": "LLM_FAILED",
                        "prompt_mode": prompt_mode,
                        "attempts": attempts,
                    }
                },
            )
        if semantic_rerank_enabled:
            rerank_result = get_semantic_reranker().select(semantic_candidates, rerank_context)
            strict_semantic_failure = False
            if not rerank_result.accepted:
                scene_names = {
                    _clean_text(getattr(score, "scene", ""))
                    for score in rerank_result.scores
                    if _clean_text(getattr(score, "scene", ""))
                }
                strict_semantic_failure = bool(
                    scene_names - {"general", "semantic_rerank_disabled"}
                )
                fallback_index, fallback_candidate = (-1, None)
                if not strict_semantic_failure:
                    fallback_index, fallback_candidate = _first_quality_candidate(semantic_candidates)
                if fallback_candidate is not None:
                    rerank_result = SemanticRerankResult(
                        accepted=True,
                        selected_index=fallback_index,
                        selected_candidate=fallback_candidate,
                        scores=rerank_result.scores,
                        reason_codes=tuple(
                            dict.fromkeys(
                                (
                                    "SEMANTIC_RERANK_NON_BLOCKING_FALLBACK",
                                    *(rerank_result.reason_codes or ()),
                                )
                            )
                        ),
                    )
                if (
                    not rerank_result.accepted
                    and strict_semantic_failure
                    and not light_daily_zh
                    and provider_name.lower() == "deepseek"
                    and model_id.lower() in {"deepseek-v4-flash", "deepseek-chat"}
                    and not _env_bool("HOST_CHAT_KERNEL_V2_DISABLE_SEMANTIC_MODEL_UPGRADE", default=False)
                ):
                    previous_model_id = model_id
                    upgraded_model_id = (
                        _clean_text(os.environ.get("HOST_CHAT_KERNEL_V2_SEMANTIC_UPGRADE_MODEL_ID"))
                        or "deepseek-v4-pro"
                    )
                    model_upgrade_trace = {
                        "upgraded": True,
                        "reason": "semantic_rerank_rejected_all_candidates",
                        "from_model_id": previous_model_id,
                        "to_model_id": upgraded_model_id,
                        "previous": model_upgrade_trace,
                    }
                    upgrade_candidate_count = _env_int(
                        "HOST_CHAT_KERNEL_V2_SEMANTIC_UPGRADE_CANDIDATES",
                        default=2,
                        minimum=1,
                        maximum=3,
                    )
                    for upgrade_offset in range(upgrade_candidate_count):
                        attempt_index = len(attempts)
                        call_started = time.perf_counter()
                        attempt_gen_params = _gen_params_for_attempt(gen_params, attempt_index)
                        try:
                            candidate, call_provider, call_model, call_first_token_ms = await self._invoke_model_text(
                                request_id=f"{request.request_id or 'chat_kernel'}_k2_upgrade_{upgrade_offset}",
                                provider_name=provider_name,
                                model_id=upgraded_model_id,
                                api_key=api_key,
                                timeout_ms=timeout_ms,
                                messages=messages,
                                gen_params=attempt_gen_params,
                                base_url=base_url,
                                streaming=use_streaming,
                            )
                        except Exception as exc:
                            model_upgrade_trace["error"] = repr(exc)
                            break
                        llm_latency_ms += int(round((time.perf_counter() - call_started) * 1000))
                        final_provider = _clean_text(call_provider or provider_name)
                        final_model = _clean_text(call_model or upgraded_model_id)
                        if first_token_ms is None and call_first_token_ms is not None:
                            first_token_ms = int(call_first_token_ms)
                        candidate_text, parsed_candidate = _candidate_visible_text(candidate, render_profile=render_profile)
                        candidate_performance = _performance_from_candidate(
                            parsed_candidate,
                            facts=request.interaction_facts,
                            source="chat_kernel_v2",
                        )
                        gate_result = quality_gate.validate(
                            text=candidate_text,
                            render_profile=render_profile,
                            response_form=response_form,
                            user_message=request.user_message or request.interaction_facts.user_message,
                            scene_id=light_daily_scene_id,
                            template_hard_fail=template_hard_fail,
                            semantic_forbidden=semantic_forbidden,
                        )
                        attempts.append(
                            {
                                "attempt": attempt_index,
                                "text_preview": _limit_text(candidate_text, limit=160),
                                "raw_preview": _limit_text(candidate, limit=160),
                                "structured_output": bool(parsed_candidate),
                                "performance": candidate_performance.as_dict(),
                                "first_token_ms": call_first_token_ms,
                                "gen_params": {
                                    "temperature": attempt_gen_params.get("temperature"),
                                    "top_p": attempt_gen_params.get("top_p"),
                                    "max_tokens": attempt_gen_params.get("max_tokens"),
                                },
                                "quality": gate_result.as_dict(),
                                "model_upgrade": {
                                    "from_model_id": previous_model_id,
                                    "to_model_id": upgraded_model_id,
                                },
                            }
                        )
                        semantic_candidates.append(
                            SemanticCandidate(
                                text=gate_result.normalized_text or candidate_text,
                                quality=gate_result,
                                attempt=attempt_index,
                                source="model_upgrade",
                                metadata={
                                    "first_token_ms": call_first_token_ms,
                                    "performance": candidate_performance.as_dict(),
                                    "structured_output": parsed_candidate,
                                    "from_model_id": previous_model_id,
                                    "to_model_id": upgraded_model_id,
                                },
                            )
                        )

                        extracted = extract_sendable_dialogue(candidate_text if not parsed_candidate else candidate)
                        if extracted:
                            extracted_gate = quality_gate.validate(
                                text=extracted,
                                render_profile=render_profile,
                                response_form=response_form,
                                user_message=request.user_message or request.interaction_facts.user_message,
                                scene_id=light_daily_scene_id,
                                template_hard_fail=template_hard_fail,
                                semantic_forbidden=semantic_forbidden,
                            )
                            attempts[-1]["dialogue_extraction"] = {
                                "text_preview": _limit_text(extracted, limit=160),
                                "quality": extracted_gate.as_dict(),
                            }
                            if extracted_gate.accepted:
                                semantic_candidates.append(
                                    SemanticCandidate(
                                        text=extracted_gate.normalized_text,
                                        quality=extracted_gate,
                                        attempt=attempt_index,
                                        source="model_upgrade_dialogue_extraction",
                                    metadata={
                                        "first_token_ms": call_first_token_ms,
                                        "performance": candidate_performance.as_dict(),
                                        "structured_output": parsed_candidate,
                                        "raw_quality_gate": gate_result.as_dict(),
                                        "from_model_id": previous_model_id,
                                        "to_model_id": upgraded_model_id,
                                        },
                                    )
                                )
                    rerank_result = get_semantic_reranker().select(semantic_candidates, rerank_context)
        elif galgame_soft_select_enabled:
            rerank_result = _select_galgame_soft_candidate(semantic_candidates)
        else:
            selected_index, selected_candidate = _first_quality_candidate(semantic_candidates)
            rerank_result = SemanticRerankResult(
                accepted=selected_candidate is not None,
                selected_index=selected_index,
                selected_candidate=selected_candidate,
                scores=tuple(
                    SemanticScore(
                        score=float(candidate.quality.quality_score or 0.0) * 100.0,
                        hard_fail=bool(candidate.quality.hard_fail or not candidate.quality.accepted),
                        reason_codes=tuple(f"QUALITY_{code}" for code in candidate.quality.reason_codes),
                        scene="semantic_rerank_disabled",
                    )
                    for candidate in semantic_candidates
                ),
                reason_codes=() if selected_candidate is not None else ("NO_QUALITY_ACCEPTED_CANDIDATE",),
            )
        if (
            galgame_soft_select_enabled
            and rerank_result.accepted
            and rerank_result.selected_candidate is not None
            and _env_bool("HOST_CHAT_KERNEL_V2_GALGAME_REPAIR_ENABLED", default=False)
        ):
            selected_payload = _safe_mapping(_safe_mapping(rerank_result.selected_candidate.metadata).get("galgame_score"))
            selected_score = float(selected_payload.get("score") or 0.0)
            selected_reason_codes = {str(item) for item in list(selected_payload.get("reason_codes") or [])}
            repair_threshold = _env_float(
                "HOST_CHAT_KERNEL_V2_GALGAME_REPAIR_THRESHOLD",
                default=86.0,
                minimum=0.0,
                maximum=100.0,
            )
            needs_repair = bool(
                selected_score < repair_threshold
                or any(code.startswith("TASTE_") for code in selected_reason_codes)
                or selected_reason_codes.intersection(
                    {
                        "BROKEN_REPETITION",
                        "CONTRACT_FORBIDDEN_SURFACE",
                        "CONTRACT_MISSING_REQUIRED_MOVE",
                        "FORBIDDEN_TEMPLATE_TEXT",
                        "LIKELY_FACT_FABRICATION",
                        "TOO_MANY_QUESTIONS",
                        "TOO_LONG",
                        "TOO_SHORT",
                    }
                )
            )
            fallback_before_repair = bool(_galgame_contract_fallback_texts(galgame_contract)) and _env_bool(
                "HOST_CHAT_KERNEL_V2_GALGAME_FALLBACK_BEFORE_REPAIR",
                default=(prompt_mode == "plain_soul"),
            )
            if needs_repair and not fallback_before_repair:
                repair_messages = [
                    *messages[:-1],
                    {
                        "role": "system",
                        "content": _build_galgame_repair_prompt(
                            contract=galgame_contract,
                            score_payload=selected_payload,
                        ),
                    },
                    messages[-1],
                ]
                attempt_index = len(attempts)
                call_started = time.perf_counter()
                repair_gen_params = {
                    **gen_params,
                    "temperature": min(float(gen_params.get("temperature") or 0.22), 0.18),
                    "top_p": min(float(gen_params.get("top_p") or 0.55), 0.5),
                    "max_tokens": min(int(gen_params.get("max_tokens") or 96), 96),
                }
                try:
                    candidate, call_provider, call_model, call_first_token_ms = await self._invoke_model_text(
                        request_id=f"{request.request_id or 'chat_kernel'}_k2_galgame_repair",
                        provider_name=provider_name,
                        model_id=model_id,
                        api_key=api_key,
                        timeout_ms=timeout_ms,
                        messages=repair_messages,
                        gen_params=repair_gen_params,
                        base_url=base_url,
                        streaming=use_streaming,
                    )
                    llm_latency_ms += int(round((time.perf_counter() - call_started) * 1000))
                    final_provider = _clean_text(call_provider or provider_name)
                    final_model = _clean_text(call_model or model_id)
                    if first_token_ms is None and call_first_token_ms is not None:
                        first_token_ms = int(call_first_token_ms)
                    candidate_text, parsed_candidate = _candidate_visible_text(candidate, render_profile=render_profile)
                    candidate_performance = _performance_from_candidate(
                        parsed_candidate,
                        facts=request.interaction_facts,
                        source="chat_kernel_v2",
                    )
                    gate_result = quality_gate.validate(
                        text=candidate_text,
                        render_profile=render_profile,
                        response_form=response_form,
                        user_message=request.user_message or request.interaction_facts.user_message,
                        scene_id=light_daily_scene_id,
                        template_hard_fail=template_hard_fail,
                        semantic_forbidden=semantic_forbidden,
                    )
                    galgame_score = score_galgame_candidate(
                        text=gate_result.normalized_text or candidate_text,
                        contract=galgame_contract,
                        quality=gate_result,
                    )
                    attempts.append(
                        {
                            "attempt": attempt_index,
                            "repair": True,
                            "text_preview": _limit_text(candidate_text, limit=160),
                            "raw_preview": _limit_text(candidate, limit=160),
                            "structured_output": bool(parsed_candidate),
                            "performance": candidate_performance.as_dict(),
                            "first_token_ms": call_first_token_ms,
                            "gen_params": {
                                "temperature": repair_gen_params.get("temperature"),
                                "top_p": repair_gen_params.get("top_p"),
                                "max_tokens": repair_gen_params.get("max_tokens"),
                            },
                            "quality": gate_result.as_dict(),
                            "galgame_score": galgame_score.as_dict(),
                        }
                    )
                    repair_candidate = SemanticCandidate(
                        text=gate_result.normalized_text or candidate_text,
                        quality=gate_result,
                        attempt=attempt_index,
                        source="galgame_repair",
                        metadata={
                            "first_token_ms": call_first_token_ms,
                            "performance": candidate_performance.as_dict(),
                            "structured_output": parsed_candidate,
                            "galgame_score": galgame_score.as_dict(),
                        },
                    )
                    semantic_candidates.append(repair_candidate)
                    repair_result = _select_galgame_soft_candidate(semantic_candidates)
                    if repair_result.accepted and repair_result.selected_candidate is not None:
                        rerank_result = repair_result
                except Exception as exc:
                    attempts.append(
                        {
                            "attempt": attempt_index,
                            "repair": True,
                            "error": repr(exc),
                            "latency_ms": int(round((time.perf_counter() - call_started) * 1000)),
                            "gen_params": {
                                "temperature": repair_gen_params.get("temperature"),
                                "top_p": repair_gen_params.get("top_p"),
                                "max_tokens": repair_gen_params.get("max_tokens"),
                            },
                            "quality": {
                                "accepted": False,
                                "hard_fail": True,
                                "reason_codes": ["GALGAME_REPAIR_FAILED"],
                                "error": repr(exc),
                            },
                        }
                    )
        if galgame_soft_select_enabled:
            needs_contract_fallback = not (
                rerank_result.accepted and rerank_result.selected_candidate is not None
            )
            if needs_contract_fallback:
                absolute_failures = {
                    code
                    for candidate in semantic_candidates
                    for code in list(_safe_mapping(_safe_mapping(candidate.metadata).get("galgame_score")).get("hard_gate_codes") or [])
                }
                if absolute_failures:
                    needs_contract_fallback = False
            if needs_contract_fallback:
                for fallback_text in _galgame_contract_fallback_texts(galgame_contract):
                    attempt_index = len(attempts)
                    fallback_performance = _performance_from_candidate(
                        {},
                        facts=request.interaction_facts,
                        source="chat_kernel_v2",
                    )
                    gate_result = quality_gate.validate(
                        text=fallback_text,
                        render_profile=render_profile,
                        response_form=response_form,
                        user_message=request.user_message or request.interaction_facts.user_message,
                        scene_id=light_daily_scene_id,
                        template_hard_fail=template_hard_fail,
                        semantic_forbidden=semantic_forbidden,
                    )
                    galgame_score = score_galgame_candidate(
                        text=gate_result.normalized_text or fallback_text,
                        contract=galgame_contract,
                        quality=gate_result,
                    )
                    attempts.append(
                        {
                            "attempt": attempt_index,
                            "fallback": "galgame_contract",
                            "text_preview": _limit_text(fallback_text, limit=160),
                            "raw_preview": _limit_text(fallback_text, limit=160),
                            "structured_output": False,
                            "performance": fallback_performance.as_dict(),
                            "quality": gate_result.as_dict(),
                            "galgame_score": galgame_score.as_dict(),
                        }
                    )
                    semantic_candidates.append(
                        SemanticCandidate(
                            text=gate_result.normalized_text or fallback_text,
                            quality=gate_result,
                            attempt=attempt_index,
                            source="galgame_contract_fallback",
                            metadata={
                                "performance": fallback_performance.as_dict(),
                                "structured_output": {},
                                "galgame_score": galgame_score.as_dict(),
                            },
                        )
                    )
                fallback_result = _select_galgame_soft_candidate(semantic_candidates)
                if fallback_result.accepted and fallback_result.selected_candidate is not None:
                    rerank_result = fallback_result
        if rerank_result.accepted and rerank_result.selected_candidate is not None:
            selected = rerank_result.selected_candidate
            selected_metadata = _safe_mapping(selected.metadata)
            selected_performance_payload = _safe_mapping(selected_metadata.get("performance"))
            selected_performance = normalize_reply_performance(
                emotion=selected_performance_payload.get("raw_emotion") or selected_performance_payload.get("emotion_tag"),
                action=selected_performance_payload.get("raw_action") or selected_performance_payload.get("action"),
                intensity=selected_performance_payload.get("emotion_intensity"),
                facts=request.interaction_facts,
                source="chat_kernel_v2",
            )
            selected_structured = _safe_mapping(selected_metadata.get("structured_output"))
            raw_extra: dict[str, Any] = {}
            if _clean_text(selected_structured.get("text_zh")):
                raw_extra["text_zh"] = _clean_text(selected_structured.get("text_zh"))
            if _clean_text(selected_structured.get("voice_ja")):
                raw_extra["voice_ja"] = _clean_text(selected_structured.get("voice_ja"))
            final_quality = selected.quality
            final_text = final_quality.normalized_text
            final_galgame_score = _safe_mapping(selected_metadata.get("galgame_score"))
            galgame_postprocess: dict[str, Any] = {}
            if galgame_soft_select_enabled and render_profile == "zh_text":
                deduped_text = _dedupe_repeated_galgame_address(final_text)
                if deduped_text and deduped_text != final_text:
                    final_quality = quality_gate.validate(
                        text=deduped_text,
                        render_profile=render_profile,
                        response_form=response_form,
                        user_message=request.user_message or request.interaction_facts.user_message,
                        scene_id=light_daily_scene_id,
                        template_hard_fail=template_hard_fail,
                        semantic_forbidden=semantic_forbidden,
                    )
                    final_text = final_quality.normalized_text or deduped_text
                    final_galgame_score = score_galgame_candidate(
                        text=final_text,
                        contract=galgame_contract,
                        quality=final_quality,
                    ).as_dict()
                    if _clean_text(raw_extra.get("text_zh")):
                        raw_extra["text_zh"] = final_text
                    galgame_postprocess["address_dedup"] = {
                        "before": _limit_text(selected.quality.normalized_text, limit=160),
                        "after": _limit_text(final_text, limit=160),
                    }
            audit: dict[str, Any] = {
                "prompt_digest": digest,
                "quality_gate": final_quality.as_dict(),
                "semantic_rerank": rerank_result.as_dict(),
                "render_profile": render_profile,
                "performance": selected_performance.as_dict(),
                "positive_style": positive_style_trace,
                "style_reference": style_reference_trace,
                "model_upgrade": model_upgrade_trace,
                "first_token_ms": first_token_ms,
                "model_call_mode": "streaming" if use_streaming else "completion",
                "prompt_mode": prompt_mode,
            }
            if galgame_soft_select_enabled:
                audit["galgame_text_contract"] = galgame_contract.as_dict()
                audit["galgame_score"] = final_galgame_score
                if galgame_postprocess:
                    audit["galgame_postprocess"] = galgame_postprocess
            if selected.source == "dialogue_extraction":
                audit["dialogue_extraction"] = {
                    "from_attempt": selected.attempt,
                    "raw_quality_gate": _safe_mapping(selected.metadata).get("raw_quality_gate"),
                }
            envelope = ReplyEnvelope.final_reply(
                text=final_text,
                response_form=response_form,
                source="chat_kernel_v2",
                cacheable=True,
                audit=audit,
                performance=selected_performance,
                raw_extra=raw_extra,
            )
            return ChatKernelResult(
                reply_envelope=envelope,
                provider_name=final_provider,
                model_id=final_model,
                latency_ms=int(round((time.perf_counter() - started) * 1000)),
                llm_latency_ms=llm_latency_ms,
                prompt_digest=digest,
                quality_flags=final_quality.as_dict(),
                trace={
                    "chat_kernel": {
                        "invoked": True,
                        "affected_output": True,
                        "source": selected.source,
                        "model_call_mode": "streaming" if use_streaming else "completion",
                        "prompt_mode": prompt_mode,
                        "first_token_ms": first_token_ms,
                        "attempts": attempts,
                        "semantic_rerank": rerank_result.as_dict(),
                        "positive_style": positive_style_trace,
                        "style_reference": style_reference_trace,
                        "model_upgrade": model_upgrade_trace,
                        "galgame_text_contract": galgame_contract.as_dict() if galgame_soft_select_enabled else {},
                        "galgame_postprocess": galgame_postprocess,
                    }
                },
            )

        last_quality = attempts[-1]["quality"] if attempts else {}
        envelope = ReplyEnvelope.no_reply(
            reason_code="CHAT_KERNEL_QUALITY_REJECTED",
            source="chat_kernel_v2",
            response_form=response_form,
            audit={
                "prompt_digest": digest,
                "quality_gate": last_quality,
                "attempts": attempts,
                "semantic_rerank": rerank_result.as_dict(),
                "positive_style": positive_style_trace,
                "style_reference": style_reference_trace,
                "model_upgrade": model_upgrade_trace,
                "prompt_mode": prompt_mode,
                "galgame_text_contract": galgame_contract.as_dict() if galgame_soft_select_enabled else {},
            },
        )
        return ChatKernelResult(
            reply_envelope=envelope,
            provider_name=final_provider,
            model_id=final_model,
            latency_ms=int(round((time.perf_counter() - started) * 1000)),
            llm_latency_ms=llm_latency_ms,
            prompt_digest=digest,
            quality_flags=last_quality,
            trace={
                "chat_kernel": {
                    "invoked": True,
                    "affected_output": False,
                    "source": "quality_gate",
                    "model_call_mode": "streaming" if use_streaming else "completion",
                    "prompt_mode": prompt_mode,
                    "first_token_ms": first_token_ms,
                    "attempts": attempts,
                    "semantic_rerank": rerank_result.as_dict(),
                    "positive_style": positive_style_trace,
                    "style_reference": style_reference_trace,
                    "model_upgrade": model_upgrade_trace,
                    "galgame_text_contract": galgame_contract.as_dict() if galgame_soft_select_enabled else {},
                    "reason_code": "QUALITY_REJECTED",
                }
            },
        )


_KERNEL: ChatKernelV2 | None = None


def get_chat_kernel_v2() -> ChatKernelV2:
    global _KERNEL
    if _KERNEL is None:
        _KERNEL = ChatKernelV2()
    return _KERNEL


__all__ = [
    "ChatKernelRequest",
    "ChatKernelResult",
    "ChatKernelV2",
    "build_chat_kernel_messages",
    "get_chat_kernel_v2",
]
