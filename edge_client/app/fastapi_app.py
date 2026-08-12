"""Edge FastAPI RPC 网关。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import threading
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
from edge_client.runtime_texts import edge_runtime_text
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
from edge_client.gateway.embodiment_runtime import (
    DurableEmbodimentRuntime,
    EdgeEmbodimentTransportError,
    SQLiteEdgeEmbodimentStore,
)
from natsume_contracts.edge.v1 import (
    CONFIG_VALUE_INVALID,
    EDGE_RPC_SCHEMA_INVALID,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    SERVICE_UNAVAILABLE_GENERIC,
    VTS_EXPRESSION_TIMEOUT,
    VOICE_SCHEMA_INVALID,
    now_ms,
)
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_INVALID,
    EMBODIMENT_RECEIPT_NOT_FOUND,
    EMBODIMENT_RECEIPT_QUERY_INVALID,
    EMBODIMENT_KIND_AVATAR,
    EMBODIMENT_KIND_DESKTOP,
    EMBODIMENT_KIND_MATEENGINE,
    EMBODIMENT_KIND_VOICE,
    parse_embodiment_command,
    parse_embodiment_receipt_query,
)
from natsume_contracts.edge.transport_v1 import (
    EDGE_SESSION_RECONNECT_INVALID,
)


STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"

_SPEAK_SEGMENT_PUNCT = set("。！？!?；;\n")
_SPEAK_SEGMENT_SOFT_PUNCT = set("，,、 ")
_SPEAK_SENTENCE_ENDINGS = set("。！？!?；;，,、")


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


def _resolve_embodiment_transport_db_path() -> Path:
    configured = str(os.environ.get("EDGE_EMBODIMENT_TRANSPORT_DB") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_root = str(os.environ.get("NATSUME_DATA_DIR") or "").strip()
    if data_root:
        root = Path(data_root).expanduser()
    else:
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        root = Path(local_app_data).expanduser() / "NatsumeAI" if local_app_data else Path.home() / ".natsume-ai"
    return (root / "edge" / "embodiment_transport.sqlite3").resolve()


def create_edge_fastapi_app(
    *,
    config: EdgeConfig | None = None,
    runtime: EdgeRuntime | None = None,
    replay_cache: NonceReplayCache | None = None,
    now_fn: Callable[[], int] | None = None,
    startup_hook: LifecycleHook | None = None,
    shutdown_hook: LifecycleHook | None = None,
    health_collector: HealthCollector | None = None,
    embodiment_store: SQLiteEdgeEmbodimentStore | None = None,
) -> FastAPI:
    # FastAPI 组合根同时拥有认证缓存、设备 runtime 和具身持久 store；路由只接收
    # 已验 envelope，不能各自构造临时设备对象来绕过会话栅栏或收据恢复。
    # 这是 Host 到本机具身能力的唯一 HTTP 组合面。应用保存进程级 nonce 缓存、
    # 生命周期 runtime 与持久 embodiment store；路由不能自行绕过这些边界调用
    # 麦克风、桌面或语音设备。
    # App 持有进程级 replay cache 与 embodiment 持久化运行时：每条 HTTP 请求
    # 只能借认证后的 envelope 进入设备 handler，播放完成由观察者回填持久收据。
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
    fallback_llm_enabled = bool(getattr(runtime_config, "fallback_llm_enabled", False))
    vlm_enabled = bool(getattr(runtime_config, "vlm_enabled", False))
    clock = now_fn or now_ms
    startup_runner = startup_hook or runtime_startup
    shutdown_runner = shutdown_hook or runtime_shutdown
    health_runner = health_collector or collect_health
    if embodiment_store is None:
        # 迁移完成后才暴露具身命令端点：任何可见副作用必须能写入 session fence 与
        # durable receipt；没有数据库模式时宁可启动失败也不运行无对账能力的命令。
        # 先迁移再开放命令面：具身副作用必须有可用的 session fence 和收据表，
        # 不能在临时内存状态下执行后才尝试补写证明。
        from edge_client.gateway.migration_runner import apply_edge_gateway_migrations

        embodiment_db_path = _resolve_embodiment_transport_db_path()
        apply_edge_gateway_migrations(embodiment_db_path)
        embodiment_store_value = SQLiteEdgeEmbodimentStore(embodiment_db_path, clock=clock)
    else:
        embodiment_store_value = embodiment_store
    embodiment_runtime = DurableEmbodimentRuntime(embodiment_store_value)
    voice_pipeline = getattr(runtime_value, "voice_pipeline", None)
    register_playback_completion = getattr(
        voice_pipeline,
        "register_playback_completion_observer",
        None,
    )
    playback_completion_unsubscribe = (
        register_playback_completion(embodiment_runtime.complete_voice_playback)
        if callable(register_playback_completion)
        else None
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        # 生命周期先启 runtime 再接流，关停反向取消播放回调后再释放 runtime；这可
        # 避免迟到的音频完成通知写入已关闭 store 或推进过期会话。
        # 播放完成观察者与持久运行时同时生灭；关机先注销，避免迟到回调写入
        # 已释放的 store 或把过期会话重新推进。
        # 生命周期先启动 runtime，再开放路由；关闭时先取消播放完成订阅，避免已释放
        # 的持久化 runtime 被迟到的音频回调写入。
        await _run_lifecycle_hook(startup_runner, runtime_value)
        try:
            yield
        finally:
            if callable(playback_completion_unsubscribe):
                playback_completion_unsubscribe()
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
    app.state.embodiment_store = embodiment_store_value
    app.state.embodiment_runtime = embodiment_runtime

    @app.get("/v1/edge/health")
    async def edge_health() -> dict[str, Any]:
        snapshot_ts = int(clock())
        collected = _run_health_collector(health_runner, runtime_value, ts_ms=snapshot_ts)
        health = build_edge_health_snapshot(runtime_value, ts_ms=snapshot_ts, collected=collected)
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

    @app.get("/v1/edge/avatar_state")
    async def edge_avatar_state() -> dict[str, Any]:
        from edge_client.runtime.avatar_behavior import build_avatar_behavior_state

        snapshot_ts = int(clock())
        state = build_avatar_behavior_state(runtime_value, ts_ms=snapshot_ts)
        return _response(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            detail="edge avatar behavior state snapshot",
            event=state,
            ts_ms=snapshot_ts,
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

    @app.post("/v1/edge/asr")
    async def edge_asr(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=(runtime_config.rpc_gateway_enabled and runtime_config.asr_enabled),
            disabled_detail="edge asr disabled by config",
            handler=_handle_asr,
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

    @app.post("/v1/edge/embodiment")
    async def edge_embodiment(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge embodiment command gateway disabled by config",
            handler=lambda active_runtime, envelope: _handle_embodiment_command(
                active_runtime,
                envelope,
                transport_runtime=embodiment_runtime,
            ),
        )

    @app.post("/v1/edge/embodiment/session")
    async def edge_embodiment_session(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge embodiment session gateway disabled by config",
            handler=lambda _active_runtime, envelope: _handle_embodiment_session(
                envelope,
                store=embodiment_store_value,
            ),
        )

    @app.post("/v1/edge/embodiment/receipt")
    async def edge_embodiment_receipt(request: Request) -> JSONResponse:
        """Return one Edge-durable receipt through the normal HMAC envelope.

        This is a reconciliation read, not a replay endpoint.  The handler
        requires the exact current session lease, its owner identity, and the
        command's stable idempotency identity before it reveals a receipt.
        """

        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge embodiment receipt gateway disabled by config",
            handler=lambda _active_runtime, envelope: _handle_embodiment_receipt_query(
                envelope,
                store=embodiment_store_value,
            ),
        )

    @app.post("/v1/edge/desktop_pet")
    async def edge_desktop_pet(request: Request) -> JSONResponse:
        return await _handle_envelope_request(
            request=request,
            config=runtime_config,
            runtime=runtime_value,
            replay=replay_value,
            clock=clock,
            enabled=runtime_config.rpc_gateway_enabled,
            disabled_detail="edge rpc gateway disabled by config",
            handler=_handle_desktop_pet,
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
                and fallback_llm_enabled
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
                and fallback_llm_enabled
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
                and fallback_llm_enabled
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
                and fallback_llm_enabled
            ),
            disabled_detail="edge fallback llm disabled by config",
            handler=_handle_fallback_context_compress,
        )

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
            enabled=runtime_config.rpc_gateway_enabled and vlm_enabled,
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

    # ── T-123：Phase 6 下行链路 - host_brain → edge_client 指令端点 ────────
    @app.post("/internal/tribe_command")
    async def internal_tribe_command(request: Request) -> JSONResponse:
        """接收来自 host_brain 的下行 Tribe 指令。

        T-123：Phase 6 躯干族独立的下行链路。
        host_brain 侧的 EdgeCommandPusher 通过此端点向 edge_client 推送指令，
        例如：
        - LimbicAgent 推送情绪状态变化 → SomaticAgent 驱动 vts_driver 表情
        - CortexAgent 推送门控指令 → ChannelAgent 控制消息路由

        请求体格式（JSON）：
        {
            "command_type": "EMOTION_STATE_CHANGED" | "GATE_CONTROL" | ...,
            "source_agent": "limbic_agent" | "cortex_agent" | ...,
            "target_agent": "edge.somatic" | "edge.channel" | ...,
            "payload": {...},
            "schema_version": "tribe.command.v1",
            "ts_ms": 1234567890
        }

        鉴权：通过 X-Internal-Token 请求头校验（与 host_brain /internal/tribe_event 同一 token）。
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe_command")
        ts = int(clock())

        # ── 鉴权：校验 X-Internal-Token ──────────────────────────────────
        _internal_token = str(
            getattr(runtime_config, "internal_tribe_token", None)
            or __import__("os").environ.get("HOST_INTERNAL_TRIBE_TOKEN", "")
        ).strip()
        if _internal_token:
            _req_token = str(request.headers.get("X-Internal-Token") or "").strip()
            if _req_token != _internal_token:
                _log.warning("tribe_command: token mismatch, rejected")
                return JSONResponse(
                    {"accepted": False, "reason": "UNAUTHORIZED", "ts_ms": ts},
                    status_code=401,
                )

        # ── 解析请求体 ────────────────────────────────────────────────────
        try:
            body = await request.json()
        except Exception as _parse_exc:
            _log.warning("tribe_command: invalid JSON body: %s", _parse_exc)
            return JSONResponse(
                {"accepted": False, "reason": "INVALID_JSON", "ts_ms": ts},
                status_code=400,
            )

        command_type = str(body.get("command_type") or "").strip().upper()
        source_agent = str(body.get("source_agent") or "").strip()
        target_agent = str(body.get("target_agent") or "").strip()
        payload = dict(body.get("payload") or {})

        if not command_type:
            return JSONResponse(
                {"accepted": False, "reason": "COMMAND_TYPE_REQUIRED", "ts_ms": ts},
                status_code=400,
            )

        _log.debug(
            "tribe_command received: type=%s source=%s target=%s",
            command_type, source_agent, target_agent,
        )

        # ── T-169：通过 EdgeTribeBridge.receive_command() 统一分发 ──────────
        try:
            registry = getattr(runtime_value, "tribe_registry", None)
            if registry is None:
                return JSONResponse(
                    {
                        "accepted": False,
                        "reason": "EDGE_TRIBE_COMPOSITION_UNAVAILABLE",
                        "ts_ms": ts,
                    },
                    status_code=503,
                )
            result = registry.bridge.receive_command(
                command_type=command_type,
                source_agent=source_agent,
                target_agent=target_agent or None,
                payload=payload,
                command_id=str(body.get("command_id") or "").strip() or None,
                schema_version=str(body.get("schema_version") or "tribe.command.v1"),
                somatic_agent=registry.somatic,
                peripheral_agent=registry.peripheral,
                channel_agent=registry.channel,
            )
            dispatched = bool(result.get("dispatched", False))
            _log.debug(
                "tribe_command dispatched via bridge: type=%s routed_to=%s ok=%s",
                command_type, result.get("routed_to"), result.get("ok"),
            )

            return JSONResponse({
                "accepted": True,
                "dispatched": dispatched,
                "command_type": command_type,
                "ts_ms": ts,
            })

        except Exception as exc:
            _log.warning("tribe_command dispatch error: %s", exc)
            return JSONResponse(
                {"accepted": False, "reason": "DISPATCH_ERROR", "detail": str(exc), "ts_ms": ts},
                status_code=500,
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
    # 所有可执行 RPC 共用的门禁顺序：严格 JSON → 信封结构 → HMAC/重放验证
    # → 功能开关 → 单个 handler。任何前置失败都不应触及设备副作用。
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
        # 验签与 nonce 检查失败只返回稳定原因；未认证请求不能借“功能已关闭”
        # 等差异探测本机能力配置。
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
        # 认证通过也不等于功能获准；配置关卡为设备能力提供独立的 fail-closed
        # 运行时撤销面。
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
        # handler 异常不能越过 HTTP 边界；响应表明服务不可用而非效果已完成，
        # 由持久具身运输层决定是否存在可查询的在途收据。
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


def _handle_embodiment_command(
    runtime: EdgeRuntime,
    envelope: EdgeRpcEnvelope,
    *,
    transport_runtime: DurableEmbodimentRuntime,
) -> EdgeGatewayResult:
    # 解析后的命令先由 DurableEmbodimentRuntime 取得会话租约并按幂等键认领，
    # 只有成功认领的执行者能触碰设备，随后将精确结果封存为可对账收据。
    """Validate, durably claim, execute once, and persist the exact receipt."""
    try:
        command = parse_embodiment_command(envelope.payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EMBODIMENT_COMMAND_INVALID,
            stage="S_EDGE_EMBODIMENT_SCHEMA",
            detail=f"invalid embodiment command: {exc}",
            event={},
        )
    try:
        receipt = transport_runtime.execute(
            command,
            lambda active_command: _execute_embodiment_effect(
                runtime,
                envelope,
                active_command,
            ),
        )
    except EdgeEmbodimentTransportError as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=exc.reason_code,
            stage="S_EDGE_EMBODIMENT_AUTHORITY",
            detail=f"edge embodiment transport rejected command: {exc.reason_code}",
            event={},
        )
    return EdgeGatewayResult(
        success=receipt.success,
        status=receipt.status,
        reason_code=receipt.reason_code,
        stage="S_EDGE_EMBODIMENT_RECEIPT",
        detail=receipt.detail,
        event={"receipt": receipt.as_dict(), "effect": dict(receipt.event or {})},
    )


