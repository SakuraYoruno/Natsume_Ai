"""Composed read/mutation boundary for migration 0099."""

from __future__ import annotations

from .task_terminal_reconciliation_mutation_store import (
    TaskTerminalReconciliationMutationMixin,
)
from .task_terminal_reconciliation_read_store import (
    TaskTerminalReconciliationReadMixin,
)


class TaskTerminalReconciliationStoreMixin(
    TaskTerminalReconciliationMutationMixin,
    TaskTerminalReconciliationReadMixin,
):
    """One Store authority; mixins borrow its connection and transaction."""


__all__ = ["TaskTerminalReconciliationStoreMixin"]
