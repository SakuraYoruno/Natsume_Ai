from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.graph_cancellation import (
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.orchestration.governed_command import GovernedCommandApplication
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.task_runtime import (
    AdmittedExecution,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
)
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


def _contracts() -> tuple[Any, dict[str, Any], ExecutorAssignment, TaskGraph]:
    graph = TaskGraph(
        graph_id="graph-b5c-cancel",
        root_work_order_id="wo-b5c-cancel",
        revision=1,
        planner_ref="planner://b5c/cancellation",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        result_node_id="node-one",
        nodes=(
            TaskNode(
                node_id="node-one",
                dependencies=(),
                route="executive_runtime",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-b5c-command",
        session_id="session-b5c",
        principal_id="principal-b5c",
        input_ref="command-input://request-b5c-command",
        input_sha256="1" * 64,
        root_work_order_id=graph.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id="executive_runtime",
        business_scope="host-chat-command",
        operation_fingerprint="2" * 64,
        created_at_ms=1,
    )
    assignment = ExecutorAssignment(
        work_order_id=graph.root_work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://wo-b5c-cancel/canonical",
        assigned_at_ms=1,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    work_order = {"work_order_id": graph.root_work_order_id}
    return command, work_order, assignment, graph


class _CommandRepository:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self.record = record
        self.get_calls = 0
        self.admit_calls = 0

    def get(self, command_id: str) -> dict[str, Any] | None:
        self.get_calls += 1
        if self.record is None:
            return None
        assert command_id == self.record["command"].command_id
        return dict(self.record)

    def admit(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.admit_calls += 1
        raise AssertionError("cancellation must not admit a Graph")


class _CoordinatorSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request_graph_cancellation(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"status": "forwarded"}


class _AuthorityVerifier:
    def __init__(self, **drift: Any) -> None:
        self.drift = drift
        self.calls: list[dict[str, Any]] = []

    def verify_graph_cancellation(self, **binding: Any) -> dict[str, Any]:
        self.calls.append(dict(binding))
        return {
            **binding,
            **self.drift,
            "principal_id": "principal-verified",
        }


def _application_record() -> tuple[dict[str, Any], tuple[Any, ...]]:
    contracts = _contracts()
    command, work_order, assignment, graph = contracts
    return {
        "command": command,
        "work_order": work_order,
        "assignment": assignment,
        "graph": graph,
        "created": True,
        "replayed": False,
    }, contracts


def _cancel_arguments(command: Any, graph: TaskGraph) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "request_id": "request-b5c-cancel",
        "request_identity": "authority://verified/principal-b5c",
        "source": "USER",
        "reason_code": "USER_REQUESTED",
    }


@pytest.mark.asyncio
async def test_cancellation_without_verifier_never_admits_or_writes() -> None:
    record, (command, _, _, graph) = _application_record()
    repository = _CommandRepository(record)
    coordinator = _CoordinatorSpy()
    application = GovernedCommandApplication(
        command_repository=repository,
        coordinator=coordinator,  # type: ignore[arg-type]
    )

    with pytest.raises(GovernedRuntimeError) as error:
        await application.request_graph_cancellation(
            **_cancel_arguments(command, graph)
        )

    assert error.value.reason_code == "GOVERNED_CANCELLATION_AUTHORITY_REQUIRED"
    assert repository.admit_calls == 0
    assert coordinator.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    (
        {"request_identity": "authority://verified/someone-else"},
        {"graph_id": "graph-other"},
        {"graph_revision": 2},
    ),
)
async def test_verified_cancellation_binding_drift_never_reaches_intent(
    drift: dict[str, Any],
) -> None:
    record, (command, _, _, graph) = _application_record()
    repository = _CommandRepository(record)
    coordinator = _CoordinatorSpy()
    verifier = _AuthorityVerifier(**drift)
    application = GovernedCommandApplication(
        command_repository=repository,
        coordinator=coordinator,  # type: ignore[arg-type]
        cancellation_authority_verifier=verifier,
    )

    with pytest.raises(GovernedRuntimeError) as error:
        await application.request_graph_cancellation(
            **_cancel_arguments(command, graph)
        )

    assert error.value.reason_code == "GOVERNED_CANCELLATION_AUTHORITY_INVALID"
    assert repository.admit_calls == 0
    assert coordinator.calls == []


@pytest.mark.asyncio
async def test_cancellation_target_must_be_an_existing_durable_admission() -> None:
    _, (command, _, _, graph) = _application_record()
    repository = _CommandRepository(None)
    coordinator = _CoordinatorSpy()
    application = GovernedCommandApplication(
        command_repository=repository,
        coordinator=coordinator,  # type: ignore[arg-type]
        cancellation_authority_verifier=_AuthorityVerifier(),
    )

    with pytest.raises(GovernedRuntimeError) as error:
        await application.request_graph_cancellation(
            **_cancel_arguments(command, graph)
        )

    assert error.value.reason_code == "GOVERNED_ADMISSION_INVALID"
    assert repository.admit_calls == 0
    assert coordinator.calls == []


class _OutboxFactory:
    @staticmethod
    def create(**kwargs: Any) -> OutboxMessage:
        return OutboxMessage.create(**kwargs)


class _Publisher:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def publish_pending(self, *, limit: int) -> dict[str, Any]:
        self.order.append(f"publish:{limit}")
        return {"status": "OK"}


class _FailingPublisher(_Publisher):
    def publish_pending(self, *, limit: int) -> dict[str, Any]:
        self.order.append(f"publish-down:{limit}")
        raise RuntimeError("cancellation Outbox publisher is unavailable")


class _CancellationStore:
    def __init__(
        self,
        graph: TaskGraph,
        order: list[str],
        *,
        ready_to_finalize: bool,
        reconciliation_evidence_refs: tuple[str, ...] | None = None,
    ) -> None:
        node = graph.nodes[0]
        self.order = order
        self.ready_to_finalize = ready_to_finalize
        self.reconciliation_evidence_refs = reconciliation_evidence_refs
        self.intent: dict[str, Any] | None = None
        self.decision: dict[str, Any] | None = None
        self.last_finalize_arguments: dict[str, Any] | None = None
        self.request_calls = 0
        self.advance_calls = 0
        self.finalize_calls = 0
        self.graph = {
            "graph_id": graph.graph_id,
            "revision": graph.revision,
            "root_work_order_id": graph.root_work_order_id,
            "status": graph.status.value,
            "status_version": 0,
            "graph": graph.to_dict(),
        }
        self.node = {
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": node.node_id,
            "route": node.route,
            "status": node.status.value,
            "status_version": node.status_version,
            "node": node.to_dict(),
        }

    def assert_graph_cancellation_ready(self) -> None:
        return None

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        assert (graph_id, revision) == (
            self.graph["graph_id"],
            self.graph["revision"],
        )
        return deepcopy(self.graph)

    def get_node(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
    ) -> dict[str, Any]:
        assert (graph_id, revision, node_id) == (
            self.graph["graph_id"],
            self.graph["revision"],
            self.node["node_id"],
        )
        return deepcopy(self.node)

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any]:
        assert (graph_id, revision) == (
            self.graph["graph_id"],
            self.graph["revision"],
        )
        return {"attempts": [], "active_leases": [], "budgets": []}

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        assert (graph_id, revision) == (
            self.graph["graph_id"],
            self.graph["revision"],
        )
        return deepcopy(self.intent)

    def request_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        **arguments: Any,
    ) -> dict[str, Any]:
        assert self.intent is None
        assert (graph_id, revision) == (
            self.graph["graph_id"],
            self.graph["revision"],
        )
        intent = normalize_graph_cancellation_intent(arguments["intent_payload"])
        assert arguments["intent_ref"] == intent.intent_ref
        self.order.append("intent")
        self.request_calls += 1
        self.graph["status"] = "BLOCKED"
        self.graph["status_version"] += 1
        self.graph["graph"]["status"] = "BLOCKED"
        target = intent.payload["node_states_after"][self.node["node_id"]]
        self.node["status"] = target["status"]
        self.node["status_version"] = target["status_version"]
        self.node["node"]["status"] = target["status"]
        self.node["node"]["status_version"] = target["status_version"]
        self.intent = {
            "intent_ref": intent.intent_ref,
            "payload": intent.payload,
            "payload_json": intent.payload_json,
            "payload_sha256": intent.payload_sha256,
            "status": "REQUESTED",
            "status_version": 0,
        }
        return {
            "graph": deepcopy(self.graph),
            "nodes": (deepcopy(self.node),),
            "budget": None,
            "intent": deepcopy(self.intent),
            "replayed": False,
        }

    def advance_graph_cancellation(self, intent_ref: str) -> dict[str, Any]:
        assert self.intent is not None
        assert intent_ref == self.intent["intent_ref"]
        self.order.append("advance")
        self.advance_calls += 1
        return {
            "intent": deepcopy(self.intent),
            "cancelled_attempt_ids": (),
            "pending_attempt_ids": (() if self.ready_to_finalize else ("attempt-one",)),
            "pending_operation_ids": (() if self.ready_to_finalize else ("operation-one",)),
            "pending_effect_ids": (),
            "reconciliation_evidence_refs": (
                tuple(
                    dict.fromkeys(
                        (intent_ref, *self.reconciliation_evidence_refs)
                    )
                )
                if self.reconciliation_evidence_refs is not None
                else (intent_ref,)
            ),
            "ready_to_finalize": self.ready_to_finalize,
            "replayed": False,
        }

    def finalize_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        **arguments: Any,
    ) -> dict[str, Any]:
        assert self.intent is not None
        self.last_finalize_arguments = dict(arguments)
        decision = normalize_graph_cancellation_decision(
            arguments["decision_payload"]
        )
        assert arguments["decision_ref"] == decision.decision_ref
        assert (graph_id, revision) == (
            self.graph["graph_id"],
            self.graph["revision"],
        )
        self.order.append("finalize")
        self.finalize_calls += 1
        target = decision.payload["node_states_after"][self.node["node_id"]]
        self.node["status"] = target["status"]
        self.node["status_version"] = target["status_version"]
        self.node["node"]["status"] = target["status"]
        self.node["node"]["status_version"] = target["status_version"]
        self.graph["status"] = "CANCELLED"
        self.graph["status_version"] += 1
        self.graph["graph"]["status"] = "CANCELLED"
        self.intent = {**self.intent, "status": "FINALIZED", "status_version": 1}
        self.decision = {
            "decision_ref": decision.decision_ref,
            "intent_ref": decision.payload["intent_ref"],
            "payload": decision.payload,
            "payload_json": decision.payload_json,
            "payload_sha256": decision.payload_sha256,
        }
        return {
            "graph": deepcopy(self.graph),
            "nodes": (deepcopy(self.node),),
            "budget": None,
            "intent": deepcopy(self.intent),
            "decision": deepcopy(self.decision),
            "replayed": False,
        }

    def get_graph_cancellation_decision_for_intent(
        self,
        intent_ref: str,
    ) -> dict[str, Any] | None:
        if self.decision is None:
            return None
        assert self.decision["intent_ref"] == intent_ref
        return deepcopy(self.decision)


