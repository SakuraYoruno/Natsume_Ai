from __future__ import annotations

import asyncio
import inspect
import time
from types import SimpleNamespace

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.observability_runtime.domain_state_projection import (
    GOVERNANCE_SNAPSHOT_MAX_AGE_MS,
    LIFE_EVOLUTION_KIND,
    LIFE_MAX_AGE_MS,
    SECURITY_IMMUNE_KEY,
    SECURITY_IMMUNE_KIND,
    SECURITY_IMMUNE_MAX_AGE_MS,
    SEMANTIC_RECALL_STATS_MAX_AGE_MS,
    SKILL_PACK_CATALOG_KEY,
    SKILL_PACK_CATALOG_KIND,
    SKILL_PACK_CATALOG_MAX_AGE_MS,
    load_life_evolution_projection_source,
    load_skill_pack_catalog_projection_source,
)
from host_brain.app.observability_runtime import process_state_projection
from host_brain.observability.projection_store import QueryProjectionStore


def _store(tmp_path) -> QueryProjectionStore:  # noqa: ANN001
    db_path = tmp_path / "projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-domain-unit")
    assert result.success, result
    return QueryProjectionStore(db_path)


def test_life_get_reads_durable_projection_without_source_getter(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from host_brain.app import obs_routes_life_evolution as routes
    from host_brain.app.settings.core import get_core_settings
    from host_brain.digital_life import application_boundary as life_application_boundary

    store = _store(tmp_path)
    now = int(time.time() * 1000)
    store.upsert(
        LIFE_EVOLUTION_KIND,
        "default",
        {
            "schema_version": "Natsume.life_evolution.view.v1",
            "view_id": "projected-life",
            "person_id": "default",
            "generated_at_ms": now,
            "read_only": True,
            "write_api_available": False,
            "world": {"status": "CURRENT", "facts": []},
            "appraisals": {"records": []},
            "bond": {"status": "CURRENT"},
            "scene": {"status": "UNAVAILABLE"},
            "action_arbiter": {"status": "UNAVAILABLE"},
            "action_preview": {"status": "NOT_AVAILABLE"},
            "legend": {},
        },
        source_cursor=8,
        freshness_ts_ms=now,
    )
    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Life GET must not call a source getter")

    monkeypatch.setattr(life_application_boundary, "read_life_snapshot", forbidden)
    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    try:
        response = asyncio.run(
            routes._get_life_evolution_snapshot(store, "default", "")
        )
    finally:
        get_core_settings.cache_clear()

    assert response["view_id"] == "projected-life"
    assert response["projection"]["projection_version"] == 1
    assert response["projection"]["source_cursor"] == 8


def test_security_gets_never_initialize_agent(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from host_brain.app import obs_routes_security_immune as routes
    from host_brain.security_immune import agent

    store = _store(tmp_path)
    now = int(time.time() * 1000)
    store.upsert(
        SECURITY_IMMUNE_KIND,
        SECURITY_IMMUNE_KEY,
        {
            "enabled": True,
            "mode": "observe",
            "assets": [{"asset_id": "asset-1"}],
            "asset_count": 1,
            "incidents": [
                {"incident_id": "low-1", "risk": "low"},
                {"incident_id": "high-1", "risk": "high"},
            ],
            "incident_count": 2,
            "risk_counts": {"low": 1, "high": 1},
            "generated_ts_ms": now,
        },
        source_cursor=now,
        freshness_ts_ms=now,
    )
    monkeypatch.setattr(
        agent,
        "get_security_immune_agent",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("GET initialized Immune Agent")),
    )

    runtime = routes.get_security_immune_runtime(store)
    assets = routes.get_security_immune_assets(store)
    incidents = routes.get_security_immune_incidents(store, "high")

    assert runtime["asset_count"] == 1
    assert assets["assets"][0]["asset_id"] == "asset-1"
    assert incidents["incident_count"] == 1
    assert incidents["incidents"][0]["incident_id"] == "high-1"
    assert incidents["projection"]["degraded_state"] == "HEALTHY"


def test_skill_catalog_get_never_initializes_registry(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_platform as routes

    store = _store(tmp_path)
    now = int(time.time() * 1000)
    store.upsert(
        SKILL_PACK_CATALOG_KIND,
        SKILL_PACK_CATALOG_KEY,
        {
            "schema_version": "Natsume.operator.skill_pack_catalog.v1",
            "catalog_available": True,
            "total_skills": 1,
            "loaded_skills": 1,
            "failed_skills": 0,
            "skills": [{"name": "skill.one", "source": "builtin", "status": "loaded"}],
            "domain_pack_count": 1,
            "domain_packs": [{"pack_id": "research.browser", "version": "1.0.0"}],
            "timestamp": now,
        },
        source_cursor=now,
        freshness_ts_ms=now,
    )
    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Skill Catalog GET initialized the registry")

    monkeypatch.setattr(dependencies, "get_skill_registry", forbidden)

    response = routes.get_skill_catalog_stats(store)

    assert response.loaded_skills == 1
    assert response.skills[0].name == "skill.one"
    assert response.domain_packs[0]["pack_id"] == "research.browser"
    assert response.projection["projection_version"] == 1


def test_empty_domain_gets_have_version_cursor_freshness_and_degraded(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from host_brain.app import obs_routes_platform, obs_routes_security_immune

    store = _store(tmp_path)
    immune = obs_routes_security_immune.get_security_immune_runtime(store)
    skills = obs_routes_platform.get_skill_catalog_stats(store)

    for projection in (immune["projection"], skills.projection):
        assert projection["schema_version"] == "Natsume.operator.query_projection.v1"
        assert projection["projection_version"] == 0
        assert projection["projection_cursor"] == 0
        assert projection["source_cursor"] == 0
        assert projection["freshness_ts_ms"] == 0
        assert projection["degraded_state"] == "REBUILDING"


def test_stale_skill_catalog_is_not_served_as_current(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from host_brain.app import obs_routes_platform

    store = _store(tmp_path)
    store.upsert(
        SKILL_PACK_CATALOG_KIND,
        SKILL_PACK_CATALOG_KEY,
        {
            "schema_version": "Natsume.operator.skill_pack_catalog.v1",
            "catalog_available": True,
            "total_skills": 1,
            "loaded_skills": 1,
            "failed_skills": 0,
            "skills": [{"name": "stale.skill"}],
            "domain_pack_count": 0,
            "domain_packs": [],
            "timestamp": 1,
        },
        source_cursor=1,
        freshness_ts_ms=1,
    )
    response = obs_routes_platform.get_skill_catalog_stats(store)

    assert response.skills == []
    assert response.loaded_skills == 0
    assert response.projection["projection_version"] == 1
    assert response.projection["source_cursor"] == 1
    assert response.projection["freshness_ts_ms"] == 1
    assert response.projection["degraded_state"] == "STALE"


def test_background_loaders_project_canonical_life_and_skill_pack_catalog() -> None:
    life, life_cursor = load_life_evolution_projection_source(
        snapshot_reader=lambda person_id: {
            "person_id": person_id,
            "version": 4,
            "state_digest": "sha256:life",
            "updated_at_ms": 1234,
            "state": {
                "clock": {"day_phase": "day"},
                "scene": {"kind": "work", "since_ms": 1200},
                "appraisal": {"event_type": "tick", "appraised_at_ms": 1220},
                "relationship": {"stage": "FRIEND", "warmth": 0.7},
            },
        }
    )
    assert life_cursor == 1234
    assert life["world"]["status"] == "CURRENT"
    assert life["world"]["facts"][0]["source"] == "canonical_life_snapshot"

    registry = SimpleNamespace(
        all=lambda: [
            SimpleNamespace(
                skill_name="skill.one",
                clearance="LOW",
                risk_level=1,
            )
        ]
    )
    catalog, cursor = load_skill_pack_catalog_projection_source(
        registry_provider=lambda: registry,
        domain_pack_reader=lambda: [{"pack_id": "pack.one", "version": "1.0.0"}],
    )
    assert cursor > 0
    assert catalog["skills"][0]["name"] == "skill.one"
    assert catalog["domain_packs"][0]["pack_id"] == "pack.one"


def test_life_background_loader_requires_the_explicit_snapshot_reader(monkeypatch) -> None:  # noqa: ANN001
    from host_brain.composition import process_services

    calls: list[str] = []

    class Reader:
        def get_snapshot(self, person_id: str) -> dict[str, object]:
            calls.append(person_id)
            return {
                "person_id": person_id,
                "version": 3,
                "updated_at_ms": 900,
                "state_digest": "sha256:life",
                "state": {},
            }

    reader = Reader()
    monkeypatch.setattr(
        process_services,
        "get_life_snapshot_reader",
        lambda: (_ for _ in ()).throw(
            AssertionError("explicit loader must not discover a process reader")
        ),
    )

    payload, cursor = load_life_evolution_projection_source(
        "natsume",
        snapshot_reader=reader.get_snapshot,
    )

    assert calls == ["natsume"]
    assert payload["person_id"] == "natsume"
    assert cursor == 900


def test_bio_ops_refresh_floors_source_cursors_at_durable_documents(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    store.upsert(
        process_state_projection.BIO_OPS_KIND,
        "run-1",
        {"run_id": "run-1", "status": "old"},
        source_cursor=20,
    )
    store.upsert(
        process_state_projection.BIO_OPS_KIND,
        process_state_projection.BIO_OPS_LATEST_KEY,
        {"run_id": "run-1", "latest_run_id": "run-1", "status": "old"},
        source_cursor=30,
    )
    monkeypatch.setattr(process_state_projection, "_store", lambda: store)
    monkeypatch.setattr(
        process_state_projection,
        "load_bio_ops_projection_sources",
        lambda _root: [("run-1", {"run_id": "run-1", "status": "new"}, 10)],
    )

    result = process_state_projection.refresh_bio_ops_projection()
    replayed = process_state_projection.refresh_bio_ops_projection()

    assert result == {"document_count": 2}
    assert replayed == {"document_count": 2}
    assert store.get(process_state_projection.BIO_OPS_KIND, "run-1").source_cursor == 21
    assert (
        store.get(
            process_state_projection.BIO_OPS_KIND,
            process_state_projection.BIO_OPS_LATEST_KEY,
        ).source_cursor
        == 31
    )


def test_life_body_has_no_legacy_trace_feed_warm_read_model() -> None:
    from host_brain.app import obs_routes_life_body

    source = inspect.getsource(obs_routes_life_body)
    assert "_get_warmed_trace_feed_snapshot" not in source
    assert "get_trace_feed_warm_status" not in source
    assert "projection_store.get(TRACE_FEED_KIND, TRACE_FEED_INDEX_KEY)" in source


def test_domain_routes_reference_only_durable_readers() -> None:
    from host_brain.app import (
        obs_routes_life_evolution,
        obs_routes_platform,
        obs_routes_security_immune,
    )

    life_source = inspect.getsource(obs_routes_life_evolution._get_life_evolution_snapshot)
    immune_source = inspect.getsource(obs_routes_security_immune._runtime_state)
    skill_source = inspect.getsource(obs_routes_platform.get_skill_catalog_stats)
    assert "_default_snapshot" not in life_source
    assert "get_security_immune_agent" not in immune_source
    assert "get_skill_registry" not in skill_source
    assert "list_installed" not in skill_source
    assert "isdir" not in skill_source
    assert "read_domain_projection_from_store" in life_source + immune_source + skill_source


def test_projection_age_contracts_are_nonzero() -> None:
    assert GOVERNANCE_SNAPSHOT_MAX_AGE_MS > 30_000
    assert LIFE_MAX_AGE_MS > 0
    assert SECURITY_IMMUNE_MAX_AGE_MS > LIFE_MAX_AGE_MS
    assert SEMANTIC_RECALL_STATS_MAX_AGE_MS > 60_000
    assert SKILL_PACK_CATALOG_MAX_AGE_MS > LIFE_MAX_AGE_MS
