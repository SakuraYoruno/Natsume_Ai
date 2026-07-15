"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from host_brain.core.runtime_helpers import (  # noqa: F401
    _format_template_text,
    _load_template_mapping,
    _mapping_section,
    _mapping_text,
    _parse_bool_env,
    _sse,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _build_reply_preview,
    _coerce_trace_mapping,
    _coerce_trace_score,
    _coerce_trace_bool,
    _normalize_trace_stage_meta,
    _resolve_envelope_confidence,
    _build_trace_output_digest,
    _detect_trace_fallback_used,
    _summarize_motive_state_for_trace,
    _summarize_conversation_interpretation_for_trace,
    _summarize_reply_plan_for_trace,
    _summarize_tactic_ledger_for_trace,
    _summarize_topic_ledger_for_trace,
    _summarize_affect_state_for_trace,
    _summarize_relationship_state_for_trace,
    _summarize_drive_state_for_trace,
    _summarize_emotion_context_for_trace,
    _summarize_persona_resident_memory_for_trace,
)
import asyncio
import re

from host_brain.agent_runtime.runtime_texts import (
    _logger,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts


def _needs_selection_followup_retrieval_budget(message: str) -> bool:
    text = re.sub(r"\s+", "", str(message or ""))
    if not text:
        return False
    try:
        from host_brain.memory.life_memory_query import CONTRACT_ANAPHORIC_SELECTION, plan_life_memory_query  # noqa: PLC0415

        plan = plan_life_memory_query(text)
        if (
            bool(plan.should_recall)
            and plan.recall_contract_id == CONTRACT_ANAPHORIC_SELECTION
            and (
                "read_detail_evidence_before_deep_quality_judgment" in set(plan.answer_obligations)
                or "compare_recalled_items_before_answering" in set(plan.answer_obligations)
            )
        ):
            return True
    except Exception:
        pass
    if not re.search(r"(哪本|哪部|哪个|哪個|喜欢哪|喜歡哪|好看|更好|最好|值得看|能看|怎么样|如何|先看|选哪|選哪|挑哪|推荐哪|推薦哪)", text):
        return False
    return bool(
        re.search(
            r"(这几本|這幾本|那几本|那些|这些|這些|这三本|這三本|那三本|"
            r"这两本|這兩本|那两本|昨天那|前天那|今天这|最近那|这几天|十几本|"
            r"这本书|這本書|书|書|轻小说|輕小說|小说|小說)",
            text,
        )
    )


def _is_low_risk_short_chat_route_reason(route_reason: str) -> bool:
    reason = str(route_reason or "").strip()
    if not reason:
        return False
    return bool(
        reason.startswith("RELATIONAL_CHAT:")
        or reason
        in {
            "THALAMUS_RULE_ASSISTANT_REPLY_FOLLOWUP",
        }
    )


def _maybe_build_previous_meaning_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    # Clarification direct rendering is retired; let the chat main chain use
    # InteractionThreadState + ReplyPlan instead of a side-door template answer.
    return None


def _build_retrieval_bond_summary(summary_raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(summary_raw)
    bond_stage = str(raw.get("bond_stage") or raw.get("stage") or "").strip()
    if not bond_stage:
        return {}

    bond_summary: dict[str, Any] = {
        "stage": bond_stage,
        "trend": str(raw.get("trend") or "").strip(),
        "capability_hint": str(raw.get("capability_hint") or "").strip(),
    }
    bond_score = _coerce_trace_score(raw.get("bond_score"))
    if bond_score is not None:
        bond_summary["score"] = bond_score
        bond_summary["bond_score"] = bond_score
    try:
        interaction_count = int(raw.get("interaction_count") or 0)
    except (TypeError, ValueError):
        interaction_count = 0
    if interaction_count > 0:
        bond_summary["interaction_count"] = interaction_count
    return bond_summary


async def _prefetch_retrieval_persona_inputs(
    *,
    application_ports: AgentRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
    mood_state: str | None,
    governor_decision: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    bond_summary: dict[str, Any] = {}
    bond_evidence: dict[str, Any] | None = None
    motive_state: dict[str, Any] = {}

    try:
        bond_summary_raw, bond_evidence = await application_ports.resolve_state_arbiter_bond_fact(
            session_id=session_id,
            request_id=request_id,
        )
        bond_summary = _build_retrieval_bond_summary(bond_summary_raw)
    except Exception as exc:
        _logger.debug("[%s] retrieval persona bond prefetch failed: %s", request_id, exc)

    try:
        from host_brain.persona.motive_runtime import (  # noqa: PLC0415
            compute_runtime_motive_state,
        )

        retrieval_emotion_context = dict(emotion_context or {})
        if mood_state and not str(retrieval_emotion_context.get("mood_state") or "").strip():
            retrieval_emotion_context["mood_state"] = mood_state
        runtime_flags = {"source": "cortex_orchestrator_retrieval"}
        resolved_sys_mode = str(
            (governor_decision or {}).get("sys_mode")
            or (governor_decision or {}).get("mood_state")
            or ""
        ).strip().lower()
        if resolved_sys_mode:
            runtime_flags["sys_mode"] = resolved_sys_mode
        motive_state_raw = await asyncio.to_thread(
            compute_runtime_motive_state,
            persona_user_id=person_id,
            current_topic=user_message,
            bond_summary=bond_summary,
            emotion_context=retrieval_emotion_context,
            pending_topics=[user_message] if user_message else [],
            runtime_flags=runtime_flags,
            now_ms=None,
        )
        motive_state = dict(motive_state_raw) if isinstance(motive_state_raw, Mapping) else {}
    except Exception as exc:
        _logger.debug("[%s] retrieval persona motive prefetch failed: %s", request_id, exc)

    return bond_summary, bond_evidence, motive_state
