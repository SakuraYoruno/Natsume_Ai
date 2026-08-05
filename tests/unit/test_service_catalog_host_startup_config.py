from __future__ import annotations

import asyncio
import sys

import pytest

from host_brain.contracts.service_catalog import ServiceCatalogRolloutError
from host_brain.service_catalog.host_startup import (
    _candidate_startup_timeout_s,
    _coordinator_lease_ms,
    host_candidate_loop_factory,
)


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


def test_windows_catalog_candidate_uses_selector_listener_loop() -> None:
    loop = host_candidate_loop_factory()
    try:
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
        else:
            assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()
