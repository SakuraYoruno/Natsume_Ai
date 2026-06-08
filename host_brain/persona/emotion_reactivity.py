"""EmotionReactivityProfile：特质→情感反应倾向映射。

Phase II 核心模块——根据 PersonalityTraits 和情感场景，
计算情感反应的调制系数（echo_weight、arousal 偏移、dominance 偏移等）。

设计原则：
  - 纯函数式：输入 (traits, emotion_tag, bond_stage) → 输出调制系数
  - 无副作用，无状态
  - 所有映射规则可审计、可测试
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.persona.personality_traits import PersonalityTraits


# ---------------------------------------------------------------------------
# 常量：情感场景分类
# ---------------------------------------------------------------------------

# 消极情感标签
_NEGATIVE_EMOTIONS: frozenset[str] = frozenset({
    "SAD", "ANGRY", "ANXIOUS", "FRUSTRATED", "CONFUSED", "BORED",
})

# 轻度消极（幽默可介入）
_MILD_NEGATIVE_EMOTIONS: frozenset[str] = frozenset({
    "BORED", "FRUSTRATED", "CONFUSED",
})

# 积极情感标签
_POSITIVE_EMOTIONS: frozenset[str] = frozenset({
    "HAPPY", "EXCITED", "LOVING", "PROUD", "GRATEFUL", "SURPRISED",
})

# 探索/思考类
_EXPLORATORY_EMOTIONS: frozenset[str] = frozenset({
    "EXCITED", "THINKING", "SURPRISED",
})

# 深层关系阶段
_DEEP_STAGES: frozenset[str] = frozenset({
    "CLOSE_FRIEND", "CONFIDANT", "SOULMATE",
})

# ---------------------------------------------------------------------------
# 情感转变方向分类
# ---------------------------------------------------------------------------

# 正→负 转变：需要惊觉共情（"怎么了？发生什么事了？"）
_POSITIVE_TO_NEGATIVE_ALERT = "sudden_drop"
# 负→正 转变：需要欣慰鼓励（"太好了，看到你好起来真开心"）
_NEGATIVE_TO_POSITIVE_RELIEF = "recovery"
# 持续负面：需要深度陪伴（"我一直在这里"）
_SUSTAINED_NEGATIVE_COMPANION = "sustained_negative"


def _load_reactivity_llm_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("emotion_reactivity.llm_hint.prompt")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _pool_credentials_available() -> bool:
    for key in (
        "HOST_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "HOST_MINIMAX_API_KEY",
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "HOST_OPENAI_API_KEY",
    ):
        if str(os.environ.get(key) or "").strip():
            return True
    return False


# ---------------------------------------------------------------------------
# ReactivtyModifiers — 调制系数数据结构
# ---------------------------------------------------------------------------

@dataclass
class ReactivityModifiers:
    """情感反应调制系数。

    由 EmotionReactivityProfile 根据特质 + 情感场景计算得出，
    供 compute_emotion_echo() 和 ExpressionAdapter 使用。
    """

    echo_weight_delta: float = 0.0       # echo_weight 调整量（叠加到基础 0.4 上）
    arousal_delta: float = 0.0           # arousal 偏移量
    dominance_delta: float = 0.0         # dominance 偏移量
    empathy_boost: float = 0.0           # 共情提升（0-1）
    humor_deflection: float = 0.0        # 幽默化解倾向（0-1）
    curiosity_follow_up: float = 0.0     # 追问倾向（0-1）
    transition_alert: str = ""           # 情感转变方向标记（sudden_drop/recovery/sustained_negative）
    transition_intensity: float = 0.0    # 情感转变强度（0-1），越高说明转变越剧烈

    def as_dict(self) -> dict[str, float]:
        return {
            "echo_weight_delta": round(self.echo_weight_delta, 4),
            "arousal_delta": round(self.arousal_delta, 4),
            "dominance_delta": round(self.dominance_delta, 4),
            "empathy_boost": round(self.empathy_boost, 4),
            "humor_deflection": round(self.humor_deflection, 4),
            "curiosity_follow_up": round(self.curiosity_follow_up, 4),
            "transition_alert": self.transition_alert,
            "transition_intensity": round(self.transition_intensity, 4),
        }


# ---------------------------------------------------------------------------
# EmotionReactivityProfile — 核心计算逻辑
# ---------------------------------------------------------------------------

class EmotionReactivityProfile:
    """根据人格特质计算情感反应调制系数。

    映射规则（来自设计文档 Phase II §耦合逻辑示例）：
      - 高敏感度 + 用户消极情感 → echo_weight 提高
      - 高幽默感 + 用户轻度消极 → humor_deflection 提高
      - 高好奇心 + 用户分享新话题 → curiosity_follow_up 提高
      - 低自信度 + 用户质疑    → dominance 降低
      - 高温柔度 + 用户悲伤    → empathy_boost 提高
    """

    @staticmethod
    def compute_modifiers(
        traits: PersonalityTraits,
        user_emotion_tag: str,
        bond_stage: str = "",
        natsume_arousal: float = 0.5,
        prev_user_emotion_tag: str = "",
    ) -> ReactivityModifiers:
        """计算情感反应调制系数。

        Args:
            traits: 当前人格特质（7 维向量）
            user_emotion_tag: 用户当前情感标签
            bond_stage: 当前 Bond 阶段
            natsume_arousal: Natsume 自身当前唤醒度（0-1），影响规则8
            prev_user_emotion_tag: 用户上一轮情感标签，用于感知情感转变方向

        Returns:
            ReactivityModifiers 调制系数
        """
        tag = str(user_emotion_tag or "").strip().upper()
        stage = str(bond_stage or "").strip().upper()
        prev_tag = str(prev_user_emotion_tag or "").strip().upper()
        natsume_ar = max(0.0, min(1.0, float(natsume_arousal)))

        echo_weight_delta = 0.0
        arousal_delta = 0.0
        dominance_delta = 0.0
        empathy_boost = 0.0
        humor_deflection = 0.0
        curiosity_follow_up = 0.0
        transition_alert = ""
        transition_intensity = 0.0

        # ── 规则 1：高敏感度 + 用户消极情感 → echo_weight 提高 ──────────
        if traits.sensitivity >= 0.7 and tag in _NEGATIVE_EMOTIONS:
            # 敏感度越高，echo_weight 提升越多（最大 +0.2）
            echo_weight_delta += (traits.sensitivity - 0.5) * 0.4
            # 高敏感度遇到消极情感，arousal 微降（更加安静地陪伴）
            arousal_delta -= 0.05

        # ── 规则 2：高幽默感 + 用户轻度消极 → humor_deflection ──────────
        if traits.humor >= 0.5 and tag in _MILD_NEGATIVE_EMOTIONS:
            humor_deflection = (traits.humor - 0.3) * 0.8
            # 幽默化解时 arousal 微升
            arousal_delta += 0.03

        # ── 规则 3：高好奇心 + 用户分享新话题 → curiosity_follow_up ─────
        if traits.curiosity >= 0.6 and tag in _EXPLORATORY_EMOTIONS:
            curiosity_follow_up = (traits.curiosity - 0.4) * 0.6
            arousal_delta += 0.05

        # ── 规则 4：低自信度 + 用户质疑 → dominance 降低 ────────────────
        if traits.assertiveness <= 0.4 and tag == "ANGRY":
            dominance_delta -= (0.5 - traits.assertiveness) * 0.5

        # ── 规则 5：高温柔度 + 用户悲伤 → empathy_boost ────────────────
        if traits.warmth >= 0.7 and tag in {"SAD", "ANXIOUS", "FRUSTRATED"}:
            empathy_boost = (traits.warmth - 0.5) * 0.6
            # 深层关系阶段 → 共情加强
            if stage in _DEEP_STAGES:
                empathy_boost += 0.1

        # ── 规则 6：高俏皮度 + 积极情感 → arousal 微升 ─────────────────
        if traits.playfulness >= 0.5 and tag in _POSITIVE_EMOTIONS:
            arousal_delta += (traits.playfulness - 0.3) * 0.15

        # ── 规则 7：高韧性 + 强烈消极 → 情绪稳定，echo_weight 不过度放大 ──
        if traits.resilience >= 0.6 and tag in {"ANGRY", "SAD"}:
            # 韧性抵消部分 echo_weight 增量
            echo_weight_delta -= (traits.resilience - 0.5) * 0.15

        # ── 规则 8：Natsume 自身 arousal 高 + 用户消极 → 共情适当收敛 ────
        # 她也有自己的情绪，不是无限配合的情绪容器
        if natsume_ar >= 0.7 and tag in _NEGATIVE_EMOTIONS:
            # arousal 越高，共情提升越受抑制（最多抵消 0.1）
            empathy_boost -= (natsume_ar - 0.6) * 0.25
            # echo_weight 也略微收敛
            echo_weight_delta -= (natsume_ar - 0.6) * 0.1

        # ── 规则 9：用户 GRATEFUL → 高温柔度 Natsume 轻微害羞反应 ────────
        if tag == "GRATEFUL" and traits.warmth >= 0.6:
            # 被感谢时有点不好意思：dominance 微降（不那么主导），playfulness 微升
            dominance_delta -= 0.08
            arousal_delta += 0.04  # 心跳加速一点点
            # 深层关系阶段更明显
            if stage in _DEEP_STAGES:
                dominance_delta -= 0.05
                arousal_delta += 0.03

        # ── 规则 10：用户 THINKING → 高好奇心 Natsume 追问欲大幅提升 ────
        if tag == "THINKING" and traits.curiosity >= 0.5:
            curiosity_follow_up += (traits.curiosity - 0.3) * 0.8
            # 思考状态下 arousal 微升（被激发了兴趣）
            arousal_delta += 0.04

        # ── 规则 11：情感转变方向感知 — 正→负 突变（惊觉共情） ──────────
        # 用户上一轮是积极情感，这一轮突然变成消极情感
        # Natsume 应该表现出"怎么了？"的惊觉和关切
        if prev_tag and prev_tag in _POSITIVE_EMOTIONS and tag in _NEGATIVE_EMOTIONS:
            transition_alert = _POSITIVE_TO_NEGATIVE_ALERT
            # 转变强度：基于敏感度，越敏感越能察觉到这种突变
            transition_intensity = 0.6 + traits.sensitivity * 0.4
            # 共情大幅提升——突然变差需要更多关注
            empathy_boost += 0.15 + traits.warmth * 0.1
            # echo_weight 提升——需要更强的情感回应
            echo_weight_delta += 0.1
            # arousal 微升——惊觉反应
            arousal_delta += 0.06
            # 深层关系阶段更敏锐
            if stage in _DEEP_STAGES:
                transition_intensity = min(1.0, transition_intensity + 0.15)
                empathy_boost += 0.05

        # ── 规则 12：情感转变方向感知 — 负→正 恢复（欣慰鼓励） ──────────
        # 用户上一轮是消极情感，这一轮变成积极情感
        # Natsume 应该表现出"太好了"的欣慰和鼓励
        elif prev_tag and prev_tag in _NEGATIVE_EMOTIONS and tag in _POSITIVE_EMOTIONS:
            transition_alert = _NEGATIVE_TO_POSITIVE_RELIEF
            transition_intensity = 0.5 + traits.warmth * 0.3
            # 共情微升——分享恢复的喜悦
            empathy_boost += 0.08
            # arousal 微升——开心的反应
            arousal_delta += 0.04
            # 幽默感高的话，可以用轻松的方式表达欣慰
            if traits.humor >= 0.6:
                humor_deflection += 0.1

        # ── 规则 13：情感转变方向感知 — 持续消极（深度陪伴） ──────────────
        # 用户连续两轮都是消极情感，需要更深层的陪伴而非表面安慰
        elif prev_tag and prev_tag in _NEGATIVE_EMOTIONS and tag in _NEGATIVE_EMOTIONS:
            transition_alert = _SUSTAINED_NEGATIVE_COMPANION
            transition_intensity = 0.4 + traits.sensitivity * 0.3
            # 持续消极时，echo_weight 进一步提升——需要持续的情感回应
            echo_weight_delta += 0.05
            # 共情持续但不过度——避免情绪传染
            empathy_boost += 0.1
            # arousal 降低——安静的陪伴比激动的安慰更好
            arousal_delta -= 0.04
            # 高韧性的 Natsume 在持续消极中更稳定
            if traits.resilience >= 0.6:
                arousal_delta -= 0.02  # 更加沉稳
            # 深层关系阶段：更深的陪伴
            if stage in _DEEP_STAGES:
                empathy_boost += 0.08
                transition_intensity = min(1.0, transition_intensity + 0.1)

        return ReactivityModifiers(
            echo_weight_delta=max(-0.2, min(0.2, echo_weight_delta)),
            arousal_delta=max(-0.15, min(0.15, arousal_delta)),
            dominance_delta=max(-0.3, min(0.3, dominance_delta)),
            empathy_boost=max(0.0, min(1.0, empathy_boost)),
            humor_deflection=max(0.0, min(1.0, humor_deflection)),
            curiosity_follow_up=max(0.0, min(1.0, curiosity_follow_up)),
            transition_alert=transition_alert,
            transition_intensity=max(0.0, min(1.0, transition_intensity)),
        )

    @staticmethod
    def apply_to_echo_weight(
        base_echo_weight: float,
        modifiers: ReactivityModifiers,
    ) -> float:
        """将调制系数应用到基础 echo_weight。

        Args:
            base_echo_weight: 基础回声权重（默认 0.4）
            modifiers: 调制系数

        Returns:
            调制后的 echo_weight，范围 [0.1, 0.8]
        """
        adjusted = float(base_echo_weight) + modifiers.echo_weight_delta
        return max(0.1, min(0.8, adjusted))


def render_reactivity_hint_with_pool(
    *,
    user_emotion_tag: str,
    bond_stage: str,
    modifiers: ReactivityModifiers,
    render_target_lang: str = "zh",
    timeout_ms: int = 1800,
) -> str:
    if not _pool_credentials_available():
        return ""
    templates = _load_reactivity_llm_templates()
    if not templates:
        return ""
    try:
        from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415
    except Exception:
        return ""

    lang = "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"
    system_prompt_raw = templates.get("system_prompt")
    user_prompt_raw = templates.get("user_prompt")
    if isinstance(system_prompt_raw, Mapping):
        system_prompt = str(system_prompt_raw.get(lang) or system_prompt_raw.get("zh") or "").strip()
    else:
        system_prompt = str(system_prompt_raw or "").strip()
    if isinstance(user_prompt_raw, Mapping):
        user_prompt = str(user_prompt_raw.get(lang) or user_prompt_raw.get("zh") or "").strip()
    else:
        user_prompt = str(user_prompt_raw or "").strip()
    if not system_prompt:
        system_prompt = "你负责把情感反应调制系数转成一句可执行的情绪回应策略，只输出 JSON。"
    if not user_prompt:
        user_prompt = (
            "user_emotion_tag={user_emotion_tag}\n"
            "bond_stage={bond_stage}\n"
            "echo_weight_delta={echo_weight_delta:.3f}\n"
            "empathy_boost={empathy_boost:.3f}\n"
            "humor_deflection={humor_deflection:.3f}\n"
            "curiosity_follow_up={curiosity_follow_up:.3f}\n"
            "transition_alert={transition_alert}\n"
            "transition_intensity={transition_intensity:.3f}\n\n"
            "请输出 JSON：{{\"reactivity_hint\":\"...\"}}"
        )
    try:
        result = complete_llm_with_pool_sync(
            request_id=f"reactivity::{int(time.time() * 1000)}",
            scene_type="EMOTION_REACTIVITY",
            cognitive_tier="C",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt.format(
                        user_emotion_tag=str(user_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
                        bond_stage=str(bond_stage or "").strip().upper() or "FRIEND",
                        echo_weight_delta=float(modifiers.echo_weight_delta),
                        empathy_boost=float(modifiers.empathy_boost),
                        humor_deflection=float(modifiers.humor_deflection),
                        curiosity_follow_up=float(modifiers.curiosity_follow_up),
                        transition_alert=str(modifiers.transition_alert or "").strip() or "none",
                        transition_intensity=float(modifiers.transition_intensity),
                    ),
                },
            ],
            timeout_ms=max(800, int(timeout_ms)),
            gen_params={"temperature": 0.4},
            response_format="json_object",
        )
    except Exception:
        return ""
    if result.pool_exhausted:
        return ""
    try:
        parsed = json.loads(result.content)
    except Exception:
        return ""
    return str(parsed.get("reactivity_hint") or "").strip()
