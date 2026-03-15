"""依赖注入工厂模块。

从 fastapi_app.py 拆分，集中管理所有全局单例的延迟初始化工厂函数。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.async_queue import BondUpdateQueue
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.neo4j_client import InMemoryNeo4jBondClient, SqliteBondClient  # 修复审计问题 #10
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.app.graph_config_builder import (
    SCENE_ROUTER_ENTRY_HANDLER_ID,
    SCENE_ROUTER_POST_CORE_HANDLER_ID,
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
from host_brain.skills.builtin_registry import build_builtin_skill_registry

from host_brain.skills.browser_registry import BrowserSkillProvider
from host_brain.skills.desktop_registry import DesktopSkillProvider
from host_brain.skills.emotion_registry import EmotionSkillProvider
from host_brain.skills.file_registry import FileSkillProvider
from host_brain.skills.ide_registry import IDESkillProvider
from host_brain.skills.memory_registry import MemorySkillProvider
from host_brain.skills.mcp_skill_bridge import register_mcp_tools_into_skill_registry
from host_brain.skills.notification_registry import NotificationSkillProvider
from host_brain.skills.personality_registry import PersonalitySkillProvider
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.scheduler_registry import SchedulerSkillProvider
from host_brain.skills.shell_registry import ShellSkillProvider
from host_brain.skills.system_registry import SystemSkillProvider

from host_brain.skills.web_registry import WebSkillProvider
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog

# 新模块导入（优化计划新增模块）
from host_brain.app.graph.main_chain import MainChain
from host_brain.app.llm.routing_matrix import RoutingMatrix
from host_brain.app.recall.recall_pack_builder import RecallPackBuilder
from host_brain.app.memory.memory_guard import MemoryGuard
from host_brain.app.memory.state_arbiter import StateArbiter
# TODO-99: 蒸馏引擎 + 语义召回器
from host_brain.app.memory.distillation_engine import DistillationEngine
from host_brain.app.recall.semantic_recall import SemanticRecaller

_logger = logging.getLogger("Natsume.dependencies")

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
_main_chain: Optional[MainChain] = None
_routing_matrix: Optional[RoutingMatrix] = None
_recall_pack_builder: Optional[RecallPackBuilder] = None
_memory_guard: Optional[MemoryGuard] = None
_state_arbiter: Optional[StateArbiter] = None
# TODO-99: 蒸馏引擎 + 语义召回器单例
_distillation_engine: Optional[DistillationEngine] = None
_semantic_recaller: Optional[SemanticRecaller] = None


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


def get_episodic_store() -> EpisodicStore:
    global _episodic_store
    if _episodic_store is None:
        _episodic_store = EpisodicStore(ledger=get_ledger())
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


def get_bond_neo4j_client():
    """修复审计问题 #10：根据环境变量自动选择持久化 Bond 客户端。

    优先级：
    1. HOST_BOND_BACKEND=sqlite → SqliteBondClient（持久化，推荐）
    2. HOST_BOND_BACKEND=memory（默认）→ InMemoryNeo4jBondClient（内存，重启丢失）
    3. HOST_BOND_SQLITE_PATH 有值 → 自动升级为 SqliteBondClient
    """
    backend = str(os.environ.get("HOST_BOND_BACKEND") or "memory").strip().lower()
    sqlite_path = str(os.environ.get("HOST_BOND_SQLITE_PATH") or "").strip()
    if backend == "sqlite" or sqlite_path:
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
        # 注册文件系统 Skills（file_read / file_write / file_append / file_delete / file_list）
        if _parse_bool_env("HOST_SKILL_FILE_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(FileSkillProvider())
                _logger.info("File Skills 已注册（file_read / file_write / file_append / file_delete / file_list）")
            except Exception as exc:
                _logger.warning("File Skills 注册失败，跳过: %s", exc)
        # 注册 Shell 命令执行 Skills（shell_run / shell_read_log / shell_run_test）
        if _parse_bool_env("HOST_SKILL_SHELL_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(ShellSkillProvider())
                _logger.info("Shell Skills 已注册（shell_run / shell_read_log / shell_run_test）")
            except Exception as exc:
                _logger.warning("Shell Skills 注册失败，跳过: %s", exc)
        # 注册通知/提醒 Skills（notify_desktop / notify_tts_speak / notify_schedule_reminder）
        if _parse_bool_env("HOST_SKILL_NOTIFICATION_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(NotificationSkillProvider())
                _logger.info("Notification Skills 已注册（notify_desktop / notify_tts_speak / notify_schedule_reminder）")
            except Exception as exc:
                _logger.warning("Notification Skills 注册失败，跳过: %s", exc)
        # 注册 IDE Skills（Cursor / PyCharm / Codex）
        if _parse_bool_env("HOST_SKILL_IDE_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(IDESkillProvider())
                _logger.info("IDE Skills 已注册（open_cursor / run_pycharm_action / codex_task / open_file_in_editor）")
            except Exception as exc:
                _logger.warning("IDE Skills 注册失败，跳过: %s", exc)
        # 注册浏览器自动化 Skills（agent-browser / Playwright / Puppeteer）
        if _parse_bool_env("HOST_SKILL_BROWSER_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(BrowserSkillProvider())
                _logger.info(
                    "Browser Skills 已注册（navigate / click / type / screenshot / "
                    "execute_script / wait_for / scrape / close）"
                )
            except Exception as exc:
                _logger.warning("Browser Skills 注册失败，跳过: %s", exc)
        # 注册人格/调度 Skills（diary_writer / dream_generator / desktop_note / focus_mode / proactive_talk）
        if _parse_bool_env("HOST_SKILL_PERSONALITY_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(PersonalitySkillProvider())
                _logger.info(
                    "Personality Skills 已注册（diary_writer / dream_generator / "
                    "desktop_note / focus_mode / proactive_talk）"
                )
            except Exception as exc:
                _logger.warning("Personality Skills 注册失败，跳过: %s", exc)
        # 注册桌面控制 Skills（desktop_screenshot / desktop_click / desktop_type / desktop_hotkey / clipboard_read / clipboard_write）
        if _parse_bool_env("HOST_SKILL_DESKTOP_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(DesktopSkillProvider())
                _logger.info(
                    "Desktop Skills 已注册（desktop_screenshot / desktop_click / desktop_type / "
                    "desktop_hotkey / clipboard_read / clipboard_write）"
                )
            except Exception as exc:
                _logger.warning("Desktop Skills 注册失败，跳过: %s", exc)
        # 注册记忆操作 Skills（memory_search / memory_store / memory_forget）
        if _parse_bool_env("HOST_SKILL_MEMORY_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(MemorySkillProvider())
                _logger.info("Memory Skills 已注册（memory_search / memory_store / memory_forget）")
            except Exception as exc:
                _logger.warning("Memory Skills 注册失败，跳过: %s", exc)
        # 注册情绪/Bond 关系 Skills（emotion_update / bond_score_update / emotion_get / bond_score_get）
        if _parse_bool_env("HOST_SKILL_EMOTION_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(EmotionSkillProvider())
                _logger.info(
                    "Emotion Skills 已注册（emotion_update / bond_score_update / "
                    "emotion_get / bond_score_get）"
                )
            except Exception as exc:
                _logger.warning("Emotion Skills 注册失败，跳过: %s", exc)
        # 注册调度器 Skills（scheduler_add / scheduler_cancel / scheduler_list）
        if _parse_bool_env("HOST_SKILL_SCHEDULER_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(SchedulerSkillProvider())
                _logger.info("Scheduler Skills 已注册（scheduler_add / scheduler_cancel / scheduler_list）")
            except Exception as exc:
                _logger.warning("Scheduler Skills 注册失败，跳过: %s", exc)
        # 注册系统感知 Skills（system_info / process_list / process_kill / network_status）
        if _parse_bool_env("HOST_SKILL_SYSTEM_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(SystemSkillProvider())
                _logger.info(
                    "System Skills 已注册（system_info / process_list / process_kill / network_status）"
                )
            except Exception as exc:
                _logger.warning("System Skills 注册失败，跳过: %s", exc)
        # 注册网络/搜索 Skills（web_search / http_request）
        if _parse_bool_env("HOST_SKILL_WEB_ENABLED", default=True):
            try:
                _skill_registry.register_decorated(WebSkillProvider())
                _logger.info("Web Skills 已注册（web_search / http_request）")
            except Exception as exc:
                _logger.warning("Web Skills 注册失败，跳过: %s", exc)
    _sync_mcp_skills_into_registry(_skill_registry)
    return _skill_registry


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

def get_main_chain() -> MainChain:
    """获取主链路编排器单例（延迟初始化）。

    [审计修复 #4] MainChain 已从死代码迁移为 _unified_chat_sse_generator 的适配层。
    通过 MainChain.run_as_sse_adapter() 可将请求委托给真实的 SSE 路由逻辑，
    同时保留 ChainContext 审计追踪能力。

    路由层调用示例：
        chain = get_main_chain()
        async for event in chain.run_as_sse_adapter(request_body=..., ...):
            yield event
    """
    global _main_chain
    if _main_chain is None:
        from host_brain.app.failure_matrix import FailureHandler

        ledger = get_ledger()

        async def _audit_writer(tag: str, detail: dict) -> None:
            try:
                ledger.log_error(
                    request_id=str(detail.get("request_id") or "main_chain"),
                    stage=tag,
                    reason_code=str(detail.get("event") or tag),
                    detail=str(detail),
                )
            except Exception as exc:
                _logger.warning("MainChain 审计写入失败: %s", exc)

        failure_handler = FailureHandler(audit_writer=_audit_writer)
        _main_chain = MainChain(failure_handler=failure_handler.handle)
        _logger.info("MainChain 已初始化（含审计写入，适配层模式）")
    return _main_chain


async def run_chat_via_main_chain(
    *,
    request_body: object,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict,
    metrics: object,
    _pre_classified_scene: str | None = None,
):
    """[审计修复 #4] 通过 MainChain 适配层执行聊天 SSE 路由。

    这是 MainChain 的标准调用入口，供路由层（如 canon_routes.py）使用。
    内部委托给 MainChain.run_as_sse_adapter → _unified_chat_sse_generator。

    Args:
        request_body: ChatRequest 实例
        request_id: 请求唯一 ID
        session_id: 会话 ID
        ts_ms: 请求时间戳（毫秒）
        intent_draft: Governor 意图草稿
        metrics: MetricsCollector 实例
        _pre_classified_scene: 预分类场景（可选，避免重复分类）

    Yields:
        SSE 事件字符串
    """
    chain = get_main_chain()
    async for event in chain.run_as_sse_adapter(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        metrics=metrics,
        _pre_classified_scene=_pre_classified_scene,
    ):
        yield event


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


def get_recall_pack_builder() -> RecallPackBuilder:
    """获取 RecallPack 组装器单例（延迟初始化）。"""
    global _recall_pack_builder
    if _recall_pack_builder is None:
        ledger = get_ledger()

        def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_error(
                    request_id=str(detail.get("request_id") or "recall_pack_builder"),
                    stage=event,
                    reason_code=event,
                    detail=str(detail),
                )
            except Exception as exc:
                _logger.warning("RecallPackBuilder 审计写入失败: %s", exc)

        _recall_pack_builder = RecallPackBuilder(audit_writer=_audit_writer)
        _logger.info("RecallPackBuilder 已初始化")
    return _recall_pack_builder


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


# ---------------------------------------------------------------------------
# TODO-99: 蒸馏引擎 + 语义召回器工厂
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


# ---------------------------------------------------------------------------
# EdgeRpcClient 工厂（Host → Edge HTTP 推送）
# ---------------------------------------------------------------------------


def get_edge_base_url() -> str:
    """返回 Edge RPC 服务的 base URL（由环境变量决定）。

    环境变量：
      EDGE_RPC_HOST  Edge 监听地址（默认 127.0.0.1）
      EDGE_RPC_PORT  Edge 监听端口（默认 17800）
    """
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


async def probe_edge_rpc_health() -> dict:
    """探测 Edge RPC 服务健康状态（GET /v1/edge/health）。

    返回 dict 包含 available / status / detail / latency_ms。
    用于 Host 启动时的依赖检查和运行时健康监控。
    """
    import time

    t0 = int(time.time() * 1000)
    base_url = get_edge_base_url()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/v1/edge/health")
        latency_ms = max(0, int(time.time() * 1000) - t0)
        if resp.status_code == 200:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "available": True,
                "status": str(body.get("status") or "OK"),
                "detail": "edge rpc reachable",
                "latency_ms": latency_ms,
                "base_url": base_url,
            }
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge health returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
            "base_url": base_url,
        }
    except ImportError:
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": "httpx 未安装，无法探测 Edge RPC",
            "latency_ms": 0,
            "base_url": base_url,
        }
    except Exception as exc:
        latency_ms = max(0, int(time.time() * 1000) - t0)
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge rpc unreachable: {exc}",
            "latency_ms": latency_ms,
            "base_url": base_url,
        }
