from __future__ import annotations

import json
from typing import Any

import pytest

from natsume_contracts.bridge_auth import build_http_auth_headers
from host_brain.service_catalog import (
    MATE_BRIDGE_HEALTH_ENDPOINT_INVALID,
    MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH,
    MATE_BRIDGE_HEALTH_OK,
    MATE_BRIDGE_HEALTH_REQUEST_FAILED,
    MATE_BRIDGE_HEALTH_RESPONSE_INVALID,
    MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH,
    MateBridgeHealthProbe,
    MateBridgeHealthProbeConfig,
    MateBridgeHealthProbeConfigurationError,
    build_local_catalog,
)


SECRET = b"aw01-mate-health-secret-material-0001"
KEY_ID = "mate-health-key-1"
PRINCIPAL = "host.service-catalog.preflight"
NOW_MS = 1_783_828_800_123
NONCE = "aw01-mate-health-nonce-1"


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.status = status
        self._payload = payload

    def read(self, _limit: int) -> bytes:
        return self._payload


class _Connection:
    def __init__(
        self,
        response: _Response,
        calls: list[tuple[Any, ...]],
    ) -> None:
        self._response = response
        self._calls = calls

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any,
        headers: dict[str, str],
    ) -> None:
        self._calls.append(("request", method, path, body, headers))

    def getresponse(self) -> _Response:
        return self._response

    def close(self) -> None:
        self._calls.append(("close",))


def _catalog_env(**overrides: str) -> dict[str, str]:
    env = {
        "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
        "NATSUME_EMBODIMENT_BRIDGE_OWNER": "mateengine",
        "MATEENGINE_BRIDGE_UDP_ENABLED": "true",
        "HOST_MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
        "HOST_MATEENGINE_BRIDGE_HTTP_PORT": "19770",
        "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        "MATEENGINE_BRIDGE_UDP_PORT": "19771",
    }
    env.update(overrides)
    return env


def _payload(**udp_overrides: Any) -> dict[str, Any]:
    udp = {
        "ready": True,
        "host": "127.0.0.1",
        "port": 19771,
        "protocol_id": "mateengine-udp",
        "protocol_version": "1",
    }
    udp.update(udp_overrides)
    return {
        "status": "ok",
        "owner_id": "host.mateengine.bridge",
        "protocol_id": "http",
        "protocol_version": "1",
        "udp_ingress": udp,
    }


def _probe(
    payload: bytes | dict[str, Any],
    *,
    calls: list[tuple[Any, ...]] | None = None,
    status: int = 200,
) -> MateBridgeHealthProbe:
    recorded = calls if calls is not None else []
    body = (
        payload
        if type(payload) is bytes
        else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )

    def connection_factory(
        host: str,
        port: int,
        timeout: float,
    ) -> _Connection:
        recorded.append(("connect", host, port, timeout))
        return _Connection(_Response(body, status=status), recorded)

    return MateBridgeHealthProbe(
        config=MateBridgeHealthProbeConfig(
            http_host="127.0.0.1",
            http_port=19770,
            key_id=KEY_ID,
            timeout_seconds=0.75,
        ),
        secret=SECRET,
        principal=PRINCIPAL,
        clock_ms=lambda: NOW_MS,
        nonce_factory=lambda: NONCE,
        connection_factory=connection_factory,
    )


def test_catalog_udp_requires_mate_owner_and_explicit_udp_enable() -> None:
    owner_without_udp = build_local_catalog(
        _catalog_env(MATEENGINE_BRIDGE_UDP_ENABLED="false")
    ).by_id()
    assert owner_without_udp["host.mateengine.bridge.http"].enabled
    assert not owner_without_udp["host.mateengine.bridge.udp"].enabled

    active = build_local_catalog(_catalog_env()).by_id()
    mate_http = active["host.mateengine.bridge.http"]
    mate_udp = active["host.mateengine.bridge.udp"]
    assert mate_http.enabled and mate_udp.enabled
    assert mate_http.protocols[0].version == "1"
    assert mate_udp.protocols[0].version == "1"
    assert mate_udp.health.path == (
        "http://127.0.0.1:19770/health#bridge_udp"
    )

    vrm_owner = build_local_catalog(
        _catalog_env(
            EDGE_AVATAR_BACKEND="vrm",
            NATSUME_EMBODIMENT_BRIDGE_OWNER="vrm",
        )
    ).by_id()
    assert not vrm_owner["host.mateengine.bridge.http"].enabled
    assert not vrm_owner["host.mateengine.bridge.udp"].enabled


def test_catalog_indirect_health_uses_configured_http_probe_target() -> None:
    endpoint = build_local_catalog(
        _catalog_env(
            HOST_MATEENGINE_BRIDGE_HTTP_HOST="192.0.2.44",
            HOST_MATEENGINE_BRIDGE_HTTP_PORT="20770",
        )
    ).by_id()["host.mateengine.bridge.udp"]

    assert endpoint.health.path == (
        "http://192.0.2.44:20770/health#bridge_udp"
    )


