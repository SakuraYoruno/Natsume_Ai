from __future__ import annotations

import json

import pytest

from host_brain.persona.organ_influence_contract import (
    CONTRACT_FIELDS,
    MAIN_CHAIN_EFFECTS,
    build_organ_influence_packet,
    organ_influence_contract_table,
    organ_influence_packet_from_mapping,
    organ_main_chain_integration_table,
)
from host_brain.organ_ops import build_default_organ_registry
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS


def test_organ_influence_contract_table_covers_23_organs() -> None:
    rows = organ_influence_contract_table()
    organ_ids = {row["organ_id"] for row in rows}

    assert len(SURFACE_ORGAN_IDS) == 23
    assert organ_ids == set(SURFACE_ORGAN_IDS)
    assert all(row["prompt_visibility"] == "contract_only" for row in rows)
    assert all(row["allowed_fields"] for row in rows)
    assert all(set(row["allowed_fields"]) <= set(CONTRACT_FIELDS) for row in rows)


def test_organ_main_chain_integration_table_covers_current_contract() -> None:
    contracts = {row["organ_id"]: row for row in organ_influence_contract_table()}
    rows = organ_main_chain_integration_table()
    organ_ids = {row["organ_id"] for row in rows}

    assert organ_ids == set(SURFACE_ORGAN_IDS)
    assert all(row["prompt_visibility"] == "contract_only" for row in rows)
    assert all(row["contract_fields"] == contracts[row["organ_id"]]["allowed_fields"] for row in rows)
    assert all("surface_text" in row["main_chain_effects"] for row in rows)
    assert all(set(row["main_chain_effects"]) <= set(MAIN_CHAIN_EFFECTS) for row in rows)
    assert all(str(row["role_summary"]).strip() for row in rows)


def test_organ_main_chain_integration_table_matches_runtime_registry() -> None:
    registry_ids = {row["organ_id"] for row in build_default_organ_registry().list_organs()}
    integration_ids = {row["organ_id"] for row in organ_main_chain_integration_table()}

    assert integration_ids == registry_ids == set(SURFACE_ORGAN_IDS)


def test_organ_main_chain_integration_effects_match_contract_fields() -> None:
    rows = organ_main_chain_integration_table()
    required_effect_by_field = {
        "emotion_delta": "emotion",
        "relationship_delta": "relationship",
        "memory_candidates": "memory",
        "inner_world_seed": "inner_world",
        "delivery_delta": "delivery",
        "surface_constraints": "safety",
        "task_delta": "task",
    }

    for row in rows:
        fields = set(row["contract_fields"])
        effects = set(row["main_chain_effects"])
        for field_name, effect in required_effect_by_field.items():
            if field_name in fields:
                assert effect in effects, row["organ_id"]


def test_build_organ_influence_packet_keeps_raw_context_out() -> None:
    packet = build_organ_influence_packet(
        {
            "emotion_context": {"emotion_tag": "HURT", "emotion_intensity": 0.7},
            "bond_context": {
                "stage": "CONFIDANT",
                "bond_vector": {"intimacy": 0.86, "trust": 0.8},
            },
            "memory_layers": {
                "recent_summary_present": True,
                "episodic_available": True,
                "semantic_available": True,
                "recent_memory_count": 3,
            },
            "default_mode_state": {
                "thought_stream_active": True,
                "self_directive": {
                    "self_identity": "current_soul",
                    "style_anchor": ["自我连续性：像同一个人持续在场。"],
                },
            },
            "raw_organ_outputs": {"limbic": {"messages": ["RAW SHOULD NOT PASS"]}},
        }
    )
    payload = packet.as_dict()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["emotion_delta"]["emotional_tone"] == "soft_containment"
    assert payload["relationship_delta"]["relationship_distance"] == "close"
    assert "情景记忆层可用于事实召回。" in payload["memory_candidates"]
    assert any("自我连续性" in item for item in payload["inner_world_seed"])
    assert payload["organ_trace"]["contract_version"] == 1
    assert payload["organ_trace"]["dropped_raw_field_count"] >= 1
    assert "RAW SHOULD NOT PASS" not in rendered


def test_organ_influence_packet_rejects_raw_context_mapping() -> None:
    with pytest.raises(ValueError, match="forbidden raw context"):
        organ_influence_packet_from_mapping(
            {
                "emotion_delta": {"emotional_tone": "soft_containment"},
                "raw_organ_outputs": {"limbic": {"messages": ["nope"]}},
            }
        )
