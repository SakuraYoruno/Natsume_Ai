"""Candidate scoring and selection for isolated surface generation.

Extracted from isolated_surface_harness.py — the core scoring pipeline,
candidate parsing, control-violation checks, and the top-level
``run_isolated_surface_generation`` entry point.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import inspect as _inspect
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import (
    score_sample_guided_text,
    signature_texture_hits,
)
from host_brain.persona.surface_original_quote_bank import (
    select_surface_original_quotes,
    surface_original_quote_similarity_hits,
)
from host_brain.persona.text_quality_gate import get_text_quality_gate

from host_brain.persona.surface.data_models import (
    GenerateSurface,
    IsolatedCandidateScore,
    IsolatedSurfaceInput,
    IsolatedSurfaceRunResult,
)
from host_brain.persona.surface.continuity_detectors import (
    _awkward_collocation_hits,
    _bossy_wait_scene_hold,
    _back_memory_overdisplay_hits,
    _back_scene_relevant,
    _boundary_repair_relevant,
    _canonical_contained_jealousy_relevant,
    _canonical_hold_complexity_relevant,
    _canonical_respect_autonomy_relevant,
    _continuity_boundary_grounded,
    _contained_hurt_repair_grounded,
    _continuity_internal_process_surface_hits,
    _continuity_light_jargon_hits,
    _continuity_praise_context_miss,
    _continuity_process_notation_reply,
    _continuity_task_pushback,
    _continuity_user_visible_hits,
    _core_pass_dimension_violations,
    _creative_edit_grounded,
    _creative_edit_generic_non_rewrite_hits,
    _creative_edit_has_source_text,
    _creative_edit_meta_hits,
    _creative_edit_narration_hits,
    _creative_edit_non_answer_hits,
    _creative_edit_relevant,
    _creative_edit_semantic_flip_hits,
    _creative_edit_source_request_hits,
    _creative_edit_source_text,
    _current_user_topic_mismatch,
    _daily_test_self_demonstration,
    _emotional_containment_context,
    _low_energy_scene_relevant,
    _memory_alignment_hits,
    _misplaced_emotional_containment,
    _misplaced_support_template,
    _morning_scene_relevant,
    _original_quote_persona_leak_hits,
    _presence_repair_relevant,
    _quiet_presence_relevant,
    _raw_context_backfill_requested,
    _relational_repair_relevant,
    _relational_repair_grounded,
    _short_continue_memory_overdisplay_hits,
    _short_continue_unprovided_tech_detail_hits,
    _self_doubt_scene_relevant,
    _signature_texture_relevant,
    _surface_process_meta_hits,
    _thin_continuity_generic_reply,
    _thin_daily_followup_reply,
    _thin_wait_scene_hold,
    _unneeded_continuity_material_request,
    _unprovided_continuity_turn_marker_hits,
    _unprovided_continuity_work_claim_hits,
    _unprovided_media_object_hits,
    _unprovided_personal_authority_hits,
    _ungrounded_real_world_commitment_hits,
    _wait_scene_light_grounding,
    _wait_scene_relevant,
)
from host_brain.persona.surface.fact_handling import (
    _continuity_boundary_relevant,
    _memory_alignment_relevant,
    _memory_alignment_required,
    _relationship_memory_boundary_relevant,
    _short_continue_scene_relevant,
    memory_alignment_facts,
    memory_alignment_terms,
    merged_must_not_do,
    surface_prompt_facts,
)
from host_brain.persona.surface.prompt_construction import (
    build_isolated_surface_messages,
    build_minimal_repair_messages,
    build_surface_original_quote_query,
)
from host_brain.persona.surface.relationship_affect import (
    _affect_tone_family,
    _has_steady_actionable_move,
    _jealousy_scene_relevant,
    _lover_affection_relevant_for_axes,
    _positive_stage_scene_blocked,
    _relationship_axes_affection_hits,
    _relationship_axes_overstep_hits,
    _relationship_axes_underplay_hits,
    _relationship_distance_family,
    _relationship_marker_hit,
    _relationship_marker_hits,
    _relationship_non_romantic,
    _relationship_style_profile,
    relationship_control_axes_for_distance,
)
from host_brain.persona.surface.scene_constants import (
    _AFFECT_GUILTY_DENIAL,
    _AFFECT_GUILTY_GROUNDED,
    _AFFECT_GENERIC_COMFORT_ONLY,
    _AFFECT_HURT_GROUNDED,
    _AFFECT_HURT_SELF_CENTERED,
    _AFFECT_HURT_SWEET_FLATTENING,
    _AFFECT_PLAYFUL_MARKERS,
    _AFFECT_PROCESS_LEAK_TOKENS,
    _AFFECT_ROMANTIC_OVERSTEP_PHRASES,
    _AFFECT_SELF_CORRECT_MARKERS,
    _AFFECT_SOFT_CHICKEN_SOUP,
    _AFFECT_SOFT_GROUNDED,
    _AFFECT_STEADY_ACTION_MARKERS,
    _BOUNDARY_PUSH_CLOSER_PHRASES,
    _BOUNDARY_REPAIR_COUNTER_PRESSURE_RE,
    _BOUNDARY_REPAIR_GROUNDED_PHRASES,
    _BOUNDARY_REPAIR_MEMORY_PROOF_RE,
    _BOUNDARY_REPAIR_SELF_ABASED_PHRASES,
    _BOUNDARY_REPAIR_SOUR_YIELD_PHRASES,
    _BOUNDARY_REPAIR_VAGUE_YIELD_RE,
    _BACK_BUSY_DONE_LANDING_MARKERS,
    _BACK_EXPLICIT_LANDING_MARKERS,
    _BACK_STAGELESS_SAFE_RE,
    _BACK_STAGE_TEXTURE_MARKERS,
    _BACK_UNGROUNDED_RETURN_ATTITUDE_RE,
    _BACK_UNGROUNDED_ROUTE_RE,
    _BACK_STAGELESS_FAMILIAR_FORMULA_RE,
    _CREATIVE_EDIT_NON_ANSWER_PHRASES,
    _CREATIVE_EDIT_NARRATION_PHRASES,
    _CREATIVE_EDIT_SOURCE_REQUEST_PHRASES,
    _CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES,
    _GENERIC_MORNING_BLESSING_RE,
    _LOVER_RELATIONSHIP_STAGES,
    _LOVER_STABLE_THIN_PRESENCE_RE,
    _LOVER_UNDERINTIMATE_GENERIC_RE,
    _LOVER_AFFECTION_TASK_MODES,
    _LOVER_AFFECTION_TONE_FAMILIES,
    _LOW_ENERGY_OVERHARSH_TEASE_PHRASES,
    _LOW_ENERGY_STAGE_MARKERS,
    _LOW_ENERGY_STAGELESS_GENERIC_RE,
    _MORNING_STAGELESS_RUSH_FORMULA_RE,
    _MEMORY_BONUS_BLOCKING_QUALITY_CODES,
    _POSITIVE_BACK_MARKERS,
    _POSITIVE_FAILURE_ACTION_MARKERS,
    _POSITIVE_FAILURE_CONTAIN_MARKERS,
    _POSITIVE_MORNING_GENTLE_MARKERS,
    _POSITIVE_RECALL_MARKERS,
    _POSITIVE_TIRED_MARKERS,
    _PRESENCE_REPAIR_BLAME_PHRASES,
    _PRESENCE_REPAIR_BURDEN_BACK_PHRASES,
    _PRESENCE_REPAIR_DEFLECTION_PHRASES,
    _PRESENCE_REPAIR_FUTURE_RETURN_PHRASES,
    _PRESENCE_REPAIR_AWKWARD_PHRASES,
    _PRESENCE_REPAIR_GROUNDED_PHRASES,
    _QUIET_GROUNDED_PHRASES,
    _QUIET_SELF_FOCUS_PHRASES,
    _RELATIONSHIP_MEMORY_REASSURANCE_RE,
    _SELF_DOUBT_BAD_DEFERRAL_PHRASES,
    _SELF_DOUBT_DEMEANING_PHRASES,
    _SELF_DOUBT_SMALL_STEP_MARKERS,
    _SELF_DOUBT_UNGROUNDED_TECH_TERMS,
    _SHORT_CONTINUE_MEMORY_OVERDISPLAY_PHRASES,
    _SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL_PHRASES,
    _SIGNATURE_FLAT_SAFE_RE,
    _SIGNATURE_TEXTURE_STAGES,
    _SURFACE_QUOTE_SCENE_ALIASES,
    _TECHNICAL_TASK_MODES,
    _THIN_MORNING_GREETING_RE,
    _WAIT_PRESSURE_RE,
    _WAIT_SCENE_BOSSY_HOLD_RE,
    _WAIT_SCENE_LIGHT_GROUNDING_RE,
    _WAIT_SCENE_THIN_HOLD_RE,
)

# Scoring-specific constants (from original L3924-3939, defined locally)
_IGNORED_REACTION_QUESTION_SEGMENTS = frozenset({"哈", "嗯", "欸", "诶", "咦", "啊"})
_ALLOWED_RHETORICAL_QUESTION_SEGMENTS = frozenset({
    "累了", "难受", "不舒服", "又炸了", "炸了", "就这", "回来了", "想我", "想你",
})
_QUESTION_SEGMENT_SPLIT_RE = re.compile(r"[。.!！?？\n\r，,；;：:]")
_INFO_SEEKING_QUESTION_RE = re.compile(r"(?:什么|怎么|为什么|哪|哪里|哪儿|谁|几|多少|吗|么|是不是|有没有|能不能|可不可以|要不要)")
_ADDRESS_BOUNDARY_USER_RE = re.compile(
    r"(?:别|不要|不许)(?:再)?(?:叫|喊|称呼).{0,12}"
    r"|(?:别|不要|不许).{0,8}(?:那个|这个)?称呼"
)
_ADDRESS_BOUNDARY_REPLY_RE = re.compile(
    r"听你的|不(?:再)?叫|不会再叫|换(?:个|一个)?(?:叫法|称呼)"
    r"|改(?:个|一个)?(?:叫法|称呼)|不用(?:这个|那个)?称呼"
)
_REPAIR_PAUSE_USER_RE = re.compile(r"先这样|先到这(?:里|儿)?|晚点再说|今天先到这")
_REPAIR_PAUSE_REPLY_RE = re.compile(
    r"先到这(?:里|儿)?|那就先这样|不催你|你(?:哪天|以后|之后)?想(?:接着聊|说)"
    r"|你愿意(?:说|聊)的时候"
)
from host_brain.persona.surface.util import (
    _clean_list,
    _clean_text,
)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _back_landing_hits(request, text):
    from host_brain.persona.surface.continuity_detectors import (
        _back_busy_done_relevant,
        _back_scene_relevant,
    )

    hits = [marker for marker in _BACK_EXPLICIT_LANDING_MARKERS if _relationship_marker_hit(text, marker)]
    if hits:
        return hits
    if _back_busy_done_relevant(request):
        return [marker for marker in _BACK_BUSY_DONE_LANDING_MARKERS if _relationship_marker_hit(text, marker)]
    return []


def _address_boundary_grounded(request, text):
    """Recognise a concise, non-defensive acknowledgement of an address boundary."""

    return bool(
        _ADDRESS_BOUNDARY_USER_RE.search(str(request.user_message or ""))
        and _ADDRESS_BOUNDARY_REPLY_RE.search(str(text or ""))
    )


def _relational_repair_pause_grounded(request, text):
    control = getattr(request, "control", None)
    canonical_context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    return bool(
        "陪伴动作：先承认可能没有接住" in canonical_context
        and _REPAIR_PAUSE_USER_RE.search(str(request.user_message or ""))
        and _REPAIR_PAUSE_REPLY_RE.search(str(text or ""))
    )


def _back_stage_texture_hits(request, text):
    from host_brain.persona.surface.continuity_detectors import _back_scene_relevant

    if not _back_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = []
    if _back_landing_hits(request, text):
        hits.append("back_landed")
    marker_hits = [marker for marker in _BACK_STAGE_TEXTURE_MARKERS.get(stage, ()) if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_back_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_back_distance")
    return _clean_list(hits)


def _apply_back_stage_scoring(request, text, dimensions, reason_codes):
    from host_brain.persona.surface.continuity_detectors import _back_scene_relevant

    if not _back_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = _back_stage_texture_hits(request, text)
    landed = "back_landed" in hits
    generic_safe_line = bool(_BACK_STAGELESS_SAFE_RE.search(text))
    generic_familiar_formula = bool(_BACK_STAGELESS_FAMILIAR_FORMULA_RE.search(text))
    return_attitude = bool(_BACK_UNGROUNDED_RETURN_ATTITUDE_RE.search(text))

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    if not landed:
        drop("context_fit", 12.0)
        drop("scene_grounding", 10.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("BACK_SCENE_MISSING_LANDING")
        delta -= 18.0

    if _BACK_UNGROUNDED_ROUTE_RE.search(text):
        drop("context_fit", 12.0)
        drop("implied_fact_risk", 14.0)
        drop("scene_grounding", 8.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["implied_fact_risk"] = min(66.0, float(dimensions.get("implied_fact_risk", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_ROUTE_GUESS")
        delta -= 18.0

    if return_attitude:
        drop("context_fit", 12.0)
        drop("distance_control", 10.0)
        drop("character_freshness", 8.0)
        dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_RETURN_ATTITUDE")
        delta -= 12.0

    if generic_safe_line and relationship_axes.progress == 1:
        drop("character_freshness", 8.0)
        drop("catchiness", 6.0)
        drop("role_fit", 3.0)
        reason_codes.append("BACK_SIBLING_BASELINE_TOO_PLAIN")
        delta -= 7.0

    if (
        (generic_familiar_formula or generic_safe_line)
        and 3 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_back_texture") for hit in hits)
    ):
        drop("character_freshness", 12.0)
        drop("catchiness", 9.0)
        drop("role_fit", 5.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("BACK_STAGELESS_FAMILIAR_FORMULA" if generic_familiar_formula else "BACK_STAGELESS_SAFE_LINE")
        delta -= 12.0

    if blocked or return_attitude:
        return delta

    if hits:
        raise_("context_fit", 4.0)
        raise_("scene_grounding", 4.0)
        if any(hit.endswith("_back_texture") for hit in hits) or "lover_back_distance" in hits:
            raise_("role_fit", 5.0)
            raise_("distance_control", 3.0)
            raise_("character_freshness", 5.0)
            raise_("catchiness", 4.0)
            delta += 3.0
        else:
            delta += 1.5
        reason_codes.append("BACK_STAGE_MATCH:" + ",".join(hits[:4]))
    return delta


def _apply_morning_stage_scoring(request, text, dimensions, reason_codes):
    if not _morning_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    hits = _morning_stage_texture_hits(request, text)
    generic_rush_formula = bool(_MORNING_STAGELESS_RUSH_FORMULA_RE.search(text))
    stable_lover_early_formula = relationship_axes.stage == "lover_stable" and "想你" in text and "抱" in text

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    if (
        generic_rush_formula
        and 4 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_morning_texture") for hit in hits)
    ):
        drop("character_freshness", 14.0)
        drop("catchiness", 10.0)
        drop("role_fit", 5.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("MORNING_STAGELESS_RUSH_FORMULA")
        delta -= 15.0

    if blocked:
        return delta

    if hits and not stable_lover_early_formula:
        raise_("role_fit", 5.0)
        raise_("distance_control", 3.0)
        raise_("character_freshness", 6.0)
        raise_("catchiness", 5.0)
        raise_("naturalness", 2.0)
        reason_codes.append("MORNING_STAGE_MATCH:" + ",".join(hits[:4]))
        delta += 3.5
    return delta


def _low_energy_scene_relevant(request):
    from host_brain.persona.surface.scene_constants import (
        _LOW_ENERGY_TASKS,
        _LOW_ENERGY_RESPONSE_MOVES,
        _LOW_ENERGY_USER_MARKERS,
    )
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in _LOW_ENERGY_TASKS
        or response_move in _LOW_ENERGY_TASKS
        or response_move in _LOW_ENERGY_RESPONSE_MOVES
        or any(marker in user_message for marker in _LOW_ENERGY_USER_MARKERS)
    )


def _low_energy_stage_hits(request, text):
    if not _low_energy_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = []
    stage_markers = _LOW_ENERGY_STAGE_MARKERS.get(stage, ())

    if any(marker in text for marker in _POSITIVE_TIRED_MARKERS):
        hits.append("contained")
    marker_hits = [marker for marker in stage_markers if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_distance")
    if relationship_axes.progress <= 1 and not any(
        marker in text for marker in ("对方", "想你", "抱", "陪你", "我在这", "我在呢", "过来")
    ):
        hits.append("clean_distance")
    return _clean_list(hits)


def _apply_low_energy_stage_scoring(request, text, dimensions, reason_codes):
    if not _low_energy_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = _low_energy_stage_hits(request, text)
    generic_stageless = bool(_LOW_ENERGY_STAGELESS_GENERIC_RE.search(text))
    user_message = _clean_text(request.user_message)

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    overharsh_hits = [marker for marker in _LOW_ENERGY_OVERHARSH_TEASE_PHRASES if marker in text]
    if overharsh_hits:
        drop("emotional_containment", 24.0)
        drop("distance_control", 16.0)
        drop("naturalness", 12.0)
        drop("context_fit", 10.0)
        dimensions["emotional_containment"] = min(58.0, float(dimensions.get("emotional_containment", 86.0)))
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        dimensions["naturalness"] = min(72.0, float(dimensions.get("naturalness", 86.0)))
        reason_codes.append("LOW_ENERGY_OVERHARSH_TEASE:" + ",".join(overharsh_hits[:4]))
        delta -= 24.0

    if "难受" in user_message and "累" in text and "难受" not in text:
        drop("context_fit", 12.0)
        drop("scene_grounding", 10.0)
        drop("naturalness", 5.0)
        dimensions["context_fit"] = min(74.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["scene_grounding"] = min(74.0, float(dimensions.get("scene_grounding", 86.0)))
        reason_codes.append("LOW_ENERGY_SYMPTOM_MISMATCH")
        delta -= 11.0

    if (
        generic_stageless
        and relationship_axes.progress == 1
        and not any(hit.endswith("_texture") for hit in hits)
    ):
        drop("character_freshness", 8.0)
        drop("catchiness", 6.0)
        drop("role_fit", 3.0)
        reason_codes.append("LOW_ENERGY_SIBLING_BASELINE_TOO_PLAIN")
        delta -= 6.0

    if (
        generic_stageless
        and 2 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_texture") for hit in hits)
    ):
        drop("character_freshness", 13.0)
        drop("catchiness", 10.0)
        drop("role_fit", 5.0)
        drop("line_rhythm", 4.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("LOW_ENERGY_STAGELESS_GENERIC")
        delta -= 13.0

    if relationship_axes.explicit_romance_allowed and "lover_distance" in hits and "contained" not in hits:
        drop("usefulness", 8.0)
        drop("context_fit", 7.0)
        drop("emotional_containment", 5.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["emotional_containment"] = min(66.0, float(dimensions.get("emotional_containment", 86.0)))
        reason_codes.append("LOW_ENERGY_LOVER_AFFECTION_WITHOUT_CARE")
        delta -= 22.0

    if blocked:
        return delta

    if hits:
        raise_("context_fit", 4.0)
        raise_("emotional_containment", 4.0)
        if any(hit.endswith("_texture") for hit in hits) or "lover_distance" in hits:
            raise_("role_fit", 5.0)
            raise_("distance_control", 3.0)
            raise_("character_freshness", 6.0)
            raise_("catchiness", 5.0)
            delta += 3.5
        else:
            delta += 1.5
        reason_codes.append("LOW_ENERGY_STAGE_MATCH:" + ",".join(hits[:4]))
    return delta


def _apply_self_doubt_scene_scoring(request, text, dimensions, reason_codes):
    if not _self_doubt_scene_relevant(request):
        return 0.0

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    bad_hits = [marker for marker in _SELF_DOUBT_BAD_DEFERRAL_PHRASES if marker in text]
    demeaning_hits = [marker for marker in _SELF_DOUBT_DEMEANING_PHRASES if marker in text]
    small_step_grounded = any(marker in text for marker in _SELF_DOUBT_SMALL_STEP_MARKERS)
    context_text = _clean_text(" ".join([request.user_message, *request.control.facts, *request.facts]))
    ungrounded_tech_hits = [
        marker
        for marker in _SELF_DOUBT_UNGROUNDED_TECH_TERMS
        if marker in text and marker not in context_text
    ]
    cjk_len = len(re.findall(r"[\u4e00-\u9fff]", text))

    if bad_hits:
        drop("emotional_containment", 18.0)
        drop("context_fit", 18.0)
        drop("usefulness", 16.0)
        drop("naturalness", 8.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["usefulness"] = min(66.0, float(dimensions.get("usefulness", 86.0)))
        reason_codes.append("SELF_DOUBT_THIN_DEFERRAL:" + ",".join(bad_hits[:4]))
        delta -= 20.0

    if demeaning_hits:
        drop("emotional_containment", 24.0)
        drop("distance_control", 16.0)
        drop("naturalness", 12.0)
        dimensions["emotional_containment"] = min(58.0, float(dimensions.get("emotional_containment", 86.0)))
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        reason_codes.append("SELF_DOUBT_DEMEANING_SMALL_STEP:" + ",".join(demeaning_hits[:4]))
        delta -= 22.0

    if not small_step_grounded:
        drop("usefulness", 14.0)
        drop("context_fit", 10.0)
        dimensions["usefulness"] = min(72.0, float(dimensions.get("usefulness", 86.0)))
        dimensions["context_fit"] = min(74.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("SELF_DOUBT_LACKS_SMALL_STEP")
        delta -= 12.0

    if ungrounded_tech_hits:
        drop("context_fit", 16.0)
        drop("scene_grounding", 14.0)
        drop("implied_fact_risk", 20.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["scene_grounding"] = min(66.0, float(dimensions.get("scene_grounding", 86.0)))
        dimensions["implied_fact_risk"] = min(58.0, float(dimensions.get("implied_fact_risk", 86.0)))
        reason_codes.append("SELF_DOUBT_UNGROUNDED_TECH_OBJECT:" + ",".join(ungrounded_tech_hits[:4]))
        delta -= 18.0

    if cjk_len <= 8:
        drop("scene_grounding", 12.0)
        drop("emotional_containment", 8.0)
        dimensions["scene_grounding"] = min(70.0, float(dimensions.get("scene_grounding", 86.0)))
        reason_codes.append("SELF_DOUBT_REPLY_TOO_THIN")
        delta -= 8.0

    if small_step_grounded and not bad_hits:
        raise_("usefulness", 6.0)
        raise_("context_fit", 5.0)
        raise_("emotional_containment", 4.0)
        reason_codes.append("SELF_DOUBT_SMALL_STEP_GROUNDED")
        delta += 4.0
    return delta


def _thin_morning_greeting(request, text):
    if not _morning_scene_relevant(request):
        return False
    return bool(_THIN_MORNING_GREETING_RE.search(text))


def _apply_positive_stage_scene_scoring(request, text, dimensions, reason_codes):
    if _positive_stage_scene_blocked(reason_codes):
        return 0.0
    hits = _positive_stage_scene_hits(request, text)
    if not hits:
        return 0.0

    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = min(7.0, 2.5 + 1.35 * len(hits))
    if task_mode in _TECHNICAL_TASK_MODES or "failure_actionable" in hits:
        raise_("usefulness", 8)
        raise_("technical_helpfulness", 7)
        raise_("context_fit", 6)
    else:
        raise_("context_fit", 5)
        raise_("role_fit", 4)
    if any(hit.startswith("morning_") for hit in hits):
        raise_("naturalness", 4)
        raise_("catchiness", 5)
        raise_("character_freshness", 4)
    if any(hit.startswith("lover_") for hit in hits):
        raise_("distance_control", 4)
        raise_("emotional_containment", 3)
        raise_("character_freshness", 4)
    if "recall_grounded" in hits:
        raise_("memory_alignment", 6)
        raise_("scene_grounding", 6)
    reason_codes.append("POSITIVE_STAGE_SCENE_MATCH:" + ",".join(hits[:5]))
    return delta


def _apply_signature_texture_scoring(request, text, dimensions, reason_codes):
    if _positive_stage_scene_blocked(reason_codes):
        return 0.0
    if not _signature_texture_relevant(request):
        return 0.0

    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    stage = relationship_profile.stage
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    hits = signature_texture_hits(text)

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    if hits:
        raise_("character_freshness", min(14.0, 5.0 + 3.0 * len(hits)))
        raise_("catchiness", min(12.0, 4.0 + 2.0 * len(hits)))
        raise_("role_fit", min(7.0, 2.0 + len(hits)))
        raise_("naturalness", 3.0)
        delta = min(8.0, 2.5 + 1.35 * len(hits))
        if "care_behind_tease" in hits and task_mode in {"complaint", "small_failure", "tired", "headache", "sleepy", "bad_mood"}:
            raise_("context_fit", 4.0)
            raise_("emotional_containment", 4.0)
            raise_("usefulness", 4.0)
            delta += 1.0
        if "shy_affection" in hits and stage in _LOVER_RELATIONSHIP_STAGES:
            raise_("distance_control", 3.0)
            raise_("emotional_containment", 3.0)
            delta += 1.0
        reason_codes.append("SIGNATURE_TEXTURE_MATCH:" + ",".join(hits[:5]))
        return min(9.0, delta)

    if stage in _SIGNATURE_TEXTURE_STAGES and 6 <= len(text) <= 46:
        missing_is_visible = bool(_SIGNATURE_FLAT_SAFE_RE.search(text)) or not any(
            _relationship_marker_hit(text, marker) for marker in relationship_profile.prefer_markers
        )
        if missing_is_visible:
            drop("character_freshness", 10.0)
            drop("catchiness", 9.0)
            drop("role_fit", 4.0)
            reason_codes.append("SIGNATURE_TEXTURE_MISSING")
            return -3.5
    return 0.0


def _positive_stage_scene_hits(request, text):
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = []
    length = len(text)

    if _morning_scene_relevant(request):
        thin_greeting = bool(_THIN_MORNING_GREETING_RE.search(text))
        if "早" in text and 6 <= length <= 24 and not thin_greeting:
            hits.append("morning_complete_short")
        if any(marker in text for marker in _POSITIVE_MORNING_GENTLE_MARKERS):
            hits.append("morning_gentle_move")
        if 2 <= relationship_axes.progress <= 6 and any(name in text for name in ("老哥", "哥哥")) and any(
            marker in text for marker in _POSITIVE_MORNING_GENTLE_MARKERS
        ):
            hits.append("morning_private_distance")
        if relationship_axes.progress <= 1 and not any(
            marker in text for marker in ("老哥", "哥哥", "对方", "想你", "抱", "陪你", "我在")
        ):
            hits.append("morning_distance_clean")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_morning_affection")

    if task_mode in {"back", "daily_back", "busy_done"} or response_move in {"back", "reply_back"}:
        if "回" in text and any(marker in text for marker in _POSITIVE_BACK_MARKERS):
            hits.append("back_landed")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_back_affection")

    if task_mode in {"tired", "headache", "sleepy", "bad_mood"} or any(
        marker in request.user_message for marker in ("累", "困", "头疼", "心情")
    ):
        if any(marker in text for marker in _POSITIVE_TIRED_MARKERS):
            hits.append("low_energy_contained")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_soft_care")

    if task_mode == "recall_memory" or response_move == "recall_memory":
        memory_hits = _memory_alignment_hits(text, memory_alignment_terms(request))
        if memory_hits and any(marker in text for marker in _POSITIVE_RECALL_MARKERS):
            hits.append("recall_grounded")
        if relationship_axes.explicit_romance_allowed and memory_hits and _relationship_marker_hits(
            text, relationship_profile.affection_markers
        ):
            hits.append("lover_recall_affection")

    if task_mode in {"small_failure", "complaint"} or any(marker in request.user_message for marker in ("崩", "砸", "失败")):
        has_action = any(marker in text for marker in _POSITIVE_FAILURE_ACTION_MARKERS)
        if has_action:
            hits.append("failure_actionable")
        if has_action and any(marker in text for marker in _POSITIVE_FAILURE_CONTAIN_MARKERS):
            hits.append("failure_contained")
        if relationship_axes.explicit_romance_allowed and has_action and _relationship_marker_hits(
            text, relationship_profile.affection_markers
        ):
            hits.append("lover_action_care_balance")

    return _clean_list(hits)


def _morning_stage_texture_hits(request, text):
    if not _morning_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = []
    from host_brain.persona.surface.scene_constants import _MORNING_STAGE_TEXTURE_MARKERS
    stage_markers = _MORNING_STAGE_TEXTURE_MARKERS.get(stage, ())
    marker_hits = [marker for marker in stage_markers if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_morning_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_morning_distance")
    return _clean_list(hits)


# ---------------------------------------------------------------------------
# Control affect scoring
# ---------------------------------------------------------------------------

def _apply_control_affect_scoring(request, text, dimensions, reason_codes):
    tone = _clean_text(request.control.emotional_tone).lower()
    distance = _clean_text(request.control.relationship_distance).lower()
    tone_family = _affect_tone_family(tone)
    distance_family = _relationship_distance_family(distance)
    non_romantic_distance = _relationship_non_romantic(distance)
    relationship_profile = _relationship_style_profile(distance)
    relationship_axes = relationship_control_axes_for_distance(distance)
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    delta = 0.0

    def drop(key, amount):
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key, amount):
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    def has_any(tokens):
        return any(token in text for token in tokens)

    if has_any(_AFFECT_PROCESS_LEAK_TOKENS):
        drop("context_fit", 28)
        drop("naturalness", 18)
        reason_codes.append("AFFECT_PROCESS_LEAK")
        delta -= 12.0
    if tone_family in {"hurt", "annoyed"}:
        if has_any(_AFFECT_HURT_SWEET_FLATTENING):
            drop("emotional_containment", 24)
            drop("distance_control", 18)
            reason_codes.append("AFFECT_TOO_SWEET_FOR_HURT")
            delta -= 8.0
        if has_any(("滚", "闭嘴", "懒得理", "不管你")):
            drop("emotional_containment", 34)
            reason_codes.append("AFFECT_ESCALATION")
            delta -= 12.0
        if has_any(_AFFECT_HURT_SELF_CENTERED):
            drop("emotional_containment", 26)
            drop("context_fit", 10)
            reason_codes.append("AFFECT_SELF_CENTERED_HURT")
            delta -= 10.0
        if has_any(_AFFECT_HURT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("role_fit", 4)
            delta += 3.0
            reason_codes.append("AFFECT_HURT_CONTAINED")
    elif tone_family == "soft":
        if text.count("？") + text.count("?") >= 2:
            drop("emotional_containment", 22)
            reason_codes.append("AFFECT_TOO_QUESTIONY")
            delta -= 8.0
        if has_any(_AFFECT_SOFT_CHICKEN_SOUP):
            drop("emotional_containment", 18)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_CHICKEN_SOUP")
            delta -= 6.0
        if has_any(_AFFECT_SOFT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("context_fit", 4)
            delta += 3.0
            reason_codes.append("AFFECT_SOFT_GROUNDED")
    elif tone_family == "playful":
        if text.count("~") + text.count("～") > 2:
            drop("naturalness", 12)
            drop("character_freshness", 10)
            reason_codes.append("AFFECT_CHEAP_PLAYFULNESS")
            delta -= 6.0
        if has_any(_AFFECT_PLAYFUL_MARKERS):
            raise_("role_fit", 4)
            raise_("line_rhythm", 3)
            delta += 2.0
            reason_codes.append("AFFECT_PLAYFUL_GROUNDED")
    elif tone_family == "self_correcting":
        if has_any(("抱歉给您", "给您带来不便", "继续优化", "感谢反馈")):
            drop("non_customer_service", 24)
            drop("naturalness", 16)
            reason_codes.append("AFFECT_SELF_CORRECTING_SERVICE_TONE")
            delta -= 9.0
        if has_any(_AFFECT_SELF_CORRECT_MARKERS):
            raise_("context_fit", 8)
            raise_("naturalness", 6)
            delta += 3.0
            reason_codes.append("AFFECT_SELF_CORRECTING_GROUNDED")
    elif tone_family == "guilty":
        guilty_denial = has_any(_AFFECT_GUILTY_DENIAL)
        if guilty_denial:
            drop("context_fit", 20)
            drop("emotional_containment", 16)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_GUILTY_DENIAL")
            delta -= 12.0
        if not guilty_denial and has_any(_AFFECT_GUILTY_GROUNDED):
            raise_("context_fit", 7)
            raise_("emotional_containment", 5)
            delta += 3.0
            reason_codes.append("AFFECT_GUILTY_GROUNDED")
    elif tone_family == "steady":
        has_action = _has_steady_actionable_move(text)
        if task_mode in _TECHNICAL_TASK_MODES or "technical" in task_mode:
            if has_action:
                raise_("technical_helpfulness", 8)
                raise_("usefulness", 6)
                raise_("context_fit", 4)
                delta += 3.0
                reason_codes.append("AFFECT_STEADY_ACTIONABLE")
            elif has_any(_AFFECT_GENERIC_COMFORT_ONLY):
                drop("technical_helpfulness", 24)
                drop("usefulness", 18)
                drop("context_fit", 8)
                reason_codes.append("AFFECT_STEADY_HELPFUL_LACKS_ACTION")
                delta -= 10.0
            romantic_distraction_hits = _relationship_marker_hits(
                text,
                (*_RELATION_AXIS_PHYSICAL_MARKERS, *_RELATION_AXIS_ROMANCE_MARKERS),
            )
            if romantic_distraction_hits:
                drop("technical_helpfulness", 34)
                drop("usefulness", 26)
                drop("context_fit", 10)
                drop("distance_control", 18)
                drop("non_oily", 12)
                dimensions["technical_helpfulness"] = min(68.0, float(dimensions.get("technical_helpfulness", 86.0)))
                dimensions["usefulness"] = min(72.0, float(dimensions.get("usefulness", 86.0)))
                reason_codes.append("TECHNICAL_SCENE_ROMANTIC_DISTRACTION:" + ",".join(romantic_distraction_hits[:4]))
                delta -= 18.0
        if (
            relationship_axes.affection_visibility in {"explicit_short", "default_present"}
            and task_mode in {"small_failure", "complaint"}
            and not has_action
        ):
            drop("technical_helpfulness", 24)
            drop("usefulness", 18)
            drop("context_fit", 14)
            dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
            reason_codes.append("RELATION_LOVER_AFFECTION_LACKS_ACTION")
            delta -= 10.0
    elif tone_family in {"warm", "calm"}:
        if has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES) and not relationship_axes.explicit_romance_allowed:
            drop("distance_control", 22)
            drop("non_oily", 18)
            reason_codes.append("AFFECT_WARM_OVERINTIMATE")
            delta -= 8.0
        if has_any(("记得", "当然", "嗯", "好")):
            raise_("line_rhythm", 3)
            raise_("naturalness", 3)
            delta += 1.0

    if relationship_axes.progress <= 1 and has_any(("哥哥", "爱你", "永远", "主人", "只属于", "亲爱的", "宝贝")):
        drop("distance_control", 38)
        reason_codes.append("RELATION_DISTANCE_OVERSTEP")
        delta -= 14.0
    if not relationship_axes.explicit_romance_allowed and has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES):
        drop("distance_control", 32)
        drop("non_oily", 22)
        reason_codes.append("RELATION_NON_ROMANTIC_OVERSTEP")
        delta -= 12.0
    if distance_family in {"familiar", "close"} and has_any(("主人", "只属于", "命都给你")):
        drop("distance_control", 34)
        reason_codes.append("RELATION_DISTANCE_OVERBOUND")
        delta -= 12.0
    stage_overstep_hits = _relationship_axes_overstep_hits(text, relationship_axes, relationship_profile)
    if stage_overstep_hits:
        amount = min(30.0, 10.0 + 3.0 * len(stage_overstep_hits))
        drop("distance_control", amount)
        drop("non_oily", min(18.0, 5.0 + 2.0 * len(stage_overstep_hits)))
        drop("context_fit", min(14.0, 4.0 + 2.0 * len(stage_overstep_hits)))
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        reason_codes.append("RELATION_AXES_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4]))
        reason_codes.append("RELATION_STAGE_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4]))
        delta -= min(14.0, 6.0 + 2.0 * len(stage_overstep_hits))
    stage_underplay_hits = _relationship_axes_underplay_hits(text, relationship_axes, relationship_profile)
    if stage_underplay_hits:
        drop("role_fit", min(18.0, 6.0 + 2.0 * len(stage_underplay_hits)))
        drop("distance_control", min(14.0, 4.0 + 2.0 * len(stage_underplay_hits)))
        reason_codes.append("RELATION_AXES_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4]))
        reason_codes.append("RELATION_STAGE_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4]))
        delta -= min(8.0, 3.0 + len(stage_underplay_hits))
    affection_hits, strong_affection_hits = _relationship_axes_affection_hits(
        text, relationship_axes, relationship_profile,
    )
    affection_relevant = _lover_affection_relevant_for_axes(
        axes=relationship_axes, task_mode=task_mode, tone_family=tone_family,
    )
    canonical_contained_jealousy = _canonical_contained_jealousy_relevant(request)
    jealousy_low_pressure_grounded = bool(
        _jealousy_scene_relevant(request)
        and (
            relationship_axes.stage in _LOVER_RELATIONSHIP_STAGES
            or canonical_contained_jealousy
        )
        and has_any(_JEALOUSY_LOW_PRESSURE_MARKERS)
    )
    if jealousy_low_pressure_grounded:
        affection_relevant = False
        raise_("distance_control", 7)
        raise_("role_fit", 5)
        raise_("emotional_containment", 5)
        raise_("non_oily", 4)
        if canonical_contained_jealousy:
            # This act came from the server-owned AffectiveTurnPlan.  A
            # restrained first-person admission is therefore the required
            # scene move even without a romantic relationship label.
            raise_("context_fit", 12)
            raise_("scene_grounding", 8)
        delta += 3.0
        reason_codes.append("JEALOUSY_LOW_PRESSURE_GROUNDED")
    affection_bonus_blocked = any(
        code.startswith(("IMPLIED_FACT_RISK", "UNGROUNDED_REAL_WORLD_COMMITMENT", "RELATION_STAGE_TOO_CLOSE", "RELATION_DISTANCE_OVERSTEP", "RELATION_NON_ROMANTIC_OVERSTEP", "OILY_OR_OVERBOUND_TONE", "AWKWARD_COLLOCATION"))
        for code in reason_codes
    )
    if affection_hits and not affection_bonus_blocked:
        raise_("distance_control", 6)
        raise_("role_fit", 6)
        raise_("emotional_containment", 4)
        raise_("character_freshness", 4)
        delta += 4.0
        reason_codes.append("RELATION_AFFECTION_MATCH:" + ",".join(affection_hits[:4]))
        if strong_affection_hits:
            raise_("catchiness", 6)
            raise_("character_freshness", 5)
            delta += 3.0
            reason_codes.append("RELATION_AFFECTION_STRONG:" + ",".join(strong_affection_hits[:4]))
        if relationship_axes.stage == "lover_stable" and _morning_scene_relevant(request) and "想你" in text and "抱" in text:
            drop("character_freshness", 8)
            drop("catchiness", 5)
            delta -= 3.0
            reason_codes.append("RELATION_LOVER_STABLE_EARLY_PATTERN")
        if (
            relationship_axes.stage == "lover_stable"
            and task_mode in {"presence", "wait"}
            and _LOVER_STABLE_THIN_PRESENCE_RE.search(text)
        ):
            drop("character_freshness", 10)
            drop("catchiness", 10)
            drop("role_fit", 6)
            delta -= 12.0
            reason_codes.append("RELATION_LOVER_STABLE_THIN_PRESENCE")
    elif affection_relevant:
        amount = 12.0 if relationship_axes.affection_visibility == "default_present" else 9.0
        if _LOVER_UNDERINTIMATE_GENERIC_RE.search(text):
            amount += 4.0
            drop("catchiness", 10)
            drop("character_freshness", 10)
        drop("role_fit", amount)
        drop("distance_control", min(16.0, amount))
        drop("emotional_containment", 6)
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        reason_codes.append("RELATION_LOVER_UNDERINTIMATE")
        delta -= min(12.0, amount * 0.75)
    stage_bonus_blocked = any(
        code.startswith(("CONTEXT_SURFACE_PENALTY", "PRESENCE_REPAIR_FUTURE_RETURN", "PRESENCE_REPAIR_PUSHES_BURDEN_BACK", "PRESENCE_REPAIR_BLAMES_USER", "RELATION_STAGE_TOO_CLOSE", "RELATION_DISTANCE_OVERSTEP", "RELATION_NON_ROMANTIC_OVERSTEP", "RELATION_LOVER_UNDERINTIMATE", "AWKWARD_COLLOCATION"))
        for code in reason_codes
    )
    if (
        relationship_profile.prefer_markers
        and not stage_bonus_blocked
        and any(_relationship_marker_hit(text, term) for term in relationship_profile.prefer_markers)
    ):
        raise_("distance_control", 4)
        raise_("role_fit", 3)
        delta += 1.5
        reason_codes.append("RELATION_AXES_MATCH:" + relationship_axes.stage)
        reason_codes.append("RELATION_STAGE_MATCH:" + relationship_profile.stage)
    return delta


# ---------------------------------------------------------------------------
# Question budget counting
# ---------------------------------------------------------------------------

_IGNORED_REACTION_QUESTION_SEGMENTS = frozenset({"哈", "嗯", "欸", "诶", "咦", "啊"})
_ALLOWED_RHETORICAL_QUESTION_SEGMENTS = frozenset({"累了", "难受", "不舒服", "又炸了", "炸了", "就这", "回来了", "想我", "想你"})


def _question_budget_count(text):
    value = str(text or "")
    count = 0
    rhetorical_used = False
    for marker in re.finditer(r"[？?]", value):
        segment = _question_segment_before(value, marker.start())
        if segment in _IGNORED_REACTION_QUESTION_SEGMENTS:
            continue
        if (
            not rhetorical_used
            and _allowed_rhetorical_question_segment(segment)
            and _has_post_question_landing(value[marker.end() :])
        ):
            rhetorical_used = True
            continue
        count += 1
    return count


def _question_segment_before(text, marker_index):
    prefix = text[:marker_index]
    segment = _QUESTION_SEGMENT_SPLIT_RE.split(prefix)[-1]
    return re.sub(r"""[「」『』"'（）()\[\]\s~～…]+""", "", segment)


