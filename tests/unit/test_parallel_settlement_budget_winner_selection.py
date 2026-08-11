from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import pytest

import host_brain.persistence.task_decision_integrity as decision_integrity
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_budget_usage_lineage,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TaskStoreError,
)
from host_brain.task_runtime.admission import AdmittedExecution
from host_brain.task_runtime.budget_runtime import BudgetRuntimeContext
from host_brain.task_runtime.coordinator_records import _ParallelNodeOutcome
from host_brain.task_runtime.errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from host_brain.task_runtime.parallel_settlement_projection import (
    ParallelSettlementProjection,
)


class _SnapshotStore:
    def __init__(self, usages: list[dict[str, Any]]) -> None:
        self._usages = usages

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        assert graph_id == "graph-selection"
        assert revision == 1
        return {"budget_usage": self._usages}


def _admitted() -> AdmittedExecution:
    return AdmittedExecution(
        command_id="command-selection",
        work_order_id="work-order-selection",
        assignment_id="assignment-selection",
        executor="canonical",
        graph_id="graph-selection",
        graph_revision=1,
        nodes=(),
        business_scope="scope:selection",
    )


def _budget() -> BudgetRuntimeContext:
    policy = BudgetPolicy(
        scope="scope:selection",
        limits=BudgetAmount(token_count=1),
    )
    return BudgetRuntimeContext(
        budget_id="budget-selection",
        command_id="command-selection",
        graph_id="graph-selection",
        graph_revision=1,
        policy=policy,
        record={"status": "EXCEEDED", "status_version": 1},
    )


def _outcome(node_id: str, attempt_id: str) -> _ParallelNodeOutcome:
    return _ParallelNodeOutcome(
        node_id=node_id,
        attempt_id=attempt_id,
        status="FAILED",
        accepted=False,
        reason_code="WORKER_FAILED",
        event={
            "execution_receipt": {
                "node_id": node_id,
                "attempt_id": attempt_id,
                "status": "FAILED",
                "accepted": False,
                "reason_code": "WORKER_FAILED",
                "evidence_refs": [],
            }
        },
    )


def _usage(
    *,
    usage_id: str,
    node_id: str,
    attempt_id: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "budget_id": "budget-selection",
        "usage_id": usage_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "accepted": False,
        "reason_code": reason_code,
        "exceeded_dimensions": ["token_count"],
        "reservation_version_after": 1,
        "created_ts_ms": 100,
    }


@pytest.mark.asyncio
async def test_projection_selects_winner_not_lexically_earlier_passive_sibling() -> None:
    winner_ref = "budget-usage://zzz-winner"
    sibling_ref = "budget-usage://aaa-passive"
    winner = _usage(
        usage_id=winner_ref,
        node_id="winner-node",
        attempt_id="attempt-winner",
        reason_code=TASK_STORE_BUDGET_EXCEEDED,
    )
    sibling = _usage(
        usage_id=sibling_ref,
        node_id="sibling-node",
        attempt_id="attempt-sibling",
        reason_code=TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    )
    projection = ParallelSettlementProjection(
        task_store=_SnapshotStore([sibling, winner]),  # type: ignore[arg-type]
    )

    selected = await projection.durable_budget_blocked_outcome(
        admitted=_admitted(),
        outcomes=(
            _outcome("sibling-node", "attempt-sibling"),
            _outcome("winner-node", "attempt-winner"),
        ),
        budget=_budget(),
    )

    assert selected.node_id == "winner-node"
    evidence = selected.event["execution_receipt"]["evidence_refs"]
    assert winner_ref in evidence
    assert sibling_ref not in evidence


@pytest.mark.asyncio
async def test_projection_rejects_multiple_terminal_winner_authorities() -> None:
    usages = [
        _usage(
            usage_id=f"budget-usage://winner-{index}",
            node_id=f"node-{index}",
            attempt_id=f"attempt-{index}",
            reason_code=TASK_STORE_BUDGET_EXCEEDED,
        )
        for index in range(2)
    ]
    projection = ParallelSettlementProjection(
        task_store=_SnapshotStore(usages),  # type: ignore[arg-type]
    )
    with pytest.raises(GovernedRuntimeError) as invalid:
        await projection.durable_budget_blocked_outcome(
            admitted=_admitted(),
            outcomes=tuple(
                _outcome(f"node-{index}", f"attempt-{index}")
                for index in range(2)
            ),
            budget=_budget(),
        )
    assert invalid.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


