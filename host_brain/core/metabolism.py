"""Hard-state metabolism helpers for governor snapshot generation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any, Mapping

from host_brain.contracts.host_hard_state import HOST_SYS_MODES
from host_brain.core.ids import now_ms
from host_brain.core.runtime_helpers import _optional_text as _as_optional_text
from natsume_contracts.edge.v1 import emotion_tag_from_vad


ALLOWED_SYS_MODES = HOST_SYS_MODES
ALLOWED_MOOD_STATES = {"calm", "steady", "engaged", "tired", "critical"}


# ---------------------------------------------------------------------------
# M28-A：EmotionVector 情绪连续体（VAD 三维模型）（OPT-1）
# ---------------------------------------------------------------------------

# VAD 模型：Valence（效价）/ Arousal（唤醒度）/ Dominance（支配感）
# 所有维度范围 [-1.0, 1.0]，0.0 为中性
# 可通过环境变量 HOST_EMOTION_DECAY_RATE 覆盖
_EMOTION_DECAY_RATE = float(os.environ.get("HOST_EMOTION_DECAY_RATE", "0.05"))


@dataclass
class EmotionVector:
    """VAD 三维情绪连续体（M28-A OPT-1）。

    Attributes:
        valence: 效价，[-1.0, 1.0]，正值=积极，负值=消极
        arousal: 唤醒度，[-1.0, 1.0]，正值=兴奋，负值=平静
        dominance: 支配感，[-1.0, 1.0]，正值=主导，负值=顺从
        ts_ms: 最后更新时间戳（毫秒）
    """
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    ts_ms: int = 0

    def __post_init__(self) -> None:
        self.valence = _clamp_float(self.valence)
        self.arousal = _clamp_float(self.arousal)
        self.dominance = _clamp_float(self.dominance)

    def decay(self, rate: float = _EMOTION_DECAY_RATE) -> "EmotionVector":
        """情绪衰减：向中性（0）靠近。"""
        r = max(0.0, min(1.0, float(rate)))
        return EmotionVector(
            valence=self.valence * (1.0 - r),
            arousal=self.arousal * (1.0 - r),
            dominance=self.dominance * (1.0 - r),
            ts_ms=self.ts_ms,
        )

    def blend(self, other: "EmotionVector", weight: float = 0.3) -> "EmotionVector":
        """情绪混合：将 other 以 weight 权重混入当前情绪。"""
        w = max(0.0, min(1.0, float(weight)))
        return EmotionVector(
            valence=self.valence * (1.0 - w) + other.valence * w,
            arousal=self.arousal * (1.0 - w) + other.arousal * w,
            dominance=self.dominance * (1.0 - w) + other.dominance * w,
            ts_ms=self.ts_ms,
        )

    def to_mood_state(self) -> str:
        """将 VAD 向量映射为离散 mood_state（向后兼容）。"""
        # 基于 valence + arousal 的简单映射规则
        if self.valence < -0.5 and self.arousal < -0.3:
            return "critical"
        if self.valence < -0.2 or self.arousal < -0.4:
            return "tired"
        if self.valence > 0.3 and self.arousal > 0.3:
            return "engaged"
        if abs(self.valence) < 0.2 and abs(self.arousal) < 0.2:
            return "calm"
        return "steady"

    def to_emotion_tag(self) -> str:
        """将 VAD 向量映射为离散 emotion_tag。"""
        return emotion_tag_from_vad(self.valence, self.arousal, self.dominance)

    # 方向 C：情绪渐变插值——用于 VTS 表情过渡帧
    @staticmethod
    def interpolate(
        start: "EmotionVector",
        end: "EmotionVector",
        t: float,
    ) -> "EmotionVector":
        """两个情绪向量之间的线性插值（方向 C：VTS 渐变过渡）。

        Args:
            start: 起始情绪向量
            end: 目标情绪向量
            t: 插值系数 [0.0, 1.0]，0.0=start, 1.0=end

        Returns:
            插值后的 EmotionVector（emotion_tag 由 VAD 值决定）
        """
        t = max(0.0, min(1.0, float(t)))
        return EmotionVector(
            valence=start.valence * (1.0 - t) + end.valence * t,
            arousal=start.arousal * (1.0 - t) + end.arousal * t,
            dominance=start.dominance * (1.0 - t) + end.dominance * t,
            ts_ms=end.ts_ms,
        )

    def distance(self, other: "EmotionVector") -> float:
        """方向 C：计算两个情绪向量的欧氏距离（用于判断是否需要过渡帧）。"""
        import math
        return math.sqrt(
            (self.valence - other.valence) ** 2
            + (self.arousal - other.arousal) ** 2
            + (self.dominance - other.dominance) ** 2
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "ts_ms": self.ts_ms,
            "mood_state": self.to_mood_state(),
            "emotion_tag": self.to_emotion_tag(),
        }


def _clamp_float(value: float, low: float = -1.0, high: float = 1.0) -> float:
    """将浮点数截断到 [low, high] 范围。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    return max(low, min(high, v))


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
    # M28-E：情感事件语义扩展（OPT-13）
    kind = str(event_kind or "").strip().lower()
    if kind == "emotion_spike":
        # 情感激增：能量上升，mood 向 engaged 靠近
        energy = min(100, energy + 5)
        effective_mood = "engaged" if energy >= 60 else mood_state
    elif kind == "emotion_calm":
        # 情感平静：mood 向 calm/steady 靠近
        effective_mood = "calm" if energy >= 40 else mood_state
    elif kind == "bond_deepen":
        # 关系深化：能量小幅上升
        energy = min(100, energy + 3)
        effective_mood = mood_state
    else:
        effective_mood = mood_state
    normalized_mood = _normalize_mood(effective_mood, energy=energy, event_kind=kind)
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
    allowlist_raw = merged.get(
        "scope_allowlist",
        ("edge", "host", "memory", "system", "task_runtime"),
    )
    if isinstance(allowlist_raw, (list, tuple, set)):
        allowlist = [str(item).strip().lower() for item in allowlist_raw if str(item).strip()]
    else:
        allowlist = ["host", "memory", "system", "task_runtime"]
    if not allowlist:
        allowlist = ["host", "memory", "system", "task_runtime"]

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
            # M29-K（OPT-?）：情感时间戳，用于判断情感状态是否过期
            "emotion_ts_ms": int(merged.get("emotion_ts_ms") or state.ts_ms or 0),
        },
        # M27-C：情感字段透传到 GateSnapshot（OPT-22）
        "mood_state": merged.get("mood_state", state.mood_state),
        "emotion_tag": merged.get("emotion_tag", "NEUTRAL"),
        "emotion_intensity": float(merged.get("emotion_intensity", 0.5)),
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


