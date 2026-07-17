from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore, OutboxMessage
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.task_runtime import GovernedRuntimeError, GovernedTaskCoordinator
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _Acceptance,
    _Publisher,
    _Verifier,
    _contracts,
    _migrated_db,
)


class _ProcessLostAfterNodeAcceptance(BaseException):
    """Model process loss after ACCEPTED is durable but before outer finalization."""


class _SingleNodeProjectionWorker:
    def __init__(
        self,
        *,
        projection_store: WorkerProjectionStore,
        calls: list[str],
    ) -> None:
        self._projection_store = projection_store
        self._calls = calls

    async def execute_stream(
        self,
        *,
        command: Any,
        execution_context: dict[str, Any],
        **_: Any,
    ):
        node_id = str(execution_context["node_id"])
        attempt_id = str(execution_context["attempt_id"])
        self._calls.append(node_id)
        body = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "command_id": command.command_id,
            "request_id": command.request_id,
            "narrative": "durable reply recovered after restart",
            "route_type": "skill",
            "target_id": node_id,
            "graph_id": str(execution_context["graph_id"]),
            "graph_revision": int(execution_context["graph_revision"]),
            "node_id": node_id,
            "attempt_id": attempt_id,
            "node_grant_ref": f"grant://accepted-effect/{node_id}",
            "audit_refs": {"worker": f"audit://accepted-effect/{node_id}"},
            "budget_usage": {
                "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                "usage_id": f"worker-usage://accepted-effect/{node_id}",
                "measurements": {
                    "token_count": 10,
                    "retry_count": 0,
                    "model_cost_micros": 25,
                },
                "evidence_refs": [f"model-budget://accepted-effect/{node_id}"],
            },
        }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        projection_id = (
            "worker-projection://accepted-effect/1/"
            f"{node_id}/{attempt_id}/{digest.removeprefix('sha256:')}"
        )
        self._projection_store.stage(
            projection_id=projection_id,
            projection={
                **body,
                "projection_id": projection_id,
                "payload_digest": digest,
                "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
            },
        )
        yield {**body, "receipt_ref": projection_id}


class _ProjectionResolver:
    def __init__(self, worker: _SingleNodeProjectionWorker) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> _SingleNodeProjectionWorker:
        assert executor == "canonical"
        assert worker_route == "skill:root"
        return self._worker


class _ForbiddenResolver:
    def __init__(self) -> None:
        self.resolve_count = 0

    def resolve(self, **_: Any) -> Any:
        self.resolve_count += 1
        raise AssertionError("accepted worker must not run during Effect finalization recovery")


class _TamperedProjectionReader:
    def __init__(self, delegate: WorkerProjectionStore) -> None:
        self._delegate = delegate

    def load(self, receipt_ref: str) -> dict[str, Any] | None:
        loaded = self._delegate.load(receipt_ref)
        if not isinstance(loaded, dict):
            return None
        return {**loaded, "command_id": "host-command://tampered"}


class _NodeGrantService:
    def validate_parent_binding(self, **_kwargs: Any) -> bool:
        return True

    def derive(
        self,
        *,
        parent_grant_ref: str,
        task_graph: Any,
        **_: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            parent_grant_ref=parent_grant_ref,
            task_graph_id=task_graph.graph_id,
            graph_revision=task_graph.revision,
            grant_refs={
                node.node_id: f"grant://accepted-effect/{node.node_id}"
                for node in task_graph.nodes
            },
        )


def _runtime(
    db_path: Path,
    *,
    projection_store: Any,
    worker_resolver: Any,
    task_store: DurableTaskStore | None = None,
) -> GovernedTaskCoordinator:
    return GovernedTaskCoordinator(
        task_store=task_store or DurableTaskStore(db_path, clock=lambda: NOW),
        outbox_publisher=_Publisher(),
        outbox_factory=OutboxMessage,
        worker_resolver=worker_resolver,
        verifier=_Verifier(),
        acceptance=_Acceptance(),
        projection_reader=projection_store,
        acceptance_effects_enabled=True,
        owner_id="accepted-effect-worker",
    )


async def _execute(
    db_path: Path,
    *,
    command: Any,
    work_order: Any,
    assignment: Any,
    graph: Any,
    runtime: GovernedTaskCoordinator,
) -> list[dict[str, Any]]:
    return [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=runtime,
            node_grant_service=_NodeGrantService(),
        ).execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]


