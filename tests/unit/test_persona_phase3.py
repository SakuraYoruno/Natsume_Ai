"""Phase III 验收测试：自我意识模型。

验收标准：
  V-III-1: SelfModelSnapshot 可从当前 EmotionTimeline + Bond 状态生成
  V-III-2: self_reflection_pulse 槽位成功注入 system prompt
  V-III-3: 自省内容概率注入（每 5-10 轮触发一次），非每轮注入
  V-III-4: 自省内容与当前 Bond 阶段/情感趋势一致（不出现矛盾描述）
"""

from __future__ import annotations

import pytest

from host_brain.persona.self_model import (
    SelfModel,
    build_growth_note_text,
    build_mood_awareness_text,
    build_self_awareness_text,
)
from host_brain.persona.self_reflection import (
    SelfReflection,
    build_self_reflection_pulse,
)
from host_brain.persona.persona_engine import (
    PersonaEngine,
    SelfModelSnapshot,
    TraitChangeRecord,
)
from host_brain.persona.personality_traits import (
    PersonalityTraits,
    TraitsDelta,
)


# ---------------------------------------------------------------------------
# 测试辅助：模拟数据
# ---------------------------------------------------------------------------

def _make_timeline_summary(
    dominant: str = "HAPPY",
    trend: str = "improving",
    entry_count: int = 10,
) -> dict:
    return {
        "dominant_emotion": dominant,
        "trend": trend,
        "entry_count": entry_count,
        "entries": [],
    }


def _make_timeline_diary(
    dominant: str = "HAPPY",
    avg_valence: float = 0.5,
    total_entries: int = 15,
    arc_start: str = "NEUTRAL",
    arc_end: str = "HAPPY",
) -> dict:
    return {
        "dominant_emotion": dominant,
        "avg_valence": avg_valence,
        "total_entries": total_entries,
        "emotion_arc": {"start": arc_start, "middle": "HAPPY", "end": arc_end},
        "diary_text": "今天心情不错",
        "time_range_ms": (1000, 2000),
    }


class _MockBondState:
    """模拟 BondScoreState。"""
    def __init__(
        self,
        current_stage: str = "FRIEND",
        event_count: int = 30,
        total_score: int = 50,
    ):
        self.user_id = "test_user"
        self.current_stage = current_stage
        self.total_score = total_score
        self.event_count = event_count
        self.last_event_ts_ms = 1000

    def trend(self):
        return "稳定"

    def capability_hint(self):
        return "朋友"

    def to_summary(self):
        return {
            "stage": "朋友",
            "trend": self.trend(),
            "capability_hint": self.capability_hint(),
        }


def _make_bond_summary(trend: str = "稳定") -> dict:
    return {"stage": "朋友", "trend": trend, "capability_hint": "朋友"}


# ---------------------------------------------------------------------------
# V-III-1: SelfModelSnapshot 生成
# ---------------------------------------------------------------------------

