"""Governor gate rules (R1-R8) with stable rule_trace outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    AIRLOCK_BLOCKED,
    ENERGY_LOW,
    RATE_LIMITED,
    SAFETY_BLOCKED,
    SCOPE_DENY,
    SYSMODE_BLOCKED,
    TRUST_LOW,
)


@dataclass(frozen=True)
class GateSnapshot:
    trust_level: int
    energy_level: int
    sys_mode: str
    rate_limit_hit: bool
    scope: str
    scope_allowlist: tuple[str, ...]
    safety_flag: bool
    airlock_blocked: bool
    # Rate Limit 扩展字段（由 RateLimitChecker 提供，向后兼容）
    rate_limit_backend: str | None = None
    rate_limit_degraded: bool = False
    rate_limit_degraded_from: str | None = None
    rate_limit_reason_code: str | None = None
    # M27-A：情感字段（OPT-19）
    mood_state: str = "steady"
    emotion_tag: str = "NEUTRAL"
    emotion_intensity: float = 0.5


def evaluate_governor_gates(input_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate R1-R8 in fixed order and emit stable trace."""

    snapshot = _normalize_snapshot(input_snapshot or {})
    trace: list[dict[str, Any]] = []
    decision = "APPROVE"
    reason_code = None

    for evaluate in (
        _r1_trust_gate,
        _r2_energy_gate,
        _r3_sys_mode_gate,
        _r4_rate_limit_gate,
        _r5_scope_gate,
        _r6_safety_gate,
        _r7_airlock_gate,
        _r8_emotion_gate,
    ):
        gate = evaluate(snapshot)
        trace.append(gate)
        if gate["hit"] and reason_code is None:
            decision = "REJECT"
            reason_code = gate["reason_hint"]

    return {"decision": decision, "reason_code": reason_code, "rule_trace": trace}


def _normalize_snapshot(raw: dict[str, Any]) -> GateSnapshot:
    trust_level = _clamp_int(raw.get("trust_level", 50), low=0, high=100)
    energy_level = _clamp_int(raw.get("energy_level", 60), low=0, high=100)
    sys_mode = str(raw.get("sys_mode", "normal")).strip().lower()
    rate_limit_hit = bool(raw.get("rate_limit_hit", False))
    scope = str(raw.get("scope", "host")).strip().lower()
    default_allowlist = ("edge", "host", "memory", "system")
    allowlist_raw = raw.get("scope_allowlist", list(default_allowlist))
    if isinstance(allowlist_raw, (list, tuple, set)):
        allowlist = tuple(sorted(str(x).strip().lower() for x in allowlist_raw if str(x).strip()))
    else:
        allowlist = default_allowlist
    if not allowlist:
        allowlist = default_allowlist
    safety_flag = bool(raw.get("safety_flag", False))
    airlock_blocked = bool(raw.get("airlock_blocked", False))
    # Rate Limit 扩展字段
    rl_backend = raw.get("rate_limit_backend")
    rl_degraded = bool(raw.get("rate_limit_degraded", False))
    rl_degraded_from = raw.get("rate_limit_degraded_from")
    rl_reason_code = raw.get("rate_limit_reason_code")
    return GateSnapshot(
        trust_level=trust_level,
        energy_level=energy_level,
        sys_mode=sys_mode,
        rate_limit_hit=rate_limit_hit,
        scope=scope,
        scope_allowlist=allowlist,
        safety_flag=safety_flag,
        airlock_blocked=airlock_blocked,
        rate_limit_backend=str(rl_backend) if rl_backend else None,
        rate_limit_degraded=rl_degraded,
        rate_limit_degraded_from=str(rl_degraded_from) if rl_degraded_from else None,
        rate_limit_reason_code=str(rl_reason_code) if rl_reason_code else None,
        mood_state=str(raw.get("mood_state", "steady")).strip().lower() or "steady",
        emotion_tag=str(raw.get("emotion_tag", "NEUTRAL")).strip().upper() or "NEUTRAL",
        emotion_intensity=float(raw.get("emotion_intensity", 0.5)),
    )


def _r1_trust_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    min_trust = 30
    hit = snapshot.trust_level < min_trust
    return {
        "gate_id": "R1_TRUST",
        "hit": hit,
        "threshold_summary": {"min_trust_bucket": ">=30"},
        "snapshot_summary": {"trust_bucket": _bucket(snapshot.trust_level)},
        "reason_hint": TRUST_LOW if hit else "PASS",
    }


def _r2_energy_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    min_energy = 20
    hit = snapshot.energy_level < min_energy
    return {
        "gate_id": "R2_ENERGY",
        "hit": hit,
        "threshold_summary": {"min_energy_bucket": ">=20"},
        "snapshot_summary": {"energy_bucket": _bucket(snapshot.energy_level)},
        "reason_hint": ENERGY_LOW if hit else "PASS",
    }


