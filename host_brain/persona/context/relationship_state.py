from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from host_brain.core.runtime_helpers import _coerce_float
from host_brain.persona.text_utils import _safe_mapping

def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


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


_STAGE_BASE: dict[str, dict[str, float]] = {
    "FRIEND": {"familiarity": 0.48, "trust": 0.40, "comfort": 0.46, "openness": 0.38, "dependency_tolerance": 0.28, "protectiveness": 0.34, "boundary_softness": 0.36, "affection_signal": 0.18, "playfulness": 0.40, "romantic_tension": 0.10, "attachment_depth": 0.32, "conflict_rift": 0.0},
    "CLOSE": {"familiarity": 0.56, "trust": 0.52, "comfort": 0.56, "openness": 0.46, "dependency_tolerance": 0.34, "protectiveness": 0.38, "boundary_softness": 0.44, "affection_signal": 0.22, "playfulness": 0.46, "romantic_tension": 0.16, "attachment_depth": 0.42, "conflict_rift": 0.0},
    "CLOSE_FRIEND": {"familiarity": 0.62, "trust": 0.58, "comfort": 0.62, "openness": 0.52, "dependency_tolerance": 0.40, "protectiveness": 0.44, "boundary_softness": 0.50, "affection_signal": 0.28, "playfulness": 0.50, "romantic_tension": 0.22, "attachment_depth": 0.50, "conflict_rift": 0.0},
    "CONFIDANT": {"familiarity": 0.82, "trust": 0.78, "comfort": 0.82, "openness": 0.72, "dependency_tolerance": 0.62, "protectiveness": 0.58, "boundary_softness": 0.70, "affection_signal": 0.54, "playfulness": 0.56, "romantic_tension": 0.62, "attachment_depth": 0.76, "conflict_rift": 0.0},
    "INTIMATE": {"familiarity": 0.82, "trust": 0.80, "comfort": 0.82, "openness": 0.74, "dependency_tolerance": 0.64, "protectiveness": 0.60, "boundary_softness": 0.72, "affection_signal": 0.56, "playfulness": 0.56, "romantic_tension": 0.64, "attachment_depth": 0.78, "conflict_rift": 0.0},
    "SOULMATE": {"familiarity": 0.94, "trust": 0.92, "comfort": 0.94, "openness": 0.88, "dependency_tolerance": 0.80, "protectiveness": 0.68, "boundary_softness": 0.86, "affection_signal": 0.72, "playfulness": 0.62, "romantic_tension": 0.78, "attachment_depth": 0.92, "conflict_rift": 0.0},
}


_COMPATIBILITY_BOND_AUTHORITY = "COMPATIBILITY_BOND"
_CANONICAL_RELATIONSHIP_AUTHORITY = "CANONICAL_RELATIONSHIP_LIFE"
_CANONICAL_RELATIONSHIP_REJECTED_AUTHORITY = "CANONICAL_RELATIONSHIP_REJECTED"


