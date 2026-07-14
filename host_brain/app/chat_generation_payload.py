from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_builder import _truncate_for_prompt, infer_render_target_lang
from host_brain.app.context.persona_expression import (
    load_runtime_guidance_templates as _load_runtime_guidance_templates,
    template_text as _template_text,
)
from host_brain.app.context.retrieval_context import (
    build_prompt_context_observability,
    build_retrieval_rows as _build_retrieval_rows,
)
from host_brain.app.chat_runtime_input import build_emotion_recall_text
from host_brain.composition.process_services import get_ledger
from host_brain.app.user_preference_store import build_user_preference_pack
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.core.prompt_context import (
    build_emotion_pulse_slot,
    build_world_canon_slot,
    validate_prompt_context,
)
from host_brain.core.qa import check_wcanon_slot_violation
from host_brain.core.soft_fail import soft_fail

logger = logging.getLogger(__name__)


def _resolve_sys_mode_default() -> str:
    try:
        from host_brain.app.settings import get_core_settings  # noqa: PLC0415

        resolved = str(get_core_settings().sys_mode or "").strip().lower()
        if resolved:
            return resolved
    except Exception:
        pass
    try:
        resolved = str(os.environ.get("HOST_SYS_MODE") or "").strip().lower()
        if resolved:
            return resolved
    except Exception:
        pass
    return "normal"


