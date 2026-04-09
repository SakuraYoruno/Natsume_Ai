from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import host_brain.scheduler.scheduler_routes as scheduler_routes


def test_get_scheduler_health_snapshot_safe_includes_immune_gate_state(monkeypatch) -> None:
    monkeypatch.setattr(scheduler_routes, "_registry", None, raising=False)
    monkeypatch.setattr(scheduler_routes, "_health_reporter", None, raising=False)
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
                "counters": {"total": 9, "allow": 6, "report": 3, "block": 0},
                "last_inspection": {
                    "decision": "REPORT",
                    "risk_level": "medium",
                    "score": 0.63,
                    "reason_codes": ["PROMPT_INJECTION_DETECTED"],
                },
            }
        ),
    )

    snapshot = scheduler_routes.get_scheduler_health_snapshot_safe()
    immune = dict(snapshot.get("immune_gate_state") or {})
    assert immune["status"] == "live"
    assert immune["template_rule_count"] == 2
    assert immune["last_decision"] == "REPORT"


def test_scheduler_health_endpoint_returns_immune_gate_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.tribe.immune_agent.get_immune_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "enabled": True,
                "blocking_enabled": True,
                "warn_threshold": 0.45,
                "block_threshold": 0.75,
                "template_id": "immune_agent.runtime_rules",
                "template_rule_count": 1,
                "total_rule_count": 6,
                "counters": {"total": 1, "allow": 0, "report": 0, "block": 1},
                "last_inspection": {
                    "decision": "BLOCK",
                    "risk_level": "high",
                    "score": 0.91,
                    "reason_codes": ["PROMPT_EXFILTRATION_ATTEMPT"],
                },
            }
        ),
    )
    scheduler_routes.set_scheduler_providers(
        registry=SimpleNamespace(),
        health_reporter=SimpleNamespace(
            build_health_snapshot=lambda: {
                "scheduler_status": "HEALTHY",
                "total_tasks": 0,
                "enabled_tasks": 0,
                "suspended_tasks": 0,
                "error_tasks": 0,
                "tasks": [],
            }
        ),
    )
    app = FastAPI()
    app.include_router(scheduler_routes.router)
    client = TestClient(app)

    response = client.get("/scheduler/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scheduler_status"] == "HEALTHY"
    assert payload["immune_gate_state"]["status"] == "live"
    assert payload["immune_gate_state"]["last_decision"] == "BLOCK"
