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
        )
        new_module_statuses: dict[str, str] = {}
        for name, factory in [
            ("main_chain", get_main_chain),
            ("routing_matrix", get_routing_matrix),
            ("context_pack_builder", get_context_pack_builder),
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
