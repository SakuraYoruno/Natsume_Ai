"""T-TTS-HW-01: Edge TTS hardware integration contract tests.

Tests the GPT-SoVITS SDK integration layer including:
- Model manager lifecycle (load/unload/health/GPU errors)
- Audio playback device (play/stop/health)
- Synthesis result structure stability
- Synthesizer callback factory (SpeechPlaybackQueue integration)
- Error code stability (TTS_MODEL_UNAVAILABLE, TTS_GPU_OOM, TTS_SYNTH_TIMEOUT)
- Resource gate integration (TTS priority)
- Fixture-driven scenarios
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("Natsume_AUDIT_DB", ":memory:")

from host_brain.core.errors import (
    TTS_GPU_OOM,
    TTS_MODEL_UNAVAILABLE,
    TTS_SYNTH_TIMEOUT,
    VOICE_SYNTH_FAILED,
)
from edge_client.actuators.tts_engine import (
    AudioPlaybackDevice,
    SoVITSModelManager,
    TtsConfig,
    TtsState,
    TtsSynthResult,
    make_audio_playback_starter,
    make_sovits_synthesizer,
)
from edge_client.rpc.voice import VoiceRpcContract, VoiceRpcResult
from edge_client.rpc.resource_gate import VoiceResourceGate

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "voice"


def _load_fixture(subpath: str) -> dict:
    fpath = FIXTURES_DIR / subpath
    with open(fpath, "r", encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# T-TTS-HW-01.1: Model Manager Lifecycle
# ---------------------------------------------------------------------------

class TestTtsModelManagerLifecycle:
    """Verify SoVITSModelManager state transitions and error handling."""

    def test_initial_state_is_unloaded(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        assert mgr.state == TtsState.UNLOADED

    def test_load_without_sovits_returns_model_unavailable(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        # GPT-SoVITS not installed
        result = mgr.load()
        assert not result.success
        assert result.reason_code == TTS_MODEL_UNAVAILABLE
        assert mgr.state == TtsState.ERROR

    def test_health_snapshot_structure(self):
        mgr = SoVITSModelManager(TtsConfig(model_path="test/path", device="cpu"))
        health = mgr.health()
        assert "state" in health
        assert "model_path" in health
        assert "device" in health
        assert "sample_rate" in health
        assert health["model_path"] == "test/path"
        assert health["state"] == TtsState.UNLOADED.value

    def test_unload_resets_to_unloaded(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        mgr._state = TtsState.READY
        mgr._model = MagicMock()
        mgr.unload()
        assert mgr.state == TtsState.UNLOADED
        assert mgr._model is None

    def test_load_idempotent_when_ready(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        mgr._state = TtsState.READY
        mgr._model = MagicMock()
        result = mgr.load()
        assert result.success
        assert "already loaded" in result.detail

    def test_synthesize_empty_text_fails(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        mgr._state = TtsState.READY
        mgr._model = MagicMock()
        result = mgr.synthesize("")
        assert not result.success
        assert result.reason_code == VOICE_SYNTH_FAILED

    def test_synthesize_when_model_not_ready_triggers_load(self):
        """When model is not ready, synthesize() attempts load first."""
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        # Model not installed, so load will fail
        result = mgr.synthesize("hello")
        assert not result.success
        assert result.reason_code == TTS_MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# T-TTS-HW-01.2: Audio Playback Device
# ---------------------------------------------------------------------------

class TestAudioPlaybackDevice:
    """Verify AudioPlaybackDevice health and error handling."""

    def test_initial_state_not_playing(self):
        dev = AudioPlaybackDevice()
        assert not dev.playing

    def test_health_snapshot_structure(self):
        dev = AudioPlaybackDevice(sample_rate=32000)
        health = dev.health()
        assert health["sample_rate"] == 32000
        assert health["playing"] is False

    def test_stop_when_not_playing(self):
        dev = AudioPlaybackDevice()
        result = dev.stop()
        assert result.success
        assert "no active playback" in result.detail

    def test_play_without_sounddevice_fails(self):
        dev = AudioPlaybackDevice()
        import numpy as np
        audio = np.zeros(1000, dtype=np.float32)

        _orig = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _mock_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("mocked")
            return _orig(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_import):
            result = dev.play(audio)
        assert not result.success
        assert result.reason_code == TTS_MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# T-TTS-HW-01.3: TtsConfig Structure
# ---------------------------------------------------------------------------

class TestTtsConfig:
    """Verify TtsConfig defaults and serialization."""

    def test_default_config_values(self):
        cfg = TtsConfig()
        assert cfg.device == "cuda"
        assert cfg.sample_rate == 32000
        assert cfg.language == "ja"

    def test_as_dict_completeness(self):
        cfg = TtsConfig()
        d = cfg.as_dict()
        expected_keys = {
            "model_path", "device", "sample_rate", "max_text_length",
            "synth_timeout_s", "ref_audio_path", "language",
        }
        assert expected_keys.issubset(set(d.keys()))


# ---------------------------------------------------------------------------
# T-TTS-HW-01.4: TtsSynthResult Structure
# ---------------------------------------------------------------------------

class TestTtsSynthResult:
    """Verify TtsSynthResult serialization."""

    def test_success_result_as_dict(self):
        result = TtsSynthResult(
            success=True,
            reason_code=None,
            detail="ok",
            audio_ref="memory://test",
            duration_s=1.5,
            processing_time_s=0.3,
            sample_rate=32000,
        )
        d = result.as_dict()
        assert d["success"] is True
        assert d["audio_ref"] == "memory://test"
        assert d["sample_rate"] == 32000

    def test_failure_result_as_dict(self):
        result = TtsSynthResult(
            success=False,
            reason_code=TTS_MODEL_UNAVAILABLE,
            detail="not loaded",
        )
        d = result.as_dict()
        assert d["success"] is False
        assert d["reason_code"] == TTS_MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# T-TTS-HW-01.5: Synthesizer Callback Factory
# ---------------------------------------------------------------------------

class TestSynthesizerCallbackFactory:
    """Verify make_sovits_synthesizer produces compatible callback."""

    def test_factory_returns_callable(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        synth = make_sovits_synthesizer(mgr)
        assert callable(synth)

    def test_factory_callback_raises_on_failure(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        synth = make_sovits_synthesizer(mgr)
        with pytest.raises(RuntimeError, match="TTS synthesis failed"):
            synth({"text": "hello", "voice_profile": "default"})

    def test_factory_callback_returns_dict_on_success(self):
        mgr = SoVITSModelManager(TtsConfig(device="cpu"))
        mgr._state = TtsState.READY
        mgr._model = MagicMock()

        import numpy as np
        mock_audio = np.zeros(16000, dtype=np.float32)
        with patch.object(mgr, "synthesize", return_value=TtsSynthResult(
            success=True,
            reason_code=None,
            detail="ok",
            audio_ref="memory://test",
            audio_data=mock_audio,
            duration_s=1.0,
            processing_time_s=0.5,
            sample_rate=32000,
        )):
            synth = make_sovits_synthesizer(mgr)
            result = synth({"text": "hello"})
        assert "audio_ref" in result
        assert "audio_data" in result
        assert result["sample_rate"] == 32000


# ---------------------------------------------------------------------------
# T-TTS-HW-01.6: Error Code Stability
# ---------------------------------------------------------------------------

class TestTtsErrorCodeStability:
    """Verify all TTS error codes are stable string constants."""

    def test_error_codes_are_stable_strings(self):
        assert TTS_MODEL_UNAVAILABLE == "TTS_MODEL_UNAVAILABLE"
        assert TTS_GPU_OOM == "TTS_GPU_OOM"
        assert TTS_SYNTH_TIMEOUT == "TTS_SYNTH_TIMEOUT"


# ---------------------------------------------------------------------------
# T-TTS-HW-01.7: Resource Gate Integration (TTS Priority)
# ---------------------------------------------------------------------------

class TestTtsResourceGateIntegration:
    """Verify TTS has correct priority in resource gate."""

    def test_tts_blocked_by_asr(self):
        """TTS_SYNTH (priority 2) should be blocked when ASR (priority 0) is active."""
        gate = VoiceResourceGate()
        gate.request(
            request_id="req-asr-001",
            task="ASR_INGRESS",
            session_id="sess-001",
            hold=True,
            ts_ms=1000,
        )
        result = gate.request(
            request_id="req-tts-001",
            task="TTS_SYNTH",
            session_id="sess-001",
            hold=False,
            ts_ms=2000,
        )
        assert not result.admitted

    def test_tts_admitted_when_no_higher_priority(self):
        gate = VoiceResourceGate()
        result = gate.request(
            request_id="req-tts-001",
            task="TTS_SYNTH",
            session_id="sess-001",
            hold=True,
            ts_ms=1000,
        )
        assert result.admitted


# ---------------------------------------------------------------------------
# T-TTS-HW-01.8: Fixture-Driven Scenarios
# ---------------------------------------------------------------------------

class TestTtsFixtureScenarios:
    """Validate all TTS fixture scenarios."""

    def test_synth_success_fixture(self):
        fixture = _load_fixture("good/tts_hw_synth_success.json")
        assert fixture["scenario"] == "synth_success"
        assert fixture["expected"]["success"] is True
        assert fixture["expected"]["has_audio_ref"] is True

    def test_synth_failed_fixture(self):
        fixture = _load_fixture("bad/tts_hw_synth_failed.json")
        assert fixture["scenario"] == "synth_failed_model_unavailable"
        assert fixture["expected"]["success"] is False
        assert "TTS_MODEL_UNAVAILABLE" in fixture["expected"]["reason_codes"]

    def test_barge_in_recovery_fixture(self):
        fixture = _load_fixture("good/tts_hw_barge_in_recovery.json")
        assert fixture["scenario"] == "barge_in_recovery"
        assert fixture["expected"]["tts_interrupted"] is True
        assert fixture["expected"]["vts_reset_to_neutral"] is True


# ---------------------------------------------------------------------------
# T-TTS-HW-01.9: SpeechPlaybackQueue Integration via Callback
# ---------------------------------------------------------------------------

class TestTtsSpeechPlaybackQueueIntegration:
    """Verify TTS engine integrates with SpeechPlaybackQueue via callbacks."""

    def test_enqueue_and_start_with_mock_synth(self):
        """Full pipeline: enqueue -> start_next with mocked synthesizer."""
        from edge_client.actuators.speech import SpeechPlaybackQueue

        rpc = VoiceRpcContract()
        import numpy as np
        mock_audio = np.zeros(16000, dtype=np.float32)

        def mock_synth(speak_event):
            return {
                "audio_ref": "memory://mock",
                "audio_data": mock_audio,
                "duration_s": 1.0,
                "sample_rate": 32000,
            }

        def mock_playback(speak_event, synth_event):
            pass  # 不实际播放

        queue = SpeechPlaybackQueue(
            rpc=rpc,
            synthesizer=mock_synth,
            playback_starter=mock_playback,
        )

        enqueue_result = queue.enqueue_speak(
            request_id="req-001",
            payload={
                "session_id": "sess-001",
                "utterance_id": "utt-001",
                "text": "hello world",
                "seq": 1,
            },
            ts_ms=1000,
        )
        assert enqueue_result.success

        start_result = queue.start_next(ts_ms=2000)
        assert start_result is not None
        assert start_result.success
