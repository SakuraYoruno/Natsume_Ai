"""T-ASR-EVT-01~05: ASR 事件化上送强化单元测试（M15-A）。

覆盖需求：FR-VOICE-001
"""

from __future__ import annotations

import pytest

from edge_client.senses.asr import (
    ASRTranscriptEvent,
    LOW_CONFIDENCE_THRESHOLD,
    MAX_ALTERNATES,
    build_asr_transcript_event,
)
from host_brain.core.errors import ASR_LOW_CONFIDENCE


# ── 夹具 ─────────────────────────────────────────────────────────────────────

GOOD_EVIDENCE_REF = "evidence::asr_001"


def _make_event(**kwargs) -> ASRTranscriptEvent:
    defaults = dict(
        session_id="session_001",
        segment_id="seg_001",
        text="こんにちは",
        confidence=0.9,
        ts_start_ms=1000,
        ts_end_ms=2000,
        is_final=True,
        evidence_ref=GOOD_EVIDENCE_REF,
    )
    defaults.update(kwargs)
    return build_asr_transcript_event(**defaults)


# ── T-ASR-EVT-01: 正常转写事件，confidence ≥ 0.6，low_confidence=False ─────────

class TestNormalTranscriptEvent:
    """T-ASR-EVT-01: 正常转写事件，confidence ≥ 0.6，low_confidence=False。"""

    def test_returns_asr_transcript_event(self):
        event = _make_event()
        assert isinstance(event, ASRTranscriptEvent)

    def test_low_confidence_false_when_high_confidence(self):
        event = _make_event(confidence=0.9)
        assert event.low_confidence is False

    def test_reason_code_none_when_high_confidence(self):
        event = _make_event(confidence=0.9)
        assert event.reason_code is None

    def test_text_preserved(self):
        event = _make_event(text="テスト")
        assert event.text == "テスト"

    def test_confidence_preserved(self):
        event = _make_event(confidence=0.85)
        assert event.confidence == 0.85

    def test_is_final_preserved(self):
        event = _make_event(is_final=True)
        assert event.is_final is True

    def test_evidence_ref_preserved(self):
        event = _make_event(evidence_ref="evidence::test_001")
        assert event.evidence_ref == "evidence::test_001"

    def test_as_dict_structure(self):
        event = _make_event()
        d = event.as_dict()
        assert "event_id" in d
        assert "session_id" in d
        assert "confidence" in d
        assert "low_confidence" in d
        assert "evidence_ref" in d
        assert "alternates" in d

    def test_boundary_confidence_exactly_threshold(self):
        """confidence = LOW_CONFIDENCE_THRESHOLD 时不标注低置信度。"""
        event = _make_event(confidence=LOW_CONFIDENCE_THRESHOLD)
        assert event.low_confidence is False


# ── T-ASR-EVT-02: 低置信度，confidence < 0.6，low_confidence=True ─────────────

class TestLowConfidenceEvent:
    """T-ASR-EVT-02: 低置信度，confidence < 0.6，low_confidence=True，reason_code=ASR_LOW_CONFIDENCE。"""

    def test_low_confidence_true_when_below_threshold(self):
        event = _make_event(confidence=0.3)
        assert event.low_confidence is True

    def test_reason_code_asr_low_confidence(self):
        event = _make_event(confidence=0.3)
        assert event.reason_code == ASR_LOW_CONFIDENCE

    def test_boundary_just_below_threshold(self):
        event = _make_event(confidence=LOW_CONFIDENCE_THRESHOLD - 0.001)
        assert event.low_confidence is True
        assert event.reason_code == ASR_LOW_CONFIDENCE

    def test_zero_confidence_is_low(self):
        event = _make_event(confidence=0.0)
        assert event.low_confidence is True

    def test_text_still_preserved_on_low_confidence(self):
        event = _make_event(confidence=0.1, text="低信頼度テスト")
        assert event.text == "低信頼度テスト"


# ── T-ASR-EVT-03: alternates 最多 3 个（边界：4 个时截断）────────────────────

class TestAlternates:
    """T-ASR-EVT-03: alternates 最多 3 个（边界：4 个时截断）。"""

    def test_alternates_preserved_when_within_limit(self):
        event = _make_event(alternates=["alt1", "alt2"])
        assert list(event.alternates) == ["alt1", "alt2"]

    def test_alternates_truncated_to_max(self):
        event = _make_event(alternates=["a1", "a2", "a3", "a4"])
        assert len(event.alternates) == MAX_ALTERNATES

    def test_alternates_exactly_max(self):
        event = _make_event(alternates=["a1", "a2", "a3"])
        assert len(event.alternates) == 3

    def test_alternates_empty_by_default(self):
        event = _make_event()
        assert len(event.alternates) == 0

    def test_alternates_none_becomes_empty(self):
        event = _make_event(alternates=None)
        assert len(event.alternates) == 0

    def test_max_alternates_constant_is_3(self):
        assert MAX_ALTERNATES == 3


# ── T-ASR-EVT-04: evidence_ref 必填（缺失时拒绝构建）────────────────────────

class TestEvidenceRefRequired:
    """T-ASR-EVT-04: evidence_ref 必填（缺失时拒绝构建）。"""

    def test_missing_evidence_ref_raises(self):
        with pytest.raises(ValueError):
            build_asr_transcript_event(
                session_id="session_001",
                segment_id="seg_001",
                text="テスト",
                confidence=0.9,
                ts_start_ms=1000,
                ts_end_ms=2000,
                evidence_ref=None,
            )

    def test_empty_evidence_ref_raises(self):
        with pytest.raises(ValueError):
            build_asr_transcript_event(
                session_id="session_001",
                segment_id="seg_001",
                text="テスト",
                confidence=0.9,
                ts_start_ms=1000,
                ts_end_ms=2000,
                evidence_ref="",
            )

    def test_whitespace_evidence_ref_raises(self):
        with pytest.raises(ValueError):
            build_asr_transcript_event(
                session_id="session_001",
                segment_id="seg_001",
                text="テスト",
                confidence=0.9,
                ts_start_ms=1000,
                ts_end_ms=2000,
                evidence_ref="   ",
            )

    def test_valid_evidence_ref_accepted(self):
        event = _make_event(evidence_ref="evidence::valid_001")
        assert event.evidence_ref == "evidence::valid_001"


# ── T-ASR-EVT-05: is_final=True 时事件可回放（含 ts_start_ms/ts_end_ms）────────

class TestFinalEventReplay:
    """T-ASR-EVT-05: is_final=True 时事件可回放（含 ts_start_ms/ts_end_ms）。"""

    def test_ts_start_ms_preserved(self):
        event = _make_event(ts_start_ms=5000, ts_end_ms=7000, is_final=True)
        assert event.ts_start_ms == 5000

    def test_ts_end_ms_preserved(self):
        event = _make_event(ts_start_ms=5000, ts_end_ms=7000, is_final=True)
        assert event.ts_end_ms == 7000

    def test_event_id_auto_generated(self):
        event = _make_event()
        assert event.event_id
        assert len(event.event_id) > 0

    def test_custom_event_id_preserved(self):
        event = _make_event(event_id="custom_event_001")
        assert event.event_id == "custom_event_001"

    def test_as_dict_contains_timestamps(self):
        event = _make_event(ts_start_ms=1000, ts_end_ms=2000)
        d = event.as_dict()
        assert d["ts_start_ms"] == 1000
        assert d["ts_end_ms"] == 2000

    def test_frozen_dataclass_immutable(self):
        event = _make_event()
        with pytest.raises((AttributeError, TypeError)):
            event.text = "modified"  # type: ignore[misc]
