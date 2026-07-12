"""Deterministic inventory of repository-managed local endpoints."""

from __future__ import annotations

import ipaddress
from typing import Mapping

from .models import (
    AuthKind,
    EndpointKind,
    EndpointSpec,
    Exposure,
    HealthSpec,
    ProtocolSpec,
    Transport,
    build_catalog_snapshot,
    CatalogSnapshot,
)


def _text(env: Mapping[str, str], key: str, default: str) -> str:
    value = str(env.get(key, default) or "").strip()
    return value or default


def _port(env: Mapping[str, str], key: str, default: int) -> int:
    raw = str(env.get(key, default) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer port") from exc
    if not (1 <= value <= 65535):
        raise ValueError(f"{key} must be in 1..65535")
    return value


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    if key not in env:
        return default
    raw = str(env.get(key, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled", ""}:
        return False
    raise ValueError(f"{key} must be a boolean")


def _exposure(host: str) -> Exposure:
    raw = host.strip().strip("[]").lower()
    if raw in {"localhost", "127.0.0.1", "::1"}:
        return Exposure.LOOPBACK
    if raw in {"*", "0.0.0.0", "::"}:
        return Exposure.PUBLIC
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return Exposure.PUBLIC
    return Exposure.PRIVATE if address.is_private else Exposure.PUBLIC


def _endpoint(
    *,
    endpoint_id: str,
    listener_id: str,
    owner_id: str,
    kind: EndpointKind,
    transport: Transport,
    host: str,
    port: int,
    protocols: tuple[tuple[str, str], ...],
    health_kind: str,
    health_path: str,
    auth_kind: AuthKind,
    auth_policy_ref: str,
    enabled: bool,
    source_refs: tuple[str, ...],
) -> EndpointSpec:
    protocol_specs = tuple(ProtocolSpec(name, version) for name, version in protocols)
    primary = protocol_specs[0]
    return EndpointSpec(
        endpoint_id=endpoint_id,
        listener_id=listener_id,
        owner_id=owner_id,
        kind=kind,
        transport=transport,
        bind_host=host,
        port=port,
        protocols=protocol_specs,
        health=HealthSpec(
            kind=health_kind,
            path=health_path,
            expected_owner_id=owner_id,
            expected_protocol=primary.name,
            expected_protocol_version=primary.version,
        ),
        exposure=_exposure(host),
        auth_kind=auth_kind,
        auth_policy_ref=auth_policy_ref,
        enabled=enabled,
        source_refs=source_refs,
    )


def build_local_catalog(env: Mapping[str, str] | None = None) -> CatalogSnapshot:
    """Build endpoint declarations without importing Host/Edge implementations."""

    source = dict(env or {})
    avatar_backend = _text(source, "EDGE_AVATAR_BACKEND", "vts").lower()
    if avatar_backend not in {"vts", "vrm"}:
        raise ValueError("EDGE_AVATAR_BACKEND must be vts or vrm")
    mate_enabled = _bool(source, "HOST_MATEENGINE_BRIDGE_ENABLED", False)
    edge_insecure = _bool(source, "EDGE_ALLOW_INSECURE_AUTH", True)
    gateway_enabled = _bool(source, "EDGE_GATEWAY_ENABLED", True)
    infra_enabled = _bool(source, "HOST_EXTERNAL_INFRA_MANAGED", False)
    obs_enabled = _bool(source, "HOST_OBS_APP_ENABLED", True)
    monitor_proxy_enabled = _bool(source, "HOST_MONITOR_PROXY_ENABLED", True)

    host_host = _text(source, "HOST_APP_HOST", "127.0.0.1")
    obs_host = _text(source, "HOST_OBS_APP_HOST", "127.0.0.1")
    monitor_proxy_host = _text(source, "HOST_MONITOR_PROXY_HOST", "127.0.0.1")
    edge_rpc_host = _text(source, "EDGE_RPC_HOST", "127.0.0.1")
    # The current Gateway bootstrap hard-binds 0.0.0.0.  Do not advertise an
    # environment override that the real listener does not consume.
    gateway_host = "0.0.0.0"
    mate_http_host = _text(
        source,
        "HOST_MATEENGINE_BRIDGE_HTTP_HOST",
        _text(source, "MATEENGINE_BRIDGE_HTTP_HOST", "127.0.0.1"),
    )
    mate_udp_host = _text(source, "MATEENGINE_BRIDGE_UDP_HOST", "127.0.0.1")
    vrm_host = _text(source, "EDGE_VRM_BRIDGE_HOST", "127.0.0.1")
    requested_embodiment_owner = _text(
        source,
        "NATSUME_EMBODIMENT_BRIDGE_OWNER",
        "",
    ).lower()
    if requested_embodiment_owner and requested_embodiment_owner not in {
        "mateengine",
        "vrm",
    }:
        raise ValueError("NATSUME_EMBODIMENT_BRIDGE_OWNER must be mateengine or vrm")
    if mate_enabled and avatar_backend == "vrm" and not requested_embodiment_owner:
        raise ValueError("SERVICE_CATALOG_EMBODIMENT_OWNER_REQUIRED")
    embodiment_owner = requested_embodiment_owner or (
        "mateengine" if mate_enabled else ("vrm" if avatar_backend == "vrm" else "")
    )
    if embodiment_owner == "mateengine" and not mate_enabled:
        raise ValueError("SERVICE_CATALOG_MATEENGINE_OWNER_DISABLED")
    if embodiment_owner == "vrm" and avatar_backend != "vrm":
        raise ValueError("SERVICE_CATALOG_VRM_OWNER_DISABLED")
    mate_active = mate_enabled and embodiment_owner == "mateengine"
    vrm_active = avatar_backend == "vrm" and embodiment_owner == "vrm"

    endpoints = [
        _endpoint(
            endpoint_id="host.main.http", listener_id="host.main.http",
            owner_id="host.main", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host=host_host,
            port=_port(source, "HOST_APP_PORT", 18080),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/health", auth_kind=AuthKind.ROUTE_POLICY,
            auth_policy_ref="host.route-policy.v1", enabled=True,
            source_refs=("host_brain/app/settings/core.py", "host_brain/__main__.py"),
        ),
        _endpoint(
            endpoint_id="host.obs.http", listener_id="host.obs.http",
            owner_id="host.obs", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host=obs_host,
            port=_port(source, "HOST_OBS_APP_PORT", 18082),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/health", auth_kind=AuthKind.ROUTE_POLICY,
            auth_policy_ref="host.obs.query-policy.v1", enabled=obs_enabled,
            source_refs=("host_brain/obs_main.py", "host_brain/app/obs_fastapi_app.py"),
        ),
        _endpoint(
            endpoint_id="host.monitor.proxy.http",
            listener_id="host.monitor.proxy.http",
            owner_id="host.monitor.proxy",
            kind=EndpointKind.LISTENER,
            transport=Transport.TCP,
            host=monitor_proxy_host,
            port=_port(source, "HOST_MONITOR_PROXY_PORT", 18180),
            protocols=(("http", "1.1"),),
            health_kind="http",
            health_path="/monitor/",
            auth_kind=AuthKind.NONE,
            auth_policy_ref="",
            enabled=monitor_proxy_enabled,
            source_refs=(
                "scripts/runtime/start_monitor_proxy.ps1",
                "scripts/runtime/start_host_side.ps1",
            ),
        ),
        _endpoint(
            endpoint_id="edge.rpc.http", listener_id="edge.rpc.http",
            owner_id="edge.rpc", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host=edge_rpc_host,
            port=_port(source, "EDGE_RPC_PORT", 17800),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/v1/edge/health",
            auth_kind=(AuthKind.NONE if edge_insecure else AuthKind.HMAC),
            auth_policy_ref=("" if edge_insecure else "edge.rpc.hmac-nonce.v1"),
            # EDGE_RPC_GATEWAY_ENABLED disables RPC capabilities, not uvicorn;
            # python -m edge_client still owns this listener unconditionally.
            enabled=True,
            source_refs=("edge_client/config.py", "edge_client/__main__.py"),
        ),
        _endpoint(
            endpoint_id="edge.gateway.http", listener_id="edge.gateway.http",
            owner_id="edge.gateway", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host=gateway_host,
            port=_port(source, "EDGE_GATEWAY_PORT", 19000),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/v1/gateway/health", auth_kind=AuthKind.ROUTE_POLICY,
            auth_policy_ref="edge.gateway.platform-signature-and-host-hmac.v1",
            enabled=gateway_enabled,
            source_refs=("edge_client/gateway/bootstrap.py", "edge_client/gateway/server.py"),
        ),
        _endpoint(
            endpoint_id="desktop.pet.http", listener_id="desktop.pet.http",
            owner_id="desktop.pet", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host="127.0.0.1",
            port=_port(source, "NATSUME_DESKTOP_PET_PORT", 18788),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/health", auth_kind=AuthKind.NONE,
            auth_policy_ref="", enabled=_bool(source, "NATSUME_DESKTOP_PET_ENABLED", False),
            source_refs=("desktop_pet/main.cjs",),
        ),
        _endpoint(
            endpoint_id="host.mateengine.bridge.http",
            listener_id="embodiment.bridge.http", owner_id="host.mateengine.bridge",
            kind=EndpointKind.LISTENER, transport=Transport.TCP,
            host=mate_http_host,
            port=_port(source, "HOST_MATEENGINE_BRIDGE_HTTP_PORT", _port(source, "MATEENGINE_BRIDGE_HTTP_PORT", 18770)),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/health", auth_kind=AuthKind.HMAC,
            auth_policy_ref="mateengine.bridge.hmac-v1", enabled=mate_active,
            source_refs=("tools/mateengine_bridge_server.py", "scripts/runtime/start_host_side.ps1"),
        ),
        _endpoint(
            endpoint_id="host.mateengine.bridge.udp",
            listener_id="host.mateengine.bridge.udp", owner_id="host.mateengine.bridge",
            kind=EndpointKind.LISTENER, transport=Transport.UDP,
            host=mate_udp_host,
            port=_port(source, "MATEENGINE_BRIDGE_UDP_PORT", 18771),
            protocols=(("mateengine-udp", "0.1"),), health_kind="indirect",
            health_path="http://127.0.0.1:18770/health#bridge_udp",
            auth_kind=AuthKind.HMAC,
            auth_policy_ref="mateengine.bridge.hmac-v1", enabled=mate_active,
            source_refs=("tools/mateengine_bridge_server.py",),
        ),
        _endpoint(
            endpoint_id="edge.unity.avatar.bridge.http",
            listener_id="embodiment.bridge.http", owner_id="edge.unity.avatar.bridge",
            kind=EndpointKind.LISTENER, transport=Transport.TCP,
            host=vrm_host, port=_port(source, "EDGE_VRM_BRIDGE_PORT", 18770),
            protocols=(("http", "0.1"),), health_kind="http",
            health_path="/health", auth_kind=AuthKind.NONE,
            auth_policy_ref="", enabled=vrm_active,
            source_refs=("tools/unity_avatar_runtime/Assets/NatsumeAvatarRuntime/Scripts/NatsumeAvatarBridgeServer.cs",),
        ),
        _endpoint(
            endpoint_id="tools.vrm.preview.http", listener_id="tools.vrm.preview.http",
            owner_id="tools.vrm.preview", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host=_text(source, "VRM_BRIDGE_HOST", "127.0.0.1"),
            port=_port(source, "VRM_BRIDGE_PORT", 18760),
            protocols=(("http", "0.1"), ("websocket", "0.1")),
            health_kind="http", health_path="/health", auth_kind=AuthKind.NONE,
            auth_policy_ref="", enabled=_bool(source, "HOST_VRM_PREVIEW_ENABLED", False),
            source_refs=("tools/vrm_bridge/server.mjs",),
        ),
        _endpoint(
            endpoint_id="infra.chroma.http", listener_id="infra.chroma.http",
            owner_id="infra.chroma", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host="0.0.0.0",
            port=18000,
            protocols=(("http", "1"),), health_kind="http", health_path="/api/v1/heartbeat",
            auth_kind=AuthKind.NONE, auth_policy_ref="", enabled=infra_enabled,
            source_refs=("tools/start_chroma_api.py", "scripts/runtime/docker-compose.external.yml"),
        ),
        _endpoint(
            endpoint_id="infra.neo4j.http", listener_id="infra.neo4j.http",
            owner_id="infra.neo4j", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host="0.0.0.0",
            port=_port(source, "NATSUME_NEO4J_HTTP_PORT", 7474),
            protocols=(("http", "1"),), health_kind="http", health_path="/",
            auth_kind=AuthKind.TOKEN, auth_policy_ref="neo4j.credentials.v1", enabled=infra_enabled,
            source_refs=("scripts/runtime/docker-compose.external.yml",),
        ),
        _endpoint(
            endpoint_id="infra.neo4j.bolt", listener_id="infra.neo4j.bolt",
            owner_id="infra.neo4j", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host="0.0.0.0",
            port=17687,
            protocols=(("bolt", "5"),), health_kind="indirect", health_path="http://127.0.0.1:7474/",
            auth_kind=AuthKind.TOKEN, auth_policy_ref="neo4j.credentials.v1", enabled=infra_enabled,
            source_refs=("scripts/runtime/docker-compose.external.yml",),
        ),
        _endpoint(
            endpoint_id="infra.redis.tcp", listener_id="infra.redis.tcp",
            owner_id="infra.redis", kind=EndpointKind.LISTENER,
            transport=Transport.TCP, host="0.0.0.0",
            port=_port(source, "NATSUME_REDIS_PORT", 6379),
            protocols=(("redis", "7"),), health_kind="redis", health_path="PING",
            auth_kind=AuthKind.NONE, auth_policy_ref="", enabled=infra_enabled,
            source_refs=("scripts/runtime/docker-compose.external.yml",),
        ),
        _endpoint(
            endpoint_id="edge.avatar.vrm.dependency", listener_id="embodiment.bridge.http",
            owner_id="edge.unity.avatar.bridge", kind=EndpointKind.DEPENDENCY,
            transport=Transport.TCP, host=vrm_host,
            port=_port(source, "EDGE_VRM_BRIDGE_PORT", 18770),
            protocols=(("http", "0.1"),), health_kind="http", health_path="/health",
            auth_kind=AuthKind.NONE, auth_policy_ref="", enabled=vrm_active,
            source_refs=("edge_client/actuators/vrm_bridge_driver.py",),
        ),
        _endpoint(
            endpoint_id="edge.avatar.vts.dependency", listener_id="vts.websocket",
            owner_id="external.vts", kind=EndpointKind.DEPENDENCY,
            transport=Transport.TCP, host=_text(source, "EDGE_VTS_HOST", "localhost"),
            port=_port(source, "EDGE_VTS_PORT", 8001),
            protocols=(("websocket", "1"),), health_kind="websocket", health_path="/",
            auth_kind=AuthKind.TOKEN, auth_policy_ref="vts.plugin-token.v1", enabled=avatar_backend == "vts",
            source_refs=("edge_client/actuators/vts_driver.py",),
        ),
        _endpoint(
            endpoint_id="host.ollama.dependency", listener_id="ollama.http",
            owner_id="external.ollama", kind=EndpointKind.DEPENDENCY,
            transport=Transport.TCP, host=_text(source, "OLLAMA_HOST", "127.0.0.1"),
            port=_port(source, "OLLAMA_PORT", 11434),
            protocols=(("http", "1"),), health_kind="http", health_path="/api/tags",
            auth_kind=AuthKind.NONE, auth_policy_ref="", enabled=_bool(source, "HOST_OLLAMA_ENABLED", False),
            source_refs=("host_brain/observability/service_registry.py",),
        ),
    ]
    return build_catalog_snapshot(endpoints)


__all__ = ["build_local_catalog"]
