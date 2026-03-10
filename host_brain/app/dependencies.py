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
from host_brain.skills.mcp_skill_bridge import register_mcp_tools_into_skill_registry
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog

# 新模块导入（优化计划新增模块）
from host_brain.app.graph.main_chain import MainChain
from host_brain.app.llm.routing_matrix import RoutingMatrix
from host_brain.app.recall.context_pack_builder import ContextPackBuilder
from host_brain.app.memory.memory_guard import MemoryGuard
from host_brain.app.memory.state_arbiter import StateArbiter

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
_context_pack_builder: Optional[ContextPackBuilder] = None
_memory_guard: Optional[MemoryGuard] = None
_state_arbiter: Optional[StateArbiter] = None


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
        _bond_stage_engine = BondStageEngine(ledger=get_ledger())
    return _bond_stage_engine


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

    if _bond_update_queue is None:
        _bond_update_queue = BondUpdateQueue(
            writer_factory=lambda: BondDynamicsWriter(
                ledger=get_ledger(),
                stage_engine=get_bond_stage_engine(),
            ),
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_bond_update_queue, "_config_signature", signature)
        return _bond_update_queue

    current_signature = getattr(_bond_update_queue, "_config_signature", None)
    if current_signature != signature:
        _bond_update_queue.shutdown()
        _bond_update_queue = BondUpdateQueue(
            writer_factory=lambda: BondDynamicsWriter(
                ledger=get_ledger(),
                stage_engine=get_bond_stage_engine(),
            ),
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_bond_update_queue, "_config_signature", signature)
    return _bond_update_queue


def get_skill_registry() -> SkillRegistry:
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = build_builtin_skill_registry()
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
    """获取主链路编排器单例（延迟初始化）。"""
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
        _logger.info("MainChain 已初始化（含审计写入）")
    return _main_chain


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


def get_context_pack_builder() -> ContextPackBuilder:
    """获取 ContextPack 组装器单例（延迟初始化）。"""
    global _context_pack_builder
    if _context_pack_builder is None:
        ledger = get_ledger()

        def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_error(
                    request_id=str(detail.get("request_id") or "context_pack_builder"),
                    stage=event,
                    reason_code=event,
                    detail=str(detail),
                )
            except Exception as exc:
                _logger.warning("ContextPackBuilder 审计写入失败: %s", exc)

        _context_pack_builder = ContextPackBuilder(audit_writer=_audit_writer)
        _logger.info("ContextPackBuilder 已初始化")
    return _context_pack_builder


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
