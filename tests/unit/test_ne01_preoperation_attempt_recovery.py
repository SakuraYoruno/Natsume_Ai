from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

import pytest

from host_brain.task_runtime.admission import AdmittedExecution, AdmittedTaskNode
from host_brain.task_runtime.errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from host_brain.task_runtime.preoperation_attempt_recovery import (
    PreOperationRecoveryCandidate,
    acquire_preoperation_takeover_lease,
    classify_preoperation_recovery_candidates,
)


GRAPH_ID = "graph-preoperation"
GRAPH_REVISION = 1
NODE_ID = "node-root"
ASSIGNMENT_ID = "assignment-root"
BUSINESS_SCOPE = "task:preoperation"
OPERATION_FINGERPRINT = "sha256:operation-root"
BUDGET_ID = "budget-root"


def _expected_attempt_id(
    *,
    node_id: str = NODE_ID,
    attempt_no: int = 1,
) -> str:
    material = (
        f"{GRAPH_ID}@{GRAPH_REVISION}/{node_id}/{ASSIGNMENT_ID}/{attempt_no}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


ATTEMPT_ID = _expected_attempt_id()


def _node(node_id: str = NODE_ID, *, status: str = "RUNNING") -> dict[str, Any]:
    return {
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
        "node_id": node_id,
        "status": status,
        "status_version": 2,
    }


def _attempt(
    attempt_id: str = ATTEMPT_ID,
    *,
    node_id: str = NODE_ID,
    attempt_no: int = 1,
    status: str = "RUNNING",
    status_version: int = 7,
    owner_id: str | None = "owner-old",
    operation_scope: str | None = BUSINESS_SCOPE,
    operation_fingerprint: str | None = OPERATION_FINGERPRINT,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
        "node_id": node_id,
        "attempt_no": attempt_no,
        "status": status,
        "status_version": status_version,
        "owner_id": owner_id,
        "operation_scope": operation_scope,
        "operation_fingerprint": operation_fingerprint,
    }


def _lease(
    lease_id: str = "lease-old",
    *,
    attempt_id: str = ATTEMPT_ID,
    owner_id: str = "owner-old",
    fencing_token: int = 3,
    expires_ts_ms: int = 100,
) -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "attempt_id": attempt_id,
        "owner_id": owner_id,
        "fencing_token": fencing_token,
        "expires_ts_ms": expires_ts_ms,
        "status": "ACTIVE",
        "status_version": 0,
    }


