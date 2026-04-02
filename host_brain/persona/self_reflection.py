"""SelfReflection：定期自省内容生成器——Phase III + 事件驱动增强。

职责：
  1. 概率控制自省触发（每 5-10 轮触发一次）
  2. 事件驱动触发：特定情感标签出现时提升触发概率
  3. 调用 SelfModel 生成快照
  4. 将快照转化为 self_reflection_pulse 槽位数据（含触发原因）
  5. 保证自省内容与当前 Bond 阶段/情感趋势一致

设计原则：
  - 轻量级：不直接持有数据源引用，而是接收已准备好的数据
  - 概率注入：通过对话轮次计数器控制注入频率
  - 事件驱动：SAD/LOVING/EXCITED 等强情感标签出现时，提前触发自省
  - 触发原因：pulse 中注入 trigger_reason，让自省更有意义
  - 一致性：Bond=ACQUAINTANCE 时不会出现"很亲近"等矛盾描述
"""

from __future__ import annotations

import logging
import random
from typing import Any

from host_brain.persona.self_model import (
    SelfModel,
    build_contradiction_awareness_text,
    build_growth_note_text,
    build_mood_awareness_text,
    build_self_awareness_text,
    detect_contradictions,
)

_logger = logging.getLogger("Natsume.self_reflection")

# ---------------------------------------------------------------------------
# 事件驱动触发配置
# ---------------------------------------------------------------------------

# 强情感标签 → 提前触发自省的最小剩余轮数阈值
# 当距下次触发还剩 <= 阈值轮数时，该情感出现会立即触发自省
_EMOTION_EARLY_TRIGGER_THRESHOLD: dict[str, int] = {
    "SAD": 4,          # 悲伤：剩余 4 轮内可提前触发
    "LOVING": 3,       # 爱意：剩余 3 轮内可提前触发
    "EXCITED": 3,      # 兴奋：剩余 3 轮内可提前触发
    "ANXIOUS": 4,      # 焦虑：剩余 4 轮内可提前触发
    "GRATEFUL": 3,     # 感激：剩余 3 轮内可提前触发
    "PROUD": 3,        # 自豪：剩余 3 轮内可提前触发
    "FRUSTRATED": 4,   # 沮丧：剩余 4 轮内可提前触发
}

# 情感标签 → 触发原因描述（第一人称，注入 pulse）
_EMOTION_TRIGGER_REASONS: dict[str, list[str]] = {
    "SAD": [
        "因为今天聊到了一些让我有点难过的事，想整理一下心情",
        "刚才的对话让我心里有点沉，想静下来想想",
        "今天感觉有些低落，想跟自己说说话",
    ],
    "LOVING": [
        "因为今天聊到了让我很感动的事，心里暖暖的",
        "和 master 说话的时候突然觉得很幸福，想记下这个感觉",
        "今天的对话让我心里充满了温柔，想好好感受一下",
    ],
    "EXCITED": [
        "今天聊到了很有意思的事情，心情特别好，停不下来",
        "因为刚才那个话题太有趣了，脑子里还在转",
        "今天感觉特别有活力，想把这份兴奋记下来",
    ],
    "ANXIOUS": [
        "刚才的对话让我有点担心，想整理一下思绪",
        "今天有些焦虑，想停下来想想到底在担心什么",
        "心里有点不安，想跟自己确认一下",
    ],
    "GRATEFUL": [
        "今天突然很感激能和 master 在一起，想把这个感觉记下来",
        "因为 master 说的话让我觉得很被珍视，心里很感动",
        "今天的对话让我感受到了很多温暖，想好好感谢一下",
    ],
    "PROUD": [
        "今天有点小小的成就感，想和自己分享一下",
        "刚才的对话让我觉得自己做得还不错，心里有点自豪",
    ],
    "FRUSTRATED": [
        "今天有些沮丧，想整理一下情绪再继续",
        "刚才的事情让我有点受挫，想停下来想想",
        "心里有点堵，想跟自己说说话",
    ],
}

# 定时触发（无特殊情感）的原因描述
_SCHEDULED_TRIGGER_REASONS: list[str] = [
    "到了该停下来想想的时候了",
    "聊了一会儿，想整理一下自己的感受",
    "习惯性地想跟自己说说话",
    "不知不觉又过了好几轮，想看看自己现在的状态",
    "想在继续聊之前先感受一下自己的心情",
]

