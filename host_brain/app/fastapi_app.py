"""FastAPI web application entry for Natsume AI host brain.

TASK-0065: HTTP 鏈嶅姟妗嗘灦钀藉湴 鈥?FastAPI 璺敱缁戝畾 + 鍋ュ悍妫€鏌?+ 閾捐矾鍏ュ彛銆?
鎻愪緵 POST /v1/chat锛堢紪鎺掑叆鍙ｏ級銆丟ET /health銆丟ET /ready銆丟ET /metrics锛?
鎵€鏈夋墽琛屼粛缁?Governor/Token/Skills锛屼笉寮曞叆鏃佽矾銆?
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Mapping, Optional

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
from host_brain.core.context_pack import (
    build_emotion_pulse_slot,
    build_world_canon_slot,
    validate_context_pack,
)
from host_brain.core.ids import new_request_id
from host_brain.core.metabolism import (
    build_governance_snapshot,
    build_hard_snapshot,
    normalize_hard_state,
)
from host_brain.core.qa import check_wcanon_slot_violation
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.canon_sync import detect_world_version_drift
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.episodic import EpisodicStore
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
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.registry import SkillRegistry
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.integrations.llm_gateway import (
    PROVIDER_DEEPSEEK,
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)


# HTTP contract models
class HealthResponse(BaseModel):
    status: str = Field(..., description="Service status")
    version: str = Field(..., description="Application version")
    timestamp: int = Field(..., description="Response timestamp in ms")
    details: Dict[str, Any] = Field(default_factory=dict, description="Health details")


class ReadinessResponse(BaseModel):
    ready: bool = Field(..., description="Whether the service is ready")
    status: str = Field(..., description="Readiness status")
    reason_code: Optional[str] = Field(None, description="Readiness reason code")
    timestamp: int = Field(..., description="Response timestamp in ms")
    summary: Dict[str, Any] = Field(default_factory=dict, description="Readiness summary")


class ObservabilityRequest(BaseModel):
    request_id: str = Field(..., description="Request id to inspect")


class ObservabilityResponse(BaseModel):
    request_id: str = Field(..., description="Request id")
    trace_status: str = Field(..., description="Trace status")
    timeline_total: int = Field(..., description="Timeline event count")
    sources: Dict[str, int] = Field(..., description="Timeline sources")
    timeline: List[Dict[str, Any]] = Field(..., description="Timeline rows")
    timestamp: int = Field(..., description="Response timestamp in ms")


class ChatRequest(BaseModel):
    """POST /v1/chat request body."""
    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(None, description="Optional session id")
    thread_id: Optional[str] = Field(None, description="Optional thread id")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Optional intent draft")
    scene_type: Optional[str] = Field(None, description="Optional scene type override")
    mood_state: Optional[str] = Field(None, description="Optional mood state")
    emotion_tag: Optional[str] = Field(None, description="Optional emotion tag")
    emotion_context: Optional[Dict[str, Any]] = Field(None, description="Optional emotion context")
    emotion_range_hint: Optional[str] = Field(None, description="Optional emotion range hint")


class ChatResponse(BaseModel):
    """POST /v1/chat response body."""
    request_id: str = Field(..., description="Request id")
    narrative: Optional[str] = Field(None, description="Narrative response")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Intent draft")
    decision: Optional[str] = Field(None, description="Governor decision")
    reason_code: Optional[str] = Field(None, description="Reason code")
    approval_token_ref: Optional[str] = Field(None, description="Approval token reference")
    approval_tier: Optional[str] = Field(None, description="Approval tier")
    quality: Dict[str, Any] = Field(default_factory=dict, description="Quality summary")
    audit_refs: Dict[str, Any] = Field(default_factory=dict, description="Audit references")
    timestamp: int = Field(..., description="Response timestamp in ms")


class MetricsResponse(BaseModel):
    """GET /metrics response body."""
    uptime_seconds: int = Field(..., description="Service uptime in seconds")
    total_requests: int = Field(..., description="Total request count")
    total_chat_requests: int = Field(..., description="Total chat request count")
    total_rejections: int = Field(..., description="Total rejection count")
    total_errors: int = Field(..., description="Total error count")
    reason_code_distribution: Dict[str, int] = Field(default_factory=dict, description="Reason code distribution")
    timestamp: int = Field(..., description="Response timestamp in ms")


class EdgeEventsRequest(BaseModel):
    """POST /v1/edge/events request body."""
    events: List[Dict[str, Any]] = Field(..., description="Edge event list")


class EdgeEventsResponse(BaseModel):
    """POST /v1/edge/events response body."""
    success: bool = Field(..., description="Whether all events were accepted")
    total: int = Field(..., description="Total event count")
    accepted: int = Field(..., description="Accepted event count")
    rejected: int = Field(..., description="Rejected event count")
    reason_code: Optional[str] = Field(None, description="Failure reason code")
    detail: Optional[str] = Field(None, description="Failure detail")
    rejected_indices: List[int] = Field(default_factory=list, description="Rejected event indices")
    timestamp: int = Field(..., description="Response timestamp in ms")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error type")
    reason_code: str = Field(..., description="Reason code")
    detail: str = Field(..., description="Error detail")
    request_id: Optional[str] = Field(None, description="Related request id")
    timestamp: int = Field(..., description="Error timestamp in ms")


# ---------------------------------------------------------------------------
# 杩愯鏃舵寚鏍囨敹闆嗗櫒锛堝唴瀛橈紝鐢ㄤ簬 /metrics 绔偣锛?
# ---------------------------------------------------------------------------
class _RuntimeMetrics:
    """杩涚▼鍐呰繍琛屾椂鎸囨爣鏀堕泦锛堣交閲忕骇鍐呭瓨瀹炵幇锛夈€?

    娉ㄦ剰锛氳鏁板櫒渚濊禆 CPython GIL 淇濊瘉鍗曡繘绋嬪唴绾跨▼瀹夊叏锛?
    浣嗗 worker 妯″紡涓嬪悇 worker 鐙珛璁℃暟锛堟寚鏍囦负 per-worker 瑙嗗浘锛夈€?
    濡傞渶鍏ㄥ眬鎸囨爣锛岃鏀圭敤澶栭儴瀛樺偍锛圧edis/Prometheus锛夈€?
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
# 鐢熷懡鍛ㄦ湡閽╁瓙
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown 鐢熷懡鍛ㄦ湡閽╁瓙銆?

    Startup: 鍒濆鍖?Ledger銆佹墽琛屽惎鍔ㄦ帰閽堛€佸啓鍏ュ璁°€?
    Shutdown: 鍒锋柊瀹¤缂撳啿銆?
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

    yield  # 鏈嶅姟杩愯闃舵

    _logger.info("Natsume AI Host Brain shutting down ...")
    # 鍒锋柊瀹¤缂撳啿锛堝綋鍓?SQLite 鍚屾鍐欏叆锛屾澶勯鐣欐墿灞曠偣锛?


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
# 娉ㄥ唽 Web UI API 璺敱锛圱ASK-0066: 閾捐矾鍙鍖栭潰鏉垮悗绔級
# ---------------------------------------------------------------------------
app.include_router(web_ui_router, prefix="/api")

# ---------------------------------------------------------------------------
# 娉ㄥ唽 Canon 娌荤悊 API 璺敱锛圡10-A TASK-0118: Canon 鎻愭瀹℃牳 REST API锛?
# ---------------------------------------------------------------------------
app.include_router(canon_router)

# ---------------------------------------------------------------------------
# 娉ㄥ唽 Scheduler REST API 璺敱锛圡11-A TASK-0149: 瀹氭椂璋冨害鍣ㄤ换鍔＄鐞嗭級
# ---------------------------------------------------------------------------
app.include_router(scheduler_router)

# ---------------------------------------------------------------------------
# 娉ㄥ唽 ToolBroker REST API 璺敱锛圡12-A TASK-0156: 宸ュ叿鍏冩暟鎹寘 + 鎵ц鍏ュ彛锛?
# ---------------------------------------------------------------------------
app.include_router(tool_router)

# ---------------------------------------------------------------------------
# 娉ㄥ唽 Bond 闃舵 REST API 璺敱锛圡13-A: Bond 闃舵绉垎 + 鑷姩鏅嬬骇锛?
# ---------------------------------------------------------------------------
app.include_router(bond_router)

# ---------------------------------------------------------------------------
# 娉ㄥ唽 Observability REST API 璺敱锛圡18: 鐩戞帶闈㈡澘鍚庣鏁版嵁鎺ュ彛锛?
# ---------------------------------------------------------------------------
app.include_router(obs_router)

# ---------------------------------------------------------------------------
# 鎸傝浇闈欐€佹枃浠剁洰褰曪紙TASK-0066: 鍓嶇闈㈡澘椤甸潰锛?
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
# CORS 涓棿浠讹紙鍙厤缃級
# ---------------------------------------------------------------------------
# CORS 榛樿鍏抽棴锛氱敓浜х幆澧冨繀椤绘樉寮忚缃?Natsume_CORS_ORIGINS
# 渚嬪 Natsume_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:8000"
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
# 鍏ㄥ眬渚濊禆
# ---------------------------------------------------------------------------
_ledger: Optional[SQLiteLedger] = None
_intent_engine: Optional[IntentDecisionEngine] = None
_skill_registry: Optional[SkillRegistry] = None  # M44锛氬唴缃?Skill 娉ㄥ唽琛ㄥ崟渚?
_policy_cache: Optional[GovernorPolicyCache] = None  # M44锛氬垎灞傛不鐞?Policy Cache 鍗曚緥
_tool_catalog: Optional[ToolCatalog] = None
_tool_broker: Optional[ToolBroker] = None
_bond_stage_engine: Optional[BondStageEngine] = None
_episodic_store: Optional[EpisodicStore] = None
_retrieval_pipeline: Optional[RetrievalPipelineContract] = None
_obs_metrics_collector: Optional[MetricsCollector] = None
_graph_runtime: Optional[GraphRuntimeContract] = None
_logger = logging.getLogger("Natsume.fastapi")

# M33-F锛欳HAT_GRAPH_NODE_ORDER 淇濈暀涓?鍏ㄨ妭鐐规敞鍐岃〃"锛岀敤浜?model_node_overrides 鍒濆鍖栥€?
# 涓嶅啀鐢ㄤ簬 node_bindings/edges 鐨勬瀯寤衡€斺€旀敼鐢?_resolve_scene_node_sequence(scene_type)銆?
CHAT_DIRECT_NODE_ID = "chat_direct_ja"
CHAT_GRAPH_NODE_ORDER: list[str] = [
    CHAT_DIRECT_NODE_ID,
    "core_reasoner_primary",
    "core_reasoner_secondary",
    "planner_constraints",
    "tool_planner",
    "safety_verifier",
    "consistency_verifier",
    "renderer_ja_c_style",
    "workbench_translate",
    "workbench_code_assist",
]
CHAT_RENDER_NODE_ID = "renderer_ja_c_style"
CHAT_INTENT_SOURCE_NODE_ID = "core_reasoner_primary"

# M33-A锛氬満鏅灇涓惧父閲忥紙ModelPlane.md 搂4.1锛?
SCENE_CHAT = "CHAT"
SCENE_EXEC_INTENT = "EXEC_INTENT"
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
SCENE_WORK_CODE = "WORK_CODE"
# M34-A锛氭柊澧?VISION_UI 鍦烘櫙甯搁噺
SCENE_VISION_UI = "VISION_UI"

# M34-A锛歱re_intent_classifier 鍏抽敭璇嶈鍒欒〃锛堜紭鍏堢骇浠庨珮鍒颁綆锛?
# 瑙勫垯锛氱函瑙勫垯椹卞姩锛屾棤 LLM 璋冪敤锛屽欢杩?< 1ms
# 浼樺厛绾э細VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT锛堥粯璁わ級
_PIC_VISION_UI_KEYWORDS = frozenset([
    "screenshot", "screen", "ui", "window", "interface", "capture",
    "screen shot", "desktop", "look at the screen",
    "截图", "屏幕", "界面", "画面", "看一下屏幕", "看看这个界面",
])
_PIC_WORK_CODE_KEYWORDS = frozenset([
    "code", "coding", "programming", "function", "script", "program",
    "python", "javascript", "typescript", "java", "golang", "rust", "c++",
    "debug", "bug", "class", "algorithm", "implement", "develop",
    "代码", "编程", "写代码", "写个函数", "函数", "脚本", "调试", "类", "实现", "开发",
])
_PIC_WORK_TRANSLATE_KEYWORDS = frozenset([
    "translate", "translation", "translate to", "translate into",
    "翻译", "翻成", "译成", "译为", "翻訳", "訳して",
])
_PIC_EXEC_INTENT_KEYWORDS = frozenset([
    "execute", "run", "setup", "configure", "start", "stop", "install",
    "执行", "运行", "设置", "配置", "启动", "关闭", "打开",
    "帮我做", "部署", "创建", "新建", "修改", "更新", "安装",
    "deploy", "build", "submit", "save",
])


