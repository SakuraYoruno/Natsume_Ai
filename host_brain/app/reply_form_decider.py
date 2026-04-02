"""reply_form_decider.py — Gateway 智能回复形式策略（Phase XV P4）。

设计理念：文字是日常，语音是惊喜——偶尔一条语音消息，比每次都发更打动人。

决策规则优先级：
  1. 长回复（>80字）→ 永远文字
  2. 低亲密度（STRANGER/ACQUAINTANCE）→ 0% 语音
  3. 深夜时段（23:00-02:00）→ 语音概率减半（不打扰睡眠）
  4. 每日语音上限（默认3次）→ 超出后不再触发语音
  5. 最小间隔保护（默认10分钟）→ 两次语音之间至少间隔10分钟
  6. 热词场景（晚安/加油/抱抱/辛苦了）→ 文字+语音双发
  7. 高亲密+强情感+短回复 → 概率触发语音
  8. 用户发语音 → 偶尔镜像回语音（20%）
  9. 默认 → 文字

双发模式：先文字字幕（中文），后语音消息（日语 TTS）。
"""

from __future__ import annotations

import datetime
import os
import random
import time
from dataclasses import dataclass, field
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

# 每日语音发送上限
DEFAULT_DAILY_VOICE_LIMIT = 3

# 两次语音之间的最小间隔（毫秒）：10 分钟
DEFAULT_MIN_VOICE_INTERVAL_MS = 10 * 60 * 1000

# 深夜时段（23:00-02:00）语音概率倍率
_LATE_NIGHT_PROB_SCALE = 0.5


class ReplyFormDecider:
    """Gateway 智能回复形式决策器（Phase XV P4）。

    根据情感标签、Bond 阶段、回复长度、用户消息类型动态选择
    Gateway 回复形式（文字 / 语音 / 文字+语音双发）。

    设计原则：
      - 文字是基础，语音是惊喜
      - 概率性触发，不机械镜像用户
      - 关系越深，语音越多
      - 长回复永远文字（方便阅读）
      - 深夜不打扰，每日有上限，两次之间有间隔
    """

    def __init__(
        self,
        *,
        voice_enabled: bool | None = None,
        probability_scale: float | None = None,
        daily_voice_limit: int = DEFAULT_DAILY_VOICE_LIMIT,
        min_voice_interval_ms: int = DEFAULT_MIN_VOICE_INTERVAL_MS,
    ) -> None:
        """
        Args:
            voice_enabled: 是否启用语音回复（默认读 HOST_GATEWAY_VOICE_REPLY_ENABLED）
            probability_scale: 语音概率倍率（默认读 HOST_GATEWAY_VOICE_PROBABILITY_SCALE）
            daily_voice_limit: 每日语音发送上限（默认3次）
            min_voice_interval_ms: 两次语音之间的最小间隔毫秒数（默认10分钟）
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

        self._daily_voice_limit = max(1, int(daily_voice_limit))
        self._min_voice_interval_ms = max(0, int(min_voice_interval_ms))

        # user_id → {"date": str, "count": int, "last_voice_ts_ms": int}
        self._voice_stats: dict[str, dict] = {}

    def decide(
        self,
        *,
        user_id: str = "default",
        user_msg_type: MsgType = MsgType.TEXT,
        narrative_zh: str,
        emotion_tag: str = "NEUTRAL",
        bond_stage: str = "STRANGER",
    ) -> ReplyFormDecision:
        """根据上下文智能选择 Gateway 回复形式。

        Args:
            user_id: 用户 ID（用于每日计数和间隔保护）
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
        uid = str(user_id or "default").strip()
        now_ts = _now_ms()

        # ── 规则 1：长回复永远文字（语音太长不友好，读起来费时）
        if length > 80:
            return ReplyFormDecision(MsgType.TEXT, None, "long_reply")

        # ── 规则 2：低亲密度不发语音
        if stage in ("STRANGER", "ACQUAINTANCE"):
            return ReplyFormDecision(MsgType.TEXT, None, "low_intimacy")

        # ── 计算当前有效概率倍率（含深夜折扣）
        effective_scale = self._prob_scale
        if self._is_late_night(now_ts):
            effective_scale *= _LATE_NIGHT_PROB_SCALE

        # ── 规则 3：热词场景 → 文字+语音双发（最暖的体验）
        if any(kw in text for kw in _WARMTH_KEYWORDS):
            if self._can_send_voice(uid, now_ts):
                self._record_voice(uid, now_ts)
                return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "warmth_scenario")
            else:
                return ReplyFormDecision(MsgType.TEXT, None, "warmth_scenario_voice_limited")

        # ── 规则 4：高亲密+强情感+短回复 → 概率触发语音
        if etag in _VOICE_EMOTION_TAGS and length <= 40:
            base_prob = _VOICE_PROBABILITY_BY_BOND.get(stage, 0.0)
            prob = min(1.0, base_prob * effective_scale)
            if prob > 0 and random.random() < prob and self._can_send_voice(uid, now_ts):
                self._record_voice(uid, now_ts)
                return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "intimate_emotional")

        # ── 规则 5：用户发语音 → 偶尔镜像回语音（20%）
        if user_msg_type == MsgType.VOICE and length <= 30:
            base_prob = _VOICE_PROBABILITY_BY_BOND.get(stage, 0.0)
            echo_prob = min(1.0, 0.20 * effective_scale) if base_prob > 0 else 0.0
            if echo_prob > 0 and random.random() < echo_prob and self._can_send_voice(uid, now_ts):
                self._record_voice(uid, now_ts)
                return ReplyFormDecision(MsgType.VOICE, None, "voice_echo")

        # ── 默认：文字
        return ReplyFormDecision(MsgType.TEXT, None, "default_text")

    # ── 内部辅助方法 ──────────────────────────────────────────────────────

    def _can_send_voice(self, user_id: str, now_ts: int) -> bool:
        """检查是否允许发送语音（每日上限 + 最小间隔）。"""
        today = _today_str(now_ts)
        stats = self._voice_stats.get(user_id)

        if stats is None:
            return True

        # 每日计数重置
        if stats.get("date") != today:
            return True

        # 每日上限检查
        if stats.get("count", 0) >= self._daily_voice_limit:
            return False

        # 最小间隔检查
        last_ts = stats.get("last_voice_ts_ms", 0)
        if last_ts > 0 and (now_ts - last_ts) < self._min_voice_interval_ms:
            return False

        return True

    def _record_voice(self, user_id: str, now_ts: int) -> None:
        """记录一次语音发送。"""
        today = _today_str(now_ts)
        stats = self._voice_stats.get(user_id)

        if stats is None or stats.get("date") != today:
            self._voice_stats[user_id] = {
                "date": today,
                "count": 1,
                "last_voice_ts_ms": now_ts,
            }
        else:
            stats["count"] = stats.get("count", 0) + 1
            stats["last_voice_ts_ms"] = now_ts

    @staticmethod
    def _is_late_night(now_ts: int) -> bool:
        """判断是否为深夜时段（23:00-02:00）。"""
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return dt.hour >= 23 or dt.hour < 2
        except Exception:
            return False

    def get_voice_stats(self, user_id: str) -> dict:
        """获取用户当日语音发送统计（调试用）。"""
        return dict(self._voice_stats.get(str(user_id).strip(), {}))


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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _today_str(now_ts: int) -> str:
    """返回当前日期字符串 YYYY-MM-DD。"""
    try:
        dt = datetime.datetime.fromtimestamp(now_ts / 1000)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"