def _budget(
    budget_id: str = BUDGET_ID,
    *,
    node_id: str | None = None,
    attempt_id: str | None = None,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    return {
        "budget_id": budget_id,
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "status": status,
        "status_version": 1,
    }


def _canonical_precharge_usage_id(
    *,
    budget_id: str = BUDGET_ID,
    node_id: str = NODE_ID,
    attempt_id: str = ATTEMPT_ID,
) -> str:
    digest = hashlib.sha256(
        f"{budget_id}\n{node_id}\n{attempt_id}\nworker-precharge".encode("utf-8")
    ).hexdigest()
    return f"budget-usage://{digest}"


def _budget_usage(
    usage_id: str | None = None,
    *,
    budget_id: str = BUDGET_ID,
    node_id: str = NODE_ID,
    attempt_id: str = ATTEMPT_ID,
    charge_kind: str = "PRECHARGE",
    accepted: Any = True,
) -> dict[str, Any]:
    return {
        "usage_id": usage_id or _canonical_precharge_usage_id(
            budget_id=budget_id,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
        "budget_id": budget_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "charge_kind": charge_kind,
        "accepted": accepted,
    }


def _snapshot(
    *,
    graph_status: str = "ACTIVE",
    nodes: list[dict[str, Any]] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    active_leases: list[dict[str, Any]] | None = None,
    budgets: list[dict[str, Any]] | None = None,
    budget_usage: list[dict[str, Any]] | None = None,
    observed_ts_ms: int = 100,
) -> dict[str, Any]:
    return {
        "observed_ts_ms": observed_ts_ms,
        "graph_record": {
            "graph_id": GRAPH_ID,
            "revision": GRAPH_REVISION,
            "status": graph_status,
            "nodes": list(nodes if nodes is not None else [_node()]),
        },
        "attempts": list(attempts if attempts is not None else [_attempt()]),
        "active_leases": list(active_leases or []),
        "budgets": list(budgets if budgets is not None else [_budget()]),
        "budget_usage": list(budget_usage or []),
    }


@pytest.mark.parametrize(
    (
        "graph_status",
        "node_status",
        "attempt_status",
        "lease_expires_ts_ms",
        "operation_free",
        "expected",
    ),
    (
        ("ACTIVE", "RUNNING", "RUNNING", None, True, True),
        ("ACTIVE", "RUNNING", "RECOVERABLE", None, True, True),
        ("ACTIVE", "LEASED", "LEASED", 100, True, True),
        ("ACTIVE", "RUNNING", "RUNNING", 101, True, False),
        ("CREATED", "RUNNING", "RUNNING", None, True, False),
        ("COMPLETED", "RUNNING", "RUNNING", None, True, False),
        ("ACTIVE", "READY", "CREATED", None, True, False),
        ("ACTIVE", "ACCEPTED", "SUCCEEDED", None, True, False),
        ("ACTIVE", "RUNNING", "RUNNING", None, False, False),
    ),
)
def test_classifier_covers_preoperation_state_matrix(
    graph_status: str,
    node_status: str,
    attempt_status: str,
    lease_expires_ts_ms: int | None,
    operation_free: bool,
    expected: bool,
) -> None:
    leases = (
        []
        if lease_expires_ts_ms is None
        else [_lease(expires_ts_ms=lease_expires_ts_ms)]
    )
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(
            graph_status=graph_status,
            nodes=[_node(status=node_status)],
            attempts=[_attempt(status=attempt_status)],
            active_leases=leases,
        ),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={NODE_ID} if operation_free else set(),
    )
    assert bool(candidates) is expected


def test_empty_operation_authority_has_a_strict_no_read_fast_path() -> None:
    assert classify_preoperation_recovery_candidates(
        {"observed_ts_ms": 100, "active_leases": []},
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids=set(),
    ) == ()


def test_classifier_selects_latest_contiguous_attempt_and_is_stably_sorted() -> None:
    alpha_attempt_1 = _attempt(
        "attempt-alpha-1",
        node_id="alpha",
        attempt_no=1,
        status="FAILED",
        status_version=4,
    )
    alpha_attempt_2 = _attempt(
        "attempt-alpha-2",
        node_id="alpha",
        attempt_no=2,
        status="RECOVERABLE",
        status_version=9,
        owner_id=None,
    )
    beta_attempt = _attempt(
        "attempt-beta-1",
        node_id="beta",
        status="RUNNING",
        status_version=5,
    )
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(
            nodes=[_node("beta"), _node("alpha")],
            attempts=[beta_attempt, alpha_attempt_2, alpha_attempt_1],
            active_leases=[
                _lease(
                    "lease-beta",
                    attempt_id="attempt-beta-1",
                    fencing_token=2,
                    expires_ts_ms=100,
                )
            ],
        ),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={"beta", "alpha"},
    )
    assert candidates == (
        PreOperationRecoveryCandidate(
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            node_id="alpha",
            node_status="RUNNING",
            attempt_id="attempt-alpha-2",
            attempt_no=2,
            attempt_status="RECOVERABLE",
            attempt_version=9,
            previous_lease_id=None,
            previous_fencing_token=None,
        ),
        PreOperationRecoveryCandidate(
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            node_id="beta",
            node_status="RUNNING",
            attempt_id="attempt-beta-1",
            attempt_no=1,
            attempt_status="RUNNING",
            attempt_version=5,
            previous_lease_id="lease-beta",
            previous_fencing_token=2,
        ),
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value["graph_record"].__setitem__("graph_id", "graph-tampered"),
        lambda value: value["graph_record"]["nodes"][0].__setitem__(
            "graph_revision", 2
        ),
        lambda value: value["attempts"][0].__setitem__("node_id", "node-tampered"),
        lambda value: value["attempts"].append(
            _attempt("attempt-root-3", attempt_no=3, status="RECOVERABLE")
        ),
        lambda value: value["active_leases"].append(
            _lease(attempt_id="attempt-unknown")
        ),
    ),
)
def test_classifier_fails_closed_on_durable_identity_tampering(mutate: Any) -> None:
    snapshot = _snapshot()
    mutate(snapshot)
    with pytest.raises(GovernedRuntimeError) as raised:
        classify_preoperation_recovery_candidates(
            snapshot,
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            operation_free_node_ids={NODE_ID},
        )
    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


