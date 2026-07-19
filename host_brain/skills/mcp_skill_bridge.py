"""Bridge MCP tool metadata into SkillRegistry (single-entry skill namespace)."""

from __future__ import annotations

from typing import Any

from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.skills.registry import SkillMeta, SkillRegistry


def build_mcp_skill_name(*, provider_id: str, tool_id: str) -> str:
    return f"mcp::{str(provider_id).strip()}::{str(tool_id).strip()}"


def _map_risk(meta: ToolMeta) -> tuple[str, int, bool]:
    level = str(meta.risk_level or "LOW").strip().upper()
    if level == "HIGH":
        return "HIGH", 5, True
    if level == "MEDIUM":
        return "MEDIUM", 3, False
    return "LOW", 1, False


def register_mcp_tools_into_skill_registry(
    *,
    skill_registry: SkillRegistry,
    mcp_registry: MCPToolRegistry,
    broker: Any | None = None,
) -> int:
    del broker
    count = 0
    for meta in mcp_registry.list_all():
        if not isinstance(meta, ToolMeta):
            continue
        skill_name = build_mcp_skill_name(provider_id=meta.provider_id, tool_id=meta.tool_id)
        if skill_registry.get(skill_name) is not None:
            continue
        clearance, risk_level, require_confirm = _map_risk(meta)

        def _handler(
            args: dict[str, Any],
            ctx: dict[str, Any] | None,
            *,
            _provider_id: str = meta.provider_id,
            _tool_id: str = meta.tool_id,
        ) -> dict[str, Any]:
            del args, ctx
            return {
                "status": "REJECTED",
                "reason_code": MCP_TASK_AUTHORITY_REQUIRED,
                "provider_id": _provider_id,
                "tool_id": _tool_id,
                "detail": (
                    "MCP provider execution requires a governed Tool Task node; "
                    "nested Skill execution is retired"
                ),
                "output": None,
            }

        skill_registry.register(
            SkillMeta(
                skill_name=skill_name,
                clearance=clearance,
                risk_level=risk_level,
                scope="system",
                reversible=bool(meta.reversible),
                handler=_handler,
                require_confirm=require_confirm,
                description=str(meta.description or ""),
            )
        )
        count += 1
    return count
