import json
import unittest
from pathlib import Path
from typing import Any

from edge_client.actuators.speech import SpeechPlaybackQueue
from edge_client.actuators.vts_driver import VtsDriverResult, VtsState
from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import STATUS_ACCEPTED, STATUS_REJECTED, VoiceRpcContract
from edge_client.runtime.voice_pipeline import EdgeVoicePipeline
from host_brain.core.errors import VOICE_INTERRUPTED, VOICE_SCHEMA_INVALID


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "edge" / "voice"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class _MockVtsDriver:
    def __init__(self) -> None:
        self.talk_start_calls = 0
        self.talk_end_calls = 0
        self.reset_calls = 0
        self.set_expression_calls: list[str] = []
        self.trigger_hotkey_calls: list[str] = []
        self.state = VtsState.AUTHENTICATED

    def set_expression(self, emotion_tag: str, *, intensity: float = 0.5) -> VtsDriverResult:
        self.set_expression_calls.append(emotion_tag)
        return VtsDriverResult(success=True, reason_code=None, detail=f"set_expression:{emotion_tag}", action="set_expression")

    def trigger_hotkey(self, hotkey_id: str) -> VtsDriverResult:
        self.trigger_hotkey_calls.append(hotkey_id)
        return VtsDriverResult(success=True, reason_code=None, detail=f"trigger_hotkey:{hotkey_id}", action="trigger_hotkey")

    def talk_start(self) -> VtsDriverResult:
        self.talk_start_calls += 1
        return VtsDriverResult(success=True, reason_code=None, detail="talk_start", action="TALK_START")

    def talk_end(self) -> VtsDriverResult:
        self.talk_end_calls += 1
        return VtsDriverResult(success=True, reason_code=None, detail="talk_end", action="TALK_END")

    def reset_to_neutral(self) -> VtsDriverResult:
        self.reset_calls += 1
        return VtsDriverResult(success=True, reason_code=None, detail="reset", action="RESET")

    def health(self) -> dict:
        return {"state": self.state.value, "mouth_parameters": {}}

    def connect(self) -> VtsDriverResult:
        return VtsDriverResult(success=True, reason_code=None, detail="connected", action="CONNECT")


class _SpySpeechPlaybackQueue(SpeechPlaybackQueue):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.start_next_calls = 0

    def start_next(self, *, ts_ms: int | None = None):  # type: ignore[override]
        self.start_next_calls += 1
        return super().start_next(ts_ms=ts_ms)


def _success_batch(total: int) -> BatchReportResult:
    return BatchReportResult(
        success=True,
        total=total,
        accepted=total,
        rejected=0,
    )


