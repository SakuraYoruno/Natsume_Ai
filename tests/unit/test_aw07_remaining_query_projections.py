from __future__ import annotations

import inspect
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


def _store(tmp_path) -> QueryProjectionStore:
    path = tmp_path / "projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="aw07-remaining-unit"
    )
    assert result.success, result
    return QueryProjectionStore(path)


class _SourceLedger:
    canonical_db_path = "unused.sqlite3"

    @staticmethod
    def resolve_fetch_table(table):
        return table

    @staticmethod
    def fetch_by_time_range(table, **_kwargs):
        now = int(time.time() * 1000)
        if table == "agent_loop_log":
            return [
                {
                    "request_id": "req-loop",
                    "session_id": "session-public",
                    "phase": "LOOP_END",
                    "goal": "PRIVATE_GOAL_BODY",
                    "goal_achieved": True,
                    "round": 2,
                    "action_count": 3,
                    "success_count": 3,
                    "latency_ms": 8,
                    "ts_ms": now,
                }
            ]
        if table == "audit_log":
            return [
                {
                    "request_id": "req-loop",
                    "stage": "LOOP_DONE",
                    "detail": json.dumps(
                        {
                            "goal": "PRIVATE_HISTORY_GOAL",
                            "final_status": "DONE",
                            "goal_achieved": True,
                        }
                    ),
                    "ts_ms": now,
                }
            ]
        if table == "checkpoints":
            return [
                {
                    "checkpoint_id": "cp-1",
                    "request_id": "req-loop",
                    "interrupt_reason": "REVIEW_REQUIRED",
                    "payload": {"private_body": "PRIVATE_CHECKPOINT_BODY"},
                    "created_at_ms": now,
                }
            ]
        return []