def test_classifier_rejects_active_lease_on_a_superseded_attempt() -> None:
    snapshot = _snapshot(
        attempts=[
            _attempt("attempt-root-1", attempt_no=1, status="FAILED"),
            _attempt(
                "attempt-root-2",
                attempt_no=2,
                status="RECOVERABLE",
                status_version=8,
                owner_id=None,
            ),
        ],
        active_leases=[_lease(attempt_id="attempt-root-1", expires_ts_ms=100)],
    )
    with pytest.raises(GovernedRuntimeError, match="superseded Attempt"):
        classify_preoperation_recovery_candidates(
            snapshot,
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            operation_free_node_ids={NODE_ID},
        )


def test_classifier_accepts_active_budget_without_precharge() -> None:
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={NODE_ID},
    )
    assert [candidate.attempt_id for candidate in candidates] == [ATTEMPT_ID]


def test_classifier_accepts_active_budget_with_one_canonical_precharge() -> None:
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(budget_usage=[_budget_usage(accepted=True)]),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={NODE_ID},
    )
    assert [candidate.attempt_id for candidate in candidates] == [ATTEMPT_ID]


@pytest.mark.parametrize("accepted", (False, True))
def test_classifier_accepts_exceeded_budget_with_canonical_precharge(
    accepted: bool,
) -> None:
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(
            budgets=[_budget(status="EXCEEDED")],
            budget_usage=[_budget_usage(accepted=accepted)],
        ),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={NODE_ID},
    )
    assert [candidate.attempt_id for candidate in candidates] == [ATTEMPT_ID]


def test_classifier_rejects_single_node_exceeded_budget_without_precharge() -> None:
    with pytest.raises(GovernedRuntimeError, match="without a recoverable PRECHARGE"):
        classify_preoperation_recovery_candidates(
            _snapshot(budgets=[_budget(status="EXCEEDED")]),
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            operation_free_node_ids={NODE_ID},
        )


def test_classifier_accepts_multi_node_exceeded_budget_without_candidate_precharge() -> None:
    sibling_node_id = "node-sibling"
    sibling_attempt_id = "attempt-sibling-1"
    candidates = classify_preoperation_recovery_candidates(
        _snapshot(
            nodes=[_node(), _node(sibling_node_id, status="ACCEPTED")],
            attempts=[
                _attempt(),
                _attempt(
                    sibling_attempt_id,
                    node_id=sibling_node_id,
                    status="SUCCEEDED",
                ),
            ],
            budgets=[_budget(status="EXCEEDED")],
            budget_usage=[
                _budget_usage(
                    usage_id="budget-usage://sibling-exhaustion",
                    node_id=sibling_node_id,
                    attempt_id=sibling_attempt_id,
                    charge_kind="WORKER_USAGE:TOKEN",
                    accepted=False,
                )
            ],
        ),
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        operation_free_node_ids={NODE_ID},
    )
    assert [candidate.attempt_id for candidate in candidates] == [ATTEMPT_ID]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.__setitem__("budgets", []),
        lambda value: value["budgets"].append(_budget("budget-second")),
        lambda value: value["budgets"][0].__setitem__("node_id", NODE_ID),
        lambda value: value["budgets"][0].__setitem__("graph_id", "graph-tampered"),
        lambda value: value["budget_usage"].append(
            _budget_usage(usage_id="budget-usage://tampered")
        ),
        lambda value: value["budget_usage"][0].__setitem__(
            "budget_id", "budget-unknown"
        ),
        lambda value: value["budget_usage"][0].__setitem__(
            "node_id", "node-tampered"
        ),
        lambda value: value["budget_usage"][0].__setitem__(
            "attempt_id", "attempt-unknown"
        ),
        lambda value: value["budget_usage"][0].__setitem__("accepted", 1),
        lambda value: value["budget_usage"][0].__setitem__("accepted", False),
        lambda value: value["budget_usage"][0].__setitem__(
            "charge_kind", "WORKER_ELAPSED"
        ),
        lambda value: value["budget_usage"][0].__setitem__(
            "charge_kind", "RETRY"
        ),
        lambda value: value["budget_usage"].append(
            {
                **_budget_usage(charge_kind="WORKER_USAGE:TOKEN"),
                "usage_id": "budget-usage://worker",
            }
        ),
        lambda value: value["budget_usage"].append(deepcopy(value["budget_usage"][0])),
    ),
)
def test_classifier_fails_closed_on_budget_lineage_tampering(mutate: Any) -> None:
    snapshot = _snapshot(budget_usage=[_budget_usage()])
    mutate(snapshot)
    with pytest.raises(GovernedRuntimeError) as raised:
        classify_preoperation_recovery_candidates(
            snapshot,
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            operation_free_node_ids={NODE_ID},
        )
    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


