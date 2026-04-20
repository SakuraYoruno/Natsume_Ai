"""T-OBS-01/T-OBS-02: /obs/metrics contract tests."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.observability.metrics import MetricsCollector, ObsMode, get_obs_mode
from host_brain.observability.routes import build_obs_metrics_response


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "obs"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _seed_ledger(ledger: SQLiteLedger, ts_base: int = 1_800_000_000_000) -> str:
    """Seed ledger with one full request chain; return request_id."""
    request_id = "req_obs_001"
    intent_id = "intent_obs_001"
    session_id = "session_obs"

    ledger.log_intent({
        "request_id": request_id,
        "thread_id": "thread_obs",
        "turn_id": "turn_obs_1",
        "intent_id": intent_id,
        "session_id": session_id,
        "seq": 1,
        "ts_ms": ts_base,
        "nonce": "nonce_obs_001",
        "parse_stage": "S_INTENT",
        "raw_text": "hello",
        "normalized_json": {"action": "chat"},
        "proposer_node_id": "node_obs",
        "model_snapshot": "snap_obs",
        "prompt_profile_id": "pp_obs",
        "tool_visibility_hash": "tvh_obs",
        "proposed_intent_draft_json": {"action": "chat", "args": {"text": "hello"}},
        "lint_result": {"ok": True},
    })
    ledger.log_governor({
        "request_id": request_id,
        "thread_id": "thread_obs",
        "turn_id": "turn_obs_1",
        "intent_id": intent_id,
        "decision": "APPROVE",
        "reason_code": None,
        "rule_trace": [
            {"gate_id": "R1_TRUST", "hit": False, "reason_hint": "PASS"},
            {"gate_id": "R6_SAFETY", "hit": False, "reason_hint": "PASS"},
        ],
        "tokens": {},
        "snapshot_refs": [],
        "stage": "S_GOVERNOR",
        "ts_ms": ts_base + 10,
    })
    ledger.log_skill_exec({
        "request_id": request_id,
        "thread_id": "thread_obs",
        "turn_id": "turn_obs_1",
        "intent_id": intent_id,
        "event_id": "evt_obs_001",
        "skill_name": "proactive_talk",
        "approval_token_ref": None,
        "confirm_token_ref": None,
        "undo_token_ref": None,
        "result_status": "SUCCESS",
        "result_code": None,
        "result_digest": "digest_obs",
        "evidence_ref": "evidence::obs_001",
        "manifest_ref": None,
        "ts_ms": ts_base + 20,
    })
    # 一条 retrieval 事件（含 latency_ms）
    ledger.log_retrieval_event({
        "request_id": request_id,
        "intent_id": intent_id,
        "op_kind": "RECALL",
        "status": "SUCCESS",
        "reason_code": None,
        "stage": "S_RETRIEVAL",
        "payload": {"latency_ms": 42},
        "detail": "ok",
        "ts_ms": ts_base + 5,
    })
    return request_id


class TestObsMetricsContract(unittest.TestCase):
    """T-OBS-01: /obs/metrics 结构稳定 + request_id 串联可回放。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.collector = MetricsCollector(ledger=self.ledger)
        self.ts_base = 1_800_000_000_000

    def test_t_obs_01_structure_complete(self) -> None:
        """返回结构包含所有必需字段。"""
        request_id = _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            request_id=request_id,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        required_keys = {
            "request_id", "ts_ms", "obs_mode",
            "governor_decisions", "retrieval_latency",
            "hard_snapshot_staleness_ms", "recent_chains", "debug_trace",
            "latency_baseline",  # M8 TASK-0109 新增
            "emotion_metrics",   # M29-Q TASK-0280 新增
            "supervisor_metrics",    # 新增
            "budget_breach_metrics", # 新增
            "agent_metrics",         # 新增
            "soft_failure_metrics",  # OPT-3 新增：进程内 SoftFailure 计数
        }
        self.assertEqual(required_keys, set(result.keys()))

    def test_t_obs_01_governor_decisions_count(self) -> None:
        """governor_decisions 计数与 Ledger 中 governor_log 一致。"""
        _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        gov = result["governor_decisions"]
        self.assertEqual(1, gov["total"])
        self.assertEqual(1, gov["approved"])
        self.assertEqual(0, gov["rejected"])

    def test_t_obs_01_retrieval_latency_p50(self) -> None:
        """retrieval_latency.p50_ms 与 Ledger 中 retrieval_event_log 一致。"""
        _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        lat = result["retrieval_latency"]
        self.assertEqual(42, lat["p50_ms"])
        self.assertEqual(1, lat["sample_count"])

    def test_t_obs_01_alerting_fields_present(self) -> None:
        """默认样本下存在告警字段，且无激活告警。"""
        _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        gov = result["governor_decisions"]
        lat = result["retrieval_latency"]

        self.assertIn("degraded_rate_pct", gov)
        self.assertIn("failure_rate_pct", gov)
        self.assertIn("alerting", gov)
        self.assertEqual([], list(gov["alerting"].get("active_alerts") or []))

        self.assertIn("timeout_count", lat)
        self.assertIn("degraded_count", lat)
        self.assertIn("failure_count", lat)
        self.assertIn("timeout_rate_pct", lat)
        self.assertIn("degraded_rate_pct", lat)
        self.assertIn("failure_rate_pct", lat)
        self.assertIn("alerting", lat)
        self.assertEqual([], list(lat["alerting"].get("active_alerts") or []))

    def test_t_obs_01_alerting_thresholds_trigger(self) -> None:
        """超时/降级/失败率超过阈值时产出稳定告警码。"""
        request_id = "req_obs_alert_001"
        intent_id = "intent_obs_alert_001"

        for idx in range(10):
            if idx < 4:
                decision = "DEGRADED"
            elif idx < 7:
                decision = "REJECT"
            else:
                decision = "APPROVE"
            self.ledger.log_governor({
                "request_id": request_id,
                "thread_id": "thread_obs_alert",
                "turn_id": f"turn_obs_alert_{idx}",
                "intent_id": intent_id,
                "decision": decision,
                "reason_code": ("R_ALERT_TEST" if decision != "APPROVE" else None),
                "rule_trace": [],
                "tokens": {},
                "snapshot_refs": [],
                "stage": "S_GOVERNOR",
                "ts_ms": self.ts_base + idx,
            })

        for idx in range(10):
            status = "SUCCESS"
            reason_code = None
            detail = "ok"
            if idx < 3:
                status = "FAILED"
                reason_code = "RETRIEVAL_TIMEOUT"
                detail = "retrieval timeout in contract test"
            elif idx < 6:
                status = "DEGRADED"
                reason_code = "RETRIEVAL_PARTIAL_DEGRADED"
                detail = "retrieval degraded in contract test"
            self.ledger.log_retrieval_event({
                "request_id": request_id,
                "intent_id": intent_id,
                "op_kind": "RECALL",
                "status": status,
                "reason_code": reason_code,
                "stage": "S_RETRIEVAL",
                "payload": {"latency_ms": 30 + idx},
                "detail": detail,
                "ts_ms": self.ts_base + 100 + idx,
            })

        with patch.dict(
            os.environ,
            {
                "OBS_ALERT_TIMEOUT_RATE_PCT": "20",
                "OBS_ALERT_DEGRADED_RATE_PCT": "20",
                "OBS_ALERT_FAILURE_RATE_PCT": "20",
            },
            clear=False,
        ):
            result = build_obs_metrics_response(
                collector=self.collector,
                request_id=request_id,
                obs_mode=ObsMode.PROD,
                ts_ms=self.ts_base + 300,
            )

        gov_alert_codes = {
            str(a.get("code"))
            for a in list(result["governor_decisions"]["alerting"].get("active_alerts") or [])
            if isinstance(a, dict)
        }
        ret_alert_codes = {
            str(a.get("code"))
            for a in list(result["retrieval_latency"]["alerting"].get("active_alerts") or [])
            if isinstance(a, dict)
        }

        self.assertIn("GOVERNOR_DEGRADED_RATE_HIGH", gov_alert_codes)
        self.assertIn("GOVERNOR_FAILURE_RATE_HIGH", gov_alert_codes)
        self.assertIn("RETRIEVAL_TIMEOUT_RATE_HIGH", ret_alert_codes)
        self.assertIn("RETRIEVAL_DEGRADED_RATE_HIGH", ret_alert_codes)
        self.assertIn("RETRIEVAL_FAILURE_RATE_HIGH", ret_alert_codes)

    def test_t_obs_01_recent_chains_request_id_traceable(self) -> None:
        """recent_chains 中每条记录的 request_id 可在 Ledger 中找到对应审计行。"""
        request_id = _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        chains = result["recent_chains"]
        self.assertEqual(1, len(chains))
        chain = chains[0]
        self.assertEqual(request_id, chain["request_id"])
        self.assertEqual("APPROVE", chain["decision"])
        self.assertEqual("proactive_talk", chain["skill_name"])

        # 验证 request_id 可在 Ledger 中回放
        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual(1, len(trace["intent_log"]))
        self.assertEqual(1, len(trace["governor_log"]))
        self.assertEqual(1, len(trace["skill_exec_log"]))

    def test_t_obs_01_empty_ledger_returns_zero_stats(self) -> None:
        """空 Ledger 时返回零值统计，不抛异常。"""
        result = build_obs_metrics_response(
            collector=self.collector,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base,
        )
        self.assertEqual(0, result["governor_decisions"]["total"])
        self.assertIsNone(result["retrieval_latency"]["p50_ms"])
        self.assertEqual(0, result["recent_chains"].__len__())

    def test_t_obs_01_fixture_structure(self) -> None:
        """Fixture 文件结构与 /obs/metrics 响应一致。"""
        fixture = _load_json(FIXTURES / "metrics_response_prod.json")
        required_keys = {
            "request_id", "ts_ms", "obs_mode",
            "governor_decisions", "retrieval_latency",
            "hard_snapshot_staleness_ms", "recent_chains", "debug_trace",
        }
        self.assertEqual(required_keys, set(fixture.keys()))
        self.assertEqual("prod", fixture["obs_mode"])
        self.assertIsNone(fixture["debug_trace"])


