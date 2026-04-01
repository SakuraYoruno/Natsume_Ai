"""reply_form_decider.py — Gateway 智能回复形式策略（Phase XV P4）。

设计理念：文字是日常，语音是惊喜——偶尔一条语音消息，比每次都发更打动人。

决策规则优先级：
  1. 长回复（>80字）→ 永远文字
  2. 低亲密度（STRANGER/ACQUAINTANCE）→ 0% 语音
  3. 热词场景（晚安/加油/抱抱/辛苦了）→ 文字+语音双发
  4. 高亲密+强情感+短回复 → 概率触发语音
  5. 用户发语音 → 偶尔镜像回语音（20%）
  6. 默认 → 文字

双发模式：先文字字幕（中文），后语音消息（日语 TTS）。
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MsgType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"
    EMOJI = "emoji"


@dataclass(frozen=True)
class ReplyFormDecision:
    """Gateway 回复形式决策结果。"""
    primary: MsgType            # 主要回复形式
    secondary: Optional[MsgType]  # 附加回复形式（双发模式时使用）
    reason: str                 # 决策原因（审计/日志用）

    @property
    def is_dual(self) -> bool:
        """是否需要双发（文字+语音）。"""
        return self.secondary is not None


# ---------------------------------------------------------------------------
# 热词场景关键词
# ---------------------------------------------------------------------------

_WARMTH_KEYWORDS: frozenset[str] = frozenset({
    "晚安", "早安", "早上好", "晚上好", "加油", "抱抱", "辛苦了", "辛苦",
    "想你", "别担心", "我在呢", "没事的", "好好休息", "休息一下",
    "生日快乐", "节日快乐", "新年快乐", "祝你", "陪着你",
    "おやすみ", "おはよう", "頑張って", "ありがとう",
})

# 强情感标签（触发概率语音的情感）
_VOICE_EMOTION_TAGS: frozenset[str] = frozenset({
    "COMFORT", "COQUETTISH", "HAPPINESS", "PLAYFUL", "LOVING", "SURPRISED",
})

# 各关系阶段的语音触发概率
_VOICE_PROBABILITY_BY_BOND: dict[str, float] = {
    "STRANGER": 0.0,
    "ACQUAINTANCE": 0.0,
    "FRIEND": 0.10,
    "CLOSE_FRIEND": 0.20,
    "CONFIDANT": 0.30,
    "SOULMATE": 0.30,
}


class ReplyFormDecider:
    """Gateway 智能回复形式决策器（Phase XV P4）。

    根据情感标签、Bond 阶段、回复长度、用户消息类型动态选择
    Gateway 回复形式（文字 / 语音 / 文字+语音双发）。

    设计原则：
      - 文字是基础，语音是惊喜
      - 概率性触发，不机械镜像用户
      - 关系越深，语音越多
      - 长回复永远文字（方便阅读）
    """

    def __init__(
        self,
        *,
        voice_enabled: bool | None = None,
        probability_scale: float | None = None,
    ) -> None:
        """
        Args:
            voice_enabled: 是否启用语音回复（默认读 HOST_GATEWAY_VOICE_REPLY_ENABLED）
            probability_scale: 语音概率倍率（默认读 HOST_GATEWAY_VOICE_PROBABILITY_SCALE）
        """
        if voice_enabled is None:
            raw = str(os.environ.get("HOST_GATEWAY_VOICE_REPLY_ENABLED") or "").strip().lower()
            self._voice_enabled = raw not in ("0", "false", "no", "off")
        else:
            self._voice_enabled = bool(voice_enabled)

        if probability_scale is None:
            try:
                self._prob_scale = float(os.environ.get("HOST_GATEWAY_VOICE_PROBABILITY_SCALE") or "1.0")
            except (TypeError, ValueError):
                self._prob_scale = 1.0
        else:
            self._prob_scale = max(0.0, float(probability_scale))

    def decide(
        self,
        *,
        user_msg_type: MsgType = MsgType.TEXT,
        narrative_zh: str,
        emotion_tag: str = "NEUTRAL",
        bond_stage: str = "STRANGER",
    ) -> ReplyFormDecision:
        """根据上下文智能选择 Gateway 回复形式。

        Args:
            user_msg_type: 用户发送的消息类型
            narrative_zh: 中文回复内容（用于判断长度和热词）
            emotion_tag: 当前情感标签（NEUTRAL/HAPPY/COMFORT/...）
            bond_stage: 当前 Bond 关系阶段

        Returns:
            ReplyFormDecision
        """
        if not self._voice_enabled:
            return ReplyFormDecision(MsgType.TEXT, None, "voice_disabled")

        text = str(narrative_zh or "").strip()
        length = len(text)
        etag = str(emotion_tag or "NEUTRAL").strip().upper()
        stage = str(bond_stage or "STRANGER").strip().upper()

        # ── 规则 1：长回复永远文字（语音太长不友好，读起来费时）
        if length > 80:
            return ReplyFormDecision(MsgType.TEXT, None, "long_reply")

        # ── 规则 2：低亲密度不发语音
        if stage in ("STRANGER", "ACQUAINTANCE"):
            return ReplyFormDecision(MsgType.TEXT, None, "low_intimacy")

        # ── 规则 3：热词场景 → 文字+语音双发（最暖的体验）
        if any(kw in text for kw in _WARMTH_KEYWORDS):
            return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "warmth_scenario")

        # ── 规则 4：高亲密+强情感+短回复 → 概率触发语音
        if etag in _VOICE_EMOTION_TAGS and length <= 40:
            base_prob = _VOICE_PROBABILITY_BY_BOND.get(stage, 0.0)
            effective_prob = min(1.0, base_prob * self._prob_scale)
            if effective_prob > 0 and random.random() < effective_prob:
                return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "intimate_emotional")

        # ── 规则 5：用户发语音 → 偶尔镜像回语音（20%）
        if user_msg_type == MsgType.VOICE and length <= 30:
            base_prob = _VOICE_PROBABILITY_BY_BOND.get(stage, 0.0)
            echo_prob = min(1.0, 0.20 * self._prob_scale) if base_prob > 0 else 0.0
            if echo_prob > 0 and random.random() < echo_prob:
                return ReplyFormDecision(MsgType.VOICE, None, "voice_echo")

        # ── 默认：文字
        return ReplyFormDecision(MsgType.TEXT, None, "default_text")


# ---------------------------------------------------------------------------
# 模块级全局实例（懒加载）
# ---------------------------------------------------------------------------

_decider: ReplyFormDecider | None = None


def get_reply_form_decider() -> ReplyFormDecider:
    """获取全局 ReplyFormDecider 实例（懒加载单例）。"""
    global _decider
    if _decider is None:
        _decider = ReplyFormDecider()
    return _decider
