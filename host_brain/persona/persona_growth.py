"""PersonaGrowthTracker：人格成长追踪器——Phase IV。

职责：
  1. Bond 阶段晋级时触发特质微调
  2. 里程碑事件触发特质微调
  3. 通过 TraitDriftGuard 进行安全校验
  4. 通过 PersonaEngine.apply_growth_delta 应用变化
  5. 记录完整的成长审计链

设计原则：
  - 不直接修改特质，而是生成 TraitsDelta 后经过安全校验再应用
  - 所有成长规则在配置中定义，支持扩展
  - 每次变化可追溯、可回滚
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.personality_traits import (
    TRAIT_NAMES,
    PersonalityTraits,
    TraitsDelta,
)
from host_brain.persona.trait_drift_guard import (
    TraitDriftGuard,
    GuardCheckResult,
)

_logger = logging.getLogger("Natsume.persona_growth")


# ---------------------------------------------------------------------------
# 成长规则配置
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GrowthRule:
    """单条成长规则定义。"""
    trigger: str           # 触发条件标识
    delta: dict[str, float]  # 各维度变化量
    description: str       # 规则描述（审计用）
    one_time: bool = True  # 是否仅首次触发


# Bond 阶段晋级成长规则
STAGE_PROMOTION_RULES: dict[str, GrowthRule] = {
    # ACQUAINTANCE → FRIEND
    "ACQUAINTANCE→FRIEND": GrowthRule(
        trigger="bond_stage_promoted:ACQUAINTANCE→FRIEND",
        delta={"playfulness": 0.05},
        description="首次成为朋友：俏皮度 +0.05",
    ),
    # FRIEND → CLOSE_FRIEND
    "FRIEND→CLOSE_FRIEND": GrowthRule(
        trigger="bond_stage_promoted:FRIEND→CLOSE_FRIEND",
        delta={"warmth": 0.03, "sensitivity": 0.02},
        description="成为好友：温柔度 +0.03，敏感度 +0.02",
    ),
    # CLOSE_FRIEND → CONFIDANT
    "CLOSE_FRIEND→CONFIDANT": GrowthRule(
        trigger="bond_stage_promoted:CLOSE_FRIEND→CONFIDANT",
        delta={"humor": 0.05, "assertiveness": 0.03},
        description="成为挚友：幽默感 +0.05，自信度 +0.03",
    ),
    # CONFIDANT → SOULMATE
    "CONFIDANT→SOULMATE": GrowthRule(
        trigger="bond_stage_promoted:CONFIDANT→SOULMATE",
        delta={"resilience": 0.05, "curiosity": 0.03},
        description="成为灵魂伴侣：韧性 +0.05，好奇心 +0.03",
    ),
}

# 里程碑成长规则
MILESTONE_RULES: dict[str, GrowthRule] = {
    "deep_talk_50": GrowthRule(
        trigger="milestone:deep_talk_count>=50",
        delta={"curiosity": 0.03},
        description="深度对话累计超过 50 次：好奇心 +0.03",
    ),
    "emotional_support_20": GrowthRule(
        trigger="milestone:emotional_support_count>=20",
        delta={"resilience": 0.05},
        description="情感支持累计超过 20 次：韧性 +0.05",
    ),
}


# ---------------------------------------------------------------------------
# GrowthApplyResult — 成长应用结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GrowthApplyResult:
    """成长应用结果。"""
    applied: bool                  # 是否成功应用
    rule_trigger: str             # 触发规则标识
    delta_applied: dict[str, float] | None = None  # 实际应用的变化量
    guard_result: dict[str, Any] | None = None      # 安全校验结果
    reason: str = ""              # 结果说明

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "applied": self.applied,
            "rule_trigger": self.rule_trigger,
            "reason": self.reason,
        }
        if self.delta_applied:
            result["delta_applied"] = self.delta_applied
        if self.guard_result:
            result["guard_result"] = self.guard_result
        return result


# ---------------------------------------------------------------------------
# PersonaGrowthTracker — 人格成长追踪器
# ---------------------------------------------------------------------------

class PersonaGrowthTracker:
    """人格成长追踪器。

    用法：
        tracker = PersonaGrowthTracker(engine=persona_engine)
        result = tracker.on_stage_promoted(
            user_id="user_1",
            from_stage="ACQUAINTANCE",
            to_stage="FRIEND",
        )
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        guard: TraitDriftGuard | None = None,
        stage_rules: dict[str, GrowthRule] | None = None,
        milestone_rules: dict[str, GrowthRule] | None = None,
        canon_store: Any | None = None,
    ) -> None:
        """初始化成长追踪器。

        Args:
            engine: PersonaEngine 实例（延迟导入以避免循环依赖）
            guard: TraitDriftGuard 实例（默认创建新实例）
            stage_rules: 阶段晋级规则（显式传入时覆盖 Canon 和内置默认值）
            milestone_rules: 里程碑规则（显式传入时覆盖 Canon 和内置默认值）
            canon_store: WorldCanonStore 实例（可选，用于从 Canon 热加载规则）
        """
        self._engine = engine
        self._guard = guard or TraitDriftGuard()
        self._canon_store = canon_store
        # 规则加载优先级：显式参数 > Canon 文档 > 硬编码默认值
        canon_stage, canon_milestone = self._load_rules_from_canon()
        self._stage_rules = dict(stage_rules or canon_stage or STAGE_PROMOTION_RULES)
        self._milestone_rules = dict(milestone_rules or canon_milestone or MILESTONE_RULES)
        # user_id → 已触发的 one_time 规则集合
        self._triggered_rules: dict[str, set[str]] = {}
        # user_id → 本周累计变化量
        self._weekly_cumulative: dict[str, dict[str, float]] = {}
        # user_id → 变化历史（delta.as_dict() 列表）
        self._change_history: dict[str, list[dict[str, float]]] = {}

    def _load_rules_from_canon(
        self,
    ) -> tuple[dict[str, GrowthRule] | None, dict[str, GrowthRule] | None]:
        """从 Canon wc-004-persona-growth-rules.json 热加载规则。

        Returns:
            (stage_rules, milestone_rules)，任一为 None 表示 Canon 不可用/无数据。
        """
        if self._canon_store is None:
            return None, None
        try:
            doc = self._canon_store.get("wc-004-persona-growth-rules")
            if doc is None:
                _logger.debug("Canon wc-004 不存在，降级到硬编码规则")
                return None, None

            # Canon 文档可能是 WorldCanonDocument 对象或 dict
            raw: dict[str, Any] = {}
            if hasattr(doc, "raw_json"):
                raw = doc.raw_json or {}
            elif hasattr(doc, "body"):
                # body 可能是 JSON 字符串，需要解析
                import json as _json
                body = getattr(doc, "body", "")
                if isinstance(body, str):
                    try:
                        raw = _json.loads(body)
                    except (ValueError, TypeError):
                        raw = {}
                elif isinstance(body, dict):
                    raw = body
            # 有些实现直接在 doc 上有这些字段
            if not raw and hasattr(doc, "doc_id"):
                raw = {
                    k: getattr(doc, k)
                    for k in ("stage_promotion_rules", "milestone_rules",
                              "interaction_trait_map", "growth_params")
                    if hasattr(doc, k)
                }

            # 尝试直接从文件读取（最可靠的方式）
            if not raw.get("stage_promotion_rules"):
                try:
                    from pathlib import Path
                    import json as _json
                    canon_path = (
                        Path(__file__).resolve().parent.parent
                        / "canon" / "world" / "wc-004-persona-growth-rules.json"
                    )
                    if canon_path.exists():
                        raw = _json.loads(canon_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            stage_rules = self._parse_rules_section(
                raw.get("stage_promotion_rules", {}),
                trigger_prefix="bond_stage_promoted",
            )
            milestone_rules = self._parse_rules_section(
                raw.get("milestone_rules", {}),
                trigger_prefix="milestone",
            )

            if stage_rules:
                _logger.info(
                    "Canon 热加载成长规则: stage=%d, milestone=%d",
                    len(stage_rules), len(milestone_rules or {}),
                )

            return stage_rules or None, milestone_rules or None

        except Exception as exc:
            _logger.warning("Canon 规则加载失败（降级到硬编码）: %s", exc)
            return None, None

    @staticmethod
    def _parse_rules_section(
        section: dict[str, Any],
        trigger_prefix: str,
    ) -> dict[str, GrowthRule] | None:
        """将 Canon JSON section 解析为 GrowthRule 字典。"""
        if not section or not isinstance(section, dict):
            return None
        rules: dict[str, GrowthRule] = {}
        for key, val in section.items():
            if not isinstance(val, dict):
                continue
            delta = val.get("delta", {})
            if not delta or not isinstance(delta, dict):
                continue
            rules[key] = GrowthRule(
                trigger=f"{trigger_prefix}:{key}",
                delta={k: float(v) for k, v in delta.items() if isinstance(v, (int, float))},
                description=str(val.get("description", key)),
                one_time=bool(val.get("one_time", True)),
            )
        return rules if rules else None

    def _get_engine(self) -> Any:
        """延迟获取 PersonaEngine。"""
        if self._engine is None:
            from host_brain.persona.persona_engine import PersonaEngine
            self._engine = PersonaEngine.get_instance()
        return self._engine

    # ── 阶段晋级触发 ─────────────────────────────────────────────────────

    def on_stage_promoted(
        self,
        user_id: str,
        from_stage: str,
        to_stage: str,
        *,
        ts_ms: int = 0,
    ) -> GrowthApplyResult:
        """Bond 阶段晋级时触发人格成长。

        Args:
            user_id: 用户 ID
            from_stage: 原阶段
            to_stage: 新阶段
            ts_ms: 时间戳

        Returns:
            GrowthApplyResult
        """
        rule_key = f"{from_stage}→{to_stage}"
        rule = self._stage_rules.get(rule_key)

        if rule is None:
            return GrowthApplyResult(
                applied=False,
                rule_trigger=f"bond_stage_promoted:{rule_key}",
                reason=f"无匹配的成长规则: {rule_key}",
            )

        return self._apply_rule(
            user_id=user_id,
            rule=rule,
            rule_key=rule_key,
            ts_ms=ts_ms,
        )

    # ── 里程碑触发 ────────────────────────────────────────────────────────

    def on_milestone(
        self,
        user_id: str,
        milestone_key: str,
        *,
        ts_ms: int = 0,
    ) -> GrowthApplyResult:
        """里程碑事件触发人格成长。

        Args:
            user_id: 用户 ID
            milestone_key: 里程碑键（如 "deep_talk_50"）
            ts_ms: 时间戳

        Returns:
            GrowthApplyResult
        """
        rule = self._milestone_rules.get(milestone_key)
        if rule is None:
            return GrowthApplyResult(
                applied=False,
                rule_trigger=f"milestone:{milestone_key}",
                reason=f"无匹配的里程碑规则: {milestone_key}",
            )

        return self._apply_rule(
            user_id=user_id,
            rule=rule,
            rule_key=milestone_key,
            ts_ms=ts_ms,
        )

    # ── 核心应用逻辑 ─────────────────────────────────────────────────────

    def _apply_rule(
        self,
        *,
        user_id: str,
        rule: GrowthRule,
        rule_key: str,
        ts_ms: int = 0,
    ) -> GrowthApplyResult:
        """应用单条成长规则。"""
        uid = str(user_id).strip()
        trigger = rule.trigger

        # 检查 one_time 规则是否已触发
        if rule.one_time:
            triggered = self._triggered_rules.get(uid, set())
            if rule_key in triggered:
                return GrowthApplyResult(
                    applied=False,
                    rule_trigger=trigger,
                    reason=f"规则已触发过（one_time）: {rule_key}",
                )

        engine = self._get_engine()
        current_traits = engine.get_traits(uid)

        # 构建 delta
        delta = TraitsDelta(**{
            name: rule.delta.get(name, 0.0)
            for name in TRAIT_NAMES
        })

        # 安全校验
        guard_result = self._guard.check(
            delta=delta,
            current_traits=current_traits,
            change_history=self._change_history.get(uid, []),
            weekly_cumulative=self._weekly_cumulative.get(uid, {}),
        )

        if not guard_result.allowed:
            _logger.warning(
                "人格成长被拒绝: user=%s rule=%s violations=%s",
                uid, rule_key, guard_result.violations,
            )
            return GrowthApplyResult(
                applied=False,
                rule_trigger=trigger,
                guard_result=guard_result.as_dict(),
                reason=f"安全校验失败: {'; '.join(guard_result.violations)}",
            )

        # 应用变化
        new_traits = engine.apply_growth_delta(
            uid,
            delta,
            reason=trigger,
            ts_ms=ts_ms or _now_ms(),
        )

        # 更新追踪状态
        if rule.one_time:
            if uid not in self._triggered_rules:
                self._triggered_rules[uid] = set()
            self._triggered_rules[uid].add(rule_key)

        # 更新变化历史
        if uid not in self._change_history:
            self._change_history[uid] = []
        self._change_history[uid].append(delta.as_dict())

        # 更新每周累计
        if uid not in self._weekly_cumulative:
            self._weekly_cumulative[uid] = {n: 0.0 for n in TRAIT_NAMES}
        for name in TRAIT_NAMES:
            self._weekly_cumulative[uid][name] = (
                self._weekly_cumulative[uid].get(name, 0.0)
                + abs(delta.as_dict().get(name, 0.0))
            )

        _logger.info(
            "人格成长应用: user=%s rule=%s delta=%s",
            uid, rule_key, delta.as_dict(),
        )

        # 检查 Evolution 提案
        if guard_result.evolution_proposal:
            _logger.info(
                "Canon Evolution 提案: %s", guard_result.evolution_proposal,
            )

        return GrowthApplyResult(
            applied=True,
            rule_trigger=trigger,
            delta_applied=delta.as_dict(),
            guard_result=guard_result.as_dict(),
            reason=rule.description,
        )

    # ── 交互驱动成长（Phase IX）─────────────────────────────────────────

    def on_interaction_cycle(
        self,
        user_id: str,
        *,
        interaction_stats: dict[str, int],
        lookback_days: int = 7,
        ts_ms: int = 0,
        feedback_collector: Any | None = None,
    ) -> GrowthApplyResult:
        """交互驱动的人格成长——根据实际交互频谱动态计算成长方向。

        Phase IX-C 升级：支持叠加用户反馈修正（feedback_collector）。

        由 NatsumeScheduler 每周调度，也可手动触发。

        Args:
            user_id: 用户 ID
            interaction_stats: 交互类型→次数统计
            lookback_days: 分析周期天数
            ts_ms: 时间戳

        Returns:
            GrowthApplyResult
        """
        uid = str(user_id).strip()
        trigger = f"interaction_cycle:lookback={lookback_days}d"

        if not interaction_stats or sum(interaction_stats.values()) < 5:
            return GrowthApplyResult(
                applied=False,
                rule_trigger=trigger,
                reason="交互数据不足（< 5 次），跳过本周期成长",
            )

        engine = self._get_engine()
        current_traits = engine.get_traits(uid)

        # 调用 InteractionGrowthAnalyzer 计算 delta
        from host_brain.persona.interaction_growth_analyzer import (  # noqa: PLC0415
            InteractionGrowthAnalyzer,
        )
        analyzer = InteractionGrowthAnalyzer()
        analysis = analyzer.analyze(
            interaction_stats=interaction_stats,
            current_traits=current_traits,
            analysis_period_days=lookback_days,
            ts_ms=ts_ms or _now_ms(),
        )

        delta = analysis.delta

        # Phase IX-C：叠加用户反馈修正
        feedback_detail = ""
        if feedback_collector is not None:
            try:
                fb_result = feedback_collector.compute_adjustment(
                    uid, ts_ms=ts_ms or _now_ms(),
                )
                if fb_result.total_signals > 0:
                    # 合并两个 delta：交互驱动 + 反馈修正
                    merged = {}
                    interaction_d = delta.as_dict()
                    feedback_d = fb_result.delta.as_dict()
                    for name in TRAIT_NAMES:
                        merged[name] = interaction_d.get(name, 0.0) + feedback_d.get(name, 0.0)
                    delta = TraitsDelta(**merged)
                    feedback_detail = f", 反馈信号={fb_result.total_signals}"
                    _logger.info(
                        "反馈修正叠加: user=%s feedback_signals=%d merged_delta=%s",
                        uid, fb_result.total_signals, delta.as_dict(),
                    )
                    # 消费后清空反馈缓冲区
                    feedback_collector.clear_buffer(uid)
            except Exception as fb_exc:
                _logger.warning("反馈修正失败（降级跳过）: %s", fb_exc)

        # 如果所有维度 delta 都是 0，跳过
        if all(abs(v) < 0.0001 for v in delta.as_dict().values()):
            return GrowthApplyResult(
                applied=False,
                rule_trigger=trigger,
                reason=f"交互频谱无显著倾向（total={analysis.total_interactions}，dominant={analysis.dominant_interaction}）",
            )

        # 安全校验（复用 TraitDriftGuard）
        guard_result = self._guard.check(
            delta=delta,
            current_traits=current_traits,
            change_history=self._change_history.get(uid, []),
            weekly_cumulative=self._weekly_cumulative.get(uid, {}),
        )

        if not guard_result.allowed:
            _logger.warning(
                "交互驱动成长被拒绝: user=%s violations=%s",
                uid, guard_result.violations,
            )
            return GrowthApplyResult(
                applied=False,
                rule_trigger=trigger,
                guard_result=guard_result.as_dict(),
                reason=f"安全校验失败: {'; '.join(guard_result.violations)}",
            )

        # 应用变化
        engine.apply_growth_delta(
            uid,
            delta,
            reason=trigger,
            ts_ms=ts_ms or _now_ms(),
        )

        # 更新追踪状态
        if uid not in self._change_history:
            self._change_history[uid] = []
        self._change_history[uid].append(delta.as_dict())

        if uid not in self._weekly_cumulative:
            self._weekly_cumulative[uid] = {n: 0.0 for n in TRAIT_NAMES}
        for name in TRAIT_NAMES:
            self._weekly_cumulative[uid][name] = (
                self._weekly_cumulative[uid].get(name, 0.0)
                + abs(delta.as_dict().get(name, 0.0))
            )

        _logger.info(
            "交互驱动成长应用: user=%s total=%d dominant=%s delta=%s",
            uid, analysis.total_interactions,
            analysis.dominant_interaction, delta.as_dict(),
        )

        return GrowthApplyResult(
            applied=True,
            rule_trigger=trigger,
            delta_applied=delta.as_dict(),
            guard_result=guard_result.as_dict(),
            reason=(
                f"交互驱动成长: {analysis.total_interactions}次交互, "
                f"主导={analysis.dominant_interaction}, "
                f"周期={lookback_days}天{feedback_detail}"
            ),
        )

    # ── 查询接口 ──────────────────────────────────────────────────────────

    def get_triggered_rules(self, user_id: str) -> set[str]:
        """获取用户已触发的 one_time 规则。"""
        return set(self._triggered_rules.get(str(user_id).strip(), set()))

    def get_weekly_cumulative(self, user_id: str) -> dict[str, float]:
        """获取用户本周累计变化量。"""
        return dict(self._weekly_cumulative.get(str(user_id).strip(), {}))

    def get_change_history(self, user_id: str) -> list[dict[str, float]]:
        """获取用户变化历史。"""
        return list(self._change_history.get(str(user_id).strip(), []))

    def reset_weekly_cumulative(self, user_id: str) -> None:
        """重置每周累计（由定时任务调用）。"""
        uid = str(user_id).strip()
        self._weekly_cumulative[uid] = {n: 0.0 for n in TRAIT_NAMES}
        _logger.info("每周累计已重置: user=%s", uid)


def _now_ms() -> int:
    return int(time.time() * 1000)
