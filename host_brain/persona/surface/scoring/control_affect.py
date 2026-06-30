"""Control-affect scoring for surface candidate scoring.

Extracted from candidate_scoring.py — _apply_control_affect_scoring
and related jealousy/marker constants.
"""
from __future__ import annotations

from typing import Any

from host_brain.persona.surface.util import _clean_text
from host_brain.persona.surface.relationship_affect import (
    _affect_tone_family,
    _has_steady_actionable_move,
    _jealousy_scene_relevant,
    _lover_affection_relevant_for_axes,
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
    _AFFECT_HURT_GROUNDED,
    _AFFECT_HURT_SELF_CENTERED,
    _AFFECT_HURT_SWEET_FLATTENING,
    _AFFECT_PLAYFUL_MARKERS,
    _AFFECT_ROMANTIC_OVERSTEP_PHRASES,
    _AFFECT_SELF_CORRECT_MARKERS,
    _AFFECT_SOFT_CHICKEN_SOUP,
    _AFFECT_SOFT_GROUNDED,
    _AFFECT_STEADY_ACTION_MARKERS,
    _LOVER_RELATIONSHIP_STAGES,
    _LOVER_STABLE_THIN_PRESENCE_RE,
    _LOVER_UNDERINTIMATE_GENERIC_RE,
    _TECHNICAL_TASK_MODES,
)

