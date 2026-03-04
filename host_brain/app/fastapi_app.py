"""FastAPI web application entry for Nastume AI host brain.

TASK-0065: HTTP 服务框架落地 — FastAPI 路由绑定 + 健康检查 + 链路入口。
提供 POST /v1/chat（编排入口）、GET /health、GET /ready、GET /metrics，
所有执行仍经 Governor/Token/Skills，不引入旁路。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from host_brain.app.main import (
    build_env_readiness_report,
    build_request_observability_report,
    build_service_health_report,
    build_startup_report,
    emit_runtime_bootstrap_logs,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_SCHEMA_INVALID,
)
from host_brain.core.ids import new_request_id
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.observability.cross_timeline import build_cross_timeline
from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.canon_routes import router as canon_router, set_ledger_provider as _set_canon_ledger_provider
from host_brain.scheduler.scheduler_routes import router as scheduler_router


# HTTP契约模型定义
class HealthResponse(BaseModel):
    status: str = Field(..., description="服务状态: OK/DEGRADED/FAILED")
    version: str = Field(..., description="应用版本")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")
    details: Dict[str, Any] = Field(default_factory=dict, description="详细状态信息")


class ReadinessResponse(BaseModel):
    ready: bool = Field(..., description="环境就绪状态")
    status: str = Field(..., description="就绪状态: READY/DEGRADED/FAILED")
    reason_code: Optional[str] = Field(None, description="状态原因代码")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")
    summary: Dict[str, Any] = Field(default_factory=dict, description="环境摘要信息")


class ObservabilityRequest(BaseModel):
    request_id: str = Field(..., description="要查询的请求ID")


class ObservabilityResponse(BaseModel):
    request_id: str = Field(..., description="请求ID")
    trace_status: str = Field(..., description="追踪状态")
    timeline_total: int = Field(..., description="时间线事件总数")
    sources: Dict[str, int] = Field(..., description="事件源统计")
    timeline: List[Dict[str, Any]] = Field(..., description="时间线事件列表")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")


class ChatRequest(BaseModel):
    """POST /v1/chat 请求体"""
    message: str = Field(..., description="用户输入文本")
    session_id: Optional[str] = Field(None, description="会话ID（可选，缺省自动生成）")
    thread_id: Optional[str] = Field(None, description="线程ID（微信thread_key等）")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="预构造的intent草案（可选）")


class ChatResponse(BaseModel):
    """POST /v1/chat 响应体 — 成功/拒绝/降级都返回此结构"""
    request_id: str = Field(..., description="请求唯一ID")
    narrative: Optional[str] = Field(None, description="叙事输出（LLM或模板）")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Intent草案（可为空）")
    decision: Optional[str] = Field(None, description="Governor裁决: APPROVE/REJECT/None")
    reason_code: Optional[str] = Field(None, description="拒绝/降级原因代码")
    approval_token_ref: Optional[str] = Field(None, description="审批票据引用（仅APPROVE时）")
    quality: Dict[str, Any] = Field(default_factory=dict, description="质量标记: stage/reason_code")
    audit_refs: Dict[str, Any] = Field(default_factory=dict, description="审计引用: intent_id/governor_log等")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")


class MetricsResponse(BaseModel):
    """GET /metrics 响应体 — 运行时指标摘要"""
    uptime_seconds: int = Field(..., description="服务运行时长(秒)")
    total_requests: int = Field(..., description="总请求数")
    total_chat_requests: int = Field(..., description="chat请求数")
    total_rejections: int = Field(..., description="Governor拒绝数")
    total_errors: int = Field(..., description="错误数")
    reason_code_distribution: Dict[str, int] = Field(default_factory=dict, description="拒绝码分布")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")


class EdgeEventsRequest(BaseModel):
    """POST /v1/edge/events 请求体 — Edge 端事件批量上报"""
    events: List[Dict[str, Any]] = Field(..., description="Edge 事件列表")


class EdgeEventsResponse(BaseModel):
    """POST /v1/edge/events 响应体"""
    success: bool = Field(..., description="是否全部接受")
    total: int = Field(..., description="收到事件总数")
    accepted: int = Field(..., description="成功写入数")
    rejected: int = Field(..., description="拒绝数")
    reason_code: Optional[str] = Field(None, description="部分失败/全失败原因")
    detail: Optional[str] = Field(None, description="描述")
    rejected_indices: List[int] = Field(default_factory=list, description="被拒绝的事件索引")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="错误类型")
    reason_code: str = Field(..., description="错误原因代码")
    detail: str = Field(..., description="错误详情")
    request_id: Optional[str] = Field(None, description="关联请求ID")
    timestamp: int = Field(..., description="错误时间戳(毫秒)")


# ---------------------------------------------------------------------------
# 运行时指标收集器（内存，用于 /metrics 端点）
# ---------------------------------------------------------------------------
class _RuntimeMetrics:
    """进程内运行时指标收集（轻量级内存实现）。

    注意：计数器依赖 CPython GIL 保证单进程内线程安全，
    但多 worker 模式下各 worker 独立计数（指标为 per-worker 视图）。
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
async def lifespan(application: FastAPI):
    """Startup / shutdown 生命周期钩子。

    Startup: 初始化 Ledger、执行启动探针、写入审计。
    Shutdown: 刷新审计缓冲。
    """
    _logger.info("Nastume AI Host Brain starting up ...")
    ledger = get_ledger()
    try:
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes={},
            request_id=new_request_id(),
            ledger=ledger,
            ts_ms=int(time.time() * 1000),
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        _logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})
    except Exception as exc:
        _logger.warning("Startup probe degraded", extra={"error": str(exc)})

    yield  # 服务运行阶段

    _logger.info("Nastume AI Host Brain shutting down ...")
    # 刷新审计缓冲（当前 SQLite 同步写入，此处预留扩展点）


