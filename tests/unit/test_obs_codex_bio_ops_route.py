from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import host_brain.app.obs_routes_agent_bio_ops as route_module
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _materialize_bio_ops_projection(tmp_path) -> QueryProjectionStore:
    from host_brain.app.observability_runtime import process_state_projection

    db_path = tmp_path / "operator-projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="bio-ops-route-projection",
    )
    assert result.success, result.as_dict()
    store = QueryProjectionStore(db_path)
    refreshed = process_state_projection.refresh_bio_ops_projection(
        store=store,
        root=tmp_path,
    )
    assert refreshed["document_count"] >= 2
    return store


def _app_with_projection_store(store: QueryProjectionStore) -> FastAPI:
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: store,
    )
    app.include_router(route_module.router)
    return app


def test_codex_bio_ops_observability_compacts_latest_run(tmp_path) -> None:
    run_dir = tmp_path / "20260425_102824"
    run_dir.mkdir(parents=True)
    contract_payload = {
        "status": "EXECUTED",
        "work_order_id": "wo_inspect",
        "summary": "Codex WorkOrder executed.",
        "result_text": "BioOps capabilities inspected.",
        "changed_files": [],
        "artifacts_dir": str(run_dir / "codex_artifacts" / "wo_inspect"),
        "thread_id": "thread-1",
        "verification": {"status": "NOT_RUN"},
        "risk_flags": [],
        "review_required": False,
        "next_actions": [],
        "rollback_manifest_ref": None,
        "metadata": {
            "codex_model": "gpt-5.4-mini",
            "prompt_profile": "bio_ops_inspect",
            "sandbox_mode": "read-only",
            "returncode": 0,
        },
    }
    blocked_payload = {
        **contract_payload,
        "status": "BLOCKED",
        "work_order_id": "wo_blocked",
        "summary": "Codex WorkOrder blocked by policy.",
        "result_text": "",
        "verification": {"status": "NOT_RUN"},
        "risk_flags": ["policy_block"],
        "review_required": True,
        "reason_code": "CODEX_WORKORDER_BLOCKED",
    }
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": run_dir.name,
            "bio_ops_capability_count": 27,
            "bio_ops_status_counts": {"PROPOSED": 20, "CANDIDATE": 5, "AUDITED": 2},
            "agent_inspect_fixed_status": "EXECUTED",
            "codex_blocked_status": "BLOCKED",
            "hard_cut_scan_exit_code": 1,
            "pytest_exit_code": 0,
            "work_order_preview_ok": True,
            "immune_proposal_triggered": True,
        },
    )
    _write_json(
        run_dir / "bio_ops_daily_summary.json",
        {
            "status": "AUDITED",
            "capability_count": 27,
            "status_counts": {"PROPOSED": 20, "CANDIDATE": 5, "AUDITED": 2},
        },
    )
    _write_json(
        run_dir / "bio_ops_daily_full.json",
        {
            "results": [
                {
                    "status": "CANDIDATE",
                    "capability": "habit_compiler",
                    "summary": "Habit candidate.",
                    "metadata": {
                        "bio_ops_adoption": {
                            "status": "PENDING_REVIEW",
                            "candidate": {
                                "candidate_id": "candidate-1",
                                "capability": "habit_compiler",
                                "owner_organ": "social_brain_agent",
                                "output_kind": "habit_candidate",
                                "summary": "Habit candidate.",
                                "evidence_refs": ["chat:pattern"],
                                "next_actions": ["owner must review"],
                                "review_required": True,
                            },
                        }
                    },
                }
            ]
        },
    )
    _write_json(
        run_dir / "focused_effects.json",
        {
            "sensitive_write_blocked": {
                "status": "BLOCKED",
                "capability": "habit_compiler",
                "summary": "BioOps task blocked by policy.",
                "risk_flags": ["bio_ops_policy_block"],
                "review_required": True,
                "reason_code": "BIO_OPS_SENSITIVE_TARGET_BLOCKED",
                "detail": ".env",
                "metadata": {
                    "bio_ops_adoption": {
                        "status": "BLOCKED",
                        "policy": {"owner_organ": "social_brain_agent"},
                        "candidate": {"owner_organ": "social_brain_agent"},
                    }
                },
            }
        },
    )
    _write_json(
        run_dir / "runtime_traces.json",
        {
            "short_ack_chat": {
                "proposal_only": True,
                "reflex": {"matched": True, "reflex_type": "short_ack", "action": "chat_short_ack"},
                "attention_budget": {
                    "pattern": "short_ack_message_overprocessed",
                    "overprocessed": True,
                    "forbidden_actions": ["agent_worker"],
                },
                "internal_weather": {"internal_weather": "night_fog", "ttl_minutes": 30},
                "active_silence": {"silence_policy": "quiet_presence", "reason_code": "LATE_NIGHT_HIGH_LOAD_NO_ASK"},
                "response_breathing": {"reply_density": "normal", "reason_code": "DEFAULT_BREATHING"},
                "forbidden_actions": ["agent_worker"],
            },
            "complex_code_request": {
                "proposal_only": True,
                "reflex": {"matched": False, "reason_code": "NO_LOW_RISK_REFLEX"},
                "attention_budget": {"pattern": "within_budget", "overprocessed": False, "forbidden_actions": []},
                "internal_weather": {"internal_weather": "clear", "ttl_minutes": 30},
                "active_silence": {"silence_policy": "normal_presence", "reason_code": "NO_ACTIVE_SILENCE"},
                "response_breathing": {"reply_density": "compact_status"},
                "forbidden_actions": [],
            },
        },
    )
    _write_json(run_dir / "agent_inspect_fixed_result.json", contract_payload)
    _write_json(run_dir / "codex_blocked_write_result.json", blocked_payload)
    _write_json(run_dir / "hard_cut_scan.json", {"exit_code": 1})
    _write_json(run_dir / "pytest_result.json", {"exit_code": 0})

    store = _materialize_bio_ops_projection(tmp_path)
    app = _app_with_projection_store(store)

    response = TestClient(app).get("/obs/codex-bio-ops")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["run_id"] == run_dir.name
    assert payload["artifact_ref"] == f"bio-ops://{run_dir.name}"
    assert payload["codex_runtime"] == {}
    assert payload["benefits"] == []
    assert payload["runtime_traces"] == []
    assert payload["bio_ops"]["status_counts"]["CANDIDATE"] == 5
    adoption = payload["bio_ops"]["pending_adoptions"][0]
    assert adoption["candidate_id"] == "candidate-1"
    assert adoption["owner_organ"] == "social_brain_agent"
    assert adoption["output_kind"] == "habit_candidate"
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "Habit candidate." not in serialized
    assert "result_text" not in serialized
    assert str(run_dir) not in serialized

