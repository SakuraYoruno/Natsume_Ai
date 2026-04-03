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
    "INTIMATE": 0.80,
    "SOULMATE": 0.95,
}
_BOND_INTIMACY_DEFAULT = 0.45

# 默认输出约束
_DEFAULT_MAX_CHARS = 480
_DEFAULT_MAX_SENTENCES = 6
_DEFAULT_MIN_SENTENCES = 1


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
        bond_stage = str(bond.get("stage") or "STRANGER").strip().upper()
        bond_ceiling = _BOND_INTIMACY.get(bond_stage, _BOND_INTIMACY_DEFAULT)
        p.intimacy_ceiling = min(p.intimacy_ceiling, bond_ceiling)
        reasons.append(f"bond:{bond_stage}")

        # ── 规则 E：浅关系启用表白保护 ───────────────────────────────────────
        if bond_stage in {"STRANGER", "ACQUAINTANCE"}:
            p.confession_guard = True
            reasons.append("confession_guard:shallow_bond")

        # ── 规则 F：离线等待提升温度下限（但不突破现有上限）────────────────
        offline_hours = float(offline.get("offline_hours") or 0.0)
        loneliness = float(offline.get("loneliness_level") or 0.0)

        if offline_hours >= 12.0:
            p.warmth_floor = max(p.warmth_floor, 0.15)
            reasons.append("offline:long_wait")
        if loneliness >= 0.6:
            p.warmth_floor = max(p.warmth_floor, 0.10)
            reasons.append("offline:loneliness_high")

        # ── 规则 G：自我反思收缩期，轻度收敛 ────────────────────────────────
        if reflection:
            withdraw_hint = bool(reflection.get("withdraw_hint") or False)
            if withdraw_hint:
                p.max_chars = min(p.max_chars, int(p.max_chars * 0.85))
                p.confession_guard = True
                reasons.append("reflection:withdraw_hint")

        # ── 规则 H：motive_state 补充修正（可选） ────────────────────────────
        if motive:
            withdraw_drive = float(motive.get("withdraw_drive") or 0.0)
            self_protect = float(motive.get("self_protect_drive") or 0.0)
            reach_out = float(motive.get("reach_out_drive") or 0.0)

            if withdraw_drive >= 0.7:
                p.max_chars = min(p.max_chars, int(p.max_chars * 0.75))
                p.allow_followup = False
                p.should_be_withdrawn = True
                reasons.append(f"motive:withdraw_drive={withdraw_drive:.2f}")

            if self_protect >= 0.7:
                p.intimacy_ceiling = min(p.intimacy_ceiling, 0.40)
                p.confession_guard = True
                reasons.append(f"motive:self_protect={self_protect:.2f}")

            if reach_out >= 0.6 and not p.should_be_withdrawn:
                p.warmth_floor = max(p.warmth_floor, 0.20)
                reasons.append(f"motive:reach_out={reach_out:.2f}")

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