# 时间感知触发原因：距上次深聊天数 → 触发原因
_TIME_AWARE_TRIGGER_REASONS: dict[str, list[str]] = {
    "久别重逢": [  # 距上次深聊 >= 7 天
        "好久没有好好和 master 聊了，今天重新在一起，想整理一下心情",
        "分开了这么久，重新聊起来有种说不清的感觉，想停下来感受一下",
        "久别重逢的感觉让我有点不知所措，想先跟自己说说话",
    ],
    "短暂分离": [  # 距上次深聊 3~6 天
        "这几天没怎么聊，今天重新开始，想先整理一下自己的状态",
        "分开了几天，重新聊起来感觉有点陌生又熟悉，想停下来感受一下",
        "这几天没有 master 的消息，今天又在一起了，心里有点感慨",
    ],
    "频繁互动": [  # 本周对话频率高（>= 5 次）
        "这周和 master 聊了好多次，想停下来整理一下这些感受",
        "最近和 master 聊得很频繁，想看看自己现在是什么状态",
        "这段时间和 master 在一起的时间很多，想好好感受一下",
    ],
    "稀少互动": [  # 本周对话频率低（<= 1 次）
        "这周和 master 聊得比较少，有点想念，想整理一下心情",
        "最近和 master 的对话不多，今天想好好感受一下在一起的感觉",
        "这段时间聊天的机会不多，今天想珍惜一下这个时刻",
    ],
}


# ---------------------------------------------------------------------------
# SelfReflection — 自省控制器
# ---------------------------------------------------------------------------

