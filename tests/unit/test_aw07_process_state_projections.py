from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import time
from types import SimpleNamespace

from fastapi import FastAPI, Request

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore
from natsume_contracts.operator.v1 import ProjectionDegradedState


def _document(payload: dict, *, cursor: int = 7):
    now = int(time.time() * 1000)
    return SimpleNamespace(
        payload=payload,
        schema_version="Natsume.operator.query_projection.v1",
        projection_version=2,
        projection_cursor=cursor + 1,
        source_cursor=cursor,
        source_digest="sha256:" + "0" * 64,
        freshness_ts_ms=now,
        degraded_state=ProjectionDegradedState.HEALTHY,
        degraded_detail=None,
    )


def _request_for_store(store) -> Request:  # noqa: ANN001
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: store,
    )
    return Request({"type": "http", "app": app})


def test_summary_runtime_resource_and_life_gets_never_call_sources(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_core as core
    from host_brain.app import obs_routes_life_body as life
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app import obs_routes_sysmode as sysmode
    from host_brain.app.observability_runtime import process_state_projection as projection

    documents = {
        (projection.INSIGHTS_SUMMARY_KIND, "window:60"): _document(
            {"window_minutes": 60, "timestamp": 10, "govData": {"total": 1}}
        ),
        (projection.DIGITAL_LIFE_OVERVIEW_KIND, "window:360"): _document(
            {"window_minutes": 360, "timestamp": 11, "servicesData": {"total": 2}}
        ),
        (projection.RUNTIME_SNAPSHOT_KIND, projection.RUNTIME_SNAPSHOT_KEY): _document(
            {"persona_user_id": "user_1", "sys_mode": "normal", "timestamp": 12}
        ),
        (projection.LIFE_BODY_KIND, "window:360"): _document(
            {"window_minutes": 360, "timestamp": 13, "model": {"timelineEvents": []}}
        ),
        (projection.RESOURCE_MANAGER_KIND, projection.RESOURCE_MANAGER_KEY): _document(
            {"status": "OK", "source": "live_manager", "timestamp": 14, "running": True}
        ),
    }
    store = SimpleNamespace(get=lambda kind, key: documents.get((kind, key)))
    request = _request_for_store(store)

    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("GET called a live/file/aggregate source")

    monkeypatch.setattr(summary, "_build_insights_summary_snapshot_sync", forbidden)
    monkeypatch.setattr(summary, "_build_digital_life_overview_snapshot_async", forbidden)
    monkeypatch.setattr(sysmode, "_build_runtime_snapshot_response", forbidden)
    monkeypatch.setattr(life, "_build_life_body_payload", forbidden)
    monkeypatch.setattr(life, "_build_targeted_request_event", forbidden)
    monkeypatch.setattr(dependencies, "get_resource_manager", forbidden)
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        forbidden,
        raising=False,
    )

    insights = summary.get_insights_summary_snapshot(
        request=request,
        window_minutes=60,
    )
    overview = asyncio.run(
        summary.get_digital_life_overview_snapshot(
            request=request,
            window_minutes=360,
        )
    )
    runtime = sysmode.get_digital_life_runtime_snapshot(
        request=request,
        current_topic="private topic",
    )
    body = life.get_life_body(request=request, window_minutes=360)
    timeline = life.get_life_timeline(
        request=request,
        window_minutes=360,
        event_type="",
        organ="",
        request_id="missing",
        session_id="",
        tick_id="",
    )
    resource = core.get_resource_manager_status(request=request)

    assert insights["govData"]["total"] == 1
    assert insights["projection"]["projection_version"] == 2
    assert overview["servicesData"]["total"] == 2
    assert runtime.sys_mode == "normal"
    assert runtime.current_topic == ""
    assert body.meta["projection"]["source_cursor"] == 7
    assert timeline.items == []
    assert resource["status"] == "OK"
    assert resource["projection"]["freshness_ts_ms"] > 0


