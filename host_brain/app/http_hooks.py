"""HTTP lifecycle, middleware, and global exception hooks."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.schemas import ErrorResponse
from host_brain.core.ids import new_request_id


@asynccontextmanager
async def runtime_lifespan(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> AsyncGenerator[None, None]:
    logger.info("Natsume AI Host Brain starting up ...")
    ledger = get_ledger()
    try:
        # 预初始化新模块，确保启动时即可感知降级状态
        from host_brain.app.dependencies import (
            get_main_chain,
            get_routing_matrix,
            get_context_pack_builder,
            get_memory_guard,
            get_state_arbiter,
            get_distillation_engine,
            get_semantic_recaller,
        )
        new_module_statuses: dict[str, str] = {}
        for name, factory in [
            ("main_chain", get_main_chain),
            ("routing_matrix", get_routing_matrix),
            ("context_pack_builder", get_context_pack_builder),
            ("memory_guard", get_memory_guard),
            ("state_arbiter", get_state_arbiter),
            ("distillation_engine", get_distillation_engine),
            ("semantic_recaller", get_semantic_recaller),
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

        # [选项二] 接入 StateArbiter：注册状态源（WorldCanon、StyleCanon、EpisodicStore）
        try:
            from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415
            from host_brain.app.memory.state_arbiter import StateSource  # noqa: PLC0415
            _arbiter = get_state_arbiter()

            # 注册 WorldCanonStore（最高优先级 SSOT）
            try:
                from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

                _world_store = WorldCanonStore()

                def _world_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "world_canon_facts":
                        return None
                    try:
                        docs = _world_store.list_documents()
                    except Exception:
                        return None
                    chunks: list[str] = []
                    for doc in docs[:3]:
                        title = str(getattr(doc, "title", "") or "").strip()
                        body = str(getattr(doc, "body", "") or "").strip()
                        line = f"{title}: {body}" if title and body else (body or title)
                        if line:
                            chunks.append(line)
                    merged = "\n".join(chunks).strip()
                    return merged or None

                _arbiter.register_source(StateSource.WORLD_CANON, _world_get_fact)
                logger.info("StateArbiter: WorldCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: WorldCanonStore 注册失败（降级）: %s", _exc)

            # 注册 StyleCanonStore
            try:
                from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

                _style_store = StyleCanonStore()

                def _style_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "style_canon_facts":
                        return None
                    try:
                        entries = _style_store.list_entries()
                    except Exception:
                        return None
                    chunks: list[str] = []
                    for entry in entries[:3]:
                        content = str(getattr(entry, "content", "") or "").strip()
                        if content:
                            chunks.append(content)
                    merged = "\n".join(chunks).strip()
                    return merged or None

                _arbiter.register_source(StateSource.STYLE_CANON, _style_get_fact)
                logger.info("StateArbiter: StyleCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: StyleCanonStore 注册失败（降级）: %s", _exc)

            # 注册 EpisodicStore（取最新记录）
            try:
                from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

                _ep_store = get_episodic_store()

                def _episodic_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "episodic_latest":
                        return None
                    records_map = getattr(_ep_store, "_records", None)
                    if not isinstance(records_map, dict) or not records_map:
                        return None
                    try:
                        latest = max(records_map.values(), key=lambda row: int(getattr(row, "ts_ms", 0)))
                    except Exception:
                        return None
                    text = str(getattr(latest, "text", "") or "").strip()
                    return text or None

                _arbiter.register_source(StateSource.EPISODIC_STORE, _episodic_get_fact)
                logger.info("StateArbiter: EpisodicStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: EpisodicStore 注册失败（降级）: %s", _exc)

            new_module_statuses["state_arbiter_sources"] = (
                f"OK:{len(_arbiter._sources)} sources"
            )
        except Exception as _sa_exc:
            logger.warning("StateArbiter 状态源注册失败（降级）: %s", _sa_exc)
            new_module_statuses["state_arbiter_sources"] = f"DEGRADED: {_sa_exc}"

        # [P1] Edge RPC 健康探测：启动时检查 Edge 是否可达
        try:
            from host_brain.app.dependencies import probe_edge_rpc_health  # noqa: PLC0415
            _edge_health = await probe_edge_rpc_health()
            _edge_available = bool(_edge_health.get("available", False))
            new_module_statuses["edge_rpc"] = (
                f"OK (latency={_edge_health.get('latency_ms', 0)}ms)"
                if _edge_available
                else f"DEGRADED: {_edge_health.get('detail', 'unreachable')}"
            )
            if _edge_available:
                logger.info(
                    "Edge RPC 可达: %s (latency=%dms)",
                    _edge_health.get("base_url", ""),
                    _edge_health.get("latency_ms", 0),
                )
            else:
                logger.warning(
                    "⚠️ Edge RPC 不可达（降级模式）: %s",
                    _edge_health.get("detail", ""),
                    extra={"edge_health": _edge_health},
                )
        except Exception as _edge_exc:
            new_module_statuses["edge_rpc"] = f"DEGRADED: {_edge_exc}"
            logger.warning("Edge RPC 健康探测失败（降级）: %s", _edge_exc)

        # [TODO-100] DistillationEngine 蒸馏定时任务注册到 HostTaskScheduler（P1，每 30 分钟）
        try:
            from host_brain.app.dependencies import get_distillation_engine  # noqa: PLC0415
            from host_brain.app.automation.task_scheduler import (  # noqa: PLC0415
                ScheduledTask, TaskPriority, get_scheduler,
            )

            _distill_engine = get_distillation_engine()
            _scheduler = get_scheduler()

            async def _run_distillation_task() -> None:
                """DistillationEngine 蒸馏定时任务执行体。"""
                report = await _distill_engine.run_batch()
                logger.info(
                    "蒸馏任务完成: 候选=%d, 成功=%d, 跳过=%d, 失败=%d, 耗时=%dms",
                    report.total_candidates, report.distilled_count,
                    report.skipped_count, report.failed_count, report.duration_ms,
                )

            _distill_task = ScheduledTask(
                task_id="distillation_engine_periodic",
                name="情节记忆蒸馏（每 30 分钟）",
                fn=_run_distillation_task,
                interval_s=1800.0,  # 30 分钟
                max_retries=2,
                retry_delay_s=30.0,
                enabled=True,
                priority=TaskPriority.P1,
            )
            _scheduler.register(_distill_task)
            new_module_statuses["distillation_engine_scheduler"] = (
                f"OK (task_id={_distill_task.task_id}, interval=1800s)"
            )
            logger.info(
                "DistillationEngine 蒸馏任务已注册到调度器: task_id=%s",
                _distill_task.task_id,
            )
        except Exception as _distill_exc:
            new_module_statuses["distillation_engine_scheduler"] = f"DEGRADED: {_distill_exc}"
            logger.warning("DistillationEngine 调度任务注册失败（降级）: %s", _distill_exc)

        # [TODO-31] LongTermDistiller 蒸馏任务注册到调度器
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

        # [TODO-34] AuditRetention 保留策略任务注册到调度器（每日 02:00 CRON）
        try:
            from host_brain.audit.retention import RetentionPolicy  # noqa: PLC0415
            from host_brain.scheduler.task_registry import SchedulerTaskRegistry, SchedulerTaskConfig  # noqa: PLC0415

            _retention_policy = RetentionPolicy()
            _retention_registry = SchedulerTaskRegistry.get_instance()

            async def _run_audit_retention_task() -> dict:
                """AuditRetention 调度任务执行体。"""
                import time as _time  # noqa: PLC0415
                from host_brain.audit.retention import run_retention_policy  # noqa: PLC0415
                from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415
                _req_id = _new_id()
                _result = run_retention_policy(
                    ledger=get_ledger(),
                    policy=_retention_policy,
                    request_id=_req_id,
                    now_ts_ms=int(_time.time() * 1000),
                )
                return _result.as_dict()

            _retention_task = SchedulerTaskConfig(
                task_id="audit_retention_daily",
                display_name="审计数据保留策略（每日清理）",
                trigger_mode="cron",
                trigger_config={"cron": "0 2 * * *"},
                task_fn=_run_audit_retention_task,
                enabled=True,
            )
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

            # 注册 proactive_talk 任务（每 15 分钟检查一次）
            _proactive_task = SchedulerTaskConfig(
                task_id="proactive_talk_periodic",
                trigger_mode=TriggerMode.INTERVAL,
                trigger_config={"seconds": 900},  # 15 分钟
                intent_template={
                    "skill_name": "proactive_talk",
                    "args": {"idle_threshold_ms": 14400000},  # 4 小时空闲阈值
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.LOW,
                mode_allowlist=["normal", "sleep"],
                enabled=True,
                description="主动对话：每 15 分钟检查一次，空闲 4 小时后触发问候",
            )
            # 避免重复注册（重启时可能已存在）
            if _proactive_registry.get("proactive_talk_periodic") is None:
                _proactive_registry.register(_proactive_task)

            # 构建 ui_pusher 回调（同步推送到 Edge）
            _push_client = build_proactive_push_client()

            def _ui_pusher(message: dict) -> dict:
                return _push_client.push(message)

            # 构建 intent_dispatcher：执行 proactive_talk skill 并注入 ui_pusher
            from host_brain.skills.impl.proactive_talk import execute_proactive_talk  # noqa: PLC0415

            def _proactive_intent_dispatcher(system_intent: dict) -> dict:
                """将 scheduler 生成的 system_intent 路由到 proactive_talk skill。"""
                skill_name = system_intent.get("skill_name", "")
                if skill_name != "proactive_talk":
                    return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}
                args = dict(system_intent.get("args") or {})
                context = {
                    "sys_mode": system_intent.get("mood_state", "normal"),
                    "request_id": system_intent.get("request_id"),
                    "intent_id": system_intent.get("intent_id"),
                    "ui_pusher": _ui_pusher,
                }
                return execute_proactive_talk(args=args, context=context)

            # 启动 NatsumeScheduler
            from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
            _health_reporter = SchedulerHealthReporter()
            _natsume_scheduler = NatsumeScheduler(
                registry=_proactive_registry,
                health_reporter=_health_reporter,
                intent_dispatcher=_proactive_intent_dispatcher,
            )
            _natsume_scheduler.start()

            new_module_statuses["proactive_talk_scheduler"] = (
                f"OK (task_id={_proactive_task.task_id}, interval=900s)"
            )
            logger.info(
                "proactive_talk 任务已注册并启动调度器: task_id=%s",
                _proactive_task.task_id,
            )
        except Exception as _proactive_exc:
            new_module_statuses["proactive_talk_scheduler"] = f"DEGRADED: {_proactive_exc}"
            logger.warning("proactive_talk 调度任务注册失败（降级）: %s", _proactive_exc)

        report = build_startup_report(
            runtime_statuses=new_module_statuses,
            dependency_probes={},
            request_id=new_request_id(),
            ledger=ledger,
            ts_ms=int(time.time() * 1000),
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})

        # 新模块中有降级时，明显提示
        degraded_modules = [k for k, v in new_module_statuses.items() if v != "OK"]
        if degraded_modules:
            logger.warning(
                "⚠️ 以下新模块启动时处于降级状态，请检查配置: %s",
                degraded_modules,
                extra={"degraded_modules": degraded_modules},
            )
    except Exception as exc:
        logger.warning("Startup probe degraded", extra={"error": str(exc)})
    yield
    logger.info("Natsume AI Host Brain shutting down ...")


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
