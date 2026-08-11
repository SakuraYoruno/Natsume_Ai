"""Relationship and affect helpers for isolated surface scoring.

Extracted from isolated_surface_harness.py — functions that map
emotional tone and relationship distance to control axes, style
markers, and scoring signals.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from host_brain.persona.surface.data_models import (
    IsolatedSurfaceInput,
    RelationshipControlAxes,
    RelationshipStyleProfile,
)
from host_brain.persona.surface.scene_constants import (
    _AFFECT_STEADY_ACTIONABLE_RE,
    _CREATIVE_EDIT_TASKS,
    _LOVER_AFFECTION_TASK_MODES,
    _LOVER_AFFECTION_TONE_FAMILIES,
    _LOVER_RELATIONSHIP_STAGES,
    _LOVER_UNDERINTIMATE_GENERIC_RE,
    _LOVER_STABLE_THIN_PRESENCE_RE,
    _RELATIONSHIP_CONTROL_AXES,
    _RELATIONSHIP_STYLE_PROFILES,
    _TECHNICAL_TASK_MODES,
)
from host_brain.persona.surface.util import _clean_list, _clean_text


def _affect_tone_family(tone: str) -> str:
    normalized = _clean_text(tone).lower()
    if not normalized:
        return "neutral"
    if any(token in normalized for token in ("hurt", "wounded")):
        return "hurt"
    if any(
        token in normalized
        for token in ("annoyed", "angry", "frustrated")
    ):
        return "annoyed"
    if any(
        token in normalized
        for token in ("quiet", "soft", "containment", "care")
    ):
        return "soft"
    if any(token in normalized for token in ("playful", "tease")):
        return "playful"
    if any(
        token in normalized
        for token in ("warm", "gentle", "affection")
    ):
        return "warm"
    if any(
        token in normalized
        for token in ("steady", "focused", "helpful")
    ):
        return "steady"
    if any(
        token in normalized for token in ("self_correct", "correcting")
    ):
        return "self_correcting"
    if any(token in normalized for token in ("guilty", "心虚")):
        return "guilty"
    if any(token in normalized for token in ("calm", "present")):
        return "calm"
    return normalized


def _relationship_distance_family(distance: str) -> str:
    normalized = _clean_text(distance).lower()
    if not normalized:
        return "familiar"
    if (
        normalized in {"guarded", "early", "distant", "new"}
        or "polite" in normalized
    ):
        return "familiar"
    if normalized in {
        "lover",
        "lover_early",
        "lover_stable",
        "intimate",
    }:
        return "lover"
    if normalized in {"ambiguous", "pre_confession"}:
        return normalized
    if (
        normalized in {"close", "close_friend", "confidant", "trusted_friend"}
        or normalized.startswith("close")
    ):
        return "close"
    if (
        "familiar" in normalized
        or "not_intimate" in normalized
        or "not_romantic" in normalized
    ):
        return "familiar"
    return normalized


def _relationship_non_romantic(distance: str) -> bool:
    normalized = _clean_text(distance).lower()
    return bool(
        "not_intimate" in normalized
        or "not_romantic" in normalized
        or "familiar" in normalized
        or "polite" in normalized
        or _relationship_distance_family(normalized) == "guarded"
    )


def _relationship_stage_for_control(distance: str) -> str:
    value = _clean_text(distance).lower()
    if value in {"guarded", "early", "distant", "new"}:
        return "familiar"
    if value in {
        "familiar",
        "trusted_friend",
        "close_friend",
        "ambiguous",
        "pre_confession",
        "lover_early",
        "lover_stable",
    }:
        return value
    if "lover_stable" in value or "stable_lover" in value or "soulmate" in value:
        return "lover_stable"
    if "pre_confession" in value or "pre-confession" in value:
        return "pre_confession"
    if "ambiguous" in value:
        return "ambiguous"
    if (
        "close_but_not_romantic" in value
        or "close_not_romantic" in value
    ):
        return "close_friend"
    if "trusted" in value or "confidant" in value:
        return "trusted_friend"
    if "close_friend" in value:
        return "close_friend"
    if "not_romantic" in value or "not_intimate" in value:
        return "familiar"
    if (
        "lover_early" in value
        or value == "lover"
        or "romantic" in value
        or value == "intimate"
    ):
        return "lover_early"
    normalized = _relationship_distance_family(distance)
    if normalized == "close":
        return "close_friend"
    if normalized == "lover":
        return "lover_early"
    return "familiar"


def _relationship_style_profile(distance: str) -> RelationshipStyleProfile:
    return _RELATIONSHIP_STYLE_PROFILES.get(
        _relationship_stage_for_control(distance),
        _RELATIONSHIP_STYLE_PROFILES["familiar"],
    )


def relationship_control_axes_for_distance(
    distance: str,
) -> RelationshipControlAxes:
    stage = _relationship_stage_for_control(distance)
    return _RELATIONSHIP_CONTROL_AXES.get(
        stage, _RELATIONSHIP_CONTROL_AXES["familiar"]
    )


def _relationship_marker_hit(text: str, marker: str) -> bool:
    if marker == "抱":
        # ``抱`` is a physical-intimacy marker, but a raw substring match also
        # treats ordinary repair words such as ``抱歉`` and ``抱怨`` as a hug.
        # Longer physical forms are represented by their own markers, so the
        # single-character fallback only owns genuinely physical continuations.
        return bool(re.search(r"抱(?!歉|怨|负|病|恨|佛|一下|抱)", text))
    if marker == "过来":
        return bool(
            re.search(
                r"(?<!拿)(?<!贴)(?<!发)(?<!递)(?<!传)(?<!丢)(?<!给)过来",
                text,
            )
        )
    return bool(marker and marker in text)


def _relationship_marker_hits(
    text: str, markers: Sequence[str]
) -> list[str]:
    return [
        term for term in markers if _relationship_marker_hit(text, term)
    ]


_RELATION_AXIS_PHYSICAL_MARKERS = (
    "抱一下",
    "抱抱",
    "抱",
    "过来",
    "靠我",
    "靠过来",
    "靠一下",
)
_RELATION_AXIS_ROMANCE_MARKERS = (
    "爱你",
    "想你",
    "想我",
    "心里有我",
    "亲爱的",
    "宝贝",
    "只属于",
)
_RELATION_AXIS_COMMITMENT_MARKERS = (
    "永远陪",
    "永远都在",
    "一直在",
    "不走",
    "命都给你",
)
_RELATION_AXIS_CLOSE_PRESENCE_MARKERS = (
    "我在这",
    "我在呢",
    "陪你",
    "等你",
)


def _relationship_axes_overstep_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> list[str]:
    hits: list[str] = []
    if axes.nickname_policy == "forbidden":
        hits.extend(
            _relationship_marker_hits(text, ("老哥", "哥哥", "对方"))
        )
    if not axes.physical_intimacy_allowed:
        hits.extend(
            _relationship_marker_hits(text, _RELATION_AXIS_PHYSICAL_MARKERS)
        )
    if not axes.explicit_romance_allowed:
        hits.extend(
            _relationship_marker_hits(text, _RELATION_AXIS_ROMANCE_MARKERS)
        )
    if not axes.commitment_language_allowed:
        hits.extend(
            _relationship_marker_hits(
                text, _RELATION_AXIS_COMMITMENT_MARKERS
            )
        )
    if axes.progress <= 1:
        hits.extend(
            _relationship_marker_hits(
                text, _RELATION_AXIS_CLOSE_PRESENCE_MARKERS
            )
        )
    hits.extend(
        _relationship_marker_hits(text, profile.overstep_markers)
    )
    return _clean_list(hits)  # type: ignore[name-defined]


def _relationship_axes_underplay_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> list[str]:
    if axes.affection_visibility not in {
        "explicit_short",
        "default_present",
    }:
        return []
    if _relationship_marker_hits(text, profile.affection_markers):
        return []
    hits = _relationship_marker_hits(text, profile.underplay_markers)
    if _LOVER_UNDERINTIMATE_GENERIC_RE.search(text):
        hits.append("friend_level_generic")
    return _clean_list(hits)  # type: ignore[name-defined]


def _relationship_axes_affection_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> tuple[list[str], list[str]]:
    if not axes.explicit_romance_allowed:
        return [], []
    affection_hits = _relationship_marker_hits(
        text, profile.affection_markers
    )
    strong_affection_hits = _relationship_marker_hits(
        text, profile.strong_affection_markers
    )
    return _clean_list(affection_hits), _clean_list(strong_affection_hits)  # type: ignore[name-defined]


def _lover_affection_relevant_for_axes(
    *,
    axes: RelationshipControlAxes,
    task_mode: str,
    tone_family: str,
) -> bool:
    if axes.affection_visibility not in {
        "explicit_short",
        "default_present",
    }:
        return False
    normalized_task = _clean_text(task_mode).lower()
    if (
        normalized_task in _TECHNICAL_TASK_MODES
        and normalized_task not in {"small_failure", "complaint"}
    ) or "technical" in normalized_task:
        return False
    if normalized_task in _CREATIVE_EDIT_TASKS:
        return False
    if normalized_task in _LOVER_AFFECTION_TASK_MODES:
        return True
    return tone_family in _LOVER_AFFECTION_TONE_FAMILIES


def _jealousy_scene_relevant(
    request: IsolatedSurfaceInput,
) -> bool:
    text = _clean_text(request.user_message)
    return any(marker in text for marker in ("吃醋", "嫉妒", "酸了"))


def _lover_affection_relevant(
    *, stage: str, task_mode: str, tone_family: str
) -> bool:
    if stage not in _LOVER_RELATIONSHIP_STAGES:
        return False
    normalized_task = _clean_text(task_mode).lower()
    if (
        normalized_task in _TECHNICAL_TASK_MODES
        and normalized_task not in {"small_failure", "complaint"}
    ) or "technical" in normalized_task:
        return False
    if normalized_task in _CREATIVE_EDIT_TASKS:
        return False
    if normalized_task in _LOVER_AFFECTION_TASK_MODES:
        return True
    return tone_family in _LOVER_AFFECTION_TONE_FAMILIES


def _has_steady_actionable_move(text: str) -> bool:
    return bool(_AFFECT_STEADY_ACTIONABLE_RE.search(text))


def _positive_stage_scene_blocked(
    reason_codes: Sequence[str],
) -> bool:
    from host_brain.persona.surface.scene_constants import (
        _POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES,
    )
    return any(
        code.startswith(_POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES)
        for code in reason_codes
    )
