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
  - 一致性：自省内容不会和当前 Bond 阶段矛盾
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

from host_brain.persona.runtime_labels import (
    BOND_GROWTH_CLOSER,
    BOND_GROWTH_STABLE,
    TIME_CONTEXT_FREQUENT_INTERACTION,
    TIME_CONTEXT_REUNION,
    TIME_CONTEXT_SHORT_ABSENCE,
    TIME_CONTEXT_SPARSE_INTERACTION,
)
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


class _SelfReflectionModelPort:
    """Typed text port backed only by the canonical model-attempt service."""

    canonical_model_attempt_port = True

    def __init__(self, *, request_id: str, deadline_ms: int = 4_000) -> None:
        self._request_id = str(request_id or "self-reflection")
        self._deadline_ms = max(800, int(deadline_ms))
        self._traces: list[dict[str, Any]] = []

    @property
    def traces(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._traces]

    def bind(self, prompt_id: str) -> Any:
        def _complete(prompt: str) -> str:
            from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
            from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

            result = complete_compiled_prompt_sync(
                prompt_id=prompt_id,
                request_id=f"{self._request_id}:{prompt_id}",
                scene_type="SELF_REFLECTION",
                cognitive_tier="B",
                messages=({"role": "user", "content": str(prompt or "")},),
                deadline_ms=self._deadline_ms,
                foreground=False,
                metadata={"chain": prompt_id},
            )
            self._traces.append(model_attempt_result_trace(result))
            return str(result.content or "").strip() if result.success else ""

        setattr(_complete, "canonical_model_attempt_port", True)
        return _complete


def _canonical_self_reflection_port(
    candidate: Any,
    *,
    request_id: str,
    enabled: bool,
) -> _SelfReflectionModelPort | None:
    if isinstance(candidate, _SelfReflectionModelPort):
        return candidate
    if not enabled:
        return None
    return _SelfReflectionModelPort(request_id=request_id)


def _normalize_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _resolve_prompt_template_json_localized(
    template_id: str,
    *,
    render_target_lang: str = "zh",
) -> Any:
    lang_key = _normalize_template_lang(render_target_lang)
    for candidate_id in (f"{template_id}.{lang_key}", template_id):
        try:
            raw = resolve_prompt_template_json_merged(candidate_id)
        except Exception:
            continue
        if isinstance(raw, Mapping):
            localized = raw.get(lang_key)
            if localized is not None:
                return localized
        if raw is not None:
            return raw
    return {}


def _load_prompt_templates(*, render_target_lang: str = "zh") -> dict[str, Any]:
    raw = _resolve_prompt_template_json_localized(
        "persona.self_narrative_prompts",
        render_target_lang=render_target_lang,
    )
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _call_with_optional_render_target_lang(func: Any, *args: Any, render_target_lang: str, **kwargs: Any) -> Any:
    try:
        return func(*args, render_target_lang=render_target_lang, **kwargs)
    except TypeError as exc:
        if "render_target_lang" not in str(exc):
            raise
        return func(*args, **kwargs)

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
_EMOTION_TRIGGER_REASONS: dict[str, list[str]] = {}

# 定时触发（无特殊情感）的原因描述
_SCHEDULED_TRIGGER_REASONS: list[str] = []

# 时间感知触发原因：距上次深聊天数 → 触发原因
_TIME_AWARE_TRIGGER_REASONS: dict[str, list[str]] = {}


_DEFAULT_TRIGGER_REASON_TEMPLATES: dict[str, Any] = {
    "emotion": _EMOTION_TRIGGER_REASONS,
    "scheduled": _SCHEDULED_TRIGGER_REASONS,
    "time_aware": _TIME_AWARE_TRIGGER_REASONS,
    "force_generate_reason": "",
}


