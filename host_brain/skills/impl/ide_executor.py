"""IDE skill executors for Cursor, PyCharm, and file opening."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"

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


_EDITOR_CMDS: dict[str, list[str]] = {
    "cursor": ["cursor"],
    "vscode": ["code", "code-insiders"],
    "pycharm": ["pycharm", "pycharm64", "charm"],
    "default": ["cursor", "code", "code-insiders", "pycharm", "notepad++", "notepad"],
}


def _find_editor_cmd(editor: str) -> str | None:
    for cmd in _EDITOR_CMDS.get(editor.lower(), _EDITOR_CMDS["default"]):
        if shutil.which(cmd):
            return cmd
    return None


def _open_with_system(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def execute_open_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    path = str(payload.get("path") or payload.get("workspace") or "").strip()
    request_id = payload.get("request_id")
    if not path:
        return _reject(
            event_id=event_id,
            reason_code=IDE_PATH_MISSING,
            detail="path is required",
            ts_ms=ts,
            request_id=request_id,
        )
    cmd = _find_editor_cmd("cursor")
    if cmd is None:
        return _reject(
            event_id=event_id,
            reason_code=IDE_NOT_FOUND,
            detail="cursor command not found",
            ts_ms=ts,
            request_id=request_id,
        )
    try:
        subprocess.Popen([cmd, path])
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"cursor::{path[:80]}",
            "cmd": cmd,
            "path": path,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("open_cursor failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


def execute_run_pycharm_action(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    action = str(payload.get("action") or "").strip()
    project_path = str(payload.get("project_path") or "").strip()
    request_id = payload.get("request_id")
    if not action:
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail="action is required",
            ts_ms=ts,
            request_id=request_id,
        )
    cmd = _find_editor_cmd("pycharm")
    if cmd is None:
        return _reject(
            event_id=event_id,
            reason_code=IDE_NOT_FOUND,
            detail="pycharm command not found",
            ts_ms=ts,
            request_id=request_id,
        )
    try:
        args = [cmd]
        if project_path:
            args.append(project_path)
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"pycharm::{action}",
            "cmd": cmd,
            "action": action,
            "project_path": project_path or None,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("run_pycharm_action failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


def execute_open_file_in_editor(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    path = str(payload.get("path") or "").strip()
    editor = str(payload.get("editor") or "default").strip().lower()
    line = payload.get("line")
    column = payload.get("column")
    request_id = payload.get("request_id")
    if not path:
        return _reject(
            event_id=event_id,
            reason_code=IDE_PATH_MISSING,
            detail="path is required",
            ts_ms=ts,
            request_id=request_id,
        )
    cmd = _find_editor_cmd(editor)
    if cmd is None:
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
            return _reject(
                event_id=event_id,
                reason_code=IDE_EXEC_ERROR,
                detail=str(exc),
                ts_ms=ts,
                request_id=request_id,
            )
    try:
        args = [cmd]
        if line is not None and cmd in {"code", "code-insiders", "cursor"}:
            col_part = f":{column}" if column is not None else ""
            args.extend(["--goto", f"{path}:{line}{col_part}"])
        else:
            args.append(str(Path(path)))
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
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


_ACTION_MAP = {
    "cursor": execute_open_cursor,
    "open": execute_open_cursor,
    "pycharm": execute_run_pycharm_action,
    "open_file_in_editor": execute_open_file_in_editor,
}


def execute_ide_skill(payload: dict[str, Any]) -> dict[str, Any]:
    tool = str(payload.get("tool") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
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
