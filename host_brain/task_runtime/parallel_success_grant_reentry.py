"""原子图重入时恢复不可变 node grant 授权。"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .node_execution_projection import NodeExecutionProjection
from .ports import ExecutionProjectionReaderPort


async def restore_prior_parallel_success_grants(
    *,
    admitted: AdmittedExecution,
    statuses: Mapping[str, str],
    grant_refs: dict[str, str],
    node_execution_projection: NodeExecutionProjection,
    projection_reader: ExecutionProjectionReaderPort | None,
) -> None:
    """恢复当前调用前已验收节点的 grant 引用。

    审查续跑会在长时间人工暂停后更新仍可运行节点的 grant；暂停前已验收节点的
    grant 则冻结在已提交 worker 投影中。这里只恢复这些历史节点，当前调用新执行
    的 worker 仍必须证明本次传入的实时 grant，不能借用历史授权。
    """

    if projection_reader is None:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "parallel success re-entry requires a projection reader",
        )
    for binding in admitted.nodes:
        if statuses.get(binding.node_id) != "ACCEPTED":
            continue
        # reservation 的 COMMITTED 回执是读取历史授权的唯一入口；不能直接从
        # 内存 grant_refs 保留值恢复，因为续跑调用可带来新的映射。
        reservation = await node_execution_projection.try_operation(
            admitted.for_node(binding.node_id).reservation_id
        )
        receipt_ref = _text((reservation or {}).get("receipt_ref"))
        if _status(reservation or {}) != "COMMITTED" or not receipt_ref:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode lacks a committed operation projection: "
                f"{binding.node_id}",
            )
        loaded = await _maybe_await(projection_reader.load(receipt_ref))
        if not isinstance(loaded, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode lacks its immutable worker projection: "
                f"{binding.node_id}",
            )
        try:
            projection = validate_worker_projection_payload(
                loaded,
                expected_projection_id=receipt_ref,
            )
        except WorkerProjectionStoreError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode worker projection is not canonical: "
                f"{binding.node_id}",
            ) from exc
        # 投影需同时匹配图、修订和节点，避免相同 receipt 格式的其他任务授权
        # 被嫁接到本图并行成功结算。
        historical_ref = _text(projection.get("node_grant_ref"))
        if (
            _text(projection.get("graph_id")) != admitted.graph_id
            or int(projection.get("graph_revision") or 0) != admitted.graph_revision
            or _text(projection.get("node_id")) != binding.node_id
            or not historical_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"accepted TaskNode projection authority drifted: {binding.node_id}",
            )
        grant_refs[binding.node_id] = historical_ref


async def _maybe_await(value: Any) -> Any:
    """兼容同步/异步投影读取端口；不影响读取历史事实的严格校验。"""
    return await value if inspect.isawaitable(value) else value


__all__ = ["restore_prior_parallel_success_grants"]
