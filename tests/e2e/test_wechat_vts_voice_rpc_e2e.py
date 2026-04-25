"""T-E2E-RPC-01: WeChat -> Governor -> real Edge RPC event ingest."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.wechat import receive_wechat_event
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry
from tests.fakes.host_app import build_host_test_app

try:
    from fastapi.testclient import TestClient

    _HAS_TEST_CLIENT = True
except Exception:
    _HAS_TEST_CLIENT = False


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "e2e"
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_only_status(args: dict[str, Any], _ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


@unittest.skipUnless(_HAS_TEST_CLIENT, "fastapi.testclient not available")
class TestWeChatVtsVoiceRpcE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_150_000_000
        self.ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
        self.tokens = TokenService(
            approval_ttl_ms=20_000,
            confirm_ttl_ms=20_000,
            undo_ttl_ms=20_000,
        )
        self.engine = IntentDecisionEngine(
            ledger=self.ledger,
            token_service=self.tokens,
            expiry_window_ms=300_000,
        )
        registry = SkillRegistry()
        registry.register(
            SkillMeta(
                skill_name="read_only_status",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=False,
                handler=_read_only_status,
            )
        )
        self.loader = SkillLoader(
            registry=registry,
            token_service=self.tokens,
            ledger=self.ledger,
        )

        self.client = TestClient(build_host_test_app(get_ledger=lambda: self.ledger))

    def tearDown(self) -> None:
        self.client.close()
        self.ledger.close()

    def _make_intent(self, overrides: dict[str, Any]) -> dict[str, Any]:
        base = _load_json(INTENT_FIX)
        base["ts_ms"] = self.now_ms - 1000
        base.update(overrides)
        return base

    def _http_transport(self, events: list[dict[str, Any]]) -> BatchReportResult:
        resp = self.client.post("/v1/edge/events", json={"events": events})
        self.assertEqual(200, resp.status_code)
        body = resp.json()
        return BatchReportResult(
            success=bool(body.get("success")),
            total=int(body.get("total", len(events))),
            accepted=int(body.get("accepted", 0)),
            rejected=int(body.get("rejected", 0)),
            reason_code=body.get("reason_code"),
            detail=body.get("detail"),
            rejected_indices=list(body.get("rejected_indices") or []),
        )

    def test_e2e_with_real_edge_rpc_endpoint(self) -> None:
        fixture = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        request_id = "req_e2e_wc_rpc_001"

        ingress = receive_wechat_event(
            fixture["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(ingress.accepted)

        intent = self._make_intent(
            {
                **fixture["intent_overrides"],
                "id": "intent_e2e_wechat_rpc_001",
                "nonce": "nonce_e2e_wechat_rpc_001",
            }
        )
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id=ingress.thread_key,
            turn_id="turn_e2e_wechat_rpc_001",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        run_intent = dict(intent)
        run_intent.update(
            {
                "request_id": request_id,
                "thread_id": ingress.thread_key,
                "turn_id": "turn_e2e_wechat_rpc_001",
            }
        )
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)

        reporter = EdgeEventReporter(
            transport_fn=self._http_transport,
            max_retry=1,
        )
        event_batch = [
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": self.now_ms + 10,
                "request_id": request_id,
                "session_id": intent["session_id"],
                "source": "edge",
                "direction": "EDGE_TO_HOST",
                "seq": 1,
                "status": "RECEIVED",
                "stage": "S_ASR_CAPTURE",
                "payload": {"text": ingress.payload.get("content", "")},
            },
            {
                "event_type": "TTS_PLAY",
                "ts_ms": self.now_ms + 20,
                "request_id": request_id,
                "session_id": intent["session_id"],
                "source": "edge",
                "direction": "EDGE_TO_HOST",
                "seq": 2,
                "status": "COMPLETED",
                "stage": "S_VOICE_PLAY",
                "payload": {"text": "rpc delivered"},
            },
        ]
        report = reporter.report(event_batch)
        self.assertTrue(report.success, msg=report.to_dict())
        self.assertEqual(2, report.accepted)

        voice_rows = self.ledger._fetch_by_request("voice_event_log", request_id)
        self.assertGreaterEqual(len(voice_rows), 2)
        event_types = {row.get("event_type") for row in voice_rows}
        self.assertIn("ASR_CHUNK", event_types)
        self.assertIn("TTS_PLAY", event_types)

        trace = self.ledger.trace_request(request_id)
        self.assertGreaterEqual(len(trace.get("intent_log") or []), 1)
        self.assertGreaterEqual(len(trace.get("governor_log") or []), 1)
