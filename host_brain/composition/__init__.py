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
from host_brain.composition.security_services import (
    ProcessSecurityServiceContainer,
    SecurityServiceCloseFailure,
    SecurityServiceContainerCloseError,
    SecurityServiceName,
    SecurityServiceStatus,
    build_process_security_service_container,
)

__all__ = [
    "ActivationResult",
    "PreparedServiceStart",
    "ProcessSecurityServiceContainer",
    "SecurityServiceCloseFailure",
    "SecurityServiceContainerCloseError",
    "SecurityServiceName",
    "SecurityServiceStatus",
    "ServiceCatalogStartupCoordinator",
    "activate_candidate",
    "build_process_security_service_container",
    "prepare_service_start",
]
