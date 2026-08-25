import json
import unittest
from pathlib import Path

from edge_client.actuators.speech import SpeechPlaybackQueue
from edge_client.rpc.resource_gate import (
    EVENT_VOICE_RESOURCE_GATE,
    S_VOICE_RESOURCE_GATE,
    STATUS_ADMITTED,
    STATUS_PREEMPTED,
    STATUS_REJECTED,
    TASK_ASR_INGRESS,
    TASK_PLAYBACK,
    TASK_TTS_SYNTH,
    VoiceResourceGate,
)
from edge_client.rpc.voice import STATUS_ACCEPTED, VoiceRpcContract
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_GATE_PREEMPTED,
    VOICE_GATE_PRIORITY_BLOCKED,
    VOICE_GATE_RESOURCE_LIMITED,
    VOICE_GATE_TASK_INVALID,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestVoiceResourceGateContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_700_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.gate = VoiceResourceGate(
            ledger=self.ledger,
            vram_soft_limit_ratio=0.90,
        )

    def test_t_voice_05_priority_and_pressure_observability(self) -> None:
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")
        pressure = _load_json(FIXTURES / "good" / "resource_gate_pressure.json")
        bad = _load_json(FIXTURES / "bad" / "resource_gate_unknown_task.json")

        playback = self.gate.request(
            request_id=relaxed["request_id"],
            task=TASK_PLAYBACK,
            session_id=relaxed["session_id"],
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.now_ms,
        )
        self.assertTrue(playback.admitted)
        self.assertEqual(STATUS_ADMITTED, playback.status)
        self.assertEqual(S_VOICE_RESOURCE_GATE, playback.stage)

        tts_blocked = self.gate.request(
            request_id="req_voice_053",
            task=TASK_TTS_SYNTH,
            session_id=relaxed["session_id"],
            metrics=relaxed["metrics"],
            hold=False,
            ts_ms=self.now_ms + 1,
        )
        self.assertFalse(tts_blocked.admitted)
        self.assertEqual(VOICE_GATE_PRIORITY_BLOCKED, tts_blocked.reason_code)
        self.assertEqual(STATUS_REJECTED, tts_blocked.status)

        asr_preempt = self.gate.request(
            request_id=pressure["request_id"],
            task=TASK_ASR_INGRESS,
            session_id=pressure["session_id"],
            metrics=pressure["metrics"],
            hold=False,
            ts_ms=self.now_ms + 2,
        )
        self.assertTrue(asr_preempt.admitted)
        self.assertEqual(STATUS_ADMITTED, asr_preempt.status)
        self.assertEqual(VOICE_GATE_PREEMPTED, asr_preempt.reason_code)
        self.assertEqual(["PLAYBACK"], asr_preempt.event["preempted_tasks"])

        tts_limited = self.gate.request(
            request_id="req_voice_054",
            task=TASK_TTS_SYNTH,
            session_id=pressure["session_id"],
            metrics=pressure["metrics"],
            hold=False,
            ts_ms=self.now_ms + 3,
        )
        self.assertFalse(tts_limited.admitted)
        self.assertEqual(VOICE_GATE_RESOURCE_LIMITED, tts_limited.reason_code)

        unknown = self.gate.request(
            request_id=bad["request_id"],
            task=bad["task"],
            session_id=bad["session_id"],
            metrics=bad["metrics"],
            hold=False,
            ts_ms=self.now_ms + 5,
        )
        self.assertFalse(unknown.admitted)
        self.assertEqual(VOICE_GATE_TASK_INVALID, unknown.reason_code)

        snapshot = self.gate.snapshot()
        self.assertEqual(S_VOICE_RESOURCE_GATE, snapshot["stage"])
        self.assertIn("vram_total_mb", snapshot["metrics"])
        self.assertIn("vram_used_mb", snapshot["metrics"])
        self.assertIn("queue_depth", snapshot["metrics"])
        self.assertEqual([], snapshot["active_tasks"])

        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_VOICE_RESOURCE_GATE]
        self.assertGreaterEqual(len(rows), 6)
        self.assertTrue(any(r["status"] == STATUS_PREEMPTED for r in rows), msg=rows)
        self.assertTrue(
            any(
                r["status"] == STATUS_REJECTED and r["reason_code"] == VOICE_GATE_RESOURCE_LIMITED
                for r in rows
            ),
            msg=rows,
        )
        self.assertTrue(
            all("metrics" in (r["payload"] or {}) for r in rows),
            msg=rows,
        )

    def test_t_voice_05_integration_prefers_asr_over_playback_and_tts(self) -> None:
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")
        speak = _load_json(FIXTURES / "good" / "speak_valid.json")
        asr = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")

        voice_rpc = VoiceRpcContract(ledger=self.ledger, resource_gate=self.gate)
        queue = SpeechPlaybackQueue(rpc=voice_rpc, resource_gate=self.gate)
        queued = queue.enqueue_speak(
            request_id=speak["request_id"],
            payload=speak["payload"],
            ts_ms=self.now_ms + 10,
        )
        self.assertTrue(queued.success)
        started = queue.start_next(ts_ms=self.now_ms + 11)
        self.assertIsNotNone(started)
        self.assertTrue(started.success)

        tts_blocked = self.gate.request(
            request_id="req_voice_055",
            task=TASK_TTS_SYNTH,
            session_id=relaxed["session_id"],
            metrics=relaxed["metrics"],
            hold=False,
            ts_ms=self.now_ms + 12,
        )
        self.assertFalse(tts_blocked.admitted)
        self.assertEqual(VOICE_GATE_PRIORITY_BLOCKED, tts_blocked.reason_code)

        asr_payload = dict(asr["payload"])
        asr_payload.update(relaxed["metrics"])
        asr_ok = voice_rpc.asr_chunk(
            request_id=asr["request_id"],
            payload=asr_payload,
            ts_ms=self.now_ms + 13,
        )
        self.assertTrue(asr_ok.success)
        self.assertEqual(STATUS_ACCEPTED, asr_ok.status)

        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_VOICE_RESOURCE_GATE]
        self.assertTrue(
            any(
                r["status"] == STATUS_PREEMPTED
                and (r["payload"] or {}).get("preempted_by_task") == TASK_ASR_INGRESS
                for r in rows
            ),
            msg=rows,
        )


if __name__ == "__main__":
    unittest.main()
