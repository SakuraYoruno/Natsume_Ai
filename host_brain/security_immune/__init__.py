"""Security immune agent package.

This package implements Natsume's authorized-network defensive immune layer.
The MVP is observe-only by default: it collects signals, scores risk, writes
evidence, and prepares response plans without destructive actions.
"""

from host_brain.security_immune.actions import SecurityResponseExecutor
from host_brain.security_immune.agent import SecurityImmuneAgent
from host_brain.security_immune.models import (
    ActionExecutionResult,
    AssetSnapshot,
    IncidentRecord,
    ResponseAction,
    SecuritySignal,
    SecuritySnapshot,
)
from host_brain.security_immune.policy import SecurityImmunePolicy

__all__ = [
    "ActionExecutionResult",
    "AssetSnapshot",
    "IncidentRecord",
    "ResponseAction",
    "SecurityImmuneAgent",
    "SecurityImmunePolicy",
    "SecurityResponseExecutor",
    "SecuritySignal",
    "SecuritySnapshot",
]
