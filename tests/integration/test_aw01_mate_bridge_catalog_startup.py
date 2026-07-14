from __future__ import annotations

import json
import http.client
from pathlib import Path
import socket

import pytest

from host_brain.service_catalog.defaults import build_local_catalog
from host_brain.service_catalog.mate_health_probe import (
    MATE_BRIDGE_HTTP_ENDPOINT_ID,
    MATE_BRIDGE_UDP_ENDPOINT_ID,
    MateBridgeHealthProbe,
    MateBridgeHealthProbeConfig,
)
from host_brain.service_catalog.socket_reservation import (
    OwnedSocketReservationProbe,
)
from tools import mateengine_bridge_server as bridge


_INGRESS_SECRET = "aw01-ingress-secret-material-0000000000000001"
_PLUGIN_SECRET = "aw01-plugin-secret-material-00000000000000002"
_PRINCIPAL = "host.mateengine.bridge"


def _free_port(socket_type: int) -> int:
    with socket.socket(socket.AF_INET, socket_type) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _exclusive_socket(socket_type: int) -> socket.socket:
    owned = socket.socket(socket.AF_INET, socket_type)
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if exclusive is not None:
        owned.setsockopt(socket.SOL_SOCKET, exclusive, 1)
    return owned


def _assert_bind_blocked(socket_type: int, port: int) -> None:
    with _exclusive_socket(socket_type) as contender:
        with pytest.raises(OSError):
            contender.bind(("127.0.0.1", port))


def _assert_bind_available(socket_type: int, port: int) -> None:
    with _exclusive_socket(socket_type) as contender:
        contender.bind(("127.0.0.1", port))


def _environment(tmp_path: Path, *, http_port: int, udp_port: int) -> dict[str, str]:
    return {
        "HOST_SERVICE_CATALOG_MODE": "ENFORCE",
        "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "5000",
        "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
        "NATSUME_EMBODIMENT_BRIDGE_OWNER": "mateengine",
        "EDGE_AVATAR_BACKEND": "vts",
        "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
        "HOST_MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
        "HOST_MATEENGINE_BRIDGE_HTTP_PORT": str(http_port),
        "MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
        "MATEENGINE_BRIDGE_HTTP_PORT": str(http_port),
        "MATEENGINE_BRIDGE_UDP_ENABLED": "true",
        "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        "MATEENGINE_BRIDGE_UDP_PORT": str(udp_port),
        "MATEENGINE_SIGNAL_HOST": "127.0.0.1",
        "MATEENGINE_SIGNAL_PORT": str(_free_port(socket.SOCK_DGRAM)),
        "MATEENGINE_PLUGIN_HOST": "127.0.0.1",
        "MATEENGINE_PLUGIN_PORT": str(_free_port(socket.SOCK_DGRAM)),
        "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": "aw01.mate.ingress",
        "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": _INGRESS_SECRET,
        "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": _PRINCIPAL,
        "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES": "bridge:mateengine:read",
        "NATSUME_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL": _PRINCIPAL,
        "NATSUME_MATEENGINE_BRIDGE_HEALTH_TIMEOUT_SECONDS": "2",
        "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": "aw01.mate.plugin",
        "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": _PLUGIN_SECRET,
        "NATSUME_MATEENGINE_PLUGIN_PRINCIPAL": _PRINCIPAL,
        "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": str(
            tmp_path / "mate-audit.jsonl"
        ),
        "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": str(
            tmp_path / "mate-replay.sqlite3"
        ),
    }


def _enabled_mate_endpoints(env: dict[str, str]):
    return {
        endpoint.endpoint_id: endpoint
        for endpoint in build_local_catalog(env).endpoints
        if endpoint.enabled and endpoint.owner_id == bridge.MATE_BRIDGE_OWNER_ID
    }


