"""已接受 Host 系统模式效果的 canonical 内容寻址回执绑定。

该回执不替代 HardState 事件本身；它为 Acceptance dispatcher 提供一条可稳定
比较的 handler 结果引用，将冻结的模式切换身份与 ``APPLIED``、``REPLAYED``
或 ``NO_CHANGE`` 三种终态一同纳入摘要。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _receipt


def sys_mode_transition_handler_receipt_ref(
    payload: Mapping[str, Any],
    *,
    outcome_status: str,
) -> str:
    """为一条冻结模式效果生成与 outcome 绑定的 canonical handler 回执。

    执行期 Preview 上下文和 terminal proof 会先被排除：它们用于授权验证而非
    业务效果身份，若进入摘要会使同一历史事件因运行时对象差异无法重放比对。
    精确字段集则反过来防止未接受的新参数被悄然写进回执。
    """

    value = dict(payload or {})
    value.pop("_action_preview_context", None)
    value.pop("_action_preview_terminal_proof", None)
    required = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(value) != required:
        raise ValueError("sys_mode transition receipt payload drifted")
    status = str(outcome_status or "").strip().upper()
    if status not in {"APPLIED", "REPLAYED", "NO_CHANGE"}:
        raise ValueError("sys_mode transition outcome status is invalid")
    return _receipt(
        f"host-sys-mode-{status.lower().replace('_', '-')}",
        {**value, "handler_outcome_status": status},
    )


__all__ = ["sys_mode_transition_handler_receipt_ref"]
