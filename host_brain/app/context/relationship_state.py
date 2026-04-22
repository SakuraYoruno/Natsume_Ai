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


_STAGE_BASE: dict[str, dict[str, float]] = {
    "STRANGER": {"familiarity": 0.08, "trust": 0.06, "comfort": 0.10, "openness": 0.08, "dependency_tolerance": 0.05, "protectiveness": 0.10, "boundary_softness": 0.08, "affection_signal": 0.02},
    "ACQUAINTANCE": {"familiarity": 0.18, "trust": 0.14, "comfort": 0.18, "openness": 0.14, "dependency_tolerance": 0.10, "protectiveness": 0.15, "boundary_softness": 0.14, "affection_signal": 0.05},
    "FRIEND": {"familiarity": 0.42, "trust": 0.38, "comfort": 0.42, "openness": 0.36, "dependency_tolerance": 0.26, "protectiveness": 0.30, "boundary_softness": 0.34, "affection_signal": 0.16},
    "CLOSE": {"familiarity": 0.56, "trust": 0.52, "comfort": 0.56, "openness": 0.46, "dependency_tolerance": 0.34, "protectiveness": 0.38, "boundary_softness": 0.44, "affection_signal": 0.22},
    "CLOSE_FRIEND": {"familiarity": 0.62, "trust": 0.58, "comfort": 0.62, "openness": 0.52, "dependency_tolerance": 0.40, "protectiveness": 0.44, "boundary_softness": 0.50, "affection_signal": 0.28},
    "CONFIDANT": {"familiarity": 0.74, "trust": 0.70, "comfort": 0.74, "openness": 0.64, "dependency_tolerance": 0.52, "protectiveness": 0.52, "boundary_softness": 0.62, "affection_signal": 0.40},
    "INTIMATE": {"familiarity": 0.82, "trust": 0.80, "comfort": 0.82, "openness": 0.74, "dependency_tolerance": 0.64, "protectiveness": 0.60, "boundary_softness": 0.72, "affection_signal": 0.56},
    "SOULMATE": {"familiarity": 0.94, "trust": 0.92, "comfort": 0.94, "openness": 0.88, "dependency_tolerance": 0.80, "protectiveness": 0.68, "boundary_softness": 0.86, "affection_signal": 0.72},
}


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
    bond_stage: str = "STRANGER"

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
            "bond_stage": self.bond_stage,
        }


def resolve_relationship_state(
    *,
    bond_summary: Mapping[str, Any] | None,
    social_brain_state: Mapping[str, Any] | None = None,
) -> RelationshipStateBlock:
    bond = _safe_mapping(bond_summary)
    social = _safe_mapping(social_brain_state)
    stage = str(
        social.get("bond_stage")
        or bond.get("stage")
        or "STRANGER"
    ).strip().upper() or "STRANGER"
    base = dict(_STAGE_BASE.get(stage, _STAGE_BASE["STRANGER"]))

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

    if trend in {"rising", "progress", "warming"}:
        openness = _clamp(openness + 0.04)
        boundary_softness = _clamp(boundary_softness + 0.03)
        affection_signal = _clamp(affection_signal + 0.03)
    elif trend in {"declining", "regress", "cooling"}:
        openness = _clamp(openness - 0.05)
        boundary_softness = _clamp(boundary_softness - 0.05)
        trust_score = _clamp(trust_score - 0.04)

    return RelationshipStateBlock(
        familiarity=familiarity,
        trust=trust_score,
        comfort=comfort,
        openness=openness,
        dependency_tolerance=dependency_tolerance,
        protectiveness=protectiveness,
        boundary_softness=boundary_softness,
        affection_signal=affection_signal,
        bond_stage=stage,
    )


def build_relationship_summary(*, relationship: RelationshipStateBlock, render_target_lang: str = "zh") -> str:
    lang = str(render_target_lang or "").strip().lower()
    if lang == "ja":
        return (
            f"相手との関係段階は{relationship.bond_stage.lower()}寄り。"
            f" 信頼は{relationship.trust:.2f}、開示許容量は{relationship.openness:.2f}、"
            f" 距離の柔らかさは{relationship.boundary_softness:.2f}。"
        )
    return (
        f"你和对方当前仍偏{relationship.bond_stage}阶段，"
        f"信任约{relationship.trust:.2f}，开放度约{relationship.openness:.2f}，"
        f"边界松软度约{relationship.boundary_softness:.2f}。"
    )


__all__ = ["RelationshipStateBlock", "build_relationship_summary", "resolve_relationship_state"]
