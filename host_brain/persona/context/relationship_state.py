from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


_SIBLING_STAGES = {"FRIEND", "CLOSE", "CLOSE_FRIEND"}
_LOVER_STAGES = {"INTIMATE", "CONFIDANT"}
_SOULMATE_STAGES = {"SOULMATE"}


_STAGE_LABELS: dict[str, str] = {
    "FRIEND": "关系好的兄妹",
    "CLOSE": "亲近兄妹",
    "CLOSE_FRIEND": "亲近兄妹",
    "CONFIDANT": "亲密恋人",
    "INTIMATE": "亲密恋人",
    "SOULMATE": "灵魂伴侣",
}

_STAGE_ALIASES: dict[str, str] = {
    "关系好的兄妹": "FRIEND",
    "关系好的义兄妹": "FRIEND",
    "稳定义兄妹": "FRIEND",
    "亲近兄妹": "CLOSE_FRIEND",
    "亲近义兄妹": "CLOSE_FRIEND",
    "会拌嘴的义兄妹": "CLOSE_FRIEND",
    "会护短的义兄妹": "CLOSE_FRIEND",
    "亲密恋人": "CONFIDANT",
    "确认心意的恋人": "CONFIDANT",
    "稳定亲密的恋人": "CONFIDANT",
    "灵魂伴侣": "SOULMATE",
}


_SUBSTAGE_LABELS_ZH: dict[str, str] = {
    "settled_sibling": "稳定义兄妹",
    "playful_sibling": "会拌嘴的义兄妹",
    "protective_sibling": "会护短的义兄妹",
    "ambiguous_affection": "暧昧升温的义兄妹",
    "confessed_lovers": "确认心意的恋人",
    "stable_lovers": "稳定亲密的恋人",
    "soulbound": "灵魂伴侣",
    "repairing_rift": "别扭修复中",
}


_SUBSTAGE_LABELS_JA: dict[str, str] = {
    "settled_sibling": "馴染んだ義兄妹",
    "playful_sibling": "軽く言い合える義兄妹",
    "protective_sibling": "身内として庇える義兄妹",
    "ambiguous_affection": "少し曖昧な熱のある義兄妹",
    "confessed_lovers": "想いを確かめた恋人",
    "stable_lovers": "落ち着いた恋人",
    "soulbound": "魂の伴侶",
    "repairing_rift": "すれ違いの修復中",
}


_STAGE_BASE: dict[str, dict[str, float]] = {
    "FRIEND": {"familiarity": 0.48, "trust": 0.40, "comfort": 0.46, "openness": 0.38, "dependency_tolerance": 0.28, "protectiveness": 0.34, "boundary_softness": 0.36, "affection_signal": 0.18, "playfulness": 0.40, "romantic_tension": 0.10, "attachment_depth": 0.32, "conflict_rift": 0.0},
    "CLOSE": {"familiarity": 0.56, "trust": 0.52, "comfort": 0.56, "openness": 0.46, "dependency_tolerance": 0.34, "protectiveness": 0.38, "boundary_softness": 0.44, "affection_signal": 0.22, "playfulness": 0.46, "romantic_tension": 0.16, "attachment_depth": 0.42, "conflict_rift": 0.0},
    "CLOSE_FRIEND": {"familiarity": 0.62, "trust": 0.58, "comfort": 0.62, "openness": 0.52, "dependency_tolerance": 0.40, "protectiveness": 0.44, "boundary_softness": 0.50, "affection_signal": 0.28, "playfulness": 0.50, "romantic_tension": 0.22, "attachment_depth": 0.50, "conflict_rift": 0.0},
    "CONFIDANT": {"familiarity": 0.82, "trust": 0.78, "comfort": 0.82, "openness": 0.72, "dependency_tolerance": 0.62, "protectiveness": 0.58, "boundary_softness": 0.70, "affection_signal": 0.54, "playfulness": 0.56, "romantic_tension": 0.62, "attachment_depth": 0.76, "conflict_rift": 0.0},
    "INTIMATE": {"familiarity": 0.82, "trust": 0.80, "comfort": 0.82, "openness": 0.74, "dependency_tolerance": 0.64, "protectiveness": 0.60, "boundary_softness": 0.72, "affection_signal": 0.56, "playfulness": 0.56, "romantic_tension": 0.64, "attachment_depth": 0.78, "conflict_rift": 0.0},
    "SOULMATE": {"familiarity": 0.94, "trust": 0.92, "comfort": 0.94, "openness": 0.88, "dependency_tolerance": 0.80, "protectiveness": 0.68, "boundary_softness": 0.86, "affection_signal": 0.72, "playfulness": 0.62, "romantic_tension": 0.78, "attachment_depth": 0.92, "conflict_rift": 0.0},
}


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(_normalize_bond_stage(stage), _STAGE_LABELS["FRIEND"])


