"""Validation-only compatibility entrypoint for the legacy life soak harness.

The harness may isolate environment variables, fixture singletons and JSONL
artifacts for deterministic validation.  Production composition must never
import this module; AW-04's authority lives in ``host_brain.digital_life``.
"""

from host_brain.validation.digital_life_supervisor import (
    CORE_TICK_TASK_IDS,
    DigitalLifeRuntimeConfig,
    DigitalLifeSupervisor,
    ForegroundTurnResult,
)

__all__ = [
    "CORE_TICK_TASK_IDS",
    "DigitalLifeRuntimeConfig",
    "DigitalLifeSupervisor",
    "ForegroundTurnResult",
]