def _execute_embodiment_effect(
    runtime: EdgeRuntime,
    envelope: EdgeRpcEnvelope,
    command: Any,
) -> EdgeGatewayResult:
    # 此处分派的前提是 transport 已完成授权、fence 与幂等主张；不能把本函数
    # 当作自由调用的设备快捷入口。
    effect_request_id = (
        command.command_id
        if command.kind == EMBODIMENT_KIND_VOICE and command.action == "speak"
        else command.request_id
    )
    effect_envelope = EdgeRpcEnvelope(
        request_id=effect_request_id,
        ts_ms=command.issued_at_ms,
        nonce=envelope.nonce,
        signature=envelope.signature,
        payload={"action": command.action, **dict(command.payload)},
    )
    if command.kind == EMBODIMENT_KIND_VOICE:
        if command.action == "barge_in":
            effect = _handle_barge_in(runtime, effect_envelope)
        elif command.action == "asr_control":
            effect = _handle_asr(runtime, effect_envelope)
        elif command.action == "release":
            effect = EdgeGatewayResult(
                success=True,
                status="APPLIED",
                reason_code=None,
                stage="S_EDGE_VOICE_RELEASE",
                detail="voice stream released; queued playback remains authoritative",
                event={"external_side_effects": False, "release_applied": True},
            )
        else:
            effect = _handle_speak(runtime, effect_envelope)
    elif command.kind == EMBODIMENT_KIND_AVATAR:
        if command.action == "release":
            effect = EdgeGatewayResult(
                success=True,
                status="APPLIED",
                reason_code=None,
                stage="S_EDGE_AVATAR_RELEASE",
                detail="avatar stream released; playback completion owns idle transition",
                event={"external_side_effects": False, "release_applied": True},
            )
        elif command.action == "desktop_pet":
            pet_payload = dict(command.payload)
            pet_payload["action"] = str(
                pet_payload.pop("desktop_pet_action", "status") or "status"
            ).strip().lower()
            effect = _handle_desktop_pet(
                runtime,
                EdgeRpcEnvelope(
                    request_id=effect_envelope.request_id,
                    ts_ms=effect_envelope.ts_ms,
                    nonce=effect_envelope.nonce,
                    signature=effect_envelope.signature,
                    payload=pet_payload,
                ),
            )
        else:
            effect = _handle_vts(runtime, effect_envelope)
    elif command.kind == EMBODIMENT_KIND_DESKTOP:
        if command.action == "screenshot":
            effect = _handle_screenshot(runtime, effect_envelope)
        elif command.action in {"clipboard_read", "clipboard_write"}:
            from edge_client.actuators.desktop_clipboard import (
                execute_desktop_clipboard_effect,
            )

            outcome = execute_desktop_clipboard_effect(
                action=command.action,
                payload=command.payload,
            )
            effect = EdgeGatewayResult(
                success=bool(outcome.success),
                status=str(outcome.status),
                reason_code=outcome.reason_code,
                stage="S_EDGE_CLIPBOARD",
                detail=str(outcome.detail),
                event=dict(outcome.event),
            )
        elif command.action == "visual_locate":
            effect = _handle_visual_locate(runtime, effect_envelope)
        else:
            effect = _handle_automate(runtime, effect_envelope)
    elif command.kind == EMBODIMENT_KIND_MATEENGINE:
        from edge_client.actuators.mateengine_driver import execute_mateengine_effect

        outcome = execute_mateengine_effect(action=command.action, payload=command.payload)
        effect = EdgeGatewayResult(
            success=bool(outcome.get("success")),
            status=str(outcome.get("status") or STATUS_REJECTED),
            reason_code=(str(outcome.get("reason_code") or "").strip() or None),
            stage="S_EDGE_MATEENGINE",
            detail=str(outcome.get("detail") or "MateEngine command completed"),
            event=dict(outcome),
        )
    else:  # pragma: no cover - parser owns kind validation
        raise AssertionError(command.kind)
    return EdgeGatewayResult(
        success=effect.success,
        status=effect.status,
        reason_code=effect.reason_code,
        stage=effect.stage,
        detail=effect.detail,
        event={
            **dict(effect.event),
            "kind": command.kind,
            "action": command.action,
            "authority_ref": command.authority_ref,
            "grant_ref": command.grant_ref,
            "effect": dict(effect.event),
        },
    )


