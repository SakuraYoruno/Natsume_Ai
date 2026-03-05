"""FastAPI web application entry for Natsume AI host brain.

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
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

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
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from edge_client.rpc.health import build_edge_transport_probe_statuses
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_SCHEMA_INVALID,
    STARTUP_MCP_DEGRADED,
)
from host_brain.core.ids import new_request_id
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.canon_sync import detect_world_version_drift
from host_brain.obs.metrics import MetricsCollector
from host_brain.obs.routes import build_obs_metrics_response
from host_brain.observability.cross_timeline import build_cross_timeline
from host_brain.retrieval.pipeline import (
    RetrievalPipelineContract,
    build_retrieval_pipeline_from_env,
)
from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.canon_routes import router as canon_router, set_ledger_provider as _set_canon_ledger_provider
from host_brain.scheduler.scheduler_routes import router as scheduler_router
from host_brain.app.tool_routes import router as tool_router, set_tool_providers as _set_tool_providers
from host_brain.app.bond_routes import router as bond_router, set_bond_engine_provider as _set_bond_engine_provider
from host_brain.app.obs_routes import router as obs_router, set_ledger_provider as _set_obs_ledger_provider
from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.broker import ToolBroker
from host_brain.governor.tokens import TokenService
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.integrations.llm_gateway import (
    PROVIDER_DEEPSEEK,
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)


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
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown 生命周期钩子。

    Startup: 初始化 Ledger、执行启动探针、写入审计。
    Shutdown: 刷新审计缓冲。
    """
    _logger.info("Natsume AI Host Brain starting up ...")
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

    _logger.info("Natsume AI Host Brain shutting down ...")
    # 刷新审计缓冲（当前 SQLite 同步写入，此处预留扩展点）


# ---------------------------------------------------------------------------
# FastAPI 应用实例
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
# 注册 Canon 治理 API 路由（M10-A TASK-0118: Canon 提案审核 REST API）
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
# 全局依赖
# ---------------------------------------------------------------------------
_ledger: Optional[SQLiteLedger] = None
_intent_engine: Optional[IntentDecisionEngine] = None
_tool_catalog: Optional[ToolCatalog] = None
_tool_broker: Optional[ToolBroker] = None
_bond_stage_engine: Optional[BondStageEngine] = None
_retrieval_pipeline: Optional[RetrievalPipelineContract] = None
_obs_metrics_collector: Optional[MetricsCollector] = None
_graph_runtime: Optional[GraphRuntimeContract] = None
_logger = logging.getLogger("Natsume.fastapi")


