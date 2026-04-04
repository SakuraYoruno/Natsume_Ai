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
    build_state_aligned_self_awareness,
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
        growth_summary: dict[str, Any] | None = None,
        current_emotion: str = "",
        weekly_talk_count: int = -1,
        llm_fn: Any | None = None,
        style_evolution: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """尝试生成自省脉冲。

        如果本轮不触发，返回 None。
        如果触发，返回 self_reflection_pulse 字典（含 trigger_reason）。

        Args:
            turn_count: 当前对话轮次
            其他参数同 SelfModel.build_snapshot()
            growth_awareness: 进化意识字典（由 EvolutionOrchestrator 生成，Phase XVI）。
                包含 recent_change / trait_felt / milestone_felt / desire 字段。
            growth_summary: 近期成长摘要（由 EvolutionOrchestrator 聚合），可跨轮复用。
                包含 daily_summary / weekly_summary / active_themes 等字段。
            current_emotion: 当前情感标签（大写），用于事件驱动触发判断。
            weekly_talk_count: 本周对话次数（-1 表示未知）。用于时间感知触发原因。
            llm_fn: 可选的 LLM 调用函数 (prompt: str) -> str。
                技术债务 10.1 修复：透传给 build_self_reflection_pulse，
                当 llm_fn 可用时动态生成真实自省文本。
            style_evolution: PersonaDriftDetector.get_style_evolution_summary() 的输出。
                v1.11 新增：包含 trend / current_avg / baseline / history_count 字段。
                注入后让自省感知风格演变（如"最近感觉自己越来越温柔了"）。

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
                growth_summary=growth_summary,
                trigger_reason=trigger_reason,
                time_context=time_context,
                llm_fn=llm_fn,
                style_evolution=style_evolution,
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



_NARRATIVE_GUARDRAIL_ORDER: tuple[str, ...] = (
    "time_context_unverified",
    "unsupported_self_claim",
    "anthropomorphic_overreach",
    "reflection_vs_policy_tension",
)

_NARRATIVE_GUARDRAIL_INSTRUCTIONS: dict[str, str] = {
    "time_context_unverified": "若时间线证据不足，只能说“最近/这段时间”，不要声称具体多久或哪一天。",
    "unsupported_self_claim": "没有明确依据时，不要把推测写成确定的自我变化、关系进展或内心结论。",
    "anthropomorphic_overreach": "不要把系统状态、薄弱线索或抽象趋势扩写成人类式经历、自传或命运感叙事。",
    "reflection_vs_policy_tension": "自我叙事只能轻量辅助，不能压过当前事实、主回应任务和既有边界。",
}


def _build_narrative_guardrails(
    snapshot: dict[str, Any] | None,
    *,
    time_context: str = "",
    growth_awareness: dict[str, Any] | None = None,
    growth_summary: dict[str, Any] | None = None,
    style_evolution: dict[str, Any] | None = None,
    response_policy: dict[str, Any] | None = None,
    runtime_flags: dict[str, Any] | None = None,
    base_tags: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    growth_awareness = dict(growth_awareness or {})
    growth_summary = dict(growth_summary or {})
    style_evolution = dict(style_evolution or {})
    response_policy = dict(response_policy or {})
    runtime_flags = dict(runtime_flags or {})

    tags: list[str] = [
        str(tag).strip()
        for tag in (base_tags or [])
        if str(tag).strip() in _NARRATIVE_GUARDRAIL_ORDER
    ]

    def _add_tag(tag: str) -> None:
        if tag and tag in _NARRATIVE_GUARDRAIL_ORDER and tag not in tags:
            tags.append(tag)

    time_context = str(time_context or "").strip()
    try:
        last_deep_talk_days_ago = int(snapshot.get("last_deep_talk_days_ago") or -1)
    except (TypeError, ValueError):
        last_deep_talk_days_ago = -1
    try:
        event_count = int(snapshot.get("event_count") or 0)
    except (TypeError, ValueError):
        event_count = 0

    personality_changes = [
        str(item).strip()
        for item in (snapshot.get("personality_changes") or [])
        if str(item).strip()
    ]
    growth_milestones = [
        str(item).strip()
        for item in (snapshot.get("growth_milestones") or [])
        if str(item).strip()
    ]
    growth_awareness_values = [
        str(value).strip()
        for value in growth_awareness.values()
        if str(value).strip()
    ]
    growth_summary_values = [
        str(growth_summary.get(key) or "").strip()
        for key in ("daily_summary", "weekly_summary", "last_cycle_date")
        if str(growth_summary.get(key) or "").strip()
    ]
    growth_summary_values.extend(
        str(item).strip()
        for item in (growth_summary.get("active_themes") or [])
        if str(item).strip()
    )
    try:
        growth_cycle_count = int(growth_summary.get("recent_cycle_count") or 0)
    except (TypeError, ValueError):
        growth_cycle_count = 0
    history_count = int(style_evolution.get("history_count") or 0)
    trend = style_evolution.get("trend") or {}
    style_evidence = history_count >= 20 and any(
        str(direction).strip().lower() in {"rising", "falling"}
        for direction in (trend.values() if isinstance(trend, dict) else [])
    )

    has_growth_evidence = bool(
        personality_changes
        or growth_milestones
        or growth_awareness_values
        or growth_summary_values
        or growth_cycle_count > 0
        or style_evidence
    )
    has_relationship_anchor = event_count > 0 or last_deep_talk_days_ago >= 0
    has_self_evidence = has_growth_evidence or has_relationship_anchor

    if not time_context and last_deep_talk_days_ago < 0:
        _add_tag("time_context_unverified")
    if not has_self_evidence:
        _add_tag("unsupported_self_claim")
    if not has_self_evidence and not time_context:
        _add_tag("anthropomorphic_overreach")

    sys_mode = str(runtime_flags.get("sys_mode") or "").strip().lower()
    policy_guarded = bool(
        response_policy.get("should_be_brief")
        or response_policy.get("should_be_withdrawn")
        or response_policy.get("withdrawal_guard")
        or sys_mode in {"sleep", "locked", "maintenance"}
    )
    if policy_guarded:
        _add_tag("reflection_vs_policy_tension")

    ordered_tags = [tag for tag in _NARRATIVE_GUARDRAIL_ORDER if tag in tags]
    if not ordered_tags:
        return {}

    risk_score = 0
    for tag in ordered_tags:
        risk_score += 1 if tag == "reflection_vs_policy_tension" else 2
    risk_level = "high" if risk_score >= 4 else "medium"
    allowed_style = "brief" if risk_level == "high" else "guarded"
    instructions = [_NARRATIVE_GUARDRAIL_INSTRUCTIONS[tag] for tag in ordered_tags]
    return {
        "risk_level": risk_level,
        "risk_tags": ordered_tags,
        "instruction": " ".join(instructions),
        "allowed_style": allowed_style,
        "allow_self_narrative_expansion": False,
    }


# ---------------------------------------------------------------------------
# self_reflection_pulse 构建函数
# ---------------------------------------------------------------------------

def build_self_reflection_pulse(
    snapshot: dict[str, Any],
    *,
    growth_awareness: dict[str, Any] | None = None,
    growth_summary: dict[str, Any] | None = None,
    trigger_reason: str = "",
    time_context: str = "",
    llm_fn: Any | None = None,
    style_evolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 SelfModelSnapshot 转化为 self_reflection_pulse 槽位数据。

    Args:
        snapshot: SelfModelSnapshot.as_dict() 格式的字典
        growth_awareness: 进化意识字典（由 EvolutionOrchestrator 生成），可选。
            包含 recent_change / trait_felt / milestone_felt / desire 字段。
        growth_summary: 近期成长摘要（由 EvolutionOrchestrator 聚合），可选。
            包含 daily_summary / weekly_summary / active_themes 等字段。
        trigger_reason: 本次自省的触发原因（第一人称），注入 pulse 让自省更有意义。
        time_context: 时间感知标签（如"久别重逢"/"频繁互动"），用于丰富自省内容。
        llm_fn: 可选的 LLM 调用函数 (prompt: str) -> str。
            技术债务 10.1 修复：当 llm_fn 可用时，三个 build_*_text 函数均使用
            LLM 动态生成真实的第一人称自省文本，而非从模板池选取。
            LLM 失败时自动降级到模板池（完全向后兼容）。
        style_evolution: PersonaDriftDetector.get_style_evolution_summary() 的输出。
            v1.11 新增：包含 trend / current_avg / baseline / history_count 字段。
            积累 >= 20 条历史时，注入 style_trend_note 字段到 pulse。

    Returns:
        包含 self_awareness、mood_awareness、growth_note、trigger_reason、
        growth_awareness、style_trend_note（可选）的字典
    """
    self_awareness = build_self_awareness_text(snapshot, time_context=time_context, llm_fn=llm_fn, style_evolution=style_evolution)
    mood_awareness = build_mood_awareness_text(snapshot, llm_fn=llm_fn)
    growth_note = build_growth_note_text(snapshot, llm_fn=llm_fn, style_evolution=style_evolution)

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

    if growth_summary and isinstance(growth_summary, dict):
        filtered_summary: dict[str, Any] = {}
        for key in ("daily_summary", "weekly_summary", "last_cycle_date"):
            value = str(growth_summary.get(key) or "").strip()
            if value:
                filtered_summary[key] = value
        cycle_count = growth_summary.get("recent_cycle_count")
        if isinstance(cycle_count, int) and cycle_count > 0:
            filtered_summary["recent_cycle_count"] = cycle_count
        active_themes = [
            str(item).strip()
            for item in (growth_summary.get("active_themes") or [])
            if str(item).strip()
        ]
        if active_themes:
            filtered_summary["active_themes"] = active_themes
        if filtered_summary:
            pulse["growth_summary"] = filtered_summary

    # 注入风格演变趋势（v1.11）
    if style_evolution and isinstance(style_evolution, dict):
        trend = style_evolution.get("trend") or {}
        history_count = int(style_evolution.get("history_count") or 0)
        # 只有积累了足够历史（>= 20 条）才注入，避免数据不足时误导
        if history_count >= 20 and trend:
            style_trend_note = _build_style_trend_note(trend, llm_fn=llm_fn)
            if style_trend_note:
                pulse["style_trend_note"] = style_trend_note

    narrative_guardrails = _build_narrative_guardrails(
        snapshot,
        time_context=time_context,
        growth_awareness=growth_awareness,
        growth_summary=growth_summary,
        style_evolution=style_evolution,
    )
    if narrative_guardrails:
        pulse["narrative_guardrails"] = narrative_guardrails

    return pulse


