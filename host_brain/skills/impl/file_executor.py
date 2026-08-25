"""文件系统 Skill 执行层。

将 file_registry.py 的归一化 payload 转化为真正的 I/O 操作。
所有操作均在统一的受治理 Skill 执行链路授权后，由本模块直接执行。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

# 状态常量
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"

# 错误码
FILE_EXEC_ERROR = "FILE_EXEC_ERROR"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
FILE_PATH_MISSING = "FILE_PATH_MISSING"
FILE_CONTENT_MISSING = "FILE_CONTENT_MISSING"


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


def execute_file_read(payload: dict[str, Any]) -> dict[str, Any]:
    """执行文件读取操作。

    payload 来自 FileSkillProvider.file_read() 的返回值。
    """
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    encoding = payload.get("encoding") or "utf-8"
    max_bytes = int(payload.get("max_bytes") or 102400)
    offset_line = max(1, int(payload.get("offset_line") or 1))
    limit_lines = payload.get("limit_lines")
    request_id = payload.get("request_id") or payload.get("session_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=FILE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)
    if not target.exists():
        return _reject(event_id=event_id, reason_code=FILE_NOT_FOUND,
                       detail=f"file not found: {path_str}", ts_ms=ts, request_id=request_id)

    try:
        raw = target.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
        text = raw.decode(encoding, errors="replace")
        lines = text.splitlines(keepends=True)

        # 行偏移与行数限制
        start = offset_line - 1
        if limit_lines is not None:
            selected = lines[start: start + int(limit_lines)]
        else:
            selected = lines[start:]

        content = "".join(selected)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "content": content,
            "total_lines": len(lines),
            "returned_lines": len(selected),
            "truncated": len(raw) >= max_bytes,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("file_read failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=FILE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_file_write(payload: dict[str, Any]) -> dict[str, Any]:
    """执行文件写入操作（覆盖模式，自动备份）。"""
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    content = payload.get("content") or ""
    encoding = payload.get("encoding") or "utf-8"
    create_dirs = bool(payload.get("create_dirs", True))
    backup = bool(payload.get("backup", True))
    request_id = payload.get("request_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=FILE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)
    backup_path = None

    try:
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        if backup and target.exists():
            backup_path = target.with_suffix(f"{target.suffix}.bak.{ts}")
            shutil.copy2(str(target), str(backup_path))

        before_hash = _file_hash(target)
        target.write_text(content, encoding=encoding)
        after_hash = _file_hash(target)

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "backup_path": str(backup_path) if backup_path else None,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "bytes_written": len(content.encode(encoding)),
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("file_write failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=FILE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_file_append(payload: dict[str, Any]) -> dict[str, Any]:
    """执行文件追加操作。"""
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    content = payload.get("content") or ""
    encoding = payload.get("encoding") or "utf-8"
    newline = bool(payload.get("newline", True))
    create_if_missing = bool(payload.get("create_if_missing", True))
    request_id = payload.get("request_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=FILE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)

    if not target.exists() and not create_if_missing:
        return _reject(event_id=event_id, reason_code=FILE_NOT_FOUND,
                       detail=f"file not found: {path_str}", ts_ms=ts, request_id=request_id)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        before_size = target.stat().st_size if target.exists() else 0

        with target.open("a", encoding=encoding) as f:
            if newline and before_size > 0:
                f.write("\n")
            f.write(content)

        after_size = target.stat().st_size
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "bytes_appended": after_size - before_size,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("file_append failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=FILE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_file_delete(payload: dict[str, Any]) -> dict[str, Any]:
    """执行文件删除操作（优先移入回收站）。"""
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    trash = bool(payload.get("trash", True))
    dry_run = bool(payload.get("dry_run", False))
    request_id = payload.get("request_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=FILE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)
    if not target.exists():
        return _reject(event_id=event_id, reason_code=FILE_NOT_FOUND,
                       detail=f"file not found: {path_str}", ts_ms=ts, request_id=request_id)

    if dry_run:
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "dry_run": True,
            "detail": "dry_run: no actual deletion performed",
            "request_id": request_id,
            "ts_ms": ts,
        }

    try:
        trash_path = None
        if trash:
            try:
                import send2trash  # type: ignore[import-not-found]
                send2trash.send2trash(str(target))
                trash_path = "system_trash"
            except ImportError:
                # send2trash 不可用，降级为移入 .trash 目录
                trash_dir = target.parent / ".trash"
                trash_dir.mkdir(parents=True, exist_ok=True)
                trash_path = str(trash_dir / f"{target.name}.{ts}")
                shutil.move(str(target), trash_path)
        else:
            target.unlink()

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "trash_path": trash_path,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("file_delete failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=FILE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_file_list(payload: dict[str, Any]) -> dict[str, Any]:
    """执行目录列表操作。"""
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    pattern = payload.get("pattern") or "*"
    recursive = bool(payload.get("recursive", False))
    include_hidden = bool(payload.get("include_hidden", False))
    max_items = int(payload.get("max_items") or 200)
    request_id = payload.get("session_id") or payload.get("request_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=FILE_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)
    if not target.exists():
        return _reject(event_id=event_id, reason_code=FILE_NOT_FOUND,
                       detail=f"directory not found: {path_str}", ts_ms=ts, request_id=request_id)

    try:
        if recursive:
            all_paths = list(target.rglob(pattern))
        else:
            all_paths = list(target.glob(pattern))

        # 过滤隐藏文件
        if not include_hidden:
            all_paths = [p for p in all_paths if not any(
                part.startswith(".") for part in p.relative_to(target).parts
            )]

        # 截断
        truncated = len(all_paths) > max_items
        all_paths = all_paths[:max_items]

        items = []
        for p in all_paths:
            try:
                stat = p.stat()
                items.append({
                    "name": p.name,
                    "path": str(p),
                    "type": "dir" if p.is_dir() else "file",
                    "size": stat.st_size if p.is_file() else None,
                    "modified_ms": int(stat.st_mtime * 1000),
                })
            except OSError:
                pass

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "items": items,
            "total": len(items),
            "truncated": truncated,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("file_list failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=FILE_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _file_hash(path: Path) -> str | None:
    """计算文件 SHA-256 哈希。"""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# 统一分发入口
_ACTION_MAP = {
    "read": execute_file_read,
    "write": execute_file_write,
    "append": execute_file_append,
    "delete": execute_file_delete,
    "list": execute_file_list,
}


def execute_file_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """根据 payload.action 分发到对应的文件操作执行函数。

    payload 为 FileSkillProvider 各 handler 的返回值（tool="file"）。
    """
    action = str(payload.get("action") or "").strip().lower()
    handler = _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "FILE_UNKNOWN_ACTION",
            "detail": f"unknown file action: {action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