def _handle_embodiment_session(
    envelope: EdgeRpcEnvelope,
    *,
    store: SQLiteEdgeEmbodimentStore,
) -> EdgeGatewayResult:
    # 会话租约把 Host 重连与本机副作用绑定到 fencing token。仅 JSON 真值可请求
    # 轮换 fence，避免 "false" 等字符串在替换运行时时意外使旧持有者失效。
    payload = dict(envelope.payload or {})
    try:
        ttl_ms = max(1_000, min(300_000, int(payload.get("ttl_ms") or 30_000)))
        reconnect_fence = payload.get("reconnect_fencing_token")
        lease = store.open_session(
            session_id=str(payload.get("session_id") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            ttl_ms=ttl_ms,
            reconnect_lease_id=(
                str(payload.get("reconnect_lease_id") or "").strip() or None
            ),
            reconnect_fencing_token=(
                int(reconnect_fence) if reconnect_fence is not None else None
            ),
            # This value intentionally reaches the store without bool-like
            # coercion: only a JSON boolean may ask Edge to invalidate the
            # current live fence during a Host runtime replacement.
            rotate_fencing_token=payload.get("rotate_fencing_token", False),
        )
    except (TypeError, ValueError, EdgeEmbodimentTransportError) as exc:
        reason_code = getattr(exc, "reason_code", EDGE_SESSION_RECONNECT_INVALID)
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=str(reason_code),
            stage="S_EDGE_EMBODIMENT_SESSION",
            detail=f"edge embodiment session rejected: {reason_code}",
            event={},
        )
    return EdgeGatewayResult(
        success=True,
        status=STATUS_ACCEPTED,
        reason_code=None,
        stage="S_EDGE_EMBODIMENT_SESSION",
        detail="edge embodiment transport lease active",
        event={"lease": lease.as_dict()},
    )


