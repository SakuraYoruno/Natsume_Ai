from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.service_catalog import (
    AuthKind,
    CatalogMode,
    CatalogSnapshot,
    EndpointKind,
    EndpointSpec,
    SERVICE_CATALOG_SCHEMA_VERSION,
    build_local_catalog,
    load_catalog_mode,
)


def test_catalog_mode_defaults_to_audit_and_invalid_value_is_explicit() -> None:
    default = load_catalog_mode({})
    assert default.valid and default.mode is CatalogMode.AUDIT

    preflight = load_catalog_mode({"HOST_SERVICE_CATALOG_MODE": "preflight"})
    assert preflight.valid and preflight.mode is CatalogMode.PREFLIGHT

    invalid = load_catalog_mode({"HOST_SERVICE_CATALOG_MODE": "best-effort"})
    assert not invalid.valid
    assert invalid.mode is CatalogMode.AUDIT
    assert invalid.reason_code == "SERVICE_CATALOG_MODE_INVALID"


def test_default_inventory_is_deterministic_complete_and_content_addressed() -> None:
    first = build_local_catalog(
        {"EDGE_GATEWAY_PORT": "19000", "HOST_APP_PORT": "18080"}
    )
    second = build_local_catalog(
        {"HOST_APP_PORT": "18080", "EDGE_GATEWAY_PORT": "19000"}
    )
    assert first == second
    assert first.digest == second.digest
    assert first.digest.startswith("sha256:") and len(first.digest) == 71
    assert tuple(item.endpoint_id for item in first.endpoints) == tuple(
        sorted(item.endpoint_id for item in first.endpoints)
    )

    by_id = first.by_id()
    required = {
        "host.main.http",
        "host.obs.http",
        "host.monitor.proxy.http",
        "edge.rpc.http",
        "edge.gateway.http",
        "desktop.pet.http",
        "host.mateengine.bridge.http",
        "host.mateengine.bridge.udp",
        "edge.unity.avatar.bridge.http",
        "tools.vrm.preview.http",
        "infra.chroma.http",
        "infra.neo4j.http",
        "infra.neo4j.bolt",
        "infra.redis.tcp",
    }
    assert required.issubset(by_id)
    for endpoint in by_id.values():
        assert endpoint.owner_id
        assert endpoint.protocols
        assert endpoint.health.expected_owner_id == endpoint.owner_id
        assert endpoint.source_refs

    disabled = build_local_catalog(
        {
            "HOST_OBS_APP_ENABLED": "false",
            "HOST_MONITOR_PROXY_ENABLED": "false",
        }
    ).by_id()
    assert disabled["host.obs.http"].enabled is False
    assert disabled["host.monitor.proxy.http"].enabled is False


def test_vrm_profile_resolves_18770_to_one_explicit_owner() -> None:
    vts = build_local_catalog({"EDGE_AVATAR_BACKEND": "vts"})
    vts_claims = [
        endpoint
        for endpoint in vts.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    ]
    assert vts_claims == []

    vrm = build_local_catalog({"EDGE_AVATAR_BACKEND": "vrm"})
    vrm_claims = [
        endpoint
        for endpoint in vrm.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    ]
    assert {item.owner_id for item in vrm_claims} == {"edge.unity.avatar.bridge"}
    assert {item.listener_id for item in vrm_claims} == {"embodiment.bridge.http"}

    dependency = vrm.by_id()["edge.avatar.vrm.dependency"]
    assert dependency.kind is EndpointKind.DEPENDENCY
    assert dependency.enabled


def test_public_listener_auth_state_reflects_actual_runtime_configuration() -> None:
    default = build_local_catalog({})
    gateway = default.by_id()["edge.gateway.http"]
    assert gateway.bind_host == "0.0.0.0"
    assert gateway.auth_kind is AuthKind.ROUTE_POLICY
    assert gateway.auth_policy_ref

    public_insecure_rpc = build_local_catalog(
        {"EDGE_RPC_HOST": "0.0.0.0", "EDGE_ALLOW_INSECURE_AUTH": "true"}
    ).by_id()["edge.rpc.http"]
    assert public_insecure_rpc.auth_kind is AuthKind.NONE
    assert public_insecure_rpc.auth_policy_ref == ""

    # Mate ingress is disabled and loopback/HMAC by default even before an
    # operator elects it as the one embodiment owner.
    mate_udp = default.by_id()["host.mateengine.bridge.udp"]
    assert mate_udp.bind_host == "127.0.0.1"
    assert mate_udp.auth_kind is AuthKind.HMAC
    assert mate_udp.enabled is False


def test_inventory_uses_only_runtime_wired_overrides() -> None:
    snapshot = build_local_catalog(
        {
            "EDGE_RPC_GATEWAY_ENABLED": "false",
            "EDGE_GATEWAY_HOST": "127.0.0.1",
            "NATSUME_NEO4J_HTTP_PORT": "17474",
            "NATSUME_REDIS_PORT": "16379",
            "HOST_NEO4J_HTTP_PORT": "27474",
            "HOST_REDIS_PORT": "26379",
        }
    ).by_id()

    assert snapshot["edge.rpc.http"].enabled is True
    assert snapshot["edge.gateway.http"].bind_host == "0.0.0.0"
    assert snapshot["infra.neo4j.http"].port == 17474
    assert snapshot["infra.redis.tcp"].port == 16379


@pytest.mark.parametrize(
    "env",
    [
        {"HOST_APP_PORT": "0"},
        {"HOST_APP_PORT": "not-a-port"},
        {"EDGE_AVATAR_BACKEND": "auto"},
        {"EDGE_GATEWAY_ENABLED": "sometimes"},
    ],
)
def test_invalid_inventory_configuration_fails_closed(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_local_catalog(env)


def test_snapshot_rejects_duplicate_id_and_mismatched_digest() -> None:
    snapshot = build_local_catalog({})
    endpoint = snapshot.endpoints[0]
    with pytest.raises(ValueError, match="unique"):
        CatalogSnapshot(
            schema_version=SERVICE_CATALOG_SCHEMA_VERSION,
            endpoints=(endpoint, endpoint),
        )
    with pytest.raises(ValueError, match="digest"):
        CatalogSnapshot(
            schema_version=SERVICE_CATALOG_SCHEMA_VERSION,
            endpoints=snapshot.endpoints,
            digest="sha256:" + "0" * 64,
        )


def test_endpoint_is_immutable_and_health_owner_cannot_drift() -> None:
    endpoint = build_local_catalog({}).endpoints[0]
    with pytest.raises((AttributeError, TypeError)):
        endpoint.port = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="health owner"):
        EndpointSpec(
            endpoint_id="fixture.listener",
            listener_id="fixture.listener",
            owner_id="fixture.owner",
            kind=endpoint.kind,
            transport=endpoint.transport,
            bind_host="127.0.0.1",
            port=12345,
            protocols=endpoint.protocols,
            health=replace(endpoint.health, expected_owner_id="other.owner"),
            exposure=endpoint.exposure,
            auth_kind=endpoint.auth_kind,
            auth_policy_ref=endpoint.auth_policy_ref,
            source_refs=("fixture",),
        )
