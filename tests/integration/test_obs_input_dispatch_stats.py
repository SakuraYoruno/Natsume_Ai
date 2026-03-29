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


def _log_voice_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    session_id: str,
    event_type: str,
    source: str,
    ts_ms: int,
    latency_ms: int | None = None,
) -> None:
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": event_type,
            "direction": "EDGE_TO_HOST",
            "status": "OK" if "COMPLETED" in event_type else event_type.replace("INPUT_", ""),
            "stage": f"input_dispatch.{event_type.lower()}",
            "payload": {
                "source": source,
                "latency_ms": latency_ms,
            },
            "detail": event_type,
            "ts_ms": ts_ms,
        }
    )


def test_input_dispatch_stats_reads_input_rows_from_voice_payload(client, ledger):
    now = int(time.time() * 1000)
    _log_voice_input_event(
        ledger,
        request_id="req-gateway-accepted",
        session_id="qq_dm_3414578517",
        event_type="INPUT_ACCEPTED",
        source="GATEWAY",
        ts_ms=now - 4_000,
    )
    _log_voice_input_event(
        ledger,
        request_id="req-gateway-dispatched",
        session_id="qq_dm_3414578517",
        event_type="INPUT_DISPATCHED",
        source="GATEWAY",
        ts_ms=now - 3_000,
    )
    _log_voice_input_event(
        ledger,
        request_id="req-gateway-completed",
        session_id="qq_dm_3414578517",
        event_type="INPUT_COMPLETED",
        source="GATEWAY",
        ts_ms=now - 2_000,
        latency_ms=19_724,
    )

    resp = client.get("/obs/input/dispatch-stats?window_minutes=60&interval_minutes=5")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_accepted"] == 1
    assert data["total_completed"] == 1
    assert data["total_failed"] == 0
    assert data["total_not_wired"] == 0
    assert data["overall_success_rate"] == 100.0
    assert sum(data["timeseries_accepted"]) == 1
    assert sum(data["timeseries_completed"]) == 1

    gateway = next(item for item in data["sources"] if item["source"] == "GATEWAY")
    assert gateway["accepted"] == 1
    assert gateway["completed"] == 1
    assert gateway["avg_latency_ms"] == 19724.0
    assert gateway["success_rate"] == 100.0


def test_input_dispatch_stats_does_not_truncate_voice_event_window(client, ledger):
    now = int(time.time() * 1000)
    for idx in range(120):
        _log_voice_input_event(
            ledger,
            request_id=f"req-asr-{idx:03d}",
            session_id=f"asr-{idx:03d}",
            event_type="INPUT_ACCEPTED",
            source="ASR",
            ts_ms=now - 10_000 + idx,
        )

    resp = client.get("/obs/input/dispatch-stats?window_minutes=60&interval_minutes=5")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_accepted"] == 120
    assert sum(data["timeseries_accepted"]) == 120

    asr = next(item for item in data["sources"] if item["source"] == "ASR")
    assert asr["accepted"] == 120
