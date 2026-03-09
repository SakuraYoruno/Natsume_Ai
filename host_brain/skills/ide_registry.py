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


def build_ide_skill_registry(provider: IDESkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or IDESkillProvider())
    return registry
