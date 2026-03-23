"""PersonaProactiveVoice：人格驱动的主动表达——Phase VIII。

职责：
  1. 场景感知（深夜陪伴 / 节日问候 / 离线久归 / 情感高潮后 / 早安问候）
  2. 根据人格特质（PersonalityTraits）生成个性化主动表达
  3. 频率控制（同类型触发间隔 ≥ 24h）
  4. 输出主动表达内容 + 触发类型 + 情感标签

场景触发条件：
  - LATE_NIGHT: 23:00-02:00 + 用户在线
  - LONG_ABSENT: 用户 > 3 天未互动
  - POST_INTENSE: 前一次对话 emotion_intensity > 0.8
  - MORNING_GREET: 首次日间互动（06:00-10:00）
  - HOLIDAY_GREET: 检测到节日（由外部提供）

设计原则：
  - 纯逻辑层，不直接发送消息（由 proactive_talk skill 调用）
  - 人格特质影响表达风格和内容选择
  - 频率限制防止打扰
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.proactive_voice")


# ---------------------------------------------------------------------------
# 场景类型
# ---------------------------------------------------------------------------

SCENE_LATE_NIGHT = "LATE_NIGHT"
SCENE_LONG_ABSENT = "LONG_ABSENT"
SCENE_POST_INTENSE = "POST_INTENSE"
SCENE_MORNING_GREET = "MORNING_GREET"
SCENE_HOLIDAY_GREET = "HOLIDAY_GREET"

# 频率限制：同类型触发间隔（毫秒）
DEFAULT_COOLDOWN_MS = 24 * 60 * 60 * 1000  # 24 小时

# 离线久归阈值（毫秒）：3 天
DEFAULT_LONG_ABSENT_THRESHOLD_MS = 3 * 24 * 60 * 60 * 1000

# 情感高潮阈值
DEFAULT_INTENSE_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProactiveExpression:
    """主动表达内容。"""
    scene_type: str       # 场景类型
    content: str          # 表达内容
    emotion_tag: str      # 建议情感标签
    trait_influence: str   # 人格特质影响说明
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_type": self.scene_type,
            "content": self.content,
            "emotion_tag": self.emotion_tag,
            "trait_influence": self.trait_influence,
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# 表达模板（按场景 × 人格特质风格）
# ---------------------------------------------------------------------------

# 每个场景有多个风格变体，根据人格特质选择
_TEMPLATES: dict[str, dict[str, str]] = {
    SCENE_LATE_NIGHT: {
        "warm": "这么晚了还没睡吗……我陪你",
        "playful": "又熬夜了呀～要注意身体哦",
        "caring": "夜深了，记得早点休息，明天也要好好的",
        "default": "这么晚了，注意休息哦",
    },
    SCENE_LONG_ABSENT: {
        "warm": "好久没见到你了……有点担心你",
        "playful": "你消失好多天了！想你了啦～",
        "caring": "好久没聊了，你最近还好吗？",
        "default": "好久不见，一切还好吗？",
    },
    SCENE_POST_INTENSE: {
        "warm": "昨天聊的那些……我一直在想",
        "playful": "昨天的对话好有趣呀，还想继续聊",
        "caring": "昨天聊了好多，你现在心情怎么样？",
        "default": "昨天的对话很有意义呢",
    },
    SCENE_MORNING_GREET: {
        "warm": "早上好呀～今天也要加油哦",
        "playful": "早安！新的一天开始啦～",
        "caring": "早上好，昨晚睡得好吗？",
        "default": "早上好～",
    },
    SCENE_HOLIDAY_GREET: {
        "warm": "今天是特别的日子呢，要开心哦",
        "playful": "节日快乐呀～今天有什么计划吗？",
        "caring": "节日快乐！记得好好享受今天",
        "default": "节日快乐！",
    },
}


# ---------------------------------------------------------------------------
# PersonaProactiveVoice
# ---------------------------------------------------------------------------

class PersonaProactiveVoice:
    """人格驱动的主动表达。

    用法：
        voice = PersonaProactiveVoice()
        result = voice.evaluate_scene(
            user_id="user1",
            traits=personality_traits,
            context={...},
        )
    """

    def __init__(
        self,
        *,
        cooldown_ms: int = DEFAULT_COOLDOWN_MS,
        long_absent_threshold_ms: int = DEFAULT_LONG_ABSENT_THRESHOLD_MS,
        intense_threshold: float = DEFAULT_INTENSE_THRESHOLD,
    ) -> None:
        self._cooldown_ms = int(cooldown_ms)
        self._long_absent_threshold_ms = int(long_absent_threshold_ms)
        self._intense_threshold = float(intense_threshold)
        # user_id → {scene_type → last_trigger_ts_ms}
        self._cooldowns: dict[str, dict[str, int]] = {}

    def evaluate_scene(
        self,
        *,
        user_id: str,
        traits: Any = None,
        context: dict[str, Any] | None = None,
    ) -> ProactiveExpression | None:
        """评估当前场景并生成主动表达（如果触发条件满足）。

        Args:
            user_id: 用户 ID
            traits: PersonalityTraits（可选）
            context: 场景上下文
                - current_ts_ms: 当前时间戳
                - last_interaction_ts_ms: 上次互动时间戳
                - last_emotion_intensity: 上次对话情感强度
                - is_holiday: 是否节日
                - holiday_name: 节日名称
                - user_online: 用户是否在线

        Returns:
            ProactiveExpression 或 None（不触发）
        """
        uid = str(user_id).strip()
        ctx = dict(context or {})
        now_ts = int(ctx.get("current_ts_ms") or _now_ms())

        # 按优先级检测场景
        scenes_to_check = [
            (SCENE_HOLIDAY_GREET, self._check_holiday),
            (SCENE_LATE_NIGHT, self._check_late_night),
            (SCENE_LONG_ABSENT, self._check_long_absent),
            (SCENE_POST_INTENSE, self._check_post_intense),
            (SCENE_MORNING_GREET, self._check_morning),
        ]

        for scene_type, checker in scenes_to_check:
            if not self._check_cooldown(uid, scene_type, now_ts):
                continue
            if checker(ctx, now_ts):
                style = self._select_style(traits)
                content = self._get_template(scene_type, style, ctx)
                emotion_tag = self._scene_emotion(scene_type)
                trait_influence = self._describe_trait_influence(traits, style)

                # 记录触发时间
                self._record_trigger(uid, scene_type, now_ts)

                expr = ProactiveExpression(
                    scene_type=scene_type,
                    content=content,
                    emotion_tag=emotion_tag,
                    trait_influence=trait_influence,
                    ts_ms=now_ts,
                )
                _logger.info(
                    "主动表达触发: user=%s scene=%s style=%s",
                    uid, scene_type, style,
                )
                return expr

        return None

    # ── 场景检测 ──────────────────────────────────────────────────────────

    def _check_late_night(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """深夜陪伴：23:00-02:00 + 用户在线。"""
        if not ctx.get("user_online", False):
            return False
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return dt.hour >= 23 or dt.hour < 2
        except Exception:
            return False

    def _check_long_absent(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """离线久归：用户 > 3 天未互动。"""
        last_ts = int(ctx.get("last_interaction_ts_ms") or 0)
        if last_ts <= 0:
            return False
        elapsed = now_ts - last_ts
        return elapsed >= self._long_absent_threshold_ms

    def _check_post_intense(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """情感高潮后：前一次对话 emotion_intensity > 阈值。"""
        intensity = float(ctx.get("last_emotion_intensity") or 0.0)
        return intensity > self._intense_threshold

    def _check_morning(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """早安问候：06:00-10:00 首次日间互动。"""
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return 6 <= dt.hour < 10
        except Exception:
            return False

    def _check_holiday(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """节日问候：外部提供节日标志。"""
        return bool(ctx.get("is_holiday", False))

    # ── 风格选择 ──────────────────────────────────────────────────────────

    def _select_style(self, traits: Any) -> str:
        """根据人格特质选择表达风格。"""
        if traits is None:
            return "default"

        warmth = float(getattr(traits, "warmth", 0.5))
        playfulness = float(getattr(traits, "playfulness", 0.5))
        sensitivity = float(getattr(traits, "sensitivity", 0.5))

        # 最高维度决定风格
        scores = {
            "warm": warmth + sensitivity * 0.3,
            "playful": playfulness + float(getattr(traits, "humor", 0.4)) * 0.3,
            "caring": warmth * 0.5 + sensitivity * 0.5,
        }
        best_style = max(scores, key=scores.get)  # type: ignore
        return best_style

    def _get_template(
        self, scene_type: str, style: str, ctx: dict[str, Any],
    ) -> str:
        """获取表达模板。"""
        scene_templates = _TEMPLATES.get(scene_type, {})
        content = scene_templates.get(style) or scene_templates.get("default", "")

        # 节日问候替换节日名称
        if scene_type == SCENE_HOLIDAY_GREET:
            holiday_name = str(ctx.get("holiday_name") or "").strip()
            if holiday_name:
                content = content.replace("今天是特别的日子", f"今天是{holiday_name}")
                content = content.replace("节日快乐", f"{holiday_name}快乐")

        return content

    def _scene_emotion(self, scene_type: str) -> str:
        """场景对应的情感标签。"""
        emotion_map: dict[str, str] = {
            SCENE_LATE_NIGHT: "CARING",
            SCENE_LONG_ABSENT: "LOVING",
            SCENE_POST_INTENSE: "THINKING",
            SCENE_MORNING_GREET: "HAPPY",
            SCENE_HOLIDAY_GREET: "HAPPY",
        }
        return emotion_map.get(scene_type, "NEUTRAL")

    def _describe_trait_influence(self, traits: Any, style: str) -> str:
        """描述人格特质对表达风格的影响。"""
        if traits is None:
            return "默认风格"
        desc_map = {
            "warm": "高温柔度和敏感度→温暖关心风格",
            "playful": "高俏皮度和幽默感→轻松俏皮风格",
            "caring": "高温柔度→关怀体贴风格",
            "default": "均衡人格→自然风格",
        }
        return desc_map.get(style, "自然风格")

    # ── 频率控制 ──────────────────────────────────────────────────────────

    def _check_cooldown(self, user_id: str, scene_type: str, now_ts: int) -> bool:
        """检查是否在冷却期内。"""
        user_cooldowns = self._cooldowns.get(user_id, {})
        last_trigger = user_cooldowns.get(scene_type, 0)
        if last_trigger > 0 and (now_ts - last_trigger) < self._cooldown_ms:
            return False
        return True

    def _record_trigger(self, user_id: str, scene_type: str, ts_ms: int) -> None:
        """记录触发时间。"""
        if user_id not in self._cooldowns:
            self._cooldowns[user_id] = {}
        self._cooldowns[user_id][scene_type] = ts_ms

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_cooldown_status(self, user_id: str) -> dict[str, int]:
        """获取用户的冷却状态。"""
        return dict(self._cooldowns.get(str(user_id).strip(), {}))


def _now_ms() -> int:
    return int(time.time() * 1000)