def test_bio_ops_gets_use_metadata_projection_only(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_agent_bio_ops as routes

    payload = {
        "ok": True,
        "artifact_available": True,
        "generated_at_ms": 100,
        "run_id": "run-1",
        "artifact_ref": "bio-ops://run-1",
        "capability_count": 1,
        "status_counts": {"PASS": 1},
        "adoptions": [
            {
                "candidate_id": "candidate-1",
                "owner_organ": "memory",
                "status": "PENDING_REVIEW",
                "pending": True,
                "source_ref": "bio-ops://run-1/adoptions/candidate-1",
            }
        ],
        "owner_inbox": [
            {"candidate_id": "candidate-1", "owner_organ": "memory", "status": "PENDING_REVIEW"}
        ],
        "owner_import_candidates": [],
        "owner_queues": [],
        "owner_state": [],
        "owner_runtime_hints": [],
    }
    store = SimpleNamespace(get=lambda _kind, _key: _document(payload))
    request = _request_for_store(store)

    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("BioOps GET scanned files")

    monkeypatch.setattr(routes, "_find_run_dir", forbidden)
    monkeypatch.setattr(routes, "_adoption_context", forbidden)
    monkeypatch.setattr(routes.BioOpsOwnerInbox, "scan", forbidden)
    monkeypatch.setattr(routes.BioOpsOwnerInbox, "scan_import_candidates", forbidden)
    monkeypatch.setattr(routes.BioOpsOwnerIntakeConsumer, "scan_queue", forbidden)
    monkeypatch.setattr(routes.OwnerCandidateStateStore, "scan_state", forbidden)
    monkeypatch.setattr(routes.OwnerCandidateStateStore, "scan_runtime_hints", forbidden)
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        forbidden,
        raising=False,
    )

    overview = routes.get_codex_bio_ops_observability(request, "run-1")
    adoptions = routes.list_codex_bio_ops_adoptions(request, "run-1", "pending")
    adoption = routes.get_codex_bio_ops_adoption(request, "candidate-1", "run-1")
    inbox = routes.get_codex_bio_ops_owner_inbox(request, "run-1", "memory", "all")
    imports = routes.get_codex_bio_ops_owner_import_candidates(
        request,
        "run-1",
        "memory",
    )
    queues = routes.get_codex_bio_ops_owner_queues(request, "run-1", "memory")
    state = routes.get_codex_bio_ops_owner_state(request, "run-1", "memory")
    hints = routes.get_codex_bio_ops_owner_runtime_hints(
        request,
        "run-1",
        "memory",
        True,
    )

    assert overview["artifact_dir"] is None
    assert overview["bio_ops"]["pending_adoptions"][0]["candidate_id"] == "candidate-1"
    assert adoptions["count"] == 1
    assert adoption["adoption"]["source_ref"].startswith("bio-ops://")
    assert inbox["count"] == 1
    assert imports["items"] == []
    assert queues["items"] == []
    assert state["items"] == []
    assert hints["items"] == []


def test_empty_process_projections_preserve_shape_and_degradation(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_agent_bio_ops as bio
    from host_brain.app import obs_routes_core as core
    from host_brain.app import obs_routes_life_body as life
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app import obs_routes_sysmode as sysmode

    request = _request_for_store(SimpleNamespace(get=lambda _kind, _key: None))
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        lambda: (_ for _ in ()).throw(AssertionError("GET touched process locator")),
        raising=False,
    )

    values = (
        summary.get_insights_summary_snapshot(
            request=request,
            window_minutes=60,
        )["projection"],
        asyncio.run(
            summary.get_digital_life_overview_snapshot(
                request=request,
                window_minutes=360,
            )
        )["projection"],
        sysmode.get_digital_life_runtime_snapshot(
            request=request,
            current_topic="",
        ).projection,
        life.get_life_body(request=request, window_minutes=360).meta["projection"],
        core.get_resource_manager_status(request=request)["projection"],
        bio.get_codex_bio_ops_observability(request, None)["projection"],
    )
    for projection in values:
        assert projection["projection_version"] == 0
        assert projection["projection_cursor"] == 0
        assert projection["cursor"] == 0
        assert projection["freshness_ts_ms"] == 0
        assert projection["degraded_state"] == "REBUILDING"


