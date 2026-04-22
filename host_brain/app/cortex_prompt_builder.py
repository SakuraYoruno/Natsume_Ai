"""cortex_prompt_builder.py — Cortex Orchestrator messages 构建。"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.conversation.render import build_chat_messages
from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)

_logger = logging.getLogger("Natsume.cortex_prompt_builder")

CORTEX_NODE_ID = "cortex_orchestrator"


def _get_style_correction_block(render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return resolve_prompt_template_text("cortex_prompt_builder.style_correction.ja")
    return resolve_prompt_template_text("cortex_prompt_builder.style_correction.cn")


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(key): "" if value is None else str(value) for key, value in kwargs.items()})
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _normalize_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _resolve_localized_template_value(value: Any, *, render_target_lang: str | None = None) -> Any:
    if not isinstance(value, Mapping):
        return value
    lang_key = _normalize_template_lang(render_target_lang)
    if lang_key in value:
        return value.get(lang_key)
    if "zh" in value:
        return value.get("zh")
    if "ja" in value:
        return value.get("ja")
    return value


def _build_relational_chat_node_role(*, render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return "役割：夏目藍本人として、短い関係会話に自然な口語でそのまま返す。説明を広げすぎず、客服口調にもならない。"
    return "角色：以夏目蓝本人身份直接短答当前关系闲聊，自然口语、少解释、不客服腔。"


def _build_soul_section_title(*, render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return "【人格核心】\n"
    return "【人格核心】\n"


def build_cortex_prompt(
    *,
    user_message: str,
    render_target_lang: str = "ja",
    emotion_context: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    person_id: str = "",
    session_id: str = "",
    request_id: str = "",
    thread_id: str | None = None,
    bond_summary: dict[str, Any] | None = None,
    turn_context_sections: list[str] | tuple[str, ...] | None = None,
    include_runtime_payload: bool = False,
) -> tuple[list[dict[str, str]], dict[str, Any]] | tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    """构建 Cortex Orchestrator 的原生 messages。"""
    from host_brain.app.chat_payload_builder import (  # noqa: PLC0415
        run_build_chat_prompt_runtime_payload,
        run_build_chat_prompt_runtime_payload_lightweight,
    )
    from host_brain.app.relational_chat_detector import detect_relational_chat_topic  # noqa: PLC0415
    from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: PLC0415
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        _build_current_local_time_context,
        _build_node_role_instruction,
    )
    from host_brain.integrations.llm_helpers import adjust_gen_params_for_emotion  # noqa: PLC0415

    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}
    relational_topic = detect_relational_chat_topic(user_message)
    is_relational_chat = bool(relational_topic)

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
        scene_type="CHAT",
    )

    if is_relational_chat:
        prompt_payload = run_build_chat_prompt_runtime_payload_lightweight(
            runtime_input=runtime_input,
            profile=f"relational_chat:{relational_topic}",
        )
    else:
        prompt_payload = run_build_chat_prompt_runtime_payload(runtime_input=runtime_input)
    if emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    context_plane = assemble_prompt_context_plane(
        node_id=CORTEX_NODE_ID,
        runtime_payload=prompt_payload,
        turn_context_sections=turn_context_sections,
        history_max_turns=(6 if is_relational_chat else None),
        allow_older_summary=(not is_relational_chat),
    )

    _soul_text = str(
        context_plane.soul.compact_text if is_relational_chat else context_plane.soul.full_text
    ).strip()

    _node_role = (
        _build_relational_chat_node_role(render_target_lang=render_target_lang)
        if is_relational_chat
        else _build_node_role_instruction(
            CORTEX_NODE_ID,
            render_target_lang=render_target_lang,
        )
    )
    _section_templates = _load_template_mapping("cortex_prompt_builder.sections")

    _parts = []
    if _soul_text:
        _parts.append(
            _build_soul_section_title(render_target_lang=render_target_lang) + _soul_text
        )
    if not is_relational_chat:
        _time_context_text = _build_current_local_time_context(
            render_target_lang=render_target_lang,
            line_kind="full",
        )
        if _time_context_text:
            _parts.append(
                _format_template_text(
                    _resolve_localized_template_value(
                        _section_templates.get("current_time_title"),
                        render_target_lang=render_target_lang,
                    )
                    or "【当前本地时间】\n"
                )
                + _time_context_text
            )
        _parts.append(_get_style_correction_block(render_target_lang))
    _parts.append(_node_role)
    continuity_summary = "" if is_relational_chat else str(context_plane.continuity.summary or "").strip()
    if continuity_summary:
        continuity_title = _format_template_text(
            _resolve_localized_template_value(
                _section_templates.get("continuity_summary_title"),
                render_target_lang=render_target_lang,
            )
            or "【更早对话摘要】\n"
        )
        _parts.append(continuity_title + continuity_summary)
    _reply_plan = dict(getattr(getattr(context_plane, "reply_plan", None), "state", {}) or {})
    _affect_summary = str(_reply_plan.get("affect_summary") or "").strip()
    if _affect_summary:
        _parts.append(_affect_summary)
    _relationship_summary = str(_reply_plan.get("relationship_summary") or "").strip()
    if _relationship_summary:
        _parts.append(_relationship_summary)
    _reply_contract = str(_reply_plan.get("reply_contract") or "").strip()
    if _reply_contract:
        _parts.append("【本轮回应动作】\n" + _reply_contract)
    turn_contexts = [str(section or "").strip() for section in list(context_plane.intent.turn_context_sections or ()) if str(section or "").strip()]
    if turn_contexts:
        turn_context_title = _format_template_text(
            _resolve_localized_template_value(
                _section_templates.get("current_turn_context_title"),
                render_target_lang=render_target_lang,
            )
            or "【当前轮上下文】\n"
        )
        _parts.append(turn_context_title + "\n\n".join(turn_contexts))
    system_prompt = "\n\n".join(_parts)

    conversation_history_meta = {
        "turns_injected": int(context_plane.continuity.turns_injected or 0),
        "fetched_turns": int(context_plane.continuity.fetched_turns or 0),
        "older_turn_count": int(context_plane.continuity.older_turn_count or 0),
        "summary_present": bool(continuity_summary),
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

    messages = build_chat_messages(
        system_prompt=system_prompt,
        history_turns=[
            dict(item or {})
            for item in list(context_plane.continuity.recent_turns or [])
            if isinstance(item, Mapping)
        ],
        user_message=user_message,
    )

    if include_runtime_payload:
        return messages, gen_params, dict(prompt_payload)
    return messages, gen_params


__all__ = ["build_cortex_prompt"]
