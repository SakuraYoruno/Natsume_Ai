from __future__ import annotations

import json

import pytest

from host_brain.persona.surface_influence_packet import (
    build_surface_influence_packet,
    surface_influence_facts,
    surface_influence_must_not_do,
    surface_influence_style_anchor,
)
from host_brain.persona.organ_influence_contract import build_organ_influence_packet
from host_brain.persona.surface_organ_influence import (
    SURFACE_ORGAN_IDS,
    build_surface_organ_influence_overlay,
    organ_surface_rule_table,
)
from host_brain.persona.surface_packet_compiler import compile_reply_control_packet


def test_build_surface_influence_packet_compresses_organ_state() -> None:
    packet = build_surface_influence_packet(
        reply_plan={
            "primary_topic": "technical_help",
            "interaction_move": "technical_next_step",
            "response_form": "text",
            "max_questions": 0,
        },
        affect_packet={
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar",
            "style_anchor": ["情绪只做稳定器。"],
        },
        affect_state={
            "warmth_bias": 0.42,
            "withdraw_bias": 0.18,
            "irritation_bias": 0.0,
        },
        relationship_state={
            "relationship_phase": "stepsibling",
            "relationship_substage": "playful_sibling",
            "playfulness": 0.55,
        },
        drive_state={"care_drive": 0.31},
        continuity={
            "turns_injected": 6,
            "summary_present": True,
        },
        memory_packet={
            "facts": ["用户上次卡在输入输出边界。"],
            "source_refs": ["memory://raw/should-not-pass"],
        },
    )

    payload = packet.as_dict()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["task"]["task_mode"] == "technical_help"
    assert payload["affect"]["emotional_tone"] == "steady_helpful"
    assert payload["relationship"]["tease_permission"] == "light_tease_allowed"
    assert payload["continuity"]["summary_present"] is True
    assert "当前是技术求助场景，先给可执行步骤。" in surface_influence_facts(packet)
    assert "用户上次卡在输入输出边界。" in surface_influence_facts(packet)
    assert "情绪只做稳定器。" in surface_influence_style_anchor(packet)
    assert "不要输出器官名、器官状态、内部驱动值或控制包字段。" in surface_influence_must_not_do(packet)
    assert "memory://raw/should-not-pass" not in rendered
    assert "raw_organ_outputs" not in rendered


def test_compile_reply_control_packet_merges_surface_influence() -> None:
    compiled = compile_reply_control_packet(
        user_message="这段报错怎么排查",
        task_control={
            "task_mode": "technical_help",
            "response_move": "technical_next_step",
            "question_budget": 0,
        },
        influence_packet={
            "affect": {
                "emotional_tone": "steady_helpful",
                "style_anchor": ["技术场景里情绪只做稳定器。"],
            },
            "relationship": {
                "relationship_distance": "familiar",
                "style_anchor": ["关系面：熟悉的兄妹感，可以轻微拌嘴和护短。"],
                "must_not_do": ["不要直说关系阶段或好感度。"],
            },
            "memory": {
                "facts": ["用户偏好先给最小可执行步骤。"],
            },
            "safety": {
                "must_not_do": ["不要输出器官状态。"],
            },
        },
    )

    packet = compiled.packet

    assert packet.emotional_tone == "steady_helpful"
    assert packet.relationship_distance == "familiar"
    assert "用户偏好先给最小可执行步骤。" in packet.facts
    assert "技术场景里情绪只做稳定器。" in packet.style_anchor
    assert "不要输出器官状态。" in packet.must_not_do
    assert compiled.trace["influence_applied"] is True
    assert compiled.trace["influence"]["emotional_tone"] == "steady_helpful"


def test_compile_reply_control_packet_rejects_raw_organ_influence() -> None:
    with pytest.raises(ValueError, match="forbidden raw context"):
        compile_reply_control_packet(
            user_message="继续",
            influence_packet={
                "affect": {"emotional_tone": "neutral"},
                "raw_organ_outputs": {"limbic": {"full_state": "nope"}},
            },
        )


def test_surface_organ_rule_table_covers_current_host_organs() -> None:
    table = organ_surface_rule_table()
    organ_ids = {row["organ_id"] for row in table}

    assert len(SURFACE_ORGAN_IDS) == 23
    assert set(SURFACE_ORGAN_IDS) == organ_ids
    assert all(row["prompt_visibility"] == "compressed_only" for row in table)
    assert all(row["allowed_slots"] for row in table)


