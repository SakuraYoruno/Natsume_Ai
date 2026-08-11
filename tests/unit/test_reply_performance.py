from __future__ import annotations

from host_brain.input_runtime.reply_performance import ReplyPerformance, normalize_reply_performance


def test_reply_performance_normalizes_fine_galgame_emotion() -> None:
    performance = normalize_reply_performance(
        emotion="hurt_sulk",
        action="look_away",
        intensity=0.73,
        source="unit",
    )

    assert performance.emotion_tag == "SAD"
    assert performance.emotion_intensity == 0.73
    assert performance.action == "look_away"
    assert performance.raw_emotion == "hurt_sulk"


def test_reply_performance_falls_back_to_runtime_facts() -> None:
    performance = normalize_reply_performance(
        facts={
            "affect_state": {"emotion_tag": "ANXIOUS"},
            "emotional_act": {
                "primary_emotion": "gentle_worry",
                "stance": "lean_forward",
                "intensity": 0.61,
            },
        },
        source="facts",
    )

    assert performance.emotion_tag == "ANXIOUS"
    assert performance.emotion_intensity == 0.61
    assert performance.action == "lean_forward"


def test_reply_performance_rejects_unknown_standard_tag_to_neutral() -> None:
    performance = ReplyPerformance(emotion_tag="sleepy-soft", emotion_intensity=2.0)

    assert performance.emotion_tag == "NEUTRAL"
    assert performance.emotion_intensity == 1.0
