"""TEST-ONLY Task Runtime persistence fixtures.

These helpers deliberately borrow private ``DurableTaskStore`` transaction
hooks so Store-transition and historical-corruption tests can seed raw/orphan
rows without restoring a public production write authority.  They do not write
``host_command_journal`` and therefore never create a canonical admission;
production admission must use ``HostCommandRepository.admit``.  Production
code must never import this module.
"""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
    DurableTaskStore,
    OutboxMessage,
)


def seed_raw_task_graph_for_test(
    store: DurableTaskStore,
    graph: TaskGraph,
    *,
    outbox: OutboxMessage,
) -> dict[str, Any]:
    """Seed an orphan TaskStore graph for transition/recovery tests only."""

    ts_ms = store._now()
    with store._transaction() as conn:
        store._insert_graph_from_host_command_in_transaction(
            conn,
            graph,
            outbox=outbox,
            ts_ms=ts_ms,
        )
    return store.get_graph(graph.graph_id, graph.revision)


def seed_raw_acceptance_effect_for_test(
    store: DurableTaskStore,
    *,
    effect_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    commit_scope: AcceptanceEffectCommitScope | str,
    effect_type: str,
    payload_ref: str,
    payload: Mapping[str, Any],
    payload_sha256: str,
    idempotency_key: str,
    allow_terminal_graph_insert_for_test: bool = False,
) -> dict[str, Any]:
    """Seed a pre-stage/historical Effect outside canonical Acceptance."""

    store.assert_acceptance_effect_intent_ready()
    spec = store._normalize_acceptance_effect_intent_spec(
        AcceptanceEffectIntentSpec(
            effect_id=effect_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            commit_scope=commit_scope,
            effect_type=effect_type,
            payload_ref=payload_ref,
            payload=payload,
            payload_sha256=payload_sha256,
            idempotency_key=idempotency_key,
        )
    )
    ts_ms = store._now()
    with store._transaction() as conn:
        store._assert_graph_not_cancelling(
            conn,
            graph_id=spec.graph_id,
            graph_revision=spec.graph_revision,
        )
        return store._stage_acceptance_effect_intent_in_transaction(
            conn,
            spec,
            ts_ms=ts_ms,
            allow_terminal_graph_insert=bool(
                allow_terminal_graph_insert_for_test
            ),
        )


def seed_active_graph_budget_for_test(
    store: DurableTaskStore,
    *,
    graph_id: str,
    graph_revision: int,
    budget_id: str,
    scope: str,
    outbox: OutboxMessage,
    limits: BudgetAmount | None = None,
) -> dict[str, Any]:
    """Create the one ACTIVE graph Budget required by execution authority."""

    amount = limits or BudgetAmount(token_count=1_000_000)
    return store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=scope,
            limits=amount,
            reserved=amount,
            status=BudgetReservationStatus.ACTIVE,
            status_version=0,
        ),
        graph_id=graph_id,
        graph_revision=graph_revision,
        outbox=outbox,
    )


__all__ = [
    "seed_raw_task_graph_for_test",
    "seed_raw_acceptance_effect_for_test",
    "seed_active_graph_budget_for_test",
]
