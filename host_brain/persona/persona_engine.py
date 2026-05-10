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

        升级实现：
          - 接入 emotion 参数，情感状态直接影响风格和语气
          - 加入特质组合效应（高 warmth + 高 sensitivity → 深度共情）
          - Bond 阶段影响表达开放度

        Args:
            traits: 当前人格特质
            emotion: EmotionVector 或含 label/valence/arousal 字段的对象/字典
            bond_stage: 当前 Bond 阶段

        Returns:
            ExpressionHint 表达策略提示
        """
        # ── 解析 emotion 参数 ─────────────────────────────────────────────
        emotion_label = "NEUTRAL"
        emotion_valence = 0.0   # -1.0 ~ 1.0，负=消极，正=积极
        emotion_arousal = 0.5   # 0.0 ~ 1.0，低=平静，高=激动

        if emotion is not None:
            if isinstance(emotion, dict):
                emotion_label = str(emotion.get("label") or emotion.get("emotion") or "NEUTRAL").upper()
                emotion_valence = float(emotion.get("valence") or 0.0)
                emotion_arousal = float(emotion.get("arousal") or 0.5)
            else:
                emotion_label = str(getattr(emotion, "label", None) or getattr(emotion, "emotion", None) or "NEUTRAL").upper()
                emotion_valence = float(getattr(emotion, "valence", 0.0) or 0.0)
                emotion_arousal = float(getattr(emotion, "arousal", 0.5) or 0.5)

        # ── Bond 阶段开放度系数 ───────────────────────────────────────────
        # 越亲密，特质和情感对表达的影响越大
        _BOND_OPENNESS: dict[str, float] = {
            "STRANGER": 0.45,
            "ACQUAINTANCE": 0.55,
            "FRIEND": 0.7,
            "CLOSE_FRIEND": 0.85,
            "CONFIDANT": 0.95,
            "SOULMATE": 1.0,
        }
        stage = str(bond_stage or "STRANGER").strip().upper()
        openness = _BOND_OPENNESS.get(stage, 0.5)

        # ── 特质组合效应 ──────────────────────────────────────────────────
        # 组合效应优先于单特质判断，捕捉更细腻的人格表达
        style = "自然温和"
        tone = "gentle"

        # 组合1：高 warmth + 高 sensitivity → 深度共情（最强共情模式）
        if traits.warmth >= 0.7 and traits.sensitivity >= 0.7:
            style = "深度共情"
            tone = "deeply_caring"

        # 组合2：高 warmth + 高 playfulness → 温柔俏皮（暖萌风格）
        elif traits.warmth >= 0.65 and traits.playfulness >= 0.6:
            style = "温柔俏皮"
            tone = "warm_playful"

        # 组合3：高 curiosity + 高 assertiveness → 主动探索（积极引导）
        elif traits.curiosity >= 0.65 and traits.assertiveness >= 0.6:
            style = "主动探索"
            tone = "inquisitive"

        # 组合4：高 humor + 高 playfulness → 轻松幽默（活泼风格）
        elif traits.humor >= 0.6 and traits.playfulness >= 0.65:
            style = "轻松幽默"
            tone = "playful"

        # 单特质兜底
        elif traits.warmth >= 0.7:
            style = "温柔共情"
            tone = "caring"
        elif traits.playfulness >= 0.6:
            style = "轻松俏皮"
            tone = "playful"
        elif traits.assertiveness >= 0.6:
            style = "自信坚定"
            tone = "serious"

        # ── 情感状态叠加修正 ─────────────────────────────────────────────
        # 当前情感会在 Bond 开放度允许的范围内修正风格
        _NEGATIVE_EMOTIONS = {"SAD", "ANXIOUS", "FRUSTRATED", "ANGRY", "BORED"}
        _POSITIVE_HIGH_AROUSAL = {"EXCITED", "HAPPY", "PROUD", "SURPRISED"}
        _TENDER_EMOTIONS = {"LOVING", "GRATEFUL", "EMBARRASSED"}

        if openness >= 0.7:
            # 亲密关系下，情感对风格有显著影响
            if emotion_label in _NEGATIVE_EMOTIONS or emotion_valence < -0.3:
                # Natsume 自身情绪低落时，风格收敛、更内敛
                tone = f"{tone}_subdued"
                style = f"{style}（情绪偏低）"
            elif emotion_label in _TENDER_EMOTIONS:
                # 温柔/感激/害羞情绪 → 增加柔软质感
                tone = f"{tone}_tender"
                style = f"{style}（温柔质感）"
            elif emotion_label in _POSITIVE_HIGH_AROUSAL and emotion_arousal >= 0.7:
                # 高唤醒正面情绪 → 更活泼
                tone = f"{tone}_bright"
                style = f"{style}（活泼状态）"
        elif openness >= 0.5:
            # 中等亲密度下，只有强烈情感才影响风格
            if emotion_valence < -0.5:
                tone = f"{tone}_subdued"
            elif emotion_arousal >= 0.8 and emotion_valence > 0.4:
                tone = f"{tone}_bright"

        # ── 回声权重（sensitivity 驱动，情感强化）────────────────────────
        echo_modifier = 0.0
        if traits.sensitivity >= 0.7:
            echo_modifier = 0.1
        elif traits.sensitivity >= 0.5:
            echo_modifier = 0.05
        # 情感强烈时，回声权重额外提升（更关注对方感受）
        if abs(emotion_valence) >= 0.5 and openness >= 0.6:
            echo_modifier = min(0.25, echo_modifier + 0.08)

        # ── 追问倾向（curiosity 驱动，Bond 阶段放大）────────────────────
        follow_up = 0.0
        if traits.curiosity >= 0.6:
            follow_up = 0.1 * openness  # Bond 越亲密，追问越自然
        elif traits.curiosity >= 0.4:
            follow_up = 0.05 * openness

        _logger.debug(
            "ExpressionAdapter: stage=%s emotion=%s(v=%.2f,a=%.2f) "
            "openness=%.2f → style=%s tone=%s echo=%.2f followup=%.2f",
            stage, emotion_label, emotion_valence, emotion_arousal,
            openness, style, tone, echo_modifier, follow_up,
        )

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

    def get_self_model_snapshot(
        self,
        user_id: str,
        *,
        timeline_summary: dict[str, Any] | None = None,
        timeline_diary: dict[str, Any] | None = None,
        bond_state: Any | None = None,
        bond_summary: dict[str, Any] | None = None,
        last_deep_talk_ts_ms: int = 0,
        current_ts_ms: int = 0,
    ) -> SelfModelSnapshot:
        """获取自我意识快照（Phase III 完整实现）。

        委托给 SelfModel 进行完整的自省快照构建。
        当 SelfModel 不可用时，降级到 Phase I 基础实现。

        Args:
            user_id: 用户 ID
            timeline_summary: EmotionTimeline.rebuild_summary() 输出
            timeline_diary: EmotionTimeline.build_daily_diary() 输出
            bond_state: BondScoreState（可为 None）
            bond_summary: BondScoreState.to_summary() 输出
            last_deep_talk_ts_ms: 上次深度对话时间戳
            current_ts_ms: 当前时间戳

        Returns:
            SelfModelSnapshot
        """
        uid = str(user_id).strip()
        changes = self.get_change_log(uid, limit=5)

        try:
            from host_brain.persona.self_model import SelfModel  # noqa: PLC0415
            model = SelfModel()
            snapshot_dict = model.build_snapshot(
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
                persona_changes=changes,
                last_deep_talk_ts_ms=last_deep_talk_ts_ms,
                current_ts_ms=current_ts_ms,
            )
            return SelfModelSnapshot(**{
                k: v for k, v in snapshot_dict.items()
                if k in SelfModelSnapshot.__dataclass_fields__
            })
        except Exception as exc:
            _logger.debug("SelfModel 不可用，降级到基础实现: %s", exc)

        # Phase I 降级实现
        personality_changes: list[str] = []
        if changes:
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
