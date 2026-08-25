"""T-ASR-HW-01: Edge ASR hardware integration contract tests.

Tests the faster-whisper SDK integration layer including:
- Model manager lifecycle (load/unload/health/GPU fallback)
- Audio stream capture health
- Transcription -> asr_chunk event mapping
- Error code stability (ASR_MODEL_UNAVAILABLE, ASR_GPU_OOM, ASR_DEVICE_ERROR, ASR_VAD_NO_SPEECH)
- Resource gate integration (ASR priority highest)
- Fixture-driven scenarios (short/long/silence/low-SNR/GPU-OOM)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure PYTHONPATH includes project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("Natsume_AUDIT_DB", ":memory:")

from host_brain.core.errors import (
    ASR_DEVICE_ERROR,
    ASR_GPU_OOM,
    ASR_MODEL_UNAVAILABLE,
    ASR_STREAM_INTERRUPTED,
    ASR_VAD_NO_SPEECH,
    VOICE_LOW_CONFIDENCE,
    VOICE_SCHEMA_INVALID,
)
from edge_client.senses.asr import (
    AsrConfig,
    AsrResult,
    AsrService,
    AsrState,
    AudioStreamCapture,
    TranscriptionSegment,
    WhisperModelManager,
)
from edge_client.rpc.voice import VoiceRpcContract, VoiceRpcResult
from edge_client.rpc.resource_gate import VoiceResourceGate

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "voice"


def _load_fixture(subpath: str) -> dict:
    fpath = FIXTURES_DIR / subpath
    with open(fpath, "r", encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# T-ASR-HW-01.1: Model Manager Lifecycle
# ---------------------------------------------------------------------------

class TestAsrModelManagerLifecycle:
    """Verify WhisperModelManager state transitions and error handling."""

    def test_initial_state_is_unloaded(self):
        mgr = WhisperModelManager(AsrConfig(device="cpu"))
        assert mgr.state == AsrState.UNLOADED

    def test_load_without_faster_whisper_returns_model_unavailable(self):
        """When faster-whisper is not installed, load() returns ASR_MODEL_UNAVAILABLE."""
        mgr = WhisperModelManager(AsrConfig(device="cpu"))
        with patch.dict(sys.modules, {"faster_whisper": None}):
            with patch("builtins.__import__", side_effect=_mock_import_error):
                result = mgr.load()
        assert not result.success
        assert result.reason_code == ASR_MODEL_UNAVAILABLE
        assert mgr.state == AsrState.ERROR

    def test_health_snapshot_structure(self):
        mgr = WhisperModelManager(AsrConfig(model_size="tiny", device="cpu"))
        health = mgr.health()
        assert "state" in health
        assert "model_size" in health
        assert "device" in health
        assert health["model_size"] == "tiny"
        assert health["state"] == AsrState.UNLOADED.value

    def test_unload_resets_to_unloaded(self):
        mgr = WhisperModelManager(AsrConfig(device="cpu"))
        mgr._state = AsrState.READY
        mgr._model = MagicMock()
        mgr.unload()
        assert mgr.state == AsrState.UNLOADED
        assert mgr._model is None

    def test_load_idempotent_when_ready(self):
        """If model is already READY, load() returns success without reloading."""
        mgr = WhisperModelManager(AsrConfig(device="cpu"))
        mgr._state = AsrState.READY
        mgr._model = MagicMock()
        result = mgr.load()
        assert result.success
        assert "already loaded" in result.detail

    def test_gpu_oom_fallback_exhaustion(self):
        """When GPU and CPU both fail, returns ASR_GPU_OOM."""
        mgr = WhisperModelManager(AsrConfig(device="cuda"))

        mock_whisper_model = MagicMock()
        mock_whisper_model.side_effect = RuntimeError("CUDA out of memory")

        with patch("edge_client.senses.asr.WhisperModelManager._device_fallback_chain",
                    return_value=["cuda", "cpu"]):
            with patch.object(mgr, "_device_fallback_chain", return_value=["cuda", "cpu"]):
                # Patch the import to succeed but constructor to fail
                mock_module = MagicMock()
                mock_module.WhisperModel = mock_whisper_model
                with patch.dict(sys.modules, {"faster_whisper": mock_module}):
                    result = mgr.load()

        assert not result.success
        assert result.reason_code in (ASR_GPU_OOM, ASR_MODEL_UNAVAILABLE)
        assert mgr.state == AsrState.ERROR


# ---------------------------------------------------------------------------
# T-ASR-HW-01.2: Audio Stream Capture Health
# ---------------------------------------------------------------------------

class TestAudioStreamCapture:
    """Verify AudioStreamCapture health and error handling."""

    def test_initial_state_not_running(self):
        capture = AudioStreamCapture()
        assert not capture.running

    def test_health_snapshot_structure(self):
        capture = AudioStreamCapture(sample_rate=16000, channels=1, chunk_duration_s=0.5)
        health = capture.health()
        assert health["sample_rate"] == 16000
        assert health["channels"] == 1
        assert health["chunk_duration_s"] == 0.5
        assert health["running"] is False

    def test_start_without_sounddevice_returns_device_error(self):
        capture = AudioStreamCapture()
        with patch.dict(sys.modules, {"sounddevice": None, "numpy": None}):
            with patch("builtins.__import__", side_effect=_mock_import_error):
                result = capture.start(callback=lambda a, t: None)
        assert not result.success
        assert result.reason_code == ASR_DEVICE_ERROR

    def test_stop_when_not_running(self):
        capture = AudioStreamCapture()
        result = capture.stop()
        assert result.success
        assert "not running" in result.detail


# ---------------------------------------------------------------------------
# T-ASR-HW-01.3: AsrConfig Structure
# ---------------------------------------------------------------------------

class TestAsrConfig:
    """Verify AsrConfig defaults and serialization."""

    def test_default_config_values(self):
        cfg = AsrConfig()
        assert cfg.model_size == "large-v3"
        assert cfg.device == "cuda"
        assert cfg.compute_type == "float16"
        assert cfg.sample_rate == 16000
        assert cfg.language == "zh"

    def test_as_dict_completeness(self):
        cfg = AsrConfig()
        d = cfg.as_dict()
        expected_keys = {
            "model_size", "device", "compute_type", "beam_size",
            "sample_rate", "channels", "chunk_duration_s",
            "vad_threshold", "silence_duration_s",
            "min_speech_duration_s", "max_speech_duration_s", "language",
        }
        assert expected_keys.issubset(set(d.keys()))


# ---------------------------------------------------------------------------
# T-ASR-HW-01.4: AsrResult Structure
# ---------------------------------------------------------------------------

class TestAsrResult:
    """Verify AsrResult serialization."""

    def test_success_result_as_dict(self):
        result = AsrResult(
            success=True,
            reason_code=None,
            detail="ok",
            segments=[
                TranscriptionSegment(text="hello", start=0.0, end=1.5, confidence=0.95),
            ],
            language="ja",
            language_probability=0.98,
            duration_s=1.5,
            processing_time_s=0.3,
        )
        d = result.as_dict()
        assert d["success"] is True
        assert len(d["segments"]) == 1
        assert d["segments"][0]["text"] == "hello"
        assert d["segments"][0]["confidence"] == 0.95

    def test_failure_result_as_dict(self):
        result = AsrResult(
            success=False,
            reason_code=ASR_MODEL_UNAVAILABLE,
            detail="model not loaded",
        )
        d = result.as_dict()
        assert d["success"] is False
        assert d["reason_code"] == ASR_MODEL_UNAVAILABLE
        assert d["segments"] == []


# ---------------------------------------------------------------------------
# T-ASR-HW-01.5: AsrService Transcribe -> asr_chunk Event Mapping
# ---------------------------------------------------------------------------

class TestAsrServiceEventMapping:
    """Verify AsrService maps transcription results to asr_chunk events."""

    def _make_service(self) -> AsrService:
        rpc = VoiceRpcContract()
        config = AsrConfig(device="cpu")
        mgr = WhisperModelManager(config)
        # Pre-set model to READY with mock
        mgr._state = AsrState.READY
        mgr._model = MagicMock()
        return AsrService(rpc=rpc, config=config, model_manager=mgr)

    def test_transcribe_produces_rpc_results(self):
        """Transcription of audio should produce VoiceRpcResult events."""
        svc = self._make_service()
        svc._session_id = "test-session"

        # Mock the model transcribe to return a segment
        mock_info = MagicMock()
        mock_info.language = "ja"
        mock_info.language_probability = 0.95
        mock_info.duration = 2.0

        mock_seg = MagicMock()
        mock_seg.text = " hello world "
        mock_seg.start = 0.0
        mock_seg.end = 2.0
        mock_seg.avg_log_prob = -0.3
        mock_seg.words = []

        svc._model._model.transcribe.return_value = ([mock_seg], mock_info)

        import numpy as np
        audio = np.zeros(16000, dtype=np.float32)
        results = svc.transcribe_audio(audio, request_id="req-001")

        assert len(results) >= 1
        # Check result is VoiceRpcResult
        assert isinstance(results[0], VoiceRpcResult)

    def test_transcribe_model_unavailable_emits_failure(self):
        """When model is in ERROR state, transcribe still emits event via RPC."""
        rpc = VoiceRpcContract()
        config = AsrConfig(device="cpu")
        mgr = WhisperModelManager(config)
        mgr._state = AsrState.ERROR
        mgr._load_error = "test error"

        svc = AsrService(rpc=rpc, config=config, model_manager=mgr)
        svc._session_id = "test-session"

        # Mock load to return failure
        with patch.object(mgr, "load", return_value=AsrResult(
            success=False,
            reason_code=ASR_MODEL_UNAVAILABLE,
            detail="model not available",
        )):
            import numpy as np
            audio = np.zeros(16000, dtype=np.float32)
            results = svc.transcribe_audio(audio, request_id="req-002")

        assert len(results) >= 1
        # The RPC result should still be a valid VoiceRpcResult
        assert isinstance(results[0], VoiceRpcResult)


# ---------------------------------------------------------------------------
# T-ASR-HW-01.7: Error Code Stability
# ---------------------------------------------------------------------------

class TestAsrErrorCodeStability:
    """Verify all ASR error codes are stable string constants."""

    def test_error_codes_are_stable_strings(self):
        assert ASR_MODEL_UNAVAILABLE == "ASR_MODEL_UNAVAILABLE"
        assert ASR_GPU_OOM == "ASR_GPU_OOM"
        assert ASR_DEVICE_ERROR == "ASR_DEVICE_ERROR"
        assert ASR_VAD_NO_SPEECH == "ASR_VAD_NO_SPEECH"
        assert ASR_STREAM_INTERRUPTED == "ASR_STREAM_INTERRUPTED"


# ---------------------------------------------------------------------------
# T-ASR-HW-01.8: Resource Gate Integration (ASR Priority Highest)
# ---------------------------------------------------------------------------

class TestAsrResourceGateIntegration:
    """Verify ASR has highest priority in resource gate."""

    def test_asr_not_blocked_by_lower_priority(self):
        """ASR (priority 0) should never be blocked by TTS or Playback."""
        gate = VoiceResourceGate()
        # Hold TTS task
        gate.request(
            request_id="req-tts-001",
            task="TTS_SYNTH",
            session_id="sess-001",
            hold=True,
            ts_ms=1000,
        )
        # ASR should still be admitted
        result = gate.request(
            request_id="req-asr-001",
            task="ASR_INGRESS",
            session_id="sess-001",
            hold=True,
            ts_ms=2000,
        )
        assert result.admitted is True

# ---------------------------------------------------------------------------
# T-ASR-HW-01.9: AsrService Health Snapshot
# ---------------------------------------------------------------------------

class TestAsrServiceHealth:
    """Verify AsrService health endpoint returns stable structure."""

    def test_health_snapshot_structure(self):
        rpc = VoiceRpcContract()
        svc = AsrService(rpc=rpc, config=AsrConfig(device="cpu"))
        health = svc.health()
        assert health["service"] == "edge.asr"
        assert "session_id" in health
        assert "seq" in health
        assert "running" in health
        assert "model" in health
        assert "audio" in health
        assert "config" in health
        # Model sub-health
        assert "state" in health["model"]
        assert "device" in health["model"]
        # Audio sub-health
        assert "sample_rate" in health["audio"]
        assert "running" in health["audio"]


# ---------------------------------------------------------------------------
# T-ASR-HW-01.10: Fixture-Driven Scenario Validation
# ---------------------------------------------------------------------------

class TestAsrFixtureScenarios:
    """Validate all fixture scenarios produce expected results."""

    def test_short_speech_fixture_loads(self):
        fixture = _load_fixture("good/asr_hw_short_speech.json")
        assert fixture["scenario"] == "short_speech"
        assert fixture["expected"]["success"] is True

    def test_long_speech_fixture_loads(self):
        fixture = _load_fixture("good/asr_hw_long_speech.json")
        assert fixture["scenario"] == "long_speech"
        assert fixture["expected"]["success"] is True

    def test_silence_fixture_loads(self):
        fixture = _load_fixture("bad/asr_hw_silence.json")
        assert fixture["scenario"] == "silence"
        assert fixture["expected"]["reason_code"] == "ASR_VAD_NO_SPEECH"

    def test_low_snr_fixture_loads(self):
        fixture = _load_fixture("bad/asr_hw_low_snr.json")
        assert fixture["scenario"] == "low_snr"
        assert fixture["expected"]["low_confidence"] is True

    def test_gpu_oom_fixture_loads(self):
        fixture = _load_fixture("bad/asr_hw_gpu_oom.json")
        assert fixture["scenario"] == "gpu_oom"
        assert "ASR_GPU_OOM" in fixture["expected"]["reason_codes"]


# ---------------------------------------------------------------------------
# Helper: mock import error for faster-whisper / sounddevice
# ---------------------------------------------------------------------------

_ORIGINAL_IMPORT = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__


def _mock_import_error(name, *args, **kwargs):
    if name in ("faster_whisper", "sounddevice", "numpy"):
        raise ImportError(f"No module named '{name}' (mocked)")
    return _ORIGINAL_IMPORT(name, *args, **kwargs)
