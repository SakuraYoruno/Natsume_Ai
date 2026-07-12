from __future__ import annotations

from dataclasses import FrozenInstanceError
import threading
from typing import Any

import pytest

from host_brain.composition.service_catalog_startup import (
    MODE_AUDIT,
    MODE_ENFORCE,
    MODE_PREFLIGHT,
    REASON_AUDIT_ONLY,
    REASON_HEALTH_IDENTITY_MISMATCH,
    REASON_MODE_INVALID,
    REASON_PREPARATION_TAMPERED,
    REASON_RESERVATION_EXPIRED,
    REASON_RESERVATION_REPLAYED,
    REASON_SNAPSHOT_CHANGED,
    STATUS_AUDIT_ONLY,
    STATUS_BLOCKED,
    STATUS_HEALTH_FAILED,
    STATUS_STARTED,
    ServiceCatalogStartupCoordinator,
    activate_candidate,
    prepare_service_start,
)
from host_brain.service_catalog.models import (
    AuthKind,
    EndpointKind,
    EndpointSpec,
    Exposure,
    HealthSpec,
    ProtocolSpec,
    Transport,
    build_catalog_snapshot,
)
from host_brain.service_catalog.preflight import ISSUE_LISTENER_CONFLICT
from host_brain.service_catalog.probes import (
    BindProbeResult,
    HealthProbeResult,
    ReservationAttestation,
)


class _Clock:
    def __init__(self, value: int = 10_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class _ReservationProbe:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.inspect_calls: list[str] = []
        self.reserve_calls: list[tuple[str, ...]] = []
        self.consume_calls: list[str] = []
        self.last_attestation: ReservationAttestation | None = None
        self._consumed: set[str] = set()

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        self.inspect_calls.append(endpoint.endpoint_id)
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
    ) -> ReservationAttestation:
        endpoint_ids = tuple(sorted(endpoint.endpoint_id for endpoint in endpoints))
        self.reserve_calls.append(endpoint_ids)
        self.last_attestation = ReservationAttestation(
            reservation_ref=f"probe-reservation-{len(self.reserve_calls)}",
            snapshot_digest=snapshot_digest,
            endpoint_ids=endpoint_ids,
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + ttl_ms,
            consumable=True,
        )
        return self.last_attestation

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        self.consume_calls.append(attestation.reservation_ref)
        if (
            attestation.reservation_ref in self._consumed
            or attestation.snapshot_digest != snapshot_digest
            or now_ms >= attestation.expires_at_ms
        ):
            return False
        self._consumed.add(attestation.reservation_ref)
        return True


class _IdentityHealthProbe:
    def __init__(self, *, owner_override: str | None = None) -> None:
        self.owner_override = owner_override
        self.calls: list[str] = []

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        self.calls.append(endpoint.endpoint_id)
        return HealthProbeResult(
            endpoint_id=endpoint.endpoint_id,
            healthy=True,
            status_code=200,
            owner_id=self.owner_override or endpoint.health.expected_owner_id,
            protocol_id=endpoint.health.expected_protocol,
            protocol_version=endpoint.health.expected_protocol_version,
        )


def _endpoint(
    *,
    endpoint_id: str = "candidate.http",
    owner_id: str = "candidate.service",
    listener_id: str | None = None,
    port: int = 19191,
) -> EndpointSpec:
    protocol = ProtocolSpec("http", "1")
    return EndpointSpec(
        endpoint_id=endpoint_id,
        listener_id=listener_id or endpoint_id,
        owner_id=owner_id,
        kind=EndpointKind.LISTENER,
        transport=Transport.TCP,
        bind_host="127.0.0.1",
        port=port,
        protocols=(protocol,),
        health=HealthSpec(
            kind="http",
            path="/health",
            expected_owner_id=owner_id,
            expected_protocol=protocol.name,
            expected_protocol_version=protocol.version,
        ),
        exposure=Exposure.LOOPBACK,
        auth_kind=AuthKind.NONE,
        auth_policy_ref="",
    )


