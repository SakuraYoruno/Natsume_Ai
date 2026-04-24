"""IDE skill pack: Cursor / PyCharm / open-file wrappers."""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_open_cursor(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str((args or {}).get("path") or "").strip(),
        "workspace": str((args or {}).get("workspace") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_run_pycharm_action(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "action": str((args or {}).get("action_id") or "").strip(),
        "project_path": str((args or {}).get("project_path") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_open_file(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
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
        "path": path,
        "editor": editor,
        "line": line_num,
        "column": col_num,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


class IDESkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="open_cursor",
            method_name="open_cursor",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="Open a file or project in Cursor.",
            tool="cursor",
            action="open",
            normalize_args=_normalize_open_cursor,
        ),
        DeclarativeSkillSpec(
            skill_name="run_pycharm_action",
            method_name="run_pycharm_action",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="Trigger a PyCharm action by action_id.",
            tool="pycharm",
            action=None,
            normalize_args=_normalize_run_pycharm_action,
        ),
        DeclarativeSkillSpec(
            skill_name="open_file_in_editor",
            method_name="open_file_in_editor",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="Open a file in the preferred editor.",
            tool="open_file_in_editor",
            action=None,
            normalize_args=_normalize_open_file,
        ),
    )


def build_ide_skill_registry(provider: IDESkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or IDESkillProvider())
    return registry