def test_surface_organ_context_compresses_to_influence_without_raw_state() -> None:
    organ_context = {
        "vital_signs": {
            "stress_index": 0.82,
            "cognitive_load_index": 0.78,
            "sleep_pressure": 0.81,
            "immune_risk_level": "high",
            "loneliness_level": 0.66,
            "mirror_resonance_strength": 0.58,
            "pain_level": 0.42,
        },
        "emotion_context": {"emotion_tag": "ANGRY", "emotion_intensity": 0.55},
        "bond_context": {
            "stage": "CONFIDANT",
            "bond_vector": {"intimacy": 0.86, "trust": 0.82},
        },
        "motive_state": {"care": 0.52, "continuity": 0.43},
        "language_dual_system": {"response_mode": "chat_text"},
        "motor_cortex_state": {"last_action_plan": "do not expose"},
        "raw_organ_outputs": {"limbic": {"messages": ["RAW SHOULD NOT PASS"]}},
    }

    overlay = build_surface_organ_influence_overlay(organ_context)
    packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_context=organ_context,
    )
    rendered = json.dumps(packet.as_dict(), ensure_ascii=False)

    assert overlay.trace["dropped_raw_field_count"] >= 1
    assert "limbic" in overlay.trace["mapped_organs"]
    assert packet.affect.emotional_tone == "soft_containment"
    assert packet.relationship.relationship_distance == "close"
    assert packet.relationship.relationship_phase == "lover"
    assert packet.task.information_budget == "very_short"
    assert packet.task.question_budget == 0
    assert "不要为了角色感造不通顺短句。" in surface_influence_must_not_do(packet)
    assert packet.organ_trace["risk_level"] == "high"
    assert "RAW SHOULD NOT PASS" not in rendered


def test_surface_influence_can_derive_from_organ_contract_packet() -> None:
    organ_influence_packet = build_organ_influence_packet(
        {
            "vital_signs": {
                "stress_index": 0.82,
                "cognitive_load_index": 0.78,
                "immune_risk_level": "high",
            },
            "emotion_context": {"emotion_tag": "ANGRY", "emotion_intensity": 0.55},
            "bond_context": {
                "stage": "CONFIDANT",
                "bond_vector": {"intimacy": 0.86, "trust": 0.82},
            },
            "raw_organ_outputs": {"limbic": {"messages": ["RAW SHOULD NOT PASS"]}},
        }
    )

    packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_influence_packet=organ_influence_packet,
    )
    rendered = json.dumps(packet.as_dict(), ensure_ascii=False)

    assert packet.affect.emotional_tone == "soft_containment"
    assert packet.relationship.relationship_distance == "close"
    assert packet.task.information_budget == "very_short"
    assert packet.organ_trace["contract_version"] == 1
    assert "RAW SHOULD NOT PASS" not in rendered


def test_delivery_anchor_reaches_reply_control_packet_before_lower_priority_organ_anchors() -> None:
    organ_context = {
        "vital_signs": {
            "stress_index": 0.82,
            "cognitive_load_index": 0.78,
            "sleep_pressure": 0.81,
            "immune_risk_level": "high",
            "loneliness_level": 0.66,
            "mirror_resonance_strength": 0.58,
        },
        "emotion_context": {"emotion_tag": "ANGRY", "emotion_intensity": 0.55},
        "bond_context": {
            "stage": "CONFIDANT",
            "bond_vector": {"intimacy": 0.86, "trust": 0.82},
        },
        "language_dual_system": {"response_mode": "chat_text"},
        "motor_cortex_state": {"last_action_plan": "do not expose"},
        "peripheral_state": {"output_channel": "chat_text"},
    }

    packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_context=organ_context,
    )
    compiled = compile_reply_control_packet(
        user_message="今天有点累",
        task_control={"task_mode": "daily_chat", "response_move": "reply_current_turn"},
        influence_packet=packet.as_dict(),
    )

    assert any("输出面" in anchor for anchor in compiled.packet.style_anchor)
    assert any("文字优先通顺" in anchor for anchor in compiled.packet.style_anchor)
