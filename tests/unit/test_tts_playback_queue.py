"""T-TTS-QUEUE-01~05: TTS 播报队列强化单元测试（M15-B）。

覆盖需求：FR-VOICE-002
"""

from __future__ import annotations

import pytest

from edge_client.actuators.tts_engine import (
    TTSPlaybackEvent,
    TTS_PLAYBACK_STATUS_DONE,
    TTS_PLAYBACK_STATUS_FAILED,
    TTS_PLAYBACK_STATUS_INTERRUPTED,
    TTS_PLAYBACK_STATUS_PLAYING,
    TTS_PLAYBACK_STATUS_QUEUED,
    build_tts_playback_event,
)
from host_brain.core.errors import TTS_QUEUE_FULL, VOICE_SCHEMA_INVALID, VOICE_SYNTH_FAILED


NOW_MS = 1_800_000_000_000
GOOD_EVIDENCE_REF = "evidence::tts_001"


def _make_event(**kwargs) -> TTSPlaybackEvent:
    defaults = dict(
        request_id="req_tts_001",
        text="こんにちは、夏目です。",
        emotion_tag="neutral",
        status=TTS_PLAYBACK_STATUS_QUEUED,
        evidence_ref=GOOD_EVIDENCE_REF,
        ts_ms=NOW_MS,
    )
    defaults.update(kwargs)
    return build_tts_playback_event(**defaults)


# ── T-TTS-QUEUE-01: 正常入队，status=queued，evidence_ref 存在 ────────────────

class TestNormalQueue:
    """T-TTS-QUEUE-01: 正常入队，status=queued，evidence_ref 存在。"""

    def test_returns_tts_playback_event(self):
        event = _make_event()
        assert isinstance(event, TTSPlaybackEvent)

    def test_status_queued(self):
        event = _make_event(status=TTS_PLAYBACK_STATUS_QUEUED)
        assert event.status == TTS_PLAYBACK_STATUS_QUEUED

    def test_evidence_ref_present(self):
        event = _make_event()
        assert event.evidence_ref == GOOD_EVIDENCE_REF

    def test_request_id_preserved(self):
        event = _make_event(request_id="req_custom_001")
        assert event.request_id == "req_custom_001"

    def test_text_preserved(self):
        event = _make_event(text="テストテキスト")
        assert event.text == "テストテキスト"

    def test_emotion_tag_preserved(self):
        event = _make_event(emotion_tag="happy")
        assert event.emotion_tag == "happy"

    def test_as_dict_structure(self):
        event = _make_event()
        d = event.as_dict()
        assert "event_id" in d
        assert "request_id" in d
        assert "status" in d
        assert "evidence_ref" in d
        assert "reason_code" in d


# ── T-TTS-QUEUE-02: 播报完成，status=done，request_id 可追溯 ─────────────────

class TestPlaybackDone:
    """T-TTS-QUEUE-02: 播报完成，status=done，request_id 可追溯。"""

    def test_status_done(self):
        event = _make_event(status=TTS_PLAYBACK_STATUS_DONE)
        assert event.status == TTS_PLAYBACK_STATUS_DONE

    def test_request_id_traceable(self):
        event = _make_event(request_id="req_trace_001", status=TTS_PLAYBACK_STATUS_DONE)
        assert event.request_id == "req_trace_001"

    def test_no_reason_code_on_done(self):
        event = _make_event(status=TTS_PLAYBACK_STATUS_DONE)
        assert event.reason_code is None

    def test_intent_id_optional(self):
        event = _make_event(status=TTS_PLAYBACK_STATUS_DONE, intent_id="intent_001")
        assert event.intent_id == "intent_001"

    def test_intent_id_none_by_default(self):
        event = _make_event(status=TTS_PLAYBACK_STATUS_DONE)
        assert event.intent_id is None


# ── T-TTS-QUEUE-03: 队列满，status=failed，reason_code=TTS_QUEUE_FULL ─────────

class TestQueueFull:
    """T-TTS-QUEUE-03: 队列满，status=failed，reason_code=TTS_QUEUE_FULL。"""

    def test_status_failed_with_queue_full(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=TTS_QUEUE_FULL,
            stage="queue_admission",
        )
        assert event.status == TTS_PLAYBACK_STATUS_FAILED

    def test_reason_code_tts_queue_full(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=TTS_QUEUE_FULL,
            stage="queue_admission",
        )
        assert event.reason_code == TTS_QUEUE_FULL

    def test_stage_present_on_failure(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=TTS_QUEUE_FULL,
            stage="queue_admission",
        )
        assert event.stage == "queue_admission"


# ── T-TTS-QUEUE-04: 合成失败，status=failed，reason_code=TTS_SYNTH_FAILED ──────

class TestSynthFailed:
    """T-TTS-QUEUE-04: 合成失败，status=failed，reason_code=TTS_SYNTH_FAILED（或 VOICE_SYNTH_FAILED）。"""

    def test_status_failed_with_synth_failed(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=VOICE_SYNTH_FAILED,
            stage="synthesis",
        )
        assert event.status == TTS_PLAYBACK_STATUS_FAILED

    def test_reason_code_voice_synth_failed(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=VOICE_SYNTH_FAILED,
            stage="synthesis",
        )
        assert event.reason_code == VOICE_SYNTH_FAILED

    def test_stage_synthesis(self):
        event = _make_event(
            status=TTS_PLAYBACK_STATUS_FAILED,
            reason_code=VOICE_SYNTH_FAILED,
            stage="synthesis",
        )
        assert event.stage == "synthesis"


# ── T-TTS-QUEUE-05: 缺 request_id → 拒绝入队（VOICE_SCHEMA_INVALID）────────────

class TestRequestIdRequired:
    """T-TTS-QUEUE-05: 缺 request_id → 拒绝入队（VOICE_SCHEMA_INVALID）。"""

    def test_missing_request_id_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_tts_playback_event(
                request_id=None,  # type: ignore[arg-type]
                text="テスト",
                emotion_tag="neutral",
                status=TTS_PLAYBACK_STATUS_QUEUED,
                evidence_ref=GOOD_EVIDENCE_REF,
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_empty_request_id_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_tts_playback_event(
                request_id="",
                text="テスト",
                emotion_tag="neutral",
                status=TTS_PLAYBACK_STATUS_QUEUED,
                evidence_ref=GOOD_EVIDENCE_REF,
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_missing_evidence_ref_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_tts_playback_event(
                request_id="req_001",
                text="テスト",
                emotion_tag="neutral",
                status=TTS_PLAYBACK_STATUS_QUEUED,
                evidence_ref="",
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_tts_playback_event(
                request_id="req_001",
                text="テスト",
                emotion_tag="neutral",
                status="invalid_status",
                evidence_ref=GOOD_EVIDENCE_REF,
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_frozen_dataclass_immutable(self):
        event = _make_event()
        with pytest.raises((AttributeError, TypeError)):
            event.text = "modified"  # type: ignore[misc]
