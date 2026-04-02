"""M28-S: SoVITS emotion_intensity propagation tests."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestTtsIntensityFix:
    """Verify emotion_intensity mapping reaches the backend synth call."""

    def _make_mock_model(self, captured_kwargs: dict):
        mock_model = MagicMock()
        import numpy as np

        def mock_synthesize(**kwargs):
            captured_kwargs.update(kwargs)
            return {"audio": np.zeros(1000, dtype=np.float32), "sample_rate": 22050}

        mock_model.synthesize.side_effect = mock_synthesize
        return mock_model

    def test_high_intensity_different_from_low_intensity(self):
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

        assert captured_high.get("speed_factor") != captured_low.get("speed_factor")
        assert captured_high.get("energy_factor") != captured_low.get("energy_factor")

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
        assert captured.get("speed_factor", 0) <= 1.2

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
        assert abs(captured.get("speed_factor", 0) - 0.95) < 0.01