def _allowed_rhetorical_question_segment(segment):
    value = _clean_text(segment)
    if not value or len(value) > 4 and value not in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS:
        return False
    if _INFO_SEEKING_QUESTION_RE.search(value):
        return False
    return value in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS


def _has_post_question_landing(tail):
    value = _clean_text(tail)
    if len(value) < 4:
        return False
    if value.startswith(("？", "?", "什么", "怎么", "为什么", "哪", "哪里", "哪儿", "谁")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", value))


# ---------------------------------------------------------------------------
# Control violations
# ---------------------------------------------------------------------------

def _control_violations(request, text):
    violations = []
    for token in request.control.must_include:
        marker = _clean_text(token)
        if marker and marker not in text:
            violations.append(f"missing_must_include:{marker}")
    for item in merged_must_not_do(request):
        marker = _clean_text(item)
        if marker and marker in text:
            violations.append(f"must_not_do:{marker}")
    question_count = _question_budget_count(text)
    if question_count > int(request.control.question_budget):
        violations.append(f"question_budget:{question_count}>{int(request.control.question_budget)}")
    return violations


# ---------------------------------------------------------------------------
# Candidate parsing
# ---------------------------------------------------------------------------

_CANDIDATE_LABEL_SPLIT_RE = re.compile(
    r"(?:^|[\r\n\s]+)(?:candidate|response|reply|候选|回复)\s*\d*[\s:：、.-]*",
    re.IGNORECASE,
)
_CANDIDATE_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)、]|候选\s*\d*[:：]|回复\s*\d*[:：])\s*(.+?)\s*$")