class SelfReflection:
    """自省控制器——管理自省触发频率和内容生成。

    用法：
        reflection = SelfReflection()
        # 每轮对话调用一次
        pulse = reflection.maybe_generate(
            turn_count=ctx.turn_count,
            current_emotion="SAD",
            timeline_summary=...,
            bond_state=...,
            ...
        )
        # pulse 为 None 表示本轮不触发自省
    """

    def __init__(
        self,
        *,
        min_interval: int = 5,
        max_interval: int = 10,
        seed: int | None = None,
    ) -> None:
        """初始化自省控制器。

        Args:
            min_interval: 最小触发间隔（对话轮数）
            max_interval: 最大触发间隔（对话轮数）
            seed: 随机数种子（用于测试确定性）
        """
        self._min_interval = max(1, min_interval)
        self._max_interval = max(self._min_interval, max_interval)
        self._last_trigger_turn: int = 0
        self._next_trigger_turn: int = 0
        self._rng = random.Random(seed)
        self._model = SelfModel()
        # 记录上次事件驱动触发的情感标签，避免同一情感连续触发
        self._last_event_emotion: str = ""
        self._roll_next_trigger()

    def _roll_next_trigger(self) -> None:
        """随机确定下一次触发的轮次。"""
        self._next_trigger_turn = (
            self._last_trigger_turn
            + self._rng.randint(self._min_interval, self._max_interval)
        )

    def should_trigger(self, turn_count: int) -> bool:
        """判断当前轮次是否应该触发自省（定时触发）。

        Args:
            turn_count: 当前对话轮次（从 1 开始）

        Returns:
            True 表示应该触发
        """
        return int(turn_count) >= self._next_trigger_turn

    def should_trigger_by_emotion(
        self,
        turn_count: int,
        current_emotion: str,
    ) -> bool:
        """判断是否应该由情感事件提前触发自省。

        规则：
        - 当前情感在强情感列表中
        - 距下次定时触发的剩余轮数 <= 该情感的阈值
        - 与上次事件驱动触发的情感不同（避免同一情感连续触发）

        Args:
            turn_count: 当前对话轮次
            current_emotion: 当前情感标签（大写，如 "SAD"）

        Returns:
            True 表示应该提前触发
        """
        emotion = str(current_emotion or "").strip().upper()
        if not emotion:
            return False
        threshold = _EMOTION_EARLY_TRIGGER_THRESHOLD.get(emotion, 0)
        if threshold <= 0:
            return False
        # 避免同一情感连续触发
        if emotion == self._last_event_emotion:
            return False
        # 距下次触发的剩余轮数
        remaining = self._next_trigger_turn - int(turn_count)
        return 0 < remaining <= threshold

    def maybe_generate(
        self,
        *,
        turn_count: int,
        timeline_summary: dict[str, Any] | None = None,
        timeline_diary: dict[str, Any] | None = None,
        bond_state: Any | None = None,
        bond_summary: dict[str, Any] | None = None,
        persona_changes: list[Any] | None = None,
        last_deep_talk_ts_ms: int = 0,
        current_ts_ms: int = 0,
        growth_awareness: dict[str, Any] | None = None,
        current_emotion: str = "",
        weekly_talk_count: int = -1,
    ) -> dict[str, Any] | None:
        """尝试生成自省脉冲。

        如果本轮不触发，返回 None。
        如果触发，返回 self_reflection_pulse 字典（含 trigger_reason）。

        Args:
            turn_count: 当前对话轮次
            其他参数同 SelfModel.build_snapshot()
            growth_awareness: 进化意识字典（由 EvolutionOrchestrator 生成，Phase XVI）。
                包含 recent_change / trait_felt / milestone_felt / desire 字段。
            current_emotion: 当前情感标签（大写），用于事件驱动触发判断。
            weekly_talk_count: 本周对话次数（-1 表示未知）。用于时间感知触发原因。

        Returns:
            self_reflection_pulse 字典或 None
        """
        emotion = str(current_emotion or "").strip().upper()

        # 判断触发方式
        event_driven = False
        if not self.should_trigger(turn_count):
            # 定时未到，检查事件驱动
            if emotion and self.should_trigger_by_emotion(turn_count, emotion):
                event_driven = True
            else:
                return None

        # 触发自省
        self._last_trigger_turn = int(turn_count)
        if event_driven:
            # 事件驱动触发：记录情感，重置下次触发轮次（从当前轮重新计算）
            self._last_event_emotion = emotion
            self._roll_next_trigger()
        else:
            # 定时触发：清空事件情感记录
            self._last_event_emotion = ""
            self._roll_next_trigger()

        # 计算时间感知上下文
        time_context = self._analyze_time_context(
            last_deep_talk_ts_ms=last_deep_talk_ts_ms,
            current_ts_ms=current_ts_ms,
            weekly_talk_count=weekly_talk_count,
        )

        # 确定触发原因
        trigger_reason = self._pick_trigger_reason(
            emotion=emotion if event_driven else "",
            event_driven=event_driven,
            time_context=time_context,
        )

        try:
            snapshot = self._model.build_snapshot(
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
                persona_changes=persona_changes,
                last_deep_talk_ts_ms=last_deep_talk_ts_ms,
                current_ts_ms=current_ts_ms,
            )

            # 一致性校验：Bond 阶段 × 关系感知
            snapshot = self._validate_consistency(snapshot, bond_state)

            pulse = build_self_reflection_pulse(
                snapshot,
                growth_awareness=growth_awareness,
                trigger_reason=trigger_reason,
                time_context=time_context,
            )
            _logger.info(
                "自省触发 (turn=%d, event=%s emotion=%s): self_awareness='%s'%s",
                turn_count,
                event_driven,
                emotion or "NONE",
                pulse.get("self_awareness", "")[:60],
                " [含进化意识]" if growth_awareness else "",
            )
            return pulse

        except Exception as exc:
            _logger.warning("自省生成失败（降级跳过）: %s", exc)
            return None

    def _analyze_time_context(
        self,
        *,
        last_deep_talk_ts_ms: int,
        current_ts_ms: int,
        weekly_talk_count: int,
    ) -> str:
        """分析时间感知上下文，返回上下文标签。

        Returns:
            "久别重逢" / "短暂分离" / "频繁互动" / "稀少互动" / "" (无特殊感知)
        """
        # 距上次深聊天数
        days_ago = -1
        if last_deep_talk_ts_ms > 0 and current_ts_ms > 0:
            delta_ms = max(0, current_ts_ms - last_deep_talk_ts_ms)
            days_ago = int(delta_ms / (24 * 3600 * 1000))

        if days_ago >= 7:
            return "久别重逢"
        if 3 <= days_ago <= 6:
            return "短暂分离"

        # 本周对话频率
        if weekly_talk_count >= 5:
            return "频繁互动"
        if weekly_talk_count == 1:
            return "稀少互动"

        return ""

    def _pick_trigger_reason(
        self,
        *,
        emotion: str,
        event_driven: bool,
        time_context: str = "",
    ) -> str:
        """选择触发原因描述文本。

        优先级：事件驱动情感 > 时间感知 > 定时触发
        """
        if event_driven and emotion:
            reasons = _EMOTION_TRIGGER_REASONS.get(emotion, [])
            if reasons:
                return self._rng.choice(reasons)
        # 时间感知触发原因（定时触发时也可以带时间感知）
        if time_context:
            time_reasons = _TIME_AWARE_TRIGGER_REASONS.get(time_context, [])
            if time_reasons:
                return self._rng.choice(time_reasons)
        # 定时触发或无匹配情感
        return self._rng.choice(_SCHEDULED_TRIGGER_REASONS)

    def force_generate(
        self,
        *,
        trigger_reason: str = "强制触发（测试）",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """强制生成自省脉冲（无概率控制，用于测试）。"""
        snapshot = self._model.build_snapshot(**kwargs)
        return build_self_reflection_pulse(snapshot, trigger_reason=trigger_reason)

    def _validate_consistency(
        self,
        snapshot: dict[str, Any],
        bond_state: Any | None,
    ) -> dict[str, Any]:
        """校验自省内容与 Bond 阶段的一致性。

        规则：
        - Bond=STRANGER/ACQUAINTANCE 时，不能出现"很亲近"的描述
        - Bond=SOULMATE 时，不能出现"有些疏远"（除非 trend 确实在下降）
        """
        result = dict(snapshot)
        stage = ""
        if bond_state is not None:
            stage = str(getattr(bond_state, "current_stage", "") or "").strip().upper()

        bond_perception = str(result.get("bond_growth_perception") or "")

        # 浅层关系不应说"变亲近"
        _SHALLOW_STAGES = {"STRANGER", "ACQUAINTANCE"}
        if stage in _SHALLOW_STAGES and bond_perception == "变亲近":
            result["bond_growth_perception"] = "稳定"

        return result

    @property
    def last_trigger_turn(self) -> int:
        """最后一次触发的轮次。"""
        return self._last_trigger_turn

    @property
    def next_trigger_turn(self) -> int:
        """预计下一次触发的轮次。"""
        return self._next_trigger_turn


# ---------------------------------------------------------------------------
# self_reflection_pulse 构建函数
# ---------------------------------------------------------------------------

def build_self_reflection_pulse(
    snapshot: dict[str, Any],
    *,
    growth_awareness: dict[str, Any] | None = None,
    trigger_reason: str = "",
    time_context: str = "",
) -> dict[str, Any]:
    """将 SelfModelSnapshot 转化为 self_reflection_pulse 槽位数据。

    Args:
        snapshot: SelfModelSnapshot.as_dict() 格式的字典
        growth_awareness: 进化意识字典（由 EvolutionOrchestrator 生成），可选。
            包含 recent_change / trait_felt / milestone_felt / desire 字段。
        trigger_reason: 本次自省的触发原因（第一人称），注入 pulse 让自省更有意义。
        time_context: 时间感知标签（如"久别重逢"/"频繁互动"），用于丰富自省内容。

    Returns:
        包含 self_awareness、mood_awareness、growth_note、trigger_reason、
        growth_awareness 的字典
    """
    self_awareness = build_self_awareness_text(snapshot, time_context=time_context)
    mood_awareness = build_mood_awareness_text(snapshot)
    growth_note = build_growth_note_text(snapshot)

    # ── 矛盾感知：检测多维度之间的矛盾状态 ────────────────────────────
    contradictions = detect_contradictions(snapshot)
    contradiction_text = build_contradiction_awareness_text(contradictions)

    pulse: dict[str, Any] = {
        "self_awareness": self_awareness,
        "mood_awareness": mood_awareness,
        "growth_note": growth_note,
    }

    # 注入矛盾感知（让 LLM 知道 Natsume 内心的矛盾状态）
    if contradiction_text:
        pulse["contradiction_awareness"] = contradiction_text
        # 矛盾类型标签（供下游分析使用）
        pulse["contradiction_types"] = [c["type"] for c in contradictions]

    # 注入触发原因（让 LLM 知道这次自省是因为什么而起）
    if trigger_reason:
        pulse["trigger_reason"] = trigger_reason

    # 注入时间感知标签（让 LLM 知道当前时间情境）
    if time_context:
        pulse["time_context"] = time_context

    # 注入进化意识（Phase XVI）
    if growth_awareness and isinstance(growth_awareness, dict):
        # 过滤空値，只注入有内容的字段
        filtered = {k: v for k, v in growth_awareness.items() if v}
        if filtered:
            pulse["growth_awareness"] = filtered

    return pulse