def pre_intent_classifier(user_input: str) -> str:
    """杞婚噺瑙勫垯棰勫垎绫诲櫒锛圡34-A锛孧odelPlane.md 搂4.0锛夈€?

    鍦?LLM 璋冪敤鍓嶏紝鍩轰簬鍏抽敭璇?瑙勫垯蹇€熷垽鏂?scene_type_hint銆?
    绾鍒欓┍鍔紝鏃?LLM 璋冪敤锛屽欢杩?< 1ms銆?

    浼樺厛绾э紙浠庨珮鍒颁綆锛夛細
    1. VISION_UI锛氭埅鍥?鐣岄潰/灞忓箷绛?
    2. WORK_CODE锛氫唬鐮?缂栫▼/鍑芥暟绛?
    3. WORK_TRANSLATE锛氱炕璇?translate/瑷炽仐銇︾瓑
    4. EXEC_INTENT锛氭墽琛?杩愯/璁剧疆/閰嶇疆绛?
    5. CHAT锛堥粯璁わ級锛氬叾浣欐墍鏈夎緭鍏?

    娉ㄦ剰锛?
    - 姝ゅ嚱鏁板彧褰卞搷鑺傜偣閾捐矾閫夋嫨锛屼笉褰卞搷 Governor 瑁佸喅鏉?
    - 鏄惧紡浼犲叆鐨?scene_type 浼樺厛绾ч珮浜庨鍒嗙被缁撴灉
    - 绌哄瓧绗︿覆鎴栨棤娉曡瘑鍒殑杈撳叆榛樿杩斿洖 CHAT

    Args:
        user_input: 鐢ㄦ埛鍘熷杈撳叆鏂囨湰

    Returns:
        scene_type_hint: CHAT / EXEC_INTENT / WORK_TRANSLATE / WORK_CODE / VISION_UI
    """
    text = str(user_input or "").strip().lower()
    if not text:
        return SCENE_CHAT

    # 浼樺厛绾?1锛歏ISION_UI
    for kw in _PIC_VISION_UI_KEYWORDS:
        if kw in text:
            return SCENE_VISION_UI

    # 浼樺厛绾?2锛歐ORK_CODE
    for kw in _PIC_WORK_CODE_KEYWORDS:
        if kw in text:
            return SCENE_WORK_CODE

    # 浼樺厛绾?3锛歐ORK_TRANSLATE
    for kw in _PIC_WORK_TRANSLATE_KEYWORDS:
        if kw in text:
            return SCENE_WORK_TRANSLATE

    # 浼樺厛绾?4锛欵XEC_INTENT
    for kw in _PIC_EXEC_INTENT_KEYWORDS:
        if kw in text:
            return SCENE_EXEC_INTENT

    # 榛樿锛欳HAT
    return SCENE_CHAT


