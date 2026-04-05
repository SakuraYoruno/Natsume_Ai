from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edge_client.senses.asr import ASR_VAD_NO_SPEECH, AsrConfig, AsrResult, AsrService, AsrState, TranscriptionSegment, WhisperModelManager


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
            precheck_min_peak=0.0,
            precheck_min_rms=0.0,
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


def test_gpu_runtime_failure_reloads_cpu_and_retries() -> None:
    gpu_model = MagicMock()
    gpu_model.transcribe.side_effect = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    cpu_model = MagicMock()
    cpu_model.transcribe.return_value = ([_segment()], _info())
    whisper_ctor = MagicMock(return_value=cpu_model)
    mock_module = SimpleNamespace(WhisperModel=whisper_ctor)

    mgr = WhisperModelManager(AsrConfig(device="cuda"))
    mgr._state = AsrState.READY
    mgr._actual_device = "cuda"
    mgr._model = gpu_model

    with patch.dict(sys.modules, {"faster_whisper": mock_module}):
        result = mgr.transcribe([0.2, -0.1, 0.05], vad_filter=False)

    assert result.success is True
    assert result.reason_code is None
    assert len(result.segments) == 1
    assert mgr.actual_device == "cpu"
    assert mgr._recovery_count == 1
    assert gpu_model.transcribe.call_count == 1
    assert cpu_model.transcribe.call_count == 1
    assert whisper_ctor.call_args.kwargs["device"] == "cpu"


def test_gpu_load_failure_falls_back_to_cpu_on_cublas_error() -> None:
    cpu_model = MagicMock()
    whisper_ctor = MagicMock(
        side_effect=[
            RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"),
            cpu_model,
        ]
    )
    mock_module = SimpleNamespace(WhisperModel=whisper_ctor)

    mgr = WhisperModelManager(AsrConfig(device="cuda"))

    with patch.dict(sys.modules, {"faster_whisper": mock_module}):
        result = mgr.load()

    assert result.success is True
    assert mgr.state == AsrState.READY
    assert mgr.actual_device == "cpu"
    assert whisper_ctor.call_args_list[0].kwargs["device"] == "cuda"
    assert whisper_ctor.call_args_list[1].kwargs["device"] == "cpu"


def test_asr_service_respects_use_vad_filter_flag() -> None:
    rpc = MagicMock()
    rpc.asr_chunk.return_value = MagicMock()
    model = MagicMock()
    model.transcribe.return_value = AsrResult(
        success=True,
        reason_code=None,
        detail="ok",
        segments=[],
        language="zh",
        language_probability=0.99,
        duration_s=1.0,
        processing_time_s=0.1,
    )

    service = AsrService(
        rpc=rpc,
        config=AsrConfig(device="cpu", use_vad_filter=False),
        model_manager=model,
    )

    service.transcribe_audio([0.1, -0.1], request_id="req-test", ts_ms=1)

    assert model.transcribe.call_args.kwargs["vad_filter"] is False


def test_asr_service_offsets_segment_timestamps() -> None:
    rpc = MagicMock()
    rpc.asr_chunk.return_value = MagicMock()
    model = MagicMock()
    model.transcribe.return_value = AsrResult(
        success=True,
        reason_code=None,
        detail="ok",
        segments=[
            TranscriptionSegment(text="hello", start=0.0, end=0.4, confidence=0.8),
            TranscriptionSegment(text="world", start=0.6, end=1.0, confidence=0.8),
        ],
        language="zh",
        language_probability=0.99,
        duration_s=1.0,
        processing_time_s=0.1,
    )

    service = AsrService(
        rpc=rpc,
        config=AsrConfig(device="cpu"),
        model_manager=model,
    )

    service.transcribe_audio([0.1, -0.1], request_id="req-test", ts_ms=1000)

    assert rpc.asr_chunk.call_args_list[0].kwargs["ts_ms"] == 1000
    assert rpc.asr_chunk.call_args_list[1].kwargs["ts_ms"] == 1600


