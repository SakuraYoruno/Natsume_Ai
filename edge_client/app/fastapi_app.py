"""Edge FastAPI RPC gateway."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from edge_client.app.schemas import EdgeRpcEnvelope, EdgeRpcResponse, parse_edge_rpc_envelope
from edge_client.config import EdgeConfig, load_edge_config
from edge_client.rpc.auth import NonceReplayCache, validate_edge_rpc_auth
from edge_client.runtime import EdgeRuntime, build_edge_health_snapshot, build_edge_runtime
from host_brain.core.errors import (
    CONFIG_VALUE_INVALID,
    EDGE_RPC_SCHEMA_INVALID,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    SERVICE_UNAVAILABLE_GENERIC,
    VOICE_SCHEMA_INVALID,
)
from host_brain.core.ids import now_ms


STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"


@dataclass(frozen=True)
class EdgeGatewayResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


EnvelopeHandler = Callable[[EdgeRuntime, EdgeRpcEnvelope], EdgeGatewayResult]


def create_edge_fastapi_app(
    *,
    config: EdgeConfig | None = None,
    runtime: EdgeRuntime | None = None,
    replay_cache: NonceReplayCache | None = None,
    now_fn: Callable[[], int] | None = None,
) -> FastAPI:
    runtime_config = config
    if runtime_config is None:
        loaded = load_edge_config()
        if not loaded.success or loaded.config is None:
            raise RuntimeError(f"edge config load failed: {loaded.as_dict()}")
        runtime_config = loaded.config

    runtime_value = runtime or build_edge_runtime(runtime_config)
    replay_value = replay_cache or NonceReplayCache(
        window_ms=runtime_config.rpc_nonce_window_ms
    )
    clock = now_fn or now_ms

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        yield
        runtime_value.close()

    app = FastAPI(
        title="Natsume Edge RPC Gateway",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )
    app.state.edge_config = runtime_config
    app.state.edge_runtime = runtime_value
    app.state.edge_replay_cache = replay_value
    app.state.now_fn = clock

    @app.get("/v1/edge/health")
    async def edge_health() -> dict[str, Any]:
        health = build_edge_health_snapshot(runtime_value, ts_ms=int(clock()))
        return _response(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            detail="edge health snapshot",
            event=health,
            ts_ms=health["ts_ms"],
        )

    @app.post("/v1/edge/speak")
    async def edge_speak(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(runtime_config.rpc_gateway_enabled and runtime_config.tts_enabled),
            disabled_detail="edge speak disabled by config",
            handler=_handle_speak,
        )

    @app.post("/v1/edge/barge_in")
    async def edge_barge_in(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(
                runtime_config.rpc_gateway_enabled
                and runtime_config.barge_in_enabled
                and runtime_config.tts_enabled
            ),
            disabled_detail="edge barge_in requires EDGE_BARGE_IN_ENABLED and EDGE_TTS_ENABLED",
            handler=_handle_barge_in,
        )

    @app.post("/v1/edge/vts")
    async def edge_vts(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_vts,
        )

    @app.post("/v1/edge/fallback/presence")
    async def edge_fallback_presence(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(
                runtime_config.rpc_gateway_enabled
                and runtime_config.fallback_llm_enabled
            ),
            disabled_detail="edge fallback llm disabled by config",
            handler=_handle_fallback_presence,
        )

    @app.post("/v1/edge/fallback/asr_compile")
    async def edge_fallback_asr_compile(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(
                runtime_config.rpc_gateway_enabled
                and runtime_config.fallback_llm_enabled
            ),
            disabled_detail="edge fallback llm disabled by config",
            handler=_handle_fallback_asr_compile,
        )

    @app.post("/v1/edge/fallback/retrieval_hints")
    async def edge_fallback_retrieval_hints(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(
                runtime_config.rpc_gateway_enabled
                and runtime_config.fallback_llm_enabled
            ),
            disabled_detail="edge fallback llm disabled by config",
            handler=_handle_fallback_retrieval_hints,
        )

    @app.post("/v1/edge/fallback/context_compress")
    async def edge_fallback_context_compress(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(
                runtime_config.rpc_gateway_enabled
                and runtime_config.fallback_llm_enabled
            ),
            disabled_detail="edge fallback llm disabled by config",
            handler=_handle_fallback_context_compress,
        )

    return app


async def _handle_envelope_request(
    *,
    request: Request,
    config: EdgeConfig,
    runtime: EdgeRuntime,
    replay: NonceReplayCache,
    clock: Callable[[], int],
    enabled: bool,
    disabled_detail: str,
    handler: EnvelopeHandler,
) -> JSONResponse:
    ts = int(clock())
    payload_or_error = await _load_json_body(request)
    if isinstance(payload_or_error, JSONResponse):
        return payload_or_error

    parsed = parse_edge_rpc_envelope(payload_or_error)
    if not parsed.success or parsed.envelope is None:
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=parsed.reason_code or EDGE_RPC_SCHEMA_INVALID,
                detail=parsed.detail,
                event={"stage": parsed.stage},
                ts_ms=ts,
            )
        )

    auth = validate_edge_rpc_auth(
        envelope=parsed.envelope,
        shared_key=config.hmac_shared_key,
        allow_insecure_auth=config.allow_insecure_auth,
        auth_window_ms=config.rpc_auth_window_ms,
        replay_cache=replay,
        now_fn=clock,
    )
    if not auth.success:
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=auth.reason_code,
                detail=auth.detail,
                event={"stage": auth.stage, "insecure_auth": auth.insecure_auth},
                ts_ms=ts,
            )
        )

    if not enabled:
        reason = (
            SERVICE_UNAVAILABLE_EDGE_RPC
            if not config.rpc_gateway_enabled
            else SERVICE_UNAVAILABLE_DEPENDENCY
        )
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=reason,
                detail=disabled_detail,
                event={"stage": "S_EDGE_RPC_GATEWAY", "insecure_auth": auth.insecure_auth},
                ts_ms=ts,
            )
        )

    try:
        result = handler(runtime, parsed.envelope)
    except Exception as exc:  # pragma: no cover - defensive gateway guard
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=SERVICE_UNAVAILABLE_GENERIC,
                detail=f"edge handler failed: {exc}",
                event={"stage": "S_EDGE_RPC_GATEWAY", "insecure_auth": auth.insecure_auth},
                ts_ms=ts,
            )
        )

    event = dict(result.event)
    event.setdefault("stage", result.stage)
    event.setdefault("insecure_auth", auth.insecure_auth)
    return JSONResponse(
        _response(
            success=result.success,
            status=result.status,
            reason_code=result.reason_code,
            detail=result.detail,
            event=event,
            ts_ms=ts,
        )
    )


async def _load_json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=EDGE_RPC_SCHEMA_INVALID,
                detail="request body must be valid JSON",
                event={"stage": "S_EDGE_RPC_SCHEMA"},
                ts_ms=int(now_ms()),
            )
        )
    if not isinstance(raw, dict):
        return JSONResponse(
            _response(
                success=False,
                status=STATUS_REJECTED,
                reason_code=EDGE_RPC_SCHEMA_INVALID,
                detail="request body root must be object",
                event={"stage": "S_EDGE_RPC_SCHEMA"},
                ts_ms=int(now_ms()),
            )
        )
    return dict(raw)


def _handle_speak(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    pipeline_result = runtime.voice_pipeline.enqueue_speak(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
        auto_start=True,
    )
    result = pipeline_result.queue_result
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _handle_barge_in(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    pipeline_result = runtime.voice_pipeline.barge_in(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
    )
    result = pipeline_result.barge_result
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _handle_vts(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    action = str(envelope.payload.get("action") or "").strip().lower()
    if runtime.vts_driver is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_VTS",
            detail="vts driver unavailable",
            event={},
        )

    if action == "talk_start":
        result = runtime.vts_driver.talk_start()
    elif action == "talk_end":
        result = runtime.vts_driver.talk_end()
    elif action == "set_expression":
        emotion = str(envelope.payload.get("emotion_tag") or "").strip()
        if not emotion:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_SCHEMA_INVALID,
                stage="S_EDGE_VTS",
                detail="set_expression requires emotion_tag",
                event={},
            )
        result = runtime.vts_driver.set_expression(emotion)
    elif action == "reset":
        result = runtime.vts_driver.reset_to_neutral()
    else:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=VOICE_SCHEMA_INVALID,
            stage="S_EDGE_VTS",
            detail="vts action must be one of talk_start/talk_end/set_expression/reset",
            event={},
        )

    event = result.as_dict()
    return EdgeGatewayResult(
        success=result.success,
        status=(STATUS_ACCEPTED if result.success else STATUS_REJECTED),
        reason_code=result.reason_code,
        stage="S_EDGE_VTS",
        detail=result.detail,
        event=event,
    )


def _handle_fallback_presence(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    result = runtime.fallback_rpc.presence(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
    )
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _handle_fallback_asr_compile(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    result = runtime.fallback_rpc.asr_compile(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
    )
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _handle_fallback_retrieval_hints(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    result = runtime.fallback_rpc.retrieval_hints(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
    )
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _handle_fallback_context_compress(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    result = runtime.fallback_rpc.context_compress(
        request_id=envelope.request_id,
        payload=envelope.payload,
        ts_ms=envelope.ts_ms,
    )
    return EdgeGatewayResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _response(
    *,
    success: bool,
    status: str,
    reason_code: str | None,
    detail: str,
    event: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return EdgeRpcResponse(
        success=bool(success),
        status=str(status),
        reason_code=reason_code,
        detail=str(detail),
        event=dict(event),
        ts_ms=int(ts_ms),
    ).as_dict()


def build_edge_app_from_env() -> FastAPI:
    loaded = load_edge_config()
    if not loaded.success or loaded.config is None:
        reason = loaded.reason_code or CONFIG_VALUE_INVALID
        raise RuntimeError(f"edge config invalid [{reason}]: {loaded.as_dict()}")
    runtime = build_edge_runtime(loaded.config)
    return create_edge_fastapi_app(config=loaded.config, runtime=runtime)