@pytest.mark.asyncio
async def test_accepted_node_restart_stages_effects_without_worker_rerun(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    command = replace(command, governor_grant_ref="grant://accepted-effect/parent")
    graph = replace(
        graph,
        result_node_id="root",
        nodes=(replace(graph.nodes[0], route="skill:root"),),
    )
    projection_store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    calls: list[str] = []
    first_store = DurableTaskStore(db_path, clock=lambda: NOW)
    first_runtime = _runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=_ProjectionResolver(
            _SingleNodeProjectionWorker(
                projection_store=projection_store,
                calls=calls,
            )
        ),
        task_store=first_store,
    )

    async def lose_process_before_outer_finalization(*_: Any, **__: Any) -> Any:
        assert first_store.get_graph(graph.graph_id, graph.revision)["status"] == "ACTIVE"
        assert first_store.get_node(graph.graph_id, graph.revision, "root")["status"] == (
            "ACCEPTED"
        )
        assert first_store.list_acceptance_effect_intents(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
        ) == []
        raise _ProcessLostAfterNodeAcceptance()

    first_runtime._budgets.finalize = lose_process_before_outer_finalization  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(_ProcessLostAfterNodeAcceptance):
        await _execute(
            db_path,
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
            runtime=first_runtime,
        )

    assert calls == ["root"]
    crashed = first_store.read_scheduler_snapshot(graph.graph_id, graph.revision)
    assert crashed["graph_record"]["status"] == "ACTIVE"
    assert crashed["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
    assert crashed["budgets"][0]["status"] == "ACTIVE"
    assert len(crashed["attempts"]) == 1
    assert crashed["attempts"][0]["status"] == "SUCCEEDED"

    forbidden = _ForbiddenResolver()
    restarted_store = DurableTaskStore(db_path, clock=lambda: NOW)
    recovered = await _execute(
        db_path,
        command=command,
        work_order=work_order,
        assignment=assignment,
        graph=graph,
        runtime=_runtime(
            db_path,
            projection_store=projection_store,
            worker_resolver=forbidden,
            task_store=restarted_store,
        ),
    )

    assert forbidden.resolve_count == 0
    assert calls == ["root"]
    assert recovered[-1]["execution_receipt"]["accepted"] is True
    assert recovered[-1]["execution_receipt"]["replayed"] is True
    recovered_snapshot = restarted_store.read_scheduler_snapshot(
        graph.graph_id,
        graph.revision,
    )
    assert recovered_snapshot["graph_record"]["status"] == "COMPLETED"
    assert recovered_snapshot["budgets"][0]["status"] == "CONSUMED"
    assert len(recovered_snapshot["attempts"]) == len(crashed["attempts"]) == 1
    effects = restarted_store.list_acceptance_effect_intents(
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
    )
    assert {effect["effect_type"] for effect in effects} == {
        "conversation.user_turn",
        "life.foreground_completed",
    }
    frozen_effects = [
        (
            effect["effect_id"],
            effect["payload_sha256"],
            effect["idempotency_key"],
            effect["status"],
        )
        for effect in effects
    ]

    replay_forbidden = _ForbiddenResolver()
    replay_store = DurableTaskStore(db_path, clock=lambda: NOW)
    replayed = await _execute(
        db_path,
        command=command,
        work_order=work_order,
        assignment=assignment,
        graph=graph,
        runtime=_runtime(
            db_path,
            projection_store=projection_store,
            worker_resolver=replay_forbidden,
            task_store=replay_store,
        ),
    )

    assert replay_forbidden.resolve_count == 0
    assert replayed[-1]["execution_receipt"]["replayed"] is True
    replayed_effects = replay_store.list_acceptance_effect_intents(
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
    )
    assert [
        (
            effect["effect_id"],
            effect["payload_sha256"],
            effect["idempotency_key"],
            effect["status"],
        )
        for effect in replayed_effects
    ] == frozen_effects


@pytest.mark.asyncio
async def test_accepted_node_restart_rejects_tampered_projection_before_effects(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    command = replace(command, governor_grant_ref="grant://accepted-effect/parent")
    graph = replace(
        graph,
        result_node_id="root",
        nodes=(replace(graph.nodes[0], route="skill:root"),),
    )
    projection_store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    calls: list[str] = []
    first_store = DurableTaskStore(db_path, clock=lambda: NOW)
    first_runtime = _runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=_ProjectionResolver(
            _SingleNodeProjectionWorker(
                projection_store=projection_store,
                calls=calls,
            )
        ),
        task_store=first_store,
    )

    async def lose_process_before_outer_finalization(*_: Any, **__: Any) -> Any:
        assert first_store.get_node(graph.graph_id, graph.revision, "root")["status"] == (
            "ACCEPTED"
        )
        raise _ProcessLostAfterNodeAcceptance()

    first_runtime._budgets.finalize = lose_process_before_outer_finalization  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(_ProcessLostAfterNodeAcceptance):
        await _execute(
            db_path,
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
            runtime=first_runtime,
        )

    forbidden = _ForbiddenResolver()
    tampered_store = DurableTaskStore(db_path, clock=lambda: NOW)
    with pytest.raises(GovernedRuntimeError, match="projection is not canonical"):
        await _execute(
            db_path,
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
            runtime=_runtime(
                db_path,
                projection_store=_TamperedProjectionReader(projection_store),
                worker_resolver=forbidden,
                task_store=tampered_store,
            ),
        )

    assert forbidden.resolve_count == 0
    assert calls == ["root"]
    assert tampered_store.get_graph(graph.graph_id, graph.revision)["status"] == "ACTIVE"
    assert tampered_store.get_node(graph.graph_id, graph.revision, "root")["status"] == (
        "ACCEPTED"
    )
    assert tampered_store.list_acceptance_effect_intents(
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
    ) == []

    repaired_forbidden = _ForbiddenResolver()
    repaired_store = DurableTaskStore(db_path, clock=lambda: NOW)
    repaired = await _execute(
        db_path,
        command=command,
        work_order=work_order,
        assignment=assignment,
        graph=graph,
        runtime=_runtime(
            db_path,
            projection_store=projection_store,
            worker_resolver=repaired_forbidden,
            task_store=repaired_store,
        ),
    )
    assert repaired_forbidden.resolve_count == 0
    assert repaired[-1]["execution_receipt"]["accepted"] is True
    assert repaired_store.get_graph(graph.graph_id, graph.revision)["status"] == (
        "COMPLETED"
    )
    assert len(
        repaired_store.list_acceptance_effect_intents(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
        )
    ) == 2
