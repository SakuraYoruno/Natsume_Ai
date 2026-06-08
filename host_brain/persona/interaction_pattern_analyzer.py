"""InteractionPatternAnalyzer：对话模式无监督归纳——Phase XIX 涌现式成长。

职责：
  1. 从 EpisodicStore 召回近期（默认 7 天）情节记忆
  2. 将记忆文本聚合为"本周交互摘要"
  3. 调用 LLM 分析交互模式，提取特质变化建议（无监督归纳）
  4. 返回结构化的 PatternAnalysisResult，供 EmergentGrowthEngine 消费

设计原则：
  - LLM 不可用时降级到基于关键词的规则归纳（完全向后兼容）
  - 不直接修改特质，只生成"建议"，由 EmergentGrowthEngine 决策
  - 分析结果可序列化，支持审计追溯
  - 每次分析最多处理 50 条记忆（防止 token 超限）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)

_logger = logging.getLogger("Natsume.interaction_pattern_analyzer")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 每次分析最多处理的记忆条数（防止 LLM token 超限）
MAX_MEMORIES_PER_ANALYSIS = 50

# 默认分析回溯天数
DEFAULT_LOOKBACK_DAYS = 7

# 特质建议的最大幅度（安全上限，由 EmergentGrowthEngine 进一步校验）
MAX_SUGGESTION_DELTA = 0.03

def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _get_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("interaction_pattern_analyzer.runtime_texts")


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


def _get_pattern_analysis_prompt_template() -> str:
    return resolve_prompt_template_text("interaction_pattern_analyzer.analysis_prompt")

# 关键词规则归纳（LLM 不可用时的降级方案）
_KEYWORD_TRAIT_RULES: list[tuple[list[str], str, float]] = [
    # (关键词列表, 特质名, delta)
    (["温柔", "体贴", "关心", "安慰", "陪伴"], "warmth", 0.01),
    (["好奇", "为什么", "怎么", "了解", "探索", "学习"], "curiosity", 0.01),
    (["哈哈", "笑", "开玩笑", "有趣", "好玩", "逗"], "humor", 0.01),
    (["感受", "心情", "情绪", "难过", "开心", "担心"], "sensitivity", 0.01),
    (["觉得", "认为", "我的想法", "我认为", "坚持"], "assertiveness", 0.01),
    (["撒娇", "嘻嘻", "~", "啦", "呀", "嗯嗯"], "playfulness", 0.01),
    (["没关系", "会好的", "加油", "振作", "坚强"], "resilience", 0.01),
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternAnalysisResult:
    """对话模式分析结果。"""

    pattern_summary: str
    """本周交互模式的一句话总结"""

    dominant_theme: str
    """主导主题（如：情感支持/轻松玩闹/深度探讨/日常陪伴）"""

    trait_suggestions: dict[str, float]
    """各维度特质变化建议（范围 [-0.03, 0.03]）"""

    suggestion_reason: str
    """建议变化的理由"""

    confidence: float
    """分析置信度 [0.0, 1.0]"""

    memory_count: int
    """参与分析的记忆条数"""

    analysis_method: str
    """分析方法：'llm' / 'keyword_rules' / 'empty'"""

    ts_ms: int
    """分析时间戳"""

    raw_memories_digest: str = ""
    """参与分析的记忆摘要（前 200 字，用于审计）"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_summary": self.pattern_summary,
            "dominant_theme": self.dominant_theme,
            "trait_suggestions": {k: round(v, 4) for k, v in self.trait_suggestions.items()},
            "suggestion_reason": self.suggestion_reason,
            "confidence": round(self.confidence, 3),
            "memory_count": self.memory_count,
            "analysis_method": self.analysis_method,
            "ts_ms": self.ts_ms,
            "raw_memories_digest": self.raw_memories_digest,
        }

    @property
    def has_suggestions(self) -> bool:
        """是否有非零的特质变化建议。"""
        return any(abs(v) > 0.001 for v in self.trait_suggestions.values())


# ---------------------------------------------------------------------------
# InteractionPatternAnalyzer — 核心分析器
# ---------------------------------------------------------------------------