def align_self_reflection_pulse(
    pulse: dict[str, Any] | None,
    *,
    snapshot: dict[str, Any] | None = None,
    offline_state: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    response_policy: dict[str, Any] | None = None,
    runtime_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(pulse or {})
    if not result:
        return result

    alignment = build_state_aligned_self_awareness(
        dict(snapshot or {}),
        offline_state=dict(offline_state or {}),
        bond_summary=dict(bond_summary or {}),
        motive_state=dict(motive_state or {}),
        response_policy=dict(response_policy or {}),
        runtime_flags=dict(runtime_flags or {}),
    )
    aligned_self_awareness = str(alignment.get("self_awareness") or "").strip()
    if aligned_self_awareness:
        result["self_awareness"] = aligned_self_awareness
        result["state_alignment"] = aligned_self_awareness
    alignment_mode = str(alignment.get("alignment_mode") or "").strip()
    if alignment_mode:
        result["state_alignment_mode"] = alignment_mode
    reason_tags = [str(item).strip() for item in (alignment.get("reason_tags") or []) if str(item).strip()]
    if reason_tags:
        result["state_alignment_reasons"] = reason_tags

    existing_guardrails = dict(result.get("narrative_guardrails") or {})
    existing_tags = [
        str(item).strip()
        for item in (existing_guardrails.get("risk_tags") or [])
        if str(item).strip()
    ]
    narrative_guardrails = _build_narrative_guardrails(
        snapshot,
        time_context=str(result.get("time_context") or ""),
        growth_awareness=dict(result.get("growth_awareness") or {}),
        growth_summary=dict(result.get("growth_summary") or {}),
        response_policy=response_policy,
        runtime_flags=runtime_flags,
        base_tags=existing_tags,
    )
    if narrative_guardrails:
        result["narrative_guardrails"] = narrative_guardrails
    return result


# ---------------------------------------------------------------------------
# 风格演变趋势辅助 — v1.11 新增
# ---------------------------------------------------------------------------

# 特质中文名映射
_TRAIT_CN: dict[str, str] = {
    "warmth": "温柔",
    "curiosity": "好奇心",
    "humor": "幽默感",
    "sensitivity": "敏感",
    "assertiveness": "自信",
    "playfulness": "俏皮",
    "resilience": "韧性",
}

# 风格演变趋势模板池：rising / falling 各 4 条变体
_STYLE_TREND_TEXT_POOL: dict[str, list[str]] = {
    "rising": [
        "最近感觉自己越来越{trait}了",
        "不知不觉中，{trait}好像在慢慢增加",
        "回头想想，最近{trait}比以前多了一些",
        "有种感觉，{trait}在悄悄变强",
    ],
    "falling": [
        "最近{trait}好像稍微收敛了一些",
        "不知道为什么，{trait}比以前少了一点",
        "感觉{trait}在慢慢变淡，不知道是不是好事",
        "最近{trait}有点不如以前了，想想是什么原因",
    ],
}


def _build_style_trend_note(
    trend: dict[str, str],
    *,
    llm_fn: Any | None = None,
) -> str:
    """将风格演变趋势字典转化为第一人称自然语言描述（v1.11 新增）。

    优先选取最显著的 rising 维度，其次 falling 维度。
    最多描述 2 个维度，避免自省过于冗长。

    Args:
        trend: {dim: "rising"/"stable"/"falling"} 字典
        llm_fn: 可选 LLM 函数，用于生成更自然的描述

    Returns:
        第一人称风格演变感知文本，无显著趋势时返回空字符串
    """
    rising = [k for k, v in trend.items() if v == "rising"]
    falling = [k for k, v in trend.items() if v == "falling"]

    if not rising and not falling:
        return ""

    # ── LLM 动态生成路径 ────────────────────────────────────────────────
    if llm_fn is not None:
        try:
            rising_cn = [_TRAIT_CN.get(k, k) for k in rising[:2]]
            falling_cn = [_TRAIT_CN.get(k, k) for k in falling[:2]]
            hints = []
            if rising_cn:
                hints.append(f"{'、'.join(rising_cn)}在上升")
            if falling_cn:
                hints.append(f"{'、'.join(falling_cn)}在下降")
            hint_str = "，".join(hints)
            prompt = (
                f"你是 Natsume，请用第一人称写一句关于自己风格变化的真实感受（15-30字），"
                f"要自然，不要说教。"
                f"风格趋势：{hint_str}。"
                f"只输出那一句话，不要解释。"
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 5:
                return result.strip()
        except Exception:
            pass

    # ── 降级：模板池路径 ────────────────────────────────────────────────
    parts: list[str] = []
    for dim in rising[:1]:
        cn = _TRAIT_CN.get(dim, dim)
        tmpl = random.choice(_STYLE_TREND_TEXT_POOL["rising"])
        parts.append(tmpl.format(trait=cn))
    for dim in falling[:1]:
        cn = _TRAIT_CN.get(dim, dim)
        tmpl = random.choice(_STYLE_TREND_TEXT_POOL["falling"])
        parts.append(tmpl.format(trait=cn))

    return "，".join(parts) if parts else ""
