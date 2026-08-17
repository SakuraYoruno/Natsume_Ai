"""由进程持有的耐久 Graph 取消继续执行 worker。

该 worker 只协调投递：迁移 0092 的取消意图、TaskGraph 与决定仍是业务权威；0094
的 claim 是重新进入 ``GraphCancellationRuntime`` 时的跨进程租约互斥锁。失去租约
后不再写入，由后续持有者按规范持久状态重放。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import inspect
import re
from typing import Any, Protocol

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand

from .admission import AdmittedExecution


_DEFAULT_POLL_INTERVAL_MS = 1_000
_DEFAULT_CLAIM_LEASE_MS = 30_000
_DEFAULT_RETRY_DELAY_MS = 1_000
_DEFAULT_BATCH_LIMIT = 16
_DEFAULT_SHUTDOWN_GRACE_MS = 5_000
_MAX_ERROR_CODE_LENGTH = 192
GRAPH_CANCELLATION_CONTINUATION_SHUTDOWN_TIMEOUT = (
    "GRAPH_CANCELLATION_CONTINUATION_SHUTDOWN_TIMEOUT"
)


class GraphCancellationContinuationShutdownError(RuntimeError):
    """关闭宽限期内 worker 未静止；组合层必须保持依赖可用并稍后重试。"""
    def __init__(self) -> None:
        self.reason_code = GRAPH_CANCELLATION_CONTINUATION_SHUTDOWN_TIMEOUT
        super().__init__(
            f"{self.reason_code}: cancellation continuation worker did not quiesce"
        )


class GraphCancellationContinuationStorePort(Protocol):
    def assert_graph_cancellation_continuation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None: ...

    def claim_pending_graph_cancellations(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    def heartbeat_graph_cancellation(
        self,
        intent_ref: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        lease_ms: int,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def defer_graph_cancellation(
        self,
        intent_ref: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        retry_delay_ms: int,
        last_error: str,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def complete_graph_cancellation(
        self,
        intent_ref: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...


class GraphCancellationCommandRepositoryPort(Protocol):
    def get_by_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any] | None: ...


class GraphCancellationContinuationRuntimePort(Protocol):
    async def continue_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        cancellation: Mapping[str, Any],
        local_pending: Sequence[str] | None = None,
        replayed: bool = False,
    ) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class _ClaimLease:
    intent_ref: str
    graph_id: str
    graph_revision: int
    claim_token: str
    fencing_token: int
    expected_version: int


Sleeper = Callable[[float], Awaitable[None]]


class GraphCancellationContinuationWorker:
    """安全认领并重新进入尚未完成的 0092 取消意图。

        每个 claim 带 token、围栏号和状态版本；后台心跳只续租当前所有权。继续运行时
        重建规范准入而不相信队列负载，因而消息重复、进程崩溃和接管都不会扩大权限。
    """

    def __init__(
        self,
        *,
        task_store: GraphCancellationContinuationStorePort,
        command_repository: GraphCancellationCommandRepositoryPort,
        cancellation_runtime: GraphCancellationContinuationRuntimePort,
        worker_id: str,
        claim_lease_ms: int = _DEFAULT_CLAIM_LEASE_MS,
        poll_interval_ms: int = _DEFAULT_POLL_INTERVAL_MS,
        retry_delay_ms: int = _DEFAULT_RETRY_DELAY_MS,
        batch_limit: int = _DEFAULT_BATCH_LIMIT,
        shutdown_grace_ms: int = _DEFAULT_SHUTDOWN_GRACE_MS,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        """校验调度参数并保存端口；生命周期对象仅在 ``start`` 后创建。"""
        owner = str(worker_id or "").strip()
        if not owner:
            raise ValueError("worker_id is required")
        if (
            int(claim_lease_ms) <= 0
            or int(poll_interval_ms) <= 0
            or int(retry_delay_ms) < 0
            or int(batch_limit) <= 0
            or int(shutdown_grace_ms) <= 0
        ):
            raise ValueError("continuation worker timing and batch values are invalid")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._store = task_store
        self._commands = command_repository
        self._runtime = cancellation_runtime
        self._worker_id = owner
        self._claim_lease_ms = int(claim_lease_ms)
        self._poll_interval_seconds = int(poll_interval_ms) / 1_000.0
        self._retry_delay_ms = int(retry_delay_ms)
        self._batch_limit = int(batch_limit)
        self._shutdown_grace_seconds = int(shutdown_grace_ms) / 1_000.0
        self._shutdown_cancel_grace_seconds = max(
            0.1,
            self._shutdown_grace_seconds,
        )
        self._sleeper = sleeper
        self._wake_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._shutdown_cancelled_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    async def start(self) -> None:
        """先失败关闭地确认 schema 就绪，再启动立即扫描的后台循环。"""

        async with self._lifecycle_lock:
            if self.running:
                return
            await _maybe_await(
                self._store.assert_graph_cancellation_continuation_ready()
            )
            self._wake_event = asyncio.Event()
            self._stop_event = asyncio.Event()
            self._shutdown_cancelled_task = None
            self._task = asyncio.create_task(
                self._run(),
                name=f"graph-cancellation-continuation::{self._worker_id}",
            )

    def wake(self) -> None:
        """请求提前扫描但不取得取消权限；权限只能来自持久 claim。"""

        wake_event = self._wake_event
        if wake_event is not None:
            wake_event.set()

    async def stop(self) -> None:
        """给活动 claim 有界收尾时间，超时后取消本地任务并由租约自然移交。"""

        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        self.wake()
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._shutdown_grace_seconds,
            )
            return
        except TimeoutError:
            self._shutdown_cancelled_task = task
            task.cancel()
        except asyncio.CancelledError:
            # stop 调用者被取消属于外部权威；shield 保留 worker，供后续生命周期所有者
            # 继续执行受控关闭。
            raise
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._shutdown_cancel_grace_seconds,
            )
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            if task is not self._shutdown_cancelled_task or not task.cancelled():
                raise
        except TimeoutError as exc:
            # Python 无法强制终止刻意吞掉取消的协程；组合层必须保持存储打开，并在
            # 稍后重试关闭，不能假装 worker 已静止。
            raise GraphCancellationContinuationShutdownError() from exc

    async def join(self) -> None:
        """等待后台循环静止；超时不假装它已停止。"""
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._shutdown_grace_seconds,
            )
        except TimeoutError as exc:
            raise GraphCancellationContinuationShutdownError() from exc
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            if task is self._shutdown_cancelled_task and task.cancelled():
                return
            raise

    async def aclose(self) -> None:
        """异步关闭入口，先请求停止再确认任务已汇合。"""
        await self.stop()
        await self.join()

    async def run_once(self) -> int:
        """认领一个有界批次并并发处理，使每项租约能独立续期。"""

        claimed_raw = await _maybe_await(
            self._store.claim_pending_graph_cancellations(
                worker_id=self._worker_id,
                lease_ms=self._claim_lease_ms,
                limit=self._batch_limit,
            )
        )
        if not isinstance(claimed_raw, Sequence) or isinstance(
            claimed_raw,
            (str, bytes, bytearray),
        ):
            raise RuntimeError("cancellation claim store returned an invalid batch")
        claimed = tuple(claimed_raw)
        if len(claimed) > self._batch_limit:
            raise RuntimeError("cancellation claim store exceeded the requested limit")
        if not claimed:
            return 0

        # 立即启动批次内所有 claim，使每个租约在其他图对账期间也能续期；过期或无效
        # claim 被隔离并等待耐久租约到期，不能取消其他图的继续执行。
        await asyncio.gather(
            *(self._process_claim_isolated(raw) for raw in claimed),
        )
        return len(claimed)

    async def _process_claim_isolated(self, raw: Mapping[str, Any]) -> None:
        """隔离单个损坏 claim；让其租约到期而不拖垮整批投递。"""
        try:
            await self._process_claim(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 损坏或过期 claim 留给耐久租约到期后由其他 worker 重新认领。
            return

    async def _run(self) -> None:
        """轮询循环：满批立即继续，非满批等待唤醒或下一次轮询。"""
        stop_event = self._required_stop_event()
        while not stop_event.is_set():
            try:
                claimed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                claimed = 0
            if stop_event.is_set():
                break
            if claimed >= self._batch_limit:
                continue
            await self._wait_for_wake_or_poll()

    async def _wait_for_wake_or_poll(self) -> None:
        """竞争唤醒、停止与轮询延迟，并取消其余 waiter 防止后台泄漏。"""
        wake_event = self._required_wake_event()
        stop_event = self._required_stop_event()
        if wake_event.is_set():
            wake_event.clear()
            return

        async def _poll_delay() -> None:
            await self._sleeper(self._poll_interval_seconds)

        waiters = {
            asyncio.create_task(wake_event.wait()),
            asyncio.create_task(stop_event.wait()),
            asyncio.create_task(_poll_delay()),
        }
        try:
            done, pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                await task
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            wake_event.clear()

    async def _process_claim(self, raw: Mapping[str, Any]) -> None:
        """在独立心跳下重放一个 claim，并只在规范取消已终结时完成认领。"""
        lease, intent = self._validated_claim(raw)
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_claim(lease, heartbeat_stop),
            name=f"graph-cancellation-heartbeat::{lease.intent_ref}",
        )
        continuation_error: Exception | None = None
        continuation_cancelled: asyncio.CancelledError | None = None
        try:
            command, work_order, admitted = await self._canonical_admission(lease)
            await self._runtime.continue_graph_cancellation(
                admitted=admitted,
                command=command,
                work_order=work_order,
                cancellation=intent,
                replayed=True,
            )
        except asyncio.CancelledError as exc:
            continuation_cancelled = exc
        except Exception as exc:
            continuation_error = exc
        finally:
            heartbeat_stop.set()

        heartbeat_error: Exception | None = None
        if continuation_cancelled is not None and not heartbeat_task.done():
            heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            if continuation_cancelled is None:
                raise
        except Exception as exc:
            heartbeat_error = exc
        if heartbeat_error is not None:
            # 所有权可能已过期或转移；绝不能发出过期的 complete/defer CAS，下一持有者
            # 会从规范状态重放。
            return

        if continuation_cancelled is not None:
            raise continuation_cancelled
        if continuation_error is not None:
            await self._defer_claim(
                lease,
                last_error=_stable_error_code(continuation_error),
            )
            return

        canonical_raw = await _maybe_await(
            self._store.get_graph_cancellation_for_graph(
                lease.graph_id,
                lease.graph_revision,
            )
        )
        if not isinstance(canonical_raw, Mapping):
            await self._defer_claim(
                lease,
                last_error="GRAPH_CANCELLATION_INTENT_MISSING",
            )
            return
        canonical = dict(canonical_raw)
        if (
            str(canonical.get("intent_ref") or "").strip() != lease.intent_ref
            or str(canonical.get("graph_id") or "").strip() != lease.graph_id
            or _positive_int(canonical.get("graph_revision"))
            != lease.graph_revision
        ):
            await self._defer_claim(
                lease,
                last_error="GRAPH_CANCELLATION_INTENT_DRIFT",
            )
            return
        if str(canonical.get("status") or "").strip().upper() != "FINALIZED":
            await self._defer_claim(
                lease,
                last_error="GRAPH_CANCELLATION_RECONCILIATION_PENDING",
            )
            return
        await _maybe_await(
            self._store.complete_graph_cancellation(
                lease.intent_ref,
                worker_id=self._worker_id,
                claim_token=lease.claim_token,
                fencing_token=lease.fencing_token,
                expected_version=lease.expected_version,
            )
        )

    async def _heartbeat_claim(
        self,
        lease: _ClaimLease,
        stop_event: asyncio.Event,
    ) -> None:
        """按租约三分之一周期续期，并确认响应仍属于同一 claim。"""
        interval = max(0.001, self._claim_lease_ms / 3_000.0)
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                refreshed_raw = await _maybe_await(
                    self._store.heartbeat_graph_cancellation(
                        lease.intent_ref,
                        worker_id=self._worker_id,
                        claim_token=lease.claim_token,
                        fencing_token=lease.fencing_token,
                        lease_ms=self._claim_lease_ms,
                        expected_version=lease.expected_version,
                    )
                )
                refreshed = _mapping(
                    refreshed_raw,
                    field_name="cancellation heartbeat",
                )
                self._assert_same_claim(lease, refreshed)
                lease.expected_version = _non_negative_int(
                    refreshed.get("status_version"),
                    field_name="heartbeat.status_version",
                )

    async def _canonical_admission(
        self,
        lease: _ClaimLease,
    ) -> tuple[HostCommand, Any, AdmittedExecution]:
        """从持久准入记录重建取消运行时所需的不可变命令、分配和图。"""
        record_raw = await _maybe_await(
            self._commands.get_by_graph(
                graph_id=lease.graph_id,
                graph_revision=lease.graph_revision,
            )
        )
        record = _mapping(record_raw, field_name="Host command admission")
        command = record.get("command")
        work_order = record.get("work_order")
        assignment = record.get("assignment")
        graph = record.get("graph")
        if (
            not isinstance(command, HostCommand)
            or not isinstance(assignment, ExecutorAssignment)
            or graph is None
            or work_order is None
        ):
            raise RuntimeError("canonical Host command admission is incomplete")
        admitted = AdmittedExecution.from_contracts(
            command=command,
            assignment=assignment,
            graph=graph,
            replayed=True,
        )
        if (
            admitted.graph_id != lease.graph_id
            or admitted.graph_revision != lease.graph_revision
        ):
            raise RuntimeError("canonical Host command Graph binding drifted")
        return command, work_order, admitted

    async def _defer_claim(
        self,
        lease: _ClaimLease,
        *,
        last_error: str,
    ) -> None:
        """以当前 fencing/version 推迟失败 claim，避免旧 worker 覆盖新所有者。"""
        await _maybe_await(
            self._store.defer_graph_cancellation(
                lease.intent_ref,
                worker_id=self._worker_id,
                claim_token=lease.claim_token,
                fencing_token=lease.fencing_token,
                retry_delay_ms=self._retry_delay_ms,
                last_error=last_error,
                expected_version=lease.expected_version,
            )
        )

    def _validated_claim(
        self,
        raw: Mapping[str, Any],
    ) -> tuple[_ClaimLease, dict[str, Any]]:
        """校验队列 claim 与嵌入取消意图的图绑定及所有者身份。"""
        record = _mapping(raw, field_name="cancellation continuation claim")
        intent = _mapping(record.get("intent"), field_name="cancellation intent")
        intent_ref = _required_text(record.get("intent_ref"), "intent_ref")
        graph_id = _required_text(record.get("graph_id"), "graph_id")
        graph_revision = _positive_int(record.get("graph_revision"))
        claim_owner = _required_text(record.get("claim_owner"), "claim_owner")
        claim_token = _required_text(record.get("claim_token"), "claim_token")
        fencing_token = _positive_int(record.get("claim_fencing_token"))
        expected_version = _non_negative_int(
            record.get("status_version"),
            field_name="status_version",
        )
        if claim_owner != self._worker_id:
            raise RuntimeError("cancellation claim belongs to another worker")
        if (
            _required_text(intent.get("intent_ref"), "intent.intent_ref")
            != intent_ref
            or _required_text(intent.get("graph_id"), "intent.graph_id")
            != graph_id
            or _positive_int(intent.get("graph_revision")) != graph_revision
        ):
            raise RuntimeError("cancellation continuation/intent binding drifted")
        return (
            _ClaimLease(
                intent_ref=intent_ref,
                graph_id=graph_id,
                graph_revision=graph_revision,
                claim_token=claim_token,
                fencing_token=fencing_token,
                expected_version=expected_version,
            ),
            intent,
        )

    def _assert_same_claim(
        self,
        lease: _ClaimLease,
        record: Mapping[str, Any],
    ) -> None:
        """心跳响应必须回显同一 token 与围栏号，否则视为所有权已丢失。"""
        if (
            _required_text(record.get("intent_ref"), "intent_ref")
            != lease.intent_ref
            or _required_text(record.get("graph_id"), "graph_id")
            != lease.graph_id
            or _positive_int(record.get("graph_revision"))
            != lease.graph_revision
            or _required_text(record.get("claim_owner"), "claim_owner")
            != self._worker_id
            or _required_text(record.get("claim_token"), "claim_token")
            != lease.claim_token
            or _positive_int(record.get("claim_fencing_token"))
            != lease.fencing_token
        ):
            raise RuntimeError("cancellation heartbeat returned a different claim")

    def _required_wake_event(self) -> asyncio.Event:
        """仅允许已启动实例访问唤醒原语。"""
        if self._wake_event is None:
            raise RuntimeError("cancellation continuation worker is not started")
        return self._wake_event

    def _required_stop_event(self) -> asyncio.Event:
        """仅允许已启动实例访问停止原语。"""
        if self._stop_event is None:
            raise RuntimeError("cancellation continuation worker is not started")
        return self._stop_event


async def _maybe_await(value: Any) -> Any:
    """兼容同步和异步端口，而不改变 claim 的异常传播语义。"""
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    """要求端口返回结构化记录，防止模糊值进入所有权比较。"""
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{field_name} must be a mapping")
    return dict(value)


def _required_text(value: Any, field_name: str) -> str:
    """规范化不可为空的标识字段。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(f"{field_name} is required")
    return normalized


