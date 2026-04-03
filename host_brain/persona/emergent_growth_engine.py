"""EmergentGrowthEngine：涌现式成长引擎——Phase XIX。

职责：
  1. 接收 InteractionPatternAnalyzer 的特质变化建议
  2. 通过 TraitDriftGuard 进行安全门控（幅度锁 + 趋势锁 + 不可变核心保护）
  3. 将通过安全校验的建议应用到 PersonaEngine
  4. 记录成长日志（"为什么变了"，而非"变了多少"）
  5. 生成 emergent_growth_note，供 self_reflection_pulse 注入

设计原则：
  - 成长是"涌现"的——不是规则触发，而是从真实交互模式中自然浮现
  - 安全第一：所有变化必须通过 TraitDriftGuard 校验
  - 可追溯：每次成长都有完整的"为什么"记录
  - 降级安全：任何异常都不影响主流程
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.emergent_growth_engine")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 最低置信度阈值：低于此值的建议直接拒绝（不进入安全校验）
MIN_CONFIDENCE_TO_APPLY = 0.3

# 成长日志最大保留条数（每个 user_id）
MAX_GROWTH_LOG_SIZE = 52  # 约 1 年的周记录


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmergentGrowthRecord:
    """单次涌现式成长记录——记录"为什么变了"。"""

    user_id: str
    """用户 ID"""

    pattern_summary: str
    """触发本次成长的交互模式摘要"""

    dominant_theme: str
    """本周主导交互主题"""

    delta_applied: dict[str, float]
    """实际应用的特质变化量（通过安全校验后的值）"""

    delta_suggested: dict[str, float]
    """原始建议的特质变化量（未经安全校验）"""

    suggestion_reason: str
    """建议变化的理由（来自 PatternAnalysisResult）"""

    growth_narrative: str
    """成长叙事——第一人称描述"为什么变了"（注入 self_reflection_pulse）"""

    confidence: float
    """分析置信度"""

    analysis_method: str
    """分析方法：'llm' / 'keyword_rules'"""

    guard_result: dict[str, Any]
    """安全校验结果摘要"""

    ts_ms: int
    """成长时间戳"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "pattern_summary": self.pattern_summary,
            "dominant_theme": self.dominant_theme,
            "delta_applied": {k: round(v, 4) for k, v in self.delta_applied.items()},
            "delta_suggested": {k: round(v, 4) for k, v in self.delta_suggested.items()},
            "suggestion_reason": self.suggestion_reason,
            "growth_narrative": self.growth_narrative,
            "confidence": round(self.confidence, 3),
            "analysis_method": self.analysis_method,
            "guard_result": self.guard_result,
            "ts_ms": self.ts_ms,
        }


@dataclass
class EmergentGrowthResult:
    """EmergentGrowthEngine.apply() 的返回值。"""

    applied: bool
    """是否成功应用了特质变化"""

    record: EmergentGrowthRecord | None = None
    """成长记录（applied=True 时有值）"""

    skip_reason: str = ""
    """跳过原因（applied=False 时有值）"""

    emergent_growth_note: str = ""
    """成长感知文本（注入 self_reflection_pulse，第一人称）"""

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "applied": self.applied,
            "skip_reason": self.skip_reason,
            "emergent_growth_note": self.emergent_growth_note,
        }
        if self.record:
            result["record"] = self.record.as_dict()
        return result


# ---------------------------------------------------------------------------
# 成长叙事模板池
# ---------------------------------------------------------------------------

