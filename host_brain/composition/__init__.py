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
from host_brain.composition.task_runtime import (
    ProcessTaskRuntimeContainer,
    TaskRuntimeCloseFailure,
    TaskRuntimeContainerCloseError,
    TaskRuntimeContainerConstructionError,
    TaskRuntimeContainerError,
    build_process_task_runtime_container,
)
from host_brain.composition.memory import (
    ProcessMemoryContainer,
    build_process_memory_container,
)
from host_brain.composition.command_ingress import (
    ProcessCommandIngressContainer,
    build_process_command_ingress_container,
)
from host_brain.composition.runtime_group import GovernedRuntimeGroup

__all__ = [
    "ActivationResult",
    "PreparedServiceStart",
    "ProcessSecurityServiceContainer",
    "ProcessTaskRuntimeContainer",
    "ProcessMemoryContainer",
    "ProcessCommandIngressContainer",
    "GovernedRuntimeGroup",
    "SecurityServiceCloseFailure",
    "SecurityServiceContainerCloseError",
    "SecurityServiceName",
    "SecurityServiceStatus",
    "ServiceCatalogStartupCoordinator",
    "TaskRuntimeCloseFailure",
    "TaskRuntimeContainerCloseError",
    "TaskRuntimeContainerConstructionError",
    "TaskRuntimeContainerError",
    "activate_candidate",
    "build_process_security_service_container",
    "build_process_task_runtime_container",
    "build_process_memory_container",
    "build_process_command_ingress_container",
    "prepare_service_start",
]