@pytest.mark.parametrize(
    "status",
    ("PENDING", "RELEASED", "CONSUMED", "CANCELLED"),
)
def test_classifier_rejects_terminal_or_uncommitted_graph_budget(status: str) -> None:
    with pytest.raises(GovernedRuntimeError, match="ACTIVE or EXCEEDED"):
        classify_preoperation_recovery_candidates(
            _snapshot(budgets=[_budget(status=status)]),
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION,
            operation_free_node_ids={NODE_ID},
        )


class _StoreError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _Store:
    def __init__(
        self,
        *,
        previous: dict[str, Any] | None,
        claimed: dict[str, Any] | None,
        refreshed: dict[str, Any] | None,
        acquire_error: Exception | None = None,
        operation_results: list[dict[str, Any] | None] | None = None,
    ) -> None:
        self.previous = deepcopy(previous)
        self.claimed = deepcopy(claimed)
        self.refreshed = deepcopy(refreshed)
        self.acquire_error = acquire_error
        self.operation_results = list(operation_results or [None, None])
        self.acquire_calls: list[dict[str, Any]] = []

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        assert reservation_id == "reservation-root"
        result = self.operation_results.pop(0) if self.operation_results else None
        if result is None:
            raise _StoreError("TASK_STORE_NOT_FOUND")
        return deepcopy(result)

    def get_active_lease(self, attempt_id: str) -> dict[str, Any] | None:
        assert attempt_id == ATTEMPT_ID
        return deepcopy(self.previous)

    def acquire_lease(self, attempt_id: str, **kwargs: Any) -> dict[str, Any]:
        self.acquire_calls.append({"attempt_id": attempt_id, **kwargs})
        if self.acquire_error is not None:
            raise self.acquire_error
        assert self.claimed is not None
        return deepcopy(self.claimed)

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        assert attempt_id == ATTEMPT_ID
        assert self.refreshed is not None
        return deepcopy(self.refreshed)


class _Outbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"outbox": kwargs}


def _admitted() -> AdmittedExecution:
    admitted = AdmittedExecution(
        command_id="command-root",
        work_order_id="work-order-root",
        assignment_id=ASSIGNMENT_ID,
        executor="canonical",
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        nodes=(
            AdmittedTaskNode(
                node_id=NODE_ID,
                worker_route="chat:root",
                dependencies=(),
                acceptance_criteria=(),
                operation_fingerprint=OPERATION_FINGERPRINT,
                reservation_id="reservation-root",
            ),
        ),
        business_scope=BUSINESS_SCOPE,
        active_node_id=NODE_ID,
    )
    admitted.validate()
    return admitted


def _new_lease(*, fencing_token: int = 4, owner_id: str = "owner-new") -> dict[str, Any]:
    return _lease(
        "lease-new",
        owner_id=owner_id,
        fencing_token=fencing_token,
        expires_ts_ms=200,
    )


def _refreshed_attempt(**overrides: Any) -> dict[str, Any]:
    value = _attempt(status="LEASED", status_version=8, owner_id="owner-new")
    value.update(overrides)
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("attempt_id", "attempt::governed::tampered"),
        ("attempt_no", 2),
        ("operation_scope", "task:tampered"),
        ("operation_fingerprint", "sha256:tampered"),
    ),
)
async def test_takeover_rejects_noncanonical_attempt_authority(
    field_name: str,
    tampered_value: Any,
) -> None:
    attempt = _attempt(status="RECOVERABLE", owner_id=None)
    attempt[field_name] = tampered_value
    store = _Store(previous=None, claimed=None, refreshed=None)
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=attempt,
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
    assert store.acquire_calls == []


@pytest.mark.asyncio
async def test_takeover_reuses_same_attempt_and_advances_fence() -> None:
    store = _Store(
        previous=_lease(fencing_token=3),
        claimed=_new_lease(fencing_token=4),
        refreshed=_refreshed_attempt(),
    )
    outbox = _Outbox()
    refreshed, lease = await acquire_preoperation_takeover_lease(
        task_store=store,
        runtime_outbox=outbox,
        admitted=_admitted(),
        attempt=_attempt(status="RECOVERABLE", owner_id=None),
        owner_id="owner-new",
        lease_ttl_ms=50,
    )
    assert refreshed["attempt_id"] == ATTEMPT_ID
    assert refreshed["status"] == "LEASED"
    assert lease["fencing_token"] == 4
    assert store.acquire_calls == [
        {
            "attempt_id": ATTEMPT_ID,
            "owner_id": "owner-new",
            "ttl_ms": 50,
            "expected_attempt_version": 7,
            "absent_operation_reservation_id": "reservation-root",
            "outbox": {"outbox": outbox.calls[0]},
        }
    ]
    assert outbox.calls == [
        {
            "command_id": "command-root",
            "aggregate_type": "TASK_ATTEMPT",
            "aggregate_id": ATTEMPT_ID,
            "aggregate_version": 8,
            "event_type": "TASK_ATTEMPT_LEASED",
            "attempt_id": ATTEMPT_ID,
        }
    ]


