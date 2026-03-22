from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from edge_client.senses.asr import ASR_VAD_NO_SPEECH, AsrConfig, AsrState, WhisperModelManager


def _info() -> SimpleNamespace:
    return SimpleNamespace(language="ja", language_probability=0.99, duration=1.2)


def _segment() -> SimpleNamespace:
    return SimpleNamespace(
        text=" hello ",
        start=0.0,
        end=1.2,
        avg_log_prob=-0.2,
        words=[],
    )


def test_vad_empty_retries_without_vad_when_peak_is_high() -> None:
    mgr = WhisperModelManager(
        AsrConfig(
            device="cpu",
            vad_retry_without_filter_on_empty=True,
            vad_retry_min_peak=0.01,
        )
    )
    mgr._state = AsrState.READY
    mgr._model = MagicMock()
    mgr._model.transcribe.side_effect = [
        ([], _info()),
        ([_segment()], _info()),
    ]

    result = mgr.transcribe([0.02, -0.03, 0.01], vad_filter=True)

    assert result.success is True
    assert result.reason_code is None
    assert len(result.segments) == 1
    assert "after no-VAD retry" in result.detail
    assert mgr._model.transcribe.call_count == 2
    assert mgr._model.transcribe.call_args_list[0].kwargs["vad_filter"] is True
    assert mgr._model.transcribe.call_args_list[1].kwargs["vad_filter"] is False


def test_vad_empty_does_not_retry_without_signal_peak() -> None:
    mgr = WhisperModelManager(
        AsrConfig(
            device="cpu",
            vad_retry_without_filter_on_empty=True,
            vad_retry_min_peak=0.02,
        )
    )
    mgr._state = AsrState.READY
    mgr._model = MagicMock()
    mgr._model.transcribe.return_value = ([], _info())

    result = mgr.transcribe([0.0, 0.0, 0.0], vad_filter=True)

    assert result.success is True
    assert result.reason_code == ASR_VAD_NO_SPEECH
    assert "skip no-VAD retry" in result.detail
    assert mgr._model.transcribe.call_count == 1
