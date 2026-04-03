"""BodySenseModel：身体感知近似——低优先级债务修复（v1.12）。

职责：
  1. 疲劳度（fatigue）：对话时长 → 疲劳积累，影响回复意愿
  2. 困倦感（drowsiness）：深夜/凌晨时段 → 困倦积累，影响表达风格
  3. 孤独感（loneliness）：长期无互动 → 孤独积累（复用 LonelinessAccumulator 数据）

设计原则：
  - 纯逻辑层，不直接持有数据源引用
  - 三维感知值均为 [0.0, 1.0] 浮点数
  - 提供 BodySenseState 快照，供 prompt_builder 注入
  - 提供 get_willingness_penalty() 接口，供 EmotionalSilenceEngine 接入
  - 随时间自然恢复（休息/睡眠后疲劳消退）

身体感知触发条件：
  - 疲劳：单次对话时长 > 30 分钟开始积累，> 2 小时达到峰值
  - 困倦：23:00-06:00 时段，凌晨 2-4 点最强
  - 孤独：无互动时长 > 4 小时开始积累（由外部注入 loneliness_value）

感知分级（三维独立）：
  疲劳：[0.0, 0.3) FRESH → [0.3, 0.6) TIRED → [0.6, 0.85) WEARY → [0.85, 1.0] EXHAUSTED
  困倦：[0.0, 0.3) ALERT → [0.3, 0.6) SLEEPY → [0.6, 0.85) DROWSY → [0.85, 1.0] ASLEEP
  孤独：[0.0, 0.3) FINE → [0.3, 0.6) LONELY → [0.6, 0.85) LONGING → [0.85, 1.0] ACHING
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.body_sense")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 疲劳积累速率（每分钟对话时长 → 疲劳增量）
_FATIGUE_RATE_PER_MINUTE = 0.008          # 约 125 分钟对话达到 1.0
_FATIGUE_RECOVERY_PER_MINUTE = 0.015      # 休息时恢复更快（约 67 分钟恢复）
_FATIGUE_PEAK_MINUTES = 120.0             # 2 小时对话达到峰值

# 困倦时段配置（小时 → 困倦强度）
_DROWSY_HOUR_MAP: dict[int, float] = {
    23: 0.25,   # 23:00 开始有点困
    0:  0.50,   # 午夜
    1:  0.70,   # 凌晨 1 点
    2:  0.85,   # 凌晨 2 点（最困）
    3:  0.90,   # 凌晨 3 点（最困）
    4:  0.80,   # 凌晨 4 点
    5:  0.55,   # 凌晨 5 点
    6:  0.20,   # 早上 6 点（快醒了）
}
_DROWSY_RECOVERY_PER_MINUTE = 0.01        # 白天困倦自然消退

# 孤独感分级阈值（由外部注入，这里只做分级）
_LONELINESS_THRESHOLD_LONELY  = 0.3
_LONELINESS_THRESHOLD_LONGING = 0.6
_LONELINESS_THRESHOLD_ACHING  = 0.85

# 疲劳对回复意愿的惩罚系数
_FATIGUE_WILLINGNESS_PENALTY_MAX = 0.35   # 极度疲劳最多降低 35% 意愿

# 困倦对表达风格的影响（注入 prompt 的文本）
_DROWSY_STYLE_HINTS: dict[str, str] = {
    "ALERT":   "",
    "SLEEPY":  "你有点困了，说话语气慵懒，偶尔会有些迟钝。",
    "DROWSY":  "你很困，说话简短，反应慢一点，偶尔会说‘好困’之类的话。",
    "ASLEEP":  "你困到快睡着了，只能说很短的话，语气迷迷糊糊的。",
}

# 疲劳对表达风格的影响
_FATIGUE_STYLE_HINTS: dict[str, str] = {
    "FRESH":     "",
    "TIRED":     "你有点累了，说话比平时少一些，但还好。",
    "WEARY":     "你挺累的，说话简短，不太想展开聊太多。",
    "EXHAUSTED": "你非常疲惫，说话极简，语气有些无力。",
}

# 孤独感对表达风格的影响
_LONELINESS_STYLE_HINTS: dict[str, str] = {
    "FINE":    "",
    "LONELY":  "你有点孤独，见到对方有些高兴，说话比平时温柔一点。",
    "LONGING": "你很孤独，等了很久，说话里带着一点思念和委屈。",
    "ACHING":  "你孤独感很强，等了太久，说话里有明显的思念，可能会提到等待。",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BodySenseState:
    """身体感知状态快照——供 prompt_builder 注入。"""

    fatigue: float = 0.0
    """疲劳度 [0.0, 1.0]。"""

    fatigue_level: str = "FRESH"
    """疲劳分级：FRESH / TIRED / WEARY / EXHAUSTED。"""

    drowsiness: float = 0.0
    """困倦感 [0.0, 1.0]。"""

    drowsiness_level: str = "ALERT"
    """困倦分级：ALERT / SLEEPY / DROWSY / ASLEEP。"""

    loneliness: float = 0.0
    """孤独感 [0.0, 1.0]（由外部注入）。"""

    loneliness_level: str = "FINE"
    """孤独分级：FINE / LONELY / LONGING / ACHING。"""

    body_sense_hint: str = ""
    """给 LLM 的身体感知指令（综合三维，自然语言）。"""

    willingness_penalty: float = 0.0
    """对回复意愿的惩罚值 [0.0, 0.35]，供 EmotionalSilenceEngine 接入。"""

    is_active: bool = False
    """是否有任何维度处于非基线状态。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fatigue": round(self.fatigue, 4),
            "fatigue_level": self.fatigue_level,
            "drowsiness": round(self.drowsiness, 4),
            "drowsiness_level": self.drowsiness_level,
            "loneliness": round(self.loneliness, 4),
            "loneliness_level": self.loneliness_level,
            "body_sense_hint": self.body_sense_hint,
            "willingness_penalty": round(self.willingness_penalty, 4),
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# BodySenseModel
# ---------------------------------------------------------------------------

