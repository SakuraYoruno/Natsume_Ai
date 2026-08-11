"""Application wiring for the read-only durable task status projection."""

from __future__ import annotations

from typing import Any

from host_brain.persistence.durable_task_status_projection import (
    DurableTaskStatusProjection,
)


def build_durable_task_status_projection(*, ledger: Any) -> DurableTaskStatusProjection:
    """Bind progress reads to the ledger-owned canonical SQLite database."""

    return DurableTaskStatusProjection.from_ledger(ledger)


__all__ = ["build_durable_task_status_projection"]
