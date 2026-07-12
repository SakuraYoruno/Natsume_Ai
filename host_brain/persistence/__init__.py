"""Canonical Host persistence adapters used by production composition.

The package root is intentionally lazy.  Persistence adapters depend on
domain contracts, so eagerly importing every adapter here would make importing
one repository initialize unrelated runtime packages and create cycles.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "AGENT_EXECUTION_RESULT_CONFLICT": "agent_execution_repository",
    "AGENT_EXECUTION_RESULT_INVALID": "agent_execution_repository",
    "AGENT_EXECUTION_SCHEMA_NOT_READY": "agent_execution_repository",
    "AgentExecutionRepositoryError": "agent_execution_repository",
    "AgentExecutionResultRepository": "agent_execution_repository",
    "HOST_COMMAND_CONFLICT": "host_command_repository",
    "HOST_COMMAND_DURABLE_DATA_INVALID": "host_command_repository",
    "HOST_COMMAND_INGRESS_CONFLICT": "host_command_repository",
    "HOST_COMMAND_INVALID": "host_command_repository",
    "HOST_COMMAND_SCHEMA_NOT_READY": "host_command_repository",
    "HostCommandRepository": "host_command_repository",
    "HostCommandRepositoryError": "host_command_repository",
    "LIFE_MEMORY_ADMISSION_CONFLICT": "life_memory_repository",
    "LIFE_MEMORY_ADMISSION_INVALID": "life_memory_repository",
    "LIFE_MEMORY_DURABLE_DATA_INVALID": "life_memory_repository",
    "LIFE_MEMORY_SCHEMA_NOT_READY": "life_memory_repository",
    "LifeMemoryRepository": "life_memory_repository",
    "LifeMemoryRepositoryError": "life_memory_repository",
    "SCHEDULER_DEFINITION_CONFLICT": "scheduler_repository",
    "SCHEDULER_DEFINITION_INVALID": "scheduler_repository",
    "SCHEDULER_DEFINITION_NOT_FOUND": "scheduler_repository",
    "SCHEDULER_EXECUTION_RESULT_CONFLICT": "scheduler_repository",
    "SCHEDULER_EXECUTION_RESULT_INVALID": "scheduler_repository",
    "SCHEDULER_EXECUTION_RESULT_NOT_FOUND": "scheduler_repository",
    "SCHEDULER_OCCURRENCE_CONFLICT": "scheduler_repository",
    "SCHEDULER_OCCURRENCE_INVALID": "scheduler_repository",
    "SCHEDULER_SCHEMA_NOT_READY": "scheduler_repository",
    "SchedulerDefinitionRepository": "scheduler_repository",
    "SchedulerExecutionResultRepository": "scheduler_repository",
    "SchedulerRepositoryError": "scheduler_repository",
    "RuntimeEventRepository": "runtime_event_repository",
    "RuntimeEventRepositoryError": "runtime_event_repository",
    "TASK_RUNTIME_PROJECTION": "runtime_event_repository",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(__all__))
