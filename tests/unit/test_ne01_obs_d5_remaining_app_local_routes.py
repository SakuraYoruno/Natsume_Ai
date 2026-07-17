from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from host_brain.app.obs_fastapi_app import create_obs_app
from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.observability_runtime.domain_state_projection import (
    GOVERNANCE_SNAPSHOT_KEY,
    GOVERNANCE_SNAPSHOT_KIND,
    LIFE_EVOLUTION_KIND,
    SECURITY_IMMUNE_KEY,
    SECURITY_IMMUNE_KIND,
    SEMANTIC_RECALL_STATS_KEY,
    SEMANTIC_RECALL_STATS_KIND,
    SKILL_PACK_CATALOG_KEY,
    SKILL_PACK_CATALOG_KIND,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.composition.obs_application import (
    ObsApplicationPorts,
    build_default_obs_application_ports,
)
from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
)


_EXPECTED_APP_LOCAL_GETS = {
    "/obs/governance/snapshot",
    "/obs/life-evolution/snapshot",
    "/obs/security/assets",
    "/obs/security/incidents",
    "/obs/security/runtime",
    "/obs/semantic-recall/stats",
    "/obs/skill-catalog/stats",
}


def _seed_ports(path: Path, *, label: str, now_ms: int) -> ObsApplicationPorts:
    base = build_default_obs_application_ports(canonical_db_path=path)
    ports = replace(
        base,
        projection_jobs=(),
        projection_coverage="TEST_D5_REMAINING_APP_LOCAL_GETS",
    )
    store = ports.query_projection_store
    store.upsert(
        SECURITY_IMMUNE_KIND,
        SECURITY_IMMUNE_KEY,
        {
            "enabled": True,
            "mode": label,
            "assets": [{"asset_id": f"asset-{label}"}],
            "asset_count": 1,
            "incidents": [
                {"incident_id": f"incident-{label}", "risk": "high"}
            ],
            "incident_count": 1,
            "risk_counts": {"high": 1},
            "generated_ts_ms": now_ms,
        },
        source_cursor=1,
        freshness_ts_ms=now_ms,
    )
    store.upsert(
        SKILL_PACK_CATALOG_KIND,
        SKILL_PACK_CATALOG_KEY,
        {
            "schema_version": "Natsume.operator.skill_pack_catalog.v1",
            "catalog_available": True,
            "total_skills": 1,
            "loaded_skills": 1,
            "failed_skills": 0,
            "skills": [{"name": f"skill.{label}"}],
            "domain_pack_count": 0,
            "domain_packs": [],
            "timestamp": now_ms,
        },
        source_cursor=2,
        freshness_ts_ms=now_ms,
    )
    store.upsert(
        LIFE_EVOLUTION_KIND,
        label,
        {
            "schema_version": "Natsume.life_evolution.view.v1",
            "view_id": f"life-{label}",
            "person_id": label,
            "read_only": True,
            "write_api_available": False,
        },
        source_cursor=3,
        freshness_ts_ms=now_ms,
    )
    store.upsert(
        SEMANTIC_RECALL_STATS_KIND,
        SEMANTIC_RECALL_STATS_KEY,
        {
            "use_mock": False,
            "chroma_host": f"chroma-{label}",
            "chroma_port": 8000,
            "top_k": 7 if label == "one" else 9,
            "similarity_threshold": 0.75,
            "collection_name": f"memory-{label}",
            "mock_doc_count": 0,
            "status": f"OK-{label}",
            "timestamp": now_ms,
        },
        source_cursor=4,
        freshness_ts_ms=now_ms,
    )
    store.upsert(
        GOVERNANCE_SNAPSHOT_KIND,
        GOVERNANCE_SNAPSHOT_KEY,
        {
            "schema_version": "Natsume.governance.snapshot.v1",
            "read_only": True,
            "write_api": False,
            "marker": label,
        },
        source_cursor=5,
        freshness_ts_ms=now_ms,
    )
    return ports


def _dependency_calls(route: APIRoute) -> set[Any]:
    calls: set[Any] = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            calls.add(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def test_actual_get_routes_all_require_the_app_local_projection_store() -> None:
    app = create_obs_app()
    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }

    assert _EXPECTED_APP_LOCAL_GETS <= set(routes)
    for path in sorted(_EXPECTED_APP_LOCAL_GETS):
        assert require_obs_query_projection_store in _dependency_calls(routes[path]), path


