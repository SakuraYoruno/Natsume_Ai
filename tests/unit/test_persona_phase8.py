"""Phase VIII 验收测试：场景化主动表达（场景感知 + 人格驱动 + 节日问候）。

验收标准：
  V-VIII-1: 深夜时段（23:00-02:00）+ 用户在线时触发主动表达
  V-VIII-2: 主动表达内容受人格特质影响（高 warmth → 关心类；高 playfulness → 俏皮类）
  V-VIII-3: 离线 > 3 天的用户回归时触发"久别重逢"表达
  V-VIII-4: 主动表达频率有节制（同类型触发间隔 ≥ 24h）
"""

from __future__ import annotations

import datetime
import time

import pytest

from host_brain.persona.personality_traits import PersonalityTraits
from host_brain.persona.proactive_voice import (
    PersonaProactiveVoice,
    ProactiveExpression,
    SCENE_LATE_NIGHT,
    SCENE_LONG_ABSENT,
    SCENE_POST_INTENSE,
    SCENE_TOPIC_FOLLOWUP,
    SCENE_MORNING_GREET,
    SCENE_HOLIDAY_GREET,
    DEFAULT_COOLDOWN_MS,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_ts_at_hour(hour: int, minute: int = 0) -> int:
    """构造指定小时的时间戳。"""
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(target.timestamp() * 1000)


# ---------------------------------------------------------------------------
# V-VIII-1: 深夜时段 + 用户在线时触发
# ---------------------------------------------------------------------------

class TestVVIII1_LateNight:
    """V-VIII-1: 深夜陪伴。"""

    def test_late_night_trigger(self):
        """23:30 + 用户在线触发深夜陪伴。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": True,
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_LATE_NIGHT

    def test_late_night_no_trigger_offline(self):
        """23:30 但用户离线不触发。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
            },
        )
        # 可能被其他场景匹配，检查不是 LATE_NIGHT
        if result is not None:
            assert result.scene_type != SCENE_LATE_NIGHT

    def test_daytime_no_late_night(self):
        """白天不触发深夜陪伴。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(14, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": True,
            },
        )
        if result is not None:
            assert result.scene_type != SCENE_LATE_NIGHT


# ---------------------------------------------------------------------------
# V-VIII-2: 人格特质影响表达风格
# ---------------------------------------------------------------------------

class TestVVIII2_TraitInfluence:
    """V-VIII-2: 人格特质影响表达。"""

    def test_high_warmth_caring_style(self):
        """高 warmth + 高 sensitivity → 温暖关心风格。"""
        traits = PersonalityTraits(warmth=0.95, sensitivity=0.9, playfulness=0.2)
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            traits=traits,
            context={
                "current_ts_ms": ts,
                "user_online": True,
            },
        )
        assert result is not None
        # 高 warmth 应选择 warm 或 caring 风格
        assert any(kw in result.content for kw in ("陪", "关心", "担心", "休息", "早点"))

    def test_high_playfulness_playful_style(self):
        """高 playfulness + 高 humor → 俏皮风格。"""
        traits = PersonalityTraits(warmth=0.3, playfulness=0.95, humor=0.9, sensitivity=0.2)
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            traits=traits,
            context={
                "current_ts_ms": ts,
                "user_online": True,
            },
        )
        assert result is not None
        # 高 playfulness 应选择 playful 风格
        assert any(kw in result.content for kw in ("～", "呀", "啦", "哈", "又"))

    def test_trait_influence_description(self):
        """表达包含人格影响说明。"""
        traits = PersonalityTraits(warmth=0.9)
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            traits=traits,
            context={"current_ts_ms": ts, "user_online": True},
        )
        assert result is not None
        assert result.trait_influence != ""

    def test_no_traits_default_style(self):
        """无人格特质时使用默认风格。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            traits=None,
            context={"current_ts_ms": ts, "user_online": True},
        )
        assert result is not None
        assert result.trait_influence == "默认风格"


# ---------------------------------------------------------------------------
# V-VIII-3: 离线 > 3 天触发"久别重逢"
# ---------------------------------------------------------------------------