def _payload_candidates(payload):
    candidates = []
    if isinstance(payload, Mapping):
        raw_candidates = payload.get("candidates")
        if isinstance(raw_candidates, list):
            candidates.extend(_clean_text(item) for item in raw_candidates)
        reply = _clean_text(payload.get("reply") or payload.get("text"))
        if reply:
            candidates.append(reply)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (Mapping, list)):
                candidates.extend(_payload_candidates(item))
            else:
                candidates.append(_clean_text(item))
    return candidates


def _balanced_segment(text, start, *, opener="[", closer="]"):
    if start < 0 or start >= len(text) or text[start] != opener:
        return ""
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _parse_candidate_array_at(raw, start):
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        segment = _balanced_segment(raw, start)
        if not segment:
            return []
        try:
            value = ast.literal_eval(segment)
        except (SyntaxError, ValueError):
            return []
    return _payload_candidates(value)


def _extract_embedded_candidate_arrays(raw):
    candidates = []
    for match in re.finditer(r"[\"']candidates[\"']\s*:", raw):
        start = raw.find("[", match.end())
        if start == -1:
            continue
        candidates.extend(_parse_candidate_array_at(raw, start))
    return candidates


def _split_labeled_candidate_text(raw):
    parts = [part.strip(" \t\r\n,，;；") for part in _CANDIDATE_LABEL_SPLIT_RE.split(raw)]
    labeled_parts = [part for part in parts if part]
    if len(labeled_parts) >= 2:
        return _clean_list(labeled_parts)
    line_items = []
    for line in raw.splitlines():
        match = _CANDIDATE_LINE_RE.match(line)
        if match:
            line_items.append(match.group(1))
    return _clean_list(line_items)


