"""Scoring sub-packet for isolated surface candidate scoring.

Extracted from candidate_scoring.py — covers candidate parsing,
question budget counting, control-violation detection, and the
signature-texture scoring passes.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

from host_brain.persona.surface.fact_handling import memory_alignment_terms, merged_must_not_do
from host_brain.persona.surface.util import _clean_list, _clean_text
from host_brain.persona.surface.relationship_affect import (
    relationship_control_axes_for_distance as _relationship_control_axes_for_distance,
    _relationship_distance_family,
    _relationship_marker_hits,
    _relationship_style_profile,
)
from host_brain.persona.surface.continuity_detectors import (
    _signature_texture_relevant,
)
from host_brain.persona.surface.relationship_affect import (
    _positive_stage_scene_blocked,
)


# ---------------------------------------------------------------------------
# Signature texture hits (extracted from scoring_texture.py)
# ---------------------------------------------------------------------------


def signature_texture_hits(text: str) -> list[str]:
    """Return canonical signature texture tags present in *text*."""
    tags: list[str] = []
    if "后面再说" in text or "后面讲" in text or "回头再聊" in text:
        tags.append("care_behind_tease")
    if any(c in text for c in ("害羞", "脸红", "耳朵红", "低头", "别说了")):
        tags.append("shy_affection")
    if any(c in text for c in ("其实", "说实话", "说真的", "不骗你")):
        tags.append("flat_confession")
    if any(c in text for c in ("算了", "不管了", "懒得管")):
        tags.append("flat_giveup")
    if any(c in text for c in ("算了", "不管了", "懒得管")):
        tags.append("flat_resign")
    return _clean_list(tags)


# ---------------------------------------------------------------------------
# Scoring-specific constants (from original L3924-3939, defined locally)
# ---------------------------------------------------------------------------
_IGNORED_REACTION_QUESTION_SEGMENTS = frozenset({"哈", "嗯", "欸", "诶", "咦", "啊"})
_ALLOWED_RHETORICAL_QUESTION_SEGMENTS = frozenset({
    "累了", "难受", "不舒服", "又炸了", "炸了", "就这", "回来了", "想我", "想你",
})
_QUESTION_SEGMENT_SPLIT_RE = re.compile(r"[。.!！?？\n\r，,；;：:]")
_INFO_SEEKING_QUESTION_RE = re.compile(r"(?:什么|怎么|为什么|哪|哪里|哪儿|谁|几|多少|吗|么|是不是|有没有|能不能|可不可以|要不要)")


# ---------------------------------------------------------------------------
# Signature texture scoring
# ---------------------------------------------------------------------------


def _apply_signature_texture_scoring(
    request: Any,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    from host_brain.persona.surface.scene_constants import (
        _LOVER_RELATIONSHIP_STAGES,
        _SIGNATURE_FLAT_SAFE_RE,
        _SIGNATURE_TEXTURE_STAGES,
    )

    if _positive_stage_scene_blocked(reason_codes):
        return 0.0
    if not _signature_texture_relevant(request):
        return 0.0

    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    stage = relationship_profile.stage
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    hits = signature_texture_hits(text)

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    if hits:
        raise_("character_freshness", min(14.0, 5.0 + 3.0 * len(hits)))
        raise_("catchiness", min(12.0, 4.0 + 2.0 * len(hits)))
        raise_("role_fit", min(7.0, 2.0 + len(hits)))
        raise_("naturalness", 3.0)
        delta = min(8.0, 2.5 + 1.35 * len(hits))
        if "care_behind_tease" in hits and task_mode in {
            "complaint", "small_failure", "tired", "headache", "sleepy", "bad_mood"
        }:
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
            _relationship_marker_hit(text, marker)
            for marker in relationship_profile.prefer_markers
        )
        if missing_is_visible:
            drop("character_freshness", 10.0)
            drop("catchiness", 9.0)
            drop("role_fit", 4.0)
            reason_codes.append("SIGNATURE_TEXTURE_MISSING")
            return -3.5
    return 0.0


# ---------------------------------------------------------------------------
# Question budget counting
# ---------------------------------------------------------------------------


def _question_budget_count(text: str) -> int:
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
            and _has_post_question_landing(value[marker.end():])
            ):
            rhetorical_used = True
            continue
        count += 1
    return count


def _question_segment_before(text: str, marker_index: int) -> str:
    prefix = text[:marker_index]
    segment = _QUESTION_SEGMENT_SPLIT_RE.split(prefix)[-1]
    return re.sub(r"[「」『』""\"'（）()\[\]\s~～…]+", "", segment)


def _allowed_rhetorical_question_segment(segment: str) -> bool:
    value = _clean_text(segment)
    if not value or len(value) > 4 and value not in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS:
        return False
    if _INFO_SEEKING_QUESTION_RE.search(value):
        return False
    return value in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS


def _has_post_question_landing(tail: str) -> bool:
    value = _clean_text(tail)
    if len(value) < 4:
        return False
    if value.startswith(("？", "?", "什么", "怎么", "为什么", "哪", "哪里", "哪儿", "谁")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", value))


# ---------------------------------------------------------------------------
# Control violations
# ---------------------------------------------------------------------------


def _control_violations(request: Any, text: str) -> list[str]:
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