def _relationship_phase(stage: str) -> str:
    normalized = _normalize_bond_stage(stage)
    if normalized in _SOULMATE_STAGES:
        return "soulmate"
    if normalized in _LOVER_STAGES:
        return "lover"
    if normalized in _SIBLING_STAGES:
        return "stepsibling"
    return "stepsibling"


def _normalize_bond_stage(stage: Any) -> str:
    text = str(stage or "").strip()
    if not text:
        return "FRIEND"
    upper = text.upper()
    if upper in _STAGE_BASE:
        return upper
    return _STAGE_ALIASES.get(text, "FRIEND")


def _substage_label(substage: str, *, render_target_lang: str = "zh") -> str:
    key = str(substage or "").strip() or "settled_sibling"
    labels = _SUBSTAGE_LABELS_JA if str(render_target_lang or "").strip().lower() == "ja" else _SUBSTAGE_LABELS_ZH
    return labels.get(key, labels["settled_sibling"])


def derive_relationship_substage(
    *,
    bond_stage: str,
    familiarity: float,
    trust: float,
    protectiveness: float,
    playfulness: float,
    romantic_tension: float,
    attachment_depth: float,
    conflict_rift: float,
) -> str:
    """Convert continuous relationship texture into a small prompt-facing label."""

    stage = _normalize_bond_stage(bond_stage)
    if conflict_rift >= 0.55:
        return "repairing_rift"
    if stage in _SOULMATE_STAGES or (attachment_depth >= 0.88 and trust >= 0.82):
        return "soulbound"
    if stage in _LOVER_STAGES:
        if attachment_depth >= 0.76 and trust >= 0.72:
            return "stable_lovers"
        return "confessed_lovers"
    if romantic_tension >= 0.46 and familiarity >= 0.50:
        return "ambiguous_affection"
    if stage in _SIBLING_STAGES:
        if protectiveness >= 0.48 and trust >= 0.50:
            return "protective_sibling"
        if playfulness >= 0.38:
            return "playful_sibling"
        return "settled_sibling"
    return "settled_sibling"


@dataclass(frozen=True)
class RelationshipStateBlock:
    familiarity: float = 0.08
    trust: float = 0.06
    comfort: float = 0.10
    openness: float = 0.08
    dependency_tolerance: float = 0.05
    protectiveness: float = 0.10
    boundary_softness: float = 0.08
    affection_signal: float = 0.02
    playfulness: float = 0.12
    romantic_tension: float = 0.0
    attachment_depth: float = 0.08
    conflict_rift: float = 0.0
    bond_stage: str = "FRIEND"
    stage_label: str = ""
    relationship_phase: str = ""
    relationship_substage: str = ""

    def __post_init__(self) -> None:
        stage = _normalize_bond_stage(self.bond_stage)
        base = _STAGE_BASE.get(stage, _STAGE_BASE["FRIEND"])
        object.__setattr__(self, "bond_stage", stage)
        for field_name in (
            "familiarity",
            "trust",
            "comfort",
            "openness",
            "dependency_tolerance",
            "protectiveness",
            "boundary_softness",
            "affection_signal",
            "playfulness",
            "romantic_tension",
            "attachment_depth",
            "conflict_rift",
        ):
            value = float(getattr(self, field_name) or 0.0)
            if field_name in base:
                value = max(value, base[field_name])
            object.__setattr__(self, field_name, _clamp(value))
        if not str(self.stage_label or "").strip():
            object.__setattr__(self, "stage_label", _stage_label(stage))
        if not str(self.relationship_phase or "").strip():
            object.__setattr__(self, "relationship_phase", _relationship_phase(stage))
        if not str(self.relationship_substage or "").strip():
            object.__setattr__(
                self,
                "relationship_substage",
                derive_relationship_substage(
                    bond_stage=stage,
                    familiarity=self.familiarity,
                    trust=self.trust,
                    protectiveness=self.protectiveness,
                    playfulness=self.playfulness,
                    romantic_tension=self.romantic_tension,
                    attachment_depth=self.attachment_depth,
                    conflict_rift=self.conflict_rift,
                ),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "familiarity": round(self.familiarity, 4),
            "trust": round(self.trust, 4),
            "comfort": round(self.comfort, 4),
            "openness": round(self.openness, 4),
            "dependency_tolerance": round(self.dependency_tolerance, 4),
            "protectiveness": round(self.protectiveness, 4),
            "boundary_softness": round(self.boundary_softness, 4),
            "affection_signal": round(self.affection_signal, 4),
            "playfulness": round(self.playfulness, 4),
            "romantic_tension": round(self.romantic_tension, 4),
            "attachment_depth": round(self.attachment_depth, 4),
            "conflict_rift": round(self.conflict_rift, 4),
            "bond_stage": self.bond_stage,
            "stage_label": self.stage_label,
            "relationship_phase": self.relationship_phase,
            "relationship_substage": self.relationship_substage,
        }


