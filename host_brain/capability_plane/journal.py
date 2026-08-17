"""能力执行的耐久声明与回执日志。

日志提供跨进程的至多一次准入。遗留或超时声明被刻意禁止自动接管：外部副作用可能已经
发生，必须由恢复/对账流程处理，而非让重试器盲目再调用一次。
"""

from __future__ import annotations

from dataclasses import asdict
import json
import threading
from typing import Any, Protocol

from host_brain.persistence.capability_execution_repository import (
    CapabilityExecutionRepository,
)

from .contracts import (
    CapabilityAttempt,
    CapabilityEvidence,
    CapabilityKind,
    CapabilityReceipt,
    CapabilityRequest,
    ReceiptStatus,
)


CLAIM_ACQUIRED = "ACQUIRED"
CLAIM_IN_FLIGHT = "IN_FLIGHT"
CLAIM_ABANDONED = "ABANDONED"
CLAIM_CONFLICT = "CONFLICT"
CLAIM_REPLAY = "REPLAY"


class CapabilityExecutionJournal(Protocol):
    """跨进程幂等执行边界：声明、封存回执与晚到结果必须具有耐久语义。"""
    # 该协议是跨进程的“至多一次准入”边界；Broker 依赖它而不是依赖内存锁恢复重启后的状态。
    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None: ...

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str: ...

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt: ...

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None: ...


