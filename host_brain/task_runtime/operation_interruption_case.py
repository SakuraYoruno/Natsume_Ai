"""与中断绑定的 Operation 恢复 case 校验辅助函数。

case 在 ``RECOVERY_REQUIRED`` 标记的同一持久化写入中创建；这里仅重新读取并核验
该快照，供回执和后续对账引用，绝不在异常路径中临时伪造恢复凭据。
"""

from __future__ import annotations

from typing import Any

from .coordinator_records import _mapping, _required_record_text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .ports import TaskStorePort


def require_interruption_recovery_case(
    *,
    store: TaskStorePort,
    reservation_id: str,
    expected_version: int,
    reason_code: str,
) -> dict[str, Any]:
    """重新读取与 ``RECOVERY_REQUIRED`` 原子写入的当前恢复 case。"""

    recovery_case = _mapping(
        store.get_current_operation_recovery_hold_for_reservation(
            reservation_id,
            expected_version=expected_version,
            reason_code=reason_code,
        ),
        field_name="interrupted operation recovery case",
    )
    # case_ref 与 subject 摘要是恢复处置绑定的最小证明，任何漂移都必须隔离为冲突。
    if (
        recovery_case.get("current") is not True
        or not _required_record_text(
            recovery_case,
            "case_ref",
        ).startswith("operation-recovery-hold://sha256/")
        or not _required_record_text(
            recovery_case,
            "subject_sha256",
        ).startswith("sha256:")
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "interrupted operation recovery case returned a drifting identity",
        )
    return recovery_case


def interruption_recovery_case_projection(
    recovery_case: dict[str, Any] | None,
) -> dict[str, str]:
    """抽取可随回执传播的恢复 case 引用与摘要，不泄露完整持久化记录。"""
    if recovery_case is None:
        return {}
    return {
        "recovery_case_ref": _required_record_text(
            recovery_case,
            "case_ref",
        ),
        "recovery_case_digest": _required_record_text(
            recovery_case,
            "subject_sha256",
        ),
        "recovery_case_kind": _required_record_text(
            recovery_case,
            "case_kind",
        ),
    }


__all__ = [
    "interruption_recovery_case_projection",
    "require_interruption_recovery_case",
]