def _handle_embodiment_receipt_query(
    envelope: EdgeRpcEnvelope,
    *,
    store: SQLiteEdgeEmbodimentStore,
) -> EdgeGatewayResult:
    # 查询严格绑定当前租约持有者和幂等身份；“未找到”只代表没有匹配收据，
    # 不是对命令从未执行的负面证明，更不能触发重放。
    """Read, but never replay, one receipt owned by the current lease holder."""

    try:
        query = parse_embodiment_receipt_query(envelope.payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=EMBODIMENT_RECEIPT_QUERY_INVALID,
            stage="S_EDGE_EMBODIMENT_RECEIPT_QUERY",
            detail=f"invalid embodiment receipt query: {exc}",
            event={},
        )
    try:
        lookup = store.query_receipt_for_live_lease(
            session_id=query.session_id,
            lease_id=query.lease_id,
            owner_id=query.owner_id,
            fencing_token=query.fencing_token,
            command_id=query.command_id,
            idempotency_key=query.idempotency_key,
        )
    except EdgeEmbodimentTransportError as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=exc.reason_code,
            stage="S_EDGE_EMBODIMENT_RECEIPT_QUERY",
            detail=f"edge embodiment receipt query rejected: {exc.reason_code}",
            event={},
        )

    query_event: dict[str, Any] = {
        "schema_version": query.schema_version,
        "command_id": query.command_id,
        "idempotency_key": query.idempotency_key,
        "session_id": query.session_id,
        "owner_id": query.owner_id,
    }
    if lookup is None:
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=EMBODIMENT_RECEIPT_NOT_FOUND,
            stage="S_EDGE_EMBODIMENT_RECEIPT_QUERY",
            detail="no durable embodiment receipt matches the live session query",
            event={
                "receipt_query": {**query_event, "status": "NOT_FOUND"},
                "receipt": None,
            },
        )

    return EdgeGatewayResult(
        success=True,
        status=STATUS_ACCEPTED,
        reason_code=None,
        stage="S_EDGE_EMBODIMENT_RECEIPT_QUERY",
        detail="edge durable embodiment receipt found",
        event={
            "receipt_query": {
                **query_event,
                "status": "FOUND",
                "receipt_sha256": lookup.receipt_sha256,
            },
            "receipt": lookup.receipt.as_dict(),
        },
    )