# ---------------------------------------------------------------------------
# M28-B：HardStateSnapshot 增加情感策略字段（OPT-6）
# ---------------------------------------------------------------------------

# mood_state → 中文情感范围提示
_MOOD_RANGE_HINTS: dict[str, str] = {
    "critical": "当前状态极度疲惫，只能表达平静或悲伤",
    "tired": "当前状态疲惫，适合平静、温柔的情感表达",
    "calm": "当前状态平和，适合思考性、平静的情感表达",
    "steady": "当前状态正常，可以表达多种情感",
    "engaged": "当前状态活跃，可以表达积极、兴奋的情感",
}


@dataclass(frozen=True)
class HardStateSnapshot:
    """包含情感策略字段的硬状态快照（M28-B OPT-6）。"""
    mood_state: str
    energy_level: int
    allowed_emotion_tags: tuple[str, ...]
    emotion_range_hint: str
    # M29-K（OPT-18）：情感状态时间戳，支持时效性判断
    emotion_ts_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mood_state": self.mood_state,
            "energy_level": self.energy_level,
            "allowed_emotion_tags": list(self.allowed_emotion_tags),
            "emotion_range_hint": self.emotion_range_hint,
            "emotion_ts_ms": self.emotion_ts_ms,
        }


def build_hard_snapshot(state: HardState, *, ts_ms: int | None = None) -> HardStateSnapshot:
    """从 HardState 构建 HardStateSnapshot（M28-B OPT-6；M29-K OPT-18 增加 emotion_ts_ms）。"""
    allowed = mood_to_emotion_constraint(state.mood_state)
    hint = _MOOD_RANGE_HINTS.get(state.mood_state, _MOOD_RANGE_HINTS["steady"])
    emotion_ts = int(ts_ms if ts_ms is not None else now_ms())
    return HardStateSnapshot(
        mood_state=state.mood_state,
        energy_level=state.energy_level,
        allowed_emotion_tags=tuple(allowed),
        emotion_range_hint=hint,
        emotion_ts_ms=emotion_ts,
    )


