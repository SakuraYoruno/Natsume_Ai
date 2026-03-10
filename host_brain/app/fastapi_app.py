"""FastAPI web application entry for Natsume AI host brain.

TASK-0065: HTTP 服务框架落地 — FastAPI 路由绑定 + 健康检测 + 链路入口。
提供 POST /v1/chat（编排入口）、GET /health、GET /ready、GET /metrics，
所有执行仍继续 Governor/Token/Skills，不引入旁路。
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from host_brain.app.schemas import (
    ChatRequest,
    EdgeEventsRequest,
    EdgeEventsResponse,
    MetricsResponse,
)
from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.canon_routes import router as canon_router, set_ledger_provider as _set_canon_ledger_provider
from host_brain.scheduler.scheduler_routes import router as scheduler_router
from host_brain.app.tool_routes import router as tool_router, set_tool_providers as _set_tool_providers
from host_brain.app.bond_routes import router as bond_router, set_bond_engine_provider as _set_bond_engine_provider
from host_brain.app.obs_routes import router as obs_router, set_ledger_provider as _set_obs_ledger_provider
from host_brain.app.health_routes import router as health_router
from host_brain.app.dependencies import (
    get_ledger,
    get_tool_catalog,
    get_tool_broker,
    get_bond_stage_engine,
)
from host_brain.app.http_hooks import (
    runtime_lifespan,
    global_exception_handler as _global_exception_handler_fn,
    add_request_id_and_audit as _add_request_id_fn,
)
from host_brain.app.chat_handler import handle_chat
from host_brain.app.system_handler import receive_edge_events_response
from host_brain.core.ids import new_request_id


_logger = logging.getLogger("Natsume.fastapi")


# ---------------------------------------------------------------------------
# 运行时指标收集器（内存，用于 /metrics 端点）
# ---------------------------------------------------------------------------
class _RuntimeMetrics:
    """进程内运行时指标收集（轻量级内存实现）。

    注意：该计数器依赖 CPython GIL 保证单进程内线程安全，
    但在 worker 模式下各 worker 独立计数（指标为 per-worker 视图）。
    如需全局指标，请改用外部存储（Redis/Prometheus）。
    """

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.total_requests: int = 0
        self.total_chat_requests: int = 0
        self.total_rejections: int = 0
        self.total_errors: int = 0
        self.reason_code_counts: Dict[str, int] = {}

    def inc_request(self) -> None:
        self.total_requests += 1

    def inc_chat(self) -> None:
        self.total_chat_requests += 1

    def inc_rejection(self, reason_code: str) -> None:
        self.total_rejections += 1
        self.reason_code_counts[reason_code] = self.reason_code_counts.get(reason_code, 0) + 1

    def inc_error(self) -> None:
        self.total_errors += 1

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.start_time)


_metrics = _RuntimeMetrics()


# ---------------------------------------------------------------------------
# 生命周期钩子
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown 生命周期钩子。"""
    async with runtime_lifespan(get_ledger=get_ledger, logger=_logger):
        yield


# ---------------------------------------------------------------------------
# FastAPI 搴旂敤瀹炰緥
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Natsume AI Host Brain",
    description="AI agent orchestration runtime with deterministic execution and audit trails",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# 注册 Web UI API 路由（TASK-0066: 链路可视化面板后端）
# ---------------------------------------------------------------------------
app.include_router(web_ui_router, prefix="/api")

# ---------------------------------------------------------------------------
# 注册 Canon 处理 API 路由（M10-A TASK-0118: Canon 提案审核 REST API）
# ---------------------------------------------------------------------------
app.include_router(canon_router)

# ---------------------------------------------------------------------------
# 注册 Scheduler REST API 路由（M11-A TASK-0149: 定时调度器任务管理）
# ---------------------------------------------------------------------------
app.include_router(scheduler_router)

# ---------------------------------------------------------------------------
# 注册 ToolBroker REST API 路由（M12-A TASK-0156: 工具元数据包 + 执行入口）
# ---------------------------------------------------------------------------
app.include_router(tool_router)

# ---------------------------------------------------------------------------
# 注册 Bond 阶段 REST API 路由（M13-A: Bond 阶段积分 + 自动晋级）
# ---------------------------------------------------------------------------
app.include_router(bond_router)

# ---------------------------------------------------------------------------
# 注册 Observability REST API 路由（M18: 监控面板后端数据接口）
# ---------------------------------------------------------------------------
app.include_router(obs_router)