def _handle_speak(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    # 语音命令先进入可持久观察的队列，再选择快速确认或等待播放起始；快速确认
    # 只说明已排队，不能被 Host 当作音频已经输出的收据。
    import time as _time

    handler_t0 = _time.monotonic()
    fast_ack_default = _parse_bool_like(os.environ.get("EDGE_TTS_FAST_ACK_ENABLED"), default=True)
    wait_playback_start = _parse_bool_like(
        envelope.payload.get("wait_playback_start"),
        default=not fast_ack_default,
    )
    segments = _split_speak_payload_for_streaming(envelope.payload)
    primary_payload = dict(envelope.payload)
    segmented_playback = len(segments) > 1
    if segmented_playback:
        # 首段用原 request_id 取得主链收据，后续段以派生 ID 异步排队，避免长文
        # 合成阻塞 RPC；任何后续段问题仍由语音队列自身状态追踪。
        primary_payload = _with_speak_segment_payload(
            envelope.payload,
            text=segments[0],
            segment_index=1,
            segment_count=len(segments),
            parent_request_id=envelope.request_id,
        )
    pipeline_t0 = _time.monotonic()
    pipeline_result = runtime.voice_pipeline.enqueue_speak(
        request_id=envelope.request_id,
        payload=primary_payload,
        ts_ms=envelope.ts_ms,
        auto_start=True,
        background_start=not wait_playback_start,
    )
    pipeline_elapsed_s = _time.monotonic() - pipeline_t0
    if segmented_playback:
        _enqueue_remaining_speak_segments(
            runtime=runtime,
            parent_request_id=envelope.request_id,
            source_payload=envelope.payload,
            segments=segments,
            ts_ms=envelope.ts_ms,
            primary_playback_started=wait_playback_start,
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
    event["fast_ack"] = not wait_playback_start
    event["wait_playback_start"] = wait_playback_start
    event["background_start_scheduled"] = bool(pipeline_result.background_start_scheduled)
    event["edge_speak_pipeline_elapsed_s"] = round(pipeline_elapsed_s, 4)
    if segmented_playback:
        event["segmented_playback"] = True
        event["segment_index"] = 1
        event["segment_count"] = len(segments)
        event["segment_chars"] = [len(segment) for segment in segments]
        event["segment_parent_request_id"] = envelope.request_id
        event["segment_prefetch_enabled"] = _parse_bool_like(
            os.environ.get("EDGE_TTS_SEGMENT_PREFETCH_ENABLED"),
            default=True,
        )
        event["segment_prefetch_max_ahead"] = _parse_int_clamped(
            os.environ.get("EDGE_TTS_SEGMENT_PREFETCH_MAX_AHEAD"),
            default=1,
            min_value=0,
            max_value=8,
        )
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

    event["edge_speak_handler_elapsed_s"] = round(_time.monotonic() - handler_t0, 4)

    return EdgeGatewayResult(
        success=success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=detail,
        event=event,
    )


def _split_speak_payload_for_streaming(payload: dict[str, Any]) -> list[str]:
    if not _parse_bool_like(os.environ.get("EDGE_TTS_SEGMENTED_PLAYBACK_ENABLED"), default=True):
        return []
    if payload.get("segment_parent_request_id") or payload.get("segment_index"):
        return []
    text = str(payload.get("text") or payload.get("message") or "").strip()
    if not text:
        return []
    min_chars = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENTED_PLAYBACK_MIN_CHARS"),
        default=48,
        min_value=12,
        max_value=2000,
    )
    max_chars = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENTED_PLAYBACK_MAX_CHARS"),
        default=36,
        min_value=12,
        max_value=500,
    )
    first_max_chars = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENTED_PLAYBACK_FIRST_MAX_CHARS"),
        default=18,
        min_value=10,
        max_value=200,
    )
    max_segments = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENTED_PLAYBACK_MAX_SEGMENTS"),
        default=8,
        min_value=2,
        max_value=64,
    )
    if len(text) < int(min_chars):
        return []

    clean_segments = _split_text_into_speak_segments(
        text,
        first_max_chars=int(first_max_chars),
        max_chars=int(max_chars),
        max_segments=int(max_segments),
    )
    return clean_segments if len(clean_segments) > 1 else []


def _split_text_into_speak_segments(
    text: str,
    *,
    first_max_chars: int,
    max_chars: int,
    max_segments: int,
) -> list[str]:
    segments: list[str] = []
    cursor = 0
    text_len = len(text)
    while cursor < text_len and len(segments) < max_segments:
        target_chars = int(first_max_chars) if not segments else int(max_chars)
        remaining_slots = max(1, int(max_segments) - len(segments))
        if remaining_slots == 1:
            end = text_len
        else:
            end = _choose_speak_segment_end(text, cursor, target_chars)
        segment = text[cursor:end].strip()
        cursor = end
        while cursor < text_len and text[cursor].isspace():
            cursor += 1
        if segment:
            segments.append(_normalize_speak_segment_text(segment, is_last=cursor >= text_len))
    return [segment for segment in segments if segment]


def _choose_speak_segment_end(text: str, start: int, target_chars: int) -> int:
    text_len = len(text)
    hard_min = start + max(8, int(target_chars * 0.45))
    target_end = min(text_len, start + int(target_chars))
    if target_end >= text_len:
        return text_len
    for index in range(target_end, min(text_len, start + int(target_chars * 1.35))):
        if text[index] in _SPEAK_SEGMENT_PUNCT and index + 1 >= hard_min:
            return index + 1
    for index in range(target_end - 1, max(start, hard_min) - 1, -1):
        if text[index] in _SPEAK_SEGMENT_PUNCT:
            return index + 1
    for index in range(target_end - 1, max(start, hard_min) - 1, -1):
        if text[index] in _SPEAK_SEGMENT_SOFT_PUNCT:
            return index + 1
    return target_end


def _normalize_speak_segment_text(segment: str, *, is_last: bool) -> str:
    clean = str(segment or "").strip()
    if not clean:
        return ""
    if is_last or clean[-1] in _SPEAK_SENTENCE_ENDINGS:
        return clean
    return f"{clean}，"