def test_unmaterialized_semantic_and_governance_views_fail_truthfully(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from host_brain.app import (
        obs_routes_governance,
        obs_routes_stats_capability_agent,
    )

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "empty.sqlite3"
    )
    semantic = obs_routes_stats_capability_agent.get_semantic_recall_stats(
        ports.query_projection_store
    )
    assert semantic.status == "DEGRADED: REBUILDING"
    assert semantic.projection["available"] is False
    assert semantic.projection["projection_version"] == 0

    monkeypatch.setenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as unavailable:
            asyncio.run(
                obs_routes_governance._get_governance_snapshot(
                    ports.query_projection_store
                )
            )
        assert unavailable.value.status_code == 503
        assert unavailable.value.detail["reason_code"] == (
            "GOVERNANCE_READ_MODEL_UNAVAILABLE"
        )
        assert unavailable.value.detail["degraded_state"] == "REBUILDING"
    finally:
        ports.close_owned_resources()
        get_core_settings.cache_clear()


def test_seven_gets_keep_two_obs_apps_and_sqlite_databases_distinct(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "d5-remaining-app-local"
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.d5.remaining-app-local",
                    "scopes": ["obs:operator:read"],
                }
            ]
        ),
    )
    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "true")
    monkeypatch.setenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()

    from host_brain.app import obs_routes_governance
    from host_brain.composition import process_services

    def forbidden_global_source(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an Obs GET crossed into a process-global source")

    monkeypatch.setattr(
        process_services,
        "get_query_projection_store",
        forbidden_global_source,
    )
    monkeypatch.setattr(
        process_services,
        "get_semantic_recaller",
        forbidden_global_source,
    )
    monkeypatch.setattr(
        process_services,
        "get_capability_scorecard_provider",
        forbidden_global_source,
    )

    now_ms = int(time.time() * 1000)
    ports_one = _seed_ports(tmp_path / "one.sqlite3", label="one", now_ms=now_ms)
    ports_two = _seed_ports(tmp_path / "two.sqlite3", label="two", now_ms=now_ms)
    assert ports_one.query_projection_store.db_path != ports_two.query_projection_store.db_path
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with TestClient(create_obs_app(ports=ports_one), headers=headers) as one, TestClient(
            create_obs_app(ports=ports_two),
            headers=headers,
        ) as two:
            for path in sorted(_EXPECTED_APP_LOCAL_GETS):
                params_one = {"person_id": "one"} if "life-evolution" in path else None
                params_two = {"person_id": "two"} if "life-evolution" in path else None
                response_one = one.get(path, params=params_one)
                response_two = two.get(path, params=params_two)
                assert response_one.status_code == 200, (path, response_one.text)
                assert response_two.status_code == 200, (path, response_two.text)

            assert one.get("/obs/security/runtime").json()["mode"] == "one"
            assert two.get("/obs/security/runtime").json()["mode"] == "two"
            assert one.get("/obs/security/assets").json()["assets"][0]["asset_id"] == "asset-one"
            assert two.get("/obs/security/assets").json()["assets"][0]["asset_id"] == "asset-two"
            assert one.get("/obs/security/incidents").json()["incidents"][0]["incident_id"] == "incident-one"
            assert two.get("/obs/security/incidents").json()["incidents"][0]["incident_id"] == "incident-two"
            assert one.get("/obs/skill-catalog/stats").json()["skills"][0]["name"] == "skill.one"
            assert two.get("/obs/skill-catalog/stats").json()["skills"][0]["name"] == "skill.two"
            assert one.get("/obs/life-evolution/snapshot", params={"person_id": "one"}).json()["view_id"] == "life-one"
            assert two.get("/obs/life-evolution/snapshot", params={"person_id": "two"}).json()["view_id"] == "life-two"
            assert one.get("/obs/semantic-recall/stats").json()["collection_name"] == "memory-one"
            assert two.get("/obs/semantic-recall/stats").json()["collection_name"] == "memory-two"
            assert one.get("/obs/governance/snapshot").json()["marker"] == "one"
            assert two.get("/obs/governance/snapshot").json()["marker"] == "two"
    finally:
        get_core_settings.cache_clear()
