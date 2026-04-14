from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router, set_ledger_provider
from host_brain.audit.sqlite_ledger import SQLiteLedger


@pytest.fixture
def ledger():
    db = SQLiteLedger(":memory:", check_same_thread=False)
    yield db
    db.close()


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return TestClient(app)


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    ts_ms: int,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_src_001",
    status: str | None = None,
    input_preview: str | None = None,
    reply_preview: str | None = None,
    reason_code: str | None = None,
) -> None:
    payload = {"source": source}
    if input_preview is not None:
        payload["input_preview"] = input_preview
    if reply_preview is not None:
        payload["reply_preview"] = reply_preview

    normalized_status = str(status or ("OK" if "COMPLETED" in event_type else event_type.replace("INPUT_", ""))).upper()
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": event_type,
            "direction": "EDGE_TO_HOST",
            "status": normalized_status,
            "stage": f"input_dispatch.{event_type.lower()}",
            "payload": payload,
            "detail": event_type,
            "reason_code": reason_code,
            "ts_ms": ts_ms,
        }
    )


def _seed_baseline_gateway_requests(ledger: SQLiteLedger, *, now: int) -> None:
    _log_input_event(ledger, request_id="req-src-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-src-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-src-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-src-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 300)
    _log_input_event(ledger, request_id="req-src-base-2", event_type="INPUT_ACCEPTED", ts_ms=now - 25 * 60 * 1000)
    _log_input_event(ledger, request_id="req-src-base-2", event_type="INPUT_ACK_PENDING", ts_ms=now - 25 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-src-base-2", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 25 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-src-base-2", event_type="INPUT_COMPLETED", ts_ms=now - 25 * 60 * 1000 + 300)


def _log_skill_failure(ledger: SQLiteLedger, *, request_id: str, ts_ms: int, skill_name: str, result_code: str) -> None:
    ledger.log_skill_exec(
        {
            "request_id": request_id,
            "thread_id": "thread-src",
            "turn_id": "turn-src",
            "intent_id": f"intent-{request_id}",
            "skill_name": skill_name,
            "approval_token_ref": f"approval::{request_id}",
            "result_status": "FAILED",
            "result_code": result_code,
            "evidence_ref": f"evidence::{request_id}",
            "ts_ms": ts_ms,
        }
    )


def _log_governor_reject(ledger: SQLiteLedger, *, request_id: str, ts_ms: int, reason_code: str) -> None:
    ledger.log_governor(
        {
            "request_id": request_id,
            "thread_id": "thread-src",
            "turn_id": "turn-src",
            "intent_id": f"intent-{request_id}",
            "decision": "REJECT",
            "reason_code": reason_code,
            "stage": "S_GOVERNOR",
            "ts_ms": ts_ms,
        }
    )


def _log_llm_failure(ledger: SQLiteLedger, *, request_id: str, ts_ms: int, node_id: str, reason_code: str) -> None:
    ledger.log_llm_node_call(
        {
            "request_id": request_id,
            "node_id": node_id,
            "provider": "gemini",
            "model_id": "gemini-2.5-pro-preview-03-25",
            "status": "FAILED",
            "reason_code": reason_code,
            "latency_ms": 4200,
            "ts_ms": ts_ms,
        }
    )


def test_input_humanoid_loop_source_correlation_prefers_error_execution_source(client, ledger):
    now = int(time.time() * 1000)
    _seed_baseline_gateway_requests(ledger, now=now)

    _log_input_event(
        ledger,
        request_id="req-src-fail-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000,
        input_preview="帮我生成今天的项目日报",
    )
    _log_input_event(
        ledger,
        request_id="req-src-fail-1",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 300,
        reason_code="SKILL_TIMEOUT",
    )
    ledger.log_error(
        request_id="req-src-fail-1",
        stage="S_EXEC",
        reason_code="SKILL_TIMEOUT",
        detail="skill timed out",
    )
    _log_skill_failure(
        ledger,
        request_id="req-src-fail-1",
        ts_ms=now - 5 * 60 * 1000 + 280,
        skill_name="daily_report",
        result_code="SKILL_TIMEOUT",
    )

    _log_input_event(
        ledger,
        request_id="req-src-fail-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 800,
        input_preview="整理一下今天的待办",
    )
    _log_input_event(
        ledger,
        request_id="req-src-fail-2",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 1150,
        reason_code="TOOL_EXEC_FAILED",
    )
    ledger.log_error(
        request_id="req-src-fail-2",
        stage="S_EXEC",
        reason_code="TOOL_EXEC_FAILED",
        detail="tool execution failed",
    )
    _log_skill_failure(
        ledger,
        request_id="req-src-fail-2",
        ts_ms=now - 5 * 60 * 1000 + 1100,
        skill_name="task_planner",
        result_code="TOOL_EXEC_FAILED",
    )
    _log_llm_failure(
        ledger,
        request_id="req-src-fail-2",
        ts_ms=now - 5 * 60 * 1000 + 1120,
        node_id="workbench_code_assist",
        reason_code="UPSTREAM_TIMEOUT",
    )

    resp = client.get("/obs/input/humanoid-loop-source-correlation?window_minutes=60&interval_minutes=10&min_bucket_requests=1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["primary_source_system"] == "errors"
    assert data["source_count"] >= 2
    assert data["items"][0]["source_id"] == "errors"
    assert data["items"][0]["confidence_pct"] >= 80.0
    assert "EXECUTION_FAILED" in data["items"][0]["correlated_hotspots"]
    assert any(str(evidence).startswith("stage=S_EXEC") for evidence in data["items"][0]["evidence"])
    assert any(str(evidence).startswith("reason=") for evidence in data["items"][0]["evidence"])

    by_source = {item["source_id"]: item for item in data["items"]}
    assert "subsystems" in by_source
    assert by_source["subsystems"]["impact_level"] in {"warning", "critical"}


def test_input_humanoid_loop_source_correlation_prefers_gateway_for_touchless_loop(client, ledger):
    now = int(time.time() * 1000)
    _seed_baseline_gateway_requests(ledger, now=now)

    _log_input_event(
        ledger,
        request_id="req-src-touchless-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 4 * 60 * 1000,
        input_preview="发消息告诉我今晚安排",
    )
    _log_input_event(
        ledger,
        request_id="req-src-touchless-1",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 4 * 60 * 1000 + 400,
        reply_preview="今晚安排已经整理好了",
    )

    _log_input_event(
        ledger,
        request_id="req-src-touchless-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 3 * 60 * 1000,
        input_preview="提醒我晚上八点开会",
    )
    _log_input_event(
        ledger,
        request_id="req-src-touchless-2",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 3 * 60 * 1000 + 500,
        reply_preview="会议提醒已经设置完成",
    )

    resp = client.get("/obs/input/humanoid-loop-source-correlation?window_minutes=60&interval_minutes=10&min_bucket_requests=1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["primary_source_system"] == "gateway"
    assert data["items"][0]["source_id"] == "gateway"
    assert data["items"][0]["confidence_pct"] >= 85.0
    assert "COMPLETED_WITHOUT_HUMANOID_TOUCH" in data["items"][0]["correlated_hotspots"]
    assert any(str(evidence).startswith("touchless=") for evidence in data["items"][0]["evidence"])
    assert data["items"][0]["recommended_view_path"] == "/gateway"


def test_input_humanoid_loop_source_correlation_prefers_governor_for_blocked_requests(client, ledger):
    now = int(time.time() * 1000)
    _seed_baseline_gateway_requests(ledger, now=now)

    _log_input_event(
        ledger,
        request_id="req-src-blocked-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 4 * 60 * 1000,
        input_preview="直接把生产配置删掉",
    )
    _log_governor_reject(
        ledger,
        request_id="req-src-blocked-1",
        ts_ms=now - 4 * 60 * 1000 + 120,
        reason_code="HIGH_RISK_WRITE",
    )

    _log_input_event(
        ledger,
        request_id="req-src-blocked-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 3 * 60 * 1000,
        input_preview="把这台机器上的文件都清空",
    )
    _log_governor_reject(
        ledger,
        request_id="req-src-blocked-2",
        ts_ms=now - 3 * 60 * 1000 + 180,
        reason_code="DESTRUCTIVE_ACTION",
    )

    resp = client.get("/obs/input/humanoid-loop-source-correlation?window_minutes=60&interval_minutes=10&min_bucket_requests=1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["primary_source_system"] == "governor"
    assert data["items"][0]["source_id"] == "governor"
    assert "GOVERNOR_BLOCKED" in data["items"][0]["correlated_hotspots"]
    assert any(str(evidence).startswith("rejected=") for evidence in data["items"][0]["evidence"])