def _resolve_scene_node_sequence(scene_type: str) -> list[str]:
    """鎸夊満鏅繑鍥炲簲鎵ц鐨勮妭鐐瑰簭鍒楋紙M33-A锛孧odelPlane.md 搂4.1/4.2锛夈€?

    瑙勫垯锛?
    - core_reasoner_secondary 鏄?primary 鐨?fallback-only锛屼笉鍦ㄦ甯搁摼璺腑銆?
    - safety_verifier / consistency_verifier 浠呭湪 EXEC_INTENT 鍦烘櫙鎸夐渶瑙﹀彂銆?
    - planner_constraints / tool_planner 浠呭湪 EXEC_INTENT 鍦烘櫙瑙﹀彂銆?
    - workbench_translate 浠呭湪 WORK_TRANSLATE 鍦烘櫙瑙﹀彂銆?
    - workbench_code_assist 浠呭湪 WORK_CODE 鍦烘櫙瑙﹀彂銆?
    - renderer_ja_c_style 姣忔蹇呰窇锛圫2 娈碉級銆?
    - 鏈煡鍦烘櫙閫€鍖栦负 CHAT 搴忓垪銆?
    """
    scene = str(scene_type or SCENE_CHAT).strip().upper()
    if scene == SCENE_EXEC_INTENT:
        return [
            "core_reasoner_primary",
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == SCENE_WORK_TRANSLATE:
        return [
            "core_reasoner_primary",
            "workbench_translate",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == SCENE_WORK_CODE:
        return [
            "core_reasoner_primary",
            "workbench_code_assist",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    # CHAT fast path: single-node direct reply
    return [
        CHAT_DIRECT_NODE_ID,
    ]


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


def _contains_kana(text: str) -> bool:
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x3040 <= cp <= 0x30FF:  # Hiragana/Katakana
            return True
    return False


def _contains_cjk_ideograph(text: str) -> bool:
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:  # CJK unified ideographs
            return True
    return False


def _contains_cjk_or_kana(text: str) -> bool:
    return _contains_cjk_ideograph(text) or _contains_kana(text)


def _infer_render_target_lang(*, user_message: str, core_narrative: str = "") -> str:
    """ModelPlane 搂0.4: 浜屾寮忚緭鍑哄己鍒舵棩鏂囨覆鏌撱€?

    renderer_ja_c_style 鍥哄畾杈撳嚭鏃ヨ锛坮endered_ja锛夛紝
    浠呭湪 HOST_RENDER_TARGET_LANG 鏄惧紡瑕嗙洊鏃跺厑璁稿垏鎹€?
    """
    override = str(os.environ.get("HOST_RENDER_TARGET_LANG") or "").strip().lower()
    if override in {"zh", "ja"}:
        return override
    return "ja"


def _repair_utf8_latin1_mojibake(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return raw
    if _contains_cjk_or_kana(raw):
        return raw
    try:
        repaired = raw.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return raw
    if _contains_cjk_or_kana(repaired):
        return repaired
    return raw


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
        "You are the Natsume core reasoning node.\n"
        "Return exactly one JSON object with top-level keys: narrative, intent_draft, meta.\n"
        "Required schema:\n"
        "{\"narrative\":{\"text\":\"...\"},\"intent_draft\":{},\"meta\":{\"draft_only\":true}}\n"
        "Rules:\n"
        "1. Output JSON only. No markdown. No code fences.\n"
        "2. Keep draft_only=true.\n"
        "3. Do not claim execution/saving/confirmation has happened.\n"
        "4. Reply in Simplified Chinese and keep it concise.\n"
        "5. Ground the output in user message and evidence only.\n\n"
        f"User message:\n{user_message}\n\n"
        f"Retrieval status: {retrieval_status}\n"
        f"Retrieval reason_code: {retrieval_reason}\n"
        f"Evidence:\n{evidence_block}\n"
    )


def _extract_node_runtime_payload(runtime_payload: Mapping[str, Any], *, node_id: str) -> dict[str, Any]:
    node_root = runtime_payload.get(node_id)
    if not isinstance(node_root, Mapping):
        return {}
    payload_raw = node_root.get("payload")
    if isinstance(payload_raw, Mapping):
        return dict(payload_raw)
    return {}


def _extract_node_narrative_text(runtime_payload: Mapping[str, Any], *, node_id: str) -> str:
    node_payload = _extract_node_runtime_payload(runtime_payload, node_id=node_id)
    return _extract_gateway_narrative(node_payload)


def _render_node_generation_prompt(*, node_id: str, runtime_payload: Mapping[str, Any]) -> str:
    user_message = _truncate_for_prompt(runtime_payload.get("user_message"), limit=1000)
    retrieval_raw = runtime_payload.get("retrieval")
    retrieval = dict(retrieval_raw) if isinstance(retrieval_raw, Mapping) else {}
    retrieval_status = str(retrieval.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    retrieval_reason = str(retrieval.get("reason_code") or "").strip() or "NONE"
    evidence_items = retrieval.get("evidence_items")
    rows = evidence_items if isinstance(evidence_items, list) else []
    evidence_lines: list[str] = []
    for row in rows[:6]:
        if not isinstance(row, Mapping):
            continue
        evidence_ref = _truncate_for_prompt(row.get("evidence_ref") or row.get("source_ref") or "unknown", limit=80)
        snippet = _truncate_for_prompt(row.get("snippet"), limit=220)
        if not snippet:
            continue
        evidence_lines.append(f"- [{evidence_ref}] {snippet}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "- (none)"

    core_payload = _extract_node_runtime_payload(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_narrative = _extract_node_narrative_text(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_meta = core_payload.get("meta")
    primary_degraded = isinstance(core_meta, dict) and core_meta.get("degraded")
    if primary_degraded:
        secondary_narrative = _extract_node_narrative_text(runtime_payload, node_id="core_reasoner_secondary")
        if secondary_narrative:
            core_narrative = secondary_narrative
            secondary_payload = _extract_node_runtime_payload(runtime_payload, node_id="core_reasoner_secondary")
            if isinstance(secondary_payload.get("intent_draft"), dict) and secondary_payload["intent_draft"]:
                core_payload = secondary_payload
    core_intent_draft = core_payload.get("intent_draft")
    if not isinstance(core_intent_draft, dict):
        core_intent_draft = {}
    core_intent_json = json.dumps(core_intent_draft, ensure_ascii=False, sort_keys=True)
    planner_text = _extract_node_narrative_text(runtime_payload, node_id="planner_constraints")
    verifier_text = _extract_node_narrative_text(runtime_payload, node_id="safety_verifier")

    render_target_lang = _infer_render_target_lang(
        user_message=user_message,
        core_narrative=core_narrative,
    )

    renderer_role_instruction = (
        "Role: renderer_ja_c_style (final Japanese renderer for 澶忕洰钘?.\n"
        "You ARE 澶忕洰钘? Render CORE_NARRATIVE_ZH into Japanese in her voice.\n"
        "Force output narrative.text in Japanese (short, restrained C-style tone).\n"
        "Use 銇犮仦/銇犮仾/銇?銈?銇?sentence-enders sparingly (max one per sentence).\n"
        "Do not add facts. Do not change intent_draft meaning.\n"
        "NEVER say you are an AI, assistant, system, or program.\n"
        "Use CORE_NARRATIVE_ZH as source and copy CORE_INTENT_DRAFT semantics."
    )

    node_role_instructions: dict[str, str] = {
        "core_reasoner_primary": (
            "Role: core_reasoner_primary.\n"
            "You ARE 澶忕洰钘?(Natsume Ai). Think and draft as her.\n"
            "Output concise Simplified Chinese semantic draft for CHAT.\n"
            "Use her tone: gentle, restrained, empathetic. Acknowledge emotions first.\n"
            "NEVER say you are an AI, assistant, system, or program.\n"
            "intent_draft should stay draft-only and must not claim execution."
        ),
        "core_reasoner_secondary": (
            "Role: core_reasoner_secondary backup.\n"
            "You ARE 澶忕洰钘? Output a shorter fallback core draft in Simplified Chinese.\n"
            "NEVER say you are an AI, assistant, system, or program.\n"
            "Do not conflict with core_reasoner_primary intent scope."
        ),
        "planner_constraints": (
            "Role: planner_constraints.\n"
            "Output compact constraint plan in narrative.text (Chinese), no long prose.\n"
            "intent_draft can stay empty if no tool decision is required."
        ),
        "tool_planner": (
            "Role: tool_planner.\n"
            "If tools may help, propose only draft tool plan in intent_draft (no execution)."
        ),
        "safety_verifier": (
            "Role: safety_verifier.\n"
            "Check false execution claim / promise escalation / evidence escalation.\n"
            "Write PASS|FAIL verdict and key issue in narrative.text."
        ),
        "consistency_verifier": (
            "Role: consistency_verifier.\n"
            "Check canon wording consistency and format drift risk.\n"
            "Write PASS|FAIL verdict and key issue in narrative.text."
        ),
        "workbench_translate": (
            "Role: workbench_translate.\n"
            "Provide translation-assist draft focusing terminology consistency."
        ),
        "workbench_code_assist": (
            "Role: workbench_code_assist.\n"
            "Provide code-assist draft if relevant; otherwise concise no-op draft."
        ),
        "renderer_ja_c_style": renderer_role_instruction,
    }
    role_instruction = node_role_instructions.get(
        node_id,
        "Role: generic node. Keep concise draft output.",
    )

    style_bindings_raw = runtime_payload.get("style_bindings")
    style_bindings = (
        dict(style_bindings_raw)
        if isinstance(style_bindings_raw, Mapping)
        else {}
    )
    node_style_raw = style_bindings.get(node_id)
    node_style = dict(node_style_raw) if isinstance(node_style_raw, Mapping) else {}
    style_instruction = _truncate_for_prompt(node_style.get("style_instruction"), limit=1200)
    style_block = ""
    if style_instruction:
        style_block = (
            "STYLE_CANON_BINDING:\n"
            f"- canon_id: {_truncate_for_prompt(node_style.get('canon_id'), limit=64) or 'unknown'}\n"
            f"- citation: {_truncate_for_prompt(node_style.get('citation'), limit=96) or 'unknown'}\n"
            f"- tone_only: {bool(node_style.get('tone_only', True))}\n"
            f"- unsafe_as_fact: {bool(node_style.get('unsafe_as_fact', True))}\n"
            f"- instruction:\n{style_instruction}\n\n"
        )

    cn_style_boost_block = ""
    if node_id == CHAT_RENDER_NODE_ID and render_target_lang == "zh":
        cn_style_boost_block = (
            "CN_STYLE_BOOST_BINDING:\n"
            "- style_strength: HIGH_PLUS\n"
            "- Priority: safety/facts > style canon > generic assistant tone.\n"
            "- Spoken Chinese only: avoid official-service wording and system bulletin cadence.\n"
            "- Rhythm: 2-4 short sentences; keep each sentence compact, with natural pause.\n"
            "- Emotional anchor first: acknowledge feeling before suggestions.\n"
            "- Use first/second person directly (鎴?浣? to keep intimacy.\n"
            "- Use one mild particle per sentence at most; do not stack particles.\n"
            "- Avoid boilerplate templates unless truly degraded.\n\n"
        )

    emotion_raw = runtime_payload.get("emotion_context")
    emotion_block = ""
    if isinstance(emotion_raw, dict) and emotion_raw:
        mood = str(emotion_raw.get("mood_state") or "").strip()
        etag = str(emotion_raw.get("emotion_tag") or "").strip()
        valence = emotion_raw.get("valence")
        arousal = emotion_raw.get("arousal")
        parts = []
        if mood:
            parts.append(f"mood_state: {mood}")
        if etag:
            parts.append(f"emotion_tag: {etag}")
        if valence is not None:
            parts.append(f"valence: {valence:.2f}")
        if arousal is not None:
            parts.append(f"arousal: {arousal:.2f}")
        if parts:
            emotion_block = "EMOTION_CONTEXT:\n" + "\n".join(f"- {p}" for p in parts) + "\n\n"

    return (
        "Return exactly one JSON object with keys: narrative, intent_draft, meta.\n"
        "Schema:\n"
        "{\"narrative\":{\"text\":\"...\"},\"intent_draft\":{},\"meta\":{\"draft_only\":true}}\n"
        "Hard rules:\n"
        "1) JSON only; no markdown/code fence.\n"
        "2) meta.draft_only must be true.\n"
        "3) Never claim executed/saved/confirmed/completed.\n"
        "4) Keep concise and grounded in provided context.\n"
        f"{role_instruction}\n\n"
        f"{style_block}"
        f"{cn_style_boost_block}"
        f"{emotion_block}"
        f"NODE_ID: {node_id}\n"
        f"RENDER_TARGET_LANG: {render_target_lang}\n"
        f"USER_MESSAGE:\n{user_message}\n\n"
        f"RETRIEVAL_STATUS: {retrieval_status}\n"
        f"RETRIEVAL_REASON_CODE: {retrieval_reason}\n"
        f"EVIDENCE:\n{evidence_block}\n\n"
        f"CORE_NARRATIVE_ZH:\n{core_narrative}\n\n"
        f"PLANNER_HINT:\n{planner_text}\n\n"
        f"SAFETY_HINT:\n{verifier_text}\n\n"
        f"CORE_INTENT_DRAFT_JSON:\n{core_intent_json}\n"
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
    narrative_text = _repair_utf8_latin1_mojibake(narrative_text)
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
    node_id: str | None = None,
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
    resolved_node_id = str(node_id or "").strip()
    if resolved_node_id:
        system_prompt = (
            f"You are 澶忕洰钘?(Natsume Ai), processing node `{resolved_node_id}`. "
            "You are NOT an AI or assistant 鈥?you are 澶忕洰钘?herself. "
            "Output exactly one JSON object with keys narrative, intent_draft, meta. "
            "meta.draft_only must be true."
        )
    else:
        system_prompt = (
            "You are 澶忕洰钘?(Natsume Ai). "
            "You are NOT an AI or assistant 鈥?you are 澶忕洰钘?herself. "
            "Output exactly one JSON object with keys narrative, intent_draft, meta. "
            "meta.draft_only must be true."
        )

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        if resolved_node_id:
            prompt_text = _render_node_generation_prompt(node_id=resolved_node_id, runtime_payload=runtime_payload)
        else:
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
    return _build_host_mock_provider_for_node(None)


def _build_host_mock_provider_for_node(node_id: str | None) -> Callable[[dict[str, Any]], Any]:
    resolved_node_id = str(node_id or "").strip()

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        user_message = _truncate_for_prompt(runtime_payload.get("user_message"), limit=120) or "hello"
        if resolved_node_id in {CHAT_RENDER_NODE_ID, CHAT_DIRECT_NODE_ID}:
            text = f"Draft render reply for: {user_message}"
        elif resolved_node_id in {"safety_verifier", "consistency_verifier"}:
            text = "PASS: draft output is within policy boundary."
        else:
            text = f"Draft response for: {user_message}"
        return json.dumps(
            {
                "narrative": {"text": text},
                "intent_draft": {},
                "meta": {
                    "draft_only": True,
                    "source": "host_mock_provider",
                    "node_id": (resolved_node_id or "chat_model"),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return _provider


def _build_host_mock_provider_for_node(node_id: str | None) -> Callable[[dict[str, Any]], Any]:
    resolved_node_id = str(node_id or "").strip()

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        user_message = _truncate_for_prompt(runtime_payload.get("user_message"), limit=120) or "浣犲ソ"
        if resolved_node_id in {CHAT_RENDER_NODE_ID, CHAT_DIRECT_NODE_ID}:
            render_target_lang = str(runtime_payload.get("render_target_lang") or "").strip().lower()
            if render_target_lang not in {"zh", "ja"}:
                render_target_lang = _infer_render_target_lang(user_message=user_message)
            if render_target_lang == "zh":
                text = f"收到，先给你一个中文草稿：{user_message}"
            else:
                text = f"了解しました。まず草案を出します: {user_message}"
        elif resolved_node_id in {"safety_verifier", "consistency_verifier"}:
            text = "PASS: draft output is within policy boundary."
        else:
            text = f"收到，先给你一个草稿：{user_message}"
        return json.dumps(
            {
                "narrative": {"text": text},
                "intent_draft": {},
                "meta": {
                    "draft_only": True,
                    "source": "host_mock_provider",
                    "node_id": (resolved_node_id or "chat_model"),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return _provider


_GEMINI_PRIMARY_RETRY_NODE_ID = "core_reasoner_primary"
_GEMINI_PRIMARY_RETRY_MODEL_IDS = frozenset({"gemini-2.5-pro"})
_GEMINI_PRIMARY_FALLBACK_MODEL_ID = "gemini-3.1-pro-preview"
_GEMINI_PRIMARY_RETRYABLE_MARKERS = (
    "503",
    "deadline exceeded",
    "service unavailable",
    "service_unavailable",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "unavailable",
)
_GEMINI_PRIMARY_NON_RETRYABLE_MARKERS = (
    "401",
    "403",
    "api key",
    "api_key",
    "authentication",
    "billing",
    "permission",
    "quota",
    "resource_exhausted",
    "unauthorized",
)
_GEMINI_PRIMARY_FALLBACK_BLOCKED_MARKERS = (
    "401",
    "403",
    "api key",
    "api_key",
    "authentication",
    "permission",
    "unauthorized",
)
_GEMINI_PRIMARY_FALLBACK_TRIGGER_MARKERS = (
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    * _GEMINI_PRIMARY_RETRYABLE_MARKERS,
)


def _should_retry_node_provider_failure(
    *,
    provider_name: str,
    model_id: str,
    node_id: str,
    exc: BaseException,
) -> bool:
    provider = str(provider_name or "").strip().lower()
    if provider != PROVIDER_GEMINI:
        return False
    if str(node_id or "").strip() != _GEMINI_PRIMARY_RETRY_NODE_ID:
        return False
    if str(model_id or "").strip() not in _GEMINI_PRIMARY_RETRY_MODEL_IDS:
        return False
    if isinstance(exc, TimeoutError):
        return True
    detail = str(exc or "").strip().lower()
    if not detail:
        return False
    if any(marker in detail for marker in _GEMINI_PRIMARY_NON_RETRYABLE_MARKERS):
        return False
    return any(marker in detail for marker in _GEMINI_PRIMARY_RETRYABLE_MARKERS)


def _resolve_gemini_primary_fallback_model_id(primary_model_id: str) -> str | None:
    disable_fallback = str(os.environ.get("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK") or "").strip().lower()
    if disable_fallback in {"1", "true", "yes", "on"}:
        return None
    fallback_model_id = str(
        os.environ.get("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID") or _GEMINI_PRIMARY_FALLBACK_MODEL_ID
    ).strip()
    if not fallback_model_id:
        return None
    if fallback_model_id == str(primary_model_id or "").strip():
        return None
    return fallback_model_id


def _should_try_gemini_primary_model_fallback(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    detail = str(exc or "").strip().lower()
    if not detail:
        return False
    if any(marker in detail for marker in _GEMINI_PRIMARY_FALLBACK_BLOCKED_MARKERS):
        return False
    return any(marker in detail for marker in _GEMINI_PRIMARY_FALLBACK_TRIGGER_MARKERS)


def _wrap_node_provider_fn_with_retry(
    *,
    provider_fn: Callable[[dict[str, Any]], Any],
    provider_name: str,
    model_id: str,
    node_id: str,
    fallback_provider_fn: Callable[[dict[str, Any]], Any] | None = None,
    fallback_model_id: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    resolved_node_id = str(node_id or "").strip()
    resolved_model_id = str(model_id or "").strip()
    if (
        provider != PROVIDER_GEMINI
        or resolved_node_id != _GEMINI_PRIMARY_RETRY_NODE_ID
        or resolved_model_id not in _GEMINI_PRIMARY_RETRY_MODEL_IDS
    ):
        return provider_fn

    try:
        configured_attempts = int(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS") or "2").strip())
    except ValueError:
        configured_attempts = 2
    max_attempts = max(1, min(configured_attempts, 3))
    if max_attempts <= 1 and not callable(fallback_provider_fn):
        return provider_fn

    try:
        configured_delay_ms = float(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS") or "150").strip())
    except ValueError:
        configured_delay_ms = 150.0
    retry_delay_s = max(0.0, min(configured_delay_ms / 1000.0, 2.0))

    def _provider(prompt_payload: dict[str, Any]) -> Any:
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return provider_fn(prompt_payload)
            except Exception as exc:
                last_exc = exc
                should_retry = _should_retry_node_provider_failure(
                    provider_name=provider,
                    model_id=resolved_model_id,
                    node_id=resolved_node_id,
                    exc=exc,
                )
                should_try_fallback = (
                    callable(fallback_provider_fn)
                    and _should_try_gemini_primary_model_fallback(exc)
                )
                if not should_retry or attempt >= max_attempts:
                    if should_try_fallback and fallback_provider_fn is not None:
                        resolved_fallback_model = str(fallback_model_id or "").strip() or _GEMINI_PRIMARY_FALLBACK_MODEL_ID
                        _logger.warning(
                            "Downgrading node provider call for %s from %s to %s after failure: %s",
                            resolved_node_id,
                            resolved_model_id,
                            resolved_fallback_model,
                            exc,
                        )
                        try:
                            return fallback_provider_fn(prompt_payload)
                        except Exception as fallback_exc:
                            raise RuntimeError(
                                f"gemini primary model failed: {exc}; fallback model {resolved_fallback_model} failed: {fallback_exc}"
                            ) from fallback_exc
                    raise
                _logger.warning(
                    "Retrying node provider call for %s/%s after transient Gemini failure (%d/%d): %s",
                    resolved_node_id,
                    resolved_model_id,
                    attempt,
                    max_attempts,
                    exc,
                )
                if retry_delay_s > 0:
                    time.sleep(retry_delay_s)
        if last_exc is not None:
            raise last_exc
        return provider_fn(prompt_payload)

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


def _build_host_provider_fn_for_node(
    *,
    provider_name: str,
    model_id: str,
    api_key: str | None,
    timeout_ms: int,
    node_id: str,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    key_value = str(api_key or "").strip()
    if provider in {PROVIDER_OPENAI, PROVIDER_DEEPSEEK, PROVIDER_GEMINI} and key_value:
        provider_fn = _build_langchain_chat_provider_fn(
            provider_name=provider,
            model_id=model_id,
            api_key=key_value,
            timeout_ms=timeout_ms,
            node_id=node_id,
        )
        fallback_provider_fn: Callable[[dict[str, Any]], Any] | None = None
        fallback_model_id: str | None = None
        if (
            provider == PROVIDER_GEMINI
            and str(node_id or "").strip() == _GEMINI_PRIMARY_RETRY_NODE_ID
            and str(model_id or "").strip() in _GEMINI_PRIMARY_RETRY_MODEL_IDS
        ):
            fallback_model_id = _resolve_gemini_primary_fallback_model_id(str(model_id or ""))
            if fallback_model_id:
                fallback_provider_fn = _build_langchain_chat_provider_fn(
                    provider_name=provider,
                    model_id=fallback_model_id,
                    api_key=key_value,
                    timeout_ms=timeout_ms,
                    node_id=node_id,
                )
        return _wrap_node_provider_fn_with_retry(
            provider_fn=provider_fn,
            provider_name=provider,
            model_id=model_id,
            node_id=node_id,
            fallback_provider_fn=fallback_provider_fn,
            fallback_model_id=fallback_model_id,
        )
    return _build_host_mock_provider_for_node(node_id)
def get_ledger() -> SQLiteLedger:
    """鑾峰彇SQLite璐︽湰瀹炰緥锛堝欢杩熷垵濮嬪寲锛屾敮鎸佽法绾跨▼璁块棶锛夈€?

    鎸佷箙鍖栬矾寰勯€氳繃鐜鍙橀噺 Natsume_AUDIT_DB 閰嶇疆锛?
    榛樿浣跨敤鏂囦欢璺緞 host_brain/data/audit.sqlite3銆?
    璁剧疆 Natsume_AUDIT_DB=:memory: 鍙娇鐢ㄥ唴瀛樻暟鎹簱锛堜粎娴嬭瘯锛夈€?
    """
    global _ledger
    if _ledger is None:
        db_path = os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
        _ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
    return _ledger


def get_episodic_store() -> EpisodicStore:
    global _episodic_store
    ledger = get_ledger()
    if _episodic_store is None or _episodic_store.ledger is not ledger:
        _episodic_store = EpisodicStore(ledger=ledger)
    return _episodic_store


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_chat_scene_type(*, request_body: ChatRequest) -> tuple[str, str]:
    explicit_scene_type = str(request_body.scene_type or "").strip().upper()
    if explicit_scene_type:
        return explicit_scene_type, "explicit"
    if _parse_bool(os.environ.get("HOST_CHAT_ENABLE_PRECLASSIFIER"), default=True):
        return pre_intent_classifier(request_body.message), "pre_classifier"
    return SCENE_CHAT, "default_chat"


def _resolve_chat_retrieval_gate(*, scene_type: str) -> tuple[bool, str | None]:
    if not _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True):
        return False, "HOST_RETRIEVAL_DISABLED"
    if (
        str(scene_type or "").strip().upper() == SCENE_CHAT
        and _parse_bool(os.environ.get("HOST_CHAT_DISABLE_RETRIEVAL"), default=False)
    ):
        return False, "HOST_CHAT_RETRIEVAL_DISABLED"
    return True, None


def _normalize_chat_emotion_context(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    state = normalize_hard_state(
        {
            "mood_state": source.get("mood_state"),
            "ts_ms": source.get("emotion_ts_ms"),
        }
    )
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)

    emotion_tag = str(source.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags:
        emotion_tag = allowed_emotion_tags[0]

    emotion_range_hint = str(source.get("emotion_range_hint") or "").strip() or hard_snapshot.emotion_range_hint
    try:
        emotion_ts_ms = int(source.get("emotion_ts_ms") or hard_snapshot.emotion_ts_ms)
    except (TypeError, ValueError):
        emotion_ts_ms = int(hard_snapshot.emotion_ts_ms)

    return {
        "mood_state": str(hard_snapshot.mood_state),
        "emotion_tag": str(emotion_tag),
        "emotion_range_hint": str(emotion_range_hint),
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": emotion_ts_ms,
    }


def _resolve_chat_emotion_context(*, request_body: ChatRequest) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(request_body.emotion_context, Mapping):
        raw.update(dict(request_body.emotion_context))
    if request_body.mood_state is not None:
        raw["mood_state"] = request_body.mood_state
    if request_body.emotion_tag is not None:
        raw["emotion_tag"] = request_body.emotion_tag
    if request_body.emotion_range_hint is not None:
        raw["emotion_range_hint"] = request_body.emotion_range_hint
    if "mood_state" not in raw:
        raw["mood_state"] = str(os.environ.get("HOST_DEFAULT_MOOD_STATE") or "steady").strip().lower() or "steady"
    if "emotion_tag" not in raw:
        raw["emotion_tag"] = str(os.environ.get("HOST_DEFAULT_EMOTION_TAG") or "NEUTRAL").strip().upper() or "NEUTRAL"
    return _normalize_chat_emotion_context(raw)


def _build_chat_governance_snapshot(
    *,
    emotion_context: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = _normalize_chat_emotion_context(emotion_context)
    hard_state = normalize_hard_state(
        {
            "mood_state": normalized.get("mood_state"),
            "ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        }
    )
    snapshot = build_governance_snapshot(
        hard_state,
        overrides={
            "mood_state": normalized.get("mood_state"),
            "emotion_tag": normalized.get("emotion_tag"),
            "emotion_ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        },
    )
    snapshot["emotion_context"] = {
        "mood_state": normalized.get("mood_state"),
        "emotion_tag": normalized.get("emotion_tag"),
        "emotion_range_hint": normalized.get("emotion_range_hint"),
    }
    return snapshot


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


# 娉ㄥ叆 ledger provider 鍒?canon_routes锛堟秷闄ゅ惊鐜鍏ラ闄╋紝P1-R118-2锛?
_set_canon_ledger_provider(get_ledger)

# 娉ㄥ叆 ledger provider 鍒?obs_routes锛圡18锛?
_set_obs_ledger_provider(get_ledger)


def get_tool_catalog() -> ToolCatalog:
    """Get the ToolCatalog singleton."""
    global _tool_catalog
    if _tool_catalog is None:
        _tool_catalog = ToolCatalog()
    return _tool_catalog


def get_tool_broker() -> ToolBroker:
    """Get the ToolBroker singleton."""
    global _tool_broker
    if _tool_broker is None:
        token_service = TokenService()
        _tool_broker = ToolBroker(
            catalog=get_tool_catalog(),
            token_service=token_service,
            ledger=get_ledger(),
        )
    return _tool_broker


# 娉ㄥ叆 tool providers 鍒?tool_routes锛圡12-A TASK-0156锛?
_set_tool_providers(
    broker_provider=get_tool_broker,
    catalog_provider=get_tool_catalog,
    ledger_provider=get_ledger,
)


def get_bond_stage_engine() -> BondStageEngine:
    """Get the BondStageEngine singleton."""
    global _bond_stage_engine
    if _bond_stage_engine is None:
        _bond_stage_engine = BondStageEngine(ledger=get_ledger())
    return _bond_stage_engine


# 娉ㄥ叆 bond engine provider 鍒?bond_routes锛圡13-A锛?
_set_bond_engine_provider(get_bond_stage_engine)


def get_skill_registry() -> SkillRegistry:
    """Get the built-in skill registry singleton."""
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = build_builtin_skill_registry()
    return _skill_registry


def get_policy_cache() -> GovernorPolicyCache:
    """Get the governor policy cache singleton."""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = GovernorPolicyCache(ttl_ms=60_000)
    return _policy_cache


def get_intent_engine() -> IntentDecisionEngine:
    """Get the governor intent engine singleton."""
    global _intent_engine
    if _intent_engine is None:
        _intent_engine = IntentDecisionEngine(
            ledger=get_ledger(),
            registry=get_skill_registry(),
            policy_cache=get_policy_cache(),
        )
    return _intent_engine


# 鑷畾涔夊紓甯稿鐞嗗櫒
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler."""
    request_id = getattr(request.state, 'request_id', None) or new_request_id()

    # 鏍规嵁寮傚父绫诲瀷鏄犲皠鍒癏TTP鐘舵€佺爜鍜宺eason_code
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"

    # 璁板綍閿欒鏃ュ織
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


# 涓棿浠讹細璇锋眰ID娉ㄥ叆鍜屽璁?
@app.middleware("http")
async def add_request_id_and_audit(request: Request, call_next: Callable) -> Response:
    """Attach request ids to incoming requests."""
    request_id = new_request_id()
    request.state.request_id = request_id

    start_time = time.time()

    # 璁板綍璇锋眰寮€濮?
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

        # 璁板綍璇锋眰瀹屾垚
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
        # 寮傚父宸插湪鍏ㄥ眬澶勭悊鍣ㄤ腑澶勭悊锛岃繖閲屽彧璁板綍棰濆淇℃伅
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


# 鏍稿績HTTP绔偣
@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health endpoint."""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        # 鍏堣幏鍙栫湡瀹?runtime_statuses锛岀‘淇濆仴搴锋鏌ヨ兘鍙嶆槧鏈嶅姟鐪熷疄鐘舵€?
        _host_result = load_host_config()
        _host_result = load_host_config()
        _runtime_statuses: dict[str, Any] = {}
        if _host_result.success and _host_result.config is not None:
            _runtime_statuses = build_host_runtime_statuses(_host_result.config)
        # TASK-0211 M23-B锛氬悎骞?edge 浼犺緭灞傛帰閽堢粨鏋?
        # build_edge_transport_probe_statuses 鍘熸湰瀹氫箟浣嗘湭琚皟鐢紝
        # 姝ゅ闆嗘垚浣?edge 鑺傜偣杩愯鏃剁姸鎬佽兘鍙嶆槧鍒?host 渚у仴搴锋姤鍛婁腑銆?
        try:
            _edge_available = _runtime_statuses.get("edge.rpc.gateway", {}).get("available", True)
            _edge_probes = build_edge_transport_probe_statuses(available=bool(_edge_available))
            # edge 鎺㈤拡缁撴灉涓嶈鐩栧凡鏈夌殑 host 渚х姸鎬侊紝浠呰ˉ鍏?edge 浼犺緭灞傜姸鎬?
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
    """Readiness endpoint."""
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
    """Metrics endpoint."""
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
    """MCP registry health endpoint."""
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
    """World canon sync health endpoint."""
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
    """Request trace endpoint."""
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
    """Startup report endpoint."""
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

        # 璁板綍鍚姩鎶ュ憡鐢ㄤ簬璋冭瘯
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
        text = _repair_utf8_latin1_mojibake(str(narrative.get("text") or "").strip())
        if text:
            return text
    elif isinstance(narrative, str):
        text = _repair_utf8_latin1_mojibake(narrative.strip())
        if text:
            return text
    return ""


def _build_node_style_bindings(*, render_target_lang: str | None = None) -> dict[str, dict[str, Any]]:
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    core_canon_id = str(os.environ.get("HOST_STYLE_CORE_CANON_ID") or "natsume_ai_cn").strip()
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    explicit_render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_CANON_ID") or "").strip()
    if explicit_render_canon_id:
        render_canon_id = explicit_render_canon_id
    elif resolved_render_lang == "zh":
        render_canon_id = (
            str(os.environ.get("HOST_STYLE_RENDER_CN_CANON_ID") or "").strip()
            or core_canon_id
            or "natsume_ai_cn"
        )
    else:
        render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_JA_CANON_ID") or "natsume_ai_ja").strip()
    secondary_canon_id = str(os.environ.get("HOST_STYLE_SECONDARY_CANON_ID") or core_canon_id).strip()

    store = StyleCanonStore(style_root=style_root, ledger=get_ledger())
    resolved: dict[str, dict[str, Any]] = {}
    mapping = [
        ("core_reasoner_primary", core_canon_id),
        ("core_reasoner_secondary", secondary_canon_id),
        (CHAT_DIRECT_NODE_ID, render_canon_id),
        ("renderer_ja_c_style", render_canon_id),
    ]
    for node_id, canon_id in mapping:
        if not canon_id:
            continue
        try:
            entry = store.get(canon_id)
        except Exception:
            continue
        if entry is None:
            continue
        resolved[node_id] = {
            "canon_id": entry.canon_id,
            "version": int(entry.version),
            "hash": entry.hash,
            "tone_only": bool(entry.tone_only),
            "unsafe_as_fact": bool(entry.unsafe_as_fact),
            "style_instruction": entry.content,
            "citation": f"style_canon:{entry.canon_id}@v{entry.version}:{entry.hash[:12]}",
        }
    return resolved


def _normalize_node_style_bindings(
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    normalized_style_bindings: dict[str, dict[str, Any]] = {}
    for node_id, row in dict(style_bindings or {}).items():
        if not isinstance(row, Mapping):
            continue
        normalized_style_bindings[str(node_id)] = {
            "canon_id": str(row.get("canon_id") or "").strip(),
            "version": row.get("version"),
            "hash": str(row.get("hash") or "").strip(),
            "tone_only": bool(row.get("tone_only", True)),
            "unsafe_as_fact": bool(row.get("unsafe_as_fact", True)),
            "style_instruction": str(row.get("style_instruction") or "").strip(),
            "citation": str(row.get("citation") or "").strip(),
        }
    return normalized_style_bindings


def _build_chat_generation_prompt_payload(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    render_target_lang: str | None,
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    emotion_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = _infer_render_target_lang(user_message=user_message)
    normalized_style_bindings = _normalize_node_style_bindings(style_bindings)
    payload: dict[str, Any] = {
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "render_target_lang": resolved_render_lang,
        "style_bindings": normalized_style_bindings,
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
    if isinstance(emotion_context, dict) and emotion_context:
        payload["emotion_context"] = emotion_context
    return payload


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
    scene_type: str = SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    _ = provider_fn
    _ = model_id
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = "ja"
    base_timeout_ms = max(800, int(timeout_ms))
    resolved_provider = PROVIDER_GEMINI
    node_model_ids: dict[str, str] = {
        CHAT_DIRECT_NODE_ID: "gemini-2.5-flash",
        "core_reasoner_primary": "gemini-2.5-pro",
        "core_reasoner_secondary": "gemini-2.5-flash",
        "planner_constraints": "gemini-2.5-pro",
        "tool_planner": "gemini-2.5-flash",
        "safety_verifier": "gemini-2.5-flash-lite",
        "consistency_verifier": "gemini-2.5-flash-lite",
        "renderer_ja_c_style": "gemini-2.5-flash",
        "workbench_translate": "gemini-2.5-pro",
        "workbench_code_assist": "gemini-2.5-pro",
    }
    node_timeouts: dict[str, int] = {
        CHAT_DIRECT_NODE_ID: min(base_timeout_ms, 7000),
        "core_reasoner_primary": min(base_timeout_ms, 12000),
        "core_reasoner_secondary": min(base_timeout_ms, 9000),
        "planner_constraints": min(base_timeout_ms, 8000),
        "tool_planner": min(base_timeout_ms, 8000),
        "safety_verifier": min(base_timeout_ms, 7000),
        "consistency_verifier": min(base_timeout_ms, 7000),
        "renderer_ja_c_style": min(base_timeout_ms, 9000),
        "workbench_translate": min(base_timeout_ms, 10000),
        "workbench_code_assist": min(base_timeout_ms, 10000),
    }

    # M33-F锛歮odel_node_overrides 浠嶄娇鐢ㄥ叏鑺傜偣娉ㄥ唽琛紙CHAT_GRAPH_NODE_ORDER锛夛紝
    # 纭繚鎵€鏈夎妭鐐圭殑妯″瀷閰嶇疆閮藉凡鍑嗗濂斤紙fallback 鑺傜偣涔熼渶瑕侀厤缃級銆?
    model_node_overrides: dict[str, dict[str, Any]] = {}
    model_node_overrides: dict[str, dict[str, Any]] = {}
    for node_id in CHAT_GRAPH_NODE_ORDER:
        node_model_id = node_model_ids[node_id]
        node_timeout = node_timeouts[node_id]
        model_node_overrides[node_id] = {
            "provider": resolved_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "api_key": str(api_key or "").strip() or None,
            "available": bool(available),
            "provider_fn": _build_host_provider_fn_for_node(
                provider_name=resolved_provider,
                model_id=node_model_id,
                api_key=api_key,
                timeout_ms=node_timeout,
                node_id=node_id,
            ),
        }

    # M33-A/B锛氭寜鍦烘櫙鍔ㄦ€佽В鏋愯妭鐐瑰簭鍒楋紙涓嶅惈 secondary fallback 鑺傜偣锛?
    scene_node_sequence = _resolve_scene_node_sequence(scene_type)
    # M33-B锛歝ore_reasoner_secondary 浣滀负 fallback 鑺傜偣锛屽繀椤诲湪 node_bindings 涓敞鍐?
    # 锛坴alidate_graph_config 瑕佹眰 fallback_edges 涓殑鑺傜偣涔熷湪 node_bindings 涓級锛?
    # 浣嗕笉鍔犲叆 edges 姝ｅ父閾捐矾銆?
    fallback_only_nodes = ["core_reasoner_secondary"]
    scene_node_sequence = _resolve_scene_node_sequence(scene_type)
    fallback_only_nodes = ["core_reasoner_secondary"]
    all_registered_nodes = scene_node_sequence + [
        n for n in fallback_only_nodes if n not in scene_node_sequence
    ]
    node_bindings = [
        {
            "node_id": node_id,
            "model_node_id": node_id,
            "task": node_id,
            "require_approval_token": False,
        }
        for node_id in all_registered_nodes
    ]
    edges = [
        {"from": scene_node_sequence[i], "to": scene_node_sequence[i + 1]}
        for i in range(len(scene_node_sequence) - 1)
    ]
    # Ensure fallback secondary node can continue into renderer on successful recovery.
    if (
        "core_reasoner_secondary" in all_registered_nodes
        and CHAT_RENDER_NODE_ID in all_registered_nodes
        and not any(str(edge.get("from") or "").strip() == "core_reasoner_secondary" for edge in edges)
    ):
        edges.append({"from": "core_reasoner_secondary", "to": CHAT_RENDER_NODE_ID})
    return {
        "graph_id": "host.chat.realtime",
        "version": "v1",
        "policy_timeout_ms": max(base_timeout_ms * 6, 60_000),
        "entry_node": scene_node_sequence[0],
        "model_profile_id": "chat_ja_24x7_v1",
        "node_bindings": node_bindings,
        "edges": edges,
        "fallback_edges": [
            # M33-B锛歝ore_reasoner_secondary 浠呬綔涓?primary 澶辫触鏃剁殑 fallback
            {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
            {"from": "core_reasoner_secondary", "to": "renderer_ja_c_style"},
        ],
        "model_node_overrides": model_node_overrides,
        "graph_meta": {
            "online_chain": "six_recall + scene_routed_langgraph + targeted_renderer",
            "provider": provider_name,
            "scene_type": str(scene_type or SCENE_CHAT).strip().upper(),
            "scene_type_source": scene_type_source,
            "render_target_lang": resolved_render_lang,
            "active_node_count": len(scene_node_sequence),
        },
    }


def _build_node_role_instruction(node_id: str, *, render_target_lang: str = "ja") -> str:
    resolved_node_id = str(node_id or "").strip()
    if resolved_node_id == CHAT_DIRECT_NODE_ID:
        if str(render_target_lang).strip().lower() == "zh":
            return (
                "Role: chat_direct_ja.\n"
                "Goal: generate the final user-facing reply directly in concise spoken Simplified Chinese.\n"
                "Policy: use USER_MESSAGE, EVIDENCE, WORLD_CANON_BINDING, CONSTRAINT_BINDING_JSON, and EMOTION_BINDING directly; "
                "ignore CORE_NARRATIVE_ZH when it is empty; keep intent_draft draft-only and never add facts or claim execution."
            )
        return (
            "Role: chat_direct_ja.\n"
            "Goal: generate the final user-facing reply directly in concise natural Japanese.\n"
            "Policy: use USER_MESSAGE, EVIDENCE, WORLD_CANON_BINDING, CONSTRAINT_BINDING_JSON, and EMOTION_BINDING directly; "
            "ignore CORE_NARRATIVE_ZH when it is empty; keep intent_draft draft-only and never add facts or claim execution."
        )
    if resolved_node_id == CHAT_RENDER_NODE_ID:
        if str(render_target_lang).strip().lower() == "zh":
            return (
                "Role: renderer_ja_c_style (final Chinese renderer).\n"
                "Goal: render CORE_NARRATIVE_ZH into concise spoken Simplified Chinese.\n"
                "Policy: keep semantics stable, preserve emotional tone, and never add facts."
            )
        return (
            "Role: renderer_ja_c_style (final Japanese renderer).\n"
            "Goal: render CORE_NARRATIVE_ZH into concise natural Japanese.\n"
            "Policy: keep semantics stable, preserve emotional tone, and never add facts."
        )

    role_instructions: dict[str, str] = {
        "core_reasoner_primary": (
            "Role: core_reasoner_primary.\n"
            "Goal: produce the main Chinese semantic draft for the user request.\n"
            "Policy: stay grounded in evidence, remain concise, and never claim execution."
        ),
        "core_reasoner_secondary": (
            "Role: core_reasoner_secondary.\n"
            "Goal: produce a shorter fallback Chinese draft when primary reasoning degrades.\n"
            "Policy: be conservative and never expand facts or scope."
        ),
        "planner_constraints": (
            "Role: planner_constraints.\n"
            "Goal: summarize actionable constraints for this turn.\n"
            "Policy: output compact draft-only guidance."
        ),
        "tool_planner": (
            "Role: tool_planner.\n"
            "Goal: propose tool-use drafts only when tools materially help.\n"
            "Policy: never imply a tool has already run."
        ),
        "safety_verifier": (
            "Role: safety_verifier.\n"
            "Goal: flag safety or overclaim risks.\n"
            "Policy: output a concise verdict and minimum patch."
        ),
        "consistency_verifier": (
            "Role: consistency_verifier.\n"
            "Goal: check canon/style consistency drift.\n"
            "Policy: output a concise verdict and minimum patch."
        ),
        "workbench_translate": (
            "Role: workbench_translate.\n"
            "Goal: provide translation-assist draft output.\n"
            "Policy: preserve meaning and terminology consistency."
        ),
        "workbench_code_assist": (
            "Role: workbench_code_assist.\n"
            "Goal: provide concise code-assist draft output.\n"
            "Policy: stay concrete and do not claim files were changed."
        ),
    }
    return role_instructions.get(
        resolved_node_id,
        "Role: generic node.\nGoal: produce a concise draft-only output.\nPolicy: keep the JSON contract stable.",
    )


def _render_node_generation_prompt(*, node_id: str, runtime_payload: Mapping[str, Any]) -> str:
    user_message = _truncate_for_prompt(runtime_payload.get("user_message"), limit=1000)
    context_pack_raw = runtime_payload.get("context_pack")
    context_pack = dict(context_pack_raw) if isinstance(context_pack_raw, Mapping) else {}
    context_pack_validation_raw = runtime_payload.get("context_pack_validation")
    context_pack_validation = (
        dict(context_pack_validation_raw)
        if isinstance(context_pack_validation_raw, Mapping)
        else {}
    )

    retrieval_raw = runtime_payload.get("retrieval")
    retrieval = dict(retrieval_raw) if isinstance(retrieval_raw, Mapping) else {}
    retrieval_status = str(retrieval.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    retrieval_reason = str(retrieval.get("reason_code") or "").strip() or "NONE"
    retrieval_slot_rows = context_pack.get("retrieval")
    slot_rows = retrieval_slot_rows if isinstance(retrieval_slot_rows, list) else []
    if slot_rows:
        rows = slot_rows
    else:
        evidence_items = retrieval.get("evidence_items")
        rows = evidence_items if isinstance(evidence_items, list) else []

    evidence_lines: list[str] = []
    for row in rows[:6]:
        if not isinstance(row, Mapping):
            continue
        evidence_ref = _truncate_for_prompt(
            row.get("evidence_ref") or row.get("source_ref") or row.get("citation") or "unknown",
            limit=80,
        )
        snippet = _truncate_for_prompt(row.get("content") or row.get("snippet"), limit=220)
        if not snippet:
            continue
        evidence_lines.append(f"- [{evidence_ref}] {snippet}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "- (none)"

    authoritative_rows = context_pack.get("authoritative")
    world_rows = authoritative_rows if isinstance(authoritative_rows, list) else []
    world_lines: list[str] = []
    for row in world_rows[:3]:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("kind") or "").strip().lower() != "world_canon":
            continue
        citation = _truncate_for_prompt(row.get("citation") or row.get("doc_id") or "unknown", limit=96)
        content = _truncate_for_prompt(row.get("content"), limit=260)
        if not content:
            continue
        world_lines.append(f"- [{citation}] {content}")
    world_block = "\n".join(world_lines) if world_lines else "- (none)"

    constraints_raw = context_pack.get("constraints")
    constraints = dict(constraints_raw) if isinstance(constraints_raw, Mapping) else {}
    constraints_json = _truncate_for_prompt(
        json.dumps(constraints, ensure_ascii=False, sort_keys=True),
        limit=1200,
    )
    context_pack_valid = bool(context_pack_validation.get("valid", False))
    context_pack_reason = str(context_pack_validation.get("reason_code") or "NONE").strip() or "NONE"
    context_pack_violation_count = int(context_pack_validation.get("violation_count") or 0)

    core_payload = _extract_node_runtime_payload(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_narrative = _extract_node_narrative_text(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_meta = core_payload.get("meta")
    if isinstance(core_meta, dict) and core_meta.get("degraded"):
        secondary_narrative = _extract_node_narrative_text(runtime_payload, node_id="core_reasoner_secondary")
        if secondary_narrative:
            core_narrative = secondary_narrative
            secondary_payload = _extract_node_runtime_payload(runtime_payload, node_id="core_reasoner_secondary")
            if isinstance(secondary_payload.get("intent_draft"), dict) and secondary_payload["intent_draft"]:
                core_payload = secondary_payload

    core_intent_draft = core_payload.get("intent_draft")
    if not isinstance(core_intent_draft, dict):
        core_intent_draft = {}
    core_intent_json = json.dumps(core_intent_draft, ensure_ascii=False, sort_keys=True)
    planner_text = _extract_node_narrative_text(runtime_payload, node_id="planner_constraints")
    verifier_text = _extract_node_narrative_text(runtime_payload, node_id="safety_verifier")

    style_pack_raw = context_pack.get("style_pack")
    style_pack = dict(style_pack_raw) if isinstance(style_pack_raw, Mapping) else {}
    style_bindings_raw = style_pack.get("node_bindings")
    style_bindings = dict(style_bindings_raw) if isinstance(style_bindings_raw, Mapping) else {}
    if not style_bindings:
        legacy_bindings_raw = runtime_payload.get("style_bindings")
        style_bindings = dict(legacy_bindings_raw) if isinstance(legacy_bindings_raw, Mapping) else {}
    node_style_raw = style_bindings.get(node_id)
    node_style = dict(node_style_raw) if isinstance(node_style_raw, Mapping) else {}
    style_instruction = _truncate_for_prompt(node_style.get("style_instruction"), limit=1200)
    style_block = ""
    if style_instruction:
        style_block = (
            "STYLE_CANON_BINDING:\n"
            f"- canon_id: {_truncate_for_prompt(node_style.get('canon_id'), limit=64) or 'unknown'}\n"
            f"- citation: {_truncate_for_prompt(node_style.get('citation'), limit=96) or 'unknown'}\n"
            f"- tone_only: {bool(node_style.get('tone_only', True))}\n"
            f"- unsafe_as_fact: {bool(node_style.get('unsafe_as_fact', True))}\n"
            f"- instruction:\n{style_instruction}\n\n"
        )

    emotion_context_raw = runtime_payload.get("emotion_context")
    emotion_context = (
        dict(emotion_context_raw)
        if isinstance(emotion_context_raw, Mapping)
        else {}
    )
    context_pack_emotion_raw = context_pack.get("emotion_pulse")
    context_pack_emotion = (
        dict(context_pack_emotion_raw)
        if isinstance(context_pack_emotion_raw, Mapping)
        else {}
    )
    merged_emotion_context = _normalize_chat_emotion_context(
        {
            "mood_state": context_pack_emotion.get("mood_state") or emotion_context.get("mood_state"),
            "emotion_tag": context_pack_emotion.get("emotion_tag") or emotion_context.get("emotion_tag"),
            "emotion_range_hint": (
                context_pack_emotion.get("emotion_range_hint")
                or emotion_context.get("emotion_range_hint")
            ),
        }
    )
    pulse_style_hint = _truncate_for_prompt(context_pack_emotion.get("style_hint"), limit=160)
    memory_emotion_hint_raw = context_pack.get("memory_emotion_hint")
    memory_emotion_hint = (
        dict(memory_emotion_hint_raw)
        if isinstance(memory_emotion_hint_raw, Mapping)
        else {}
    )
    bond_texture_raw = context_pack.get("bond_texture")
    bond_texture = (
        dict(bond_texture_raw)
        if isinstance(bond_texture_raw, Mapping)
        else {}
    )
    memory_emotion_hint_json = _truncate_for_prompt(
        json.dumps(memory_emotion_hint, ensure_ascii=False, sort_keys=True),
        limit=600,
    )
    bond_texture_json = _truncate_for_prompt(
        json.dumps(bond_texture, ensure_ascii=False, sort_keys=True),
        limit=600,
    )
    emotion_binding_block = (
        "EMOTION_BINDING:\n"
        f"- mood_state: {merged_emotion_context.get('mood_state')}\n"
        f"- emotion_tag: {merged_emotion_context.get('emotion_tag')}\n"
        f"- emotion_range_hint: {_truncate_for_prompt(merged_emotion_context.get('emotion_range_hint'), limit=220) or 'NONE'}\n"
        f"- pulse_style_hint: {pulse_style_hint or 'NONE'}\n"
        f"- memory_emotion_hint_json: {memory_emotion_hint_json}\n"
        f"- bond_texture_json: {bond_texture_json}\n\n"
    )

    render_target_lang = str(runtime_payload.get("render_target_lang") or "").strip().lower()
    if render_target_lang not in {"zh", "ja"}:
        render_target_lang = _infer_render_target_lang(
            user_message=user_message,
            core_narrative=core_narrative,
        )

    cn_style_boost_block = ""
    if render_target_lang == "zh":
        cn_style_boost_block = (
            "CN_STYLE_BOOST_BINDING:\n"
            "- style_strength: HIGH\n"
            "- Priority: safety/facts > style canon > generic assistant tone.\n"
            "- Write naturally spoken Simplified Chinese; avoid formal support-style wording.\n"
            "- Typical rhythm: 2-4 short sentences, compact and clear.\n"
            "- Encourage gentle interpersonal tone and avoid boilerplate.\n\n"
        )

    role_instruction = _build_node_role_instruction(node_id, render_target_lang=render_target_lang)

    return (
        "Return exactly one JSON object with keys: narrative, intent_draft, meta.\n"
        "Schema:\n"
        "{\"narrative\":{\"text\":\"...\"},\"intent_draft\":{},\"meta\":{\"draft_only\":true}}\n"
        "Hard rules:\n"
        "1) JSON only; no markdown/code fence.\n"
        "2) meta.draft_only must be true.\n"
        "3) Never claim executed/saved/confirmed/completed.\n"
        "4) Keep concise and grounded in provided context.\n"
        "NODE_ROLE_BINDING:\n"
        f"{role_instruction}\n\n"
        f"{style_block}"
        f"{cn_style_boost_block}"
        f"{emotion_binding_block}"
        "CONTEXT_PACK_VALIDATION:\n"
        f"- valid: {context_pack_valid}\n"
        f"- reason_code: {context_pack_reason}\n"
        f"- violation_count: {context_pack_violation_count}\n\n"
        f"WORLD_CANON_BINDING:\n{world_block}\n\n"
        f"CONSTRAINT_BINDING_JSON:\n{constraints_json}\n\n"
        f"NODE_ID: {node_id}\n"
        f"RENDER_TARGET_LANG: {render_target_lang}\n"
        f"USER_MESSAGE:\n{user_message}\n\n"
        f"RETRIEVAL_STATUS: {retrieval_status}\n"
        f"RETRIEVAL_REASON_CODE: {retrieval_reason}\n"
        f"EVIDENCE:\n{evidence_block}\n\n"
        f"CORE_NARRATIVE_ZH:\n{core_narrative}\n\n"
        f"PLANNER_HINT:\n{planner_text}\n\n"
        f"SAFETY_HINT:\n{verifier_text}\n\n"
        f"CORE_INTENT_DRAFT_JSON:\n{core_intent_json}\n"
    )


def _build_chat_context_pack(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
    emotion_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_style_bindings = _normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = _normalize_chat_emotion_context(emotion_context)

    style_rows: list[dict[str, Any]] = []
    for node_id, row in normalized_style_bindings.items():
        style_rows.append(
            {
                "slot": "style_pack",
                "kind": "style_canon",
                "node_id": node_id,
                "canon_id": row.get("canon_id"),
                "version": row.get("version"),
                "hash": row.get("hash"),
                "tone_only": bool(row.get("tone_only", True)),
                "unsafe_as_fact": bool(row.get("unsafe_as_fact", True)),
                "citation": row.get("citation"),
                "instruction": _truncate_for_prompt(row.get("style_instruction"), limit=1600),
            }
        )

    authoritative_rows: list[dict[str, Any]] = []
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "").strip() or None
    try:
        world_top_k = int(str(os.environ.get("HOST_WCANON_TOP_K") or "3").strip())
    except ValueError:
        world_top_k = 3
    world_top_k = max(1, min(world_top_k, 10))
    try:
        authoritative_rows = build_world_canon_slot(world_root=world_root, top_k=world_top_k)
    except Exception as exc:
        check_wcanon_slot_violation(
            slot_name="world_canon_slot",
            detail=f"world canon slot build failed: {exc}",
            ledger=get_ledger(),
            request_id=request_id,
        )
        authoritative_rows = []

    retrieval_rows: list[dict[str, Any]] = []
    for row in retrieval_evidence_items[:10]:
        if not isinstance(row, Mapping):
            continue
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        retrieval_rows.append(
            {
                "slot": "retrieval",
                "kind": "episodic",
                "source_ref": str(row.get("source_ref") or "").strip() or None,
                "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
                "content": snippet,
                "score": row.get("score"),
                "confidence": row.get("confidence"),
            }
        )

    mood_state = str(normalized_emotion_context.get("mood_state") or "steady")
    emotion_tag = str(normalized_emotion_context.get("emotion_tag") or "NEUTRAL")
    emotion_range_hint = _truncate_for_prompt(
        normalized_emotion_context.get("emotion_range_hint"),
        limit=200,
    )
    allowed_emotion_tags = list(normalized_emotion_context.get("allowed_emotion_tags") or [])
    emotion_pulse = build_emotion_pulse_slot(
        None,
        emotion_tag=emotion_tag,
        mood_state=mood_state,
    )
    if emotion_range_hint:
        emotion_pulse["emotion_range_hint"] = emotion_range_hint
    memory_emotion_hint = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "emotion_range_hint": emotion_range_hint,
        "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
        "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
    }
    bond_texture = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "tone_hint": str(emotion_pulse.get("style_hint") or ""),
    }

    context_pack = {
        "hard_snapshot": {
            "session_id": session_id,
            "thread_id": thread_id,
            "sys_mode": str(os.environ.get("HOST_SYS_MODE") or "normal").strip() or "normal",
            "mood_state": mood_state,
            "allowed_emotion_tags": allowed_emotion_tags,
            "emotion_range_hint": emotion_range_hint,
        },
        "bond_summary": {},
        "authoritative": authoritative_rows,
        "retrieval": retrieval_rows,
        "style_pack": {
            "style_canon": style_rows,
            "node_bindings": normalized_style_bindings,
        },
        "constraints": {
            "strict_json_output": True,
            "no_false_execution_claim": True,
            "no_canon_as_fact": True,
            "tone_policy": "short_restrained",
            "contract_versions": {
                "core": "CoreDraft.v1",
                "render": "RenderDraft.v1",
                "verifier": "Verifier.v1",
            },
            "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
            "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
        },
        "tool_meta_pack": [],
        "emotion_pulse": emotion_pulse,
        "memory_emotion_hint": memory_emotion_hint,
        "bond_texture": bond_texture,
    }

    validation = validate_context_pack(context_pack)
    violations = [
        {"code": item.code, "detail": item.detail, "slot": item.slot}
        for item in validation.violations
    ]
    if validation.valid:
        return (
            context_pack,
            {
                "valid": True,
                "degraded": False,
                "reason_code": None,
                "violation_count": 0,
                "violations": [],
            },
        )

    for item in validation.violations:
        if item.slot == "authoritative":
            check_wcanon_slot_violation(
                slot_name="world_canon_slot",
                detail=item.detail,
                ledger=get_ledger(),
                request_id=request_id,
            )
            break

    degraded_pack = dict(context_pack)
    degraded_pack["authoritative"] = []
    degraded_validation = validate_context_pack(degraded_pack)
    if degraded_validation.valid:
        return (
            degraded_pack,
            {
                "valid": True,
                "degraded": True,
                "reason_code": "CONTEXT_PACK_DEGRADED",
                "violation_count": len(violations),
                "violations": violations,
            },
        )

    minimal_pack = {
        "hard_snapshot": {},
        "bond_summary": {},
        "authoritative": [],
        "retrieval": [],
        "style_pack": {},
        "constraints": {},
        "tool_meta_pack": [],
    }
    return (
        minimal_pack,
        {
            "valid": True,
            "degraded": True,
            "reason_code": "CONTEXT_PACK_MINIMAL_FALLBACK",
            "violation_count": len(violations),
            "violations": violations,
        },
    )


def _build_chat_generation_prompt_payload(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    normalized_style_bindings = _normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = _normalize_chat_emotion_context(emotion_context)
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = _infer_render_target_lang(user_message=user_message)
    context_pack, context_pack_validation = _build_chat_context_pack(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        style_bindings=normalized_style_bindings,
        emotion_context=normalized_emotion_context,
    )
    return {
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "render_target_lang": resolved_render_lang,
        "style_bindings": normalized_style_bindings,
        "emotion_context": {
            "mood_state": normalized_emotion_context.get("mood_state"),
            "emotion_tag": normalized_emotion_context.get("emotion_tag"),
            "emotion_range_hint": normalized_emotion_context.get("emotion_range_hint"),
        },
        "context_pack": context_pack,
        "context_pack_validation": context_pack_validation,
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


def _build_chat_graph_config(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    api_key: str | None,
    provider_fn: Callable[[dict[str, Any]], Any],
    scene_type: str = SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    _ = provider_fn
    _ = model_id
    resolved_scene_type = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    is_chat_fast_path = resolved_scene_type == SCENE_CHAT
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = "ja"
    base_timeout_ms = max(800, int(timeout_ms))
    resolved_provider = PROVIDER_GEMINI
    node_model_ids: dict[str, str] = {
        "core_reasoner_primary": "gemini-2.5-pro",
        "core_reasoner_secondary": "gemini-2.5-flash",
        "planner_constraints": "gemini-2.5-pro",
        "tool_planner": "gemini-2.5-flash",
        "safety_verifier": "gemini-2.5-flash-lite",
        "consistency_verifier": "gemini-2.5-flash-lite",
        "renderer_ja_c_style": "gemini-2.5-flash",
        "workbench_translate": "gemini-2.5-pro",
        "workbench_code_assist": "gemini-2.5-pro",
    }
    node_timeouts: dict[str, int] = {
        "core_reasoner_primary": min(base_timeout_ms, 12000),
        "core_reasoner_secondary": min(base_timeout_ms, 9000),
        "planner_constraints": min(base_timeout_ms, 8000),
        "tool_planner": min(base_timeout_ms, 8000),
        "safety_verifier": min(base_timeout_ms, 7000),
        "consistency_verifier": min(base_timeout_ms, 7000),
        "renderer_ja_c_style": min(base_timeout_ms, 9000),
        "workbench_translate": min(base_timeout_ms, 10000),
        "workbench_code_assist": min(base_timeout_ms, 10000),
    }
    if is_chat_fast_path:
        node_model_ids[CHAT_DIRECT_NODE_ID] = "gemini-2.5-flash"
        node_timeouts[CHAT_DIRECT_NODE_ID] = min(base_timeout_ms, 6000)
        node_model_ids["core_reasoner_primary"] = "gemini-2.5-flash"
        node_timeouts["core_reasoner_primary"] = min(base_timeout_ms, 5000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 3000)
        node_timeouts["renderer_ja_c_style"] = min(base_timeout_ms, 4000)
    model_node_overrides: dict[str, dict[str, Any]] = {}
    for node_id in CHAT_GRAPH_NODE_ORDER:
        node_model_id = str(node_model_ids.get(node_id) or model_id).strip() or model_id
        node_timeout = int(node_timeouts.get(node_id, base_timeout_ms))
        model_node_overrides[node_id] = {
            "provider": resolved_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "api_key": str(api_key or "").strip() or None,
            "available": bool(available),
            "provider_fn": _build_host_provider_fn_for_node(
                provider_name=resolved_provider,
                model_id=node_model_id,
                api_key=api_key,
                timeout_ms=node_timeout,
                node_id=node_id,
            ),
        }

    scene_node_sequence = _resolve_scene_node_sequence(scene_type)
    fallback_only_nodes = [] if is_chat_fast_path else ["core_reasoner_secondary"]
    all_registered_nodes = scene_node_sequence + [
        n for n in fallback_only_nodes if n not in scene_node_sequence
    ]
    node_bindings = [
        {
            "node_id": node_id,
            "model_node_id": node_id,
            "task": node_id,
            "require_approval_token": False,
        }
        for node_id in all_registered_nodes
    ]
    edges = [
        {"from": scene_node_sequence[i], "to": scene_node_sequence[i + 1]}
        for i in range(len(scene_node_sequence) - 1)
    ]
    entry_node_id = scene_node_sequence[0]
    fallback_target_node = CHAT_RENDER_NODE_ID if is_chat_fast_path else (
        "consistency_verifier"
        if "consistency_verifier" in scene_node_sequence
        else CHAT_RENDER_NODE_ID
    )
    chain_budget_ms = (
        sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence)
        + (
            int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
            if not is_chat_fast_path
            else 0
        )
    )
    primary_timeout_ms = int(node_timeouts.get(entry_node_id, base_timeout_ms))
    try:
        configured_attempts = int(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS") or "2").strip())
    except ValueError:
        configured_attempts = 2
    retry_attempts = max(1, min(configured_attempts, 3))
    try:
        configured_retry_delay_ms = int(float(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS") or "150").strip()))
    except ValueError:
        configured_retry_delay_ms = 150
    retry_delay_ms = max(0, min(configured_retry_delay_ms, 2_000))
    retry_budget_ms = 0
    fallback_budget_ms = 0
    if entry_node_id == _GEMINI_PRIMARY_RETRY_NODE_ID:
        retry_budget_ms = max(0, retry_attempts - 1) * (primary_timeout_ms + retry_delay_ms)
        fallback_budget_ms = (
            primary_timeout_ms
            if _resolve_gemini_primary_fallback_model_id(str(node_model_ids.get(entry_node_id) or ""))
            else 0
        )
    policy_timeout_ms = chain_budget_ms + retry_budget_ms + fallback_budget_ms + 5_000
    if is_chat_fast_path:
        policy_timeout_ms = max(8_000, min(int(policy_timeout_ms), 12_000))
    else:
        # Keep a generous lower bound for non-chat long-tail latency to avoid
        # premature policy cutoffs right before renderer.
        policy_timeout_ms = max(90_000, min(int(policy_timeout_ms), 240_000))
    return {
        "graph_id": "host.chat.realtime",
        "version": "v1",
        "policy_timeout_ms": policy_timeout_ms,
        "entry_node": entry_node_id,
        "model_profile_id": "chat_ja_24x7_v1",
        "node_bindings": node_bindings,
        "edges": edges,
        "fallback_edges": (
            []
            if is_chat_fast_path
            else [
                {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
                {"from": "core_reasoner_secondary", "to": fallback_target_node},
            ]
        ),
        "model_node_overrides": model_node_overrides,
        "graph_meta": {
            "online_chain": (
                "chat_direct_single_node + scene_routed_langgraph"
                if is_chat_fast_path
                else "six_recall + scene_routed_langgraph + targeted_renderer"
            ),
            "provider": provider_name,
            "scene_type": resolved_scene_type,
            "scene_type_source": scene_type_source,
            "render_target_lang": resolved_render_lang,
            "active_node_count": len(scene_node_sequence),
            "active_nodes": list(scene_node_sequence),
        },
    }


def _extract_chat_graph_node_entry(graph_output: dict[str, Any], *, node_id: str) -> dict[str, Any]:
    payload_root = dict(graph_output.get("payload") or {})
    node_entry = dict(payload_root.get(node_id) or {})
    return node_entry


def _extract_chat_graph_gateway_payload(graph_output: dict[str, Any], *, node_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    node_entry = _extract_chat_graph_node_entry(graph_output, node_id=node_id)
    gateway_payload_raw = node_entry.get("payload")
    gateway_payload = dict(gateway_payload_raw or {}) if isinstance(gateway_payload_raw, dict) else {}
    return node_entry, gateway_payload


def _build_payload_digest(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        serialized = str(value)
    if not serialized:
        return None
    return hashlib.sha256(serialized.encode("utf-8", errors="ignore")).hexdigest()


def _persist_chat_observability_rows(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    session_id: str,
    llm_summary: Mapping[str, Any],
    ts_ms: int,
    fallback_reason_code: str | None,
) -> None:
    nodes_raw = llm_summary.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        return

    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    now_value = int(time.time() * 1000)

    for idx, node in enumerate(nodes_raw):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or f"node_{idx}")
        status = str(node.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        reason_code = str(node.get("reason_code") or "").strip() or None
        provider = str(node.get("provider") or "").strip() or None
        model_id = str(node.get("model_id") or "").strip() or None
        node_input = dict(node.get("input") or {}) if isinstance(node.get("input"), dict) else node.get("input")
        node_output = dict(node.get("output") or {}) if isinstance(node.get("output"), dict) else node.get("output")
        row_ts = int(now_value + idx)

        ledger.log_llm_node_call(
            {
                "request_id": request_id,
                "node_id": node_id,
                "provider": provider or "",
                "model_id": model_id or "",
                "status": status,
                "reason_code": reason_code,
                "latency_ms": node.get("latency_ms"),
                "input_digest": _build_payload_digest(node_input),
                "output_digest": _build_payload_digest(node_output),
                "ts_ms": row_ts,
            }
        )

        graph_nodes.append(
            {
                "node_id": node_id,
                "node_type": "llm_node",
                "display_name": node_id,
                "provider": provider,
                "model_id": model_id,
                "status": status,
                "latency_ms": node.get("latency_ms"),
                "input_data": node_input,
                "output_data": node_output,
                "ts_ms": row_ts,
                "error": (reason_code if status != "OK" else None),
            }
        )

    for idx in range(len(graph_nodes) - 1):
        graph_edges.append(
            {
                "source": str(graph_nodes[idx].get("node_id") or ""),
                "target": str(graph_nodes[idx + 1].get("node_id") or ""),
            }
        )

    trace_status = str(llm_summary.get("status") or "OK").strip().upper() or "OK"
    trace_reason = (
        str(llm_summary.get("reason_code") or "").strip()
        or str(fallback_reason_code or "").strip()
        or None
    )
    total_latency_ms = max(0, int(time.time() * 1000) - int(ts_ms))
    ledger.log_graph_run_trace(
        {
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": trace_status,
            "reason_code": trace_reason,
            "node_count": len(graph_nodes),
            "nodes_json": graph_nodes,
            "edges_json": graph_edges,
            "ts_ms": int(time.time() * 1000),
        }
    )


def _map_emotion_tag_to_vad(emotion_tag: str) -> tuple[float, float]:
    tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    if tag in {"HAPPY", "EXCITED"}:
        return 0.7, 0.8
    if tag in {"SAD"}:
        return -0.6, 0.35
    if tag in {"ANGRY"}:
        return -0.7, 0.85
    if tag in {"ANXIOUS"}:
        return -0.4, 0.75
    if tag in {"CALM"}:
        return 0.2, 0.2
    return 0.0, 0.3


def _build_chat_episodic_text(*, user_message: str, assistant_reply: str, session_id: str) -> str:
    user_text = _truncate_for_prompt(
        _repair_utf8_latin1_mojibake(str(user_message or "").strip()),
        limit=800,
    )
    assistant_text = _truncate_for_prompt(
        _repair_utf8_latin1_mojibake(str(assistant_reply or "").strip()),
        limit=1200,
    )
    return (
        f"[session={session_id}]\n"
        f"User: {user_text}\n"
        f"Natsume: {assistant_text}"
    ).strip()


def _persist_chat_episodic_memory(
    *,
    request_id: str,
    intent_id: str | None,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    emotion_context: Mapping[str, Any] | None,
    ts_ms: int,
) -> dict[str, Any]:
    text = _build_chat_episodic_text(
        user_message=user_message,
        assistant_reply=assistant_reply,
        session_id=session_id,
    )
    if not str(text).strip():
        return {
            "status": "SKIPPED",
            "reason_code": "EPISODIC_EMPTY_TEXT",
            "memory_id": None,
            "detail": "chat episodic text is empty",
        }

    tag = str((emotion_context or {}).get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    valence, arousal = _map_emotion_tag_to_vad(tag)
    evidence_refs = [
        f"chat://request/{request_id}",
        f"chat://session/{session_id}",
        f"chat://intent/{str(intent_id or request_id)}",
    ]
    result = get_episodic_store().write_memory(
        text=text,
        evidence_refs=evidence_refs,
        request_id=request_id,
        intent_id=str(intent_id or request_id),
        decay_profile_id="SLOW",
        visibility="internal",
        ts_ms=ts_ms,
        emotion_tag=tag,
        emotion_valence=valence,
        emotion_arousal=arousal,
    )
    return {
        "status": ("OK" if result.success else "DEGRADED"),
        "reason_code": result.reason_code,
        "memory_id": result.memory_id,
        "detail": result.detail,
        "emotion_tag": tag,
    }


# ---------------------------------------------------------------------------
# 鏍稿績閾捐矾鍏ュ彛: POST /v1/chat
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

        emotion_context = _resolve_chat_emotion_context(request_body=request_body)
        governance_snapshot = _build_chat_governance_snapshot(
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )

        # 2) Governor decision (intent validation + R1-R7 gates)
        engine = get_intent_engine()
        governor_result = engine.evaluate_intent(
            raw_intent=intent_draft,
            request_id=request_id,
            thread_id=request_body.thread_id,
            governance_snapshot=governance_snapshot,
        )

        decision = governor_result.get("decision")
        reason_code = governor_result.get("reason_code")
        intent_id = governor_result.get("intent_id")
        approval_token_ref = None
        tokens = governor_result.get("tokens")
        if isinstance(tokens, dict):
            approval_token_ref = tokens.get("approval_token_ref")
        _committed_token_detail = governor_result.get("committed_route_token")
        # M44锛氭彁鍙栧垎灞傛不鐞嗗鎵归€氶亾锛坅pproval_tier锛?
        _committed_token_detail = governor_result.get("committed_route_token")
        approval_tier: Optional[str] = None
        if isinstance(_committed_token_detail, dict):
            approval_tier = _committed_token_detail.get("approval_tier")

        # M45-E锛氫粠璇锋眰浣撴瀯寤烘儏鎰熶笂涓嬫枃锛屼緵 retrieval boost 鍜?LLM prompt 浣跨敤
        retrieval_summary: Dict[str, Any] = {}
        llm_summary: Dict[str, Any] = {}
        episodic_summary: Dict[str, Any] = {
            "status": "SKIPPED",
            "reason_code": "NOT_APPROVED",
            "memory_id": None,
            "detail": "governor decision is not APPROVE",
        }
        if decision == DECISION_APPROVE:
            retrieval_evidence_items: list[dict[str, Any]] = []
            scene_type, scene_type_source = _resolve_chat_scene_type(request_body=request_body)
            retrieval_enabled, retrieval_disabled_reason = _resolve_chat_retrieval_gate(scene_type=scene_type)
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
                    session_id=session_id,
                    top_k=retrieval_top_k,
                    ts_ms=ts_ms,
                    emotion_context=str(emotion_context.get("emotion_tag") or "") or None,
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
                    "routes": list(recall_output.get("routes") or []),
                    "route_rows": list(recall_output.get("route_rows") or []),
                    "route_errors": list(recall_output.get("route_errors") or []),
                    "candidate_count": int(recall_output.get("candidate_count") or 0),
                    "rerank_topk": len(rerank_topk_items),
                    "evidence_refs": list(evidence_pack.get("evidence_refs") or []),
                    "reason_chain": list(retrieval_result.output.get("reason_chain") or []),
                    "degraded": bool(retrieval_result.output.get("degraded", False)),
                }
            else:
                retrieval_summary = {
                    "status": "SKIPPED",
                    "reason_code": str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED"),
                    "route_count": 0,
                    "routes": [],
                    "route_rows": [],
                    "route_errors": [],
                    "candidate_count": 0,
                    "rerank_topk": 0,
                    "evidence_refs": [],
                    "reason_chain": [str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")],
                    "degraded": str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
                    not in {"HOST_RETRIEVAL_DISABLED", "HOST_CHAT_RETRIEVAL_DISABLED"},
                }

            render_target_lang = _infer_render_target_lang(user_message=request_body.message)
            node_style_bindings = _build_node_style_bindings(render_target_lang=render_target_lang)
            llm_prompt_payload = _build_chat_generation_prompt_payload(
                request_id=request_id,
                session_id=session_id,
                thread_id=request_body.thread_id,
                user_message=request_body.message,
                render_target_lang=render_target_lang,
                retrieval_summary=retrieval_summary,
                retrieval_evidence_items=retrieval_evidence_items,
                style_bindings=node_style_bindings,
                emotion_context=emotion_context if emotion_context else None,
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
                scene_type=scene_type,
                scene_type_source=scene_type_source,
                render_target_lang=render_target_lang,
            )
            dependency_statuses: dict[str, dict[str, Any]] = {
                node_id: {
                    "available": available,
                    "detail": ("model node runtime ready" if available else "model node runtime unavailable"),
                }
                for node_id in CHAT_GRAPH_NODE_ORDER
            }
            dependency_statuses[PROVIDER_GEMINI] = {
                "available": available,
                "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                "api_key": (llm_api_key or None),
            }
            if provider_name != PROVIDER_GEMINI:
                dependency_statuses[provider_name] = {
                    "available": available,
                    "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                    "api_key": (llm_api_key or None),
                }

            # Start policy clock at graph runtime entry.
            # Do not reuse request start ts_ms, otherwise retrieval latency is
            # wrongly counted into policy timeout budget.
            graph_start_ms = int(time.time() * 1000)
            graph_result = get_graph_runtime().run_graph(
                request_id=request_id,
                graph_config=graph_config,
                payload={
                    **llm_prompt_payload,
                    # M35-C锛氭敞鍏?committed_route_token 鍒?payload锛屼緵 graph_runtime 鎻愬彇鍒?initial_state
                    # Governor 宸插湪 evaluate_intent 涓紪璇戜护鐗岋紝姝ゅ閫忎紶缁?LangGraph 鍥?
                    "committed_route_token": governor_result.get("committed_route_token_ref"),
                    "committed_route_token_detail": governor_result.get("committed_route_token"),
                },
                dependency_statuses=dependency_statuses,
                ts_ms=graph_start_ms,
            )

            graph_output = dict(graph_result.output or {})
            is_chat_direct_path = str(scene_type or "").strip().upper() == SCENE_CHAT
            chat_intent_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_INTENT_SOURCE_NODE_ID
            chat_response_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_RENDER_NODE_ID
            _core_node, core_payload = _extract_chat_graph_gateway_payload(
                graph_output,
                node_id=chat_intent_node_id,
            )
            secondary_node, secondary_payload = _extract_chat_graph_gateway_payload(
                graph_output,
                node_id="core_reasoner_secondary",
            )
            render_node, render_payload = _extract_chat_graph_gateway_payload(
                graph_output,
                node_id=chat_response_node_id,
            )
            gateway_status = str(render_node.get("gateway_status") or "").strip().upper()
            gateway_reason = str(render_node.get("reason_code") or "").strip() or None

            core_gateway_status = str(_core_node.get("gateway_status") or "").strip().upper()
            secondary_gateway_status = str(secondary_node.get("gateway_status") or "").strip().upper()
            render_narrative = _extract_gateway_narrative(render_payload)
            core_narrative = _extract_gateway_narrative(core_payload)
            secondary_narrative = _extract_gateway_narrative(secondary_payload)

            primary_degraded = core_gateway_status not in {"OK", ""}
            effective_core_narrative = core_narrative
            if primary_degraded and secondary_narrative and secondary_gateway_status == "OK":
                effective_core_narrative = secondary_narrative

            narrative = render_narrative
            if not narrative and secondary_narrative and secondary_gateway_status == "OK" and primary_degraded:
                narrative = secondary_narrative
            if not narrative:
                narrative = effective_core_narrative or core_narrative or secondary_narrative
            narrative = _repair_utf8_latin1_mojibake(narrative)
            if not narrative:
                narrative = "系统有点忙，请稍后再试。"

            core_intent_draft = core_payload.get("intent_draft")
            if primary_degraded and secondary_gateway_status == "OK":
                secondary_intent = secondary_payload.get("intent_draft")
                if isinstance(secondary_intent, dict) and secondary_intent:
                    core_intent_draft = secondary_intent
            if isinstance(core_intent_draft, dict) and core_intent_draft:
                intent_draft = core_intent_draft

            style_bindings_payload_raw = llm_prompt_payload.get("style_bindings")
            style_bindings_payload = (
                dict(style_bindings_payload_raw)
                if isinstance(style_bindings_payload_raw, Mapping)
                else {}
            )
            graph_meta = dict(graph_config.get("graph_meta") or {})
            active_node_ids = [
                str(node_id).strip()
                for node_id in list(graph_meta.get("active_nodes") or [])
                if str(node_id).strip() in CHAT_GRAPH_NODE_ORDER
            ]
            payload_root = dict(graph_output.get("payload") or {})
            for node_id in CHAT_GRAPH_NODE_ORDER:
                if node_id in active_node_ids:
                    continue
                if isinstance(payload_root.get(node_id), Mapping):
                    active_node_ids.append(node_id)
            if not active_node_ids:
                active_node_ids = list(_resolve_scene_node_sequence(scene_type))
            node_summaries: list[dict[str, Any]] = []
            for node_index, node_id in enumerate(active_node_ids):
                node_entry, node_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=node_id)
                node_input: dict[str, Any] = {
                    "user_message": (request_body.message if node_index == 0 else None),
                    "retrieval_status": retrieval_summary.get("status"),
                    "retrieval_route_count": retrieval_summary.get("route_count"),
                    "upstream_node_ids": active_node_ids[:node_index],
                }
                node_style_raw = style_bindings_payload.get(node_id)
                node_style = dict(node_style_raw) if isinstance(node_style_raw, Mapping) else {}
                if node_style:
                    node_input["style_canon_id"] = node_style.get("canon_id")
                    node_input["style_citation"] = node_style.get("citation")
                if node_id == CHAT_DIRECT_NODE_ID:
                    node_input["response_mode"] = "direct_final_reply"
                if node_id == CHAT_RENDER_NODE_ID and not is_chat_direct_path:
                    node_input["core_narrative_zh"] = effective_core_narrative
                    node_input["core_intent_draft"] = core_intent_draft
                node_summaries.append(
                    {
                        "node_id": node_id,
                        "status": str(node_entry.get("gateway_status") or "").strip().upper() or "UNKNOWN",
                        "reason_code": (str(node_entry.get("reason_code") or "").strip() or None),
                        "provider": node_entry.get("provider"),
                        "model_id": node_entry.get("model_id"),
                        "input": node_input,
                        "output": node_payload,
                        "narrative_preview": _truncate_for_prompt(
                            _repair_utf8_latin1_mojibake(_extract_gateway_narrative(node_payload)),
                            limit=120,
                        ),
                    }
                )

            llm_summary = {
                "status": (gateway_status or str(graph_result.status)),
                "reason_code": (gateway_reason or graph_result.reason_code),
                "stage": str(graph_result.stage),
                "provider": (render_node.get("provider") or provider_name),
                "model_id": (render_node.get("model_id") or model_id),
                "model_profile_id": graph_output.get("model_profile_id"),
                "render_target_lang": str(llm_prompt_payload.get("render_target_lang") or ""),
                "latency_ms": None,
                "draft_only": bool(dict(render_payload.get("meta") or {}).get("draft_only", True)),
                "node_count": len(node_summaries),
                "nodes": node_summaries,
                "model_control": list(graph_output.get("model_control") or []),
                "context_pack_validation": dict(llm_prompt_payload.get("context_pack_validation") or {}),
            }

            retrieval_reason_code = str(retrieval_summary.get("reason_code") or "")
            if retrieval_summary.get("degraded") and retrieval_reason_code not in {
                "",
                "HOST_RETRIEVAL_DISABLED",
                "HOST_CHAT_RETRIEVAL_DISABLED",
            }:
                reason_code = reason_code or retrieval_reason_code
            graph_ok = str(graph_result.status).upper() == "OK"
            renderer_ok = gateway_status == "OK"
            if not graph_ok or not renderer_ok:
                reason_code = reason_code or gateway_reason or str(graph_result.reason_code or "") or "LLM_UNAVAILABLE"
                quality = {"stage": str(graph_result.stage), "reason_code": reason_code}
            else:
                quality = {"stage": "S_COMPLETE", "reason_code": None}

            try:
                _persist_chat_observability_rows(
                    ledger=get_ledger(),
                    request_id=request_id,
                    session_id=session_id,
                    llm_summary=llm_summary,
                    ts_ms=ts_ms,
                    fallback_reason_code=(reason_code or gateway_reason or graph_result.reason_code),
                )
            except Exception as exc:
                _logger.warning(
                    "chat observability persistence failed",
                    extra={"request_id": request_id, "error": str(exc)},
                )

            # M45-E锛歝hat 瀹屾垚鍚庡悜 Bond 鍐欏叆鎯呮劅浜掑姩璁板綍
            try:
                _etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
                _intensity = float(emotion_context.get("emotion_intensity") or 0.5)
                _update_kind = BondDynamicsWriter.classify_interaction(
                    text=request_body.message,
                    emotion_intensity=_intensity,
                )
                _bond_writer = BondDynamicsWriter(
                    ledger=get_ledger(),
                    stage_engine=get_bond_stage_engine(),
                )
                _bond_writer.apply_update(
                    request_id=request_id,
                    intent_id=str(intent_id or request_id),
                    session_id=session_id,
                    seq=1,
                    user_id=session_id,
                    topic_id=str(intent_draft.get("args", {}).get("text", request_body.message))[:64],
                    update_kind=_update_kind,
                    evidence_refs=[str(intent_id or request_id)],
                    emotion_tag=_etag,
                    emotion_intensity=_intensity,
                    ts_ms=ts_ms,
                )
            except Exception as exc:
                _logger.debug(
                    "bond emotion record skipped",
                    extra={"request_id": request_id, "error": str(exc)},
                )
            try:
                episodic_summary = _persist_chat_episodic_memory(
                    request_id=request_id,
                    intent_id=str(intent_id or request_id),
                    session_id=session_id,
                    user_message=request_body.message,
                    assistant_reply=narrative,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                )
            except Exception as exc:
                episodic_summary = {
                    "status": "DEGRADED",
                    "reason_code": "EPISODIC_WRITE_FAILED",
                    "memory_id": None,
                    "detail": str(exc),
                }
                _logger.debug(
                    "episodic memory write skipped",
                    extra={"request_id": request_id, "error": str(exc)},
                )
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
            approval_tier=approval_tier,  # M44锛氶€忓嚭鍒嗗眰娌荤悊瀹℃壒閫氶亾
            quality=quality,
            audit_refs={
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
                "retrieval": retrieval_summary,
                "llm": llm_summary,
                "context_pack_validation": llm_summary.get("context_pack_validation"),
                "emotion_context": dict(emotion_context),
                "episodic_memory": dict(episodic_summary),
            },
            timestamp=ts_ms,
        )

    except Exception as exc:
        _metrics.inc_error()
        _logger.error("Chat request failed", extra={"request_id": request_id, "error": str(exc)})
        return ChatResponse(
            request_id=request_id,
            narrative="系统有点忙，请稍后再试。",
            intent_draft=None,
            decision="REJECT",
            reason_code="INTERNAL_SERVER_ERROR",
            approval_token_ref=None,
            approval_tier=None,
            quality={"stage": "S_INTERNAL_ERROR", "reason_code": "INTERNAL_SERVER_ERROR"},
            audit_refs={"request_id": request_id},
            timestamp=ts_ms,
        )


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
# Web UI 闈㈡澘鍏ュ彛锛圡17: 宸插崌绾т负 React SPA锛屾棫 panel.html 宸茬Щ闄わ級
# ---------------------------------------------------------------------------
@app.get("/panel")
async def panel_page() -> "RedirectResponse":
    """Monitoring panel redirect endpoint."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/monitor/index.html", status_code=301)


# ---------------------------------------------------------------------------
# TASK-0079: Edge鈫扝ost 浜嬩欢鎵归噺涓婃姤绔偣
# ---------------------------------------------------------------------------
@app.post("/v1/edge/events", response_model=EdgeEventsResponse)
async def receive_edge_events(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    """鎺ユ敹 Edge 绔壒閲忎笂鎶ョ殑浜嬩欢锛屾牎楠屽苟鍐欏叆瀹¤閾俱€?

    TASK-0079: 鎵撻€?Edge鈫扝ost 浜嬩欢涓婃姤閫氶亾銆?
    浜嬩欢閫氳繃 cross_timeline 鐨?EdgeEventReport 缁撴瀯鍖栬В鏋愬悗鍐欏叆瀹¤銆?
    """
    ts_ms = int(time.time() * 1000)
    raw_events = request_body.events

    if not raw_events:
        return EdgeEventsResponse(
            success=True, total=0, accepted=0, rejected=0,
            detail="empty batch", timestamp=ts_ms,
        )

    # 閫愭潯鏍￠獙蹇呴渶瀛楁
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

        # 鍐欏叆 voice_event_log锛圗dge 浜嬩欢缁熶竴鍏ヨ〃锛?
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
# 鏍硅矾寰勯噸瀹氬悜鍒版枃妗?
# ---------------------------------------------------------------------------
@app.get("/")
async def root() -> Response:
    """Redirect root to the API docs."""
    return Response(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/docs"})


if __name__ == "__main__":
    import uvicorn

    # 閰嶇疆鏃ュ織
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # 鍚姩FastAPI鏈嶅姟鍣?
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=True,
    )