class TestVIII1_SelfModelSnapshot:
    """V-III-1: SelfModelSnapshot 可从 EmotionTimeline + Bond 状态生成。"""

    def test_snapshot_from_complete_data(self):
        """提供完整数据 → 快照字段均非空。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
            bond_state=_MockBondState(),
            bond_summary=_make_bond_summary(),
        )
        assert snapshot["recent_mood_trend"] != ""
        assert snapshot["dominant_recent_emotion"] != ""
        assert snapshot["bond_growth_perception"] != ""
        assert isinstance(snapshot["emotional_stability"], float)
        assert 0.0 <= snapshot["emotional_stability"] <= 1.0

    def test_snapshot_from_empty_data(self):
        """无数据 → 快照仍可生成（使用默认值）。"""
        model = SelfModel()
        snapshot = model.build_snapshot()
        assert snapshot["recent_mood_trend"] == "平稳"
        assert snapshot["dominant_recent_emotion"] == "NEUTRAL"
        assert snapshot["bond_growth_perception"] == "稳定"

    def test_mood_trend_improving(self):
        """趋势 improving → 心情偏好。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(trend="improving"),
            timeline_diary=_make_timeline_diary(avg_valence=0.5),
        )
        assert snapshot["recent_mood_trend"] == "偏好"

    def test_mood_trend_declining(self):
        """趋势 declining → 心情偏低。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(trend="declining"),
            timeline_diary=_make_timeline_diary(avg_valence=-0.5),
        )
        assert snapshot["recent_mood_trend"] == "偏低"

    def test_mood_trend_stable(self):
        """趋势 stable → 心情平稳。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(trend="stable"),
            timeline_diary=_make_timeline_diary(avg_valence=0.0),
        )
        assert snapshot["recent_mood_trend"] == "平稳"

    def test_bond_growth_rising(self):
        """Bond 上升 → 变亲近。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            bond_summary=_make_bond_summary(trend="上升"),
        )
        assert snapshot["bond_growth_perception"] == "变亲近"

    def test_bond_growth_declining(self):
        """Bond 下降 → 有些疏远。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            bond_summary=_make_bond_summary(trend="下降"),
        )
        assert snapshot["bond_growth_perception"] == "有些疏远"

    def test_personality_changes_extracted(self):
        """特质变化记录 → 自然语言描述。"""
        model = SelfModel()
        record = TraitChangeRecord(
            user_id="test",
            delta={"warmth": 0.05},
            before={"warmth": 0.85},
            after={"warmth": 0.90},
            reason="test",
            ts_ms=1000,
        )
        snapshot = model.build_snapshot(persona_changes=[record])
        assert len(snapshot["personality_changes"]) > 0
        assert any("温柔" in c for c in snapshot["personality_changes"])

    def test_growth_milestones_from_bond(self):
        """Bond 阶段 → 里程碑。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            bond_state=_MockBondState(current_stage="CLOSE_FRIEND", event_count=55),
        )
        assert len(snapshot["growth_milestones"]) > 0

    def test_last_deep_talk_days_ago(self):
        """深度对话时间戳 → 天数计算。"""
        model = SelfModel()
        current = 1000 * 60 * 60 * 24 * 10  # 10 天
        last = 1000 * 60 * 60 * 24 * 7       # 7 天
        snapshot = model.build_snapshot(
            last_deep_talk_ts_ms=last,
            current_ts_ms=current,
        )
        assert snapshot["last_deep_talk_days_ago"] == 3

    def test_emotional_stability_high_when_stable(self):
        """弧线变化小 → 稳定性高。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_diary=_make_timeline_diary(
                arc_start="HAPPY", arc_end="HAPPY", total_entries=5,
            ),
        )
        assert snapshot["emotional_stability"] >= 0.5

    def test_persona_engine_delegates_to_self_model(self):
        """PersonaEngine.get_self_model_snapshot 委托给 SelfModel。"""
        PersonaEngine.reset_instance()
        try:
            engine = PersonaEngine.get_instance()
            snapshot = engine.get_self_model_snapshot(
                "test_user",
                timeline_summary=_make_timeline_summary(),
                timeline_diary=_make_timeline_diary(),
                bond_state=_MockBondState(),
                bond_summary=_make_bond_summary(),
            )
            assert isinstance(snapshot, SelfModelSnapshot)
            assert snapshot.recent_mood_trend in {"偏好", "偏低", "平稳"}
            assert snapshot.dominant_recent_emotion != ""
        finally:
            PersonaEngine.reset_instance()


# ---------------------------------------------------------------------------
# V-III-2: self_reflection_pulse 槽位注入
# ---------------------------------------------------------------------------