class TestEdgeVoicePipelineContract(unittest.TestCase):
    def test_t_edge_voice_01_asr_upstream_retry_cache_backfill(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "asr_upstream_recovery_accept.json")
        call_batches: list[list[dict[str, Any]]] = []

        def _transport(events: list[dict[str, Any]]) -> BatchReportResult:
            call_batches.append([dict(row) for row in events])
            if len(call_batches) == 1:
                raise ConnectionError("network down")
            return _success_batch(len(events))

        gate = VoiceResourceGate()
        reporter = EdgeEventReporter(
            transport_fn=_transport,
            max_retry=1,
            base_delay_s=0.01,
        )
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(rpc=rpc)
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
            resource_gate=gate,
        )

        first = pipeline.asr_chunk(
            request_id=fixture["first"]["request_id"],
            payload=fixture["first"]["payload"],
            ts_ms=fixture["ts_ms"],
        )
        self.assertTrue(first.rpc_result.success, msg=first.rpc_result)
        self.assertIsNotNone(first.report_result)
        assert first.report_result is not None
        self.assertFalse(first.report_result.success)
        self.assertEqual(fixture["expected"]["cache_after_first"], reporter.cache.size)

        second = pipeline.asr_chunk(
            request_id=fixture["second"]["request_id"],
            payload=fixture["second"]["payload"],
            ts_ms=fixture["ts_ms"] + 200,
        )
        self.assertTrue(second.success, msg=second)
        self.assertIsNotNone(second.report_result)
        self.assertIsNotNone(second.flush_result)
        assert second.report_result is not None
        assert second.flush_result is not None
        self.assertTrue(second.report_result.success)
        self.assertTrue(second.flush_result.success)
        self.assertEqual(fixture["expected"]["cache_after_second"], reporter.cache.size)

        self.assertEqual(3, len(call_batches))
        self.assertTrue(
            any(
                "vram_total_mb" in (row.get("payload") or {})
                and "vram_used_mb" in (row.get("payload") or {})
                and "queue_depth" in (row.get("payload") or {})
                for batch in call_batches
                for row in batch
            ),
            msg=call_batches,
        )

    def test_t_edge_voice_01_invalid_asr_payload_rejected_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "asr_payload_invalid_reject.json")
        sent: list[dict[str, Any]] = []

        def _transport(events: list[dict[str, Any]]) -> BatchReportResult:
            sent.extend(events)
            return _success_batch(len(events))

        reporter = EdgeEventReporter(transport_fn=_transport, max_retry=1)
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(rpc=rpc)
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
        )

        result = pipeline.asr_chunk(
            request_id=fixture["request_id"],
            payload=fixture["payload"],
            ts_ms=fixture["ts_ms"],
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_REJECTED, result.rpc_result.status)
        self.assertEqual(VOICE_SCHEMA_INVALID, result.rpc_result.reason_code)
        self.assertIsNotNone(result.report_result)
        assert result.report_result is not None
        self.assertTrue(result.report_result.success)
        self.assertEqual(1, len(sent))
        self.assertEqual(VOICE_SCHEMA_INVALID, sent[0]["reason_code"])

    def test_t_edge_voice_01_asr_emotion_fields_reported(self) -> None:
        sent: list[dict[str, Any]] = []

        def _transport(events: list[dict[str, Any]]) -> BatchReportResult:
            sent.extend(events)
            return _success_batch(len(events))

        reporter = EdgeEventReporter(transport_fn=_transport, max_retry=1)
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(rpc=rpc)
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
        )

        result = pipeline.asr_chunk(
            request_id="req_edge_emotion_001",
            payload={
                "session_id": "sess_edge_emotion_001",
                "text": "今天真的好开心！",
                "seq": 1,
                "confidence": 0.96,
                "final": True,
            },
            ts_ms=1_800_102_300_000,
        )

        self.assertTrue(result.success, msg=result)
        self.assertEqual(1, len(sent))
        self.assertEqual("HAPPY", sent[0]["emotion_tag"])
        self.assertGreater(float(sent[0]["emotion_intensity"]), 0.2)
        self.assertEqual("HAPPY", sent[0]["payload"]["emotion_tag"])
        self.assertEqual(sent[0]["emotion_intensity"], sent[0]["payload"]["emotion_intensity"])

    def test_t_edge_voice_02_barge_in_interrupts_and_vts_resets(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "barge_in_vts_linkage_accept.json")
        vts = _MockVtsDriver()
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(
            rpc=rpc,
            synthesizer=lambda _: {"audio_ref": "mem://audio"},
            playback_starter=lambda _s, _a: None,
        )
        reporter = EdgeEventReporter(
            transport_fn=lambda events: _success_batch(len(events)),
            max_retry=1,
        )
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
            vts_driver=vts,
        )

        start = pipeline.enqueue_speak(
            request_id=fixture["speak"]["request_id"],
            payload=fixture["speak"]["payload"],
            ts_ms=1_800_102_100_000,
            auto_start=True,
        )
        self.assertTrue(start.success, msg=start)
        self.assertEqual(STATUS_ACCEPTED, start.queue_result.status)
        self.assertIsNotNone(start.start_result)
        assert start.start_result is not None
        self.assertTrue(start.start_result.success)
        self.assertGreaterEqual(vts.talk_start_calls, 1)

        interrupted = pipeline.barge_in(
            request_id=fixture["barge_in"]["request_id"],
            payload=fixture["barge_in"]["payload"],
            ts_ms=1_800_102_100_001,
        )
        self.assertTrue(interrupted.success, msg=interrupted)
        self.assertEqual(VOICE_INTERRUPTED, interrupted.barge_result.reason_code)
        self.assertEqual(0, vts.reset_calls)

        start_second = pipeline.enqueue_speak(
            request_id=fixture["speak_second"]["request_id"],
            payload=fixture["speak_second"]["payload"],
            ts_ms=1_800_102_100_002,
            auto_start=True,
        )
        self.assertTrue(start_second.success)
        done = pipeline.complete_playback(ts_ms=1_800_102_100_003)
        self.assertIsNotNone(done)
        assert done is not None
        self.assertTrue(done.success)
        self.assertGreaterEqual(vts.talk_end_calls, 1)

    def test_t_edge_voice_02_enqueue_autostart_invokes_start_once(self) -> None:
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = _SpySpeechPlaybackQueue(
            rpc=rpc,
            synthesizer=lambda _: {"audio_ref": "mem://audio"},
            playback_starter=lambda _s, _a: None,
        )
        reporter = EdgeEventReporter(
            transport_fn=lambda events: _success_batch(len(events)),
            max_retry=1,
        )
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
            vts_driver=_MockVtsDriver(),
        )

        result = pipeline.enqueue_speak(
            request_id="req_edge_voice_once",
            payload={
                "session_id": "sess_edge_voice_once",
                "utterance_id": "utt_edge_voice_once",
                "text": "联调语音播放",
                "synchronization_id": "sync-edge-voice-once",
            },
            ts_ms=1_800_102_200_000,
            auto_start=True,
        )

        self.assertTrue(result.success, msg=result)
        self.assertEqual(1, queue.start_next_calls)
        self.assertIsNotNone(result.start_result)
        assert result.start_result is not None
        self.assertTrue(result.start_result.success, msg=result.start_result)
        self.assertEqual(
            "sync-edge-voice-once",
            result.start_result.event["synchronization_id"],
        )

    def test_t_edge_voice_02_enqueue_while_active_is_reported_as_queued(self) -> None:
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(
            rpc=rpc,
            synthesizer=lambda _: {"audio_ref": "mem://audio"},
            playback_starter=lambda _s, _a: None,
        )
        reporter = EdgeEventReporter(
            transport_fn=lambda events: _success_batch(len(events)),
            max_retry=1,
        )
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
            vts_driver=_MockVtsDriver(),
        )

        first = pipeline.enqueue_speak(
            request_id="req_edge_voice_active_1",
            payload={
                "session_id": "sess_edge_voice_active",
                "utterance_id": "utt_edge_voice_active_1",
                "text": "第一条语音",
            },
            ts_ms=1_800_102_210_000,
            auto_start=True,
        )
        self.assertTrue(first.success, msg=first)
        self.assertIsNotNone(queue.active_playback)

        second = pipeline.enqueue_speak(
            request_id="req_edge_voice_active_2",
            payload={
                "session_id": "sess_edge_voice_active",
                "utterance_id": "utt_edge_voice_active_2",
                "text": "第二条语音",
            },
            ts_ms=1_800_102_210_001,
            auto_start=True,
        )

        self.assertTrue(second.success, msg=second)
        self.assertIsNotNone(second.start_result)
        assert second.start_result is not None
        self.assertTrue(second.start_result.success, msg=second.start_result)
        self.assertTrue(second.start_result.event.get("waiting_for_active_playback"))
        self.assertEqual("waiting for active playback to finish", second.start_result.detail)

        done = pipeline.complete_playback(ts_ms=1_800_102_210_002)
        self.assertIsNotNone(done)
        active = queue.active_playback
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual("req_edge_voice_active_2", active.get("request_id"))

    def test_t_edge_voice_02_invalid_barge_in_payload_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "barge_in_missing_session_reject.json")
        rpc = VoiceRpcContract(asr_min_interval_ms=0)
        queue = SpeechPlaybackQueue(
            rpc=rpc,
            synthesizer=lambda _: {"audio_ref": "mem://audio"},
            playback_starter=lambda _s, _a: None,
        )
        reporter = EdgeEventReporter(
            transport_fn=lambda events: _success_batch(len(events)),
            max_retry=1,
        )
        pipeline = EdgeVoicePipeline(
            voice_rpc=rpc,
            speech_queue=queue,
            event_reporter=reporter,
            vts_driver=_MockVtsDriver(),
        )

        result = pipeline.barge_in(
            request_id=fixture["request_id"],
            payload=fixture["payload"],
            ts_ms=fixture["ts_ms"],
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_REJECTED, result.barge_result.status)
        self.assertEqual(VOICE_SCHEMA_INVALID, result.barge_result.reason_code)


if __name__ == "__main__":
    unittest.main()
