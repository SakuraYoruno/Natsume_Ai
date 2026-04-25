from __future__ import annotations

from host_brain.persona.image_social_surface import (
    build_emergency_image_social_reply,
    candidate_uses_image_observation,
    image_social_anchor_terms,
    is_image_social_surface_sample_disallowed,
)


def test_image_social_surface_rejects_old_template_shapes() -> None:
    assert is_image_social_surface_sample_disallowed("")
    assert is_image_social_surface_sample_disallowed("嗯")
    assert is_image_social_surface_sample_disallowed("哥哥，突然这么可爱，是想让我哄你吗？")
    assert is_image_social_surface_sample_disallowed("突然这么可爱，是想跟我撒娇吗？")
    assert is_image_social_surface_sample_disallowed("怎么啦，突然发这个给我看呀？")
    assert is_image_social_surface_sample_disallowed("怎么了，哥哥？")
    assert is_image_social_surface_sample_disallowed("哥哥是想让我看看吗？")
    assert is_image_social_surface_sample_disallowed("哥哥，你看我今天可爱吗？")
    assert is_image_social_surface_sample_disallowed("这张图我看到的内容大概是猫耳女孩。")
    assert not is_image_social_surface_sample_disallowed("猫耳这张有点犯规，哥哥故意的吧。")


def test_image_social_surface_requires_visible_observation_anchor() -> None:
    multimodal = {
        "summary_for_chat": "可爱、卖萌；一张动漫风格的猫耳女孩特写图，灰色头发、蓝色眼睛。",
        "core_observation": "猫耳女孩特写，蓝色眼睛。",
        "emotion_signals": ["可爱", "卖萌"],
    }

    assert "猫耳" in image_social_anchor_terms(multimodal)
    assert candidate_uses_image_observation("猫耳这张有点犯规，哥哥故意的吧。", multimodal)
    assert candidate_uses_image_observation("这张有点犯规，哥哥故意的吧。", multimodal)
    assert candidate_uses_image_observation("蓝眼睛这下太犯规了。", multimodal)
    assert not candidate_uses_image_observation("哥哥今天怎么这么安静。", multimodal)
    assert not candidate_uses_image_observation("", multimodal)
    assert candidate_uses_image_observation("这张有点犯规，哥哥故意的吧。", {"summary_for_chat": "一张图片。"})
    assert not candidate_uses_image_observation("哥哥故意的吧。", {"summary_for_chat": "一张图片。"})


def test_image_social_emergency_reply_is_observation_anchored() -> None:
    for payload in (
        {"summary_for_chat": "可爱、卖萌；猫耳女孩特写。"},
        {"summary_for_chat": "可爱、卖萌。"},
        {"summary_for_chat": "哭泣，泪，委屈。"},
        {"summary_for_chat": "生气、炸毛。"},
        {"summary_for_chat": "一张普通图片。"},
    ):
        reply = build_emergency_image_social_reply(payload)
        assert "图我看到了" in reply
        assert "故意拿来晃" not in reply
        assert "先不乱猜" not in reply