class TestVIII2_SelfReflectionPulse:
    """V-III-2: self_reflection_pulse 槽位注入。"""

    def test_pulse_contains_required_fields(self):
        """自省脉冲包含 self_awareness、mood_awareness、growth_note。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
            bond_state=_MockBondState(),
            bond_summary=_make_bond_summary(),
        )
        pulse = build_self_reflection_pulse(snapshot)
        assert "self_awareness" in pulse
        assert "mood_awareness" in pulse
        assert "growth_note" in pulse

    def test_self_awareness_text_non_empty(self):
        """self_awareness 非空。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(),
            bond_summary=_make_bond_summary(),
        )
        pulse = build_self_reflection_pulse(snapshot)
        assert pulse["self_awareness"], "self_awareness 不应为空"

    def test_mood_awareness_text_includes_emotion(self):
        """mood_awareness 包含情感描述。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(dominant="HAPPY"),
            timeline_diary=_make_timeline_diary(),
        )
        pulse = build_self_reflection_pulse(snapshot)
        assert pulse["mood_awareness"], "mood_awareness 不应为空"

    def test_prompt_context_supports_self_reflection_slot(self):
        """prompt_context OPTIONAL_SLOTS 包含 self_reflection_pulse。"""
        from host_brain.core.prompt_context import OPTIONAL_SLOTS
        assert "self_reflection_pulse" in OPTIONAL_SLOTS
        assert OPTIONAL_SLOTS["self_reflection_pulse"] is dict

    def test_prompt_context_validation_accepts_self_reflection(self):
        """含 self_reflection_pulse 的 prompt_context 通过验证。"""
        from host_brain.core.prompt_context import validate_prompt_context, EXPECTED_SLOTS
        # 构建最小有效 prompt_context
        pc = {
            slot_name: ([] if expected_type is list else {})
            for slot_name, expected_type in EXPECTED_SLOTS.items()
        }
        pc["self_reflection_pulse"] = {
            "self_awareness": "心情不错",
            "mood_awareness": "今天感觉开心",
            "growth_note": "",
        }
        result = validate_prompt_context(pc)
        # 检查没有 self_reflection_pulse 相关的 violation
        sr_violations = [
            v for v in result.violations
            if v.slot == "self_reflection_pulse"
        ]
        assert len(sr_violations) == 0


# ---------------------------------------------------------------------------
# V-III-3: 自省概率触发
# ---------------------------------------------------------------------------

class TestVIII3_ProbabilisticTrigger:
    """V-III-3: 自省概率注入控制。"""

    def test_not_triggered_on_first_turn(self):
        """第 1 轮不应触发（最小间隔 = 5）。"""
        reflection = SelfReflection(min_interval=5, max_interval=10, seed=42)
        result = reflection.maybe_generate(turn_count=1)
        assert result is None

    def test_triggered_within_expected_range(self):
        """在 20 轮中触发 2-4 次。"""
        reflection = SelfReflection(min_interval=5, max_interval=10, seed=42)
        trigger_count = 0
        for turn in range(1, 21):
            result = reflection.maybe_generate(
                turn_count=turn,
                timeline_summary=_make_timeline_summary(),
                timeline_diary=_make_timeline_diary(),
                bond_state=_MockBondState(),
                bond_summary=_make_bond_summary(),
            )
            if result is not None:
                trigger_count += 1
        assert 2 <= trigger_count <= 4, (
            f"20 轮对话中应触发 2-4 次，实际触发 {trigger_count} 次"
        )

    def test_should_trigger_deterministic(self):
        """should_trigger 根据轮次确定性判断。"""
        reflection = SelfReflection(min_interval=5, max_interval=5, seed=0)
        # 固定间隔 5，首次在第 5 轮
        assert not reflection.should_trigger(1)
        assert not reflection.should_trigger(4)
        assert reflection.should_trigger(5)

    def test_force_generate_always_returns(self):
        """force_generate 总是返回结果（无概率控制）。"""
        reflection = SelfReflection(seed=42)
        result = reflection.force_generate(
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
        )
        assert isinstance(result, dict)
        assert "self_awareness" in result

    def test_next_trigger_changes_after_trigger(self):
        """触发后 next_trigger_turn 更新。"""
        reflection = SelfReflection(min_interval=5, max_interval=5, seed=0)
        initial_next = reflection.next_trigger_turn
        # 触发
        reflection.maybe_generate(
            turn_count=initial_next,
            timeline_summary=_make_timeline_summary(),
        )
        assert reflection.next_trigger_turn > initial_next


# ---------------------------------------------------------------------------
# V-III-4: 一致性校验
# ---------------------------------------------------------------------------

class TestVIII4_Consistency:
    """V-III-4: 自省内容与 Bond 阶段/情感趋势一致。"""

    def test_stranger_no_intimate_description(self):
        """Bond=STRANGER 时不出现"变亲近"。"""
        reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
        result = reflection.maybe_generate(
            turn_count=1,
            bond_state=_MockBondState(current_stage="STRANGER"),
            bond_summary={"stage": "陌生人", "trend": "上升", "capability_hint": ""},
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
        )
        assert result is not None
        assert "亲近" not in result.get("self_awareness", "")

    def test_acquaintance_no_intimate_description(self):
        """Bond=ACQUAINTANCE 时不出现"很亲近"。"""
        reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
        result = reflection.maybe_generate(
            turn_count=1,
            bond_state=_MockBondState(current_stage="ACQUAINTANCE"),
            bond_summary={"stage": "相识", "trend": "上升", "capability_hint": ""},
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
        )
        assert result is not None
        # "变亲近" 应被一致性校验替换为 "稳定"
        assert "亲近" not in result.get("self_awareness", "")

    def test_sad_mood_no_happy_description(self):
        """情感趋势 declining + SAD → 不出现"心情好"。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(dominant="SAD", trend="declining"),
            timeline_diary=_make_timeline_diary(avg_valence=-0.5, arc_start="NEUTRAL", arc_end="SAD"),
        )
        pulse = build_self_reflection_pulse(snapshot)
        assert "心情挺好" not in pulse["self_awareness"]
        assert "低落" in pulse["self_awareness"] or "平稳" in pulse["self_awareness"]

    def test_improving_mood_positive_description(self):
        """情感趋势 improving → 出现正面描述。"""
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=_make_timeline_summary(dominant="HAPPY", trend="improving"),
            timeline_diary=_make_timeline_diary(avg_valence=0.5),
        )
        pulse = build_self_reflection_pulse(snapshot)
        assert "好" in pulse["self_awareness"] or "不错" in pulse["self_awareness"]

    def test_close_friend_can_say_intimate(self):
        """Bond=CLOSE_FRIEND + 上升趋势 → 可以说"变亲近"。"""
        reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
        result = reflection.maybe_generate(
            turn_count=1,
            bond_state=_MockBondState(current_stage="CLOSE_FRIEND"),
            bond_summary={"stage": "好友", "trend": "上升", "capability_hint": ""},
            timeline_summary=_make_timeline_summary(),
            timeline_diary=_make_timeline_diary(),
        )
        assert result is not None
        assert "变好" in result.get("self_awareness", "") or "亲近" in result.get("self_awareness", "")