def _with_speak_segment_payload(
    payload: dict[str, Any],
    *,
    text: str,
    segment_index: int,
    segment_count: int,
    parent_request_id: str,
) -> dict[str, Any]:
    segment_payload = dict(payload)
    if "message" in segment_payload:
        segment_payload["message"] = text
    segment_payload["text"] = text
    segment_payload["segment_parent_request_id"] = parent_request_id
    segment_payload["segment_index"] = int(segment_index)
    segment_payload["segment_count"] = int(segment_count)
    segment_payload["wait_playback_start"] = False
    return segment_payload


def _enqueue_remaining_speak_segments(
    *,
    runtime: EdgeRuntime,
    parent_request_id: str,
    source_payload: dict[str, Any],
    segments: list[str],
    ts_ms: int,
    primary_playback_started: bool,
) -> None:
    def _runner() -> None:
        queued_payloads: list[tuple[str, dict[str, Any]]] = []
        for offset, segment in enumerate(segments[1:], start=2):
            segment_request_id = f"{parent_request_id}::seg{offset}"
            segment_payload = _with_speak_segment_payload(
                source_payload,
                text=segment,
                segment_index=offset,
                segment_count=len(segments),
                parent_request_id=parent_request_id,
            )
            runtime.voice_pipeline.enqueue_speak(
                request_id=segment_request_id,
                payload=segment_payload,
                ts_ms=ts_ms,
                auto_start=True,
                background_start=True,
            )
            queued_payloads.append((segment_request_id, segment_payload))
        _prefetch_speak_segment_audio_lookahead(
            runtime=runtime,
            queued_payloads=queued_payloads,
            primary_playback_started=primary_playback_started,
        )

    threading.Thread(
        target=_runner,
        name="edge-tts-segment-enqueue",
        daemon=True,
    ).start()


def _prefetch_speak_segment_audio_lookahead(
    *,
    runtime: EdgeRuntime,
    queued_payloads: list[tuple[str, dict[str, Any]]],
    primary_playback_started: bool,
) -> None:
    if not queued_payloads:
        return
    if not _parse_bool_like(os.environ.get("EDGE_TTS_SEGMENT_PREFETCH_ENABLED"), default=True):
        return
    max_ahead = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENT_PREFETCH_MAX_AHEAD"),
        default=1,
        min_value=0,
        max_value=8,
    )
    if max_ahead <= 0:
        return
    default_delay_ms = 0 if primary_playback_started else 800
    delay_ms = _parse_int_clamped(
        os.environ.get("EDGE_TTS_SEGMENT_PREFETCH_DELAY_MS"),
        default=default_delay_ms,
        min_value=0,
        max_value=10000,
    )
    if delay_ms > 0:
        threading.Event().wait(float(delay_ms) / 1000.0)
    for request_id, payload in queued_payloads[:max_ahead]:
        _prefetch_speak_segment_audio(
            runtime=runtime,
            request_id=request_id,
            payload=payload,
        )


