"""FastAPI web application entry for Natsume AI host brain.

TASK-0065: HTTP 服务框架落地 — FastAPI 路由绑定 + 健康检测 + 链路入口。
提供 POST /v1/chat（编排入口）、GET /health、GET /ready、GET /metrics，
所有执行仍继续 Governor/Token/Skills，不引入旁路。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Mapping, Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from host_brain.app.schemas import (
    ChatRequest,
    EdgeEventsRequest,
    EdgeEventsResponse,
    MetricsResponse,
)
from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.canon_routes import router as canon_router, set_ledger_provider as _set_canon_ledger_provider
from host_brain.scheduler.scheduler_routes import (
    router as scheduler_router,
    set_scheduler_providers as _set_scheduler_providers,
)
from host_brain.app.automation.scheduler_routes import router as host_scheduler_router  # Ref #108: HostTaskScheduler 管理路由
from host_brain.app.tool_routes import router as tool_router, tool_kits_alias_router, set_tool_providers as _set_tool_providers
from host_brain.app.bond_routes import router as bond_router, set_bond_engine_provider as _set_bond_engine_provider
from host_brain.app.obs_routes import router as obs_router, set_ledger_provider as _set_obs_ledger_provider
from host_brain.app.health_routes import router as health_router
from host_brain.app.agent_routes import router as agent_router
from host_brain.app.input_routes import router as input_router
from host_brain.app.prompt_config_routes import router as prompt_config_router
from host_brain.app.internal_tribe_routes import router as internal_tribe_router
from host_brain.app.internal_codex_routes import router as internal_codex_router
from host_brain.app.dependencies import (
    get_ledger as _get_ledger_dep,
    get_tool_catalog,
    get_tool_broker,
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

# Legacy injectable globals retained for compatibility with older E2E tests.
_ledger = None
_intent_engine = None


def _get_ledger_compat():
    if _ledger is not None:
        return _ledger
    return _get_ledger_dep()


def _get_bond_engine_via_social_brain():
    from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

    return get_social_brain_agent().resolve_stage_engine()


def _sse_single_commit(payload: dict[str, Any]) -> StreamingResponse:
    async def _single_event():
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _single_event(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_immediate_events(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(dict(event or {}), ensure_ascii=False)}\n\n"
        for event in list(events or [])
    )
    return Response(
        content=body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _maybe_build_http_chat_fastpath(
    *,
    request_id: str,
    request_body: ChatRequest,
) -> dict[str, Any] | None:
    """Resolve deterministic companionship fastpaths before entering the SSE generator.

    These ultra-common companionship turns do not need the full streaming chat
    stack. Returning them directly keeps presence/commit responsive even when the
    async generator startup path is noisy.
    """
    try:
        from host_brain.app.agent_runtime_shared import _maybe_build_phatic_fastpath_reply  # noqa: PLC0415
        from host_brain.app.prompt_builder import infer_render_target_lang  # noqa: PLC0415
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - defensive import guard
        _logger.debug("http chat fastpath import skipped: %s", exc)
        return None

    session_id = str(request_body.session_id or "").strip() or f"session::{request_id}"
    request_body.person_id = resolve_person_id(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )
    render_target_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower()
    if not render_target_lang:
        try:
            render_target_lang = infer_render_target_lang(user_message=str(request_body.message or ""))
        except Exception:
            render_target_lang = "zh"
    normalized_message = str(request_body.message or "").strip()
    reply = _maybe_build_phatic_fastpath_reply(
        request_id=request_id,
        session_id=session_id,
        person_id=str(request_body.person_id or "").strip() or session_id,
        user_message=normalized_message,
        render_target_lang=render_target_lang or "zh",
    )
    if not reply and any(
        token in normalized_message
        for token in (
            "认真聊聊最近的状态",
            "认真聊聊最近",
            "慢慢陪我说下去",
            "慢慢陪我聊",
            "陪我说下去",
            "慢慢聊聊最近",
            "想认真聊聊",
            "想和你认真聊聊",
            "认真说一说最近",
        )
    ):
        reply = _maybe_build_phatic_fastpath_reply(
            request_id=request_id,
            session_id=session_id,
            person_id=str(request_body.person_id or "").strip() or session_id,
            user_message=normalized_message,
            render_target_lang=render_target_lang or "zh",
            load_continuity_turns=lambda **_kwargs: [],
            looks_like_active_task_context=lambda _turns: False,
        )
    if not reply:
        return None
    return {
        "reply": str(reply),
        "session_id": session_id,
        "render_target_lang": render_target_lang or "zh",
    }


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _resolve_uvicorn_access_log_enabled() -> bool:
    raw = str(os.environ.get("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED") or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "on"}


def _normalize_bind_host(raw: str, fallback: str = "127.0.0.1") -> str:
    value = str(raw or "").strip()
    if not value or value == "0.0.0.0":
        return fallback
    return value


def _build_redirect_target(
    *,
    host: str,
    port: int,
    path: str,
    query_string: bytes,
) -> str:
    target = f"http://{host}:{port}{path}"
    if query_string:
        target = f"{target}?{query_string.decode('utf-8', errors='ignore')}"
    return target


def _resolve_obs_redirect_target(request: Request) -> str | None:
    if not _env_flag("HOST_MAIN_OBS_REDIRECT_ENABLED", default=False):
        return None
    path = str(request.url.path or "")
    if not (path.startswith("/obs/") or path.startswith("/api/ui/")):
        return None
    host = _normalize_bind_host(
        os.environ.get("HOST_OBS_APP_HOST", os.environ.get("HOST_APP_HOST", "127.0.0.1"))
    )
    try:
        port = int(os.environ.get("HOST_OBS_APP_PORT", "18082"))
    except Exception:
        port = 18082
    return _build_redirect_target(
        host=host,
        port=port,
        path=path,
        query_string=request.scope.get("query_string", b""),
    )


def _resolve_monitor_redirect_target(request: Request) -> str | None:
    if not _env_flag("HOST_MAIN_MONITOR_REDIRECT_ENABLED", default=False):
        return None
    path = str(request.url.path or "")
    if path == "/panel":
        path = "/monitor/"
    elif not (
        path.startswith("/monitor")
        or path.startswith("/static/monitor")
    ):
        return None
    host = _normalize_bind_host(os.environ.get("HOST_MONITOR_PROXY_HOST", "127.0.0.1"))
    try:
        port = int(os.environ.get("HOST_MONITOR_PROXY_PORT", "18180"))
    except Exception:
        port = 18180
    if path.startswith("/static/monitor"):
        suffix = path[len("/static/monitor"):]
        path = "/monitor" + suffix
    return _build_redirect_target(
        host=host,
        port=port,
        path=path,
        query_string=request.scope.get("query_string", b""),
    )


def _evaluate_immune_gate_for_chat(request_body: ChatRequest) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

    return get_immune_agent().inspect_text(
        text=str(request_body.message or ""),
        source="http.v1.chat",
        metadata={
            "session_id": str(request_body.session_id or "").strip() or None,
            "person_id": str(request_body.person_id or "").strip() or None,
            "input_source": str(request_body.input_source or "").strip() or None,
        },
    )


def _log_chat_immune_gate_event(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: Mapping[str, Any],
) -> None:
    try:
        ledger = _get_ledger_compat()
    except Exception:
        return
    if ledger is None:
        return
    decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    status = "OK"
    if decision == "REPORT":
        status = "DEGRADED"
    elif decision == "BLOCK":
        status = "REJECTED"
    try:
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": str(request_body.session_id or "").strip() or None,
                "event_type": "IMMUNE_CHAT_GATE",
                "direction": "INGRESS",
                "status": status,
                "reason_code": "IMMUNE_BLOCKED_HIGH_RISK" if decision == "BLOCK" else None,
                "stage": "chat.immune_gate",
                "detail": f"decision={decision} risk={immune_result.get('risk_level')} score={immune_result.get('score')}",
                "payload": {
                    "decision": decision,
                    "risk_level": immune_result.get("risk_level"),
                    "score": immune_result.get("score"),
                    "reason_codes": list(immune_result.get("reason_codes") or []),
                    "matched_rules": list(immune_result.get("matched_rules") or [])[:6],
                    "input_source": str(request_body.input_source or "").strip() or None,
                    "person_id": str(request_body.person_id or "").strip() or None,
                },
                "ts_ms": int(time.time() * 1000),
            }
        )
    except Exception as exc:
        _logger.debug("chat immune gate audit skipped: %s", exc)


def _schedule_chat_immune_gate_event(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: Mapping[str, Any],
) -> None:
    """Persist immune gate audit off the hot request path.

    The chat route should return its SSE stream immediately. SQLite ledger writes
    can occasionally block on lock contention, so we schedule the audit in a
    worker thread instead of performing it synchronously before StreamingResponse
    is returned.
    """
    try:
        asyncio.create_task(
            asyncio.to_thread(
                _log_chat_immune_gate_event,
                request_id=request_id,
                request_body=request_body.model_copy(deep=True),
                immune_result=dict(immune_result or {}),
            )
        )
    except Exception as exc:
        _logger.debug("chat immune gate audit schedule skipped: %s", exc)


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
# 路由依赖注入（避免 provider 未配置导致假性 DEGRADED）
# ---------------------------------------------------------------------------
_set_canon_ledger_provider(_get_ledger_compat)
_set_obs_ledger_provider(_get_ledger_compat)
_set_tool_providers(
    broker_provider=get_tool_broker,
    catalog_provider=get_tool_catalog,
    ledger_provider=_get_ledger_compat,
)
_set_bond_engine_provider(_get_bond_engine_via_social_brain)
try:
    from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415
    from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415

    _scheduler_registry = SchedulerTaskRegistry.get_instance()
    _scheduler_health = SchedulerHealthReporter(registry=_scheduler_registry)
    _set_scheduler_providers(
        registry=_scheduler_registry,
        health_reporter=_scheduler_health,
    )
except Exception as exc:  # pragma: no cover - defensive startup guard
    _logger.warning("scheduler provider wiring degraded: %s", exc)


# ---------------------------------------------------------------------------
# 生命周期钩子
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown 生命周期钩子。"""
    async with runtime_lifespan(get_ledger=_get_ledger_compat, logger=_logger):
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
# 注册 HostTaskScheduler REST API 路由（Ref #108: app/automation/task_scheduler.py 管理接口）
# 使用 /host-scheduler 前缀，避免与旧 /scheduler 路由冲突
# ---------------------------------------------------------------------------
app.include_router(host_scheduler_router, prefix="/host-scheduler")

