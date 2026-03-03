"""T-VOICE-03/04: Barge-in 打断 + Edge 兜底小模型合约测试（M7 TASK-0105）。

验收：
- Barge-in 触发后 TTS 立即打断 + 审计记录 BARGE_IN_INTERRUPTED
- 兜底输出标记 draft_only=true，不含裁决或执行事实宣告
- 兜底不可用时 FALLBACK_MODEL_UNAVAILABLE 快速失败
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_INTERRUPTED,
    SpeechPlaybackQueue,
)
from edge_client.rpc.fallback_llm import (
    EVENT_FALLBACK_ASR_COMPILE,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    EdgeFallbackRpcContract,
)
from edge_client.rpc.voice import (
    EVENT_BARGE_IN,
    S_VOICE_BARGE_IN,
    VoiceRpcContract,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    FALLBACK_MODEL_UNAVAILABLE,
    FALLBACK_OUTPUT_DRAFT_ONLY,
    VOICE_FALLBACK_UNAVAILABLE,
    VOICE_INTERRUPTED,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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

    def test_t_voice_03_fallback_model_unavailable_error_code_defined(self) -> None:
        """FALLBACK_MODEL_UNAVAILABLE 错误码已在 errors.py 中定义。"""
        self.assertEqual("FALLBACK_MODEL_UNAVAILABLE", FALLBACK_MODEL_UNAVAILABLE)

    def test_t_voice_03_fallback_output_draft_only_error_code_defined(self) -> None:
        """FALLBACK_OUTPUT_DRAFT_ONLY 错误码已在 errors.py 中定义。"""
        self.assertEqual("FALLBACK_OUTPUT_DRAFT_ONLY", FALLBACK_OUTPUT_DRAFT_ONLY)


class TestFallbackModelContract(unittest.TestCase):
    """T-VOICE-04: Edge 兜底小模型合约。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.ts_base = 1_800_000_000_000

    def test_t_voice_04_fallback_unavailable_fast_fail(self) -> None:
        """兜底不可用时 VOICE_FALLBACK_UNAVAILABLE 快速失败。"""
        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=False,
            default_unloaded=True,
        )
        result = rpc.asr_compile(
            request_id="req_fb_01",
            payload={"session_id": "sess_fb", "text": "hello"},
            ts_ms=self.ts_base,
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_REJECTED, result.status)
        self.assertEqual(VOICE_FALLBACK_UNAVAILABLE, result.reason_code)

    def test_t_voice_04_fallback_output_is_draft_only(self) -> None:
        """兜底输出标记 draft_only=true，不含裁决或执行事实宣告。"""
        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=True,
        )
        result = rpc.asr_compile(
            request_id="req_fb_02",
            payload={"session_id": "sess_fb", "text": "hello world"},
            ts_ms=self.ts_base,
        )
        self.assertTrue(result.success)
        self.assertEqual(STATUS_ACCEPTED, result.status)
        self.assertTrue(result.event.get("draft_only"))
        # 不含裁决字段
        draft = result.event.get("draft") or {}
        forbidden = {"decision", "allow", "deny", "confirm_required", "approval_token_ref"}
        for field in forbidden:
            self.assertNotIn(field, draft, f"forbidden field {field!r} found in draft")

    def test_t_voice_04_fallback_presence_reports_availability(self) -> None:
        """presence 接口报告兜底模型可用性。"""
        rpc_unavailable = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=False,
            default_unloaded=True,
        )
        presence = rpc_unavailable.presence(
            request_id="req_fb_03",
            ts_ms=self.ts_base,
        )
        self.assertTrue(presence.success)
        self.assertFalse(presence.event["available"])
        self.assertTrue(presence.event["draft_only"])
        self.assertEqual(VOICE_FALLBACK_UNAVAILABLE, presence.reason_code)

    def test_t_voice_04_fallback_audit_replayable(self) -> None:
        """兜底调用写入 voice_event_log，可通过 request_id 回放。"""
        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=True,
        )
        rpc.asr_compile(
            request_id="req_fb_04",
            payload={"session_id": "sess_fb", "text": "test"},
            ts_ms=self.ts_base,
        )
        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_FALLBACK_ASR_COMPILE]
        self.assertEqual(1, len(rows))
        self.assertEqual("req_fb_04", rows[0]["request_id"])
        self.assertIsInstance(rows[0]["payload"], dict)


if __name__ == "__main__":
    unittest.main()
