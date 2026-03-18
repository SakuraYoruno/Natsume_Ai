import json
import unittest
from pathlib import Path

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_INTERRUPTED,
    EVENT_TTS_PLAYBACK_STARTED,
    EVENT_TTS_QUEUED,
    EVENT_TTS_SYNTH_FAILED,
    EVENT_TTS_SYNTH_READY,
    S_VOICE_TTS_PLAYBACK,
    S_VOICE_TTS_SYNTH,
    SpeechPlaybackQueue,
)
from edge_client.rpc.voice import EVENT_ASR_CHUNK, STATUS_ACCEPTED, STATUS_REJECTED, VoiceRpcContract
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import VOICE_INTERRUPTED, VOICE_SYNTH_FAILED


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestVoiceTtsQueueContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_500_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.rpc = VoiceRpcContract(
            ledger=self.ledger,
            asr_min_confidence=0.60,
            asr_min_interval_ms=120,
        )

    def test_t_voice_02_speak_queue_has_replayable_chain_and_failure_reason(self) -> None:
        speak_ok = _load_json(FIXTURES / "good" / "speak_valid.json")
        speak_fail = _load_json(FIXTURES / "bad" / "speak_synth_failure.json")

        def _synth(speak_event: dict) -> dict:
            if speak_event.get("utterance_id") == "utt_voice_fail":
                raise RuntimeError("mock synth down")
            return {"audio_ref": f"mem://{speak_event.get('utterance_id')}"}

        queue = SpeechPlaybackQueue(rpc=self.rpc, synthesizer=_synth)

        queued = queue.enqueue_speak(
            request_id=speak_ok["request_id"],
            payload=speak_ok["payload"],
            ts_ms=self.now_ms,
        )
        self.assertTrue(queued.success)
        self.assertEqual(STATUS_ACCEPTED, queued.status)

        started = queue.start_next(ts_ms=self.now_ms + 1)
        self.assertIsNotNone(started)
        self.assertTrue(started.success)
        self.assertEqual(EVENT_TTS_PLAYBACK_STARTED, started.event["event_type"])

        finished = queue.complete_active(ts_ms=self.now_ms + 2)
        self.assertIsNotNone(finished)
        self.assertTrue(finished.success)
        self.assertEqual(EVENT_TTS_PLAYBACK_FINISHED, finished.event["event_type"])

        queued_fail = queue.enqueue_speak(
            request_id=speak_fail["request_id"],
            payload=speak_fail["payload"],
            ts_ms=self.now_ms + 10,
        )
        self.assertTrue(queued_fail.success)

        failed = queue.start_next(ts_ms=self.now_ms + 11)
        self.assertIsNotNone(failed)
        self.assertFalse(failed.success)
        self.assertEqual(STATUS_REJECTED, failed.status)
        self.assertEqual(VOICE_SYNTH_FAILED, failed.reason_code)
        self.assertEqual(S_VOICE_TTS_SYNTH, failed.stage)

        event_types = [row["event_type"] for row in self.ledger.voice_event_log]
        self.assertIn(EVENT_TTS_QUEUED, event_types)
        self.assertIn(EVENT_TTS_SYNTH_READY, event_types)
        self.assertIn(EVENT_TTS_PLAYBACK_STARTED, event_types)
        self.assertIn(EVENT_TTS_PLAYBACK_FINISHED, event_types)
        self.assertIn(EVENT_TTS_SYNTH_FAILED, event_types)
        self.assertTrue(
            any(
                row["request_id"] == speak_fail["request_id"]
                and row["event_type"] == EVENT_TTS_SYNTH_FAILED
                and row["reason_code"] == VOICE_SYNTH_FAILED
                for row in self.ledger.voice_event_log
            ),
            msg=self.ledger.voice_event_log,
        )

    def test_t_voice_03_barge_in_interrupts_playback_and_asr_keeps_working(self) -> None:
        speak = _load_json(FIXTURES / "good" / "speak_valid.json")
        barge = _load_json(FIXTURES / "good" / "barge_in_valid.json")
        asr = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")

        queue = SpeechPlaybackQueue(rpc=self.rpc)

        queued = queue.enqueue_speak(
            request_id=speak["request_id"],
            payload=speak["payload"],
            ts_ms=self.now_ms + 20,
        )
        self.assertTrue(queued.success)

        started = queue.start_next(ts_ms=self.now_ms + 21)
        self.assertIsNotNone(started)
        self.assertTrue(started.success)
        self.assertIsNotNone(queue.active_playback)

        interrupted = queue.handle_barge_in(
            request_id=barge["request_id"],
            payload=barge["payload"],
            ts_ms=self.now_ms + 22,
        )
        self.assertTrue(interrupted.success)
        self.assertEqual(STATUS_ACCEPTED, interrupted.status)
        self.assertEqual(VOICE_INTERRUPTED, interrupted.reason_code)
        self.assertEqual(S_VOICE_TTS_PLAYBACK, interrupted.stage)
        self.assertEqual(EVENT_TTS_PLAYBACK_INTERRUPTED, interrupted.event["event_type"])
        self.assertIsNone(queue.active_playback)

        asr_ok = self.rpc.asr_chunk(
            request_id=asr["request_id"],
            payload=asr["payload"],
            ts_ms=self.now_ms + 23,
        )
        self.assertTrue(asr_ok.success)
        self.assertEqual(STATUS_ACCEPTED, asr_ok.status)
        self.assertEqual(EVENT_ASR_CHUNK, asr_ok.event["event_type"])

        self.assertTrue(
            any(
                row["event_type"] == EVENT_TTS_PLAYBACK_INTERRUPTED
                and row["reason_code"] == VOICE_INTERRUPTED
                for row in self.ledger.voice_event_log
            ),
            msg=self.ledger.voice_event_log,
        )
        self.assertTrue(
            any(
                row["request_id"] == asr["request_id"]
                and row["event_type"] == EVENT_ASR_CHUNK
                and row["status"] == STATUS_ACCEPTED
                for row in self.ledger.voice_event_log
            ),
            msg=self.ledger.voice_event_log,
        )


if __name__ == "__main__":
    unittest.main()
