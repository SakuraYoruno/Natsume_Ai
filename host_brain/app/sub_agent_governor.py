"""sub_agent_governor.py — 子 Agent 轻量级治理 + 闲聊极轻量 Governor（Phase XIV v4）。

两个职责：
1. chat_lite_governor：闲聊路径的极轻量 Governor（仅速率限制 + sys_mode 检查）
2. sub_agent_governor：子 Agent 内部的执行治理（Governor + 三联画审计）
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

_logger = logging.getLogger("Natsume.sub_agent_governor")


# ---------------------------------------------------------------------------
# 闲聊极轻量 Governor（~10 行核心逻辑）
# ---------------------------------------------------------------------------

# 简易内存速率限制器（进程级）
_rate_limit_state: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW_SEC = 10.0
_RATE_LIMIT_MAX_REQUESTS = 15  # 10 秒内最多 15 条（远高于正常对话频率）


def chat_lite_governor(
    *,
    session_id: str,
    request_id: str,
) -> dict[str, Any]:
    """闲聊路径极轻量 Governor。

    仅做两件事：
    1. sys_mode 检查（sleep/locked/maintenance 拦截）
    2. 速率限制（防刷）

    Returns:
        {"decision": "APPROVE"} 或 {"decision": "REJECT", "reason_code": "..."}
    """
    # ── 1. sys_mode 检查 ────────────────────────────────────────────
    sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower()
    if sys_mode in ("locked", "maintenance"):
        return {
            "decision": "REJECT",
            "reason_code": f"SYSMODE_FORBIDDEN_{sys_mode.upper()}",
        }
    # sleep 模式允许聊天（被动响应），但可选标记
    # gaming 模式允许聊天

    # ── 2. 速率限制 ─────────────────────────────────────────────────
    now = time.monotonic()
    window = _rate_limit_state.setdefault(session_id, [])
    # 清理过期记录
    cutoff = now - _RATE_LIMIT_WINDOW_SEC
    _rate_limit_state[session_id] = [t for t in window if t > cutoff]
    window = _rate_limit_state[session_id]

    if len(window) >= _RATE_LIMIT_MAX_REQUESTS:
        _logger.warning(
            "[%s] chat_lite_governor rate limit hit session=%s count=%d",
            request_id, session_id, len(window),
        )
        return {
            "decision": "REJECT",
            "reason_code": "RATE_LIMIT_EXCEEDED",
        }
    window.append(now)

    return {"decision": "APPROVE"}


# ---------------------------------------------------------------------------
# 子 Agent 执行治理（Governor + 三联画审计）
# ---------------------------------------------------------------------------

def sub_agent_pre_exec_check(
    *,
    agent_id: str,
    task: str,
    request_id: str,
    session_id: str,
) -> dict[str, Any]:
    """子 Agent 执行前的 Governor 检查。

    仅对执行型子 Agent（intent_agent）做完整门控。
    translate_agent / vision_agent 等只读型子 Agent 门控较轻。

    Returns:
        {"decision": "APPROVE"} 或 {"decision": "REJECT", "reason_code": "..."}
    """
    # sys_mode 门控：locked/maintenance 拒绝所有执行
    sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower()
    if sys_mode in ("locked", "maintenance"):
        return {
            "decision": "REJECT",
            "reason_code": f"SYSMODE_FORBIDDEN_{sys_mode.upper()}",
            "agent_id": agent_id,
        }

    # sleep 模式：拒绝执行类（intent），允许只读类（translate/vision）
    if sys_mode == "sleep" and agent_id == "intent_agent":
        return {
            "decision": "REJECT",
            "reason_code": "SYSMODE_SLEEP_NO_EXEC",
            "agent_id": agent_id,
        }

    # gaming 模式：拒绝 vision（白皮书要求禁止视觉/UI 自动化）
    if sys_mode == "gaming" and agent_id == "vision_agent":
        return {
            "decision": "REJECT",
            "reason_code": "SYSMODE_GAMING_NO_VISION",
            "agent_id": agent_id,
        }

    return {
        "decision": "APPROVE",
        "agent_id": agent_id,
    }


def build_sub_agent_audit_refs(
    *,
    agent_id: str,
    request_id: str,
    task: str,
    governor_decision: str,
    exec_status: str = "OK",
    latency_ms: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    """构建子 Agent 的三联画审计摘要（精简版）。

    只在执行型任务时才有意义。
    """
    return {
        "intent": {
            "agent_id": agent_id,
            "task_preview": task[:100],
            "request_id": request_id,
        },
        "governor": {
            "decision": governor_decision,
        },
        "exec": {
            "status": exec_status,
            "latency_ms": latency_ms,
            "error": error,
        },
    }