def _prefetch_speak_segment_audio(
    *,
    runtime: EdgeRuntime,
    request_id: str,
    payload: dict[str, Any],
) -> None:
    voice_pipeline = getattr(runtime, "voice_pipeline", None)
    speech_queue = getattr(voice_pipeline, "speech_queue", None)
    synthesizer = getattr(speech_queue, "_synthesizer", None)
    synthesize = getattr(synthesizer, "synthesize", None)
    if not callable(synthesize):
        return
    text = str(payload.get("text") or payload.get("message") or "").strip()
    if not text:
        return
    try:
        result = synthesize(
            text,
            language=str(payload.get("language") or payload.get("lang_hint") or "zh"),
            emotion_tag=str(payload.get("emotion_tag") or "").strip() or None,
            emotion_intensity=payload.get("emotion_intensity"),
            speed_factor=payload.get("speed_factor"),
        )
        print(
            "EDGE_TTS_SEGMENT_PREFETCH "
            f"request_id={request_id} success={bool(getattr(result, 'success', False))} "
            f"detail={str(getattr(result, 'detail', '') or '')}",
            flush=True,
        )
    except Exception as exc:  # pragma: no cover - prefetch is best effort
        print(
            f"EDGE_TTS_SEGMENT_PREFETCH request_id={request_id} success=False detail={exc}",
            flush=True,
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


def _handle_asr(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    asr_service = getattr(runtime, "asr_service", None)
    if asr_service is None:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_ASR_CONTROL",
            detail="asr service unavailable",
            event={"action": str(envelope.payload.get("action") or "status")},
        )

    action = str(envelope.payload.get("action") or "status").strip().lower()
    if action in {"status", "health"}:
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_ASR_CONTROL",
            detail="asr status",
            event={
                "action": "status",
                "asr": asr_service.health(),
            },
        )

    if action in {"voiceprint_status", "speaker_status"}:
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_ASR_CONTROL",
            detail="asr voiceprint status",
            event={
                "action": "voiceprint_status",
                "voiceprint": asr_service.voiceprint_status(),
                "asr": asr_service.health(),
            },
        )

    if action in {"voiceprint_enroll", "speaker_enroll"}:
        audio_path = str(envelope.payload.get("audio_path") or "").strip()
        if not audio_path:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_SCHEMA_INVALID,
                stage="S_EDGE_ASR_CONTROL",
                detail="voiceprint_enroll requires audio_path",
                event={"action": action},
            )
        label = str(envelope.payload.get("label") or "primary_user").strip() or "primary_user"
        result = asr_service.enroll_voiceprint_from_wav(audio_path, label=label)
        success = bool(result.get("success"))
        return EdgeGatewayResult(
            success=success,
            status=STATUS_ACCEPTED if success else STATUS_REJECTED,
            reason_code=result.get("reason_code"),
            stage="S_EDGE_ASR_CONTROL",
            detail=str(result.get("detail") or ""),
            event={
                "action": "voiceprint_enroll",
                "result": dict(result),
                "voiceprint": asr_service.voiceprint_status(),
                "asr": asr_service.health(),
            },
        )

    if action in {"start", "listen", "start_session"}:
        health_before = asr_service.health()
        if bool(health_before.get("running")):
            return EdgeGatewayResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage="S_EDGE_ASR_CONTROL",
            detail="asr already running",
            event={
                "action": "start",
                "already_running": True,
                "asr": health_before,
                "avatar_behavior": _apply_asr_control_avatar_behavior(
                    runtime=runtime,
                    envelope=envelope,
                    action="start",
                    behavior_id="listening",
                    success=True,
                ),
            },
        )
        session_id = str(envelope.payload.get("session_id") or "").strip() or None
        result = asr_service.start_session(session_id=session_id)
        return EdgeGatewayResult(
            success=bool(result.success),
            status=STATUS_ACCEPTED if result.success else STATUS_REJECTED,
            reason_code=result.reason_code,
            stage="S_EDGE_ASR_CONTROL",
            detail=result.detail,
            event={
                "action": "start",
                "result": result.as_dict() if hasattr(result, "as_dict") else {
                    "success": bool(result.success),
                    "reason_code": result.reason_code,
                    "detail": result.detail,
                },
                "asr": asr_service.health(),
                "avatar_behavior": _apply_asr_control_avatar_behavior(
                    runtime=runtime,
                    envelope=envelope,
                    action="start",
                    behavior_id="listening",
                    success=bool(result.success),
                ),
            },
        )

    if action in {"stop", "mute", "stop_session"}:
        health_before = asr_service.health()
        if not bool(health_before.get("running")):
            return EdgeGatewayResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage="S_EDGE_ASR_CONTROL",
            detail="asr already stopped",
            event={
                "action": "stop",
                "already_stopped": True,
                "asr": health_before,
                "avatar_behavior": _apply_asr_control_avatar_behavior(
                    runtime=runtime,
                    envelope=envelope,
                    action="stop",
                    behavior_id="idle",
                    success=True,
                ),
            },
        )
        result = asr_service.stop_session()
        return EdgeGatewayResult(
            success=bool(result.success),
            status=STATUS_ACCEPTED if result.success else STATUS_REJECTED,
            reason_code=result.reason_code,
            stage="S_EDGE_ASR_CONTROL",
            detail=result.detail,
            event={
                "action": "stop",
                "result": result.as_dict() if hasattr(result, "as_dict") else {
                    "success": bool(result.success),
                    "reason_code": result.reason_code,
                    "detail": result.detail,
                },
                "asr": asr_service.health(),
                "avatar_behavior": _apply_asr_control_avatar_behavior(
                    runtime=runtime,
                    envelope=envelope,
                    action="stop",
                    behavior_id="idle",
                    success=bool(result.success),
                ),
            },
        )

    if action in {"restart", "reset"}:
        health_before = asr_service.health()
        stop_result = None
        if bool(health_before.get("running")):
            stop_result = asr_service.stop_session()
        session_id = str(envelope.payload.get("session_id") or "").strip() or None
        start_result = asr_service.start_session(session_id=session_id)
        return EdgeGatewayResult(
            success=bool(start_result.success),
            status=STATUS_ACCEPTED if start_result.success else STATUS_REJECTED,
            reason_code=start_result.reason_code,
            stage="S_EDGE_ASR_CONTROL",
            detail=(
                f"asr restarted: {start_result.detail}"
                if start_result.success
                else f"asr restart failed: {start_result.detail}"
            ),
            event={
                "action": "restart",
                "stop_result": (
                    None
                    if stop_result is None
                    else (
                        stop_result.as_dict()
                        if hasattr(stop_result, "as_dict")
                        else {
                            "success": bool(stop_result.success),
                            "reason_code": stop_result.reason_code,
                            "detail": stop_result.detail,
                        }
                    )
                ),
                "start_result": (
                    start_result.as_dict()
                    if hasattr(start_result, "as_dict")
                    else {
                        "success": bool(start_result.success),
                        "reason_code": start_result.reason_code,
                        "detail": start_result.detail,
                    }
                ),
                "asr": asr_service.health(),
                "avatar_behavior": _apply_asr_control_avatar_behavior(
                    runtime=runtime,
                    envelope=envelope,
                    action="restart",
                    behavior_id="listening",
                    success=bool(start_result.success),
                ),
            },
        )

    return EdgeGatewayResult(
        success=False,
        status=STATUS_REJECTED,
        reason_code=VOICE_SCHEMA_INVALID,
        stage="S_EDGE_ASR_CONTROL",
        detail="asr action must be one of status/start/stop/restart",
        event={"action": action},
    )


def _apply_asr_control_avatar_behavior(
    *,
    runtime: EdgeRuntime,
    envelope: EdgeRpcEnvelope,
    action: str,
    behavior_id: str,
    success: bool,
) -> dict[str, Any] | None:
    if not success:
        return None
    if not _parse_bool_like(
        os.environ.get("EDGE_AVATAR_BEHAVIOR_ASR_CONTROL_AUTO_STATE_ENABLED"),
        default=True,
    ):
        return None
    voice_pipeline = getattr(runtime, "voice_pipeline", None)
    apply_behavior = getattr(voice_pipeline, "_apply_quiet_avatar_behavior", None)
    if not callable(apply_behavior):
        return None
    speech_queue = getattr(voice_pipeline, "speech_queue", None)
    speech_active = bool(getattr(speech_queue, "active_playback", None))
    if speech_active and not _parse_bool_like(
        os.environ.get("EDGE_AVATAR_BEHAVIOR_ASR_DURING_SPEECH_ENABLED"),
        default=False,
    ):
        return {
            "success": True,
            "state": "skipped",
            "reason": "speech_active",
            "detail": "ASR control avatar state skipped while AI speech is active",
            "behavior_id": str(behavior_id or ""),
            "source": f"asr_control_{action}",
        }
    payload = {
        **dict(envelope.payload or {}),
        "request_id": str(envelope.request_id or ""),
        "ts_ms": int(envelope.ts_ms),
        "source": f"asr_control_{action}",
        "body_hint_enabled": False,
    }
    return apply_behavior(
        behavior_id=str(behavior_id or ""),
        payload=payload,
        source=f"asr_control_{action}",
    )