# ---------------------------------------------------------------------------
# FastAPI 应用实例
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Nastume AI Host Brain",
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
# 注册 Canon 治理 API 路由（M10-A TASK-0118: Canon 提案审核 REST API）
# ---------------------------------------------------------------------------
app.include_router(canon_router)

# ---------------------------------------------------------------------------
# 注册 Scheduler REST API 路由（M11-A TASK-0149: 定时调度器任务管理）
# ---------------------------------------------------------------------------
app.include_router(scheduler_router)

# ---------------------------------------------------------------------------
# 挂载静态文件目录（TASK-0066: 前端面板页面）
# ---------------------------------------------------------------------------
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ---------------------------------------------------------------------------
# CORS 中间件（可配置）
# ---------------------------------------------------------------------------
# CORS 默认关闭：生产环境必须显式设置 Nastume_CORS_ORIGINS
# 例如 Nastume_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:8000"
_cors_origins_raw = os.environ.get("Nastume_CORS_ORIGINS", "").strip()
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
# 全局依赖
# ---------------------------------------------------------------------------
_ledger: Optional[SQLiteLedger] = None
_intent_engine: Optional[IntentDecisionEngine] = None
_logger = logging.getLogger("Nastume.fastapi")


def get_ledger() -> SQLiteLedger:
    """获取SQLite账本实例（延迟初始化，支持跨线程访问）。

    持久化路径通过环境变量 Nastume_AUDIT_DB 配置，
    默认使用文件路径 host_brain/data/audit.sqlite3。
    设置 Nastume_AUDIT_DB=:memory: 可使用内存数据库（仅测试）。
    """
    global _ledger
    if _ledger is None:
        db_path = os.environ.get("Nastume_AUDIT_DB", "host_brain/data/audit.sqlite3")
        _ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
    return _ledger


# 注入 ledger provider 到 canon_routes（消除循环导入风险，P1-R118-2）
_set_canon_ledger_provider(get_ledger)


def get_intent_engine() -> IntentDecisionEngine:
    """获取 Governor IntentDecisionEngine 实例（延迟初始化）"""
    global _intent_engine
    if _intent_engine is None:
        _intent_engine = IntentDecisionEngine(ledger=get_ledger())
    return _intent_engine


# 自定义异常处理器
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理，确保所有错误都有稳定的HTTP状态码映射"""
    request_id = getattr(request.state, 'request_id', None) or new_request_id()

    # 根据异常类型映射到HTTP状态码和reason_code
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"

    # 记录错误日志
    _logger.error(
        "HTTP request failed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "reason_code": reason_code,
            "detail": detail,
        }
    )

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            reason_code=reason_code,
            detail=detail,
            request_id=request_id,
            timestamp=int(time.time() * 1000),
        ).dict(),
    )


# 中间件：请求ID注入和审计
@app.middleware("http")
async def add_request_id_and_audit(request: Request, call_next):
    """为每个请求注入唯一ID并记录审计日志"""
    request_id = new_request_id()
    request.state.request_id = request_id

    start_time = time.time()

    # 记录请求开始
    _logger.info(
        "HTTP request started",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host if request.client else "unknown",
        }
    )

    try:
        _metrics.inc_request()
        response = await call_next(request)

        # 记录请求完成
        duration_ms = int((time.time() - start_time) * 1000)
        _logger.info(
            "HTTP request completed",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        )

        return response

    except Exception as exc:
        # 异常已在全局处理器中处理，这里只记录额外信息
        duration_ms = int((time.time() - start_time) * 1000)
        _logger.error(
            "HTTP request failed with exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "duration_ms": duration_ms,
                "exception": str(exc),
            }
        )
        raise


# 核心HTTP端点
@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查端点 - 快速服务可用性检查"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        health_report = build_service_health_report(
            runtime_statuses={},
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )

        return HealthResponse(
            status=health_report.get("status", "OK"),
            version="0.1.0",
            timestamp=ts_ms,
            details=health_report,
        )
    except Exception as exc:
        _logger.error("Health check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service health check failed",
        )


@app.get("/ready", response_model=ReadinessResponse)
async def environment_readiness() -> ReadinessResponse:
    """环境就绪性检查 - 完整的环境依赖验证"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        readiness_report = build_env_readiness_report(
            env="production",
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )

        readiness_status = readiness_report.get("readiness_status", "FAILED")

        return ReadinessResponse(
            ready=(readiness_status == "READY"),
            status=readiness_status,
            reason_code=readiness_report.get("reason_code"),
            timestamp=ts_ms,
            summary=readiness_report,
        )
    except Exception as exc:
        _logger.error("Readiness check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Environment readiness check failed",
        )


