"""冻结单个持久化 Operation recovery hold 的运行时边界。

当操作的外部效果无法安全判定时，先落下带图、节点、Attempt、预约和预算绑定的
恢复 case，再向上返回可恢复回执；这样后续处置有稳定事实可审计与签名确认。
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import TaskStorePort


async def persist_operation_recovery_hold(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
    budget: BudgetRuntimeContext,
    attempt_id: str,
    reason_code: str,
    checkpoint_state: str | None = None,
) -> dict[str, Any]:
    """在返回可恢复执行回执前持久化不确定性，禁止跳过该事实记录。"""

    result = task_store.record_operation_recovery_hold(
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
        attempt_id=str(attempt_id or "").strip(),
        reservation_id=admitted.reservation_id,
        budget_id=budget.budget_id,
        reason_code=str(reason_code or "").strip().upper(),
        checkpoint_state=checkpoint_state,
    )
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Operation recovery hold store returned no durable case",
        )
    # Store 返回的 case 必须是当前快照且携带内容摘要，避免错误 case 被作为恢复授权引用。
    case_ref = str(result.get("case_ref") or "").strip()
    digest = str(result.get("subject_sha256") or "").strip()
    if (
        not case_ref.startswith("operation-recovery-hold://sha256/")
        or not digest.startswith("sha256:")
        or result.get("current") is not True
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Operation recovery hold identity is invalid",
        )
    return dict(result)


__all__ = ["persist_operation_recovery_hold"]
