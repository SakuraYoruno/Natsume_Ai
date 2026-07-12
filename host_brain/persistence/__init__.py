"""Canonical Host persistence adapters used by production composition."""

from host_brain.persistence.agent_execution_repository import (
    AGENT_EXECUTION_RESULT_CONFLICT,
    AGENT_EXECUTION_RESULT_INVALID,
    AGENT_EXECUTION_SCHEMA_NOT_READY,
    AgentExecutionRepositoryError,
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_CONFLICT,
    HOST_COMMAND_DURABLE_DATA_INVALID,
    HOST_COMMAND_INGRESS_CONFLICT,
    HOST_COMMAND_INVALID,
    HOST_COMMAND_SCHEMA_NOT_READY,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.life_memory_repository import (
    LIFE_MEMORY_ADMISSION_CONFLICT,
    LIFE_MEMORY_ADMISSION_INVALID,
    LIFE_MEMORY_DURABLE_DATA_INVALID,
    LIFE_MEMORY_SCHEMA_NOT_READY,
    LifeMemoryRepository,
    LifeMemoryRepositoryError,
)

__all__ = [
    "AGENT_EXECUTION_RESULT_CONFLICT",
    "AGENT_EXECUTION_RESULT_INVALID",
    "AGENT_EXECUTION_SCHEMA_NOT_READY",
    "AgentExecutionRepositoryError",
    "AgentExecutionResultRepository",
    "HOST_COMMAND_CONFLICT",
    "HOST_COMMAND_DURABLE_DATA_INVALID",
    "HOST_COMMAND_INGRESS_CONFLICT",
    "HOST_COMMAND_INVALID",
    "HOST_COMMAND_SCHEMA_NOT_READY",
    "HostCommandRepository",
    "HostCommandRepositoryError",
    "LIFE_MEMORY_ADMISSION_CONFLICT",
    "LIFE_MEMORY_ADMISSION_INVALID",
    "LIFE_MEMORY_DURABLE_DATA_INVALID",
    "LIFE_MEMORY_SCHEMA_NOT_READY",
    "LifeMemoryRepository",
    "LifeMemoryRepositoryError",
]
