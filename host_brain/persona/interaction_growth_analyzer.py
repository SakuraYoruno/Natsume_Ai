"""InteractionGrowthAnalyzer：交互驱动的人格成长引擎——Phase IX + 对话质量加权。

根据用户与 AI 的实际交互模式动态计算人格成长方向和幅度，
替代硬编码的静态成长规则。

设计原理：
  - 统计最近 N 天的交互类型分布（交互频谱）
  - 根据频谱反推应该强化哪些特质
  - 边际递减：已经很高的特质增长放缓
  - base_delta = 0.01，远小于 TraitDriftGuard 的幅度锁 0.05
  - 只有占比 ≥ 15% 的交互类型才触发对应特质成长

对话质量加权：
  - 每次交互可附带质量评分（0.0~2.0，1.0=标准质量）
  - 高质量交互（深入、有情感共鸣）放大成长效果
  - 低质量交互（敷衍、无实质内容）缩减成长效果
  - 质量评分可由 InteractionQualityEvaluator 自动计算
  - 不传质量分时行为与之前完全一致（默认质量=1.0）

触发时机：
  - 由 NatsumeScheduler 每周调度一次（周一凌晨 4:00）
  - 也可通过 PersonaGrowthTracker.on_interaction_cycle() 手动触发
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.personality_traits import (
    TRAIT_NAMES,
    PersonalityTraits,
    TraitsDelta,
)

_logger = logging.getLogger("Natsume.interaction_growth")


# ---------------------------------------------------------------------------
# 对话质量评估器
# ---------------------------------------------------------------------------

# 质量评分范围
_QUALITY_MIN = 0.1   # 最低质量（防止归零）
_QUALITY_MAX = 2.0   # 最高质量（防止过度放大）
_QUALITY_DEFAULT = 1.0  # 默认质量（无评分时）


@dataclass(frozen=True)
class InteractionQualityScore:
    """单次交互的质量评分。"""
    interaction_type: str
    quality: float = 1.0       # 质量分 [0.1, 2.0]，1.0=标准
    msg_count: int = 0         # 该次交互的消息轮次数
    avg_msg_length: float = 0  # 平均消息长度（字符数）
    has_emotion_depth: bool = False  # 是否有情感深度
    has_followup: bool = False       # 是否有追问/延续


class InteractionQualityEvaluator:
    """从对话元数据自动评估交互质量。

    质量维度（各维度独立贡献，最终取加权平均）：
      1. 对话轮次深度：轮次越多质量越高（上限10轮）
      2. 消息平均长度：长消息通常意味着更深入的交流
      3. 情感深度：有情感共鸣的对话质量更高
      4. 追问/延续：用户主动追问说明对话有价值

    用法：
        evaluator = InteractionQualityEvaluator()
        score = evaluator.evaluate(
            interaction_type="INTERACTION_DEEP",
            msg_count=8,
            avg_msg_length=120.0,
            has_emotion_depth=True,
            has_followup=True,
        )
        # score.quality ≈ 1.5（高质量深度对话）
    """

    # 各维度权重
    _WEIGHT_MSG_COUNT = 0.30
    _WEIGHT_MSG_LENGTH = 0.25
    _WEIGHT_EMOTION = 0.25
    _WEIGHT_FOLLOWUP = 0.20

    def evaluate(
        self,
        *,
        interaction_type: str,
        msg_count: int = 0,
        avg_msg_length: float = 0.0,
        has_emotion_depth: bool = False,
        has_followup: bool = False,
    ) -> InteractionQualityScore:
        """评估单次交互的质量。

        Returns:
            InteractionQualityScore，quality 字段为最终质量分
        """
        # 维度1：对话轮次深度 → [0.5, 1.5]
        # 1轮=0.5, 3轮=0.8, 5轮=1.0, 10轮+=1.5
        if msg_count <= 0:
            depth_score = 1.0  # 无数据时默认标准
        elif msg_count <= 1:
            depth_score = 0.5
        elif msg_count <= 3:
            depth_score = 0.5 + (msg_count - 1) * 0.15  # 2→0.65, 3→0.80
        elif msg_count <= 10:
            depth_score = 0.8 + (msg_count - 3) * 0.1   # 5→1.0, 10→1.5
        else:
            depth_score = 1.5

        # 维度2：消息平均长度 → [0.6, 1.4]
        # <20字=0.6, 50字=1.0, 150字+=1.4
        if avg_msg_length <= 0:
            length_score = 1.0  # 无数据时默认标准
        elif avg_msg_length < 20:
            length_score = 0.6
        elif avg_msg_length < 50:
            length_score = 0.6 + (avg_msg_length - 20) / 30 * 0.4  # 线性插值到1.0
        elif avg_msg_length < 150:
            length_score = 1.0 + (avg_msg_length - 50) / 100 * 0.4  # 线性插值到1.4
        else:
            length_score = 1.4

        # 维度3：情感深度 → 0.8 / 1.3
        emotion_score = 1.3 if has_emotion_depth else 0.8

        # 维度4：追问/延续 → 0.8 / 1.3
        followup_score = 1.3 if has_followup else 0.8

        # 加权平均
        quality = (
            depth_score * self._WEIGHT_MSG_COUNT
            + length_score * self._WEIGHT_MSG_LENGTH
            + emotion_score * self._WEIGHT_EMOTION
            + followup_score * self._WEIGHT_FOLLOWUP
        )

        # 钳位到合理范围
        quality = max(_QUALITY_MIN, min(_QUALITY_MAX, round(quality, 3)))

        return InteractionQualityScore(
            interaction_type=interaction_type,
            quality=quality,
            msg_count=msg_count,
            avg_msg_length=round(avg_msg_length, 1),
            has_emotion_depth=has_emotion_depth,
            has_followup=has_followup,
        )


# ---------------------------------------------------------------------------
# 交互类型 → 相关特质权重映射
# ---------------------------------------------------------------------------

# 每种交互类型对 7 维特质的影响权重（总和不需要 = 1，用于相对比较）
INTERACTION_TRAIT_MAP: dict[str, dict[str, float]] = {
    # 深度对话：好奇心 > 敏感度 > 温柔度
    "INTERACTION_DEEP": {
        "curiosity": 0.40,
        "sensitivity": 0.30,
        "warmth": 0.20,
        "resilience": 0.10,
    },
    "DEEP_TALK": {
        "curiosity": 0.40,
        "sensitivity": 0.30,
        "warmth": 0.20,
        "resilience": 0.10,
    },
    # 积极互动（长文本/含问号）：好奇心 > 俏皮 > 温柔
    "INTERACTION_ENGAGED": {
        "curiosity": 0.30,
        "playfulness": 0.25,
        "warmth": 0.25,
        "humor": 0.20,
    },
    # 情感支持：温柔度 > 敏感度 > 韧性
    "EMOTIONAL_SUPPORT": {
        "warmth": 0.40,
        "sensitivity": 0.30,
        "resilience": 0.30,
    },
    # 冲突修复：韧性 > 自信度 > 温柔度
    "CONFLICT_RESOLVED": {
        "resilience": 0.40,
        "assertiveness": 0.30,
        "warmth": 0.30,
    },
    # 简短互动：俏皮度 > 幽默感（轻松氛围）
    "INTERACTION_MINIMAL": {
        "playfulness": 0.40,
        "humor": 0.30,
        "warmth": 0.30,
    },
    # 里程碑事件：全面微涨
    "MILESTONE": {
        "warmth": 0.20,
        "curiosity": 0.20,
        "resilience": 0.20,
        "sensitivity": 0.15,
        "playfulness": 0.15,
        "humor": 0.10,
    },
}

# 交互驱动成长的基础 delta（远小于幅度锁 0.05，确保安全）
BASE_DELTA = 0.01

# 交互类型占比阈值：低于此值不触发对应特质成长
MIN_INTERACTION_RATIO = 0.15

# 特质饱和上限：高于此值启动边际递减
DIMINISHING_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InteractionGrowthResult:
    """交互驱动成长分析结果。"""
    delta: TraitsDelta
    interaction_stats: dict[str, int]
    trait_contributions: dict[str, float]
    total_interactions: int
    dominant_interaction: str
    analysis_period_days: int
    ts_ms: int
    quality_multipliers: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "delta": self.delta.as_dict(),
            "interaction_stats": dict(self.interaction_stats),
            "trait_contributions": {
                k: round(v, 4) for k, v in self.trait_contributions.items()
            },
            "total_interactions": self.total_interactions,
            "dominant_interaction": self.dominant_interaction,
            "analysis_period_days": self.analysis_period_days,
            "ts_ms": self.ts_ms,
        }
        if self.quality_multipliers:
            d["quality_multipliers"] = {
                k: round(v, 3) for k, v in self.quality_multipliers.items()
            }
        return d


# ---------------------------------------------------------------------------
# InteractionGrowthAnalyzer — 核心分析器
# ---------------------------------------------------------------------------

class InteractionGrowthAnalyzer:
    """交互驱动的人格成长分析器。

    用法：
        analyzer = InteractionGrowthAnalyzer()
        result = analyzer.analyze(
            interaction_stats={"INTERACTION_DEEP": 15, "EMOTIONAL_SUPPORT": 8, ...},
            current_traits=persona_engine.get_traits(user_id),
        )
        # result.delta 即为建议的 TraitsDelta
    """

    def __init__(
        self,
        *,
        interaction_trait_map: dict[str, dict[str, float]] | None = None,
        base_delta: float = BASE_DELTA,
        min_ratio: float = MIN_INTERACTION_RATIO,
        diminishing_threshold: float = DIMINISHING_THRESHOLD,
    ) -> None:
        self._trait_map = dict(interaction_trait_map or INTERACTION_TRAIT_MAP)
        self._base_delta = float(base_delta)
        self._min_ratio = float(min_ratio)
        self._diminishing_threshold = float(diminishing_threshold)

    @classmethod
    def from_canon(cls, canon_store: Any = None) -> "InteractionGrowthAnalyzer":
        """从 Canon wc-004-persona-growth-rules.json 创建分析器（参数热加载）。

        优先级：Canon 文档 > 硬编码默认值。Canon 不可用时静默降级。
        """
        trait_map: dict[str, dict[str, float]] | None = None
        params: dict[str, Any] = {}

        try:
            raw = _load_canon_growth_raw()
            if raw:
                # 加载 interaction_trait_map
                canon_map = raw.get("interaction_trait_map", {})
                if canon_map and isinstance(canon_map, dict):
                    trait_map = {
                        k.upper(): {tk: float(tv) for tk, tv in v.items()}
                        for k, v in canon_map.items()
                        if isinstance(v, dict)
                    }
                # 加载 growth_params
                canon_params = raw.get("growth_params", {})
                if isinstance(canon_params, dict):
                    params = canon_params
        except Exception as exc:
            _logger.warning("Canon 参数加载失败（降级到默认值）: %s", exc)

        return cls(
            interaction_trait_map=trait_map,
            base_delta=float(params.get("base_delta", BASE_DELTA)),
            min_ratio=float(params.get("min_interaction_ratio", MIN_INTERACTION_RATIO)),
            diminishing_threshold=float(params.get("diminishing_threshold", DIMINISHING_THRESHOLD)),
        )

    def analyze(
        self,
        *,
        interaction_stats: dict[str, int],
        current_traits: PersonalityTraits,
        analysis_period_days: int = 7,
        ts_ms: int = 0,
        interaction_quality_scores: dict[str, float] | None = None,
    ) -> InteractionGrowthResult:
        """根据交互频谱计算人格成长方向。

        Args:
            interaction_stats: 交互类型 → 次数 统计（如 {"INTERACTION_DEEP": 15, ...}）
            current_traits: 当前人格特质
            analysis_period_days: 分析周期天数（用于审计记录）
            ts_ms: 时间戳
            interaction_quality_scores: 交互类型 → 平均质量分（0.1~2.0，1.0=标准）。
                不传或为 None 时所有交互质量视为 1.0（向后兼容）。
                可由 InteractionQualityEvaluator 自动计算后聚合传入。

        Returns:
            InteractionGrowthResult，包含建议的 TraitsDelta
        """
        total = sum(interaction_stats.values())
        if total == 0:
            _logger.info("InteractionGrowthAnalyzer: 无交互数据，跳过")
            return InteractionGrowthResult(
                delta=TraitsDelta(),
                interaction_stats=dict(interaction_stats),
                trait_contributions={n: 0.0 for n in TRAIT_NAMES},
                total_interactions=0,
                dominant_interaction="NONE",
                analysis_period_days=analysis_period_days,
                ts_ms=ts_ms,
            )

        # ── Step 0：构建质量乘数表 ─────────────────────────────────────
        quality_multipliers: dict[str, float] = {}
        if interaction_quality_scores:
            for kind in interaction_stats:
                raw_q = interaction_quality_scores.get(kind, _QUALITY_DEFAULT)
                quality_multipliers[kind] = max(
                    _QUALITY_MIN, min(_QUALITY_MAX, float(raw_q))
                )
        # 无质量分时 quality_multipliers 为空，后续默认 1.0

        # ── Step 1：计算每种交互类型的占比 ──────────────────────────────
        ratios: dict[str, float] = {
            kind: count / total
            for kind, count in interaction_stats.items()
        }

        # 找出主导交互类型
        dominant = max(interaction_stats, key=interaction_stats.get)  # type: ignore[arg-type]

        # ── Step 2：按占比 × 质量加权累积各特质的贡献值 ─────────────────
        trait_contributions: dict[str, float] = {n: 0.0 for n in TRAIT_NAMES}

        for interaction_type, ratio in ratios.items():
            if ratio < self._min_ratio:
                continue  # 占比太低，忽略

            # 获取该交互类型的质量乘数（默认1.0）
            q_mult = quality_multipliers.get(interaction_type, _QUALITY_DEFAULT)

            trait_weights = self._trait_map.get(
                interaction_type.upper(), {}
            )
            for trait_name, weight in trait_weights.items():
                if trait_name in trait_contributions:
                    # 质量分直接作为贡献值的缩放系数
                    trait_contributions[trait_name] += ratio * weight * q_mult

        # ── Step 3：应用边际递减 + base_delta 缩放 ─────────────────────
        deltas: dict[str, float] = {}
        for name in TRAIT_NAMES:
            contribution = trait_contributions[name]
            if contribution < 0.01:
                deltas[name] = 0.0
                continue

            current_val = getattr(current_traits, name, 0.5)

            # 边际递减：特质越接近饱和上限，增长越慢
            if current_val >= self._diminishing_threshold:
                # 超过阈值后，增长率线性衰减到 0
                remaining = max(0.0, 1.0 - current_val)
                ceiling = 1.0 - self._diminishing_threshold
                diminishing_factor = remaining / ceiling if ceiling > 0 else 0.0
            else:
                diminishing_factor = 1.0

            delta_val = contribution * self._base_delta * diminishing_factor
            deltas[name] = round(delta_val, 4)

        result_delta = TraitsDelta(**deltas)

        _logger.info(
            "InteractionGrowthAnalyzer: total=%d dominant=%s quality=%s delta=%s",
            total, dominant,
            {k: round(v, 2) for k, v in quality_multipliers.items()} if quality_multipliers else "default",
            result_delta.as_dict(),
        )

        return InteractionGrowthResult(
            delta=result_delta,
            interaction_stats=dict(interaction_stats),
            trait_contributions=trait_contributions,
            total_interactions=total,
            dominant_interaction=dominant,
            analysis_period_days=analysis_period_days,
            ts_ms=ts_ms,
            quality_multipliers=quality_multipliers,
        )

    @staticmethod
    def collect_interaction_stats_from_ledger(
        ledger: Any,
        user_id: str,
        lookback_days: int = 7,
    ) -> dict[str, int]:
        """从 SQLiteLedger 的 bond_update_log 收集交互统计。

        Args:
            ledger: SQLiteLedger 实例
            user_id: 用户 ID
            lookback_days: 回溯天数

        Returns:
            交互类型 → 次数 的字典
        """
        import time as _time

        stats: dict[str, int] = {}
        cutoff_ts = int(_time.time() * 1000) - lookback_days * 86400 * 1000

        try:
            db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
            if not db_path:
                _logger.warning("InteractionGrowthAnalyzer: ledger 无 db_path")
                return stats

            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT update_kind, COUNT(*) FROM bond_update_log "
                    "WHERE update_kind NOT IN ('INIT', 'STAGE_TRANSITION') "
                    "AND status IN ('OK', 'SUCCESS') "
                    "AND user_id = ? "
                    "AND ts_ms >= ? "
                    "GROUP BY update_kind",
                    (user_id, cutoff_ts),
                ).fetchall()
                for kind, count in rows:
                    stats[str(kind).strip().upper()] = int(count)
            finally:
                conn.close()
        except Exception as exc:
            _logger.warning("交互统计收集失败: %s", exc)

        return stats


# ---------------------------------------------------------------------------
# Canon 规则文件读取辅助
# ---------------------------------------------------------------------------

def _load_canon_growth_raw() -> dict[str, Any] | None:
    """从 Canon 目录直接读取 wc-004-persona-growth-rules.json。

    这是一个轻量级的文件读取方式，不依赖 WorldCanonStore 实例。
    适用于初始化阶段需要加载配置但 WorldCanonStore 尚未可用的场景。
    """
    try:
        from pathlib import Path
        import json as _json
        canon_path = (
            Path(__file__).resolve().parent.parent
            / "canon" / "world" / "wc-004-persona-growth-rules.json"
        )
        if canon_path.exists():
            return _json.loads(canon_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        _logger.debug("Canon 规则文件读取失败: %s", exc)
    return None


# ---------------------------------------------------------------------------
# 调度器任务工厂
# ---------------------------------------------------------------------------

def make_interaction_growth_task_config() -> "SchedulerTaskConfig":
    """创建交互驱动成长调度任务配置（每周一凌晨 04:00）。

    Returns:
        SchedulerTaskConfig 实例，可注册到 SchedulerTaskRegistry。
    """
    from host_brain.scheduler.task_registry import (
        SchedulerTaskConfig,
        TaskPriority,
        TriggerMode,
    )
    return SchedulerTaskConfig(
        task_id="interaction_driven_persona_growth",
        trigger_mode=TriggerMode.CRON,
        trigger_config={"day_of_week": 0, "hour": 4, "minute": 0},
        intent_template={
            "skill_name": "interaction_persona_growth",
            "args": {"lookback_days": 7},
        },
        priority=TaskPriority.LOW,
        mode_allowlist=["normal", "sleep"],
        enabled=True,
        max_consecutive_failures=3,
        description="每周一 04:00 分析交互模式，数据驱动人格微调（Phase IX）",
    )
