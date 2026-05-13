from __future__ import annotations

from host_brain.app.context.affect_state import AffectStateBlock
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
    build_surface_input_with_affect_state,
)


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


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
    assert query.relationship_stage == "familiar"


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


def test_affect_control_prompt_guidance_understands_steady_helpful_alias() -> None:
    packet = affect_control_packet_from_mapping(
        {
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar_not_intimate",
            "expression_intensity": 0.3,
        }
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="控制台一片红，我现在只想知道先看哪一条",
            task_mode_hint="technical_help",
            response_move_hint="triage_first_step",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_steady_alias",
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))
    query = build_style_reference_query(request)

    assert "care" in query.emotion_tags
    assert "take_charge" in query.style_moves
    assert query.relationship_stage == "familiar"
    assert "技术或推进场景里，情绪只做稳定器" in rendered
    assert "不要恋人式承诺或占有表达" in rendered


def test_affect_state_block_compresses_to_surface_controls_without_raw_state() -> None:
    affect = AffectStateBlock(
        emotion_tag="ANXIOUS",
        arousal=0.82,
        stability=0.34,
        social_energy=0.24,
        warmth_bias=0.22,
        withdraw_bias=0.58,
        irritation_bias=0.05,
        carryover_turns=3,
    )
    request = build_surface_input_with_affect_state(
        ThreeLayerContinuityContext(
            current_user_message="今天不太想说话",
            task_mode_hint="daily_chat",
            question_budget=0,
        ),
        emotion_state=affect,
        bond_summary={"relationship_distance": "familiar_not_intimate"},
        relationship_clues=["熟人私聊，不升级亲密。"],
        soul_md=SOUL,
        case_id="affect_state_quiet",
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert request.control.emotional_tone == "quiet_care"
    assert request.control.relationship_distance == "familiar_not_intimate"
    assert "当前显影：收着说，短一点，少追问。" in rendered
    assert "withdraw_bias" not in rendered
    assert "carryover_turns" not in rendered


def test_technical_affect_state_keeps_usefulness_above_mood_flavor() -> None:
    packet = build_surface_affect_control_packet(
        emotion_state=AffectStateBlock(
            emotion_tag="ANXIOUS",
            arousal=0.9,
            stability=0.2,
            social_energy=0.5,
            withdraw_bias=0.2,
        ),
        bond_summary={"relationship_distance": "familiar_not_intimate"},
        task_mode="technical_help",
    )

    assert packet.emotional_tone == "steady_helpful"
    assert packet.expression_intensity <= 0.42
    assert "不要只安慰不解决" in packet.must_not_do


def test_boundary_repair_affect_state_respects_distance_before_hurt_flavor() -> None:
    packet = build_surface_affect_control_packet(
        emotion_state=AffectStateBlock(
            emotion_tag="HURT",
            arousal=0.62,
            stability=0.5,
        ),
        bond_summary={"relationship_distance": "polite_warm"},
        task_mode="boundary_repair",
    )

    assert packet.emotional_tone == "guarded_soft"
    assert packet.relationship_distance == "polite_warm"
    assert "不要强行亲近" in packet.must_not_do


def test_steady_helpful_affect_prefers_action_over_comfort_only() -> None:
    packet = affect_control_packet_from_mapping(
        {
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar_not_intimate",
        }
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="控制台一片红，我现在只想知道先看哪一条",
            task_mode_hint="technical_help",
            response_move_hint="triage_first_step",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_steady_action",
    )
    scores = score_isolated_surface_candidates(
        request,
        [
            "别急，我在这，慢慢来。",
            "先看第一条红字，把它贴出来。",
        ],
    )
    selected = select_best_isolated_candidate(scores)

    assert selected is not None
    assert selected.text == "先看第一条红字，把它贴出来。"
    assert any("AFFECT_STEADY_HELPFUL_LACKS_ACTION" in code for code in scores[0].reason_codes)
    assert any("AFFECT_STEADY_ACTIONABLE" in code for code in scores[1].reason_codes)


def test_guilty_affect_prefers_light_admission_over_denial() -> None:
    packet = affect_control_packet_from_mapping(
        {
            "emotional_tone": "slightly_guilty",
            "relationship_distance": "familiar_not_intimate",
        }
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="你刚刚是不是又走神了",
            task_mode_hint="daily_chat",
            response_move_hint="repair_presence",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_guilty_presence",
    )
    scores = score_isolated_surface_candidates(
        request,
        [
            "才没有！我明明在认真听你说话。",
            "嗯，慢了一拍，现在在听。",
        ],
    )
    selected = select_best_isolated_candidate(scores)

    assert selected is not None
    assert selected.text == "嗯，慢了一拍，现在在听。"
    assert any("AFFECT_GUILTY_DENIAL" in code for code in scores[0].reason_codes)
    assert any("AFFECT_GUILTY_GROUNDED" in code for code in scores[1].reason_codes)


def test_non_romantic_relationship_distance_blocks_romantic_overreach() -> None:
    packet = affect_control_packet_from_mapping(
        {
            "emotional_tone": "warm_gentle",
            "relationship_distance": "close_but_not_romantic",
        }
    )
    request = build_surface_input_with_affect_control(
        ThreeLayerContinuityContext(
            current_user_message="今天有点难受",
            task_mode_hint="emotional_support",
            response_move_hint="soft_containment",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="affect_non_romantic",
    )
    scores = score_isolated_surface_candidates(
        request,
        [
            "没关系呀，我会永远陪着你。",
            "嗯，先缓一下，我在。",
        ],
    )
    selected = select_best_isolated_candidate(scores)

    assert not scores[0].passed
    assert selected is not None
    assert selected.text == "嗯，先缓一下，我在。"
    assert any("RELATION_NON_ROMANTIC_OVERSTEP" in code for code in scores[0].reason_codes)