def _load_trigger_reason_templates(*, render_target_lang: str = "zh") -> dict[str, Any]:
    try:
        raw = _resolve_prompt_template_json_localized(
            "self_reflection.trigger_reason_templates",
            render_target_lang=render_target_lang,
        )
    except Exception:
        return dict(_DEFAULT_TRIGGER_REASON_TEMPLATES)
    return dict(raw) if isinstance(raw, Mapping) else dict(_DEFAULT_TRIGGER_REASON_TEMPLATES)


def _resolve_trigger_reason_sources(
    *,
    render_target_lang: str = "zh",
) -> tuple[Mapping[str, Any], list[str], Mapping[str, Any], str]:
    templates = _load_trigger_reason_templates(render_target_lang=render_target_lang)
    emotion_mapping = templates.get("emotion")
    scheduled_reasons = templates.get("scheduled")
    time_aware_mapping = templates.get("time_aware")
    force_generate_reason = _mapping_text(
        templates,
        "force_generate_reason",
    )
    return (
        emotion_mapping if isinstance(emotion_mapping, Mapping) else _EMOTION_TRIGGER_REASONS,
        scheduled_reasons if isinstance(scheduled_reasons, list) else _SCHEDULED_TRIGGER_REASONS,
        time_aware_mapping if isinstance(time_aware_mapping, Mapping) else _TIME_AWARE_TRIGGER_REASONS,
        force_generate_reason,
    )


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
        render_target_lang: str = "zh",
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
            render_target_lang=render_target_lang,
        )
        resolved_llm_fn = _canonical_self_reflection_port(
            llm_fn,
            request_id=f"self_reflection::{int(current_ts_ms or turn_count)}",
            enabled=True,
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
                llm_fn=resolved_llm_fn,
                strict_llm=True,
                style_evolution=style_evolution,
                render_target_lang=render_target_lang,
            )
            if not any(
                str(pulse.get(key) or "").strip()
                for key in ("self_awareness", "mood_awareness", "growth_note", "style_trend_note")
            ):
                _logger.info("自省触发已跳过：共享池不可用或未返回有效文本")
                return None
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
            return TIME_CONTEXT_REUNION
        if 3 <= days_ago <= 6:
            return TIME_CONTEXT_SHORT_ABSENCE

        # 本周对话频率
        if weekly_talk_count >= 5:
            return TIME_CONTEXT_FREQUENT_INTERACTION
        if weekly_talk_count == 1:
            return TIME_CONTEXT_SPARSE_INTERACTION

        return ""

    def _pick_trigger_reason(
        self,
        *,
        emotion: str,
        event_driven: bool,
        time_context: str = "",
        render_target_lang: str = "zh",
    ) -> str:
        """选择触发原因描述文本。

        优先级：事件驱动情感 > 时间感知 > 定时触发
        """
        emotion_reasons, scheduled_reasons, time_aware_reasons, _ = _resolve_trigger_reason_sources(
            render_target_lang=render_target_lang,
        )
        if event_driven and emotion:
            reasons = emotion_reasons.get(emotion, [])
            if isinstance(reasons, list) and reasons:
                return self._rng.choice(reasons)
        # 时间感知触发原因（定时触发时也可以带时间感知）
        if time_context:
            time_reasons = time_aware_reasons.get(time_context, [])
            if isinstance(time_reasons, list) and time_reasons:
                return self._rng.choice(time_reasons)
        # 定时触发或无匹配情感
        return self._rng.choice(scheduled_reasons) if scheduled_reasons else ""

    def force_generate(
        self,
        *,
        trigger_reason: str = "",
        render_target_lang: str = "zh",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """强制生成自省脉冲（无概率控制，用于测试）。"""
        _, _, _, default_trigger_reason = _resolve_trigger_reason_sources(
            render_target_lang=render_target_lang,
        )
        trigger_reason_text = str(trigger_reason or "").strip() or default_trigger_reason
        snapshot = self._model.build_snapshot(**kwargs)
        return build_self_reflection_pulse(
            snapshot,
            trigger_reason=trigger_reason_text,
            render_target_lang=render_target_lang,
        )

    def _validate_consistency(
        self,
        snapshot: dict[str, Any],
        bond_state: Any | None,
    ) -> dict[str, Any]:
        """校验自省内容与 Bond 阶段的一致性。

        规则：
        - Bond=SOULMATE 时，不能出现"有些疏远"（除非 trend 确实在下降）
        """
        result = dict(snapshot)
        stage = ""
        if bond_state is not None:
            stage = str(getattr(bond_state, "current_stage", "") or "").strip().upper()

        bond_perception = str(result.get("bond_growth_perception") or "")

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

_NARRATIVE_GUARDRAIL_INSTRUCTIONS: dict[str, str] = {}


_DEFAULT_NARRATIVE_GUARDRAIL_TEXTS: dict[str, Any] = {
    "instructions": _NARRATIVE_GUARDRAIL_INSTRUCTIONS,
    "directive_high_risk": "",
    "directive_time_context_unverified": "",
    "directive_unsupported_self_claim": "",
    "directive_anthropomorphic_overreach": "",
    "directive_no_self_narrative_expansion": "",
    "directive_reflection_vs_policy_tension": "",
}


def _load_narrative_guardrail_texts(*, render_target_lang: str = "zh") -> dict[str, Any]:
    try:
        raw = _resolve_prompt_template_json_localized(
            "self_reflection.narrative_guardrail_texts",
            render_target_lang=render_target_lang,
        )
    except Exception:
        return dict(_DEFAULT_NARRATIVE_GUARDRAIL_TEXTS)
    return dict(raw) if isinstance(raw, Mapping) else dict(_DEFAULT_NARRATIVE_GUARDRAIL_TEXTS)


def _build_narrative_guardrail_prompt_directives(
    ordered_tags: list[str],
    *,
    risk_level: str,
    allow_self_narrative_expansion: bool,
    render_target_lang: str = "zh",
) -> list[str]:
    directives: list[str] = []
    guardrail_texts = _load_narrative_guardrail_texts(render_target_lang=render_target_lang)

    def _add_directive(text: str) -> None:
        text = str(text or "").strip()
        if text and text not in directives:
            directives.append(text)

    if risk_level == "high":
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_high_risk",
            )
        )
    if "time_context_unverified" in ordered_tags:
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_time_context_unverified",
            )
        )
    if "unsupported_self_claim" in ordered_tags:
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_unsupported_self_claim",
            )
        )
    if "anthropomorphic_overreach" in ordered_tags:
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_anthropomorphic_overreach",
            )
        )
    if not allow_self_narrative_expansion:
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_no_self_narrative_expansion",
            )
        )
    if "reflection_vs_policy_tension" in ordered_tags:
        _add_directive(
            _mapping_text(
                guardrail_texts,
                "directive_reflection_vs_policy_tension",
            )
        )
    return directives


