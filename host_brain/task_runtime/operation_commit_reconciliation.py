"""Operation COMMIT 的精确读后写对账。

网络响应丢失后不能重新执行外部 worker；本模块只通过读取满足全部预约、租约、
fencing token、版本和摘要条件的 checkpoint 来确认提交已经发生。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .operation_checkpoint_validation import (
    assert_exact_committed_checkpoint,
    is_exact_operation_commit,
)
from .ports import TaskStorePort


async def commit_or_reconcile_exact(
    *,
    task_store: TaskStorePort,
    commit: Callable[[], Any],
    reservation_id: str,
    business_scope: str,
    operation_fingerprint: str,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    expected_version: int,
    receipt_ref: str,
    checkpoint_digest_ref: str,
) -> dict[str, Any]:
    """提交操作并回读精确 checkpoint；若响应丢失且无法证明提交则保留原异常。"""
    response_loss: Exception | None = None
    try:
        committed = await _maybe_await(commit())
    except Exception as exc:
        response_loss = exc
        committed = None
    # 正常响应也必须精确匹配；Store 返回任意“成功样”记录都不足以作为副作用依据。
    if response_loss is None and not is_exact_operation_commit(
        committed,
        reservation_id=reservation_id,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        attempt_id=attempt_id,
        lease_id=lease_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        expected_version=expected_version,
        receipt_ref=receipt_ref,
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "operation commit returned a non-exact durable checkpoint",
        )
    try:
        checkpoint = assert_exact_committed_checkpoint(
            await _maybe_await(
                task_store.get_committed_operation_checkpoint(reservation_id)
            ),
            reservation_id=reservation_id,
            business_scope=business_scope,
            operation_fingerprint=operation_fingerprint,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_version=expected_version,
            receipt_ref=receipt_ref,
            checkpoint_digest_ref=checkpoint_digest_ref,
        )
    except Exception:
        # 响应丢失时仅在精确 checkpoint 可见后成功；否则把原异常交给上层恢复处理。
        if response_loss is not None:
            raise response_loss
        raise
    return checkpoint["reservation"]


async def _maybe_await(value: Any) -> Any:
    """统一同步或异步 Store 回读与 commit 适配器返回值。"""
    return await value if inspect.isawaitable(value) else value


__all__ = ["commit_or_reconcile_exact"]
