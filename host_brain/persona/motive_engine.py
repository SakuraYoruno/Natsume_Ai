"""MotiveEngine：统一动机中枢。

职责：
  把当前分散在多个模块中的驱动因子整合成
  统一、可解释、可观测的行为倾向向量。

  不负责生成内容，只回答：
    - 现在有多想靠近 / 联系对方（reach_out_drive）
    - 现在有多想照顾 / 安抚对方（care_drive）
    - 现在有多想往回收 / 少说话（withdraw_drive）
    - 现在有多想延续之前的话题 / 关系线（continuity_drive）
    - 现在有多需要保护边界 / 避免消耗（self_protect_drive）

设计原则：
  - 纯规则层，第一版不做任何学习，不调用 LLM
  - 五个驱动力可同时高（允许张力共存，不强制互斥）
  - 每个驱动力来源可解释，reason_tags 记录因子链
  - 与 ResponsePolicyLayer 是互补关系：
      Motive Engine 决定"更倾向于做什么"
      ResponsePolicyLayer 决定"允许做到什么程度"
  - 输出注入 runtime_payload["motive_state"]

与其他模块的关系：
  - proactive_voice：读 reach_out_drive / continuity_drive 决定是否主动
  - emotional_silence：withdraw_drive / self_protect_drive 可增强沉默权重
  - response_policy：motive_state 可作为补充输入
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

_logger = logging.getLogger("Natsume.motive_engine")


# ── 常量 ─────────────────────────────────────────────────────────────────────

# 离线时长因子映射（小时 → reach_out 增益）
_OFFLINE_REACH_OUT_TABLE: list[tuple[float, float]] = [
    (72.0, 0.55),  # > 3 天
    (24.0, 0.40),  # > 1 天
    (8.0,  0.25),  # > 8 小时
    (3.0,  0.15),  # > 3 小时
    (1.0,  0.05),  # > 1 小时
    (0.0,  0.0),
]

# Bond 阶段对应的基础 care 加成
_BOND_CARE_BONUS: dict[str, float] = {
    "SOULMATE": 0.30,
    "INTIMATE": 0.25,
    "CLOSE": 0.18,
    "FRIEND": 0.10,
    "ACQUAINTANCE": 0.04,
    "STRANGER": 0.0,
}

# willingness 低值对应 withdraw 增益
_WILLINGNESS_WITHDRAW_TABLE: list[tuple[float, float]] = [
    (0.15, 0.60),
    (0.30, 0.45),
    (0.50, 0.25),
    (0.70, 0.10),
    (1.01, 0.0),
]

# 身体疲劳对 withdraw / self_protect 增益
_FATIGUE_WITHDRAW: dict[str, float] = {
    "EXHAUSTED": 0.50,
    "HIGH": 0.40,
    "WEARY": 0.28,
    "TIRED": 0.18,
    "FRESH": 0.0,
}
_FATIGUE_PROTECT: dict[str, float] = {
    "EXHAUSTED": 0.45,
    "HIGH": 0.35,
    "WEARY": 0.22,
    "TIRED": 0.12,
    "FRESH": 0.0,
}


def _coerce_runtime_flags(raw: dict[str, Any] | None) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _resolve_sys_mode(runtime_flags: dict[str, Any]) -> str:
    mode = str(runtime_flags.get("sys_mode") or "").strip().lower()
    if mode in {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}:
        return mode
    activity_monitor = runtime_flags.get("activity_monitor")
    if isinstance(activity_monitor, dict) and bool(activity_monitor.get("is_gaming")):
        return "gaming"
    return "normal"


def _resolve_activity_monitor(runtime_flags: dict[str, Any]) -> dict[str, Any]:
    activity_monitor = runtime_flags.get("activity_monitor")
    return dict(activity_monitor) if isinstance(activity_monitor, dict) else {}


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MotiveInput:
    """汇总状态输入快照。所有字段均为可选；None 表示无对应状态。"""

    silence_state: dict[str, Any] | None = None
    body_sense: dict[str, Any] | None = None
    offline_state: dict[str, Any] | None = None
    bond_summary: dict[str, Any] | None = None
    self_reflection_pulse: dict[str, Any] | None = None
    emotion_context: dict[str, Any] | None = None
    pending_followup_topics: list[str] | None = None
    shared_memory_count: int | None = None
    user_message: str = ""
    runtime_flags: dict[str, Any] | None = None


@dataclass
class MotiveState:
    """统一动机向量，所有驱动力为 [0.0, 1.0] 浮点数。"""

    reach_out_drive: float = 0.0
    care_drive: float = 0.0
    withdraw_drive: float = 0.0
    continuity_drive: float = 0.0
    self_protect_drive: float = 0.0

    dominant_motive: str = "none"
    motive_reason_tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reach_out_drive": round(self.reach_out_drive, 4),
            "care_drive": round(self.care_drive, 4),
            "withdraw_drive": round(self.withdraw_drive, 4),
            "continuity_drive": round(self.continuity_drive, 4),
            "self_protect_drive": round(self.self_protect_drive, 4),
            "dominant_motive": self.dominant_motive,
            "motive_reason_tags": list(self.motive_reason_tags),
        }


# ── 核心计算层 ────────────────────────────────────────────────────────────────

class MotiveEngine:
    """统一动机中枢，纯规则版本。

    调用方式：
        state = MotiveEngine().compute(motive_input)
    """

    def compute(self, inp: MotiveInput) -> MotiveState:
        silence = dict(inp.silence_state or {})
        body = dict(inp.body_sense or {})
        offline = dict(inp.offline_state or {})
        bond = dict(inp.bond_summary or {})
        reflection = dict(inp.self_reflection_pulse or {})
        emotion = dict(inp.emotion_context or {})
        runtime_flags = _coerce_runtime_flags(inp.runtime_flags)
        sys_mode = _resolve_sys_mode(runtime_flags)
        activity_monitor = _resolve_activity_monitor(runtime_flags)

        reasons: list[str] = []

        # 解包常用值
        willingness = float(silence.get("response_willingness") or 1.0)
        topic_resistance = bool(silence.get("topic_resistance_active") or False)
        fatigue_level = str(body.get("fatigue_level") or "FRESH").upper()
        drowsy_level = str(body.get("drowsiness_level") or "AWAKE").upper()
        offline_hours = float(offline.get("offline_hours") or 0.0)
        loneliness = float(offline.get("loneliness_level") or 0.0)
        anticipation = float(offline.get("anticipation_level") or 0.0)
        anticipation_topics = list(offline.get("anticipation_topics") or [])
        bond_stage = str(bond.get("stage") or "STRANGER").strip().upper()
        shared_count = int(inp.shared_memory_count or bond.get("shared_memory_count") or 0)
        pending_topics = list(inp.pending_followup_topics or [])
        emotion_intensity = float(emotion.get("emotion_intensity") or 0.0)
        emotion_tag = str(emotion.get("emotion_tag") or "").upper()
        user_distress = emotion_tag in {"SAD", "HURT", "SCARED", "LONELY", "DEPRESSED", "ANXIOUS"}
        withdraw_hint = bool(reflection.get("withdraw_hint") or False)

        # ── reach_out_drive ───────────────────────────────────────────────────
        reach = 0.0

        # 离线时长加成
        offline_bonus = _lookup_stepped(offline_hours, _OFFLINE_REACH_OUT_TABLE)
        reach += offline_bonus
        if offline_bonus > 0:
            reasons.append(f"reach_out:offline={offline_hours:.1f}h")

        # 孤独感加成
        if loneliness >= 0.6:
            reach += 0.20
            reasons.append(f"reach_out:loneliness={loneliness:.2f}")
        elif loneliness >= 0.35:
            reach += 0.10

        # 期待值加成
        if anticipation >= 0.5:
            reach += 0.12
            reasons.append("reach_out:anticipation_high")

        # Bond 深度加成
        if bond_stage in {"SOULMATE", "INTIMATE", "CLOSE"}:
            reach += 0.08

        # 待续话题加成
        if anticipation_topics or pending_topics:
            reach += 0.10
            reasons.append("reach_out:pending_topics")

        # 疲劳惩罚
        if fatigue_level in {"EXHAUSTED", "HIGH"}:
            reach -= 0.20
        elif fatigue_level in {"WEARY", "TIRED"}:
            reach -= 0.10

        # 低意愿惩罚
        reach -= (1.0 - willingness) * 0.30

        # ── care_drive ────────────────────────────────────────────────────────
        care = 0.0

        # 用户情绪低落/强烈
        if user_distress:
            care += 0.35
            reasons.append(f"care:user_distress={emotion_tag}")
        elif emotion_intensity >= 0.7:
            care += 0.20
            reasons.append(f"care:emotion_intensity={emotion_intensity:.2f}")

        # Bond 深度基础 care
        care += _BOND_CARE_BONUS.get(bond_stage, 0.0)

        # 久别重逢
        if offline_hours >= 24.0:
            care += 0.10
            reasons.append("care:long_reunion")

        # 疲劳惩罚
        if fatigue_level in {"EXHAUSTED", "HIGH"}:
            care -= 0.15
        if topic_resistance:
            care -= 0.12

        # ── withdraw_drive ────────────────────────────────────────────────────
        withdraw = 0.0

        # 低意愿
        will_withdraw = _lookup_stepped(willingness, _WILLINGNESS_WITHDRAW_TABLE)
        withdraw += will_withdraw
        if will_withdraw >= 0.30:
            reasons.append(f"withdraw:willingness={willingness:.2f}")

        # 疲劳
        fatigue_w = _FATIGUE_WITHDRAW.get(fatigue_level, 0.0)
        withdraw += fatigue_w
        if fatigue_w >= 0.25:
            reasons.append(f"withdraw:fatigue={fatigue_level}")

        # 困倦
        if drowsy_level in {"HIGH", "HEAVY"}:
            withdraw += 0.20
            reasons.append(f"withdraw:drowsy={drowsy_level}")
        elif drowsy_level in {"MED", "DROWSY"}:
            withdraw += 0.10

        # 话题抗拒
        if topic_resistance:
            withdraw += 0.20
            reasons.append("withdraw:topic_resistance")

        # 反思收缩期
        if withdraw_hint:
            withdraw += 0.15
            reasons.append("withdraw:reflection_hint")

        # ── continuity_drive ──────────────────────────────────────────────────
        continuity = 0.0

        if anticipation_topics:
            continuity += min(0.40, 0.12 * len(anticipation_topics))
            reasons.append(f"continuity:anticipation_topics={len(anticipation_topics)}")

        if pending_topics:
            continuity += min(0.25, 0.10 * len(pending_topics))
            reasons.append(f"continuity:pending_topics={len(pending_topics)}")

        if emotion_intensity >= 0.8:
            continuity += 0.15
            reasons.append("continuity:post_intense")

        if shared_count >= 5:
            continuity += 0.08
        elif shared_count >= 2:
            continuity += 0.04

        # ── self_protect_drive ────────────────────────────────────────────────
        protect = 0.0

        # 低意愿
        protect += (1.0 - willingness) * 0.30

        # 疲劳
        fatigue_p = _FATIGUE_PROTECT.get(fatigue_level, 0.0)
        protect += fatigue_p
        if fatigue_p >= 0.20:
            reasons.append(f"protect:fatigue={fatigue_level}")

        # 话题抗拒
        if topic_resistance:
            protect += 0.20
            reasons.append("protect:topic_resistance")

        # 浅关系
        if bond_stage in {"STRANGER", "ACQUAINTANCE"}:
            protect += 0.15
            reasons.append("protect:shallow_bond")

        # 反思保护
        if withdraw_hint:
            protect += 0.12

        try:
            idle_minutes = float(activity_monitor.get("idle_minutes") or 0.0)
        except (TypeError, ValueError):
            idle_minutes = 0.0

        if sys_mode == "focus":
            reach -= 0.12
            continuity -= 0.08
            withdraw += 0.12
            protect += 0.10
            reasons.append("mode:focus")
        elif sys_mode == "gaming":
            reach += 0.08
            care += 0.04
            continuity += 0.10
            protect -= 0.04
            reasons.append("mode:gaming")
        elif sys_mode == "sleep":
            reach -= 0.18
            continuity -= 0.10
            withdraw += 0.16
            protect += 0.18
            reasons.append("mode:sleep")

        if idle_minutes >= 20.0 and sys_mode == "normal":
            reach += 0.06
            continuity += 0.04
            reasons.append("activity:idle")

        # ── 截断归一化 ────────────────────────────────────────────────────────
        reach = _clamp(reach)
        care = _clamp(care)
        withdraw = _clamp(withdraw)
        continuity = _clamp(continuity)
        protect = _clamp(protect)

        # ── 主导动机 ──────────────────────────────────────────────────────────
        scores = {
            "reach_out": reach,
            "care": care,
            "withdraw": withdraw,
            "continuity": continuity,
            "self_protect": protect,
        }
        dominant = max(scores, key=lambda k: scores[k])
        dominant_label = dominant if scores[dominant] > 0.05 else "none"

        state = MotiveState(
            reach_out_drive=round(reach, 4),
            care_drive=round(care, 4),
            withdraw_drive=round(withdraw, 4),
            continuity_drive=round(continuity, 4),
            self_protect_drive=round(protect, 4),
            dominant_motive=dominant_label,
            motive_reason_tags=reasons,
        )
        _logger.debug("MotiveState: %s", state.as_dict())
        return state


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _lookup_stepped(value: float, table: list[tuple[float, float]]) -> float:
    """按阶梯表查找映射值，table 按阈值从高到低排列。"""
    for threshold, result in table:
        if value >= threshold:
            return result
    return 0.0


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── 单例工厂 ──────────────────────────────────────────────────────────────────

_engine: MotiveEngine | None = None


def get_motive_engine() -> MotiveEngine:
    """返回全局 MotiveEngine 实例。"""
    global _engine
    if _engine is None:
        _engine = MotiveEngine()
    return _engine


def compute_motive_state(inp: MotiveInput) -> MotiveState:
    """便捷调用入口。

    用法：
        from host_brain.persona.motive_engine import compute_motive_state, MotiveInput
        motive = compute_motive_state(MotiveInput(
            silence_state=silence_snap,
            body_sense=body_snap,
            offline_state=offline_snap,
            bond_summary=bond_snap,
        ))
    """
    return get_motive_engine().compute(inp)
