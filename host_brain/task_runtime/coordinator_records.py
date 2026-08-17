"""受治理协调器使用的纯记录编解码器和值对象。

本模块不持有运行时端口、锁、协程任务或 Coordinator 引用；它集中定义持久化
记录如何被安全解释，供首次执行、并行结算和恢复路径共享。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .admission import AdmittedExecution
from .errors import (
    GOVERNED_ASSIGNMENT_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)

if TYPE_CHECKING:
    from .review_runtime import TaskNodeReviewGate


@dataclass(frozen=True, slots=True)
class _ParallelNodeOutcome:
    """从节点终态事件提炼出的并行结算输入，保留其可验证身份。"""
    node_id: str
    attempt_id: str | None
    status: str
    accepted: bool
    reason_code: str | None
    event: dict[str, Any]

    @classmethod
    def from_terminal_event(
        cls,
        *,
        node_id: str,
        event: Mapping[str, Any],
    ) -> "_ParallelNodeOutcome":
        """验证终态回执与预期节点匹配，再转换为结算可用的不可变结果。"""
        receipt = event.get("execution_receipt")
        if not isinstance(receipt, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} has no execution receipt",
            )
        receipt_node_id = _text(receipt.get("node_id"))
        status = _text(receipt.get("status")).upper()
        raw_attempt_id = receipt.get("attempt_id")
        attempt_id = _text(raw_attempt_id) or None
        accepted = receipt.get("accepted")
        if (
            receipt_node_id != node_id
            or not status
            or not isinstance(accepted, bool)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} returned an invalid terminal identity",
            )
        if status == "WAITING_APPROVAL":
            if raw_attempt_id is not None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {node_id} pre-execution review bound a TaskAttempt",
                )
        elif attempt_id is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} terminal identity has no TaskAttempt",
            )
        if accepted != (status == "ACCEPTED"):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel TaskNode {node_id} returned an inconsistent acceptance status",
            )
        return cls(
            node_id=node_id,
            attempt_id=attempt_id,
            status=status,
            accepted=accepted,
            reason_code=_text(receipt.get("reason_code")) or None,
            event=dict(event),
        )

def _assert_durable_identity(
    admitted: AdmittedExecution,
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
) -> None:
    """确认 durable 图/节点仍等于准入时冻结的身份、路由和依赖。"""
    graph_payload = _nested_mapping(graph, "graph")
    durable_graph_id = _text(graph.get("graph_id") or graph_payload.get("graph_id"))
    durable_revision = int(graph.get("revision") or graph_payload.get("revision") or 0)
    durable_root = _text(graph.get("root_work_order_id") or graph_payload.get("root_work_order_id"))
    raw_durable_result_node_id = graph_payload.get("result_node_id")
    durable_result_node_id = (
        _text(raw_durable_result_node_id)
        if raw_durable_result_node_id is not None
        else None
    )
    raw_completion_policy = graph_payload.get("completion_policy")
    durable_completion_policy = (
        _text(raw_completion_policy)
        if raw_completion_policy is not None
        else None
    )
    node_payload = _nested_mapping(node, "node")
    durable_node_id = _text(node.get("node_id") or node_payload.get("node_id"))
    durable_route = _text(node.get("route") or node_payload.get("route"))
    raw_dependencies = node_payload.get("dependencies")
    durable_dependencies = tuple(
        _text(item)
        for item in (raw_dependencies if isinstance(raw_dependencies, list) else ())
        if _text(item)
    )
    # 图修订、结果权限和完成策略均属于授权范围，不能只核对易见的 graph_id。
    if (
        durable_graph_id != admitted.graph_id
        or durable_revision != admitted.graph_revision
        or durable_root != admitted.work_order_id
        or durable_result_node_id != admitted.result_node_id
        or durable_completion_policy != admitted.completion_policy
        or durable_node_id != admitted.node_id
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable graph/node identity differs from admitted command",
        )
    if durable_route != admitted.worker_route:
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "durable TaskNode.route differs from the frozen worker route",
        )
    if durable_dependencies != admitted.dependencies:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable TaskNode.dependencies differ from the frozen DAG",
        )

def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    """规范化 mapping 或受支持序列化对象；其他输入一律视为 durable 冲突。"""
    if isinstance(value, Mapping):
        return dict(value)
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        payload = serializer()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"{field_name} must be a mapping")

def _nested_mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    """安全取得嵌套记录，不让格式错误的可选字段污染调用方。"""
    nested = value.get(key)
    return dict(nested) if isinstance(nested, Mapping) else {}

def _node_input_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    """提取并去重节点输入引用，保持记录顺序作为可重放的展示顺序。"""
    raw = _nested_mapping(node, "node").get("input_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _node_output_contract(node: Mapping[str, Any]) -> dict[str, Any]:
    """返回节点声明的输出契约；缺失或畸形值不伪造默认契约。"""
    raw = _nested_mapping(node, "node").get("output_contract")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _record_evidence_refs(record: Mapping[str, Any]) -> tuple[str, ...]:
    """读取顶层或嵌套节点的证据引用，并以稳定顺序去重。"""
    raw = record.get("evidence_refs") if isinstance(record, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        raw = _nested_mapping(record, "node").get("evidence_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))

def _review_evidence_refs(gate: "TaskNodeReviewGate") -> tuple[str, ...]:
    """把审查门的标识投影为可随终态事件传播的证据引用。"""
    refs: list[str] = []
    if _text(gate.review_id):
        refs.append(f"review://{_text(gate.review_id)}")
    if _text(gate.checkpoint_id):
        refs.append(_text(gate.checkpoint_id))
    return tuple(dict.fromkeys(refs))

def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()

def _status(record: Mapping[str, Any]) -> str:
    """读取顶层或节点/图嵌套状态并规范为大写；非记录输入返回空。"""
    if not isinstance(record, Mapping):
        return ""
    nested = record.get("node") if isinstance(record.get("node"), Mapping) else record.get("graph")
    return _text(record.get("status") or (nested or {}).get("status")).upper()

def _version(record: Mapping[str, Any]) -> int:
    """取得精确的非负状态版本，作为所有乐观并发写入的围栏。"""
    value = record.get("status_version") if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable record lacks an exact non-negative status_version",
        )
    return value

def _required_record_text(record: Mapping[str, Any], key: str) -> str:
    """读取不可为空的 durable 文本字段，避免缺失身份继续传播。"""
    value = _text(record.get(key)) if isinstance(record, Mapping) else ""
    if not value:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} is required")
    return value

def _record_positive_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} must be positive")
    return value

def _record_non_negative_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"durable {key} must be a non-negative integer",
        )
    return value

def _optional_record_non_negative_int(
    record: Mapping[str, Any],
    key: str,
) -> int | None:
    value = record.get(key) if isinstance(record, Mapping) else None
    if value is None:
        return None
    return _record_non_negative_int(record, key)

def _reservation_result(value: Any) -> tuple[bool, dict[str, Any]]:
    """规范化预约结果；领取是否成功与 reservation 本体必须同时可信。"""
    if isinstance(value, Mapping):
        acquired = value.get("acquired")
        reservation = value.get("reservation")
    else:
        acquired = getattr(value, "acquired", None)
        reservation = getattr(value, "reservation", None)
    if not isinstance(acquired, bool) or not isinstance(reservation, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "reserve_operation returned an invalid result",
        )
    return acquired, dict(reservation)

def _evidence_refs(value: Mapping[str, Any]) -> tuple[str, ...]:
    raw = value.get("evidence_refs") if isinstance(value, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _attempt_id(admitted: AdmittedExecution, *, attempt_no: int) -> str:
    """以冻结图、节点、分配与序号派生确定性 Attempt 标识。"""
    material = (
        f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}/"
        f"{admitted.assignment_id}/{int(attempt_no)}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _exception_reason(exc: BaseException, default: str) -> str:
    return (
        _text(getattr(exc, "reason_code", None))
        or _text(default)
        or exc.__class__.__name__.upper()
    )

__all__ = [
    "_ParallelNodeOutcome",
    "_attempt_id",
    "_assert_durable_identity",
    "_evidence_refs",
    "_exception_reason",
    "_mapping",
    "_nested_mapping",
    "_node_input_refs",
    "_node_output_contract",
    "_optional_record_non_negative_int",
    "_record_evidence_refs",
    "_record_non_negative_int",
    "_record_positive_int",
    "_required_record_text",
    "_reservation_result",
    "_review_evidence_refs",
    "_status",
    "_text",
    "_version",
]
