from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_services_health_get_never_primes_or_probes(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_health as routes
    from host_brain.app.observability_runtime import health_device_projection as projection
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat
    from natsume_contracts.operator.v1 import ProjectionDegradedState

    async def _forbidden_prime() -> None:
        raise AssertionError("GET must not prime the health worker")

    def _forbidden_source(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("GET must not call the health source loader")

    document = SimpleNamespace(
        payload={
                "total": 0,
                "up": 0,
                "degraded": 0,
                "down": 0,
                "unknown": 0,
                "services": [],
                "timestamp": 1_800_000_000_000,
        },
        schema_version="Natsume.operator.query_projection.v1",
        projection_version=2,
        projection_cursor=9,
        source_cursor=8,
        source_digest="sha256:" + "0" * 64,
        freshness_ts_ms=1_800_000_000_000,
        degraded_state=ProjectionDegradedState.HEALTHY,
        degraded_detail=None,
    )
    store = SimpleNamespace(get=lambda kind, key: document)

    monkeypatch.setattr(heartbeat, "prime_core_service_cache_once", _forbidden_prime)
    monkeypatch.setattr(projection, "load_health_projection_sources", _forbidden_source)
    monkeypatch.setattr(dependencies, "get_query_projection_store", lambda: store)

    response = asyncio.run(routes.get_services_health(window_minutes=60, side=None))

    assert response.total == 0
    assert response.services == []
    assert response.projection_version == 2


def test_avatar_state_get_reads_projection_without_device_command(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_stats_system as routes
    from host_brain.channels import embodiment
    from host_brain.app.observability_runtime import health_device_projection as projection
    from natsume_contracts.operator.v1 import ProjectionDegradedState

    async def _forbidden_command(**_kwargs):  # noqa: ANN003
        raise AssertionError("GET must not submit an embodiment command")

    def _forbidden_source():
        raise AssertionError("GET must not call the device source loader")

    document = SimpleNamespace(
        payload={
            "status": "OK",
            "success": True,
            "reason_code": None,
            "detail": "projected",
            "latency_ms": 4,
            "event": {"device": "avatar"},
        },
        schema_version="Natsume.operator.query_projection.v1",
        projection_version=3,
        projection_cursor=18,
        source_cursor=17,
        source_digest="sha256:" + "0" * 64,
        freshness_ts_ms=1_800_000_000_000,
        degraded_state=ProjectionDegradedState.HEALTHY,
        degraded_detail=None,
    )
    store = SimpleNamespace(get=lambda kind, key: document if (kind, key) == ("device_status", "avatar") else None)
    monkeypatch.setattr(dependencies, "get_query_projection_store", lambda: store)
    monkeypatch.setattr(embodiment, "submit_avatar_command", _forbidden_command)
    monkeypatch.setattr(projection, "load_device_status_projection_sources", _forbidden_source)

    response = asyncio.run(routes.get_avatar_state())

    assert response["status"] == "OK"
    assert response["projection_version"] == 3
    assert response["cursor"] == 17


def test_llm_and_organ_gets_do_not_call_background_loaders(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import obs_routes_health as routes
    from host_brain.app.observability_runtime import health_device_projection as projection

    def _forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("GET must not call a projection source loader")

    monkeypatch.setattr(projection, "load_health_projection_sources", _forbidden)
    monkeypatch.setattr(projection, "load_organ_tribe_projection_source", _forbidden)
    monkeypatch.setattr(
        dependencies,
        "get_query_projection_store",
        lambda: SimpleNamespace(get=lambda _kind, _key: None),
    )

    nodes = routes.get_llm_nodes(window_minutes=60)
    organ = routes.get_organ_tribe_status()

    assert nodes.nodes == []
    assert nodes.projection_version == 0
    assert nodes.freshness_ts_ms == 0
    assert nodes.degraded_state == "REBUILDING"
    assert organ.agents == []
    assert organ.projection_version == 0
    assert organ.cursor == 0
    assert organ.degraded_state == "REBUILDING"