def _safe_text(value: Any, *, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _strict_unit(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


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
class CanonicalRelationshipProjection:
    """Validated, prompt-safe subset of canonical RelationshipStateV2.

    The projection deliberately excludes all intimacy-consent fields.  Those
    fields constrain actions elsewhere; they must never be converted into a
    relationship stage, a Bond score, or a more intimate prompt voice.
    """

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    revision: int
    trust: float
    safety: float
    attunement: float
    rupture_load: float
    repair_required: bool
    proactive_permission: str


def canonical_relationship_context_claimed(
    emotion_context: Mapping[str, Any] | None,
) -> bool:
    """Whether a hand-off claims to be the internal Affective Life context."""

    context = _safe_mapping(emotion_context)
    return bool(context.get("_affective_life_internal")) and isinstance(
        context.get("affective_life"),
        Mapping,
    )


def _strict_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def extract_canonical_relationship_projection(
    emotion_context: Mapping[str, Any] | None,
    *,
    expected_relationship_subject_id: str = "",
    expected_workspace_id: str = "",
    expected_session_id: str = "",
) -> CanonicalRelationshipProjection | None:
    """Return a canonical relationship projection only after scope validation.

    A ``RelationshipStateV2`` is accepted only when the complete internal
    AffectFusion envelope agrees with the current request scope.  This keeps
    user-provided or stale payloads from reviving the old Bond score as a
    fallback route to romance, exclusivity, or proactive permission.
    """

    if not canonical_relationship_context_claimed(emotion_context):
        return None
    context = _safe_mapping(emotion_context)
    life = _safe_mapping(context.get("affective_life"))
    if (
        _safe_text(life.get("schema_version"), limit=96)
        != "affective_turn_context.v1"
        or _safe_text(life.get("self_id"), limit=64) != "natsume_ai"
    ):
        return None

    workspace_id = _safe_text(life.get("workspace_id"), limit=240)
    relationship_subject_id = _safe_text(
        life.get("relationship_subject_id"),
        limit=240,
    )
    session_id = _safe_text(life.get("session_id"), limit=240)
    if not workspace_id or not relationship_subject_id or not session_id:
        return None
    expected_subject = _safe_text(expected_relationship_subject_id, limit=240)
    expected_workspace = _safe_text(expected_workspace_id, limit=240)
    expected_session = _safe_text(expected_session_id, limit=240)
    if not expected_subject or not expected_workspace or not expected_session:
        return None
    if (
        relationship_subject_id != expected_subject
        or workspace_id != expected_workspace
        or session_id != expected_session
    ):
        return None

    relationship_life = _safe_mapping(life.get("relationship_life"))
    if (
        _safe_text(relationship_life.get("schema_version"), limit=96)
        != "relationship_turn_context.v1"
        or _safe_text(relationship_life.get("workspace_id"), limit=240)
        != workspace_id
        or _safe_text(
            relationship_life.get("relationship_subject_id"),
            limit=240,
        )
        != relationship_subject_id
    ):
        return None

    state = _safe_mapping(relationship_life.get("state"))
    if (
        _safe_text(state.get("schema_version"), limit=96)
        != "relationship_state.v2"
        or _safe_text(state.get("self_id"), limit=64) != "natsume_ai"
        or _safe_text(state.get("workspace_id"), limit=240) != workspace_id
        or _safe_text(state.get("relationship_subject_id"), limit=240)
        != relationship_subject_id
    ):
        return None

    revision = _strict_non_negative_int(state.get("revision"))
    trust = _strict_unit(state.get("trust"))
    safety = _strict_unit(state.get("safety"))
    attunement = _strict_unit(state.get("attunement"))
    rupture_load = _strict_unit(state.get("rupture_load"))
    proactive_permission = _safe_text(
        state.get("proactive_permission"),
        limit=64,
    ).upper()
    repair_required = state.get("repair_required")
    if (
        revision is None
        or trust is None
        or safety is None
        or attunement is None
        or rupture_load is None
        or proactive_permission not in {"UNKNOWN", "ALLOWED", "DENIED"}
        or not isinstance(repair_required, bool)
    ):
        return None

    return CanonicalRelationshipProjection(
        workspace_id=workspace_id,
        relationship_subject_id=relationship_subject_id,
        session_id=session_id,
        revision=revision,
        trust=trust,
        safety=safety,
        attunement=attunement,
        rupture_load=rupture_load,
        repair_required=repair_required,
        proactive_permission=proactive_permission,
    )


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
    source_authority: str = _COMPATIBILITY_BOND_AUTHORITY

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
        authority = _safe_text(self.source_authority, limit=96).upper()
        if authority not in {
            _COMPATIBILITY_BOND_AUTHORITY,
            _CANONICAL_RELATIONSHIP_AUTHORITY,
            _CANONICAL_RELATIONSHIP_REJECTED_AUTHORITY,
        }:
            authority = _COMPATIBILITY_BOND_AUTHORITY
        object.__setattr__(self, "source_authority", authority)

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
            "source_authority": self.source_authority,
        }


def _resolve_canonical_relationship_state(
    projection: CanonicalRelationshipProjection,
) -> RelationshipStateBlock:
    """Map canonical texture into the legacy prompt vector without intimacy.

    The prompt-facing block is retained for compatibility with the existing
    reply planner.  It must not turn trust, attunement, consent, or proactive
    permission into a Bond score or a romantic stage.
    """

    base = _STAGE_BASE["FRIEND"]
    trust = max(base["trust"], projection.trust)
    comfort = min(
        0.72,
        max(base["comfort"], 0.18 + projection.safety * 0.54 + projection.attunement * 0.12),
    )
    openness = min(
        0.62,
        max(base["openness"], 0.12 + min(trust, projection.safety) * 0.45),
    )
    protectiveness = min(
        0.47,
        max(base["protectiveness"], 0.16 + projection.safety * 0.22 + projection.attunement * 0.12),
    )
    boundary_softness = min(
        0.48,
        max(base["boundary_softness"], 0.16 + projection.safety * 0.30),
    )
    affection_signal = min(
        0.34,
        max(base["affection_signal"], 0.10 + projection.attunement * 0.18),
    )
    playfulness = min(
        0.52,
        max(base["playfulness"], 0.18 + projection.safety * 0.20 + projection.attunement * 0.20),
    )
    conflict_rift = max(
        projection.rupture_load,
        0.65 if projection.repair_required else 0.0,
    )
    return RelationshipStateBlock(
        familiarity=base["familiarity"],
        trust=trust,
        comfort=comfort,
        openness=openness,
        dependency_tolerance=base["dependency_tolerance"],
        protectiveness=protectiveness,
        boundary_softness=boundary_softness,
        affection_signal=affection_signal,
        playfulness=playfulness,
        # ``RelationshipStateV2`` has explicit consent fields outside this
        # prompt projection. Never manufacture romantic tension from them.
        romantic_tension=base["romantic_tension"],
        attachment_depth=base["attachment_depth"],
        conflict_rift=conflict_rift,
        bond_stage="FRIEND",
        stage_label=_stage_label("FRIEND"),
        relationship_phase="stepsibling",
        source_authority=_CANONICAL_RELATIONSHIP_AUTHORITY,
    )


def _resolve_rejected_canonical_relationship_state() -> RelationshipStateBlock:
    """Fail closed when a claimed canonical hand-off cannot be verified."""

    return RelationshipStateBlock(
        bond_stage="FRIEND",
        stage_label=_stage_label("FRIEND"),
        relationship_phase="stepsibling",
        source_authority=_CANONICAL_RELATIONSHIP_REJECTED_AUTHORITY,
    )


def resolve_relationship_state(
    *,
    bond_summary: Mapping[str, Any] | None,
    social_brain_state: Mapping[str, Any] | None = None,
    canonical_relationship: CanonicalRelationshipProjection | None = None,
    canonical_context_claimed: bool = False,
) -> RelationshipStateBlock:
    if isinstance(canonical_relationship, CanonicalRelationshipProjection):
        return _resolve_canonical_relationship_state(canonical_relationship)
    if canonical_context_claimed:
        return _resolve_rejected_canonical_relationship_state()

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
        source_authority=_COMPATIBILITY_BOND_AUTHORITY,
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
    "CanonicalRelationshipProjection",
    "RelationshipStateBlock",
    "build_relationship_summary",
    "canonical_relationship_context_claimed",
    "derive_relationship_substage",
    "extract_canonical_relationship_projection",
    "resolve_relationship_state",
]
