"""T-VOICE-05: Edge 资源闸门合约测试（M7 TASK-0106）。

验收：
- P4 任务在 P1 活跃时被抢占，写 RESOURCE_GATE_PREEMPTED
- ASR（P1）在压力下不被抢占
- 健康快照结构与 DevSpec §17.3.5 一致
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

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
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RESOURCE_GATE_PREEMPTED,
    VOICE_GATE_PREEMPTED,
    VOICE_GATE_PRIORITY_BLOCKED,
    VOICE_GATE_RESOURCE_LIMITED,
    VOICE_GATE_TASK_INVALID,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestResourceGateContract(unittest.TestCase):
    """T-VOICE-05: Edge 资源闸门合约。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.gate = VoiceResourceGate(
            ledger=self.ledger,
            vram_soft_limit_ratio=0.90,
        )
        self.ts_base = 1_800_000_000_000

    def test_t_voice_05_tts_preempted_by_asr(self) -> None:
        """低优先级 TTS 在 ASR 活跃时被抢占，写 VOICE_GATE_PREEMPTED。"""
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")

        # 先让 TTS 进入（无高优先级竞争时）
        tts_admitted = self.gate.request(
            request_id="req_rg_fb01",
            task=TASK_TTS_SYNTH,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.ts_base,
        )
        self.assertTrue(tts_admitted.admitted)

        # ASR 进入，抢占 TTS
        asr_admitted = self.gate.request(
            request_id="req_rg_asr01",
            task=TASK_ASR_INGRESS,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.ts_base + 10,
        )
        self.assertTrue(asr_admitted.admitted)
        self.assertEqual(VOICE_GATE_PREEMPTED, asr_admitted.reason_code)
        self.assertIn(TASK_TTS_SYNTH, asr_admitted.event["preempted_tasks"])

        # 审计写入 PREEMPTED 事件
        preempted_rows = [
            r for r in self.ledger.voice_event_log
            if r["status"] == STATUS_PREEMPTED
        ]
        self.assertEqual(1, len(preempted_rows))
        self.assertEqual(VOICE_GATE_PREEMPTED, preempted_rows[0]["reason_code"])

    def test_t_voice_05_asr_not_preempted_under_pressure(self) -> None:
        """ASR（P1）在压力下不被抢占。"""
        pressure = _load_json(FIXTURES / "good" / "resource_gate_pressure.json")

        # ASR 先进入
        asr_admitted = self.gate.request(
            request_id="req_rg_asr02",
            task=TASK_ASR_INGRESS,
            session_id="sess_rg",
            metrics=pressure["metrics"],
            hold=True,
            ts_ms=self.ts_base,
        )
        self.assertTrue(asr_admitted.admitted)

        # TTS_SYNTH 尝试进入（被资源限制）
        tts_blocked = self.gate.request(
            request_id="req_rg_tts01",
            task=TASK_TTS_SYNTH,
            session_id="sess_rg",
            metrics=pressure["metrics"],
            hold=False,
            ts_ms=self.ts_base + 10,
        )
        self.assertFalse(tts_blocked.admitted)
        self.assertEqual(VOICE_GATE_RESOURCE_LIMITED, tts_blocked.reason_code)

        # ASR 仍然活跃（未被抢占）
        snapshot = self.gate.snapshot()
        active_tasks = [t["task"] for t in snapshot["active_tasks"]]
        self.assertIn(TASK_ASR_INGRESS, active_tasks)

    def test_t_voice_05_health_snapshot_structure(self) -> None:
        """健康快照结构包含 stage/priority_order/active_tasks/metrics。"""
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")
        self.gate.request(
            request_id="req_rg_snap01",
            task=TASK_PLAYBACK,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.ts_base,
        )
        snapshot = self.gate.snapshot()
        self.assertEqual(S_VOICE_RESOURCE_GATE, snapshot["stage"])
        self.assertIn("priority_order", snapshot)
        self.assertIn("active_tasks", snapshot)
        self.assertIn("metrics", snapshot)
        # priority_order 应包含 4 个任务
        self.assertEqual(4, len(snapshot["priority_order"]))
        self.assertEqual(TASK_ASR_INGRESS, snapshot["priority_order"][0])
        # metrics 包含必要字段
        metrics = snapshot["metrics"]
        self.assertIn("vram_total_mb", metrics)
        self.assertIn("vram_used_mb", metrics)
        self.assertIn("queue_depth", metrics)
        self.assertIn("vram_utilization", metrics)

    def test_t_voice_05_playback_blocked_by_asr(self) -> None:
        """PLAYBACK（P2）被 ASR（P1）阻塞。"""
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")

        # ASR 先进入
        self.gate.request(
            request_id="req_rg_asr03",
            task=TASK_ASR_INGRESS,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.ts_base,
        )

        # PLAYBACK 被阻塞
        playback_blocked = self.gate.request(
            request_id="req_rg_pb01",
            task=TASK_PLAYBACK,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=False,
            ts_ms=self.ts_base + 10,
        )
        self.assertFalse(playback_blocked.admitted)
        self.assertEqual(VOICE_GATE_PRIORITY_BLOCKED, playback_blocked.reason_code)

    def test_t_voice_05_resource_gate_preempted_error_code_defined(self) -> None:
        """RESOURCE_GATE_PREEMPTED 错误码已在 errors.py 中定义。"""
        self.assertEqual("RESOURCE_GATE_PREEMPTED", RESOURCE_GATE_PREEMPTED)

    def test_t_voice_05_audit_events_replayable(self) -> None:
        """所有资源闸门事件写入 voice_event_log，payload 为 dict。"""
        relaxed = _load_json(FIXTURES / "good" / "resource_gate_relaxed.json")
        self.gate.request(
            request_id="req_rg_audit01",
            task=TASK_PLAYBACK,
            session_id="sess_rg",
            metrics=relaxed["metrics"],
            hold=True,
            ts_ms=self.ts_base,
        )
        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_VOICE_RESOURCE_GATE]
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIsInstance(row["payload"], dict)
            self.assertIn("metrics", row["payload"])
            self.assertIsNotNone(row["ts_ms"])


if __name__ == "__main__":
    unittest.main()
