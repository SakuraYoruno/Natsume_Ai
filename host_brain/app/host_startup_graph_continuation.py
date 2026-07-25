"""Bounded Host startup continuation for durable nonterminal TaskGraphs.

The TaskStore owns lease expiry and every TaskGraph transition.  This module
only discovers already-admitted Host graphs and re-enters their exact immutable
command contracts.  It never reconstructs a request, synthesizes a plan, or
replays a worker after a live lease or uncertain effect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import inspect
from typing import Any

from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import ExecutorAssignment, HostCommand, HostCommandSurface


_HOST_SURFACES = frozenset(
    {
        HostCommandSurface.CHAT,
        HostCommandSurface.HOST_INPUT,
        HostCommandSurface.TOOL,
        HostCommandSurface.GATEWAY,
    }
)
_HOST_SURFACE_VALUES = tuple(sorted(surface.value for surface in _HOST_SURFACES))
_RESUMABLE_GRAPH_STATUSES = frozenset({"CREATED", "ACTIVE", "BLOCKED"})
_LIVE_LEASE_STATUS = "ACTIVE"
_EXECUTION_IN_PROGRESS = "GOVERNED_EXECUTION_IN_PROGRESS"


class HostStartupGraphContinuationError(RuntimeError):
    """The durable Host recovery inventory cannot be trusted."""

    def __init__(self, detail: str) -> None:
        self.reason_code = "GOVERNED_HOST_STARTUP_CONTINUATION_INVALID"
        self.detail = str(detail)
        super().__init__(f"{self.reason_code}: {self.detail}")


ResumeAdmission = Callable[
    [HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph],
    Awaitable[Mapping[str, Any]],
]


class HostStartupGraphContinuation:
    """Re-enter exact Host admissions after TaskStore startup recovery.

    The inventory is intentionally bounded.  Exceeding the limit is an
    activation error rather than silently claiming that all resumable Graphs
    were inspected; an exact full page is valid.  A graph with a live Lease is
    reported as deferred and is never sent to a worker by this component.
    """

    def __init__(
        self,
        *,
        task_store: Any | None,
        command_repository: Any,
        resume_admission: ResumeAdmission,
    ) -> None:
        if not callable(resume_admission):
            raise TypeError("resume_admission must be callable")
        if task_store is not None:
            for method_name in ("read_scheduler_snapshot",):
                if not callable(getattr(task_store, method_name, None)):
                    raise TypeError(
                        "Host startup continuation TaskStore lacks "
                        f"{method_name}"
                    )
        if task_store is not None and not callable(
            getattr(command_repository, "list_nonterminal_admissions", None)
        ):
            raise TypeError(
                "Host startup continuation requires a surface-filtered "
                "command_repository inventory"
            )
        self._store = task_store
        self._commands = command_repository
        self._resume_admission = resume_admission
        self._report: tuple[dict[str, Any], ...] = ()

    @property
    def report(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._report)

    async def recover(
        self,
        *,
        page_limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """Resume all bounded Host-owned nonterminal Graphs once."""

        store = self._store
        if store is None:
            self._report = ()
            return ()
        if not 1 <= int(page_limit) <= 500:
            raise HostStartupGraphContinuationError(
                "startup continuation page_limit is outside [1, 500]"
            )
        raw_records = await _maybe_await(
            self._commands.list_nonterminal_admissions(
                surfaces=_HOST_SURFACE_VALUES,
                limit=int(page_limit),
            )
        )
        if not isinstance(raw_records, Sequence) or isinstance(
            raw_records,
            (str, bytes, bytearray),
        ):
            raise HostStartupGraphContinuationError(
                "nonterminal Host admission inventory is invalid"
            )
        records = tuple(raw_records)
        if len(records) > int(page_limit):
            raise HostStartupGraphContinuationError(
                "nonterminal Host admission inventory exceeded its safe startup bound"
            )

        reports: list[dict[str, Any]] = []
        for record in records:
            graph_id, graph_revision, graph_status = _admission_identity(record)
            command = _command_for_graph(
                record,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            if command.surface not in _HOST_SURFACES:
                raise HostStartupGraphContinuationError(
                    "surface-filtered Host admission inventory returned foreign work"
                )
            command, work_order, assignment, graph = _canonical_host_admission(
                record,
                command=command,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            if graph_status not in _RESUMABLE_GRAPH_STATUSES:
                raise HostStartupGraphContinuationError(
                    "nonterminal TaskGraph inventory has an unsupported status"
                )
            snapshot = await _maybe_await(
                store.read_scheduler_snapshot(graph_id, graph_revision)
            )
            snapshot_status = _snapshot_status(
                snapshot,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            if snapshot_status not in _RESUMABLE_GRAPH_STATUSES:
                continue
            if _has_live_lease(snapshot):
                reports.append(
                    _report(
                        graph_id,
                        graph_revision,
                        status="DEFERRED",
                        reason_code="GOVERNED_HOST_STARTUP_LIVE_LEASE",
                    )
                )
                continue
            try:
                terminal = await self._resume_admission(
                    command,
                    work_order,
                    assignment,
                    graph,
                )
            except Exception as exc:
                if str(getattr(exc, "reason_code", "")) == _EXECUTION_IN_PROGRESS:
                    reports.append(
                        _report(
                            graph_id,
                            graph_revision,
                            status="DEFERRED",
                            reason_code=_EXECUTION_IN_PROGRESS,
                        )
                    )
                    continue
                raise HostStartupGraphContinuationError(
                    f"exact Host TaskGraph continuation failed: {type(exc).__name__}"
                ) from exc
            reports.append(
                _terminal_report(
                    graph_id,
                    graph_revision,
                    terminal,
                )
            )
        self._report = tuple(reports)
        return self.report


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _canonical_host_admission(
    record: Any,
    *,
    command: HostCommand,
    graph_id: str,
    graph_revision: int,
) -> tuple[HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    if not isinstance(record, Mapping):
        raise HostStartupGraphContinuationError(
            "resumable TaskGraph has no canonical HostCommand admission"
        )
    work_order = record.get("work_order")
    assignment = record.get("assignment")
    graph = record.get("graph")
    if (
        not isinstance(work_order, WorkOrderV3)
        or not isinstance(assignment, ExecutorAssignment)
        or not isinstance(graph, TaskGraph)
        or graph.graph_id != graph_id
        or graph.revision != graph_revision
        or graph.root_work_order_id != command.root_work_order_id
        or work_order.root_work_order_id != command.root_work_order_id
        or work_order.task_graph_id != graph_id
        or work_order.graph_revision != graph_revision
        or assignment.work_order_id != command.root_work_order_id
    ):
        raise HostStartupGraphContinuationError(
            "canonical HostCommand admission differs from durable TaskGraph"
        )
    return command, work_order, assignment, graph


def _command_for_graph(
    record: Any,
    *,
    graph_id: str,
    graph_revision: int,
) -> HostCommand:
    if not isinstance(record, Mapping):
        raise HostStartupGraphContinuationError(
            "resumable TaskGraph has no canonical HostCommand admission"
        )
    command = record.get("command")
    if (
        not isinstance(command, HostCommand)
        or command.graph_id != graph_id
        or command.graph_revision != graph_revision
    ):
        raise HostStartupGraphContinuationError(
            "canonical HostCommand identity differs from durable TaskGraph"
        )
    return command


def _admission_identity(value: Any) -> tuple[str, int, str]:
    if not isinstance(value, Mapping):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission inventory item is invalid"
        )
    graph_id = str(value.get("graph_id") or "").strip()
    revision = value.get("graph_revision")
    status = str(value.get("runtime_graph_status") or "").strip().upper()
    if not graph_id or isinstance(revision, bool) or not isinstance(revision, int):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission inventory has an invalid identity"
        )
    if revision < 1 or status not in _RESUMABLE_GRAPH_STATUSES:
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission inventory has an invalid state"
        )
    return graph_id, revision, status


def _snapshot_status(
    value: Any,
    *,
    graph_id: str,
    graph_revision: int,
) -> str:
    if not isinstance(value, Mapping):
        raise HostStartupGraphContinuationError(
            "TaskGraph scheduler snapshot is invalid"
        )
    graph = value.get("graph_record")
    if not isinstance(graph, Mapping):
        raise HostStartupGraphContinuationError(
            "TaskGraph scheduler snapshot has no graph record"
        )
    observed_graph_id = str(graph.get("graph_id") or "").strip()
    observed_revision = graph.get("revision")
    status = str(graph.get("status") or "").strip().upper()
    if (
        observed_graph_id != graph_id
        or observed_revision != graph_revision
        or status not in _RESUMABLE_GRAPH_STATUSES
    ):
        raise HostStartupGraphContinuationError(
            "TaskGraph scheduler snapshot differs from its durable inventory"
        )
    return status


def _has_live_lease(snapshot: Mapping[str, Any]) -> bool:
    observed_ts_ms = snapshot.get("observed_ts_ms")
    leases = snapshot.get("active_leases")
    if (
        isinstance(observed_ts_ms, bool)
        or not isinstance(observed_ts_ms, int)
        or observed_ts_ms < 0
        or not isinstance(leases, Sequence)
        or isinstance(leases, (str, bytes, bytearray))
    ):
        raise HostStartupGraphContinuationError(
            "TaskGraph scheduler Lease snapshot is invalid"
        )
    for lease in leases:
        if not isinstance(lease, Mapping):
            raise HostStartupGraphContinuationError(
                "TaskGraph scheduler Lease item is invalid"
            )
        status = str(lease.get("status") or "").strip().upper()
        expires_ts_ms = lease.get("expires_ts_ms")
        if (
            isinstance(expires_ts_ms, bool)
            or not isinstance(expires_ts_ms, int)
            or expires_ts_ms < 0
        ):
            raise HostStartupGraphContinuationError(
                "TaskGraph scheduler Lease expiry is invalid"
            )
        if status == _LIVE_LEASE_STATUS and expires_ts_ms > observed_ts_ms:
            return True
    return False


def _terminal_report(
    graph_id: str,
    graph_revision: int,
    terminal: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(terminal, Mapping):
        raise HostStartupGraphContinuationError(
            "exact Host TaskGraph continuation has no terminal event"
        )
    receipt = terminal.get("execution_receipt")
    if not isinstance(receipt, Mapping):
        raise HostStartupGraphContinuationError(
            "exact Host TaskGraph continuation has no execution receipt"
        )
    if (
        str(receipt.get("graph_id") or "").strip() != graph_id
        or receipt.get("graph_revision") != graph_revision
    ):
        raise HostStartupGraphContinuationError(
            "Host TaskGraph continuation receipt identity drifted"
        )
    accepted = receipt.get("accepted") is True
    status = str(receipt.get("status") or "").strip().upper()
    if not status:
        raise HostStartupGraphContinuationError(
            "Host TaskGraph continuation receipt status is missing"
        )
    reason_code = str(
        terminal.get("reason_code") or receipt.get("reason_code") or ""
    ).strip() or None
    return _report(
        graph_id,
        graph_revision,
        status="COMPLETED" if accepted and status == "ACCEPTED" else "DEFERRED",
        reason_code=reason_code,
    )


def _report(
    graph_id: str,
    graph_revision: int,
    *,
    status: str,
    reason_code: str | None,
) -> dict[str, Any]:
    return {
        "graph_id": graph_id,
        "graph_revision": graph_revision,
        "status": status,
        "reason_code": reason_code,
    }


__all__ = [
    "HostStartupGraphContinuation",
    "HostStartupGraphContinuationError",
]