def _tree_snapshot(root) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_codex_bio_ops_legacy_mutations_are_unregistered_and_side_effect_free(
    tmp_path,
) -> None:
    run_dir = tmp_path / "20260425_120000"
    candidate_dir = run_dir / "bio_ops_task_artifacts" / "habit_compiler" / "task-1"
    _write_json(
        candidate_dir / "adoption_candidate.json",
        {
            "candidate_id": "candidate-1",
            "task_id": "task-1",
            "capability": "habit_compiler",
            "owner_organ": "social_brain_agent",
            "output_kind": "habit_candidate",
            "summary": "Habit candidate needs owner review.",
            "payload": {"proposal": {"kind": "habit_candidate"}},
            "evidence_refs": ["chat:pattern"],
            "artifacts": [],
            "risk_flags": [],
            "review_required": True,
            "next_actions": ["owner review required"],
            "status": "PENDING_REVIEW",
            "created_at_ms": 1,
        },
    )
    store = _materialize_bio_ops_projection(tmp_path)
    app = _app_with_projection_store(store)
    client = TestClient(app)

    listed = client.get("/obs/codex-bio-ops/adoptions")
    detail = client.get("/obs/codex-bio-ops/adoptions/candidate-1")
    assert listed.status_code == 200
    assert listed.json()["adoptions"][0]["candidate_id"] == "candidate-1"
    assert detail.status_code == 200
    assert detail.json()["adoption"]["decision"] is None

    before = _tree_snapshot(tmp_path)
    removed_mutations = (
        (
            "/obs/codex-bio-ops/daily-cycle/run",
            {"run_id": "must-not-exist", "inputs": {"message": "continue"}},
        ),
        (
            "/obs/codex-bio-ops/adoptions/candidate-1/accept",
            {"owner_organ": "social_brain_agent"},
        ),
        (
            "/obs/codex-bio-ops/adoptions/candidate-1/reject",
            {"owner_organ": "social_brain_agent"},
        ),
        ("/obs/codex-bio-ops/owner-inbox/materialize", {}),
        ("/obs/codex-bio-ops/owner-inbox/intake-packets", {}),
        ("/obs/codex-bio-ops/owner-queues/consume", {}),
        ("/obs/codex-bio-ops/owner-state/import", {}),
        (
            "/obs/codex-bio-ops/owner-state/candidate-1/accept",
            {"owner_organ": "social_brain_agent"},
        ),
        (
            "/obs/codex-bio-ops/owner-state/candidate-1/reject",
            {"owner_organ": "social_brain_agent"},
        ),
    )
    for path, payload in removed_mutations:
        response = client.post(path, json=payload)
        assert response.status_code in {404, 405}, path
    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "must-not-exist").exists()

    pure_queries = (
        "/obs/codex-bio-ops/adoptions",
        "/obs/codex-bio-ops/adoptions/candidate-1",
        "/obs/codex-bio-ops/owner-inbox",
        "/obs/codex-bio-ops/owner-inbox/import-candidates",
        "/obs/codex-bio-ops/owner-queues",
        "/obs/codex-bio-ops/owner-state",
        "/obs/codex-bio-ops/owner-state/runtime-hints",
    )
    for path in pure_queries:
        assert client.get(path).status_code == 200, path


