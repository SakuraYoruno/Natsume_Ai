"""文件系统 Skill 包（P1 优先级）。

提供对本地文件系统的读写操作能力，支撑 PRD S2/S3 场景（开发协作 + 本地文件产物）。

风险分级说明：
  risk_level=2  只读操作（file_read / file_list）
  risk_level=3  写操作（file_write / file_append）
  risk_level=4  破坏性操作（file_delete），require_confirm=True

所有 handler 均为轻量归一化层，返回标准化 payload，
由上层运行时（FileAgent / HostExecutor）负责实际 I/O 执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill


class FileSkillProvider:
    """文件系统 Skill 集合。

    所有方法通过 @skill 装饰器注册元数据，
    由 build_file_skill_registry() 统一扫描注册。
    """

    @skill(
        skill_name="file_read",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="读取本地文件内容（只读，不修改文件）。",
    )
    def file_read(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """读取本地文件内容。

        Args:
            path (str): 文件绝对路径或相对路径（必填）
            encoding (str): 文件编码，默认 utf-8
            max_bytes (int): 最大读取字节数，默认 102400（100KB），防止超大文件
            offset_line (int): 从第几行开始读取（从 1 计，默认 1）
            limit_lines (int): 最多读取行数（默认不限制）
        """
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
            "status": "OK",
            "tool": "file",
            "action": "read",
            "path": path,
            "encoding": encoding,
            "max_bytes": max_bytes,
            "offset_line": offset_line,
            "limit_lines": limit_lines,
            "manifest_ref": f"file_read::{path}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="file_write",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="写入内容到本地文件（覆盖模式，自动备份原文件）。",
    )
    def file_write(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """写入内容到本地文件（覆盖模式）。

        Args:
            path (str): 目标文件路径（必填）
            content (str): 要写入的内容（必填）
            encoding (str): 文件编码，默认 utf-8
            create_dirs (bool): 是否自动创建父目录（默认 True）
            backup (bool): 写入前是否备份原文件（默认 True，可逆保障）
        """
        path = str((args or {}).get("path") or "").strip()
        content = str((args or {}).get("content") or "")
        encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
        create_dirs_raw = (args or {}).get("create_dirs")
        create_dirs = bool(create_dirs_raw) if create_dirs_raw is not None else True
        backup_raw = (args or {}).get("backup")
        backup = bool(backup_raw) if backup_raw is not None else True

        return {
            "status": "OK",
            "tool": "file",
            "action": "write",
            "path": path,
            "content": content,
            "encoding": encoding,
            "create_dirs": create_dirs,
            "backup": backup,
            "manifest_ref": f"file_write::{path}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="file_append",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="追加内容到本地文件末尾（不覆盖原有内容）。",
    )
    def file_append(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """追加内容到文件末尾。

        Args:
            path (str): 目标文件路径（必填）
            content (str): 要追加的内容（必填）
            encoding (str): 文件编码，默认 utf-8
            newline (bool): 追加前是否先插入换行符（默认 True）
            create_if_missing (bool): 文件不存在时是否自动创建（默认 True）
        """
        path = str((args or {}).get("path") or "").strip()
        content = str((args or {}).get("content") or "")
        encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
        newline_raw = (args or {}).get("newline")
        newline = bool(newline_raw) if newline_raw is not None else True
        create_if_missing_raw = (args or {}).get("create_if_missing")
        create_if_missing = bool(create_if_missing_raw) if create_if_missing_raw is not None else True

        return {
            "status": "OK",
            "tool": "file",
            "action": "append",
            "path": path,
            "content": content,
            "encoding": encoding,
            "newline": newline,
            "create_if_missing": create_if_missing,
            "manifest_ref": f"file_append::{path}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="file_delete",
        clearance="MEDIUM",
        risk_level=4,
        scope="system",
        reversible=False,
        require_confirm=True,
        description="删除本地文件（不可逆，需用户确认；支持移入回收站模式）。",
    )
    def file_delete(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """删除本地文件（不可逆操作，需确认）。

        Args:
            path (str): 要删除的文件路径（必填）
            trash (bool): 是否移入系统回收站而非永久删除（默认 True，更安全）
            dry_run (bool): 仅模拟执行，不实际删除（默认 False）
        """
        path = str((args or {}).get("path") or "").strip()
        trash_raw = (args or {}).get("trash")
        trash = bool(trash_raw) if trash_raw is not None else True
        dry_run_raw = (args or {}).get("dry_run")
        dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

        return {
            "status": "OK",
            "tool": "file",
            "action": "delete",
            "path": path,
            "trash": trash,
            "dry_run": dry_run,
            "manifest_ref": f"file_delete::{path}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="file_list",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="列出目录内容（只读，支持过滤和递归）。",
    )
    def file_list(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """列出目录内容。

        Args:
            path (str): 目录路径（必填）
            pattern (str): 文件名过滤模式，支持 glob（如 *.py，默认 *）
            recursive (bool): 是否递归列出子目录（默认 False）
            include_hidden (bool): 是否包含隐藏文件（默认 False）
            max_items (int): 最多返回条目数（默认 200）
        """
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
            "status": "OK",
            "tool": "file",
            "action": "list",
            "path": path,
            "pattern": pattern,
            "recursive": recursive,
            "include_hidden": include_hidden,
            "max_items": max_items,
            "manifest_ref": f"file_list::{path}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }


def build_file_skill_registry(provider: FileSkillProvider | None = None) -> SkillRegistry:
    """构建包含文件系统 Skills 的 SkillRegistry。

    文件系统 Skill 列表：
    - file_read：读取文件内容（risk=2，只读）
    - file_write：写入文件（risk=3，覆盖，自动备份）
    - file_append：追加文件内容（risk=3，追加）
    - file_delete：删除文件（risk=4，不可逆，需确认）
    - file_list：列出目录内容（risk=2，只读）

    Returns:
        已注册 5 个文件系统 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or FileSkillProvider())
    return registry
