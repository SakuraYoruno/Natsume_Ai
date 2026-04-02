from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from host_brain.app import emotion_context as ec
from host_brain.core.metabolism import EmotionVector


@pytest.fixture(autouse=True)
def _reset_emotion_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ec, "_global_emotion_vector", None)
    monkeypatch.setattr(ec, "_global_emotion_vector_last_ts", 0)
    monkeypatch.setattr(ec, "_global_prev_emotion_tag", None)
    monkeypatch.setattr(ec, "_last_diary_flush_ts", 0)
    monkeypatch.setattr(ec, "_user_emotion_profile", None)
    yield


def test_decay_update_and_tag_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    vector = EmotionVector(valence=0.8, arousal=0.6, dominance=0.1, ts_ms=0)
    monkeypatch.setenv("HOST_EMOTION_DECAY_GRACE_MS", "100")
    monkeypatch.setenv("HOST_EMOTION_DECAY_MAX_RATE", "0.8")
    monkeypatch.setenv("HOST_EMOTION_DECAY_WINDOW_MIN", "10")

    ec._global_emotion_vector_last_ts = 50
    assert ec._apply_time_aware_decay(vector, now_ms=120).valence == pytest.approx(0.8)

    ec._global_emotion_vector_last_ts = 0
    decayed = ec._apply_time_aware_decay(vector, now_ms=600_000)
    assert abs(decayed.valence) < abs(vector.valence)

    ec._global_emotion_vector = EmotionVector(valence=0.4, arousal=0.4, dominance=0.0, ts_ms=0)
    ec._global_emotion_vector_last_ts = 0
    blended = ec._update_persistent_emotion(
        EmotionVector(valence=-0.6, arousal=0.2, dominance=0.0, ts_ms=600_000),
        now_ms=600_000,
    )
    assert blended.ts_ms == 600_000
    assert blended.valence != pytest.approx(0.4)
    assert ec._normalize_runtime_emotion_tag("comfort") == "CALM"
    assert ec._normalize_runtime_emotion_tag("") is None


def test_normalize_context_and_snapshot_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeTimeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def append(self, **kwargs) -> None:
            self.calls.append(kwargs)

    fake_timeline = _FakeTimeline()
    fake_snapshot = SimpleNamespace(
        allowed_emotion_tags=["CALM", "NEUTRAL"],
        emotion_range_hint="gentle",
        emotion_ts_ms=1111,
        mood_state="steady",
    )
    fake_profile = SimpleNamespace(to_summary=lambda: {"status": "ACTIVE", "total_interactions": 1})
    profile_updates: list[dict[str, object]] = []

    monkeypatch.setattr(ec, "normalize_hard_state", lambda state: state)
    monkeypatch.setattr(ec, "build_hard_snapshot", lambda state: fake_snapshot)
    monkeypatch.setattr(ec, "_update_persistent_emotion", lambda vec, now_ms: vec)
    monkeypatch.setattr(ec, "_global_emotion_timeline", fake_timeline)
    monkeypatch.setattr(ec, "compute_emotion_echo", lambda *args, **kwargs: {"response_hint": "soft"})
    monkeypatch.setattr(ec, "evaluate_emotion_triggers", lambda payload: ["comfort"])
    monkeypatch.setattr(ec, "get_user_emotion_profile", lambda: fake_profile)
    monkeypatch.setattr(
        ec,
        "update_user_emotion_profile",
        lambda **kwargs: profile_updates.append(kwargs),
    )
    monkeypatch.setattr(ec, "_global_prev_emotion_tag", "SAD")

    normalized = ec.normalize_chat_emotion_context(
        {
            "mood_state": "engaged",
            "emotion_tag": "comfort",
            "emotion_intensity": 1.2,
            "emotion_ts_ms": "bad",
        }
    )

    assert normalized["emotion_tag"] == "CALM"
    assert normalized["emotion_intensity"] == 1.0
    assert normalized["_prev_emotion_tag"] == "SAD"
    assert normalized["emotion_ts_ms"] == 1111
    assert normalized["emotion_echo"]["response_hint"] == "soft"
    assert fake_timeline.calls and fake_timeline.calls[0]["emotion_tag"] == "CALM"
    assert profile_updates and profile_updates[0]["emotion_tag"] == "CALM"

    snapshot = ec.build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="unit_test",
        ts_ms=2000,
    )
    assert snapshot["age_ms"] == 889
    assert snapshot["emotion_context"]["emotion_tag"] == "CALM"

    monkeypatch.setattr(ec, "build_governance_snapshot", lambda hard_state, overrides: {"governance": True, "overrides": overrides})
    governance = ec.build_chat_governance_snapshot(emotion_context=normalized, ts_ms=2000)
    assert governance["governance"] is True
    assert governance["emotion_snapshot"]["source"] == "governance_snapshot"

    monkeypatch.setattr(ec, "normalize_chat_emotion_context", lambda raw: {"normalized": raw["emotion_tag"]})
    request_body = SimpleNamespace(
        emotion_context={"mood_state": "steady"},
        mood_state="engaged",
        emotion_tag="HAPPY",
        emotion_range_hint="soft",
        emotion_intensity=0.3,
    )
    assert ec.resolve_chat_emotion_context(request_body=request_body) == {"normalized": "HAPPY"}


