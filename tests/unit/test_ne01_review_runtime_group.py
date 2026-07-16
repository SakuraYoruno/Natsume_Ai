from __future__ import annotations

from typing import Any, Mapping

import pytest

from host_brain.composition.runtime_group import GovernedRuntimeGroup
from natsume_contracts.task import HostCommandSurface, build_host_command


def _command(surface: HostCommandSurface, *, with_claim: bool = True):
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
        target_id="review-group-target",
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
