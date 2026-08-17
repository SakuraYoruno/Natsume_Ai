"""在历史围栏安全前提下为 Operation checkpoint 接管获取 Lease。

接管者必须在前任租约失效后获得更高 fencing token；该模块拒绝用重试循环跨过版本
冲突，并同时核对 checkpoint 中的历史租约与刚读取的活动租约以抵御并发接管。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import _status, _text, _version
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


async def acquire_checkpoint_takeover_lease(
    *,
    task_store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    attempt: Mapping[str, Any],
    historical_lease: Mapping[str, Any],
    owner_id: str,
    lease_ttl_ms: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """接管可继续的已提交 Attempt，返回刷新后的 Attempt 与新 Lease。"""
    attempt_record = dict(attempt)
    attempt_status = _status(attempt_record)
    if attempt_status == "SUCCEEDED":
        # 已成功的 Attempt 不再获取新租约：恢复路径只消费既有终态，避免
        # 迟到的接管者把完成任务重新开放为可执行状态。
        return attempt_record, None
    if attempt_status not in {"LEASED", "RUNNING", "RECOVERABLE"}:
        _blocked(f"committed checkpoint Attempt cannot continue from {attempt_status}")
    attempt_id = _required_text(attempt_record.get("attempt_id"), "attempt_id")
    previous = await _maybe_await(task_store.get_active_lease(attempt_id))
    expected_attempt_version = _version(attempt_record)
    lease_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_ATTEMPT",
        aggregate_id=attempt_id,
        aggregate_version=expected_attempt_version + 1,
        event_type="TASK_ATTEMPT_LEASED",
        attempt_id=attempt_id,
    )

    async def _acquire() -> Any:
        """用相同的版本、预约与 outbox 条件尝试一次持久化租约领取。"""
        return await _maybe_await(
            task_store.acquire_lease(
                attempt_id,
                owner_id=owner_id,
                ttl_ms=lease_ttl_ms,
                expected_attempt_version=expected_attempt_version,
                existing_operation_reservation_id=admitted.reservation_id,
                outbox=lease_outbox,
            )
        )

    try:
        claimed = await _acquire()
    except Exception as exc:
        if _text(getattr(exc, "reason_code", None)):
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed checkpoint is waiting for its former lease to expire",
            ) from exc
        try:
            # 第一次失败可能是响应丢失而非未写入；允许一次相同条件的回读式重试，
            # 之后统一阻塞，不能靠循环重试跨过前任租约或版本冲突。
            claimed = await _acquire()
        except Exception as replay_exc:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed checkpoint is waiting for its former lease to expire",
            ) from replay_exc
    if not isinstance(claimed, Mapping):
        _blocked("online takeover returned no durable lease")
    lease = dict(claimed)
    _assert_advanced_fence(
        lease,
        former=historical_lease,
        detail="historical owner",
    )
    if previous is not None:
        # 除了检查检查点里保存的历史租约，也比较刚读取到的活动租约，覆盖
        # 两个恢复者并发接管时“历史记录已过期”的竞态。
        _assert_advanced_fence(lease, former=previous, detail="former owner")
    if (
        _status(lease) != "ACTIVE"
        or _text(lease.get("attempt_id")) != attempt_id
        or _text(lease.get("owner_id")) != owner_id
    ):
        _blocked("online takeover lease binding is invalid")
    _required_positive_int(lease.get("fencing_token"), "takeover fencing_token")
    refreshed = await _maybe_await(task_store.get_attempt(attempt_id))
    if not isinstance(refreshed, Mapping):
        _blocked("online takeover lost its durable Attempt")
    refreshed_attempt = dict(refreshed)
    if (
        _status(refreshed_attempt) != "LEASED"
        or _text(refreshed_attempt.get("owner_id")) != owner_id
    ):
        _blocked("online takeover Attempt binding is invalid")
    return refreshed_attempt, lease


def _assert_advanced_fence(
    lease: Mapping[str, Any],
    *,
    former: Mapping[str, Any],
    detail: str,
) -> None:
    """确保新租约既不是旧 lease，也严格提升其 fencing token。"""
    if (
        _text(former.get("lease_id")) == _text(lease.get("lease_id"))
        or _required_positive_int(
            former.get("fencing_token"),
            f"{detail} fencing_token",
        )
        >= _required_positive_int(
            lease.get("fencing_token"),
            "takeover fencing_token",
        )
    ):
        _blocked(f"online takeover did not advance the {detail} fence")


def _required_text(value: Any, field_name: str) -> str:
    """读取 checkpoint 接管必需的非空标识字段。"""
    normalized = _text(value)
    if not normalized:
        _blocked(f"committed checkpoint has no {field_name}")
    return normalized


def _required_positive_int(value: Any, field_name: str) -> int:
    """读取严格正整数的 fence/版本字段，拒绝布尔和无效值。"""
    if type(value) is not int or value < 1:
        _blocked(f"committed checkpoint has invalid {field_name}")
    return value


def _blocked(detail: str) -> None:
    """以统一恢复必需错误阻止不安全的接管继续执行。"""
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


async def _maybe_await(value: Any) -> Any:
    """兼容同步与异步 Store 实现的返回值。"""
    return await value if inspect.isawaitable(value) else value


__all__ = ["acquire_checkpoint_takeover_lease"]