class TestVVIII3_LongAbsent:
    """V-VIII-3: 久别重逢。"""

    def test_long_absent_trigger(self):
        """4 天未互动触发久别重逢。"""
        voice = PersonaProactiveVoice()
        now_ts = _now_ms()
        last_ts = now_ts - 4 * 24 * 60 * 60 * 1000  # 4 天前
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": now_ts,
                "last_interaction_ts_ms": last_ts,
                "user_online": False,  # 不触发深夜
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_LONG_ABSENT

    def test_recent_interaction_no_trigger(self):
        """昨天互动不触发久别重逢。"""
        voice = PersonaProactiveVoice()
        now_ts = _now_ms()
        last_ts = now_ts - 1 * 24 * 60 * 60 * 1000  # 1 天前
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": now_ts,
                "last_interaction_ts_ms": last_ts,
                "user_online": False,
            },
        )
        # 不应触发 LONG_ABSENT
        if result is not None:
            assert result.scene_type != SCENE_LONG_ABSENT

    def test_long_absent_content(self):
        """久别重逢表达包含关心词汇。"""
        voice = PersonaProactiveVoice()
        now_ts = _now_ms()
        last_ts = now_ts - 5 * 24 * 60 * 60 * 1000
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": now_ts,
                "last_interaction_ts_ms": last_ts,
            },
        )
        assert result is not None
        assert any(kw in result.content for kw in ("好久", "担心", "想", "不见"))


# ---------------------------------------------------------------------------
# V-VIII-4: 主动表达频率节制
# ---------------------------------------------------------------------------

class TestVVIII4_Cooldown:
    """V-VIII-4: 频率限制。"""

    def test_cooldown_prevents_repeat(self):
        """同类型在 24h 内不重复触发。"""
        voice = PersonaProactiveVoice(cooldown_ms=DEFAULT_COOLDOWN_MS)
        ts = _make_ts_at_hour(23, 30)

        # 第一次触发
        r1 = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts, "user_online": True},
        )
        assert r1 is not None

        # 1 小时后再次触发（同类型应被冷却）
        ts2 = ts + 1 * 3600 * 1000
        r2 = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts2, "user_online": True},
        )
        # 同类型应被冷却，不再触发 LATE_NIGHT
        if r2 is not None:
            assert r2.scene_type != SCENE_LATE_NIGHT

    def test_cooldown_expires(self):
        """冷却期过后可再次触发。"""
        voice = PersonaProactiveVoice(cooldown_ms=1000)  # 1 秒冷却
        ts = _make_ts_at_hour(23, 30)

        r1 = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts, "user_online": True},
        )
        assert r1 is not None

        # 2 秒后
        ts2 = ts + 2000
        # 调整时间使其仍在深夜
        r2 = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts2, "user_online": True},
        )
        # 冷却已过期，应能再次触发
        assert r2 is not None

    def test_different_scenes_independent_cooldown(self):
        """不同场景冷却独立。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)

        # 触发深夜陪伴
        r1 = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts, "user_online": True},
        )
        assert r1 is not None
        assert r1.scene_type == SCENE_LATE_NIGHT

        # 同时满足久别重逢条件（深夜 + 4天未互动）
        last_ts = ts - 4 * 24 * 60 * 60 * 1000
        r2 = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts + 1000,
                "last_interaction_ts_ms": last_ts,
                "user_online": False,
            },
        )
        # LATE_NIGHT 被冷却，但 LONG_ABSENT 应能触发
        if r2 is not None:
            assert r2.scene_type == SCENE_LONG_ABSENT

    def test_cooldown_status_readable(self):
        """冷却状态可查询。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(23, 30)
        voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts, "user_online": True},
        )
        status = voice.get_cooldown_status("u1")
        assert isinstance(status, dict)


# ---------------------------------------------------------------------------
# 其他场景测试
# ---------------------------------------------------------------------------

class TestOtherScenes:
    """其他场景测试。"""

    def test_morning_greet(self):
        """早安问候（06:00-10:00）。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(7, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts},
        )
        assert result is not None
        assert result.scene_type == SCENE_MORNING_GREET

    def test_holiday_greet(self):
        """节日问候。"""
        voice = PersonaProactiveVoice()
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": _make_ts_at_hour(12, 0),
                "is_holiday": True,
                "holiday_name": "元宵节",
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_HOLIDAY_GREET
        assert "元宵节" in result.content

    def test_post_intense(self):
        """情感高潮后表达。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "last_emotion_intensity": 0.9,
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_POST_INTENSE

    def test_expression_as_dict(self):
        """表达结果可序列化。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(7, 30)
        result = voice.evaluate_scene(
            user_id="u1",
            context={"current_ts_ms": ts},
        )
        assert result is not None
        d = result.as_dict()
        assert "scene_type" in d
        assert "content" in d
        assert "emotion_tag" in d
        assert "trait_influence" in d

    def test_no_scene_returns_none(self):
        """无场景触发返回 None。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,  # 刚刚互动过
                "last_emotion_intensity": 0.3,         # 低强度
            },
        )
        assert result is None


# ---------------------------------------------------------------------------
# V-VIII-6: 话题延续主动发声
# ---------------------------------------------------------------------------