def _coordinator(
    store: _CancellationStore,
    order: list[str],
    *,
    coordinator_type: type[GovernedTaskCoordinator] = GovernedTaskCoordinator,
    outbox_publisher: Any | None = None,
    execution_registry: LocalGraphExecutionRegistry | None = None,
) -> GovernedTaskCoordinator:
    coordinator = coordinator_type(
        task_store=store,  # type: ignore[arg-type]
        outbox_publisher=(
            outbox_publisher
            if outbox_publisher is not None
            else _Publisher(order)
        ),
        outbox_factory=_OutboxFactory(),
        worker_resolver=object(),  # type: ignore[arg-type]
        verifier=object(),  # type: ignore[arg-type]
        acceptance=object(),  # type: ignore[arg-type]
        execution_registry=execution_registry,
        owner_id="worker-b5c",
    )
    if isinstance(coordinator, _BlockingCoordinator):
        coordinator.order = order
    return coordinator


class _BlockingDagScheduling:
    def __init__(self, coordinator: "_BlockingCoordinator") -> None:
        self._coordinator = coordinator

    async def execute_stream(self, **kwargs: Any):
        del kwargs
        coordinator = self._coordinator
        coordinator.execution_started.set()
        try:
            await coordinator.execution_gate.wait()
        finally:
            coordinator.order.append("local-cancel")
        if False:
            yield {}


