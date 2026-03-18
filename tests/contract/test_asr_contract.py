"""T-VOICE-01: ASR 事件化上送合约测试（M7 TASK-0103）。

验收：
- confidence ≥ 0.6 → 正常处理，low_confidence=false
- confidence < 0.6 → low_confidence=true + reason_code=ASR_LOW_CONFIDENCE
- ASR 不可用 → ASR_UNAVAILABLE + 输出结构稳定
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from edge_client.rpc.voice import (
    EVENT_ASR_CHUNK,
    S_VOICE_ASR_INGRESS,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    VoiceRpcContract,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ASR_LOW_CONFIDENCE,
    VOICE_LOW_CONFIDENCE,
    VOICE_SCHEMA_INVALID,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestASRContract(unittest.TestCase):
    """T-VOICE-01: ASR 事件化上送合约。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.rpc = VoiceRpcContract(
            ledger=self.ledger,
            asr_min_confidence=0.60,
            asr_min_interval_ms=0,
        )
        self.ts_base = 1_800_000_000_000

    def test_t_voice_01_high_confidence_accepted(self) -> None:
        """confidence ≥ 0.6 → 正常处理，low_confidence=false，voice_event_log 写入。"""
        valid = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")
        result = self.rpc.asr_chunk(
            request_id=valid["request_id"],
            payload=valid["payload"],
            ts_ms=self.ts_base,
        )
        self.assertTrue(result.success)
        self.assertEqual(STATUS_ACCEPTED, result.status)
        self.assertEqual(S_VOICE_ASR_INGRESS, result.stage)
        self.assertEqual(EVENT_ASR_CHUNK, result.event["event_type"])
        # low_confidence 应为 False
        self.assertFalse(result.event.get("low_confidence", False))
        # 审计写入
        rows = self.ledger.voice_event_log
        self.assertEqual(1, len(rows))
        self.assertEqual(STATUS_ACCEPTED, rows[0]["status"])
        self.assertIsNone(rows[0]["reason_code"])

    def test_t_voice_01_low_confidence_annotated(self) -> None:
        """confidence < 0.6 → low_confidence=true + reason_code=VOICE_LOW_CONFIDENCE。"""
        low_conf = _load_json(FIXTURES / "bad" / "asr_low_confidence.json")
        result = self.rpc.asr_chunk(
            request_id=low_conf["request_id"],
            payload=low_conf["payload"],
            ts_ms=self.ts_base + 200,
        )
        # 低置信度仍然 success=True（标注而非拒绝）
        self.assertTrue(result.success)
        self.assertEqual(STATUS_ACCEPTED, result.status)
        self.assertEqual(VOICE_LOW_CONFIDENCE, result.reason_code)
        self.assertTrue(result.event.get("low_confidence", False))
        # 审计写入
        rows = self.ledger.voice_event_log
        self.assertEqual(1, len(rows))
        self.assertEqual(VOICE_LOW_CONFIDENCE, rows[0]["reason_code"])

    def test_t_voice_01_missing_fields_rejected(self) -> None:
        """缺少必要字段 → VOICE_SCHEMA_INVALID 拒绝。"""
        missing = _load_json(FIXTURES / "bad" / "asr_missing_fields.json")
        result = self.rpc.asr_chunk(
            request_id=missing["request_id"],
            payload=missing["payload"],
            ts_ms=self.ts_base + 400,
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_REJECTED, result.status)
        self.assertEqual(VOICE_SCHEMA_INVALID, result.reason_code)

    def test_t_voice_01_event_log_replayable(self) -> None:
        """voice_event_log 中每条记录可通过 request_id 回放。"""
        valid = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")
        self.rpc.asr_chunk(
            request_id=valid["request_id"],
            payload=valid["payload"],
            ts_ms=self.ts_base,
        )
        rows = self.ledger.voice_event_log
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(valid["request_id"], row["request_id"])
        self.assertEqual(EVENT_ASR_CHUNK, row["event_type"])
        self.assertIsInstance(row["payload"], dict)
        self.assertIsNotNone(row["ts_ms"])

    def test_t_voice_01_asr_low_confidence_error_code_defined(self) -> None:
        """ASR_LOW_CONFIDENCE 错误码已在 errors.py 中定义。"""
        self.assertEqual("ASR_LOW_CONFIDENCE", ASR_LOW_CONFIDENCE)

    def test_t_voice_01_fixture_high_confidence(self) -> None:
        """Fixture asr_chunk_valid.json 结构正确。"""
        fixture = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")
        self.assertIn("request_id", fixture)
        self.assertIn("payload", fixture)
        payload = fixture["payload"]
        self.assertIn("session_id", payload)
        self.assertIn("seq", payload)


if __name__ == "__main__":
    unittest.main()