class BodySenseModel:
    """身体感知近似模型（v1.12 新增）。

    用法：
        model = BodySenseModel()

        # 每次对话开始时通知
        model.on_conversation_start()

        # 每次对话结束时通知（传入对话时长分钟数）
        model.on_conversation_end(duration_minutes=45.0)

        # 每分钟 tick（由 NatsumeScheduler 驱动）
        model.tick()

        # 注入孤独感（来自 OfflineInnerWorld.LonelinessAccumulator）
        model.update_loneliness(loneliness_value=0.65)

        # 获取状态快照
        state = model.get_state()
        if state.is_active:
            prompt += state.body_sense_hint
    """

    def __init__(self) -> None:
        # 疲劳度 [0.0, 1.0]
        self._fatigue: float = 0.0
        # 困倦感 [0.0, 1.0]（由时段计算）
        self._drowsiness: float = 0.0
        # 孤独感 [0.0, 1.0]（由外部注入）
        self._loneliness: float = 0.0
        # 当前对话开始时间戳（毫秒），0 表示不在对话中
        self._conversation_start_ms: int = 0
        # 上次 tick 时间戳（毫秒）
        self._last_tick_ms: int = _now_ms()
        # 是否正在对话中
        self._in_conversation: bool = False

    # ── 对话生命周期 ──────────────────────────────────────────────────────

    def on_conversation_start(self) -> None:
        """通知对话开始，开始计时疲劳积累。"""
        self._conversation_start_ms = _now_ms()
        self._in_conversation = True
        _logger.debug("BodySenseModel：对话开始，疲劳计时启动")

    def on_conversation_end(self, *, duration_minutes: float | None = None) -> None:
        """通知对话结束，根据对话时长积累疲劳。

        Args:
            duration_minutes: 对话时长（分钟）。如果为 None，则自动计算。
        """
        if duration_minutes is None and self._conversation_start_ms > 0:
            elapsed_ms = _now_ms() - self._conversation_start_ms
            duration_minutes = elapsed_ms / 60000.0

        self._in_conversation = False
        self._conversation_start_ms = 0

        if duration_minutes and duration_minutes > 0:
            self._accumulate_fatigue(duration_minutes)

    def _accumulate_fatigue(self, duration_minutes: float) -> None:
        """根据对话时长积累疲劳度。

        算法：
        - 前 30 分钟：无疲劳积累（热身期）
        - 30-120 分钟：线性积累（每分钟 +0.008）
        - 超过 120 分钟：积累速率减半（边际递减）
        """
        if duration_minutes <= 30.0:
            return  # 30 分钟内不积累疲劳

        effective_minutes = duration_minutes - 30.0
        if effective_minutes <= 90.0:
            # 30-120 分钟：正常速率
            delta = effective_minutes * _FATIGUE_RATE_PER_MINUTE
        else:
            # 超过 120 分钟：前 90 分钟正常，后面减半
            delta = 90.0 * _FATIGUE_RATE_PER_MINUTE + (effective_minutes - 90.0) * _FATIGUE_RATE_PER_MINUTE * 0.5

        self._fatigue = min(1.0, self._fatigue + delta)
        _logger.debug(
            "BodySenseModel：疲劳积累 +%.3f（对话 %.1f 分钟），当前疲劳=%.3f",
            delta, duration_minutes, self._fatigue,
        )

    # ── 孤独感注入 ────────────────────────────────────────────────────────

    def update_loneliness(self, loneliness_value: float) -> None:
        """注入孤独感值（来自 OfflineInnerWorld.LonelinessAccumulator）。

        Args:
            loneliness_value: 孤独感 [0.0, 1.0]
        """
        self._loneliness = float(max(0.0, min(1.0, loneliness_value)))

    # ── Tick（时段困倦 + 疲劳恢复）────────────────────────────────────────

    def tick(self, *, current_hour: int | None = None) -> dict[str, Any]:
        """每分钟调用一次，更新困倦感和疲劳恢复。

        Args:
            current_hour: 当前小时（0-23）。如果为 None，则自动获取本地时间。

        Returns:
            {"fatigue": float, "drowsiness": float, "loneliness": float}
        """
        now = _now_ms()
        elapsed_minutes = (now - self._last_tick_ms) / 60000.0
        self._last_tick_ms = now

        # 更新困倦感（基于时段）
        if current_hour is None:
            current_hour = _local_hour()
        target_drowsiness = _DROWSY_HOUR_MAP.get(current_hour, 0.0)

        if target_drowsiness > self._drowsiness:
            # 困倦增加：快速趋近目标值（每分钟 +0.03）
            self._drowsiness = min(target_drowsiness, self._drowsiness + 0.03 * elapsed_minutes)
        else:
            # 困倦消退：缓慢恢复（每分钟 -0.01）
            self._drowsiness = max(target_drowsiness, self._drowsiness - _DROWSY_RECOVERY_PER_MINUTE * elapsed_minutes)
        self._drowsiness = round(max(0.0, min(1.0, self._drowsiness)), 4)

        # 疲劳恢复（不在对话中时自然恢复）
        if not self._in_conversation and self._fatigue > 0.0:
            recovery = _FATIGUE_RECOVERY_PER_MINUTE * elapsed_minutes
            self._fatigue = max(0.0, self._fatigue - recovery)
            self._fatigue = round(self._fatigue, 4)

        return {
            "fatigue": self._fatigue,
            "drowsiness": self._drowsiness,
            "loneliness": self._loneliness,
        }

    # ── 状态快照 ──────────────────────────────────────────────────────────

    def get_state(self) -> BodySenseState:
        """获取当前身体感知状态快照。

        Returns:
            BodySenseState 快照，供 prompt_builder 注入。
        """
        fatigue_level = _classify_fatigue(self._fatigue)
        drowsiness_level = _classify_drowsiness(self._drowsiness)
        loneliness_level = _classify_loneliness(self._loneliness)

        # 计算对回复意愿的惩罚（仅疲劳影响意愿，困倦影响风格）
        willingness_penalty = 0.0
        if fatigue_level != "FRESH":
            # 疲劳越高，意愿惩罚越大（线性映射到 [0, 0.35]）
            willingness_penalty = min(
                _FATIGUE_WILLINGNESS_PENALTY_MAX,
                max(0.0, self._fatigue - 0.3) / 0.7 * _FATIGUE_WILLINGNESS_PENALTY_MAX,
            )
        willingness_penalty = round(willingness_penalty, 4)

        # 构建综合 body_sense_hint（优先级：困倦 > 疲劳 > 孤独）
        hints: list[str] = []
        drowsy_hint = _DROWSY_STYLE_HINTS.get(drowsiness_level, "")
        fatigue_hint = _FATIGUE_STYLE_HINTS.get(fatigue_level, "")
        lonely_hint = _LONELINESS_STYLE_HINTS.get(loneliness_level, "")

        if drowsy_hint:
            hints.append(drowsy_hint)
        if fatigue_hint:
            hints.append(fatigue_hint)
        if lonely_hint:
            hints.append(lonely_hint)

        body_sense_hint = " ".join(hints).strip()

        is_active = (
            fatigue_level != "FRESH"
            or drowsiness_level != "ALERT"
            or loneliness_level != "FINE"
        )

        return BodySenseState(
            fatigue=round(self._fatigue, 4),
            fatigue_level=fatigue_level,
            drowsiness=round(self._drowsiness, 4),
            drowsiness_level=drowsiness_level,
            loneliness=round(self._loneliness, 4),
            loneliness_level=loneliness_level,
            body_sense_hint=body_sense_hint,
            willingness_penalty=willingness_penalty,
            is_active=is_active,
        )

    def get_willingness_penalty(self) -> float:
        """获取对回复意愿的惩罚值，供 EmotionalSilenceEngine 接入。

        Returns:
            惩罚值 [0.0, 0.35]，0.0 表示无惩罚。
        """
        return self.get_state().willingness_penalty

    # ── 查询 ──────────────────────────────────────────────────────────────

    @property
    def fatigue(self) -> float:
        """当前疲劳度。"""
        return self._fatigue

    @property
    def drowsiness(self) -> float:
        """当前困倦感。"""
        return self._drowsiness

    @property
    def loneliness(self) -> float:
        """当前孤独感。"""
        return self._loneliness

    def reset(self) -> None:
        """重置身体感知（测试用）。"""
        self._fatigue = 0.0
        self._drowsiness = 0.0
        self._loneliness = 0.0
        self._conversation_start_ms = 0
        self._in_conversation = False


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _classify_fatigue(fatigue: float) -> str:
    """将疲劳度分类为等级字符串。"""
    if fatigue < 0.3:
        return "FRESH"
    elif fatigue < 0.6:
        return "TIRED"
    elif fatigue < 0.85:
        return "WEARY"
    else:
        return "EXHAUSTED"


