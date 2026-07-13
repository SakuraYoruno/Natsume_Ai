from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from host_brain.app.obs_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from tests.fakes.obs_projection_runtime import LedgerStatsRefreshingTestClient, compose_durable_obs_projection_runtime


@pytest.fixture
def ledger(tmp_path, request):
    composition = compose_durable_obs_projection_runtime(tmp_path, request_id="obs-input-hotspots")
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
    session_id: str = "qq_dm_obs_hotspots_001",
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


def test_input_humanoid_loop_hotspots_clusters_root_causes(client, ledger):
    now = int(time.time() * 1000)

    # Baseline healthy bucket
    _log_input_event(ledger, request_id="req-hot-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-hot-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-hot-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-hot-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 300)
    _log_input_event(ledger, request_id="req-hot-base-2", event_type="INPUT_ACCEPTED", ts_ms=now - 25 * 60 * 1000)
    _log_input_event(ledger, request_id="req-hot-base-2", event_type="INPUT_ACK_PENDING", ts_ms=now - 25 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-hot-base-2", event_type="INPUT_COMPLETED", ts_ms=now - 25 * 60 * 1000 + 300)

    # Latest bucket: two execution failures
    _log_input_event(
        ledger,
        request_id="req-hot-fail-1",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000,
        input_preview="帮我生成日报",
    )
    _log_input_event(
        ledger,
        request_id="req-hot-fail-1",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 200,
        reason_code="SKILL_TIMEOUT",
    )
    ledger.log_error(
        request_id="req-hot-fail-1",
        stage="S_EXEC",
        reason_code="SKILL_TIMEOUT",
        detail="execution timeout",
    )

    _log_input_event(
        ledger,
        request_id="req-hot-fail-2",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 500,
        input_preview="帮我整理一份任务清单",
    )
    _log_input_event(
        ledger,
        request_id="req-hot-fail-2",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 900,
        reason_code="TOOL_EXEC_FAILED",
    )
    ledger.log_error(
        request_id="req-hot-fail-2",
        stage="S_EXEC",
        reason_code="TOOL_EXEC_FAILED",
        detail="tool exec failed",
    )

    # Latest bucket: completed but no touch
    _log_input_event(
        ledger,
        request_id="req-hot-touchless",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 1200,
        input_preview="发消息告诉我晚上安排",
    )
    _log_input_event(
        ledger,
        request_id="req-hot-touchless",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 5 * 60 * 1000 + 1500,
        reply_preview="今晚安排已经整理完成",
    )

    resp = client.get("/obs/input/humanoid-loop-hotspots?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert data["diagnosed_count"] == 3
    assert data["hotspot_count"] == 2
    assert len(data["hotspots"]) == 2

    top = data["hotspots"][0]
    assert top["root_cause_code"] == "EXECUTION_FAILED"
    assert top["organ_system"] == "motor_execution"
    assert top["request_count"] == 2
    assert top["share_pct"] >= 66.0
    assert top["recommended_view_path"].startswith("/trace/")
    assert any(item["request_id"] == "req-hot-fail-1" for item in top["representative_requests"])

    second = data["hotspots"][1]
    assert second["root_cause_code"] == "COMPLETED_WITHOUT_HUMANOID_TOUCH"
    assert second["organ_system"] == "language_feedback"
    assert second["request_count"] == 1
    assert "ACK_TOUCH_LOW" in second["evidence_codes"]