@app.post("/observability/trace", response_model=ObservabilityResponse)
async def request_trace(request_body: ObservabilityRequest) -> ObservabilityResponse:
    """请求追踪 - 获取特定请求的完整执行时间线"""
    ts_ms = int(time.time() * 1000)

    try:
        trace_report = build_request_observability_report(
            request_id=request_body.request_id,
            ledger=get_ledger(),
        )

        return ObservabilityResponse(
            request_id=request_body.request_id,
            trace_status=trace_report.get("trace_status", "UNKNOWN"),
            timeline_total=trace_report.get("timeline_total", 0),
            sources=trace_report.get("sources", {}),
            timeline=trace_report.get("timeline", []),
            timestamp=ts_ms,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        _logger.error("Request trace failed", extra={"request_id": request_body.request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve request trace",
        )


@app.get("/startup")
async def startup_report() -> Dict[str, Any]:
    """启动报告 - 获取系统启动时的详细配置和依赖状态"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes={},
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )

        # 记录启动报告用于调试
        emit_runtime_bootstrap_logs(startup_report=report)

        return {
            "request_id": request_id,
            "timestamp": ts_ms,
            "report": report,
        }
    except Exception as exc:
        _logger.error("Startup report failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate startup report",
        )


# ---------------------------------------------------------------------------
# 核心链路入口: POST /v1/chat
# ---------------------------------------------------------------------------
@app.post("/v1/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """编排入口 — 用户消息 → Intent 构造 → Governor 裁决 → 输出。

    所有执行仍经 Governor/Token/Skills，不引入旁路。
    成功/拒绝/降级均返回统一 ChatResponse 结构。
    """
    _metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"

    try:
        # 1) 构造 intent_draft（如果用户未提供则构建最小 intent）
        intent_draft = request_body.intent_draft
        if intent_draft is None:
            intent_draft = {
                "schema_version": "Nastume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": ts_ms,
                "session_id": session_id,
                "seq": 1,
                "type": "query",
                "action": "narrative_only",
                "target": "host",
                "args": {"text": request_body.message},
                "nonce": f"nonce::{request_id}",
            }

        # 2) Governor 裁决（经 Intent 校验 + R1-R7 Gates）
        engine = get_intent_engine()
        governor_result = engine.evaluate_intent(
            raw_intent=intent_draft,
            request_id=request_id,
            thread_id=request_body.thread_id,
        )

        decision = governor_result.get("decision")
        reason_code = governor_result.get("reason_code")
        intent_id = governor_result.get("intent_id")
        approval_token_ref = None
        tokens = governor_result.get("tokens")
        if isinstance(tokens, dict):
            approval_token_ref = tokens.get("approval_token_ref")

        # 3) 构造叙事输出
        if decision == DECISION_APPROVE:
            narrative = f"[已裁决通过] 用户输入: {request_body.message}"
            quality = {"stage": "S_COMPLETE", "reason_code": None}
        else:
            narrative = f"[已拒绝] 原因: {reason_code or 'UNKNOWN'}"
            quality = {"stage": governor_result.get("stage", "S_GOV_REJECT"), "reason_code": reason_code}
            _metrics.inc_rejection(reason_code or "UNKNOWN")

        return ChatResponse(
            request_id=request_id,
            narrative=narrative,
            intent_draft=intent_draft,
            decision=decision,
            reason_code=reason_code,
            approval_token_ref=approval_token_ref,
            quality=quality,
            audit_refs={
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
            },
            timestamp=ts_ms,
        )

    except Exception as exc:
        _metrics.inc_error()
        _logger.error("Chat request failed", extra={"request_id": request_id, "error": str(exc)})
        # 降级输出：结构稳定 + reason_code 可统计
        return ChatResponse(
            request_id=request_id,
            narrative="[降级] 处理请求时发生内部错误，请稍后重试。",
            intent_draft=None,
            decision="REJECT",
            reason_code="INTERNAL_SERVER_ERROR",
            approval_token_ref=None,
            quality={"stage": "S_INTERNAL_ERROR", "reason_code": "INTERNAL_SERVER_ERROR"},
            audit_refs={"request_id": request_id},
            timestamp=ts_ms,
        )


# ---------------------------------------------------------------------------
# GET /metrics — 运行时指标摘要
# ---------------------------------------------------------------------------
@app.get("/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    """运行时指标摘要 — 拒绝码分布、请求计数、运行时长"""
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
# Web UI 面板入口（TASK-0066: 链路可视化面板）
# ---------------------------------------------------------------------------
# 面板 HTML 缓存（避免每次请求读磁盘）
_panel_html_cache: Optional[str] = None
_panel_html_mtime: float = 0.0


@app.get("/panel")
async def panel_page() -> Response:
    """链路可视化面板入口 — 返回 HTML 页面（带缓存）"""
    global _panel_html_cache, _panel_html_mtime
    panel_path = os.path.join(os.path.dirname(__file__), "static", "panel.html")
    if not os.path.isfile(panel_path):
        return Response(
            content="<html><body><h1>Panel not found</h1><p>Static file panel.html missing.</p></body></html>",
            media_type="text/html",
            status_code=404,
        )
    # 检查文件是否更新（开发模式热刷新友好）
    mtime = os.path.getmtime(panel_path)
    if _panel_html_cache is None or mtime != _panel_html_mtime:
        with open(panel_path, "r", encoding="utf-8") as f:
            _panel_html_cache = f.read()
        _panel_html_mtime = mtime
    return Response(content=_panel_html_cache, media_type="text/html")


# ---------------------------------------------------------------------------
# TASK-0079: Edge→Host 事件批量上报端点
# ---------------------------------------------------------------------------
@app.post("/v1/edge/events", response_model=EdgeEventsResponse)
async def receive_edge_events(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    """接收 Edge 端批量上报的事件，校验并写入审计链。

    TASK-0079: 打通 Edge→Host 事件上报通道。
    事件通过 cross_timeline 的 EdgeEventReport 结构化解析后写入审计。
    """
    ts_ms = int(time.time() * 1000)
    raw_events = request_body.events

    if not raw_events:
        return EdgeEventsResponse(
            success=True, total=0, accepted=0, rejected=0,
            detail="empty batch", timestamp=ts_ms,
        )

    # 逐条校验必需字段
    required_fields = {"event_type", "ts_ms", "request_id", "session_id"}
    accepted_count = 0
    rejected_indices: list[int] = []
    ledger = get_ledger()

    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            rejected_indices.append(idx)
            continue
        missing = required_fields - set(raw.keys())
        event_type = str(raw.get("event_type") or "").strip()
        request_id = str(raw.get("request_id") or "").strip()
        session_id = str(raw.get("session_id") or "").strip()
        ts_val = raw.get("ts_ms")

        if missing or not event_type or not request_id or not session_id or not isinstance(ts_val, (int, float)):
            rejected_indices.append(idx)
            continue

        # 写入 voice_event_log（Edge 事件统一入表）
        try:
            ledger.log_voice_event({
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": str(raw.get("direction") or "EDGE_TO_HOST"),
                "seq": raw.get("seq") if isinstance(raw.get("seq"), int) else None,
                "status": str(raw.get("status") or "RECEIVED"),
                "reason_code": str(raw.get("reason_code") or "").strip() or None,
                "stage": str(raw.get("stage") or "").strip() or None,
                "detail": str(raw.get("detail") or "").strip() or None,
                "payload": raw.get("payload") or {},
                "ts_ms": int(ts_val),
            })
            accepted_count += 1
        except Exception as exc:
            _logger.warning("Failed to write edge event %d: %s", idx, exc)
            rejected_indices.append(idx)

    rejected_count = len(rejected_indices)
    total = len(raw_events)

    if rejected_count == 0:
        reason_code = None
        detail = f"all {total} events accepted"
    elif accepted_count == 0:
        reason_code = RPC_EVENT_SCHEMA_INVALID
        detail = f"all {total} events rejected"
    else:
        reason_code = RPC_EVENT_BATCH_PARTIAL_FAIL
        detail = f"accepted {accepted_count}, rejected {rejected_count} of {total}"

    return EdgeEventsResponse(
        success=(rejected_count == 0),
        total=total,
        accepted=accepted_count,
        rejected=rejected_count,
        reason_code=reason_code,
        detail=detail,
        rejected_indices=rejected_indices,
        timestamp=ts_ms,
    )


# ---------------------------------------------------------------------------
# 根路径重定向到文档
# ---------------------------------------------------------------------------
@app.get("/")
async def root() -> Response:
    """根路径重定向到API文档"""
    return Response(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/docs"})


if __name__ == "__main__":
    import uvicorn

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # 启动FastAPI服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=True,
    )
