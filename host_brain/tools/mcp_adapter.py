"""MCP provider metadata sync and governed invocation adapter.

M14-C 强化：
- sync_provider_tools()：返回 ToolMeta 列表（含 READ/WRITE 分级）
- execute_mcp_tool()：AIRLOCK 出站净化 + evidence_ref 校验 + 审计串联
- _airlock_sanitize()：出站 payload 最小化，生成 sanitization_report
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    MCP_AUDIT_REF_MISSING,
    MCP_PROVIDER_SYNC_FAILED,
    MCP_TOOL_NOT_FOUND,
    MCP_WRITE_UNAUTHORIZED,
)
from host_brain.tools.broker import ToolBroker, ToolExecutionResult
from host_brain.tools.catalog import AUTH_LEVELS, SIDE_EFFECT_LEVELS, ToolCatalog


MCP_PREFIX = "mcp"

MCP_SIDE_EFFECT_ALIAS = {
    "read": "READ",
    "write": "WRITE",
    "readonly": "READ",
    "read_only": "READ",
}

# AIRLOCK：出站时需要移除的敏感字段（硬状态原始值/宪法全文/规则细节）
_AIRLOCK_SENSITIVE_KEYS = frozenset({
    "hard_state",
    "constitution",
    "rule_detail",
    "raw_score",
    "bond_score",
    "trust_value",
    "clearance_raw",
    "internal_trace",
    "governor_trace",
    "rule_trace",
    "approval_token",
    "confirm_token",
    "undo_token",
    "secret",
    "password",
    "api_key",
})


@dataclass(frozen=True)
class ToolMeta:
    """MCP 工具元数据（同步后的只读视图）。"""
    tool_id: str
    provider_id: str
    name: str
    desc: str
    side_effect_level: str          # READ | WRITE
    auth_level: str                 # NONE | APPROVAL | CONFIRM
    schema_hash: str
    domain_tags: list[str]
    version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "provider_id": self.provider_id,
            "name": self.name,
            "desc": self.desc,
            "side_effect_level": self.side_effect_level,
            "auth_level": self.auth_level,
            "schema_hash": self.schema_hash,
            "domain_tags": list(self.domain_tags),
            "version": self.version,
        }


@dataclass(frozen=True)
class SanitizationReport:
    """AIRLOCK 出站净化报告（必须存在，否则拒绝出站）。"""
    removed_fields: list[str]
    redactions: list[str]
    payload_digest: str
    original_field_count: int
    sanitized_field_count: int


def to_mcp_tool_id(provider_id: str, tool_id: str) -> str:
    provider = _slug(provider_id)
    tool = _slug(tool_id)
    return f"{MCP_PREFIX}::{provider}::{tool}"


class MCPAdapter:
    """Maps MCP provider tools into ToolCatalog and executes via ToolBroker.

    M14-C 新增能力：
    - sync_provider_tools()：同步 provider 工具为 ToolMeta 列表（含 READ/WRITE 分级）
    - execute_mcp_tool()：AIRLOCK 出站净化 + evidence_ref 校验 + 审计串联
    """

    def __init__(self, *, catalog: ToolCatalog, broker: ToolBroker) -> None:
        self.catalog = catalog
        self.broker = broker
        self._mapping: dict[tuple[str, str], str] = {}
        # provider_id → ToolMeta 列表（sync_provider_tools 后填充）
        self._provider_tools: dict[str, list[ToolMeta]] = {}

    # ── 原有接口（向后兼容）────────────────────────────────────────────────

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
        tool_metas: list[ToolMeta] = []

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

            tool_metas.append(ToolMeta(
                tool_id=mapped_tool_id,
                provider_id=provider_id,
                name=meta["name"],
                desc=meta["desc"],
                side_effect_level=side_effect,
                auth_level=auth_level,
                schema_hash=schema_hash,
                domain_tags=domain_tags,
                version=meta["version"],
            ))

            handler = handler_map.get(source_tool_id) or handler_map.get(mapped_tool_id)
            if handler is not None:
                self.broker.register_handler(mapped_tool_id, handler)

        # 更新 provider_tools 缓存
        self._provider_tools[provider_id] = tool_metas
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

    # ── M14-C 新增接口 ────────────────────────────────────────────────────

    def sync_provider_tools(
        self,
        provider_id: str,
        *,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], Any]] | None = None,
    ) -> list[ToolMeta]:
        """
        M14-C：同步 MCP provider 暴露的工具为 ToolMeta 列表。
        - 仅同步元数据，不执行
        - 按 READ/WRITE 分级
        - 返回 ToolMeta 列表（含 provider_id）

        Raises:
            ValueError: 携带 reason_code=MCP_PROVIDER_SYNC_FAILED，指示同步失败原因。
        """
        try:
            self.sync_provider(provider_id=provider_id, tools=tools, handlers=handlers)
        except ValueError as exc:
            err = ValueError(f"MCP provider sync failed for {provider_id!r}: {exc}")
            err.reason_code = MCP_PROVIDER_SYNC_FAILED  # type: ignore[attr-defined]
            raise err from exc

        return list(self._provider_tools.get(provider_id, []))

    def execute_mcp_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        *,
        provider_id: str,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        approval_token_ref: str | None = None,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """
        M14-C：执行 MCP 工具（必须经 ToolBroker 单入口）。
        - WRITE 必须有 approval_token_ref
        - 出站经 AIRLOCK 净化（生成 sanitization_report）
        - 缺 sanitization_report → AIRLOCK_BLOCKED_EGRESS
        - 执行无 evidence_ref → MCP_AUDIT_REF_MISSING
        - 返回含 sanitization_report 的结果字典

        Raises:
            ValueError: 携带 reason_code，指示具体错误类型。
        """
        mapped_tool_id = self.resolve_tool_id(provider_id=provider_id, tool_id=tool_id)

        # 校验 tool_id 是否已同步
        try:
            tool_meta = self.catalog.get(mapped_tool_id)
        except KeyError:
            err = ValueError(f"MCP tool not found: {mapped_tool_id!r}")
            err.reason_code = MCP_TOOL_NOT_FOUND  # type: ignore[attr-defined]
            raise err

        # WRITE 工具必须有 approval_token_ref
        if tool_meta.side_effect_level == "WRITE" and not approval_token_ref:
            err = ValueError(f"WRITE MCP tool {mapped_tool_id!r} requires approval_token_ref")
            err.reason_code = MCP_WRITE_UNAUTHORIZED  # type: ignore[attr-defined]
            raise err

        # AIRLOCK 出站净化（在调用前净化 args）
        sanitized_args, report = self._airlock_sanitize(args)
        if report is None:
            err = ValueError("AIRLOCK sanitization_report missing, egress blocked")
            err.reason_code = AIRLOCK_BLOCKED_EGRESS  # type: ignore[attr-defined]
            raise err

        # 执行（经 ToolBroker 单入口）
        result = self.broker.execute(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            tool_id=mapped_tool_id,
            args=sanitized_args,
            context={
                "provider_id": provider_id,
                "original_tool_id": tool_id,
                "sanitization_report": report.payload_digest,
            },
            approval_token_ref=approval_token_ref,
            ts_ms=ts_ms,
        )

        # 校验 evidence_ref（WRITE 工具成功执行必须有 evidence_ref）
        if result.status == "SUCCESS" and tool_meta.side_effect_level == "WRITE":
            if not result.evidence_ref:
                err = ValueError(f"MCP WRITE tool {mapped_tool_id!r} executed without evidence_ref")
                err.reason_code = MCP_AUDIT_REF_MISSING  # type: ignore[attr-defined]
                raise err

        return {
            "status": result.status,
            "result_code": result.result_code,
            "tool_id": mapped_tool_id,
            "provider_id": provider_id,
            "intent_id": intent_id,
            "evidence_ref": result.evidence_ref,
            "output": result.output,
            "sanitization_report": {
                "removed_fields": report.removed_fields,
                "redactions": report.redactions,
                "payload_digest": report.payload_digest,
                "original_field_count": report.original_field_count,
                "sanitized_field_count": report.sanitized_field_count,
            },
        }

    def get_provider_tools(self, provider_id: str) -> list[ToolMeta]:
        """返回已同步的 provider 工具列表（空列表表示未同步）。"""
        return list(self._provider_tools.get(provider_id, []))

    # ── AIRLOCK 净化 ──────────────────────────────────────────────────────

    @staticmethod
    def _airlock_sanitize(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], SanitizationReport]:
        """
        AIRLOCK 出站净化：移除敏感字段，生成 sanitization_report。
        - 移除 _AIRLOCK_SENSITIVE_KEYS 中的字段
        - 生成 payload_digest（sha256）
        - 返回净化后的 payload 和 SanitizationReport
        """
        removed_fields: list[str] = []
        redactions: list[str] = []
        original_count = len(payload)

        sanitized: dict[str, Any] = {}
        for key, value in payload.items():
            if key.lower() in _AIRLOCK_SENSITIVE_KEYS:
                removed_fields.append(key)
            elif isinstance(value, str) and len(value) > 512:
                # 超长字符串截断（防止泄露大段内部内容）
                sanitized[key] = value[:512] + "...[REDACTED]"
                redactions.append(key)
            else:
                sanitized[key] = value

        canonical = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

        report = SanitizationReport(
            removed_fields=removed_fields,
            redactions=redactions,
            payload_digest=digest,
            original_field_count=original_count,
            sanitized_field_count=len(sanitized),
        )
        return sanitized, report


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
    auth = {"LOW": "NONE", "MEDIUM": "APPROVAL", "HIGH": "CONFIRM"}.get(auth, auth)
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