# ---------------------------------------------------------------------------
# 注册 ToolBroker REST API 路由（M12-A TASK-0156: 工具元数据包 + 执行入口）
# ---------------------------------------------------------------------------
app.include_router(tool_router)
app.include_router(tool_kits_alias_router)  # GET /tool_kits 别名，兼容 UAT/外部前端

# ---------------------------------------------------------------------------
# 注册 Bond 阶段 REST API 路由（M13-A: Bond 阶段积分 + 自动晋级）
# ---------------------------------------------------------------------------
app.include_router(bond_router)

# ---------------------------------------------------------------------------
# 注册 Observability REST API 路由（M18: 监控面板后端数据接口）
# ---------------------------------------------------------------------------
app.include_router(obs_router)

# ---------------------------------------------------------------------------
# 注册 Agent Loop REST API 路由（ClawdBot 视觉自动化闭环）
# ---------------------------------------------------------------------------
app.include_router(agent_router)

# ---------------------------------------------------------------------------
# 注册 Prompt 模板配置 REST API 路由
# ---------------------------------------------------------------------------
app.include_router(prompt_config_router)

# ---------------------------------------------------------------------------
# 注册 Edge 输入接收路由（ASR / 微信消息 → Graph 流通）
# ---------------------------------------------------------------------------
app.include_router(input_router)

