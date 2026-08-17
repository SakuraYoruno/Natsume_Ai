"""能力授权、调用、时限、幂等与审计的唯一 Broker 所有者。

适配器只接收已验证 Grant，不能自行调度、重试或绕过 journal。Broker 先固定幂等声明和
终态证据，再交给单能力执行器；任何日志或授权依赖异常都在调用适配器之前失败关闭。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
import hashlib
import json
import re
import threading
import time
import uuid
import weakref
from typing import Any, Callable, Protocol

from .contracts import (
    CapabilityAttempt, CapabilityEvidence, CapabilityGrant, CapabilityKind,
    CapabilityReceipt, CapabilityRequest, GrantDecision, ReceiptStatus,
)
from .journal import (
    CLAIM_ABANDONED,
    CLAIM_ACQUIRED,
    CLAIM_CONFLICT,
    CLAIM_IN_FLIGHT,
    CLAIM_REPLAY,
    CapabilityExecutionJournal,
    InMemoryCapabilityExecutionJournal,
)


def _shutdown_executor_map(
    executors: dict[tuple[CapabilityKind, str], ThreadPoolExecutor],
    lock: threading.RLock,
) -> None:
    with lock:
        active = tuple(executors.values())
        executors.clear()
    for executor in active:
        executor.shutdown(wait=False, cancel_futures=True)


class CapabilityPolicy(Protocol):
    """为具体请求与描述符签发绑定 Grant 的权威策略端口。"""
    def authorize(self, request: CapabilityRequest, descriptor: "CapabilityDescriptor", *, now_ms: int) -> CapabilityGrant: ...


class CapabilityAuditSink(Protocol):
    """可选审计投影端口；其失败不得抹去 canonical journal 的终态。"""
    def record(self, attempt: CapabilityAttempt, evidence: CapabilityEvidence, receipt: CapabilityReceipt) -> None: ...


class CapabilityAdapter(Protocol):
    """实际能力适配器，只能接收 Broker 已验证的请求与 Grant。"""
    def __call__(self, request: CapabilityRequest, grant: CapabilityGrant) -> Any: ...


class CapabilityInvocationError(RuntimeError):
    """适配器可安全暴露的稳定失败原因及脱敏细节。"""
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = str(reason_code or "CAPABILITY_INVOCATION_FAILED")


# 描述符在组合期把能力、风险级别和授权策略固定在一起，适配器不能自行扩大权限。
@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """组合期固定的能力身份、风险、适配器和专属授权策略。"""
    capability_id: str
    kind: CapabilityKind
    version: str
    risk_level: int
    adapter: CapabilityAdapter
    # 生产环境以一个 Broker 管理所有能力种类，因此授权在组合期绑定到描述符，不能隐藏在
    # 每个 facade 私有 Broker 内。测试 Broker 可省略该字段并继续使用注入的默认策略。
    authorization_policy: CapabilityPolicy | None = None


@dataclass(frozen=True, slots=True)
class _TerminalBundle:
    receipt: CapabilityReceipt
    attempt: CapabilityAttempt
    evidence: CapabilityEvidence


class InMemoryCapabilityAuditSink:
    """线程安全的内存审计投影，适合测试或非耐久观察，不替代执行 journal。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.records: list[tuple[CapabilityAttempt, CapabilityEvidence, CapabilityReceipt]] = []

    def record(self, attempt: CapabilityAttempt, evidence: CapabilityEvidence, receipt: CapabilityReceipt) -> None:
        with self._lock:
            self.records.append((attempt, evidence, receipt))


