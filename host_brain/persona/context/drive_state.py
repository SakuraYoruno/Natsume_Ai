from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.core.runtime_helpers import _coerce_float
from host_brain.persona.text_utils import _safe_mapping
from .affect_state import AffectStateBlock
from .relationship_state import RelationshipStateBlock


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _dialogue_value(raw: Any, *keys: str, default: Any = "") -> Any:
    if isinstance(raw, Mapping):
        for key in keys:
            if key in raw:
                value = raw.get(key)
                if value not in (None, ""):
                    return value
        return default
    for key in keys:
        value = getattr(raw, key, None)
        if value not in (None, ""):
            return value
    return default


@dataclass(frozen=True)
class DriveStateBlock:
    reach_out_drive: float = 0.0
    withdraw_drive: float = 0.0
    continuity_drive: float = 0.0
    self_protection_drive: float = 0.0
    care_drive: float = 0.0
    repair_drive: float = 0.0
    playfulness_drive: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "reach_out_drive": round(self.reach_out_drive, 4),
            "withdraw_drive": round(self.withdraw_drive, 4),
            "continuity_drive": round(self.continuity_drive, 4),
            "self_protection_drive": round(self.self_protection_drive, 4),
            "care_drive": round(self.care_drive, 4),
            "repair_drive": round(self.repair_drive, 4),
            "playfulness_drive": round(self.playfulness_drive, 4),
        }


def resolve_drive_state(
    *,
    motive_state: Mapping[str, Any] | None,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    dialogue_state: Any,
) -> DriveStateBlock:
    motive = _safe_mapping(motive_state)
    reach_out = _clamp(_coerce_float(motive.get("reach_out_drive"), default=0.0))
    withdraw = _clamp(_coerce_float(motive.get("withdraw_drive"), default=0.0))
    continuity = _clamp(_coerce_float(motive.get("continuity_drive"), default=0.0))
    self_protect = _clamp(_coerce_float(motive.get("self_protect_drive"), default=0.0))
    care = _clamp(_coerce_float(motive.get("care_drive"), default=0.0))
    current_user_act = str(
        _dialogue_value(
            dialogue_state,
            "current_user_act",
            "literal_topic",
            default="",
        )
        or ""
    ).strip()
    user_stance = str(_dialogue_value(dialogue_state, "user_stance", default="neutral") or "neutral").strip()

    if current_user_act == "rejection":
        self_protect = _clamp(max(self_protect, affect_state.withdraw_bias * 0.75))
    if current_user_act in {"boundary_probe", "motivation_probe", "relationship_boundary", "motivation"}:
        continuity = _clamp(max(continuity, 0.24))
    if current_user_act in {"help_continuity_probe", "help_commitment"}:
        care = _clamp(max(care, 0.20))

    repair_drive = _clamp(
        0.42 if current_user_act == "rejection" else 0.18 if user_stance == "challenging" else 0.0
    )
    playfulness_drive = _clamp(
        max(0.0, affect_state.warmth_bias * 0.42 + relationship_state.openness * 0.22 - affect_state.withdraw_bias * 0.25)
    )

    return DriveStateBlock(
        reach_out_drive=reach_out,
        withdraw_drive=withdraw,
        continuity_drive=continuity,
        self_protection_drive=self_protect,
        care_drive=care,
        repair_drive=repair_drive,
        playfulness_drive=playfulness_drive,
    )


__all__ = ["DriveStateBlock", "resolve_drive_state"]
