"""T-VOICE-03: Barge-in 打断合约测试（M7 TASK-0105）。

验收：
- Barge-in 触发后 TTS 立即打断 + 审计记录 BARGE_IN_INTERRUPTED
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_INTERRUPTED,
    SpeechPlaybackQueue,
)
from edge_client.rpc.voice import (
    EVENT_BARGE_IN,
    S_VOICE_BARGE_IN,
    VoiceRpcContract,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_INTERRUPTED,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestBargeInContract(unittest.TestCase):
    """T-VOICE-03: Barge-in 打断合约。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.rpc = VoiceRpcContract(
            ledger=self.ledger,
            asr_min_confidence=0.60,
            asr_min_interval_ms=0,
        )
        self.ts_base = 1_800_000_000_000

    def test_t_voice_03_barge_in_interrupts_tts(self) -> None:
        """Barge-in 触发后 TTS 立即打断，审计记录 VOICE_INTERRUPTED。"""
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
        interrupted_rows = [
            r for r in self.ledger.voice_event_log
            if r["event_type"] == EVENT_TTS_PLAYBACK_INTERRUPTED
        ]
        self.assertEqual(1, len(interrupted_rows))
        self.assertEqual(VOICE_INTERRUPTED, interrupted_rows[0]["reason_code"])

    def test_t_voice_03_barge_in_rpc_event_logged(self) -> None:
        """barge_in RPC 事件写入 voice_event_log。"""
        barge = _load_json(FIXTURES / "good" / "barge_in_valid.json")
        result = self.rpc.barge_in(
            request_id=barge["request_id"],
            payload=barge["payload"],
            ts_ms=self.ts_base,
        )
        self.assertTrue(result.success)
        self.assertEqual(S_VOICE_BARGE_IN, result.stage)
        self.assertEqual(EVENT_BARGE_IN, result.event["event_type"])

        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_BARGE_IN]
        self.assertEqual(1, len(rows))
        self.assertEqual(barge["request_id"], rows[0]["request_id"])

if __name__ == "__main__":
    unittest.main()
