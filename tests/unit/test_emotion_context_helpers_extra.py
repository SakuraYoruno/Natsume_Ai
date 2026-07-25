from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from host_brain.app import emotion_context as ec
from host_brain.core.metabolism import EmotionVector


def test_decay_update_and_tag_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    vector = EmotionVector(valence=0.8, arousal=0.6, dominance=0.1, ts_ms=0)
    monkeypatch.setenv("HOST_EMOTION_DECAY_GRACE_MS", "100")
    monkeypatch.setenv("HOST_EMOTION_DECAY_MAX_RATE", "0.8")
    monkeypatch.setenv("HOST_EMOTION_DECAY_WINDOW_MIN", "10")

    assert ec._apply_time_aware_decay(
        vector,
        now_ms=120,
        last_update_ms=50,
    ).valence == pytest.approx(0.8)
    decayed = ec._apply_time_aware_decay(
        vector,
        now_ms=600_000,
        last_update_ms=0,
    )
    assert abs(decayed.valence) < abs(vector.valence)

    runtime = ec.HostEmotionRuntime(clock_ms=lambda: 0, hour_provider=lambda: 12)
    runtime._emotion_vector = EmotionVector(  # noqa: SLF001
        valence=0.4,
        arousal=0.4,
        dominance=0.0,
        ts_ms=0,
    )
    runtime._emotion_vector_last_ts = 0  # noqa: SLF001
    blended, previous, profile = runtime.ingest(
        input_vector=EmotionVector(
            valence=-0.6,
            arousal=0.2,
            dominance=0.0,
            ts_ms=600_000,
        ),
        emotion_tag="SAD",
        emotion_intensity=0.8,
        mood_state="steady",
        emotion_ts_ms=600_000,
    )
    assert blended.ts_ms == 600_000
    assert blended.valence != pytest.approx(0.4)
    assert previous is None
    assert profile["total_interactions"] == 1
    assert ec._normalize_runtime_emotion_tag("comfort") == "CALM"
    assert ec._normalize_runtime_emotion_tag("") is None


def test_normalize_context_advances_only_explicit_host_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Timeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def append(self, **kwargs) -> None:
            self.calls.append(kwargs)

    timeline = _Timeline()
    runtime = ec.HostEmotionRuntime(
        timeline_provider=lambda: timeline,
        clock_ms=lambda: 1000,
        hour_provider=lambda: 12,
    )
    runtime._previous_emotion_tag = "SAD"  # noqa: SLF001
    fake_snapshot = SimpleNamespace(
        allowed_emotion_tags=["CALM", "NEUTRAL"],
        emotion_range_hint="gentle",
        emotion_ts_ms=1111,
        mood_state="steady",
    )
    monkeypatch.setattr(ec, "normalize_hard_state", lambda state: state)
    monkeypatch.setattr(ec, "build_hard_snapshot", lambda _state: fake_snapshot)
    monkeypatch.setattr(
        ec,
        "compute_emotion_echo",
        lambda *_args, **_kwargs: {"response_hint": "soft"},
    )
    monkeypatch.setattr(ec, "evaluate_emotion_triggers", lambda _payload: ["comfort"])

    normalized = ec.normalize_chat_emotion_context(
        {
            "mood_state": "engaged",
            "emotion_tag": "comfort",
            "emotion_intensity": 1.2,
            "emotion_ts_ms": "bad",
        },
        runtime=runtime,
    )
    assert normalized["emotion_tag"] == "CALM"
    assert normalized["emotion_intensity"] == 1.0
    assert normalized["_prev_emotion_tag"] == "SAD"
    assert normalized["emotion_ts_ms"] == 1111
    assert normalized["emotion_echo"]["response_hint"] == "soft"
    assert timeline.calls[0]["emotion_tag"] == "CALM"
    assert normalized["user_emotion_profile"]["total_interactions"] == 1

    before = len(timeline.calls)
    pure = ec.normalize_chat_emotion_context(normalized)
    assert pure["emotion_tag"] == "CALM"
    assert len(timeline.calls) == before

    snapshot = ec.build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="unit_test",
        ts_ms=2000,
    )
    assert snapshot["age_ms"] == 889
    monkeypatch.setattr(
        ec,
        "build_governance_snapshot",
        lambda hard_state, overrides: {
            "governance": True,
            "overrides": overrides,
        },
    )
    governance = ec.build_chat_governance_snapshot(
        emotion_context=normalized,
        ts_ms=2000,
    )
    assert governance["governance"] is True


