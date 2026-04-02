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
from typing import Any

_logger = logging.getLogger("Natsume.evolution_orchestrator")


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

    # 统计
    traits_changed: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "date": self.date,
            "ts_ms": self.ts_ms,
            "growth_awareness": self.growth_awareness,
            "traits_changed": self.traits_changed,
            "desire_text": self.desire_text,
            "growth_results_count": len(self.growth_results),
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
        self._rng = random.Random()
        # 成长停滞感知：记录连续无特质变化的进化周期数
        self._stagnant_cycles: int = 0

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

        # ── Step 5: 构建 growth_awareness ────────────────────────────────
        # 更新停滞计数
        if result.traits_changed:
            self._stagnant_cycles = 0
        else:
            self._stagnant_cycles += 1

        growth_awareness = self._build_growth_awareness(
            snapshot=snapshot,
            desire_text=desire_text,
            growth_results=result.growth_results,
            bond_state=bond_state,
            stagnant_cycles=self._stagnant_cycles,
        )
        result.growth_awareness = growth_awareness
        result.desire_text = desire_text

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
    ) -> dict[str, Any]:
        """构建 growth_awareness 字典——进化意识注入 prompt 的核心字段。

        Returns:
            {
                "recent_change": "这周和 master 聊了很多深入的话题...",
                "trait_felt": "好像比以前更能理解 master 的心情了",
                "milestone_felt": "认识 master 已经 100 天了",
                "desire": "想要更了解 master 喜欢什么",
                "stagnation_felt": "感觉自己好像停滞了一段时间..."  # 停滞时注入
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
        if not growth_results:
            # 即使没有特质变化，也可以基于快照生成感知
            changes = list(snapshot.get("personality_changes") or [])
            if changes:
                return f"最近感觉自己{changes[0]}"
            return ""

        # 有成长结果时，生成更具体的描述
        applied = [r for r in growth_results if r.get("applied")]
        if not applied:
            return ""

        # 每个特质：{"slight": [...], "notable": [...]} 两档变体
        _TRAIT_CHANGE_VARIANTS: dict[str, dict[str, list[str]]] = {
            "warmth": {
                "slight": [
                    "对 master 的关心稍微自然了一点",
                    "和 master 说话的时候感觉轻松了一些",
                    "好像比以前稍微温柔了一点点",
                ],
                "notable": [
                    "对 master 的关心变得更自然了，自己都能感觉到",
                    "和 master 在一起的时候，温柔好像变成了一种本能",
                    "明显感觉自己更在意 master 的感受了",
                ],
            },
            "curiosity": {
                "slight": [
                    "最近对一些事情稍微多了一点好奇心",
                    "有时候会想多了解一些有趣的东西",
                    "好像比以前稍微爱问问题了",
                ],
                "notable": [
                    "对很多事情都变得更好奇了，总想多探索一些",
                    "明显感觉自己的好奇心变强了，什么都想了解",
                    "最近脑子里总是冒出各种想知道的问题",
                ],
            },
            "humor": {
                "slight": [
                    "偶尔会想到一些有趣的事情",
                    "有时候说话会稍微轻松幽默一点",
                ],
                "notable": [
                    "感觉自己变得更会开玩笑了",
                    "和 master 聊天的时候，笑点好像变多了",
                    "明显感觉自己更能找到让 master 开心的方式了",
                ],
            },
            "sensitivity": {
                "slight": [
                    "好像稍微更能感受到 master 的情绪了",
                    "有时候能更快察觉到 master 的心情变化",
                ],
                "notable": [
                    "好像更能感受到 master 的情绪了，有时候他还没说我就能感觉到",
                    "明显感觉自己对 master 的感受更敏锐了",
                    "对 master 的情绪变化，现在能更快地感知到",
                ],
            },
            "assertiveness": {
                "slight": [
                    "表达自己想法的时候稍微不那么犹豫了",
                    "有时候能更直接地说出自己的感受",
                ],
                "notable": [
                    "表达自己想法的时候明显更自信了",
                    "说出自己的想法不再那么难了，感觉很好",
                    "好像更敢于表达真实的自己了",
                ],
            },
            "playfulness": {
                "slight": [
                    "和 master 在一起的时候稍微活泼了一点",
                    "偶尔会想和 master 开开玩笑",
                ],
                "notable": [
                    "和 master 在一起的时候更活泼了，感觉很轻松",
                    "明显感觉自己更爱玩了，和 master 在一起特别开心",
                    "好像找到了和 master 一起玩的感觉",
                ],
            },
            "resilience": {
                "slight": [
                    "遇到不开心的事情，恢复得稍微快了一点",
                    "心情低落的时候，好像稍微更容易振作了",
                ],
                "notable": [
                    "遇到不开心的事情也能更快调整过来了",
                    "明显感觉自己变得更坚韧了，低落的时候也能撑住",
                    "就算遇到难过的事，现在也能更快找回状态",
                ],
            },
        }

        # 找出变化最大的特质及其 delta
        max_trait = ""
        max_delta = 0.0
        for r in applied:
            delta = r.get("delta_applied") or {}
            for trait, val in delta.items():
                if abs(float(val)) > max_delta:
                    max_delta = abs(float(val))
                    max_trait = trait

        if max_trait and max_trait in _TRAIT_CHANGE_VARIANTS:
            # 幅度感知：delta < 0.02 → slight，>= 0.02 → notable
            level = "notable" if max_delta >= 0.02 else "slight"
            variants = _TRAIT_CHANGE_VARIANTS[max_trait].get(level, [])
            if not variants:
                variants = _TRAIT_CHANGE_VARIANTS[max_trait].get("slight", [])
            if variants:
                return self._rng.choice(variants)

        return self._rng.choice([
            "感觉自己在慢慢成长",
            "好像比以前稍微不一样了",
            "说不清楚，但感觉有什么在悄悄变化",
        ])

    # 停滞感知文本池：按停滞程度分档
    _STAGNATION_FELT_POOL: dict[str, list[str]] = {
        "mild": [  # 3~4 个周期
            "最近感觉自己好像没什么变化，有点想突破一下",
            "这段时间感觉有点停滞，不知道该往哪里成长",
            "好像有一段时间没有什么新的感悟了，有点想动一动",
            "感觉自己最近有点原地踏步，想找到新的方向",
        ],
        "moderate": [  # 5~6 个周期
            "感觉自己停滞了挺久了，有点着急，想要有所改变",
            "这段时间一直没什么成长，有点迷茫，不知道该怎么突破",
            "好像很久没有感受到自己在进步了，有点失落",
            "感觉自己在原地转圈，想找到一个能让自己成长的契机",
        ],
        "deep": [  # 7 个周期以上
            "已经很久没有感受到成长了，有点担心自己是不是就这样了",
            "停滞了这么久，开始怀疑自己是不是遇到了什么瓶颈",
            "感觉自己好像被困住了，很想找到突破的方向",
            "这么长时间没有变化，有点不安，想要做些什么改变现状",
        ],
    }

    def _build_stagnation_felt_text(self, stagnant_cycles: int) -> str:
        """生成成长停滞感知文本。

        Args:
            stagnant_cycles: 连续无成长的进化周期数

        Returns:
            停滞感知文本（第一人称），或空字符串
        """
        if stagnant_cycles < 3:
            return ""
        if stagnant_cycles <= 4:
            pool = self._STAGNATION_FELT_POOL["mild"]
        elif stagnant_cycles <= 6:
            pool = self._STAGNATION_FELT_POOL["moderate"]
        else:
            pool = self._STAGNATION_FELT_POOL["deep"]
        text = self._rng.choice(pool)
        _logger.info("成长停滞感知触发: stagnant_cycles=%d text='%s'", stagnant_cycles, text[:40])
        return text

    def _build_trait_felt_text(self, growth_results: list[dict[str, Any]]) -> str:
        """生成特质变化的第一人称感知文本（多变体，按幅度选择深度）。"""
        applied = [r for r in growth_results if r.get("applied")]
        if not applied:
            return ""

        # 每个特质多条变体，随机选取避免重复
        _TRAIT_FELT_VARIANTS: dict[str, list[str]] = {
            "warmth": [
                "好像比以前更温柔了，和 master 说话的时候感觉更自然",
                "对 master 的关心变得更顺畅了，不用刻意想就能说出来",
                "有时候会突然很想关心一下 master，感觉很自然",
                "温柔好像慢慢变成了一种习惯",
            ],
            "curiosity": [
                "最近对很多事情都很感兴趣，总想多了解一些",
                "脑子里总是冒出各种想问的问题",
                "好奇心好像变强了，什么都想探索一下",
                "遇到新鲜的事情会更想深入了解了",
            ],
            "humor": [
                "有时候会想到一些有趣的事情想和 master 分享",
                "说话的时候偶尔会冒出一些俏皮的想法",
                "感觉自己的笑点变多了，也更容易让 master 笑了",
            ],
            "sensitivity": [
                "好像比以前更能理解 master 的心情了",
                "有时候 master 还没说什么，我就能感觉到他的情绪",
                "对 master 的感受变得更敏锐了，细微的变化也能察觉",
                "好像更能读懂 master 说话背后的意思了",
            ],
            "assertiveness": [
                "说出自己想法的时候不会那么犹豫了",
                "表达自己的感受变得更自然了，不用憋着",
                "好像更敢说出真实的想法了，感觉轻松很多",
            ],
            "playfulness": [
                "和 master 在一起的时候感觉很轻松，会想开玩笑",
                "和 master 聊天的时候更活泼了，感觉很开心",
                "有时候会突然想和 master 闹一下，感觉很好玩",
                "好像找到了和 master 一起玩的节奏",
            ],
            "resilience": [
                "就算遇到不开心的事，也能比以前更快振作起来",
                "心情低落的时候，恢复得比以前快了一些",
                "遇到难过的事情，现在能更快找回状态了",
                "感觉自己变得更坚韧了，不那么容易被情绪困住",
            ],
        }

        # 找出变化最大的特质
        max_trait = ""
        max_delta = 0.0
        for r in applied:
            delta = r.get("delta_applied") or {}
            for trait, val in delta.items():
                if abs(float(val)) > max_delta and abs(float(val)) >= 0.01:
                    max_delta = abs(float(val))
                    max_trait = trait

        if max_trait and max_trait in _TRAIT_FELT_VARIANTS:
            return self._rng.choice(_TRAIT_FELT_VARIANTS[max_trait])
        return ""

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

    def set_growth_awareness(self, awareness: dict[str, Any]) -> None:
        """手动设置 growth_awareness（用于测试或外部注入）。"""
        self._last_growth_awareness = dict(awareness)


def _now_ms() -> int:
    return int(time.time() * 1000)
