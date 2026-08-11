from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from host_brain.app.obs_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from tests.fakes.obs_projection_runtime import LedgerStatsRefreshingTestClient, compose_durable_obs_projection_runtime


@pytest.fixture
def projection_runtime(tmp_path, request):
    composition = compose_durable_obs_projection_runtime(tmp_path, request_id="obs-input-loop-trend")
    runtime = composition.__enter__()
    request.addfinalizer(lambda: composition.__exit__(None, None, None))
    yield runtime


@pytest.fixture
def ledger(projection_runtime):
    yield projection_runtime.ledger


@pytest.fixture
def client(projection_runtime):
    app = FastAPI()
    app.include_router(router)
    return LedgerStatsRefreshingTestClient(
        app,
        projection_runtime=projection_runtime,
        obs_route_ports=projection_runtime.new_route_ports(),
    )


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_001",
    status: str | None = None,
    gateway_delivery_committed: bool = False,
    ts_ms: int,
) -> None:
    normalized_status = str(status or ("OK" if "COMPLETED" in event_type else event_type.replace("INPUT_", ""))).upper()
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": event_type,
            "direction": "EDGE_TO_HOST",
            "status": normalized_status,
            "stage": f"input_dispatch.{event_type.lower()}",
            "payload": {
                "source": source,
                **(
                    {
                        "gateway_delivery_authority": "acceptance_effect_outbox",
                        "gateway_delivery_state": "OUTBOX_COMMITTED",
                    }
                    if gateway_delivery_committed
                    else {}
                ),
            },
            "detail": event_type,
            "ts_ms": ts_ms,
        }
    )


def test_input_humanoid_loop_trend_returns_bucketed_rates(client, ledger):
    now = int(time.time() * 1000)

    # req-1: closed loop
    _log_input_event(ledger, request_id="req-trend-1", event_type="INPUT_ACCEPTED", ts_ms=now - 55 * 60 * 1000)
    _log_input_event(ledger, request_id="req-trend-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 55 * 60 * 1000 + 1000)
    _log_input_event(ledger, request_id="req-trend-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 55 * 60 * 1000 + 2000)
    _log_input_event(ledger, request_id="req-trend-1", event_type="INPUT_COMPLETED", ts_ms=now - 55 * 60 * 1000 + 3000)

    # req-2: completed without humanoid touches
    _log_input_event(ledger, request_id="req-trend-2", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-trend-2", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 1200)

    # req-3: failed with deviation detected but not recovered
    _log_input_event(ledger, request_id="req-trend-3", event_type="INPUT_ACCEPTED", ts_ms=now - 15 * 60 * 1000)
    _log_input_event(ledger, request_id="req-trend-3", event_type="INPUT_ACK_PENDING", ts_ms=now - 15 * 60 * 1000 + 900)
    _log_input_event(ledger, request_id="req-trend-3", event_type="INPUT_DEVIATION_DETECTED", ts_ms=now - 15 * 60 * 1000 + 1800)
    _log_input_event(
        ledger,
        request_id="req-trend-3",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 15 * 60 * 1000 + 2600,
    )

    # req-4: current governed reply path; no retired ACK/progress event.
    _log_input_event(ledger, request_id="req-trend-4", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000)
    _log_input_event(
        ledger,
        request_id="req-trend-4",
        event_type="INPUT_COMPLETED",
        gateway_delivery_committed=True,
        ts_ms=now - 5 * 60 * 1000 + 1200,
    )

    # non-gateway should be ignored
    _log_input_event(
        ledger,
        request_id="req-trend-asr-1",
        event_type="INPUT_ACCEPTED",
        source="ASR",
        session_id="asr_sess_001",
        ts_ms=now - 10 * 60 * 1000,
    )

    resp = client.get("/obs/input/humanoid-loop-trend?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["timeseries_labels"]) == 6
    assert sum(data["timeseries_total_gateway_requests"]) == 4
    assert sum(data["timeseries_completed_requests"]) == 3
    assert sum(data["timeseries_failed_requests"]) == 1
    assert sum(data["timeseries_closed_loop_requests"]) == 2
    assert sum(data["timeseries_reply_committed_requests"]) == 1
    assert sum(data["timeseries_ack_touched_requests"]) == 2
    assert sum(data["timeseries_progress_touched_requests"]) == 1
    assert sum(data["timeseries_deviation_detected_requests"]) == 1
    assert sum(data["timeseries_deviation_recovered_requests"]) == 0

    completion_rates = [v for v in data["timeseries_completion_rate"] if v is not None]
    closed_loop_rates = [v for v in data["timeseries_closed_loop_rate"] if v is not None]
    reply_commit_rates = [v for v in data["timeseries_reply_commit_rate"] if v is not None]
    ack_touch_rates = [v for v in data["timeseries_ack_touch_rate"] if v is not None]
    progress_touch_rates = [v for v in data["timeseries_progress_touch_rate"] if v is not None]
    deviation_recovery_rates = [v for v in data["timeseries_deviation_recovery_rate"] if v is not None]

    assert completion_rates
    assert closed_loop_rates
    assert reply_commit_rates
    assert ack_touch_rates
    assert progress_touch_rates
    assert deviation_recovery_rates
    assert max(completion_rates) == 100.0
    assert max(closed_loop_rates) == 100.0
    assert max(reply_commit_rates) == 100.0
    assert max(ack_touch_rates) == 100.0
    assert max(progress_touch_rates) == 100.0
    assert min(deviation_recovery_rates) == 0.0