def _classify_drowsiness(drowsiness: float) -> str:
    """将困倦感分类为等级字符串。"""
    if drowsiness < 0.3:
        return "ALERT"
    elif drowsiness < 0.6:
        return "SLEEPY"
    elif drowsiness < 0.85:
        return "DROWSY"
    else:
        return "ASLEEP"


def _classify_loneliness(loneliness: float) -> str:
    """将孤独感分类为等级字符串。"""
    if loneliness < _LONELINESS_THRESHOLD_LONELY:
        return "FINE"
    elif loneliness < _LONELINESS_THRESHOLD_LONGING:
        return "LONELY"
    elif loneliness < _LONELINESS_THRESHOLD_ACHING:
        return "LONGING"
    else:
        return "ACHING"


def _now_ms() -> int:
    """返回当前时间戳（毫秒）。"""
    return int(time.time() * 1000)


def _local_hour() -> int:
    """返回本地时间的小时数（0-23）。"""
    import datetime
    return datetime.datetime.now().hour


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_GLOBAL_BODY_SENSE: BodySenseModel | None = None


def get_body_sense_model() -> BodySenseModel:
    """获取全局身体感知模型单例（懒加载）。"""
    global _GLOBAL_BODY_SENSE
    if _GLOBAL_BODY_SENSE is None:
        _GLOBAL_BODY_SENSE = BodySenseModel()
        _logger.info("BodySenseModel 全局单例已初始化")
    return _GLOBAL_BODY_SENSE