class _BlockingCoordinator(GovernedTaskCoordinator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.execution_started = asyncio.Event()
        self.execution_gate = asyncio.Event()
        self.order: list[str] = []
        self._dag_scheduling = _BlockingDagScheduling(self)


async def _drain_execution(
    coordinator: GovernedTaskCoordinator,
    *,
    admitted: AdmittedExecution,
    command: Any,
    work_order: Any,
) -> None:
    async for _ in coordinator.execute_stream(
        admitted=admitted,
        command=command,
        work_order=work_order,
    ):
        pass


@pytest.mark.asyncio
async def test_durable_intent_precedes_local_cancel_and_pending_is_not_finalized() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=False)
    coordinator = _coordinator(
        store,
        order,
        coordinator_type=_BlockingCoordinator,
    )
    assert isinstance(coordinator, _BlockingCoordinator)
    execution = asyncio.create_task(
        _drain_execution(
            coordinator,
            admitted=admitted,
            command=command,
            work_order=work_order,
        )
    )
    await coordinator.execution_started.wait()

    event = await coordinator.request_graph_cancellation(
        admitted=admitted,
        command=command,
        work_order=work_order,
        request_id="request-b5c-cancel",
        request_identity="authority://verified/principal-b5c",
        source="USER",
        reason_code="USER_REQUESTED",
    )

    assert execution.cancelled()
    assert order.index("intent") < coordinator.order.index("local-cancel")
    assert store.finalize_calls == 0
    receipt = event["execution_receipt"]
    assert receipt["status"] == "CANCEL_REQUESTED"
    assert receipt["reason_code"] == "GOVERNED_GRAPH_CANCELLATION_PENDING"
    assert receipt["projection"]["graph_cancellation"]["graph_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_process_shared_registry_joins_execution_owned_by_another_coordinator() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=False)
    registry = LocalGraphExecutionRegistry()
    executor = _coordinator(
        store,
        order,
        coordinator_type=_BlockingCoordinator,
        execution_registry=registry,
    )
    canceller = _coordinator(
        store,
        order,
        execution_registry=registry,
    )
    assert isinstance(executor, _BlockingCoordinator)
    execution = asyncio.create_task(
        _drain_execution(
            executor,
            admitted=admitted,
            command=command,
            work_order=work_order,
        ),
        name="shared-registry-executor",
    )
    await executor.execution_started.wait()

    event = await canceller.request_graph_cancellation(
        admitted=admitted,
        command=command,
        work_order=work_order,
        request_id="request-b5c-shared-registry",
        request_identity="authority://verified/principal-b5c",
        source="USER",
        reason_code="USER_REQUESTED",
    )

    assert execution.cancelled()
    assert event["execution_receipt"]["status"] == "CANCEL_REQUESTED"
    assert store.finalize_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    (
        ("request_id", "request-b5c-cancel-other"),
        ("request_identity", "authority://verified/principal-other"),
        ("source", "OPERATOR"),
        ("reason_code", "OPERATOR_REQUESTED"),
    ),
)
async def test_existing_cancellation_intent_rejects_request_binding_drift(
    field_name: str,
    drifted_value: str,
) -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=False)
    coordinator = _coordinator(store, order)
    arguments = {
        "admitted": admitted,
        "command": command,
        "work_order": work_order,
        "request_id": "request-b5c-cancel",
        "request_identity": "authority://verified/principal-b5c",
        "source": "USER",
        "reason_code": "USER_REQUESTED",
    }
    await coordinator.request_graph_cancellation(**arguments)
    calls_before_replay = (
        store.request_calls,
        store.advance_calls,
        store.finalize_calls,
        tuple(order),
    )

    with pytest.raises(GovernedRuntimeError) as error:
        await coordinator.request_graph_cancellation(
            **{**arguments, field_name: drifted_value}
        )

    assert error.value.reason_code == "GOVERNED_CANCELLATION_AUTHORITY_INVALID"
    assert (
        store.request_calls,
        store.advance_calls,
        store.finalize_calls,
        tuple(order),
    ) == calls_before_replay


