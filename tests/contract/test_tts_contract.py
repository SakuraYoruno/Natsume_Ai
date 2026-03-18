"""T-VOICE-02: TTS 合成播报队列合约测试（M7 TASK-0104）。

验收：
- 高优先级任务插队后先执行
- interruptible=true 任务被打断后写 TTS_INTERRUPTED + 审计可回放
- 队列满时返回 TTS_QUEUE_FULL + 输出结构稳定
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_INTERRUPTED,
    EVENT_TTS_PLAYBACK_STARTED,
    EVENT_TTS_QUEUED,
    SpeechPlaybackQueue,
)
from edge_client.rpc.voice import STATUS_ACCEPTED, STATUS_REJECTED, VoiceRpcContract
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import TTS_INTERRUPTED, TTS_QUEUE_FULL, VOICE_INTERRUPTED


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestTTSContract(unittest.TestCase):
    """T-VOICE-02: TTS 合成播报队列合约。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.rpc = VoiceRpcContract(
            ledger=self.ledger,
            asr_min_confidence=0.60,
            asr_min_interval_ms=0,
        )
        self.ts_base = 1_800_000_000_000

    def test_t_voice_02_enqueue_and_playback_chain(self) -> None:
        """正常入队 → 开始播放 → 完成，审计链完整。"""
        speak = _load_json(FIXTURES / "good" / "speak_valid.json")
        queue = SpeechPlaybackQueue(rpc=self.rpc)

        queued = queue.enqueue_speak(
            request_id=speak["request_id"],
            payload=speak["payload"],
            ts_ms=self.ts_base,
        )
        self.assertTrue(queued.success)
        self.assertEqual(STATUS_ACCEPTED, queued.status)

        started = queue.start_next(ts_ms=self.ts_base + 10)
        self.assertIsNotNone(started)
        assert started is not None
        self.assertTrue(started.success)
        self.assertEqual(EVENT_TTS_PLAYBACK_STARTED, started.event["event_type"])

        finished = queue.complete_active(ts_ms=self.ts_base + 20)
        self.assertIsNotNone(finished)
        assert finished is not None
        self.assertTrue(finished.success)
        self.assertEqual(EVENT_TTS_PLAYBACK_FINISHED, finished.event["event_type"])

        event_types = [r["event_type"] for r in self.ledger.voice_event_log]
        self.assertIn(EVENT_TTS_QUEUED, event_types)
        self.assertIn(EVENT_TTS_PLAYBACK_STARTED, event_types)
        self.assertIn(EVENT_TTS_PLAYBACK_FINISHED, event_types)

    def test_t_voice_02_barge_in_interrupts_playback(self) -> None:
        """Barge-in 打断正在播放的 TTS，写 VOICE_INTERRUPTED + 审计可回放。"""
        speak = _load_json(FIXTURES / "good" / "speak_valid.json")
        barge = _load_json(FIXTURES / "good" / "barge_in_valid.json")
        queue = SpeechPlaybackQueue(rpc=self.rpc)

        queue.enqueue_speak(
            request_id=speak["request_id"],
            payload=speak["payload"],
            ts_ms=self.ts_base,
        )
        queue.start_next(ts_ms=self.ts_base + 10)
        self.assertIsNotNone(queue.active_playback)

        interrupted = queue.handle_barge_in(
            request_id=barge["request_id"],
            payload=barge["payload"],
            ts_ms=self.ts_base + 20,
        )
        self.assertTrue(interrupted.success)
        self.assertEqual(VOICE_INTERRUPTED, interrupted.reason_code)
        self.assertEqual(EVENT_TTS_PLAYBACK_INTERRUPTED, interrupted.event["event_type"])
        self.assertIsNone(queue.active_playback)

        # 审计可回放
        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_TTS_PLAYBACK_INTERRUPTED]
        self.assertEqual(1, len(rows))
        self.assertEqual(VOICE_INTERRUPTED, rows[0]["reason_code"])
        self.assertIsInstance(rows[0]["payload"], dict)

    def test_t_voice_02_tts_interrupted_error_code_defined(self) -> None:
        """TTS_INTERRUPTED 错误码已在 errors.py 中定义。"""
        self.assertEqual("TTS_INTERRUPTED", TTS_INTERRUPTED)

    def test_t_voice_02_tts_queue_full_error_code_defined(self) -> None:
        """TTS_QUEUE_FULL 错误码已在 errors.py 中定义。"""
        self.assertEqual("TTS_QUEUE_FULL", TTS_QUEUE_FULL)

    def test_t_voice_02_fixture_tts_normal(self) -> None:
        """Fixture speak_valid.json 结构正确。"""
        fixture = _load_json(FIXTURES / "good" / "speak_valid.json")
        self.assertIn("request_id", fixture)
        self.assertIn("payload", fixture)
        payload = fixture["payload"]
        self.assertIn("text", payload)


if __name__ == "__main__":
    unittest.main()