class CapabilityBroker:
    """失败关闭的能力执行入口；适配器永不获得调度或重试权威。

    同一幂等键先由本进程锁收敛并发，再由耐久 journal 跨进程判定重放、冲突或遗留租约；
    超时后未来结果也会被单独记录，避免调用方把不确定效果错误重试。
    """

    def __init__(
        self,
        *,
        policy: CapabilityPolicy,
        audit_sink: CapabilityAuditSink | None = None,
        execution_journal: CapabilityExecutionJournal | None = None,
        workers: int = 8,
        claim_lease_ms: int = 300_000,
    ) -> None:
        if policy is None:
            raise ValueError("CapabilityBroker requires an explicit policy")
        self._policy = policy
        # The canonical journal is always recorded before any optional
        # projection sink.  A projection outage must not erase the receipt and
        # make a side effect eligible for a second invocation.
        self._audit_projection = audit_sink
        self._execution_journal = execution_journal or InMemoryCapabilityExecutionJournal()
        self._claim_lease_ms = max(1_000, int(claim_lease_ms))
        self._workers_per_capability = max(1, int(workers))
        self._descriptors: dict[tuple[CapabilityKind, str], CapabilityDescriptor] = {}
        self._claim_locks: dict[str, threading.Lock] = {}
        self._claim_users: dict[str, int] = {}
        self._lock = threading.RLock()
        self._executor_lock = threading.RLock()
        self._executors: dict[tuple[CapabilityKind, str], ThreadPoolExecutor] = {}
        self._executor_finalizer = weakref.finalize(
            self,
            _shutdown_executor_map,
            self._executors,
            self._executor_lock,
        )

    def register(self, descriptor: CapabilityDescriptor, *, replace_existing: bool = False) -> None:
        """在组合期登记能力；默认拒绝同名替换，防止运行中适配器劫持。"""
        if not descriptor.capability_id.strip() or not callable(descriptor.adapter):
            raise ValueError("capability descriptor is invalid")
        key = (descriptor.kind, descriptor.capability_id)
        with self._lock:
            # 注册表是“能力名称 → 固定风险与适配器”的唯一组合期绑定；默认拒绝
            # 覆盖，避免运行中同名适配器被静默替换而继承已有调用授权。
            if key in self._descriptors and not replace_existing:
                raise ValueError(f"capability already registered: {descriptor.kind.value}/{descriptor.capability_id}")
            self._descriptors[key] = descriptor

    def unregister(self, *, kind: CapabilityKind, capability_id: str) -> None:
        """移除 facade 所有的绑定而不影响其他种类；不会撤销已持久化的历史收据。"""

        key = (kind, str(capability_id or "").strip())
        with self._lock:
            self._descriptors.pop(key, None)

    def execute(self, request: CapabilityRequest) -> CapabilityReceipt:
        """执行或重放一次请求：先序列化幂等键，再进入耐久声明与授权校验主链。"""
        # 同一幂等键先在进程内串行化，减少并发请求同时抢到持久租约的窗口。
        with self._lock:
            claim = self._claim_locks.setdefault(request.idempotency_key, threading.Lock())
            self._claim_users[request.idempotency_key] = self._claim_users.get(request.idempotency_key, 0) + 1
        timeout_s = None
        if request.deadline_at_ms is not None:
            timeout_s = max(0.0, (request.deadline_at_ms - _now_ms()) / 1000.0)
        acquired = claim.acquire() if timeout_s is None else claim.acquire(timeout=timeout_s)
        if not acquired:
            # 进程内锁只缩窄本地并发窗口；等锁超时返回可审计 transient，而非绕过
            # 同一幂等键直接调用适配器，跨进程权威仍由 durable journal 提供。
            started = _now_ms()
            try:
                return self._transient(
                    self._terminal_bundle(
                        request,
                        started=started,
                        status=ReceiptStatus.TIMED_OUT,
                        reason="CAPABILITY_CLAIM_DEADLINE_EXCEEDED",
                        detail="deadline elapsed while waiting for idempotency claim",
                    )
                )
            finally:
                self._release_claim_user(request.idempotency_key, claim, acquired=False)
        try:
            return self._execute_claimed(request)
        finally:
            claim.release()
            self._release_claim_user(request.idempotency_key, claim, acquired=True)

    def _release_claim_user(self, idempotency_key: str, claim: threading.Lock, *, acquired: bool) -> None:
        del acquired
        with self._lock:
            users = self._claim_users.get(idempotency_key, 1) - 1
            if users <= 0:
                self._claim_users.pop(idempotency_key, None)
                if self._claim_locks.get(idempotency_key) is claim:
                    self._claim_locks.pop(idempotency_key, None)
            else:
                self._claim_users[idempotency_key] = users

    def _execute_claimed(self, request: CapabilityRequest) -> CapabilityReceipt:
        started = _now_ms()
        request_hash = request.request_hash
        # 在触碰适配器前先查询持久日志：进程重启后的重试也必须服从既有终态。
        try:
            cached = self._execution_journal.lookup(request.idempotency_key)
        except Exception:
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.IN_DOUBT,
                reason="CAPABILITY_JOURNAL_UNAVAILABLE",
                detail="durable execution journal is unavailable; invocation was not attempted",
            ))
        if cached is not None:
            cached_hash, cached_receipt = cached
            if cached_hash == request_hash:
                return replace(cached_receipt, replayed=True)
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.REJECTED,
                reason="CAPABILITY_IDEMPOTENCY_CONFLICT",
                detail="idempotency key is bound to a different request",
            ))

        # 租约所有者令牌把完成权绑定给本次调用，过期或遗留租约只能进入对账而不能盲目重试。
        owner_token = f"capability-owner://{uuid.uuid4().hex}"
        lease_expires_at_ms = max(
            started + self._claim_lease_ms,
            int(request.deadline_at_ms or 0),
        )
        try:
            claim_state = self._execution_journal.claim(
                request,
                owner_token=owner_token,
                now_ms=started,
                lease_expires_at_ms=lease_expires_at_ms,
            )
        except Exception:
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.IN_DOUBT,
                reason="CAPABILITY_JOURNAL_UNAVAILABLE",
                detail="durable claim could not be acquired; invocation was not attempted",
            ))
        if claim_state == CLAIM_REPLAY:
            replay = self._execution_journal.lookup(request.idempotency_key)
            if replay is not None and replay[0] == request_hash:
                return replace(replay[1], replayed=True)
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.IN_DOUBT,
                reason="CAPABILITY_RECEIPT_UNAVAILABLE",
                detail="durable receipt disappeared after replay decision",
            ))
        if claim_state == CLAIM_CONFLICT:
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.REJECTED,
                reason="CAPABILITY_IDEMPOTENCY_CONFLICT",
                detail="idempotency key is bound to a different request",
            ))
        if claim_state in {CLAIM_IN_FLIGHT, CLAIM_ABANDONED}:
            # 持久 claim 处于进行中或遗留状态时不可自动重试，因为外部副作用可能
            # 已发生。调用方得到 IN_DOUBT，随后由专门对账流程而不是本链猜测处理。
            reason = (
                "CAPABILITY_EXECUTION_IN_FLIGHT"
                if claim_state == CLAIM_IN_FLIGHT
                else "CAPABILITY_EXECUTION_RECONCILIATION_REQUIRED"
            )
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.IN_DOUBT,
                reason=reason,
                detail="existing durable claim forbids automatic duplicate invocation",
            ))
        if claim_state != CLAIM_ACQUIRED:
            return self._transient(self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.IN_DOUBT,
                reason="CAPABILITY_CLAIM_STATE_INVALID",
                detail="execution journal returned an unsupported claim state",
            ))

        # 未注册能力默认拒绝；注册表是从请求名称到可执行适配器的唯一入口。
        descriptor = self._descriptors.get((request.kind, request.capability_id))
        if descriptor is None:
            return self._finish(
                request,
                owner_token,
                self._terminal_bundle(
                    request,
                    started=started,
                    status=ReceiptStatus.REJECTED,
                    reason="CAPABILITY_NOT_REGISTERED",
                    detail="capability is not registered",
                ),
            )
        if request.deadline_at_ms is not None and started >= request.deadline_at_ms:
            return self._finish(request, owner_token, self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.TIMED_OUT,
                reason="CAPABILITY_DEADLINE_EXCEEDED",
                detail="deadline elapsed before authorization",
            ))

        # 授权失败采用 fail-closed：策略不可用时绝不把“无法判断”解释为允许。
        try:
            # policy 同时绑定当前 request 与组合期 descriptor；策略异常按拒绝处理，
            # 不把“无法授权”降级为调用适配器的默认许可。
            grant = self._policy.authorize(request, descriptor, now_ms=started)
        except Exception as exc:
            return self._finish(request, owner_token, self._terminal_bundle(
                request,
                started=started,
                status=ReceiptStatus.REJECTED,
                reason="CAPABILITY_POLICY_UNAVAILABLE",
                detail=f"policy failed closed: {type(exc).__name__}",
            ))
        # 在提交线程前再次检查授予物是否绑定请求、未过期且明确批准。
        grant_error = _invalid_grant(grant, request, now_ms=started)
        if grant_error:
            return self._finish(request, owner_token, self._terminal_bundle(
                request, started=started, status=ReceiptStatus.REJECTED,
                reason=str(getattr(grant, "reason_code", "") or grant_error),
                detail=grant_error,
                grant=grant if isinstance(grant, CapabilityGrant) else None,
            ))

        timeout_s = None
        if request.deadline_at_ms is not None:
            timeout_s = max(0.001, (request.deadline_at_ms - _now_ms()) / 1000.0)
        # 每项能力有独立执行池，慢适配器不会占用其他能力的并发配额。
        future = self._executor_for(descriptor).submit(descriptor.adapter, request, grant)
        try:
            output = future.result(timeout=timeout_s)
        except FutureTimeout:
            # 线程超时不能证明外部动作没有完成：未启动可取消，已启动只能登记迟到
            # 结果并返回 IN_DOUBT，禁止在未知状态下重新调用同一能力。
            # 已开始的外部副作用无法可靠取消，只能标为不确定并异步记录迟到结果供恢复流程对账。
            cancelled_before_start = future.cancel()
            if not cancelled_before_start:
                future.add_done_callback(
                    lambda completed: self._record_late_outcome(
                        request,
                        owner_token=owner_token,
                        future=completed,
                    )
                )
            bundle = self._terminal_bundle(
                request,
                started=started,
                status=(ReceiptStatus.TIMED_OUT if cancelled_before_start else ReceiptStatus.IN_DOUBT),
                reason=(
                    "CAPABILITY_ADAPTER_NOT_STARTED"
                    if cancelled_before_start
                    else "CAPABILITY_ADAPTER_OUTCOME_UNKNOWN"
                ),
                detail=(
                    "adapter was cancelled before invocation"
                    if cancelled_before_start
                    else "adapter exceeded deadline and may still complete; reconciliation required"
                ),
                grant=grant,
            )
        except CapabilityInvocationError as exc:
            bundle = self._terminal_bundle(
                request, started=started, status=ReceiptStatus.FAILED,
                reason=exc.reason_code, detail=str(exc), grant=grant,
            )
        except Exception as exc:
            bundle = self._terminal_bundle(
                request, started=started, status=ReceiptStatus.FAILED,
                reason="CAPABILITY_INVOCATION_FAILED",
                detail=f"{type(exc).__name__}: capability adapter failed",
                grant=grant,
            )
        else:
            bundle = self._terminal_bundle(
                request, started=started, status=ReceiptStatus.SUCCEEDED,
                reason=None, detail="capability completed", grant=grant, output=output,
            )
        return self._finish(request, owner_token, bundle)

    def _finish(
        self,
        request: CapabilityRequest,
        owner_token: str,
        bundle: _TerminalBundle,
    ) -> CapabilityReceipt:
        # 先封存持久回执，后发布可选审计投影；投影故障不能让副作用重新获得执行资格。
        try:
            persisted = self._execution_journal.complete(
                request,
                owner_token=owner_token,
                attempt=bundle.attempt,
                evidence=bundle.evidence,
                receipt=bundle.receipt,
            )
        except Exception:
            return self._transient(self._terminal_bundle(
                request,
                started=bundle.attempt.started_at_ms,
                status=ReceiptStatus.IN_DOUBT,
                reason="CAPABILITY_RECEIPT_PERSIST_FAILED",
                detail="durable receipt could not be sealed; automatic retry is forbidden",
            ))
        if persisted is bundle.receipt:
            self._publish(bundle)
            return persisted
        return replace(persisted, replayed=True)

    def _terminal_bundle(self, request: CapabilityRequest, *, started: int, status: ReceiptStatus, reason: str | None, detail: str, grant: CapabilityGrant | None = None, output: Any = None) -> _TerminalBundle:
        finished = _now_ms()
        evidence_id = "capability-evidence://" + hashlib.sha256(f"{request.attempt_id}:{finished}:{status.value}".encode()).hexdigest()
        safe_output = None if output is None else _sanitize_output(output)
        output_digest = None if safe_output is None else _digest(safe_output)
        receipt = CapabilityReceipt(
            receipt_id="capability-receipt://" + hashlib.sha256(f"{request.idempotency_key}:{request.request_hash}".encode()).hexdigest(),
            request_id=request.request_id, attempt_id=request.attempt_id, idempotency_key=request.idempotency_key,
            capability_id=request.capability_id, kind=request.kind, status=status, reason_code=reason,
            detail=_sanitize_text(detail), grant_id=grant.grant_id if grant else None, evidence_ref=evidence_id, output=safe_output,
        )
        attempt = CapabilityAttempt(
            attempt_id=request.attempt_id, request_id=request.request_id, capability_id=request.capability_id,
            kind=request.kind, grant_id=receipt.grant_id, status=status.value, started_at_ms=started, finished_at_ms=finished,
        )
        evidence = CapabilityEvidence(
            evidence_id=evidence_id, attempt_id=request.attempt_id, output_digest=output_digest,
            latency_ms=max(0, finished - started), error_type=reason if not receipt.success else None,
            metadata={"receipt_id": receipt.receipt_id},
        )
        return _TerminalBundle(receipt=receipt, attempt=attempt, evidence=evidence)

    def _transient(self, bundle: _TerminalBundle) -> CapabilityReceipt:
        self._publish(bundle)
        return bundle.receipt

    def _publish(self, bundle: _TerminalBundle) -> None:
        if self._audit_projection is not None:
            try:
                self._audit_projection.record(bundle.attempt, bundle.evidence, bundle.receipt)
            except Exception:
                pass

    def _record_late_outcome(self, request: CapabilityRequest, *, owner_token: str, future: Any) -> None:
        # 超时后的完成结果只补写为晚到证据，绝不翻转已返回给调用方的终态回执。
        try:
            output = future.result()
        except Exception as exc:
            status = "LATE_FAILED"
            output_digest = None
            error_type = type(exc).__name__
        else:
            status = "LATE_SUCCEEDED"
            output_digest = _digest(output)
            error_type = None
        try:
            self._execution_journal.record_late_outcome(
                request,
                owner_token=owner_token,
                status=status,
                output_digest=output_digest,
                error_type=error_type,
                observed_at_ms=_now_ms(),
            )
        except Exception:
            pass

    def _executor_for(self, descriptor: CapabilityDescriptor) -> ThreadPoolExecutor:
        key = (descriptor.kind, descriptor.capability_id)
        with self._executor_lock:
            executor = self._executors.get(key)
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=self._workers_per_capability,
                    thread_name_prefix=f"capability-{descriptor.kind.value}",
                )
                self._executors[key] = executor
            return executor

    def close(self) -> None:
        """Release idle execution pools; running adapters remain quarantined."""

        if self._executor_finalizer.alive:
            self._executor_finalizer()


