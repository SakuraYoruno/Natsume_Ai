from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from host_brain.persistence.task_terminal_reconciliation_read_store import (
    TaskTerminalReconciliationReadMixin,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


ATTEMPT_ID = "attempt-terminal-checkpoint-budget-1"


def _observation() -> dict[str, Any]:
    return {
        "graph": {
            "graph_id": "graph-terminal-checkpoint-budget-1",
            "revision": 2,
        }
    }


def _node() -> dict[str, Any]:
    return {
        "node_id": "node-terminal-checkpoint-budget-1",
        "status_version": 4,
    }


def _attempt() -> dict[str, Any]:
    return {
        "attempt_id": ATTEMPT_ID,
        "status_version": 5,
    }


def _budget() -> dict[str, Any]:
    return {
        "budget_id": "budget-terminal-checkpoint-budget-1",
        "status_version": 3,
    }


def _resolved(authority_kind: str) -> dict[str, Any]:
    return {
        "authority_kind": authority_kind,
        "graph": {
            "graph_id": "graph-terminal-checkpoint-budget-1",
            "revision": 2,
        },
        "node": _node(),
        "attempt": _attempt(),
        "budget": _budget(),
    }


class _Reader(TaskTerminalReconciliationReadMixin):
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[str] = []

    def _try_resolve_checkpoint_budget_rejection_terminal_uow(
        self,
        _conn: Any,
        attempt_id: str,
    ) -> Any:
        self.calls.append(attempt_id)
        if isinstance(self.result, BaseException):
            raise self.result
        return deepcopy(self.result)


@pytest.mark.parametrize(
    "authority_kind",
    (
        "INTERNAL_AGENT_BUDGET_REJECTED",
        "CANONICAL_CHECKPOINT_BUDGET_REJECTED",
    ),
)
def test_reconciliation_accepts_exact_internal_and_canonical_checkpoint_terminals(
    authority_kind: str,
) -> None:
    reader = _Reader(_resolved(authority_kind))

    exact = reader._exact_checkpoint_budget_rejection_terminal(  # noqa: SLF001
        None,
        observation=_observation(),
        selected_node=_node(),
        selected_attempt=_attempt(),
        selected_budget=_budget(),
    )

    assert exact is True
    assert reader.calls == [ATTEMPT_ID]


def test_reconciliation_missing_checkpoint_terminal_is_not_misclassified() -> None:
    reader = _Reader(None)

    exact = reader._exact_checkpoint_budget_rejection_terminal(  # noqa: SLF001
        None,
        observation=_observation(),
        selected_node=_node(),
        selected_attempt=_attempt(),
        selected_budget=_budget(),
    )

    assert exact is False


def test_reconciliation_checkpoint_terminal_identity_drift_fails_closed() -> None:
    resolved = _resolved("CANONICAL_CHECKPOINT_BUDGET_REJECTED")
    resolved["node"]["status_version"] = 99
    reader = _Reader(resolved)

    with pytest.raises(TaskStoreError) as exc_info:
        reader._exact_checkpoint_budget_rejection_terminal(  # noqa: SLF001
            None,
            observation=_observation(),
            selected_node=_node(),
            selected_attempt=_attempt(),
            selected_budget=_budget(),
        )

    assert exc_info.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_reconciliation_checkpoint_terminal_corruption_is_not_swallowed() -> None:
    corruption = TaskStoreError(
        TASK_STORE_DURABLE_DATA_INVALID,
        "checkpoint terminal Outbox authority drifted",
    )
    reader = _Reader(corruption)

    with pytest.raises(TaskStoreError) as exc_info:
        reader._exact_checkpoint_budget_rejection_terminal(  # noqa: SLF001
            None,
            observation=_observation(),
            selected_node=_node(),
            selected_attempt=_attempt(),
            selected_budget=_budget(),
        )

    assert exc_info.value is corruption


def test_extra_release_outbox_conflict_fails_closed_in_reconciliation() -> None:
    corruption = TaskStoreError(
        TASK_STORE_DURABLE_DATA_INVALID,
        "checkpoint budget terminal Outbox authority is ambiguous",
    )
    reader = _Reader(corruption)

    with pytest.raises(TaskStoreError) as exc_info:
        reader._exact_checkpoint_budget_rejection_terminal(  # noqa: SLF001
            None,
            observation=_observation(),
            selected_node=_node(),
            selected_attempt=_attempt(),
            selected_budget=_budget(),
        )

    assert exc_info.value is corruption
