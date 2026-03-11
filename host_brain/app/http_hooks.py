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
                from host_brain.memory.canon_world_store import CanonWorldStore  # noqa: PLC0415
                _world_store = CanonWorldStore()
                _arbiter.register_source(StateSource.WORLD_CANON, _world_store.get_fact)
                logger.info("StateArbiter: WorldCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: WorldCanonStore 注册失败（降级）: %s", _exc)

            # 注册 StyleCanonStore
            try:
                from host_brain.memory.canon_style_store import CanonStyleStore  # noqa: PLC0415
                _style_store = CanonStyleStore()
                _arbiter.register_source(StateSource.STYLE_CANON, _style_store.get_fact)
                logger.info("StateArbiter: StyleCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: StyleCanonStore 注册失败（降级）: %s", _exc)

            # 注册 EpisodicStore（取最新记录）
            try:
                from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
                _ep_store = get_episodic_store()
                _arbiter.register_source(StateSource.EPISODIC_STORE, _ep_store.get_fact)
                logger.info("StateArbiter: EpisodicStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: EpisodicStore 注册失败（降级）: %s", _exc)

            new_module_statuses["state_arbiter_sources"] = (
                f"OK:{len(_arbiter._sources)} sources"
            )
        except Exception as _sa_exc:
            logger.warning("StateArbiter 状态源注册失败（降级）: %s", _sa_exc)
            new_module_statuses["state_arbiter_sources"] = f"DEGRADED: {_sa_exc}"

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