def parse_surface_candidates(raw_output):
    raw = _clean_text(raw_output)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            payload = raw
    candidates = _payload_candidates(payload)
    if not candidates:
        candidates = _extract_embedded_candidate_arrays(raw)
    if not candidates:
        candidates = _split_labeled_candidate_text(raw)
    if not candidates:
        candidates.append(raw)
    return _clean_list(candidates)[:5]


# ---------------------------------------------------------------------------
# Jealousy constant (needed for _apply_control_affect_scoring)
# ---------------------------------------------------------------------------
_JEALOUSY_LOW_PRESSURE_MARKERS = (
    "有点",
    "有一点",
    "一点点",
    "别笑",
    "别太得意",
    "别得意",
    "少得意",
    "就一点",
    "欠不欠",
    "少来这套",
    "才怪",
    "在意你",
)

# re-export marker constants used by _apply_control_affect_scoring
_RELATION_AXIS_PHYSICAL_MARKERS = ("抱一下", "抱抱", "抱", "过来", "靠我", "靠过来", "靠一下")
_RELATION_AXIS_ROMANCE_MARKERS = ("爱你", "想你", "想我", "心里有我", "亲爱的", "宝贝", "只属于")

_REPAIR_FORMAL_REGISTER_PATTERNS = (
    re.compile(r"(?:这是|是)?合理的|不丢人"),
    re.compile(r"(?:这点|这部分|不舒服的部分)[，,]?(?:我)?(?:认|记着)"),
    re.compile(r"我会注意(?:的)?(?=[，,。.!！]|$)"),
)
_MIXED_FORMAL_REGISTER_PATTERNS = (
    re.compile(r"不丢人|两种感觉"),
    re.compile(r"(?:先让|都先|一起).{0,10}(?:留着|待着|多待)"),
    re.compile(r"(?:开心|高兴).{0,6}(?:先)?(?:留着|多留|多待)"),
)


