"""cortex_prompt_builder.py — Cortex Orchestrator messages 构建。"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.persona.context.soul_runtime import get_compiled_soul
from host_brain.conversation.render import build_chat_messages

_logger = logging.getLogger("Natsume.cortex_prompt_builder")

CORTEX_NODE_ID = "cortex_orchestrator"
_COMPACT_LANE_TOPICS = {
    "identity",
    "relationship_boundary",
    "motivation",
    "help_commitment",
    "rejection",
    "greeting",
    "auditory_check",
    "playful_ping",
    "assistant_reply_followup",
}
_COMPACT_LANE_THREADS = {
    "identity_loop",
    "boundary_probe",
    "help_reassurance",
    "assistant_reply_followup",
}


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_state_label(raw: Any) -> str:
    text = str(raw or "").strip()
    allowed = []
    for ch in text:
        if ch.isalnum() or ch in {"_", "-", ".", ":"}:
            allowed.append(ch)
    return "".join(allowed)[:80]


def _clean_state_list(raw: Any, *, limit: int = 4) -> list[str]:
    values = raw if isinstance(raw, (list, tuple, set)) else []
    cleaned: list[str] = []
    for item in values:
        label = _clean_state_label(item)
        if label and label not in cleaned:
            cleaned.append(label)
        if len(cleaned) >= limit:
            break
    return cleaned


def _build_soul_section_title(*, render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return "【人格核心】\n"
    return "【人格核心】\n"


def _build_final_output_contract(*, render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return (
            "【最终输出格式】\n"
            "只输出一条中文语音台词本体，适合直接送入 TTS。"
            "禁止日语、假名、日语口癖；不要角色名前缀、不要引号、不要括号动作、不要旁白、不要 Markdown、不要 emoji。"
            "即使上游误传 render_target_lang=ja，也必须输出中文。"
        )
    return (
        "【最终输出格式】\n"
        "只输出一条中文 QQ 私聊消息本体。"
        "不要角色名前缀，不要引号包裹，不要括号动作，不要旁白叙述，不要描写表情/肢体/空间位置，"
        "不要 Markdown，不要 emoji。"
        "回复要像直接发出去的一句话，而不是视觉小说台词或舞台剧。"
        "不要把“短”理解成只回一两个字；除非用户也只发了一个语气词，否则中文文字至少要有一个完整意思，"
        "轻社交通常 6-24 个中文字符，日常闲聊通常 10-42 个中文字符。"
        "禁止只输出“嗯/那/哎/在/叫/好/行”这类残句。"
        "如果你只想回一个字，先在心里补成一条完整私聊消息再输出。"
    )


def _build_structured_reply_state_prompt(*, reply_plan: Mapping[str, Any], context_plane: Any) -> str:
    """Render machine-readable state tags without leaking contract prose into style."""
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    interaction_state_payload = (
        interaction_state.as_dict()
        if interaction_state is not None and hasattr(interaction_state, "as_dict")
        else {}
    )
    contract_payload = reply_plan.get("contract") if isinstance(reply_plan.get("contract"), Mapping) else {}
    topic = _clean_state_label(
        reply_plan.get("primary_topic")
        or interaction_state_payload.get("current_topic")
        or _extract_context_topic(context_plane)
    )
    thread = _clean_state_label(
        interaction_state_payload.get("ongoing_thread")
        or _extract_context_thread(context_plane)
    )
    move = _clean_state_label(
        reply_plan.get("interaction_move")
        or interaction_state_payload.get("interaction_move")
    )
    stance = _clean_state_label(interaction_state_payload.get("user_stance"))
    freshness = _clean_state_label(interaction_state_payload.get("freshness"))
    repeat_count = _clean_state_label(interaction_state_payload.get("repeat_question_count"))
    repeat_group = _clean_state_label(
        interaction_state_payload.get("repeat_surface_group")
        or interaction_state_payload.get("repeat_group")
    )
    response_form = _clean_state_label(reply_plan.get("response_form") or "text")
    required = _clean_state_list(contract_payload.get("required_actions"))
    forbidden = _clean_state_list(contract_payload.get("forbidden_actions"))
    tags = [
        f"topic={topic or 'chat'}",
        f"thread={thread or 'none'}",
        f"move={move or 'none'}",
        f"stance={stance or 'none'}",
        f"form={response_form or 'text'}",
    ]
    if freshness:
        tags.append(f"freshness={freshness}")
    if repeat_count and repeat_count != "0":
        tags.append(f"repeat_count={repeat_count}")
    if repeat_group:
        tags.append(f"repeat_group={repeat_group}")
    if required:
        tags.append("required=" + ",".join(required))
    if forbidden:
        tags.append("forbidden=" + ",".join(forbidden))
    max_chars = _clean_state_label(reply_plan.get("max_chars"))
    if max_chars:
        tags.append(f"max_chars={max_chars}")
    return (
        "【本轮状态标签】\n"
        + "; ".join(tags)
        + "\n这些是内部状态标签，不是可说出口的话；最终措辞只按 Soul 和上下文自然生成。"
    )


def _build_interaction_state_summary(context_plane: Any) -> str:
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    interaction_thread = getattr(getattr(context_plane, "interaction_thread", None), "state", None)
    if interaction_state is None:
        return ""

    interaction_state_payload = (
        interaction_state.as_dict()
        if hasattr(interaction_state, "as_dict")
        else {}
    )
    interaction_thread_payload = (
        interaction_thread.as_dict()
        if hasattr(interaction_thread, "as_dict")
        else {}
    )

    current_topic = str(
        getattr(interaction_state, "current_topic", "") or interaction_state_payload.get("current_topic") or ""
    ).strip()
    interaction_move = str(
        getattr(interaction_state, "interaction_move", "") or interaction_state_payload.get("interaction_move") or ""
    ).strip()
    user_stance = str(
        getattr(interaction_state, "user_stance", "") or interaction_state_payload.get("user_stance") or ""
    ).strip()
    followup_target = str(
        getattr(interaction_state, "followup_target", "") or interaction_state_payload.get("followup_target") or ""
    ).strip()
    followup_excerpt = str(
        getattr(interaction_state, "followup_excerpt", "") or interaction_state_payload.get("followup_excerpt") or ""
        or getattr(interaction_thread, "source_excerpt", "")
        or interaction_thread_payload.get("source_excerpt")
        or ""
    ).strip()
    followup_context_excerpt = str(
        getattr(interaction_state, "followup_context_excerpt", "")
        or interaction_state_payload.get("followup_context_excerpt")
        or getattr(interaction_thread, "source_context_excerpt", "")
        or interaction_thread_payload.get("source_context_excerpt")
        or ""
    ).strip()
    ongoing_thread = str(
        getattr(interaction_state, "ongoing_thread", "") or interaction_state_payload.get("ongoing_thread") or ""
    ).strip()
    perception_summary = str(
        getattr(interaction_state, "perception_summary", "") or interaction_state_payload.get("perception_summary") or ""
    ).strip()
    repeat_question_count = 0
    try:
        repeat_question_count = int(
            getattr(interaction_state, "repeat_question_count", None)
            if getattr(interaction_state, "repeat_question_count", None) is not None
            else interaction_state_payload.get("repeat_question_count")
            or 0
        )
    except (TypeError, ValueError):
        repeat_question_count = 0
    repeat_surface_group = str(
        getattr(interaction_state, "repeat_surface_group", "")
        or interaction_state_payload.get("repeat_surface_group")
        or interaction_state_payload.get("repeat_group")
        or ""
    ).strip()
    emotion_anchor = str(
        getattr(interaction_thread, "emotion_anchor", "") or interaction_thread_payload.get("emotion_anchor") or ""
    ).strip()

    if repeat_question_count > 0:
        group_text = f"同类输入是“{repeat_surface_group}”。" if repeat_surface_group else ""
        return (
            "【当前对话线程】\n"
            f"用户这轮是在重复刚才同类输入，已重复 {repeat_question_count} 次。{group_text}"
            "先承接“又说了一遍”的事实，再处理当前文字本身；不要把它当第一次，"
            "也不要猜用户睡迷糊、故意逗你或状态异常。"
        )

    if ongoing_thread == "image_social":
        if current_topic == "emoji_social_reply":
            return (
                "【当前对话线程】\n"
                "对方这轮发来的是社交图片/表情包，不是让你做图片鉴赏。"
                "把它当成对方递来的一点情绪来回应；如果观察没有明确情绪，就不要硬猜成委屈、可爱或逗你。"
            )
        if current_topic == "emoji_social_followup":
            if interaction_move == "repair_needed":
                return (
                    "【当前对话线程】\n"
                    "这句话是在接上一张社交图片，不是新话题。对方是在嘴硬或否认刚才那层委屈/别扭，"
                "顺着上一张图的情绪轻轻退一步，不要装作第一次听见。"
                )
            if interaction_move == "reconfirm":
                return (
                    "【当前对话线程】\n"
                    "这句话是在接上一张社交图片。对方是在追问你刚才为什么那样理解，"
                    "直接自然解释你的读法，不要退回空话。"
                )
            if interaction_move == "light_tease":
                return (
                    "【当前对话线程】\n"
                    "这句话还是在接上一张社交图片。对方是在把刚才那层情绪轻轻带过去，"
                    "顺着这股别扭/卖萌感接一句就好，不要重新分析画面。"
                )
        return (
            "【当前对话线程】\n"
            "你们还在接上一张社交图片的后话。不要把这轮当新话题，也不要重新解说图片内容。"
        )

    if ongoing_thread == "assistant_reply_followup":
        quoted = f"上一条你回的是“{followup_excerpt}”。" if followup_excerpt else ""
        context = f"它前面接的用户话是“{followup_context_excerpt}”。" if followup_context_excerpt else ""
        return (
            "【当前对话线程】\n"
            f"这轮是在追问你上一条回复的意思或指代，不是新话题。{context}{quoted}"
            "先承认刚才说得太省或不够清楚，再补清楚你上一句具体指什么；"
            "不要只回语气词，不要复读上一句，也不要转成固定开放问句。"
        )

    if ongoing_thread == "identity_loop":
        return (
            "【当前对话线程】\n"
            "这轮是在接连续身份追问。先自然认下自己，再顺着“怎么又问这个”的语气回，不要完整自报，也不要用固定追问句。"
        )

    if ongoing_thread == "boundary_probe":
        return (
            "【当前对话线程】\n"
            "这轮是在接关系/动机回访。先把义妹关系事实放稳，再换一个更生活化的短理由，不要重复上一轮原句。"
        )

    if ongoing_thread == "help_reassurance":
        return (
            "【当前对话线程】\n"
            "这轮是在接“你还会不会理我/帮我”的回访。先回应不安，再把会回应的态度放稳，不要重新许很重的承诺。"
        )

    if current_topic == "auditory_check":
        return (
            "【当前对话线程】\n"
            "这轮是在确认听觉输入。若当前没有语音输入证据，只能说没听见声音、只看到文字；"
            "不要只回在呢，也不要假装听到了声音。"
        )

    if current_topic == "playful_ping":
        return (
            "【当前对话线程】\n"
            "这轮是轻社交招呼，只要自然回应，不要上来解释或把话说重；"
            "但也不要只喊称呼、只发表情，至少给一句有内容的熟人式回应。"
        )

    if perception_summary and followup_target:
        return (
            "【当前对话线程】\n"
            f"这轮更像是在接着“{followup_target}”往下说。{perception_summary}"
        )

    if current_topic and user_stance:
        return (
            "【当前对话线程】\n"
            f"当前话题是“{current_topic}”，对方这轮更像是在{user_stance}地往下接。"
        )

    if emotion_anchor:
        return (
            "【当前对话线程】\n"
            f"这轮延续的是上一轮偏“{emotion_anchor}”的气氛，别突然换成新话题。"
        )

    return ""


def _extract_context_topic(context_plane: Any) -> str:
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    if interaction_state is None:
        return ""
    topic = str(getattr(interaction_state, "current_topic", "") or "").strip()
    if topic:
        return topic
    if hasattr(interaction_state, "as_dict"):
        return str((interaction_state.as_dict() or {}).get("current_topic") or "").strip()
    return ""


def _extract_context_thread(context_plane: Any) -> str:
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    if interaction_state is not None:
        thread_name = str(getattr(interaction_state, "ongoing_thread", "") or "").strip()
        if thread_name:
            return thread_name
        if hasattr(interaction_state, "as_dict"):
            thread_name = str((interaction_state.as_dict() or {}).get("ongoing_thread") or "").strip()
            if thread_name:
                return thread_name
    interaction_thread = getattr(getattr(context_plane, "interaction_thread", None), "state", None)
    if interaction_thread is None:
        return ""
    thread_name = str(getattr(interaction_thread, "thread_kind", "") or "").strip()
    if thread_name:
        return thread_name
    if hasattr(interaction_thread, "as_dict"):
        return str((interaction_thread.as_dict() or {}).get("thread_kind") or "").strip()
    return ""


def _should_use_compact_chat_lane(
    *,
    relational_topic: str,
    context_plane: Any,
    user_message: str,
) -> bool:
    if str(relational_topic or "").strip():
        return True
    topic = _extract_context_topic(context_plane).lower()
    thread_name = _extract_context_thread(context_plane).lower()
    if topic in _COMPACT_LANE_TOPICS or thread_name in _COMPACT_LANE_THREADS:
        return True
    return False


def build_cortex_prompt(
    *,
    user_message: str,
    render_target_lang: str = "zh",
    emotion_context: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    person_id: str = "",
    session_id: str = "",
    request_id: str = "",
    thread_id: str | None = None,
    bond_summary: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    multimodal_observations: dict[str, Any] | None = None,
    attachment_summary: str | None = None,
    eval_history_turns: list[dict[str, Any]] | None = None,
    turn_context_sections: list[str] | tuple[str, ...] | None = None,
    include_runtime_payload: bool = False,
) -> tuple[list[dict[str, str]], dict[str, Any]] | tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    """构建 Cortex Orchestrator 的原生 messages。"""
    from host_brain.persona.relational_chat import detect_relational_chat_topic  # noqa: PLC0415

    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}
    relational_topic = detect_relational_chat_topic(user_message)
    initial_compact_lane = bool(relational_topic)

    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        person_id=person_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=user_message,
        render_target_lang=render_target_lang,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED"},
        retrieval_evidence_items=retrieval_evidence_items or [],
        emotion_state=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        attachments=attachments or [],
        multimodal_observations=multimodal_observations or {},
        attachment_summary=attachment_summary,
        scene_type="CHAT",
    )
    prompt_payload = runtime_input.to_runtime_payload(
        prompt_context={
            "bond_summary": dict(bond_summary or {}),
            "request_id": request_id,
            "person_id": person_id,
            "session_id": session_id,
            "thread_id": thread_id,
        },
        prompt_context_validation={},
        prompt_context_observability={},
    )
    prompt_payload["bond_summary"] = dict(bond_summary or {})
    prompt_payload["emotion_context"] = dict(emotion_context or {})
    prompt_payload["emotion_snapshot"] = dict(emotion_snapshot or {})
    prompt_payload["multimodal_input"] = {
        "observations": dict(multimodal_observations or {}),
        "summary_for_chat": str(attachment_summary or "").strip(),
        "modalities": list(
            prompt_payload.get("request", {}).get("multimodal_observations", {}).get("observed_types") or []
        ),
        "primary_modality": str(
            prompt_payload.get("request", {}).get("multimodal_observations", {}).get("primary_modality") or ""
        ).strip(),
    }
    if eval_history_turns:
        prompt_payload["ab_eval"] = {
            "history_turns": [
                dict(item or {})
                for item in list(eval_history_turns or [])
                if isinstance(item, Mapping)
            ]
        }

    gen_params: dict[str, Any] = {}
    context_plane = assemble_prompt_context_plane(
        node_id=CORTEX_NODE_ID,
        runtime_payload=prompt_payload,
        turn_context_sections=turn_context_sections,
        history_max_turns=(6 if initial_compact_lane else None),
        allow_older_summary=(not initial_compact_lane),
    )
    use_compact_chat_lane = _should_use_compact_chat_lane(
        relational_topic=str(relational_topic or "").strip(),
        context_plane=context_plane,
        user_message=user_message,
    )
    if use_compact_chat_lane and not initial_compact_lane:
        context_plane = assemble_prompt_context_plane(
            node_id=CORTEX_NODE_ID,
            runtime_payload=prompt_payload,
            turn_context_sections=turn_context_sections,
            history_max_turns=6,
            allow_older_summary=False,
        )
    compiled_soul = get_compiled_soul()
    _soul_display_name = str(getattr(compiled_soul, "display_name", "") or "当前人格").strip()
    _reply_plan = dict(getattr(getattr(context_plane, "reply_plan", None), "state", {}) or {})
    _soul_text = str(
        compiled_soul.runtime_compact_text if use_compact_chat_lane else compiled_soul.runtime_full_text
    ).strip()

    _parts = []
    if _soul_text:
        _parts.append(
            _build_soul_section_title(render_target_lang=render_target_lang) + _soul_text
        )
    _parts.append(_build_final_output_contract(render_target_lang=render_target_lang))
    _parts.append(
        "【人格优先级】\n"
        "Soul 是说话风格、关系姿态和称呼习惯的最高优先级。情绪、关系状态和本轮目标只调节热度，"
        f"不能把{_soul_display_name}压成客服式、冷淡式、扫描式或只有称呼/表情的空壳短句。"
    )
    _affect_summary = str(_reply_plan.get("affect_summary") or "").strip()
    if _affect_summary:
        _parts.append(_affect_summary)
    _relationship_summary = str(_reply_plan.get("relationship_summary") or "").strip()
    if _relationship_summary:
        _parts.append(_relationship_summary)
    _image_social_prompt_signal = ""
    _is_image_social_prompt = False
    _multimodal_state = getattr(getattr(context_plane, "multimodal", None), "state", None)
    if hasattr(_multimodal_state, "summary_for_chat"):
        _conversation_interpretation_for_mm = getattr(getattr(context_plane, "conversation_interpretation", None), "state", None)
        _interaction_thread_for_mm = getattr(getattr(context_plane, "interaction_thread", None), "state", None)
        _mm_topic = str(getattr(_conversation_interpretation_for_mm, "literal_topic", "") or "").strip()
        _mm_thread = str(getattr(_interaction_thread_for_mm, "thread_kind", "") or "").strip()
        if _mm_topic in {"emoji_social_reply", "emoji_social_followup"} or _mm_thread == "image_social":
            _is_image_social_prompt = True
            _multimodal_summary = str(getattr(_multimodal_state, "social_signal", "") or "").strip()
            if not _multimodal_summary:
                _multimodal_summary = str(getattr(_multimodal_state, "summary_for_chat", "") or "").strip()
            _image_social_prompt_signal = _multimodal_summary
            if _multimodal_summary:
                _parts.append(
                    "【表情包输入】\n"
                    f"这张图/表情传来的意思：{_multimodal_summary}\n"
                    "最后一条用户消息代表对方正在表达这种情绪，不是让你猜图里是谁。"
                    "如果上一轮是你自己的回复，这张表情很可能是在回应你上一句；负向情绪要优先收回、修正或接住上一句，不要只给表情贴标签。"
                    "像聊天里顺手回这股情绪，不要描述图片、外貌或表情包本身。"
                    "不要编造对方此刻的位置、动作或心理，比如“房间里/一个人/装深沉/发呆/不说话”。"
                    "回复 8-24 个中文字符，不要只回“嗯/唔/哼/哦/好啦”。"
                    "严禁用“怎么啦/怎么了/咋了/什么情况/怎么不说话/别憋着/跟我说说”这类问句或劝说承接。"
                )
        else:
            _multimodal_summary = str(getattr(_multimodal_state, "summary_for_chat", "") or "").strip()
            if _multimodal_summary:
                _parts.append("【附件观察】\n" + _multimodal_summary)
    _interaction_summary = _build_interaction_state_summary(context_plane)
    if _interaction_summary:
        _parts.append(_interaction_summary)
    _disable_reply_state_prompt = _truthy_env("HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT")
    if not _disable_reply_state_prompt:
        _parts.append(_build_structured_reply_state_prompt(reply_plan=_reply_plan, context_plane=context_plane))
    system_prompt = "\n\n".join(_parts)

    conversation_history_meta = {
        "turns_injected": int(context_plane.continuity.turns_injected or 0),
        "fetched_turns": int(context_plane.continuity.fetched_turns or 0),
        "older_turn_count": int(context_plane.continuity.older_turn_count or 0),
        "summary_present": bool(context_plane.continuity.summary),
        "oldest_ts_ms": context_plane.continuity.oldest_ts_ms,
        "newest_ts_ms": context_plane.continuity.newest_ts_ms,
        "skipped_corrupted_count": int(context_plane.continuity.skipped_corrupted_count or 0),
        "skipped_empty_count": int(context_plane.continuity.skipped_empty_count or 0),
        "deduped_same_role_count": int(context_plane.continuity.deduped_same_role_count or 0),
    }
    prompt_payload = dict(prompt_payload)
    prompt_payload["conversation_history"] = conversation_history_meta
    _conversation_interpretation = getattr(getattr(context_plane, "conversation_interpretation", None), "state", None)
    if hasattr(_conversation_interpretation, "as_dict"):
        prompt_payload["conversation_interpretation"] = _conversation_interpretation.as_dict()
    _interaction_thread = getattr(getattr(context_plane, "interaction_thread", None), "state", None)
    if hasattr(_interaction_thread, "as_dict"):
        prompt_payload["interaction_thread"] = _interaction_thread.as_dict()
    _interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    if hasattr(_interaction_state, "as_dict"):
        prompt_payload["interaction_state"] = _interaction_state.as_dict()
    _topic_ledger = getattr(getattr(context_plane, "topic_ledger", None), "state", None)
    if hasattr(_topic_ledger, "as_dict"):
        prompt_payload["topic_ledger"] = _topic_ledger.as_dict()
    _tactic_ledger = getattr(getattr(context_plane, "tactic_ledger", None), "state", None)
    if hasattr(_tactic_ledger, "as_dict"):
        prompt_payload["tactic_ledger"] = _tactic_ledger.as_dict()
    _affect_state = getattr(getattr(context_plane, "affect", None), "state", None)
    if hasattr(_affect_state, "as_dict"):
        prompt_payload["affect_state"] = _affect_state.as_dict()
    _relationship_state = getattr(getattr(context_plane, "relationship", None), "state", None)
    if hasattr(_relationship_state, "as_dict"):
        prompt_payload["relationship_state"] = _relationship_state.as_dict()
    _drive_state = getattr(getattr(context_plane, "drive", None), "state", None)
    if hasattr(_drive_state, "as_dict"):
        prompt_payload["drive_state"] = _drive_state.as_dict()
    _multimodal_block = getattr(getattr(context_plane, "multimodal", None), "state", None)
    if hasattr(_multimodal_block, "as_dict"):
        prompt_payload["multimodal"] = _multimodal_block.as_dict()
    prompt_payload["reply_plan"] = _reply_plan
    _logger.info(
        "[%s] conversation_history turns_injected=%s older_turn_count=%s summary_present=%s oldest_ts_ms=%s newest_ts_ms=%s",
        request_id,
        conversation_history_meta["turns_injected"],
        conversation_history_meta["older_turn_count"],
        conversation_history_meta["summary_present"],
        conversation_history_meta["oldest_ts_ms"],
        conversation_history_meta["newest_ts_ms"],
    )

    effective_user_message_for_prompt = str(user_message or "")
    if _is_image_social_prompt:
        signal = _image_social_prompt_signal or "一种表情包情绪"
        effective_user_message_for_prompt = f"（{signal}）"
        prompt_payload["effective_user_message_for_prompt"] = effective_user_message_for_prompt

    messages = build_chat_messages(
        system_prompt=system_prompt,
        history_turns=[
            dict(item or {})
            for item in list(context_plane.continuity.recent_turns or [])
            if isinstance(item, Mapping)
        ],
        user_message=effective_user_message_for_prompt,
    )

    if include_runtime_payload:
        return messages, gen_params, dict(prompt_payload)
    return messages, gen_params


__all__ = ["build_cortex_prompt"]
