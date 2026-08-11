from __future__ import annotations

import asyncio
import sys

import pytest

from host_brain.contracts.service_catalog import ServiceCatalogRolloutError
from host_brain.service_catalog.host_startup import (
    _acquire_coordinator_authority_with_crash_recovery,
    _candidate_startup_timeout_s,
    _coordinator_lease_ms,
    host_candidate_loop_factory,
)


class _Probe:
    def __init__(self, *, available: bool | list[bool]) -> None:
        self.available = (
            list(available) if isinstance(available, list) else [available]
        )

    def inspect(self, _endpoint: object) -> object:
        value = self.available.pop(0) if len(self.available) > 1 else self.available[0]
        return type("ProbeResult", (), {"available": value})()


class _Store:
    def __init__(self, outcomes: list[object | None]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def acquire_authority(self, **_kwargs: object) -> object | None:
        self.calls += 1
        return self.outcomes.pop(0) if self.outcomes else None


def test_service_catalog_coordinator_lease_defaults_to_one_minute() -> None:
    assert _coordinator_lease_ms({}) == 60_000


@pytest.mark.parametrize("raw", ("59999", "300001", "invalid"))
def test_service_catalog_coordinator_lease_rejects_unsafe_value(raw: str) -> None:
    with pytest.raises(ServiceCatalogRolloutError):
        _coordinator_lease_ms(
            {"HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS": raw}
        )


def test_service_catalog_coordinator_lease_accepts_bounded_override() -> None:
    assert (
        _coordinator_lease_ms(
            {"HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS": "90000"}
        )
        == 90_000
    )


def test_service_catalog_candidate_startup_timeout_defaults_for_recovery() -> None:
    assert _candidate_startup_timeout_s({}) == 180.0


@pytest.mark.parametrize("raw", ("14.9", "600.1", "invalid"))
def test_service_catalog_candidate_startup_timeout_rejects_unsafe_value(
    raw: str,
) -> None:
    with pytest.raises(ServiceCatalogRolloutError):
        _candidate_startup_timeout_s(
            {"HOST_SERVICE_CATALOG_CANDIDATE_STARTUP_TIMEOUT_S": raw}
        )


def test_service_catalog_candidate_startup_timeout_accepts_launcher_window() -> None:
    assert (
        _candidate_startup_timeout_s(
            {"HOST_SERVICE_CATALOG_CANDIDATE_STARTUP_TIMEOUT_S": "300"}
        )
        == 300.0
    )


def test_catalog_coordinator_retries_stale_lease_when_listener_is_free() -> None:
    authority = object()
    store = _Store([None, authority])

    result = _acquire_coordinator_authority_with_crash_recovery(
        store=store,  # type: ignore[arg-type]
        runtime_scope_id="runtime-one",
        owner_id="owner-two",
        lease_ms=60_000,
        endpoint=object(),  # type: ignore[arg-type]
        reservation_probe=_Probe(available=True),  # type: ignore[arg-type]
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert result is authority
    assert store.calls == 2


def test_catalog_coordinator_does_not_wait_out_a_live_listener() -> None:
    store = _Store([None])

    result = _acquire_coordinator_authority_with_crash_recovery(
        store=store,  # type: ignore[arg-type]
        runtime_scope_id="runtime-one",
        owner_id="owner-two",
        lease_ms=60_000,
        endpoint=object(),  # type: ignore[arg-type]
        reservation_probe=_Probe(available=False),  # type: ignore[arg-type]
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("slept")),
        monotonic=lambda: 0.0,
    )

    assert result is None
    assert store.calls == 1


def test_catalog_coordinator_stops_when_listener_appears_during_wait() -> None:
    store = _Store([None, object()])

    result = _acquire_coordinator_authority_with_crash_recovery(
        store=store,  # type: ignore[arg-type]
        runtime_scope_id="runtime-one",
        owner_id="owner-two",
        lease_ms=60_000,
        endpoint=object(),  # type: ignore[arg-type]
        reservation_probe=_Probe(available=[True, False]),  # type: ignore[arg-type]
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert result is None
    assert store.calls == 1


def test_windows_catalog_candidate_uses_selector_listener_loop() -> None:
    loop = host_candidate_loop_factory()
    try:
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
        else:
            assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()