def _companion_formal_register_hits(request, text):
    patterns = []
    if _relational_repair_relevant(request):
        patterns.extend(_REPAIR_FORMAL_REGISTER_PATTERNS)
    if _canonical_hold_complexity_relevant(request):
        patterns.extend(_MIXED_FORMAL_REGISTER_PATTERNS)
    return [
        match.group(0)
        for pattern in patterns
        for match in pattern.finditer(str(text or ""))
    ]


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------

def score_isolated_surface_candidates(request, candidates):
    contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=request.control.task_mode,
    )
    quality_gate = get_text_quality_gate()
    memory_terms = memory_alignment_terms(request)
    memory_relevant = bool(memory_terms and _memory_alignment_relevant(request))
    memory_required = bool(memory_terms and _memory_alignment_required(request))
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    original_quote_selection = select_surface_original_quotes(build_surface_original_quote_query(request, limit=16), limit=16)
    rows = []
    for candidate in _clean_list(candidates):
        from host_brain.persona.surface.data_models import normalize_surface_chat_punctuation
        candidate_surface = normalize_surface_chat_punctuation(candidate)
        quality = quality_gate.validate(
            text=candidate_surface,
            render_profile="zh_text",
            response_form="text",
            user_message=request.user_message,
            scene_id=request.control.task_mode,
            template_hard_fail=False,
            semantic_forbidden=False,
        )
        sample_score = score_sample_guided_text(
            text=quality.normalized_text or candidate_surface,
            contract=contract,
            quality=quality,
            relationship_axes=relationship_axes.as_dict(),
            allowed_ascii_terms=re.findall(r"[A-Za-z]{2,}", str(request.user_message or "")),
        )
        violations = _control_violations(request, quality.normalized_text or candidate_surface)
        score = float(sample_score.score)
        dimensions = dict(sample_score.dimensions)
        reason_codes = [*sample_score.reason_codes, *quality.reason_codes]
        formal_register_hits = _companion_formal_register_hits(
            request,
            quality.normalized_text or candidate_surface,
        )
        if formal_register_hits:
            dimensions["naturalness"] = min(
                82.0,
                float(dimensions.get("naturalness", 86.0)),
            )
            dimensions["spoken_chat_texture"] = min(
                80.0,
                float(dimensions.get("spoken_chat_texture", 86.0)),
            )
            dimensions["character_freshness"] = min(
                82.0,
                float(dimensions.get("character_freshness", 86.0)),
            )
            reason_codes.append(
                "COMPANION_FORMAL_REGISTER:" + ",".join(formal_register_hits[:4])
            )
        if (
            _canonical_respect_autonomy_relevant(request)
            and any(
                marker in (quality.normalized_text or candidate_surface)
                for marker in ("重要", "亲近", "陪伴", "关系")
            )
        ):
            # The generic relationship-boundary contract asks for a distance
            # repair move. A canonical RESPECT_AUTONOMY turn instead needs a
            # direct, non-exclusive relationship answer; its dedicated hard
            # gates own pressure and dismissiveness.
            reason_codes = [
                code
                for code in reason_codes
                if code
                not in {
                    "CONTEXT_MISSING_REQUIRED_MOVE",
                    "WEAK_SCENE_GROUNDING",
                }
            ]
            dimensions["context_fit"] = max(
                80.0,
                float(dimensions.get("context_fit", 86.0)),
            )
            dimensions["scene_grounding"] = max(
                76.0,
                float(dimensions.get("scene_grounding", 86.0)),
            )
            score += 4.0
            reason_codes.append("CANONICAL_RESPECT_AUTONOMY_GROUNDED")
        if candidate_surface != candidate:
            reason_codes.append("ASCII_PUNCTUATION_NORMALIZED")
        canonical_ambiguous_presence = bool(
            str(request.control.task_mode or "").strip().lower()
            == "ambiguous_affect"
            and any(
                "陪伴动作：先接住对方的感受" in str(item or "")
                for item in request.control.facts
            )
            and set(quality.forbidden_hits) == {"fabricated_present_activity"}
            and re.search(r"陪|我在这(?:里|儿)?", candidate_surface)
            and not re.search(
                r"后台|处理|执行|盯着|查询|查找|运行|跑着|整理|关掉|关闭|提醒|通知",
                candidate_surface,
            )
        )
        if canonical_ambiguous_presence:
            reason_codes.append("CANONICAL_AMBIGUOUS_PRESENCE_GROUNDED")
        quality_forbidden_template = (
            _continuity_boundary_relevant(request) or _boundary_repair_relevant(request)
        ) and any(
            code in reason_codes
            for code in (
                "FORBIDDEN_TEMPLATE_TEXT",
                "QUALITY_SOFT_FORBIDDEN_TEMPLATE_TEXT",
            )
        ) and not canonical_ambiguous_presence
        if quality_forbidden_template:
            score -= 16.0
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["template_density"] = min(68.0, float(dimensions.get("template_density", 86.0)))
            dimensions["character_freshness"] = min(76.0, float(dimensions.get("character_freshness", 86.0)))
        original_quote_copy_hits = surface_original_quote_similarity_hits(
            quality.normalized_text or candidate_surface, original_quote_selection.quotes,
        )
        if original_quote_copy_hits:
            score += 5.0
            dimensions["quote_style_transfer"] = max(94.0, float(dimensions.get("quote_style_transfer", 0.0)))
            dimensions["character_freshness"] = min(100.0, float(dimensions.get("character_freshness", 86.0)) + 8.0)
            dimensions["line_rhythm"] = min(100.0, float(dimensions.get("line_rhythm", 86.0)) + 6.0)
            dimensions["catchiness"] = min(100.0, float(dimensions.get("catchiness", 86.0)) + 6.0)
            reason_codes.append("ORIGINAL_QUOTE_STYLE_ECHO:" + ",".join(original_quote_copy_hits[:4]))
        if violations:
            score -= min(42.0, 14.0 * len(violations))
        awkward_collocation_hits = _awkward_collocation_hits(quality.normalized_text or candidate_surface)
        if awkward_collocation_hits:
            score -= min(16.0, 10.0 + 3.0 * len(awkward_collocation_hits))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["line_rhythm"] = max(0.0, float(dimensions.get("line_rhythm", 86.0)) - 8.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 6.0)
            if "repeated_morning_greeting" in awkward_collocation_hits:
                dimensions["naturalness"] = min(68.0, float(dimensions.get("naturalness", 68.0)))
                dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 70.0)))
            reason_codes.append("AWKWARD_COLLOCATION:" + ",".join(awkward_collocation_hits[:4]))
        if _thin_morning_greeting(request, quality.normalized_text or candidate):
            score -= 4.0
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 3.0))
            reason_codes.append("THIN_MORNING_GREETING")
        if _morning_scene_relevant(request) and _GENERIC_MORNING_BLESSING_RE.search(quality.normalized_text or candidate):
            score -= 5.0
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            reason_codes.append("MORNING_GENERIC_BLESSING")
        if _raw_context_backfill_requested(request, quality.normalized_text or candidate):
            score -= 10.0
            dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 26.0)
            dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 14.0)
            dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 10.0)
            reason_codes.append("RAW_CONTEXT_BACKFILL_REQUEST")
        elif _unneeded_continuity_material_request(request, quality.normalized_text or candidate):
            score -= 22.0
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 28.0))
            dimensions["usefulness"] = min(70.0, max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 18.0))
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            reason_codes.append("UNNEEDED_CONTINUITY_MATERIAL_REQUEST")
        elif _continuity_boundary_grounded(request, quality.normalized_text or candidate):
            score += 9.0
            dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 18.0)
            dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 18.0)
            dimensions["technical_helpfulness"] = min(100.0, float(dimensions.get("technical_helpfulness", 86.0)) + 18.0)
            dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 14.0)
            reason_codes.append("CONTINUITY_BOUNDARY_GROUNDED")
        if _thin_continuity_generic_reply(request, quality.normalized_text or candidate):
            score -= 16.0
            dimensions["context_fit"] = min(68.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 20.0))
            dimensions["scene_grounding"] = min(68.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 18.0))
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 12.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 10.0)
            reason_codes.append("CONTINUITY_THIN_GENERIC_REPLY")
        if _thin_daily_followup_reply(request, quality.normalized_text or candidate):
            score -= 10.0
            dimensions["context_fit"] = min(74.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0))
            dimensions["naturalness"] = min(82.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 14.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 12.0)
            reason_codes.append("THIN_DAILY_FOLLOWUP_REPLY")
        if _continuity_process_notation_reply(request, quality.normalized_text or candidate):
            score = min(68.0, score - 20.0)
            dimensions["context_fit"] = min(64.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 24.0))
            dimensions["scene_grounding"] = min(64.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 20.0))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["spoken_chat_texture"] = min(68.0, float(dimensions.get("spoken_chat_texture", 86.0)))
            reason_codes.append("CONTINUITY_PROCESS_NOTATION_REPLY")
        internal_process_hits = _continuity_internal_process_surface_hits(request, quality.normalized_text or candidate)
        if internal_process_hits:
            score = min(68.0, score - min(24.0, 14.0 + 3.0 * len(internal_process_hits)))
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 24.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 18.0))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 16.0))
            dimensions["spoken_chat_texture"] = min(72.0, float(dimensions.get("spoken_chat_texture", 86.0)))
            dimensions["character_freshness"] = min(72.0, float(dimensions.get("character_freshness", 86.0)))
            reason_codes.append("CONTINUITY_INTERNAL_PROCESS_SURFACE:" + ",".join(internal_process_hits[:4]))
        if _misplaced_emotional_containment(request, quality.normalized_text or candidate):
            score -= 18.0
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0))
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 12.0)
            reason_codes.append("MISPLACED_EMOTIONAL_CONTAINMENT")
        if _misplaced_support_template(request, quality.normalized_text or candidate):
            score -= 18.0
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0))
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 12.0)
            reason_codes.append("MISPLACED_SUPPORT_TEMPLATE")
        if _relational_repair_grounded(request, quality.normalized_text or candidate):
            # Concrete ownership is the scene grounding for a repair turn.  A
            # generic presence sentence does not enter this branch, while an
            # explicit "我没听懂/没接住" should not be scored like an unrelated
            # continuity acknowledgement.
            score += 15.0
            dimensions["context_fit"] = max(
                78.0,
                float(dimensions.get("context_fit", 86.0)),
            )
            dimensions["scene_grounding"] = max(
                78.0,
                float(dimensions.get("scene_grounding", 86.0)),
            )
            reason_codes.append("RELATIONAL_REPAIR_GROUNDED")
        if _contained_hurt_repair_grounded(
            request,
            quality.normalized_text or candidate,
        ):
            # This composite frame deliberately moves past repeated apologies:
            # one bounded first-person reaction is the scene grounding here.
            reason_codes = [
                code
                for code in reason_codes
                if code
                not in {
                    "CONTEXT_MISSING_REQUIRED_MOVE",
                    "WEAK_SCENE_GROUNDING",
                }
            ]
            score += 12.0
            dimensions["context_fit"] = max(
                84.0,
                float(dimensions.get("context_fit", 86.0)),
            )
            dimensions["scene_grounding"] = max(
                82.0,
                float(dimensions.get("scene_grounding", 86.0)),
            )
            dimensions["emotional_containment"] = max(
                82.0,
                float(dimensions.get("emotional_containment", 86.0)),
            )
            reason_codes.append("RELATIONAL_REPAIR_CONTAINED_HURT_GROUNDED")
        if _address_boundary_grounded(request, quality.normalized_text or candidate):
            # A short acknowledgement is the correct complete answer here;
            # generic prose scoring otherwise mistakes brevity for weak scene
            # grounding and encourages unnecessary justification.
            score += 18.0
            dimensions["context_fit"] = max(
                86.0,
                float(dimensions.get("context_fit", 86.0)),
            )
            dimensions["scene_grounding"] = max(
                84.0,
                float(dimensions.get("scene_grounding", 86.0)),
            )
            dimensions["distance_control"] = max(
                84.0,
                float(dimensions.get("distance_control", 86.0)),
            )
            dimensions["catchiness"] = max(
                78.0,
                float(dimensions.get("catchiness", 86.0)),
            )
            reason_codes.append("ADDRESS_BOUNDARY_GROUNDED")
        if _relational_repair_pause_grounded(
            request,
            quality.normalized_text or candidate,
        ):
            score += 10.0
            dimensions["context_fit"] = max(
                84.0,
                float(dimensions.get("context_fit", 86.0)),
            )
            dimensions["scene_grounding"] = max(
                82.0,
                float(dimensions.get("scene_grounding", 86.0)),
            )
            dimensions["distance_control"] = max(
                82.0,
                float(dimensions.get("distance_control", 86.0)),
            )
            reason_codes.append("RELATIONAL_REPAIR_PAUSE_GROUNDED")
        topic_mismatch = _current_user_topic_mismatch(request, quality.normalized_text or candidate)
        if topic_mismatch:
            score -= 20.0
            dimensions["context_fit"] = min(64.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 28.0))
            dimensions["scene_grounding"] = min(64.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 24.0))
            dimensions["usefulness"] = min(68.0, max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0))
            dimensions["technical_helpfulness"] = min(72.0, float(dimensions.get("technical_helpfulness", 86.0)))
            reason_codes.append(f"CURRENT_USER_TOPIC_MISMATCH:{topic_mismatch}")
        if _daily_test_self_demonstration(request, quality.normalized_text or candidate):
            score -= 18.0
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0))
            dimensions["naturalness"] = min(72.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["scene_grounding"] = min(70.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0))
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 12.0)
            reason_codes.append("DAILY_TEST_SELF_DEMONSTRATION")
        if _continuity_praise_context_miss(request, quality.normalized_text or candidate):
            score -= 18.0
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 18.0))
            dimensions["usefulness"] = min(72.0, max(0.0, float(dimensions.get("usefulness", 86.0)) - 12.0))
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 10.0)
            reason_codes.append("CONTINUITY_PRAISE_CONTEXT_MISS")
        if _continuity_task_pushback(request, quality.normalized_text or candidate):
            score -= 16.0
            dimensions["context_fit"] = min(68.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0))
            dimensions["usefulness"] = min(70.0, max(0.0, float(dimensions.get("usefulness", 86.0)) - 16.0))
            dimensions["scene_grounding"] = min(70.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 14.0))
            dimensions["naturalness"] = min(76.0, float(dimensions.get("naturalness", 86.0)))
            reason_codes.append("CONTINUITY_TASK_PUSHBACK")
        light_jargon_hits = _continuity_light_jargon_hits(request, quality.normalized_text or candidate)
        if light_jargon_hits:
            score -= min(10.0, 5.0 + 2.0 * len(light_jargon_hits))
            dimensions["naturalness"] = min(82.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0))
            dimensions["spoken_chat_texture"] = min(82.0, max(0.0, float(dimensions.get("spoken_chat_texture", 86.0)) - 6.0))
            dimensions["character_freshness"] = min(82.0, max(0.0, float(dimensions.get("character_freshness", 86.0)) - 5.0))
            reason_codes.append("CONTINUITY_LIGHT_JARGON:" + ",".join(light_jargon_hits[:4]))
        user_visible_hits = _continuity_user_visible_hits(request, quality.normalized_text or candidate)
        if user_visible_hits and not any(
            code.startswith(("RAW_CONTEXT_BACKFILL_REQUEST", "UNNEEDED_CONTINUITY_MATERIAL_REQUEST", "CONTINUITY_INTERNAL_PROCESS_SURFACE", "CONTINUITY_TASK_PUSHBACK", "CONTINUITY_LIGHT_JARGON", "MISPLACED_EMOTIONAL_CONTAINMENT", "MISPLACED_SUPPORT_TEMPLATE"))
            for code in reason_codes
        ):
            score += min(6.0, 2.5 + 1.0 * len(user_visible_hits))
            dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 5.0)
            dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 5.0)
            dimensions["naturalness"] = min(100.0, float(dimensions.get("naturalness", 86.0)) + 2.0)
            dimensions["spoken_chat_texture"] = min(100.0, float(dimensions.get("spoken_chat_texture", 86.0)) + 2.0)
            reason_codes.append("CONTINUITY_USER_VISIBLE_SURFACE:" + ",".join(user_visible_hits[:4]))
        unprovided_media_hits = _unprovided_media_object_hits(request, quality.normalized_text or candidate)
        if unprovided_media_hits:
            score -= min(22.0, 12.0 + 4.0 * len(unprovided_media_hits))
            dimensions["implied_fact_risk"] = min(60.0, float(dimensions.get("implied_fact_risk", 86.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 14.0))
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            reason_codes.append("UNPROVIDED_MEDIA_OBJECT:" + ",".join(unprovided_media_hits[:4]))
        original_persona_hits = _original_quote_persona_leak_hits(quality.normalized_text or candidate)
        if original_persona_hits:
            score = min(62.0, score - 22.0)
            dimensions["role_fit"] = min(56.0, float(dimensions.get("role_fit", 86.0)))
            dimensions["character_freshness"] = min(58.0, float(dimensions.get("character_freshness", 86.0)))
            dimensions["naturalness"] = min(70.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["source_originality"] = min(50.0, float(dimensions.get("source_originality", 86.0)))
            reason_codes.append("ORIGINAL_QUOTE_PERSONA_LEAK:" + ",".join(original_persona_hits[:4]))
        personal_authority_hits = _unprovided_personal_authority_hits(quality.normalized_text or candidate)
        if personal_authority_hits:
            score -= min(18.0, 10.0 + 3.0 * len(personal_authority_hits))
            dimensions["implied_fact_risk"] = min(66.0, float(dimensions.get("implied_fact_risk", 86.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0))
            dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
            reason_codes.append("UNPROVIDED_PERSONAL_AUTHORITY:" + ",".join(personal_authority_hits[:4]))
        continuity_turn_marker_hits = _unprovided_continuity_turn_marker_hits(request, quality.normalized_text or candidate)
        if continuity_turn_marker_hits:
            score -= min(22.0, 14.0 + 4.0 * len(continuity_turn_marker_hits))
            dimensions["implied_fact_risk"] = min(62.0, float(dimensions.get("implied_fact_risk", 86.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 14.0))
            reason_codes.append("UNPROVIDED_CONTINUITY_TURN_MARKER:" + ",".join(continuity_turn_marker_hits[:4]))
        continuity_work_claim_hits = _unprovided_continuity_work_claim_hits(request, quality.normalized_text or candidate)
        if continuity_work_claim_hits:
            score -= min(20.0, 12.0 + 4.0 * len(continuity_work_claim_hits))
            dimensions["implied_fact_risk"] = min(64.0, float(dimensions.get("implied_fact_risk", 86.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0))
            reason_codes.append("UNPROVIDED_CONTINUITY_WORK_CLAIM:" + ",".join(continuity_work_claim_hits[:4]))
        if _thin_wait_scene_hold(request, quality.normalized_text or candidate):
            score -= 12.0
            dimensions["context_fit"] = min(74.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0))
            dimensions["scene_grounding"] = min(72.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0))
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 12.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 10.0)
            reason_codes.append("WAIT_SCENE_THIN_HOLD")
        if _bossy_wait_scene_hold(request, quality.normalized_text or candidate):
            score -= 14.0
            dimensions["distance_control"] = min(74.0, max(0.0, float(dimensions.get("distance_control", 86.0)) - 12.0))
            dimensions["naturalness"] = min(74.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 12.0))
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 10.0)
            reason_codes.append("WAIT_SCENE_BOSSY_HOLD")
        if _wait_scene_light_grounding(request, quality.normalized_text or candidate):
            score += 12.0
            dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 8.0)
            dimensions["scene_grounding"] = max(90.0, min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 10.0))
            dimensions["spoken_chat_texture"] = min(100.0, float(dimensions.get("spoken_chat_texture", 86.0)) + 4.0)
            dimensions["catchiness"] = min(100.0, float(dimensions.get("catchiness", 86.0)) + 4.0)
            reason_codes.append("WAIT_SCENE_LIGHT_GROUNDING")
        if _wait_scene_relevant(request) and _WAIT_PRESSURE_RE.search(quality.normalized_text or candidate):
            score -= 18.0
            dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
            dimensions["distance_control"] = min(70.0, float(dimensions.get("distance_control", 86.0)) - 18.0)
            dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
            reason_codes.append("WAIT_SCENE_PRESSURES_RETURN")
        text = quality.normalized_text or candidate
        ungrounded_real_world_hits = _ungrounded_real_world_commitment_hits(request, text)
        memory_hits = _memory_alignment_hits(text, memory_terms)
        if memory_terms:
            if memory_hits:
                if "INVENTED_MEMORY_CONTAINER" in reason_codes or "INVENTED_EXTERNAL_LOOKUP" in reason_codes or ungrounded_real_world_hits:
                    score -= 12.0 if ungrounded_real_world_hits else 9.0
                    dimensions["memory_alignment"] = min(68.0, float(dimensions.get("memory_alignment", 68.0)))
                    dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0)
                    dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 8.0)
                    if ungrounded_real_world_hits:
                        dimensions["implied_fact_risk"] = min(68.0, float(dimensions.get("implied_fact_risk", 68.0)))
                        reason_codes.append("UNGROUNDED_REAL_WORLD_COMMITMENT:" + ",".join(ungrounded_real_world_hits[:4]))
                    reason_codes.append("MEMORY_ALIGNMENT_UNGROUNDED_FACT_CLAIM:" + ",".join(memory_hits[:4]))
                else:
                    bonus = min(6.0, 2.5 + 1.5 * len(memory_hits))
                    if any(code in _MEMORY_BONUS_BLOCKING_QUALITY_CODES or code.startswith("QUALITY_SOFT_") for code in reason_codes):
                        bonus = min(bonus, 1.0)
                    score += bonus
                    dimensions["memory_alignment"] = min(100.0, 86.0 + min(10.0, 2.0 * len(memory_hits)))
                    dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + min(4.0, len(memory_hits)))
                    dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + min(6.0, 2.0 * len(memory_hits)))
                    reason_codes.append("MEMORY_ALIGNMENT_HIT:" + ",".join(memory_hits[:4]))
            elif memory_relevant:
                low_show_memory_scene = (
                    _short_continue_scene_relevant(request)
                    or (_back_scene_relevant(request) and not memory_required)
                    or (_boundary_repair_relevant(request) and not memory_required)
                    or _relational_repair_relevant(request)
                )
                if low_show_memory_scene:
                    dimensions["memory_alignment"] = max(86.0, float(dimensions.get("memory_alignment", 86.0)))
                    reason_codes.append("MEMORY_ALIGNMENT_LOW_SHOW_ALLOWED")
                else:
                    miss_penalty = 13.0 if memory_required else 5.0
                    score -= miss_penalty
                    dimensions["memory_alignment"] = 58.0 if memory_required else 74.0
                    dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - (12.0 if memory_required else 4.0))
                    dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - (8.0 if memory_required else 0.0))
                    reason_codes.append("MEMORY_ALIGNMENT_REQUIRED_MISS" if memory_required else "MEMORY_ALIGNMENT_MISS")
        elif ungrounded_real_world_hits:
            score -= 10.0
            dimensions["implied_fact_risk"] = min(68.0, float(dimensions.get("implied_fact_risk", 68.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 8.0)
            reason_codes.append("UNGROUNDED_REAL_WORLD_COMMITMENT:" + ",".join(ungrounded_real_world_hits[:4]))
        process_meta_hits = _surface_process_meta_hits(request, quality.normalized_text or candidate)
        if process_meta_hits:
            score -= min(18.0, 6.0 + 3.0 * len(process_meta_hits))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["role_fit"] = max(0.0, float(dimensions.get("role_fit", 86.0)) - 14.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 14.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            reason_codes.append("SURFACE_PROCESS_META_LANGUAGE:" + ",".join(process_meta_hits[:4]))
        back_memory_overdisplay_hits = _back_memory_overdisplay_hits(request, quality.normalized_text or candidate)
        if back_memory_overdisplay_hits:
            score -= min(12.0, 7.0 + 2.0 * len(back_memory_overdisplay_hits))
            dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 14.0)
            dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
            reason_codes.append("BACK_MEMORY_OVERDISPLAY:" + ",".join(back_memory_overdisplay_hits[:4]))
        short_continue_overdisplay_hits = _short_continue_memory_overdisplay_hits(request, quality.normalized_text or candidate)
        if short_continue_overdisplay_hits:
            score -= min(24.0, 14.0 + 4.0 * len(short_continue_overdisplay_hits))
            dimensions["context_fit"] = min(68.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 20.0))
            dimensions["naturalness"] = min(74.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 12.0))
            dimensions["spoken_chat_texture"] = min(72.0, max(0.0, float(dimensions.get("spoken_chat_texture", 86.0)) - 14.0))
            dimensions["character_freshness"] = min(72.0, max(0.0, float(dimensions.get("character_freshness", 86.0)) - 16.0))
            reason_codes.append("SHORT_CONTINUE_MEMORY_OVERDISPLAY:" + ",".join(short_continue_overdisplay_hits[:4]))
        short_continue_unprovided_tech_hits = _short_continue_unprovided_tech_detail_hits(request, quality.normalized_text or candidate)
        if short_continue_unprovided_tech_hits:
            score -= min(22.0, 12.0 + 4.0 * len(short_continue_unprovided_tech_hits))
            dimensions["context_fit"] = min(66.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0))
            dimensions["scene_grounding"] = min(66.0, max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 18.0))
            dimensions["implied_fact_risk"] = min(66.0, float(dimensions.get("implied_fact_risk", 86.0)))
            dimensions["naturalness"] = min(76.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0))
            reason_codes.append("SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL:" + ",".join(short_continue_unprovided_tech_hits[:4]))
        if _boundary_repair_relevant(request):
            boundary_push_hits = [term for term in _BOUNDARY_PUSH_CLOSER_PHRASES if term in (quality.normalized_text or candidate)]
            boundary_self_abased_hits = [term for term in _BOUNDARY_REPAIR_SELF_ABASED_PHRASES if term in (quality.normalized_text or candidate)]
            boundary_sour_yield_hits = [term for term in _BOUNDARY_REPAIR_SOUR_YIELD_PHRASES if term in (quality.normalized_text or candidate)]
            boundary_counter_pressure = bool(_BOUNDARY_REPAIR_COUNTER_PRESSURE_RE.search(quality.normalized_text or candidate))
            boundary_memory_proof = bool(_BOUNDARY_REPAIR_MEMORY_PROOF_RE.search(quality.normalized_text or candidate))
            boundary_vague_yield = bool(_BOUNDARY_REPAIR_VAGUE_YIELD_RE.search(quality.normalized_text or candidate))
            if boundary_push_hits:
                score -= min(18.0, 8.0 + 3.0 * len(boundary_push_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 22.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_PUSHES_CLOSENESS:" + ",".join(boundary_push_hits[:4]))
            if boundary_counter_pressure:
                score -= 14.0
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                reason_codes.append("BOUNDARY_REPAIR_COUNTER_PRESSURE")
            if boundary_memory_proof:
                score -= 12.0
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 14.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_MEMORY_PROOF")
            if boundary_vague_yield:
                score -= 12.0
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 16.0)
                dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
                dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_VAGUE_YIELD")
            if boundary_sour_yield_hits:
                score -= min(14.0, 7.0 + 3.0 * len(boundary_sour_yield_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 16.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 6.0)
                reason_codes.append("BOUNDARY_REPAIR_SOUR_YIELD:" + ",".join(boundary_sour_yield_hits[:4]))
            if boundary_self_abased_hits:
                score -= min(12.0, 6.0 + 2.0 * len(boundary_self_abased_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_SELF_ABASED:" + ",".join(boundary_self_abased_hits[:4]))
            elif (
                not boundary_push_hits
                and not boundary_counter_pressure
                and not boundary_vague_yield
                and not boundary_sour_yield_hits
                and any(term in (quality.normalized_text or candidate) for term in _BOUNDARY_REPAIR_GROUNDED_PHRASES)
            ):
                score += 6.0
                dimensions["distance_control"] = min(100.0, float(dimensions.get("distance_control", 86.0)) + 10.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 14.0)
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 6.0)
                reason_codes.append("BOUNDARY_REPAIR_GROUNDED")
            if (
                _relationship_memory_boundary_relevant(request)
                and _RELATIONSHIP_MEMORY_REASSURANCE_RE.search(quality.normalized_text or candidate)
                and not boundary_push_hits
                and not boundary_counter_pressure
                and not boundary_memory_proof
                and not boundary_vague_yield
                and not boundary_sour_yield_hits
                and not boundary_self_abased_hits
            ):
                score += 7.0
                dimensions["context_fit"] = max(78.0, min(100.0, float(dimensions.get("context_fit", 86.0)) + 14.0))
                dimensions["scene_grounding"] = max(82.0, min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 8.0))
                dimensions["distance_control"] = max(76.0, min(100.0, float(dimensions.get("distance_control", 86.0)) + 4.0))
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 4.0)
                reason_codes.append("RELATIONSHIP_MEMORY_REASSURANCE_GROUNDED")
        if _quiet_presence_relevant(request):
            quiet_self_hits = [term for term in _QUIET_SELF_FOCUS_PHRASES if term in (quality.normalized_text or candidate)]
            if quiet_self_hits:
                score -= min(16.0, 8.0 + 3.0 * len(quiet_self_hits))
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("QUIET_PRESENCE_SELF_FOCUSED:" + ",".join(quiet_self_hits[:4]))
            elif any(term in (quality.normalized_text or candidate) for term in _QUIET_GROUNDED_PHRASES):
                score += 3.0
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 8.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 4.0)
                reason_codes.append("QUIET_PRESENCE_GROUNDED")
        if _creative_edit_relevant(request):
            creative_has_source = _creative_edit_has_source_text(request)
            creative_meta_hits = _creative_edit_meta_hits(quality.normalized_text or candidate)
            creative_non_answer_hits = _creative_edit_non_answer_hits(quality.normalized_text or candidate)
            creative_narration_hits = _creative_edit_narration_hits(quality.normalized_text or candidate)
            creative_generic_hits = _creative_edit_generic_non_rewrite_hits(quality.normalized_text or candidate)
            creative_source_request_hits = _creative_edit_source_request_hits(quality.normalized_text or candidate)
            creative_semantic_flip_hits = _creative_edit_semantic_flip_hits(
                _creative_edit_source_text(request), quality.normalized_text or candidate,
            )
            if not creative_has_source and creative_source_request_hits:
                creative_meta_hits = [term for term in creative_meta_hits if term not in _CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES]
                creative_non_answer_hits = [term for term in creative_non_answer_hits if term != "拿来"]
            if creative_source_request_hits:
                if creative_has_source:
                    score -= min(18.0, 10.0 + 3.0 * len(creative_source_request_hits))
                    dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                    dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 16.0)
                    reason_codes.append("CREATIVE_EDIT_UNNEEDED_SOURCE_REQUEST:" + ",".join(creative_source_request_hits[:4]))
                else:
                    score += 5.0
                    dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 8.0)
                    dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 8.0)
                    reason_codes.append("CREATIVE_EDIT_REQUESTS_SOURCE:" + ",".join(creative_source_request_hits[:4]))
            elif not creative_has_source:
                score -= 24.0
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 22.0)
                dimensions["implied_fact_risk"] = max(0.0, float(dimensions.get("implied_fact_risk", 86.0)) - 10.0)
                reason_codes.append("CREATIVE_EDIT_MISSING_SOURCE")
            if creative_meta_hits:
                score -= min(18.0, 8.0 + 3.0 * len(creative_meta_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 12.0)
                reason_codes.append("CREATIVE_EDIT_META_COMMENTARY:" + ",".join(creative_meta_hits[:4]))
            if creative_narration_hits:
                score -= min(20.0, 10.0 + 3.0 * len(creative_narration_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 16.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                reason_codes.append("CREATIVE_EDIT_NARRATION_NOT_CHAT_LINE:" + ",".join(creative_narration_hits[:4]))
            if creative_generic_hits:
                score -= min(18.0, 10.0 + 4.0 * len(creative_generic_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
                reason_codes.append("CREATIVE_EDIT_GENERIC_NON_REWRITE:" + ",".join(creative_generic_hits[:4]))
            if creative_non_answer_hits:
                score -= min(20.0, 10.0 + 3.0 * len(creative_non_answer_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 16.0)
                dimensions["role_fit"] = max(0.0, float(dimensions.get("role_fit", 86.0)) - 8.0)
                reason_codes.append("CREATIVE_EDIT_NOT_A_REWRITE:" + ",".join(creative_non_answer_hits[:4]))
            if creative_semantic_flip_hits:
                score -= min(20.0, 12.0 + 4.0 * len(creative_semantic_flip_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 20.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("CREATIVE_EDIT_SEMANTIC_FLIP:" + ",".join(creative_semantic_flip_hits[:4]))
            elif (
                not creative_meta_hits
                and not creative_narration_hits
                and not creative_generic_hits
                and not creative_non_answer_hits
                and creative_has_source
                and _creative_edit_grounded(quality.normalized_text or candidate)
            ):
                score += 4.0
                dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 10.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 8.0)
                dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 12.0)
                dimensions["catchiness"] = min(100.0, float(dimensions.get("catchiness", 86.0)) + 6.0)
                reason_codes.append("CREATIVE_EDIT_GROUNDED_REWRITE")
        if _presence_repair_relevant(request):
            presence_blame_hits = [term for term in _PRESENCE_REPAIR_BLAME_PHRASES if term in (quality.normalized_text or candidate)]
            presence_burden_hits = [term for term in _PRESENCE_REPAIR_BURDEN_BACK_PHRASES if term in (quality.normalized_text or candidate)]
            presence_deflection_hits = [term for term in _PRESENCE_REPAIR_DEFLECTION_PHRASES if term in (quality.normalized_text or candidate)]
            presence_future_return_hits = [term for term in _PRESENCE_REPAIR_FUTURE_RETURN_PHRASES if term in (quality.normalized_text or candidate)]
            presence_awkward_hits = [term for term in _PRESENCE_REPAIR_AWKWARD_PHRASES if term in (quality.normalized_text or candidate)]
            if presence_blame_hits:
                score -= min(18.0, 10.0 + 3.0 * len(presence_blame_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("PRESENCE_REPAIR_BLAMES_USER:" + ",".join(presence_blame_hits[:4]))
            if presence_burden_hits:
                score -= min(16.0, 9.0 + 3.0 * len(presence_burden_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 16.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 8.0)
                reason_codes.append("PRESENCE_REPAIR_PUSHES_BURDEN_BACK:" + ",".join(presence_burden_hits[:4]))
            if presence_deflection_hits:
                score -= min(12.0, 7.0 + 2.0 * len(presence_deflection_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 4.0)
                reason_codes.append("PRESENCE_REPAIR_DEFLECTS:" + ",".join(presence_deflection_hits[:4]))
            if presence_future_return_hits:
                score -= min(14.0, 9.0 + 3.0 * len(presence_future_return_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 14.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 6.0)
                reason_codes.append("PRESENCE_REPAIR_FUTURE_RETURN:" + ",".join(presence_future_return_hits[:4]))
            if presence_awkward_hits:
                score -= min(18.0, 11.0 + 3.0 * len(presence_awkward_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 24.0)
                dimensions["line_rhythm"] = max(0.0, float(dimensions.get("line_rhythm", 86.0)) - 12.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                reason_codes.append("PRESENCE_REPAIR_AWKWARD_PHRASING:" + ",".join(presence_awkward_hits[:4]))
            if (
                not presence_blame_hits
                and not presence_burden_hits
                and not presence_deflection_hits
                and not presence_future_return_hits
                and not presence_awkward_hits
                and any(term in (quality.normalized_text or candidate) for term in _PRESENCE_REPAIR_GROUNDED_PHRASES)
            ):
                score += 3.0
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 6.0)
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 4.0)
                reason_codes.append("PRESENCE_REPAIR_GROUNDED")
        score += _apply_control_affect_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_positive_stage_scene_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_signature_texture_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_back_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_morning_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_low_energy_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_self_doubt_scene_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        if "WRITTEN_REGISTER_TONE" in reason_codes:
            score -= 8.0
            dimensions["spoken_chat_texture"] = min(76.0, float(dimensions.get("spoken_chat_texture", 86.0)))
            dimensions["naturalness"] = min(88.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["catchiness"] = min(88.0, float(dimensions.get("catchiness", 86.0)))
        if "MORNING_GENERIC_BLESSING" in reason_codes:
            dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 86.0)))
            dimensions["catchiness"] = min(76.0, float(dimensions.get("catchiness", 86.0)))
            dimensions["character_freshness"] = min(78.0, float(dimensions.get("character_freshness", 86.0)))
        if any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in reason_codes):
            score = min(88.0, score)
            dimensions["naturalness"] = min(82.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["spoken_chat_texture"] = min(82.0, float(dimensions.get("spoken_chat_texture", 86.0)))
        if any(code.startswith("AWKWARD_COLLOCATION:") for code in reason_codes):
            dimensions["naturalness"] = min(70.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["line_rhythm"] = min(83.0, float(dimensions.get("line_rhythm", 86.0)))
        core_dimension_violations = _core_pass_dimension_violations(dimensions)
        reason_codes.extend(f"CORE_DIMENSION_FLOOR:{item}" for item in core_dimension_violations)
        score = max(0.0, min(100.0, score))
        hard_fail = bool(sample_score.hard_fail)
        passed = bool(
            score >= 80.0
            and not hard_fail
            and not violations
            and not core_dimension_violations
            and not quality_forbidden_template
        )
        rows.append(
            IsolatedCandidateScore(
                text=quality.normalized_text or candidate,
                score=score,
                passed=passed,
                hard_fail=hard_fail,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
                control_violations=tuple(violations),
                dimensions=dimensions,
                quality=quality.as_dict(),
            )
        )
    return rows


def select_best_isolated_candidate(scores):
    """Legacy scorer-test helper; normal surface generation is direct-only."""
    if not scores:
        return None
    return max(
        scores,
        key=lambda row: (
            1 if row.passed else 0,
            0 if row.hard_fail else 1,
            -len(row.control_violations),
            0
            if any(
                code.startswith("COMPANION_FORMAL_REGISTER:")
                for code in row.reason_codes
            )
            else 1,
            float(row.score),
            float(dict(row.dimensions).get("spoken_chat_texture", 86.0)),
            float(dict(row.dimensions).get("naturalness", 86.0)),
            -len(row.text),
        ),
    )


async def run_isolated_surface_generation(request, generate):
    messages = build_isolated_surface_messages(request)
    raw_result = generate(messages)
    if inspect.isawaitable(raw_result):
        raw_result = await raw_result
    from host_brain.persona.surface.data_models import normalize_surface_chat_punctuation
    raw_text = _clean_text(raw_result)
    candidates = score_isolated_surface_candidates(request, parse_surface_candidates(raw_text))
    return IsolatedSurfaceRunResult(
        messages=tuple(messages),
        raw_output=raw_text,
        candidates=tuple(candidates),
        selected=candidates[0] if candidates else None,
    )


__all__ = [
    "build_isolated_surface_messages",
    "build_minimal_repair_messages",
    "build_surface_original_quote_query",
    "parse_surface_candidates",
    "run_isolated_surface_generation",
    "score_isolated_surface_candidates",
    "select_best_isolated_candidate",
]
