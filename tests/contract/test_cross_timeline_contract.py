"""T-Obs-03: 跨端事件时间线聚合与排序稳定性契约测试。

覆盖：
1. Host/Edge 混合事件正常路径——按 request_id 输出合并时间线，source 标记正确
2. 时间交叉——Edge 事件时间戳穿插在 Host 事件之间，排序稳定
3. Edge 事件缺失——Host 时间线仍完整且标注缺失来源
4. 时间漂移检测——超过容忍阈值时标记 clock_drift_detected
5. 按 session_id 关联——request_id 为空时可按 session_id 聚合
6. 无效 Edge 事件过滤——缺少必要字段的 Edge 事件被忽略
7. 输出结构可序列化——CrossTimelineResult.to_dict() 可 JSON 序列化

断言点：
- timeline 排序按 ts_ms 严格单调非递减
- source 标记为 "host" 或 "edge"
- edge_missing=True 时 warnings 包含 TIMELINE_EDGE_EVENTS_MISSING
- clock_drift_detected=True 时 warnings 包含 TIMELINE_CLOCK_DRIFT_DETECTED
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    TIMELINE_CLOCK_DRIFT_DETECTED,
    TIMELINE_EDGE_EVENTS_MISSING,
    TIMELINE_SESSION_NOT_FOUND,
)
from host_brain.observability.cross_timeline import (
    SOURCE_EDGE,
    SOURCE_HOST,
    CrossTimelineAggregator,
    CrossTimelineResult,
    EdgeEventReport,
    UnifiedTimelineEntry,
    build_cross_timeline,
    parse_edge_event,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "observability"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _seed_host_events(ledger: SQLiteLedger, host_events: dict[str, Any]) -> None:
    """将 fixture 中的 host_events 写入 ledger。"""
    if "intent_log" in host_events:
        ledger.log_intent(host_events["intent_log"])
    if "governor_log" in host_events:
        ledger.log_governor(host_events["governor_log"])
    if "skill_exec_log" in host_events:
        ledger.log_skill_exec(host_events["skill_exec_log"])
    if "voice_event_log" in host_events:
        ledger.log_voice_event(host_events["voice_event_log"])
    if "channel_io_log" in host_events:
        ledger.log_channel_io(host_events["channel_io_log"])


class TestCrossTimelineContract(unittest.TestCase):
    """T-Obs-03: 跨端时间线聚合与排序稳定性。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()

    # ─────────────────────────────────────────────
    # 场景 1：Host/Edge 混合正常路径
    # ─────────────────────────────────────────────

    def test_01_mixed_normal_timeline_merged(self) -> None:
        """T-Obs-03.1: Host+Edge 混合事件合并为统一时间线。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        self.assertEqual("COMPLETE", result["status"])
        self.assertFalse(result["edge_missing"])
        self.assertFalse(result["clock_drift_detected"])
        self.assertGreaterEqual(result["host_event_count"], fix["expected"]["host_event_count"])
        self.assertEqual(fix["expected"]["edge_event_count"], result["edge_event_count"])

    def test_02_mixed_normal_source_labels(self) -> None:
        """T-Obs-03.2: 每条事件 source 标记为 host 或 edge。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        for entry in result["timeline"]:
            self.assertIn(entry["source"], {SOURCE_HOST, SOURCE_EDGE})

    def test_03_mixed_normal_sorted_by_ts_ms(self) -> None:
        """T-Obs-03.3: 时间线按 ts_ms 严格单调非递减排序。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        ts_list = [e["ts_ms"] for e in result["timeline"]]
        self.assertEqual(ts_list, sorted(ts_list), "时间线必须按 ts_ms 排序")

    def test_04_mixed_normal_request_id_consistent(self) -> None:
        """T-Obs-03.4: 所有事件 request_id 一致。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        for entry in result["timeline"]:
            self.assertEqual(fix["request_id"], entry["request_id"])

    # ─────────────────────────────────────────────
    # 场景 2：时间交叉
    # ─────────────────────────────────────────────

    def test_05_interleaved_timeline_order(self) -> None:
        """T-Obs-03.5: Edge 事件穿插在 Host 事件之间时排序稳定。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_time_interleaved.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        self.assertEqual("COMPLETE", result["status"])
        ts_list = [e["ts_ms"] for e in result["timeline"]]
        self.assertEqual(ts_list, sorted(ts_list), "交叉时间线排序必须稳定")

        # 验证 Host 和 Edge 事件交替出现
        sources = [e["source"] for e in result["timeline"]]
        self.assertIn(SOURCE_HOST, sources)
        self.assertIn(SOURCE_EDGE, sources)

        # 验证事件总数
        total = result["host_event_count"] + result["edge_event_count"]
        self.assertEqual(total, len(result["timeline"]))

    def test_06_interleaved_edge_before_host(self) -> None:
        """T-Obs-03.6: Edge ASR 事件在 Host intent 之前（时间戳更早）。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_time_interleaved.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        first_event = result["timeline"][0]
        self.assertEqual(SOURCE_EDGE, first_event["source"], "第一个事件应为 Edge ASR")
        self.assertEqual("ASR_CHUNK", first_event["event_type"])

    # ─────────────────────────────────────────────
    # 场景 3：Edge 事件缺失
    # ─────────────────────────────────────────────

    def test_07_edge_missing_host_timeline_complete(self) -> None:
        """T-Obs-03.7: Edge 缺失时 Host 时间线仍完整。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_edge_missing.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        self.assertEqual("HOST_ONLY", result["status"])
        self.assertTrue(result["edge_missing"])
        self.assertEqual(0, result["edge_event_count"])
        self.assertGreaterEqual(result["host_event_count"], fix["expected"]["host_event_count"])

    def test_08_edge_missing_warnings_contain_reason(self) -> None:
        """T-Obs-03.8: Edge 缺失时 warnings 包含 TIMELINE_EDGE_EVENTS_MISSING。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_edge_missing.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=[],
        )

        self.assertTrue(
            any(TIMELINE_EDGE_EVENTS_MISSING in w for w in result["warnings"]),
            f"warnings 必须包含 {TIMELINE_EDGE_EVENTS_MISSING}",
        )

    def test_09_edge_missing_host_sorted(self) -> None:
        """T-Obs-03.9: 仅 Host 事件时排序仍稳定。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_edge_missing.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=[],
        )

        ts_list = [e["ts_ms"] for e in result["timeline"]]
        self.assertEqual(ts_list, sorted(ts_list))

    # ─────────────────────────────────────────────
    # 场景 4：时间漂移检测
    # ─────────────────────────────────────────────

    def test_10_clock_drift_detected_when_exceeds_tolerance(self) -> None:
        """T-Obs-03.10: 时间漂移超过容忍阈值时 clock_drift_detected=True。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        # 构造一个漂移超过 1ms 容忍的 Edge 事件（使用极端时间差）
        drifted_edge_events = [
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": 1800050000050 + 100_000,  # 漂移 100秒
                "request_id": fix["request_id"],
                "session_id": fix["session_id"],
                "direction": "EDGE_TO_HOST",
                "seq": 1,
                "status": "ACCEPTED",
                "stage": "S_VOICE_ASR_INGRESS",
                "detail": "drifted edge event",
                "payload": {},
            }
        ]

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=drifted_edge_events,
            clock_drift_tolerance_ms=1_000,
        )

        self.assertTrue(result["clock_drift_detected"])
        self.assertGreater(result["clock_drift_max_ms"], 1_000)
        self.assertTrue(
            any(TIMELINE_CLOCK_DRIFT_DETECTED in w for w in result["warnings"]),
            f"warnings 必须包含 {TIMELINE_CLOCK_DRIFT_DETECTED}",
        )

    def test_11_clock_drift_within_tolerance(self) -> None:
        """T-Obs-03.11: 时间漂移在容忍阈值内时不报告。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
            clock_drift_tolerance_ms=10_000,
        )

        self.assertFalse(result["clock_drift_detected"])

    def test_12_clock_drift_status_partial(self) -> None:
        """T-Obs-03.12: 检测到漂移时 status='PARTIAL'。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        drifted_edge_events = [
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": 1800050000050 + 50_000,
                "request_id": fix["request_id"],
                "session_id": fix["session_id"],
                "direction": "EDGE_TO_HOST",
                "status": "ACCEPTED",
                "payload": {},
            }
        ]

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=drifted_edge_events,
            clock_drift_tolerance_ms=1_000,
        )

        self.assertEqual("PARTIAL", result["status"])

    # ─────────────────────────────────────────────
    # 场景 5：按 session_id 关联
    # ─────────────────────────────────────────────

    def test_13_aggregate_by_session_id(self) -> None:
        """T-Obs-03.13: request_id 为空时可按 session_id 聚合。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            session_id=fix["session_id"],
            edge_events=fix["edge_events"],
        )

        # session_id 关联应能找到 voice_event_log（含 session_id 字段）
        self.assertGreaterEqual(len(result["timeline"]), 1)

    def test_14_no_request_id_no_session_id(self) -> None:
        """T-Obs-03.14: 无 request_id 和 session_id 时返回空时间线。"""
        result = build_cross_timeline(
            ledger=self.ledger,
            edge_events=[],
        )

        self.assertEqual("HOST_ONLY", result["status"])
        self.assertEqual(0, len(result["timeline"]))
        self.assertTrue(
            any(TIMELINE_SESSION_NOT_FOUND in w for w in result["warnings"]),
        )

    # ─────────────────────────────────────────────
    # 场景 6：无效 Edge 事件过滤
    # ─────────────────────────────────────────────

    def test_15_invalid_edge_event_filtered(self) -> None:
        """T-Obs-03.15: 缺少必要字段的 Edge 事件被忽略。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        invalid_events = [
            {"event_type": "", "ts_ms": 100, "request_id": "req"},  # 空 event_type
            {"event_type": "ASR", "request_id": "req"},  # 缺 ts_ms
            {"event_type": "ASR", "ts_ms": 100},  # 缺 request_id
            {},  # 完全为空
        ]

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=invalid_events,
        )

        self.assertEqual(0, result["edge_event_count"], "无效 Edge 事件应被过滤")

    def test_16_parse_edge_event_valid(self) -> None:
        """T-Obs-03.16: parse_edge_event 正确解析有效事件。"""
        raw = {
            "event_type": "ASR_CHUNK",
            "ts_ms": 12345,
            "request_id": "req_test",
            "session_id": "sess_test",
            "payload": {"text": "hello"},
            "status": "ACCEPTED",
            "seq": 1,
        }
        parsed = parse_edge_event(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual("ASR_CHUNK", parsed.event_type)
        self.assertEqual(12345, parsed.ts_ms)
        self.assertEqual("req_test", parsed.request_id)
        self.assertEqual(1, parsed.seq)

    def test_17_parse_edge_event_invalid(self) -> None:
        """T-Obs-03.17: parse_edge_event 对无效输入返回 None。"""
        self.assertIsNone(parse_edge_event({}))
        self.assertIsNone(parse_edge_event({"event_type": "ASR"}))
        self.assertIsNone(parse_edge_event({"event_type": "ASR", "ts_ms": 100}))

    def test_18_parse_edge_event_string_payload(self) -> None:
        """T-Obs-03.18: parse_edge_event 可解析 JSON 字符串 payload。"""
        raw = {
            "event_type": "ASR_CHUNK",
            "ts_ms": 12345,
            "request_id": "req_test",
            "session_id": "sess_test",
            "payload": '{"text":"hello"}',
        }
        parsed = parse_edge_event(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual({"text": "hello"}, parsed.payload)

    # ─────────────────────────────────────────────
    # 场景 7：输出结构可序列化
    # ─────────────────────────────────────────────

    def test_19_result_json_serializable(self) -> None:
        """T-Obs-03.19: CrossTimelineResult 可 JSON 序列化。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=fix["edge_events"],
        )

        serialized = json.dumps(result, ensure_ascii=False)
        reparsed = json.loads(serialized)
        self.assertEqual(result["request_id"], reparsed["request_id"])
        self.assertEqual(result["status"], reparsed["status"])
        self.assertEqual(len(result["timeline"]), len(reparsed["timeline"]))

    def test_20_edge_event_report_to_dict(self) -> None:
        """T-Obs-03.20: EdgeEventReport.to_dict() 结构完整。"""
        report = EdgeEventReport(
            event_type="ASR_CHUNK",
            ts_ms=12345,
            request_id="req_test",
            session_id="sess_test",
            payload={"text": "hello"},
            seq=1,
            status="ACCEPTED",
        )
        d = report.to_dict()
        self.assertEqual("ASR_CHUNK", d["event_type"])
        self.assertEqual(12345, d["ts_ms"])
        self.assertEqual("req_test", d["request_id"])
        self.assertEqual(1, d["seq"])

    def test_21_unified_entry_to_dict(self) -> None:
        """T-Obs-03.21: UnifiedTimelineEntry.to_dict() 结构完整。"""
        entry = UnifiedTimelineEntry(
            event_id="host:intent_log:1",
            source=SOURCE_HOST,
            source_table="intent_log",
            event_type=None,
            stage="S_INTENT_PARSE",
            status="ACCEPTED",
            reason_code=None,
            ts_ms=12345,
            detail="test",
            request_id="req_test",
        )
        d = entry.to_dict()
        self.assertEqual("host", d["source"])
        self.assertEqual("intent_log", d["source_table"])
        self.assertFalse(d["clock_drift_flag"])

    # ─────────────────────────────────────────────
    # 场景 8：回归兼容——现有 T-Obs-01 观测报告仍正常
    # ─────────────────────────────────────────────

    def test_22_regression_existing_observability_report(self) -> None:
        """T-Obs-03.22: 跨端时间线不影响现有 Host 观测报告。"""
        from host_brain.app.main import build_request_observability_report

        request_id = "req_obs_regression"
        self.ledger.log_intent({
            "request_id": request_id,
            "thread_id": "thread_reg",
            "turn_id": "turn_reg",
            "intent_id": "intent_reg",
            "session_id": "sess_reg",
            "seq": 1,
            "ts_ms": 1800050000001,
            "nonce": "nonce_reg",
            "parse_stage": "S_INTENT_PARSE",
            "raw_text": "regression test",
            "normalized_json": {"id": "intent_reg"},
            "proposer_node_id": "core_reasoner_primary",
            "model_snapshot": "test-model",
            "prompt_profile_id": "default",
            "tool_visibility_hash": "",
            "proposed_intent_draft_json": {"id": "intent_reg"},
            "lint_result": {"ok": True},
        })
        self.ledger.log_governor({
            "request_id": request_id,
            "thread_id": "thread_reg",
            "turn_id": "turn_reg",
            "intent_id": "intent_reg",
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": [],
            "tokens": {"approval_token_ref": "approval::reg"},
            "snapshot_refs": {},
            "stage": "S_GOVERNOR",
            "ts_ms": 1800050000002,
        })

        # 现有观测报告仍可正常工作
        report = build_request_observability_report(
            request_id=request_id, ledger=self.ledger,
        )
        self.assertEqual(request_id, report["request_id"])
        self.assertEqual("COMPLETE", report["trace_status"])
        self.assertGreaterEqual(report["timeline_total"], 2)

        # 跨端时间线也可正常工作
        cross = build_cross_timeline(
            ledger=self.ledger,
            request_id=request_id,
            edge_events=[],
        )
        self.assertEqual("HOST_ONLY", cross["status"])
        self.assertGreaterEqual(cross["host_event_count"], 2)

    # ─────────────────────────────────────────────
    # Fixture 验证
    # ─────────────────────────────────────────────

    def test_23_fixture_mixed_normal_loadable(self) -> None:
        """T-Obs-03.23: 正常路径 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        self.assertEqual("cross_timeline_mixed_normal", fix["scenario"])
        self.assertIn("host_events", fix)
        self.assertIn("edge_events", fix)
        self.assertIn("expected", fix)
        self.assertGreaterEqual(len(fix["edge_events"]), 1)

    def test_24_fixture_interleaved_loadable(self) -> None:
        """T-Obs-03.24: 时间交叉 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_time_interleaved.json")
        self.assertEqual("cross_timeline_time_interleaved", fix["scenario"])
        self.assertIn("interleaved_order", fix["expected"])

    def test_25_fixture_edge_missing_loadable(self) -> None:
        """T-Obs-03.25: Edge 缺失 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_edge_missing.json")
        self.assertEqual("cross_timeline_edge_missing", fix["scenario"])
        self.assertEqual([], fix["edge_events"])
        self.assertTrue(fix["expected"]["edge_missing"])

    def test_26_all_fixtures_json_roundtrip(self) -> None:
        """T-Obs-03.26: 所有 fixture 可 JSON 往返序列化。"""
        for name in [
            "cross_timeline_mixed_normal.json",
            "cross_timeline_time_interleaved.json",
            "cross_timeline_edge_missing.json",
        ]:
            fix = _load_json(FIXTURE_DIR / name)
            s = json.dumps(fix, ensure_ascii=False)
            reparsed = json.loads(s)
            self.assertEqual(fix["scenario"], reparsed["scenario"])

    # ─────────────────────────────────────────────
    # 边界：Edge 事件跨 request_id 过滤
    # ─────────────────────────────────────────────

    def test_27_edge_events_filtered_by_request_id(self) -> None:
        """T-Obs-03.27: Edge 事件按 request_id 过滤，不同 request_id 的被排除。"""
        fix = _load_json(FIXTURE_DIR / "cross_timeline_mixed_normal.json")
        _seed_host_events(self.ledger, fix["host_events"])

        # 添加一个不同 request_id 的 Edge 事件
        mixed_events = list(fix["edge_events"]) + [
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": 1800050000999,
                "request_id": "req_other",
                "session_id": "sess_other",
                "payload": {},
            }
        ]

        result = build_cross_timeline(
            ledger=self.ledger,
            request_id=fix["request_id"],
            edge_events=mixed_events,
        )

        # 不同 request_id 的事件不应出现在时间线中
        for entry in result["timeline"]:
            if entry["source"] == SOURCE_EDGE:
                self.assertEqual(fix["request_id"], entry["request_id"])


if __name__ == "__main__":
    unittest.main()