def build_prompt_context(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    user_message: str = "",
    style_bindings: Mapping[str, Any] | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from host_brain.app.emotion_context import normalize_chat_emotion_context  # noqa: PLC0415
    from host_brain.bond.summarizer import normalize_bond_summary  # noqa: PLC0415

    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)
    resolved_bond_summary = (
        normalize_bond_summary(dict(bond_summary))
        if isinstance(bond_summary, Mapping) and bond_summary
        else {}
    )

    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "").strip() or None
    try:
        world_top_k = int(str(os.environ.get("HOST_WCANON_TOP_K") or "3").strip())
    except ValueError:
        world_top_k = 3
    world_top_k = max(1, min(world_top_k, 10))
    authoritative_rows: list[dict[str, Any]] = []
    try:
        authoritative_rows = build_world_canon_slot(world_root=world_root, top_k=world_top_k)
    except Exception as exc:
        check_wcanon_slot_violation(
            slot_name="world_canon_slot",
            detail=f"world canon slot 构建失败: {exc}",
            ledger=get_ledger(),
            request_id=request_id,
        )
        authoritative_rows = []

    retrieval_rows = _build_retrieval_rows(
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
    )

    mood_state = str(normalized_emotion_context.get("mood_state") or "steady")
    emotion_tag = str(normalized_emotion_context.get("emotion_tag") or "NEUTRAL")
    emotion_range_hint = _truncate_for_prompt(normalized_emotion_context.get("emotion_range_hint"), limit=200)
    allowed_emotion_tags = list(normalized_emotion_context.get("allowed_emotion_tags") or [])
    emotion_pulse = build_emotion_pulse_slot(
        None,
        emotion_tag=emotion_tag,
        mood_state=mood_state,
    )
    if emotion_range_hint:
        emotion_pulse["emotion_range_hint"] = emotion_range_hint
    memory_emotion_hint = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "emotion_range_hint": emotion_range_hint,
        "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
        "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
    }
    emotion_recall_text = build_emotion_recall_text(
        normalized_emotion_context,
        render_target_lang=render_target_lang,
    )
    bond_texture = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "tone_hint": str(emotion_pulse.get("style_hint") or ""),
    }
    runtime_guidance_templates = _load_runtime_guidance_templates(render_target_lang=render_target_lang)
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        user_id = get_canonical_bond_user_id()
        social_snapshot = get_social_brain_agent().resolve_bond_snapshot(
            person_id=user_id,
            bond_summary=resolved_bond_summary,
        )
        bond_state = social_snapshot.get("bond_state_obj")
        if bond_state is not None:
            if not resolved_bond_summary:
                resolved_bond_summary = dict(social_snapshot.get("bond_summary") or {})
            bond_texture["bond_stage"] = str(social_snapshot.get("bond_stage") or "")
            bond_texture["bond_trend"] = str(social_snapshot.get("bond_trend") or "")
            texture = bond_state.texture
            bond_texture["intimacy_temperature"] = round(texture.intimacy_temperature, 3)
            if texture.intimacy_temperature > 0.8:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates, "bond_texture", "temperature_hints", "gt_0_8", render_target_lang=render_target_lang
                )
            elif texture.intimacy_temperature > 0.6:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates, "bond_texture", "temperature_hints", "gt_0_6", render_target_lang=render_target_lang
                )
            elif texture.intimacy_temperature > 0.4:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates, "bond_texture", "temperature_hints", "gt_0_4", render_target_lang=render_target_lang
                )
            elif texture.intimacy_temperature > 0.2:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates, "bond_texture", "temperature_hints", "gt_0_2", render_target_lang=render_target_lang
                )
            else:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates, "bond_texture", "temperature_hints", "default", render_target_lang=render_target_lang
                )
            rift = bond_state.rift
            if rift is not None and rift.is_active():
                bond_texture["rift_active"] = True
                bond_texture["rift_cause"] = rift.rift_cause
                bond_texture["rift_repair_progress"] = round(rift.repair_progress, 2)
                if rift.repair_progress > 0.7:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates, "bond_texture", "rift_hints", "repair_high", render_target_lang=render_target_lang
                    )
                elif rift.repair_progress > 0.3:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates, "bond_texture", "rift_hints", "repair_mid", render_target_lang=render_target_lang
                    )
                else:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates,
                        "bond_texture",
                        "rift_hints",
                        "repair_low_template",
                        render_target_lang=render_target_lang,
                        rift_cause=rift.rift_cause,
                    )
            else:
                bond_texture["rift_active"] = False
    except Exception:
        pass

    try:
        user_preference_pack = build_user_preference_pack(
            person_id=get_canonical_bond_user_id(),
            query_text=user_message,
            session_id=session_id,
            conversation_id=session_id,
            ledger=get_ledger(),
        )
    except Exception:
        user_preference_pack = {}

    prompt_context = {
        "hard_snapshot": {
            "session_id": session_id,
            "thread_id": thread_id,
            "sys_mode": _resolve_sys_mode_default(),
            "mood_state": mood_state,
            "allowed_emotion_tags": allowed_emotion_tags,
            "emotion_range_hint": emotion_range_hint,
        },
        "bond_summary": resolved_bond_summary,
        "authoritative": authoritative_rows,
        "retrieval": retrieval_rows,
        "constraints": {
            "strict_json_output": True,
            "no_false_execution_claim": True,
            "no_canon_as_fact": True,
            "tone_policy": "short_restrained",
            "contract_versions": {
                "core": "CoreDraft.v1",
                "render": "RenderDraft.v1",
                "verifier": "Verifier.v1",
            },
            "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
            "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
        },
        "tool_meta_pack": [],
        "emotion_pulse": emotion_pulse,
        "emotion_recall": emotion_recall_text,
        "memory_emotion_hint": memory_emotion_hint,
        "bond_texture": bond_texture,
    }
    if user_preference_pack:
        prompt_context["user_preference_pack"] = user_preference_pack

    validation = validate_prompt_context(prompt_context)
    violations = [{"code": item.code, "detail": item.detail, "slot": item.slot} for item in validation.violations]
    if validation.valid:
        return (
            prompt_context,
            {"valid": True, "degraded": False, "reason_code": None, "violation_count": 0, "violations": []},
        )

    for item in validation.violations:
        if item.slot == "authoritative":
            check_wcanon_slot_violation(
                slot_name="world_canon_slot",
                detail=item.detail,
                ledger=get_ledger(),
                request_id=request_id,
            )
            break

    degraded_pack = dict(prompt_context)
    degraded_pack["authoritative"] = []
    degraded_validation = validate_prompt_context(degraded_pack)
    if degraded_validation.valid:
        return (
            degraded_pack,
            {"valid": True, "degraded": True, "reason_code": "PROMPT_CONTEXT_DEGRADED", "violation_count": len(violations), "violations": violations},
        )

    minimal_pack = {
        "hard_snapshot": {},
        "bond_summary": {},
        "authoritative": [],
        "retrieval": [],
        "constraints": {},
        "tool_meta_pack": [],
    }
    return (
        minimal_pack,
        {"valid": True, "degraded": True, "reason_code": "PROMPT_CONTEXT_MINIMAL_FALLBACK", "violation_count": len(violations), "violations": violations},
    )


