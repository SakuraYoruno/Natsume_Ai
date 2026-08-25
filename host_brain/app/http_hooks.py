"""Host HTTP 生命周期、请求钩子与全局异常投影的组合入口。"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Mapping

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.lifecycle_runtime._shared import (
    read_episodic_latest_fact,
    read_world_canon_fact,
)
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.app.host_lifecycle_composition import HostLifecycleContainer
from host_brain.app.tribe_composition import (
    TribeRuntimeAgentPorts,
)
from host_brain.app.tribe_runtime_scope import TribeRuntimeScope
from host_brain.tribe.application_ports import TribeApplicationPorts
# P3-2：导入拆分后的状态判定；请求/异常处理器由组合根直接绑定 canonical 子模块。
from host_brain.app.http_hooks_outbound import startup_status_is_degraded
from host_brain.app.settings.core import get_core_settings
from host_brain.app.settings.persistence import get_persistence_settings
from host_brain.app.settings.scheduler import get_scheduler_settings
from host_brain.core.ids import new_request_id
from host_brain.composition.process_runtime_shutdown import (
    ProcessRuntimeShutdownDependencies,
    shutdown_process_runtime,
)

def _publish_startup_probe_batch(
    *,
    statuses: Mapping[str, Any],
    startup_probe_sink: Callable[[Mapping[str, Any]], None] | None,
    logger: Any,
) -> bool:
    """仅经当前应用拥有的 sink 发布启动观测，不能借观测路径写入第二套降级真相。"""

    if startup_probe_sink is None:
        logger.warning(
            "Startup probe batch is unbound; skipping degradation projection",
            extra={"startup_probe_status_count": len(statuses)},
        )
        return False
    startup_probe_sink(dict(statuses))
    return True


def _tool_runtime_startup_status(
    *,
    seeded_count: int,
    readiness: Mapping[str, Any],
) -> str:
    """Project executable Tool readiness, not Catalog presence alone."""

    status_value = str(readiness.get("status") or "UNSEEDED").strip().upper()
    catalog_ids = tuple(readiness.get("catalog_ids") or ())
    registered_ids = tuple(readiness.get("registered_ids") or ())
    executable_ids = tuple(readiness.get("governed_executable_ids") or ())
    exact_seed = int(seeded_count) == len(catalog_ids) == len(registered_ids)
    if status_value == "READY" and exact_seed and executable_ids:
        return (
            f"OK ({len(catalog_ids)} tools registered, "
            f"{len(executable_ids)} governed executors)"
        )
    return (
        f"DEGRADED: {status_value} "
        f"(seeded={int(seeded_count)}, catalog={len(catalog_ids)}, "
        f"registered={len(registered_ids)}, governed_executors={len(executable_ids)})"
    )


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


def _sync_world_canon_index_on_startup(
    *,
    logger: Any,
    service: Any,
    world_root: str | None = None,
    ts_ms: int | None = None,
) -> str:
    resolved_world_root = str(
        world_root or get_persistence_settings().world_canon_root
    ).strip() or "host_brain/canon/world"
    sync_ts = int(ts_ms if ts_ms is not None else time.time() * 1000)

    try:
        from host_brain.memory.canon_sync import sync_world_canon_index  # noqa: PLC0415

        records = sync_world_canon_index(service=service, ts_ms=sync_ts)
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
    lifecycle_ports: LifecycleRuntimeApplicationPorts,
    host_lifecycle: HostLifecycleContainer,
    tribe_application_ports: TribeApplicationPorts,
    tribe_runtime_agent_ports: TribeRuntimeAgentPorts,
    tribe_runtime: TribeRuntimeScope,
    canon_service_provider: Callable[[], Any | None] | None = None,
    mode_transition_service_sink: Callable[[Any | None], None] | None = None,
    startup_probe_sink: Callable[[Mapping[str, Any]], None] | None = None,
    degradation_state_provider: Callable[[], Mapping[str, Any] | None] | None = None,
    tool_broker_provider: Callable[[], Any] | None = None,
    tool_catalog_provider: Callable[[], Any] | None = None,
    skill_registry_provider: Callable[[], Any] | None = None,
    begin_authority_runtime: Callable[[], Any] | None = None,
    close_authority_runtime: Callable[[], Any] | None = None,
    begin_governance_runtime: Callable[[], Any] | None = None,
    close_governance_runtime: Callable[[], Any] | None = None,
    defer_activity_monitor: bool = False,
    defer_resource_manager: bool = False,
    resource_manager_status_journal: Any | None = None,
) -> AsyncGenerator[None, None]:
    """返回 Host ASGI 生命周期上下文，按所有权顺序装配并在失败时逆序释放。

    authority/governance 运行时先于 Host 生命周期取得控制权；Tribe 端口和执行上下文
    只能绑定到该 Host scope。任一后续初始化失败都会撤销已取得的绑定与 owner，避免
    半启动进程遗留仍可分发事件或持有 lease 的后台组件。
    """
    @asynccontextmanager
    async def _lifespan() -> AsyncGenerator[None, None]:
        logger.info("Natsume AI Host Brain starting up ...")
        authority_runtime_started = False
        governance_runtime_started = False

        def close_started_runtime_owners() -> None:
            first_error: BaseException | None = None
            for started, close_runtime in (
                (governance_runtime_started, close_governance_runtime),
                (authority_runtime_started, close_authority_runtime),
            ):
                if not started or close_runtime is None:
                    continue
                try:
                    close_runtime()
                except BaseException as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error

        try:
            if begin_authority_runtime is not None:
                begin_authority_runtime()
                authority_runtime_started = True
            if begin_governance_runtime is not None:
                begin_governance_runtime()
                governance_runtime_started = True
            host_lifecycle.begin()
        except Exception:
            close_started_runtime_owners()
            raise
        tribe_ports_bound = False
        tribe_execution_activation = None
        try:
            tribe_runtime.bind_application_ports(
                owner=host_lifecycle,
                ports=tribe_application_ports,
            )
            tribe_ports_bound = True
            tribe_execution_activation = (
                tribe_runtime.activate_execution_context(
                    owner=host_lifecycle,
                )
            )
            ledger = get_ledger()
        except Exception:
            try:
                if tribe_execution_activation is not None:
                    tribe_runtime.deactivate_execution_context(
                        tribe_execution_activation
                    )
            finally:
                try:
                    if tribe_ports_bound:
                        tribe_runtime.unbind_application_ports(
                            owner=host_lifecycle
                        )
                finally:
                    try:
                        await host_lifecycle.aclose(logger=logger)
                    finally:
                        close_started_runtime_owners()
            raise
        lifecycle_services = host_lifecycle.ports
        try:
            # 预初始化新模块，确保启动时即可感知降级状态
            # Phase XIV v4：移除旧的 main_chain 适配字段（已被 cortex_orchestrator 架构取代）
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
                        logger=logger,
                        service=canon_service,
                    )
                    if canon_service is not None
                    else "DEFERRED: production Canon composition"
                )
            else:
                new_module_statuses["world_canon_sync"] = (
                    "DEFERRED: production Canon composition"
                )
            for name, factory in [
                ("memory_guard", lifecycle_services.memory_guard),
                ("state_arbiter", lifecycle_services.state_arbiter),
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
                ("distillation_engine", lifecycle_services.distillation_engine),
                ("semantic_recaller", lifecycle_services.semantic_recaller),
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
                    build_startup_application_ports,
                    warmup_codex_exec_intent_pool,
                    warmup_chat_llm_runtime_profiles,
                    warmup_chat_runtime_basics,
                    warmup_retrieval_runtime_basics,
                    warmup_surface_quote_runtime,
                )

                startup_ports = build_startup_application_ports(
                    lifecycle_services
                )
                new_module_statuses["chat_runtime_warmup"] = warmup_chat_runtime_basics(
                    ports=startup_ports,
                    eager_persona=True,
                    thalamus_agent_provider=(
                        tribe_runtime_agent_ports.get_thalamus_agent
                    ),
                )
                new_module_statuses["chat_llm_runtime_warmup"] = (
                    warmup_chat_llm_runtime_profiles(ports=startup_ports)
                )
                new_module_statuses["surface_quote_runtime_warmup"] = warmup_surface_quote_runtime()
                new_module_statuses["codex_exec_intent_warmup"] = (
                    warmup_codex_exec_intent_pool(ports=startup_ports)
                )
                new_module_statuses["retrieval_runtime_warmup"] = (
                    warmup_retrieval_runtime_basics(ports=startup_ports)
                )
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
                from host_brain.app.memory.state_arbiter import StateSource  # noqa: PLC0415
                _arbiter = lifecycle_services.state_arbiter()

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
                    def _episodic_get_fact(fact_key: str) -> Any:
                        return read_episodic_latest_fact(
                            lifecycle_services.episodic_store(),
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
                if _edge_status in {"SKIPPED", "NOT_TESTED"}:
                    new_module_statuses["edge_rpc"] = (
                        "NOT_TESTED: "
                        f"{_edge_health.get('detail', 'remote Edge not tested')}"
                    )
                    logger.info(
                        "Edge RPC 启动探针未执行（外部验收待定）: %s",
                        _edge_health.get("detail", ""),
                    )
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

            try:
                from host_brain.composition.tool_runtime import (  # noqa: PLC0415
                    seed_production_tool_runtime,
                )

                if (
                    tool_broker_provider is None
                    or tool_catalog_provider is None
                    or skill_registry_provider is None
                ):
                    raise RuntimeError("APP_LOCAL_TOOL_RUNTIME_NOT_COMPOSED")
                tool_broker = tool_broker_provider()
                tool_seed = seed_production_tool_runtime(
                    catalog=tool_catalog_provider(),
                    broker=tool_broker,
                    skill_registry=skill_registry_provider(),
                    lifecycle_ports=lifecycle_ports,
                )
                new_module_statuses["tool_runtime_catalog"] = _tool_runtime_startup_status(
                    seeded_count=tool_seed.count,
                    readiness=tool_broker.readiness_snapshot(),
                )
            except Exception as tool_seed_exc:
                new_module_statuses["tool_runtime_catalog"] = (
                    f"DEGRADED: {tool_seed_exc}"
                )
                logger.warning("Tool runtime composition failed: %s", tool_seed_exc)

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
                from host_brain.domain_packs.production_runtime import (  # noqa: PLC0415
                    recover_interrupted_production_jobs,
                )
                from host_brain.app.model_provider_composition import (  # noqa: PLC0415
                    ApplicationModelAttemptServicePort,
                )
                from host_brain.app.production_authority import (  # noqa: PLC0415
                    build_durable_production_authority_verifier,
                )

                asyncio.create_task(
                    asyncio.to_thread(
                        recover_interrupted_production_jobs,
                        artifacts_root=(
                            os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_ARTIFACTS_ROOT")
                            or "artifacts/codex_runs/lightnovel_recommender"
                        ),
                        limit=3,
                        max_age_hours=72,
                        push_deliveries=True,
                        skill_ids={"agent_lightnovel_recommender"},
                        capability_broker=lifecycle_services.capability_broker(),
                        model_attempt_service=ApplicationModelAttemptServicePort(
                            lifecycle_services.model_attempt_service,
                        ),
                        authority_verifier=(
                            build_durable_production_authority_verifier(
                                ledger_provider=get_ledger,
                            )
                        ),
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
            new_module_statuses["pentest_legacy_run_recovery"] = (
                "RETIRED: durable SecureRun receipts own recovery and query state"
            )
            new_module_statuses["life_continuity_loop"] = (
                "RETIRED: governed SchedulerClock life_tick owns periodic Life commands"
            )
            new_module_statuses["runtime_guardian_startup_probe"] = (
                "EXPORTED: app-local RuntimeGuardian observer"
                if startup_probe_sink is not None
                else "UNBOUND: app-local RuntimeGuardian observer unavailable"
            )
            _publish_startup_probe_batch(
                statuses=new_module_statuses,
                startup_probe_sink=startup_probe_sink,
                logger=logger,
            )
            report = build_startup_report(
                runtime_statuses=new_module_statuses,
                dependency_probes=build_edge_dependency_probe_statuses(dependency_probes),
                request_id=new_request_id(),
                ledger=ledger,
                ts_ms=int(time.time() * 1000),
            )
            emit_runtime_bootstrap_logs(startup_report=report)
            logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})

            # 新模块中有降级时，明显提示
            degraded_modules = [k for k, v in new_module_statuses.items() if startup_status_is_degraded(v)]
            if degraded_modules:
                logger.warning(
                    "以下新模块启动时处于降级状态，请检查配置: %s",
                    degraded_modules,
                    extra={"degraded_modules": degraded_modules},
                )

            new_module_statuses["ans_legacy_event_loop"] = (
                "RETIRED: canonical Life owns affect; Host does not bind an "
                "ANS cross-thread event loop"
            )

            # [ModeTransition] ActivityMonitor 启动：游戏进程检测 + 空闲检测 + 早间唤醒
            if defer_activity_monitor:
                new_module_statuses["activity_monitor"] = (
                    "DEFERRED: durable HardState authority must bind first"
                )
            else:
                try:
                    _activity_monitor = host_lifecycle.start_activity_monitor(
                        ledger=ledger,
                    )
                    if mode_transition_service_sink is not None:
                        mode_transition_service_sink(
                            host_lifecycle.get_mode_transition_service()
                        )

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
                if defer_resource_manager:
                    new_module_statuses["system_resource_manager"] = (
                        "DEFERRED: Task Runtime and Host lease composition"
                    )
                elif not _background_tasks_enabled:
                    new_module_statuses["system_resource_manager"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                elif not core_settings.resource_manager_enabled:
                    new_module_statuses["system_resource_manager"] = "SKIPPED: HOST_RESOURCE_MANAGER_ENABLED=false"
                else:
                    _resource_manager = host_lifecycle.start_resource_manager(
                        status_journal=resource_manager_status_journal,
                        degradation_state_provider=degradation_state_provider,
                        run_immediately=(
                            core_settings.resource_manager_run_on_startup
                        ),
                    )
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

            # [Tribe 基础设施] 只使用当前 Host scope 已绑定的 fresh
            # EventBus/State/Registry。Legacy Agent helper 在 active lifespan
            # 中只能借用这三个 exact 对象，不得懒建第二套进程真相。
            try:
                _tribe_family_count = len(
                    tribe_runtime.tribe_registry.list_families()
                )
                _tribe_agent_count = len(
                    tribe_runtime.tribe_registry.list_agents()
                )
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
                _bus = tribe_runtime.event_bus
                await _bus.start()

                new_module_statuses["tribe_state_persistence"] = (
                    "RETIRED: production Tribe registry is transient; durable Life state "
                    "belongs to the command repository"
                )

                _tribe_sub_errors = tribe_runtime.register_subscriptions(logger=logger)

                # T-113~T-122: ANSAgent +VOICE_PIPELINE_STARTED/ENDED/CHANNEL_MESSAGE_RECEIVED(+3)
                #              CortexAgent +PLATFORM_CONNECTED/DISCONNECTED(+2) = +5（第二十一批）
                # T-123~T-132 compatibility event subscriptions remain; the
                # former process-global Edge command owner is retired.
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
            if mode_transition_service_sink is not None:
                mode_transition_service_sink(None)
            try:
                # Stop this Host-owned Tribe bus before releasing its exact
                # execution context and application-port owner.
                await shutdown_process_runtime(
                    activity_monitor=None,
                    resource_manager=None,
                    logger=logger,
                    dependencies=ProcessRuntimeShutdownDependencies(
                        get_tribe_event_bus=(
                            lambda event_bus=tribe_runtime.event_bus: event_bus
                        ),
                    ),
                )
            finally:
                try:
                    close_started_runtime_owners()
                finally:
                    try:
                        if tribe_execution_activation is not None:
                            tribe_runtime.deactivate_execution_context(
                                tribe_execution_activation
                            )
                    finally:
                        try:
                            if tribe_ports_bound:
                                tribe_runtime.unbind_application_ports(
                                    owner=host_lifecycle
                                )
                        finally:
                            await host_lifecycle.aclose(logger=logger)

    return _lifespan()
