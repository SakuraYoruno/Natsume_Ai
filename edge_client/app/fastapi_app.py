"""Edge FastAPI RPC gateway."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
import json
from typing import Any, Callable
import urllib.error
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from edge_client.app.schemas import EdgeRpcEnvelope, EdgeRpcResponse, parse_edge_rpc_envelope
from edge_client.channels.wechat_command_queue import (
    ALLOWED_WECHAT_COMMAND_ACTIONS,
    enqueue_wechat_command,
    resolve_command_file_paths,
    wait_wechat_command_result,
)
from edge_client.config import EdgeConfig, load_edge_config
from edge_client.rpc.auth import NonceReplayCache, validate_edge_rpc_auth
from edge_client.runtime import (
    EdgeRuntime,
    bootstrap,
    build_edge_health_snapshot,
    collect_health,
    shutdown as runtime_shutdown,
    startup as runtime_startup,
)
from edge_client.actuators.vts_driver import VtsState
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
LifecycleHook = Callable[[EdgeRuntime], Any]
HealthCollector = Callable[[EdgeRuntime], dict[str, Any]]


def create_edge_fastapi_app(
    *,
    config: EdgeConfig | None = None,
    runtime: EdgeRuntime | None = None,
    replay_cache: NonceReplayCache | None = None,
    now_fn: Callable[[], int] | None = None,
    startup_hook: LifecycleHook | None = None,
    shutdown_hook: LifecycleHook | None = None,
    health_collector: HealthCollector | None = None,
) -> FastAPI:
    runtime_config = config
    if runtime_config is None:
        loaded = load_edge_config()
        if not loaded.success or loaded.config is None:
            raise RuntimeError(f"edge config load failed: {loaded.as_dict()}")
        runtime_config = loaded.config

    runtime_value = runtime or bootstrap(runtime_config)
    replay_value = replay_cache or NonceReplayCache(
        window_ms=runtime_config.rpc_nonce_window_ms
    )
    clock = now_fn or now_ms
    startup_runner = startup_hook or runtime_startup
    shutdown_runner = shutdown_hook or runtime_shutdown
    health_runner = health_collector or collect_health

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        await _run_lifecycle_hook(startup_runner, runtime_value)
        try:
            yield
        finally:
            await _run_lifecycle_hook(shutdown_runner, runtime_value)

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
        snapshot_ts = int(clock())
        collected = _run_health_collector(health_runner, runtime_value, ts_ms=snapshot_ts)
        health = build_edge_health_snapshot(runtime_value, ts_ms=snapshot_ts)
        for key in ("status", "timestamp", "components", "resource_gate"):
            if key in collected:
                health[key] = collected[key]
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

    @app.post("/v1/edge/wechat/callback")
    async def edge_wechat_callback(request: Request) -> JSONResponse:
        """接收微信 Hook 工具（wcf 等）推送的消息，转发给 Host Brain。

        此接口不需要 HMAC 鉴权（本地 Hook 工具调用），但只监听 127.0.0.1。
        """
        ts = int(clock())
        _ = request
        return JSONResponse(_response(
            success=False,
            status=STATUS_REJECTED,
            reason_code=CONFIG_VALUE_INVALID,
            detail="legacy /v1/edge/wechat/callback disabled; use /v1/gateway/{platform}/webhook",
            event={"stage": "S_EDGE_WECHAT_CALLBACK", "deprecated": True},
            ts_ms=ts,
        ))

    @app.post("/v1/edge/wechat/command")
    async def edge_wechat_command(request: Request) -> JSONResponse:
        ts = int(clock())
        _ = request
        return JSONResponse(_response(
            success=False,
            status=STATUS_REJECTED,
            reason_code=CONFIG_VALUE_INVALID,
            detail="legacy /v1/edge/wechat/command disabled; use /v1/gateway/reply on EDGE_GATEWAY_PORT",
            event={"stage": "S_EDGE_WECHAT_COMMAND", "deprecated": True},
            ts_ms=ts,
        ))

    @app.post("/v1/edge/screenshot")
    async def edge_screenshot(request: Request) -> JSONResponse:
        """截取 Edge 端屏幕，返回 base64 编码的 PNG 图像。

        Host Brain 通过此接口获取 Edge 端屏幕截图，用于 Agent Loop 视觉分析。
        需要 HMAC 鉴权（Host → Edge 方向）。
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_screenshot,
        )

    @app.post("/v1/edge/automate")
    async def edge_automate(request: Request) -> JSONResponse:
        """在 Edge 端执行桌面自动化操作（点击/输入/滚动等）。

        Host Brain 通过此接口远程控制 Edge 端的鼠标和键盘。
        需要 HMAC 鉴权（Host → Edge 方向）。
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_automate,
        )

    @app.post("/v1/edge/visual_locate")
    async def edge_visual_locate(request: Request) -> JSONResponse:
        """截图 + VLM 视觉定位：在当前屏幕中找到目标控件并返回坐标。

        Host Brain 通过此接口让 Edge 端自主完成"截图→VLM推理→返回坐标"，
        避免将截图传输到 Host 端处理，降低带宽消耗。
        需要 HMAC 鉴权（Host → Edge 方向）。

        请求 payload：
          - instruction: str  — 自然语言描述目标控件，例如 "微信发送按钮"
          - monitor_index: int（可选，默认 1）— 截图显示器索引
          - auto_click: bool（可选，默认 false）— 定位成功后是否自动点击

        响应 event 字段：
          - found: bool
          - x, y: int  — 目标中心坐标（像素）
          - confidence: float
          - description: str
          - screenshot_latency_ms: int
          - locate_latency_ms: int
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled and runtime_config.vlm_enabled,
            disabled_detail="edge visual locate disabled (check EDGE_VLM_ENABLED and EDGE_RPC_GATEWAY_ENABLED)",
            handler=_handle_visual_locate,
        )

    @app.post("/v1/edge/proactive_push")
    async def edge_proactive_push(request: Request) -> JSONResponse:
        """接收 Host Brain 推送的主动对话消息，存入 EdgeRuntime 消息队列。

        Host Brain 的 proactive_talk skill 通过此接口将主动问候推送到 Edge，
        Edge 侧前端通过轮询 /v1/edge/proactive_messages 拉取待展示消息。
        需要 HMAC 鉴权（Host → Edge 方向）。
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_proactive_push,
        )

    @app.get("/v1/edge/proactive_messages")
    async def edge_proactive_messages() -> JSONResponse:
        """前端轮询接口：拉取并清空待展示的主动对话消息队列。

        返回 Edge 侧积压的所有主动消息，调用后队列清空。
        前端可每 5 秒轮询一次，有消息时弹出对话气泡。
        """
        ts = int(clock())
        messages = _drain_proactive_queue(runtime_value)
        return JSONResponse(_response(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            detail=f"drained {len(messages)} proactive messages",
            event={"messages": messages, "count": len(messages)},
            ts_ms=ts,
        ))

    # ------------------------------------------------------------------
    # /v1/edge/wx/* — 微信数据查询端点（基于 WxDbService 解密方案）
    # 这些端点供 Host Brain MCP 工具调用，走 HMAC 鉴权
    # ------------------------------------------------------------------

    @app.post("/v1/edge/wx/contacts")
    async def edge_wx_contacts(request: Request) -> JSONResponse:
        """查询微信联系人列表。

        payload: {"include_groups": true}
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_wx_contacts,
        )

    @app.post("/v1/edge/wx/messages")
    async def edge_wx_messages(request: Request) -> JSONResponse:
        """查询微信聊天记录。

        payload: {"conversation_id": "wxid_xxx", "limit": 50, "after_ts_ms": 0}
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_wx_messages,
        )

    @app.post("/v1/edge/wx/search")
    async def edge_wx_search(request: Request) -> JSONResponse:
        """全文搜索微信聊天记录。

        payload: {"keyword": "关键词", "limit": 20}
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_wx_search,
        )

    @app.post("/v1/edge/wx/refresh")
    async def edge_wx_refresh(request: Request) -> JSONResponse:
        """重新执行微信数据库解密（微信重新登录后密钥变化时调用）。

        payload: {}
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_wx_refresh,
        )

    @app.get("/v1/edge/wx/stats")
    async def edge_wx_stats() -> JSONResponse:
        """返回微信数据库统计信息（无需鉴权，仅供调试）。"""
        ts = int(clock())
        try:
            wx_svc = _get_wx_db_service(runtime_value)
            if wx_svc is None:
                return JSONResponse(_response(
                    success=False,
                    status=STATUS_REJECTED,
                    reason_code="WX_SERVICE_UNAVAILABLE",
                    detail="WxDbService not initialized (check WECHAT_DECRYPT_OUTPUT_DIR config)",
                    event={},
                    ts_ms=ts,
                ))
            return JSONResponse(_response(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                detail="wx db stats",
                event=wx_svc.stats(),
                ts_ms=ts,
            ))
        except Exception as exc:
            return JSONResponse(_response(
                success=False,
                status=STATUS_REJECTED,
                reason_code="WX_STATS_ERROR",
                detail=f"wx stats failed: {exc}",
                event={},
                ts_ms=ts,
            ))

    @app.post("/v1/edge/wx/moments")
    async def edge_wx_moments(request: Request) -> JSONResponse:
        """读取朋友圈（Sns.db）。

        payload: {"limit": 20}
        """
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_wx_moments,
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
    queue_result = pipeline_result.queue_result
    start_result = pipeline_result.start_result
    expression_result = pipeline_result.expression_result
    talk_result = pipeline_result.talk_result
    mouth_sync_debug = pipeline_result.mouth_sync_debug

    # Prefer the real playback-start result when available so Host can detect
    # synth/playback failures instead of only seeing "queued".
    result = start_result if start_result is not None else queue_result
    event = dict(result.event)
    event.setdefault("queue_event", dict(queue_result.event))
    if start_result is not None:
        event["start_event"] = dict(start_result.event)
        event["start_status"] = start_result.status
        event["start_reason_code"] = start_result.reason_code
        event["start_stage"] = start_result.stage
        event["start_detail"] = start_result.detail
    if expression_result is not None:
        event["vts_expression_result"] = expression_result.as_dict()
    if talk_result is not None:
        event["vts_talk_result"] = talk_result.as_dict()
    if mouth_sync_debug is not None:
        event["vts_mouth_sync"] = dict(mouth_sync_debug)

    success = bool(queue_result.success and (start_result is None or start_result.success))
    detail = str(result.detail)
    if start_result is not None and not start_result.success:
        detail = f"{queue_result.detail}; playback start failed: {start_result.detail}"
    elif start_result is not None and start_result.success:
        detail = f"{queue_result.detail}; {start_result.detail}"

    return EdgeGatewayResult(
        success=success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=detail,
        event=event,
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
        if runtime.vts_driver.state != VtsState.AUTHENTICATED:
            conn_result = runtime.vts_driver.connect()
            if not conn_result.success:
                event = conn_result.as_dict()
                return EdgeGatewayResult(
                    success=False,
                    status=STATUS_REJECTED,
                    reason_code=conn_result.reason_code,
                    stage="S_EDGE_VTS",
                    detail=conn_result.detail,
                    event=event,
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


def _handle_gateway_reply(_runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    payload = dict(envelope.payload or {})
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    content = _normalize_gateway_reply_content(payload.get("content"))
    msg_type = str(payload.get("msg_type") or "text").strip().lower()
    timeout_ms = _parse_int_clamped(
        payload.get("timeout_ms"),
        default=12000,
        min_value=300,
        max_value=120000,
    )

    if not platform:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_GATEWAY_REPLY",
            detail="gateway reply requires non-empty platform",
            event={},
        )
    if not conversation_id:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_GATEWAY_REPLY",
            detail="gateway reply requires non-empty conversation_id",
            event={},
        )
    if msg_type == "text" and not content.strip():
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_GATEWAY_REPLY",
            detail="text gateway reply requires non-empty content",
            event={},
        )

    gateway_port = int(getattr(getattr(_runtime, "config", None), "gateway_port", 19000) or 19000)
    gateway_url = f"http://127.0.0.1:{gateway_port}/v1/gateway/reply"
    request_payload = dict(payload)
    request_payload["platform"] = platform
    request_payload["conversation_id"] = conversation_id
    request_payload["msg_type"] = msg_type or "text"
    if content:
        request_payload["content"] = content

    body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        gateway_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    response_body = ""
    http_status = 0
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, timeout_ms / 1000.0)) as resp:
            http_status = int(resp.getcode() or 0)
            response_body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        detail = f"gateway reply http error: {exc.code}"
        if err_body:
            detail = f"{detail}; {err_body[:300]}"
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_GATEWAY_REPLY",
            detail=detail,
            event={
                "gateway_url": gateway_url,
                "http_status": int(getattr(exc, "code", 0) or 0),
                "platform": platform,
                "conversation_id": conversation_id,
            },
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_GATEWAY_REPLY",
            detail=f"gateway reply transport failed: {exc}",
            event={
                "gateway_url": gateway_url,
                "platform": platform,
                "conversation_id": conversation_id,
            },
        )

    parsed_response: dict[str, Any] = {}
    if response_body.strip():
        try:
            loaded = json.loads(response_body)
            if isinstance(loaded, dict):
                parsed_response = loaded
        except Exception:
            parsed_response = {}

    gateway_status = str(parsed_response.get("status") or "").strip().lower()
    success = (http_status >= 200 and http_status < 300) and gateway_status in {
        "sent",
        "accepted",
        "ok",
        "queued",
    }
    detail = "gateway reply dispatched"
    if not success:
        detail = (
            str(parsed_response.get("detail") or "").strip()
            or f"gateway reply failed: status={gateway_status or 'unknown'}"
        )
    return EdgeGatewayResult(
        success=bool(success),
        status=STATUS_ACCEPTED if success else STATUS_REJECTED,
        reason_code=None if success else str(parsed_response.get("reason_code") or "GATEWAY_REPLY_FAILED"),
        stage="S_EDGE_GATEWAY_REPLY",
        detail=detail,
        event={
            "gateway_url": gateway_url,
            "http_status": http_status,
            "gateway_status": gateway_status,
            "platform": platform,
            "conversation_id": conversation_id,
            "msg_type": msg_type,
            "forwarded": True,
        },
    )


def _normalize_gateway_reply_content(raw: Any) -> str:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        if text[0] not in {"{", "["}:
            return raw
        try:
            parsed = json.loads(text)
        except Exception:
            return raw
        extracted = _extract_gateway_reply_text(parsed)
        return extracted or raw
    extracted = _extract_gateway_reply_text(raw)
    if extracted:
        return extracted
    return str(raw or "")


def _extract_gateway_reply_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("narrative", "reply", "text", "content", "message", "value"):
            candidate = raw.get(key)
            extracted = _extract_gateway_reply_text(candidate)
            if extracted:
                return extracted
        return ""
    if isinstance(raw, list):
        for item in raw:
            extracted = _extract_gateway_reply_text(item)
            if extracted:
                return extracted
    return ""


def _handle_wechat_command(_runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    payload = dict(envelope.payload or {})
    action = str(payload.get("action") or "").strip().lower()
    target = str(payload.get("target") or "").strip()
    target_candidates_raw = payload.get("target_candidates")
    target_candidates: list[str] = []
    if isinstance(target_candidates_raw, list):
        seen: set[str] = set()
        for item in target_candidates_raw:
            value = str(item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            target_candidates.append(value)
    if (not target) and target_candidates:
        target = str(target_candidates[0] or "").strip()
    content = str(payload.get("content") or payload.get("text") or "")
    wait_result = _parse_bool_like(payload.get("wait_result"), default=False)
    timeout_ms = _parse_int_clamped(
        payload.get("timeout_ms"),
        default=8000,
        min_value=300,
        max_value=120000,
    )

    if action not in ALLOWED_WECHAT_COMMAND_ACTIONS:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_WECHAT_COMMAND",
            detail=f"wechat action must be one of {sorted(ALLOWED_WECHAT_COMMAND_ACTIONS)}",
            event={"allowed_actions": sorted(ALLOWED_WECHAT_COMMAND_ACTIONS)},
        )
    if not target and action != "cancel_call":
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_WECHAT_COMMAND",
            detail="wechat command requires non-empty target",
            event={},
        )
    if action == "send_text" and not str(content).strip():
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EDGE_RPC_SCHEMA_INVALID,
            stage="S_EDGE_WECHAT_COMMAND",
            detail="send_text requires non-empty content",
            event={},
        )

    queue_file, result_file = resolve_command_file_paths()
    try:
        command = enqueue_wechat_command(
            action=action,
            target=target,
            target_candidates=target_candidates,
            content=content,
            request_id=envelope.request_id,
            queue_file=queue_file,
            ts_ms=envelope.ts_ms,
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_WECHAT_COMMAND",
            detail=f"wechat command enqueue failed: {exc}",
            event={"queue_file": str(queue_file)},
        )

    event: dict[str, Any] = {
        "command": dict(command),
        "queue_file": str(queue_file),
        "result_file": str(result_file),
    }
    if not wait_result:
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WECHAT_COMMAND",
            detail="wechat command queued",
            event=event,
        )

    command_id = str(command.get("command_id") or "")
    result = wait_wechat_command_result(
        command_id=command_id,
        timeout_ms=timeout_ms,
        result_file=result_file,
    )
    if result is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WECHAT_COMMAND_TIMEOUT",
            stage="S_EDGE_WECHAT_COMMAND",
            detail=f"wechat command timed out after {timeout_ms}ms",
            event=event,
        )

    event["result"] = dict(result)
    success = _parse_bool_like(result.get("success"), default=False)
    if not success:
        status_text = str(result.get("status") or "").strip().lower()
        success = status_text in {"ok", "success", "done"}

    return EdgeGatewayResult(
        success=bool(success),
        status=(STATUS_ACCEPTED if success else STATUS_REJECTED),
        reason_code=(None if success else str(result.get("reason_code") or "WECHAT_COMMAND_FAILED")),
        stage="S_EDGE_WECHAT_COMMAND",
        detail=str(result.get("detail") or ("wechat command succeeded" if success else "wechat command failed")),
        event=event,
    )


def _handle_screenshot(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """处理截图请求：调用 DesktopController 截图并返回 base64 图像。"""
    try:
        from edge_client.actuators.desktop_controller import DesktopController
        monitor_index = int(envelope.payload.get("monitor_index") or 1)
        controller = DesktopController(monitor_index=monitor_index)
        result = controller.capture_screenshot()
        if result.success:
            return EdgeGatewayResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage="S_EDGE_SCREENSHOT",
                detail=result.detail,
                event={
                    "image_b64": result.image_b64,
                    "image_mime": result.image_mime,
                    "width": result.width,
                    "height": result.height,
                    "monitor_index": result.monitor_index,
                    "latency_ms": result.latency_ms,
                },
            )
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=result.reason_code,
            stage="S_EDGE_SCREENSHOT",
            detail=result.detail,
            event={"latency_ms": result.latency_ms},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_GENERIC,
            stage="S_EDGE_SCREENSHOT",
            detail=f"screenshot failed: {exc}",
            event={},
        )


def _handle_automate(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """处理自动化操作请求：调用 DesktopController 执行鼠标/键盘操作。"""
    try:
        from edge_client.actuators.desktop_controller import (
            DesktopController,
            DesktopAction,
            DesktopActionType,
        )
        raw_actions = envelope.payload.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=EDGE_RPC_SCHEMA_INVALID,
                stage="S_EDGE_AUTOMATE",
                detail="automate requires non-empty actions list",
                event={},
            )

        # 解析操作列表
        actions: list[DesktopAction] = []
        for raw in raw_actions:
            if not isinstance(raw, dict):
                continue
            try:
                action_type = DesktopActionType(str(raw.get("type") or "").lower())
            except ValueError:
                continue
            actions.append(DesktopAction(
                type=action_type,
                x=raw.get("x"),
                y=raw.get("y"),
                x2=raw.get("x2"),
                y2=raw.get("y2"),
                text=raw.get("text"),
                amount=raw.get("amount"),
                duration_ms=int(raw.get("duration_ms") or 0),
                wait_after_ms=int(raw.get("wait_after_ms") or 100),
            ))

        if not actions:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=EDGE_RPC_SCHEMA_INVALID,
                stage="S_EDGE_AUTOMATE",
                detail="no valid actions parsed from request",
                event={},
            )

        stop_on_error = bool(envelope.payload.get("stop_on_error", True))
        controller = DesktopController()
        report = controller.execute_actions(actions, stop_on_error=stop_on_error)

        report_event = report.as_dict()
        try:
            screen_w, screen_h = DesktopController.get_screen_size()
            report_event["screen_size"] = {"width": int(screen_w), "height": int(screen_h)}
        except Exception:
            pass

        return EdgeGatewayResult(
            success=report.all_success,
            status=STATUS_ACCEPTED if report.all_success else STATUS_REJECTED,
            reason_code=None if report.all_success else "DESKTOP_ACTION_PARTIAL_FAILURE",
            stage="S_EDGE_AUTOMATE",
            detail=f"executed {report.success_count}/{report.total_actions} actions",
            event=report_event,
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_GENERIC,
            stage="S_EDGE_AUTOMATE",
            detail=f"automate failed: {exc}",
            event={},
        )


def _handle_visual_locate(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """视觉定位请求：Edge 端 VLM 已移除，返回不可用。"""
    return EdgeGatewayResult(
        success=False,
        status=STATUS_REJECTED,
        reason_code="VLM_REMOVED",
        stage="S_EDGE_VISUAL_LOCATE",
        detail="Edge 端 VLM 模型已移除，视觉定位功能不可用",
        event={},
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


async def _run_lifecycle_hook(hook: LifecycleHook, runtime: EdgeRuntime) -> None:
    outcome = hook(runtime)
    if inspect.isawaitable(outcome):
        await outcome


def _run_health_collector(
    collector: HealthCollector,
    runtime: EdgeRuntime,
    *,
    ts_ms: int,
) -> dict[str, Any]:
    try:
        payload = collector(runtime, ts_ms=ts_ms)  # type: ignore[misc]
    except TypeError:
        payload = collector(runtime)
    return dict(payload) if isinstance(payload, dict) else {"status": "degraded"}


def _handle_proactive_push(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """处理主动对话推送：将消息存入 EdgeRuntime 的内存队列。"""
    try:
        message = dict(envelope.payload)
        if not message.get("type") or not message.get("content"):
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code="PROACTIVE_PUSH_SCHEMA_INVALID",
                stage="S_EDGE_PROACTIVE_PUSH",
                detail="proactive push requires type and content fields",
                event={},
            )
        # 存入 EdgeRuntime 的主动消息队列（懒初始化）
        queue: list = getattr(runtime, "_proactive_message_queue", None)
        if queue is None:
            runtime._proactive_message_queue = []  # type: ignore[attr-defined]
            queue = runtime._proactive_message_queue
        queue.append(message)
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_PROACTIVE_PUSH",
            detail=f"proactive message queued (queue_size={len(queue)})",
            event={"event_id": message.get("event_id", ""), "queue_size": len(queue)},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_GENERIC,
            stage="S_EDGE_PROACTIVE_PUSH",
            detail=f"proactive push failed: {exc}",
            event={},
        )


def _drain_proactive_queue(runtime: EdgeRuntime) -> list[dict]:
    """拉取并清空 EdgeRuntime 的主动消息队列。"""
    queue: list = getattr(runtime, "_proactive_message_queue", None)
    if not queue:
        return []
    messages = list(queue)
    queue.clear()
    return messages


def _parse_bool_like(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_clamped(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    if parsed < min_value:
        return int(min_value)
    if parsed > max_value:
        return int(max_value)
    return int(parsed)


def build_edge_app_from_env() -> FastAPI:
    loaded = load_edge_config()
    if not loaded.success or loaded.config is None:
        reason = loaded.reason_code or CONFIG_VALUE_INVALID
        raise RuntimeError(f"edge config invalid [{reason}]: {loaded.as_dict()}")
    runtime = bootstrap(loaded.config)
    return create_edge_fastapi_app(config=loaded.config, runtime=runtime)


# ---------------------------------------------------------------------------
# 微信数据查询端点 Handler（/v1/edge/wx/*）
# ---------------------------------------------------------------------------

def _get_wx_db_service(runtime: EdgeRuntime) -> Any | None:
    """从 EdgeRuntime 获取 WxDbService 实例（懒初始化）。"""
    return getattr(runtime, "_wx_db_service", None)


def _handle_wx_contacts(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """查询微信联系人列表。"""
    wx_svc = _get_wx_db_service(runtime)
    if wx_svc is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_SERVICE_UNAVAILABLE",
            stage="S_EDGE_WX_CONTACTS",
            detail="WxDbService not initialized (check WECHAT_DECRYPT_OUTPUT_DIR config)",
            event={},
        )
    try:
        include_groups = bool(envelope.payload.get("include_groups", True))
        contacts = wx_svc.get_contacts(include_groups=include_groups)
        contact_list = [
            {
                "wxid": c.wxid,
                "nickname": c.nickname,
                "remark": c.remark,
                "alias": c.alias,
                "is_group": c.is_group,
                "display_name": c.display_name(),
            }
            for c in contacts
        ]
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WX_CONTACTS",
            detail=f"found {len(contact_list)} contacts",
            event={"contacts": contact_list, "count": len(contact_list)},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_CONTACTS_ERROR",
            stage="S_EDGE_WX_CONTACTS",
            detail=f"wx contacts query failed: {exc}",
            event={},
        )


def _handle_wx_messages(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """查询微信聊天记录。"""
    wx_svc = _get_wx_db_service(runtime)
    if wx_svc is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_SERVICE_UNAVAILABLE",
            stage="S_EDGE_WX_MESSAGES",
            detail="WxDbService not initialized",
            event={},
        )
    try:
        conversation_id = str(envelope.payload.get("conversation_id") or "").strip() or None
        limit = _parse_int_clamped(envelope.payload.get("limit"), default=50, min_value=1, max_value=200)
        after_ts_ms = envelope.payload.get("after_ts_ms")
        before_ts_ms = envelope.payload.get("before_ts_ms")
        keyword = str(envelope.payload.get("keyword") or "").strip() or None
        msg_types_raw = envelope.payload.get("msg_types")
        msg_types = [int(t) for t in msg_types_raw] if isinstance(msg_types_raw, list) else None

        messages = wx_svc.get_messages(
            conversation_id=conversation_id,
            limit=limit,
            after_ts_ms=int(after_ts_ms) if after_ts_ms else None,
            before_ts_ms=int(before_ts_ms) if before_ts_ms else None,
            keyword=keyword,
            msg_types=msg_types,
        )
        msg_list = [
            {
                "msg_id": m.msg_id,
                "from_wxid": m.from_wxid,
                "to_wxid": m.to_wxid,
                "conversation_id": m.conversation_id,
                "content": m.content,
                "msg_type": m.msg_type,
                "ts_ms": m.ts_ms,
                "is_sender": m.is_sender,
                "media_url": m.media_url,
            }
            for m in messages
        ]
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WX_MESSAGES",
            detail=f"found {len(msg_list)} messages",
            event={"messages": msg_list, "count": len(msg_list)},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_MESSAGES_ERROR",
            stage="S_EDGE_WX_MESSAGES",
            detail=f"wx messages query failed: {exc}",
            event={},
        )


def _handle_wx_search(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """全文搜索微信聊天记录。"""
    wx_svc = _get_wx_db_service(runtime)
    if wx_svc is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_SERVICE_UNAVAILABLE",
            stage="S_EDGE_WX_SEARCH",
            detail="WxDbService not initialized",
            event={},
        )
    try:
        keyword = str(envelope.payload.get("keyword") or "").strip()
        if not keyword:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=EDGE_RPC_SCHEMA_INVALID,
                stage="S_EDGE_WX_SEARCH",
                detail="wx search requires non-empty keyword",
                event={},
            )
        limit = _parse_int_clamped(envelope.payload.get("limit"), default=20, min_value=1, max_value=100)
        messages = wx_svc.get_messages(keyword=keyword, limit=limit)
        msg_list = [
            {
                "msg_id": m.msg_id,
                "from_wxid": m.from_wxid,
                "conversation_id": m.conversation_id,
                "content": m.content,
                "msg_type": m.msg_type,
                "ts_ms": m.ts_ms,
                "is_sender": m.is_sender,
            }
            for m in messages
        ]
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WX_SEARCH",
            detail=f"found {len(msg_list)} messages for keyword '{keyword}'",
            event={"messages": msg_list, "count": len(msg_list), "keyword": keyword},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_SEARCH_ERROR",
            stage="S_EDGE_WX_SEARCH",
            detail=f"wx search failed: {exc}",
            event={},
        )


def _handle_wx_refresh(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """重新执行微信数据库解密（微信重新登录后密钥变化时调用）。"""
    config: EdgeConfig = getattr(runtime, "_edge_config", None) or EdgeConfig.__new__(EdgeConfig)
    wx_decrypt_output_dir = getattr(config, "wx_decrypt_output_dir", None)
    wx_db_storage_dir = getattr(config, "wx_db_storage_dir", None)
    wx_key_cache_path = getattr(config, "wx_key_cache_path", None)

    if not wx_db_storage_dir or not wx_decrypt_output_dir:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_CONFIG_MISSING",
            stage="S_EDGE_WX_REFRESH",
            detail="WECHAT_DB_STORAGE_DIR and WECHAT_DECRYPT_OUTPUT_DIR must be configured",
            event={},
        )
    try:
        from tools.wx4_decrypt import decrypt_all_databases
        results = decrypt_all_databases(
            wx_db_storage_dir,
            wx_decrypt_output_dir,
            key_cache_path=wx_key_cache_path,
        )
        ok = sum(1 for r in results.values() if r["status"] in ("ok", "ok_partial"))
        fail = sum(1 for r in results.values() if r["status"] == "fail")
        no_key = sum(1 for r in results.values() if r["status"] == "no_key")
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WX_REFRESH",
            detail=f"decrypt complete: ok={ok}, fail={fail}, no_key={no_key}",
            event={"ok": ok, "fail": fail, "no_key": no_key, "total": len(results)},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_REFRESH_ERROR",
            stage="S_EDGE_WX_REFRESH",
            detail=f"wx refresh failed: {exc}",
            event={},
        )


def _handle_wx_moments(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    """读取朋友圈（Sns.db）。"""
    wx_svc = _get_wx_db_service(runtime)
    if wx_svc is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_SERVICE_UNAVAILABLE",
            stage="S_EDGE_WX_MOMENTS",
            detail="WxDbService not initialized",
            event={},
        )
    try:
        limit = _parse_int_clamped(envelope.payload.get("limit"), default=20, min_value=1, max_value=100)
        moments = wx_svc.get_moments(limit=limit)
        moment_list = [
            {
                "moment_id": m.moment_id,
                "wxid": m.wxid,
                "content": m.content,
                "ts_ms": m.ts_ms,
                "media_urls": m.media_urls,
            }
            for m in moments
        ]
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_WX_MOMENTS",
            detail=f"found {len(moment_list)} moments",
            event={"moments": moment_list, "count": len(moment_list)},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code="WX_MOMENTS_ERROR",
            stage="S_EDGE_WX_MOMENTS",
            detail=f"wx moments query failed: {exc}",
            event={},
        )