def test_bio_ops_projection_sqlite_contains_no_owner_body_or_path(tmp_path) -> None:
    from host_brain.app.observability_runtime import process_state_projection as projection

    run_dir = tmp_path / "observable_effects" / "run-private"
    candidate_dir = run_dir / "candidate"
    candidate_dir.mkdir(parents=True)
    secret = "OWNER_PACKET_PRIVATE_BODY_9f35"
    local_path = str(tmp_path)
    (candidate_dir / "adoption_candidate.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-private",
                "capability": "memory.repair",
                "owner_organ": "memory",
                "output_kind": "memory_candidate",
                "status": "PENDING_REVIEW",
                "summary": secret,
                "payload": {"private_body": secret, "path": local_path},
                "evidence_refs": [local_path],
                "created_at_ms": 123,
            }
        ),
        encoding="utf-8",
    )
    sources = projection.load_bio_ops_projection_sources(tmp_path / "observable_effects")
    assert len(sources) == 1
    run_id, payload, cursor = sources[0]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert secret not in serialized
    assert local_path not in serialized
    assert '"payload"' not in serialized
    assert '"summary"' not in serialized
    assert "source_ref" in serialized
    assert "metadata_digest" in serialized

    db_path = tmp_path / "projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-process-state")
    assert result.success, result
    store = QueryProjectionStore(db_path)
    store.upsert(
        projection.BIO_OPS_KIND,
        run_id,
        payload,
        source_cursor=cursor,
        source_ref="bio-ops-test",
    )
    with sqlite3.connect(db_path) as conn:
        raw = "\n".join(
            str(row[0])
            for table, column in (
                ("operator_query_projection_document", "payload_json"),
                ("operator_query_projection_journal", "source_payload_json"),
            )
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
        )
    assert secret not in raw
    assert local_path not in raw
    assert '"payload"' not in raw
    assert '"summary"' not in raw


def test_process_state_get_handlers_have_no_warm_miss_or_source_calls() -> None:
    from host_brain.app import obs_routes_agent_bio_ops as bio
    from host_brain.app import obs_routes_core as core
    from host_brain.app import obs_routes_life_body as life
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app import obs_routes_sysmode as sysmode

    handlers = (
        summary.get_insights_summary_snapshot,
        summary.get_digital_life_overview_snapshot,
        sysmode.get_digital_life_runtime_snapshot,
        core.get_resource_manager_status,
        life._get_life_body_snapshot,
        life.get_life_body,
        life.get_life_timeline,
        life.get_pathologies,
        bio.get_codex_bio_ops_observability,
        bio.list_codex_bio_ops_adoptions,
        bio.get_codex_bio_ops_adoption,
        bio.get_codex_bio_ops_owner_inbox,
        bio.get_codex_bio_ops_owner_import_candidates,
        bio.get_codex_bio_ops_owner_queues,
        bio.get_codex_bio_ops_owner_state,
        bio.get_codex_bio_ops_owner_runtime_hints,
    )
    forbidden = (
        "_build_",
        "_get_warmed",
        "_find_run_dir(",
        "_adoption_context(",
        ".scan(",
        ".scan_",
        "get_resource_manager(",
        "read_text(",
        "rglob(",
        "QueryProjectionStore(",
        "get_query_projection_store(",
        "_get_obs_route_cached_response(",
    )
    for handler in handlers:
        source = inspect.getsource(handler)
        for marker in forbidden:
            assert marker not in source, f"{handler.__name__} contains {marker}"


def test_legacy_warm_start_authorities_are_deleted() -> None:
    from host_brain.app import obs_routes_life_body as life
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app import obs_routes_sysmode as sysmode

    for module in (life, summary, sysmode):
        names = set(vars(module))
        assert not any(name.startswith("start_") and name.endswith("_warm_cache") for name in names)
        assert not any(name.endswith("_warm_thread") for name in names)
