from __future__ import annotations

from host_brain.persona.surface_scene_quality_pack import (
    get_surface_scene_quality_pack,
    render_surface_scene_quality_guidance,
    select_surface_scene_quality_sample,
    surface_scene_quality_memory_ab_case_payloads,
)


def test_surface_scene_quality_pack_has_named_sources() -> None:
    samples = get_surface_scene_quality_pack()

    assert len(samples) >= 8
    assert {sample.scene_id for sample in samples} >= {
        "technical_next_step",
        "short_continue",
        "relationship_memory_reassurance",
        "negative_feedback",
    }
    assert all(sample.source for sample in samples)
    assert all(sample.good_examples for sample in samples)
    assert all(sample.bad_examples for sample in samples)


def test_scene_quality_selector_prefers_relationship_memory_reassurance() -> None:
    sample = select_surface_scene_quality_sample(
        user_message="你是不是不记得我了",
        task_mode="relationship_boundary",
        response_move="relationship_reassure",
    )

    assert sample is not None
    assert sample.scene_id == "relationship_memory_reassurance"
    assert "记得啊，别瞎想。" in sample.good_examples
    assert "熟人之间还问这种话，我记着呢。" in sample.bad_examples


def test_scene_quality_prompt_guidance_keeps_sources_out_of_runtime_prompt() -> None:
    guidance = render_surface_scene_quality_guidance(
        user_message="你忘了我吗",
        task_mode="relationship_memory_reassurance",
        response_move="relationship_reassure",
    )

    assert "## 场景质量样本" in guidance
    assert "scene_id: relationship_memory_reassurance" in guidance
    assert "good_examples:" in guidance
    assert "bad_examples:" in guidance
    assert "记得啊，别瞎想。" in guidance
    assert "source_note" not in guidance
    assert "evd://" not in guidance


def test_scene_quality_memory_ab_payloads_expand_beyond_smoke_set() -> None:
    payloads = surface_scene_quality_memory_ab_case_payloads()

    assert len(payloads) >= 12
    assert all(payload["scene_id"] for payload in payloads)
    assert all(payload["case_id"] for payload in payloads)
    assert {payload["case_id"] for payload in payloads} >= {
        "short_ack_plain",
        "relationship_doubt_direct",
        "negative_feedback_grammar",
    }
