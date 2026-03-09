"""Bridge MCP tool metadata into SkillRegistry (single-entry skill namespace)."""

from __future__ import annotations

from typing import Any

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
            context = dict(ctx or {})
            if broker is None:
                return {
                    "status": "SKIPPED",
                    "reason_code": "MCP_BROKER_UNBOUND",
                    "provider_id": _provider_id,
                    "tool_id": _tool_id,
                }
            result = broker.call(
                provider_id=_provider_id,
                tool_id=_tool_id,
                request_id=str(context.get("request_id") or "") or None,
                intent_id=str(context.get("intent_id") or ""),
                session_id=str(context.get("session_id") or ""),
                seq=int(context.get("seq") or 0),
                args=dict(args or {}),
                approval_token_ref=str(context.get("approval_token_ref") or "") or None,
            )
            return {
                "status": ("OK" if bool(getattr(result, "success", False)) else "REJECTED"),
                "reason_code": getattr(result, "reason_code", None),
                "provider_id": _provider_id,
                "tool_id": _tool_id,
                "detail": str(getattr(result, "detail", "") or ""),
                "output": getattr(result, "output", None),
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
