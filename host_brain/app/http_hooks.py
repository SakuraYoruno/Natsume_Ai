"""HTTP lifecycle, middleware, and global exception hooks."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.schemas import ErrorResponse
from host_brain.app.http_hooks_runtime_helpers import (
    read_episodic_latest_fact,
    read_world_canon_fact,
    run_life_memory_sleep_replay_handler,
    run_long_term_distillation_handler,
)
# P3-2：导入拆分后的子模块
from host_brain.app.http_hooks_inbound import add_request_id_and_audit, should_emit_request_info
from host_brain.app.http_hooks_outbound import global_exception_handler, startup_status_is_degraded
from host_brain.app.settings import (
    get_core_settings,
    get_persistence_settings,
    get_scheduler_settings,
)
from host_brain.core.ids import new_request_id
from host_brain.app.legacy_runtime_shutdown_adapter import (
    shutdown_runtime as _shutdown_runtime,
)

_AUDIT_SILENT_PATH_PREFIXES: tuple[str, ...] = (
    "/monitor/",
    "/docs",
    "/openapi",
    "/favicon",
    "/health",
    "/ready",
    "/metrics",
)
_AUDIT_SLOW_REQUEST_MS = 1200


def _should_emit_request_info(path: str) -> bool:
    # P3-2：委托给 http_hooks_inbound.should_emit_request_info
    return should_emit_request_info(path)


def _startup_status_is_degraded(value: Any) -> bool:
    # P3-2：委托给 http_hooks_outbound.startup_status_is_degraded
    return startup_status_is_degraded(value)


def _build_world_canon_reader(*, ledger: Any) -> Any:
    """Bind the startup Canon reader to the process canonical ledger."""

    canonical_db_path = str(
        getattr(ledger, "canonical_db_path", "")
        or getattr(ledger, "db_path", "")
    ).strip()
    if not canonical_db_path:
        raise RuntimeError("World Canon reader requires the canonical process ledger")
    from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

    return WorldCanonStore(
        world_root=get_persistence_settings().world_canon_root,
        ledger=ledger,
        create_directories=False,
    )


def _init_edge_command_pusher_status(*, logger: Any) -> str:
    try:
        from host_brain.tribe.edge_command_pusher import init_edge_command_pusher  # noqa: PLC0415

        edge_cmd_pusher = init_edge_command_pusher()
        logger.info(
            "EdgeCommandPusher initialized: enabled=%s url=%s",
            edge_cmd_pusher.enabled,
            edge_cmd_pusher.edge_base_url,
        )
        return (
            f"OK (enabled={edge_cmd_pusher.enabled}, "
            f"edge_url={edge_cmd_pusher.edge_base_url})"
        )
    except Exception as exc:
        logger.warning("EdgeCommandPusher initialization degraded: %s", exc)
        return f"DEGRADED: {exc}"


def _sync_world_canon_index_on_startup(
    *,
    ledger: Any,
    logger: Any,
    service: Any | None = None,
    world_root: str | None = None,
    ts_ms: int | None = None,
) -> str:
    resolved_world_root = str(
        world_root or get_persistence_settings().world_canon_root
    ).strip() or "host_brain/canon/world"
    sync_ts = int(ts_ms if ts_ms is not None else time.time() * 1000)

    try:
        from host_brain.memory.canon_sync import sync_world_canon_index  # noqa: PLC0415

        if service is not None:
            records = list(service.sync_startup(ts_ms=sync_ts).records)
        else:
            records = sync_world_canon_index(
                world_root=resolved_world_root,
                ledger=ledger,
                ts_ms=sync_ts,
            )
        changed_count = sum(1 for record in records if bool(getattr(record, "changed", False)))
        logger.info(
            "World canon startup sync completed",
            extra={
                "world_root": resolved_world_root,
                "synced_count": len(records),
                "changed_count": changed_count,
            },
        )
        return f"OK (synced={len(records)}, changed={changed_count})"
    except Exception as exc:
        logger.warning(
            "World canon startup sync degraded: %s",
            exc,
            extra={"world_root": resolved_world_root, "error": str(exc)},
        )
        return f"DEGRADED: {exc}"


def runtime_lifespan(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
    canon_service_provider: Callable[[], Any | None] | None = None,
) -> AsyncGenerator[None, None]:
    @asynccontextmanager
    async def _lifespan() -> AsyncGenerator[None, None]:
        logger.info("Natsume AI Host Brain starting up ...")
        ledger = get_ledger()
        _activity_monitor = None
        _resource_manager = None
        try:
            # 预初始化新模块，确保启动时即可感知降级状态
            # Phase XIV v4：移除 main_chain 和 routing_matrix（已被 cortex_orchestrator 架构取代）
            from host_brain.app.dependencies import (
                get_memory_guard,
                get_state_arbiter,
                get_distillation_engine,
                get_semantic_recaller,
            )
            new_module_statuses: dict[str, str] = {}
            core_settings = get_core_settings()
            scheduler_settings = get_scheduler_settings()
            _startup_eager_warmup = bool(core_settings.startup_eager_warmup)
            # Periodic execution is owned by the governed SchedulerClock,
            # which is composed only after Task Runtime activation in asgi.py.
            # This legacy lifespan may initialize producers, but it must never
            # start either historical scheduler implementation.
            _background_tasks_enabled = bool(
                scheduler_settings.background_tasks_enabled
            )
            new_module_statuses["legacy_periodic_schedulers"] = (
                "RETIRED: governed SchedulerClock owns periodic execution"
            )
            new_module_statuses["proactive_push_client"] = (
                "DEFERRED: governed scheduler composition"
            )
            new_module_statuses["codex_immune_monitor"] = (
                "RETIRED: governed scheduler immune definition"
            )
            if canon_service_provider is not None:
                canon_service = canon_service_provider()
                new_module_statuses["world_canon_sync"] = (
                    _sync_world_canon_index_on_startup(
                        ledger=ledger,
                        logger=logger,
                        service=canon_service,
                    )
                    if canon_service is not None
                    else "DEFERRED: production Canon composition"
                )
            else:
                new_module_statuses["world_canon_sync"] = (
                    _sync_world_canon_index_on_startup(
                        ledger=ledger,
                        logger=logger,
                    )
                )
            for name, factory in [
                ("memory_guard", get_memory_guard),
                ("state_arbiter", get_state_arbiter),
            ]:
                try:
                    factory()
                    new_module_statuses[name] = "OK"
                except Exception as exc:
                    new_module_statuses[name] = f"DEGRADED: {exc}"
                    logger.warning(
                        "新模块 %s 启动探针失败（降级）: %s",
                        name, exc,
                        extra={"startup_module": name, "error": str(exc)},
                    )
            for name, factory in [
                ("distillation_engine", get_distillation_engine),
                ("semantic_recaller", get_semantic_recaller),
            ]:
                if not _startup_eager_warmup:
                    new_module_statuses[name] = "OK (lazy init)"
                    logger.info(
                        "新模块 %s 跳过启动预热，改为按需初始化",
                        name,
                        extra={"startup_module": name},
                    )
                    continue
                try:
                    factory()
                    new_module_statuses[name] = "OK"
                except Exception as exc:
                    new_module_statuses[name] = f"DEGRADED: {exc}"
                    logger.warning(
                        "新模块 %s 启动探针失败（降级）: %s",
                        name, exc,
                        extra={"startup_module": name, "error": str(exc)},
                    )
            try:
                from host_brain.app.startup import (  # noqa: PLC0415
                    warmup_codex_exec_intent_pool,
                    warmup_chat_llm_runtime_profiles,
                    warmup_chat_runtime_basics,
                    warmup_retrieval_runtime_basics,
                    warmup_surface_quote_runtime,
                )

                new_module_statuses["chat_runtime_warmup"] = warmup_chat_runtime_basics(
                    eager_persona=True,
                )
                new_module_statuses["chat_llm_runtime_warmup"] = warmup_chat_llm_runtime_profiles()
                new_module_statuses["surface_quote_runtime_warmup"] = warmup_surface_quote_runtime()
                new_module_statuses["codex_exec_intent_warmup"] = warmup_codex_exec_intent_pool()
                new_module_statuses["retrieval_runtime_warmup"] = warmup_retrieval_runtime_basics()
            except Exception as _chat_warmup_exc:
                new_module_statuses["chat_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["chat_llm_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["surface_quote_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["codex_exec_intent_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["retrieval_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                logger.warning(
                    "Chat runtime 预热失败（降级）: %s",
                    _chat_warmup_exc,
                )

            # [选项二] 接入 StateArbiter：注册状态源（WorldCanon、EpisodicStore）
            try:
                from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415
                from host_brain.app.memory.state_arbiter import StateSource  # noqa: PLC0415
                _arbiter = get_state_arbiter()

                # 注册 WorldCanonStore（最高优先级 SSOT）
                try:
                    _world_store = _build_world_canon_reader(ledger=ledger)

                    _world_get_fact = lambda fact_key, _store=_world_store: read_world_canon_fact(
                        _store,
                        fact_key,
                    )

                    _arbiter.register_source(StateSource.WORLD_CANON, _world_get_fact)
                    logger.info("StateArbiter: WorldCanonStore 已注册")
                except Exception as _exc:
                    logger.warning("StateArbiter: WorldCanonStore 注册失败（降级）: %s", _exc)

                # 注册 EpisodicStore（取最新记录）
                try:
                    from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

                    def _episodic_get_fact(fact_key: str) -> Any:
                        return read_episodic_latest_fact(
                            get_episodic_store(),
                            fact_key,
                        )

                    _arbiter.register_source(StateSource.EPISODIC_STORE, _episodic_get_fact)
                    logger.info("StateArbiter: EpisodicStore 已注册（lazy）")
                except Exception as _exc:
                    logger.warning("StateArbiter: EpisodicStore 注册失败（降级）: %s", _exc)

                new_module_statuses["state_arbiter_sources"] = (
                    f"OK:{len(_arbiter._sources)} sources (episodic lazy)"
                )
            except Exception as _sa_exc:
                logger.warning("StateArbiter 状态源注册失败（降级）: %s", _sa_exc)
                new_module_statuses["state_arbiter_sources"] = f"DEGRADED: {_sa_exc}"

            _edge_health: dict[str, Any] | None = None

            # [P1] Edge RPC 健康探测：启动时检查 Edge 是否可达
            try:
                from host_brain.app.startup import (  # noqa: PLC0415
                    build_edge_dependency_probe_statuses,
                    probe_edge_rpc_health,
                )
                _edge_health = await probe_edge_rpc_health()
                _edge_available = bool(_edge_health.get("available", False))
                _edge_status = str(_edge_health.get("status") or "").strip().upper()
                if _edge_status == "SKIPPED":
                    new_module_statuses["edge_rpc"] = f"SKIPPED: {_edge_health.get('detail', 'gateway-only mode')}"
                    logger.info("Edge RPC 启动探针已跳过: %s", _edge_health.get("detail", ""))
                elif _edge_available:
                    new_module_statuses["edge_rpc"] = f"OK (latency={_edge_health.get('latency_ms', 0)}ms)"
                    logger.info(
                        "Edge RPC 可达: %s (latency=%dms)",
                        _edge_health.get("base_url", ""),
                        _edge_health.get("latency_ms", 0),
                    )
                else:
                    new_module_statuses["edge_rpc"] = f"DEGRADED: {_edge_health.get('detail', 'unreachable')}"
                    logger.warning(
                        "⚠️ Edge RPC 不可达（降级模式）: %s",
                        _edge_health.get("detail", ""),
                        extra={"edge_health": _edge_health},
                    )
            except Exception as _edge_exc:
                new_module_statuses["edge_rpc"] = f"DEGRADED: {_edge_exc}"
                logger.warning("Edge RPC 健康探测失败（降级）: %s", _edge_exc)

            new_module_statuses.update({
                "scheduler_seed_catalog": "DEFERRED: governed scheduler composition",
                "distillation_engine_scheduler": "DEFERRED: governed scheduler composition",
                "codex_immune_scheduler": "DEFERRED: governed scheduler composition",
                "long_term_distiller_scheduler": "DEFERRED: governed scheduler composition",
                "audit_retention_scheduler": "DEFERRED: governed scheduler composition",
                "proactive_talk_scheduler": "DEFERRED: governed scheduler composition",
                "personality_tick_registry": "DEFERRED: governed scheduler composition",
            })

            # [builtin_tools_catalog] 将内置 READ 工具注册到 ToolCatalog / ToolBroker
            try:
                from host_brain.app.dependencies import (  # noqa: PLC0415
                    get_tool_catalog, get_tool_broker, get_skill_registry,
                )
                from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
                import hashlib as _hashlib  # noqa: PLC0415
                import json as _json_tools  # noqa: PLC0415

                _catalog = get_tool_catalog()
                _broker = get_tool_broker()
                _skill_reg = get_skill_registry()

                _BUILTIN_TOOL_DEFS = [
                    ("file_list",      "READ", "NONE", ["file"],   "列出目录内容（只读）"),
                    ("file_read",      "READ", "NONE", ["file"],   "读取文件内容（只读）"),
                    ("memory_search",  "READ", "NONE", ["memory"], "语义检索记忆库"),
                    ("system_info",    "READ", "NONE", ["system"], "查询系统信息"),
                    ("system_resource_snapshot", "READ", "NONE", ["system"], "查询整机资源快照"),
                    ("process_resource_top", "READ", "NONE", ["system"], "查询资源热点进程"),
                    ("resource_pressure_analysis", "READ", "NONE", ["system"], "分析整机资源压力根因"),
                    ("resource_manager_status", "READ", "NONE", ["system"], "查询自动资源管家状态"),
                    ("network_status", "READ", "NONE", ["system"], "查询网络状态"),
                    ("web_search",     "READ", "NONE", ["web"],    "执行网络搜索"),
                ]

                _registered_tools = 0
                for _tid, _sel, _auth, _tags, _desc in _BUILTIN_TOOL_DEFS:
                    _skill_meta = _skill_reg.get(_tid)
                    if _skill_meta is None:
                        continue
                    _schema = {"type": "object", "properties": {}}
                    _schema_hash = "sha256:" + _hashlib.sha256(
                        _json_tools.dumps(_schema, sort_keys=True).encode()
                    ).hexdigest()
                    _tool_meta = ToolMeta(
                        id=_tid, name=_tid, desc=_desc,
                        schema_hash=_schema_hash,
                        side_effect_level=_sel, auth_level=_auth,
                        domain_tags=tuple(_tags), version="1.0",
                    )
                    try:
                        _catalog.register(_tool_meta)
                    except Exception:
                        pass  # 已注册则跳过
                    _broker.register_handler(
                        _tid,
                        lambda args, ctx, _h=_skill_meta.handler: _h(args or {}, ctx or {}),
                    )
                    _registered_tools += 1

                new_module_statuses["builtin_tools_catalog"] = f"OK ({_registered_tools} tools registered)"
                logger.info("内置工具已注册到 ToolCatalog: %d 项", _registered_tools)
            except Exception as _tool_exc:
                new_module_statuses["builtin_tools_catalog"] = f"DEGRADED: {_tool_exc}"
                logger.warning("内置工具注册失败（降级）: %s", _tool_exc)

            # [personality_tools_catalog] 将 Personality / Scheduler Skills 注册到 ToolCatalog
            try:
                from host_brain.app.dependencies import get_tool_catalog, get_tool_broker  # noqa: PLC0415
                from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
                from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
                from host_brain.skills.scheduler_registry import build_scheduler_skill_registry  # noqa: PLC0415
                import hashlib as _hlib  # noqa: PLC0415
                import json as _jtools  # noqa: PLC0415

                _cat = get_tool_catalog()
                _brk = get_tool_broker()

                # Personality skills: diary_writer, dream_generator, desktop_note, focus_mode, proactive_talk
                _personality_reg = build_personality_skill_registry()
                _PERSONALITY_TOOL_DEFS = [
                    ("diary_writer",    "WRITE", "LOW",  ["memory", "file"],   "每日日记生成：汇总当日对话→LLM→双写文件+Episodic"),
                    ("dream_generator", "WRITE", "LOW",  ["memory"],           "梦境生成：读取Episodic→LLM生成梦境叙事→写回Episodic"),
                    ("desktop_note",    "WRITE", "LOW",  ["system", "file"],   "桌面留言：创建/更新/删除文本文件（路径穿越防护）"),
                    ("focus_mode",      "READ",  "NONE", ["system"],           "专注模式切换：进入/退出专注模式"),
                    ("proactive_talk",  "WRITE", "LOW",  ["chat"],             "主动对话：空闲超阈值时触发LLM生成问候并推送到Edge"),
                ]
                # Scheduler skills: scheduler_add, scheduler_cancel, scheduler_list
                _scheduler_reg = build_scheduler_skill_registry()
                _SCHEDULER_TOOL_DEFS = [
                    ("scheduler_add",    "WRITE", "MEDIUM", ["system"], "向调度器添加定时任务"),
                    ("scheduler_cancel", "WRITE", "MEDIUM", ["system"], "取消/删除调度任务（不可逆）"),
                    ("scheduler_list",   "READ",  "NONE",   ["system"], "列出当前所有调度任务"),
                ]

                _personality_count = 0
                for _reg, _defs in [(_personality_reg, _PERSONALITY_TOOL_DEFS), (_scheduler_reg, _SCHEDULER_TOOL_DEFS)]:
                    for _tid, _sel, _auth, _tags, _desc in _defs:
                        _smeta = _reg.get(_tid)
                        if _smeta is None:
                            continue
                        _schema = {"type": "object", "properties": {}}
                        _shash = "sha256:" + _hlib.sha256(
                            _jtools.dumps(_schema, sort_keys=True).encode()
                        ).hexdigest()
                        _tmeta = ToolMeta(
                            id=_tid, name=_tid, desc=_desc,
                            schema_hash=_shash,
                            side_effect_level=_sel, auth_level=_auth,
                            domain_tags=tuple(_tags), version="1.0",
                        )
                        try:
                            _cat.register(_tmeta)
                        except Exception:
                            pass
                        _brk.register_handler(
                            _tid,
                            lambda args, ctx, _h=_smeta.handler: _h(args or {}, ctx or {}),
                        )
                        _personality_count += 1

                new_module_statuses["personality_tools_catalog"] = f"OK ({_personality_count} tools registered)"
                logger.info("Personality/Scheduler 工具已注册到 ToolCatalog: %d 项", _personality_count)
            except Exception as _pt_exc:
                new_module_statuses["personality_tools_catalog"] = f"DEGRADED: {_pt_exc}"
                logger.warning("Personality 工具注册失败（降级）: %s", _pt_exc)

            # [memory_maintenance_catalog] 注册长期记忆蒸馏/睡眠回放工具到 ToolCatalog
            try:
                from host_brain.app.dependencies import get_tool_catalog, get_tool_broker, get_ledger as _get_ledger  # noqa: PLC0415
                from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
                import hashlib as _hlib2  # noqa: PLC0415
                import json as _jtools2  # noqa: PLC0415

                _cat2 = get_tool_catalog()
                _brk2 = get_tool_broker()

                _schema_lt = {"type": "object", "properties": {}}
                _shash_lt = "sha256:" + _hlib2.sha256(
                    _jtools2.dumps(_schema_lt, sort_keys=True).encode()
                ).hexdigest()
                _tmeta_lt = ToolMeta(
                    id="long_term_memory_distillation",
                    name="long_term_memory_distillation",
                    desc="长期记忆蒸馏：从EpisodicStore召回高频记忆→LLM摘要→生成World Canon提案",
                    schema_hash=_shash_lt,
                    side_effect_level="WRITE", auth_level="LOW",
                    domain_tags=("memory",), version="1.0",
                )
                try:
                    _cat2.register(_tmeta_lt)
                except Exception:
                    pass

                _long_term_distillation_handler = (
                    lambda args, ctx: run_long_term_distillation_handler(args, ctx)
                )

                _brk2.register_handler("long_term_memory_distillation", _long_term_distillation_handler)
                _tmeta_sleep = ToolMeta(
                    id="life_memory_sleep_replay",
                    name="life_memory_sleep_replay",
                    desc="生命记忆睡眠回放：执行 HOT/WARM/COLD/GONE 遗忘分层并强化高显著经历",
                    schema_hash=_shash_lt,
                    side_effect_level="WRITE", auth_level="LOW",
                    domain_tags=("memory",), version="1.0",
                )
                try:
                    _cat2.register(_tmeta_sleep)
                except Exception:
                    pass

                _brk2.register_handler(
                    "life_memory_sleep_replay",
                    lambda args, ctx: run_life_memory_sleep_replay_handler(args, ctx),
                )
                new_module_statuses["long_term_distillation_catalog"] = "OK (2 tools registered)"
                logger.info("长期蒸馏/睡眠回放工具已注册到 ToolCatalog")
            except Exception as _lt_exc:
                new_module_statuses["long_term_distillation_catalog"] = f"DEGRADED: {_lt_exc}"
                logger.warning("记忆维护工具注册失败（降级）: %s", _lt_exc)

            new_module_statuses["security_immune_agent"] = (
                "DEFERRED: governed scheduler definition"
            )

            dependency_probes = _edge_health
            if dependency_probes is None:
                dependency_probes = await probe_edge_rpc_health()
            new_module_statuses["host_task_scheduler"] = (
                "RETIRED: governed SchedulerClock owns periodic execution"
            )
            try:
                from host_brain.agent_runtime.production_executor import (  # noqa: PLC0415
                    recover_interrupted_lightnovel_recommendation_jobs,
                )

                asyncio.create_task(
                    asyncio.to_thread(
                        recover_interrupted_lightnovel_recommendation_jobs,
                        limit=3,
                        max_age_hours=72,
                        push_gateway=True,
                    )
                )
                new_module_statuses["lightnovel_recommender_recovery"] = "OK (scheduled)"
            except Exception as _lightnovel_recovery_exc:
                new_module_statuses["lightnovel_recommender_recovery"] = (
                    f"DEGRADED: {_lightnovel_recovery_exc}"
                )
                logger.warning(
                    "Lightnovel recommender interrupted job recovery startup degraded: %s",
                    _lightnovel_recovery_exc,
                )
            try:
                from host_brain.app.pentest_event_bus import (  # noqa: PLC0415
                    recover_zombie_pentest_runs,
                )

                asyncio.create_task(
                    asyncio.to_thread(recover_zombie_pentest_runs, max_age_hours=72)
                )
                new_module_statuses["pentest_zombie_recovery"] = "OK (scheduled)"
            except Exception as _pentest_recovery_exc:
                new_module_statuses["pentest_zombie_recovery"] = (
                    f"DEGRADED: {_pentest_recovery_exc}"
                )
                logger.warning(
                    "Pentest zombie run recovery startup degraded: %s",
                    _pentest_recovery_exc,
                )
            new_module_statuses["life_continuity_loop"] = (
                "RETIRED: governed SchedulerClock life_tick owns periodic Life commands"
            )
            try:
                from host_brain.app.startup import reconcile_startup_degradation  # noqa: PLC0415

                _degradation_ts_ms = int(time.time() * 1000)
                reconcile_startup_degradation(
                    new_module_statuses,
                    source_ref=f"startup-probe://runtime/{_degradation_ts_ms}",
                    now_ms=_degradation_ts_ms,
                )
            except Exception as _degradation_exc:
                # Degradation projection must never make the existing startup
                # guardian unavailable; its feature flag is the rollback path.
                logger.warning(
                    "Startup degradation projection skipped: %s",
                    _degradation_exc,
                )
            report = build_startup_report(
                runtime_statuses=new_module_statuses,
                dependency_probes=build_edge_dependency_probe_statuses(dependency_probes),
                request_id=new_request_id(),
                ledger=ledger,
                ts_ms=int(time.time() * 1000),
            )
            from host_brain.app.startup_snapshot import (  # noqa: PLC0415
                build_startup_snapshot,
                set_current_startup_snapshot,
            )

            set_current_startup_snapshot(
                build_startup_snapshot(report, ts_ms=int(report.get("ts_ms") or time.time() * 1000))
            )
            emit_runtime_bootstrap_logs(startup_report=report)
            logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})

            # 新模块中有降级时，明显提示
            degraded_modules = [k for k, v in new_module_statuses.items() if _startup_status_is_degraded(v)]
            if degraded_modules:
                logger.warning(
                    "⚠️ 以下新模块启动时处于降级状态，请检查配置: %s",
                    degraded_modules,
                    extra={"degraded_modules": degraded_modules},
                )

            # 注册主事件循环到 vts 模块，确保 asyncio.to_thread worker 线程能通过
            # run_coroutine_threadsafe 正确提交 VTS 驱动协程到主循环。
            try:
                from host_brain.channels.vts import register_main_loop as _register_vts_loop  # noqa: PLC0415
                _register_vts_loop(asyncio.get_running_loop())
                logger.info("VTS 主事件循环已注册")
            except Exception as _vts_loop_exc:
                logger.warning("VTS 主事件循环注册失败（降级）: %s", _vts_loop_exc)

            # [ANSAgent] 注册主事件循环到 ANSAgent，使 Timer 线程可跨线程发布 Tribe 事件
            # Phase 1：ANSAgent.emit_from_thread() 依赖此循环引用
            try:
                from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

                get_ans_agent().set_event_loop(asyncio.get_running_loop())
                logger.info("ANSAgent 主事件循环已注册")
            except Exception as _ans_loop_exc:
                logger.warning("ANSAgent 主事件循环注册失败（降级）: %s", _ans_loop_exc)

            # [ModeTransition] ActivityMonitor 启动：游戏进程检测 + 空闲检测 + 早间唤醒
            try:
                from host_brain.core.activity_monitor import ActivityMonitor  # noqa: PLC0415
                from host_brain.app.dependencies import get_hard_state, set_sys_mode  # noqa: PLC0415
                from host_brain.app.dependencies import set_activity_monitor  # noqa: PLC0415

                # 初始化 ModeTransitionService（obs_routes 也会延迟初始化，此处确保提前可用）
                from host_brain.core.mode_transition import ModeTransitionService  # noqa: PLC0415
                _mode_service = ModeTransitionService(
                    get_hard_state=get_hard_state,
                    set_sys_mode=set_sys_mode,
                    ledger=ledger,
                )

                _activity_monitor = ActivityMonitor(mode_transition_service=_mode_service)
                _activity_monitor.start()
                set_activity_monitor(_activity_monitor)  # 注册到 dependencies 供消息链路调用

                # 将 ModeTransitionService 实例注入到 obs_routes 模块，
                # 确保 /obs/sys-mode API 端点和 ActivityMonitor 共享同一实例
                # （避免历史记录/规则开关各自独立的问题）
                try:
                    import host_brain.app.obs_routes_sysmode as _obs_mod  # noqa: PLC0415
                    with _obs_mod._mode_transition_lock:
                        _obs_mod._mode_transition_service = _mode_service
                    logger.info("ModeTransitionService 已注入 obs_routes（单例共享）")
                except Exception as _inject_exc:
                    logger.warning("ModeTransitionService 注入 obs_routes 失败（降级）: %s", _inject_exc)

                new_module_statuses["activity_monitor"] = (
                    f"OK (gaming_check={_activity_monitor._gaming_interval}s, "
                    f"idle_check={_activity_monitor._idle_interval}s)"
                )
                logger.info(
                    "ActivityMonitor 已启动 (gaming_interval=%ds, idle_interval=%ds)",
                    _activity_monitor._gaming_interval,
                    _activity_monitor._idle_interval,
                )
            except Exception as _am_exc:
                new_module_statuses["activity_monitor"] = f"DEGRADED: {_am_exc}"
                logger.warning("ActivityMonitor 启动失败（降级）: %s", _am_exc)

            # 整机资源管家：自动采样 CPU/GPU/内存/显存，压力触发 LLM 分析。
            try:
                if not _background_tasks_enabled:
                    new_module_statuses["system_resource_manager"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                elif not core_settings.resource_manager_enabled:
                    new_module_statuses["system_resource_manager"] = "SKIPPED: HOST_RESOURCE_MANAGER_ENABLED=false"
                else:
                    from host_brain.app.dependencies import set_resource_manager  # noqa: PLC0415
                    from host_brain.core.resource_manager import SystemResourceManager  # noqa: PLC0415

                    _resource_manager = SystemResourceManager()
                    _resource_manager.start(
                        run_immediately=core_settings.resource_manager_run_on_startup
                    )
                    set_resource_manager(_resource_manager)
                    _rm_status = _resource_manager.get_status()
                    new_module_statuses["system_resource_manager"] = (
                        f"OK (interval={_rm_status.get('interval_s')}s, "
                        f"llm={_rm_status.get('llm_enabled')}, "
                        f"auto_gc={_rm_status.get('auto_gc_enabled')}, "
                        f"auto_trim={_rm_status.get('auto_trim_enabled')})"
                    )
                    logger.info("SystemResourceManager 已启动: %s", new_module_statuses["system_resource_manager"])
            except Exception as _rm_exc:
                new_module_statuses["system_resource_manager"] = f"DEGRADED: {_rm_exc}"
                logger.warning("SystemResourceManager 启动失败（降级）: %s", _rm_exc)

            # 服务健康心跳：定时探测 Edge/Host，避免总览一直显示「未知」
            try:
                from host_brain.observability.service_health_heartbeat import start_heartbeat
                start_heartbeat(interval_sec=30.0)
            except Exception as hb_exc:
                logger.warning("Service health heartbeat start skipped: %s", hb_exc)

            # [Tribe 基础设施] 初始化 TribeEventBus 单例 + HostTribeRegistry 单例
            # Phase 0：确保所有族 Agent 在启动时可以通过 get_tribe_event_bus() /
            # get_host_tribe_registry() 拿到同一个实例，而不是各自懒加载出不同对象。
            try:
                from host_brain.app.internal_tribe_routes import (  # noqa: PLC0415
                    get_tribe_event_bus,
                    set_tribe_event_bus,
                )
                from host_brain.tribe.event_bus import InMemoryTribeEventBus  # noqa: PLC0415
                from host_brain.tribe.registry import (  # noqa: PLC0415
                    get_host_tribe_registry,
                    set_host_tribe_registry,
                    HostTribeRegistry,
                )

                # 强制创建单例（若已存在则复用）
                _tribe_bus = get_tribe_event_bus()
                if _tribe_bus is None:
                    _tribe_bus = InMemoryTribeEventBus(max_events=2048)
                    set_tribe_event_bus(_tribe_bus)

                _tribe_registry = get_host_tribe_registry()
                if _tribe_registry is None:
                    _tribe_registry = HostTribeRegistry()
                    set_host_tribe_registry(_tribe_registry)

                _tribe_family_count = len(_tribe_registry.list_families())
                _tribe_agent_count = len(_tribe_registry.list_agents())
                new_module_statuses["tribe_infrastructure"] = (
                    f"OK (families={_tribe_family_count}, agents={_tribe_agent_count})"
                )
                logger.info(
                    "Tribe 基础设施已初始化: families=%d, agents=%d",
                    _tribe_family_count,
                    _tribe_agent_count,
                )
            except Exception as _tribe_exc:
                new_module_statuses["tribe_infrastructure"] = f"DEGRADED: {_tribe_exc}"
                logger.warning("Tribe 基础设施初始化失败（降级）: %s", _tribe_exc)

            # [Tribe 分发循环] 启动 TribeEventBus dispatch_loop + 注册各族 Agent 订阅
            # Phase 1：Bus 启动后，各族 Agent 的 handler 才能被正确触发
            try:
                from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

                _bus = get_tribe_event_bus()
                await _bus.start()

                new_module_statuses["tribe_state_persistence"] = (
                    "RETIRED: production Tribe registry is transient; durable Life state "
                    "belongs to the command repository"
                )

                # 注册各族 Agent 订阅（顺序：LimbicAgent → PrefrontalAgent → SocialBrainAgent → CortexAgent
                # → PainRewardAgent → EndocrineAgent → ImmuneAgent → HippocampusAgent → ANSAgent
                # → LanguageSystemAgent → SomaticAgent → PeripheralAgent → CircadianAgent
                # → ProprioceptionAgent → InitiativeGovernorAgent → DefaultModeAgent → MirrorNeuronAgent
                # → InteroceptionAgent → MotorCortexAgent → CerebellumAgent → GlymphaticAgent → VestibularAgent）
                _tribe_sub_errors: list[str] = []
                for _agent_name, _agent_factory in [
                    ("limbic_agent", "host_brain.tribe.limbic_agent.get_limbic_agent"),
                    ("prefrontal_agent", "host_brain.tribe.prefrontal_agent.get_prefrontal_agent"),
                    ("social_brain_agent", "host_brain.tribe.social_brain_agent.get_social_brain_agent"),
                    ("cortex_agent", "host_brain.tribe.cortex_agent.get_cortex_agent"),
                    ("pain_reward_agent", "host_brain.tribe.pain_reward_agent.get_pain_reward_agent"),  # T-13
                    ("endocrine_agent", "host_brain.tribe.endocrine_agent.get_endocrine_agent"),  # T-28
                    ("immune_agent", "host_brain.tribe.immune_agent.get_immune_agent"),  # T-35
                    ("hippocampus_agent", "host_brain.tribe.hippocampus_agent.get_hippocampus_agent"),  # T-36
                    ("ans_agent", "host_brain.tribe.ans_agent.get_ans_agent"),  # T-37
                    ("language_system_agent", "host_brain.tribe.language_system_agent.get_language_system_agent"),  # T-103
                    ("somatic_agent", "host_brain.tribe.somatic_agent.get_somatic_agent"),  # T-109
                    ("peripheral_agent", "host_brain.tribe.peripheral_agent.get_peripheral_agent"),  # T-110
                    ("circadian_agent", "host_brain.tribe.circadian_agent.get_circadian_agent"),
                    ("proprioception_agent", "host_brain.tribe.proprioception_agent.get_proprioception_agent"),
                    ("initiative_governor_agent", "host_brain.tribe.initiative_governor_agent.get_initiative_governor_agent"),
                    ("default_mode_agent", "host_brain.tribe.default_mode_agent.get_default_mode_agent"),
                    ("mirror_neuron_agent", "host_brain.tribe.mirror_neuron_agent.get_mirror_neuron_agent"),
                    ("interoception_agent", "host_brain.tribe.interoception_agent.get_interoception_agent"),
                    ("motor_cortex_agent", "host_brain.tribe.motor_cortex_agent.get_motor_cortex_agent"),
                    ("cerebellum_agent", "host_brain.tribe.cerebellum_agent.get_cerebellum_agent"),
                    ("glymphatic_agent", "host_brain.tribe.glymphatic_agent.get_glymphatic_agent"),
                    ("vestibular_agent", "host_brain.tribe.vestibular_agent.get_vestibular_agent"),
                ]:
                    try:
                        _module_path, _func_name = _agent_factory.rsplit(".", 1)
                        import importlib as _importlib  # noqa: PLC0415
                        _mod = _importlib.import_module(_module_path)
                        _agent = getattr(_mod, _func_name)()
                        _agent.register_subscriptions()
                        logger.info("Tribe 订阅已注册: %s", _agent_name)
                    except Exception as _sub_exc:
                        _tribe_sub_errors.append(f"{_agent_name}:{_sub_exc}")
                        logger.warning("Tribe 订阅注册失败（降级）: agent=%s exc=%s", _agent_name, _sub_exc)


                # T-130: initialize the shared EdgeCommandPusher singleton for host->edge commands.
                try:
                    new_module_statuses["edge_command_pusher"] = _init_edge_command_pusher_status(
                        logger=logger,
                    )
                except Exception as _ecp_exc:
                    new_module_statuses["edge_command_pusher"] = f"DEGRADED: {_ecp_exc}"
                    logger.warning("EdgeCommandPusher initialization failed (degraded): %s", _ecp_exc)

                # T-113~T-122: ANSAgent +VOICE_PIPELINE_STARTED/ENDED/CHANNEL_MESSAGE_RECEIVED(+3)
                #              CortexAgent +PLATFORM_CONNECTED/DISCONNECTED(+2) = +5（第二十一批）
                # T-123~T-132: EdgeCommandPusher 下行链路 + /edge_command 诊断端点（第二十二批）
                _total_subscriptions = 88
                _sub_status = (
                    f"OK (dispatch_loop=running, subscriptions={_total_subscriptions})"
                    if not _tribe_sub_errors
                    else f"DEGRADED: {'; '.join(_tribe_sub_errors)}"
                )
                new_module_statuses["tribe_dispatch_loop"] = _sub_status
                logger.info("TribeEventBus dispatch_loop 已启动，订阅注册完成")
            except Exception as _dispatch_exc:
                new_module_statuses["tribe_dispatch_loop"] = f"DEGRADED: {_dispatch_exc}"
                logger.warning("TribeEventBus dispatch_loop 启动失败（降级）: %s", _dispatch_exc)

        except Exception as exc:
            logger.warning("Startup probe degraded: %s", exc, exc_info=True)
        try:
            yield
        finally:
            await _shutdown_runtime(
                activity_monitor=_activity_monitor,
                resource_manager=_resource_manager,
                logger=logger,
            )

    return _lifespan()

# P3-2：global_exception_handler 和 add_request_id_and_audit 已拆分到子模块：
# - global_exception_handler → http_hooks_outbound.global_exception_handler（已导入）
# - add_request_id_and_audit → http_hooks_inbound.add_request_id_and_audit（已导入）
# 此处保留导入的版本，不再重复定义。
