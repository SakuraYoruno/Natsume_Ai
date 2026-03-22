from __future__ import annotations

import pytest

from edge_client.actuators.tts_engine import _synthesize_audio


class _RetryModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        if len(self.calls) == 1:
            raise OSError("[Errno 22] Invalid argument")
        return {"audio": [0.1, -0.2, 0.05], "sample_rate": 32000}


def test_synthesize_audio_retries_without_prosody_on_invalid_argument() -> None:
    model = _RetryModel()

    audio, sample_rate = _synthesize_audio(
        model=model,
        text="test",
        language="ja",
        emotion_tag="HAPPY",
        emotion_intensity=0.8,
        timeout_s=5.0,
    )

    assert sample_rate == 32000
    assert len(audio) == 3
    assert len(model.calls) == 2
    assert "speed_factor" in model.calls[0]
    assert "energy_factor" in model.calls[0]
    assert "speed_factor" not in model.calls[1]
    assert "energy_factor" not in model.calls[1]


def test_synthesize_audio_raises_non_retryable_exception() -> None:
    class _FailModel:
        def synthesize(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            raise RuntimeError("backend exploded")

    with pytest.raises(RuntimeError):
        _synthesize_audio(
            model=_FailModel(),
            text="test",
            language="ja",
            emotion_tag=None,
            emotion_intensity=0.5,
            timeout_s=5.0,
        )