def _positive_int(value: Any) -> int:
    """解析严格正整数，拒绝 bool 等会破坏围栏比较的伪整数。"""
    if isinstance(value, bool):
        raise RuntimeError("positive integer is required")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("positive integer is required") from exc
    if normalized < 1:
        raise RuntimeError("positive integer is required")
    return normalized


def _non_negative_int(value: Any, *, field_name: str) -> int:
    """解析版本号等允许为零的整数。"""
    if isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{field_name} must be a non-negative integer"
        ) from exc
    if normalized < 0:
        raise RuntimeError(f"{field_name} must be a non-negative integer")
    return normalized


def _stable_error_code(exc: Exception) -> str:
    """将内部异常压缩为可持久化、无敏感细节的稳定延迟原因码。"""
    reason_code = getattr(exc, "reason_code", None)
    if isinstance(reason_code, str) and re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,127}",
        reason_code,
    ):
        normalized = reason_code
    else:
        class_name = exc.__class__.__name__.upper()
        normalized = (
            re.sub(r"[^A-Z0-9_]+", "_", class_name).strip("_")
            or "UNKNOWN_ERROR"
        )[:128]
    return f"GRAPH_CANCELLATION_CONTINUATION_{normalized}"[
        :_MAX_ERROR_CODE_LENGTH
    ]


__all__ = [
    "GRAPH_CANCELLATION_CONTINUATION_SHUTDOWN_TIMEOUT",
    "GraphCancellationCommandRepositoryPort",
    "GraphCancellationContinuationShutdownError",
    "GraphCancellationContinuationRuntimePort",
    "GraphCancellationContinuationStorePort",
    "GraphCancellationContinuationWorker",
]