def test_catalog_enforce_hands_the_same_prebound_sockets_to_real_http_and_udp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_port = _free_port(socket.SOCK_STREAM)
    udp_port = _free_port(socket.SOCK_DGRAM)
    env = _environment(tmp_path, http_port=http_port, udp_port=udp_port)
    config = bridge.build_security_config(env)
    captured: dict[str, tuple[socket.socket, ...]] = {}
    captured_fingerprints: dict[str, tuple[tuple[int, object], ...]] = {}
    original_take = OwnedSocketReservationProbe.take_consumed_sockets

    def _capture_take(self, **kwargs):
        handoff = original_take(self, **kwargs)
        if handoff is not None:
            captured.update(handoff)
            captured_fingerprints.update(
                {
                    endpoint_id: tuple(
                        (owned.fileno(), owned.getsockname())
                        for owned in endpoint_sockets
                    )
                    for endpoint_id, endpoint_sockets in handoff.items()
                }
            )
        return handoff

    monkeypatch.setattr(
        OwnedSocketReservationProbe,
        "take_consumed_sockets",
        _capture_take,
    )
    prior_udp_inputs = int(bridge.state["udp_input_count"])
    prior_udp_outputs = int(bridge.state["udp_count"])

    handle = bridge.start_catalog_governed_bridge(config, environ=env)
    try:
        assert handle.http_server.socket is captured[MATE_BRIDGE_HTTP_ENDPOINT_ID][0]
        assert handle.udp_socket is captured[MATE_BRIDGE_UDP_ENDPOINT_ID][0]
        assert (
            handle.http_server.socket.fileno(),
            handle.http_server.socket.getsockname(),
        ) == captured_fingerprints[MATE_BRIDGE_HTTP_ENDPOINT_ID][0]
        assert handle.udp_socket is not None
        assert (handle.udp_socket.fileno(), handle.udp_socket.getsockname()) == (
            captured_fingerprints[MATE_BRIDGE_UDP_ENDPOINT_ID][0]
        )
        _assert_bind_blocked(socket.SOCK_STREAM, http_port)
        _assert_bind_blocked(socket.SOCK_DGRAM, udp_port)

        unsigned = http.client.HTTPConnection("127.0.0.1", http_port, timeout=2.0)
        try:
            unsigned.request("GET", "/health")
            unsigned_response = unsigned.getresponse()
            unsigned_response.read()
        finally:
            unsigned.close()
        assert unsigned_response.status == 401

        endpoints = _enabled_mate_endpoints(env)
        assert set(endpoints) == {
            MATE_BRIDGE_HTTP_ENDPOINT_ID,
            MATE_BRIDGE_UDP_ENDPOINT_ID,
        }
        health_probe = MateBridgeHealthProbe(
            config=MateBridgeHealthProbeConfig(
                http_host="127.0.0.1",
                http_port=http_port,
                key_id=config.key_id,
                timeout_seconds=2.0,
            ),
            secret=config.hmac_secret,
            principal=_PRINCIPAL,
        )
        http_health = health_probe.inspect(endpoints[MATE_BRIDGE_HTTP_ENDPOINT_ID])
        udp_health = health_probe.inspect(endpoints[MATE_BRIDGE_UDP_ENDPOINT_ID])
        assert http_health.healthy is True
        assert http_health.owner_id == bridge.MATE_BRIDGE_OWNER_ID
        assert http_health.protocol_id == "http"
        assert http_health.protocol_version == bridge.PROTOCOL_VERSION
        assert udp_health.healthy is True
        assert udp_health.owner_id == bridge.MATE_BRIDGE_OWNER_ID
        assert udp_health.protocol_id == "mateengine-udp"
        assert udp_health.protocol_version == bridge.PROTOCOL_VERSION
        assert int(bridge.state["udp_input_count"]) == prior_udp_inputs
        assert int(bridge.state["udp_count"]) == prior_udp_outputs

        audit_rows = [
            json.loads(line)
            for line in Path(
                env["NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH"]
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        signed_health = [
            row
            for row in audit_rows
            if row.get("event") == "health_access"
            and row.get("principal") == _PRINCIPAL
        ]
        assert len(signed_health) >= 4
    finally:
        handle.stop()

    assert handle.http_server.socket.fileno() == -1
    assert handle.udp_socket is not None and handle.udp_socket.fileno() == -1
    _assert_bind_available(socket.SOCK_STREAM, http_port)
    _assert_bind_available(socket.SOCK_DGRAM, udp_port)


def test_incumbent_is_never_adopted_or_closed_and_partial_claims_roll_back(
    tmp_path: Path,
) -> None:
    http_port = _free_port(socket.SOCK_STREAM)
    udp_port = _free_port(socket.SOCK_DGRAM)
    env = _environment(tmp_path, http_port=http_port, udp_port=udp_port)
    config = bridge.build_security_config(env)

    with _exclusive_socket(socket.SOCK_STREAM) as incumbent:
        incumbent.bind(("127.0.0.1", http_port))
        incumbent.listen(1)
        incumbent_fileno = incumbent.fileno()
        with pytest.raises(bridge.CatalogGovernedBridgeStartupError):
            bridge.start_catalog_governed_bridge(config, environ=env)
        assert incumbent.fileno() == incumbent_fileno
        assert incumbent.getsockname() == ("127.0.0.1", http_port)
        _assert_bind_blocked(socket.SOCK_STREAM, http_port)
        _assert_bind_available(socket.SOCK_DGRAM, udp_port)
        assert not Path(
            env["NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH"]
        ).exists()
        assert not Path(
            env["NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH"]
        ).exists()

    _assert_bind_available(socket.SOCK_STREAM, http_port)


def test_non_enforce_mode_creates_no_listener_or_runtime_state(tmp_path: Path) -> None:
    http_port = _free_port(socket.SOCK_STREAM)
    udp_port = _free_port(socket.SOCK_DGRAM)
    env = _environment(tmp_path, http_port=http_port, udp_port=udp_port)
    env["HOST_SERVICE_CATALOG_MODE"] = "AUDIT"
    config = bridge.build_security_config(env)

    with pytest.raises(
        bridge.CatalogGovernedBridgeStartupError,
        match=bridge.BRIDGE_CATALOG_ENFORCE_REQUIRED,
    ):
        bridge.start_catalog_governed_bridge(config, environ=env)
    _assert_bind_available(socket.SOCK_STREAM, http_port)
    _assert_bind_available(socket.SOCK_DGRAM, udp_port)
    assert not Path(env["NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH"]).exists()
    assert not Path(env["NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH"]).exists()
