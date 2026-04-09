from __future__ import annotations

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


def test_scheduler_history_includes_reason_and_gate_aggregates(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "host_brain.scheduler.scheduler_routes.get_scheduler_health_snapshot_safe",
        lambda: {
            "scheduler_status": "DEGRADED",
            "total_tasks": 4,
            "enabled_tasks": 3,
            "suspended_tasks": 0,
            "error_tasks": 1,
            "tasks": [
                {
                    "task_id": "proactive_talk_periodic",
                    "status": "RUNNING",
                    "enabled": True,
                    "run_count": 10,
                    "error_count": 1,
                    "last_run_ts": 1710000001000,
                    "last_error": None,
                    "last_reason_code": "ENDOCRINE_PROACTIVE_BLOCKED",
                    "last_gate_state": "cautious",
                    "last_mood_state": "steady",
                    "last_endocrine_cortisol": 0.71,
                    "last_endocrine_energy": 0.33,
                },
                {
                    "task_id": "inner_world_tick",
                    "status": "SUCCESS",
                    "enabled": True,
                    "run_count": 20,
                    "error_count": 0,
                    "last_run_ts": 1710000002000,
                    "last_error": None,
                    "last_reason_code": None,
                    "last_gate_state": "open",
                    "last_mood_state": "steady",
                    "last_endocrine_cortisol": 0.24,
                    "last_endocrine_energy": 0.69,
                },
                {
                    "task_id": "silence_tick",
                    "status": "FAILED",
                    "enabled": True,
                    "run_count": 5,
                    "error_count": 2,
                    "last_run_ts": 1710000003000,
                    "last_error": "dispatch failed",
                    "last_reason_code": "EMOTION_GATE_BLOCKED",
                    "last_gate_state": "protective",
                    "last_mood_state": "critical",
                    "last_endocrine_cortisol": 0.91,
                    "last_endocrine_energy": 0.12,
                },
                {
                    "task_id": "manual_task",
                    "status": "DISABLED",
                    "enabled": False,
                    "run_count": 0,
                    "error_count": 0,
                    "last_run_ts": None,
                    "last_error": None,
                    "last_reason_code": "SYSMODE_FORBIDDEN",
                    "last_gate_state": "sys_mode",
                    "last_mood_state": "steady",
                    "last_endocrine_cortisol": 0.0,
                    "last_endocrine_energy": 0.0,
                },
            ],
        },
    )

    resp = client.get("/obs/scheduler/history?window_minutes=60")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_tasks"] == 4
    assert data["running_count"] == 1
    assert data["success_count"] == 1
    assert data["failed_count"] == 1
    assert data["disabled_count"] == 1
    assert data["gate_blocked_total"] == 3
    assert data["gate_blocked_rate"] == 75.0

    blocked_codes = {item["reason_code"] for item in data["blocked_reason_codes"]}
    assert "ENDOCRINE_PROACTIVE_BLOCKED" in blocked_codes
    assert "EMOTION_GATE_BLOCKED" in blocked_codes
    assert "SYSMODE_FORBIDDEN" in blocked_codes

    gate_states = {item["gate_state"] for item in data["gate_states"]}
    assert "cautious" in gate_states
    assert "protective" in gate_states
    assert "open" in gate_states

    proactive_task = next(item for item in data["tasks"] if item["task_id"] == "proactive_talk_periodic")
    assert proactive_task["last_reason_code"] == "ENDOCRINE_PROACTIVE_BLOCKED"
    assert proactive_task["last_gate_state"] == "cautious"
    assert proactive_task["last_mood_state"] == "steady"


def test_scheduler_history_counts_immune_proactive_suppressed_as_gate_blocked(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "host_brain.scheduler.scheduler_routes.get_scheduler_health_snapshot_safe",
        lambda: {
            "scheduler_status": "DEGRADED",
            "total_tasks": 1,
            "enabled_tasks": 1,
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [
                {
                    "task_id": "proactive_talk_periodic",
                    "status": "SUCCESS",
                    "enabled": True,
                    "run_count": 3,
                    "error_count": 0,
                    "last_run_ts": 1710000001000,
                    "last_error": None,
                    "last_reason_code": "IMMUNE_PROACTIVE_SUPPRESSED",
                    "last_gate_state": "immune:block",
                    "last_mood_state": "steady",
                    "last_endocrine_cortisol": 0.32,
                    "last_endocrine_energy": 0.62,
                    "last_immune_decision": "BLOCK",
                    "last_immune_risk_level": "high",
                    "last_immune_reason_codes": ["PROMPT_INJECTION_DETECTED"],
                }
            ],
        },
    )

    resp = client.get("/obs/scheduler/history?window_minutes=60")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gate_blocked_total"] == 1
    assert data["gate_blocked_rate"] == 100.0
    blocked_codes = {item["reason_code"] for item in data["blocked_reason_codes"]}
    assert "IMMUNE_PROACTIVE_SUPPRESSED" in blocked_codes
    task = data["tasks"][0]
    assert task["last_immune_decision"] == "BLOCK"
    assert task["last_immune_risk_level"] == "high"
    assert task["last_immune_reason_codes"] == ["PROMPT_INJECTION_DETECTED"]