def _invalid_grant(grant: Any, request: CapabilityRequest, *, now_ms: int) -> str | None:
    # 这里集中验证授权闭合，避免各适配器自行解释 Grant 的安全语义。
    if not isinstance(grant, CapabilityGrant):
        return "CAPABILITY_GRANT_MISSING"
    if grant.decision is not GrantDecision.APPROVE:
        return "CAPABILITY_GRANT_REJECTED"
    if grant.request_hash != request.request_hash or grant.capability_id != request.capability_id or grant.kind is not request.kind:
        return "CAPABILITY_GRANT_BINDING_INVALID"
    if grant.issued_at_ms > now_ms or grant.expires_at_ms <= now_ms:
        return "CAPABILITY_GRANT_EXPIRED"
    return None


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        try:
            encoded = f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"
        except Exception:
            encoded = f"{type(value).__module__}.{type(value).__qualname__}:unrepresentable"
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_SENSITIVE_OUTPUT_KEY = re.compile(
    r"(?i)(?:authorization|cookie|password|passwd|private[_-]?key|secret|"
    r"api[_-]?key|credential|token|grant[_-]?ref|"
    r"private[_-]?(?:body|memory)|raw[_-]?(?:body|thought|reasoning)|"
    r"chain[_-]?of[_-]?thought|reasoning)"
)
_SENSITIVE_OUTPUT_VALUE = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)\b(?:approval|confirm|undo|canon|review_resume)::"
        r"[A-Za-z0-9._~+/=-]+"
    ),
    re.compile(
        r"(?i)\b(?:api[_-]?key|credential|token|approval[_-]?token[_-]?ref|"
        r"access[_-]?token|refresh[_-]?token|secret|password|authorization)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _sanitize_text(value: Any) -> str:
    cleaned = str(value or "")
    for pattern in _SENSITIVE_OUTPUT_VALUE:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned if len(cleaned) <= 32_768 else cleaned[:32_765] + "..."


def _sanitize_output(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, bytes):
        return {"bytes_digest": _digest(value.hex()), "size": len(value)}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            result[key] = (
                "[REDACTED]"
                if _SENSITIVE_OUTPUT_KEY.search(key)
                else _sanitize_output(raw_value, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_output(item, depth=depth + 1) for item in value]
    try:
        projected = str(value)
    except Exception:
        projected = f"<{type(value).__module__}.{type(value).__qualname__}>"
    return _sanitize_text(projected)


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "CapabilityAdapter", "CapabilityAuditSink", "CapabilityBroker", "CapabilityDescriptor",
    "CapabilityInvocationError", "CapabilityPolicy", "InMemoryCapabilityAuditSink",
]
