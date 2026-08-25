"""T-RGATE-01~05: Edge 资源闸门强化单元测试（M15-E）。

覆盖需求：FR-VOICE-005
"""

from __future__ import annotations

import pytest

from edge_client.rpc.resource_gate import (
    TASK_ASR_INGRESS,
    TASK_PLAYBACK,
    TASK_TTS_SYNTH,
    ResourcePreemptionEvent,
    VoiceResourceGate,
    build_resource_preemption_event,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import RESOURCE_GATE_PREEMPTED, VOICE_GATE_PREEMPTED


NOW_MS = 1_800_000_000_000
GOOD_EVIDENCE_REF = "evidence::rgate_001"


@pytest.fixture
def gate() -> VoiceResourceGate:
    ledger = SQLiteLedger(db_path=":memory:")
    return VoiceResourceGate(ledger=ledger)


@pytest.fixture
def gate_no_ledger() -> VoiceResourceGate:
    return VoiceResourceGate()


# ── T-RGATE-01: ASR 抢占 TTS_SYNTH，ResourcePreemptionEvent 写入审计链 ────────

class TestASRPreemptsTS:
    """T-RGATE-01: ASR 抢占 TTS_SYNTH，ResourcePreemptionEvent 写入审计链。"""

    def test_asr_preempts_tts_synth(self, gate):
        # 先让 TTS_SYNTH 占用资源
        gate.request(
            request_id="req_tts_001",
            task=TASK_TTS_SYNTH,
            session_id="session_001",
            ts_ms=NOW_MS,
        )
        # ASR 抢占 TTS_SYNTH
        decision = gate.request(
            request_id="req_asr_001",
            task=TASK_ASR_INGRESS,
            session_id="session_001",
            ts_ms=NOW_MS + 100,
        )
        assert decision.admitted is True
        assert TASK_TTS_SYNTH in decision.event.get("preempted_tasks", [])

    def test_preemption_event_can_be_built(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert isinstance(event, ResourcePreemptionEvent)
        assert event.preemptor == TASK_ASR_INGRESS
        assert event.preempted == TASK_TTS_SYNTH
        assert event.reason_code == RESOURCE_GATE_PREEMPTED

    def test_preemption_event_evidence_ref(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert event.evidence_ref == GOOD_EVIDENCE_REF


# ── T-RGATE-02: PLAYBACK 抢占 TTS_SYNTH（ASR 不在运行时）────────────────────

class TestPlaybackPreemptsTS:
    """T-RGATE-03: PLAYBACK 抢占 TTS_SYNTH（ASR 不在运行时）。"""

    def test_playback_preempts_tts_synth(self, gate):
        gate.request(
            request_id="req_tts_002",
            task=TASK_TTS_SYNTH,
            session_id="session_001",
            ts_ms=NOW_MS,
        )
        decision = gate.request(
            request_id="req_playback_001",
            task=TASK_PLAYBACK,
            session_id="session_001",
            ts_ms=NOW_MS + 100,
        )
        assert decision.admitted is True
        assert TASK_TTS_SYNTH in decision.event.get("preempted_tasks", [])

# ── T-RGATE-03: 压力下 ASR 始终保证（不被任何任务抢占）──────────────────────

class TestASRAlwaysGuaranteed:
    """T-RGATE-04: 压力下 ASR 始终保证（不被任何任务抢占）。"""

    def test_asr_not_blocked_by_playback(self, gate):
        gate.request(
            request_id="req_playback_003",
            task=TASK_PLAYBACK,
            session_id="session_001",
            ts_ms=NOW_MS,
        )
        decision = gate.request(
            request_id="req_asr_003",
            task=TASK_ASR_INGRESS,
            session_id="session_001",
            ts_ms=NOW_MS + 100,
        )
        assert decision.admitted is True

    def test_asr_not_blocked_by_tts_synth(self, gate):
        gate.request(
            request_id="req_tts_003",
            task=TASK_TTS_SYNTH,
            session_id="session_001",
            ts_ms=NOW_MS,
        )
        decision = gate.request(
            request_id="req_asr_004",
            task=TASK_ASR_INGRESS,
            session_id="session_001",
            ts_ms=NOW_MS + 100,
        )
        assert decision.admitted is True

    def test_asr_highest_priority(self, gate):
        """ASR 是最高优先级，不被任何任务阻塞。"""
        from edge_client.rpc.resource_gate import TASK_PRIORITY
        assert TASK_PRIORITY[TASK_ASR_INGRESS] == 0


# ── T-RGATE-04: 抢占事件含 preemptor/preempted/evidence_ref ──────────────────

class TestPreemptionEventFields:
    """T-RGATE-05: 抢占事件含 preemptor/preempted/evidence_ref。"""

    def test_event_has_preemptor(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert event.preemptor == TASK_ASR_INGRESS

    def test_event_has_preempted(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert event.preempted == TASK_TTS_SYNTH

    def test_event_has_evidence_ref(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert event.evidence_ref == GOOD_EVIDENCE_REF

    def test_event_has_ts_ms(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
            ts_ms=NOW_MS,
        )
        assert event.ts_ms == NOW_MS

    def test_event_id_auto_generated(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
        )
        assert event.event_id
        assert len(event.event_id) > 0

    def test_missing_evidence_ref_raises(self):
        with pytest.raises(ValueError):
            build_resource_preemption_event(
                preemptor=TASK_ASR_INGRESS,
                preempted=TASK_TTS_SYNTH,
                evidence_ref="",
            )

    def test_invalid_preemptor_raises(self):
        with pytest.raises(ValueError):
            build_resource_preemption_event(
                preemptor="INVALID_TASK",
                preempted=TASK_TTS_SYNTH,
                evidence_ref=GOOD_EVIDENCE_REF,
            )

    def test_frozen_dataclass_immutable(self):
        event = build_resource_preemption_event(
            preemptor=TASK_ASR_INGRESS,
            preempted=TASK_TTS_SYNTH,
            evidence_ref=GOOD_EVIDENCE_REF,
        )
        with pytest.raises((AttributeError, TypeError)):
            event.preemptor = "MODIFIED"  # type: ignore[misc]
