from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.internal_agent_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.agent_runtime.job_registry import GLOBAL_AGENT_JOB_REGISTRY
from host_brain.memory.life_memory import LifeMemoryStore


ROOT = Path(__file__).resolve().parents[2]
REVIEW_STORE_MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0055_agent_runtime_review_store.sql"


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
    job = GLOBAL_AGENT_JOB_REGISTRY.create(kind="codex_work_order", work_order=_work_order())

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


def test_codex_internal_owner_directive_route_persists_life_memory(monkeypatch) -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        store = LifeMemoryStore(ledger=ledger)
        monkeypatch.setattr("host_brain.app.dependencies.get_life_memory_store", lambda: store)

        response = _client().post(
            "/internal/codex/owner-directives",
            json={
                "request_id": "owner-route-1",
                "directive_text": "以后推荐轻小说时，每本简介后换行，不要套模板。",
                "distill": False,
            },
        )

        assert response.status_code == 200
        memory = response.json()["owner_directive_memory"]
        assert memory["status"] == "OK"
        assert memory["episode_id"]
        assert len(ledger.life_episode) == 1
        assert ledger.life_episode[0]["action"] == "owner_directive"
        assert ledger.life_evidence[0]["source_ref"] == "chat://request/owner-route-1#owner"
    finally:
        ledger.close()


def test_codex_internal_work_order_execute_persists_explicit_owner_directive(monkeypatch) -> None:
    class FakeExecutor:
        def execute(self, order):
            return {"status": "EXECUTED", "work_order_id": order.work_order_id}

    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        store = LifeMemoryStore(ledger=ledger)
        monkeypatch.setattr("host_brain.app.dependencies.get_life_memory_store", lambda: store)
        monkeypatch.setattr("host_brain.app.internal_agent_routes.AgentRuntimeExecutor", FakeExecutor)

        work_order = _work_order()
        work_order["metadata"] = {
            "owner_directive": {
                "request_id": "owner-wo-1",
                "directive_text": "Thalamus LLM 用模型池，优先 DeepSeek，不要用 Pro。",
            },
            "distill_owner_directive": False,
        }
        response = _client().post("/internal/codex/work-orders/execute", json={"work_order": work_order})

        assert response.status_code == 200
        payload = response.json()
        assert payload["result"]["status"] == "EXECUTED"
        assert payload["owner_directive_memory"]["status"] == "OK"
        assert len(ledger.life_episode) == 1
        assert ledger.life_episode[0]["primary_ref"] == "owner://directive/owner-wo-1"
    finally:
        ledger.close()


def test_codex_internal_work_order_execute_does_not_persist_unmarked_work_order(monkeypatch) -> None:
    class FakeExecutor:
        def execute(self, order):
            return {"status": "EXECUTED", "work_order_id": order.work_order_id}

    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        store = LifeMemoryStore(ledger=ledger)
        monkeypatch.setattr("host_brain.app.dependencies.get_life_memory_store", lambda: store)
        monkeypatch.setattr("host_brain.app.internal_agent_routes.AgentRuntimeExecutor", FakeExecutor)

        response = _client().post("/internal/codex/work-orders/execute", json={"work_order": _work_order()})

        assert response.status_code == 200
        payload = response.json()
        assert payload["result"]["status"] == "EXECUTED"
        assert "owner_directive_memory" not in payload
        assert ledger.life_episode == []
    finally:
        ledger.close()


def test_codex_internal_work_order_execute_attaches_legacy_runtime_assignment(monkeypatch) -> None:
    class FakeExecutor:
        def execute(self, order):
            return {
                "status": "EXECUTED",
                "work_order_id": order.work_order_id,
                "runtime_assignment": order.metadata["runtime_assignment"],
            }

    monkeypatch.setattr("host_brain.app.internal_agent_routes.AgentRuntimeExecutor", FakeExecutor)

    response = _client().post("/internal/codex/work-orders/execute", json={"work_order": _work_order()})

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_assignment"]["executor"] == "legacy"
    assert payload["result"]["runtime_assignment"]["executor"] == "legacy"


def test_codex_internal_runtime_drain_report_reads_legacy_registry() -> None:
    GLOBAL_AGENT_JOB_REGISTRY.reset()
    job = GLOBAL_AGENT_JOB_REGISTRY.create(kind="codex_work_order", work_order=_work_order())

    response = _client().get("/internal/codex/runtime/drain-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["drain"]["drained"] is False
    assert job["job_id"] in payload["drain"]["legacy_open_job_ids"]
    GLOBAL_AGENT_JOB_REGISTRY.reset()


def test_codex_internal_review_routes_resume_once(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "review-routes.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(REVIEW_STORE_MIGRATION.read_text(encoding="utf-8"))
    monkeypatch.setenv("HOST_AGENT_REVIEW_DB_PATH", str(db_path))
    client = _client()
    review = {
        "schema_version": "Natsume.agent.review_item.v1",
        "review_id": "review-route-1",
        "review_type": "POST_EXECUTION_ACCEPTANCE",
        "subject_ref": "work-order://route/1",
        "subject_version": 1,
        "requester": "codex-route",
        "reason": "acceptance checkpoint requires owner review",
        "risk_level": 3,
        "side_effects": ["write_files"],
        "evidence_refs": ["evidence://route/review/1"],
        "status": "PENDING",
        "expires_at": 9_999_999_999_999,
        "resume_phase": "POST_EXECUTION_ACCEPTANCE",
        "checkpoint_id": "checkpoint://route/post/1",
        "resume_count": 0,
    }

    create_response = client.post("/internal/codex/reviews", json={"review": review})
    assert create_response.status_code == 200

    approve_response = client.post(
        "/internal/codex/reviews/review-route-1/approve",
        json={
            "actor_id": "owner",
            "subject_ref": "work-order://route/1",
            "subject_version": 1,
            "resume_phase": "POST_EXECUTION_ACCEPTANCE",
            "checkpoint_id": "checkpoint://route/post/1",
        },
    )
    assert approve_response.status_code == 200
    resume_token = approve_response.json()["resume_token_ref"]

    resume_response = client.post(
        "/internal/codex/reviews/review-route-1/resume",
        json={
            "resume_token_ref": resume_token,
            "subject_ref": "work-order://route/1",
            "subject_version": 1,
            "resume_phase": "POST_EXECUTION_ACCEPTANCE",
            "checkpoint_id": "checkpoint://route/post/1",
            "resume_result_ref": "review-resume://route/post/1",
        },
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["resume"]["resume_action"] == "RETURN_POST_EXECUTION_CHECKPOINT"
    assert resume_response.json()["resume"]["replay_side_effects"] is False

    duplicate_response = client.post(
        "/internal/codex/reviews/review-route-1/resume",
        json={
            "resume_token_ref": resume_token,
            "subject_ref": "work-order://route/1",
            "subject_version": 1,
            "resume_phase": "POST_EXECUTION_ACCEPTANCE",
            "checkpoint_id": "checkpoint://route/post/1",
            "resume_result_ref": "review-resume://route/post/duplicate",
        },
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"]["reason_code"] == "REVIEW_RESUME_ALREADY_CONSUMED"