def _r3_sys_mode_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    blocked_modes = ("locked", "maintenance")
    hit = snapshot.sys_mode in blocked_modes
    return {
        "gate_id": "R3_SYSMODE",
        "hit": hit,
        "threshold_summary": {"blocked_modes": list(blocked_modes)},
        "snapshot_summary": {"sys_mode": snapshot.sys_mode},
        "reason_hint": SYSMODE_BLOCKED if hit else "PASS",
    }


def _r4_rate_limit_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    hit = snapshot.rate_limit_hit
    # 从快照中获取 Rate Limit 详情（如果由 RateLimitChecker 提供）
    rl_backend = snapshot.rate_limit_backend
    rl_degraded = snapshot.rate_limit_degraded
    rl_degraded_from = snapshot.rate_limit_degraded_from
    rl_reason_code = snapshot.rate_limit_reason_code

    threshold_summary: dict[str, Any] = {"window": "global_bucket_window"}
    if rl_backend:
        threshold_summary["backend"] = rl_backend
    if rl_degraded:
        threshold_summary["degraded"] = True
        threshold_summary["degraded_from"] = rl_degraded_from

    snapshot_summary: dict[str, Any] = {"rate_limit_hit": hit}
    if rl_backend:
        snapshot_summary["backend"] = rl_backend
    if rl_degraded and rl_reason_code:
        snapshot_summary["degraded_reason_code"] = rl_reason_code

    return {
        "gate_id": "R4_RATE_LIMIT",
        "hit": hit,
        "threshold_summary": threshold_summary,
        "snapshot_summary": snapshot_summary,
        "reason_hint": RATE_LIMITED if hit else "PASS",
    }


def _r5_scope_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    hit = snapshot.scope not in snapshot.scope_allowlist
    return {
        "gate_id": "R5_SCOPE",
        "hit": hit,
        "threshold_summary": {"allowlist": list(snapshot.scope_allowlist)},
        "snapshot_summary": {"scope": snapshot.scope},
        "reason_hint": SCOPE_DENY if hit else "PASS",
    }


def _r6_safety_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    hit = snapshot.safety_flag
    return {
        "gate_id": "R6_SAFETY",
        "hit": hit,
        "threshold_summary": {"safety_flag_required": False},
        "snapshot_summary": {"safety_flag": snapshot.safety_flag},
        "reason_hint": SAFETY_BLOCKED if hit else "PASS",
    }


def _r7_airlock_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    hit = snapshot.airlock_blocked
    return {
        "gate_id": "R7_AIRLOCK",
        "hit": hit,
        "threshold_summary": {"airlock_blocked_required": False},
        "snapshot_summary": {"airlock_blocked": snapshot.airlock_blocked},
        "reason_hint": AIRLOCK_BLOCKED if hit else "PASS",
    }


def _r8_emotion_gate(snapshot: GateSnapshot) -> dict[str, Any]:
    """R8 情感感知门控（M28-D OPT-8）。

    规则：
    - mood_state="critical" 且 emotion_tag 为 ANGRY/FRUSTRATED 时，拒绝请求并提示降级
    - mood_state="critical" 时，注入 simplified_prompt=True 标记
    """
    mood = str(snapshot.mood_state or "").strip().lower()
    emotion = str(snapshot.emotion_tag or "").strip().upper()

    angry_emotions = {"ANGRY", "FRUSTRATED"}
    simplified_prompt = mood == "critical"

    # critical + ANGRY/FRUSTRATED 组合：触发 R8 拒绝，明显提示降级
    hit = mood == "critical" and emotion in angry_emotions
    reason = "EMOTION_GATE_BLOCKED" if hit else "PASS"

    return {
        "gate_id": "R8_EMOTION",
        "hit": hit,
        "threshold_summary": {
            "blocked_combinations": [{"mood": "critical", "emotions": list(angry_emotions)}],
            "critical_mood_simplified": True,
        },
        "snapshot_summary": {
            "mood_state": mood,
            "emotion_tag": emotion,
            "simplified_prompt": simplified_prompt,
        },
        "reason_hint": reason,
        "simplified_prompt": simplified_prompt,
        # 降级明显提示：hit 时附加用户可见的降级说明
        "degraded_hint": (
            "⚠️ [DEGRADED] 当前情感状态（critical+愤怒）已触发 R8 门控，请求被拒绝，"
            "请稍后在情绪平稳后重试。"
            if hit else None
        ),
    }


def _bucket(value: int) -> str:
    if value < 20:
        return "very_low"
    if value < 40:
        return "low"
    if value < 70:
        return "normal"
    return "high"


def _clamp_int(value: Any, *, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    if number < low:
        return low
    if number > high:
        return high
    return number
