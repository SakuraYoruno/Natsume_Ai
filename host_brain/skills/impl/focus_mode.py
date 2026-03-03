"""Focus Mode Skill——专注模式切换。

用户主动进入/退出专注模式；进入后抑制 LOW 优先级调度任务
和主动对话，仅保留 HIGH 优先级调度（如日记生成）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from host_brain.core.errors import SYSMODE_FORBIDDEN
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "focus_mode"
SKILL_CLEARANCE = "LOW"
SKILL_RISK_LEVEL = 1
SKILL_SCOPE = "system"
SKILL_REVERSIBLE = True

# Focus 状态
FOCUS_ACTIVE = "active"
FOCUS_INACTIVE = "inactive"

# 操作
ACTION_ENTER = "enter"
ACTION_EXIT = "exit"
ALLOWED_ACTIONS = {ACTION_ENTER, ACTION_EXIT}

# 默认专注时长（毫秒）: 90 分钟
DEFAULT_DURATION_MS = 90 * 60 * 1000

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"

# 回调类型
ModeChanger = Callable[[str], dict[str, Any]]


def execute_focus_mode(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行专注模式切换。

    args:
        action: "enter" | "exit"
        duration_ms: 专注时长（毫秒），默认 90 分钟
        reason: 进入原因（可选）

    context:
        sys_mode: 当前系统模式
        request_id / intent_id
        mode_changer: 系统模式切换回调 (new_mode -> result)
        focus_state: 当前焦点状态（active/inactive）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    action = str(args.get("action", "")).strip().lower()

    # 校验操作
    if action not in ALLOWED_ACTIONS:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": "FOCUS_INVALID_ACTION",
            "detail": f"action must be 'enter' or 'exit', got: {action!r}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    current_focus = str(ctx.get("focus_state", FOCUS_INACTIVE)).strip().lower()

    # 进入专注模式
    if action == ACTION_ENTER:
        # 如果当前不在 normal 模式，拒绝进入
        if sys_mode not in ("normal",):
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": SYSMODE_FORBIDDEN,
                "detail": f"cannot enter focus from sys_mode={sys_mode}",
                "request_id": request_id,
                "ts_ms": ts,
            }

        # 已在专注模式
        if current_focus == FOCUS_ACTIVE:
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": "FOCUS_ALREADY_ACTIVE",
                "detail": "already in focus mode",
                "request_id": request_id,
                "ts_ms": ts,
            }

        duration_ms = int(args.get("duration_ms", DEFAULT_DURATION_MS))
        reason = str(args.get("reason", "")).strip()
        expiry_ts = ts + duration_ms

        # 调用 mode_changer
        mode_changer: ModeChanger | None = ctx.get("mode_changer")
        change_result: dict[str, Any] | None = None
        if mode_changer:
            try:
                change_result = mode_changer("focus")
            except Exception as exc:
                logger.warning("Mode change to focus failed: %s", exc)
                return {
                    "status": STATUS_REJECTED,
                    "event_id": event_id,
                    "reason_code": "FOCUS_MODE_CHANGE_FAILED",
                    "detail": f"mode_changer failed: {exc}",
                    "request_id": request_id,
                    "ts_ms": ts,
                }

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "reason_code": None,
            "detail": f"focus mode entered for {duration_ms}ms",
            "focus_state": FOCUS_ACTIVE,
            "expiry_ts": expiry_ts,
            "duration_ms": duration_ms,
            "reason": reason,
            "change_result": change_result,
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": ts,
        }

    # 退出专注模式
    elif action == ACTION_EXIT:
        if current_focus != FOCUS_ACTIVE:
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": "FOCUS_NOT_ACTIVE",
                "detail": "not in focus mode",
                "request_id": request_id,
                "ts_ms": ts,
            }

        mode_changer = ctx.get("mode_changer")
        change_result = None
        if mode_changer:
            try:
                change_result = mode_changer("normal")
            except Exception as exc:
                logger.warning("Mode change from focus failed: %s", exc)
                return {
                    "status": STATUS_REJECTED,
                    "event_id": event_id,
                    "reason_code": "FOCUS_MODE_CHANGE_FAILED",
                    "detail": f"mode_changer failed: {exc}",
                    "request_id": request_id,
                    "ts_ms": ts,
                }

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "reason_code": None,
            "detail": "focus mode exited",
            "focus_state": FOCUS_INACTIVE,
            "change_result": change_result,
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": ts,
        }

    # unreachable
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": "FOCUS_UNKNOWN_ACTION",
        "detail": f"unknown action: {action}",
        "request_id": request_id,
        "ts_ms": ts,
    }