def _handle_vts(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    action = str(envelope.payload.get("action") or "").strip().lower()
    if action in {"status", "state", "avatar_state", "debug_state"}:
        from edge_client.runtime.avatar_behavior import build_avatar_behavior_state

        state = build_avatar_behavior_state(runtime, ts_ms=envelope.ts_ms)
        return EdgeGatewayResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_EDGE_AVATAR_STATE",
            detail="edge avatar behavior state snapshot",
            event=state,
        )

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
    elif action in {"set_mouth", "mouth", "set_mouth_open_level"}:
        raw_level = envelope.payload.get("value", envelope.payload.get("level"))
        try:
            level = max(0.0, min(1.0, float(raw_level)))
        except (TypeError, ValueError):
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_SCHEMA_INVALID,
                stage="S_EDGE_VTS",
                detail="set_mouth requires numeric value or level in [0,1]",
                event={},
            )
        motion_values = envelope.payload.get("motion_values")
        result = runtime.vts_driver.set_mouth_open_level(
            level,
            _include_companions=True,
            _batch_companions=True,
            _motion_values=motion_values if isinstance(motion_values, dict) else None,
        )
    elif action in {"body_action", "perform_action"}:
        action_id = str(envelope.payload.get("action_id") or "").strip()
        if not action_id:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_SCHEMA_INVALID,
                stage="S_EDGE_VTS",
                detail="body_action requires action_id",
                event={},
            )
        voice_pipeline = getattr(runtime, "voice_pipeline", None)
        if voice_pipeline is None:
            return EdgeGatewayResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
                stage="S_EDGE_VTS",
                detail="voice pipeline unavailable",
                event={},
            )
        summary = voice_pipeline.perform_body_action(
            action_id=action_id,
            emotion_tag=envelope.payload.get("emotion_tag"),
            intensity=envelope.payload.get("emotion_intensity"),
            duration_s=envelope.payload.get("duration_s"),
        )
        success = bool(summary.get("success"))
        return EdgeGatewayResult(
            success=success,
            status=(STATUS_ACCEPTED if success else STATUS_REJECTED),
            reason_code=None if success else str(summary.get("reason") or VOICE_SCHEMA_INVALID),
            stage="S_EDGE_VTS",
            detail=str(summary.get("detail") or "body action completed"),
            event=dict(summary),
        )
    elif action in {"behavior", "avatar_behavior", "perform_behavior"}:
        from edge_client.runtime.avatar_behavior import execute_avatar_behavior

        summary = execute_avatar_behavior(runtime, envelope.payload)
        success = bool(summary.get("success"))
        return EdgeGatewayResult(
            success=success,
            status=(STATUS_ACCEPTED if success else STATUS_REJECTED),
            reason_code=None if success else str(summary.get("reason") or VOICE_SCHEMA_INVALID),
            stage="S_EDGE_AVATAR_BEHAVIOR",
            detail=str(summary.get("detail") or "avatar behavior completed"),
            event=dict(summary),
        )
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
        if (
            not result.success
            and result.reason_code == VTS_EXPRESSION_TIMEOUT
        ):
            try:
                runtime.vts_driver.trigger_reconnect()
            except Exception:
                pass
            event = result.as_dict()
            event["degraded"] = True
            event["degraded_reason"] = "VTS_EXPRESSION_TIMEOUT"
            return EdgeGatewayResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                stage="S_EDGE_VTS",
                detail=f"{result.detail} (degraded accepted, reconnect scheduled)",
                event=event,
            )
    elif action == "reset":
        result = runtime.vts_driver.reset_to_neutral()
    else:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=VOICE_SCHEMA_INVALID,
            stage="S_EDGE_VTS",
            detail="vts action must be one of status/talk_start/talk_end/set_mouth/set_expression/reset/body_action/behavior",
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


def _handle_desktop_pet(runtime: EdgeRuntime, envelope: EdgeRpcEnvelope) -> EdgeGatewayResult:
    import json
    import os
    import urllib.error
    import urllib.request

    payload = dict(envelope.payload or {})
    action = str(payload.get("action") or "status").strip().lower()
    if not action:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=VOICE_SCHEMA_INVALID,
            stage="S_EDGE_DESKTOP_PET_SCHEMA",
            detail="desktop pet action is required",
            event={},
        )

    base_url = str(os.getenv("EDGE_DESKTOP_PET_BASE_URL") or "http://127.0.0.1:18788").rstrip("/")
    try:
        if action == "status":
            request = urllib.request.Request(f"{base_url}/health", method="GET")
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"{base_url}/action",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urllib.request.urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
    except urllib.error.URLError as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
            stage="S_EDGE_DESKTOP_PET",
            detail=f"desktop pet runtime unavailable: {exc}",
            event={"action": action, "base_url": base_url},
        )
    except Exception as exc:
        return EdgeGatewayResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=SERVICE_UNAVAILABLE_GENERIC,
            stage="S_EDGE_DESKTOP_PET",
            detail=f"desktop pet request failed: {exc}",
            event={"action": action, "base_url": base_url},
        )

    success = bool(data.get("success", True))
    return EdgeGatewayResult(
        success=success,
        status=STATUS_ACCEPTED if success else STATUS_REJECTED,
        reason_code=None if success else str(data.get("reason_code") or "DESKTOP_PET_REJECTED"),
        stage="S_EDGE_DESKTOP_PET",
        detail=str(data.get("detail") or "desktop pet action delivered"),
        event={"action": action, "runtime": data},
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
        detail=edge_runtime_text(
            "fastapi_app",
            "vlm_removed_detail",
            default="",
        ),
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