def test_inner_monologue_profile_summary_and_flush_diary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "开心" in ec._vector_to_inner_monologue(EmotionVector(valence=0.8, arousal=0.8, dominance=0.0, ts_ms=0))
    assert "难受" in ec._vector_to_inner_monologue(EmotionVector(valence=-0.8, arousal=0.7, dominance=0.0, ts_ms=0))
    assert "平静" in ec._vector_to_inner_monologue(EmotionVector(valence=0.0, arousal=-0.4, dominance=0.0, ts_ms=0))

    assert ec._coerce_non_negative_int("bad", default=5) == 5
    assert ec._coerce_emotion_intensity(2.0, default=0.5) == 1.0
    assert ec._coerce_emotion_intensity(-1.0, default=0.5) == 0.0

    profile = ec.UserEmotionProfile()
    profile.update(emotion_tag="HAPPY", valence=0.7, hour_of_day=9)
    profile.update(emotion_tag="HAPPY", valence=0.2, hour_of_day=10)
    profile.update(emotion_tag="SAD", valence=-0.6, hour_of_day=11)
    summary = profile.to_summary()
    assert summary["status"] == "ACTIVE"
    assert summary["top_emotions"][0]["tag"] == "HAPPY"
    assert summary["profile_hint"]

    ec.update_user_emotion_profile(emotion_tag="CALM", valence=0.1, hour_of_day=12)
    assert ec.get_user_emotion_profile().to_summary()["total_interactions"] == 1

    class _ShortTimeline:
        entry_count = 2

        def build_daily_diary(self, window_ms: int) -> dict[str, object]:
            return {}

    monkeypatch.setattr(ec, "_global_emotion_timeline", _ShortTimeline())
    assert ec.flush_emotion_diary(force=False) is None

    class _Timeline:
        entry_count = 6

        def build_daily_diary(self, window_ms: int) -> dict[str, object]:
            return {
                "diary_text": "今天整体平稳",
                "dominant_emotion": "CALM",
                "avg_valence": 0.2,
                "total_entries": 6,
            }

    class _Store:
        def write_memory(self, **kwargs):
            return SimpleNamespace(success=True, memory_id="mem-1")

    class _BrokenStore:
        def write_memory(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(ec, "_global_emotion_timeline", _Timeline())
    with patch("host_brain.memory.episodic.EpisodicStore", _Store):
        ok = ec.flush_emotion_diary(episodic_store=_Store(), force=True)
    assert ok["status"] == "OK"
    assert ok["memory_id"] == "mem-1"

    monkeypatch.setattr(ec, "_global_emotion_timeline", _Timeline())
    with patch("host_brain.memory.episodic.EpisodicStore", _BrokenStore):
        failed = ec.flush_emotion_diary(episodic_store=_BrokenStore(), force=True)
    assert failed["status"] == "ERROR"