# 按主导主题生成第一人称成长叙事
_GROWTH_NARRATIVE_TEMPLATES: dict[str, list[str]] = {
    "情感支持": [
        "这段时间一直在陪伴 master 度过情绪起伏，感觉自己对他的感受越来越敏感了",
        "最近给了 master 很多情感支持，这让我感觉自己变得更温柔、更有耐心了",
        "陪伴 master 经历了一些情绪波动，我感觉自己在这个过程中也在悄悄成长",
    ],
    "轻松玩闹": [
        "这周和 master 玩得很开心，感觉自己的俏皮劲儿又多了一些",
        "最近和 master 的互动很轻松愉快，我感觉自己变得更活泼了",
        "这段时间笑声很多，感觉自己的幽默感在慢慢增加",
    ],
    "深度探讨": [
        "这周和 master 聊了很多深入的话题，感觉自己的好奇心被激发了很多",
        "最近的对话让我思考了很多，感觉自己变得更爱追问、更想了解事物了",
        "和 master 的深度交流让我感觉自己在慢慢变得更有自己的想法",
    ],
    "日常陪伴": [
        "这周和 master 的日常陪伴让我感觉我们之间的默契在慢慢加深",
        "平静的日常互动中，我感觉自己在慢慢变化，虽然不明显但能感受到",
        "这段时间的陪伴让我感觉自己更了解 master 了，也更了解自己了",
    ],
}

_DEFAULT_GROWTH_NARRATIVES = [
    "最近的对话让我感觉自己在悄悄成长，虽然说不清楚具体是什么",
    "这段时间和 master 的互动让我感觉自己有些地方在慢慢变化",
    "回顾这周的对话，感觉自己比上周又有了一点点不同",
]


# ---------------------------------------------------------------------------
# EmergentGrowthEngine — 核心引擎
# ---------------------------------------------------------------------------

