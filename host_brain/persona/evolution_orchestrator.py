"""EvolutionOrchestrator：自我进化编排器——Phase XVI。

职责：
  1. 每日进化周期调度（由 NatsumeScheduler 在 sleep 模式触发）
  2. 串联所有进化相关模块：
     - SelfModel → 生成自省快照
     - PersonaGrowthTracker → 检查里程碑 / 交互驱动成长
     - LongTermDistiller → 记忆蒸馏（可选）
     - EvolutionDesireEngine → 生成主动成长意愿
  3. 生成 growth_awareness 字段，注入下一轮对话的 self_reflection_pulse
  4. 写入进化周期审计日志

设计原则：
  - 所有子模块调用均降级安全（异常不崩溃主流程）
  - 进化结果持久化到 PersonaEngine 审计链
  - growth_awareness 作为"进化意识"注入 prompt，让她感知自己在成长
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

_logger = logging.getLogger("Natsume.evolution_orchestrator")

_EMPTY_AWARENESS_TEMPLATE_DATA: dict[str, Any] = {
    "recent_change_templates": {},
    "recent_change_snapshot_prefix": "",
    "recent_change_fallbacks": [],
    "stagnation_felt_pool": {},
    "regression_felt_pool": {},
    "generic_regression_text": "",
    "sustained_regression_pool": [],
    "trait_felt_variants": {},
}


def _normalize_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        normalized = [str(item).strip() for item in raw if str(item).strip()]
        if normalized:
            return normalized
    return list(fallback)


def _format_template_text(template: str, **kwargs: Any) -> str:
    try:
        return str(template or "").format(**kwargs)
    except Exception:
        return str(template or "")


def _load_awareness_template_data() -> dict[str, Any]:
    try:
        raw = resolve_prompt_template_json_merged("evolution_orchestrator.awareness_templates")
    except Exception:
        return dict(_EMPTY_AWARENESS_TEMPLATE_DATA)
    return dict(raw) if isinstance(raw, Mapping) else dict(_EMPTY_AWARENESS_TEMPLATE_DATA)


# ---------------------------------------------------------------------------
# EvolutionCycleResult — 进化周期结果
# ---------------------------------------------------------------------------

@dataclass
class EvolutionCycleResult:
    """一次进化周期的完整结果。"""
    cycle_id: str
    date: str                                    # YYYY-MM-DD
    ts_ms: int

    # 各阶段结果
    self_reflection_snapshot: dict[str, Any] = field(default_factory=dict)
    growth_results: list[dict[str, Any]] = field(default_factory=list)
    distillation_result: dict[str, Any] | None = None
    desire_text: str = ""                        # 进化意愿文本

    # 最终输出：注入下一轮对话的 growth_awareness
    growth_awareness: dict[str, Any] = field(default_factory=dict)
    growth_summary: dict[str, Any] = field(default_factory=dict)

    # 统计
    traits_changed: bool = False
    traits_regressed: bool = False             # 是否检测到特质回退
    regression_details: list[dict[str, Any]] = field(default_factory=list)  # 回退详情
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "date": self.date,
            "ts_ms": self.ts_ms,
            "growth_awareness": self.growth_awareness,
            "growth_summary": self.growth_summary,
            "traits_changed": self.traits_changed,
            "desire_text": self.desire_text,
            "growth_results_count": len(self.growth_results),
            "traits_regressed": self.traits_regressed,
            "regression_details": list(self.regression_details),
            "distillation_done": self.distillation_result is not None,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# EvolutionOrchestrator — 进化编排器核心
# ---------------------------------------------------------------------------

class EvolutionOrchestrator:
    """自我进化编排器——每日进化周期的统一调度入口。

    用法（由 NatsumeScheduler 调用）：
        orchestrator = EvolutionOrchestrator()
        result = orchestrator.run_evolution_cycle(
            user_id="user_1",
            date="2026-04-02",
            timeline_summary=...,
            timeline_diary=...,
            bond_state=...,
            bond_summary=...,
            interaction_stats=...,
        )
        # result.growth_awareness 注入下一轮对话的 self_reflection_pulse
    """

    def __init__(
        self,
        *,
        persona_engine: Any = None,
        growth_tracker: Any = None,
        desire_engine: Any = None,
        ledger: Any = None,
    ) -> None:
        """初始化进化编排器。

        Args:
            persona_engine: PersonaEngine 实例（延迟导入）
            growth_tracker: PersonaGrowthTracker 实例（延迟导入）
            desire_engine: EvolutionDesireEngine 实例（延迟导入）
            ledger: SQLiteLedger 实例（可选，用于审计）
        """
        self._persona_engine = persona_engine
        self._growth_tracker = growth_tracker
        self._desire_engine = desire_engine
        self._ledger = ledger

        # 缓存最近一次 growth_awareness（供下一轮对话注入）
        self._last_growth_awareness: dict[str, Any] = {}
        self._growth_cycle_log: dict[str, list[dict[str, Any]]] = {}
        self._rng = random.Random()
        # 成长停滞感知：记录连续无特质变化的进化周期数
        self._stagnant_cycles: int = 0
        # 回退感知：记录连续出现特质回退的进化周期数
        self._regression_cycles: int = 0
        # 上一次特质快照（用于跨周期回退检测）
        self._prev_traits_snapshot: dict[str, float] = {}

    # ── 主入口 ────────────────────────────────────────────────────────────

    def run_evolution_cycle(
        self,
        *,
        user_id: str = "user_1",
        date: str = "",
        timeline_summary: dict[str, Any] | None = None,
        timeline_diary: dict[str, Any] | None = None,
        bond_state: Any | None = None,
        bond_summary: dict[str, Any] | None = None,
        interaction_stats: dict[str, int] | None = None,
        persona_changes: list[Any] | None = None,
        ts_ms: int = 0,
    ) -> EvolutionCycleResult:
        """执行一次完整的进化周期。

        进化步骤：
          Step 1: 自省快照（SelfModel）
          Step 2: 里程碑成长检查（PersonaGrowthTracker）
          Step 3: 交互驱动成长（PersonaGrowthTracker.on_interaction_cycle）
          Step 4: 生成进化意愿（EvolutionDesireEngine）
          Step 5: 构建 growth_awareness（进化意识）
          Step 6: 写入审计日志

        Args:
            user_id: 用户 ID
            date: 日期字符串 YYYY-MM-DD
            timeline_summary: EmotionTimeline.rebuild_summary() 输出
            timeline_diary: EmotionTimeline.build_daily_diary() 输出
            bond_state: BondScoreState
            bond_summary: BondScoreState.to_summary() 输出
            interaction_stats: 交互类型→次数统计
            persona_changes: 特质变化记录列表
            ts_ms: 时间戳

        Returns:
            EvolutionCycleResult
        """
        import uuid
        import datetime

        ts = ts_ms or _now_ms()
        cycle_id = f"evo_{uuid.uuid4().hex[:12]}"
        date_str = date or datetime.date.today().isoformat()

        _logger.info("进化周期开始: cycle_id=%s user=%s date=%s", cycle_id, user_id, date_str)

        result = EvolutionCycleResult(
            cycle_id=cycle_id,
            date=date_str,
            ts_ms=ts,
        )

        # ── Step 1: 自省快照 ─────────────────────────────────────────────
        snapshot = self._step_self_reflection(
            user_id=user_id,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state=bond_state,
            bond_summary=bond_summary,
            persona_changes=persona_changes,
            ts_ms=ts,
            result=result,
        )

        # ── Step 2: 里程碑成长检查 ───────────────────────────────────────
        self._step_milestone_growth(
            user_id=user_id,
            bond_state=bond_state,
            ts_ms=ts,
            result=result,
        )

        # ── Step 3: 交互驱动成长 ─────────────────────────────────────────
        self._step_interaction_growth(
            user_id=user_id,
            interaction_stats=interaction_stats or {},
            ts_ms=ts,
            result=result,
        )

        # ── Step 4: 生成进化意愿 ─────────────────────────────────────────
        desire_text = self._step_generate_desire(
            user_id=user_id,
            snapshot=snapshot,
            bond_state=bond_state,
            result=result,
        )

        # ── Step 4.5: 回退检测 ────────────────────────────────────────
        self._detect_regression(
            user_id=user_id,
            growth_results=result.growth_results,
            result=result,
        )

        # ── Step 5: 构建 growth_awareness ────────────────────────────────
        # 更新停滞计数
        if result.traits_changed:
            self._stagnant_cycles = 0
        else:
            self._stagnant_cycles += 1

        # 更新回退计数
        if result.traits_regressed:
            self._regression_cycles += 1
        else:
            self._regression_cycles = 0

        growth_awareness = self._build_growth_awareness(
            snapshot=snapshot,
            desire_text=desire_text,
            growth_results=result.growth_results,
            bond_state=bond_state,
            stagnant_cycles=self._stagnant_cycles,
            regression_details=result.regression_details,
            regression_cycles=self._regression_cycles,
        )
        result.growth_awareness = growth_awareness
        result.desire_text = desire_text

        self._append_growth_cycle_log(
            user_id=user_id,
            result=result,
        )
        result.growth_summary = self.get_growth_summary(user_id, now_ts_ms=ts)

        # 缓存供下一轮对话使用
        self._last_growth_awareness = growth_awareness

        # ── Step 6: 审计日志 ─────────────────────────────────────────────
        self._log_evolution_cycle(result)

        _logger.info(
            "进化周期完成: cycle_id=%s traits_changed=%s desire='%s'",
            cycle_id, result.traits_changed, desire_text[:60] if desire_text else "",
        )
        return result

    # ── Step 实现 ─────────────────────────────────────────────────────────

    def _step_self_reflection(
        self,
        *,
        user_id: str,
        timeline_summary: dict[str, Any] | None,
        timeline_diary: dict[str, Any] | None,
        bond_state: Any | None,
        bond_summary: dict[str, Any] | None,
        persona_changes: list[Any] | None,
        ts_ms: int,
        result: EvolutionCycleResult,
    ) -> dict[str, Any]:
        """Step 1: 生成自省快照。"""
        try:
            from host_brain.persona.self_model import SelfModel  # noqa: PLC0415
            model = SelfModel()
            snapshot = model.build_snapshot(
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
                persona_changes=persona_changes or self._get_persona_changes(user_id),
                current_ts_ms=ts_ms,
            )
            result.self_reflection_snapshot = snapshot
            _logger.debug("自省快照生成完成: mood=%s bond=%s",
                          snapshot.get("recent_mood_trend"),
                          snapshot.get("bond_growth_perception"))
            return snapshot
        except Exception as exc:
            _logger.warning("自省快照生成失败（降级）: %s", exc)
            result.errors.append(f"self_reflection: {exc}")
            return {}

    def _step_milestone_growth(
        self,
        *,
        user_id: str,
        bond_state: Any | None,
        ts_ms: int,
        result: EvolutionCycleResult,
    ) -> None:
        """Step 2: 检查里程碑成长触发。"""
        try:
            tracker = self._get_growth_tracker()
            if tracker is None:
                return

            # 从 bond_state 推断可能触发的里程碑
            milestones_to_check = self._infer_milestones(bond_state)
            for milestone_key in milestones_to_check:
                try:
                    growth_result = tracker.on_milestone(
                        user_id=user_id,
                        milestone_key=milestone_key,
                        ts_ms=ts_ms,
                    )
                    if growth_result.applied:
                        result.traits_changed = True
                        result.growth_results.append({
                            "type": "milestone",
                            "key": milestone_key,
                            **growth_result.as_dict(),
                        })
                        _logger.info("里程碑成长触发: %s", milestone_key)
                except Exception as exc:
                    _logger.warning("里程碑检查失败 %s: %s", milestone_key, exc)
        except Exception as exc:
            _logger.warning("里程碑成长步骤失败（降级）: %s", exc)
            result.errors.append(f"milestone_growth: {exc}")

    def _step_interaction_growth(
        self,
        *,
        user_id: str,
        interaction_stats: dict[str, int],
        ts_ms: int,
        result: EvolutionCycleResult,
    ) -> None:
        """Step 3: 交互驱动成长。"""
        if not interaction_stats or sum(interaction_stats.values()) < 5:
            return
        try:
            tracker = self._get_growth_tracker()
            if tracker is None:
                return
            growth_result = tracker.on_interaction_cycle(
                user_id=user_id,
                interaction_stats=interaction_stats,
                ts_ms=ts_ms,
            )
            if growth_result.applied:
                result.traits_changed = True
                result.growth_results.append({
                    "type": "interaction_cycle",
                    **growth_result.as_dict(),
                })
                _logger.info("交互驱动成长触发: %s", growth_result.reason[:80])
        except Exception as exc:
            _logger.warning("交互驱动成长失败（降级）: %s", exc)
            result.errors.append(f"interaction_growth: {exc}")

    def _step_generate_desire(
        self,
        *,
        user_id: str,
        snapshot: dict[str, Any],
        bond_state: Any | None,
        result: EvolutionCycleResult,
        llm_fn: Any | None = None,
    ) -> str:
        """Step 4: 生成进化意愿文本。"""
        try:
            engine = self._get_desire_engine()
            if engine is None:
                return ""
            persona_engine = self._get_persona_engine()
            traits = persona_engine.get_traits(user_id) if persona_engine else None
            bond_stage = ""
            if bond_state is not None:
                bond_stage = str(getattr(bond_state, "current_stage", "") or "")
            emotion_trend = str(snapshot.get("recent_mood_trend") or "平稳")
            desire = engine.generate_desire(
                traits=traits,
                emotion_trend=emotion_trend,
                bond_stage=bond_stage,
                llm_fn=llm_fn,
            )
            return desire or ""
        except Exception as exc:
            _logger.warning("进化意愿生成失败（降级）: %s", exc)
            result.errors.append(f"desire_engine: {exc}")
            return ""

    # ── growth_awareness 构建 ─────────────────────────────────────────────

    def _build_growth_awareness(
        self,
        *,
        snapshot: dict[str, Any],
        desire_text: str,
        growth_results: list[dict[str, Any]],
        bond_state: Any | None,
        stagnant_cycles: int = 0,
        regression_details: list[dict[str, Any]] | None = None,
        regression_cycles: int = 0,
    ) -> dict[str, Any]:
        """构建 growth_awareness 字典——进化意识注入 prompt 的核心字段.

        Returns:
            {
                "recent_change": "这周和 master 聊了很多深入的话题...",
                "trait_felt": "好像比以前更能理解 master 的心情了",
                "milestone_felt": "认识 master 已经 100 天了",
                "desire": "想要更了解 master 喜欢什么",
                "stagnation_felt": "感觉自己好像停滞了一段时间...",
                "regression_felt": "好像某方面不如以前了..."  # 回退时注入
            }
        """
        awareness: dict[str, Any] = {}

        # recent_change：基于成长结果生成变化感知
        bond_stage = str(getattr(bond_state, "current_stage", "") or "") if bond_state else ""
        recent_change = self._build_recent_change_text(growth_results, snapshot, bond_stage=bond_stage)
        if recent_change:
            awareness["recent_change"] = recent_change

        # trait_felt：基于特质变化生成第一人称感知
        trait_felt = self._build_trait_felt_text(growth_results)
        if trait_felt:
            awareness["trait_felt"] = trait_felt

        # milestone_felt：基于里程碑生成感知
        milestones = list(snapshot.get("growth_milestones") or [])
        if milestones:
            awareness["milestone_felt"] = milestones[0]

        # stagnation_felt：连续 3 个周期无成长时，注入停滞感知
        if stagnant_cycles >= 3:
            stagnation_text = self._build_stagnation_felt_text(stagnant_cycles)
            if stagnation_text:
                awareness["stagnation_felt"] = stagnation_text

        # regression_felt：检测到特质回退时，注入回退感知
        if regression_details:
            regression_text = self._build_regression_felt_text(
                regression_details=regression_details,
                regression_cycles=regression_cycles,
            )
            if regression_text:
                awareness["regression_felt"] = regression_text

        # desire：进化意愿
        if desire_text:
            awareness["desire"] = desire_text

        return awareness

    def _build_recent_change_text(
        self,
        growth_results: list[dict[str, Any]],
        snapshot: dict[str, Any],
        bond_stage: str = "",
    ) -> str:
        """生成近期变化感知文本。

        多变体 + 幅度感知（delta < 0.02 → 稍微，>= 0.02 → 明显）。
        bond_stage 影响描述深度（浅层关系用更克制的表达）。
        """
        template_data = _load_awareness_template_data()
        if not growth_results:
            # 即使没有特质变化，也可以基于快照生成感知
            changes = list(snapshot.get("personality_changes") or [])
            snapshot_prefix = str(template_data.get("recent_change_snapshot_prefix") or "").strip()
            if changes and snapshot_prefix:
                return _format_template_text(snapshot_prefix, change=changes[0])
            return ""

        # 有成长结果时，生成更具体的描述
        applied = [r for r in growth_results if r.get("applied")]
        if not applied:
            return ""
        recent_change_mapping = (
            template_data.get("recent_change_templates")
            if isinstance(template_data.get("recent_change_templates"), Mapping)
            else {}
        )

        # 找出变化最大的特质及其 delta
        max_trait = ""
        max_delta = 0.0
        for r in applied:
            delta = r.get("delta_applied") or {}
            for trait, val in delta.items():
                if abs(float(val)) > max_delta:
                    max_delta = abs(float(val))
                    max_trait = trait

        if max_trait and isinstance(recent_change_mapping, Mapping) and max_trait in recent_change_mapping:
            # 幅度感知：delta < 0.02 → slight，>= 0.02 → notable
            level = "notable" if max_delta >= 0.02 else "slight"
            variants_mapping = recent_change_mapping.get(max_trait)
            variants = _normalize_text_list(
                variants_mapping.get(level) if isinstance(variants_mapping, Mapping) else None,
                [],
            )
            if not variants:
                variants = _normalize_text_list(
                    variants_mapping.get("slight") if isinstance(variants_mapping, Mapping) else None,
                    [],
                )
            if variants:
                return self._rng.choice(variants)

        fallback_variants = _normalize_text_list(
            template_data.get("recent_change_fallbacks"),
            [],
        )
        return self._rng.choice(fallback_variants) if fallback_variants else ""

    # 停滞感知文本池：按停滞程度分档
    _STAGNATION_FELT_POOL: dict[str, list[str]] = {}

    # ── 回退感知 ─────────────────────────────────────────────────────────

    def _detect_regression(
        self,
        *,
        user_id: str,
        growth_results: list[dict[str, Any]],
        result: EvolutionCycleResult,
    ) -> None:
        """检测特质回退.

        回退来源有两种：
        1. 本周期 growth_results 中 delta 为负值的特质
        2. 与上一次特质快照对比，发现某特质下降
        """
        regression_details: list[dict[str, Any]] = []

        # 来源1：从 growth_results 中检测负向 delta
        for r in growth_results:
            if not r.get("applied"):
                continue
            delta = r.get("delta_applied") or {}
            for trait, val in delta.items():
                fval = float(val)
                if fval < -0.005:  # 阈值：忽略极微小的波动
                    regression_details.append({
                        "trait": trait,
                        "delta": round(fval, 4),
                        "source": "growth_result",
                    })

        # 来源2：与上一次特质快照对比
        try:
            engine = self._get_persona_engine()
            if engine is not None:
                current_traits = engine.get_traits(user_id)
                if current_traits is not None and self._prev_traits_snapshot:
                    trait_names = ["warmth", "curiosity", "humor", "sensitivity",
                                   "assertiveness", "playfulness", "resilience"]
                    for tn in trait_names:
                        cur_val = float(getattr(current_traits, tn, 0.5))
                        prev_val = self._prev_traits_snapshot.get(tn, cur_val)
                        diff = cur_val - prev_val
                        if diff < -0.005:
                            # 避免与来源1重复
                            already_found = any(
                                d["trait"] == tn for d in regression_details
                            )
                            if not already_found:
                                regression_details.append({
                                    "trait": tn,
                                    "delta": round(diff, 4),
                                    "source": "snapshot_compare",
                                })
                # 更新快照
                if current_traits is not None:
                    self._prev_traits_snapshot = {
                        tn: float(getattr(current_traits, tn, 0.5))
                        for tn in ["warmth", "curiosity", "humor", "sensitivity",
                                   "assertiveness", "playfulness", "resilience"]
                    }
        except Exception as exc:
            _logger.debug("回退检测快照对比失败（非致命）: %s", exc)

        if regression_details:
            result.traits_regressed = True
            result.regression_details = regression_details
            _logger.info(
                "检测到特质回退: %s",
                ", ".join(f"{d['trait']}({d['delta']:+.4f})" for d in regression_details),
            )

    # 回退感知文本池：按回退特质 × 幅度分档
    _REGRESSION_FELT_POOL: dict[str, dict[str, list[str]]] = {}

    # 持续回退时的额外感知
    _SUSTAINED_REGRESSION_POOL: list[str] = []

    def _build_regression_felt_text(
        self,
        *,
        regression_details: list[dict[str, Any]],
        regression_cycles: int = 0,
    ) -> str:
        """生成特质回退感知文本.

        Args:
            regression_details: 回退详情列表
            regression_cycles: 连续回退的进化周期数

        Returns:
            回退感知文本（第一人称），或空字符串
        """
        if not regression_details:
            return ""
        template_data = _load_awareness_template_data()
        regression_mapping = (
            template_data.get("regression_felt_pool")
            if isinstance(template_data.get("regression_felt_pool"), Mapping)
            else self._REGRESSION_FELT_POOL
        )

        # 找出回退幅度最大的特质
        max_trait = ""
        max_delta = 0.0
        for d in regression_details:
            abs_delta = abs(float(d.get("delta", 0)))
            if abs_delta > max_delta:
                max_delta = abs_delta
                max_trait = d.get("trait", "")

        text = ""
        if max_trait and isinstance(regression_mapping, Mapping) and max_trait in regression_mapping:
            level = "notable" if max_delta >= 0.02 else "slight"
            level_mapping = regression_mapping.get(max_trait)
            pool = _normalize_text_list(
                level_mapping.get(level) if isinstance(level_mapping, Mapping) else None,
                [],
            )
            if not pool:
                pool = _normalize_text_list(
                    level_mapping.get("slight") if isinstance(level_mapping, Mapping) else None,
                    [],
                )
            if pool:
                text = self._rng.choice(pool)

        if not text:
            text = str(template_data.get("generic_regression_text") or "").strip()

        # 持续回退时追加额外感知
        if regression_cycles >= 2:
            sustained_pool = _normalize_text_list(
                template_data.get("sustained_regression_pool"),
                self._SUSTAINED_REGRESSION_POOL,
            )
            if text and sustained_pool:
                sustained = self._rng.choice(sustained_pool)
                text = f"{text}...{sustained}"

        _logger.info(
            "回退感知触发: trait=%s delta=%.4f cycles=%d text='%s'",
            max_trait, max_delta, regression_cycles, text[:60],
        )
        return text

    def _build_stagnation_felt_text(self, stagnant_cycles: int) -> str:
        """生成成长停滞感知文本。

        Args:
            stagnant_cycles: 连续无成长的进化周期数

        Returns:
            停滞感知文本（第一人称），或空字符串
        """
        template_data = _load_awareness_template_data()
        stagnation_mapping = (
            template_data.get("stagnation_felt_pool")
            if isinstance(template_data.get("stagnation_felt_pool"), Mapping)
            else self._STAGNATION_FELT_POOL
        )
        if stagnant_cycles < 3:
            return ""
        if stagnant_cycles <= 4:
            pool = _normalize_text_list(
                stagnation_mapping.get("mild") if isinstance(stagnation_mapping, Mapping) else None,
                [],
            )
        elif stagnant_cycles <= 6:
            pool = _normalize_text_list(
                stagnation_mapping.get("moderate") if isinstance(stagnation_mapping, Mapping) else None,
                [],
            )
        else:
            pool = _normalize_text_list(
                stagnation_mapping.get("deep") if isinstance(stagnation_mapping, Mapping) else None,
                [],
            )
        if not pool:
            return ""
        text = self._rng.choice(pool)
        _logger.info("成长停滞感知触发: stagnant_cycles=%d text='%s'", stagnant_cycles, text[:40])
        return text

    def _build_trait_felt_text(self, growth_results: list[dict[str, Any]]) -> str:
        """生成特质变化的第一人称感知文本（多变体，按幅度选择深度）。"""
        applied = [r for r in growth_results if r.get("applied")]
        if not applied:
            return ""
        template_data = _load_awareness_template_data()
        trait_felt_mapping = (
            template_data.get("trait_felt_variants")
            if isinstance(template_data.get("trait_felt_variants"), Mapping)
            else {}
        )

        # 找出变化最大的特质
        max_trait = ""
        max_delta = 0.0
        for r in applied:
            delta = r.get("delta_applied") or {}
            for trait, val in delta.items():
                if abs(float(val)) > max_delta and abs(float(val)) >= 0.01:
                    max_delta = abs(float(val))
                    max_trait = trait

        if max_trait and isinstance(trait_felt_mapping, Mapping) and max_trait in trait_felt_mapping:
            pool = _normalize_text_list(
                trait_felt_mapping.get(max_trait),
                [],
            )
            if pool:
                return self._rng.choice(pool)
        return ""

    def _append_growth_cycle_log(self, *, user_id: str, result: EvolutionCycleResult) -> None:
        """记录最近的成长周期结果，供后续摘要复用。"""
        uid = str(user_id or "").strip()
        if not uid:
            return
        awareness = dict(result.growth_awareness or {})
        entry = {
            "cycle_id": result.cycle_id,
            "date": result.date,
            "ts_ms": int(result.ts_ms or 0),
            "growth_awareness": awareness,
            "traits_changed": bool(result.traits_changed),
            "traits_regressed": bool(result.traits_regressed),
            "desire_text": str(result.desire_text or "").strip(),
        }
        history = self._growth_cycle_log.setdefault(uid, [])
        history.append(entry)
        if len(history) > 21:
            self._growth_cycle_log[uid] = history[-21:]

    @staticmethod
    def _pick_growth_focus(awareness: dict[str, Any]) -> str:
        for key in ("recent_change", "trait_felt", "milestone_felt", "stagnation_felt", "regression_felt", "desire"):
            text = str(awareness.get(key) or "").strip()
            if text:
                return text
        return ""

    def _build_weekly_growth_summary(self, entries: list[dict[str, Any]]) -> str:
        snippets: list[str] = []
        seen: set[str] = set()
        for entry in reversed(entries):
            awareness = dict(entry.get("growth_awareness") or {})
            focus = self._pick_growth_focus(awareness)
            if focus and focus not in seen:
                snippets.append(focus)
                seen.add(focus)
            if len(snippets) >= 2:
                break
        if not snippets:
            return ""
        if len(snippets) == 1:
            return snippets[0]
        return "；".join(snippets)

    def _collect_active_growth_themes(self, entries: list[dict[str, Any]]) -> list[str]:
        ordered_keys = ("recent_change", "trait_felt", "milestone_felt", "stagnation_felt", "regression_felt", "desire")
        themes: list[str] = []
        for key in ordered_keys:
            if any(str(dict(entry.get("growth_awareness") or {}).get(key) or "").strip() for entry in entries):
                themes.append(key)
        return themes

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    def _infer_milestones(self, bond_state: Any | None) -> list[str]:
        """从 bond_state 推断可能触发的里程碑键。"""
        milestones: list[str] = []
        if bond_state is None:
            return milestones
        event_count = int(getattr(bond_state, "event_count", 0) or 0)
        if event_count >= 50:
            milestones.append("deep_talk_50")
        if event_count >= 20:
            milestones.append("emotional_support_20")
        return milestones

    def _get_persona_changes(self, user_id: str) -> list[Any]:
        """从 PersonaEngine 获取最近的特质变化记录。"""
        try:
            engine = self._get_persona_engine()
            if engine is None:
                return []
            return engine.get_change_log(user_id, limit=5)
        except Exception:
            return []

    def _get_persona_engine(self) -> Any:
        """延迟获取 PersonaEngine。"""
        if self._persona_engine is not None:
            return self._persona_engine
        try:
            from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
            self._persona_engine = PersonaEngine.get_instance()
            return self._persona_engine
        except Exception as exc:
            _logger.warning("PersonaEngine 不可用: %s", exc)
            return None

    def _get_growth_tracker(self) -> Any:
        """延迟获取 PersonaGrowthTracker。"""
        if self._growth_tracker is not None:
            return self._growth_tracker
        try:
            from host_brain.persona.persona_growth import PersonaGrowthTracker  # noqa: PLC0415
            self._growth_tracker = PersonaGrowthTracker()
            return self._growth_tracker
        except Exception as exc:
            _logger.warning("PersonaGrowthTracker 不可用: %s", exc)
            return None

    def _get_desire_engine(self) -> Any:
        """延迟获取 EvolutionDesireEngine。"""
        if self._desire_engine is not None:
            return self._desire_engine
        try:
            from host_brain.persona.evolution_desire_engine import EvolutionDesireEngine  # noqa: PLC0415
            self._desire_engine = EvolutionDesireEngine()
            return self._desire_engine
        except Exception as exc:
            _logger.warning("EvolutionDesireEngine 不可用: %s", exc)
            return None

    def _log_evolution_cycle(self, result: EvolutionCycleResult) -> None:
        """写入进化周期审计日志。"""
        if self._ledger is None:
            return
        try:
            self._ledger.log_system_event(
                event_type="evolution_cycle",
                payload=result.to_dict(),
            )
        except Exception as exc:
            _logger.warning("进化周期审计日志写入失败: %s", exc)

    # ── 对外查询接口 ──────────────────────────────────────────────────────

    def get_last_growth_awareness(self) -> dict[str, Any]:
        """获取最近一次进化周期生成的 growth_awareness。

        供 SelfReflection 在下一轮对话中注入 prompt。
        """
        return dict(self._last_growth_awareness)

    def get_growth_summary(self, user_id: str, *, now_ts_ms: int = 0) -> dict[str, Any]:
        """获取最近日级/周级成长摘要。"""
        uid = str(user_id or "").strip()
        history = list(self._growth_cycle_log.get(uid, []))
        if not history:
            return {}

        effective_now = int(now_ts_ms or history[-1].get("ts_ms") or _now_ms())
        week_start_ms = effective_now - 7 * 24 * 3600 * 1000
        weekly_entries = [entry for entry in history if int(entry.get("ts_ms") or 0) >= week_start_ms]
        if not weekly_entries:
            weekly_entries = [history[-1]]
        latest = weekly_entries[-1]
        latest_awareness = dict(latest.get("growth_awareness") or {})
        daily_summary = self._pick_growth_focus(latest_awareness)
        weekly_summary = self._build_weekly_growth_summary(weekly_entries)
        if weekly_summary and weekly_summary == daily_summary and len(weekly_entries) <= 1:
            weekly_summary = ""

        summary: dict[str, Any] = {
            "last_cycle_date": str(latest.get("date") or "").strip(),
            "recent_cycle_count": len(weekly_entries),
            "active_themes": self._collect_active_growth_themes(weekly_entries),
        }
        if daily_summary:
            summary["daily_summary"] = daily_summary
        if weekly_summary:
            summary["weekly_summary"] = weekly_summary
        return summary

    def set_growth_awareness(self, awareness: dict[str, Any]) -> None:
        """手动设置 growth_awareness（用于测试或外部注入）。"""
        self._last_growth_awareness = dict(awareness)


def _now_ms() -> int:
    return int(time.time() * 1000)
