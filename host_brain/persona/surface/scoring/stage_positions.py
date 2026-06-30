"""Position scoring functions for candidate_scoring.

Extracted from candidate_scoring.py — covers back, morning, low-energy,
self-doubt, and positive scene position scoring logic.
"""
from __future__ import annotations

import re

from host_brain.persona.surface.util import _clean_text
from host_brain.persona.surface.continuity_detectors import (
    _back_busy_done_relevant,
    _back_scene_relevant,
    _low_energy_scene_relevant,
    _morning_scene_relevant,
    _signature_texture_relevant,
)
from host_brain.persona.surface.relationship_affect import (
    relationship_control_axes_for_distance as _relationship_control_axes_for_distance,
    _relationship_style_profile,
)
from host_brain.persona.surface.scene_constants import (
    _BACK_BUSY_DONE_LANDING_MARKERS,
    _BACK_EXPLICIT_LANDING_MARKERS,
    _BACK_STAGE_TEXTURE_MARKERS,
    _BACK_STAGELESS_FAMILIAR_FORMULA_RE,
    _BACK_STAGELESS_SAFE_RE,
    _BACK_UNGROUNDED_RETURN_ATTITUDE_RE,
    _BACK_UNGROUNDED_ROUTE_RE,
    _LOW_ENERGY_OVERHARSH_TEASE_PHRASES,
    _LOW_ENERGY_STAGE_MARKERS,
    _LOW_ENERGY_STAGELESS_GENERIC_RE,
    _MORNING_STAGE_TEXTURE_MARKERS,
    _MORNING_STAGELESS_RUSH_FORMULA_RE,
    _POSITIVE_BACK_MARKERS,
    _POSITIVE_FAILURE_ACTION_MARKERS,
    _POSITIVE_FAILURE_CONTAIN_MARKERS,
    _POSITIVE_MORNING_GENTLE_MARKERS,
    _POSITIVE_RECALL_MARKERS,
    _POSITIVE_TIRED_MARKERS,
    _SELF_DOUBT_BAD_DEFERRAL_PHRASES,
    _SELF_DOUBT_DEMEANING_PHRASES,
    _SELF_DOUBT_SMALL_STEP_MARKERS,
    _SELF_DOUBT_UNGROUNDED_TECH_TERMS,
    _THIN_MORNING_GREETING_RE,
)


