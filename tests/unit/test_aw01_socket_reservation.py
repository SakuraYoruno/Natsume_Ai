from __future__ import annotations

from dataclasses import replace
import socket
import time

import pytest

from host_brain.service_catalog.models import (
    AuthKind,
    EndpointKind,
    EndpointSpec,
    Exposure,
    HealthSpec,
    ProtocolSpec,
    Transport,
)
from host_brain.service_catalog.socket_reservation import (
    OwnedSocketReservationProbe,
)


_DIGEST = "sha256:" + "4" * 64


def _free_port(transport: Transport) -> int:
    socket_type = socket.SOCK_STREAM if transport is Transport.TCP else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, socket_type) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _endpoint(
    endpoint_id: str,
    *,
    transport: Transport,
    port: int,
    host: str = "127.0.0.1",
    exposure: Exposure = Exposure.LOOPBACK,
) -> EndpointSpec:
    protocol = ProtocolSpec(
        "http" if transport is Transport.TCP else "test-udp",
        "1",
    )
    return EndpointSpec(
        endpoint_id=endpoint_id,
        listener_id=endpoint_id,
        owner_id="test.socket.owner",
        kind=EndpointKind.LISTENER,
        transport=transport,
        bind_host=host,
        port=port,
        protocols=(protocol,),
        health=HealthSpec(
            kind="http" if transport is Transport.TCP else "indirect",
            path="/health",
            expected_owner_id="test.socket.owner",
            expected_protocol=protocol.name,
            expected_protocol_version=protocol.version,
        ),
        exposure=exposure,
        auth_kind=AuthKind.NONE,
        auth_policy_ref="",
    )


def _new_socket(transport: Transport) -> socket.socket:
    socket_type = socket.SOCK_STREAM if transport is Transport.TCP else socket.SOCK_DGRAM
    candidate = socket.socket(socket.AF_INET, socket_type)
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if exclusive is not None:
        candidate.setsockopt(socket.SOL_SOCKET, exclusive, 1)
    return candidate


def _assert_bind_blocked(transport: Transport, port: int) -> None:
    with _new_socket(transport) as contender:
        with pytest.raises(OSError):
            contender.bind(("127.0.0.1", port))


def _assert_bind_available(transport: Transport, port: int) -> None:
    with _new_socket(transport) as contender:
        contender.bind(("127.0.0.1", port))


def _close_handoff(handoff: dict[str, tuple[socket.socket, ...]]) -> None:
    for endpoint_sockets in handoff.values():
        for owned in endpoint_sockets:
            owned.close()


def test_reserve_consume_and_take_transfer_the_same_tcp_and_udp_sockets() -> None:
    tcp_port = _free_port(Transport.TCP)
    udp_port = _free_port(Transport.UDP)
    endpoints = (
        _endpoint("candidate.http", transport=Transport.TCP, port=tcp_port),
        _endpoint("candidate.udp", transport=Transport.UDP, port=udp_port),
    )
    probe = OwnedSocketReservationProbe()

    attestation = probe.reserve(
        endpoints,
        snapshot_digest=_DIGEST,
        now_ms=10_000,
        ttl_ms=5_000,
    )
    assert attestation is not None
    held = probe._reservations[attestation.reservation_ref]  # noqa: SLF001
    before = {
        endpoint_id: tuple(
            (owned, owned.fileno(), owned.getsockname()) for owned in endpoint_sockets
        )
        for endpoint_id, endpoint_sockets in held.sockets.items()
    }

    _assert_bind_blocked(Transport.TCP, tcp_port)
    _assert_bind_blocked(Transport.UDP, udp_port)
    assert probe.consume_reservation(
        attestation,
        snapshot_digest=_DIGEST,
        now_ms=10_001,
    )
    handoff = probe.take_consumed_sockets(
        snapshot_digest=_DIGEST,
        endpoint_ids=("candidate.udp", "candidate.http"),
        now_ms=10_002,
    )
    assert handoff is not None

    for endpoint_id, endpoint_sockets in handoff.items():
        assert len(endpoint_sockets) == len(before[endpoint_id])
        for index, owned in enumerate(endpoint_sockets):
            prior_object, prior_fileno, prior_name = before[endpoint_id][index]
            assert owned is prior_object
            assert owned.fileno() == prior_fileno
            assert owned.getsockname() == prior_name
    assert probe.take_consumed_sockets(
        snapshot_digest=_DIGEST,
        endpoint_ids=attestation.endpoint_ids,
        now_ms=10_003,
    ) is None

    # Provider shutdown cannot close sockets whose ownership was transferred.
    probe.close()
    assert all(owned.fileno() >= 0 for rows in handoff.values() for owned in rows)
    _close_handoff(handoff)
    _assert_bind_available(Transport.TCP, tcp_port)
    _assert_bind_available(Transport.UDP, udp_port)


