"""PersonaEngine：人格引擎核心——Phase I 基座。

职责：
  1. 从 Canon (wc-003-persona-core.json) 加载 7 维特质基线
  2. 维护每个 user_id 的当前人格特质状态
  3. 提供统一查询接口 get_traits()
  4. 提供成长 delta 应用接口 apply_growth_delta()（Phase IV 调用）
  5. 提供自我意识快照接口 get_self_model_snapshot()（Phase III 调用）
  6. 审计日志记录所有特质变化

设计原则：
  - 单例模式，全局唯一实例
  - 线程安全（使用 threading.Lock）
  - Canon 基线作为"不可变宪法"，运行时状态可微调
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.persona.personality_traits import (
    DEFAULT_BASELINE,
    TRAIT_NAMES,
    ExpressionHint,
    PersonalityTraits,
    TraitsDelta,
)

_logger = logging.getLogger("Natsume.persona_engine")

# Canon 文件路径（相对于项目根目录）
_CANON_PERSONA_PATH = Path(__file__).resolve().parent.parent / "canon" / "world" / "wc-003-persona-core.json"


# ---------------------------------------------------------------------------
# SelfModelSnapshot（Phase III 数据结构，此处先定义骨架）
# ---------------------------------------------------------------------------

@dataclass
class SelfModelSnapshot:
    """自我意识快照——Natsume 对自身状态的感知。

    Phase III 将填充完整逻辑，此处先提供骨架以满足接口声明。
    """

    # 情感层面
    recent_mood_trend: str = "平稳"          # "偏好" / "偏低" / "平稳"
    emotional_stability: float = 0.5          # 情感稳定性 (0-1)
    dominant_recent_emotion: str = "NEUTRAL"  # 近期主导情感

    # 关系层面
    bond_growth_perception: str = "稳定"      # "变亲近" / "稳定" / "有些疏远"
    shared_memory_count: int = 0              # 共同记忆数量
    last_deep_talk_days_ago: int = -1         # 上次深度对话距今天数（-1=未知）

    # 成长层面
    personality_changes: list[str] = field(default_factory=list)
    growth_milestones: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "recent_mood_trend": self.recent_mood_trend,
            "emotional_stability": round(self.emotional_stability, 3),
            "dominant_recent_emotion": self.dominant_recent_emotion,
            "bond_growth_perception": self.bond_growth_perception,
            "shared_memory_count": self.shared_memory_count,
            "last_deep_talk_days_ago": self.last_deep_talk_days_ago,
            "personality_changes": list(self.personality_changes),
            "growth_milestones": list(self.growth_milestones),
        }


# ---------------------------------------------------------------------------
# TraitChangeRecord — 特质变化审计记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TraitChangeRecord:
    """特质变化审计记录——每次 apply_growth_delta 时生成。"""

    user_id: str
    delta: dict[str, float]     # 变化量
    before: dict[str, float]    # 变化前值
    after: dict[str, float]     # 变化后值
    reason: str                 # 变化原因（如 "bond_stage_promoted:FRIEND→CLOSE_FRIEND"）
    ts_ms: int                  # 时间戳

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "delta": self.delta,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# PersonaEngine
# ---------------------------------------------------------------------------

class PersonaEngine:
    """人格引擎单例，从 Canon + 持久化状态初始化。

    使用方式：
        engine = PersonaEngine.get_instance()
        traits = engine.get_traits(user_id)
    """

    _instance: "PersonaEngine | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, *, canon_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._canon_path = Path(canon_path) if canon_path else _CANON_PERSONA_PATH
        self._baseline = self._load_baseline()
        # user_id → 当前特质（运行时状态）
        self._user_traits: dict[str, PersonalityTraits] = {}
        # user_id → 变化记录（审计链）
        self._change_log: dict[str, list[TraitChangeRecord]] = {}
        _logger.info(
            "PersonaEngine 初始化完成，基线特质: %s",
            self._baseline.as_dict(),
        )

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "PersonaEngine":
        """获取全局单例。首次调用时创建。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        with cls._instance_lock:
            cls._instance = None

    # ── Canon 基线加载 ────────────────────────────────────────────────────

    def _load_baseline(self) -> PersonalityTraits:
        """从 Canon 文件加载人格特质基线。"""
        try:
            if self._canon_path.exists():
                raw = json.loads(self._canon_path.read_text(encoding="utf-8"))
                baseline_data = raw.get("personality_traits_baseline", {})
                if baseline_data:
                    _logger.info("从 Canon 加载人格基线: %s", self._canon_path.name)
                    return PersonalityTraits.from_dict(baseline_data)
                _logger.warning(
                    "Canon 文件缺少 personality_traits_baseline 字段，使用默认基线",
                )
        except Exception as exc:
            _logger.warning("加载 Canon 人格基线失败: %s，使用默认基线", exc)
        return PersonalityTraits.from_baseline(DEFAULT_BASELINE)

    @property
    def baseline(self) -> PersonalityTraits:
        """只读访问 Canon 基线。"""
        return PersonalityTraits.from_dict(self._baseline.as_dict())

    # ── 核心接口 ──────────────────────────────────────────────────────────

    def get_traits(self, user_id: str) -> PersonalityTraits:
        """获取指定用户的当前人格特质。

        首次访问时从基线拷贝初始化。

        Args:
            user_id: 用户 ID

        Returns:
            当前人格特质（副本，修改不影响引擎内部状态）
        """
        uid = str(user_id).strip()
        with self._lock:
            if uid not in self._user_traits:
                self._user_traits[uid] = PersonalityTraits.from_dict(
                    self._baseline.as_dict()
                )
                _logger.debug("用户 %s 初始化人格特质（基线拷贝）", uid)
            traits = self._user_traits[uid]
        # 返回副本
        return PersonalityTraits.from_dict(traits.as_dict())

    def get_expression_adapter(
        self,
        traits: PersonalityTraits,
        emotion: Any,
        bond_stage: str,
    ) -> ExpressionHint:
        """根据特质 + 情感 + 关系阶段生成表达策略提示。

        Phase I 提供基础实现，Phase II 将引入 PersonaExpressionAdapter 完善。

        Args:
            traits: 当前人格特质
            emotion: EmotionVector（此处用 Any 避免循环导入）
            bond_stage: 当前 Bond 阶段

        Returns:
            ExpressionHint 表达策略提示
        """
        # Phase I 基础实现：根据特质值简单映射
        if traits.warmth >= 0.7:
            style = "温柔共情"
            tone = "caring"
        elif traits.playfulness >= 0.6:
            style = "轻松俏皮"
            tone = "playful"
        elif traits.assertiveness >= 0.6:
            style = "自信坚定"
            tone = "serious"
        else:
            style = "自然温和"
            tone = "gentle"

        echo_modifier = 0.0
        if traits.sensitivity >= 0.7:
            echo_modifier = 0.1  # 高敏感度 → 回声权重提升

        follow_up = 0.0
        if traits.curiosity >= 0.6:
            follow_up = 0.1  # 高好奇心 → 追问倾向提升

        return ExpressionHint(
            style_hint=style,
            tone=tone,
            echo_weight_modifier=echo_modifier,
            追问倾向=follow_up,
        )

    def apply_growth_delta(
        self,
        user_id: str,
        delta: TraitsDelta,
        *,
        reason: str = "",
        ts_ms: int = 0,
    ) -> PersonalityTraits:
        """应用人格成长变化量（Phase IV 调用）。

        注意：Phase I 不实施幅度锁等安全机制，仅记录变化。
        Phase IV 将在调用此方法前进行安全校验。

        Args:
            user_id: 用户 ID
            delta: 特质变化量
            reason: 变化原因
            ts_ms: 时间戳

        Returns:
            应用后的新特质
        """
        uid = str(user_id).strip()
        with self._lock:
            if uid not in self._user_traits:
                self._user_traits[uid] = PersonalityTraits.from_dict(
                    self._baseline.as_dict()
                )

            before = self._user_traits[uid]
            before_dict = before.as_dict()

            # 应用 delta
            new_traits = before + delta
            self._user_traits[uid] = new_traits

            after_dict = new_traits.as_dict()

            # 审计记录
            record = TraitChangeRecord(
                user_id=uid,
                delta=delta.as_dict(),
                before=before_dict,
                after=after_dict,
                reason=reason or "unknown",
                ts_ms=ts_ms or _now_ms(),
            )
            if uid not in self._change_log:
                self._change_log[uid] = []
            self._change_log[uid].append(record)

            _logger.info(
                "人格特质变化: user=%s reason=%s delta=%s",
                uid, reason, delta.as_dict(),
            )

        return PersonalityTraits.from_dict(after_dict)

    def get_change_log(self, user_id: str, limit: int = 20) -> list[TraitChangeRecord]:
        """获取指定用户的特质变化记录（审计链）。"""
        uid = str(user_id).strip()
        with self._lock:
            records = self._change_log.get(uid, [])
            return list(records[-limit:])

    def rollback_last_change(self, user_id: str) -> PersonalityTraits | None:
        """回滚最近一次特质变化（安全机制）。

        Returns:
            回滚后的特质，如果无变化记录则返回 None
        """
        uid = str(user_id).strip()
        with self._lock:
            records = self._change_log.get(uid, [])
            if not records:
                _logger.warning("用户 %s 无特质变化记录，无法回滚", uid)
                return None

            last_record = records.pop()
            # 恢复到变化前的状态
            restored = PersonalityTraits.from_dict(last_record.before)
            self._user_traits[uid] = restored

            _logger.info(
                "人格特质回滚: user=%s reason=%s 恢复到 %s",
                uid, last_record.reason, restored.as_dict(),
            )
            return PersonalityTraits.from_dict(restored.as_dict())

    def get_self_model_snapshot(self, user_id: str) -> SelfModelSnapshot:
        """获取自我意识快照（Phase III 骨架）。

        Phase I 提供基础实现（仅含特质变化感知），
        Phase III 将引入 SelfModel 完整填充。
        """
        uid = str(user_id).strip()
        changes = self.get_change_log(uid, limit=5)

        personality_changes: list[str] = []
        if changes:
            # 简单的变化感知：找出最近变化的维度
            latest = changes[-1]
            for name in TRAIT_NAMES:
                d = latest.delta.get(name, 0.0)
                if abs(d) >= 0.01:
                    cn_names = {
                        "warmth": "温柔度", "curiosity": "好奇心",
                        "humor": "幽默感", "sensitivity": "敏感度",
                        "assertiveness": "自信度", "playfulness": "俏皮度",
                        "resilience": "韧性",
                    }
                    cn = cn_names.get(name, name)
                    if d > 0:
                        personality_changes.append(f"{cn}有所提升")
                    else:
                        personality_changes.append(f"{cn}有所降低")

        return SelfModelSnapshot(
            personality_changes=personality_changes,
        )


def _now_ms() -> int:
    """获取当前时间戳（毫秒）——避免循环导入。"""
    import time
    return int(time.time() * 1000)