def _build_narrative_guardrails(
    snapshot: dict[str, Any] | None,
    *,
    time_context: str = "",
    growth_awareness: dict[str, Any] | None = None,
    growth_summary: dict[str, Any] | None = None,
    style_evolution: dict[str, Any] | None = None,
    render_policy: dict[str, Any] | None = None,
    runtime_flags: dict[str, Any] | None = None,
    base_tags: list[str] | None = None,
    render_target_lang: str = "zh",
) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    growth_awareness = dict(growth_awareness or {})
    growth_summary = dict(growth_summary or {})
    style_evolution = dict(style_evolution or {})
    render_policy = dict(render_policy or {})
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
    raw_last_deep_talk_days_ago = snapshot.get("last_deep_talk_days_ago")
    try:
        last_deep_talk_days_ago = (
            int(raw_last_deep_talk_days_ago)
            if raw_last_deep_talk_days_ago is not None
            else -1
        )
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
        render_policy.get("should_be_brief")
        or render_policy.get("should_be_withdrawn")
        or render_policy.get("withdrawal_guard")
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
    allow_self_narrative_expansion = False
    guardrail_texts = _load_narrative_guardrail_texts(render_target_lang=render_target_lang)
    instruction_mapping = guardrail_texts.get("instructions")
    instruction_source = instruction_mapping if isinstance(instruction_mapping, Mapping) else {}
    instructions = [
        str(instruction_source.get(tag) or "").strip()
        for tag in ordered_tags
        if str(instruction_source.get(tag) or "").strip()
    ]
    prompt_directives = _build_narrative_guardrail_prompt_directives(
        ordered_tags,
        risk_level=risk_level,
        allow_self_narrative_expansion=allow_self_narrative_expansion,
        render_target_lang=render_target_lang,
    )
    return {
        "risk_level": risk_level,
        "risk_tags": ordered_tags,
        "instruction": " ".join(instructions),
        "allowed_style": allowed_style,
        "allow_self_narrative_expansion": allow_self_narrative_expansion,
        "max_self_narrative_sentences": 1 if risk_level == "high" else 2,
        "prompt_directives": prompt_directives,
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
    strict_llm: bool = False,
    style_evolution: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
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
    from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
        SELF_REFLECTION_AWARENESS_PROMPT_ID,
        SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID,
        SELF_REFLECTION_MOOD_PROMPT_ID,
        SELF_REFLECTION_STYLE_TREND_PROMPT_ID,
    )

    model_port = _canonical_self_reflection_port(
        llm_fn,
        request_id=f"self_reflection_pulse::{int(time.time() * 1000)}",
        enabled=bool(strict_llm),
    )
    awareness_fn = model_port.bind(SELF_REFLECTION_AWARENESS_PROMPT_ID) if model_port else None
    mood_fn = model_port.bind(SELF_REFLECTION_MOOD_PROMPT_ID) if model_port else None
    growth_fn = model_port.bind(SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID) if model_port else None
    style_fn = model_port.bind(SELF_REFLECTION_STYLE_TREND_PROMPT_ID) if model_port else None
    self_awareness = _call_with_optional_render_target_lang(
        build_self_awareness_text,
        snapshot,
        render_target_lang=render_target_lang,
        time_context=time_context,
        llm_fn=awareness_fn,
        strict_llm=strict_llm,
        style_evolution=style_evolution,
    )
    mood_awareness = _call_with_optional_render_target_lang(
        build_mood_awareness_text,
        snapshot,
        render_target_lang=render_target_lang,
        llm_fn=mood_fn,
        strict_llm=strict_llm,
    )
    growth_note = _call_with_optional_render_target_lang(
        build_growth_note_text,
        snapshot,
        render_target_lang=render_target_lang,
        llm_fn=growth_fn,
        strict_llm=strict_llm,
        style_evolution=style_evolution,
    )

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
            style_trend_note = _build_style_trend_note(
                trend,
                llm_fn=style_fn,
                strict_llm=strict_llm,
                render_target_lang=render_target_lang,
            )
            if style_trend_note:
                pulse["style_trend_note"] = style_trend_note

    narrative_guardrails = _build_narrative_guardrails(
        snapshot,
        time_context=time_context,
        growth_awareness=growth_awareness,
        growth_summary=growth_summary,
        style_evolution=style_evolution,
        render_target_lang=render_target_lang,
    )
    if narrative_guardrails:
        pulse["narrative_guardrails"] = narrative_guardrails

    if model_port and model_port.traces:
        pulse["model_attempts"] = model_port.traces

    return pulse