def test_asr_service_coalesces_final_segments_into_one_event() -> None:
    rpc = MagicMock()
    rpc.asr_chunk.return_value = MagicMock()
    model = MagicMock()
    model.transcribe.return_value = AsrResult(
        success=True,
        reason_code=None,
        detail="ok",
        segments=[
            TranscriptionSegment(text="hello", start=0.0, end=0.4, confidence=0.7),
            TranscriptionSegment(text="world", start=0.6, end=1.0, confidence=0.9),
        ],
        language="zh",
        language_probability=0.99,
        duration_s=1.0,
        processing_time_s=0.1,
    )

    service = AsrService(
        rpc=rpc,
        config=AsrConfig(device="cpu"),
        model_manager=model,
    )

    service.transcribe_audio([0.1, -0.1], request_id="req-test", ts_ms=1000, final=True)

    assert rpc.asr_chunk.call_count == 1
    assert rpc.asr_chunk.call_args.kwargs["payload"]["text"] == "hello world"
    assert rpc.asr_chunk.call_args.kwargs["payload"]["final"] is True
    assert rpc.asr_chunk.call_args.kwargs["ts_ms"] == 1000


def test_asr_service_marks_silence_window_flush_as_final() -> None:
    service = AsrService(
        rpc=MagicMock(),
        config=AsrConfig(
            device="cpu",
            chunk_duration_s=0.5,
            silence_duration_s=1.0,
            precheck_min_peak=0.01,
            precheck_min_rms=0.002,
        ),
        model_manager=MagicMock(),
    )
    service._running = True
    service._flush_buffer = MagicMock()

    service._on_audio_chunk([0.2, -0.2], 1000)
    service._on_audio_chunk([0.0, 0.0, 0.0], 1500)

    service._flush_buffer.assert_not_called()

    service._on_audio_chunk([0.0, 0.0, 0.0], 2000)

    service._flush_buffer.assert_called_once_with(final=True)


def test_asr_service_keeps_active_audio_window_buffered() -> None:
    service = AsrService(
        rpc=MagicMock(),
        config=AsrConfig(
            device="cpu",
            silence_duration_s=1.0,
            max_speech_duration_s=10.0,
            precheck_min_peak=0.01,
            precheck_min_rms=0.002,
        ),
        model_manager=MagicMock(),
    )
    service._running = True
    service._flush_buffer = MagicMock()

    service._on_audio_chunk([0.2, -0.2], 1000)
    service._on_audio_chunk([0.2, -0.2], 2000)

    service._flush_buffer.assert_not_called()


def test_asr_service_ignores_leading_silence_chunks() -> None:
    service = AsrService(
        rpc=MagicMock(),
        config=AsrConfig(
            device="cpu",
            chunk_duration_s=0.5,
            silence_duration_s=1.0,
            precheck_min_peak=0.01,
            precheck_min_rms=0.002,
        ),
        model_manager=MagicMock(),
    )
    service._running = True
    service._flush_buffer = MagicMock()

    service._on_audio_chunk([0.0, 0.0, 0.0], 1000)
    service._on_audio_chunk([0.0, 0.0, 0.0], 1500)

    assert service._audio_buffer == []
    service._flush_buffer.assert_not_called()


def test_word_probabilities_drive_segment_confidence() -> None:
    segment = SimpleNamespace(
        text=" 测试 ",
        start=0.0,
        end=0.8,
        avg_log_prob=-8.0,
        words=[
            SimpleNamespace(word="测", start=0.0, end=0.2, probability=0.9),
            SimpleNamespace(word="试", start=0.2, end=0.4, probability=0.8),
        ],
    )
    mgr = WhisperModelManager(AsrConfig(device="cpu"))
    mgr._state = AsrState.READY
    mgr._model = MagicMock()
    mgr._model.transcribe.return_value = ([segment], _info())

    result = mgr.transcribe([0.2, -0.1, 0.05], vad_filter=False)

    assert result.success is True
    assert len(result.segments) == 1
    assert result.segments[0].confidence == 0.85


def test_low_energy_audio_short_circuits_without_model_call() -> None:
    mgr = WhisperModelManager(
        AsrConfig(device="cpu", precheck_min_peak=0.01, precheck_min_rms=0.002)
    )
    mgr._state = AsrState.READY
    mgr._model = MagicMock()

    result = mgr.transcribe([0.0, 0.0, 0.0, 0.0005], vad_filter=False)

    assert result.success is True
    assert result.reason_code == ASR_VAD_NO_SPEECH
    assert "audio below energy gate" in result.detail
    assert mgr._model.transcribe.call_count == 0
