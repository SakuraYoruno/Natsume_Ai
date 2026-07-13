from __future__ import annotations

from dataclasses import dataclass, field

from host_brain.persona.context.affect_state import AffectStateBlock
from host_brain.persona.context.drive_state import DriveStateBlock
from host_brain.persona.context.relationship_state import RelationshipStateBlock


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class AffectPolicyDecision:
    max_chars: int = 320
    max_sentences: int = 4
    warmth_floor: float = 0.0
    warmth_ceiling: float = 1.0
    intimacy_ceiling: float = 0.5
    humor_ceiling: float = 0.35
    allow_followup: bool = True
    allow_proactive_after_reply: bool = True
    withdrawal_guard: bool = False
    should_be_brief: bool = False
    should_be_withdrawn: bool = False
    reason_tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "max_chars": int(self.max_chars),
            "max_sentences": int(self.max_sentences),
            "warmth_floor": round(self.warmth_floor, 4),
            "warmth_ceiling": round(self.warmth_ceiling, 4),
            "intimacy_ceiling": round(self.intimacy_ceiling, 4),
            "humor_ceiling": round(self.humor_ceiling, 4),
            "allow_followup": bool(self.allow_followup),
            "allow_proactive_after_reply": bool(self.allow_proactive_after_reply),
            "withdrawal_guard": bool(self.withdrawal_guard),
            "should_be_brief": bool(self.should_be_brief),
            "should_be_withdrawn": bool(self.should_be_withdrawn),
            "reason_tags": list(self.reason_tags),
        }


def build_affect_policy(
    *,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    drive_state: DriveStateBlock,
) -> AffectPolicyDecision:
    reasons: list[str] = []
    max_chars = 320
    max_sentences = 4
    allow_followup = True
    allow_proactive_after_reply = True
    withdrawal_guard = False

    if affect_state.social_energy <= 0.35 or affect_state.withdraw_bias >= 0.55:
        max_chars = 140
        max_sentences = 2
        allow_followup = False
        allow_proactive_after_reply = False
        withdrawal_guard = True
        reasons.append("low_social_energy")
    elif affect_state.social_energy <= 0.50 or affect_state.withdraw_bias >= 0.40:
        max_chars = 220
        max_sentences = 3
        allow_proactive_after_reply = False
        reasons.append("guarded_energy")

    intimacy_ceiling = max(0.15, min(0.95, relationship_state.boundary_softness * 0.75 + relationship_state.trust * 0.18))
    humor_ceiling = max(0.10, min(0.75, affect_state.warmth_bias * 0.55 + drive_state.playfulness_drive * 0.35))
    warmth_floor = max(0.0, min(0.65, affect_state.warmth_bias * 0.55))
    warmth_ceiling = max(warmth_floor, min(0.95, 0.35 + affect_state.warmth_bias * 0.50 - affect_state.irritation_bias * 0.22))

    if drive_state.self_protection_drive >= 0.55:
        intimacy_ceiling = min(intimacy_ceiling, 0.38)
        max_chars = min(max_chars, 180)
        max_sentences = min(max_sentences, 3)
        reasons.append("self_protect_high")

    should_be_brief = max_chars <= 180 or max_sentences <= 2
    should_be_withdrawn = withdrawal_guard or drive_state.withdraw_drive >= 0.45
    if should_be_withdrawn:
        reasons.append("withdrawn")

    return AffectPolicyDecision(
        max_chars=max_chars,
        max_sentences=max_sentences,
        warmth_floor=_clamp(warmth_floor),
        warmth_ceiling=_clamp(warmth_ceiling),
        intimacy_ceiling=_clamp(intimacy_ceiling),
        humor_ceiling=_clamp(humor_ceiling),
        allow_followup=allow_followup,
        allow_proactive_after_reply=allow_proactive_after_reply,
        withdrawal_guard=withdrawal_guard,
        should_be_brief=should_be_brief,
        should_be_withdrawn=should_be_withdrawn,
        reason_tags=tuple(reasons),
    )


__all__ = ["AffectPolicyDecision", "build_affect_policy"]
