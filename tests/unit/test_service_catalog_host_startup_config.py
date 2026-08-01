from __future__ import annotations

import pytest

from host_brain.contracts.service_catalog import ServiceCatalogRolloutError
from host_brain.service_catalog.host_startup import _coordinator_lease_ms


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