def test_http_probe_signs_exact_get_and_accepts_exact_identity() -> None:
    calls: list[tuple[Any, ...]] = []
    probe = _probe(_payload(), calls=calls)
    endpoint = build_local_catalog(_catalog_env()).by_id()[
        "host.mateengine.bridge.http"
    ]

    result = probe.inspect(endpoint)

    assert result.healthy
    assert result.detail == MATE_BRIDGE_HEALTH_OK
    assert result.owner_id == endpoint.owner_id
    assert result.protocol_id == "http"
    assert result.protocol_version == "1"
    assert calls[0] == ("connect", "127.0.0.1", 19770, 0.75)
    request = calls[1]
    assert request[:4] == ("request", "GET", "/health", None)
    expected_headers = build_http_auth_headers(
        secret=SECRET,
        key_id=KEY_ID,
        method="GET",
        path="/health",
        timestamp_ms=NOW_MS,
        nonce=NONCE,
        principal=PRINCIPAL,
        scope="bridge:mateengine:read",
        raw_body=b"",
    )
    expected_headers["Accept"] = "application/json"
    assert request[4] == expected_headers
    assert calls[-1] == ("close",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", "incumbent.spoof"),
        ("protocol_id", "mateengine-http"),
        ("protocol_version", "0.1"),
        ("status", "starting"),
    ],
)
def test_http_probe_rejects_any_identity_drift(
    field: str,
    value: str,
) -> None:
    payload = _payload()
    payload[field] = value
    endpoint = build_local_catalog(_catalog_env()).by_id()[
        "host.mateengine.bridge.http"
    ]

    result = _probe(payload).inspect(endpoint)

    assert not result.healthy
    assert result.detail == MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH


def test_udp_probe_requires_exact_ready_host_port_protocol_and_version() -> None:
    endpoint = build_local_catalog(_catalog_env()).by_id()[
        "host.mateengine.bridge.udp"
    ]

    result = _probe(_payload()).inspect(endpoint)

    assert result.healthy
    assert result.detail == MATE_BRIDGE_HEALTH_OK
    assert result.owner_id == endpoint.owner_id
    assert result.protocol_id == "mateengine-udp"
    assert result.protocol_version == "1"


@pytest.mark.parametrize(
    "udp_override",
    [
        {"ready": False},
        {"ready": 1},
        {"host": "0.0.0.0"},
        {"port": 19772},
        {"port": True},
        {"protocol_id": "udp"},
        {"protocol_version": "0.1"},
    ],
)
def test_udp_probe_rejects_non_proving_health_payload(
    udp_override: dict[str, Any],
) -> None:
    endpoint = build_local_catalog(_catalog_env()).by_id()[
        "host.mateengine.bridge.udp"
    ]

    result = _probe(_payload(**udp_override)).inspect(endpoint)

    assert not result.healthy
    assert result.detail == MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH


def test_probe_rejects_non_mate_endpoint_without_network_request() -> None:
    calls: list[tuple[Any, ...]] = []
    probe = _probe(_payload(), calls=calls)
    endpoint = build_local_catalog({}).by_id()["host.main.http"]

    result = probe.inspect(endpoint)

    assert not result.healthy
    assert result.detail == MATE_BRIDGE_HEALTH_ENDPOINT_INVALID
    assert calls == []


def test_probe_fails_closed_on_network_or_malformed_response() -> None:
    endpoint = build_local_catalog(_catalog_env()).by_id()[
        "host.mateengine.bridge.http"
    ]

    malformed = _probe(b'{"owner_id":"a","owner_id":"b"}').inspect(endpoint)
    assert not malformed.healthy
    assert malformed.detail == MATE_BRIDGE_HEALTH_RESPONSE_INVALID

    def failing_connection(_host: str, _port: int, _timeout: float) -> Any:
        raise OSError("ambient network detail")

    failed = MateBridgeHealthProbe(
        config=MateBridgeHealthProbeConfig(
            http_host="127.0.0.1",
            http_port=19770,
            key_id=KEY_ID,
        ),
        secret=SECRET,
        principal=PRINCIPAL,
        clock_ms=lambda: NOW_MS,
        nonce_factory=lambda: NONCE,
        connection_factory=failing_connection,
    ).inspect(endpoint)
    assert not failed.healthy
    assert failed.detail == MATE_BRIDGE_HEALTH_REQUEST_FAILED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"secret": b"short", "principal": PRINCIPAL},
        {"secret": SECRET, "principal": "bad\nprincipal"},
    ],
)
def test_probe_rejects_invalid_explicit_credentials(kwargs: dict[str, Any]) -> None:
    with pytest.raises(MateBridgeHealthProbeConfigurationError):
        MateBridgeHealthProbe(
            config=MateBridgeHealthProbeConfig(
                http_host="127.0.0.1",
                http_port=19770,
                key_id=KEY_ID,
            ),
            **kwargs,
        )
