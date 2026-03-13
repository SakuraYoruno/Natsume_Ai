"""IDE Skill 执行层（Cursor / PyCharm / VSCode / 通用编辑器）。

将 ide_registry.py 的归一化 payload 转化为真正的进程启动操作。
使用标准库 subprocess + shutil，无需第三方依赖。

支持的 tool 值：
  cursor            → open_cursor skill
  pycharm           → run_pycharm_action skill
  codex             → codex_task skill（降级：打印任务到 stdout）
  open_file_in_editor → open_file_in_editor skill（自动检测编辑器）
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

IDE_EXEC_ERROR = "IDE_EXEC_ERROR"
IDE_NOT_FOUND = "IDE_NOT_FOUND"
IDE_PATH_MISSING = "IDE_PATH_MISSING"


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


def _degraded(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_DEGRADED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


# ---------------------------------------------------------------------------
# 编辑器命令检测
# ---------------------------------------------------------------------------

# 编辑器名称 → 可能的命令列表（按优先级排序）
_EDITOR_CMDS: dict[str, list[str]] = {
    "cursor": ["cursor"],
    "vscode": ["code", "code-insiders"],
    "pycharm": ["pycharm", "pycharm64", "charm"],
    "default": ["cursor", "code", "code-insiders", "pycharm", "notepad++", "notepad"],
}


def _find_editor_cmd(editor: str) -> str | None:
    """在 PATH 中查找可用的编辑器命令。"""
    candidates = _EDITOR_CMDS.get(editor.lower(), _EDITOR_CMDS["default"])
    for cmd in candidates:
        if shutil.which(cmd):
            return cmd
    return None


def _open_with_system(path: str) -> None:
    """使用系统默认程序打开文件（最终降级方案）。"""
    if sys.platform == "win32":
        import os
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# 各操作执行函数
# ---------------------------------------------------------------------------

def execute_open_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    """在 Cursor 中打开文件或项目。"""
    ts = now_ms()
    event_id = new_event_id()
    path = payload.get("path") or ""
    workspace = payload.get("workspace") or None
    request_id = payload.get("request_id")

    if not path:
        return _reject(event_id=event_id, reason_code=IDE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    cmd = _find_editor_cmd("cursor")
    if cmd is None:
        # 降级：尝试 vscode
        cmd = _find_editor_cmd("vscode")
    if cmd is None:
        return _degraded(event_id=event_id, reason_code=IDE_NOT_FOUND,
                         detail="cursor/code not found in PATH", ts_ms=ts, request_id=request_id)

    try:
        args = [cmd]
        if workspace:
            args.append(workspace)
        args.append(path)
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"open_cursor::{path[:80]}",
            "cmd": cmd,
            "path": path,
            "workspace": workspace,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("open_cursor failed: %s", exc)
        return _reject(event_id=event_id, reason_code=IDE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_run_pycharm_action(payload: dict[str, Any]) -> dict[str, Any]:
    """通过 PyCharm CLI 触发 action（pycharm --action <action_id>）。"""
    ts = now_ms()
    event_id = new_event_id()
    action = payload.get("action") or ""
    project_path = payload.get("project_path") or None
    request_id = payload.get("request_id")

    if not action:
        return _reject(event_id=event_id, reason_code="IDE_ACTION_MISSING",
                       detail="action is required", ts_ms=ts, request_id=request_id)

    cmd = _find_editor_cmd("pycharm")
    if cmd is None:
        return _degraded(event_id=event_id, reason_code=IDE_NOT_FOUND,
                         detail="pycharm/charm not found in PATH", ts_ms=ts, request_id=request_id)

    try:
        args = [cmd]
        if project_path:
            args.append(project_path)
        # PyCharm CLI 支持 --action 参数触发内置 action
        args.extend(["--action", action])
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"pycharm_action::{action}",
            "cmd": cmd,
            "action": action,
            "project_path": project_path,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("run_pycharm_action failed: %s", exc)
        return _reject(event_id=event_id, reason_code=IDE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_codex_task(payload: dict[str, Any]) -> dict[str, Any]:
    """提交编码任务到 Codex 兼容执行器（当前降级：记录任务到日志）。

    Codex CLI 尚未有稳定的本地 CLI 接口，此处降级处理：
    将任务内容记录到 logger，返回 DEGRADED 状态，
    等待后续接入真实 Codex/OpenAI CLI。
    """
    ts = now_ms()
    event_id = new_event_id()
    task = payload.get("task") or ""
    context = payload.get("context") or None
    request_id = payload.get("session_id") or payload.get("request_id")

    if not task:
        return _reject(event_id=event_id, reason_code="IDE_TASK_MISSING",
                       detail="task is required", ts_ms=ts, request_id=request_id)

    # 尝试查找 openai CLI（openai codex）
    openai_cmd = shutil.which("openai")
    if openai_cmd:
        try:
            args = [openai_cmd, "codex", task]
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = proc.communicate(timeout=60)
            return {
                "status": STATUS_EXECUTED,
                "event_id": event_id,
                "manifest_ref": payload.get("manifest_ref") or f"codex_task::{task[:60]}",
                "task": task,
                "stdout": stdout[:4096],
                "stderr": stderr[:1024],
                "returncode": proc.returncode,
                "request_id": request_id,
                "ts_ms": ts,
            }
        except Exception as exc:
            logger.warning("codex CLI failed, degrading: %s", exc)

    # 降级：记录任务
    logger.info("[codex_task] task=%r context=%r", task, context)
    return _degraded(
        event_id=event_id,
        reason_code="CODEX_CLI_UNAVAILABLE",
        detail="openai CLI not found; task logged only",
        ts_ms=ts,
        request_id=request_id,
    )


def execute_open_file_in_editor(payload: dict[str, Any]) -> dict[str, Any]:
    """用指定或自动检测的编辑器打开文件。"""
    ts = now_ms()
    event_id = new_event_id()
    path = payload.get("path") or ""
    editor = str(payload.get("editor") or "default").strip().lower()
    line = payload.get("line")
    column = payload.get("column")
    request_id = payload.get("request_id")

    if not path:
        return _reject(event_id=event_id, reason_code=IDE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    cmd = _find_editor_cmd(editor)
    if cmd is None:
        # 最终降级：系统默认程序
        try:
            _open_with_system(path)
            return {
                "status": STATUS_EXECUTED,
                "event_id": event_id,
                "manifest_ref": payload.get("manifest_ref") or f"open_file::{path[:80]}",
                "cmd": "system_default",
                "path": path,
                "request_id": request_id,
                "ts_ms": ts,
            }
        except Exception as exc:
            return _reject(event_id=event_id, reason_code=IDE_EXEC_ERROR,
                           detail=str(exc), ts_ms=ts, request_id=request_id)

    try:
        args = [cmd]
        # VSCode / Cursor 支持 --goto path:line:column
        if line is not None and cmd in ("code", "code-insiders", "cursor"):
            col_part = f":{column}" if column is not None else ""
            args.extend(["--goto", f"{path}:{line}{col_part}"])
        else:
            args.append(path)
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"open_file::{path[:80]}",
            "cmd": cmd,
            "path": path,
            "line": line,
            "column": column,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("open_file_in_editor failed: %s", exc)
        return _reject(event_id=event_id, reason_code=IDE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


# ---------------------------------------------------------------------------
# 统一分发入口
# ---------------------------------------------------------------------------

_ACTION_MAP = {
    "cursor": execute_open_cursor,
    "open": execute_open_cursor,          # open_cursor 的 action="open"
    "pycharm": execute_run_pycharm_action,
    "codex": execute_codex_task,
    "open_file_in_editor": execute_open_file_in_editor,
}


def execute_ide_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """根据 payload.tool / payload.action 分发到对应的 IDE 执行函数。"""
    tool = str(payload.get("tool") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()

    # 优先按 tool 分发
    handler = _ACTION_MAP.get(tool) or _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "IDE_UNKNOWN_ACTION",
            "detail": f"unknown ide tool/action: tool={tool!r} action={action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
