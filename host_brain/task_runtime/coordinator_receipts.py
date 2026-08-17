"""执行回执值对象、构建器和终态事件渲染器。

本模块是纯函数边界：不持有运行时端口、锁、协程任务或 Coordinator，只把已冻结的
执行身份和持久化结果编码为可安全重放的对外事件。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT


@dataclass(frozen=True, slots=True)
class GovernedExecutionReceipt:
    """绑定命令、图修订、节点 Attempt 与副作用凭据的不可变执行回执。"""
    command_id: str
    work_order_id: str
    assignment_id: str
    executor: str
    worker_route: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str | None
    reservation_id: str
    receipt_ref: str | None
    payload_digest: str | None
    status: str
    verification_status: str
    accepted: bool
    replayed: bool
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """生成事件边界使用的独立字典，避免调用方修改内部 projection。"""
        return {
            "command_id": self.command_id,
            "work_order_id": self.work_order_id,
            "assignment_id": self.assignment_id,
            "executor": self.executor,
            "worker_route": self.worker_route,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "reservation_id": self.reservation_id,
            "receipt_ref": self.receipt_ref,
            "payload_digest": self.payload_digest,
            "status": self.status,
            "verification_status": self.verification_status,
            "accepted": self.accepted,
            "replayed": self.replayed,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "projection": dict(self.projection),
        }

def build_execution_receipt(
    admitted: AdmittedExecution,
    *,
    attempt_id: str | None,
    status: str,
    verification_status: str,
    accepted: bool,
    receipt_ref: str | None = None,
    payload_digest: str | None = None,
    replayed: bool | None = None,
    reason_code: str | None = None,
    evidence_refs: Sequence[str] = (),
    projection: Mapping[str, Any] | None = None,
) -> GovernedExecutionReceipt:
    """从准入快照派生回执，确保所有输出沿用同一图修订与节点身份。"""
    status_value = str(status)
    # WAITING_APPROVAL 尚未创建可归责的 Attempt，绑定 attempt_id 会伪造执行谱系。
    if status_value.strip().upper() == "WAITING_APPROVAL":
        if attempt_id is not None:
            raise ValueError("WAITING_APPROVAL receipt must not bind a TaskAttempt")
    return GovernedExecutionReceipt(
        command_id=admitted.command_id,
        work_order_id=admitted.work_order_id,
        assignment_id=admitted.assignment_id,
        executor=admitted.executor,
        worker_route=admitted.worker_route,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
        attempt_id=attempt_id,
        reservation_id=admitted.reservation_id,
        receipt_ref=receipt_ref,
        payload_digest=payload_digest,
        status=status_value,
        verification_status=str(verification_status),
        accepted=bool(accepted),
        replayed=admitted.replayed if replayed is None else bool(replayed),
        reason_code=str(reason_code or "").strip() or None,
        evidence_refs=tuple(dict.fromkeys(_text(item) for item in evidence_refs if _text(item))),
        projection=dict(projection or {}),
    )

def approved_commit_event(
    worker_commit: Mapping[str, Any],
    receipt: GovernedExecutionReceipt,
) -> dict[str, Any]:
    """将已获验收的 worker COMMIT 包装为带回执的批准终态事件。"""
    event = dict(worker_commit)
    event.update(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "governed": True,
            "replayed": receipt.replayed,
            "execution_receipt": receipt.to_dict(),
        }
    )
    return event

def terminal_event(receipt: GovernedExecutionReceipt) -> dict[str, Any]:
    """按是否验收渲染最终事件；拒绝事件不泄露 worker 的临时投影。"""
    if receipt.accepted:
        event = dict(receipt.projection)
        event.update({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "governed": True,
            "replayed": receipt.replayed,
            "execution_receipt": receipt.to_dict(),
        })
        return event
    return {
        "phase": "TERMINAL",
        "done": True,
        "decision": "REJECT",
        "reason_code": receipt.reason_code or GOVERNED_DURABLE_STATE_CONFLICT,
        "governed": True,
        "replayed": receipt.replayed,
        "execution_receipt": receipt.to_dict(),
    }

def _worker_receipt_ref(event: Mapping[str, Any]) -> str | None:
    """兼容直接或嵌套事件结构，提取 worker 已声明的持久化回执引用。"""
    direct = _text(event.get("receipt_ref"))
    if direct:
        return direct
    nested = event.get("execution_receipt")
    if isinstance(nested, Mapping):
        value = _text(nested.get("receipt_ref"))
        return value or None
    return None

def _digest_from_receipt_ref(receipt_ref: str | None) -> str | None:
    """仅在回执尾段确为 SHA-256 十六进制时恢复其内容摘要。"""
    if not receipt_ref:
        return None
    tail = receipt_ref.rsplit("/", 1)[-1]
    if len(tail) == 64 and all(char in "0123456789abcdef" for char in tail.lower()):
        return "sha256:" + tail.lower()
    return None

__all__ = [
    "GovernedExecutionReceipt",
    "_digest_from_receipt_ref",
    "_worker_receipt_ref",
    "approved_commit_event",
    "build_execution_receipt",
    "terminal_event",
]
