from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from host_brain.service_catalog.defaults import build_local_catalog
from host_brain.service_catalog.models import (
    AuthKind,
    CatalogMode,
    EndpointKind,
    EndpointSpec,
    Exposure,
    HealthSpec,
    ProtocolSpec,
    Transport,
    build_catalog_snapshot,
)
from host_brain.service_catalog.preflight import (
    ISSUE_BIND_INVALID,
    ISSUE_BIND_UNAVAILABLE,
    ISSUE_CLAIM_SELECTION_INVALID,
    ISSUE_HEALTH_IDENTITY_MISMATCH,
    ISSUE_HEALTH_REQUIRED,
    ISSUE_LISTENER_CONFLICT,
    ISSUE_OWNER_REQUIRED,
    ISSUE_PROTOCOL_REQUIRED,
    ISSUE_PUBLIC_AUTH_REQUIRED,
    ISSUE_RESERVATION_REQUIRED,
    preflight_catalog,
    validate_catalog,
)
from host_brain.service_catalog.probes import (
    BindProbeResult,
    HealthProbeResult,
    ReadOnlySocketBindProbe,
    ReservationAttestation,
)


def _endpoint(
    endpoint_id: str,
    *,
    owner_id: str = "owner.alpha",
    listener_id: str = "listener.alpha",
    kind: EndpointKind = EndpointKind.LISTENER,
    transport: Transport = Transport.TCP,
    host: str = "127.0.0.1",
    port: int = 18770,
    protocol: str = "http",
    protocol_version: str = "1",
    auth_kind: AuthKind = AuthKind.NONE,
    auth_policy_ref: str = "",
    enabled: bool = True,
) -> EndpointSpec:
    exposure = Exposure.LOOPBACK
    if host in {"*", "0.0.0.0", "::"}:
        exposure = Exposure.PUBLIC
    primary = ProtocolSpec(protocol, protocol_version)
    return EndpointSpec(
        endpoint_id=endpoint_id,
        listener_id=listener_id,
        owner_id=owner_id,
        kind=kind,
        transport=transport,
        bind_host=host,
        port=port,
        protocols=(primary,),
        health=HealthSpec(
            kind="http",
            path="/health",
            expected_owner_id=owner_id,
            expected_protocol=protocol,
            expected_protocol_version=protocol_version,
        ),
        exposure=exposure,
        auth_kind=auth_kind,
        auth_policy_ref=auth_policy_ref,
        enabled=enabled,
    )


