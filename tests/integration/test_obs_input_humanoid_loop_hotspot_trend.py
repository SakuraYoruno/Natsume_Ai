from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from host_brain.app.obs_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from tests.fakes.obs_projection_runtime import LedgerStatsRefreshingTestClient, compose_durable_obs_projection_runtime


@pytest.fixture
def ledger(tmp_path, request):
    composition = compose_durable_obs_projection_runtime(tmp_path, request_id="obs-input-hotspot-trend")
    runtime = composition.__enter__()
    request.addfinalizer(lambda: composition.__exit__(None, None, None))
    yield runtime.ledger


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(router)
    return LedgerStatsRefreshingTestClient(app)


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    ts_ms: int,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_hotspot_trend_001",
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


def test_input_humanoid_loop_hotspot_trend_returns_time_series(client, ledger):
    now = int(time.time() * 1000)

    # Healthy baseline
    _log_input_event(ledger, request_id="req-hottrend-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-hottrend-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-hottrend-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-hottrend-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 300)

    # Bucket -15m: language feedback hotspot
    _log_input_event(
        ledger,
        request_id="req-hottrend-touch-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 15 * 60 * 1000,
        input_preview="告诉我今天晚上安排",
    )
    _log_input_event(
        ledger,
        request_id="req-hottrend-touch-1",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 15 * 60 * 1000 + 250,
        reply_preview="今晚安排已经整理完成",
    )
    _log_input_event(
        ledger,
        request_id="req-hottrend-touch-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 15 * 60 * 1000 + 600,
        input_preview="发我明天计划",
    )
    _log_input_event(
        ledger,
        request_id="req-hottrend-touch-2",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 15 * 60 * 1000 + 900,
        reply_preview="明天计划已经整理完成",
    )

    # Bucket -5m: motor execution hotspot
    _log_input_event(
        ledger,
        request_id="req-hottrend-fail-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000,
        input_preview="帮我生成日报",
    )
    _log_input_event(
        ledger,
        request_id="req-hottrend-fail-1",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 200,
        reason_code="SKILL_TIMEOUT",
    )
    ledger.log_error(
        request_id="req-hottrend-fail-1",
        stage="S_EXEC",
        reason_code="SKILL_TIMEOUT",
        detail="execution timeout",
    )

    _log_input_event(
        ledger,
        request_id="req-hottrend-fail-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 500,
        input_preview="帮我整理工作周报",
    )
    _log_input_event(
        ledger,
        request_id="req-hottrend-fail-2",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 900,
        reason_code="TOOL_EXEC_FAILED",
    )
    ledger.log_error(
        request_id="req-hottrend-fail-2",
        stage="S_EXEC",
        reason_code="TOOL_EXEC_FAILED",
        detail="tool execution failed",
    )

    resp = client.get("/obs/input/humanoid-loop-hotspot-trend?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["timeseries_labels"]) == 6
    assert len(data["timeseries_total_requests"]) == 6
    assert len(data["timeseries_abnormal_requests"]) == 6
    assert len(data["dominant_buckets"]) == 6

    series_by_system = {item["organ_system"]: item for item in data["series"]}
    assert "language_feedback" in series_by_system
    assert "motor_execution" in series_by_system

    language_series = series_by_system["language_feedback"]["request_counts"]
    motor_series = series_by_system["motor_execution"]["request_counts"]
    assert sum(language_series) == 2
    assert sum(motor_series) == 2

    latest_bucket = data["dominant_buckets"][-1]
    assert latest_bucket["dominant_organ_system"] == "motor_execution"
    assert latest_bucket["dominant_root_cause_code"] == "EXECUTION_FAILED"
    assert latest_bucket["abnormal_requests"] == 2

    assert data["latest_dominant_organ_system"] == "motor_execution"
    assert data["latest_dominant_root_cause_code"] == "EXECUTION_FAILED"