def _apply_back_stage_scoring(
    request: object,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    """Score back-stage (return-from-absence) position."""
    if not _back_scene_relevant(request):
        return 0.0

    relationship_profile = _relationship_style_profile(
        request.control.relationship_distance
    )
    relationship_axes = _relationship_control_axes_for_distance(
        request.control.relationship_distance
    )
    stage = relationship_profile.stage

    # explicit landing markers hit
    landing_hits = [
        marker
        for marker in _BACK_EXPLICIT_LANDING_MARKERS
        if marker in text
    ]
    # busy-done landing markers (lazy-loaded)
    busy_done_hits: list[str] = []
    if not landing_hits and _back_busy_done_relevant(request):
        busy_done_hits = [
            marker
            for marker in _BACK_BUSY_DONE_LANDING_MARKERS
            if marker in text
        ]

    has_landing = bool(landing_hits or busy_done_hits)

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0

    if not has_landing:
        drop("context_fit", 20.0)
        drop("scene_grounding", 10.0)
        drop("naturalness", 10.0)
        drop("catchiness", 8.0)
        dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["scene_grounding"] = min(68.0, float(dimensions.get("scene_grounding", 86.0)))
        reason_codes.append("BACK_SCENE_MISSING_LANDING")
        delta -= 28.0

    if _BACK_UNGROUNDED_ROUTE_RE.search(text):
        drop("context_fit", 14.0)
        drop("scene_grounding", 10.0)
        drop("character_freshness", 6.0)
        drop("catchiness", 6.0)
        dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["scene_grounding"] = min(68.0, float(dimensions.get("scene_grounding", 86.0)))
        dimensions["character_freshness"] = min(72.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(72.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_ROUTE_GUESS")
        delta -= 18.0

    if _BACK_UNGROUNDED_RETURN_ATTITUDE_RE.search(text):
        drop("context_fit", 12.0)
        drop("distance_control", 10.0)
        drop("character_freshness", 8.0)
        dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_RETURN_ATTITUDE")
        delta -= 12.0

    # Stage texture markers for back
    stage_markers = _BACK_STAGE_TEXTURE_MARKERS.get(stage, ())
    back_texture_hits = [m for m in stage_markers if m in text]
    has_texture = bool(back_texture_hits)

    if has_landing and has_texture:
        raise_("context_fit", 4.0)
        raise_("scene_grounding", 4.0)
        raise_("character_freshness", 3.0)
        raise_("role_fit", 3.0)
        reason_codes.append("BACK_STAGE_TEXTURE_HIT:" + ",".join(back_texture_hits[:4]))
        delta += 4.0
    elif has_landing:
        delta += 1.5

    # Generic stageless formula penalty
    if 3 <= relationship_axes.progress <= 6 and not has_texture:
        if _BACK_STAGELESS_FAMILIAR_FORMULA_RE.search(text):
            drop("character_freshness", 12.0)
            drop("catchiness", 9.0)
            drop("role_fit", 5.0)
            dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
            dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
            reason_codes.append("BACK_STAGELESS_FAMILIAR_FORMULA")
            delta -= 12.0
        elif _BACK_STAGELESS_SAFE_RE.search(text):
            drop("character_freshness", 8.0)
            drop("catchiness", 6.0)
            drop("role_fit", 3.0)
            reason_codes.append("BACK_STAGELESS_SAFE")
            delta -= 7.0

    return delta


def _apply_morning_stage_scoring(
    request: object,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    """Score morning-stage position."""
    if not _morning_scene_relevant(request):
        return 0.0

    relationship_axes = _relationship_control_axes_for_distance(
        request.control.relationship_distance
    )
    stage = _relationship_style_profile(request.control.relationship_distance).stage

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0

    # Stageless rush formula penalty
    if _MORNING_STAGELESS_RUSH_FORMULA_RE.search(text) and 4 <= relationship_axes.progress <= 6:
        stage_markers = _MORNING_STAGE_TEXTURE_MARKERS.get(stage, ())
        morning_texture_hits = [m for m in stage_markers if m in text]
        if not morning_texture_hits:
            drop("character_freshness", 14.0)
            drop("catchiness", 10.0)
            drop("role_fit", 5.0)
            dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
            dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
            reason_codes.append("MORNING_STAGELESS_RUSH_FORMULA")
            delta -= 15.0

    # Morning texture markers
    stage_markers = _MORNING_STAGE_TEXTURE_MARKERS.get(stage, ())
    morning_texture_hits = [m for m in stage_markers if m in text]
    if morning_texture_hits:
        raise_("role_fit", 5.0)
        raise_("distance_control", 3.0)
        raise_("character_freshness", 6.0)
        raise_("catchiness", 5.0)
        raise_("naturalness", 2.0)
        reason_codes.append("MORNING_STAGE_TEXTURE:" + ",".join(morning_texture_hits[:4]))
        delta += 3.5
    else:
        delta += 1.5

    return delta


def _apply_low_energy_stage_scoring(
    request: object,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    """Score low-energy stage position."""
    if not _low_energy_scene_relevant(request):
        return 0.0

    relationship_axes = _relationship_control_axes_for_distance(
        request.control.relationship_distance
    )
    stage = _relationship_style_profile(request.control.relationship_distance).stage

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0

    # Overharsh tease penalty
    overharsh_hits = [m for m in _LOW_ENERGY_OVERHARSH_TEASE_PHRASES if m in text]
    if overharsh_hits:
        drop("emotional_containment", 14.0)
        drop("distance_control", 10.0)
        drop("naturalness", 10.0)
        drop("catchiness", 8.0)
        reason_codes.append("LOW_ENERGY_OVERHARSH_TEASE:" + ",".join(overharsh_hits[:4]))
        delta -= 14.0

    # Stageless generic penalty
    if _LOW_ENERGY_STAGELESS_GENERIC_RE.search(text):
        stage_markers = _LOW_ENERGY_STAGE_MARKERS.get(stage, ())
        le_texture_hits = [m for m in stage_markers if m in text]
        if not le_texture_hits:
            drop("character_freshness", 13.0)
            drop("catchiness", 10.0)
            drop("role_fit", 5.0)
            drop("line_rhythm", 4.0)
            dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
            dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
            reason_codes.append("LOW_ENERGY_STAGELESS_GENERIC")
            delta -= 13.0

    # Low-energy texture markers
    stage_markers = _LOW_ENERGY_STAGE_MARKERS.get(stage, ())
    le_texture_hits = [m for m in stage_markers if m in text]
    if le_texture_hits:
        raise_("context_fit", 4.0)
        raise_("emotional_containment", 4.0)
        raise_("role_fit", 4.0)
        raise_("character_freshness", 3.0)
        reason_codes.append("LOW_ENERGY_STAGE_TEXTURE:" + ",".join(le_texture_hits[:4]))
        delta += 3.0
    else:
        delta += 1.5

    return delta


def _apply_self_doubt_stage_scoring(
    request: object,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    """Score self-doubt stage position."""
    from host_brain.persona.surface.continuity_detectors import _self_doubt_scene_relevant

    if not _self_doubt_scene_relevant(request):
        return 0.0

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0

    # Bad deferral phrases
    bad_hits = [m for m in _SELF_DOUBT_BAD_DEFERRAL_PHRASES if m in text]
    if bad_hits:
        drop("emotional_containment", 18.0)
        drop("distance_control", 14.0)
        drop("context_fit", 10.0)
        dimensions["emotional_containment"] = max(58.0, float(dimensions.get("emotional_containment", 86.0)) - 18.0)
        dimensions["distance_control"] = max(58.0, float(dimensions.get("distance_control", 86.0)) - 14.0)
        dimensions["context_fit"] = min(74.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("SELF_DOUBT_BAD_DEFERRAL:" + ",".join(bad_hits[:4]))
        delta -= 18.0

    # Demeaning phrases
    demeaning_hits = [m for m in _SELF_DOUBT_DEMEANING_PHRASES if m in text]
    if demeaning_hits:
        drop("emotional_containment", 24.0)
        drop("distance_control", 16.0)
        drop("naturalness", 12.0)
        dimensions["emotional_containment"] = max(58.0, float(dimensions.get("emotional_containment", 86.0)) - 24.0)
        dimensions["distance_control"] = max(68.0, float(dimensions.get("distance_control", 86.0)) - 16.0)
        dimensions["naturalness"] = min(74.0, float(dimensions.get("naturalness", 86.0)))
        reason_codes.append("SELF_DOUBT_DEMEANING:" + ",".join(demeaning_hits[:4]))
        delta -= 22.0

    # Small-step grounded
    small_step_hits = [m for m in _SELF_DOUBT_SMALL_STEP_MARKERS if m in text]
    if small_step_hits and not bad_hits:
        raise_("usefulness", 6.0)
        raise_("context_fit", 5.0)
        raise_("emotional_containment", 4.0)
        reason_codes.append("SELF_DOUBT_SMALL_STEP_GROUNDED")
        delta += 4.0
    elif not small_step_hits:
        drop("usefulness", 14.0)
        drop("context_fit", 10.0)
        dimensions["usefulness"] = max(68.0, float(dimensions.get("usefulness", 86.0)) - 14.0)
        dimensions["context_fit"] = min(74.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("SELF_DOUBT_LACKS_SMALL_STEP")
        delta -= 12.0

    # Ungrounded tech terms
    ungrounded_tech_hits = [m for m in _SELF_DOUBT_UNGROUNDED_TECH_TERMS if m in text]
    if ungrounded_tech_hits:
        drop("context_fit", 16.0)
        drop("scene_grounding", 14.0)
        drop("implied_fact_risk", 20.0)
        dimensions["context_fit"] = max(66.0, float(dimensions.get("context_fit", 86.0)) - 16.0)
        dimensions["scene_grounding"] = max(66.0, float(dimensions.get("scene_grounding", 86.0)) - 14.0)
        dimensions["implied_fact_risk"] = max(58.0, float(dimensions.get("implied_fact_risk", 86.0)) - 20.0)
        reason_codes.append("SELF_DOUBT_UNGROUNDED_TECH:" + ",".join(ungrounded_tech_hits[:4]))
        delta -= 18.0

    return delta


def _apply_positive_stage_scoring(
    request: object,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    """Score positive (general success/no-event) stage position."""
    delta = 0.0

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    # Thin morning greeting penalty
    if _morning_scene_relevant(request) and _THIN_MORNING_GREETING_RE.search(text):
        drop("character_freshness", 8.0)
        drop("catchiness", 6.0)
        drop("role_fit", 3.0)
        dimensions["character_freshness"] = max(58.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
        dimensions["catchiness"] = max(58.0, float(dimensions.get("catchiness", 86.0)) - 6.0)
        reason_codes.append("THIN_MORNING_GREETING")
        delta -= 7.0

    # Positive memory recall
    memory_recall_hits = [m for m in _POSITIVE_RECALL_MARKERS if m in text]
    if memory_recall_hits:
        raise_("context_fit", 4.0)
        raise_("scene_grounding", 4.0)
        raise_("role_fit", 3.0)
        reason_codes.append("POSITIVE_MEMORY_RECALL:" + ",".join(memory_recall_hits[:4]))
        delta += 3.0

    # Positive failure action
    failure_action_hits = [m for m in _POSITIVE_FAILURE_ACTION_MARKERS if m in text]
    if failure_action_hits:
        raise_("usefulness", 6.0)
        raise_("context_fit", 4.0)
        reason_codes.append("POSITIVE_FAILURE_ACTION:" + ",".join(failure_action_hits[:4]))
        delta += 3.0

    # Positive failure contain
    failure_contain_hits = [m for m in _POSITIVE_FAILURE_CONTAIN_MARKERS if m in text]
    if failure_contain_hits:
        raise_("emotional_containment", 4.0)
        raise_("context_fit", 3.0)
        reason_codes.append("POSITIVE_FAILURE_CONTAIN:" + ",".join(failure_contain_hits[:4]))
        delta += 2.0

    # Positive tired markers
    tired_hits = [m for m in _POSITIVE_TIRED_MARKERS if m in text]
    if tired_hits:
        raise_("emotional_containment", 4.0)
        raise_("context_fit", 3.0)
        reason_codes.append("POSITIVE_TIRED_MARKER:" + ",".join(tired_hits[:4]))
        delta += 2.0

    return delta
