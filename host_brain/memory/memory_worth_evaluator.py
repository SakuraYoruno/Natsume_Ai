"""记忆写入价值的低延迟规则评估器（Phase X 方案 1）。

它只建议本轮是否值得进入后续记忆流水线，用规则过滤低价值写入、减少无效
embedding；最终的持久化、权限校验和索引投影仍由下游负责。显式的“记住/偏好”
信号会覆盖短文本启发式，避免用户主动设定被误当成寒暄丢弃。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger("Natsume.memory.worth_evaluator")

# ---------------------------------------------------------------------------
# 写入决策
# ---------------------------------------------------------------------------
DECISION_WRITE = "write"           # 值得记忆
DECISION_SKIP = "skip"             # 跳过（低价值）
DECISION_WRITE_LIGHT = "write_light"  # 轻量写入（不做 embedding，仅日志）

# ---------------------------------------------------------------------------
# 低价值模式（跳过写入）
# ---------------------------------------------------------------------------
_GREETING_PATTERN = re.compile(
    r"^[\s]*(你好|早上好|早安|晚安|午安|嗨|hello|hi|hey|おはよう|こんにちは|おやすみ)[\s!！。.~～]*$",
    re.IGNORECASE,
)
_SIMPLE_ACK_PATTERN = re.compile(
    r"^[\s]*(嗯|哦|好的|ok|好|是的|对|行|知道了|了解|收到|嗯嗯|好哒|okk|谢谢|感谢|拜拜|再见|bye)[\s!！。.~～♡🌸]*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 高价值信号（强制写入 + 打标签）
# ---------------------------------------------------------------------------
_EMOTIONAL_KEYWORDS = frozenset({
    # 中文情感词
    "难过", "伤心", "开心", "高兴", "生气", "焦虑", "害怕",
    "喜欢", "讨厌", "感动", "委屈", "寂寞", "想你", "谢谢你",
    "对不起", "抱歉", "原谅", "我爱你", "心情不好", "好累",
    # 日文情感词（平假名 / 汉字）
    "悲しい", "悲しみ", "嬉しい", "嬉しさ", "楽しい", "楽しみ",
    "怖い", "怖さ", "不安", "心配", "寂しい", "寂しさ",
    "好き", "嫌い", "感動", "ありがとう", "ごめん", "ごめんなさい",
    "すまない", "愛してる", "愛してます", "つらい", "辛い",
    "疲れた", "泣きたい", "怒り", "腹立つ", "むかつく",
})
_NEW_INFO_KEYWORDS = frozenset({
    "告诉你", "跟你说", "分享", "发生了", "今天",
    "工作", "学校", "家里", "朋友", "家人",
    "搬家", "辞职", "毕业", "结婚", "生日", "纪念日",
    "生病", "受伤", "难受",
})
_DEEP_TALK_INDICATORS = frozenset({
    "你觉得", "你怎么看", "你认为", "为什么", "人生",
    "意义", "梦想", "未来", "过去", "回忆", "感觉",
})


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
_EXPLICIT_MEMORY_OR_PREFERENCE_MARKERS = frozenset({
    "记住", "以后", "别给我", "不要推荐", "别再推荐", "我喜欢", "我不喜欢", "讨厌", "纠正", "不是",
})


@dataclass
class MemoryWorthResult:
    """纯评估结果：决策、可解释分数和标签，不包含或写入任何记忆内容。"""

    decision: str           # write / skip / write_light
    worth_score: float      # [0.0, 1.0]
    tags: list[str]         # 自动打标
    reason: str             # 判定原因

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "worth_score": round(self.worth_score, 3),
            "tags": list(self.tags),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# MemoryWorthEvaluator
# ---------------------------------------------------------------------------
class MemoryWorthEvaluator:
    """判断本轮对话是否值得写入记忆。

    评估维度：
    1. 用户消息长度（太短→低价值）
    2. AI 回复长度（太短→可能是简单应答）
    3. 情感信号（含情感关键词→高价值）
    4. 新信息信号（含事件/分享关键词→高价值）
    5. 深度对话信号（含思考类关键词→高价值）
    6. 寒暄/应答模式（正则命中→跳过）
    """

    def __init__(
        self,
        *,
        min_user_length: int = 5,
        min_response_length: int = 20,
        min_combined_length: int = 30,
    ) -> None:
        """设置长度启发式阈值；阈值只影响推荐，不改变下游写入授权。"""
        self._min_user_len = int(min_user_length)
        self._min_response_len = int(min_response_length)
        self._min_combined_len = int(min_combined_length)

    def evaluate(
        self,
        user_message: str,
        ai_response: str,
        *,
        emotion_tag: str = "",
        turn_count: int = 1,
    ) -> MemoryWorthResult:
        """评估本轮对话的记忆价值并给出可解释建议。

        优先排除空消息与纯寒暄，再综合显式记忆意图、情感、新信息和多轮上下文；
        评估不读取历史记忆，因而可在请求路径中稳定、幂等地重复执行。
        """
        user_msg = str(user_message or "").strip()
        ai_resp = str(ai_response or "").strip()
        tags: list[str] = []
        score = 0.0
        explicit_memory_signal = any(marker in user_msg for marker in _EXPLICIT_MEMORY_OR_PREFERENCE_MARKERS)

        # 1. 空消息
        if not user_msg or not ai_resp:
            return MemoryWorthResult(
                decision=DECISION_SKIP, worth_score=0.0,
                tags=[], reason="empty_message",
            )

        # 2. 纯寒暄/应答
        if _GREETING_PATTERN.match(user_msg) or _SIMPLE_ACK_PATTERN.match(user_msg):
            return MemoryWorthResult(
                decision=DECISION_SKIP, worth_score=0.05,
                tags=["greeting"], reason="simple_greeting_or_ack",
            )

        # 3. 长度评估
        user_len = len(user_msg)
        resp_len = len(ai_resp)
        combined_len = user_len

        if user_len < self._min_user_len and not explicit_memory_signal:
            return MemoryWorthResult(
                decision=DECISION_SKIP, worth_score=0.1,
                tags=["short"], reason=f"too_short(user={user_len},resp={resp_len})",
            )

        # 长度基础分
        if combined_len >= self._min_combined_len:
            score += 0.2
        if user_len >= 30:
            score += 0.1
        if explicit_memory_signal:
            score = max(score, 0.65)
            tags.append("explicit_memory_or_preference")

        # 4. 情感信号
        user_lower = user_msg.lower()
        has_emotion = any(kw in user_lower for kw in _EMOTIONAL_KEYWORDS)
        if has_emotion:
            score += 0.3
            tags.append("emotional")

        # 补充：从 emotion_tag 判断
        etag = str(emotion_tag or "").strip().upper()
        if etag and etag != "NEUTRAL":
            score += 0.15
            if "emotional" not in tags:
                tags.append("emotional")

        # 5. 新信息信号
        has_new_info = any(kw in user_lower for kw in _NEW_INFO_KEYWORDS)
        if has_new_info:
            score += 0.25
            tags.append("new_info")

        # 6. 深度对话信号
        has_deep = any(kw in user_lower for kw in _DEEP_TALK_INDICATORS)
        if has_deep:
            score += 0.2
            tags.append("deep_talk")

        # 7. 多轮对话加分（≥3 轮说明有实质交流）
        if turn_count >= 3:
            score += 0.1
            tags.append("multi_turn")

        # 归一化到 [0, 1]
        score = min(1.0, score)

        # 决策
        if score >= 0.3:
            decision = DECISION_WRITE
        elif score >= 0.15:
            decision = DECISION_WRITE_LIGHT
        else:
            decision = DECISION_SKIP

        _logger.debug(
            "MemoryWorth: decision=%s score=%.2f tags=%s user_len=%d resp_len=%d",
            decision, score, tags, user_len, resp_len,
        )

        return MemoryWorthResult(
            decision=decision, worth_score=score,
            tags=tags, reason=f"score={score:.2f}",
        )
