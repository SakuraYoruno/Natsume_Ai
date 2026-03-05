"""Hard-state metabolism helpers for governor snapshot generation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


ALLOWED_SYS_MODES = {"normal", "focus", "gaming", "maintenance", "locked"}
ALLOWED_MOOD_STATES = {"calm", "steady", "engaged", "tired", "critical"}


@dataclass(frozen=True)
class HardState:
    energy_level: int
    mood_state: str
    sys_mode: str
    safety_flag: bool
    last_event_id: str | None
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "energy_level": self.energy_level,
            "mood_state": self.mood_state,
            "sys_mode": self.sys_mode,
            "safety_flag": self.safety_flag,
            "last_event_id": self.last_event_id,
            "ts_ms": self.ts_ms,
            "energy_bucket": energy_bucket(self.energy_level),
        }


def normalize_hard_state(raw: Mapping[str, Any] | None) -> HardState:
    payload = dict(raw or {})
    energy = _clamp_int(payload.get("energy_level", 60), low=0, high=100, default=60)
    mood = _normalize_mood(payload.get("mood_state"), energy=energy)
    sys_mode = _normalize_sys_mode(payload.get("sys_mode", "normal"))
    safety_flag = bool(payload.get("safety_flag", False))
    last_event_id = _as_optional_text(payload.get("last_event_id"))
    ts_ms = _clamp_int(payload.get("ts_ms", 0), low=0, high=9_999_999_999_999, default=0)
    return HardState(
        energy_level=energy,
        mood_state=mood,
        sys_mode=sys_mode,
        safety_flag=safety_flag,
        last_event_id=last_event_id,
        ts_ms=ts_ms,
    )


def evolve_hard_state(
    state: HardState,
    *,
    event_kind: str = "interaction",
    energy_delta: int = 0,
    mood_state: str | None = None,
    sys_mode: str | None = None,
    safety_flag: bool | None = None,
    event_id: str | None = None,
    ts_ms: int | None = None,
) -> HardState:
    energy = _clamp_int(state.energy_level + int(energy_delta), low=0, high=100, default=state.energy_level)
    normalized_mood = _normalize_mood(mood_state, energy=energy, event_kind=event_kind)
    normalized_mode = _normalize_sys_mode(sys_mode if sys_mode is not None else state.sys_mode)
    next_safety = bool(state.safety_flag if safety_flag is None else safety_flag)
    return HardState(
        energy_level=energy,
        mood_state=normalized_mood,
        sys_mode=normalized_mode,
        safety_flag=next_safety,
        last_event_id=_as_optional_text(event_id) or state.last_event_id,
        ts_ms=int(state.ts_ms if ts_ms is None else max(0, int(ts_ms))),
    )


def build_governance_snapshot(
    state: HardState,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(overrides or {})
    scope = str(merged.get("scope", "host")).strip().lower() or "host"
    allowlist_raw = merged.get("scope_allowlist", ("host", "memory", "system"))
    if isinstance(allowlist_raw, (list, tuple, set)):
        allowlist = [str(item).strip().lower() for item in allowlist_raw if str(item).strip()]
    else:
        allowlist = ["host", "memory", "system"]
    if not allowlist:
        allowlist = ["host", "memory", "system"]

    trust = _clamp_int(merged.get("trust_level", 50), low=0, high=100, default=50)
    rate_limit_hit = bool(merged.get("rate_limit_hit", False))
    airlock_blocked = bool(merged.get("airlock_blocked", False))
    safety_flag = bool(merged.get("safety_flag", state.safety_flag))

    return {
        "trust_level": trust,
        "energy_level": int(state.energy_level),
        "sys_mode": str(state.sys_mode),
        "rate_limit_hit": rate_limit_hit,
        "scope": scope,
        "scope_allowlist": list(allowlist),
        "safety_flag": safety_flag,
        "airlock_blocked": airlock_blocked,
        "hard_state_summary": {
            "energy_bucket": energy_bucket(state.energy_level),
            "mood_state": state.mood_state,
            "sys_mode": state.sys_mode,
            "safety_flag": safety_flag,
            "last_event_id": state.last_event_id,
        },
    }


def governance_snapshot_ref(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"gov_snap::{digest}"


def hard_state_ref(state: HardState) -> str:
    canonical = json.dumps(
        state.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"hard_state::{digest}"


def energy_bucket(energy: int) -> str:
    value = int(energy)
    if value < 20:
        return "very_low"
    if value < 40:
        return "low"
    if value < 70:
        return "normal"
    return "high"


def _normalize_mood(value: Any, *, energy: int, event_kind: str = "interaction") -> str:
    text = str(value or "").strip().lower()
    if text in ALLOWED_MOOD_STATES:
        return text
    kind = str(event_kind or "").strip().lower()
    if energy < 20:
        return "critical"
    if energy < 40:
        return "tired"
    if kind in {"reward", "success", "positive"} and energy >= 60:
        return "engaged"
    if kind in {"warning", "risk"}:
        return "calm"
    return "steady"


def _normalize_sys_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in ALLOWED_SYS_MODES:
        return mode
    return "normal"


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _clamp_int(value: Any, *, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if number < low:
        return low
    if number > high:
        return high
    return number


# ---------------------------------------------------------------------------
# M26-G：mood_state → emotion_tag 约束映射（FR-EMOTION-DRIVE-004）
# ---------------------------------------------------------------------------

# mood_state → 允许的 emotion_tag 列表（第一个为降级默认值）
MOOD_EMOTION_CONSTRAINTS: dict[str, list[str]] = {
    "critical": ["NEUTRAL", "SAD"],                              # 能量极低，只允许平静/悲伤
    "tired":    ["NEUTRAL", "CALM", "SAD"],                      # 疲惫，不允许兴奋/愤怒
    "calm":     ["NEUTRAL", "CALM", "THINKING"],                 # 平和，允许思考
    "steady":   ["NEUTRAL", "CALM", "HAPPY", "THINKING", "SAD", "ANGRY"],  # 正常范围
    "engaged":  ["HAPPY", "EXCITED", "SURPRISED", "THINKING", "NEUTRAL", "ANGRY"],  # 活跃
}


def mood_to_emotion_constraint(mood_state: str) -> list[str]:
    """返回指定 mood_state 下允许的 emotion_tag 列表。

    列表第一个元素为该 mood_state 下的降级默认值（当 emotion_tag 不在允许范围时使用）。

    Args:
        mood_state: 系统硬状态（calm/steady/engaged/tired/critical）。
                    未知值视为 "steady"。

    Returns:
        允许的 emotion_tag 列表（至少包含 "NEUTRAL"）。
    """
    normalized = str(mood_state or "").strip().lower()
    return list(MOOD_EMOTION_CONSTRAINTS.get(normalized, MOOD_EMOTION_CONSTRAINTS["steady"]))