@pytest.mark.asyncio
async def test_publisher_failure_after_intent_still_cancels_and_joins_local_execution() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=False)
    coordinator = _coordinator(
        store,
        order,
        coordinator_type=_BlockingCoordinator,
        outbox_publisher=_FailingPublisher(order),
    )
    assert isinstance(coordinator, _BlockingCoordinator)
    execution = asyncio.create_task(
        _drain_execution(
            coordinator,
            admitted=admitted,
            command=command,
            work_order=work_order,
        )
    )
    await coordinator.execution_started.wait()

    with pytest.raises(RuntimeError, match="publisher is unavailable"):
        await coordinator.request_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
            request_id="request-b5c-cancel",
            request_identity="authority://verified/principal-b5c",
            source="USER",
            reason_code="USER_REQUESTED",
        )

    assert execution.cancelled()
    assert order.index("intent") < order.index("publish-down:200")
    assert order.index("publish-down:200") < order.index("local-cancel")
    assert store.intent is not None
    assert store.graph["status"] == "BLOCKED"
    assert store.advance_calls == 0
    assert store.finalize_calls == 0


@pytest.mark.asyncio
async def test_transport_or_shutdown_cancelled_error_never_creates_business_intent() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=False)
    coordinator = _coordinator(
        store,
        order,
        coordinator_type=_BlockingCoordinator,
    )
    assert isinstance(coordinator, _BlockingCoordinator)
    execution = asyncio.create_task(
        _drain_execution(
            coordinator,
            admitted=admitted,
            command=command,
            work_order=work_order,
        )
    )
    await coordinator.execution_started.wait()

    execution.cancel("transport shutdown")
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert store.intent is None
    assert store.request_calls == 0
    assert "intent" not in order


