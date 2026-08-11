from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.task_runtime import _INFRASTRUCTURE_ORDER
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.task_checkpoint_budget_terminal_read_store import (
    TaskCheckpointBudgetTerminalReadStoreMixin,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_checkpoint_budget_route_authority import (
    canonical_checkpoint_route_authority_matches,
    requires_internal_agent_authority,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_NOT_FOUND,
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
    WorkerProjectionStoreError,
)
from host_brain.task_runtime import internal_agent_sealed_checkpoint_recovery
from host_brain.task_runtime import internal_checkpoint_budget_rejection
from host_brain.task_runtime import dag_scheduling_completion_replay
from host_brain.task_runtime.internal_checkpoint_budget_rejection import (
    InternalCheckpointBudgetRejectionRuntime,
)
from host_brain.task_runtime.ports import (
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
)


NOW = 1_800_000_000_000


def _checkpoint_terminal_shape_connection(
    release_outboxes: tuple[str, ...],
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE task_runtime_graph (
            graph_id TEXT, revision INTEGER, status TEXT
        );
        CREATE TABLE task_runtime_node (
            graph_id TEXT, graph_revision INTEGER, node_id TEXT, status TEXT
        );
        CREATE TABLE task_runtime_attempt (
            attempt_id TEXT, graph_id TEXT, graph_revision INTEGER,
            node_id TEXT, status TEXT
        );
        CREATE TABLE task_runtime_idempotency_reservation (
            attempt_id TEXT, status TEXT
        );
        CREATE TABLE task_runtime_budget_reservation (
            budget_id TEXT, graph_id TEXT, graph_revision INTEGER,
            node_id TEXT, attempt_id TEXT, status TEXT
        );
        CREATE TABLE task_runtime_budget_usage (
            budget_id TEXT, graph_id TEXT, graph_revision INTEGER,
            node_id TEXT, attempt_id TEXT, accepted INTEGER, reason_code TEXT
        );
        CREATE TABLE task_runtime_outbox (
            outbox_id TEXT, attempt_id TEXT, event_type TEXT
        );
        INSERT INTO task_runtime_graph VALUES ('graph-shape', 1, 'BLOCKED');
        INSERT INTO task_runtime_node
            VALUES ('graph-shape', 1, 'node-shape', 'FAILED');
        INSERT INTO task_runtime_attempt
            VALUES ('attempt-shape', 'graph-shape', 1, 'node-shape', 'FAILED');
        INSERT INTO task_runtime_idempotency_reservation
            VALUES ('attempt-shape', 'COMMITTED');
        INSERT INTO task_runtime_budget_reservation
            VALUES ('budget-shape', 'graph-shape', 1, NULL, NULL, 'RELEASED');
        INSERT INTO task_runtime_budget_usage
            VALUES (
                'budget-shape', 'graph-shape', 1, 'node-shape',
                'attempt-shape', 0, 'TASK_STORE_BUDGET_EXCEEDED'
            );
        """
    )
    conn.executemany(
        "INSERT INTO task_runtime_outbox VALUES (?, ?, ?)",
        tuple(
            (item, "attempt-shape", "BUDGET_RESERVATION_RELEASED")
            for item in release_outboxes
        ),
    )
    return conn


def _migrated(path: Path) -> Path:
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id=f"checkpoint-uow::{path.stem}",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return path


def _projection() -> tuple[str, dict[str, object]]:
    body: dict[str, object] = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": "command-checkpoint-uow",
        "request_id": "request-checkpoint-uow",
        "narrative": "canonical checkpoint",
        "route_type": "delegate",
        "target_id": "agent_runtime",
        "graph_id": "graph-checkpoint-uow",
        "graph_revision": 1,
        "node_id": "root",
        "attempt_id": "attempt-checkpoint-uow",
        "node_grant_ref": "governor-grant://checkpoint-uow/root",
        "audit_refs": {"checkpoint": "audit://checkpoint-uow/root"},
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    projection_id = "worker-projection://checkpoint-uow/" + digest.split(":", 1)[1]
    return projection_id, {
        **body,
        "projection_id": projection_id,
        "payload_digest": digest,
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


def _response_loss_attempt_outbox() -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="TASK_ATTEMPT",
        aggregate_id="attempt-checkpoint-response-loss",
        aggregate_version=2,
        event_type="TASK_ATTEMPT_FAILED",
        payload_ref="taskstore://event/TASK_ATTEMPT/attempt-checkpoint-response-loss/2",
        attempt_id="attempt-checkpoint-response-loss",
        evidence_refs=(
            "taskerror://checkpoint-response-loss",
            "usage://checkpoint-response-loss",
        ),
    )


def test_checkpoint_mutation_authority_is_not_exposed_by_generic_task_store() -> None:
    mutation_names = {
        "commit_internal_agent_recovered_checkpoint",
        "reject_internal_agent_checkpoint_budget_bundle",
    }
    assert not mutation_names.intersection(vars(TaskStorePort))
    assert not any(hasattr(DurableTaskStore, name) for name in mutation_names)
    assert mutation_names.issubset(vars(InternalAgentCheckpointUnitOfWorkPort))
    assert hasattr(DurableTaskStore, "_commit_internal_agent_recovered_checkpoint_uow")
    assert hasattr(
        DurableTaskStore,
        "_reject_internal_agent_checkpoint_budget_bundle_uow",
    )


def test_worker_projection_insert_has_one_private_connection_scoped_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    production = root / "host_brain"
    owners: list[Path] = []
    for path in production.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "INSERT INTO task_runtime_worker_projection" in source:
            owners.append(path)
        elif path.name == "worker_projection_repository.py" and source.count(
            "INSERT INTO {_TABLE}"
        ):
            owners.append(path)
    assert [path.relative_to(root).as_posix() for path in owners] == [
        "host_brain/persistence/worker_projection_repository.py"
    ]
    source = owners[0].read_text(encoding="utf-8")
    assert source.count("INSERT INTO {_TABLE}") == 1
    hook = inspect.getsource(WorkerProjectionStore._stage_in_transaction)
    assert "conn.in_transaction" in hook
    assert "BEGIN" not in hook and ".commit(" not in hook and ".rollback(" not in hook
    hook_callers = {
        path.name
        for path in production.rglob("*.py")
        if "._stage_in_transaction(" in path.read_text(encoding="utf-8")
    }
    assert hook_callers == {
        "task_internal_agent_checkpoint_commit_store.py",
        "worker_projection_repository.py",
    }


def test_private_task_store_checkpoint_hooks_only_have_the_canonical_adapter_caller() -> None:
    root = Path(__file__).resolve().parents[2]
    production = root / "host_brain"
    private_hooks = {
        "_commit_internal_agent_recovered_checkpoint_uow",
        "_reject_internal_agent_checkpoint_budget_bundle_uow",
        "_reopen_internal_agent_budget_rejection_mutation",
    }
    for hook_name in private_hooks:
        callers = {
            path.relative_to(root).as_posix()
            for path in production.rglob("*.py")
            if f".{hook_name}(" in path.read_text(encoding="utf-8")
        }
        assert callers == {"host_brain/persistence/internal_agent_checkpoint_uow.py"}


def test_checkpoint_uow_rejects_different_physical_databases(tmp_path: Path) -> None:
    task_path = _migrated(tmp_path / "task.sqlite3")
    projection_path = _migrated(tmp_path / "projection.sqlite3")
    with pytest.raises(TaskStoreError, match="different databases"):
        DurableInternalAgentCheckpointUnitOfWork(
            task_store=DurableTaskStore(task_path),
            worker_projection_store=WorkerProjectionStore(projection_path),
        )


def test_checkpoint_uow_rejects_repository_substitution(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path / "exact.sqlite3")

    class SubstitutedTaskStore(DurableTaskStore):
        pass

    with pytest.raises(TaskStoreError, match="exact DurableTaskStore"):
        DurableInternalAgentCheckpointUnitOfWork(
            task_store=SubstitutedTaskStore(db_path),
            worker_projection_store=WorkerProjectionStore(db_path),
        )


@pytest.mark.parametrize(
    "release_outboxes",
    (
        (),
        ("outbox::foreign-checkpoint-budget-release",),
    ),
)
def test_checkpoint_terminal_shape_without_known_release_authority_fails_closed(
    release_outboxes: tuple[str, ...],
) -> None:
    classifier = TaskCheckpointBudgetTerminalReadStoreMixin()
    conn = _checkpoint_terminal_shape_connection(release_outboxes)
    mutations_before = conn.total_changes
    try:
        with pytest.raises(TaskStoreError) as exc_info:
            classifier._try_resolve_checkpoint_budget_rejection_terminal_uow(  # noqa: SLF001
                conn,
                "attempt-shape",
            )
    finally:
        mutations_after = conn.total_changes
        conn.close()

    assert exc_info.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert "lacks exact Outbox authority" in str(exc_info.value)
    assert mutations_after == mutations_before


def test_ordinary_blocked_failure_without_checkpoint_shape_has_no_authority() -> None:
    classifier = TaskCheckpointBudgetTerminalReadStoreMixin()
    conn = _checkpoint_terminal_shape_connection(())
    conn.execute(
        "UPDATE task_runtime_idempotency_reservation SET status = 'RELEASED'"
    )
    mutations_before = conn.total_changes
    try:
        resolved = classifier._try_resolve_checkpoint_budget_rejection_terminal_uow(  # noqa: SLF001
            conn,
            "attempt-shape",
        )
    finally:
        mutations_after = conn.total_changes
        conn.close()

    assert resolved is None
    assert mutations_after == mutations_before


def test_extra_release_outbox_makes_checkpoint_authority_ambiguous() -> None:
    classifier = TaskCheckpointBudgetTerminalReadStoreMixin()
    conn = _checkpoint_terminal_shape_connection(
        (
            "outbox::committed-checkpoint-budget-rejected:budget-1:2",
            "outbox::foreign-checkpoint-budget-release",
        )
    )
    mutations_before = conn.total_changes
    try:
        with pytest.raises(TaskStoreError) as exc_info:
            classifier._try_resolve_checkpoint_budget_rejection_terminal_uow(  # noqa: SLF001
                conn,
                "attempt-shape",
            )
    finally:
        mutations_after = conn.total_changes
        conn.close()

    assert exc_info.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert "ambiguous" in str(exc_info.value)
    assert mutations_after == mutations_before


@pytest.mark.parametrize(
    ("method_name", "authority_kind"),
    (
        (
            "reopen_internal_agent_budget_rejection_terminal",
            "INTERNAL_AGENT_BUDGET_REJECTED",
        ),
        (
            "reopen_committed_checkpoint_budget_rejection_terminal",
            "CANONICAL_CHECKPOINT_BUDGET_REJECTED",
        ),
    ),
)
def test_response_loss_reopen_finishes_through_unified_exact_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    authority_kind: str,
) -> None:
    db_path = _migrated(tmp_path / "checkpoint-response-loss.sqlite3")
    task_store = DurableTaskStore(db_path)
    uow = DurableInternalAgentCheckpointUnitOfWork(
        task_store=task_store,
        worker_projection_store=WorkerProjectionStore(db_path),
    )
    calls: list[str] = []

    def call_bound_reopen(
        _store: object,
        attempt_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append("call-bound:" + attempt_id)
        return {"usage_id": "usage://checkpoint-response-loss"}

    def unified_resolver(_store: object, attempt_id: str) -> dict[str, object]:
        calls.append("unified:" + attempt_id)
        return {
            "authority_kind": authority_kind,
            "attempt": {
                "attempt_id": attempt_id,
                "status_version": 2,
                "error_ref": "taskerror://checkpoint-response-loss",
            },
            "node": {"status_version": 2},
            "graph": {"status_version": 2},
            "lease": {
                "lease_id": "lease-checkpoint-response-loss",
                "owner_id": "worker-checkpoint-response-loss",
                "fencing_token": 1,
                "status_version": 2,
            },
            "budget": {
                "budget_id": "budget-checkpoint-response-loss",
                "status_version": 2,
            },
            "reservation": {
                "reservation_id": "reservation-checkpoint-response-loss",
            },
            "usage": {"usage_id": "usage://checkpoint-response-loss"},
        }

    monkeypatch.setattr(
        DurableTaskStore,
        "_reopen_internal_agent_budget_rejection_mutation",
        call_bound_reopen,
    )
    monkeypatch.setattr(
        DurableTaskStore,
        "try_resolve_checkpoint_budget_rejection_terminal",
        unified_resolver,
    )
    result = getattr(uow, method_name)(
        "attempt-checkpoint-response-loss",
        reservation_id="reservation-checkpoint-response-loss",
        budget_id="budget-checkpoint-response-loss",
        lease_id="lease-checkpoint-response-loss",
        owner_id="worker-checkpoint-response-loss",
        fencing_token=1,
        expected_attempt_version=1,
        expected_node_version=1,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_lease_version=1,
        error_ref="taskerror://checkpoint-response-loss",
        usage_id="usage://checkpoint-response-loss",
        attempt_outbox=_response_loss_attempt_outbox(),
    )

    assert result["authority_kind"] == authority_kind
    assert result["replayed"] is True
    assert calls == [
        "call-bound:attempt-checkpoint-response-loss",
        "unified:attempt-checkpoint-response-loss",
    ]


def test_response_loss_reopen_rejects_conflicting_release_outbox_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _migrated(tmp_path / "checkpoint-response-loss-conflict.sqlite3")
    task_store = DurableTaskStore(db_path)
    uow = DurableInternalAgentCheckpointUnitOfWork(
        task_store=task_store,
        worker_projection_store=WorkerProjectionStore(db_path),
    )
    corruption = TaskStoreError(
        TASK_STORE_DURABLE_DATA_INVALID,
        "checkpoint budget terminal Outbox authority is ambiguous",
    )
    monkeypatch.setattr(
        DurableTaskStore,
        "_reopen_internal_agent_budget_rejection_mutation",
        lambda *_args, **_kwargs: {
            "usage_id": "usage://checkpoint-response-loss"
        },
    )
    monkeypatch.setattr(
        DurableTaskStore,
        "try_resolve_checkpoint_budget_rejection_terminal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(corruption),
    )

    with pytest.raises(TaskStoreError) as exc_info:
        uow.reopen_committed_checkpoint_budget_rejection_terminal(
            "attempt-checkpoint-response-loss",
            reservation_id="reservation-checkpoint-response-loss",
            budget_id="budget-checkpoint-response-loss",
            lease_id="lease-checkpoint-response-loss",
            owner_id="worker-checkpoint-response-loss",
            fencing_token=1,
            expected_attempt_version=1,
            expected_node_version=1,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_lease_version=1,
            error_ref="taskerror://checkpoint-response-loss",
            usage_id="usage://checkpoint-response-loss",
            attempt_outbox=_response_loss_attempt_outbox(),
        )

    assert exc_info.value is corruption


def test_projection_hook_requires_transaction_and_rolls_back_with_caller(
    tmp_path: Path,
) -> None:
    db_path = _migrated(tmp_path / "rollback.sqlite3")
    store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    projection_id, projection = _projection()
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(WorkerProjectionStoreError, match="active transaction"):
            store._stage_in_transaction(  # noqa: SLF001
                conn,
                projection_id=projection_id,
                projection=projection,
                created_ts_ms=NOW,
            )
        conn.execute("BEGIN IMMEDIATE")
        store._stage_in_transaction(  # noqa: SLF001
            conn,
            projection_id=projection_id,
            projection=projection,
            created_ts_ms=NOW,
        )
        conn.rollback()
    finally:
        conn.close()
    with pytest.raises(WorkerProjectionStoreError) as raised:
        store.get(projection_id)
    assert raised.value.reason_code == WORKER_PROJECTION_NOT_FOUND


def test_runtime_uses_explicit_uow_once_and_keeps_abc_budget_boundary() -> None:
    sealed_tree = ast.parse(inspect.getsource(internal_agent_sealed_checkpoint_recovery))
    sealed_calls = [
        node
        for node in ast.walk(sealed_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit_internal_agent_recovered_checkpoint"
    ]
    assert len(sealed_calls) == 1
    assert isinstance(sealed_calls[0].func.value, ast.Name)
    assert sealed_calls[0].func.value.id == "checkpoint_uow"

    rejection_tree = ast.parse(inspect.getsource(internal_checkpoint_budget_rejection))
    attributes = [
        node.attr
        for node in ast.walk(rejection_tree)
        if isinstance(node, ast.Attribute)
    ]
    assert attributes.count("reject_internal_agent_checkpoint_budget_bundle") == 1
    assert attributes.count("reject_committed_checkpoint_budget_bundle") == 1
    assert attributes.count("reopen_internal_agent_budget_rejection_terminal") == 1
    assert (
        attributes.count("reopen_committed_checkpoint_budget_rejection_terminal")
        == 1
    )
    calls = [
        node.func.attr
        for node in ast.walk(rejection_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert not {"consume_budget_once", "settle_worker_checkpoint"}.intersection(calls)
    selector = internal_checkpoint_budget_rejection.is_internal_agent_checkpoint_authority
    assert selector(
        SimpleNamespace(
            surface="internal",
            route_type="delegate",
            target_id="agent_runtime",
        )
    )
    assert not selector(
        SimpleNamespace(
            surface="internal",
            route_type="delegate",
            target_id="other_agent",
        )
    )
    internal_command = SimpleNamespace(
        surface="internal",
        route_type="delegate",
        target_id="agent_runtime",
    )
    assert internal_checkpoint_budget_rejection.requires_internal_terminal_bundle(
        SimpleNamespace(nodes=(object(),)),
        internal_command,
    )
    assert not internal_checkpoint_budget_rejection.requires_internal_terminal_bundle(
        SimpleNamespace(nodes=(object(), object())),
        internal_command,
    )
    assert internal_checkpoint_budget_rejection.is_internal_budget_terminal(
        SimpleNamespace(nodes=(object(),)),
        internal_command,
        {"status": "BUDGET_BLOCKED"},
    )
    assert not internal_checkpoint_budget_rejection.is_internal_budget_terminal(
        SimpleNamespace(nodes=(object(),)),
        internal_command,
        {"status": "FAILED"},
    )
    assert canonical_checkpoint_route_authority_matches(
        command_surface="chat",
        command_route="chat",
        command_target="root",
        node_route="chat:root",
        projection_route="chat",
        projection_target="root",
    )
    assert not canonical_checkpoint_route_authority_matches(
        command_surface="chat",
        command_route="chat",
        command_target="root",
        node_route="worker:root",
        projection_route="chat",
        projection_target="root",
    )
    assert not canonical_checkpoint_route_authority_matches(
        command_surface="chat",
        command_route="chat",
        command_target="root",
        node_route="delegate:agent_runtime",
        projection_route="chat",
        projection_target="root",
    )
    assert requires_internal_agent_authority(
        command_surface="chat",
        command_route="chat",
        command_target="root",
        node_route="delegate:agent_runtime",
        projection_route="chat",
        projection_target="root",
    )
    assert "internal_agent_checkpoint_uow" in _INFRASTRUCTURE_ORDER


@pytest.mark.asyncio
async def test_completed_internal_checkpoint_replay_requires_missing_parallel_decision() -> None:
    class _Store:
        def __init__(self, decision: object | None) -> None:
            self._decision = decision

        async def get_latest_parallel_success_decision(
            self,
            graph_id: str,
            graph_revision: int,
        ) -> object | None:
            assert (graph_id, graph_revision) == ("graph-replay", 1)
            return self._decision

    admitted = SimpleNamespace(
        graph_id="graph-replay",
        graph_revision=1,
        nodes=(object(),),
    )
    command = SimpleNamespace(
        surface="internal",
        route_type="delegate",
        target_id="agent_runtime",
    )
    replay_guard = (
        dag_scheduling_completion_replay.is_completed_internal_checkpoint_without_parallel_decision
    )
    assert await replay_guard(
        task_store=_Store(None),
        admitted=admitted,
        command=command,
        graph_status="COMPLETED",
    )
    assert not await replay_guard(
        task_store=_Store({"decision_ref": "parallel-success://present"}),
        admitted=admitted,
        command=command,
        graph_status="COMPLETED",
    )
    assert not await replay_guard(
        task_store=_Store(None),
        admitted=admitted,
        command=command,
        graph_status="ACTIVE",
    )


@pytest.mark.asyncio
async def test_runtime_preserves_durable_corruption_from_response_loss_reopen() -> None:
    calls: list[str] = []

    def reject(*_args: object, **_kwargs: object) -> None:
        calls.append("mutation")
        raise TimeoutError("mutation response was lost")

    def reopen(*_args: object, **_kwargs: object) -> None:
        calls.append("reopen")
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "checkpoint terminal Outbox authority is ambiguous",
        )

    runtime = InternalCheckpointBudgetRejectionRuntime(
        checkpoint_uow=SimpleNamespace(
            reject_committed_checkpoint_budget_bundle=reject,
            reopen_committed_checkpoint_budget_rejection_terminal=reopen,
        ),
        runtime_outbox=SimpleNamespace(
            build=lambda **_kwargs: _response_loss_attempt_outbox(),
        ),
        owner_id="worker-checkpoint-response-loss",
    )
    charge = SimpleNamespace(
        context=SimpleNamespace(
            budget_id="budget-checkpoint-response-loss",
            version=4,
        ),
        evidence_ref="budget-evidence://checkpoint-response-loss",
        usage_id="usage://checkpoint-response-loss",
        exceeded_dimensions=("token_count",),
    )

    with pytest.raises(TaskStoreError) as exc_info:
        await runtime.reject(
            admitted=SimpleNamespace(command_id="command-checkpoint-response-loss"),
            charge=charge,
            attempt={"attempt_id": "attempt-checkpoint-response-loss", "status_version": 1},
            node={"status_version": 1},
            graph={"status_version": 1},
            reservation={"reservation_id": "reservation-checkpoint-response-loss"},
            lease={"lease_id": "lease-checkpoint-response-loss", "status_version": 1},
            fencing_token=7,
            evidence_refs=("evidence://checkpoint-response-loss",),
            require_internal_agent=False,
        )

    assert exc_info.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert calls == ["mutation", "reopen"]
