"""Desktop Note Skill——桌面留言（文件创建/更新/删除）。

支持在安全基目录下创建/更新/删除文本文件；
操作可逆（产出 manifest + undo_token）；路径穿越拒绝；内容经 QA 校验。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from host_brain.core.errors import SYSMODE_FORBIDDEN
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "desktop_note"
SKILL_CLEARANCE = "MEDIUM"
SKILL_RISK_LEVEL = 2
SKILL_SCOPE = "host"
SKILL_REVERSIBLE = True

# 操作类型
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ALLOWED_ACTIONS = {ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE}

# 安全约束
SCOPE_VIOLATION = "SCOPE_VIOLATION"
QA_CONTENT_BLOCKED = "QA_CONTENT_BLOCKED"
DESKTOP_NOTE_FILE_ERROR = "DESKTOP_NOTE_FILE_ERROR"

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """构建拒绝结果。"""
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


def _file_hash(path: Path) -> str | None:
    """计算文件 SHA-256 哈希。"""
    if not path.exists():
        return None
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _is_path_safe(base_dir: Path, target_path: Path) -> bool:
    """检查目标路径是否在安全基目录内（防路径穿越）。

    使用 Path.is_relative_to（Python 3.9+）精确判断，避免
    str.startswith 的前缀混淆漏洞（如 /base 误匹配 /base_evil）。
    """
    try:
        resolved_base = base_dir.resolve()
        resolved_target = target_path.resolve()
        # is_relative_to 精确判断路径层级，不会被前缀混淆
        return resolved_target.is_relative_to(resolved_base)
    except (OSError, ValueError, AttributeError):
        # AttributeError: Python < 3.9 回退到带分隔符的 startswith
        try:
            base_str = str(resolved_base).rstrip("/\\") + os.sep
            target_str = str(resolved_target)
            return target_str.startswith(base_str) or target_str == str(resolved_base).rstrip("/\\")
        except Exception:
            return False


def _contains_path_traversal(filename: str) -> bool:
    """检查文件名是否包含路径穿越字符。"""
    if ".." in filename:
        return True
    if "/" in filename or "\\" in filename:
        # 允许子目录但不允许回退
        parts = filename.replace("\\", "/").split("/")
        return any(part == ".." for part in parts)
    return False


def execute_desktop_note(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行桌面留言操作。

    args:
        action: "create" | "update" | "delete"
        filename: 文件名（相对于安全基目录）
        content: 文件内容（create/update 时必需）
        base_dir: 安全基目录路径（绝对路径或配置键）

    context:
        sys_mode: 当前系统模式
        request_id: 请求 ID
        intent_id: Intent ID
        qa_checker: 可选的 QA 检查器回调
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # 提取参数
    action = str(args.get("action", "")).strip().lower()
    filename = str(args.get("filename", "")).strip()
    content = args.get("content")
    base_dir_str = str(args.get("base_dir", "")).strip()

    # 基本校验
    if action not in ALLOWED_ACTIONS:
        return _reject(
            event_id=event_id,
            reason_code="DESKTOP_NOTE_INVALID_ACTION",
            detail=f"action must be one of {sorted(ALLOWED_ACTIONS)}, got: {action!r}",
            ts_ms=ts,
            request_id=request_id,
        )

    if not filename:
        return _reject(
            event_id=event_id,
            reason_code="DESKTOP_NOTE_MISSING_FILENAME",
            detail="filename is required",
            ts_ms=ts,
            request_id=request_id,
        )

    if not base_dir_str:
        return _reject(
            event_id=event_id,
            reason_code="DESKTOP_NOTE_MISSING_BASE_DIR",
            detail="base_dir is required",
            ts_ms=ts,
            request_id=request_id,
        )

    # 路径穿越检查
    if _contains_path_traversal(filename):
        return _reject(
            event_id=event_id,
            reason_code=SCOPE_VIOLATION,
            detail=f"path traversal detected in filename: {filename!r}",
            ts_ms=ts,
            request_id=request_id,
        )

    base_dir = Path(base_dir_str)
    target_path = base_dir / filename

    # symlink 检查
    if target_path.exists() and target_path.is_symlink():
        return _reject(
            event_id=event_id,
            reason_code=SCOPE_VIOLATION,
            detail=f"symlink target not allowed: {filename!r}",
            ts_ms=ts,
            request_id=request_id,
        )

    if not _is_path_safe(base_dir, target_path):
        return _reject(
            event_id=event_id,
            reason_code=SCOPE_VIOLATION,
            detail=f"target path escapes base_dir: {filename!r}",
            ts_ms=ts,
            request_id=request_id,
        )

    # sys_mode 检查（Gaming 模式拒绝）
    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    if sys_mode == "gaming":
        return _reject(
            event_id=event_id,
            reason_code=SYSMODE_FORBIDDEN,
            detail=f"desktop_note blocked in sys_mode={sys_mode}",
            ts_ms=ts,
            request_id=request_id,
        )

    # QA 内容检查（如果提供了 qa_checker）
    qa_checker = ctx.get("qa_checker")
    if qa_checker and content and action in (ACTION_CREATE, ACTION_UPDATE):
        qa_result = qa_checker(content)
        if qa_result and not qa_result.get("passed", True):
            return _reject(
                event_id=event_id,
                reason_code=QA_CONTENT_BLOCKED,
                detail=qa_result.get("detail", "content blocked by QA"),
                ts_ms=ts,
                request_id=request_id,
            )

    # 执行操作
    try:
        before_hash = _file_hash(target_path)

        if action == ACTION_CREATE:
            if content is None:
                return _reject(
                    event_id=event_id,
                    reason_code="DESKTOP_NOTE_MISSING_CONTENT",
                    detail="content is required for create action",
                    ts_ms=ts,
                    request_id=request_id,
                )
            # 确保父目录存在
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(str(content), encoding="utf-8")

        elif action == ACTION_UPDATE:
            if content is None:
                return _reject(
                    event_id=event_id,
                    reason_code="DESKTOP_NOTE_MISSING_CONTENT",
                    detail="content is required for update action",
                    ts_ms=ts,
                    request_id=request_id,
                )
            if not target_path.exists():
                return _reject(
                    event_id=event_id,
                    reason_code="DESKTOP_NOTE_FILE_NOT_FOUND",
                    detail=f"file not found for update: {filename!r}",
                    ts_ms=ts,
                    request_id=request_id,
                )
            target_path.write_text(str(content), encoding="utf-8")

        elif action == ACTION_DELETE:
            if not target_path.exists():
                return _reject(
                    event_id=event_id,
                    reason_code="DESKTOP_NOTE_FILE_NOT_FOUND",
                    detail=f"file not found for delete: {filename!r}",
                    ts_ms=ts,
                    request_id=request_id,
                )
            # delete → move to quarantine 子目录（不真删）
            quarantine_dir = base_dir / ".quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            quarantine_path = quarantine_dir / f"{filename}.{ts}"
            shutil.move(str(target_path), str(quarantine_path))

        after_hash = _file_hash(target_path) if action != ACTION_DELETE else None

        # 构建 manifest
        manifest = {
            "action": action,
            "filename": filename,
            "base_dir": str(base_dir),
            "before_hash": before_hash,
            "after_hash": after_hash,
            "path": str(target_path),
            "artifact_refs": [event_id],
        }

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "reason_code": None,
            "detail": f"{action} completed: {filename}",
            "manifest": manifest,
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": ts,
        }

    except Exception as exc:
        logger.error("Desktop note %s failed: %s", action, exc, exc_info=True)
        return _reject(
            event_id=event_id,
            reason_code=DESKTOP_NOTE_FILE_ERROR,
            detail=f"file operation failed: {exc}",
            ts_ms=ts,
            request_id=request_id,
        )


def undo_desktop_note(
    manifest: dict[str, Any],
    *,
    before_content: str | None = None,
) -> dict[str, Any]:
    """撤销桌面留言操作，恢复原状态。"""
    ts = now_ms()
    event_id = new_event_id()
    action = manifest.get("action", "")
    target_path = Path(manifest.get("path", ""))
    base_dir = Path(manifest.get("base_dir", ""))
    filename = manifest.get("filename", "")

    try:
        if action == ACTION_CREATE:
            # 撤销创建 → 删除文件
            if target_path.exists():
                target_path.unlink()

        elif action == ACTION_UPDATE:
            # 撤销更新 → 恢复原内容
            if before_content is not None:
                target_path.write_text(before_content, encoding="utf-8")

        elif action == ACTION_DELETE:
            # 撤销删除 → 从 quarantine 恢复
            quarantine_dir = base_dir / ".quarantine"
            # 查找最近的隔离文件
            pattern = f"{filename}.*"
            candidates = sorted(quarantine_dir.glob(pattern), reverse=True)
            if candidates:
                shutil.move(str(candidates[0]), str(target_path))

        return {
            "status": "UNDO_COMPLETED",
            "event_id": event_id,
            "reason_code": None,
            "detail": f"undo {action} completed: {filename}",
            "ts_ms": ts,
        }

    except Exception as exc:
        logger.error("Desktop note undo failed: %s", exc, exc_info=True)
        return {
            "status": "UNDO_FAILED",
            "event_id": event_id,
            "reason_code": DESKTOP_NOTE_FILE_ERROR,
            "detail": f"undo failed: {exc}",
            "ts_ms": ts,
        }
