from __future__ import annotations

from host_brain.channels.vts_runtime_mapping import (
    load_runtime_emotion_mapping,
    normalize_runtime_emotion_tag,
    render_runtime_emotion_tag_guidance,
    runtime_emotion_expression_id,
    supported_runtime_emotion_tags,
)


def test_runtime_emotion_mapping_reads_project_vts_config() -> None:
    mapping = load_runtime_emotion_mapping()

    assert mapping["LOVING"] == "HK_Love"
    assert mapping["EMBARRASSED"] == "HK_Shy"
    assert "LOVING" in supported_runtime_emotion_tags()


def test_runtime_emotion_tag_normalizes_aliases_against_mapping() -> None:
    mapping = {
        "NEUTRAL": "reset",
        "EMBARRASSED": "hk-shy",
        "LOVING": "hk-love",
    }

    assert normalize_runtime_emotion_tag("guilty", mapping=mapping) == "EMBARRASSED"
    assert normalize_runtime_emotion_tag("love", mapping=mapping) == "LOVING"
    assert normalize_runtime_emotion_tag("angry", mapping=mapping) == "NEUTRAL"
    assert runtime_emotion_expression_id("love", mapping=mapping) == "hk-love"


def test_runtime_emotion_guidance_lists_live2d_mapping() -> None:
    rendered = render_runtime_emotion_tag_guidance()

    assert "VTS Live2D EmotionTag" in rendered
    assert "natsume_emotion_tag" in rendered
    assert "- LOVING: HK_Love" in rendered