def test_all_bio_ops_get_routes_read_only_the_app_local_projection_store(
    tmp_path,
) -> None:

    db_path = tmp_path / "explicit-bio-ops-projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="bio-ops-explicit-store",
    )
    assert result.success, result.as_dict()
    store = QueryProjectionStore(db_path)
    store.upsert(
        route_module.BIO_OPS_KIND,
        route_module.BIO_OPS_LATEST_KEY,
        {
            "ok": True,
            "artifact_available": True,
            "run_id": "app-store-run",
            "artifact_ref": "bio-ops://app-store-run",
            "capability_count": 1,
            "status_counts": {"CANDIDATE": 1},
            "adoptions": [
                {
                    "candidate_id": "candidate-from-app-store",
                    "pending": True,
                    "status": "PENDING_REVIEW",
                }
            ],
            "owner_inbox": [
                {
                    "candidate_id": "inbox-from-app-store",
                    "status": "PENDING_REVIEW",
                }
            ],
            "owner_import_candidates": [
                {"candidate_id": "import-from-app-store"}
            ],
            "owner_queues": [{"candidate_id": "queue-from-app-store"}],
            "owner_state": [{"candidate_id": "state-from-app-store"}],
            "owner_runtime_hints": [
                {
                    "candidate_id": "hint-from-app-store",
                    "status": "ACTIVE",
                }
            ],
        },
        source_cursor=1,
        source_ref="test:app-local-bio-ops",
    )

    client = TestClient(_app_with_projection_store(store))

    cases = (
        ("/obs/codex-bio-ops", "run_id", "app-store-run"),
        (
            "/obs/codex-bio-ops/adoptions",
            "adoptions",
            "candidate-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/adoptions/candidate-from-app-store",
            "adoption",
            "candidate-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/owner-inbox",
            "items",
            "inbox-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/owner-inbox/import-candidates",
            "items",
            "import-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/owner-queues",
            "items",
            "queue-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/owner-state",
            "items",
            "state-from-app-store",
        ),
        (
            "/obs/codex-bio-ops/owner-state/runtime-hints",
            "items",
            "hint-from-app-store",
        ),
    )
    for path, field, expected in cases:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        if field == "run_id":
            assert payload[field] == expected
        elif field == "adoption":
            assert payload[field]["candidate_id"] == expected
        else:
            assert payload[field][0]["candidate_id"] == expected
        assert payload["projection"]["key"] == route_module.BIO_OPS_LATEST_KEY
