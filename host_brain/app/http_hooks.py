"""HTTP lifecycle, middleware, and global exception hooks."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.schemas import ErrorResponse
from host_brain.app.http_hooks_runtime_helpers import (
    read_episodic_latest_fact,
    read_style_canon_fact,
    read_world_canon_fact,
    run_long_term_distillation_handler,
    run_proactive_intent_dispatch,
)
# P3-2：导入拆分后的子模块
from host_brain.app.http_hooks_inbound import add_request_id_and_audit, should_emit_request_info
from host_brain.app.http_hooks_outbound import global_exception_handler, startup_status_is_degraded
from host_brain.app.http_hooks_middleware import (
    parse_bool_env as _middleware_parse_bool_env,
    build_scheduler_emotion_state as _middleware_build_scheduler_emotion_state,
    build_personality_tick_status as _middleware_build_personality_tick_status,
)
from host_brain.core.ids import new_request_id

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


def _parse_bool_env(name: str, *, default: bool) -> bool:
    # P3-2：委托给 http_hooks_middleware.parse_bool_env
    return _middleware_parse_bool_env(name, default=default)


def _build_scheduler_emotion_state() -> dict[str, Any]:
    """构建调度器门控使用的情绪/内分泌快照。P3-2：委托给 http_hooks_middleware。"""
    return _middleware_build_scheduler_emotion_state()


def _build_audit_retention_task_config() -> Any:
    from host_brain.scheduler.task_registry import (  # noqa: PLC0415
        SchedulerTaskConfig,
        TaskPriority,
        TriggerMode,
    )

    return SchedulerTaskConfig(
        task_id="audit_retention_daily",
        trigger_mode=TriggerMode.CRON,
        trigger_config={"hour": 2, "minute": 0},
        intent_template={
            "skill_name": "audit_retention",
            "args": {},
            "proposer_node_id": "scheduler",
        },
        priority=TaskPriority.LOW,
        mode_allowlist=["normal", "sleep"],
        enabled=True,
        description="审计数据保留策略：每日 02:00 清理过期审计数据",
    )


def _build_personality_tick_status(*, registry: Any, task_ids: list[str]) -> str:
    # P3-2：委托给 http_hooks_middleware.build_personality_tick_status
    return _middleware_build_personality_tick_status(registry=registry, task_ids=task_ids)


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


def _build_codex_self_heal_scheduler_task(*, interval_s: float) -> Any:
    from host_brain.app.automation.task_scheduler import ScheduledTask, TaskPriority  # noqa: PLC0415
    from host_brain.app.internal_codex_routes import run_scheduled_self_heal_auto_dispatch_cycle  # noqa: PLC0415

    async def _run_codex_self_heal_scheduler_task() -> None:
        await run_scheduled_self_heal_auto_dispatch_cycle()

    return ScheduledTask(
        task_id="codex_self_heal_auto_dispatch_tick",
        name="Codex self-heal auto-dispatch tick",
        fn=_run_codex_self_heal_scheduler_task,
        interval_s=float(interval_s),
        max_retries=1,
        retry_delay_s=15.0,
        enabled=True,
        priority=TaskPriority.P1,
    )


def _bootstrap_personality_scheduler(
    *,
    logger: Any,
    get_ledger: Callable[[], Any],
    retention_policy: Any,
    proactive_push_client_builder: Callable[[], Any] | None = None,
) -> dict[str, str]:
    from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
    from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415
    from host_brain.scheduler.scheduler_routes import set_scheduler_providers  # noqa: PLC0415
    from host_brain.scheduler.task_registry import (  # noqa: PLC0415
        SchedulerTaskConfig,
        SchedulerTaskRegistry,
        TaskPriority,
        TriggerMode,
    )

    registry = SchedulerTaskRegistry.get_instance()
    health_reporter = SchedulerHealthReporter(registry=registry)
    set_scheduler_providers(
        registry=registry,
        health_reporter=health_reporter,
    )

    proactive_push_state: dict[str, Any] = {
        "client": None,
        "detail": "proactive push client unavailable",
    }

    if proactive_push_client_builder is None:
        try:
            from host_brain.app.vision.proactive_push_client import (  # noqa: PLC0415
                build_proactive_push_client as proactive_push_client_builder,
            )
        except Exception as exc:
            proactive_push_client_builder = None
            proactive_push_state["detail"] = str(exc) or proactive_push_state["detail"]
            logger.warning("Proactive push client import degraded: %s", exc)

    if proactive_push_client_builder is not None:
        try:
            proactive_push_state["client"] = proactive_push_client_builder()
            proactive_push_state["detail"] = ""
        except Exception as exc:
            proactive_push_state["detail"] = str(exc) or proactive_push_state["detail"]
            logger.warning("Proactive push client init degraded: %s", exc)

    def _ui_pusher(message: dict[str, Any]) -> dict[str, Any]:
        client = proactive_push_state.get("client")
        if client is None:
            return {
                "success": False,
                "status": "SKIPPED",
                "detail": str(proactive_push_state.get("detail") or "proactive push client unavailable"),
            }
        return client.push(message)

    def _intent_dispatcher(system_intent: dict[str, Any]) -> dict[str, Any]:
        skill_name = str(system_intent.get("skill_name") or "").strip()
        if skill_name == "proactive_talk" and proactive_push_state.get("client") is None:
            return {
                "status": "SKIPPED",
                "reason_code": "PROACTIVE_PUSH_UNAVAILABLE",
                "detail": str(proactive_push_state.get("detail") or "proactive push client unavailable"),
            }
        return run_proactive_intent_dispatch(
            system_intent,
            logger=logger,
            get_ledger=get_ledger,
            retention_policy=retention_policy,
            ui_pusher=_ui_pusher,
        )

    scheduler = NatsumeScheduler(
        registry=registry,
        health_reporter=health_reporter,
        intent_dispatcher=_intent_dispatcher,
        get_emotion_state=_build_scheduler_emotion_state,
    )

    def _register_task(
        *,
        config: Any,
        success_message: str,
        failure_message: str,
    ) -> None:
        try:
            if registry.get(config.task_id) is None:
                registry.register(config)
                logger.info(success_message, config.task_id)
        except Exception as exc:
            logger.warning(failure_message, exc)

    _register_task(
        config=SchedulerTaskConfig(
            task_id="proactive_talk_periodic",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 180},
            intent_template={
                "skill_name": "proactive_talk",
                "args": {"idle_threshold_ms": 2700000},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "sleep"],
            enabled=True,
            description="主动对话：每 3 分钟检查一次，空闲 45 分钟后尝试发起问候",
        ),
        success_message="主动对话任务已注册: task_id=%s, interval=180s",
        failure_message="主动对话任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="evolution_cycle_daily",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 3, "minute": 0},
            intent_template={
                "skill_name": "evolution_cycle",
                "args": {"user_id": "user_1"},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.MEDIUM,
            mode_allowlist=["normal", "sleep"],
            enabled=True,
            description="daily evolution cycle task",
        ),
        success_message="进化周期任务已注册: task_id=%s, cron=03:00",
        failure_message="进化周期任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="inner_world_numeric_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": int(os.environ.get("HOST_INNER_WORLD_NUMERIC_TICK_INTERVAL_SECONDS") or 60)},
            intent_template={
                "skill_name": "inner_world_numeric_tick",
                "args": {"user_id": "user_1"},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            description="离线内心世界数值 tick：推进孤独感、期待值与情绪漂移，不写 inner_stream",
        ),
        success_message="离线内心世界数值任务已注册: task_id=%s, interval=60s",
        failure_message="离线内心世界数值任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="inner_world_monologue_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": int(os.environ.get("HOST_INNER_WORLD_MONOLOGUE_TICK_INTERVAL_SECONDS") or 300)},
            intent_template={
                "skill_name": "inner_world_monologue_tick",
                "args": {"user_id": "user_1"},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            description="离线内心世界独白 tick：按较慢节奏生成 inner_stream 独白",
        ),
        success_message="离线内心世界独白任务已注册: task_id=%s, interval=300s",
        failure_message="离线内心世界独白任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="silence_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 60},
            intent_template={
                "skill_name": "silence_tick",
                "args": {"user_id": "user_1"},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            description="沉默引擎 tick：每分钟恢复意愿与话题抗拒衰减",
        ),
        success_message="沉默引擎 tick 任务已注册: task_id=%s, interval=60s",
        failure_message="沉默引擎 tick 任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="body_sense_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 60},
            intent_template={
                "skill_name": "body_sense_tick",
                "args": {"user_id": "user_1"},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            description="身体感知 tick：每分钟更新困倦感并恢复疲劳值",
        ),
        success_message="身体感知 tick 任务已注册: task_id=%s, interval=60s",
        failure_message="身体感知 tick 任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="gaming_companion_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 300},
            intent_template={
                "skill_name": "gaming_companion_tick",
                "args": {
                    "user_id": "user_1",
                    "monitor": 1,
                },
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["gaming"],
            enabled=True,
            description="gaming 陪玩识图 tick：每 5 分钟截图分析并尝试陪玩反馈",
        ),
        success_message="gaming 陪玩识图 tick 任务已注册: task_id=%s, interval=300s",
        failure_message="gaming 陪玩识图 tick 任务注册失败（降级）: %s",
    )
    _register_task(
        config=SchedulerTaskConfig(
            task_id="emergent_growth_weekly",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"day_of_week": 6, "hour": 3, "minute": 0},
            intent_template={
                "skill_name": "emergent_growth_weekly",
                "args": {
                    "user_id": "user_1",
                    "lookback_days": 7,
                },
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "sleep"],
            enabled=True,
            description="涌现式成长：每周日 03:00 分析近期对话并沉淀人格微调",
        ),
        success_message="涌现式成长任务已注册: task_id=%s, cron=周日03:00",
        failure_message="涌现式成长任务注册失败（降级）: %s",
    )

    scheduler.start()

    tracked_task_ids = [
        "proactive_talk_periodic",
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
        "silence_tick",
        "body_sense_tick",
    ]
    proactive_push_ready = proactive_push_state.get("client") is not None
    proactive_push_status = (
        "OK"
        if proactive_push_ready
        else f"DEGRADED: {proactive_push_state.get('detail') or 'proactive push client unavailable'}"
    )

    return {
        "proactive_talk_scheduler": (
            "OK "
            f"(started; proactive_push={'ready' if proactive_push_ready else 'skip'}; "
            f"background_ticks=4)"
        ),
        "proactive_push_client": proactive_push_status,
        "personality_tick_registry": _build_personality_tick_status(
            registry=registry,
            task_ids=tracked_task_ids,
        ),
    }


def _sync_world_canon_index_on_startup(
    *,
    ledger: Any,
    logger: Any,
    world_root: str | None = None,
    ts_ms: int | None = None,
) -> str:
    resolved_world_root = str(
        world_root or os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world"
    ).strip() or "host_brain/canon/world"
    sync_ts = int(ts_ms if ts_ms is not None else time.time() * 1000)

    try:
        from host_brain.memory.canon_sync import sync_world_canon_index  # noqa: PLC0415

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
) -> AsyncGenerator[None, None]:
    @asynccontextmanager
    async def _lifespan() -> AsyncGenerator[None, None]:
        logger.info("Natsume AI Host Brain starting up ...")
        ledger = get_ledger()
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
            _retention_policy: Any | None = None
            _startup_eager_warmup = _parse_bool_env(
                "HOST_STARTUP_EAGER_WARMUP",
                default=False,
            )
            _background_tasks_enabled = _parse_bool_env(
                "HOST_BACKGROUND_TASKS_ENABLED",
                default=True,
            )
            if not _background_tasks_enabled:
                logger.warning(
                    "Background periodic schedulers disabled by HOST_BACKGROUND_TASKS_ENABLED=false"
                )
                new_module_statuses["proactive_push_client"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
            try:
                from host_brain.app.internal_codex_routes import (  # noqa: PLC0415
                    start_self_heal_auto_dispatch_monitor,
                )

                if not _background_tasks_enabled:
                    new_module_statuses["codex_self_heal_monitor"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                else:
                    _codex_monitor_state = start_self_heal_auto_dispatch_monitor(logger=logger)
                    if bool(_codex_monitor_state.get("scheduler_managed")):
                        new_module_statuses["codex_self_heal_monitor"] = "OK (scheduler-managed auto-dispatch)"
                    elif bool(_codex_monitor_state.get("enabled")):
                        new_module_statuses["codex_self_heal_monitor"] = "OK (auto-dispatch monitor enabled)"
                    else:
                        new_module_statuses["codex_self_heal_monitor"] = "SKIPPED: HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED=false"
            except Exception as _codex_monitor_exc:
                new_module_statuses["codex_self_heal_monitor"] = f"DEGRADED: {_codex_monitor_exc}"
                logger.warning("Codex self-heal auto-dispatch monitor startup degraded: %s", _codex_monitor_exc)
            new_module_statuses["world_canon_sync"] = _sync_world_canon_index_on_startup(
                ledger=ledger,
                logger=logger,
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
                    warmup_chat_llm_runtime_profiles,
                    warmup_chat_runtime_basics,
                    warmup_retrieval_runtime_basics,
                )

                new_module_statuses["chat_runtime_warmup"] = warmup_chat_runtime_basics(
                    eager_persona=True,
                )
                new_module_statuses["chat_llm_runtime_warmup"] = warmup_chat_llm_runtime_profiles()
                new_module_statuses["retrieval_runtime_warmup"] = warmup_retrieval_runtime_basics()
            except Exception as _chat_warmup_exc:
                new_module_statuses["chat_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["chat_llm_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                new_module_statuses["retrieval_runtime_warmup"] = f"DEGRADED: {_chat_warmup_exc}"
                logger.warning(
                    "Chat runtime 预热失败（降级）: %s",
                    _chat_warmup_exc,
                )

            # [选项二] 接入 StateArbiter：注册状态源（WorldCanon、StyleCanon、EpisodicStore）
            try:
                from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415
                from host_brain.app.memory.state_arbiter import StateSource  # noqa: PLC0415
                _arbiter = get_state_arbiter()

                # 注册 WorldCanonStore（最高优先级 SSOT）
                try:
                    from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

                    _world_store = WorldCanonStore()

                    _world_get_fact = lambda fact_key, _store=_world_store: read_world_canon_fact(
                        _store,
                        fact_key,
                    )

                    _arbiter.register_source(StateSource.WORLD_CANON, _world_get_fact)
                    logger.info("StateArbiter: WorldCanonStore 已注册")
                except Exception as _exc:
                    logger.warning("StateArbiter: WorldCanonStore 注册失败（降级）: %s", _exc)

                # 注册 StyleCanonStore
                try:
                    from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

                    _style_store = StyleCanonStore()

                    _style_get_fact = lambda fact_key, _store=_style_store: read_style_canon_fact(
                        _store,
                        fact_key,
                    )

                    _arbiter.register_source(StateSource.STYLE_CANON, _style_get_fact)
                    logger.info("StateArbiter: StyleCanonStore 已注册")
                except Exception as _exc:
                    logger.warning("StateArbiter: StyleCanonStore 注册失败（降级）: %s", _exc)

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

            # [Ref #100] DistillationEngine 蒸馏定时任务注册到 HostTaskScheduler（P1，每 30 分钟）
            try:
                if not _background_tasks_enabled:
                    raise RuntimeError("HOST_BACKGROUND_TASKS_ENABLED=false")
                from host_brain.app.dependencies import get_distillation_engine  # noqa: PLC0415
                from host_brain.app.automation.task_scheduler import (  # noqa: PLC0415
                    ScheduledTask, TaskPriority, get_scheduler,
                )

                _scheduler = get_scheduler()
                _distill_engine: Any | None = None

                def _get_distill_engine() -> Any:
                    nonlocal _distill_engine
                    if _distill_engine is None:
                        _distill_engine = get_distillation_engine()
                    return _distill_engine

                async def _run_distillation_task() -> None:
                    """DistillationEngine 蒸馏定时任务执行体。"""
                    report = await _get_distill_engine().run_batch()
                    logger.info(
                        "蒸馏任务完成: 候选=%d, 成功=%d, 跳过=%d, 失败=%d, 耗时=%dms",
                        report.total_candidates, report.distilled_count,
                        report.skipped_count, report.failed_count, report.duration_ms,
                    )

                _distill_task = ScheduledTask(
                    task_id="distillation_engine_periodic",
                    name="情节记忆蒸馏（每 10 分钟）",
                    fn=_run_distillation_task,
                    interval_s=600.0,  # 10 分钟
                    max_retries=2,
                    retry_delay_s=30.0,
                    enabled=True,
                    priority=TaskPriority.P1,
                )
                _scheduler.register(_distill_task)
                new_module_statuses["distillation_engine_scheduler"] = (
                    f"OK (task_id={_distill_task.task_id}, interval=600s)"
                )
                logger.info(
                    "DistillationEngine 蒸馏任务已注册到调度器: task_id=%s",
                    _distill_task.task_id,
                )
            except Exception as _distill_exc:
                if not _background_tasks_enabled:
                    new_module_statuses["distillation_engine_scheduler"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                else:
                    new_module_statuses["distillation_engine_scheduler"] = f"DEGRADED: {_distill_exc}"
                logger.warning("DistillationEngine 调度任务注册失败（降级）: %s", _distill_exc)

            # [Ref #31] LongTermDistiller 蒸馏任务注册到调度器
            # Codex self-heal auto-dispatch task registration for HostTaskScheduler.
            try:
                if not _background_tasks_enabled:
                    raise RuntimeError("HOST_BACKGROUND_TASKS_ENABLED=false")
                _codex_scheduler_enabled = _parse_bool_env(
                    "HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED",
                    default=False,
                )
                if not _codex_scheduler_enabled:
                    new_module_statuses["codex_self_heal_scheduler"] = (
                        "SKIPPED: HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED=false"
                    )
                else:
                    from host_brain.app.automation.task_scheduler import get_scheduler  # noqa: PLC0415

                    _codex_scheduler = get_scheduler()
                    _codex_interval_s = float(
                        max(
                            15,
                            min(
                                86400,
                                int(
                                    str(
                                        os.environ.get(
                                            "HOST_CODEX_SELF_HEAL_SCHEDULER_INTERVAL_SECONDS"
                                        )
                                        or "300"
                                    ).strip()
                                    or "300"
                                ),
                            ),
                        )
                    )
                    _codex_task = _build_codex_self_heal_scheduler_task(
                        interval_s=_codex_interval_s
                    )
                    if not any(
                        str(task.get("task_id") or "") == _codex_task.task_id
                        for task in _codex_scheduler.get_all_tasks_status()
                    ):
                        _codex_scheduler.register(_codex_task)
                    new_module_statuses["codex_self_heal_scheduler"] = (
                        f"OK (task_id={_codex_task.task_id}, interval={int(_codex_interval_s)}s)"
                    )
                    logger.info(
                        "Codex self-heal scheduler task registered: task_id=%s interval=%ss",
                        _codex_task.task_id,
                        int(_codex_interval_s),
                    )
            except Exception as _codex_scheduler_exc:
                if not _background_tasks_enabled:
                    new_module_statuses["codex_self_heal_scheduler"] = (
                        "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                    )
                else:
                    new_module_statuses["codex_self_heal_scheduler"] = (
                        f"DEGRADED: {_codex_scheduler_exc}"
                    )
                logger.warning(
                    "Codex self-heal scheduler task registration degraded: %s",
                    _codex_scheduler_exc,
                )

            try:
                if not _background_tasks_enabled:
                    raise RuntimeError("HOST_BACKGROUND_TASKS_ENABLED=false")
                from host_brain.memory.long_term_distiller import make_distillation_task_config  # noqa: PLC0415
                from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

                _distill_config = make_distillation_task_config()
                _task_registry = SchedulerTaskRegistry.get_instance()
                _task_registry.register(_distill_config)
                new_module_statuses["long_term_distiller_scheduler"] = (
                    f"OK (task_id={_distill_config.task_id})"
                )
                logger.info(
                    "LongTermDistiller 蒸馏任务已注册到调度器: task_id=%s trigger=%s",
                    _distill_config.task_id,
                    _distill_config.trigger_mode,
                )
            except Exception as _distill_exc:
                if not _background_tasks_enabled:
                    new_module_statuses["long_term_distiller_scheduler"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                else:
                    new_module_statuses["long_term_distiller_scheduler"] = f"DEGRADED: {_distill_exc}"
                logger.warning("LongTermDistiller 调度任务注册失败（降级）: %s", _distill_exc)

            # [Ref #34] AuditRetention 保留策略任务注册到调度器（每日 02:00 CRON）
            try:
                if not _background_tasks_enabled:
                    raise RuntimeError("HOST_BACKGROUND_TASKS_ENABLED=false")
                from host_brain.audit.retention import RetentionPolicy  # noqa: PLC0415
                from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

                _retention_policy = RetentionPolicy()
                _retention_registry = SchedulerTaskRegistry.get_instance()
                _retention_task = _build_audit_retention_task_config()
                if _retention_registry.get(_retention_task.task_id) is None:
                    _retention_registry.register(_retention_task)
                new_module_statuses["audit_retention_scheduler"] = (
                    f"OK (task_id={_retention_task.task_id})"
                )
                logger.info(
                    "AuditRetention 保留策略任务已注册到调度器: task_id=%s trigger=%s",
                    _retention_task.task_id,
                    _retention_task.trigger_mode,
                )
            except Exception as _ret_exc:
                if not _background_tasks_enabled:
                    new_module_statuses["audit_retention_scheduler"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                else:
                    new_module_statuses["audit_retention_scheduler"] = f"DEGRADED: {_ret_exc}"
                logger.warning("AuditRetention 调度任务注册失败（降级）: %s", _ret_exc)

            # [proactive_talk] 主动对话任务注册 + NatsumeScheduler 启动
            try:
                if not _background_tasks_enabled:
                    raise RuntimeError("HOST_BACKGROUND_TASKS_ENABLED=false")
                from host_brain.scheduler.task_registry import (  # noqa: PLC0415
                    SchedulerTaskRegistry, SchedulerTaskConfig, TriggerMode, TaskPriority,
                )
                from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415

                _proactive_registry = SchedulerTaskRegistry.get_instance()

                # 注册 proactive_talk 任务（每 3 分钟检查一次，空闲 45 分钟触发）
                _proactive_task = SchedulerTaskConfig(
                    task_id="proactive_talk_periodic",
                    trigger_mode=TriggerMode.INTERVAL,
                    trigger_config={"seconds": 180},  # 3 分钟
                    intent_template={
                        "skill_name": "proactive_talk",
                        "args": {"idle_threshold_ms": 2700000},  # 45 分钟空闲阈值
                        "proposer_node_id": "scheduler",
                    },
                    priority=TaskPriority.LOW,
                    mode_allowlist=["normal", "sleep"],
                    enabled=True,
                    description="主动对话：每 3 分钟检查一次，空闲 45 分钟后触发问候",
                )
                # 避免重复注册（重启时可能已存在）
                if _proactive_registry.get("proactive_talk_periodic") is None:
                    _proactive_registry.register(_proactive_task)

                # 构建 ui_pusher 回调（同步推送到 Edge）；失败时仅降级 proactive push，不阻断 core ticks
                _proactive_push_detail = ""
                try:
                    from host_brain.app.vision.proactive_push_client import build_proactive_push_client  # noqa: PLC0415

                    _push_client = build_proactive_push_client()
                    _ui_pusher = lambda message, _client=_push_client: _client.push(message)
                    new_module_statuses["proactive_push_client"] = "OK"
                except Exception as _push_exc:
                    _proactive_push_detail = str(_push_exc) or "proactive push client unavailable"
                    new_module_statuses["proactive_push_client"] = f"DEGRADED: {_proactive_push_detail}"
                    logger.warning("Proactive push client init degraded: %s", _push_exc)
                    _ui_pusher = (
                        lambda _message,
                        _detail=_proactive_push_detail: {
                            "success": False,
                            "status": "SKIPPED",
                            "detail": _detail,
                        }
                    )

                def _proactive_intent_dispatcher(
                    system_intent: dict[str, Any],
                    _logger: Any = logger,
                    _get_ledger: Callable[[], Any] = get_ledger,
                    _retention_policy: Any = _retention_policy,
                    _ui_pusher: Callable[[dict[str, Any]], dict[str, Any]] = _ui_pusher,
                    _push_detail: str = _proactive_push_detail,
                ) -> dict[str, Any]:
                    if (
                        str(system_intent.get("skill_name") or "").strip() == "proactive_talk"
                        and _push_detail
                    ):
                        return {
                            "status": "SKIPPED",
                            "reason_code": "PROACTIVE_PUSH_UNAVAILABLE",
                            "detail": _push_detail,
                        }
                    return run_proactive_intent_dispatch(
                        system_intent,
                        logger=_logger,
                        get_ledger=_get_ledger,
                        retention_policy=_retention_policy,
                        ui_pusher=_ui_pusher,
                    )

                from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
                from host_brain.scheduler.scheduler_routes import set_scheduler_providers  # noqa: PLC0415

                _health_reporter = SchedulerHealthReporter(registry=_proactive_registry)
                set_scheduler_providers(
                    registry=_proactive_registry,
                    health_reporter=_health_reporter,
                )
                _natsume_scheduler = NatsumeScheduler(
                    registry=_proactive_registry,
                    health_reporter=_health_reporter,
                    intent_dispatcher=_proactive_intent_dispatcher,
                    get_emotion_state=_build_scheduler_emotion_state,
                )

                # ── 注册进化周期任务（Phase XVI）：每日凌晨 3:00 触发 ──────
                try:
                    _evolution_task = SchedulerTaskConfig(
                        task_id="evolution_cycle_daily",
                        trigger_mode=TriggerMode.CRON,
                        trigger_config={"hour": 3, "minute": 0},
                        intent_template={
                            "skill_name": "evolution_cycle",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.MEDIUM,
                        mode_allowlist=["normal", "sleep"],
                        enabled=True,
                        description="daily evolution cycle task",
                    )
                    if _proactive_registry.get("evolution_cycle_daily") is None:
                        _proactive_registry.register(_evolution_task)
                        logger.info("进化周期任务已注册: task_id=evolution_cycle_daily, cron=03:00")
                except Exception as _evo_task_exc:
                    logger.warning("进化周期任务注册失败（降级）: %s", _evo_task_exc)

                # [Phase XVI] 注册 inner_world_numeric_tick / inner_world_monologue_tick
                try:
                    _inner_world_task = SchedulerTaskConfig(
                        task_id="inner_world_numeric_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": int(os.environ.get("HOST_INNER_WORLD_NUMERIC_TICK_INTERVAL_SECONDS") or 60)},
                        intent_template={
                            "skill_name": "inner_world_numeric_tick",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],  # 所有非锁定模式均运行
                        enabled=True,
                        description="Phase XVI 离线内心世界数值 tick：推进孤独感/期待值/情绪漂移积累",
                    )
                    if _proactive_registry.get("inner_world_numeric_tick") is None:
                        _proactive_registry.register(_inner_world_task)
                        logger.info("离线内心世界数值任务已注册: task_id=inner_world_numeric_tick, interval=60s")
                except Exception as _inner_world_exc:
                    logger.warning("离线内心世界数值任务注册失败（降级）: %s", _inner_world_exc)

                try:
                    _inner_world_monologue_task = SchedulerTaskConfig(
                        task_id="inner_world_monologue_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": int(os.environ.get("HOST_INNER_WORLD_MONOLOGUE_TICK_INTERVAL_SECONDS") or 300)},
                        intent_template={
                            "skill_name": "inner_world_monologue_tick",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],
                        enabled=True,
                        description="Phase XVI 离线内心世界独白 tick：按较慢节奏写入 inner_stream",
                    )
                    if _proactive_registry.get("inner_world_monologue_tick") is None:
                        _proactive_registry.register(_inner_world_monologue_task)
                        logger.info("离线内心世界独白任务已注册: task_id=inner_world_monologue_tick, interval=300s")
                except Exception as _inner_world_monologue_exc:
                    logger.warning("离线内心世界独白任务注册失败（降级）: %s", _inner_world_monologue_exc)

                # [Phase XVIII] 注册 silence_tick 任务（每分钟驱动沉默引擎意愿恢复）
                try:
                    _silence_task = SchedulerTaskConfig(
                        task_id="silence_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 60},  # 每分钟 tick 一次
                        intent_template={
                            "skill_name": "silence_tick",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],  # 所有非锁定模式均运行
                        enabled=True,
                        description="Phase XVIII 沉默引擎 tick：每分钟驱动回复意愿自然恢复和话题抗拒衰减",
                    )
                    if _proactive_registry.get("silence_tick") is None:
                        _proactive_registry.register(_silence_task)
                        logger.info("沉默引擎 tick 任务已注册: task_id=silence_tick, interval=60s")
                except Exception as _silence_exc:
                    logger.warning("沉默引擎 tick 任务注册失败（降级）: %s", _silence_exc)

                # [v1.12] 注册 body_sense_tick 任务（每分钟驱动身体感知：困倦感时段更新 + 疲劳自然恢复）
                try:
                    _body_sense_task = SchedulerTaskConfig(
                        task_id="body_sense_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 60},  # 每分钟 tick 一次
                        intent_template={
                            "skill_name": "body_sense_tick",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],  # 所有非锁定模式均运行
                        enabled=True,
                        description="v1.12 身体感知 tick：每分钟更新困倦感（基于时段）和疲劳自然恢复",
                    )
                    if _proactive_registry.get("body_sense_tick") is None:
                        _proactive_registry.register(_body_sense_task)
                        logger.info("身体感知 tick 任务已注册: task_id=body_sense_tick, interval=60s")
                except Exception as _body_sense_exc:
                    logger.warning("身体感知 tick 任务注册失败（降级）: %s", _body_sense_exc)

                # [GAP_ANALYSIS 14.2] 注册 gaming_companion_tick 任务（gaming 模式下每 5 分钟陪玩识图）
                try:
                    _gaming_task = SchedulerTaskConfig(
                        task_id="gaming_companion_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 300},  # 每 5 分钟截图分析一次
                        intent_template={
                            "skill_name": "gaming_companion_tick",
                            "args": {
                                "monitor": 1,
                            },
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["gaming"],  # 仅在 gaming 模式下运行
                        enabled=True,
                        description="gaming 陪玩识图 tick：每 5 分钟截图 → 多模态 LLM 分析 → 推送陪玩评论",
                    )
                    if _proactive_registry.get("gaming_companion_tick") is None:
                        _proactive_registry.register(_gaming_task)
                        logger.info("gaming 陪玩识图 tick 任务已注册: task_id=gaming_companion_tick, interval=300s, mode=gaming")
                except Exception as _gaming_exc:
                    logger.warning("gaming 陪玩识图 tick 任务注册失败（降级）: %s", _gaming_exc)

                # [Phase XIX] 注册 emergent_growth_weekly 任务（每周日 03:00 涌现式人格成长）
                try:
                    _emergent_growth_task = SchedulerTaskConfig(
                        task_id="emergent_growth_weekly",
                        trigger_mode=TriggerMode.CRON,
                        trigger_config={"day_of_week": 6, "hour": 3, "minute": 0},  # 周日 03:00
                        intent_template={
                            "skill_name": "emergent_growth_weekly",
                            "args": {
                                "lookback_days": 7,
                            },
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "sleep"],
                        enabled=True,
                        description="Phase XIX 涌现式成长：每周日 03:00 分析近期对话模式，自然浮现人格微调",
                    )
                    if _proactive_registry.get("emergent_growth_weekly") is None:
                        _proactive_registry.register(_emergent_growth_task)
                        logger.info("涌现式成长任务已注册: task_id=emergent_growth_weekly, cron=周日03:00")
                except Exception as _emergent_exc:
                    logger.warning("涌现式成长任务注册失败（降级）: %s", _emergent_exc)

                # [D9] 注册 emotion_decay_tick 任务（每 30s 驱动情绪衰减连续动态系统）
                try:
                    _emotion_decay_task = SchedulerTaskConfig(
                        task_id="emotion_decay_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 30},  # 每 30s tick 一次
                        intent_template={
                            "skill_name": "emotion_decay_tick",
                            "args": {},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],
                        enabled=True,
                        description="D4/D6/D7/D8: 情绪衰减连续动态系统 tick——每 30s 驱动 LimbicAgent 情绪衰减、CircadianAgent 基线调制、InteroceptionAgent 身体预算、GlymphaticAgent 恢复窗口",
                    )
                    if _proactive_registry.get("emotion_decay_tick") is None:
                        _proactive_registry.register(_emotion_decay_task)
                        logger.info("情绪衰减 tick 任务已注册: task_id=emotion_decay_tick, interval=30s")
                except Exception as _decay_exc:
                    logger.warning("情绪衰减 tick 任务注册失败（降级）: %s", _decay_exc)

                # 关键约束：必须在全部 seed tasks 注册完成后再 start，
                # 否则后续仅写入 registry 的任务不会被运行中的 scheduler 真正调度。
                _natsume_scheduler.start()
                _core_personality_task_ids = [
                    "proactive_talk_periodic",
                    "inner_world_numeric_tick",
                    "inner_world_monologue_tick",
                    "silence_tick",
                    "body_sense_tick",
                ]
                new_module_statuses["proactive_talk_scheduler"] = (
                    "OK "
                    f"(started; core_ticks={len(_core_personality_task_ids)}; "
                    f"proactive_push={'ready' if not _proactive_push_detail else 'skip'})"
                )
                new_module_statuses["personality_tick_registry"] = _build_personality_tick_status(
                    registry=_proactive_registry,
                    task_ids=_core_personality_task_ids,
                )

            except Exception as _proactive_exc:
                if not _background_tasks_enabled:
                    new_module_statuses["proactive_talk_scheduler"] = "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                else:
                    new_module_statuses["proactive_talk_scheduler"] = f"DEGRADED: {_proactive_exc}"
                logger.warning("proactive_talk 调度任务注册失败（降级）: %s", _proactive_exc)

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

            # [long_term_distillation_catalog] 注册长期记忆蒸馏工具到 ToolCatalog
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
                new_module_statuses["long_term_distillation_catalog"] = "OK (1 tool registered)"
                logger.info("长期蒸馏工具已注册到 ToolCatalog")
            except Exception as _lt_exc:
                new_module_statuses["long_term_distillation_catalog"] = f"DEGRADED: {_lt_exc}"
                logger.warning("长期蒸馏工具注册失败（降级）: %s", _lt_exc)

            dependency_probes = _edge_health
            if dependency_probes is None:
                dependency_probes = await probe_edge_rpc_health()
            try:
                if not _background_tasks_enabled:
                    new_module_statuses["host_task_scheduler"] = (
                        "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
                    )
                else:
                    from host_brain.app.automation.task_scheduler import get_scheduler  # noqa: PLC0415

                    _host_task_scheduler = get_scheduler()
                    await _host_task_scheduler.start()
                    new_module_statuses["host_task_scheduler"] = (
                        f"OK (running={_host_task_scheduler.health().get('running', False)}, "
                        f"task_count={len(_host_task_scheduler.get_all_tasks_status())})"
                    )
            except Exception as _host_task_scheduler_exc:
                new_module_statuses["host_task_scheduler"] = (
                    f"DEGRADED: {_host_task_scheduler_exc}"
                )
                logger.warning(
                    "HostTaskScheduler startup degraded: %s",
                    _host_task_scheduler_exc,
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
            _activity_monitor = None
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

                # T-29：启动时从磁盘恢复 TribeStateRegistry 状态快照
                try:
                    from host_brain.tribe.tribe_state import get_global_registry as _get_global_registry  # noqa: PLC0415
                    _registry = _get_global_registry()
                    _loaded = _registry.load_snapshot()
                    if _loaded:
                        logger.info("TribeStateRegistry 状态快照已恢复（跨会话持久化）")
                    else:
                        logger.debug("TribeStateRegistry 无持久化快照（首次启动或文件不存在）")
                    # T-32：启动定时保存（默认每 5 分钟，可通过 NATSUME_TRIBE_STATE_SAVE_INTERVAL 覆盖）
                    _registry.start_periodic_save()
                    logger.info("TribeStateRegistry 定时保存已启动")
                except Exception as _snap_exc:
                    logger.debug("TribeStateRegistry.load_snapshot 失败（降级）: %s", _snap_exc)

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
        yield
        # ── Shutdown 阶段 ──
        # 停止 ActivityMonitor
        try:
            if _activity_monitor is not None:
                _activity_monitor.stop()
                logger.info("ActivityMonitor 已停止")
        except Exception as _am_stop_exc:
            logger.warning("ActivityMonitor 停止失败: %s", _am_stop_exc)
        # 优雅关闭 BondUpdateQueue（drain 等待队列排空 + shutdown 停止 worker）
        try:
            from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

            get_cortex_agent().shutdown_bond_update_queue(
                logger=logger,
                timeout_s=3.0,
            )
        except Exception as _bq_exc:
            logger.warning("BondUpdateQueue 关闭失败: %s", _bq_exc)
        try:
            from host_brain.observability.service_health_heartbeat import stop_heartbeat
            await stop_heartbeat()
        except Exception:
            pass
        # [Tribe 分发循环] 停止 TribeEventBus dispatch_loop
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            _shutdown_bus = get_tribe_event_bus()
            if _shutdown_bus is not None:
                await _shutdown_bus.stop()
                logger.info("TribeEventBus dispatch_loop 已停止")
        except Exception as _bus_stop_exc:
            logger.warning("TribeEventBus dispatch_loop 停止失败: %s", _bus_stop_exc)
        # T-32：停止 TribeStateRegistry 定时保存（并强制最后一次落盘）
        try:
            from host_brain.tribe.tribe_state import get_global_registry as _get_global_registry_shutdown  # noqa: PLC0415
            _get_global_registry_shutdown().stop_periodic_save()
            logger.info("TribeStateRegistry 定时保存已停止，最终快照已落盘")
        except Exception as _periodic_stop_exc:
            logger.debug("TribeStateRegistry.stop_periodic_save 失败（降级）: %s", _periodic_stop_exc)
        try:
            from host_brain.app.automation.task_scheduler import get_scheduler  # noqa: PLC0415

            await get_scheduler().stop()
        except Exception as _host_task_scheduler_stop_exc:
            logger.debug("HostTaskScheduler stop degraded: %s", _host_task_scheduler_stop_exc)
        try:
            from host_brain.app.internal_codex_routes import stop_self_heal_auto_dispatch_monitor  # noqa: PLC0415

            stop_self_heal_auto_dispatch_monitor(logger=logger)
        except Exception as _codex_monitor_stop_exc:
            logger.debug("Codex self-heal auto-dispatch monitor stop degraded: %s", _codex_monitor_stop_exc)
        logger.info("Natsume AI Host Brain shutting down ...")

    return _lifespan()

# P3-2：global_exception_handler 和 add_request_id_and_audit 已拆分到子模块：
# - global_exception_handler → http_hooks_outbound.global_exception_handler（已导入）
# - add_request_id_and_audit → http_hooks_inbound.add_request_id_and_audit（已导入）
# 此处保留导入的版本，不再重复定义。