def test_background_job_materializes_metadata_without_private_bodies(
    tmp_path, monkeypatch
) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.memory import canon_world_store
    from host_brain.app.observability_runtime import remaining_query_projection as projection

    store = _store(tmp_path)
    source_db = tmp_path / "source.sqlite3"
    source_migration = SQLiteMigrationRunner(db_path=source_db).apply_migrations(
        request_id="aw07-remaining-source",
    )
    assert source_migration.success, source_migration.as_dict()
    source_ledger = _SourceLedger()
    source_ledger.canonical_db_path = str(source_db)
    profile = tmp_path / "character_profile.json"
    profile.write_text(
        json.dumps(
            {
                "doc_id": "character-profile",
                "version": 7,
                "instruction_cn": "PRIVATE_PERSONA_BODY",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NATSUME_CHARACTER_PROFILE_PATH", str(profile))
    monkeypatch.setenv("Natsume_WORLD_CANON_ROOT", str(tmp_path / "empty-world"))

    class WorldDocument:
        doc_id = "summer-city"
        title = "Summer City"
        source_path = r"C:\private\canon\summer-city.md"
        version = 3
        status = "ACTIVE"
        updated_by = "fixture"
        ts_ms = int(time.time() * 1000)
        body = "PRIVATE_WORLD_BODY"

    class RelativeWorldDocument:
        doc_id = "relative-city"
        title = "Relative City"
        source_path = "canon/world/relative-secret.md"
        version = 1
        status = "ACTIVE"
        updated_by = "fixture"
        ts_ms = int(time.time() * 1000)
        body = "PRIVATE_RELATIVE_WORLD_BODY"

    class WorldStore:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def list_documents():
            return [WorldDocument(), RelativeWorldDocument()]

    monkeypatch.setattr(canon_world_store, "WorldCanonStore", WorldStore)
    monkeypatch.setattr(dependencies, "get_ledger", lambda: source_ledger)
    monkeypatch.setattr(dependencies, "get_query_projection_store", lambda: store)

    class CanonService:
        @staticmethod
        def preview_sync():
            return type(
                "Preview",
                (),
                {"issues": (), "records": (), "removed_doc_ids": ()},
            )()

    result = projection.refresh_remaining_query_projections(
        canon_service=CanonService(),
    )

    assert result["agent_loop_count"] == 1
    assert result["checkpoint_count"] == 1
    checkpoint = store.get(projection.CHECKPOINT_KIND, "cp-1")
    assert checkpoint is not None
    assert "payload" not in checkpoint.payload
    agent = store.get(projection.AGENT_LOOP_KIND, projection.AGENT_LOOP_INDEX_KEY)
    assert agent is not None
    assert agent.payload["loops"][0]["goal"] is None
    raw = Path(store.db_path).read_bytes()
    assert b"PRIVATE_GOAL_BODY" not in raw
    assert b"PRIVATE_HISTORY_GOAL" not in raw
    assert b"PRIVATE_CHECKPOINT_BODY" not in raw
    assert b"PRIVATE_PERSONA_BODY" not in raw
    assert b"PRIVATE_WORLD_BODY" not in raw
    assert b"PRIVATE_RELATIVE_WORLD_BODY" not in raw
    assert b"C:\\private\\canon\\summer-city.md" not in raw
    assert b"canon/world/relative-secret.md" not in raw
    assert b"summer-city.md" not in raw
    assert b"canon://world/summer-city" in raw
    assert b"canon://world/relative-city" in raw


def test_remaining_gets_return_empty_projection_metadata_without_source_access(
    monkeypatch,
) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_stats_agent as agent_routes
    from host_brain.app import obs_routes_stats_capability_skill as skill_routes
    from host_brain.app import obs_routes_stats_system as system_routes
    from host_brain.app import web_ui

    class EmptyStore:
        @staticmethod
        def get(_kind, _key):
            return None

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET touched a source")

    monkeypatch.setattr(dependencies, "peek_query_projection_store", lambda: EmptyStore())
    monkeypatch.setattr(dependencies, "get_ledger", forbidden)
    monkeypatch.setattr(web_ui, "get_ledger", forbidden)

    stats = agent_routes.get_agent_loop_stats(window_minutes=60, limit=10)
    resident = agent_routes.get_memory_resident_context()
    history = skill_routes.get_agent_loop_history(window_minutes=60, limit=10)
    checkpoints = system_routes.get_checkpoints(limit=20, include_expired=False)
    now = datetime.now()
    timeline = web_ui.get_timeline_aggregation(
        start_time=now - timedelta(hours=1),
        end_time=now,
        interval="1h",
        status=None,
        request_type=None,
        limit=100,
        offset=0,
    )
    search = web_ui.search_requests(
        query="req",
        start_time=now - timedelta(hours=1),
        end_time=now,
        page=1,
        page_size=20,
        limit=100,
        offset=0,
    )

    for response in (stats, resident, history, checkpoints, timeline):
        assert response.projection_version == 0
        assert response.cursor == 0
        assert response.freshness_ts_ms == 0
        assert response.degraded_state == "REBUILDING"
    assert search["results"] == []
    assert search["degraded_state"] == "REBUILDING"


def test_stale_projection_returns_empty_payload_with_preserved_version_cursor(
    tmp_path, monkeypatch
) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_stats_agent as agent_routes
    from host_brain.app.observability_runtime import remaining_query_projection as projection

    store = _store(tmp_path)
    document = store.upsert(
        projection.AGENT_LOOP_KIND,
        projection.AGENT_LOOP_INDEX_KEY,
        {
            "loops": [
                {
                    "request_id": "req-stale",
                    "goal": None,
                    "total_rounds": 1,
                    "goal_achieved": True,
                    "total_actions": 1,
                    "success_actions": 1,
                    "failed_actions": 0,
                    "total_latency_ms": 1,
                    "ts_ms": 1,
                }
            ],
            "history": [],
        },
        source_cursor=77,
        freshness_ts_ms=1,
    )
    monkeypatch.setattr(dependencies, "peek_query_projection_store", lambda: store)

    response = agent_routes.get_agent_loop_stats(window_minutes=60, limit=10)

    assert response.recent_loops == []
    assert response.projection_version == document.projection_version
    assert response.cursor == 77
    assert response.freshness_ts_ms == 1
    assert response.degraded_state == "STALE"


def test_remaining_get_handlers_are_projection_only_by_ast_contract() -> None:
    from host_brain.app import obs_routes_stats_agent as agent_routes
    from host_brain.app import obs_routes_stats_capability_skill as skill_routes
    from host_brain.app import obs_routes_stats_system as system_routes
    from host_brain.app import web_ui

    handlers = (
        web_ui.get_timeline_aggregation,
        web_ui.get_request_detail,
        web_ui.search_requests,
        agent_routes.get_agent_loop_stats,
        agent_routes.get_memory_resident_context,
        skill_routes.get_agent_loop_history,
        system_routes.get_checkpoints,
        system_routes.get_checkpoint_detail,
    )
    forbidden = (
        "get_ledger(",
        "_get_ledger(",
        "fetch_by_time_range(",
        "QueryProjectionStore(",
        "WorldCanonStore(",
        "open(",
        "refresh_remaining_query_projections(",
    )
    for handler in handlers:
        source = inspect.getsource(handler)
        for marker in forbidden:
            assert marker not in source, f"{handler.__name__} contains {marker}"