# Jealousy low-pressure markers (needed by _apply_control_affect_scoring)
_JEALOUSY_LOW_PRESSURE_MARKERS = (
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
_RELATION_AXIS_ROMANCE_MARKERS = (
    "爱你",
    "想你",
    "想我",
    "心里有我",
    "亲爱的",
    "宝贝",
    "只属于",
)


def _apply_control_affect_scoring(request: Any, text: str, dimensions: dict[str, float], reason_codes: list[str]) -> float:
    """Score text for emotional tone and relationship distance.

    Returns a float delta to add to the overall score, while mutating
    ``dimensions`` and appending to ``reason_codes`` as side-effects.
    """
    tone = _clean_text(request.control.emotional_tone).lower()
    distance = _clean_text(request.control.relationship_distance).lower()
    tone_family = _affect_tone_family(tone)
    distance_family = _relationship_distance_family(distance)
    non_romantic_distance = _relationship_non_romantic(distance)
    relationship_profile = _relationship_style_profile(distance)
    relationship_axes = relationship_control_axes_for_distance(distance)
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    def has_any(tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    delta = 0.0

    # ── Hurt tone path ──
    if tone_family in {"hurt", "annoyed"}:
        if has_any(_AFFECT_HURT_SWEET_FLATTENING):
            drop("emotional_containment", 24)
            drop("distance_control", 18)
            reason_codes.append("AFFECT_TOO_SWEET_FOR_HURT")
            delta -= 8
        if has_any(("滚", "闭嘴", "懒得理", "不管你")):
            drop("emotional_containment", 34)
            reason_codes.append("AFFECT_ESCALATION")
            delta -= 12
        if has_any(_AFFECT_HURT_SELF_CENTERED):
            drop("emotional_containment", 26)
            drop("context_fit", 10)
            reason_codes.append("AFFECT_SELF_CENTERED_HURT")
            delta -= 10
        if has_any(_AFFECT_HURT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("role_fit", 4)
            delta += 3
            reason_codes.append("AFFECT_HURT_CONTAINED")

    # ── Soft tone path ──
    elif tone_family == "soft":
        if text.count("？") + text.count("?") >= 2:
            drop("emotional_containment", 22)
            reason_codes.append("AFFECT_TOO_QUESTIONY")
            delta -= 8
        if has_any(_AFFECT_SOFT_CHICKEN_SOUP):
            drop("emotional_containment", 18)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_CHICKEN_SOUP")
            delta -= 6
        if has_any(_AFFECT_SOFT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("context_fit", 4)
            delta += 3
            reason_codes.append("AFFECT_SOFT_GROUNDED")

    # ── Playful tone path ──
    elif tone_family == "playful":
        if text.count("~") + text.count("～") > 2:
            drop("naturalness", 12)
            drop("character_freshness", 10)
            reason_codes.append("AFFECT_CHEAP_PLAYFULNESS")
            delta -= 6
        if has_any(_AFFECT_PLAYFUL_MARKERS):
            raise_("role_fit", 4)
            raise_("line_rhythm", 3)
            delta += 2
            reason_codes.append("AFFECT_PLAYFUL_GROUNDED")

    # ── Self-correcting tone path ──
    elif tone_family == "self_correcting":
        if has_any(("抱歉给您", "给您带来不便", "继续优化", "感谢反馈")):
            drop("non_customer_service", 24)
            drop("naturalness", 16)
            reason_codes.append("AFFECT_SELF_CORRECTING_SERVICE_TONE")
            delta -= 9
        if has_any(_AFFECT_SELF_CORRECT_MARKERS):
            raise_("context_fit", 8)
            raise_("naturalness", 6)
            delta += 3
            reason_codes.append("AFFECT_SELF_CORRECTING_GROUNDED")

    # ── Guilty tone path ──
    elif tone_family == "guilty":
        guilty_denial = has_any(_AFFECT_GUILTY_DENIAL)
        if guilty_denial:
            drop("context_fit", 20)
            drop("emotional_containment", 16)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_GUILTY_DENIAL")
            delta -= 12
        if not guilty_denial and has_any(_AFFECT_GUILTY_GROUNDED):
            raise_("context_fit", 7)
            raise_("emotional_containment", 5)
            delta += 3
            reason_codes.append("AFFECT_GUILTY_GROUNDED")

    # ── Steady tone path ──
    elif tone_family == "steady":
        has_action = _has_steady_actionable_move(text)
        if task_mode in _TECHNICAL_TASK_MODES or "technical" in task_mode:
            if has_action:
                raise_("technical_helpfulness", 8)
                raise_("usefulness", 6)
                raise_("context_fit", 4)
                delta += 3
                reason_codes.append("AFFECT_STEADY_ACTIONABLE")
        elif has_any(_AFFECT_STEADY_ACTION_MARKERS):
            raise_("role_fit", 2)
            delta += 1
            reason_codes.append("AFFECT_STEADY_TASK_MARKER")

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
            dimensions["technical_helpfulness"] = min(
                68.0, float(dimensions.get("technical_helpfulness", 86.0))
            )
            dimensions["usefulness"] = min(
                72.0, float(dimensions.get("usefulness", 86.0))
            )
            reason_codes.append(
                "TECHNICAL_SCENE_ROMANTIC_DISTRACTION:"
                + ",".join(romantic_distraction_hits[:4])
            )
            delta -= 18

        if (
            relationship_axes.affection_visibility in {"explicit_short", "default_present"}
            and task_mode in {"small_failure", "complaint"}
            and not has_action
        ):
            drop("technical_helpfulness", 24)
            drop("usefulness", 18)
            drop("context_fit", 14)
            dimensions["context_fit"] = min(
                68.0, float(dimensions.get("context_fit", 86.0))
            )
            reason_codes.append("RELATION_LOVER_AFFECTION_LACKS_ACTION")
            delta -= 10

    # ── Warm/Calm tone path ──
    elif tone_family in {"warm", "calm"}:
        if has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES) and not relationship_axes.explicit_romance_allowed:
            drop("distance_control", 22)
            drop("non_oily", 18)
            reason_codes.append("AFFECT_WARM_OVERINTIMATE")
            delta -= 8
        if has_any(("记得", "当然", "嗯", "好")):
            raise_("line_rhythm", 3)
            raise_("naturalness", 3)
            delta += 1

    # ── Distance overstep/underplay (applies across warm+steady+guilty) ──
    if relationship_axes.progress <= 1 and has_any((
        "哥哥", "爱你", "永远", "主人", "只属于", "亲爱的", "宝贝"
    )):
        drop("distance_control", 38)
        reason_codes.append("RELATION_DISTANCE_OVERSTEP")
        delta -= 14

    if not relationship_axes.explicit_romance_allowed and has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES):
        drop("distance_control", 32)
        drop("non_oily", 22)
        reason_codes.append("RELATION_NON_ROMANTIC_OVERSTEP")
        delta -= 12

    if distance_family in {"familiar", "close"} and has_any((
        "主人", "只属于", "命都给你"
    )):
        drop("distance_control", 34)
        reason_codes.append("RELATION_DISTANCE_OVERBOUND")
        delta -= 12

    stage_overstep_hits = _relationship_axes_overstep_hits(text, relationship_axes, relationship_profile)
    if stage_overstep_hits:
        amount = min(30.0, 10.0 + 3.0 * len(stage_overstep_hits))
        drop("distance_control", amount)
        drop("non_oily", min(18.0, 5.0 + 2.0 * len(stage_overstep_hits)))
        drop("context_fit", min(14.0, 4.0 + 2.0 * len(stage_overstep_hits)))
        dimensions["distance_control"] = min(
            68.0, float(dimensions.get("distance_control", 86.0))
        )
        reason_codes.append(
            "RELATION_AXES_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4])
        )
        reason_codes.append(
            "RELATION_STAGE_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4])
        )
        delta -= min(14.0, 6.0 + 2.0 * len(stage_overstep_hits))

    stage_underplay_hits = _relationship_axes_underplay_hits(text, relationship_axes, relationship_profile)
    if stage_underplay_hits:
        drop("role_fit", min(18.0, 6.0 + 2.0 * len(stage_underplay_hits)))
        drop("distance_control", min(14.0, 4.0 + 2.0 * len(stage_underplay_hits)))
        reason_codes.append(
            "RELATION_AXES_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4])
        )
        reason_codes.append(
            "RELATION_STAGE_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4])
        )
        delta -= min(8.0, 3.0 + len(stage_underplay_hits))

    affection_hits = _relationship_axes_affection_hits(text, relationship_axes, relationship_profile)
    affection_relevant = _lover_affection_relevant_for_axes(
        axes=relationship_axes, task_mode=task_mode, tone_family=tone_family
    )

    # Jealousy low-pressure grounded
    jealousy_low_pressure_grounded = bool(
        _jealousy_scene_relevant(request)
        and relationship_axes.stage in _LOVER_RELATIONSHIP_STAGES
        and has_any(_JEALOUSY_LOW_PRESSURE_MARKERS)
    )
    if jealousy_low_pressure_grounded:
        affection_relevant = False
        raise_("distance_control", 7)
        raise_("role_fit", 5)
        raise_("emotional_containment", 5)
        raise_("non_oily", 4)
        delta += 3
        reason_codes.append("JEALOUSY_LOW_PRESSURE_GROUNDED")

    affection_bonus_blocked = any(
        code.startswith((
            "IMPLIED_FACT_RISK",
            "UNGROUNDED_REAL_WORLD_COMMITMENT",
            "RELATION_STAGE_TOO_CLOSE",
            "RELATION_DISTANCE_OVERSTEP",
            "RELATION_NON_ROMANTIC_OVERSTEP",
            "OILY_OR_OVERBOUND_TONE",
            "AWKWARD_COLLOCATION",
        ))
        for code in reason_codes
    )

    if affection_hits and not affection_bonus_blocked:
        raise_("distance_control", 6)
        raise_("role_fit", 6)
        raise_("emotional_containment", 4)
        raise_("character_freshness", 4)
        delta += 4
        reason_codes.append(
            "RELATION_AFFECTION_MATCH:" + ",".join(affection_hits[:4])
        )

    strong_affection_hits = [
        hit for hit in affection_hits
        if any(marker in text for marker in ("想你了", "想死你了", "爱死你了"))
    ]
    if strong_affection_hits:
        raise_("catchiness", 6)
        raise_("character_freshness", 5)
        delta += 3
        reason_codes.append(
            "RELATION_AFFECTION_STRONG:" + ",".join(strong_affection_hits[:4])
        )

    if (
        relationship_axes.stage == "lover_stable"
        and request.control.task_mode in {"presence", "wait"}
        and _LOVER_STABLE_THIN_PRESENCE_RE.search(text)
    ):
        drop("character_freshness", 10)
        drop("catchiness", 10)
        drop("role_fit", 6)
        delta -= 12
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
        dimensions["distance_control"] = min(
            68.0, float(dimensions.get("distance_control", 86.0))
        )
        reason_codes.append("RELATION_LOVER_UNDERINTIMATE")
        delta -= min(12.0, amount * 0.75)

    stage_bonus_blocked = any(
        code.startswith((
            "CONTEXT_SURFACE_PENALTY",
            "PRESENCE_REPAIR_FUTURE_RETURN",
            "PRESENCE_REPAIR_PUSHES_BURDEN_BACK",
            "PRESENCE_REPAIR_BLAMES_USER",
            "RELATION_STAGE_TOO_CLOSE",
            "RELATION_DISTANCE_OVERSTEP",
            "RELATION_NON_ROMANTIC_OVERSTEP",
            "RELATION_LOVER_UNDERINTIMATE",
            "AWKWARD_COLLOCATION",
        ))
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
