"""耐久非终态 TaskGraph 的有界 Host 启动续接。

TaskStore 拥有 lease 过期与全部 TaskGraph 转换。本模块只发现已准入的 Host graph 并
重新进入其精确不可变 command contract；绝不重建请求、合成计划，也不会在 live lease
或副作用不确定时重放 worker。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import hashlib
import json
from typing import Any

from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration.node_grant_errors import NODE_GRANT_EXPIRED
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.task_runtime.errors import (
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_REVIEW_REQUIRED,
)
from natsume_contracts.task import ExecutorAssignment, HostCommand, HostCommandSurface
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


_DEFAULT_HOST_SURFACES = frozenset(
    {
        HostCommandSurface.CHAT,
        HostCommandSurface.HOST_INPUT,
        HostCommandSurface.TOOL,
        HostCommandSurface.GATEWAY,
    }
)
_RESUMABLE_GRAPH_STATUSES = frozenset({"CREATED", "ACTIVE", "BLOCKED"})
_LIVE_LEASE_STATUS = "ACTIVE"
_DEFERRED_REASON_CODES = frozenset(
    {
        GOVERNED_EXECUTION_IN_PROGRESS,
        GOVERNED_REVIEW_REQUIRED,
        INTENT_ADMISSION_REVIEW_REQUIRED,
        NODE_GRANT_EXPIRED,
    }
)
_PAGE_SCHEMA_VERSION = "Natsume.host.nonterminal_admission_page.v1"
_CURSOR_SCHEMA_VERSION = "Natsume.host.nonterminal_admission_cursor.v1"


class HostStartupGraphContinuationError(RuntimeError):
    """耐久 Host 恢复清单无法证明一致性时抛出的失败关闭错误。"""

    def __init__(self, detail: str) -> None:
        self.reason_code = "GOVERNED_HOST_STARTUP_CONTINUATION_INVALID"
        self.detail = str(detail)
        super().__init__(f"{self.reason_code}: {self.detail}")


ResumeAdmission = Callable[
    [HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph],
    Awaitable[Mapping[str, Any]],
]


class HostStartupGraphContinuation:
    """在 TaskStore 启动恢复后重新进入精确的 Host admission。

    单页受限，独立总扫描上限防止启动工作无界；仓库使用不可变 keyset cursor 推进，
    多页时不会让 overflow sentinel 后的幸存任务永久搁置。持有 live Lease 的 graph
    仅报告 deferred，绝不由本组件再次派给 worker。
    """

    def __init__(
        self,
        *,
        task_store: Any | None,
        command_repository: Any,
        resume_admission: ResumeAdmission,
        surfaces: Sequence[HostCommandSurface] | None = None,
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
            getattr(
                command_repository,
                "list_nonterminal_admission_page",
                None,
            )
        ):
            raise TypeError(
                "Host startup continuation requires a surface-filtered "
                "paginated command_repository inventory"
            )
        self._store = task_store
        self._commands = command_repository
        self._resume_admission = resume_admission
        normalized_surfaces = _normalize_surfaces(
            _DEFAULT_HOST_SURFACES if surfaces is None else surfaces
        )
        self._surfaces = frozenset(normalized_surfaces)
        self._surface_values = tuple(
            sorted(surface.value for surface in normalized_surfaces)
        )
        self._surfaces_sha256 = _surfaces_sha256(self._surface_values)
        self._report: tuple[dict[str, Any], ...] = ()

    @property
    def report(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._report)

    async def recover(
        self,
        *,
        page_limit: int = 100,
        scan_limit: int = 10_000,
    ) -> tuple[dict[str, Any], ...]:
        """有界地一次续接所有 Host 所有的非终态 graph。

        每条记录都会复核清单身份、命令、WorkOrder、assignment 与 scheduler 快照；任一
        漂移说明恢复依据不可信，整个续接失败关闭而不是猜测应执行的工作。
        """

        store = self._store
        if store is None:
            self._report = ()
            return ()
        if not 1 <= int(page_limit) <= 500:
            raise HostStartupGraphContinuationError(
                "startup continuation page_limit is outside [1, 500]"
            )
        if not 1 <= int(scan_limit) <= 100_000:
            raise HostStartupGraphContinuationError(
                "startup continuation scan_limit is outside [1, 100000]"
            )

        reports: list[dict[str, Any]] = []
        cursor: Mapping[str, Any] | None = None
        scanned = 0
        seen_commands: set[str] = set()
        seen_graphs: set[tuple[str, int]] = set()
        while True:
            raw_page = await _maybe_await(
                self._commands.list_nonterminal_admission_page(
                    surfaces=self._surface_values,
                    limit=int(page_limit),
                    cursor=dict(cursor) if cursor is not None else None,
                )
            )
            records, next_cursor = _validated_page(
                raw_page,
                previous_cursor=cursor,
                page_limit=int(page_limit),
                surfaces_sha256=self._surfaces_sha256,
            )
            scanned += len(records)
            if scanned > int(scan_limit):
                raise HostStartupGraphContinuationError(
                    "nonterminal Host admission inventory exceeded its verified scan bound"
                )
            for record in records:
                graph_id, graph_revision, graph_status = _admission_identity(
                    record
                )
                command = _command_for_graph(
                    record,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                )
                if record.get("command_id") != command.command_id:
                    raise HostStartupGraphContinuationError(
                        "paginated Host admission command key differs from its command"
                    )
                graph_identity = (graph_id, graph_revision)
                if (
                    command.command_id in seen_commands
                    or graph_identity in seen_graphs
                ):
                    raise HostStartupGraphContinuationError(
                        "paginated Host admission inventory repeated an identity"
                    )
                seen_commands.add(command.command_id)
                seen_graphs.add(graph_identity)
                if command.surface not in self._surfaces:
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
                    reason_code = str(getattr(exc, "reason_code", ""))
                    if reason_code in _DEFERRED_REASON_CODES:
                        reports.append(
                            _report(
                                graph_id,
                                graph_revision,
                                status="DEFERRED",
                                reason_code=reason_code,
                            )
                        )
                        continue
                    raise HostStartupGraphContinuationError(
                        "exact Host TaskGraph continuation failed: "
                        f"{type(exc).__name__}"
                    ) from exc
                reports.append(
                    _terminal_report(
                        graph_id,
                        graph_revision,
                        terminal,
                    )
                )
            if next_cursor is None:
                break
            cursor = next_cursor
        self._report = tuple(reports)
        return self.report




def _canonical_host_admission(
    record: Any,
    *,
    command: HostCommand,
    graph_id: str,
    graph_revision: int,
) -> tuple[HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    """验证耐久记录中的 admission 对象共享同一 graph/root work-order 身份。"""
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
    """取出并验证 command 的 graph identity，防止记录键与载荷脱节。"""
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
    """提取经过类型与状态校验的耐久 graph identity。"""
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


def _validated_page(
    value: Any,
    *,
    previous_cursor: Mapping[str, Any] | None,
    page_limit: int,
    surfaces_sha256: str,
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any] | None]:
    """验证分页形状、表面筛选摘要和严格递增 keyset cursor。"""
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "surfaces_sha256",
        "records",
        "next_cursor",
        "has_more",
    }:
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission page shape is invalid"
        )
    if (
        value.get("schema_version") != _PAGE_SCHEMA_VERSION
        or value.get("surfaces_sha256") != surfaces_sha256
        or type(value.get("has_more")) is not bool
    ):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission page authority is invalid"
        )
    raw_records = value.get("records")
    if not isinstance(raw_records, Sequence) or isinstance(
        raw_records,
        (str, bytes, bytearray),
    ):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission page records are invalid"
        )
    records = tuple(raw_records)
    if len(records) > page_limit:
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission page exceeded its requested bound"
        )
    previous_key: tuple[int, str] | None = None
    if previous_cursor is not None:
        previous = _validated_cursor(
            previous_cursor,
            surfaces_sha256=surfaces_sha256,
        )
        previous_key = (
            previous["created_at_ms"],
            previous["command_id"],
        )
    page_tail_key = previous_key
    for record in records:
        if not isinstance(record, Mapping):
            raise HostStartupGraphContinuationError(
                "nonterminal Host admission page record is invalid"
            )
        record_key = (
            _non_negative_int(record.get("created_at_ms"), "created_at_ms"),
            str(record.get("command_id") or "").strip(),
        )
        if (
            not record_key[1]
            or len(record_key[1]) > 512
            or (page_tail_key is not None and record_key <= page_tail_key)
        ):
            raise HostStartupGraphContinuationError(
                "nonterminal Host admission page key order is invalid"
            )
        page_tail_key = record_key

    has_more = value["has_more"]
    raw_cursor = value.get("next_cursor")
    if has_more != (raw_cursor is not None) or (has_more and not records):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission page continuation is inconsistent"
        )
    if raw_cursor is None:
        return records, None
    cursor = _validated_cursor(
        raw_cursor,
        surfaces_sha256=surfaces_sha256,
    )
    cursor_key = (cursor["created_at_ms"], cursor["command_id"])
    if cursor_key != page_tail_key:
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission cursor differs from its page tail"
        )
    return records, cursor


def _validated_cursor(
    value: Any,
    *,
    surfaces_sha256: str,
) -> dict[str, Any]:
    """验证 cursor 与当前 surface 集绑定，禁止跨筛选条件续接。"""
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "surfaces_sha256",
        "created_at_ms",
        "command_id",
    }:
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission cursor shape is invalid"
        )
    command_id = str(value.get("command_id") or "").strip()
    if (
        value.get("schema_version") != _CURSOR_SCHEMA_VERSION
        or value.get("surfaces_sha256") != surfaces_sha256
        or not command_id
        or len(command_id) > 512
    ):
        raise HostStartupGraphContinuationError(
            "nonterminal Host admission cursor authority is invalid"
        )
    return {
        "schema_version": _CURSOR_SCHEMA_VERSION,
        "surfaces_sha256": surfaces_sha256,
        "created_at_ms": _non_negative_int(
            value.get("created_at_ms"),
            "cursor created_at_ms",
        ),
        "command_id": command_id,
    }


def _normalize_surfaces(
    surfaces: Sequence[HostCommandSurface],
) -> tuple[HostCommandSurface, ...]:
    """去重并校验允许恢复的 Host command surface，空集合没有安全含义。"""
    if isinstance(surfaces, (str, bytes, bytearray)) or not isinstance(
        surfaces,
        (Sequence, set, frozenset),
    ):
        raise TypeError("startup continuation surfaces must be a sequence")
    normalized: list[HostCommandSurface] = []
    seen: set[HostCommandSurface] = set()
    for surface in surfaces:
        if not isinstance(surface, HostCommandSurface):
            raise TypeError(
                "startup continuation surfaces must contain HostCommandSurface values"
            )
        if surface not in seen:
            normalized.append(surface)
            seen.add(surface)
    if not normalized:
        raise ValueError("startup continuation requires at least one surface")
    return tuple(normalized)


def _surfaces_sha256(surfaces: Sequence[str]) -> str:
    """计算有序 surface 列表摘要，作为分页结果与 cursor 的筛选 authority。"""
    encoded = json.dumps(
        list(surfaces),
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise HostStartupGraphContinuationError(
            f"nonterminal Host admission {field_name} is invalid"
        )
    return value


def _snapshot_status(
    value: Any,
    *,
    graph_id: str,
    graph_revision: int,
) -> str:
    """验证 scheduler 快照仍描述清单中的同一可续接 graph。"""
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
    """判定是否存在未过期执行 owner；仅已签名精确重获 fence 可例外续接。"""
    observed_ts_ms = snapshot.get("observed_ts_ms")
    leases = snapshot.get("active_leases")
    operation_fences = snapshot.get("operation_execution_fences", ())
    if (
        isinstance(observed_ts_ms, bool)
        or not isinstance(observed_ts_ms, int)
        or observed_ts_ms < 0
        or not isinstance(leases, Sequence)
        or isinstance(leases, (str, bytes, bytearray))
        or not isinstance(operation_fences, Sequence)
        or isinstance(operation_fences, (str, bytes, bytearray))
    ):
        raise HostStartupGraphContinuationError(
            "TaskGraph scheduler execution-owner snapshot is invalid"
        )
    verified_fences: list[Mapping[str, Any]] = []
    for operation_fence in operation_fences:
        if not isinstance(operation_fence, Mapping):
            raise HostStartupGraphContinuationError(
                "TaskGraph scheduler Operation fence item is invalid"
            )
        required_text = (
            "reservation_id",
            "attempt_id",
            "lease_id",
            "owner_id",
            "status",
        )
        fencing_token = operation_fence.get("fencing_token")
        status_version = operation_fence.get("status_version")
        signed_verified = operation_fence.get(
            "signed_reacquisition_verified"
        )
        if (
            any(
                not str(operation_fence.get(field_name) or "").strip()
                for field_name in required_text
            )
            or type(fencing_token) is not int
            or fencing_token < 1
            or type(status_version) is not int
            or status_version < 0
            or type(signed_verified) is not bool
        ):
            raise HostStartupGraphContinuationError(
                "TaskGraph scheduler Operation fence projection is invalid"
            )
        verified_fences.append(operation_fence)
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
            signed_replacements = [
                operation_fence
                for operation_fence in verified_fences
                if operation_fence.get("signed_reacquisition_verified") is True
                and str(operation_fence.get("status") or "").strip().upper()
                == "RESERVED"
                and str(operation_fence.get("attempt_id") or "").strip()
                == str(lease.get("attempt_id") or "").strip()
                and str(operation_fence.get("lease_id") or "").strip()
                == str(lease.get("lease_id") or "").strip()
                and str(operation_fence.get("owner_id") or "").strip()
                == str(lease.get("owner_id") or "").strip()
                and operation_fence.get("fencing_token")
                == lease.get("fencing_token")
            ]
            if len(signed_replacements) == 1:
                # The canonical DAG runtime still verifies that the replacement
                # fence belongs to its own execution owner before invoking a
                # worker.  Startup must re-enter this one signed state so that
                # check can run; treating it as an unrelated live owner would
                # strand a successfully reconciled Operation forever.
                continue
            return True
    return False


def _terminal_report(
    graph_id: str,
    graph_revision: int,
    terminal: Mapping[str, Any],
) -> dict[str, Any]:
    """把 execution receipt 收窄为不泄露载荷的完成或延后报告。"""
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
