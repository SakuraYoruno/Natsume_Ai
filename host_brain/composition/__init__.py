"""Explicit Host composition boundaries.

The package deliberately contains no import-time startup side effects.  A
caller must provide the environment snapshot, catalog snapshot and probes to
the startup coordinator before a candidate can be considered for activation.
"""

from host_brain.composition.service_catalog_startup import (
    ActivationResult,
    PreparedServiceStart,
    ServiceCatalogStartupCoordinator,
    activate_candidate,
    prepare_service_start,
)

__all__ = [
    "ActivationResult",
    "PreparedServiceStart",
    "ServiceCatalogStartupCoordinator",
    "activate_candidate",
    "prepare_service_start",
]