@pytest.mark.asyncio
async def test_finalized_cancellation_replays_canonical_receipt_without_mutation() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    store = _CancellationStore(graph, order, ready_to_finalize=True)
    coordinator = _coordinator(store, order)
    arguments = {
        "admitted": admitted,
        "command": command,
        "work_order": work_order,
        "request_id": "request-b5c-cancel",
        "request_identity": "authority://verified/principal-b5c",
        "source": "USER",
        "reason_code": "USER_REQUESTED",
    }

    first = await coordinator.request_graph_cancellation(**arguments)
    replay = await coordinator.request_graph_cancellation(**arguments)

    assert store.request_calls == 1
    assert store.advance_calls == 1
    assert store.finalize_calls == 1
    assert first["execution_receipt"]["status"] == "CANCELLED"
    assert first["execution_receipt"]["replayed"] is False
    assert replay["execution_receipt"]["status"] == "CANCELLED"
    assert replay["execution_receipt"]["replayed"] is True
    assert (
        "verified_principal_id"
        not in first["execution_receipt"]["projection"]["graph_cancellation"]
    )
    assert (
        replay["execution_receipt"]["receipt_ref"]
        == first["execution_receipt"]["receipt_ref"]
    )


@pytest.mark.asyncio
async def test_large_reconciliation_manifest_is_referenced_not_expanded_into_outbox() -> None:
    command, work_order, assignment, graph = _contracts()
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    order: list[str] = []
    reconciliation_refs = tuple(f"evidence://reconciliation/{index}" for index in range(64))
    store = _CancellationStore(
        graph,
        order,
        ready_to_finalize=True,
        reconciliation_evidence_refs=reconciliation_refs,
    )
    coordinator = _coordinator(store, order)

    event = await coordinator.request_graph_cancellation(
        admitted=admitted,
        command=command,
        work_order=work_order,
        request_id="request-b5c-large-manifest",
        request_identity="authority://verified/principal-b5c",
        source="USER",
        reason_code="USER_REQUESTED",
    )

    assert event["execution_receipt"]["status"] == "CANCELLED"
    assert store.last_finalize_arguments is not None
    decision_payload = store.last_finalize_arguments["decision_payload"]
    assert decision_payload["reconciliation_evidence_refs"] == sorted(
        {store.intent["intent_ref"], *reconciliation_refs}
    )
    graph_outbox = store.last_finalize_arguments["graph_outbox"]
    assert set(graph_outbox.evidence_refs) == {
        store.decision["decision_ref"],
        store.intent["intent_ref"],
        "authority://verified/principal-b5c",
    }
    assert len(graph_outbox.evidence_refs) == 3
