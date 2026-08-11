"""ToolBroker REST API 路由（M12-A TASK-0156）。

DevSpec §24：ToolBroker REST API。
端点：
  GET  /tools/meta-pack                — 获取当前 Top-K 工具元数据包（含 tool_visibility_hash）
  GET  /tools/{tool_id}                — 查询单个工具元数据
  POST /tools/execute                  — Secure Ingress → Governed Task Runtime
  GET  /tools/health                   — ToolBroker 健康状态
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from host_brain.app.host_route_ports import (
    HostRoutePorts,
    bind_host_route_endpoint,
)
from host_brain.core.errors import (
    TOOL_VISIBILITY_POLICY_VIOLATION,
)
from host_brain.core.ids import new_request_id
from host_brain.app.governed_tool_runtime import GovernedToolExecutionError
from host_brain.tools.broker import TOOL_NOT_FOUND
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.selector import ToolSelection, select_tool_meta_pack
_logger = logging.getLogger("Natsume.tool_routes")

def _get_catalog(route_ports: HostRoutePorts) -> ToolCatalog:
    provider = route_ports.tool_catalog_provider
    catalog = provider() if provider is not None else None
    if catalog is None:
        raise RuntimeError("ToolCatalog provider not configured")
    return catalog


def _get_broker(route_ports: HostRoutePorts) -> Any:
    provider = route_ports.tool_broker_provider
    broker = provider() if provider is not None else None
    if broker is None or not callable(getattr(broker, "readiness_snapshot", None)):
        raise RuntimeError("ToolBroker provider not configured")
    return broker


def _tool_runtime_readiness(route_ports: HostRoutePorts) -> dict[str, Any]:
    """Reopen exact Catalog↔Broker readiness; metadata alone is insufficient."""

    catalog = _get_catalog(route_ports)
    broker = _get_broker(route_ports)
    readiness = dict(broker.readiness_snapshot())
    catalog_ids = tuple(tool.id for tool in catalog.list_all())
    if tuple(readiness.get("catalog_ids") or ()) != catalog_ids:
        raise RuntimeError("TOOL_RUNTIME_READINESS_DRIFT")
    return readiness


def _require_tool_runtime_seeded(route_ports: HostRoutePorts) -> int:
    readiness = _tool_runtime_readiness(route_ports)
    status = str(readiness.get("status") or "")
    if status not in {"READY", "FAIL_CLOSED_NO_GOVERNED_EXECUTOR"}:
        raise RuntimeError(str(readiness.get("status") or "TOOL_RUNTIME_NOT_COMPOSED"))
    return len(tuple(readiness.get("catalog_ids") or ()))


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------

class ToolMetaResponse(BaseModel):
    """单个工具元数据响应。"""
    id: str = Field(..., description="工具唯一 ID")
    name: str = Field(..., description="工具名称")
    desc: str = Field(..., description="工具描述")
    schema_hash: str = Field(..., description="输入 Schema SHA-256 哈希")
    side_effect_level: str = Field(..., description="副作用级别: READ/WRITE")
    auth_level: str = Field(..., description="授权级别: NONE/APPROVAL/CONFIRM")
    domain_tags: List[str] = Field(..., description="领域标签")
    version: str = Field(..., description="工具版本")


class ToolMetaPackResponse(BaseModel):
    """GET /tools/meta-pack 响应体。"""
    tool_meta_pack: List[ToolMetaResponse] = Field(..., description="工具元数据列表")
    selected_ids: List[str] = Field(..., description="已选工具 ID 列表")
    tool_visibility_hash: str = Field(..., description="工具可见性哈希（SHA-256）")
    total: int = Field(..., description="返回工具数量")
    include_write: bool = Field(..., description="是否包含 WRITE 工具")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class ToolExecuteRequest(BaseModel):
    """POST /tools/execute 请求体。"""
    tool_id: str = Field(..., description="工具 ID")
    intent_id: str = Field(..., description="关联 Intent ID")
    session_id: str = Field(..., description="会话 ID")
    seq: int = Field(default=1, description="序列号")
    args: Optional[Dict[str, Any]] = Field(default=None, description="工具调用参数")
    context: Optional[Dict[str, Any]] = Field(default=None, description="执行上下文（可选）")
    approval_token_ref: Optional[str] = Field(default=None, description="WRITE 工具审批票据引用")
    confirm_token_ref: Optional[str] = Field(default=None, description="WRITE 工具确认票据引用")
    thread_id: Optional[str] = Field(default=None, description="线程 ID（可选）")
    turn_id: Optional[str] = Field(default=None, description="轮次 ID（可选）")


class ToolExecuteResponse(BaseModel):
    """POST /tools/execute 响应体。"""
    status: str = Field(..., description="执行状态: SUCCESS/REJECTED/FAILED")
    result_code: str = Field(..., description="结果代码")
    tool_id: str = Field(..., description="工具 ID")
    intent_id: str = Field(..., description="关联 Intent ID")
    evidence_ref: Optional[str] = Field(default=None, description="证据引用（成功时）")
    output: Optional[Any] = Field(default=None, description="工具输出（成功时）")
    reason_code: Optional[str] = Field(default=None, description="拒绝/失败原因代码")
    detail: Optional[str] = Field(default=None, description="详细说明")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class ToolHealthResponse(BaseModel):
    """GET /tools/health 响应体。"""
    status: str = Field(..., description="ToolBroker 状态: OK/DEGRADED")
    catalog_size: int = Field(..., description="已注册工具数量")
    write_tools: int = Field(..., description="WRITE 工具数量")
    read_tools: int = Field(..., description="READ 工具数量")
    registered_tools: int = Field(..., description="具有 Broker 注册记录的工具数量")
    governed_executable_tools: int = Field(..., description="已接真实 governed executor 的工具数量")
    readiness: str = Field(..., description="Catalog 与 Broker 的精确就绪状态")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


# ---------------------------------------------------------------------------
# GET /tools/health — ToolBroker 健康状态
# ---------------------------------------------------------------------------

async def tool_health(
    *,
    _route_ports: HostRoutePorts,
) -> ToolHealthResponse:
    """ToolBroker 健康状态检查。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        catalog = _get_catalog(_route_ports)
        all_tools = catalog.list_all()
        readiness = _tool_runtime_readiness(_route_ports)
        write_count = sum(1 for t in all_tools if t.side_effect_level == "WRITE")
        read_count = len(all_tools) - write_count
        readiness_status = str(readiness.get("status") or "UNSEEDED")

        return ToolHealthResponse(
            status="OK" if readiness_status == "READY" else "DEGRADED",
            catalog_size=len(all_tools),
            write_tools=write_count,
            read_tools=read_count,
            registered_tools=len(tuple(readiness.get("registered_ids") or ())),
            governed_executable_tools=len(
                tuple(readiness.get("governed_executable_ids") or ())
            ),
            readiness=readiness_status,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
        _logger.warning("tool_health: provider not configured: %s", exc)
        return ToolHealthResponse(
            status="DEGRADED",
            catalog_size=0,
            write_tools=0,
            read_tools=0,
            registered_tools=0,
            governed_executable_tools=0,
            readiness=str(exc),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("tool_health failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /tools/meta-pack — 获取 Top-K 工具元数据包
# ---------------------------------------------------------------------------

async def get_tool_meta_pack(
    query: str = "",
    k: int = 6,
    include_write: bool = False,
    tags: Optional[str] = None,
    *,
    _route_ports: HostRoutePorts,
) -> ToolMetaPackResponse:
    """获取当前 Top-K 工具元数据包（含 tool_visibility_hash）。

    - `query`: 语义查询词，用于 Top-K 排序
    - `k`: 最多返回工具数量（默认 6，最大 20）
    - `include_write`: 是否包含 WRITE 工具（默认 False，需显式开启）
    - `tags`: 逗号分隔的领域标签过滤（可选）
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    k = min(max(1, k), 20)
    required_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    try:
        catalog = _get_catalog(_route_ports)
        _require_tool_runtime_seeded(_route_ports)
        selection: ToolSelection = select_tool_meta_pack(
            catalog,
            query=query,
            k=k,
            include_write=include_write,
            required_tags=required_tags,
        )

        tool_responses = [
            ToolMetaResponse(
                id=meta["id"],
                name=meta["name"],
                desc=meta["desc"],
                schema_hash=meta["schema_hash"],
                side_effect_level=meta["side_effect_level"],
                auth_level=meta["auth_level"],
                domain_tags=meta["domain_tags"],
                version=meta["version"],
            )
            for meta in selection.tool_meta_pack
        ]

        return ToolMetaPackResponse(
            tool_meta_pack=tool_responses,
            selected_ids=selection.selected_ids,
            tool_visibility_hash=selection.tool_visibility_hash,
            total=len(tool_responses),
            include_write=include_write,
            request_id=request_id,
            timestamp=ts_ms,
        )

    except RuntimeError as exc:
        _logger.warning("get_tool_meta_pack: provider not configured: %s", exc)
        # 降级：返回空包（ToolBroker 未初始化时不阻断服务）
        return ToolMetaPackResponse(
            tool_meta_pack=[],
            selected_ids=[],
            tool_visibility_hash="sha256:" + "0" * 64,
            total=0,
            include_write=include_write,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("get_tool_meta_pack failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


async def get_tool_kits_alias(
    *,
    _route_ports: HostRoutePorts,
) -> ToolMetaPackResponse:
    """兼容别名：GET /tool_kits 与 GET /tools/meta-pack 返回相同结构，便于 UAT/外部前端统一访问。"""
    return await get_tool_meta_pack(
        query="",
        k=20,
        include_write=True,
        _route_ports=_route_ports,
    )


# ---------------------------------------------------------------------------
# GET /tools/{tool_id} — 查询单个工具元数据
# ---------------------------------------------------------------------------

async def get_tool_meta(
    tool_id: str,
    *,
    _route_ports: HostRoutePorts,
) -> ToolMetaResponse:
    """查询单个工具元数据。"""
    request_id = new_request_id()

    try:
        catalog = _get_catalog(_route_ports)
        _require_tool_runtime_seeded(_route_ports)
        meta = catalog.get(tool_id)
        return ToolMetaResponse(
            id=meta.id,
            name=meta.name,
            desc=meta.desc,
            schema_hash=meta.schema_hash,
            side_effect_level=meta.side_effect_level,
            auth_level=meta.auth_level,
            domain_tags=list(meta.domain_tags),
            version=meta.version,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"tool not found: {tool_id}",
            headers={"X-Reason-Code": TOOL_NOT_FOUND},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_tool_meta failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /tools/execute — Secure Ingress → HostCommand → TaskGraph
# ---------------------------------------------------------------------------

async def execute_tool(
    body: ToolExecuteRequest,
    request: Request,
    *,
    _route_ports: HostRoutePorts,
) -> ToolExecuteResponse:
    """Authenticate and submit a frozen Tool node to the governed runtime.

    The legacy token fields remain in the wire schema for compatibility, but
    are never accepted as execution authority.  WRITE authority must be
    derived by Governor/Review as a node-bound child grant.
    """
    try:
        _require_tool_runtime_seeded(_route_ports)
        provider = _route_ports.governed_tool_application_provider
        application = provider() if provider is not None else None
        if application is None or not callable(getattr(application, "execute", None)):
            raise GovernedToolExecutionError(
                503,
                "GOVERNED_TOOL_RUNTIME_UNAVAILABLE",
                "governed Tool application is not composed",
            )
        result = await application.execute(
            body=body,
            request=request,
            raw_body=await request.body(),
        )
        return ToolExecuteResponse(**result)
    except GovernedToolExecutionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers={"X-Reason-Code": exc.reason_code},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive boundary
        _logger.error(
            "execute_tool governed boundary failed: %s",
            type(exc).__name__,
            extra={"tool_id": body.tool_id},
        )
        raise HTTPException(
            status_code=500,
            detail="governed Tool execution failed",
            headers={"X-Reason-Code": "GOVERNED_TOOL_RESULT_INVALID"},
        ) from exc


def create_tool_router(route_ports: HostRoutePorts) -> APIRouter:
    """Create Tool routes with dependencies captured by one app instance."""

    router = APIRouter(tags=["tools"])

    def add(path: str, endpoint: Any, *, methods: list[str], **kwargs: Any) -> None:
        router.add_api_route(
            path,
            bind_host_route_endpoint(endpoint, route_ports),
            methods=methods,
            **kwargs,
        )

    add("/tools/health", tool_health, methods=["GET"], response_model=ToolHealthResponse)
    add(
        "/tools/meta-pack",
        get_tool_meta_pack,
        methods=["GET"],
        response_model=ToolMetaPackResponse,
    )
    add(
        "/tool_kits",
        get_tool_kits_alias,
        methods=["GET"],
        response_model=ToolMetaPackResponse,
    )
    add(
        "/tools/execute",
        execute_tool,
        methods=["POST"],
        response_model=ToolExecuteResponse,
        status_code=200,
    )
    add(
        "/tools/{tool_id}",
        get_tool_meta,
        methods=["GET"],
        response_model=ToolMetaResponse,
    )
    return router


__all__ = [
    "ToolExecuteRequest",
    "create_tool_router",
    "execute_tool",
]