def build_runtime_mode_flags(prompt_context: Mapping[str, Any] | None) -> dict[str, Any]:
    context = dict(prompt_context or {}) if isinstance(prompt_context, Mapping) else {}
    hard_snapshot_raw = context.get("hard_snapshot")
    hard_snapshot = dict(hard_snapshot_raw) if isinstance(hard_snapshot_raw, Mapping) else {}

    sys_mode = str(hard_snapshot.get("sys_mode") or _resolve_sys_mode_default() or "normal").strip().lower() or "normal"

    runtime_flags: dict[str, Any] = {"sys_mode": sys_mode}
    with soft_fail("chat.runtime_mode.activity_monitor", fallback=None) as box:
        from host_brain.composition import process_services as _deps  # noqa: PLC0415

        monitor = _deps.get_activity_monitor()
        if monitor is not None and hasattr(monitor, "get_status"):
            activity_status = monitor.get_status()
            if isinstance(activity_status, Mapping):
                box[0] = dict(activity_status)
    if box[0] is not None:
        runtime_flags["activity_monitor"] = box[0]

    return runtime_flags


def build_chat_generation_prompt_payload(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    bond_summary: Mapping[str, Any] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import build_chat_emotion_snapshot, normalize_chat_emotion_context  # noqa: PLC0415

    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)
    normalized_emotion_snapshot = (
        dict(emotion_snapshot)
        if isinstance(emotion_snapshot, Mapping)
        else build_chat_emotion_snapshot(
            emotion_context=normalized_emotion_context,
            source="prompt_payload",
        )
    )
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = infer_render_target_lang(user_message=user_message)
    prompt_context, prompt_context_validation = build_prompt_context(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=user_message,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        bond_summary=bond_summary,
        emotion_context=normalized_emotion_context,
        render_target_lang=resolved_render_lang,
    )
    prompt_context_observability = build_prompt_context_observability(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
    )
    return {
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "render_target_lang": resolved_render_lang,
        "emotion_context": {
            "mood_state": normalized_emotion_context.get("mood_state"),
            "emotion_tag": normalized_emotion_context.get("emotion_tag"),
            "emotion_range_hint": normalized_emotion_context.get("emotion_range_hint"),
            "emotion_intensity": normalized_emotion_context.get("emotion_intensity"),
            "vad_vector": normalized_emotion_context.get("vad_vector"),
            "emotion_echo": normalized_emotion_context.get("emotion_echo"),
            "inner_monologue": normalized_emotion_context.get("inner_monologue"),
            "triggered_emotions": normalized_emotion_context.get("triggered_emotions"),
            "user_emotion_profile": normalized_emotion_context.get("user_emotion_profile"),
            "emotion_ts_ms": normalized_emotion_context.get("emotion_ts_ms"),
            "_prev_emotion_tag": normalized_emotion_context.get("_prev_emotion_tag"),
        },
        "emotion_snapshot": normalized_emotion_snapshot,
        "prompt_context": prompt_context,
        "prompt_context_validation": prompt_context_validation,
        "prompt_context_observability": prompt_context_observability,
        "retrieval": {
            "status": retrieval_summary.get("status"),
            "reason_code": retrieval_summary.get("reason_code"),
            "route_count": retrieval_summary.get("route_count"),
            "candidate_count": retrieval_summary.get("candidate_count"),
            "topk_count": retrieval_summary.get("topk_count"),
            "degraded": retrieval_summary.get("degraded"),
            "evidence_items": retrieval_evidence_items,
        },
    }


__all__ = [
    "build_prompt_context",
    "build_runtime_mode_flags",
    "build_chat_generation_prompt_payload",
]