@pytest.mark.asyncio
async def test_takeover_after_startup_recovery_requires_historical_fence() -> None:
    store = _Store(
        previous=None,
        claimed=_new_lease(fencing_token=2),
        refreshed=_refreshed_attempt(),
    )
    refreshed, lease = await acquire_preoperation_takeover_lease(
        task_store=store,
        runtime_outbox=_Outbox(),
        admitted=_admitted(),
        attempt=_attempt(status="RECOVERABLE", owner_id=None),
        owner_id="owner-new",
        lease_ttl_ms=50,
    )
    assert refreshed["attempt_id"] == ATTEMPT_ID
    assert lease["fencing_token"] == 2


@pytest.mark.asyncio
async def test_unexpired_foreign_owner_is_not_taken_over() -> None:
    store = _Store(
        previous=_lease(expires_ts_ms=999),
        claimed=None,
        refreshed=None,
        acquire_error=_StoreError("TASK_STORE_LEASE_CONFLICT"),
    )
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_EXECUTION_IN_PROGRESS


@pytest.mark.asyncio
async def test_same_owner_live_lease_replay_is_not_a_takeover() -> None:
    live = _lease(owner_id="owner-new", expires_ts_ms=999)
    store = _Store(previous=live, claimed=live, refreshed=None)
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(status="LEASED", owner_id="owner-new"),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_EXECUTION_IN_PROGRESS


@pytest.mark.asyncio
async def test_existing_operation_blocks_before_lease_mutation() -> None:
    store = _Store(
        previous=None,
        claimed=None,
        refreshed=None,
        operation_results=[{"reservation_id": "reservation-root", "status": "RESERVED"}],
    )
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(status="RECOVERABLE", owner_id=None),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED
    assert store.acquire_calls == []


@pytest.mark.asyncio
async def test_operation_race_after_takeover_fails_closed_before_worker_boundary() -> None:
    store = _Store(
        previous=None,
        claimed=_new_lease(fencing_token=2),
        refreshed=_refreshed_attempt(),
        operation_results=[
            None,
            {"reservation_id": "reservation-root", "status": "RECOVERY_REQUIRED"},
        ],
    )
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(status="RECOVERABLE", owner_id=None),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_atomic_operation_race_during_lease_takeover_maps_to_recovery() -> None:
    store = _Store(
        previous=None,
        claimed=None,
        refreshed=None,
        acquire_error=_StoreError("TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED"),
        operation_results=[None],
    )
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(status="RECOVERABLE", owner_id=None),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED
    assert store.acquire_calls[0]["absent_operation_reservation_id"] == (
        "reservation-root"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claimed_overrides", "refreshed_overrides"),
    (
        ({"attempt_id": "attempt-tampered"}, {}),
        ({"owner_id": "owner-tampered"}, {}),
        ({"fencing_token": 3}, {}),
        ({}, {"graph_id": "graph-tampered"}),
        ({}, {"node_id": "node-tampered"}),
        ({}, {"operation_scope": "task:tampered"}),
        ({}, {"operation_fingerprint": "sha256:tampered"}),
        ({}, {"status": "RUNNING"}),
        ({}, {"status_version": 9}),
    ),
)
async def test_takeover_fails_closed_on_returned_identity_tampering(
    claimed_overrides: dict[str, Any],
    refreshed_overrides: dict[str, Any],
) -> None:
    claimed = _new_lease()
    claimed.update(claimed_overrides)
    refreshed = _refreshed_attempt(**refreshed_overrides)
    store = _Store(
        previous=_lease(fencing_token=3),
        claimed=claimed,
        refreshed=refreshed,
    )
    with pytest.raises(GovernedRuntimeError) as raised:
        await acquire_preoperation_takeover_lease(
            task_store=store,
            runtime_outbox=_Outbox(),
            admitted=_admitted(),
            attempt=_attempt(status="RECOVERABLE", owner_id=None),
            owner_id="owner-new",
            lease_ttl_ms=50,
        )
    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