# ---------------------------------------------------------------------------
# 补充测试：文本生成辅助函数
# ---------------------------------------------------------------------------

class TestPhaseIII_TextGenerators:
    """文本生成辅助函数测试。"""

    def test_self_awareness_text(self):
        """build_self_awareness_text 输出非空。"""
        snapshot = {
            "recent_mood_trend": "偏好",
            "dominant_recent_emotion": "HAPPY",
            "bond_growth_perception": "变亲近",
        }
        text = build_self_awareness_text(snapshot)
        assert text
        assert "心情" in text

    def test_mood_awareness_text_stable(self):
        """高稳定性 → 包含"稳定"。"""
        snapshot = {
            "dominant_recent_emotion": "HAPPY",
            "emotional_stability": 0.8,
        }
        text = build_mood_awareness_text(snapshot)
        assert "稳定" in text

    def test_mood_awareness_text_volatile(self):
        """低稳定性 → 包含"波动"。"""
        snapshot = {
            "dominant_recent_emotion": "SAD",
            "emotional_stability": 0.2,
        }
        text = build_mood_awareness_text(snapshot)
        assert "波动" in text

    def test_growth_note_with_changes(self):
        """有特质变化 → growth_note 非空。"""
        snapshot = {
            "personality_changes": ["变得更温柔了"],
            "growth_milestones": [],
        }
        text = build_growth_note_text(snapshot)
        assert "温柔" in text

    def test_growth_note_with_milestones(self):
        """有里程碑 → growth_note 非空。"""
        snapshot = {
            "personality_changes": [],
            "growth_milestones": ["和 master 已经成为朋友了"],
        }
        text = build_growth_note_text(snapshot)
        assert "朋友" in text

    def test_growth_note_empty(self):
        """无变化无里程碑 → growth_note 为空。"""
        snapshot = {
            "personality_changes": [],
            "growth_milestones": [],
        }
        text = build_growth_note_text(snapshot)
        assert text == ""


# ---------------------------------------------------------------------------
# 端到端集成测试
# ---------------------------------------------------------------------------

class TestPhaseIII_EndToEnd:
    """Phase III 端到端集成。"""

    def test_full_pipeline(self):
        """PersonaEngine → SelfModel → SelfReflection → self_reflection_pulse。"""
        PersonaEngine.reset_instance()
        try:
            engine = PersonaEngine.get_instance()

            # 模拟一次特质变化
            engine.apply_growth_delta(
                "e2e_user",
                TraitsDelta(warmth=0.03),
                reason="test_e2e",
            )

            # 获取快照（Phase III 完整实现）
            snapshot = engine.get_self_model_snapshot(
                "e2e_user",
                timeline_summary=_make_timeline_summary(dominant="HAPPY", trend="improving"),
                timeline_diary=_make_timeline_diary(avg_valence=0.5),
                bond_state=_MockBondState(current_stage="FRIEND"),
                bond_summary=_make_bond_summary(trend="上升"),
            )
            assert isinstance(snapshot, SelfModelSnapshot)
            assert snapshot.recent_mood_trend == "偏好"
            assert snapshot.bond_growth_perception == "变亲近"
            assert len(snapshot.personality_changes) > 0

            # 生成自省脉冲
            pulse = build_self_reflection_pulse(snapshot.as_dict())
            assert pulse["self_awareness"]
            assert pulse["mood_awareness"]

        finally:
            PersonaEngine.reset_instance()