def _ledger_row(
    *,
    usage_id: str,
    node_id: str,
    attempt_id: str,
    reason_code: str,
    outbox_id: str,
) -> tuple[Any, ...]:
    delta_json = json.dumps(
        BudgetAmount(token_count=2).to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    delta_digest = "sha256:" + hashlib.sha256(delta_json.encode("utf-8")).hexdigest()
    terminal_sibling = reason_code == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    return (
        "budget-selection",
        usage_id,
        "graph-selection",
        1,
        node_id,
        attempt_id,
        "WORKER_USAGE",
        delta_json,
        delta_digest,
        0,
        reason_code,
        '["token_count"]',
        1 if terminal_sibling else 0,
        1,
        outbox_id,
        100,
    )


def test_decision_integrity_accepts_only_winner_evidence_not_passive_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE task_runtime_budget_usage(
            budget_id TEXT, usage_id TEXT, graph_id TEXT, graph_revision INTEGER,
            node_id TEXT, attempt_id TEXT, charge_kind TEXT, delta_json TEXT,
            delta_digest TEXT, accepted INTEGER, reason_code TEXT,
            exceeded_dimensions_json TEXT, reservation_version_before INTEGER,
            reservation_version_after INTEGER, outbox_id TEXT, created_ts_ms INTEGER
        )
        """
    )
    conn.execute(
        "CREATE TABLE task_runtime_budget_reservation(budget_id TEXT PRIMARY KEY)"
    )
    conn.execute(
        "INSERT INTO task_runtime_budget_reservation VALUES (?)",
        ("budget-selection",),
    )
    winner_ref = "budget-usage://zzz-winner"
    sibling_ref = "budget-usage://aaa-passive"
    conn.executemany(
        """
        INSERT INTO task_runtime_budget_usage VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            _ledger_row(
                usage_id=winner_ref,
                node_id="winner-node",
                attempt_id="attempt-winner",
                reason_code=TASK_STORE_BUDGET_EXCEEDED,
                outbox_id="outbox::winner",
            ),
            _ledger_row(
                usage_id=sibling_ref,
                node_id="sibling-node",
                attempt_id="attempt-sibling",
                reason_code=TASK_STORE_BUDGET_ALREADY_EXCEEDED,
                outbox_id="outbox::winner",
            ),
        ),
    )
    winner_payload = {
        "decision_kind": "BUDGET",
        "budget_id": "budget-selection",
        "budget_status_version_before": 1,
        "graph_id": "graph-selection",
        "graph_revision": 1,
        "selected_node_id": "winner-node",
        "outcomes": [
            {
                "node_id": "winner-node",
                "attempt_id": "attempt-winner",
                "evidence_refs": [winner_ref],
            }
        ],
    }
    monkeypatch.setattr(
        decision_integrity,
        "_assert_terminal_budget_authority",
        lambda *_args, **_kwargs: (
            {"usage_id": winner_ref},
            ("token_count",),
        ),
    )
    _assert_parallel_budget_usage_lineage(
        conn,
        winner_payload,
        reason_code="TEST_INVALID",
    )

    sibling_payload = {
        **winner_payload,
        "selected_node_id": "sibling-node",
        "outcomes": [
            {
                "node_id": "sibling-node",
                "attempt_id": "attempt-sibling",
                "evidence_refs": [sibling_ref],
            }
        ],
    }
    with pytest.raises(TaskStoreError) as invalid:
        _assert_parallel_budget_usage_lineage(
            conn,
            sibling_payload,
            reason_code="TEST_INVALID",
        )
    assert invalid.value.reason_code == "TEST_INVALID"
    conn.close()
