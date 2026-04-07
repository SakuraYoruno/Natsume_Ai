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
from host_brain.core.ids import new_request_id


def _startup_status_is_degraded(value: Any) -> bool:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return False
    return normalized.startswith("DEGRADED") or normalized.startswith("FAIL")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


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
    registered: list[str] = []
    missing: list[str] = []

    for task_id in task_ids:
        task = registry.get(task_id) if registry is not None else None
        if task is None:
            missing.append(task_id)
            continue
        enabled = bool(getattr(task, "enabled", False))
        registered.append(f"{task_id}[{'enabled' if enabled else 'disabled'}]")

    if missing:
        registered_text = ",".join(registered) if registered else "none"
        return f"DEGRADED: missing={','.join(missing)}; registered={registered_text}"

    return f"OK (registered={len(registered)}/{len(task_ids)}; tasks={','.join(registered)})"


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
            # Phase XIV v4：移除 main_chain 和 routing_matrix（已被顶级 Agent 架构取代）
            from host_brain.app.dependencies import (
                get_recall_pack_builder,
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
            new_module_statuses["world_canon_sync"] = _sync_world_canon_index_on_startup(
                ledger=ledger,
                logger=logger,
            )
            for name, factory in [
                ("recall_pack_builder", get_recall_pack_builder),
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
                        extra={"module": name, "error": str(exc)},
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
                        extra={"module": name},
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
                        extra={"module": name, "error": str(exc)},
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
                from host_brain.app.dependencies import (  # noqa: PLC0415
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
                new_module_statuses["distillation_engine_scheduler"] = f"DEGRADED: {_distill_exc}"
                logger.warning("DistillationEngine 调度任务注册失败（降级）: %s", _distill_exc)

            # [Ref #31] LongTermDistiller 蒸馏任务注册到调度器
            try:
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
                new_module_statuses["long_term_distiller_scheduler"] = f"DEGRADED: {_distill_exc}"
                logger.warning("LongTermDistiller 调度任务注册失败（降级）: %s", _distill_exc)

            # [Ref #34] AuditRetention 保留策略任务注册到调度器（每日 02:00 CRON）
            try:
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
                new_module_statuses["audit_retention_scheduler"] = f"DEGRADED: {_ret_exc}"
                logger.warning("AuditRetention 调度任务注册失败（降级）: %s", _ret_exc)

            # [proactive_talk] 主动对话任务注册 + NatsumeScheduler 启动
            try:
                from host_brain.scheduler.task_registry import (  # noqa: PLC0415
                    SchedulerTaskRegistry, SchedulerTaskConfig, TriggerMode, TaskPriority,
                )
                from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415
                from host_brain.app.vision.proactive_push_client import build_proactive_push_client  # noqa: PLC0415

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

                # 构建 ui_pusher 回调（同步推送到 Edge）
                _push_client = build_proactive_push_client()

                _ui_pusher = lambda message, _client=_push_client: _client.push(message)

                _proactive_intent_dispatcher = (
                    lambda system_intent,
                    _logger=logger,
                    _get_ledger=get_ledger,
                    _retention_policy=_retention_policy,
                    _ui_pusher=_ui_pusher: run_proactive_intent_dispatch(
                        system_intent,
                        logger=_logger,
                        get_ledger=_get_ledger,
                        retention_policy=_retention_policy,
                        ui_pusher=_ui_pusher,
                    )
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
                )

                # ── 注册进化周期任务（Phase XVI）：每日凌晨 3:00 触发 ──────
                try:
                    _evolution_task = SchedulerTaskConfig(
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
                    )
                    if _proactive_registry.get("evolution_cycle_daily") is None:
                        _proactive_registry.register(_evolution_task)
                        logger.info("进化周期任务已注册: task_id=evolution_cycle_daily, cron=03:00")
                except Exception as _evo_task_exc:
                    logger.warning("进化周期任务注册失败（降级）: %s", _evo_task_exc)

                # [Phase XVI] 注册 inner_world_tick 任务（每分钟驱动离线内心世界时钟）
                try:
                    _inner_world_task = SchedulerTaskConfig(
                        task_id="inner_world_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 60},  # 每分钟 tick 一次
                        intent_template={
                            "skill_name": "inner_world_tick",
                            "args": {"user_id": "user_1"},
                            "proposer_node_id": "scheduler",
                        },
                        priority=TaskPriority.LOW,
                        mode_allowlist=["normal", "focus", "gaming", "sleep"],  # 所有非锁定模式均运行
                        enabled=True,
                        description="Phase XVI 离线内心世界时钟：每分钟驱动孤独感/期待值/情绪漂移积累",
                    )
                    if _proactive_registry.get("inner_world_tick") is None:
                        _proactive_registry.register(_inner_world_task)
                        logger.info("离线内心世界时钟任务已注册: task_id=inner_world_tick, interval=60s")
                except Exception as _inner_world_exc:
                    logger.warning("离线内心世界时钟任务注册失败（降级）: %s", _inner_world_exc)

                # [Phase XVIII] 注册 silence_tick 任务（每分钟驱动沉默引擎意愿恢复）
                try:
                    _silence_task = SchedulerTaskConfig(
                        task_id="silence_tick",
                        trigger_mode=TriggerMode.INTERVAL,
                        trigger_config={"seconds": 60},  # 每分钟 tick 一次
                        intent_template={
                            "skill_name": "silence_tick",
                            "args": {"user_id": "user_1"},
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
                            "args": {"user_id": "user_1"},
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
                                "user_id": "user_1",
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
                                "user_id": "user_1",
                                "lookback_days": 7,
                                "use_llm": False,
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

                # 关键约束：必须在全部 seed tasks 注册完成后再 start，
                # 否则后续仅写入 registry 的任务不会被运行中的 scheduler 真正调度。
                _natsume_scheduler.start()
                _core_personality_task_ids = [
                    "proactive_talk_periodic",
                    "inner_world_tick",
                    "silence_tick",
                    "body_sense_tick",
                ]
                new_module_statuses["proactive_talk_scheduler"] = (
                    f"OK (started; core_ticks={len(_core_personality_task_ids)})"
                )
                new_module_statuses["personality_tick_registry"] = _build_personality_tick_status(
                    registry=_proactive_registry,
                    task_ids=_core_personality_task_ids,
                )

            except Exception as _proactive_exc:
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
        except Exception as exc:
            logger.warning("Startup probe degraded", extra={"error": str(exc)})
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
            from host_brain.app.dependencies import get_bond_update_queue  # noqa: PLC0415
            _bond_queue = get_bond_update_queue()
            _drained = _bond_queue.drain(timeout_s=3.0)
            _bond_queue.shutdown()
            logger.info("BondUpdateQueue 已优雅关闭 (drained=%s)", _drained)
        except Exception as _bq_exc:
            logger.warning("BondUpdateQueue 关闭失败: %s", _bq_exc)
        try:
            from host_brain.observability.service_health_heartbeat import stop_heartbeat
            await stop_heartbeat()
        except Exception:
            pass
        logger.info("Natsume AI Host Brain shutting down ...")

    return _lifespan()

async def global_exception_handler(
    *,
    request: Request,
    exc: Exception,
    logger: Any,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"
    logger.error(
        "HTTP request failed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "reason_code": reason_code,
            "detail": detail,
        },
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


async def add_request_id_and_audit(
    *,
    request: Request,
    call_next: Callable,
    metrics: Any,
    logger: Any,
) -> Response:
    request_id = new_request_id()
    request.state.request_id = request_id
    start_time = time.time()
    logger.info(
        "HTTP request started",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host if request.client else "unknown",
        },
    )
    try:
        metrics.inc_request()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "HTTP request completed",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "HTTP request failed with exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "duration_ms": duration_ms,
                "exception": str(exc),
            },
        )
        raise