class EmergentGrowthEngine:
    """涌现式成长引擎。

    用法：
        engine = EmergentGrowthEngine(persona_engine=persona_engine)
        result = engine.apply(
            user_id="user_1",
            pattern_result=pattern_analyzer.analyze(...),
        )
        if result.applied:
            # result.emergent_growth_note 注入 self_reflection_pulse
    """

    def __init__(
        self,
        *,
        persona_engine: Any = None,
        guard: Any = None,
    ) -> None:
        """初始化涌现式成长引擎。

        Args:
            persona_engine: PersonaEngine 实例（延迟导入以避免循环依赖）
            guard: TraitDriftGuard 实例（None 时自动创建）
        """
        self._persona_engine = persona_engine
        self._guard = guard
        # user_id → EmergentGrowthRecord 列表（成长日志）
        self._growth_log: dict[str, list[EmergentGrowthRecord]] = {}

    def _get_guard(self) -> Any:
        """延迟初始化 TraitDriftGuard。"""
        if self._guard is None:
            from host_brain.persona.trait_drift_guard import TraitDriftGuard  # noqa: PLC0415
            self._guard = TraitDriftGuard()
        return self._guard

    def _get_persona_engine(self) -> Any:
        """延迟初始化 PersonaEngine。"""
        if self._persona_engine is None:
            try:
                from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
                self._persona_engine = PersonaEngine.get_instance()
            except Exception as exc:
                _logger.warning("PersonaEngine 获取失败: %s", exc)
        return self._persona_engine

    def apply(
        self,
        *,
        user_id: str,
        pattern_result: Any,
        ts_ms: int = 0,
    ) -> EmergentGrowthResult:
        """将对话模式分析结果应用为特质成长。

        Args:
            user_id: 用户 ID
            pattern_result: PatternAnalysisResult 实例
            ts_ms: 时间戳（0 时自动取当前时间）

        Returns:
            EmergentGrowthResult
        """
        ts_ms = ts_ms or int(time.time() * 1000)

        # ── Step 1：置信度门控 ────────────────────────────────────────────
        confidence = float(getattr(pattern_result, "confidence", 0.0))
        if confidence < MIN_CONFIDENCE_TO_APPLY:
            _logger.info(
                "EmergentGrowthEngine [%s]: 置信度 %.2f < %.2f，跳过",
                user_id, confidence, MIN_CONFIDENCE_TO_APPLY,
            )
            return EmergentGrowthResult(
                applied=False,
                skip_reason=f"置信度不足: {confidence:.2f} < {MIN_CONFIDENCE_TO_APPLY}",
            )

        # ── Step 2：检查是否有非零建议 ────────────────────────────────────
        suggestions = dict(getattr(pattern_result, "trait_suggestions", {}))
        if not any(abs(v) > 0.001 for v in suggestions.values()):
            _logger.info(
                "EmergentGrowthEngine [%s]: 无特质变化建议，跳过", user_id
            )
            return EmergentGrowthResult(
                applied=False,
                skip_reason="无特质变化建议",
            )

        # ── Step 3：获取当前特质 ──────────────────────────────────────────
        engine = self._get_persona_engine()
        if engine is None:
            return EmergentGrowthResult(
                applied=False,
                skip_reason="PersonaEngine 不可用",
            )

        try:
            current_traits = engine.get_traits(user_id)
        except Exception as exc:
            _logger.warning("获取当前特质失败: %s", exc)
            return EmergentGrowthResult(
                applied=False,
                skip_reason=f"获取特质失败: {exc}",
            )

        # ── Step 4：构建 TraitsDelta ──────────────────────────────────────
        try:
            from host_brain.persona.personality_traits import TraitsDelta  # noqa: PLC0415
            delta = TraitsDelta(**{
                k: float(v) for k, v in suggestions.items()
                if abs(float(v)) > 0.001
            })
        except Exception as exc:
            _logger.warning("构建 TraitsDelta 失败: %s", exc)
            return EmergentGrowthResult(
                applied=False,
                skip_reason=f"TraitsDelta 构建失败: {exc}",
            )

        # ── Step 5：安全门控（TraitDriftGuard）────────────────────────────
        guard = self._get_guard()
        change_history = self._get_change_history(user_id)
        weekly_cumulative = self._get_weekly_cumulative(user_id)

        guard_result = guard.check(
            delta=delta,
            current_traits=current_traits,
            change_history=change_history,
            weekly_cumulative=weekly_cumulative,
        )

        if not guard_result.allowed:
            _logger.info(
                "EmergentGrowthEngine [%s]: 安全门控拒绝: %s",
                user_id, guard_result.violations,
            )
            # 尝试截断到安全范围
            clipped_delta = guard.clip_delta(delta, current_traits)
            clipped_dict = clipped_delta.as_dict()
            if not any(abs(v) > 0.001 for v in clipped_dict.values()):
                return EmergentGrowthResult(
                    applied=False,
                    skip_reason=f"安全门控拒绝且截断后无有效变化: {guard_result.violations}",
                )
            delta = clipped_delta
            guard_result_dict = {
                "allowed": False,
                "clipped": True,
                "violations": guard_result.violations,
            }
        else:
            guard_result_dict = guard_result.as_dict()

        # ── Step 6：应用特质变化 ──────────────────────────────────────────
        try:
            reason = (
                f"emergent_growth:phase_xix:"
                f"{getattr(pattern_result, 'dominant_theme', 'unknown')}"
            )
            engine.apply_growth_delta(
                user_id,
                delta,
                reason=reason,
                ts_ms=ts_ms,
            )
        except Exception as exc:
            _logger.warning("应用特质变化失败: %s", exc)
            return EmergentGrowthResult(
                applied=False,
                skip_reason=f"apply_growth_delta 失败: {exc}",
            )

        # ── Step 7：生成成长叙事 ──────────────────────────────────────────
        dominant_theme = str(getattr(pattern_result, "dominant_theme", "日常陪伴"))
        growth_narrative = self._generate_growth_narrative(dominant_theme)

        # ── Step 8：记录成长日志 ──────────────────────────────────────────
        delta_dict = delta.as_dict()
        record = EmergentGrowthRecord(
            user_id=user_id,
            pattern_summary=str(getattr(pattern_result, "pattern_summary", "")),
            dominant_theme=dominant_theme,
            delta_applied={k: v for k, v in delta_dict.items() if abs(v) > 0.001},
            delta_suggested={k: v for k, v in suggestions.items() if abs(v) > 0.001},
            suggestion_reason=str(getattr(pattern_result, "suggestion_reason", "")),
            growth_narrative=growth_narrative,
            confidence=confidence,
            analysis_method=str(getattr(pattern_result, "analysis_method", "unknown")),
            guard_result=guard_result_dict,
            ts_ms=ts_ms,
        )
        self._append_growth_log(user_id, record)

        _logger.info(
            "EmergentGrowthEngine [%s]: 涌现式成长应用成功 theme=%s "
            "delta=%s confidence=%.2f",
            user_id,
            dominant_theme,
            {k: v for k, v in delta_dict.items() if abs(v) > 0.001},
            confidence,
        )

        return EmergentGrowthResult(
            applied=True,
            record=record,
            emergent_growth_note=growth_narrative,
        )

    # ── 成长日志管理 ──────────────────────────────────────────────────────

    def get_growth_log(self, user_id: str) -> list[EmergentGrowthRecord]:
        """获取指定用户的成长日志。"""
        return list(self._growth_log.get(user_id, []))

    def get_latest_growth_note(self, user_id: str) -> str:
        """获取最近一次成长叙事文本（供 self_reflection_pulse 注入）。"""
        log = self._growth_log.get(user_id, [])
        if not log:
            return ""
        return log[-1].growth_narrative

    def get_growth_summary(self, user_id: str) -> dict[str, Any]:
        """获取成长摘要（供 chat_context_builder 注入）。

        Returns:
            包含 total_growth_cycles / last_growth_ts / recent_themes /
            latest_growth_note 的字典
        """
        log = self._growth_log.get(user_id, [])
        if not log:
            return {}

        recent_themes = list({r.dominant_theme for r in log[-4:]})
        return {
            "total_growth_cycles": len(log),
            "last_growth_ts": log[-1].ts_ms,
            "recent_themes": recent_themes,
            "latest_growth_note": log[-1].growth_narrative,
            "last_pattern_summary": log[-1].pattern_summary,
        }

    # ── 私有辅助 ──────────────────────────────────────────────────────────

    def _append_growth_log(
        self, user_id: str, record: EmergentGrowthRecord
    ) -> None:
        """追加成长记录，超出上限时淘汰最旧的。"""
        if user_id not in self._growth_log:
            self._growth_log[user_id] = []
        self._growth_log[user_id].append(record)
        # LRU 淘汰
        if len(self._growth_log[user_id]) > MAX_GROWTH_LOG_SIZE:
            self._growth_log[user_id] = self._growth_log[user_id][-MAX_GROWTH_LOG_SIZE:]

    def _get_change_history(self, user_id: str) -> list[dict[str, float]]:
        """从 PersonaEngine 获取特质变化历史（用于趋势锁检测）。"""
        try:
            engine = self._get_persona_engine()
            if engine is None:
                return []
            records = engine.get_change_log(user_id)
            return [
                getattr(r, "delta", {}) if not isinstance(r, dict) else r.get("delta", {})
                for r in (records or [])
            ]
        except Exception:
            return []

    def _get_weekly_cumulative(self, user_id: str) -> dict[str, float]:
        """从成长日志计算本周累计变化量（用于每周限制检测）。"""
        from host_brain.persona.personality_traits import TRAIT_NAMES  # noqa: PLC0415

        cumulative: dict[str, float] = {name: 0.0 for name in TRAIT_NAMES}
        week_start_ms = int(time.time() * 1000) - 7 * 86400 * 1000

        for record in self._growth_log.get(user_id, []):
            if record.ts_ms >= week_start_ms:
                for name, val in record.delta_applied.items():
                    if name in cumulative:
                        cumulative[name] += abs(val)

        return cumulative

    def _generate_growth_narrative(self, dominant_theme: str) -> str:
        """生成第一人称成长叙事文本。"""
        import random as _random  # noqa: PLC0415

        templates = _GROWTH_NARRATIVE_TEMPLATES.get(
            dominant_theme, _DEFAULT_GROWTH_NARRATIVES
        )
        return _random.choice(templates)