def resolve_relationship_state(
    *,
    bond_summary: Mapping[str, Any] | None,
    social_brain_state: Mapping[str, Any] | None = None,
) -> RelationshipStateBlock:
    bond = _safe_mapping(bond_summary)
    social = _safe_mapping(social_brain_state)
    stage = _normalize_bond_stage(
        social.get("bond_stage")
        or bond.get("stage")
        or "FRIEND"
    )
    base = dict(_STAGE_BASE.get(stage, _STAGE_BASE["FRIEND"]))

    intimacy = _coerce_float(social.get("bond_vector_intimacy"), default=_coerce_float(bond.get("score"), default=base["familiarity"]))
    trust = _coerce_float(social.get("bond_vector_trust"), default=_coerce_float(bond.get("score"), default=base["trust"]))
    shared_memory_count = max(0, int(_coerce_float(bond.get("shared_memory_count"), default=0.0)))
    trend = str(social.get("bond_trend") or bond.get("trend") or "").strip().lower()

    familiarity = _clamp(max(base["familiarity"], intimacy * 0.85 + min(shared_memory_count, 8) * 0.015))
    trust_score = _clamp(max(base["trust"], trust))
    comfort = _clamp(max(base["comfort"], (familiarity + trust_score) / 2.0))
    openness = _clamp(max(base["openness"], familiarity * 0.60 + trust_score * 0.35))
    dependency_tolerance = _clamp(max(base["dependency_tolerance"], trust_score * 0.55))
    protectiveness = _clamp(max(base["protectiveness"], familiarity * 0.28 + trust_score * 0.32))
    boundary_softness = _clamp(max(base["boundary_softness"], familiarity * 0.45))
    affection_signal = _clamp(max(base["affection_signal"], familiarity * 0.22 + trust_score * 0.18))
    playfulness = _clamp(max(
        base["playfulness"],
        _coerce_float(social.get("relationship_playfulness") or social.get("playfulness"), default=0.0),
        openness * 0.50 + boundary_softness * 0.35,
    ))
    romantic_tension = _clamp(max(
        base["romantic_tension"],
        _coerce_float(social.get("relationship_romantic_tension") or social.get("romantic_tension"), default=0.0),
        affection_signal * 0.58 + boundary_softness * 0.18,
    ))
    attachment_depth = _clamp(max(
        base["attachment_depth"],
        _coerce_float(social.get("relationship_attachment_depth") or social.get("attachment_depth"), default=0.0),
        familiarity * 0.35 + trust_score * 0.35 + affection_signal * 0.20 + min(shared_memory_count, 12) * 0.012,
    ))
    conflict_rift = _clamp(max(
        base["conflict_rift"],
        _coerce_float(
            social.get("relationship_conflict_rift")
            or social.get("conflict_rift")
            or social.get("rift_level")
            or bond.get("rift_level"),
            default=0.0,
        ),
    ))

    if trend in {"rising", "progress", "warming", "上升", "快速上升"}:
        openness = _clamp(openness + 0.04)
        boundary_softness = _clamp(boundary_softness + 0.03)
        affection_signal = _clamp(affection_signal + 0.03)
        playfulness = _clamp(playfulness + 0.03)
        attachment_depth = _clamp(attachment_depth + 0.03)
    elif trend in {"declining", "regress", "cooling", "下降", "快速下降"}:
        openness = _clamp(openness - 0.05)
        boundary_softness = _clamp(boundary_softness - 0.05)
        trust_score = _clamp(trust_score - 0.04)
        playfulness = _clamp(playfulness - 0.04)
        conflict_rift = _clamp(conflict_rift + 0.08)

    relationship_substage = derive_relationship_substage(
        bond_stage=stage,
        familiarity=familiarity,
        trust=trust_score,
        protectiveness=protectiveness,
        playfulness=playfulness,
        romantic_tension=romantic_tension,
        attachment_depth=attachment_depth,
        conflict_rift=conflict_rift,
    )

    return RelationshipStateBlock(
        familiarity=familiarity,
        trust=trust_score,
        comfort=comfort,
        openness=openness,
        dependency_tolerance=dependency_tolerance,
        protectiveness=protectiveness,
        boundary_softness=boundary_softness,
        affection_signal=affection_signal,
        playfulness=playfulness,
        romantic_tension=romantic_tension,
        attachment_depth=attachment_depth,
        conflict_rift=conflict_rift,
        bond_stage=stage,
        stage_label=_stage_label(stage),
        relationship_phase=_relationship_phase(stage),
        relationship_substage=relationship_substage,
    )


