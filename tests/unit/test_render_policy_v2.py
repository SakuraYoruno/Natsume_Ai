from __future__ import annotations

from host_brain.app.context.affect_state import AffectStateBlock
from host_brain.app.context.dialogue_state import DialogueStateBlock
from host_brain.app.context.drive_state import DriveStateBlock
from host_brain.app.context.relationship_state import RelationshipStateBlock
from host_brain.app.context.topic_state import TopicStateBlock
from host_brain.persona.render_policy import build_render_policy


def test_render_policy_repeat_identity_enables_meta_response() -> None:
    policy = build_render_policy(
        affect_state=AffectStateBlock(emotion_tag="CALM", social_energy=0.68, warmth_bias=0.42, withdraw_bias=0.18),
        relationship_state=RelationshipStateBlock(
            familiarity=0.10,
            trust=0.08,
            comfort=0.12,
            openness=0.10,
            dependency_tolerance=0.06,
            protectiveness=0.10,
            boundary_softness=0.08,
            affection_signal=0.03,
            bond_stage="STRANGER",
        ),
        drive_state=DriveStateBlock(
            reach_out_drive=0.12,
            withdraw_drive=0.10,
            continuity_drive=0.42,
            self_protection_drive=0.18,
            care_drive=0.16,
            repair_drive=0.0,
            playfulness_drive=0.08,
        ),
        dialogue_state=DialogueStateBlock(
            current_user_act="identity_probe",
            last_assistant_act="self_identify",
            repeat_question_count=1,
            repeat_surface_group="identity_short",
            answered_topics_recent=("identity",),
            recent_identity_answered=True,
            user_stance="probing",
        ),
        topic_state=TopicStateBlock(
            active_topics=("identity",),
            recently_answered_topics=("identity",),
        ),
        render_target_lang="zh",
    )

    assert policy.avoid_verbatim_repeat is True
    assert policy.acknowledge_recent_answer is True
    assert policy.prefer_meta_response_over_restating is True
    assert policy.reply_mode == "identity_repeat_probe"
    assert "不是第一次问身份" in policy.control_line
    assert "不要只回一个词" in policy.control_line
    assert "不要再说“我是夏目蓝”" in policy.control_line


def test_render_policy_non_repeat_identity_keeps_meta_guards_off() -> None:
    policy = build_render_policy(
        affect_state=AffectStateBlock(),
        relationship_state=RelationshipStateBlock(),
        drive_state=DriveStateBlock(),
        dialogue_state=DialogueStateBlock(
            current_user_act="identity_probe",
            repeat_question_count=0,
            recent_identity_answered=False,
            user_stance="probing",
        ),
        topic_state=TopicStateBlock(),
        render_target_lang="zh",
    )

    assert policy.avoid_verbatim_repeat is False
    assert policy.acknowledge_recent_answer is False
    assert policy.prefer_meta_response_over_restating is False
    assert policy.react_to_user_stance is False
    assert policy.topic_reopen_mode == "identity_direct"
    assert policy.reply_mode == "identity_first_ask"
    assert "第一次问身份" in policy.control_line


def test_render_policy_first_boundary_ask_uses_direct_mode() -> None:
    policy = build_render_policy(
        affect_state=AffectStateBlock(),
        relationship_state=RelationshipStateBlock(),
        drive_state=DriveStateBlock(),
        dialogue_state=DialogueStateBlock(
            current_user_act="motivation_probe",
            repeat_question_count=0,
            recent_boundary_answered=False,
            user_stance="probing",
        ),
        topic_state=TopicStateBlock(),
        render_target_lang="zh",
    )

    assert policy.reply_mode == "boundary_first_ask"
    assert "直接回答对方在问的距离感或理由" in policy.control_line


def test_render_policy_first_help_ask_uses_direct_mode() -> None:
    policy = build_render_policy(
        affect_state=AffectStateBlock(),
        relationship_state=RelationshipStateBlock(),
        drive_state=DriveStateBlock(),
        dialogue_state=DialogueStateBlock(
            current_user_act="help_continuity_probe",
            repeat_question_count=0,
            user_stance="probing",
        ),
        topic_state=TopicStateBlock(),
        render_target_lang="zh",
    )

    assert policy.reply_mode == "help_first_ask"
    assert "直接回答这件事，不要先自我介绍" in policy.control_line


def test_render_policy_rejection_reacts_to_user_stance() -> None:
    policy = build_render_policy(
        affect_state=AffectStateBlock(emotion_tag="SAD", social_energy=0.46, warmth_bias=0.28, withdraw_bias=0.32),
        relationship_state=RelationshipStateBlock(),
        drive_state=DriveStateBlock(repair_drive=0.42),
        dialogue_state=DialogueStateBlock(
            current_user_act="rejection",
            user_stance="rejecting",
        ),
        topic_state=TopicStateBlock(),
        render_target_lang="zh",
    )

    assert policy.react_to_user_stance is True
    assert policy.reply_mode == "rejection_realign"
    assert "一整句短话" in policy.control_line
    assert "不要辩解" in policy.control_line