def _codes(issues: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(getattr(issue, "reason_code") for issue in issues)


class _BindProbe:
    def __init__(
        self,
        *,
        available: bool = True,
        reservation: ReservationAttestation | None = None,
    ) -> None:
        self.available = available
        self.reservation = reservation
        self.inspected: list[str] = []
        self.reserved: list[tuple[str, ...]] = []
        self._consumed: set[str] = set()

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        self.inspected.append(endpoint.endpoint_id)
        return BindProbeResult(
            endpoint_id=endpoint.endpoint_id,
            available=self.available,
            detail="occupied" if not self.available else "",
        )

    def reserve(
        self,
        endpoints: tuple[EndpointSpec, ...],
        *,
        snapshot_digest: str,
        now_ms: int,
        ttl_ms: int,
    ) -> ReservationAttestation | None:
        self.reserved.append(tuple(endpoint.endpoint_id for endpoint in endpoints))
        return self.reservation

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        if (
            attestation.reservation_ref in self._consumed
            or snapshot_digest != attestation.snapshot_digest
            or not attestation.issued_at_ms <= now_ms < attestation.expires_at_ms
        ):
            return False
        self._consumed.add(attestation.reservation_ref)
        return True


class _HealthProbe:
    def __init__(
        self,
        *,
        owner_id: str | None,
        protocol_id: str | None = "http",
        protocol_version: str | None = "1",
        healthy: bool = True,
        status_code: int | None = 200,
    ) -> None:
        self.owner_id = owner_id
        self.protocol_id = protocol_id
        self.protocol_version = protocol_version
        self.healthy = healthy
        self.status_code = status_code

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        return HealthProbeResult(
            endpoint_id=endpoint.endpoint_id,
            healthy=self.healthy,
            status_code=self.status_code,
            owner_id=self.owner_id,
            protocol_id=self.protocol_id,
            protocol_version=self.protocol_version,
        )


def test_default_catalog_detects_the_enabled_18770_multi_owner_conflict() -> None:
    default_issues = validate_catalog(build_local_catalog())
    assert ISSUE_LISTENER_CONFLICT not in _codes(default_issues)

    vrm_catalog = build_local_catalog({"EDGE_AVATAR_BACKEND": "vrm"})
    conflicts = [
        issue for issue in validate_catalog(vrm_catalog)
        if issue.reason_code == ISSUE_LISTENER_CONFLICT
    ]
    assert len(conflicts) == 1
    assert conflicts[0].endpoint_ids == (
        "edge.unity.avatar.bridge.http",
        "host.mateengine.bridge.http",
    )


def test_same_owner_and_listener_can_share_but_other_listener_or_owner_cannot() -> None:
    first = _endpoint("endpoint.first")
    shared = _endpoint("endpoint.shared", protocol="websocket")
    assert validate_catalog(build_catalog_snapshot((first, shared))) == ()

    other_listener = replace(shared, endpoint_id="endpoint.other-listener", listener_id="listener.beta")
    issue = validate_catalog(build_catalog_snapshot((first, other_listener)))
    assert _codes(issue) == (ISSUE_LISTENER_CONFLICT,)

    other_owner = _endpoint("endpoint.other-owner", owner_id="owner.beta")
    issue = validate_catalog(build_catalog_snapshot((first, other_owner)))
    assert _codes(issue) == (ISSUE_LISTENER_CONFLICT,)


def test_wildcard_and_ipv6_overlap_are_conservative_and_require_auth_policy() -> None:
    wildcard = _endpoint("endpoint.wildcard", host="::")
    ipv4 = _endpoint(
        "endpoint.ipv4",
        owner_id="owner.beta",
        listener_id="listener.beta",
        host="192.0.2.10",
    )
    issues = validate_catalog(build_catalog_snapshot((wildcard, ipv4)))
    assert ISSUE_PUBLIC_AUTH_REQUIRED in _codes(issues)
    assert ISSUE_LISTENER_CONFLICT in _codes(issues)

    authenticated = replace(
        wildcard,
        auth_kind=AuthKind.HMAC,
        auth_policy_ref="bridge.hmac-scope-rate-audit.v1",
    )
    codes = _codes(validate_catalog(build_catalog_snapshot((authenticated,))))
    assert ISSUE_PUBLIC_AUTH_REQUIRED not in codes

    hostname = replace(authenticated, endpoint_id="endpoint.hostname", bind_host="example.test")
    assert ISSUE_BIND_INVALID in _codes(validate_catalog(build_catalog_snapshot((hostname,))))


def test_tcp_and_udp_claims_on_the_same_host_and_port_are_separate() -> None:
    tcp = _endpoint("endpoint.tcp", transport=Transport.TCP)
    udp = _endpoint(
        "endpoint.udp",
        owner_id="owner.beta",
        listener_id="listener.beta",
        transport=Transport.UDP,
    )
    assert validate_catalog(build_catalog_snapshot((tcp, udp))) == ()


def test_disabled_dependency_and_dynamic_endpoints_do_not_claim_or_reserve() -> None:
    disabled = _endpoint("endpoint.disabled", enabled=False)
    dependency = _endpoint(
        "endpoint.dependency",
        kind=EndpointKind.DEPENDENCY,
        owner_id="external.service",
        listener_id="external.service",
    )
    dynamic = _endpoint(
        "endpoint.dynamic",
        kind=EndpointKind.DYNAMIC_TEMPLATE,
        port=0,
    )
    snapshot = build_catalog_snapshot((disabled, dependency, dynamic))
    probe = _BindProbe()
    decision = preflight_catalog(
        snapshot,
        mode=CatalogMode.ENFORCE,
        bind_probe=probe,
        now_ms=1_000,
    )
    assert decision.start_allowed is True
    assert decision.reservation is None
    assert probe.inspected == []
    assert probe.reserved == []


def test_untrusted_shape_gets_stable_missing_owner_protocol_and_health_issues() -> None:
    endpoint = SimpleNamespace(
        endpoint_id="endpoint.untrusted",
        listener_id="listener.untrusted",
        owner_id="",
        kind="LISTENER",
        enabled=True,
        transport="tcp",
        bind_host="127.0.0.1",
        port=18770,
        protocols=(),
        health=None,
        auth_kind="NONE",
        auth_policy_ref="",
    )
    snapshot = SimpleNamespace(endpoints=(endpoint,), digest="sha256:" + "1" * 64)
    codes = _codes(validate_catalog(snapshot))
    assert ISSUE_OWNER_REQUIRED in codes
    assert ISSUE_PROTOCOL_REQUIRED in codes
    assert ISSUE_HEALTH_REQUIRED in codes

    with pytest.raises(TypeError, match="typed CatalogSnapshot"):
        preflight_catalog(
            snapshot,
            mode=CatalogMode.PREFLIGHT,
            bind_probe=_BindProbe(),
            now_ms=1_000,
        )


def test_preflight_claim_selection_must_name_enabled_typed_listeners() -> None:
    snapshot = build_catalog_snapshot((_endpoint("endpoint.selected"),))
    decision = preflight_catalog(
        snapshot,
        mode=CatalogMode.PREFLIGHT,
        bind_probe=_BindProbe(),
        now_ms=1_000,
        claim_endpoint_ids=("endpoint.unknown",),
    )

    assert decision.start_allowed is False
    assert ISSUE_CLAIM_SELECTION_INVALID in decision.issue_codes


def test_health_identity_must_name_a_declared_protocol_version() -> None:
    endpoint = _endpoint("endpoint.health-binding")
    endpoint = replace(
        endpoint,
        health=HealthSpec(
            kind="http",
            path="/health",
            expected_owner_id=endpoint.owner_id,
            expected_protocol="grpc",
            expected_protocol_version="1",
        ),
    )
    assert ISSUE_HEALTH_IDENTITY_MISMATCH in _codes(
        validate_catalog(build_catalog_snapshot((endpoint,)))
    )


def test_audit_reports_but_preflight_blocks_occupied_and_spoofed_health() -> None:
    snapshot = build_catalog_snapshot((_endpoint("endpoint.occupied"),))
    bind_probe = _BindProbe(available=False)
    spoof = _HealthProbe(owner_id=None)

    audit = preflight_catalog(
        snapshot,
        mode=CatalogMode.AUDIT,
        bind_probe=bind_probe,
        health_probe=spoof,
        now_ms=10_000,
    )
    assert audit.start_allowed is False
    assert all(issue.blocking is False for issue in audit.issues)
    assert ISSUE_BIND_UNAVAILABLE in audit.issue_codes
    assert ISSUE_HEALTH_IDENTITY_MISMATCH in audit.issue_codes

    preflight = preflight_catalog(
        snapshot,
        mode=CatalogMode.PREFLIGHT,
        bind_probe=bind_probe,
        health_probe=spoof,
        now_ms=10_000,
    )
    assert preflight.start_allowed is False
    assert all(issue.blocking is True for issue in preflight.issues)


def test_public_liveness_is_not_authentication_and_ipv4_mapped_ipv6_overlaps() -> None:
    liveness = replace(
        _endpoint("endpoint.liveness", host="0.0.0.0"),
        auth_kind=AuthKind.PUBLIC_LIVENESS,
        auth_policy_ref="public-liveness-only.v1",
    )
    ipv4 = _endpoint("endpoint.ipv4-mapped-left", host="127.0.0.1")
    mapped = _endpoint(
        "endpoint.ipv4-mapped-right",
        owner_id="owner.beta",
        listener_id="listener.beta",
        host="::ffff:127.0.0.1",
    )

    assert ISSUE_PUBLIC_AUTH_REQUIRED in _codes(
        validate_catalog(build_catalog_snapshot((liveness,)))
    )
    assert ISSUE_LISTENER_CONFLICT in _codes(
        validate_catalog(build_catalog_snapshot((ipv4, mapped)))
    )


def test_matching_incumbent_identity_is_still_not_adopted() -> None:
    snapshot = build_catalog_snapshot((_endpoint("endpoint.occupied"),))
    decision = preflight_catalog(
        snapshot,
        mode=CatalogMode.PREFLIGHT,
        bind_probe=_BindProbe(available=False),
        health_probe=_HealthProbe(owner_id="owner.alpha"),
        now_ms=10_000,
    )
    assert decision.start_allowed is False
    assert ISSUE_BIND_UNAVAILABLE in decision.issue_codes
    assert ISSUE_HEALTH_IDENTITY_MISMATCH not in decision.issue_codes


def test_enforce_requires_exact_short_lived_consumable_reservation() -> None:
    snapshot = build_catalog_snapshot((_endpoint("endpoint.enforced"),))
    missing = preflight_catalog(
        snapshot,
        mode=CatalogMode.ENFORCE,
        bind_probe=_BindProbe(),
        now_ms=20_000,
    )
    assert missing.start_allowed is False
    assert missing.issue_codes == (ISSUE_RESERVATION_REQUIRED,)

    attestation = ReservationAttestation(
        reservation_ref="reservation:test:1",
        snapshot_digest=snapshot.digest,
        endpoint_ids=("endpoint.enforced",),
        issued_at_ms=20_000,
        expires_at_ms=24_000,
        consumable=True,
    )
    probe = _BindProbe(reservation=attestation)
    accepted = preflight_catalog(
        snapshot,
        mode=CatalogMode.ENFORCE,
        bind_probe=probe,
        now_ms=20_000,
        reservation_ttl_ms=5_000,
    )
    assert accepted.start_allowed is True
    assert accepted.snapshot_digest == snapshot.digest
    assert accepted.reservation is attestation
    assert accepted.reservation_ref == attestation.reservation_ref
    assert probe.consume_reservation(attestation, snapshot_digest=snapshot.digest, now_ms=20_001)
    assert not probe.consume_reservation(attestation, snapshot_digest=snapshot.digest, now_ms=20_002)

    wrong_digest = replace(attestation, snapshot_digest="sha256:" + "f" * 64)
    rejected = preflight_catalog(
        snapshot,
        mode=CatalogMode.ENFORCE,
        bind_probe=_BindProbe(reservation=wrong_digest),
        now_ms=20_000,
        reservation_ttl_ms=5_000,
    )
    assert rejected.start_allowed is False
    assert rejected.issue_codes == (ISSUE_RESERVATION_REQUIRED,)


def test_read_only_probe_prefers_bind_host_over_legacy_host(monkeypatch: pytest.MonkeyPatch) -> None:
    bound: list[tuple[object, ...]] = []

    class _Socket:
        def setsockopt(self, *_args: object) -> None:
            return None

        def bind(self, address: tuple[object, ...]) -> None:
            bound.append(address)

        def close(self) -> None:
            return None

    monkeypatch.setattr("host_brain.service_catalog.probes.socket.socket", lambda *_args: _Socket())
    endpoint = SimpleNamespace(
        endpoint_id="endpoint.bind-host",
        bind_host="127.0.0.1",
        host="not-a-literal.invalid",
        port=18770,
        transport="tcp",
    )
    result = ReadOnlySocketBindProbe().inspect(endpoint)
    assert result.available is True
    assert bound == [("127.0.0.1", 18770)]