class TestObsModeContract(unittest.TestCase):
    """T-OBS-02: dev/prod 模式切换，debug_trace 字段按预期出现/消失。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.collector = MetricsCollector(ledger=self.ledger)
        self.ts_base = 1_800_000_000_000

    def test_t_obs_02_prod_mode_no_debug_trace(self) -> None:
        """prod 模式下 debug_trace 为 None。"""
        request_id = _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            request_id=request_id,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        self.assertIsNone(result["debug_trace"])
        self.assertEqual("prod", result["obs_mode"])

    def test_t_obs_02_dev_mode_has_debug_trace(self) -> None:
        """dev 模式下 debug_trace 包含 intent_draft/rules_hit/skill_params。"""
        request_id = _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            request_id=request_id,
            obs_mode=ObsMode.DEV,
            ts_ms=self.ts_base + 100,
        )
        self.assertEqual("dev", result["obs_mode"])
        trace = result["debug_trace"]
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertIn("intent_draft", trace)
        self.assertIn("rules_hit", trace)
        self.assertIn("skill_params", trace)
        # intent_draft 应包含 action 字段
        self.assertIsNotNone(trace["intent_draft"])
        self.assertEqual("chat", trace["intent_draft"]["action"])
        # rules_hit 应为空列表（无命中）
        self.assertEqual([], trace["rules_hit"])
        # skill_params 应包含 skill_name
        self.assertEqual("proactive_talk", trace["skill_params"]["skill_name"])

    def test_t_obs_02_mode_switch_does_not_affect_governor_decisions(self) -> None:
        """切换模式不影响 Governor 裁决结果与 Ledger 审计写入。"""
        request_id = _seed_ledger(self.ledger, self.ts_base)
        prod_result = build_obs_metrics_response(
            collector=self.collector,
            request_id=request_id,
            obs_mode=ObsMode.PROD,
            ts_ms=self.ts_base + 100,
        )
        dev_result = build_obs_metrics_response(
            collector=self.collector,
            request_id=request_id,
            obs_mode=ObsMode.DEV,
            ts_ms=self.ts_base + 100,
        )
        # 两种模式下 governor_decisions 完全一致
        self.assertEqual(
            prod_result["governor_decisions"],
            dev_result["governor_decisions"],
        )
        # Ledger 中审计行数不变
        self.assertEqual(1, len(self.ledger.governor_log))
        self.assertEqual(1, len(self.ledger.intent_log))

    def test_t_obs_02_dev_mode_no_request_id_trace_is_none(self) -> None:
        """dev 模式但无 request_id 时，debug_trace 为 None。"""
        _seed_ledger(self.ledger, self.ts_base)
        result = build_obs_metrics_response(
            collector=self.collector,
            request_id=None,
            obs_mode=ObsMode.DEV,
            ts_ms=self.ts_base + 100,
        )
        self.assertIsNone(result["debug_trace"])

    def test_t_obs_02_fixture_dev_mode(self) -> None:
        """dev 模式 fixture 包含 debug_trace 字段。"""
        fixture = _load_json(FIXTURES / "metrics_response_dev.json")
        self.assertEqual("dev", fixture["obs_mode"])
        self.assertIsNotNone(fixture["debug_trace"])
        trace = fixture["debug_trace"]
        self.assertIn("intent_draft", trace)
        self.assertIn("rules_hit", trace)
        self.assertIn("skill_params", trace)


if __name__ == "__main__":
    unittest.main()
