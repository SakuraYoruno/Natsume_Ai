from __future__ import annotations

from typing import Any, Mapping

import pytest

from host_brain.composition.runtime_group import GovernedRuntimeGroup
from natsume_contracts.task import HostCommandSurface, build_host_command


def _command(
    surface: HostCommandSurface,
    *,
    with_claim: bool = True,
    target_id: str = "review-group-target",
):
    request_id = f"review-group-{surface.value}"
    return build_host_command(
        surface=surface,
        request_id=request_id,
        session_id="review-group-session",
        principal_id="review-group-principal",
        input_ref="input://review-group",
        input_sha256="sha256:" + "a" * 64,
        root_work_order_id="review-group-work",
        graph_id=f"review-group-graph-{surface.value}",
        graph_revision=2,
        route_type="delegate",
        target_id=target_id,
        business_scope="review-group-scope",
        operation_fingerprint="sha256:" + "b" * 64,
        created_at_ms=1_000,
        ingress_claim_ref=(
            f"ingress-claim://review-group/{surface.value}"
            if with_claim
            else None
        ),
        ingress_audit_ref=(
            f"secure-ingress-audit://review-group/{surface.value}"
            if with_claim
            else None
        ),
        governor_grant_ref="governor-decision://review-group",
    )


class _Runtime:
    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log
        self.claims: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "execution_receipt": {
                "status": "ACCEPTED",
                "receipt_ref": f"task-receipt://{name}",
                "accepted": True,
            }
        }

    async def activate(self) -> None:
        self.log.append(f"runtime:{self.name}:activate")

    async def close_admission(self) -> None:
        self.log.append(f"runtime:{self.name}:close-admission")

    async def drain(self) -> None:
        self.log.append(f"runtime:{self.name}:drain")

    async def aclose(self) -> None:
        self.log.append(f"runtime:{self.name}:close")

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert record["command"].graph_id == claim["graph_id"]
        self.claims.append(dict(claim))
        return dict(self.result)


class _Commands:
    def __init__(self, command: Any) -> None:
        self.command = command

    def get_by_graph(self, *, graph_id: str, graph_revision: int):
        assert graph_id == self.command.graph_id
        assert graph_revision == self.command.graph_revision
        return {
            "command": self.command,
            "work_order": object(),
            "assignment": object(),
            "graph": object(),
        }


class _Claims:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []

    def complete_claim(self, **kwargs: Any) -> None:
        self.completed.append(dict(kwargs))


class _OperatorOutcomeFinalizer:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []

    def finalize_review_outcome(self, **kwargs: Any) -> None:
        self.completed.append(dict(kwargs))


class _ReviewDecisions:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None

    def get_review(self, review_id: str) -> dict[str, Any]:
        assert self.record is not None
        assert self.record["review"]["review_id"] == review_id
        return dict(self.record)


class _CancellationStateReader:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any] | None:
        if self.record is not None:
            assert self.record["graph_id"] == graph_id
            assert self.record["graph_revision"] == graph_revision
        return self.record


class _Worker:
    def __init__(self, log: list[str], **kwargs: Any) -> None:
        self.log = log
        self.kwargs = kwargs
        self.wake_count = 0

    async def start(self) -> None:
        self.log.append("worker:start")

    def wake(self) -> None:
        self.wake_count += 1

    async def stop(self) -> None:
        self.log.append("worker:stop")

    async def join(self) -> None:
        self.log.append("worker:join")


