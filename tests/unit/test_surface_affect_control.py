from __future__ import annotations

from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_messages,
    build_style_reference_query,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from host_brain.persona.surface_affect_control import (
    affect_control_packet_from_mapping,
    build_surface_affect_control_packet,
    build_surface_input_with_affect_control,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def test_affect_control_packet_compresses_emotion_and_bond_without_raw_state() -> None:
    packet = build_surface_affect_control_packet(
        emotion_state={
            "emotion_tag": "sadness",
            "emotion_intensity": 0.9,
            "raw_inner_monologue": "RAW_EMOTION_SECRET_SHOULD_NOT_LEAK",
        },
        bond_summary={"stage": "friend", "closeness": 0.6},
        relationship_clues=["关系阶段：熟人私聊，不升级亲密。"],
        task_mode="tired",
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="我有点累",
            task_mode_hint="tired",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_soft",
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))
    query = build_style_reference_query(request)

    assert packet.emotional_tone == "soft_containment"
    assert packet.relationship_distance == "familiar"
    assert packet.expression_intensity <= 0.82
    assert request.control.emotional_tone == "soft_containment"
    assert "情感层只调语气、节奏和距离，不新增事实。" in rendered
    assert "不要连环追问" in rendered
    assert "RAW_EMOTION_SECRET_SHOULD_NOT_LEAK" not in rendered
    assert "soft" in query.emotion_tags
    assert query.relationship_stage == "sibling_good"


def test_affect_control_packet_from_mapping_ignores_raw_emotion_context() -> None:
    packet = affect_control_packet_from_mapping(
        {
            "emotional_tone": "hurt_controlled",
            "relationship_distance": "guarded",
            "expression_intensity": 2,
            "style_anchor": ["情绪表达：可以有一点被刺到。"],
            "must_not_do": ["不要攻击用户或冷暴力"],
            "raw_emotion_context": {"inner_monologue": "这句不应该被接收"},
            "trace": {"raw_text": "这句不应该被接收", "emotion_tag": "HURT", "clue_count": 1},
        }
    )

    payload = packet.as_dict()

    assert packet.expression_intensity == 1.0
    assert packet.relationship_distance == "guarded"
    assert "raw_emotion_context" not in payload
    assert "这句不应该被接收" not in str(payload)
    assert packet.trace == {"emotion_tag": "HURT", "clue_count": 1}


def test_affect_control_scoring_prefers_contained_hurt_over_sweet_flattening() -> None:
    packet = build_surface_affect_control_packet(
        emotion_state={"emotion_tag": "hurt", "emotion_intensity": 0.7},
        bond_summary={"stage": "friend"},
        task_mode="relationship_boundary",
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="你怎么这么像客服",
            task_mode_hint="relationship_boundary",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_hurt",
    )

    scores = score_isolated_surface_candidates(
        request,
        [
            "没关系呀，我会永远陪着你。",
            "别把我写得像没感觉一样。先把话说回来。",
        ],
    )
    selected = select_best_isolated_candidate(scores)

    assert selected is not None
    assert selected.text == "别把我写得像没感觉一样。先把话说回来。"
    assert any("AFFECT_TOO_SWEET_FOR_HURT" in code for code in scores[0].reason_codes)
