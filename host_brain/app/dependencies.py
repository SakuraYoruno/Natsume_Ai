"""依赖注入工厂模块。

从 fastapi_app.py 拆分，集中管理所有全局单例的延迟初始化工厂函数。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Mapping, Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.async_queue import BondUpdateQueue
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.milestone_tracker import MilestoneTracker
from host_brain.bond.neo4j_client import InMemoryNeo4jBondClient, SqliteBondClient, build_neo4j_bond_client, load_neo4j_runtime_config  # 修复审计问题 #10
from host_brain.bond.shared_memory_index import SharedMemoryIndex
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.app.graph_config_builder import (
    AGENT_DISPATCH_HANDLER_ID,
    INTENT_AGENT_ENTRY_HANDLER_ID,
    SCENE_ROUTER_ENTRY_HANDLER_ID,
    SCENE_ROUTER_POST_CORE_HANDLER_ID,
    SUPERVISOR_DISPATCH_HANDLER_ID,
    CHAT_AGENT_HANDLER_ID,
    TRANSLATE_AGENT_HANDLER_ID,
    INTENT_AGENT_HANDLER_ID,
    JUDGE_AGENT_HANDLER_ID,
    RETRIEVAL_AGENT_HANDLER_ID,  # P11/P16 新增
    graph_scene_router_entry_handler,
    graph_scene_router_post_core_handler,
)
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.memory.episodic import EpisodicStore
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.observability.metrics import MetricsCollector
from host_brain.retrieval.pipeline import (
    RetrievalPipelineContract,
    build_retrieval_pipeline_from_env,
)
from host_brain.retrieval.orchestrator import RetrievalOrchestrator
from host_brain.reasoning.reflection_chain import ReflectionChain
from host_brain.channels.multimodal_processor import MultimodalProcessor
from host_brain.channels.tts_context_router import TtsContextRouter
from host_brain.app.multimodal_observer import MultimodalObserver
from host_brain.memory.memory_worth_evaluator import MemoryWorthEvaluator
from host_brain.memory.episodic_memory_graph import EpisodicMemoryGraph
from host_brain.memory.consolidation_trigger import ConsolidationTrigger
from host_brain.core.predictive_state import PredictiveStateEngine
from host_brain.core.meta_learning_loop import MetaLearningLoop
from host_brain.agents.planning_agent import PlanningAgent
from host_brain.skills.skill_composer import SkillComposer
from host_brain.app.runtime_task_registry import RuntimeTaskRegistry
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.runtime_capability_synthesizer import RuntimeCapabilityRegistry
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.mcp_skill_bridge import register_mcp_tools_into_skill_registry
from host_brain.skills.provider_catalog import register_configured_skill_providers
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog

# 新模块导入（优化计划新增模块）
from host_brain.app.llm.routing_matrix import RoutingMatrix
from host_brain.app.memory.memory_guard import MemoryGuard
from host_brain.app.memory.state_arbiter import StateArbiter
from host_brain.app.memory.state_write_arbiter import StateWriteArbiter
from host_brain.app.memory.distillation_engine import DistillationEngine
from host_brain.app.recall.semantic_recall import SemanticRecaller
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.app.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.app.context.topic_ledger import SessionTopicLedgerStore
from host_brain.conversation.history import ConversationHistory
from host_brain.conversation.store import TurnStore

_logger = logging.getLogger("Natsume.dependencies")
_EDGE_RPC_DEPENDENCY_SERVICE_IDS = (
    "edge.rpc.gateway",
    "edge.rpc.asr",
    "edge.rpc.tts",
    "edge.rpc.barge_in",
    "edge.rpc.resource_gate",
)
_EDGE_RPC_COMPONENT_BY_SERVICE_ID = {
    "edge.rpc.asr": "asr",
    "edge.rpc.tts": "tts",
    "edge.rpc.barge_in": "voice_pipeline",
    "edge.rpc.resource_gate": "resource_gate",
}

# 全局单例
_ledger: Optional[SQLiteLedger] = None
_intent_engine: Optional[IntentDecisionEngine] = None
_skill_registry: Optional[SkillRegistry] = None
_policy_cache: Optional[GovernorPolicyCache] = None
_mcp_tool_registry: Optional[MCPToolRegistry] = None
_mcp_tool_broker: Optional[MCPToolBroker] = None
_tool_catalog: Optional[ToolCatalog] = None
_tool_broker: Optional[ToolBroker] = None
_bond_stage_engine: Optional[BondStageEngine] = None
_bond_update_queue: Optional[BondUpdateQueue] = None
_episodic_store: Optional[EpisodicStore] = None
_retrieval_pipeline: Optional[RetrievalPipelineContract] = None
_retrieval_orchestrator: Optional[RetrievalOrchestrator] = None
_obs_metrics_collector: Optional[MetricsCollector] = None
_graph_runtime: Optional[GraphRuntimeContract] = None

# 新模块单例
_routing_matrix: Optional[RoutingMatrix] = None
_memory_guard: Optional[MemoryGuard] = None
_state_arbiter: Optional[StateArbiter] = None
_state_write_arbiter: Optional[StateWriteArbiter] = None
_distillation_engine: Optional[DistillationEngine] = None
_semantic_recaller: Optional[SemanticRecaller] = None
_prompt_template_store: Optional[PromptTemplateStore] = None
_context_compressor: Optional[Any] = None
_emotion_timeline: Optional[Any] = None
_bond_aware_style_engine: Optional[Any] = None
_reflection_chain: Optional[ReflectionChain] = None
_tts_context_router: Optional[TtsContextRouter] = None
_multimodal_processor: Optional[MultimodalProcessor] = None
_multimodal_observer: Optional[MultimodalObserver] = None
_memory_worth_evaluator: Optional[MemoryWorthEvaluator] = None
_episodic_memory_graph: Optional[EpisodicMemoryGraph] = None
_consolidation_trigger: Optional[ConsolidationTrigger] = None
_predictive_state_engine: Optional[PredictiveStateEngine] = None
_meta_learning_loop: Optional[MetaLearningLoop] = None
_planning_agent: Optional[PlanningAgent] = None
_skill_composer: Optional[SkillComposer] = None
_runtime_task_registry: Optional[RuntimeTaskRegistry] = None
_capability_gap_telemetry: Optional[CapabilityGapTelemetry] = None
_capability_forge_service: Optional[CapabilityForgeService] = None
_capability_forge_token_service: Optional[TokenService] = None
_runtime_capability_registry: Optional[RuntimeCapabilityRegistry] = None
_shared_memory_index: Optional[SharedMemoryIndex] = None
_milestone_tracker: Optional[MilestoneTracker] = None
_conversation_turn_store: Optional[TurnStore] = None
_conversation_history: Optional[ConversationHistory] = None
_session_topic_ledger_store: Optional[SessionTopicLedgerStore] = None
_session_tactic_ledger_store: Optional[SessionTacticLedgerStore] = None

# ── 进程级 HardState 存储（ModeTransitionService 依赖） ──
_hard_state_lock = __import__("threading").Lock()
_hard_state: Optional[Any] = None  # 实际类型: HardState

# ── 进程级 ActivityMonitor 引用（用于消息链路通知 user_interaction） ──
_activity_monitor_ref: Optional[Any] = None
_activity_monitor_lock = __import__("threading").Lock()


def set_activity_monitor(monitor) -> None:
    """注册进程级 ActivityMonitor 实例（由 http_hooks 启动时调用）。"""
    global _activity_monitor_ref
    _activity_monitor_ref = monitor


def get_activity_monitor(*, create_if_missing: bool = False, ledger: Any | None = None):
    """获取进程级 ActivityMonitor，可选在缺失时懒启动。"""
    global _activity_monitor_ref
    monitor = _activity_monitor_ref
    if monitor is not None or not create_if_missing:
        return monitor

    with _activity_monitor_lock:
        monitor = _activity_monitor_ref
        if monitor is not None:
            return monitor

        from host_brain.core.activity_monitor import ActivityMonitor  # noqa: PLC0415

        mode_service = None
        try:
            from host_brain.app.obs_routes_sysmode import _get_mode_transition_service  # noqa: PLC0415

            mode_service = _get_mode_transition_service()
        except Exception as exc:
            _logger.warning("获取 ModeTransitionService 失败，改为本地初始化: %s", exc)

        if mode_service is None:
            from host_brain.core.mode_transition import ModeTransitionService  # noqa: PLC0415

            mode_service = ModeTransitionService(
                get_hard_state=get_hard_state,
                set_sys_mode=set_sys_mode,
                ledger=ledger if ledger is not None else get_ledger(),
            )

        monitor = ActivityMonitor(mode_transition_service=mode_service)
        monitor.start()
        _activity_monitor_ref = monitor
        _logger.info("ActivityMonitor 已注册到 dependencies")
        return monitor


def notify_user_interaction() -> None:
    """通知 ActivityMonitor 有用户互动（消息输入、语音输入等）。

    由 input_routes / fastapi_app 在收到用户消息时调用，
    触发 sleep→normal 唤醒 和 空闲计时器重置。
    安全降级：如果 ActivityMonitor 未初始化则静默跳过。
    """
    monitor = get_activity_monitor()
    if monitor is not None:
        try:
            monitor.notify_user_interaction()
        except Exception:
            pass  # 降级：通知失败不影响消息处理


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def get_ledger() -> SQLiteLedger:
    """获取 SQLite 账本实例（延迟初始化，支持跨线程访问）。"""
    global _ledger
    if _ledger is None:
        db_path = os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
        _ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
    return _ledger


def get_prompt_template_store() -> PromptTemplateStore:
    global _prompt_template_store
    ledger = get_ledger()
    if _prompt_template_store is None or getattr(_prompt_template_store, "_ledger", None) is not ledger:
        _prompt_template_store = PromptTemplateStore(ledger=ledger)
    return _prompt_template_store


def get_conversation_turn_store() -> TurnStore:
    global _conversation_turn_store
    if _conversation_turn_store is None:
        _conversation_turn_store = TurnStore(get_ledger())
    return _conversation_turn_store


def get_conversation_history() -> ConversationHistory:
    global _conversation_history
    if _conversation_history is None:
        _conversation_history = ConversationHistory(get_conversation_turn_store())
    return _conversation_history


def get_session_topic_ledger_store() -> SessionTopicLedgerStore:
    global _session_topic_ledger_store
    if _session_topic_ledger_store is None:
        _session_topic_ledger_store = SessionTopicLedgerStore(get_ledger())
    return _session_topic_ledger_store


def get_session_tactic_ledger_store() -> SessionTacticLedgerStore:
    global _session_tactic_ledger_store
    if _session_tactic_ledger_store is None:
        _session_tactic_ledger_store = SessionTacticLedgerStore(get_ledger())
    return _session_tactic_ledger_store


def _resolve_episodic_embedding_binding() -> tuple[Any, str, str]:
    from host_brain.retrieval.embedding import (  # noqa: PLC0415
        _invoke_embedding_provider,
        build_embedding_provider_registry,
        load_embedding_runtime_config,
        resolve_embedding_route,
    )

    cfg_result = load_embedding_runtime_config(env=os.environ)
    if not cfg_result.success or cfg_result.config is None:
        raise RuntimeError(f"episodic embedding runtime config invalid: {cfg_result.as_dict()}")
    node_id = str(
        os.environ.get("HOST_RETR_NODE_ID")
        or os.environ.get("Natsume_HOST_NODE_ID")
        or "natsu-node-01"
    ).strip() or "natsu-node-01"
    route = resolve_embedding_route(cfg_result.config, node_id=node_id)
    provider_registry = build_embedding_provider_registry(env=os.environ)
    provider = provider_registry.get(route.provider)
    if provider is None:
        raise RuntimeError(f"episodic embedding provider unavailable: {route.provider}")

    def _embed(text: str) -> tuple[list[float], str]:
        return _invoke_embedding_provider(provider, str(text or "").strip(), route)

    return _embed, str(route.model_id), str(route.provider)


def _build_episodic_embedding_callable() -> Any:
    embedder, _model_id, _provider = _resolve_episodic_embedding_binding()
    return embedder


def get_episodic_store() -> EpisodicStore:
    global _episodic_store
    if _episodic_store is None:
        from host_brain.memory.chroma_client import (  # noqa: PLC0415
            PersistentChromaClient,
            load_chroma_runtime_config,
        )

        cfg_result = load_chroma_runtime_config(env=os.environ)
        if not cfg_result.success or cfg_result.config is None:
            raise RuntimeError(f"episodic chroma runtime config invalid: {cfg_result.as_dict()}")
        _embedding_fn, _embedding_model_id, _embedding_provider = _resolve_episodic_embedding_binding()
        _episodic_store = EpisodicStore(
            ledger=get_ledger(),
            chroma_client=PersistentChromaClient.from_runtime_config(cfg_result.config),
            embedding_fn=_embedding_fn,
            embedding_model_id_hint=_embedding_model_id,
            embedding_provider_hint=_embedding_provider,
        )
    return _episodic_store


def get_retrieval_pipeline() -> RetrievalPipelineContract:
    global _retrieval_pipeline
    if _retrieval_pipeline is None:
        node_id = str(
            os.environ.get("HOST_RETR_NODE_ID")
            or os.environ.get("Natsume_HOST_NODE_ID")
            or "natsu-node-01"
        ).strip() or "natsu-node-01"
        _retrieval_pipeline = build_retrieval_pipeline_from_env(
            env=os.environ,
            ledger=get_ledger(),
            node_id=node_id,
        )
    return _retrieval_pipeline


def get_retrieval_orchestrator() -> RetrievalOrchestrator:
    global _retrieval_orchestrator
    pipeline = get_retrieval_pipeline()
    cache_enabled = _parse_bool_env("HOST_RETRIEVAL_CACHE_ENABLED", default=False)
    cache_ttl_ms = _parse_int_env(
        "HOST_RETRIEVAL_CACHE_TTL_MS",
        default=3000,
        minimum=0,
        maximum=600000,
    )
    cache_max_entries = _parse_int_env(
        "HOST_RETRIEVAL_CACHE_MAX_ENTRIES",
        default=256,
        minimum=1,
        maximum=20000,
    )
    cache_signature = (
        bool(cache_enabled and cache_ttl_ms > 0 and cache_max_entries > 0),
        int(max(0, cache_ttl_ms)),
        int(max(1, cache_max_entries)),
    )
    if (
        _retrieval_orchestrator is None
        or _retrieval_orchestrator.pipeline is not pipeline
        or _retrieval_orchestrator.cache_config_signature != cache_signature
    ):
        _retrieval_orchestrator = RetrievalOrchestrator(
            pipeline=pipeline,
            cache_enabled=cache_enabled,
            cache_ttl_ms=cache_ttl_ms,
            cache_max_entries=cache_max_entries,
        )
    return _retrieval_orchestrator


def get_graph_runtime() -> GraphRuntimeContract:
    global _graph_runtime
    if _graph_runtime is None:
        _graph_runtime = GraphRuntimeContract(
            ledger=get_ledger(),
            default_timeout_ms=5_000,
        )
        _graph_runtime.register_node_handler(
            SCENE_ROUTER_ENTRY_HANDLER_ID,
            graph_scene_router_entry_handler,
        )
        _graph_runtime.register_node_handler(
            SCENE_ROUTER_POST_CORE_HANDLER_ID,
            graph_scene_router_post_core_handler,
        )
        # M-MA1：注册 Supervisor 分派 handler（trusted handler，可写 __next_node__）
        from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler  # noqa: PLC0415
        _graph_runtime.register_node_handler(
            SUPERVISOR_DISPATCH_HANDLER_ID,
            supervisor_dispatch_handler,
        )
        # P16：agent_dispatch_handler 已删除（P3 孤岛模块），graph_executor 直接分派
        # P4：Intent 图入口（透明子图实现）
        from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler  # noqa: PLC0415
        _graph_runtime.register_node_handler(INTENT_AGENT_ENTRY_HANDLER_ID, intent_agent_entry_handler)
        # M-MA5：注册各 Agent Handler（只产出工件，不写 __next_node__）
        from host_brain.app.handlers.chat_agent_handler import chat_agent_handler  # noqa: PLC0415
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler  # noqa: PLC0415
        from host_brain.app.handlers.intent_agent_handler import intent_agent_handler  # noqa: PLC0415
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler  # noqa: PLC0415
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler  # noqa: PLC0415
        _graph_runtime.register_node_handler(CHAT_AGENT_HANDLER_ID, chat_agent_handler)
        _graph_runtime.register_node_handler(TRANSLATE_AGENT_HANDLER_ID, translate_agent_handler)
        _graph_runtime.register_node_handler(INTENT_AGENT_HANDLER_ID, intent_agent_handler)
        _graph_runtime.register_node_handler(JUDGE_AGENT_HANDLER_ID, judge_agent_handler)
        _graph_runtime.register_node_handler(RETRIEVAL_AGENT_HANDLER_ID, retrieval_agent_handler)  # P11/P16 补充注册
    return _graph_runtime


def get_obs_metrics_collector() -> MetricsCollector:
    global _obs_metrics_collector
    if _obs_metrics_collector is None:
        _obs_metrics_collector = MetricsCollector(ledger=get_ledger())
    return _obs_metrics_collector


def get_tool_catalog() -> ToolCatalog:
    global _tool_catalog
    if _tool_catalog is None:
        _tool_catalog = ToolCatalog()
    return _tool_catalog


def get_tool_broker() -> ToolBroker:
    global _tool_broker
    if _tool_broker is None:
        token_service = TokenService()
        _tool_broker = ToolBroker(
            catalog=get_tool_catalog(),
            token_service=token_service,
            ledger=get_ledger(),
        )
    return _tool_broker


def _resolve_mcp_registry_db_path() -> str:
    return (
        str(os.environ.get("HOST_MCP_REGISTRY_DB") or "host_brain/data/mcp_registry.sqlite3").strip()
        or "host_brain/data/mcp_registry.sqlite3"
    )


def get_mcp_tool_registry() -> MCPToolRegistry:
    global _mcp_tool_registry, _mcp_tool_broker
    db_path = _resolve_mcp_registry_db_path()
    current_path = str(getattr(_mcp_tool_registry, "db_path", "") or "")
    if _mcp_tool_registry is None or current_path != db_path:
        if _mcp_tool_registry is not None:
            try:
                _mcp_tool_registry.close()
            except Exception:
                pass
        _mcp_tool_registry = MCPToolRegistry(
            db_path=db_path,
            check_same_thread=False,
        )
        _mcp_tool_broker = None
    return _mcp_tool_registry


def get_mcp_tool_broker() -> MCPToolBroker:
    global _mcp_tool_broker
    registry = get_mcp_tool_registry()
    ledger = get_ledger()
    if (
        _mcp_tool_broker is None
        or _mcp_tool_broker.registry is not registry
        or _mcp_tool_broker.ledger is not ledger
    ):
        _mcp_tool_broker = MCPToolBroker(
            registry=registry,
            ledger=ledger,
        )
    return _mcp_tool_broker


def _sync_mcp_skills_into_registry(skill_registry: SkillRegistry) -> None:
    if not _parse_bool_env("HOST_SKILL_MCP_BRIDGE_ENABLED", default=True):
        return
    try:
        register_mcp_tools_into_skill_registry(
            skill_registry=skill_registry,
            mcp_registry=get_mcp_tool_registry(),
            broker=get_mcp_tool_broker(),
        )
    except Exception as exc:
        _logger.warning("mcp skill bridge sync skipped: %s", exc)


def get_bond_stage_engine() -> BondStageEngine:
    global _bond_stage_engine
    if _bond_stage_engine is None:
        # 修复问题F：Bond 积分阈值可配置化
        # 支持通过环境变量覆盖默认阈值，格式：JSON 字符串
        # 示例：HOST_BOND_STAGE_THRESHOLDS='{"STRANGER":0,"ACQUAINTANCE":5,"FRIEND":20}'
        import json as _json
        from host_brain.bond.bond_stage_engine import DEFAULT_STAGE_THRESHOLDS, DEFAULT_SCORE_MAP

        stage_thresholds = dict(DEFAULT_STAGE_THRESHOLDS)
        score_map = dict(DEFAULT_SCORE_MAP)

        _thresholds_env = str(os.environ.get("HOST_BOND_STAGE_THRESHOLDS") or "").strip()
        if _thresholds_env:
            try:
                _parsed = _json.loads(_thresholds_env)
                if isinstance(_parsed, dict):
                    stage_thresholds.update({str(k): int(v) for k, v in _parsed.items()})
                    _logger.info("Bond 阶段阈值已从环境变量覆盖: %s", _thresholds_env[:200])
            except Exception as exc:
                _logger.warning("HOST_BOND_STAGE_THRESHOLDS 解析失败，使用默认值: %s", exc)

        _score_map_env = str(os.environ.get("HOST_BOND_SCORE_MAP") or "").strip()
        if _score_map_env:
            try:
                _parsed = _json.loads(_score_map_env)
                if isinstance(_parsed, dict):
                    score_map.update({str(k): int(v) for k, v in _parsed.items()})
                    _logger.info("Bond 积分映射已从环境变量覆盖: %s", _score_map_env[:200])
            except Exception as exc:
                _logger.warning("HOST_BOND_SCORE_MAP 解析失败，使用默认值: %s", exc)

        _bond_stage_engine = BondStageEngine(
            ledger=get_ledger(),
            stage_thresholds=stage_thresholds,
            score_map=score_map,
        )
    return _bond_stage_engine


def get_shared_memory_index() -> SharedMemoryIndex:
    global _shared_memory_index
    episodic_store = None
    try:
        episodic_store = get_episodic_store()
    except Exception:
        episodic_store = None
    if _shared_memory_index is None or getattr(_shared_memory_index, "_store", None) is not episodic_store:
        _shared_memory_index = SharedMemoryIndex(store=episodic_store)
    return _shared_memory_index


def get_milestone_tracker() -> MilestoneTracker:
    global _milestone_tracker
    if _milestone_tracker is None:
        _milestone_tracker = MilestoneTracker()
    return _milestone_tracker


def get_bond_neo4j_client():
    """根据生产配置选择 Bond SSOT 客户端。

    优先级：
    1. HOST_BOND_BACKEND=sqlite → SqliteBondClient
    2. HOST_BOND_BACKEND=memory → InMemoryNeo4jBondClient
    3. HOST_BOND_BACKEND=neo4j → Neo4jBondClient
    4. 未显式指定时：prod + BOND_NEO4J_ENABLED=true → Neo4jBondClient
    5. 其它情况回退 sqlite / memory
    """
    backend_raw = str(os.environ.get("HOST_BOND_BACKEND") or "").strip().lower()
    runtime_env = str(os.environ.get("Natsume_ENV") or "dev").strip().lower()
    sqlite_path = str(os.environ.get("HOST_BOND_SQLITE_PATH") or "").strip()

    if backend_raw == "sqlite" or (sqlite_path and backend_raw != "neo4j"):
        return SqliteBondClient(db_path=sqlite_path or None)
    if backend_raw == "memory":
        return InMemoryNeo4jBondClient()

    config_result = load_neo4j_runtime_config()
    if config_result.success and config_result.config is not None:
        prefers_neo4j = backend_raw == "neo4j" or (not backend_raw and runtime_env == "prod")
        if prefers_neo4j:
            try:
                client = build_neo4j_bond_client(config=config_result.config)
                if client is not None:
                    return client
            except Exception:
                pass

    if sqlite_path:
        return SqliteBondClient(db_path=sqlite_path or None)
    return InMemoryNeo4jBondClient()


def get_bond_update_queue() -> BondUpdateQueue:
    global _bond_update_queue
    max_queue_size = _parse_int_env(
        "HOST_BOND_QUEUE_MAX_SIZE",
        default=1024,
        minimum=8,
        maximum=100000,
    )
    worker_enabled = _parse_bool_env("HOST_BOND_QUEUE_ENABLED", default=True)
    signature = (int(max_queue_size), bool(worker_enabled))

    # 修复审计问题 #10：writer_factory 使用 get_bond_neo4j_client() 而非硬编码
    # InMemoryNeo4jBondClient，确保 Bond 事件写入持久化客户端（SQLite 或内存）。
    # 客户端类型由环境变量 HOST_BOND_BACKEND / HOST_BOND_SQLITE_PATH 控制：
    #   HOST_BOND_BACKEND=sqlite  → SqliteBondClient（持久化，推荐生产环境）
    #   HOST_BOND_BACKEND=memory  → InMemoryNeo4jBondClient（内存，重启丢失）
    def _make_writer() -> BondDynamicsWriter:
        return BondDynamicsWriter(
            ledger=get_ledger(),
            neo4j_client=get_bond_neo4j_client(),
            stage_engine=get_bond_stage_engine(),
        )

    if _bond_update_queue is None:
        _bond_update_queue = BondUpdateQueue(
            writer_factory=_make_writer,
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_bond_update_queue, "_config_signature", signature)
        return _bond_update_queue

    current_signature = getattr(_bond_update_queue, "_config_signature", None)
    if current_signature != signature:
        _bond_update_queue.shutdown()
        _bond_update_queue = BondUpdateQueue(
            writer_factory=_make_writer,
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_bond_update_queue, "_config_signature", signature)
    return _bond_update_queue


def get_skill_registry() -> SkillRegistry:
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = build_builtin_skill_registry()
        register_configured_skill_providers(
            _skill_registry,
            parse_bool_env=_parse_bool_env,
            logger=_logger,
        )
        # Hub Skills 加载：扫描 host_brain/skills/hub/ 下的外部 Skill
        if _parse_bool_env("HOST_SKILL_HUB_ENABLED", default=True):
            try:
                from host_brain.skills.hub.hub_loader import load_hub_skills  # noqa: PLC0415
                from host_brain.skills.executor_registry import get_builtin_tool_executor_registry  # noqa: PLC0415
                _hub_result = load_hub_skills(
                    _skill_registry,
                    executor_registry=get_builtin_tool_executor_registry(),
                )
                if _hub_result.registered_count > 0:
                    _logger.info(
                        "Hub Skills loaded: %d registered, %d skipped, %d errors",
                        _hub_result.registered_count,
                        _hub_result.skipped_count,
                        _hub_result.error_count,
                    )
            except Exception as _hub_exc:
                _logger.warning("Hub Skills loading failed (degraded): %s", _hub_exc)
    _sync_mcp_skills_into_registry(_skill_registry)
    return _skill_registry


def refresh_skill_registry() -> SkillRegistry:
    """Clear cached registry-dependent singletons and rebuild skills."""
    global _skill_registry, _intent_engine
    _skill_registry = None
    _intent_engine = None
    return get_skill_registry()


def get_policy_cache() -> GovernorPolicyCache:
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = GovernorPolicyCache(ttl_ms=60_000)
    return _policy_cache


def get_intent_engine() -> IntentDecisionEngine:
    global _intent_engine
    if _intent_engine is None:
        _intent_engine = IntentDecisionEngine(
            ledger=get_ledger(),
            registry=get_skill_registry(),
            policy_cache=get_policy_cache(),
        )
    return _intent_engine


# ---------------------------------------------------------------------------
# 新模块工厂函数（优化计划新增）
# ---------------------------------------------------------------------------

def get_routing_matrix() -> RoutingMatrix:
    """获取模型路由矩阵单例（延迟初始化）。"""
    global _routing_matrix
    if _routing_matrix is None:
        ledger = get_ledger()

        def _route_log_writer(decision: object) -> None:
            try:
                import dataclasses
                import enum
                if dataclasses.is_dataclass(decision):
                    raw = dataclasses.asdict(decision)
                    payload = {
                        k: (v.value if isinstance(v, enum.Enum) else v)
                        for k, v in raw.items()
                    }
                else:
                    payload = {"raw": str(decision)}
                # 使用 SQLiteLedger 的路由日志专用方法，参数为 row: dict
                ledger.log_llm_route(row=payload)
            except Exception as exc:
                _logger.warning("路由日志写入失败: %s", exc)

        _routing_matrix = RoutingMatrix(route_log_writer=_route_log_writer)
        _logger.info("RoutingMatrix 已初始化")
    return _routing_matrix


def get_memory_guard() -> MemoryGuard:
    """获取记忆守卫单例（延迟初始化）。"""
    global _memory_guard
    if _memory_guard is None:
        ledger = get_ledger()

        def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_memory_op(row={"op_type": event, **detail})
            except Exception as exc:
                _logger.warning("MemoryGuard 审计写入失败: %s", exc)

        _memory_guard = MemoryGuard(audit_writer=_audit_writer)
        _logger.info("MemoryGuard 已初始化")
    return _memory_guard


def get_state_arbiter() -> StateArbiter:
    """获取状态仲裁器单例（延迟初始化）。"""
    global _state_arbiter
    if _state_arbiter is None:
        ledger = get_ledger()

        async def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_error(
                    request_id=str(detail.get("request_id") or "state_arbiter"),
                    stage=event,
                    reason_code=event,
                    detail=str(detail),
                )
            except Exception as exc:
                _logger.warning("StateArbiter 审计写入失败: %s", exc)

        _state_arbiter = StateArbiter(audit_writer=_audit_writer)
        _logger.info("StateArbiter 已初始化（未注册状态源，需业务层注入）")
    return _state_arbiter


def get_state_write_arbiter() -> StateWriteArbiter:
    """获取状态写权限仲裁器单例（延迟初始化）。"""
    global _state_write_arbiter
    if _state_write_arbiter is None:
        ledger = get_ledger()

        def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_error(
                    request_id=str(detail.get("request_id") or "state_write_arbiter"),
                    stage=event,
                    reason_code=str(detail.get("reason_code") or event),
                    detail=str(detail),
                )
            except Exception as exc:
                _logger.warning("StateWriteArbiter 审计写入失败: %s", exc)

        _state_write_arbiter = StateWriteArbiter(audit_writer=_audit_writer)
        _logger.info("StateWriteArbiter 已初始化（SoulState 默认仅 persona_core 可写）")
    return _state_write_arbiter


# ---------------------------------------------------------------------------
# 蒸馏引擎 + 语义召回器工厂
# ---------------------------------------------------------------------------


def get_distillation_engine() -> DistillationEngine:
    """获取蒸馏引擎单例（延迟初始化）。"""
    global _distillation_engine
    if _distillation_engine is None:
        from host_brain.app.memory.episodic_store import EpisodicStore as _AppEpisodicStore  # noqa: PLC0415
        # 尝试获取 app 层 EpisodicStore（若不可用则创建新实例）
        try:
            _ep_store = get_episodic_store()
            # 若是旧版 EpisodicStore（host_brain.memory.episodic），包装为新版
            if not isinstance(_ep_store, _AppEpisodicStore):
                _ep_store = _AppEpisodicStore(
                    memory_guard=get_memory_guard(),
                    ledger=get_ledger(),
                )
        except Exception:
            _ep_store = _AppEpisodicStore(
                memory_guard=get_memory_guard(),
                ledger=get_ledger(),
            )
        _distillation_engine = DistillationEngine(
            episodic_store=_ep_store,
            memory_guard=get_memory_guard(),
            state_arbiter=get_state_arbiter(),
            ledger=get_ledger(),
        )
        _logger.info("DistillationEngine 已初始化")
    return _distillation_engine


def get_semantic_recaller() -> SemanticRecaller:
    """获取语义召回器单例（延迟初始化）。"""
    global _semantic_recaller
    if _semantic_recaller is None:
        import os as _os  # noqa: PLC0415
        use_mock = _parse_bool_env("SEMANTIC_RECALL_USE_MOCK", default=True)
        chroma_host = str(_os.environ.get("CHROMA_HOST") or "localhost").strip()
        chroma_port = _parse_int_env("CHROMA_PORT", default=8000, minimum=1, maximum=65535)
        top_k = _parse_int_env("SEMANTIC_RECALL_TOP_K", default=5, minimum=1, maximum=20)
        threshold_raw = str(_os.environ.get("SEMANTIC_RECALL_THRESHOLD") or "0.70").strip()
        try:
            threshold = float(threshold_raw)
        except ValueError:
            threshold = 0.70
        _semantic_recaller = SemanticRecaller(
            collection_name="episodic_memory",
            top_k=top_k,
            similarity_threshold=threshold,
            use_mock=use_mock,
            chroma_host=chroma_host,
            chroma_port=chroma_port,
        )
        _logger.info(
            "SemanticRecaller 已初始化（use_mock=%s, chroma=%s:%d, top_k=%d, threshold=%.2f）",
            use_mock, chroma_host, chroma_port, top_k, threshold,
        )
    return _semantic_recaller


class _BondAwareStyleResultView:
    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        raw = dict(payload or {})
        self._as_dict = dict(raw.get("as_dict") or {})
        self.prompt_injection = str(raw.get("prompt_injection") or "")
        self.forbidden_patterns = tuple(str(item) for item in list(raw.get("forbidden_patterns") or []))
        self.intimacy_level = float(raw.get("intimacy_level") or self._as_dict.get("intimacy_level") or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._as_dict)


class _BondAwareStyleEngineProxy:
    def generate(
        self,
        *,
        bond_stage: str,
        mood_state: str,
        rift_active: bool = False,
        bond_trend: str = "",
        texture_hint: str = "",
        shared_memory_hints: list[str] | None = None,
        intimacy_score: float = 0.0,
        natsume_mood: str = "NEUTRAL",
        rift_severity: str = "medium",
        rift_progress: float = 0.0,
        render_target_lang: str = "zh",
    ) -> _BondAwareStyleResultView:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        payload = get_prefrontal_agent().resolve_bond_style_payload(
            bond_stage=str(bond_stage or "STRANGER"),
            intimacy_score=float(intimacy_score or 0.0),
            mood_state=str(mood_state or "neutral"),
            natsume_mood=str(natsume_mood or "NEUTRAL"),
            rift_active=bool(rift_active),
            rift_severity=str(rift_severity or "medium"),
            rift_progress=float(rift_progress or 0.0),
            bond_trend=str(bond_trend or ""),
            texture_hint=str(texture_hint or ""),
            shared_memory_hints=list(shared_memory_hints or []) or None,
            render_target_lang=str(render_target_lang or "zh"),
        )
        return _BondAwareStyleResultView(payload)


def get_bond_aware_style_engine() -> Any:
    global _bond_aware_style_engine
    if _bond_aware_style_engine is None:
        _bond_aware_style_engine = _BondAwareStyleEngineProxy()
    return _bond_aware_style_engine


def get_reflection_chain() -> ReflectionChain:
    global _reflection_chain
    if _reflection_chain is None:
        _reflection_chain = ReflectionChain(use_llm=True)
    return _reflection_chain


def get_tts_context_router() -> TtsContextRouter:
    global _tts_context_router
    if _tts_context_router is None:
        _tts_context_router = TtsContextRouter()
    return _tts_context_router


def get_multimodal_processor() -> MultimodalProcessor:
    global _multimodal_processor
    if _multimodal_processor is None:
        _multimodal_processor = MultimodalProcessor()
    return _multimodal_processor


def get_multimodal_observer() -> MultimodalObserver:
    global _multimodal_observer
    if _multimodal_observer is None:
        _multimodal_observer = MultimodalObserver()
    return _multimodal_observer


def get_memory_worth_evaluator() -> MemoryWorthEvaluator:
    global _memory_worth_evaluator
    if _memory_worth_evaluator is None:
        _memory_worth_evaluator = MemoryWorthEvaluator()
    return _memory_worth_evaluator


def get_episodic_memory_graph() -> EpisodicMemoryGraph:
    global _episodic_memory_graph
    if _episodic_memory_graph is None:
        _episodic_memory_graph = EpisodicMemoryGraph()
    return _episodic_memory_graph


def get_consolidation_trigger() -> ConsolidationTrigger:
    global _consolidation_trigger
    if _consolidation_trigger is None:
        _consolidation_trigger = ConsolidationTrigger()
    return _consolidation_trigger


def get_predictive_state_engine() -> PredictiveStateEngine:
    global _predictive_state_engine
    if _predictive_state_engine is None:
        _predictive_state_engine = PredictiveStateEngine()
    return _predictive_state_engine


def get_meta_learning_loop() -> MetaLearningLoop:
    global _meta_learning_loop
    if _meta_learning_loop is None:
        _meta_learning_loop = MetaLearningLoop()
    return _meta_learning_loop


def get_planning_agent() -> PlanningAgent:
    global _planning_agent
    if _planning_agent is None:
        _planning_agent = PlanningAgent()
    return _planning_agent


def get_runtime_task_registry() -> RuntimeTaskRegistry:
    global _runtime_task_registry
    if _runtime_task_registry is None:
        _runtime_task_registry = RuntimeTaskRegistry()
    return _runtime_task_registry


def get_runtime_capability_registry() -> RuntimeCapabilityRegistry:
    global _runtime_capability_registry
    if _runtime_capability_registry is None:
        _runtime_capability_registry = RuntimeCapabilityRegistry()
    return _runtime_capability_registry


def get_capability_gap_telemetry() -> CapabilityGapTelemetry:
    global _capability_gap_telemetry
    if _capability_gap_telemetry is None:
        _capability_gap_telemetry = CapabilityGapTelemetry()
    return _capability_gap_telemetry


def get_capability_forge_token_service() -> TokenService:
    global _capability_forge_token_service
    if _capability_forge_token_service is None:
        _capability_forge_token_service = TokenService()
    return _capability_forge_token_service


def get_capability_forge_service() -> CapabilityForgeService:
    global _capability_forge_service
    if _capability_forge_service is None:
        _capability_forge_service = CapabilityForgeService(
            telemetry=get_capability_gap_telemetry(),
            token_service=get_capability_forge_token_service(),
            ledger=get_ledger(),
        )
    return _capability_forge_service


def get_skill_composer() -> SkillComposer:
    global _skill_composer
    if _skill_composer is None:
        _skill_composer = SkillComposer()
    return _skill_composer


# ---------------------------------------------------------------------------
# EdgeRpcClient 工厂（Host → Edge HTTP 推送）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 进程级 HardState 读写（ModeTransitionService + ActivityMonitor 依赖）
# ---------------------------------------------------------------------------


def get_hard_state():
    """获取当前进程级 HardState 单例（延迟初始化）。

    初始化时从环境变量 HOST_SYS_MODE 读取初始 sys_mode。
    后续通过 set_sys_mode() 更新 sys_mode 字段。

    Returns:
        HardState 实例（frozen dataclass）
    """
    global _hard_state
    if _hard_state is not None:
        return _hard_state

    with _hard_state_lock:
        if _hard_state is not None:
            return _hard_state

        from host_brain.core.metabolism import normalize_hard_state  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        initial_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower()
        _hard_state = normalize_hard_state({
            "energy_level": 60,
            "mood_state": "steady",
            "sys_mode": initial_mode,
            "safety_flag": False,
            "ts_ms": int(_time.time() * 1000),
        })
        _logger.info("HardState 已初始化 (sys_mode=%s)", _hard_state.sys_mode)

    return _hard_state


def set_sys_mode(target_mode: str) -> dict:
    """更新进程级 HardState 的 sys_mode 字段。

    通过 evolve_hard_state 产生新的 immutable HardState，
    同时同步环境变量 HOST_SYS_MODE（供其他模块直接读取）。

    Args:
        target_mode: 目标模式（normal/focus/gaming/sleep/maintenance/locked）

    Returns:
        {"from_mode": str, "to_mode": str, "ts_ms": int}
    """
    global _hard_state
    import time as _time  # noqa: PLC0415
    from host_brain.core.metabolism import evolve_hard_state  # noqa: PLC0415

    with _hard_state_lock:
        current = get_hard_state()
        from_mode = current.sys_mode
        ts = int(_time.time() * 1000)

        _hard_state = evolve_hard_state(
            current,
            sys_mode=target_mode,
            event_kind="mode_switch",
            ts_ms=ts,
        )

        # 同步环境变量，确保其他通过 os.environ 读取的模块也能感知切换
        os.environ["HOST_SYS_MODE"] = _hard_state.sys_mode

        _logger.info(
            "sys_mode 已切换: %s → %s",
            from_mode,
            _hard_state.sys_mode,
        )

    return {
        "from_mode": from_mode,
        "to_mode": _hard_state.sys_mode,
        "ts_ms": ts,
    }


def get_endocrine_state(
    *,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
) -> dict[str, Any]:
    """基于当前 HardState 派生内分泌基调状态（EndocrineAgent 最小版）。"""
    try:
        from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

        return get_endocrine_agent().resolve_endocrine_state(
            hard_state=get_hard_state(),
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )
    except Exception as exc:
        _logger.warning("EndocrineState 计算失败（降级）: %s", exc)
        return {
            "dopamine_level": 0.5,
            "serotonin_level": 0.5,
            "cortisol_level": 0.25,
            "energy_reserve": 0.6,
            "gate_state": "open",
            "source": "endocrine_fallback",
        }


def get_context_compressor():
    """获取 ContextCompressor 单例（延迟初始化）。

    返回 ContextCompressor 实例，供主链路压缩多轮上下文使用。
    若初始化失败则返回 None（降级）。
    """
    global _context_compressor
    if _context_compressor is None:
        try:
            from host_brain.memory.context_compressor import ContextCompressor  # noqa: PLC0415
            _context_compressor = ContextCompressor()
            _logger.info("ContextCompressor 已初始化")
        except Exception as exc:
            _logger.warning("ContextCompressor 初始化失败（降级）: %s", exc)
            return None
    return _context_compressor


def get_emotion_timeline():
    """获取进程级 EmotionTimeline 单例，启动时从 memory_ops_log 预热最近 24h 情绪历史。

    返回 EmotionTimeline 实例，供主链路跨请求积累情绪趋势使用。
    若初始化失败则返回 None（降级）。
    """
    global _emotion_timeline
    if _emotion_timeline is None:
        try:
            from host_brain.core.emotion_timeline import EmotionTimeline  # noqa: PLC0415
            import sqlite3 as _sqlite3  # noqa: PLC0415
            import time as _time  # noqa: PLC0415

            _timeline = EmotionTimeline(max_entries=200)

            # 从 avatar_action_log 预热最近 24h 的情绪记录
            _ledger_inst = get_ledger()
            _db_path = (
                getattr(_ledger_inst, "_db_path", None)
                or getattr(_ledger_inst, "db_path", None)
            )
            _rows: list = []
            if _db_path:
                # Phase 2: 精细化 16 种标签的 VAD 映射
                _valence_map = {
                    "HAPPY": 0.60, "EXCITED": 0.80, "CALM": 0.20, "THINKING": 0.0,
                    "NEUTRAL": 0.0, "SAD": -0.60, "ANGRY": -0.70,
                    "ANXIOUS": -0.40, "FRUSTRATED": -0.50,
                    "LOVING": 0.80, "EMBARRASSED": -0.20, "CONFUSED": -0.10,
                    "BORED": -0.30, "PROUD": 0.70, "GRATEFUL": 0.70, "SURPRISED": 0.30,
                }
                _arousal_map = {
                    "HAPPY": 0.40, "EXCITED": 0.80, "CALM": -0.40, "THINKING": 0.20,
                    "NEUTRAL": 0.0, "SAD": -0.20, "ANGRY": 0.80,
                    "ANXIOUS": 0.70, "FRUSTRATED": 0.60,
                    "LOVING": 0.30, "EMBARRASSED": 0.50, "CONFUSED": 0.30,
                    "BORED": -0.50, "PROUD": 0.50, "GRATEFUL": 0.20, "SURPRISED": 0.70,
                }
                _since_ms = int(_time.time() * 1000) - 24 * 3600 * 1000
                _conn = _sqlite3.connect(_db_path)
                try:
                    _rows = _conn.execute(
                        "SELECT emotion_tag, ts_ms FROM avatar_action_log "
                        "WHERE ts_ms >= ? AND emotion_tag IS NOT NULL "
                        "AND emotion_tag != '' AND status='OK' "
                        "ORDER BY ts_ms ASC LIMIT 150",
                        (_since_ms,),
                    ).fetchall()
                    for _etag, _ts in _rows:
                        _etag_upper = str(_etag or "NEUTRAL").strip().upper()
                        _timeline.append(
                            ts_ms=int(_ts),
                            emotion_tag=_etag_upper,
                            valence=_valence_map.get(_etag_upper, 0.0),
                            arousal=_arousal_map.get(_etag_upper, 0.0),
                            source="restored_from_log",
                        )
                finally:
                    _conn.close()
                _logger.info(
                    "EmotionTimeline 已从日志预热 %d 条情绪记录",
                    len(_rows),
                )

            _emotion_timeline = _timeline
        except Exception as exc:
            _logger.warning("EmotionTimeline 初始化失败（降级）: %s", exc)
            return None
    return _emotion_timeline


def get_edge_base_url() -> str:
    """返回 Edge RPC 服务的 base URL（由环境变量决定）。

    环境变量：
      HOST_EDGE_RPC_BASE_URL  Host 侧显式 Edge RPC 地址（优先）
      EDGE_RPC_HOST  Edge 监听地址（默认 127.0.0.1）
      EDGE_RPC_PORT  Edge 监听端口（默认 17800）
    """
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return _normalize_base_url(override)

    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def get_edge_base_urls() -> list[str]:
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    primary = get_edge_base_url()
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800

    candidates = [primary]
    if not override:
        candidates.append(f"http://{host}:{port}")
    allow_local_fallback = str(os.environ.get("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if allow_local_fallback and not override and host not in {"127.0.0.1", "localhost"}:
        candidates.extend([f"http://127.0.0.1:{port}", f"http://localhost:{port}"])

    seen: set[str] = set()
    urls: list[str] = []
    for item in candidates:
        url = _normalize_base_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# 启动探测函数 — 已迁移到 startup.py，此处保留向后兼容别名
# 新代码请直接从 host_brain.app.startup 导入
# ---------------------------------------------------------------------------
from host_brain.app.startup import (  # noqa: E402, PLC0415
    probe_edge_rpc_health,
    build_edge_dependency_probe_statuses,
    collect_dependency_probes,
    collect_dependency_probes_sync,
)
