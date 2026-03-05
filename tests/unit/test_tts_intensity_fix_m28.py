"""M28-S：SoVITS emotion_intensity 真正传递测试（T-TTS-INTENSITY-FIX-01~02）"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestTtsIntensityFix:
    """T-TTS-INTENSITY-FIX-01~02：SoVITS emotion_intensity 真正传递测试"""

    def _make_mock_model(self, captured_kwargs: dict):
        """创建 mock 底层 TTS 模型"""
        mock_model = MagicMock()
        import numpy as np

        def mock_synthesize(**kwargs):
            captured_kwargs.update(kwargs)
            return {"audio": np.zeros(1000, dtype=np.float32), "sample_rate": 22050}

        mock_model.synthesize.side_effect = mock_synthesize
        return mock_model

    def test_high_intensity_different_from_low_intensity(self):
        """T-TTS-INTENSITY-FIX-01：emotion_intensity=0.9 时，底层 SDK 参数与 0.3 时不同"""
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured_high = {}
        captured_low = {}

        mock_high = self._make_mock_model(captured_high)
        mock_low = self._make_mock_model(captured_low)

        _synthesize_audio(
            model=mock_high, text="测试", language="zh",
            emotion_tag="HAPPY", emotion_intensity=0.9, timeout_s=10.0,
        )
        _synthesize_audio(
            model=mock_low, text="测试", language="zh",
            emotion_tag="HAPPY", emotion_intensity=0.3, timeout_s=10.0,
        )

        # 高强度和低强度的 speed_factor/energy_factor 应不同
        assert captured_high.get("speed_factor") != captured_low.get("speed_factor")
        assert captured_high.get("energy_factor") != captured_low.get("energy_factor")

    def test_intensity_out_of_range_is_clamped(self):
        """T-TTS-INTENSITY-FIX-02：emotion_intensity 超出 [0,1] 时被截断，不报错"""
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        # 超出范围的值不应报错
        _synthesize_audio(
            model=mock_model, text="测试", language="zh",
            emotion_tag="HAPPY", emotion_intensity=1.5, timeout_s=10.0,
        )
        # speed_factor 应在合理范围内（不超过 1.2）
        assert captured.get("speed_factor", 0) <= 1.2

    def test_none_intensity_uses_default(self):
        """emotion_intensity=None 时使用默认值 0.5，不报错"""
        from edge_client.actuators.tts_engine import _synthesize_audio

        captured = {}
        mock_model = self._make_mock_model(captured)

        _synthesize_audio(
            model=mock_model, text="测试", language="zh",
            emotion_tag=None, emotion_intensity=None, timeout_s=10.0,
        )
        # 默认 intensity=0.5 → speed_factor=0.95, energy_factor=1.0
        assert abs(captured.get("speed_factor", 0) - 0.95) < 0.01
