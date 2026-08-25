"""TASK-0492 composition integration without real listeners or services.

This test deliberately uses injected in-memory probes and recording lifecycle
callbacks.  It must never bind a socket, spawn a process or stop an incumbent.
"""

from __future__ import annotations

from typing import Any

import pytest

from host_brain.service_catalog.startup_coordinator import (
    REASON_RESERVATION_REPLAYED,
    STATUS_BLOCKED,
    STATUS_STARTED,
    ServiceCatalogStartupCoordinator,
)
from host_brain.service_catalog.defaults import build_local_catalog
from host_brain.service_catalog.models import EndpointKind, EndpointSpec
from host_brain.service_catalog.preflight import ISSUE_LISTENER_CONFLICT
from host_brain.service_catalog.probes import (
    BindProbeResult,
    HealthProbeResult,
    ReservationAttestation,
)


class _RecordingReservationProbe:
    def __init__(self) -> None:
        self.inspected: list[str] = []
        self.reserved: list[tuple[str, ...]] = []
        self.consumed: list[str] = []
        self._consumed_refs: set[str] = set()

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        self.inspected.append(endpoint.endpoint_id)
        return BindProbeResult(endpoint_id=endpoint.endpoint_id, available=True)

    def reserve(
        self,
        endpoints: tuple[EndpointSpec, ...],
        *,
        snapshot_digest: str,
        now_ms: int,
        ttl_ms: int,
    ) -> ReservationAttestation:
        endpoint_ids = tuple(sorted(endpoint.endpoint_id for endpoint in endpoints))
        self.reserved.append(endpoint_ids)
        return ReservationAttestation(
            reservation_ref=f"integration-reservation-{len(self.reserved)}",
            snapshot_digest=snapshot_digest,
            endpoint_ids=endpoint_ids,
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + ttl_ms,
            consumable=True,
        )

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        self.consumed.append(attestation.reservation_ref)
        if (
            attestation.reservation_ref in self._consumed_refs
            or attestation.snapshot_digest != snapshot_digest
            or now_ms >= attestation.expires_at_ms
        ):
            return False
        self._consumed_refs.add(attestation.reservation_ref)
        return True


class _ExactIdentityHealthProbe:
    def __init__(self) -> None:
        self.inspected: list[str] = []

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        self.inspected.append(endpoint.endpoint_id)
        return HealthProbeResult(
            endpoint_id=endpoint.endpoint_id,
            healthy=True,
            status_code=200,
            owner_id=endpoint.health.expected_owner_id,
            protocol_id=endpoint.health.expected_protocol,
            protocol_version=endpoint.health.expected_protocol_version,
        )


def _enabled_listener_ids_at_18770(snapshot: Any) -> tuple[str, ...]:
    return tuple(
        endpoint.endpoint_id
        for endpoint in snapshot.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    )


def test_catalog_defaults_conflict_gate_and_one_shot_candidate_activation() -> None:
    clock_ms = lambda: 42_000

    # Bridges are disabled by default, so the catalog must not silently claim
    # the shared embodiment port.
    default_snapshot = build_local_catalog(
        {
            "EDGE_AVATAR_BACKEND": "vts",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        }
    )
    assert _enabled_listener_ids_at_18770(default_snapshot) == ()

    # Explicit enablement and owner election produce one candidate authority.
    mate_snapshot = build_local_catalog(
        {
            "EDGE_AVATAR_BACKEND": "vts",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "NATSUME_EMBODIMENT_BRIDGE_OWNER": "mateengine",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        }
    )
    assert _enabled_listener_ids_at_18770(mate_snapshot) == (
        "host.mateengine.bridge.http",
    )
    default_bind = _RecordingReservationProbe()
    default_gate = ServiceCatalogStartupCoordinator(
        snapshot=mate_snapshot,
        env={
            "HOST_SERVICE_CATALOG_MODE": "PREFLIGHT",
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "1000",
        },
        bind_probe=default_bind,
        health_probe=_ExactIdentityHealthProbe(),
        clock_ms=clock_ms,
    )
    default_prepared = default_gate.prepare_service_start(
        candidate_owner_id="host.mateengine.bridge"
    )
    assert default_prepared.start_allowed is True
    assert ISSUE_LISTENER_CONFLICT not in default_prepared.issue_codes

    # Ambiguous dual enablement is rejected while constructing catalog truth,
    # before reservation or any lifecycle callback can run.
    with pytest.raises(ValueError, match="SERVICE_CATALOG_EMBODIMENT_OWNER_REQUIRED"):
        build_local_catalog(
            {
                "EDGE_AVATAR_BACKEND": "vrm",
                "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
                "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
            }
        )

    # A clean ENFORCE path consumes the exact injected reservation, starts only
    # the candidate, validates its declared owner/protocol health and rejects a
    # replay without a second callback.
    enforce_bind = _RecordingReservationProbe()
    enforce_health = _ExactIdentityHealthProbe()
    enforce_gate = ServiceCatalogStartupCoordinator(
        snapshot=mate_snapshot,
        env={
            "HOST_SERVICE_CATALOG_MODE": "ENFORCE",
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "1000",
        },
        bind_probe=enforce_bind,
        health_probe=enforce_health,
        clock_ms=clock_ms,
    )
    prepared = enforce_gate.prepare_service_start(
        candidate_owner_id="host.mateengine.bridge"
    )
    candidate_handle = object()
    lifecycle_callbacks: list[tuple[str, Any]] = []
    first = enforce_gate.activate_candidate(
        prepared,
        start_candidate=lambda: lifecycle_callbacks.append(("start", None))
        or candidate_handle,
        stop_candidate=lambda handle: lifecycle_callbacks.append(("stop", handle)),
    )
    replay = enforce_gate.activate_candidate(
        prepared,
        start_candidate=lambda: lifecycle_callbacks.append(("replay-start", None)),
        stop_candidate=lambda handle: lifecycle_callbacks.append(("replay-stop", handle)),
    )

    assert first.status == STATUS_STARTED
    assert replay.status == STATUS_BLOCKED
    assert replay.reason_code == REASON_RESERVATION_REPLAYED
    assert lifecycle_callbacks == [("start", None)]
    assert len(enforce_bind.reserved) == 1
    assert len(enforce_bind.consumed) == 1
    assert set(enforce_health.inspected) == {
        "host.mateengine.bridge.http",
    }