def _coordinator(
    *,
    mode: str,
    endpoints: tuple[EndpointSpec, ...] | None = None,
    clock: _Clock | None = None,
    bind_probe: _ReservationProbe | None = None,
    health_probe: _IdentityHealthProbe | None = None,
) -> tuple[
    ServiceCatalogStartupCoordinator,
    _Clock,
    _ReservationProbe,
    _IdentityHealthProbe,
]:
    active_clock = clock or _Clock()
    active_bind = bind_probe or _ReservationProbe()
    active_health = health_probe or _IdentityHealthProbe()
    snapshot = build_catalog_snapshot(endpoints or (_endpoint(),))
    coordinator = ServiceCatalogStartupCoordinator(
        snapshot=snapshot,
        env={
            "HOST_SERVICE_CATALOG_MODE": mode,
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "1000",
        },
        bind_probe=active_bind,
        health_probe=active_health,
        clock_ms=active_clock,
    )
    return coordinator, active_clock, active_bind, active_health


def test_audit_preparation_never_grants_start_authority_or_has_an_effect() -> None:
    bind_probe = _ReservationProbe(available=False)
    health_probe = _IdentityHealthProbe(owner_override="wrong.owner")
    coordinator, _, _, _ = _coordinator(
        mode=MODE_AUDIT,
        bind_probe=bind_probe,
        health_probe=health_probe,
    )
    lifecycle_calls: list[str] = []

    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: lifecycle_calls.append("start"),
        stop_candidate=lambda _handle: lifecycle_calls.append("stop"),
    )

    assert prepared.start_allowed is False
    assert prepared.mode == MODE_AUDIT
    assert prepared.issue_codes
    assert result.status == STATUS_AUDIT_ONLY
    assert result.reason_code == REASON_AUDIT_ONLY
    assert lifecycle_calls == []
    assert bind_probe.reserve_calls == []


def test_candidate_preflight_observes_only_its_own_dynamic_claims() -> None:
    endpoints = (
        _endpoint(endpoint_id="candidate.http", owner_id="candidate.service", port=19191),
        _endpoint(endpoint_id="already.running", owner_id="other.service", port=19192),
    )
    bind_probe = _ReservationProbe()
    coordinator, _, _, _ = _coordinator(
        mode=MODE_PREFLIGHT,
        endpoints=endpoints,
        bind_probe=bind_probe,
    )

    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    lifecycle_calls: list[str] = []
    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: lifecycle_calls.append("start") or object(),
        stop_candidate=lambda _handle: lifecycle_calls.append("stop"),
    )

    assert prepared.start_allowed is True
    assert bind_probe.inspect_calls == ["candidate.http"]
    assert result.status == STATUS_STARTED
    assert lifecycle_calls == ["start"]


def test_preflight_catalog_conflict_blocks_before_start_callback() -> None:
    endpoints = (
        _endpoint(endpoint_id="candidate.http", owner_id="candidate.service", port=18770),
        _endpoint(endpoint_id="incumbent.http", owner_id="incumbent.service", port=18770),
    )
    coordinator, _, _, _ = _coordinator(mode=MODE_PREFLIGHT, endpoints=endpoints)
    lifecycle_calls: list[str] = []

    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: lifecycle_calls.append("start"),
        stop_candidate=lambda _handle: lifecycle_calls.append("stop"),
    )

    assert prepared.start_allowed is False
    assert prepared.status == STATUS_BLOCKED
    assert ISSUE_LISTENER_CONFLICT in prepared.issue_codes
    assert result.status == STATUS_BLOCKED
    assert lifecycle_calls == []


def test_enforce_consumes_snapshot_bound_reservation_once_then_rejects_replay() -> None:
    coordinator, _, bind_probe, health_probe = _coordinator(mode=MODE_ENFORCE)
    lifecycle_calls: list[tuple[str, Any]] = []
    candidate_handle = object()
    prepared = prepare_service_start(
        coordinator,
        candidate_owner_id="candidate.service",
    )

    first = activate_candidate(
        coordinator,
        prepared,
        start_candidate=lambda: lifecycle_calls.append(("start", None)) or candidate_handle,
        stop_candidate=lambda handle: lifecycle_calls.append(("stop", handle)),
    )
    replay = activate_candidate(
        coordinator,
        prepared,
        start_candidate=lambda: lifecycle_calls.append(("replay-start", None)),
        stop_candidate=lambda handle: lifecycle_calls.append(("replay-stop", handle)),
    )

    assert first.status == STATUS_STARTED
    assert first.started is True
    assert replay.status == STATUS_BLOCKED
    assert replay.reason_code == REASON_RESERVATION_REPLAYED
    assert lifecycle_calls == [("start", None)]
    assert len(bind_probe.reserve_calls) == 1
    assert len(bind_probe.consume_calls) == 1
    assert health_probe.calls == ["candidate.http"]
    assert prepared.reservation_ref.startswith("svcstart_")
    assert bind_probe.last_attestation is not None
    assert prepared.reservation_ref != bind_probe.last_attestation.reservation_ref