# ---------------------------------------------------------------------------
# M28-M：apply_visual_emotion_hint（OPT-35）
# ---------------------------------------------------------------------------

# 视觉情感提示 → EmotionVector 增量映射
_VISUAL_EMOTION_DELTA: dict[str, dict[str, float]] = {
    "HAPPY": {"valence": 0.3, "arousal": 0.2},
    "EXCITED": {"valence": 0.4, "arousal": 0.5},
    "SAD": {"valence": -0.3, "arousal": -0.2},
    "ANGRY": {"valence": -0.4, "arousal": 0.4},
    "SURPRISED": {"valence": 0.1, "arousal": 0.5},
    "ANXIOUS": {"valence": -0.2, "arousal": 0.3},
    "FRUSTRATED": {"valence": -0.3, "arousal": 0.2},
    "NEUTRAL": {"valence": 0.0, "arousal": 0.0},
    "THINKING": {"valence": 0.0, "arousal": 0.1},
    "CALM": {"valence": 0.1, "arousal": -0.2},
}


def apply_visual_emotion_hint(
    current_vector: "EmotionVector",
    user_emotion_hint: str,
    *,
    weight: float = 0.2,
) -> "EmotionVector":
    """将用户视觉情感提示混入当前 EmotionVector（M28-M OPT-35）。

    Args:
        current_vector: 当前情绪向量
        user_emotion_hint: 视觉识别到的用户情感标签（如 "HAPPY"）
        weight: 混合权重（0.0~1.0），默认 0.2（视觉信号权重较低）

    Returns:
        混合后的新 EmotionVector
    """
    delta = _VISUAL_EMOTION_DELTA.get(str(user_emotion_hint or "").upper(), {})
    if not delta:
        return current_vector
    target = EmotionVector(
        valence=_clamp_float(current_vector.valence + delta.get("valence", 0.0)),
        arousal=_clamp_float(current_vector.arousal + delta.get("arousal", 0.0)),
        dominance=current_vector.dominance,
        ts_ms=current_vector.ts_ms,
    )
    return current_vector.blend(target, weight=weight)


# ---------------------------------------------------------------------------
# M29-D：主动情感触发器（OPT-5）
# ---------------------------------------------------------------------------

# 情感触发规则：(条件函数, 触发情感标签, 触发强度)
_PROACTIVE_TRIGGER_RULES: list[tuple[Any, str, float]] = []


def register_emotion_trigger(
    condition_fn: Any,
    emotion_tag: str,
    intensity: float = 0.5,
) -> None:
    """注册主动情感触发规则（M29-D OPT-5）。

    Args:
        condition_fn: 接受 context_data dict 并返回 bool 的函数
        emotion_tag: 触发时的情感标签
        intensity: 触发强度（[0.0, 1.0]）
    """
    _PROACTIVE_TRIGGER_RULES.append((condition_fn, str(emotion_tag).upper(), float(intensity)))


def evaluate_emotion_triggers(context_data: dict[str, Any]) -> list[dict[str, Any]]:
    """评估所有已注册的主动情感触发规则（M29-D OPT-5）。

    Returns:
        触发的情感事件列表，每项包含 emotion_tag 和 intensity
    """
    triggered: list[dict[str, Any]] = []
    for condition_fn, emotion_tag, intensity in _PROACTIVE_TRIGGER_RULES:
        try:
            if condition_fn(context_data):
                triggered.append({
                    "emotion_tag": emotion_tag,
                    "intensity": intensity,
                    "source": "proactive_trigger",
                })
        except Exception:
            pass
    return triggered
