from __future__ import annotations

from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    normalize_chat_emotion_context,
)


def test_normalize_chat_emotion_context_clamps_intensity() -> None:
    normalized = normalize_chat_emotion_context(
        {
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 1.7,
            "emotion_ts_ms": 1000,
        }
    )
    assert normalized.get("emotion_tag") == "HAPPY"
    assert float(normalized.get("emotion_intensity") or 0.0) == 1.0


def test_build_chat_emotion_snapshot_includes_age_and_context() -> None:
    snapshot = build_chat_emotion_snapshot(
        emotion_context={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.83,
            "emotion_ts_ms": 1200,
        },
        source="unit_test",
        ts_ms=2000,
    )
    assert snapshot.get("version") == "chat_emotion_snapshot.v1"
    assert snapshot.get("source") == "unit_test"
    assert int(snapshot.get("age_ms") or 0) == 800
    emotion_context = dict(snapshot.get("emotion_context") or {})
    assert emotion_context.get("emotion_tag") == "HAPPY"
    assert float(emotion_context.get("emotion_intensity") or 0.0) == 0.83


def test_normalize_chat_emotion_context_accepts_semantic_aliases() -> None:
    sadness = normalize_chat_emotion_context(
        {
            "mood_state": "steady",
            "emotion_tag": "sadness",
            "emotion_ts_ms": 1000,
        }
    )
    comfort = normalize_chat_emotion_context(
        {
            "mood_state": "steady",
            "emotion_tag": "comfort",
            "emotion_ts_ms": 1000,
        }
    )

    assert sadness.get("emotion_tag") == "SAD"
    assert comfort.get("emotion_tag") == "CALM"
