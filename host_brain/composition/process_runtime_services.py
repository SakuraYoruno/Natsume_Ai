"""Governance, tool, Bond, cognition and agent service composition."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from host_brain.agents.planning_agent import PlanningAgent
from host_brain.bond.async_queue import BondUpdateQueue
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.milestone_tracker import MilestoneTracker
from host_brain.bond.neo4j_client import (
    InMemoryNeo4jBondClient,
    SqliteBondClient,
    build_neo4j_bond_client,
    load_neo4j_runtime_config,
)
from host_brain.bond.shared_memory_index import SharedMemoryIndex
from host_brain.channels.multimodal_processor import MultimodalProcessor
from host_brain.channels.tts_context_router import TtsContextRouter
from host_brain.composition.process_application_ports import require_process_application_ports
from host_brain.composition.process_environment import _parse_bool_env, _parse_int_env
from host_brain.composition.process_service_container import get_process_service_container
from host_brain.core.meta_learning_loop import MetaLearningLoop
from host_brain.core.predictive_state import PredictiveStateEngine
from host_brain.executive_runtime.capability_synthesis import RuntimeCapabilityRegistry
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.memory_worth_evaluator import MemoryWorthEvaluator
from host_brain.observability.metrics import MetricsCollector
from host_brain.reasoning.reflection_chain import ReflectionChain
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.capability_scorecard_provider import FederatedCapabilityScorecardProvider
from host_brain.skills.mcp_skill_bridge import register_mcp_tools_into_skill_registry
from host_brain.skills.provider_catalog import register_configured_skill_providers
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.catalog import ToolCatalog

_logger = logging.getLogger("Natsume.process_runtime_services")


def _container():
    return get_process_service_container()


def _foundation():
    from host_brain.composition import process_services

    return process_services


def get_graph_runtime() -> GraphRuntimeContract:
    if _container().graph_runtime is None:
        _container().graph_runtime = GraphRuntimeContract(
            ledger=_foundation().get_ledger(),
            default_timeout_ms=5_000,
        )
        for handler_id, handler in require_process_application_ports().graph_handler_bindings():
            _container().graph_runtime.register_node_handler(handler_id, handler)
    return _container().graph_runtime


def get_obs_metrics_collector() -> MetricsCollector:
    if _container().obs_metrics_collector is None:
        _container().obs_metrics_collector = MetricsCollector(ledger=_foundation().get_ledger())
    return _container().obs_metrics_collector


def get_tool_catalog() -> ToolCatalog:
    if _container().tool_catalog is None:
        _container().tool_catalog = ToolCatalog()
    return _container().tool_catalog


def get_token_service() -> TokenService:
    """Return the process-wide execution token authority.

    Governor issues approval tokens and the app-local governed Tool boundary
    consumes them.  Both receive this same authority from the composition
    root, otherwise every real token fails validation at execution time.
    """
    if _container().token_service is None:
        _container().token_service = TokenService()
    return _container().token_service


def _resolve_mcp_registry_db_path() -> str:
    db_path = str(
        require_process_application_ports().persistence_db_path() or ""
    ).strip()
    if not db_path:
        raise RuntimeError("MCP_REGISTRY_CANONICAL_DB_PATH_REQUIRED")
    return db_path


def get_mcp_tool_registry() -> MCPToolRegistry:
    db_path = _resolve_mcp_registry_db_path()
    current_path = str(getattr(_container().mcp_tool_registry, "db_path", "") or "")
    if _container().mcp_tool_registry is None or current_path != db_path:
        if _container().mcp_tool_registry is not None:
            try:
                _container().mcp_tool_registry.close()
            except Exception:
                pass
        _container().mcp_tool_registry = MCPToolRegistry(
            db_path=db_path,
            check_same_thread=False,
        )
    return _container().mcp_tool_registry


def _sync_mcp_skills_into_registry(skill_registry: SkillRegistry) -> None:
    bridge_enabled = _parse_bool_env("HOST_SKILL_MCP_BRIDGE_ENABLED", default=False)
    if not bridge_enabled:
        # The registry can outlive a controlled feature-flag change. Purge
        # stale bridge entries so a previously enabled ``mcp::*`` closure
        # cannot remain routable after the bridge is disabled.
        for skill_name in tuple(skill_registry.list_skill_names()):
            if str(skill_name).startswith("mcp::"):
                skill_registry.unregister(skill_name)
        return
    try:
        register_mcp_tools_into_skill_registry(
            skill_registry=skill_registry,
            mcp_registry=get_mcp_tool_registry(),
            broker=None,
        )
    except Exception as exc:
        _logger.warning("mcp skill bridge sync skipped: %s", exc)


def get_bond_stage_engine() -> BondStageEngine:
    if _container().bond_stage_engine is None:
        # 修复问题F：Bond 积分阈值可配置化
        # 支持通过环境变量覆盖默认阈值，格式：JSON 字符串
        # 示例：HOST_BOND_STAGE_THRESHOLDS='{"FRIEND":0,"CLOSE_FRIEND":70,"CONFIDANT":150,"SOULMATE":300}'
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

        _container().bond_stage_engine = BondStageEngine(
            ledger=_foundation().get_ledger(),
            stage_thresholds=stage_thresholds,
            score_map=score_map,
        )
    return _container().bond_stage_engine


def get_shared_memory_index() -> SharedMemoryIndex:
    episodic_store = None
    try:
        episodic_store = _foundation().get_episodic_store()
    except Exception:
        episodic_store = None
    if _container().shared_memory_index is None or getattr(_container().shared_memory_index, "_store", None) is not episodic_store:
        _container().shared_memory_index = SharedMemoryIndex(store=episodic_store)
    return _container().shared_memory_index


def get_milestone_tracker() -> MilestoneTracker:
    if _container().milestone_tracker is None:
        _container().milestone_tracker = MilestoneTracker()
    return _container().milestone_tracker


def get_bond_neo4j_client():
    """根据运行配置选择 Bond SSOT 客户端。

    优先级：
    1. HOST_BOND_BACKEND=sqlite → SqliteBondClient
    2. HOST_BOND_BACKEND=memory → InMemoryNeo4jBondClient
    3. HOST_BOND_BACKEND=neo4j → Neo4jBondClient
    4. 未显式指定时：BOND_NEO4J_ENABLED=true → Neo4jBondClient
    5. 其它情况回退 sqlite / memory
    """
    backend_raw = str(os.environ.get("HOST_BOND_BACKEND") or "").strip().lower()
    sqlite_path = str(os.environ.get("HOST_BOND_SQLITE_PATH") or "").strip()

    if backend_raw == "sqlite" or (sqlite_path and backend_raw != "neo4j"):
        return _build_migrated_bond_sqlite_client(sqlite_path or None)
    if backend_raw == "memory":
        return InMemoryNeo4jBondClient()

    config_result = load_neo4j_runtime_config()
    if config_result.success and config_result.config is not None:
        prefers_neo4j = backend_raw == "neo4j" or (
            not backend_raw and bool(config_result.config.enabled)
        )
        if prefers_neo4j:
            try:
                client = build_neo4j_bond_client(config=config_result.config)
                if client is not None:
                    return client
            except Exception:
                if backend_raw == "neo4j" or bool(config_result.config.enabled):
                    raise

    if sqlite_path:
        return _build_migrated_bond_sqlite_client(sqlite_path or None)
    return InMemoryNeo4jBondClient()


def _build_migrated_bond_sqlite_client(
    db_path: str | os.PathLike[str] | None,
) -> SqliteBondClient:
    """Run the central catalog before constructing the Bond fallback."""

    effective_path = str(
        db_path
        or os.environ.get("HOST_BOND_SQLITE_PATH")
        or SqliteBondClient.DEFAULT_DB_PATH
    ).strip()
    if effective_path != ":memory:":
        from host_brain.audit.migration_runner import (  # noqa: PLC0415
            SQLiteMigrationRunner,
        )

        migrated = SQLiteMigrationRunner(db_path=effective_path).apply_migrations(
            request_id="bond-fallback-startup"
        )
        if not migrated.success:
            raise RuntimeError(
                f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: "
                f"{migrated.detail}"
            )
    return SqliteBondClient(db_path=effective_path)


def get_bond_dynamics_writer() -> BondDynamicsWriter:
    """Return the governed synchronous Bond command writer for API calls."""
    ledger = _foundation().get_ledger()
    stage_engine = get_bond_stage_engine()
    if (
        _container().bond_dynamics_writer is None
        or _container().bond_dynamics_writer.ledger is not ledger
        or getattr(_container().bond_dynamics_writer, "_stage_engine", None) is not stage_engine
    ):
        _container().bond_dynamics_writer = BondDynamicsWriter(
            ledger=ledger,
            neo4j_client=get_bond_neo4j_client(),
            stage_engine=stage_engine,
        )
    return _container().bond_dynamics_writer


def get_bond_update_queue() -> BondUpdateQueue:
    max_queue_size = _parse_int_env(
        "HOST_BOND_QUEUE_MAX_SIZE",
        default=1024,
        minimum=8,
        maximum=100000,
    )
    worker_enabled = _parse_bool_env("HOST_BOND_QUEUE_ENABLED", default=True)
    signature = (
        int(max_queue_size),
        bool(worker_enabled),
        str(os.environ.get("HOST_BOND_BACKEND") or "").strip().lower(),
        str(os.environ.get("BOND_NEO4J_ENABLED") or "").strip().lower(),
        str(os.environ.get("BOND_NEO4J_URI") or "").strip(),
        str(os.environ.get("BOND_NEO4J_DATABASE") or "").strip(),
        str(os.environ.get("BOND_NEO4J_AUTH_REF") or "").strip(),
    )

    # 修复审计问题 #10：writer_factory 使用 get_bond_neo4j_client() 而非硬编码
    # InMemoryNeo4jBondClient，确保 Bond 事件写入持久化客户端（SQLite 或内存）。
    # 客户端类型由环境变量 HOST_BOND_BACKEND / HOST_BOND_SQLITE_PATH 控制：
    #   HOST_BOND_BACKEND=sqlite  → SqliteBondClient（持久化，推荐生产环境）
    #   HOST_BOND_BACKEND=memory  → InMemoryNeo4jBondClient（内存，重启丢失）
    def _make_writer() -> BondDynamicsWriter:
        return BondDynamicsWriter(
            ledger=_foundation().get_ledger(),
            neo4j_client=get_bond_neo4j_client(),
            stage_engine=get_bond_stage_engine(),
        )

    if _container().bond_update_queue is None:
        _container().bond_update_queue = BondUpdateQueue(
            writer_factory=_make_writer,
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_container().bond_update_queue, "_config_signature", signature)
        return _container().bond_update_queue

    current_signature = getattr(_container().bond_update_queue, "_config_signature", None)
    if current_signature != signature:
        _container().bond_update_queue.shutdown()
        _container().bond_update_queue = BondUpdateQueue(
            writer_factory=_make_writer,
            max_queue_size=max_queue_size,
            worker_enabled=worker_enabled,
        )
        setattr(_container().bond_update_queue, "_config_signature", signature)
    return _container().bond_update_queue


def get_skill_registry() -> SkillRegistry:
    if _container().skill_registry is None:
        _container().skill_registry = build_builtin_skill_registry()
        register_configured_skill_providers(
            _container().skill_registry,
            parse_bool_env=_parse_bool_env,
            logger=_logger,
        )
    _sync_mcp_skills_into_registry(_container().skill_registry)
    return _container().skill_registry


def get_policy_cache() -> GovernorPolicyCache:
    if _container().policy_cache is None:
        _container().policy_cache = GovernorPolicyCache(ttl_ms=60_000)
    return _container().policy_cache


def get_capability_scorecard_provider(
    *,
    skill_registry: SkillRegistry | None = None,
) -> FederatedCapabilityScorecardProvider:
    """Return the read-only federated projection used by Governor policy."""
    registry = skill_registry if skill_registry is not None else get_skill_registry()
    # MCP metadata remains a supported read-only discovery source. Do not
    # make it depend on the retired execution broker or on the disabled
    # MCP-to-Skill bridge having initialized the registry first.
    mcp_registry = get_mcp_tool_registry()
    current_skill_registry = getattr(
        _container().capability_scorecard_provider, "_skill_registry", None
    )
    current_tool_catalog = getattr(
        _container().capability_scorecard_provider, "_tool_catalog", None
    )
    current_mcp_registry = getattr(_container().capability_scorecard_provider, "_mcp_registry", None)
    tool_catalog = get_tool_catalog()
    if (
        _container().capability_scorecard_provider is None
        or current_skill_registry is not registry
        or current_tool_catalog is not tool_catalog
        or current_mcp_registry is not mcp_registry
    ):
        def _get_pentest_tool(tool_id: str):
            from host_brain.pentest.agent.tool_registry import get  # noqa: PLC0415

            return get(tool_id)

        def _all_pentest_tools():
            from host_brain.pentest.agent.tool_registry import all_descriptors  # noqa: PLC0415

            return all_descriptors()

        _container().capability_scorecard_provider = FederatedCapabilityScorecardProvider(
            skill_registry=registry,
            tool_catalog=tool_catalog,
            mcp_registry=mcp_registry,
            pentest_get=_get_pentest_tool,
            pentest_all=_all_pentest_tools,
        )
    return _container().capability_scorecard_provider


def get_intent_engine() -> IntentDecisionEngine:
    if _container().intent_engine is None:
        skill_registry = get_skill_registry()
        _container().intent_engine = IntentDecisionEngine(
            ledger=_foundation().get_ledger(),
            token_service=get_token_service(),
            registry=skill_registry,
            policy_cache=get_policy_cache(),
            # Production composition is fail-closed by default.  Direct unit
            # consumers may still opt into the adapter explicitly.
            capability_maturity_enabled=True,
            capability_scorecard_provider=get_capability_scorecard_provider(
                skill_registry=skill_registry,
            ),
        )
    return _container().intent_engine


# ---------------------------------------------------------------------------
# 新模块工厂函数（优化计划新增）
# ---------------------------------------------------------------------------

def get_routing_matrix() -> Any:
    """获取模型路由矩阵单例（延迟初始化）。"""
    if _container().routing_matrix is None:
        ledger = _foundation().get_ledger()

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

        _container().routing_matrix = require_process_application_ports().routing_matrix_factory(
            route_log_writer=_route_log_writer
        )
        _logger.info("RoutingMatrix 已初始化")
    return _container().routing_matrix


def get_memory_guard() -> Any:
    """获取记忆守卫单例（延迟初始化）。"""
    if _container().memory_guard is None:
        ledger = _foundation().get_ledger()

        def _audit_writer(event: str, detail: dict) -> None:
            try:
                ledger.log_memory_op(row={"op_type": event, **detail})
            except Exception as exc:
                _logger.warning("MemoryGuard 审计写入失败: %s", exc)

        _container().memory_guard = require_process_application_ports().memory_guard_factory(
            audit_writer=_audit_writer
        )
        _logger.info("MemoryGuard 已初始化")
    return _container().memory_guard


def get_state_arbiter() -> Any:
    """获取状态仲裁器单例（延迟初始化）。"""
    if _container().state_arbiter is None:
        ledger = _foundation().get_ledger()

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

        _container().state_arbiter = require_process_application_ports().state_arbiter_factory(
            audit_writer=_audit_writer
        )
        _logger.info("StateArbiter 已初始化（未注册状态源，需业务层注入）")
    return _container().state_arbiter


def get_state_write_arbiter() -> Any:
    """获取状态写权限仲裁器单例（延迟初始化）。"""
    if _container().state_write_arbiter is None:
        ledger = _foundation().get_ledger()

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

        _container().state_write_arbiter = require_process_application_ports().state_write_arbiter_factory(
            audit_writer=_audit_writer
        )
        _logger.info("StateWriteArbiter 已初始化（SoulState 默认仅 persona_core 可写）")
    return _container().state_write_arbiter


# ---------------------------------------------------------------------------
# 蒸馏引擎 + 语义召回器工厂
# ---------------------------------------------------------------------------


def get_distillation_engine() -> Any:
    """获取蒸馏引擎单例（延迟初始化）。"""
    if _container().distillation_engine is None:
        ports = require_process_application_ports()
        episodic_store = ports.episodic_store_adapter_factory(
            memory_guard=get_memory_guard(),
            ledger=_foundation().get_ledger(),
        )
        _container().distillation_engine = ports.distillation_engine_factory(
            episodic_store=episodic_store,
            memory_guard=get_memory_guard(),
            state_arbiter=get_state_arbiter(),
            ledger=_foundation().get_ledger(),
        )
        _logger.info("DistillationEngine 已初始化")
    return _container().distillation_engine


def get_semantic_recaller() -> Any:
    """获取语义召回器单例（延迟初始化）。"""
    if _container().semantic_recaller is None:
        import os as _os  # noqa: PLC0415
        from urllib.parse import urlparse  # noqa: PLC0415

        from host_brain.memory.chroma_client import embed_text, load_chroma_runtime_config  # noqa: PLC0415

        cfg_result = load_chroma_runtime_config(env=_os.environ)
        cfg = cfg_result.config if cfg_result.success else None
        default_use_mock = not bool(cfg is not None and cfg.enabled)
        use_mock = _parse_bool_env("SEMANTIC_RECALL_USE_MOCK", default=default_use_mock)
        collection_name = str(
            _os.environ.get("SEMANTIC_RECALL_COLLECTION")
            or _os.environ.get("SEMANTIC_RECALL_COLLECTION_NAME")
            or (cfg.default_collection if cfg is not None else "")
            or "episodic"
        ).strip()
        endpoint = str(
            _os.environ.get("SEMANTIC_RECALL_CHROMA_ENDPOINT")
            or (cfg.endpoint if cfg is not None else "")
            or ""
        ).strip()
        parsed = urlparse(endpoint or "http://localhost:8000")
        chroma_host = str(
            _os.environ.get("SEMANTIC_RECALL_CHROMA_HOST")
            or parsed.hostname
            or _os.environ.get("CHROMA_HOST")
            or "localhost"
        ).strip()
        chroma_port_default = int(parsed.port or 8000)
        chroma_port = _parse_int_env(
            "SEMANTIC_RECALL_CHROMA_PORT",
            default=chroma_port_default,
            minimum=1,
            maximum=65535,
        )
        top_k = _parse_int_env("SEMANTIC_RECALL_TOP_K", default=5, minimum=1, maximum=20)
        threshold_raw = str(_os.environ.get("SEMANTIC_RECALL_THRESHOLD") or "0.70").strip()
        try:
            threshold = float(threshold_raw)
        except ValueError:
            threshold = 0.70
        profiles = dict(getattr(cfg, "collection_profiles", {}) or {})
        vector_dim = _parse_int_env(
            "SEMANTIC_RECALL_VECTOR_DIM",
            default=int(profiles.get(collection_name) or profiles.get(str(getattr(cfg, "default_collection", "") or "")) or 1024),
            minimum=1,
            maximum=8192,
        )

        def _embed_for_runtime_collection(text: str) -> list[float]:
            values, _model_id = embed_text(str(text or ""), dim=vector_dim)
            return list(values or [])

        _container().semantic_recaller = require_process_application_ports().semantic_recaller_factory(
            collection_name=collection_name,
            top_k=top_k,
            similarity_threshold=threshold,
            use_mock=use_mock,
            chroma_host=chroma_host,
            chroma_port=chroma_port,
            embed_fn=_embed_for_runtime_collection,
        )
        _logger.info(
            "SemanticRecaller 已初始化（use_mock=%s, collection=%s, chroma=%s:%d, vector_dim=%d, top_k=%d, threshold=%.2f）",
            use_mock, collection_name, chroma_host, chroma_port, vector_dim, top_k, threshold,
        )
    return _container().semantic_recaller


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
            bond_stage=str(bond_stage or "FRIEND"),
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
    if _container().bond_aware_style_engine is None:
        _container().bond_aware_style_engine = _BondAwareStyleEngineProxy()
    return _container().bond_aware_style_engine


def get_reflection_chain() -> ReflectionChain:
    if _container().reflection_chain is None:
        _container().reflection_chain = ReflectionChain(
            use_llm=True,
            model_attempt_service=_foundation().get_model_attempt_service(),
        )
    return _container().reflection_chain


def get_tts_context_router() -> TtsContextRouter:
    if _container().tts_context_router is None:
        _container().tts_context_router = TtsContextRouter()
    return _container().tts_context_router


def get_multimodal_processor() -> MultimodalProcessor:
    if _container().multimodal_processor is None:
        _container().multimodal_processor = MultimodalProcessor()
    return _container().multimodal_processor


def get_multimodal_observer() -> Any:
    if _container().multimodal_observer is None:
        _container().multimodal_observer = (
            require_process_application_ports().multimodal_observer_factory()
        )
    return _container().multimodal_observer


def get_memory_worth_evaluator() -> MemoryWorthEvaluator:
    if _container().memory_worth_evaluator is None:
        _container().memory_worth_evaluator = MemoryWorthEvaluator()
    return _container().memory_worth_evaluator


def get_predictive_state_engine() -> PredictiveStateEngine:
    if _container().predictive_state_engine is None:
        _container().predictive_state_engine = PredictiveStateEngine()
    return _container().predictive_state_engine


def get_meta_learning_loop() -> MetaLearningLoop:
    if _container().meta_learning_loop is None:
        _container().meta_learning_loop = MetaLearningLoop()
    return _container().meta_learning_loop


def get_planning_agent() -> PlanningAgent:
    if _container().planning_agent is None:
        _container().planning_agent = PlanningAgent()
    return _container().planning_agent


def get_runtime_capability_registry() -> RuntimeCapabilityRegistry:
    if _container().runtime_capability_registry is None:
        _container().runtime_capability_registry = RuntimeCapabilityRegistry()
    return _container().runtime_capability_registry


def get_capability_gap_telemetry() -> Any:
    if _container().capability_gap_telemetry is None:
        _container().capability_gap_telemetry = (
            require_process_application_ports().capability_gap_telemetry_factory()
        )
    return _container().capability_gap_telemetry


def get_capability_forge_token_service() -> TokenService:
    if _container().capability_forge_token_service is None:
        _container().capability_forge_token_service = TokenService()
    return _container().capability_forge_token_service


def get_capability_forge_service() -> Any:
    if _container().capability_forge_service is None:
        _container().capability_forge_service = (
            require_process_application_ports().capability_forge_service_factory(
            telemetry=get_capability_gap_telemetry(),
            token_service=get_capability_forge_token_service(),
            ledger=_foundation().get_ledger(),
            )
        )
    return _container().capability_forge_service
