"""MCP provider metadata sync and governed invocation adapter."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from host_brain.tools.broker import ToolBroker, ToolExecutionResult
from host_brain.tools.catalog import AUTH_LEVELS, SIDE_EFFECT_LEVELS, ToolCatalog


MCP_PREFIX = "mcp"

MCP_SIDE_EFFECT_ALIAS = {
    "read": "READ",
    "write": "WRITE",
    "readonly": "READ",
    "read_only": "READ",
}


def to_mcp_tool_id(provider_id: str, tool_id: str) -> str:
    provider = _slug(provider_id)
    tool = _slug(tool_id)
    return f"{MCP_PREFIX}::{provider}::{tool}"


class MCPAdapter:
    """Maps MCP provider tools into ToolCatalog and executes via ToolBroker."""

    def __init__(self, *, catalog: ToolCatalog, broker: ToolBroker) -> None:
        self.catalog = catalog
        self.broker = broker
        self._mapping: dict[tuple[str, str], str] = {}

    def sync_provider(
        self,
        *,
        provider_id: str,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], Any]] | None = None,
    ) -> list[str]:
        """Sync provider tool metadata into ToolCatalog and optional handlers into broker."""

        provider = _slug(provider_id)
        handler_map = handlers or {}
        synced_ids: list[str] = []
        for tool in tools:
            source_tool_id = _slug(str(tool.get("tool_id") or tool.get("id") or ""))
            if not source_tool_id:
                raise ValueError("MCP tool entry missing tool_id/id")

            mapped_tool_id = to_mcp_tool_id(provider, source_tool_id)
            side_effect = _normalize_side_effect(tool.get("side_effect_level") or tool.get("mode") or "READ")
            auth_level = _normalize_auth_level(tool.get("auth_level"), side_effect=side_effect)
            schema_hash = _normalize_schema_hash(tool)
            domain_tags = _normalize_tags(tool.get("domain_tags") or ["mcp", provider])

            meta = {
                "id": mapped_tool_id,
                "name": str(tool.get("name") or source_tool_id),
                "desc": str(tool.get("desc") or tool.get("description") or f"MCP tool {provider}:{source_tool_id}"),
                "schema_hash": schema_hash,
                "side_effect_level": side_effect,
                "auth_level": auth_level,
                "domain_tags": domain_tags,
                "version": str(tool.get("version") or "1.0.0"),
            }
            self.catalog.register(meta)
            self._mapping[(provider, source_tool_id)] = mapped_tool_id
            synced_ids.append(mapped_tool_id)

            handler = handler_map.get(source_tool_id) or handler_map.get(mapped_tool_id)
            if handler is not None:
                self.broker.register_handler(mapped_tool_id, handler)

        return sorted(synced_ids)

    def resolve_tool_id(self, *, provider_id: str, tool_id: str) -> str:
        provider = _slug(provider_id)
        source_tool = _slug(tool_id)
        mapped = self._mapping.get((provider, source_tool))
        if mapped is not None:
            return mapped
        return to_mcp_tool_id(provider, source_tool)

    def call_tool(
        self,
        *,
        provider_id: str,
        tool_id: str,
        request_id: str | None,
        intent_id: str,
        session_id: str,
        seq: int,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        confirm_token_ref: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        ts_ms: int | None = None,
    ) -> ToolExecutionResult:
        mapped_tool_id = self.resolve_tool_id(provider_id=provider_id, tool_id=tool_id)
        return self.broker.execute(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            tool_id=mapped_tool_id,
            args=args,
            context=context,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            thread_id=thread_id,
            turn_id=turn_id,
            ts_ms=ts_ms,
        )


def _normalize_side_effect(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in MCP_SIDE_EFFECT_ALIAS:
        return MCP_SIDE_EFFECT_ALIAS[raw]
    upper = str(value or "").strip().upper()
    if upper in SIDE_EFFECT_LEVELS:
        return upper
    raise ValueError(f"unsupported MCP side_effect_level/mode: {value}")


def _normalize_auth_level(value: Any, *, side_effect: str) -> str:
    if value is None or str(value).strip() == "":
        return "APPROVAL" if side_effect == "WRITE" else "NONE"
    auth = str(value).strip().upper()
    if auth not in AUTH_LEVELS:
        raise ValueError(f"unsupported MCP auth_level: {value}")
    if side_effect == "READ" and auth != "NONE":
        raise ValueError("READ MCP tool must use auth_level=NONE")
    if side_effect == "WRITE" and auth == "NONE":
        raise ValueError("WRITE MCP tool must not use auth_level=NONE")
    return auth


def _normalize_schema_hash(tool: dict[str, Any]) -> str:
    given = str(tool.get("schema_hash") or "").strip()
    if given:
        if not given.startswith("sha256:"):
            raise ValueError("MCP schema_hash must start with sha256:")
        return given

    schema = tool.get("input_schema")
    canonical = json.dumps(schema if schema is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raw = [raw]
    tags: list[str] = []
    for item in raw:
        value = _slug(str(item))
        if value and value not in tags:
            tags.append(value)
    if not tags:
        tags = ["mcp"]
    return tags


def _slug(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    return normalized.replace("::", "_")