class InMemoryCapabilityExecutionJournal:
    """仅用于隔离测试的线程安全日志；进程重启即丢失，不能作为生产恢复权威。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._claims: dict[str, tuple[str, str, int]] = {}
        self._receipts: dict[str, tuple[str, CapabilityReceipt]] = {}
        self.late_outcomes: list[dict[str, Any]] = []

    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None:
        with self._lock:
            return self._receipts.get(str(idempotency_key))

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str:
        with self._lock:
            receipt = self._receipts.get(request.idempotency_key)
            if receipt is not None:
                return CLAIM_REPLAY if receipt[0] == request.request_hash else CLAIM_CONFLICT
            existing = self._claims.get(request.idempotency_key)
            if existing is not None:
                request_hash, _owner, expiry = existing
                if request_hash != request.request_hash:
                    return CLAIM_CONFLICT
                # 过期租约不自动接管：外部系统可能已产生副作用，必须等待对账流程裁决。
                return CLAIM_IN_FLIGHT if expiry > now_ms else CLAIM_ABANDONED
            self._claims[request.idempotency_key] = (
                request.request_hash,
                owner_token,
                int(lease_expires_at_ms),
            )
            return CLAIM_ACQUIRED

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt:
        del attempt, evidence
        with self._lock:
            existing = self._receipts.get(request.idempotency_key)
            if existing is not None:
                if existing[0] != request.request_hash:
                    raise RuntimeError("CAPABILITY_IDEMPOTENCY_CONFLICT")
                return existing[1]
            claim = self._claims.get(request.idempotency_key)
            # 只有原租约持有者能封存终态，避免并发恢复者覆盖真实执行结果。
            if claim is None or claim[0] != request.request_hash or claim[1] != owner_token:
                raise RuntimeError("CAPABILITY_CLAIM_OWNERSHIP_LOST")
            self._receipts[request.idempotency_key] = (request.request_hash, receipt)
            return receipt

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None:
        with self._lock:
            self.late_outcomes.append({
                "idempotency_key": request.idempotency_key,
                "request_hash": request.request_hash,
                "owner_token": owner_token,
                "status": str(status),
                "output_digest": output_digest,
                "error_type": error_type,
                "observed_at_ms": int(observed_at_ms),
            })


class SQLiteCapabilityExecutionJournal:
    """委托持久化层处理 SQLite 细节的领域日志适配器。"""

    def __init__(self, ledger: Any) -> None:
        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path:
            raise TypeError("ledger must expose a durable canonical_db_path")
        self._repository = CapabilityExecutionRepository(db_path)

    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None:
        """查询已封存回执，供同摘要请求安全重放而不再执行适配器。"""
        row = self._repository.lookup(idempotency_key)
        if row is None:
            return None
        return row[0], _receipt_from_json(row[1])

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str:
        """原子尝试取得耐久租约；本地调用顺序不参与权威判断。"""
        # 将请求摘要、所有者和租约一起交给持久层原子裁决，避免本地时序成为权威。
        return self._repository.claim(
            {
                "idempotency_key": request.idempotency_key,
                "request_hash": request.request_hash,
                "request_id": request.request_id,
                "attempt_id": request.attempt_id,
                "capability_id": request.capability_id,
                "kind": request.kind.value,
                "owner_token": owner_token,
                "now_ms": int(now_ms),
                "lease_expires_at_ms": int(lease_expires_at_ms),
            }
        )

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt:
        """由租约所有者封存尝试、证据与回执；IN_DOUBT 保留为待恢复状态。"""
        # IN_DOUBT 不能伪装为完成：数据库明确保留待恢复状态，阻止自动重复调用。
        finished_at_ms = int(attempt.finished_at_ms)
        receipt_json = self._repository.complete(
            {
                "idempotency_key": request.idempotency_key,
                "request_hash": request.request_hash,
                "request_id": request.request_id,
                "attempt_id": request.attempt_id,
                "capability_id": request.capability_id,
                "kind": request.kind.value,
                "owner_token": owner_token,
                "status": receipt.status.value,
                "attempt_json": _json({**asdict(attempt), "kind": attempt.kind.value}),
                "evidence_json": _json(asdict(evidence)),
                "receipt_json": _json(receipt.to_dict()),
                "finished_at_ms": finished_at_ms,
                "claim_state": (
                    "IN_DOUBT" if receipt.status is ReceiptStatus.IN_DOUBT else "COMPLETED"
                ),
            }
        )
        return _receipt_from_json(receipt_json)

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None:
        """追加晚到结果作为恢复线索，不改写先前已封存的终态回执。"""
        # 晚到结果只作为恢复证据追加，不会替换已封存的回执。
        self._repository.record_late_outcome(
            {
                "idempotency_key": request.idempotency_key,
                "request_hash": request.request_hash,
                "owner_token": owner_token,
                "status": str(status),
                "output_digest": output_digest,
                "error_type": error_type,
                "observed_at_ms": int(observed_at_ms),
            }
        )


def _json(value: Any) -> str:
    """以稳定 JSON 序列化耐久字段，供仓储保存和后续解释。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _receipt_from_json(raw: str) -> CapabilityReceipt:
    """从耐久 JSON 重建严格枚举契约，拒绝向 Broker 泄露松散字典。"""
    # 持久化数据在边界处重新还原为枚举与不可变契约，拒绝把松散 JSON 直接泄漏给调用方。
    payload = json.loads(raw)
    return CapabilityReceipt(
        receipt_id=str(payload["receipt_id"]),
        request_id=str(payload["request_id"]),
        attempt_id=str(payload["attempt_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        capability_id=str(payload["capability_id"]),
        kind=CapabilityKind(str(payload["kind"])),
        status=ReceiptStatus(str(payload["status"])),
        reason_code=payload.get("reason_code"),
        detail=str(payload.get("detail") or ""),
        grant_id=payload.get("grant_id"),
        evidence_ref=str(payload.get("evidence_ref") or ""),
        output=payload.get("output"),
        replayed=bool(payload.get("replayed")),
    )


__all__ = [
    "CLAIM_ABANDONED",
    "CLAIM_ACQUIRED",
    "CLAIM_CONFLICT",
    "CLAIM_IN_FLIGHT",
    "CLAIM_REPLAY",
    "CapabilityExecutionJournal",
    "InMemoryCapabilityExecutionJournal",
    "SQLiteCapabilityExecutionJournal",
]