def test_enforce_expiry_rejects_before_probe_consume_and_callbacks() -> None:
    coordinator, clock, bind_probe, _ = _coordinator(mode=MODE_ENFORCE)
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    clock.value = prepared.expires_at_ms
    calls: list[str] = []

    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: calls.append("start"),
        stop_candidate=lambda _handle: calls.append("stop"),
    )

    assert result.reason_code == REASON_RESERVATION_EXPIRED
    assert calls == []
    assert bind_probe.consume_calls == []


def test_frozen_preparation_and_content_hmac_reject_object_setattr_tamper() -> None:
    coordinator, _, bind_probe, _ = _coordinator(mode=MODE_ENFORCE)
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    with pytest.raises(FrozenInstanceError):
        prepared.mode = MODE_AUDIT  # type: ignore[misc]
    object.__setattr__(prepared, "snapshot_digest", "sha256:" + "0" * 64)
    calls: list[str] = []

    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: calls.append("start"),
        stop_candidate=lambda _handle: calls.append("stop"),
    )

    assert result.reason_code == REASON_PREPARATION_TAMPERED
    assert calls == []
    assert bind_probe.consume_calls == []


def test_catalog_mutation_after_prepare_is_rejected_before_effect() -> None:
    endpoint = _endpoint()
    snapshot = build_catalog_snapshot((endpoint,))
    clock = _Clock()
    bind_probe = _ReservationProbe()
    coordinator = ServiceCatalogStartupCoordinator(
        snapshot=snapshot,
        env={
            "HOST_SERVICE_CATALOG_MODE": MODE_ENFORCE,
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "1000",
        },
        bind_probe=bind_probe,
        health_probe=_IdentityHealthProbe(),
        clock_ms=clock,
    )
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    object.__setattr__(endpoint, "port", 19292)
    calls: list[str] = []

    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: calls.append("start"),
        stop_candidate=lambda _handle: calls.append("stop"),
    )

    assert result.reason_code == REASON_SNAPSHOT_CHANGED
    assert calls == []
    assert bind_probe.consume_calls == []


def test_post_start_identity_failure_stops_only_candidate_exactly_once() -> None:
    health_probe = _IdentityHealthProbe(owner_override="incumbent.service")
    coordinator, _, _, _ = _coordinator(
        mode=MODE_ENFORCE,
        health_probe=health_probe,
    )
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    candidate_handle = object()
    starts: list[str] = []
    stopped_handles: list[Any] = []

    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: starts.append("candidate") or candidate_handle,
        stop_candidate=stopped_handles.append,
    )

    assert result.status == STATUS_HEALTH_FAILED
    assert result.reason_code == REASON_HEALTH_IDENTITY_MISMATCH
    assert result.stop_called is True
    assert result.candidate_stopped is True
    assert starts == ["candidate"]
    assert stopped_handles == [candidate_handle]


def test_concurrent_enforce_activation_allows_one_start() -> None:
    coordinator, _, _, _ = _coordinator(mode=MODE_ENFORCE)
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    barrier = threading.Barrier(3)
    results: list[Any] = []
    starts: list[str] = []
    result_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        result = coordinator.activate_candidate(
            prepared,
            start_candidate=lambda: starts.append("start") or object(),
            stop_candidate=lambda _handle: None,
        )
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert sorted(result.status for result in results) == [STATUS_BLOCKED, STATUS_STARTED]
    assert sum(result.reason_code == REASON_RESERVATION_REPLAYED for result in results) == 1
    assert starts == ["start"]


def test_invalid_explicit_mode_fails_closed_without_probe_or_callbacks() -> None:
    coordinator, _, bind_probe, _ = _coordinator(mode="NOT_A_MODE")
    prepared = coordinator.prepare_service_start(
        candidate_owner_id="candidate.service"
    )
    calls: list[str] = []
    result = coordinator.activate_candidate(
        prepared,
        start_candidate=lambda: calls.append("start"),
        stop_candidate=lambda _handle: calls.append("stop"),
    )

    assert prepared.reason_code == REASON_MODE_INVALID
    assert result.reason_code == REASON_MODE_INVALID
    assert bind_probe.inspect_calls == []
    assert calls == []
