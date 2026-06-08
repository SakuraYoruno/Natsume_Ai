from __future__ import annotations

from host_brain.app.context.interaction_state import build_interaction_state


def test_build_interaction_state_uses_thread_and_perception_summary() -> None:
    state = build_interaction_state(
        interpretation={
            "literal_topic": "emoji_social_followup",
            "interaction_move": "repair_needed",
            "user_stance": "challenging",
            "followup_target": "emoji_social_reply",
            "ongoing_thread": "image_social",
            "reason_tags": ["image_social_followup:deny"],
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_excerpt": "哥哥，怎么委屈成这样呀？",
            "source_context_excerpt": "[图片消息]",
        },
        affect_state={"emotion_tag": "CALM"},
        relationship_state={
            "bond_stage": "FRIEND",
            "relationship_phase": "stepsibling",
            "relationship_substage": "playful_sibling",
        },
        drive_state={"continuity_drive": 0.35},
        topic_ledger={"active_topics": ["emoji_social_reply"]},
        tactic_ledger={"recent_topics": ["emoji_social_reply"]},
        perception_summary="哭哭的表情图。",
    )

    assert state.current_topic == "emoji_social_followup"
    assert state.followup_target == "emoji_social_reply"
    assert state.followup_excerpt == "哥哥，怎么委屈成这样呀？"
    assert state.followup_context_excerpt == "[图片消息]"
    assert state.ongoing_thread == "image_social"
    assert state.perception_summary == "哭哭的表情图。"
    assert state.relationship["relationship_substage"] == "playful_sibling"
