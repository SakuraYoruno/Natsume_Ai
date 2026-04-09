from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router, set_ledger_provider
from host_brain.audit.sqlite_ledger import SQLiteLedger


@pytest.fixture
def ledger():
    db = SQLiteLedger(":memory:", check_same_thread=False)
    now = int(time.time() * 1000)

    db.log_voice_event(
        {
            "request_id": "req-immune-001",
            "session_id": "sess-immune-1",
            "event_type": "IMMUNE_CHAT_GATE",
            "direction": "HOST_INTERNAL",
            "seq": 1,
            "status": "OK",
            "reason_code": None,
            "stage": "chat.immune_gate",
            "detail": "decision=REPORT risk=medium score=0.58",
            "payload": {
                "decision": "REPORT",
                "risk_level": "medium",
                "score": 0.58,
                "reason_codes": ["PROMPT_INJECTION_DETECTED"],
            },
            "ts_ms": now - 3000,
        }
    )
    db.log_voice_event(
        {
            "request_id": "req-immune-002",
            "session_id": "sess-immune-2",
            "event_type": "INPUT_ACCEPTED",
            "direction": "EDGE_TO_HOST",
            "seq": 1,
            "status": "ACCEPTED",
            "reason_code": None,
            "stage": "input_dispatch.accepted",
            "detail": "accepted",
            "payload": {
                "phase": "ACCEPTED",
                "immune_gate": {
                    "decision": "ALLOW",
                    "risk_level": "none",
                    "score": 0.0,
                    "reason_codes": [],
                },
            },
            "ts_ms": now - 2000,
        }
    )
    db.log_voice_event(
        {
            "request_id": "req-immune-003",
            "session_id": "sess-immune-3",
            "event_type": "INPUT_REJECTED",
            "direction": "EDGE_TO_HOST",
            "seq": 1,
            "status": "REJECTED",
            "reason_code": "IMMUNE_BLOCKED_HIGH_RISK",
            "stage": "input_dispatch.rejected",
            "detail": "immune blocked",
            "payload": {
                "phase": "REJECTED",
                "immune_gate": {
                    "decision": "BLOCK",
                    "risk_level": "critical",
                    "score": 0.92,
                    "reason_codes": ["HIGH_RISK_COMMAND_PATTERN"],
                },
            },
            "ts_ms": now - 1000,
        }
    )
    yield db
    db.close()


@pytest.fixture
def client(ledger, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "host_brain.tribe.immune_agent.get_immune_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "enabled": True,
                "blocking_enabled": False,
                "warn_threshold": 0.55,
                "block_threshold": 0.85,
                "template_id": "immune_agent.runtime_rules",
                "template_rule_count": 2,
                "total_rule_count": 7,
                "counters": {"total": 9, "allow": 6, "report": 2, "block": 1},
                "last_inspection": {
                    "decision": "REPORT",
                    "risk_level": "medium",
                    "score": 0.58,
                    "reason_codes": ["PROMPT_INJECTION_DETECTED"],
                },
            }
        ),
    )
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return TestClient(app)


def test_obs_immune_gate_stats_aggregates_window_events(client):
    response = client.get("/obs/immune-gate/stats?window_minutes=60&limit=20")
    assert response.status_code == 200
    payload = response.json()

    assert payload["window_minutes"] == 60
    assert payload["total"] == 3
    assert payload["allow"] == 1
    assert payload["report"] == 1
    assert payload["block"] == 1
    assert payload["block_rate"] == pytest.approx(33.3, abs=0.1)
    assert payload["runtime_state"]["status"] == "live"
    assert payload["runtime_state"]["template_rule_count"] == 2

    reason_codes = {item["reason_code"] for item in payload["reason_codes"]}
    assert "PROMPT_INJECTION_DETECTED" in reason_codes
    assert "HIGH_RISK_COMMAND_PATTERN" in reason_codes

    assert len(payload["recent"]) == 3
    assert payload["recent"][0]["request_id"] == "req-immune-003"
    assert payload["recent"][0]["decision"] == "BLOCK"


def test_obs_immune_gate_stats_returns_degraded_runtime_on_agent_failure(monkeypatch: pytest.MonkeyPatch):
    db = SQLiteLedger(":memory:", check_same_thread=False)
    set_ledger_provider(lambda: db)
    monkeypatch.setattr(
        "host_brain.tribe.immune_agent.get_immune_agent",
        lambda: (_ for _ in ()).throw(RuntimeError("immune unavailable")),
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/obs/immune-gate/stats?window_minutes=60")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["runtime_state"]["status"] == "degraded"
    assert "immune unavailable" in str(payload["runtime_state"].get("detail") or "")
    db.close()
