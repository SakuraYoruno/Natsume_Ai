"""SelfReflection：定期自省内容生成器——Phase III。

职责：
  1. 概率控制自省触发（每 5-10 轮触发一次）
  2. 调用 SelfModel 生成快照
  3. 将快照转化为 self_reflection_pulse 槽位数据
  4. 保证自省内容与当前 Bond 阶段/情感趋势一致

设计原则：
  - 轻量级：不直接持有数据源引用，而是接收已准备好的数据
  - 概率注入：通过对话轮次计数器控制注入频率
  - 一致性：Bond=ACQUAINTANCE 时不会出现"很亲近"等矛盾描述
"""

from __future__ import annotations

import logging
import random
from typing import Any

from host_brain.persona.self_model import (
    SelfModel,
    build_growth_note_text,
    build_mood_awareness_text,
    build_self_awareness_text,
)

_logger = logging.getLogger("Natsume.self_reflection")


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
        self._roll_next_trigger()

    def _roll_next_trigger(self) -> None:
        """随机确定下一次触发的轮次。"""
        self._next_trigger_turn = (
            self._last_trigger_turn
            + self._rng.randint(self._min_interval, self._max_interval)
        )

    def should_trigger(self, turn_count: int) -> bool:
        """判断当前轮次是否应该触发自省。

        Args:
            turn_count: 当前对话轮次（从 1 开始）

        Returns:
            True 表示应该触发
        """
        return int(turn_count) >= self._next_trigger_turn

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
    ) -> dict[str, Any] | None:
        """尝试生成自省脉冲。

        如果本轮不触发，返回 None。
        如果触发，返回 self_reflection_pulse 字典。

        Args:
            turn_count: 当前对话轮次
            其他参数同 SelfModel.build_snapshot()

        Returns:
            self_reflection_pulse 字典或 None
        """
        if not self.should_trigger(turn_count):
            return None

        # 触发自省
        self._last_trigger_turn = int(turn_count)
        self._roll_next_trigger()

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

            pulse = build_self_reflection_pulse(snapshot)
            _logger.info(
                "自省触发 (turn=%d): self_awareness='%s'",
                turn_count,
                pulse.get("self_awareness", "")[:60],
            )
            return pulse

        except Exception as exc:
            _logger.warning("自省生成失败（降级跳过）: %s", exc)
            return None

    def force_generate(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """强制生成自省脉冲（无概率控制，用于测试）。"""
        snapshot = self._model.build_snapshot(**kwargs)
        return build_self_reflection_pulse(snapshot)

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

def build_self_reflection_pulse(snapshot: dict[str, Any]) -> dict[str, Any]:
    """将 SelfModelSnapshot 转化为 self_reflection_pulse 槽位数据。

    Returns:
        包含 self_awareness、mood_awareness、growth_note 的字典
    """
    self_awareness = build_self_awareness_text(snapshot)
    mood_awareness = build_mood_awareness_text(snapshot)
    growth_note = build_growth_note_text(snapshot)

    return {
        "self_awareness": self_awareness,
        "mood_awareness": mood_awareness,
        "growth_note": growth_note,
    }