# ---------------------------------------------------------------------------
# 挂载静态文件目录（TASK-0066: 前端面板页面）
# ---------------------------------------------------------------------------
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
    # M17 monitor SPA build outputs absolute asset URLs under "/monitor/*".
    # Keep an alias mount so /panel and /static/monitor/index.html both work.
    _monitor_dir = os.path.join(_static_dir, "monitor")
    if os.path.isdir(_monitor_dir):
        app.mount("/monitor", StaticFiles(directory=_monitor_dir), name="monitor")

# ---------------------------------------------------------------------------
# CORS 中间件（可配置）
# ---------------------------------------------------------------------------
# CORS 默认关闭：生产环境必须显式设置 Natsume_CORS_ORIGINS
# 例如 Natsume_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:8000"
_cors_origins_raw = os.environ.get(
    "Natsume_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"
).strip()
if _cors_origins_raw:
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---------------------------------------------------------------------------
# 自定义异常处理器
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler — 委托给 http_hooks._global_exception_handler_fn。"""
    return await _global_exception_handler_fn(request=request, exc=exc, logger=_logger)


# 中间件：请求ID注入和审计
@app.middleware("http")
async def add_request_id_and_audit(request: Request, call_next: Callable) -> Response:
    """Attach request ids to incoming requests — 委托给 http_hooks._add_request_id_fn。"""
    return await _add_request_id_fn(request=request, call_next=call_next, metrics=_metrics, logger=_logger)


# ---------------------------------------------------------------------------
# 核心 HTTP 端点（委托给各 handler 模块）
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check() -> Any:
    """Health endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import health_check as _health_check
    return await _health_check()


@app.get("/ready")
async def environment_readiness() -> Any:
    """Readiness endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import environment_readiness as _readiness
    return await _readiness()


@app.get("/obs/metrics")
async def obs_metrics(request_id: Optional[str] = None) -> Any:
    """Metrics endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import obs_metrics as _obs_metrics
    return await _obs_metrics(request_id=request_id)


@app.get("/health/mcp")
async def mcp_health_check() -> Any:
    """MCP health endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import mcp_health_check as _mcp_health
    return await _mcp_health()


@app.get("/health/canon_sync")
async def canon_sync_health() -> Any:
    """Canon sync health endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import canon_sync_health_check as _canon_sync
    return await _canon_sync()


@app.post("/observability/trace")
async def observability_trace(request: Request) -> Any:
    """Observability trace endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import request_trace as _obs_trace
    return await _obs_trace(request)


@app.get("/startup")
async def startup_report() -> Any:
    """Startup report endpoint — 委托给 health_routes。"""
    from host_brain.app.health_routes import startup_report as _startup
    return await _startup()


# ---------------------------------------------------------------------------
# FastAPI 应用实例
# ---------------------------------------------------------------------------
@app.post("/v1/chat")
async def chat(request_body: ChatRequest, request: Request) -> StreamingResponse:
    """POST /v1/chat — 统一入口，根据场景自动路由到本地单节点或云端多节点，统一返回 SSE 流式响应。"""
    return await handle_chat(request_body=request_body, request=request, metrics=_metrics)


# ---------------------------------------------------------------------------
# Edge 事件端点
# ---------------------------------------------------------------------------
@app.post("/v1/edge/events", response_model=EdgeEventsResponse)
async def receive_edge_events(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    """接收 Edge 端批量上报的事件 — 委托给 system_handler.receive_edge_events_response。"""
    return await receive_edge_events_response(request_body=request_body)


@app.get("/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    """Runtime metrics summary endpoint."""
    return MetricsResponse(
        uptime_seconds=_metrics.uptime_seconds,
        total_requests=_metrics.total_requests,
        total_chat_requests=_metrics.total_chat_requests,
        total_rejections=_metrics.total_rejections,
        total_errors=_metrics.total_errors,
        reason_code_distribution=dict(_metrics.reason_code_counts),
        timestamp=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# Web UI 面板入口（M17: 已升级为 React SPA，旧 panel.html 已移除）
# ---------------------------------------------------------------------------
@app.get("/panel")
async def panel_page() -> Response:
    """Monitoring panel redirect endpoint."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/monitor/index.html", status_code=301)


# ---------------------------------------------------------------------------
# 根路由重定向到文档
# ---------------------------------------------------------------------------
@app.get("/")
async def root() -> Response:
    """Redirect root to the API docs."""
    return Response(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/docs"})


if __name__ == "__main__":
    import uvicorn

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # 启动 FastAPI 服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=True,
    )


