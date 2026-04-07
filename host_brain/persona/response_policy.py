"""ResponsePolicyLayer：状态驱动的行为约束层。

职责：
  把当前已经存在的数字生命状态（silence / body_sense / offline / bond / reflection）
  从"Prompt 注入层"下沉为"系统级行为约束"。

  不负责决定写什么内容，只负责约束：
    - 输出长度上限
    - 是否允许追问
    - 是否允许话题扩展
    - 是否允许主动性
    - 亲密度上限
    - 幽默上限
    - 是否启用表白/过度依赖保护

设计原则：
  - 纯规则层，不调用 LLM，不访问外部服务
  - 所有规则幂等、可解释、可复现
  - willingness 优先级最高，其余因子叠加影响
  - 允许驱动力张力共存（例如疲劳 + 孤独同时高）
  - 输出的 policy 供 prompt_builder / post_processor / planner 消费

与其他模块的关系：
  - 输入来自 chat_context_builder 汇总的状态快照
  - 输出注入 runtime_payload["response_policy"]
  - motive_engine 输出可作为可选补充输入
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from host_brain.persona.offline_continuity import (
    classify_absence_band,
    resolve_absence_band as resolve_offline_absence_band,
    warmth_floor_for_absence_band,
)

_logger = logging.getLogger("Natsume.response_policy")


# ── 常量 ─────────────────────────────────────────────────────────────────────

# willingness 分级阈值
_WILLINGNESS_MUTE = 0.15
_WILLINGNESS_SILENT = 0.30
_WILLINGNESS_LOW = 0.50

# 身体感分级阈值
_BODY_HIGH = 0.65
_BODY_MED = 0.40

# Bond 阶段对应 intimacy_ceiling
_BOND_INTIMACY: dict[str, float] = {
    "STRANGER": 0.15,
    "ACQUAINTANCE": 0.30,
    "FRIEND": 0.50,
    "CLOSE": 0.65,
    "CLOSE_FRIEND": 0.65,
    "INTIMATE": 0.80,
    "CONFIDANT": 0.80,
    "SOULMATE": 0.95,
}
_BOND_INTIMACY_DEFAULT = 0.45

# 默认输出约束
_DEFAULT_MAX_CHARS = 480
_DEFAULT_MAX_SENTENCES = 6
_DEFAULT_MIN_SENTENCES = 1


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


def _resolve_recent_activity_event(activity_monitor: dict[str, Any]) -> tuple[str, float]:
    event_type = str(activity_monitor.get("recent_event_type") or "").strip().lower()
    try:
        event_age_s = float(activity_monitor.get("recent_event_age_s"))
    except (TypeError, ValueError):
        event_age_s = float("inf")
    return event_type, event_age_s


def _classify_absence_band(offline_hours: float) -> str:
    return classify_absence_band(offline_hours)


def _resolve_absence_band(offline_state: dict[str, Any]) -> str:
    return resolve_offline_absence_band(offline_state)


def _offline_warmth_floor_for_absence_band(absence_band: str) -> float:
    return warmth_floor_for_absence_band(absence_band)


_MEMORY_WARM_TAGS = frozenset({"NOSTALGIC", "WARM", "GRATEFUL", "LOVING", "TOUCHED", "HAPPY"})
_MEMORY_FRAGILE_TAGS = frozenset({"SAD", "HURT", "LONELY", "ANXIOUS", "SCARED", "DEPRESSED", "MELANCHOLY"})


def _coerce_memory_persona_signal(raw: dict[str, Any] | None) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_motive_drive(motive_state: dict[str, Any] | None, key: str) -> float:
    motive = dict(motive_state or {})
    candidate = motive.get(key)
    if candidate in (None, ""):
        drives = motive.get("drives")
        if isinstance(drives, dict):
            candidate = drives.get(key)
    try:
        return float(candidate or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResponsePolicyInput:
    """汇总输入状态快照。所有字段均为可选；None 表示无对应状态。"""

    silence_state: dict[str, Any] | None = None
    body_sense: dict[str, Any] | None = None
    offline_state: dict[str, Any] | None = None
    bond_summary: dict[str, Any] | None = None
    self_reflection_pulse: dict[str, Any] | None = None
    emotion_context: dict[str, Any] | None = None
    motive_state: dict[str, Any] | None = None
    memory_persona_signal: dict[str, Any] | None = None
    user_message: str = ""
    runtime_flags: dict[str, Any] | None = None


@dataclass
class ResponsePolicyDecision:
    """行为约束决策，供下游 prompt_builder / post_processor 使用。"""

    # ── 长度控制 ──────────────────────────────────────────────────────────────
    max_chars: int = _DEFAULT_MAX_CHARS
    max_sentences: int = _DEFAULT_MAX_SENTENCES
    min_sentences: int = _DEFAULT_MIN_SENTENCES

    # ── 行为开关 ──────────────────────────────────────────────────────────────
    allow_followup: bool = True
    allow_topic_shift: bool = True
    allow_proactive_after_reply: bool = True

    # ── 风格上限 ──────────────────────────────────────────────────────────────
    warmth_floor: float = 0.0       # 最低温度（不得低于此值）
    warmth_ceiling: float = 1.0     # 最高温度
    intimacy_ceiling: float = 0.75  # 亲密度上限
    humor_ceiling: float = 0.80     # 幽默密度上限

    # ── 保护开关 ──────────────────────────────────────────────────────────────
    confession_guard: bool = False  # 防止过度表白/依赖性表达
    withdrawal_guard: bool = False  # 是否处于收缩模式

    # ── 快捷标记 ──────────────────────────────────────────────────────────────
    should_be_brief: bool = False
    should_be_withdrawn: bool = False

    # ── 可观测字段 ────────────────────────────────────────────────────────────
    policy_reason_tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_chars": self.max_chars,
            "max_sentences": self.max_sentences,
            "min_sentences": self.min_sentences,
            "allow_followup": self.allow_followup,
            "allow_topic_shift": self.allow_topic_shift,
            "allow_proactive_after_reply": self.allow_proactive_after_reply,
            "warmth_floor": self.warmth_floor,
            "warmth_ceiling": self.warmth_ceiling,
            "intimacy_ceiling": self.intimacy_ceiling,
            "humor_ceiling": self.humor_ceiling,
            "confession_guard": self.confession_guard,
            "withdrawal_guard": self.withdrawal_guard,
            "should_be_brief": self.should_be_brief,
            "should_be_withdrawn": self.should_be_withdrawn,
            "policy_reason_tags": list(self.policy_reason_tags),
        }


# ── 核心决策层 ────────────────────────────────────────────────────────────────

class ResponsePolicyLayer:
    """状态驱动的行为约束层。

    调用方式：
        policy = ResponsePolicyLayer().decide(input_state)
    """

    def decide(self, inp: ResponsePolicyInput) -> ResponsePolicyDecision:
        p = ResponsePolicyDecision()

        silence = dict(inp.silence_state or {})
        body = dict(inp.body_sense or {})
        offline = dict(inp.offline_state or {})
        bond = dict(inp.bond_summary or {})
        reflection = dict(inp.self_reflection_pulse or {})
        emotion = dict(inp.emotion_context or {})
        motive = dict(inp.motive_state or {})
        memory_persona_signal = _coerce_memory_persona_signal(inp.memory_persona_signal)
        runtime_flags = _coerce_runtime_flags(inp.runtime_flags)
        sys_mode = _resolve_sys_mode(runtime_flags)
        activity_monitor = _resolve_activity_monitor(runtime_flags)

        reasons: list[str] = []

        # ── 规则 A：回复意愿优先级最高 ──────────────────────────────────────
        willingness = float(silence.get("response_willingness") or 1.0)
        w_level = str(silence.get("willingness_level") or "NORMAL").upper()

        if w_level == "MUTE" or willingness <= _WILLINGNESS_MUTE:
            p.max_chars = 25
            p.max_sentences = 1
            p.allow_followup = False
            p.allow_topic_shift = False
            p.allow_proactive_after_reply = False
            p.should_be_brief = True
            p.should_be_withdrawn = True
            p.withdrawal_guard = True
            p.humor_ceiling = 0.1
            reasons.append("willingness:MUTE")

        elif w_level == "SILENT" or willingness <= _WILLINGNESS_SILENT:
            p.max_chars = 70
            p.max_sentences = 2
            p.allow_followup = False
            p.allow_topic_shift = False
            p.allow_proactive_after_reply = False
            p.should_be_brief = True
            p.should_be_withdrawn = True
            p.humor_ceiling = 0.2
            reasons.append("willingness:SILENT")

        elif w_level == "LOW" or willingness <= _WILLINGNESS_LOW:
            p.max_chars = 130
            p.max_sentences = 3
            p.allow_followup = False
            p.allow_topic_shift = False
            reasons.append("willingness:LOW")

        # ── 规则 B：话题抗拒直接禁扩展 ──────────────────────────────────────
        topic_resistance = bool(silence.get("topic_resistance_active") or False)
        if topic_resistance:
            p.allow_followup = False
            p.allow_topic_shift = False
            p.should_be_withdrawn = True
            reasons.append("topic_resistance:active")

        # ── 规则 C：疲劳 / 困倦压缩输出密度 ────────────────────────────────
        fatigue_level = str(body.get("fatigue_level") or "FRESH").upper()
        drowsy_level = str(body.get("drowsiness_level") or "AWAKE").upper()

        if fatigue_level in {"HIGH", "EXHAUSTED"} or drowsy_level in {"HIGH", "HEAVY"}:
            p.max_chars = min(p.max_chars, int(p.max_chars * 0.65))
            p.max_sentences = min(p.max_sentences, 2)
            p.allow_followup = False
            p.humor_ceiling = min(p.humor_ceiling, 0.25)
            reasons.append(f"body:fatigue={fatigue_level},drowsy={drowsy_level}")

        elif fatigue_level in {"TIRED", "WEARY"} or drowsy_level in {"MED", "DROWSY"}:
            p.max_chars = min(p.max_chars, int(p.max_chars * 0.80))
            p.max_sentences = min(p.max_sentences, 4)
            p.humor_ceiling = min(p.humor_ceiling, 0.50)
            reasons.append(f"body:fatigue={fatigue_level},drowsy={drowsy_level}")

        # ── 规则 D：Bond 阶段限制亲密度上限 ──────────────────────────────────
        if sys_mode == "focus":
            p.max_chars = min(p.max_chars, 120)
            p.max_sentences = min(p.max_sentences, 2)
            p.allow_topic_shift = False
            p.allow_proactive_after_reply = False
            p.should_be_brief = True
            p.humor_ceiling = min(p.humor_ceiling, 0.18)
            reasons.append("mode:focus")
        elif sys_mode == "gaming":
            p.max_chars = min(p.max_chars, 180)
            p.max_sentences = min(p.max_sentences, 3)
            p.allow_topic_shift = False
            p.warmth_floor = max(p.warmth_floor, 0.12)
            p.intimacy_ceiling = min(p.intimacy_ceiling, 0.60)
            reasons.append("mode:gaming")
        elif sys_mode == "sleep":
            p.max_chars = min(p.max_chars, 80)
            p.max_sentences = min(p.max_sentences, 2)
            p.allow_followup = False
            p.allow_topic_shift = False
            p.allow_proactive_after_reply = False
            p.should_be_brief = True
            p.withdrawal_guard = True
            p.humor_ceiling = min(p.humor_ceiling, 0.12)
            reasons.append("mode:sleep")

        try:
            idle_minutes = float(activity_monitor.get("idle_minutes") or 0.0)
        except (TypeError, ValueError):
            idle_minutes = 0.0
        recent_event_type, recent_event_age_s = _resolve_recent_activity_event(activity_monitor)
        if idle_minutes >= 20.0 and sys_mode == "normal" and not p.should_be_withdrawn:
            p.warmth_floor = max(p.warmth_floor, 0.08)
            reasons.append("activity:idle")

        if recent_event_type == "wakeup" and recent_event_age_s <= 600.0:
            p.max_chars = min(p.max_chars, 120)
            p.max_sentences = min(p.max_sentences, 2)
            p.allow_topic_shift = False
            p.allow_proactive_after_reply = False
            p.should_be_brief = True
            p.humor_ceiling = min(p.humor_ceiling, 0.18)
            p.warmth_floor = max(p.warmth_floor, 0.12)
            reasons.append("activity:wakeup")
        elif recent_event_type == "gaming_detected" and recent_event_age_s <= 900.0 and not p.should_be_withdrawn:
            p.allow_topic_shift = False
            p.max_sentences = min(p.max_sentences, 3)
            p.warmth_floor = max(p.warmth_floor, 0.14)
            reasons.append("activity:gaming_detected")
        elif recent_event_type == "gaming_exited" and recent_event_age_s <= 600.0 and sys_mode == "normal":
            p.warmth_floor = max(p.warmth_floor, 0.12)
            reasons.append("activity:gaming_exited")

        bond_stage = str(bond.get("stage") or "STRANGER").strip().upper()
        bond_ceiling = _BOND_INTIMACY.get(bond_stage, _BOND_INTIMACY_DEFAULT)
        p.intimacy_ceiling = min(p.intimacy_ceiling, bond_ceiling)
        reasons.append(f"bond:{bond_stage}")

        # ── 规则 E：浅关系启用表白保护 ───────────────────────────────────────
        if bond_stage in {"STRANGER", "ACQUAINTANCE"}:
            p.confession_guard = True
            reasons.append("confession_guard:shallow_bond")

        # ── 规则 F：离线等待提升温度下限（但不突破现有上限）────────────────
        try:
            offline_hours = float(offline.get("offline_hours") or 0.0)
        except (TypeError, ValueError):
            offline_hours = 0.0
        loneliness = float(offline.get("loneliness_level") or 0.0)
        absence_band = _resolve_absence_band(offline)
        absence_warmth_floor = _offline_warmth_floor_for_absence_band(absence_band)

        if absence_warmth_floor > 0.0:
            p.warmth_floor = max(p.warmth_floor, absence_warmth_floor)
            reasons.append(f"offline:{absence_band}")
        if loneliness >= 0.6:
            p.warmth_floor = max(p.warmth_floor, 0.10)
            reasons.append("offline:loneliness_high")

        # ── 规则 G：自我反思收缩期，轻度收敛 + narrative guardrail ───────────
        if reflection:
            withdraw_hint = bool(reflection.get("withdraw_hint") or False)
            if withdraw_hint:
                p.max_chars = min(p.max_chars, int(p.max_chars * 0.85))
                p.confession_guard = True
                reasons.append("reflection:withdraw_hint")

            narrative_guardrails_raw = reflection.get("narrative_guardrails")
            narrative_guardrails = (
                dict(narrative_guardrails_raw)
                if isinstance(narrative_guardrails_raw, dict)
                else {}
            )
            risk_level = str(narrative_guardrails.get("risk_level") or "").strip().lower()
            risk_tags = [
                str(item).strip()
                for item in (narrative_guardrails.get("risk_tags") or [])
                if str(item).strip()
            ]
            allow_expansion = bool(narrative_guardrails.get("allow_self_narrative_expansion", True))
            if risk_level in {"medium", "high"} or not allow_expansion:
                p.allow_followup = False
                p.allow_topic_shift = False
                p.allow_proactive_after_reply = False
                p.confession_guard = True
                p.withdrawal_guard = True
                p.should_be_brief = True
                p.max_sentences = min(p.max_sentences, 2 if risk_level == "high" else 3)
                p.max_chars = min(p.max_chars, 90 if risk_level == "high" else 140)
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.35 if risk_level == "high" else 0.45)
                p.humor_ceiling = min(p.humor_ceiling, 0.12 if risk_level == "high" else 0.18)
                if risk_level == "high" or "anthropomorphic_overreach" in risk_tags:
                    p.should_be_withdrawn = True
                if risk_level:
                    reasons.append(f"reflection_guard:{risk_level}")
                for tag in risk_tags:
                    reasons.append(f"reflection_guard:{tag}")

        # ── 规则 H：motive_state 补充修正（可选） ────────────────────────────
        reach_out_drive = _score_motive_drive(motive, "reach_out_drive")
        care_drive = _score_motive_drive(motive, "care_drive")
        continuity_drive = _score_motive_drive(motive, "continuity_drive")
        withdraw_drive = _score_motive_drive(motive, "withdraw_drive")
        self_protect = _score_motive_drive(motive, "self_protect_drive")
        supportive_drive = max(care_drive, continuity_drive)
        retreat_drive = max(withdraw_drive, self_protect)

        if motive:
            if withdraw_drive >= 0.7:
                p.max_chars = min(p.max_chars, int(p.max_chars * 0.75))
                p.allow_followup = False
                p.should_be_withdrawn = True
                reasons.append(f"motive:withdraw_drive={withdraw_drive:.2f}")

            if self_protect >= 0.7:
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.40)
                p.confession_guard = True
                reasons.append(f"motive:self_protect={self_protect:.2f}")

            if reach_out_drive >= 0.6 and not p.should_be_withdrawn:
                p.warmth_floor = max(p.warmth_floor, 0.20)
                reasons.append(f"motive:reach_out={reach_out_drive:.2f}")

        # ── 规则 I：记忆人格化结果影响追问 / 延续 / 回避 ────────────────────
        if memory_persona_signal:
            suppressed_count = int(memory_persona_signal.get("suppressed_memory_count") or 0)
            distorted_count = int(memory_persona_signal.get("distorted_memory_count") or 0)
            avg_distortion = float(_coerce_optional_float(memory_persona_signal.get("avg_distortion_level")) or 0.0)
            dominant_memory_emotion = str(memory_persona_signal.get("dominant_memory_emotion") or "").strip().upper()
            recall_bias = str(memory_persona_signal.get("recall_bias") or "").strip().lower()
            shared_history_resonance_count = int(memory_persona_signal.get("shared_history_resonance_count") or 0)
            protective_recoil_count = int(memory_persona_signal.get("protective_recoil_count") or 0)

            if suppressed_count > 0:
                p.allow_followup = False
                p.max_sentences = min(p.max_sentences, 3)
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.55)
                p.withdrawal_guard = True
                reasons.append(f"memory:suppressed={suppressed_count}")

            if avg_distortion >= 0.35 or recall_bias == "uncertain":
                p.allow_topic_shift = False
                p.max_chars = min(p.max_chars, int(p.max_chars * 0.90))
                p.humor_ceiling = min(p.humor_ceiling, 0.18)
                reasons.append(f"memory:distortion={avg_distortion:.2f}")
            elif distorted_count > 0:
                reasons.append(f"memory:distorted={distorted_count}")

            if protective_recoil_count > 0:
                p.allow_followup = False
                p.allow_topic_shift = False
                p.allow_proactive_after_reply = False
                p.warmth_ceiling = min(p.warmth_ceiling, 0.74)
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.42)
                p.withdrawal_guard = True
                if retreat_drive >= 0.35:
                    p.should_be_withdrawn = True
                reasons.append(f"memory:recoil={protective_recoil_count}")

            if dominant_memory_emotion in _MEMORY_FRAGILE_TAGS or recall_bias == "guarded":
                p.allow_followup = False
                p.allow_proactive_after_reply = False
                p.warmth_ceiling = min(p.warmth_ceiling, 0.78)
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.48)
                p.withdrawal_guard = True
                if retreat_drive >= supportive_drive:
                    p.should_be_withdrawn = True
                reasons.append(f"memory:fragile={dominant_memory_emotion or recall_bias or 'unknown'}")
            elif (
                dominant_memory_emotion in _MEMORY_WARM_TAGS
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and supportive_drive >= retreat_drive
            ):
                p.warmth_floor = max(p.warmth_floor, 0.16 if supportive_drive < 0.62 else 0.22)
                if p.allow_followup and supportive_drive >= 0.58:
                    p.min_sentences = max(p.min_sentences, 2)
                reasons.append(f"memory:warm={dominant_memory_emotion}")

            if (
                shared_history_resonance_count > 0
                and protective_recoil_count <= 0
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and supportive_drive >= retreat_drive
            ):
                p.warmth_floor = max(p.warmth_floor, 0.20 if supportive_drive < 0.62 else 0.26)
                if p.allow_followup and max(care_drive, continuity_drive) >= 0.52:
                    p.min_sentences = max(p.min_sentences, 2)
                reasons.append(f"memory:resonance={shared_history_resonance_count}")

            if (
                (
                    suppressed_count > 0
                    or dominant_memory_emotion in _MEMORY_FRAGILE_TAGS
                    or avg_distortion >= 0.5
                    or protective_recoil_count > 0
                )
                and retreat_drive >= 0.45
            ):
                p.allow_proactive_after_reply = False
                p.should_be_withdrawn = True
                reasons.append("memory:protective_guard")

        # ── 最终收口：保证 max >= min ──────────────────────────────────────────
        p.max_chars = max(p.max_chars, 15)
        p.max_sentences = max(p.max_sentences, p.min_sentences)
        p.warmth_floor = min(p.warmth_floor, p.warmth_ceiling)

        p.policy_reason_tags = reasons
        _logger.debug(
            "ResponsePolicy decided: max_chars=%d max_sent=%d withdrawn=%s reasons=%s",
            p.max_chars, p.max_sentences, p.should_be_withdrawn, reasons,
        )
        return p


# ── 单例工厂（供依赖注入或简单调用） ──────────────────────────────────────────

_layer: ResponsePolicyLayer | None = None


def get_response_policy_layer() -> ResponsePolicyLayer:
    """返回全局 ResponsePolicyLayer 实例。"""
    global _layer
    if _layer is None:
        _layer = ResponsePolicyLayer()
    return _layer


def decide_response_policy(inp: ResponsePolicyInput) -> ResponsePolicyDecision:
    """便捷调用入口。

    用法：
        from host_brain.persona.response_policy import decide_response_policy, ResponsePolicyInput
        policy = decide_response_policy(ResponsePolicyInput(
            silence_state=silence_snap,
            body_sense=body_snap,
            ...
        ))
    """
    return get_response_policy_layer().decide(inp)
