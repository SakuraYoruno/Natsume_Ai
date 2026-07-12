from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from host_brain.app.internal_agent_routes import router
from host_brain.app.settings.core import get_core_settings
from host_brain.app.settings.internal_agent import get_internal_agent_settings
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEvent, RuntimeEventStore
from host_brain.memory.life_memory import LifeMemoryStore


ROOT = Path(__file__).resolve().parents[2]
REVIEW_STORE_MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0055_agent_runtime_review_store.sql"


def _migrate_runtime_event_db(db_path: Path) -> None:
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"codex-runtime-event-fixture::{db_path.name}",
    )
    assert result.success, result.as_dict()


@pytest.fixture(autouse=True)
def _clear_route_settings_caches():
    get_core_settings.cache_clear()
    get_internal_agent_settings.cache_clear()
    yield
    get_core_settings.cache_clear()
    get_internal_agent_settings.cache_clear()


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


def test_codex_internal_job_route_reports_legacy_registry_retired() -> None:
    response = _client().get("/internal/codex/jobs/legacy-job")

    assert response.status_code == 410
    assert response.json()["detail"] == "LEGACY_AGENT_JOB_REGISTRY_RETIRED"


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


def test_codex_internal_work_order_execute_fails_closed_without_governed_dependencies() -> None:
    response = _client().post("/internal/codex/work-orders/execute", json={"work_order": _work_order()})

    assert response.status_code == 503
    assert response.json()["detail"] == "INTERNAL_AGENT_RUNTIME_UNAVAILABLE"


def test_codex_internal_runtime_drain_report_uses_durable_authority() -> None:
    response = _client().get("/internal/codex/runtime/drain-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["drain"] == {
        "status": "DRAINED",
        "legacy_registry_retired": True,
        "active_legacy_jobs": 0,
        "authority": "durable_task_runtime",
    }


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


def _runtime_route_event(index: int) -> RuntimeEvent:
    return RuntimeEvent.create(
        event_type="TASK_GRAPH_CREATED",
        source_kind="TASK",
        source_ref=f"task://route/runtime-event-{index}",
        aggregate_type="TASK_GRAPH",
        aggregate_id=f"route-runtime-event-{index}",
        aggregate_version=index,
        payload_ref=f"taskstore://event/TASK_GRAPH/route-runtime-event-{index}/{index}",
        payload={
            "aggregate_type": "TASK_GRAPH",
            "aggregate_id": f"route-runtime-event-{index}",
            "aggregate_version": index,
            "payload_ref": f"taskstore://event/TASK_GRAPH/route-runtime-event-{index}/{index}",
        },
        evidence_refs=(f"evidence://route/runtime-event/{index}",),
        ts_ms=1_700_000_000_000 + index,
    )


def test_codex_internal_runtime_events_route_replays_cursor(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "runtime-event-routes.sqlite3"
    _migrate_runtime_event_db(db_path)
    store = RuntimeEventStore(db_path)
    first = store.append_event(_runtime_route_event(1))
    second = store.append_event(_runtime_route_event(2))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(db_path))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_REPLAY_ENABLED", "true")
    get_core_settings.cache_clear()

    response = _client().get(f"/internal/codex/runtime/events?after={first['cursor']}&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_count"] == 1
    assert payload["next_cursor"] == second["cursor"]
    assert payload["events"][0]["cursor"] == second["cursor"]
    assert payload["events"][0]["dedupe_key"] == second["dedupe_key"]
    get_core_settings.cache_clear()


def test_codex_internal_runtime_events_sse_replays_after_last_event_id(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "runtime-event-sse-routes.sqlite3"
    _migrate_runtime_event_db(db_path)
    store = RuntimeEventStore(db_path)
    first = store.append_event(_runtime_route_event(1))
    second = store.append_event(_runtime_route_event(2))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(db_path))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_REPLAY_ENABLED", "true")
    get_core_settings.cache_clear()

    response = _client().get(
        "/internal/codex/runtime/events/sse?limit=10",
        headers={"Last-Event-ID": str(first["cursor"])},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert f"id: {second['cursor']}" in response.text
    assert '"event_id":"' in response.text
    assert str(first["event_id"]) not in response.text
    assert "event: runtime_event_cursor" in response.text
    get_core_settings.cache_clear()


def test_codex_internal_runtime_events_route_respects_replay_flag(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "runtime-event-disabled-routes.sqlite3"
    _migrate_runtime_event_db(db_path)
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(db_path))
    monkeypatch.delenv("HOST_RUNTIME_EVENT_REPLAY_ENABLED", raising=False)
    get_core_settings.cache_clear()

    response = _client().get("/internal/codex/runtime/events")

    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "RUNTIME_EVENT_REPLAY_DISABLED"
    get_core_settings.cache_clear()