# ---------------------------------------------------------------------------
# 注册 Tribe 内部事件入口（Host/Edge 事件桥）
# ---------------------------------------------------------------------------
app.include_router(internal_tribe_router)
app.include_router(internal_codex_router)

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

# 静态资源体积较大（React + ECharts），启用 gzip 减少刷新等待。
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

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


@app.middleware("http")
async def redirect_obs_and_monitor_traffic(request: Request, call_next: Callable) -> Response:
    """Optionally offload monitor/obs traffic away from host-main.

    Runtime scripts can enable this redirect so accidental use of host-main
    monitor URLs does not send heavy observability traffic back into the main
    chat process.
    """
    monitor_target = _resolve_monitor_redirect_target(request)
    if monitor_target:
        return RedirectResponse(url=monitor_target, status_code=307)

    obs_target = _resolve_obs_redirect_target(request)
    if obs_target:
        return RedirectResponse(url=obs_target, status_code=307)

    return await call_next(request)


@app.middleware("http")
async def tune_monitor_static_cache(request: Request, call_next: Callable) -> Response:
    response = await call_next(request)
    if request.method.upper() != "GET":
        return response

    path = (request.url.path or "").strip()
    if path.startswith("/monitor/assets/") or path.startswith("/static/monitor/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif path in {"/monitor", "/monitor/", "/monitor/index.html", "/static/monitor/index.html"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# 注册 Health / Readiness / Observability 路由（health_routes.py 统一管理）
# 注意：/health、/ready、/obs/metrics、/health/mcp、/health/canon_sync、
#       /observability/trace、/startup 均由 health_router 注册，此处不重复注册。
# ---------------------------------------------------------------------------
app.include_router(health_router)


# ---------------------------------------------------------------------------
# FastAPI 应用实例
# ---------------------------------------------------------------------------
@app.post("/v1/chat")
async def chat(request_body: ChatRequest, request: Request) -> StreamingResponse:
    """POST /v1/chat — 统一入口，根据场景自动路由到本地单节点或云端多节点，统一返回 SSE 流式响应。"""
    # 通知 ActivityMonitor 有用户互动（触发 sleep→normal 唤醒 + 空闲计时器重置）
    try:
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415
        notify_user_interaction()
    except Exception:
        pass
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    request.state.request_id = request_id
    try:
        immune_result = _evaluate_immune_gate_for_chat(request_body)
    except Exception as exc:
        _logger.warning("immune gate evaluate degraded: %s", exc)
        immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    request.state.immune_inspection = immune_result
    _schedule_chat_immune_gate_event(
        request_id=request_id,
        request_body=request_body,
        immune_result=immune_result,
    )
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    if immune_decision == "BLOCK":
        _logger.warning(
            "immune gate blocked /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
        return _sse_single_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "IMMUNE_BLOCKED_HIGH_RISK",
                "narrative": "我先停一下，这条输入里有高风险指令模式。换个表达我会继续接住你。",
                "request_id": request_id,
                "immune": {
                    "risk_level": immune_result.get("risk_level"),
                    "reason_codes": immune_result.get("reason_codes"),
                    "score": immune_result.get("score"),
                },
            }
        )
    if immune_decision == "REPORT":
        _logger.info(
            "immune gate report /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
    fastpath_started_ms = int(time.time() * 1000)
    fastpath_payload = _maybe_build_http_chat_fastpath(
        request_id=request_id,
        request_body=request_body,
    )
    if fastpath_payload is not None:
        _metrics.inc_chat()
        latency_ms = max(0, int(time.time() * 1000) - fastpath_started_ms)
        reply = str(fastpath_payload.get("reply") or "").strip()
        render_target_lang = str(fastpath_payload.get("render_target_lang") or "zh").strip().lower() or "zh"
        audit_refs = {
            "request_id": request_id,
            "path": "chat_agent:sub=phatic_fastpath",
            "latency_ms": latency_ms,
            "first_token_ms": 0,
            "llm_latency_ms": 0,
            "emotion_tag": str(getattr(request_body, "emotion_tag", None) or "NEUTRAL").strip().upper() or "NEUTRAL",
            "stream_buffered": False,
            "model_id": "deterministic-plan-renderer",
            "render_target_lang": render_target_lang,
            "tts_text_ja": None,
            "timings_ms": {
                "prompt_build_ms": 0,
                "llm_runtime_resolve_ms": 0,
                "stream_after_first_token_ms": 0,
                "response_intelligence_ms": 0,
                "sanitize_egress_ms": 0,
                "precommit_log_ms": 0,
            },
            "response_intelligence": {
                "reflection": {"status": "SKIPPED", "reason_code": "NOT_TRIGGERED"},
                "multimodal_output": {
                    "status": "ACTIVE",
                    "result": {
                        "text": reply,
                        "items_count": 0,
                        "suggested_sticker": None,
                        "suggested_emoji": None,
                    },
                },
            },
            "pii_stats": {},
        }
        _logger.info(
            "chat http fastpath request_id=%s path=%s latency_ms=%s render_target_lang=%s",
            request_id,
            "chat_agent:sub=phatic_fastpath",
            latency_ms,
            render_target_lang,
        )
        return _sse_immediate_events(
            [
                {"phase": "PRESENCE", "event": "THINKING", "request_id": request_id},
                {"phase": "RENDER", "chunk": reply, "request_id": request_id},
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "APPROVE",
                    "narrative": reply,
                    "request_id": request_id,
                    "audit_refs": audit_refs,
                },
            ]
        )
    return await handle_chat(request_body=request_body, request=request, metrics=_metrics)


# ---------------------------------------------------------------------------
# Edge 事件端点
# ---------------------------------------------------------------------------
@app.post("/v1/edge/events", response_model=EdgeEventsResponse)
async def receive_edge_events(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    """接收 Edge 端批量上报的事件 — 委托给 system_handler.receive_edge_events_response。"""
    bridge_waiters: list[Any] = []
    loop = asyncio.get_running_loop()
    response = await asyncio.to_thread(
        receive_edge_events_response,
        request_body=request_body,
        get_ledger=_get_ledger_compat,
        logger=_logger,
        inline_waiters=bridge_waiters,
        main_loop=loop,
    )
    if bridge_waiters:
        await asyncio.gather(*bridge_waiters)
    return response


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
        access_log=_resolve_uvicorn_access_log_enabled(),
    )
