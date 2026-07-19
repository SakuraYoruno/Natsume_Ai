"""Composed persistence boundary for governed Internal Agent checkpoints."""

from __future__ import annotations

from .task_internal_agent_budget_rejection_store import (
    TaskInternalAgentBudgetRejectionStoreMixin,
)
from .task_internal_agent_checkpoint_commit_store import (
    TaskInternalAgentCheckpointCommitStoreMixin,
)
from .task_internal_agent_lineage_store import (
    TaskInternalAgentLineageStoreMixin,
)


class TaskInternalAgentCheckpointStoreMixin(
    TaskInternalAgentCheckpointCommitStoreMixin,
    TaskInternalAgentBudgetRejectionStoreMixin,
    TaskInternalAgentLineageStoreMixin,
):
    """One TaskStore authority composed from three focused persistence owners."""


__all__ = ["TaskInternalAgentCheckpointStoreMixin"]