def align_self_reflection_pulse(
    pulse: dict[str, Any] | None,
    *,
    snapshot: dict[str, Any] | None = None,
    offline_state: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    render_policy: dict[str, Any] | None = None,
    runtime_flags: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> dict[str, Any]:
    result = dict(pulse or {})
    if not result:
        return result

    alignment = build_state_aligned_self_awareness(
        dict(snapshot or {}),
        offline_state=dict(offline_state or {}),
        bond_summary=dict(bond_summary or {}),
        motive_state=dict(motive_state or {}),
        render_policy=dict(render_policy or {}),
        runtime_flags=dict(runtime_flags or {}),
        render_target_lang=render_target_lang,
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
        render_policy=render_policy,
        runtime_flags=runtime_flags,
        base_tags=existing_tags,
        render_target_lang=render_target_lang,
    )
    if narrative_guardrails:
        result["narrative_guardrails"] = narrative_guardrails
    return result


# ---------------------------------------------------------------------------
# 风格演变趋势辅助 — v1.11 新增
# ---------------------------------------------------------------------------

# 特质中文名映射
_TRAIT_CN: dict[str, str] = {}

# 风格演变趋势模板池：rising / falling 各 4 条变体
_STYLE_TREND_TEXT_POOL: dict[str, list[str]] = {}


_DEFAULT_STYLE_TREND_TEMPLATES: dict[str, Any] = {
    "trait_cn": _TRAIT_CN,
    "text_pool": _STYLE_TREND_TEXT_POOL,
}


def _load_style_trend_templates(*, render_target_lang: str = "zh") -> dict[str, Any]:
    try:
        raw = _resolve_prompt_template_json_localized(
            "self_reflection.style_trend_templates",
            render_target_lang=render_target_lang,
        )
    except Exception:
        return dict(_DEFAULT_STYLE_TREND_TEMPLATES)
    return dict(raw) if isinstance(raw, Mapping) else dict(_DEFAULT_STYLE_TREND_TEMPLATES)


def _build_style_trend_note(
    trend: dict[str, str],
    *,
    llm_fn: Any | None = None,
    strict_llm: bool = False,
    render_target_lang: str = "zh",
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
    style_trend_templates = _load_style_trend_templates(render_target_lang=render_target_lang)
    trait_cn_mapping = style_trend_templates.get("trait_cn")
    trait_cn = trait_cn_mapping if isinstance(trait_cn_mapping, Mapping) else _TRAIT_CN
    text_pool_mapping = style_trend_templates.get("text_pool")
    text_pool = text_pool_mapping if isinstance(text_pool_mapping, Mapping) else _STYLE_TREND_TEXT_POOL
    rising_pool = text_pool.get("rising")
    falling_pool = text_pool.get("falling")
    rising_templates = rising_pool if isinstance(rising_pool, list) and rising_pool else []
    falling_templates = falling_pool if isinstance(falling_pool, list) and falling_pool else []

    if not rising and not falling:
        return ""

    # ── LLM 动态生成路径 ────────────────────────────────────────────────
    if llm_fn is not None and bool(getattr(llm_fn, "canonical_model_attempt_port", False)):
        try:
            rising_cn = [str(trait_cn.get(k, k)) for k in rising[:2]]
            falling_cn = [str(trait_cn.get(k, k)) for k in falling[:2]]
            hints = []
            if rising_cn:
                hints.append(f"{'、'.join(rising_cn)}在上升")
            if falling_cn:
                hints.append(f"{'、'.join(falling_cn)}在下降")
            hint_str = "，".join(hints)
            prompt = _mapping_text(
                _load_prompt_templates(render_target_lang=render_target_lang),
                "style_trend_prompt",
                hint_str=hint_str,
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 5:
                return result.strip()
        except Exception:
            if strict_llm:
                return ""

    if strict_llm:
        return ""

    # ── 降级：模板池路径 ────────────────────────────────────────────────
    parts: list[str] = []
    for dim in rising[:1]:
        cn = str(trait_cn.get(dim, dim))
        if not rising_templates:
            continue
        tmpl = random.choice(rising_templates)
        parts.append(tmpl.format(trait=cn))
    for dim in falling[:1]:
        cn = str(trait_cn.get(dim, dim))
        if not falling_templates:
            continue
        tmpl = random.choice(falling_templates)
        parts.append(tmpl.format(trait=cn))

    return "，".join(parts) if parts else ""
