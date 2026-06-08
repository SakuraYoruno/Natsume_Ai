"""M28-S: SoVITS emotion_intensity propagation tests."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestTtsIntensityFix:
    """Verify TTS prosody follows emotion without leaving safe model-native bounds."""

    def _make_mock_model(self, captured_kwargs: dict):
        mock_model = MagicMock()
        import numpy as np

        def mock_synthesize(**kwargs):
            captured_kwargs.update(kwargs)
            return {"audio": np.zeros(1000, dtype=np.float32), "sample_rate": 22050}

        mock_model.synthesize.side_effect = mock_synthesize
        return mock_model

    def test_happy_intensity_changes_default_model_speed_within_bounds(self):
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured_high = {}
        captured_low = {}

        mock_high = self._make_mock_model(captured_high)
        mock_low = self._make_mock_model(captured_low)

        _synthesize_audio(
            model=mock_high,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="HAPPY",
            emotion_intensity=0.9,
            timeout_s=10.0,
        )
        _synthesize_audio(
            model=mock_low,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="HAPPY",
            emotion_intensity=0.3,
            timeout_s=10.0,
        )

        assert 1.0 < captured_low.get("speed_factor", 0) < captured_high.get("speed_factor", 0) <= 1.16
        assert 1.0 < captured_low.get("energy_factor", 0) < captured_high.get("energy_factor", 0) <= 1.12

    def test_intensity_out_of_range_is_clamped(self):
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="HAPPY",
            emotion_intensity=1.5,
            timeout_s=10.0,
        )
        assert abs(captured.get("speed_factor", 0) - 1.16) < 0.01
        assert abs(captured.get("energy_factor", 0) - 1.12) < 0.01

    def test_none_intensity_uses_default(self):
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag=None,
            emotion_intensity=None,
            timeout_s=10.0,
        )
        assert abs(captured.get("speed_factor", 0) - 1.0) < 0.01
        assert abs(captured.get("energy_factor", 0) - 1.0) < 0.01

    def test_emotion_prosody_can_be_disabled(self, monkeypatch):
        from edge_client.actuators.tts_engine import _synthesize_audio

        monkeypatch.setenv("EDGE_TTS_EMOTION_PROSODY", "false")
        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="HAPPY",
            emotion_intensity=0.9,
            timeout_s=10.0,
        )
        assert abs(captured.get("speed_factor", 0) - 1.0) < 0.01
        assert abs(captured.get("energy_factor", 0) - 1.0) < 0.01

    def test_explicit_speed_is_honored(self):
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="EXCITED",
            emotion_intensity=1.0,
            speed_factor=1.2,
            timeout_s=10.0,
        )
        assert abs(captured.get("speed_factor", 0) - 1.2) < 0.01
        assert 1.0 < captured.get("energy_factor", 0) <= 1.14

    def test_explicit_slow_speed_passes_through(self):
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model,
            text="\u3053\u3093\u306b\u3061\u306f",
            language="ja",
            emotion_tag="CALM",
            emotion_intensity=0.4,
            speed_factor=0.72,
            timeout_s=10.0,
        )
        assert abs(captured.get("speed_factor", 0) - 0.72) < 0.01

    def test_expressive_timing_adds_pauses_when_enabled(self, monkeypatch):
        import numpy as np
        from edge_client.actuators.tts_engine import _apply_expressive_audio_timing

        monkeypatch.setenv("EDGE_TTS_EXPRESSIVE_TIMING", "true")
        audio = np.ones(32000, dtype=np.float32)
        rendered, meta = _apply_expressive_audio_timing(
            audio_data=audio,
            sample_rate=32000,
            text="没关系，我在这里。慢慢说，好吗？",
            emotion_tag="COMFORT",
            emotion_intensity=0.8,
        )

        assert meta["enabled"] is True
        assert meta["segments"] >= 3
        assert meta["inserted_pause_s"] > 0.5
        assert meta["breath_count"] >= 1
        assert len(rendered) > len(audio)