def _group(surface: HostCommandSurface):
    log: list[str] = []
    runtimes = {
        name: _Runtime(name, log)
        for name in ("host", "internal_agent", "scheduler")
    }
    command = _command(surface)
    claims = _Claims()
    created: list[_Worker] = []

    def factory(**kwargs: Any) -> _Worker:
        worker = _Worker(log, **kwargs)
        created.append(worker)
        return worker

    decision_store = object()
    continuation_store = object()
    group = GovernedRuntimeGroup(
        runtimes,
        review_store=continuation_store,
        review_decision_store=decision_store,
        command_repository=_Commands(command),
        claim_result_store=claims,
        review_continuation_worker_factory=factory,
        review_continuation_worker_id="review-group-worker",
    )
    assert created[0].kwargs["store"] is continuation_store
    assert created[0].kwargs["review_expirer"] is decision_store
    assert created[0].kwargs["runtime"] is group
    return group, runtimes, command, claims, created[0], log


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "runtime_name"),
    [
        (HostCommandSurface.CHAT, "host"),
        (HostCommandSurface.HOST_INPUT, "host"),
        (HostCommandSurface.GATEWAY, "host"),
        (HostCommandSurface.INTERNAL, "internal_agent"),
        (HostCommandSurface.SCHEDULER, "scheduler"),
    ],
)
async def test_group_routes_exact_host_command_surface_and_completes_ingress(
    surface: HostCommandSurface,
    runtime_name: str,
) -> None:
    group, runtimes, command, claims, _worker, _log = _group(surface)
    await group.activate()
    claim = {
        "review_id": "review-group-1",
        "review_status": "CONSUMED",
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": "review-group-node",
    }

    result = await group.continue_review_continuation(claim)

    assert result["execution_receipt"]["status"] == "ACCEPTED"
    assert len(runtimes[runtime_name].claims) == 1
    assert sum(len(runtime.claims) for runtime in runtimes.values()) == 1
    if surface in {HostCommandSurface.INTERNAL, HostCommandSurface.SCHEDULER}:
        assert claims.completed[0]["claim_ref"] == command.ingress_claim_ref
        assert claims.completed[0]["result_ref"] == f"task-receipt://{runtime_name}"
    else:
        assert claims.completed == []
    await group.aclose()


@pytest.mark.asyncio
async def test_group_quiesces_review_worker_before_surface_admission() -> None:
    group, _runtimes, _command_value, _claims, worker, log = _group(
        HostCommandSurface.CHAT
    )

    await group.activate()
    group.wake_review_continuation()
    await group.close_admission()

    assert worker.wake_count == 1
    assert log.index("worker:stop") < log.index("runtime:host:close-admission")
    assert log.index("worker:join") < log.index("runtime:host:close-admission")
    await group.aclose()


