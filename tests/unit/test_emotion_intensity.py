from __future__ import annotations

from unittest.mock import MagicMock

from edge_client.actuators.tts_engine import SoVITSModelManager, make_sovits_synthesizer
from edge_client.actuators.vts_driver import VTubeStudioDriver


class TestSynthesizeEmotionIntensity:
    def test_synthesize_accepts_emotion_intensity(self) -> None:
        engine = SoVITSModelManager()

        result = engine.synthesize("test", emotion_tag="HAPPY", emotion_intensity=0.9)

        assert result is not None

    def test_synthesize_clamps_out_of_range_intensity(self) -> None:
        engine = SoVITSModelManager()

        high = engine.synthesize("test", emotion_tag="HAPPY", emotion_intensity=1.5)
        low = engine.synthesize("test", emotion_tag="HAPPY", emotion_intensity=-0.5)

        assert high is not None
        assert low is not None

    def test_synthesize_allows_none_intensity(self) -> None:
        engine = SoVITSModelManager()

        result = engine.synthesize("test", emotion_tag="HAPPY", emotion_intensity=None)

        assert result is not None


class TestSetExpressionIntensity:
    def test_set_expression_accepts_explicit_intensity(self) -> None:
        driver = VTubeStudioDriver()

        result = driver.set_expression("HAPPY", intensity=0.9)

        assert result is not None

    def test_set_expression_accepts_boundary_intensity_values(self) -> None:
        driver = VTubeStudioDriver()

        zero = driver.set_expression("CALM", intensity=0.0)
        one = driver.set_expression("EXCITED", intensity=1.0)

        assert zero is not None
        assert one is not None

    def test_sovits_synthesizer_forwards_emotion_intensity(self) -> None:
        engine = MagicMock(spec=SoVITSModelManager)
        engine.synthesize.return_value = MagicMock(success=True)
        synthesizer = make_sovits_synthesizer(engine)

        synthesizer(
            {
                "text": "test",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
                "voice_profile": "default",
            }
        )

        assert engine.synthesize.call_args.kwargs["emotion_intensity"] == 0.8