def test_reserve_is_all_or_none_and_never_closes_an_incumbent() -> None:
    tcp_port = _free_port(Transport.TCP)
    udp_port = _free_port(Transport.UDP)
    endpoints = (
        _endpoint("a.candidate.tcp", transport=Transport.TCP, port=tcp_port),
        _endpoint("z.candidate.udp", transport=Transport.UDP, port=udp_port),
    )
    probe = OwnedSocketReservationProbe()

    with _new_socket(Transport.UDP) as incumbent:
        incumbent.bind(("127.0.0.1", udp_port))
        incumbent_fileno = incumbent.fileno()
        assert probe.reserve(
            endpoints,
            snapshot_digest=_DIGEST,
            now_ms=20_000,
            ttl_ms=1_000,
        ) is None
        assert incumbent.fileno() == incumbent_fileno
        assert incumbent.getsockname() == ("127.0.0.1", udp_port)
        _assert_bind_available(Transport.TCP, tcp_port)
        _assert_bind_blocked(Transport.UDP, udp_port)

    _assert_bind_available(Transport.UDP, udp_port)
    probe.close()


def test_tamper_wrong_binding_expiry_and_replay_fail_closed() -> None:
    port = _free_port(Transport.TCP)
    endpoint = _endpoint("candidate.http", transport=Transport.TCP, port=port)
    probe = OwnedSocketReservationProbe()
    attestation = probe.reserve(
        (endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=30_000,
        ttl_ms=1_000,
    )
    assert attestation is not None
    untampered = replace(attestation)
    object.__setattr__(attestation, "expires_at_ms", attestation.expires_at_ms + 1)

    assert not probe.consume_reservation(
        attestation,
        snapshot_digest=_DIGEST,
        now_ms=30_001,
    )
    assert not probe.consume_reservation(
        untampered,
        snapshot_digest="sha256:" + "5" * 64,
        now_ms=30_001,
    )
    assert probe.consume_reservation(
        untampered,
        snapshot_digest=_DIGEST,
        now_ms=30_001,
    )
    assert not probe.consume_reservation(
        untampered,
        snapshot_digest=_DIGEST,
        now_ms=30_002,
    )
    assert probe.take_consumed_sockets(
        snapshot_digest=_DIGEST,
        endpoint_ids=("wrong.endpoint",),
        now_ms=30_002,
    ) is None
    handoff = probe.take_consumed_sockets(
        snapshot_digest=_DIGEST,
        endpoint_ids=(endpoint.endpoint_id,),
        now_ms=30_002,
    )
    assert handoff is not None
    _close_handoff(handoff)

    expiring = OwnedSocketReservationProbe()
    expiring_attestation = expiring.reserve(
        (endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=40_000,
        ttl_ms=100,
    )
    assert expiring_attestation is not None
    assert expiring.cleanup_expired(now_ms=40_100) == 1
    assert not expiring.consume_reservation(
        expiring_attestation,
        snapshot_digest=_DIGEST,
        now_ms=40_100,
    )
    _assert_bind_available(Transport.TCP, port)
    probe.close()
    expiring.close()


def test_wall_ttl_and_close_release_only_provider_owned_sockets() -> None:
    timer_port = _free_port(Transport.UDP)
    timer_endpoint = _endpoint(
        "candidate.timer.udp",
        transport=Transport.UDP,
        port=timer_port,
    )
    timer_probe = OwnedSocketReservationProbe()
    assert timer_probe.reserve(
        (timer_endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=50_000,
        ttl_ms=40,
    ) is not None
    _assert_bind_blocked(Transport.UDP, timer_port)

    deadline = time.monotonic() + 1.0
    while True:
        try:
            _assert_bind_available(Transport.UDP, timer_port)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)

    close_port = _free_port(Transport.TCP)
    close_endpoint = _endpoint(
        "candidate.close.http",
        transport=Transport.TCP,
        port=close_port,
    )
    close_probe = OwnedSocketReservationProbe()
    assert close_probe.reserve(
        (close_endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=60_000,
        ttl_ms=5_000,
    ) is not None
    close_probe.close()
    close_probe.close()
    _assert_bind_available(Transport.TCP, close_port)
    assert close_probe.reserve(
        (close_endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=60_001,
        ttl_ms=5_000,
    ) is None
    timer_probe.close()


def test_non_loopback_claim_is_rejected_without_binding() -> None:
    port = _free_port(Transport.TCP)
    endpoint = _endpoint(
        "candidate.public.http",
        transport=Transport.TCP,
        port=port,
        host="0.0.0.0",
        exposure=Exposure.PUBLIC,
    )
    probe = OwnedSocketReservationProbe()

    inspected = probe.inspect(endpoint)
    assert inspected.available is False
    assert probe.reserve(
        (endpoint,),
        snapshot_digest=_DIGEST,
        now_ms=70_000,
        ttl_ms=1_000,
    ) is None
    _assert_bind_available(Transport.TCP, port)
    probe.close()
