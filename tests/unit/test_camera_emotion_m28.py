"""M28-M：CameraRecognition 视觉感知触发情感响应测试（T-CAM-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.vision.camera_event import (
    CameraRecognitionServiceContract,
    _infer_user_emotion_hint,
)
from host_brain.core.metabolism import EmotionVector, apply_visual_emotion_hint


class TestCameraEmotion:
    """T-CAM-EMO-01~02：CameraRecognition 视觉情感响应测试"""

    def test_smile_label_infers_happy(self):
        """T-CAM-EMO-01：识别到 smile 标签时，user_emotion_hint="HAPPY" """
        objects = [{"label": "smile", "conf": 0.9, "low_confidence": False}]
        hint = _infer_user_emotion_hint(objects)
        assert hint == "HAPPY"

    def test_apply_visual_emotion_hint_happy_increases_valence(self):
        """T-CAM-EMO-02：apply_visual_emotion_hint("HAPPY") 后，emotion_vector.valence 上升"""
        ev = EmotionVector(valence=0.0, arousal=0.0)
        new_ev = apply_visual_emotion_hint(ev, "HAPPY", weight=0.5)
        assert new_ev.valence > ev.valence

    def test_low_confidence_object_ignored(self):
        """低置信度对象不影响情感推断"""
        objects = [{"label": "smile", "conf": 0.3, "low_confidence": True}]
        hint = _infer_user_emotion_hint(objects)
        assert hint is None

    def test_detect_output_contains_user_emotion_hint(self):
        """detect() 返回结果包含 user_emotion_hint 字段"""
        def mock_provider(evidence_ref, frame_text):
            return [{"label": "smile", "conf": 0.9}], "mock-detector"

        contract = CameraRecognitionServiceContract(provider=mock_provider)
        result = contract.detect(
            request_id="req-001",
            evidence_ref="camera://test/frame_001",
            frame_text="smile detected",
        )
        assert result.success
        assert "user_emotion_hint" in result.output

    def test_apply_visual_emotion_hint_unknown_no_change(self):
        """未知情感提示不改变 EmotionVector"""
        ev = EmotionVector(valence=0.5, arousal=0.3)
        new_ev = apply_visual_emotion_hint(ev, "UNKNOWN_EMOTION_XYZ")
        assert new_ev.valence == ev.valence
        assert new_ev.arousal == ev.arousal
