from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.internal_codex_routes import router
from host_brain.codex_runtime.job_registry import GLOBAL_CODEX_JOB_REGISTRY


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _work_order() -> dict[str, object]:
    return {
        "work_order_id": "wo-route-preview",
        "domain": "code",
        "run_mode": "inspect",
        "objective": "preview route contract",
        "workspace": {"strategy": "temp_workspace"},
        "allowed_actions": ["read_files", "generate_report"],
        "forbidden_actions": ["write_files", "modify_secret_files"],
        "review_policy": {"review_required": False},
    }


def test_codex_internal_work_order_preview_route() -> None:
    response = _client().post("/internal/codex/work-orders/preview", json={"work_order": _work_order()})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["work_order"]["run_mode"] == "inspect"


def test_codex_internal_job_route_reads_registry_job() -> None:
    job = GLOBAL_CODEX_JOB_REGISTRY.create(kind="codex_work_order", work_order=_work_order())

    response = _client().get(f"/internal/codex/jobs/{job['job_id']}")

    assert response.status_code == 200
    assert response.json()["job_id"] == job["job_id"]


def test_codex_internal_immune_routes_expose_proposal_and_auto_state() -> None:
    client = _client()
    report = {
        "overall_status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
        "components": {"edge.rpc.tts": {"status": "DOWN"}},
    }

    proposal_response = client.post("/internal/codex/immune/proposal?source=health", json={"report": report})
    state_response = client.get("/internal/codex/immune/auto-state")

    assert proposal_response.status_code == 200
    assert proposal_response.json()["work_order"]["run_mode"] == "self_heal"
    assert state_response.status_code == 200
    assert "last_job_id" in state_response.json()
