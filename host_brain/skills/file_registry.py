"""文件系统 Skill 包。

声明式 Provider：新增文件类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_file_read(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
    try:
        max_bytes = int((args or {}).get("max_bytes") or 102400)
    except (TypeError, ValueError):
        max_bytes = 102400
    try:
        offset_line = max(1, int((args or {}).get("offset_line") or 1))
    except (TypeError, ValueError):
        offset_line = 1
    try:
        limit_lines_raw = (args or {}).get("limit_lines")
        limit_lines = int(limit_lines_raw) if limit_lines_raw is not None else None
    except (TypeError, ValueError):
        limit_lines = None

    return {
        "path": path,
        "encoding": encoding,
        "max_bytes": max_bytes,
        "offset_line": offset_line,
        "limit_lines": limit_lines,
        "manifest_ref": f"file_read::{path}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
    }


def _normalize_file_write(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    content = str((args or {}).get("content") or "")
    encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
    create_dirs_raw = (args or {}).get("create_dirs")
    create_dirs = bool(create_dirs_raw) if create_dirs_raw is not None else True
    backup_raw = (args or {}).get("backup")
    backup = bool(backup_raw) if backup_raw is not None else True

    return {
        "path": path,
        "content": content,
        "encoding": encoding,
        "create_dirs": create_dirs,
        "backup": backup,
        "manifest_ref": f"file_write::{path}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_file_append(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    content = str((args or {}).get("content") or "")
    encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
    newline_raw = (args or {}).get("newline")
    newline = bool(newline_raw) if newline_raw is not None else True
    create_if_missing_raw = (args or {}).get("create_if_missing")
    create_if_missing = bool(create_if_missing_raw) if create_if_missing_raw is not None else True

    return {
        "path": path,
        "content": content,
        "encoding": encoding,
        "newline": newline,
        "create_if_missing": create_if_missing,
        "manifest_ref": f"file_append::{path}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_file_delete(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    trash_raw = (args or {}).get("trash")
    trash = bool(trash_raw) if trash_raw is not None else True
    dry_run_raw = (args or {}).get("dry_run")
    dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

    return {
        "path": path,
        "trash": trash,
        "dry_run": dry_run,
        "manifest_ref": f"file_delete::{path}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_file_list(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    pattern = str((args or {}).get("pattern") or "*").strip() or "*"
    recursive_raw = (args or {}).get("recursive")
    recursive = bool(recursive_raw) if recursive_raw is not None else False
    include_hidden_raw = (args or {}).get("include_hidden")
    include_hidden = bool(include_hidden_raw) if include_hidden_raw is not None else False
    try:
        max_items = max(1, min(1000, int((args or {}).get("max_items") or 200)))
    except (TypeError, ValueError):
        max_items = 200

    return {
        "path": path,
        "pattern": pattern,
        "recursive": recursive,
        "include_hidden": include_hidden,
        "max_items": max_items,
        "manifest_ref": f"file_list::{path}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
    }


class FileSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="file_read",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="读取本地文件内容（只读，不修改文件）。",
            tool="file",
            action="read",
            normalize_args=_normalize_file_read,
        ),
        DeclarativeSkillSpec(
            skill_name="file_write",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="写入内容到本地文件（覆盖模式，自动备份原文件）。",
            tool="file",
            action="write",
            normalize_args=_normalize_file_write,
        ),
        DeclarativeSkillSpec(
            skill_name="file_append",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="追加内容到本地文件末尾（不覆盖原有内容）。",
            tool="file",
            action="append",
            normalize_args=_normalize_file_append,
        ),
        DeclarativeSkillSpec(
            skill_name="file_delete",
            clearance="MEDIUM",
            risk_level=4,
            scope="system",
            reversible=False,
            require_confirm=True,
            description="删除本地文件（不可逆，需用户确认；支持移入回收站模式）。",
            tool="file",
            action="delete",
            normalize_args=_normalize_file_delete,
        ),
        DeclarativeSkillSpec(
            skill_name="file_list",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="列出目录内容（只读，支持过滤和递归）。",
            tool="file",
            action="list",
            normalize_args=_normalize_file_list,
        ),
    )


def build_file_skill_registry(provider: FileSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or FileSkillProvider())
    return registry
