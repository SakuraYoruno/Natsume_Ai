"""InteractionGrowthAnalyzer：交互驱动的人格成长引擎——Phase IX。

根据用户与 AI 的实际交互模式动态计算人格成长方向和幅度，
替代硬编码的静态成长规则。

设计原理：
  - 统计最近 N 天的交互类型分布（交互频谱）
  - 根据频谱反推应该强化哪些特质
  - 边际递减：已经很高的特质增长放缓
  - base_delta = 0.01，远小于 TraitDriftGuard 的幅度锁 0.05
  - 只有占比 ≥ 20% 的交互类型才触发对应特质成长

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

    def as_dict(self) -> dict[str, Any]:
        return {
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
    ) -> InteractionGrowthResult:
        """根据交互频谱计算人格成长方向。

        Args:
            interaction_stats: 交互类型 → 次数 统计（如 {"INTERACTION_DEEP": 15, ...}）
            current_traits: 当前人格特质
            analysis_period_days: 分析周期天数（用于审计记录）
            ts_ms: 时间戳

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

        # ── Step 1：计算每种交互类型的占比 ──────────────────────────────
        ratios: dict[str, float] = {
            kind: count / total
            for kind, count in interaction_stats.items()
        }

        # 找出主导交互类型
        dominant = max(interaction_stats, key=interaction_stats.get)  # type: ignore[arg-type]

        # ── Step 2：按占比加权累积各特质的贡献值 ────────────────────────
        trait_contributions: dict[str, float] = {n: 0.0 for n in TRAIT_NAMES}

        for interaction_type, ratio in ratios.items():
            if ratio < self._min_ratio:
                continue  # 占比太低，忽略

            trait_weights = self._trait_map.get(
                interaction_type.upper(), {}
            )
            for trait_name, weight in trait_weights.items():
                if trait_name in trait_contributions:
                    trait_contributions[trait_name] += ratio * weight

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
            "InteractionGrowthAnalyzer: total=%d dominant=%s delta=%s",
            total, dominant, result_delta.as_dict(),
        )

        return InteractionGrowthResult(
            delta=result_delta,
            interaction_stats=dict(interaction_stats),
            trait_contributions=trait_contributions,
            total_interactions=total,
            dominant_interaction=dominant,
            analysis_period_days=analysis_period_days,
            ts_ms=ts_ms,
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
            return _json.loads(canon_path.read_text(encoding="utf-8"))
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