def _resolve_host_llm_model_id(provider_name: str) -> str:
    provider = str(provider_name or "").strip().lower()
    override_model = str(os.environ.get("HOST_LLM_MODEL_ID") or "").strip()
    if override_model:
        return override_model
    if provider == PROVIDER_GEMINI:
        return str(os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if provider == PROVIDER_OPENAI:
        return str(os.environ.get("HOST_OPENAI_MODEL_ID") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    if provider == PROVIDER_DEEPSEEK:
        return str(os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-chat").strip() or "deepseek-chat"
    return "mock-default"


def _truncate_for_prompt(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def _render_host_generation_prompt(prompt_payload: dict[str, Any]) -> str:
    user_message = _truncate_for_prompt(prompt_payload.get("user_message"), limit=1000)
    retrieval_raw = prompt_payload.get("retrieval")
    retrieval = dict(retrieval_raw) if isinstance(retrieval_raw, dict) else {}
    retrieval_status = str(retrieval.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    retrieval_reason = str(retrieval.get("reason_code") or "").strip() or "NONE"
    evidence_items = retrieval.get("evidence_items")
    rows = evidence_items if isinstance(evidence_items, list) else []
    evidence_lines: list[str] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        evidence_ref = _truncate_for_prompt(row.get("evidence_ref") or row.get("source_ref") or "unknown", limit=80)
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        evidence_lines.append(f"- [{evidence_ref}] {snippet}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "- (none)"
    return (
        "You are the Natsume host narrative generator.\n"
        "Return exactly one JSON object with top-level keys: narrative, intent_draft, meta.\n"
        "Required schema:\n"
        "{\"narrative\":{\"text\":\"...\"},\"intent_draft\":{},\"meta\":{\"draft_only\":true}}\n"
        "Rules:\n"
        "1. Output JSON only. No markdown. No code fences.\n"
        "2. Keep draft_only=true.\n"
        "3. Do not claim execution/saving/confirmation has happened.\n"
        "4. Reply in Simplified Chinese unless user message is clearly another language.\n"
        "5. Keep response concise and grounded in user message and evidence.\n\n"
        f"User message:\n{user_message}\n\n"
        f"Retrieval status: {retrieval_status}\n"
        f"Retrieval reason_code: {retrieval_reason}\n"
        f"Evidence:\n{evidence_block}\n"
    )


def _extract_json_object_from_text(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise RuntimeError("provider returned empty content")

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        snippet = text[first : last + 1]
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("provider content does not contain a valid json object")


def _coerce_gateway_payload_json_text(raw_text: str) -> str:
    parsed = _extract_json_object_from_text(raw_text)
    narrative_node = parsed.get("narrative")
    narrative_text = ""
    if isinstance(narrative_node, dict):
        narrative_text = str(narrative_node.get("text") or "").strip()
    elif isinstance(narrative_node, str):
        narrative_text = narrative_node.strip()
    if not narrative_text:
        narrative_text = str(parsed.get("text") or "").strip()
    if not narrative_text:
        raise RuntimeError("provider output missing narrative.text")

    intent_draft = parsed.get("intent_draft")
    if not isinstance(intent_draft, dict):
        intent_draft = {}
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    safe_meta = dict(meta)
    safe_meta["draft_only"] = True

    contract_payload = {
        "narrative": {"text": narrative_text},
        "intent_draft": intent_draft,
        "meta": safe_meta,
    }
    return json.dumps(contract_payload, ensure_ascii=False, sort_keys=True)


def _extract_langchain_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if text_value is not None:
                parts.append(str(text_value))
        return "".join(parts)
    return str(content)


def _build_langchain_chat_provider_fn(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
) -> Callable[[dict[str, Any]], Any]:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    provider = str(provider_name or "").strip().lower()
    model_name = str(model_id or "").strip() or "default"
    base_url: str | None = None
    if provider == PROVIDER_DEEPSEEK:
        base_url = str(os.environ.get("HOST_DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
    elif provider == PROVIDER_GEMINI:
        base_url = str(
            os.environ.get("HOST_GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"
        ).strip()
    else:
        base_url = str(os.environ.get("HOST_OPENAI_BASE_URL") or "").strip() or None

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        timeout=max(1.0, float(timeout_ms) / 1000.0),
        max_retries=1,
        temperature=0.4,
    )
    system_prompt = (
        "You are the Natsume host narrative generator. "
        "Output exactly one JSON object with keys narrative, intent_draft, meta. "
        "meta.draft_only must be true."
    )

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        prompt_text = _render_host_generation_prompt(runtime_payload)
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt_text),
            ]
        )
        response_text = _extract_langchain_content_text(getattr(response, "content", ""))
        return _coerce_gateway_payload_json_text(response_text)

    return _provider


def _build_host_mock_provider() -> Callable[[dict[str, Any]], Any]:
    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        user_message = _truncate_for_prompt(runtime_payload.get("user_message"), limit=120)
        text = "This is a draft response."
        if user_message:
            text = f"Got your message: {user_message}"
        return json.dumps(
            {
                "narrative": {"text": text},
                "intent_draft": {},
                "meta": {"draft_only": True, "source": "host_mock_provider"},
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return _provider


def _build_host_provider_fn(
    *,
    provider_name: str,
    model_id: str,
    api_key: str | None,
    timeout_ms: int,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    key_value = str(api_key or "").strip()
    if provider in {PROVIDER_OPENAI, PROVIDER_DEEPSEEK, PROVIDER_GEMINI} and key_value:
        return _build_langchain_chat_provider_fn(
            provider_name=provider,
            model_id=model_id,
            api_key=key_value,
            timeout_ms=timeout_ms,
        )
    return _build_host_mock_provider()
def get_ledger() -> SQLiteLedger:
    """获取SQLite账本实例（延迟初始化，支持跨线程访问）。

    持久化路径通过环境变量 Natsume_AUDIT_DB 配置，
    默认使用文件路径 host_brain/data/audit.sqlite3。
    设置 Natsume_AUDIT_DB=:memory: 可使用内存数据库（仅测试）。
    """
    global _ledger
    if _ledger is None:
        db_path = os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
        _ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
    return _ledger


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            loaded = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _resolve_node_inventory_payload() -> tuple[list[dict[str, Any]], int | None, str | None]:
    configured = str(os.environ.get("HOST_NODE_INVENTORY_PATH") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("configs/deploy/node_inventory_9.json"),
            Path("tests/fixtures/services/good/node_inventory_9.json"),
        ]
    )

    for path in candidates:
        payload = _load_json_file(path)
        if payload is None:
            continue
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            continue
        nodes: list[dict[str, Any]] = [dict(row) for row in raw_nodes if isinstance(row, dict)]
        expected = payload.get("expected_node_count")
        expected_count: int | None = None
        if isinstance(expected, int):
            expected_count = int(expected)
        elif expected is not None:
            try:
                expected_count = int(str(expected).strip())
            except (TypeError, ValueError):
                expected_count = None
        return nodes, expected_count, str(path)
    return [], None, None


def _get_runtime_node_id() -> str:
    return str(
        os.environ.get("HOST_RETR_NODE_ID")
        or os.environ.get("Natsume_HOST_NODE_ID")
        or "natsu-node-01"
    ).strip() or "natsu-node-01"


def get_retrieval_pipeline() -> RetrievalPipelineContract:
    global _retrieval_pipeline
    if _retrieval_pipeline is None:
        _retrieval_pipeline = build_retrieval_pipeline_from_env(
            env=os.environ,
            ledger=get_ledger(),
            node_id=_get_runtime_node_id(),
        )
    return _retrieval_pipeline


def get_graph_runtime() -> GraphRuntimeContract:
    global _graph_runtime
    if _graph_runtime is None:
        _graph_runtime = GraphRuntimeContract(
            ledger=get_ledger(),
            default_timeout_ms=5_000,
        )
    return _graph_runtime


def get_obs_metrics_collector() -> MetricsCollector:
    global _obs_metrics_collector
    if _obs_metrics_collector is None:
        _obs_metrics_collector = MetricsCollector(ledger=get_ledger())
    return _obs_metrics_collector


# 注入 ledger provider 到 canon_routes（消除循环导入风险，P1-R118-2）
_set_canon_ledger_provider(get_ledger)

# 注入 ledger provider 到 obs_routes（M18）
_set_obs_ledger_provider(get_ledger)


def get_tool_catalog() -> ToolCatalog:
    """获取 ToolCatalog 单例（延迟初始化）。"""
    global _tool_catalog
    if _tool_catalog is None:
        _tool_catalog = ToolCatalog()
    return _tool_catalog


def get_tool_broker() -> ToolBroker:
    """获取 ToolBroker 单例（延迟初始化）。"""
    global _tool_broker
    if _tool_broker is None:
        token_service = TokenService()
        _tool_broker = ToolBroker(
            catalog=get_tool_catalog(),
            token_service=token_service,
            ledger=get_ledger(),
        )
    return _tool_broker


# 注入 tool providers 到 tool_routes（M12-A TASK-0156）
_set_tool_providers(
    broker_provider=get_tool_broker,
    catalog_provider=get_tool_catalog,
    ledger_provider=get_ledger,
)


def get_bond_stage_engine() -> BondStageEngine:
    """获取 BondStageEngine 单例（延迟初始化）。"""
    global _bond_stage_engine
    if _bond_stage_engine is None:
        _bond_stage_engine = BondStageEngine(ledger=get_ledger())
    return _bond_stage_engine


# 注入 bond engine provider 到 bond_routes（M13-A）
_set_bond_engine_provider(get_bond_stage_engine)


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
async def add_request_id_and_audit(request: Request, call_next: Callable) -> Response:
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
        # 先获取真实 runtime_statuses，确保健康检查能反映服务真实状态
        _host_result = load_host_config()
        _runtime_statuses: dict[str, Any] = {}
        if _host_result.success and _host_result.config is not None:
            _runtime_statuses = build_host_runtime_statuses(_host_result.config)
        # TASK-0211 M23-B：合并 edge 传输层探针结果
        # build_edge_transport_probe_statuses 原本定义但未被调用，
        # 此处集成使 edge 节点运行时状态能反映到 host 侧健康报告中。
        try:
            _edge_available = _runtime_statuses.get("edge.rpc.gateway", {}).get("available", True)
            _edge_probes = build_edge_transport_probe_statuses(available=bool(_edge_available))
            # edge 探针结果不覆盖已有的 host 侧状态，仅补充 edge 传输层状态
            for _svc_id, _probe in _edge_probes.items():
                if _svc_id not in _runtime_statuses:
                    _runtime_statuses[_svc_id] = _probe
        except Exception as _edge_exc:
            _logger.warning("Edge transport probe failed (degraded): %s", _edge_exc)
        health_report = build_service_health_report(
            runtime_statuses=_runtime_statuses,
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
        node_inventory, expected_node_count, inventory_path = _resolve_node_inventory_payload()
        readiness_report = build_env_readiness_report(
            env=str(os.environ.get("Natsume_ENV") or "dev"),
            node_inventory=node_inventory,
            expected_node_count=expected_node_count,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        if inventory_path:
            readiness_report["node_inventory_path"] = inventory_path

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


@app.get("/obs/metrics")
async def obs_metrics(request_id: Optional[str] = None) -> Dict[str, Any]:
    """M8 启动探针使用的 /obs/metrics HTTP 路由。"""
    ts_ms = int(time.time() * 1000)
    payload = build_obs_metrics_response(
        collector=get_obs_metrics_collector(),
        request_id=request_id,
        ts_ms=ts_ms,
    )
    payload["timestamp"] = ts_ms
    return payload


@app.get("/health/mcp", response_model=HealthResponse)
async def mcp_health_check() -> HealthResponse:
    """MCP 注册表健康检查（探针对齐）。"""
    ts_ms = int(time.time() * 1000)
    db_path = str(os.environ.get("HOST_MCP_REGISTRY_DB") or "host_brain/data/mcp_registry.sqlite3").strip()
    status_text = "OK"
    reason_code = None
    detail = "mcp registry reachable"
    tool_count = 0
    registry: MCPToolRegistry | None = None
    try:
        registry = MCPToolRegistry(db_path=db_path, check_same_thread=False)
        tool_count = len(registry.list_all())
        if tool_count == 0:
            status_text = "DEGRADED"
            reason_code = STARTUP_MCP_DEGRADED
            detail = "mcp registry reachable but no tools registered"
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = STARTUP_MCP_DEGRADED
        detail = str(exc)
    finally:
        if registry is not None:
            registry.close()
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "db_path": db_path,
            "tool_count": tool_count,
            "reason_code": reason_code,
            "detail": detail,
        },
    )


@app.get("/health/canon_sync", response_model=HealthResponse)
async def canon_sync_health_check() -> HealthResponse:
    """World Canon 同步健康检查（探针对齐）。"""
    ts_ms = int(time.time() * 1000)
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    status_text = "OK"
    reason_code = None
    detail = "canon sync healthy"
    drift_count = 0
    drift_sample: list[dict[str, Any]] = []
    try:
        drifts = detect_world_version_drift(world_root=world_root, ledger=get_ledger())
        drift_count = len(drifts)
        if drift_count > 0:
            status_text = "DEGRADED"
            reason_code = CANON_SYNC_FAILED
            detail = "world canon version drift detected"
            drift_sample = [dict(row) for row in drifts[:5]]
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = CANON_SYNC_FAILED
        detail = str(exc)
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "world_root": world_root,
            "drift_count": drift_count,
            "drift_sample": drift_sample,
            "reason_code": reason_code,
            "detail": detail,
        },
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


def _extract_gateway_narrative(payload: dict[str, Any] | None) -> str:
    node = dict(payload or {})
    narrative = node.get("narrative")
    if isinstance(narrative, dict):
        text = str(narrative.get("text") or "").strip()
        if text:
            return text
    elif isinstance(narrative, str):
        text = narrative.strip()
        if text:
            return text
    return ""


def _build_chat_generation_prompt_payload(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "retrieval": {
            "status": retrieval_summary.get("status"),
            "reason_code": retrieval_summary.get("reason_code"),
            "route_count": retrieval_summary.get("route_count"),
            "candidate_count": retrieval_summary.get("candidate_count"),
            "rerank_topk": retrieval_summary.get("rerank_topk"),
            "degraded": retrieval_summary.get("degraded"),
            "evidence_items": retrieval_evidence_items,
        },
    }


def _resolve_host_llm_runtime() -> dict[str, Any]:
    config_result = load_host_config()
    provider_name = str(os.environ.get("HOST_LLM_PROVIDER") or PROVIDER_OPENAI).strip().lower() or PROVIDER_OPENAI
    timeout_ms = 15_000
    llm_api_key: str | None = None
    available = True
    if config_result.success and config_result.config is not None:
        provider_name = str(config_result.config.llm_provider or provider_name).strip().lower() or provider_name
        timeout_ms = int(config_result.config.llm_timeout_ms)
        llm_api_key = config_result.config.llm_api_key
        available = (provider_name != PROVIDER_DISABLED)
    model_id = _resolve_host_llm_model_id(provider_name)
    provider_fn = _build_host_provider_fn(
        provider_name=provider_name,
        model_id=model_id,
        api_key=llm_api_key,
        timeout_ms=timeout_ms,
    )
    return {
        "provider_name": provider_name,
        "model_id": model_id,
        "timeout_ms": timeout_ms,
        "llm_api_key": llm_api_key,
        "available": available,
        "provider_fn": provider_fn,
    }


def _build_chat_graph_config(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    api_key: str | None,
    provider_fn: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    return {
        "graph_id": "host.chat.realtime",
        "version": "v1",
        "entry_node": "chat_model",
        "model_profile_id": "mp.chat.v1",
        "node_bindings": [
            {
                "node_id": "chat_model",
                "model_node_id": "chat_model",
                "task": "chat_narrative",
                "require_approval_token": False,
            }
        ],
        "edges": [],
        "fallback_edges": [],
        "model_nodes": {
            "chat_model": {
                "provider": provider_name,
                "model_id": model_id,
                "temperature": 0.4,
                "max_output_tokens": 600,
                "timeout_ms": timeout_ms,
                "api_key": str(api_key or "").strip() or None,
                "provider_fn": provider_fn,
                "available": bool(available),
            }
        },
    }


def _extract_chat_graph_gateway_payload(graph_output: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload_root = dict(graph_output.get("payload") or {})
    chat_node = dict(payload_root.get("chat_model") or {})
    gateway_payload_raw = chat_node.get("payload")
    gateway_payload = dict(gateway_payload_raw or {}) if isinstance(gateway_payload_raw, dict) else {}
    return chat_node, gateway_payload


# ---------------------------------------------------------------------------
# 核心链路入口: POST /v1/chat
# ---------------------------------------------------------------------------
@app.post("/v1/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """Orchestration entry: message -> intent draft -> governor -> response."""
    _metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"

    try:
        # 1) Build intent_draft (minimal intent if request did not provide one)
        intent_draft = request_body.intent_draft
        if intent_draft is None:
            intent_draft = {
                "schema_version": "Natsume.intent.v1",
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

        # 2) Governor decision (intent validation + R1-R7 gates)
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

        retrieval_summary: Dict[str, Any] = {}
        llm_summary: Dict[str, Any] = {}
        if decision == DECISION_APPROVE:
            retrieval_evidence_items: list[dict[str, Any]] = []
            retrieval_enabled = _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True)
            if retrieval_enabled:
                try:
                    retrieval_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "5").strip())
                except ValueError:
                    retrieval_top_k = 5
                retrieval_top_k = max(1, min(retrieval_top_k, 20))
                retrieval_result = get_retrieval_pipeline().run(
                    request_id=request_id,
                    query=request_body.message,
                    intent_id=intent_id,
                    top_k=retrieval_top_k,
                    ts_ms=ts_ms,
                )
                recall_output = dict(retrieval_result.output.get("recall") or {})
                rerank_output = dict(retrieval_result.output.get("rerank") or {})
                evidence_pack = dict(retrieval_result.output.get("evidence_pack") or {})
                rerank_topk_items = list(rerank_output.get("topk") or [])
                for item in rerank_topk_items[:5]:
                    if not isinstance(item, dict):
                        continue
                    snippet = _truncate_for_prompt(item.get("snippet"), limit=240)
                    if not snippet:
                        continue
                    retrieval_evidence_items.append(
                        {
                            "evidence_ref": str(item.get("evidence_ref") or "").strip() or str(item.get("doc_id") or ""),
                            "source_ref": str(item.get("source_ref") or "").strip(),
                            "snippet": snippet,
                            "score": item.get("score"),
                            "confidence": item.get("confidence"),
                        }
                    )
                retrieval_summary = {
                    "status": retrieval_result.status,
                    "reason_code": retrieval_result.reason_code,
                    "route_count": len(recall_output.get("routes") or []),
                    "candidate_count": int(recall_output.get("candidate_count") or 0),
                    "rerank_topk": len(rerank_topk_items),
                    "evidence_refs": list(evidence_pack.get("evidence_refs") or []),
                    "reason_chain": list(retrieval_result.output.get("reason_chain") or []),
                    "degraded": bool(retrieval_result.output.get("degraded", False)),
                }
            else:
                retrieval_summary = {
                    "status": "SKIPPED",
                    "reason_code": "HOST_RETRIEVAL_DISABLED",
                    "route_count": 0,
                    "candidate_count": 0,
                    "rerank_topk": 0,
                    "evidence_refs": [],
                    "reason_chain": ["HOST_RETRIEVAL_DISABLED"],
                    "degraded": True,
                }

            llm_prompt_payload = _build_chat_generation_prompt_payload(
                request_id=request_id,
                session_id=session_id,
                thread_id=request_body.thread_id,
                user_message=request_body.message,
                retrieval_summary=retrieval_summary,
                retrieval_evidence_items=retrieval_evidence_items,
            )

            llm_runtime = _resolve_host_llm_runtime()
            provider_name = str(llm_runtime["provider_name"])
            model_id = str(llm_runtime["model_id"])
            timeout_ms = int(llm_runtime["timeout_ms"])
            llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
            available = bool(llm_runtime["available"])
            provider_fn = llm_runtime["provider_fn"]

            graph_config = _build_chat_graph_config(
                provider_name=provider_name,
                model_id=model_id,
                timeout_ms=timeout_ms,
                available=available,
                api_key=llm_api_key,
                provider_fn=provider_fn,
            )
            dependency_statuses = {
                "chat_model": {
                    "available": available,
                    "detail": "chat model runtime ready" if available else "chat model runtime unavailable",
                },
                provider_name: {
                    "available": available,
                    "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                },
            }

            graph_result = get_graph_runtime().run_graph(
                request_id=request_id,
                graph_config=graph_config,
                payload=llm_prompt_payload,
                dependency_statuses=dependency_statuses,
                ts_ms=ts_ms,
            )

            graph_output = dict(graph_result.output or {})
            chat_node, gateway_payload = _extract_chat_graph_gateway_payload(graph_output)
            gateway_status = str(chat_node.get("gateway_status") or "").strip().upper()
            gateway_reason = str(chat_node.get("reason_code") or "").strip() or None
            narrative = _extract_gateway_narrative(gateway_payload)
            if not narrative:
                narrative = "Generation unavailable, please retry shortly."

            llm_summary = {
                "status": (gateway_status or str(graph_result.status)),
                "reason_code": (gateway_reason or graph_result.reason_code),
                "stage": str(graph_result.stage),
                "provider": (chat_node.get("provider") or provider_name),
                "model_id": (chat_node.get("model_id") or model_id),
                "model_profile_id": graph_output.get("model_profile_id"),
                "latency_ms": None,
                "draft_only": bool(dict(gateway_payload.get("meta") or {}).get("draft_only", True)),
            }

            retrieval_reason_code = str(retrieval_summary.get("reason_code") or "")
            if retrieval_summary.get("degraded") and retrieval_reason_code not in {"", "HOST_RETRIEVAL_DISABLED"}:
                reason_code = reason_code or retrieval_reason_code
            graph_ok = str(graph_result.status).upper() == "OK"
            gateway_ok = gateway_status == "OK"
            if not graph_ok or not gateway_ok:
                reason_code = reason_code or gateway_reason or str(graph_result.reason_code or "") or "LLM_UNAVAILABLE"
                quality = {"stage": str(graph_result.stage), "reason_code": reason_code}
            else:
                quality = {"stage": "S_COMPLETE", "reason_code": None}
        else:
            narrative = f"[REJECTED] reason: {reason_code or 'UNKNOWN'}"
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
                "retrieval": retrieval_summary,
                "llm": llm_summary,
            },
            timestamp=ts_ms,
        )

    except Exception as exc:
        _metrics.inc_error()
        _logger.error("Chat request failed", extra={"request_id": request_id, "error": str(exc)})
        return ChatResponse(
            request_id=request_id,
            narrative="[DEGRADED] Internal error while processing request.",
            intent_draft=None,
            decision="REJECT",
            reason_code="INTERNAL_SERVER_ERROR",
            approval_token_ref=None,
            quality={"stage": "S_INTERNAL_ERROR", "reason_code": "INTERNAL_SERVER_ERROR"},
            audit_refs={"request_id": request_id},
            timestamp=ts_ms,
        )


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
# Web UI 面板入口（M17: 已升级为 React SPA，旧 panel.html 已移除）
# ---------------------------------------------------------------------------
@app.get("/panel")
async def panel_page() -> "RedirectResponse":
    """监控面板入口 — 重定向到 M17 React SPA"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/monitor/index.html", status_code=301)


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
