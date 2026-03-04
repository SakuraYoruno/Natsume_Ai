"""T-BARGE-01~04: Barge-in 可打断强化单元测试（M15-C）。

覆盖需求：FR-VOICE-003
"""

from __future__ import annotations

import pytest

from edge_client.actuators.tts_engine import (
    BargeInEvent,
    build_barge_in_event,
)
from host_brain.core.errors import VOICE_SCHEMA_INVALID


NOW_MS = 1_800_000_000_000
GOOD_EVIDENCE_REF = "evidence::barge_001"


def _make_event(**kwargs) -> BargeInEvent:
    defaults = dict(
        session_id="session_001",
        interrupted_request_id="req_playing_001",
        trigger_ts_ms=NOW_MS,
        evidence_ref=GOOD_EVIDENCE_REF,
        asr_continued=True,
    )
    defaults.update(kwargs)
    return build_barge_in_event(**defaults)


# ── T-BARGE-01: 打断立即生效，interrupted_request_id 可追溯 ──────────────────

class TestBargeInImmediate:
    """T-BARGE-01: 打断立即生效，interrupted_request_id 可追溯。"""

    def test_returns_barge_in_event(self):
        event = _make_event()
        assert isinstance(event, BargeInEvent)

    def test_interrupted_request_id_preserved(self):
        event = _make_event(interrupted_request_id="req_playing_001")
        assert event.interrupted_request_id == "req_playing_001"

    def test_trigger_ts_ms_preserved(self):
        event = _make_event(trigger_ts_ms=NOW_MS)
        assert event.trigger_ts_ms == NOW_MS

    def test_session_id_preserved(self):
        event = _make_event(session_id="session_barge_001")
        assert event.session_id == "session_barge_001"

    def test_evidence_ref_preserved(self):
        event = _make_event(evidence_ref="evidence::barge_test_001")
        assert event.evidence_ref == "evidence::barge_test_001"

    def test_as_dict_structure(self):
        event = _make_event()
        d = event.as_dict()
        assert "event_id" in d
        assert "session_id" in d
        assert "interrupted_request_id" in d
        assert "trigger_ts_ms" in d
        assert "asr_continued" in d
        assert "evidence_ref" in d


# ── T-BARGE-02: 打断后 ASR 继续上送（asr_continued=True）────────────────────

class TestASRContinued:
    """T-BARGE-02: 打断后 ASR 继续上送（asr_continued=True）。"""

    def test_asr_continued_true_by_default(self):
        event = _make_event()
        assert event.asr_continued is True

    def test_asr_continued_explicit_true(self):
        event = _make_event(asr_continued=True)
        assert event.asr_continued is True

    def test_asr_continued_can_be_false(self):
        """asr_continued=False 是合法值（特殊场景）。"""
        event = _make_event(asr_continued=False)
        assert event.asr_continued is False


# ── T-BARGE-03: 打断不产生执行事实（无 intent_draft）────────────────────────

class TestNoExecutionFact:
    """T-BARGE-03: 打断不产生执行事实（无 intent_draft 字段）。"""

    def test_no_intent_draft_field(self):
        event = _make_event()
        d = event.as_dict()
        assert "intent_draft" not in d

    def test_no_execution_claim_field(self):
        event = _make_event()
        d = event.as_dict()
        assert "execution_claim" not in d

    def test_no_decision_field(self):
        event = _make_event()
        d = event.as_dict()
        assert "decision" not in d

    def test_frozen_dataclass_immutable(self):
        event = _make_event()
        with pytest.raises((AttributeError, TypeError)):
            event.asr_continued = False  # type: ignore[misc]


# ── T-BARGE-04: 打断事件写入审计链（evidence_ref 存在）──────────────────────

class TestBargeInAudit:
    """T-BARGE-04: 打断事件写入审计链（evidence_ref 存在）。"""

    def test_evidence_ref_present(self):
        event = _make_event()
        assert event.evidence_ref
        assert len(event.evidence_ref) > 0

    def test_event_id_auto_generated(self):
        event = _make_event()
        assert event.event_id
        assert len(event.event_id) > 0

    def test_custom_event_id_preserved(self):
        event = _make_event(event_id="custom_barge_001")
        assert event.event_id == "custom_barge_001"

    def test_missing_interrupted_request_id_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_barge_in_event(
                session_id="session_001",
                interrupted_request_id="",
                trigger_ts_ms=NOW_MS,
                evidence_ref=GOOD_EVIDENCE_REF,
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_missing_evidence_ref_raises(self):
        with pytest.raises(ValueError) as exc_info:
            build_barge_in_event(
                session_id="session_001",
                interrupted_request_id="req_001",
                trigger_ts_ms=NOW_MS,
                evidence_ref="",
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
