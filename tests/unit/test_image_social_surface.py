from __future__ import annotations

from host_brain.persona.image_social_surface import (
    build_emergency_image_social_reply,
    candidate_responds_to_image_social_emotion,
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
    assert is_image_social_surface_sample_disallowed("哥哥，上面看我是不是很可爱啊？")
    assert is_image_social_surface_sample_disallowed("哥哥，你看我这小猫耳朵是不是超可爱呀！")
    assert is_image_social_surface_sample_disallowed("哥哥，你又在看我的猫耳啦？是不是觉得我比这只猫猫还要可爱呀？")
    assert is_image_social_surface_sample_disallowed("哥哥，你是不是觉得这张图里的猫猫很可爱呀？")
    assert is_image_social_surface_sample_disallowed("这张看我可爱的样子，猫耳都竖起来了。")
    assert is_image_social_surface_sample_disallowed("看我今天可爱死了吧。")
    assert is_image_social_surface_sample_disallowed("哎呀，哥哥，是想让我变成猫猫精赖在你身边不走吗？")
    assert is_image_social_surface_sample_disallowed("我今天可爱惨了。")
    assert is_image_social_surface_sample_disallowed("哥哥，人家今天也很可爱哦！")
    assert is_image_social_surface_sample_disallowed("这张图我看到的内容大概是猫耳女孩。")
    assert is_image_social_surface_sample_disallowed("猫耳这张有点犯规，哥哥故意的吧。")
    assert is_image_social_surface_sample_disallowed("哥哥，我也觉得这张猫耳很可爱，轻轻戳到了。")
    assert is_image_social_surface_sample_disallowed("这个表情一脸装乖，太会了。")
    assert is_image_social_surface_sample_disallowed("嗯？这张图......好像有点熟悉，又好像在哪儿见过似的。")


def test_image_social_surface_requires_emotion_response_not_image_commentary() -> None:
    multimodal = {
        "summary_for_chat": "可爱、卖萌；一张动漫风格的猫耳女孩特写图，灰色头发、蓝色眼睛。",
        "core_observation": "猫耳女孩特写，蓝色眼睛。",
        "emotion_signals": ["可爱", "卖萌"],
    }

    anchors = image_social_anchor_terms(multimodal)
    assert "猫耳" not in anchors
    assert {"可爱", "卖萌"}.issubset(set(anchors))
    assert candidate_responds_to_image_social_emotion("好啦好啦，别拿这种眼神晃我，我会心软的。")
    assert candidate_responds_to_image_social_emotion("哥哥又开始装乖了，我接住还不行嘛。")
    assert candidate_responds_to_image_social_emotion("被你这么一晃，我有点招架不住。")
    assert candidate_responds_to_image_social_emotion("撒娇这一套我接住了。")
    assert not candidate_responds_to_image_social_emotion("猫耳这张有点犯规，哥哥故意的吧。")
    assert not candidate_responds_to_image_social_emotion("嗯？这张图......好像有点熟悉，又好像在哪儿见过似的。")
    assert not candidate_responds_to_image_social_emotion("哥哥，我也觉得这张猫耳很可爱。")
    assert not candidate_responds_to_image_social_emotion("哥哥，我也萌生了想捏这张猫耳的冲动。")
    assert not candidate_responds_to_image_social_emotion("哥哥，上面好可爱，轻轻戳到了。")
    assert not candidate_responds_to_image_social_emotion("哥哥，人家今天也很可爱哦！")
    assert not candidate_responds_to_image_social_emotion("哥哥，你看我这小猫耳朵是不是超可爱呀！")
    assert not candidate_responds_to_image_social_emotion("哥哥，你又在看我的猫耳啦？是不是觉得我比这只猫猫还要可爱呀？")
    assert not candidate_responds_to_image_social_emotion("这张看我可爱的样子，猫耳都竖起来了。")
    assert not candidate_responds_to_image_social_emotion("看我今天可爱死了吧。")
    assert not candidate_responds_to_image_social_emotion("哥哥今天怎么这么安静。")
    assert not candidate_responds_to_image_social_emotion("")


def test_image_social_emergency_reply_stays_neutral_degraded() -> None:
    for payload in (
        {"summary_for_chat": "可爱、卖萌；猫耳女孩特写。"},
        {"summary_for_chat": "可爱、卖萌。"},
        {"summary_for_chat": "哭泣，泪，委屈。"},
        {"summary_for_chat": "生气、炸毛。"},
        {"summary_for_chat": "一张普通图片。"},
    ):
        reply = build_emergency_image_social_reply(payload)
        assert reply == ""
        assert "故意拿来晃" not in reply
        assert "先不乱猜" not in reply