class InteractionPatternAnalyzer:
    """对话模式无监督归纳分析器。

    用法：
        analyzer = InteractionPatternAnalyzer()
        result = analyzer.analyze(
            user_id="natsume_ai",
            episodic_store=store,
            current_traits=persona_engine.get_traits("natsume_ai"),
            lookback_days=7,
        )
        # result.trait_suggestions 即为建议的特质变化方向
    """

    def __init__(
        self,
        *,
        llm_fn: Any | None = None,
        strict_llm: bool = False,
        max_memories: int = MAX_MEMORIES_PER_ANALYSIS,
        min_confidence_threshold: float = 0.3,
    ) -> None:
        """初始化分析器。

        Args:
            llm_fn: LLM 调用函数 (prompt: str) -> str。None 时降级到关键词规则。
            max_memories: 每次分析最多处理的记忆条数
            min_confidence_threshold: 最低置信度阈值，低于此值的建议被忽略
        """
        self._llm_fn = llm_fn
        self._strict_llm = bool(strict_llm)
        self._max_memories = int(max_memories)
        self._min_confidence = float(min_confidence_threshold)

    def analyze(
        self,
        *,
        user_id: str,
        episodic_store: Any,
        current_traits: Any,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        ts_ms: int = 0,
    ) -> PatternAnalysisResult:
        """分析近期对话模式，提取特质变化建议。

        Args:
            user_id: 用户 ID
            episodic_store: EpisodicStore 实例（host_brain.memory.episodic.EpisodicStore）
            current_traits: PersonalityTraits 实例
            lookback_days: 回溯天数
            ts_ms: 分析时间戳（0 时自动取当前时间）

        Returns:
            PatternAnalysisResult
        """
        ts_ms = ts_ms or int(time.time() * 1000)

        # ── Step 1：召回近期记忆 ──────────────────────────────────────────
        memories = self._recall_recent_memories(
            episodic_store=episodic_store,
            user_id=user_id,
            lookback_days=lookback_days,
        )

        if not memories:
            _logger.info(
                "InteractionPatternAnalyzer [%s]: 无近期记忆，跳过分析", user_id
            )
            return self._empty_result(ts_ms)

        # ── Step 2：聚合记忆文本 ──────────────────────────────────────────
        memories_text, digest = self._aggregate_memories(memories)

        # ── Step 3：分析（LLM 优先，降级到关键词规则）────────────────────
        if self._llm_fn is not None:
            result = self._analyze_with_llm(
                memories_text=memories_text,
                current_traits=current_traits,
                lookback_days=lookback_days,
                memory_count=len(memories),
                ts_ms=ts_ms,
                digest=digest,
            )
        elif self._strict_llm:
            result = self._llm_required_result(
                memory_count=len(memories),
                ts_ms=ts_ms,
                digest=digest,
                reason="llm_fn_missing",
            )
        else:
            result = self._analyze_with_keywords(
                memories_text=memories_text,
                memory_count=len(memories),
                ts_ms=ts_ms,
                digest=digest,
            )

        _logger.info(
            "InteractionPatternAnalyzer [%s]: method=%s memories=%d "
            "confidence=%.2f theme=%s suggestions=%s",
            user_id,
            result.analysis_method,
            result.memory_count,
            result.confidence,
            result.dominant_theme,
            {k: v for k, v in result.trait_suggestions.items() if abs(v) > 0.001},
        )
        return result

    # ── 私有方法 ──────────────────────────────────────────────────────────

    def _recall_recent_memories(
        self,
        *,
        episodic_store: Any,
        user_id: str,
        lookback_days: int,
    ) -> list[Any]:
        """从 EpisodicStore 召回近期记忆。"""
        try:
            cutoff_ms = int(time.time() * 1000) - lookback_days * 86400 * 1000

            # 尝试 host_brain.memory.episodic.EpisodicStore 接口
            records = getattr(episodic_store, "_records", {})
            if isinstance(records, dict):
                recent = [
                    r for r in records.values()
                    if (
                        getattr(r, "ts_ms", 0) >= cutoff_ms
                        and getattr(r, "deleted_at", None) is None
                        and getattr(r, "merged_into", None) is None
                    )
                ]
                # 按时间排序，取最新的 N 条
                recent.sort(key=lambda r: getattr(r, "ts_ms", 0), reverse=True)
                return recent[: self._max_memories]

            # 尝试 host_brain.app.memory.episodic_store.EpisodicStore 接口
            if hasattr(episodic_store, "query_by_session"):
                # 无法按时间过滤，取最近 N 条
                all_records = list(getattr(episodic_store, "_records", {}).values())
                all_records.sort(
                    key=lambda r: getattr(r, "created_at", 0), reverse=True
                )
                return all_records[: self._max_memories]

        except Exception as exc:
            _logger.warning("记忆召回失败（降级到空列表）: %s", exc)

        return []

    def _aggregate_memories(
        self, memories: list[Any]
    ) -> tuple[str, str]:
        """将记忆列表聚合为文本摘要。

        Returns:
            (完整文本, 前 200 字摘要)
        """
        lines: list[str] = []
        for i, r in enumerate(memories, 1):
            text = str(getattr(r, "text", "") or getattr(r, "content", "") or "")
            emotion = str(getattr(r, "emotion_tag", "") or "")
            if text:
                line = f"{i}. [{emotion}] {text[:100]}" if emotion else f"{i}. {text[:100]}"
                lines.append(line)

        full_text = "\n".join(lines)
        digest = full_text[:200] + ("..." if len(full_text) > 200 else "")
        return full_text, digest

    def _analyze_with_llm(
        self,
        *,
        memories_text: str,
        current_traits: Any,
        lookback_days: int,
        memory_count: int,
        ts_ms: int,
        digest: str,
    ) -> PatternAnalysisResult:
        """使用 LLM 分析对话模式。"""
        from host_brain.persona.personality_traits import TRAIT_NAMES  # noqa: PLC0415

        try:
            # 构建 prompt
            trait_values = {
                name: float(getattr(current_traits, name, 0.5))
                for name in TRAIT_NAMES
            }
            prompt = _get_pattern_analysis_prompt_template().format(
                days=lookback_days,
                count=memory_count,
                memories_text=memories_text,
                **trait_values,
            )

            # 调用 LLM
            raw_response = self._llm_fn(prompt)  # type: ignore[misc]

            # 解析 JSON 响应
            return self._parse_llm_response(
                raw_response=raw_response,
                memory_count=memory_count,
                ts_ms=ts_ms,
                digest=digest,
            )

        except Exception as exc:
            _logger.warning("LLM 分析失败: %s", exc)
            if self._strict_llm:
                return self._llm_required_result(
                    memory_count=memory_count,
                    ts_ms=ts_ms,
                    digest=digest,
                    reason=str(exc),
                )
            return self._analyze_with_keywords(
                memories_text=memories_text,
                memory_count=memory_count,
                ts_ms=ts_ms,
                digest=digest,
            )

    def _parse_llm_response(
        self,
        *,
        raw_response: str,
        memory_count: int,
        ts_ms: int,
        digest: str,
    ) -> PatternAnalysisResult:
        """解析 LLM 返回的 JSON 响应。"""
        import json as _json  # noqa: PLC0415
        from host_brain.persona.personality_traits import TRAIT_NAMES  # noqa: PLC0415

        # 提取 JSON 块（LLM 可能在 JSON 前后有额外文字）
        text = str(raw_response or "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"LLM 响应中未找到 JSON: {text[:100]}")

        data = _json.loads(text[start:end])

        # 提取并校验 trait_suggestions
        raw_suggestions = data.get("trait_suggestions", {})
        suggestions: dict[str, float] = {}
        for name in TRAIT_NAMES:
            raw_val = float(raw_suggestions.get(name, 0.0))
            # 钳位到安全范围
            suggestions[name] = max(
                -MAX_SUGGESTION_DELTA,
                min(MAX_SUGGESTION_DELTA, raw_val),
            )

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        runtime_texts = _get_runtime_texts()

        # 置信度不足时清零建议
        if confidence < self._min_confidence:
            suggestions = {name: 0.0 for name in TRAIT_NAMES}

        return PatternAnalysisResult(
            pattern_summary=str(
                data.get(
                    "pattern_summary",
                    _mapping_text(runtime_texts, "llm_pattern_summary_default"),
                )
            )[:50],
            dominant_theme=str(
                data.get(
                    "dominant_theme",
                    _mapping_text(runtime_texts, "llm_dominant_theme_default"),
                )
            )[:20],
            trait_suggestions=suggestions,
            suggestion_reason=str(data.get("suggestion_reason", ""))[:100],
            confidence=confidence,
            memory_count=memory_count,
            analysis_method="llm",
            ts_ms=ts_ms,
            raw_memories_digest=digest,
        )

    def _analyze_with_keywords(
        self,
        *,
        memories_text: str,
        memory_count: int,
        ts_ms: int,
        digest: str,
    ) -> PatternAnalysisResult:
        """使用关键词规则归纳对话模式（LLM 降级方案）。"""
        from host_brain.persona.personality_traits import TRAIT_NAMES  # noqa: PLC0415

        runtime_texts = _get_runtime_texts()
        suggestions: dict[str, float] = {name: 0.0 for name in TRAIT_NAMES}
        hit_traits: list[str] = []

        for keywords, trait_name, delta in _KEYWORD_TRAIT_RULES:
            hit_count = sum(1 for kw in keywords if kw in memories_text)
            if hit_count >= 2:  # 至少命中 2 个关键词才触发
                suggestions[trait_name] = delta
                hit_traits.append(trait_name)

        # 置信度基于记忆数量
        if memory_count >= 20:
            confidence = 0.6
        elif memory_count >= 10:
            confidence = 0.4
        elif memory_count >= 5:
            confidence = 0.3
        else:
            confidence = 0.1
            suggestions = {name: 0.0 for name in TRAIT_NAMES}

        dominant_theme = _mapping_text(
            runtime_texts,
            "keyword_theme_default",
        )
        if "warmth" in hit_traits or "sensitivity" in hit_traits:
            dominant_theme = _mapping_text(
                runtime_texts,
                "keyword_theme_support",
            )
        elif "humor" in hit_traits or "playfulness" in hit_traits:
            dominant_theme = _mapping_text(
                runtime_texts,
                "keyword_theme_playful",
            )
        elif "curiosity" in hit_traits or "assertiveness" in hit_traits:
            dominant_theme = _mapping_text(
                runtime_texts,
                "keyword_theme_inquiry",
            )

        reason = (
            _mapping_text(
                runtime_texts,
                "keyword_reason_hit",
                hit_traits=hit_traits,
            )
            if hit_traits
            else _mapping_text(
                runtime_texts,
                "keyword_reason_none",
            )
        )

        return PatternAnalysisResult(
            pattern_summary=_mapping_text(
                runtime_texts,
                "keyword_summary_template",
                dominant_theme=dominant_theme,
            ),
            dominant_theme=dominant_theme,
            trait_suggestions=suggestions,
            suggestion_reason=reason,
            confidence=confidence,
            memory_count=memory_count,
            analysis_method="keyword_rules",
            ts_ms=ts_ms,
            raw_memories_digest=digest,
        )

    def _empty_result(self, ts_ms: int) -> PatternAnalysisResult:
        """无记忆时返回空结果。"""
        from host_brain.persona.personality_traits import TRAIT_NAMES  # noqa: PLC0415
        runtime_texts = _get_runtime_texts()
        return PatternAnalysisResult(
            pattern_summary=_mapping_text(
                runtime_texts,
                "empty_pattern_summary",
            ),
            dominant_theme=_mapping_text(
                runtime_texts,
                "empty_dominant_theme",
            ),
            trait_suggestions={name: 0.0 for name in TRAIT_NAMES},
            suggestion_reason=_mapping_text(
                runtime_texts,
                "empty_reason",
            ),
            confidence=0.0,
            memory_count=0,
            analysis_method="empty",
            ts_ms=ts_ms,
        )

    def _llm_required_result(
        self,
        *,
        memory_count: int,
        ts_ms: int,
        digest: str,
        reason: str,
    ) -> PatternAnalysisResult:
        runtime_texts = _get_runtime_texts()
        return PatternAnalysisResult(
            pattern_summary=_mapping_text(
                runtime_texts,
                "llm_pattern_summary_default",
            )[:50],
            dominant_theme=_mapping_text(
                runtime_texts,
                "llm_dominant_theme_default",
            )[:20],
            trait_suggestions={},
            suggestion_reason=str(reason or "llm_required")[:100],
            confidence=0.0,
            memory_count=int(memory_count or 0),
            analysis_method="llm_required",
            ts_ms=int(ts_ms or 0),
            raw_memories_digest=digest,
        )