def test_group_requires_both_review_authorities() -> None:
    runtime = _Runtime("host", [])

    with pytest.raises(TypeError, match="both Review stores"):
        GovernedRuntimeGroup(
            {"host": runtime},
            review_store=object(),
            command_repository=object(),
            review_continuation_worker_id="review-group-worker",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "review_status",
        "historical_side_effects",
        "expected_state",
        "expected_reason",
        "expected_side_effects",
    ),
    [
        ("CONSUMED", True, "SUCCEEDED", "OBS_COMMAND_COMPLETED", True),
        ("CONSUMED", False, "SUCCEEDED", "OBS_COMMAND_COMPLETED", False),
        ("REJECTED", False, "REJECTED", "OBS_COMMAND_REVIEW_REJECTED", False),
        ("EXPIRED", False, "REJECTED", "OBS_COMMAND_REVIEW_EXPIRED", False),
    ],
    ids=("applied", "no-change", "rejected", "expired"),
)
async def test_operator_review_continuation_finalizes_outer_outcome_only(
    review_status: str,
    historical_side_effects: bool,
    expected_state: str,
    expected_reason: str,
    expected_side_effects: bool,
) -> None:
    log: list[str] = []
    runtimes = {
        "host": _Runtime("host", log),
        "internal_agent": _Runtime("internal_agent", log),
        "scheduler": _Runtime("scheduler", log),
        "operator": _Runtime("operator", log),
    }
    command = _command(
        HostCommandSurface.OPERATOR,
        target_id="operator_sys_mode_switch",
    )
    operator = runtimes["operator"]
    operator.result = (
        {
            "execution_receipt": {
                "status": "ACCEPTED",
                "receipt_ref": "task-receipt://operator/accepted",
                "accepted": True,
            },
            "audit_refs": {
                "acceptance_effects": {
                    "count": 1,
                    "committed": 1,
                    "failed": 0,
                    "pending": 0,
                    "recovery_pending": 0,
                    "dispatch_status": "COMMITTED",
                }
            },
            "historical_side_effects_executed": historical_side_effects,
        }
        if review_status == "CONSUMED"
        else {"execution_receipt": {"status": "REVIEW_DENIED"}}
    )
    finalizer = _OperatorOutcomeFinalizer()
    central_claims = _Claims()
    review_decisions = _ReviewDecisions()
    group = GovernedRuntimeGroup(
        runtimes,
        review_store=object(),
        review_decision_store=review_decisions,
        command_repository=_Commands(command),
        claim_result_store=central_claims,
        operator_outcome_finalizer=finalizer,
        review_continuation_worker_factory=(
            lambda **kwargs: _Worker(log, **kwargs)
        ),
        review_continuation_worker_id="review-group-operator-worker",
    )
    await group.activate()
    review = {
        "review_id": "review-group-operator",
        "status": review_status,
        "subject_ref": (
            f"task-node://{command.graph_id}/{command.graph_revision}/"
            "review-group-node"
        ),
        "subject_version": 1,
        "resume_phase": "PRE_EXECUTION",
        "checkpoint_id": "review-group-operator-checkpoint",
    }
    review_decisions.record = {
        "review": review,
        "status_version": 2,
        "task_graph_id": command.graph_id,
        "task_node_id": "review-group-node",
    }
    claim = {
        "review_id": review["review_id"],
        "review_status": review_status,
        "review_status_version": 2,
        "subject_version": review["subject_version"],
        "resume_phase": review["resume_phase"],
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": "review-group-node",
        "review": {"review": review},
    }

    await group.continue_review_continuation(claim)

    assert central_claims.completed == []
    assert len(finalizer.completed) == 1
    finalized = finalizer.completed[0]
    assert finalized["claim_ref"] == command.ingress_claim_ref
    assert finalized["state"] == expected_state
    assert finalized["reason_code"] == expected_reason
    assert finalized["side_effects_executed"] is expected_side_effects
    assert finalized["continuation_ref"].startswith(
        f"governed-review://{command.graph_id}/{command.graph_revision}/"
        "review-group-operator?binding=sha256:"
    )
    assert finalized["terminal_result_ref"].startswith(
        "task-receipt://" if review_status == "CONSUMED" else "review-decision://"
    )
    await group.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending_result",
    [
        {
            "execution_receipt": {
                "status": "ACCEPTED",
                "receipt_ref": "task-receipt://operator/accepted",
                "accepted": True,
            },
            "audit_refs": {
                "acceptance_effects": {
                    "count": 1,
                    "committed": 0,
                    "failed": 0,
                    "pending": 1,
                    "recovery_pending": 1,
                    "dispatch_status": "RECOVERY_PENDING",
                }
            },
        },
        {
            "execution_receipt": {
                "status": "RECOVERY_REQUIRED",
                "receipt_ref": "task-receipt://operator/recovery",
                "accepted": False,
            }
        },
    ],
    ids=("other-owner-effect-claim", "non-terminal-task-receipt"),
)
async def test_operator_review_recovery_defers_before_later_effect_commit(
    pending_result: dict[str, Any],
) -> None:
    log: list[str] = []
    runtimes = {
        "operator": _Runtime("operator", log),
    }
    command = _command(
        HostCommandSurface.OPERATOR,
        target_id="operator_sys_mode_switch",
    )
    operator = runtimes["operator"]
    operator.result = pending_result
    finalizer = _OperatorOutcomeFinalizer()
    central_claims = _Claims()
    review_decisions = _ReviewDecisions()
    group = GovernedRuntimeGroup(
        runtimes,
        review_store=object(),
        review_decision_store=review_decisions,
        command_repository=_Commands(command),
        claim_result_store=central_claims,
        operator_outcome_finalizer=finalizer,
        review_continuation_worker_factory=(
            lambda **kwargs: _Worker(log, **kwargs)
        ),
        review_continuation_worker_id="review-group-operator-recovery-worker",
    )
    await group.activate()
    review = {
        "review_id": "review-group-operator-recovery",
        "status": "CONSUMED",
        "subject_ref": (
            f"task-node://{command.graph_id}/{command.graph_revision}/"
            "review-group-node"
        ),
        "subject_version": 1,
        "resume_phase": "PRE_EXECUTION",
        "checkpoint_id": "review-group-operator-recovery-checkpoint",
    }
    review_decisions.record = {
        "review": review,
        "status_version": 2,
        "task_graph_id": command.graph_id,
        "task_node_id": "review-group-node",
    }
    claim = {
        "review_id": review["review_id"],
        "review_status": "CONSUMED",
        "review_status_version": 2,
        "subject_version": review["subject_version"],
        "resume_phase": review["resume_phase"],
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": "review-group-node",
        "review": {"review": review},
    }

    with pytest.raises(RuntimeError) as pending:
        await group.continue_review_continuation(claim)
    assert getattr(pending.value, "reason_code", None) == (
        "OPERATOR_REVIEW_RECOVERY_PENDING"
    )
    assert finalizer.completed == []
    assert central_claims.completed == []

    operator.result = {
        "execution_receipt": {
            "status": "ACCEPTED",
            "receipt_ref": "task-receipt://operator/accepted",
            "accepted": True,
        },
        "audit_refs": {
            "acceptance_effects": {
                "count": 1,
                "committed": 1,
                "failed": 0,
                "pending": 0,
                "recovery_pending": 0,
                "dispatch_status": "COMMITTED",
            }
        },
        "historical_side_effects_executed": True,
    }
    await group.continue_review_continuation(claim)

    assert central_claims.completed == []
    assert len(finalizer.completed) == 1
    assert finalizer.completed[0]["state"] == "SUCCEEDED"
    assert finalizer.completed[0]["reason_code"] == "OBS_COMMAND_COMPLETED"
    await group.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject_query", "before_status"),
    [
        ("", "WAITING_APPROVAL"),
        (
            "?attempt=not-started&authority=intent-admission&initial_status=BLOCKED",
            "BLOCKED",
        ),
    ],
    ids=("ordinary-review", "intent-admission-review"),
)
async def test_operator_cancelled_review_requires_exact_durable_cancellation_receipt(
    subject_query: str,
    before_status: str,
) -> None:
    log: list[str] = []
    operator = _Runtime("operator", log)
    operator.result = {"execution_receipt": {"status": "REVIEW_DENIED"}}
    command = _command(
        HostCommandSurface.OPERATOR,
        target_id="operator_sys_mode_switch",
    )
    finalizer = _OperatorOutcomeFinalizer()
    review_decisions = _ReviewDecisions()
    cancellation_reader = _CancellationStateReader()
    group = GovernedRuntimeGroup(
        {"operator": operator},
        review_store=object(),
        review_decision_store=review_decisions,
        command_repository=_Commands(command),
        claim_result_store=_Claims(),
        operator_outcome_finalizer=finalizer,
        review_continuation_worker_factory=(
            lambda **kwargs: _Worker(log, **kwargs)
        ),
        review_continuation_worker_id="review-group-operator-cancel-worker",
        graph_cancellation_state_reader=cancellation_reader,
    )
    await group.activate()
    review = {
        "review_id": "review-group-operator-cancelled",
        "status": "CANCELLED",
        "subject_ref": (
            f"task-node://{command.graph_id}/{command.graph_revision}/"
            f"review-group-node{subject_query}"
        ),
        "subject_version": 1,
        "resume_phase": "PRE_EXECUTION",
        "checkpoint_id": "review-group-operator-cancelled-checkpoint",
    }
    review_decisions.record = {
        "review": review,
        "status_version": 2,
        "task_graph_id": command.graph_id,
        "task_node_id": "review-group-node",
    }
    claim = {
        "review_id": review["review_id"],
        "review_status": "CANCELLED",
        "review_status_version": 2,
        "subject_version": 1,
        "resume_phase": "PRE_EXECUTION",
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": "review-group-node",
        "review": {"review": review},
    }

    with pytest.raises(RuntimeError) as pending:
        await group.continue_review_continuation(claim)
    assert getattr(pending.value, "reason_code", None) == (
        "OPERATOR_REVIEW_CANCELLATION_RECOVERY_PENDING"
    )
    assert finalizer.completed == []

    intent_ref = "taskcancel-intent://sha256/" + "c" * 64
    cancellation_reader.record = {
        "intent_ref": intent_ref,
        "status": "REQUESTED",
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "payload": {
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "node_states_before": {
                "review-group-node": {
                    "status": before_status,
                    "status_version": 1,
                }
            },
            "node_states_after": {
                "review-group-node": {
                    "status": "CANCELLED",
                    "status_version": 2,
                }
            },
        },
    }
    await group.continue_review_continuation(claim)

    assert len(finalizer.completed) == 1
    assert finalizer.completed[0]["state"] == "REJECTED"
    assert finalizer.completed[0]["reason_code"] == "OBS_COMMAND_REVIEW_CANCELLED"
    assert finalizer.completed[0]["side_effects_executed"] is False
    assert finalizer.completed[0]["terminal_result_ref"] == intent_ref
    await group.aclose()
