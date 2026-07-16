"""Exact read-after-write reconciliation for Operation COMMIT."""

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
    response_loss: Exception | None = None
    try:
        committed = await _maybe_await(commit())
    except Exception as exc:
        response_loss = exc
        committed = None
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
        if response_loss is not None:
            raise response_loss
        raise
    return checkpoint["reservation"]


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["commit_or_reconcile_exact"]
