"""IDE skill pack: Cursor / PyCharm / Codex wrappers.

Handlers are intentionally lightweight and return normalized payloads so they
can be safely invoked through the governed skill pipeline.
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill


class IDESkillProvider:
    @skill(
        skill_name="open_cursor",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="Open a file or project in Cursor.",
    )
    def open_cursor(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "OK",
            "tool": "cursor",
            "action": "open",
            "path": str((args or {}).get("path") or "").strip(),
            "workspace": str((args or {}).get("workspace") or "").strip() or None,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="run_pycharm_action",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="Trigger a PyCharm action by action_id.",
    )
    def run_pycharm_action(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "OK",
            "tool": "pycharm",
            "action": str((args or {}).get("action_id") or "").strip(),
            "project_path": str((args or {}).get("project_path") or "").strip() or None,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="codex_task",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="Submit a coding task to Codex-compatible executor.",
    )
    def codex_task(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        task = str((args or {}).get("task") or "").strip()
        context = str((args or {}).get("context") or "").strip()
        return {
            "status": "OK",
            "tool": "codex",
            "task": task,
            "context": context or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="open_file_in_editor",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="Open a file in the preferred editor (auto-detected or specified via 'editor' arg).",
    )
    def open_file_in_editor(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """通用文件打开 Skill，不绑定具体 IDE。

        Args（通过 args 传入）：
            path (str): 要打开的文件或目录路径（必填）
            editor (str): 指定编辑器，可选值 cursor / pycharm / vscode / default
                          不填时由运行时自动检测
            line (int): 跳转到指定行号（可选）
            column (int): 跳转到指定列号（可选）
        """
        path = str((args or {}).get("path") or "").strip()
        editor = str((args or {}).get("editor") or "default").strip().lower()
        line = (args or {}).get("line")
        column = (args or {}).get("column")
        try:
            line_num = int(line) if line is not None else None
        except (TypeError, ValueError):
            line_num = None
        try:
            col_num = int(column) if column is not None else None
        except (TypeError, ValueError):
            col_num = None
        return {
            "status": "OK",
            "tool": "open_file_in_editor",
            "path": path,
            "editor": editor,
            "line": line_num,
            "column": col_num,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }


def build_ide_skill_registry(provider: IDESkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or IDESkillProvider())
    return registry