class TestVVIII6_TopicFollowup:
    """V-VIII-6: 话题延续场景。"""

    def test_topic_followup_trigger(self):
        """存在待跟进话题时触发话题延续。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "面试", "topic_keywords": ["面试", "工作"]},
                ],
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_TOPIC_FOLLOWUP

    def test_topic_label_in_content(self):
        """话题标签被嵌入到表达内容中。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "搬家", "topic_keywords": ["搬家"]},
                ],
            },
        )
        assert result is not None
        assert "搬家" in result.content

    def test_no_topics_no_trigger(self):
        """无待跟进话题时不触发。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [],
            },
        )
        assert result is None

    def test_empty_label_no_trigger(self):
        """话题标签为空时不触发。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "", "topic_keywords": []},
                ],
            },
        )
        assert result is None

    def test_topic_followup_emotion_tag(self):
        """话题延续场景的情感标签为 THINKING。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "考试", "topic_keywords": ["考试"]},
                ],
            },
        )
        assert result is not None
        assert result.emotion_tag == "THINKING"

    def test_topic_followup_bond_deep(self):
        """深层关系的话题延续表达更亲密。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "bond_stage": "SOULMATE",
                "pending_followup_topics": [
                    {"topic_label": "旅行", "topic_keywords": ["旅行"]},
                ],
            },
        )
        assert result is not None
        assert "旅行" in result.content
        # 深层关系应有更亲密的表达
        assert any(kw in result.content for kw in ("惦记", "记在心里", "放在心上", "急死", "快快快"))

    def test_topic_followup_cooldown(self):
        """话题延续场景受冷却期限制。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        ctx_base = {
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "last_emotion_intensity": 0.3,
            "pending_followup_topics": [
                {"topic_label": "面试", "topic_keywords": ["面试"]},
            ],
        }

        # 第一次触发
        r1 = voice.evaluate_scene(
            user_id="u1",
            context={**ctx_base, "current_ts_ms": ts},
        )
        assert r1 is not None
        assert r1.scene_type == SCENE_TOPIC_FOLLOWUP

        # 1小时后再次触发（应被冷却）
        ts2 = ts + 1 * 3600 * 1000
        r2 = voice.evaluate_scene(
            user_id="u1",
            context={**ctx_base, "current_ts_ms": ts2},
        )
        if r2 is not None:
            assert r2.scene_type != SCENE_TOPIC_FOLLOWUP

    def test_topic_followup_serializable(self):
        """话题延续表达结果可序列化。"""
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "项目", "topic_keywords": ["项目"]},
                ],
            },
        )
        assert result is not None
        d = result.as_dict()
        assert d["scene_type"] == SCENE_TOPIC_FOLLOWUP
        assert "项目" in d["content"]


class TestMotiveDrivenProactiveVoice:
    def test_topic_followup_is_blocked_when_continuity_and_reach_out_are_low(self):
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "project", "topic_keywords": ["project"]},
                ],
                "motive_state": {
                    "reach_out_drive": 0.2,
                    "care_drive": 0.22,
                    "withdraw_drive": 0.3,
                    "continuity_drive": 0.18,
                    "self_protect_drive": 0.24,
                    "dominant_motive": "withdraw",
                },
            },
        )
        assert result is None

    def test_topic_followup_is_promoted_by_high_continuity_drive(self):
        voice = PersonaProactiveVoice()
        ts = _make_ts_at_hour(15, 0)
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": ts,
                "user_online": False,
                "last_interaction_ts_ms": ts - 1000,
                "last_emotion_intensity": 0.3,
                "pending_followup_topics": [
                    {"topic_label": "project", "topic_keywords": ["project"]},
                ],
                "motive_state": {
                    "reach_out_drive": 0.34,
                    "care_drive": 0.28,
                    "withdraw_drive": 0.1,
                    "continuity_drive": 0.86,
                    "self_protect_drive": 0.08,
                    "dominant_motive": "continuity",
                },
            },
        )
        assert result is not None
        assert result.scene_type == SCENE_TOPIC_FOLLOWUP
        assert result.motive_influence == "continuity/reach_out promotes topic follow-up"
        assert result.as_dict()["motive_influence"] == "continuity/reach_out promotes topic follow-up"

    def test_high_withdraw_drive_blocks_long_absent_outreach(self):
        voice = PersonaProactiveVoice()
        now_ts = _now_ms()
        last_ts = now_ts - 5 * 24 * 60 * 60 * 1000
        result = voice.evaluate_scene(
            user_id="u1",
            context={
                "current_ts_ms": now_ts,
                "last_interaction_ts_ms": last_ts,
                "user_online": False,
                "motive_state": {
                    "reach_out_drive": 0.12,
                    "care_drive": 0.18,
                    "withdraw_drive": 0.88,
                    "continuity_drive": 0.15,
                    "self_protect_drive": 0.81,
                    "dominant_motive": "self_protect",
                },
            },
        )
        assert result is None
