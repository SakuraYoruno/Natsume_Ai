"""依赖注入工厂模块。

从 fastapi_app.py 拆分，集中管理所有全局单例的延迟初始化工厂函数。
"""

from __future__ import annotations

import os
from typing import Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.memory.episodic import EpisodicStore
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.obs.metrics import MetricsCollector
from host_brain.retrieval.pipeline import (
    RetrievalPipelineContract,
    build_retrieval_pipeline_from_env,
)
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog

# 全局单例
_ledger: Optional[SQLiteLedger] = None
_intent_engine: Optional[IntentDecisionEngine] = None
_skill_registry: Optional[SkillRegistry] = None
_policy_cache: Optional[GovernorPolicyCache] = None
_tool_catalog: Optional[ToolCatalog] = None
_tool_broker: Optional[ToolBroker] = None
_bond_stage_engine: Optional[BondStageEngine] = None
_episodic_store: Optional[EpisodicStore] = None
_retrieval_pipeline: Optional[RetrievalPipelineContract] = None
_obs_metrics_collector: Optional[MetricsCollector] = None
_graph_runtime: Optional[GraphRuntimeContract] = None


def get_ledger() -> SQLiteLedger:
    """获取 SQLite 账本实例（延迟初始化，支持跨线程访问）。"""
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


def get_bond_stage_engine() -> BondStageEngine:
    global _bond_stage_engine
    if _bond_stage_engine is None:
        _bond_stage_engine = BondStageEngine(ledger=get_ledger())
    return _bond_stage_engine


def get_skill_registry() -> SkillRegistry:
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = build_builtin_skill_registry()
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