def _request(
    *,
    message: str,
    context: dict[str, object] | None = None,
    emotion_tag: str | None = None,
    intensity: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        emotion_context=dict(context or {}),
        mood_state=None,
        emotion_tag=emotion_tag,
        emotion_range_hint=None,
        emotion_intensity=intensity,
    )


@pytest.mark.parametrize(
    ("request_body", "expected_tag", "expected_source", "degraded"),
    (
        (
            _request(
                message="今天真的有点难受，不想动了。",
                context={
                    "emotion_tag": "NEUTRAL",
                    "emotion_inference_confidence": 0.1,
                },
            ),
            "SAD",
            "text_bootstrap_fallback",
            False,
        ),
        (
            _request(
                message="控制台一片红，怎么办。",
                context={
                    "emotion_tag": "NEUTRAL",
                    "emotion_inference_confidence": 0.1,
                },
            ),
            "ANXIOUS",
            "text_bootstrap_fallback",
            False,
        ),
        (
            _request(
                message="气死了，真的很烦。",
                emotion_tag="HAPPY",
                intensity=0.6,
            ),
            "HAPPY",
            "explicit",
            False,
        ),
        (
            _request(message="下一步"),
            "NEUTRAL",
            "default_neutral",
            True,
        ),
    ),
)
def test_resolve_chat_emotion_context_uses_exact_runtime(
    request_body: SimpleNamespace,
    expected_tag: str,
    expected_source: str,
    degraded: bool,
) -> None:
    runtime = ec.HostEmotionRuntime()
    normalized = ec.resolve_chat_emotion_context(
        request_body=request_body,
        runtime=runtime,
    )
    assert normalized["emotion_tag"] == expected_tag
    assert normalized["emotion_inference_source"] == expected_source
    assert normalized["emotion_inference_degraded"] is degraded
    assert normalized["user_emotion_profile"]["total_interactions"] == 1


def test_resolve_chat_emotion_context_rejects_missing_runtime() -> None:
    with pytest.raises(RuntimeError, match="HOST_EMOTION_RUNTIME_NOT_INJECTED"):
        ec.resolve_chat_emotion_context(
            request_body=_request(message="你好"),
            runtime=None,  # type: ignore[arg-type]
        )


def test_profile_summary_and_host_owned_diary() -> None:
    assert "开心" in ec._vector_to_inner_monologue(
        EmotionVector(valence=0.8, arousal=0.8, dominance=0.0, ts_ms=0)
    )
    profile = ec.UserEmotionProfile()
    profile.update(emotion_tag="HAPPY", valence=0.7, hour_of_day=9)
    profile.update(emotion_tag="HAPPY", valence=0.2, hour_of_day=10)
    profile.update(emotion_tag="SAD", valence=-0.6, hour_of_day=11)
    summary = profile.to_summary()
    assert summary["top_emotions"][0]["tag"] == "HAPPY"
    assert summary["profile_hint"]

    class _ShortTimeline:
        entry_count = 2

        def append(self, **_kwargs) -> None:
            return None

        def build_daily_diary(self, *, window_ms: int) -> dict[str, object]:
            del window_ms
            return {}

    assert (
        ec.flush_emotion_diary(
            runtime=ec.HostEmotionRuntime(
                timeline_provider=lambda: _ShortTimeline()
            ),
            force=False,
        )
        is None
    )

    class _Timeline:
        entry_count = 6

        def append(self, **_kwargs) -> None:
            return None

        def build_daily_diary(self, *, window_ms: int) -> dict[str, object]:
            del window_ms
            return {
                "diary_text": "今天整体平稳",
                "dominant_emotion": "CALM",
                "avg_valence": 0.2,
                "total_entries": 6,
            }

    class _Store:
        def write_memory(self, **_kwargs):
            return SimpleNamespace(success=True, memory_id="mem-1")

    class _BrokenStore:
        def write_memory(self, **_kwargs):
            raise RuntimeError("boom")

    runtime = ec.HostEmotionRuntime(timeline_provider=lambda: _Timeline())
    with patch("host_brain.memory.episodic.EpisodicStore", _Store):
        ok = ec.flush_emotion_diary(
            runtime=runtime,
            episodic_store=_Store(),
            force=True,
        )
    assert ok and ok["status"] == "OK"
    assert ok["memory_id"] == "mem-1"

    runtime = ec.HostEmotionRuntime(timeline_provider=lambda: _Timeline())
    with patch("host_brain.memory.episodic.EpisodicStore", _BrokenStore):
        failed = ec.flush_emotion_diary(
            runtime=runtime,
            episodic_store=_BrokenStore(),
            force=True,
        )
    assert failed and failed["status"] == "ERROR"
