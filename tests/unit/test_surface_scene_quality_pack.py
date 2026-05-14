from __future__ import annotations

import json

from host_brain.persona.surface_scene_quality_pack import (
    DEFAULT_SURFACE_SCENE_QUALITY_PACK_PATH,
    clear_surface_scene_quality_pack_cache,
    get_surface_scene_quality_pack,
    load_surface_scene_quality_pack,
    render_surface_scene_quality_guidance,
    select_surface_scene_quality_sample,
    surface_scene_quality_memory_ab_case_payloads,
    surface_scene_quality_pack_payload,
)


def test_surface_scene_quality_pack_has_named_sources() -> None:
    samples = get_surface_scene_quality_pack()

    assert DEFAULT_SURFACE_SCENE_QUALITY_PACK_PATH.exists()
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


def test_surface_scene_quality_pack_payload_is_json_data_backed() -> None:
    payload = surface_scene_quality_pack_payload()

    assert payload["sample_kind"] == "surface_scene_quality_pack"
    assert len(payload["scenes"]) >= 8
    assert {scene["scene_id"] for scene in payload["scenes"]} >= {"short_continue", "return_chat"}


def test_surface_scene_quality_pack_can_be_overridden(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    pack_path = tmp_path / "surface_scene_quality_pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sample_kind": "surface_scene_quality_pack",
                "scenes": [
                    {
                        "scene_id": "short_continue",
                        "title": "短继续 override",
                        "user_markers": ["继续"],
                        "required_move": "只短接。",
                        "scoring_focus": ["naturalness"],
                        "good_examples": ["行，继续。"],
                        "bad_examples": ["继续主链。"],
                        "should_not_surface": ["主链"],
                        "source": "test_override",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_SURFACE_SCENE_QUALITY_PACK_PATH", str(pack_path))
    clear_surface_scene_quality_pack_cache()

    samples = get_surface_scene_quality_pack()

    assert len(samples) == 1
    assert samples[0].scene_id == "short_continue"
    assert samples[0].good_examples == ("行，继续。",)
    assert load_surface_scene_quality_pack(pack_path)[0].source == "test_override"


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