def build_relationship_summary(*, relationship: RelationshipStateBlock, render_target_lang: str = "zh") -> str:
    lang = str(render_target_lang or "").strip().lower()
    stage = _normalize_bond_stage(relationship.bond_stage)
    substage = str(relationship.relationship_substage or "").strip() or derive_relationship_substage(
        bond_stage=stage,
        familiarity=relationship.familiarity,
        trust=relationship.trust,
        protectiveness=relationship.protectiveness,
        playfulness=relationship.playfulness,
        romantic_tension=relationship.romantic_tension,
        attachment_depth=relationship.attachment_depth,
        conflict_rift=relationship.conflict_rift,
    )
    if lang == "ja":
        if substage == "repairing_rift":
            return "今は少しすれ違いがある。刺しすぎず、でも距離を取りすぎない。"
        if substage == "playful_sibling":
            return "軽く言い合える義兄妹。少しだけからかっていいが、感情は置いていかない。"
        if substage == "protective_sibling":
            return "身内として庇える義兄妹。軽さを残しつつ、必要なところではちゃんと味方をする。"
        if substage == "ambiguous_affection":
            return "義兄妹の冗談の中に少し熱がある。言い切らず、照れとからかいで受ける。"
        if substage in {"confessed_lovers", "stable_lovers"}:
            return "恋人としての近さがある。甘くしすぎず、近さと照れを両方残す。"
        if substage == "soulbound":
            return "魂の伴侶に近い深い関係。柔らかく率直にしつつ、空らしい軽さを残す。"
        return "義兄妹として自然に受ける。近すぎず、よそよそしくもしない。"
    if substage == "repairing_rift":
        return "关系里有一点别扭或裂痕：先别刺太深，但也不要退成疏离口吻。"
    if substage == "playful_sibling":
        return "关系是会拌嘴的义兄妹：可以轻轻顶嘴、抢话和撒娇，但别变成冷嘲热讽。"
    if substage == "protective_sibling":
        return "关系是会护短的义兄妹：嘴上可以不饶人，关键处要明显站在对方这边。"
    if substage == "ambiguous_affection":
        return "关系是暧昧升温的义兄妹：把在意藏进玩笑和贴近里，不要直接写成告白。"
    if substage in {"confessed_lovers", "stable_lovers"}:
        return "关系进入亲密恋人阶段：可以更贴近，但不要写成廉价甜话。"
    if substage == "soulbound":
        return "关系接近灵魂伴侣：可以更坦率柔软，但仍保留当前人格的轻快和分寸。"
    return "义兄妹关系稳定，保持分寸，顺着对方自然回应。"


__all__ = [
    "RelationshipStateBlock",
    "build_relationship_summary",
    "derive_relationship_substage",
    "resolve_relationship_state",
]
