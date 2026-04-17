from __future__ import annotations

import logging
from typing import Any

from host_brain.skills.hub.hub_installer import install_skill, list_installed_skills, remove_skill, search_remote_skills_with_status

_logger = logging.getLogger("Natsume.skills.hub_skill_executor")


def execute_hub_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"hub_skill::{action}")
    ctx = context or payload.get("_context") or {}

    try:
        if action == "install":
            result = install_skill(str(payload.get("source") or "").strip(), force=bool(payload.get("force", False)))
            active_skill_names: list[str] = []
            activated = False
            repair_attempted = False
            repair_succeeded = False
            repair_actions = 0
            activation_error = ""
            if result.success and bool(payload.get("activate_now", True)):
                try:
                    from host_brain.app.tool_routes import _attempt_activate_hub_skill  # noqa: PLC0415
                    activated, active_skill_names, repair_attempted, repair_succeeded, repair_actions, activation_message = _attempt_activate_hub_skill(
                        install_dir=str(result.install_dir),
                        skill_name=str(result.name),
                    )
                except Exception as exc:
                    activation_message = f"技能已经安装，但激活失败：{exc}"
                    activation_error = str(exc)
            else:
                activation_message = str(result.detail or result.error or "install failed")
            return {
                "status": "OK" if result.success else "FAILED",
                "manifest_ref": manifest_ref,
                "action": action,
                "detail": {
                    "success": bool(result.success),
                    "name": str(result.name),
                    "version": str(result.version),
                    "source": str(result.source),
                    "install_dir": str(result.install_dir),
                    "activated": bool(activated),
                    "active_skill_names": list(active_skill_names),
                    "repair_attempted": bool(repair_attempted),
                    "repair_succeeded": bool(repair_succeeded),
                    "repair_actions": int(repair_actions),
                    "activation_error": activation_error or None,
                    "message": activation_message,
                },
            }
        if action == "search":
            query = str(payload.get("query") or "").strip()
            response = search_remote_skills_with_status(query)
            rows = list(response.results or [])
            return {
                "status": "OK",
                "manifest_ref": manifest_ref,
                "action": action,
                "detail": {
                    "query": query,
                    "total": len(rows),
                    "registry_url": str(response.registry_url),
                    "registry_error": str(response.error or "").strip() or None,
                    "discovery_sources": list(getattr(response, "discovery_sources", None) or []),
                    "skills": [
                        {
                            "name": str(item.name),
                            "version": str(item.version),
                            "description": str(item.description),
                            "author": str(item.author),
                            "tags": list(item.tags),
                        }
                        for item in rows
                    ],
                },
            }
        if action == "list":
            rows = list_installed_skills()
            return {
                "status": "OK",
                "manifest_ref": manifest_ref,
                "action": action,
                "detail": {
                    "total": len(rows),
                    "skills": [dict(item) for item in rows],
                },
            }
        if action == "remove":
            result = remove_skill(str(payload.get("skill_name") or "").strip())
            if result.success:
                try:
                    from host_brain.app.dependencies import refresh_skill_registry  # noqa: PLC0415
                    refresh_skill_registry()
                except Exception:
                    pass
            return {
                "status": "OK" if result.success else "FAILED",
                "manifest_ref": manifest_ref,
                "action": action,
                "detail": {
                    "success": bool(result.success),
                    "name": str(result.name),
                    "message": str(result.detail or result.error or "remove failed"),
                },
            }
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": f"unknown hub_skill action: {action}",
        }
    except Exception as exc:
        _logger.warning("Hub skill executor failed action=%s: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }
