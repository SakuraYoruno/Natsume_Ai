"""Gaming Edge RPC 路由模块。

从 chat_handler.py 拆分，包含 Gaming 模式下 Edge Fallback LLM 的所有逻辑：
  - Edge RPC 配置解析（base_url、timeout、auth）
  - _post_edge_fallback_rpc：同步 HTTP 调用 Edge RPC
  - _run_gaming_edge_chat：完整的 gaming chat 流程（presence + asr_compile）
  - _gaming_edge_chat_sse_generator：Gaming SCENE_CHAT 专用 SSE 生成器
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, AsyncGenerator, Dict, Mapping

from edge_client.rpc.auth import build_edge_rpc_signature
from host_brain.app.graph_config_builder import (
    HOST_CHAT_ROUTE_EDGE_3B,
    SCENE_CHAT,
    build_unified_route_decision,
)
from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    infer_render_target_lang,
)
from host_brain.app.schemas import ChatRequest
from host_brain.core.errors import (
    EDGE_RPC_AUTH_INVALID,
    EDGE_RPC_FAILED,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    VOICE_FALLBACK_UNAVAILABLE,
)

_logger = logging.getLogger("Natsume.chat_handler")

_EDGE_GAMING_STAGE = "S_EDGE_RPC_GAMING_CHAT"
_EDGE_GAMING_PROVIDER = "edge.fallback_llm"
_EDGE_GAMING_ROUTE_REASON = "HOST_GAMING_EDGE_CHAT"
_EDGE_GAMING_ROUTE_FAILED_REASON = "HOST_GAMING_EDGE_ROUTE_FAILED"
_EDGE_GAMING_EPISODIC_SKIPPED_REASON = "HOST_GAMING_EDGE_CHAT_MINIMAL_PATH"
_EDGE_GAMING_DEFAULT_REPLY = "知道啦，打游戏的时候姐姐就不长篇大论打扰你了哦。专心玩吧，我就在这里静静陪着你呢。"


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_edge_rpc_base_url() -> str:
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    port = max(1, min(port, 65535))
    return f"http://{host}:{port}"


def _resolve_edge_rpc_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_GAMING_EDGE_RPC_TIMEOUT_MS") or "1200").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1200
    return max(200, min(value, 10_000))


def _post_edge_fallback_rpc(
    *,
    request_id: str,
    endpoint_path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base_url = _resolve_edge_rpc_base_url()
    timeout_ms = _resolve_edge_rpc_timeout_ms()
    allow_insecure_auth = _parse_bool(
        os.environ.get("HOST_EDGE_RPC_ALLOW_INSECURE_AUTH"),
        default=_parse_bool(os.environ.get("EDGE_ALLOW_INSECURE_AUTH"), default=True),
    )
    shared_key = str(os.environ.get("HOST_EDGE_RPC_SHARED_KEY") or "").strip()
    ts_ms = int(time.time() * 1000)
    nonce = f"nonce::{request_id}::{uuid.uuid4().hex[:8]}"
    envelope_payload = dict(payload or {})

    if allow_insecure_auth:
        signature = "insecure"
    else:
        if not shared_key:
            return {
                "success": False,
                "status": "REJECTED",
                "reason_code": EDGE_RPC_AUTH_INVALID,
                "detail": "edge rpc shared key missing while insecure auth disabled",
                "event": {"stage": _EDGE_GAMING_STAGE},
                "ts_ms": ts_ms,
            }
        signature = build_edge_rpc_signature(
            request_id=request_id,
            ts_ms=ts_ms,
            nonce=nonce,
            payload=envelope_payload,
            shared_key=shared_key,
        )

    envelope = {
        "request_id": request_id,
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": envelope_payload,
    }
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    endpoint = f"{base_url}{endpoint_path}"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "NatsumeHostEdgeFallback/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_ms) / 1000.0) as resp:
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.URLError as exc:
        return {
            "success": False,
            "status": "REJECTED",
            "reason_code": SERVICE_UNAVAILABLE_EDGE_RPC,
            "detail": f"edge rpc unavailable: {exc}",
            "event": {"stage": _EDGE_GAMING_STAGE},
            "ts_ms": ts_ms,
        }
    except Exception as exc:  # pragma: no cover - defensive network guard
        return {
            "success": False,
            "status": "REJECTED",
            "reason_code": EDGE_RPC_FAILED,
            "detail": f"edge rpc call failed: {exc}",
            "event": {"stage": _EDGE_GAMING_STAGE},
            "ts_ms": ts_ms,
        }

    if not isinstance(parsed, dict):
        parsed = {}
    event = dict(parsed.get("event") or {})
    event.setdefault("stage", _EDGE_GAMING_STAGE)
    return {
        "success": bool(parsed.get("success")),
        "status": str(parsed.get("status") or "REJECTED"),
        "reason_code": (str(parsed.get("reason_code") or "").strip() or None),
        "detail": str(parsed.get("detail") or "edge rpc response"),
        "event": event,
        "ts_ms": int(parsed.get("ts_ms") or ts_ms),
    }


def _build_gaming_edge_narrative(*, draft: Mapping[str, Any]) -> str:
    summary = str(draft.get("summary") or "").strip()
    if not summary:
        summary = str(draft.get("compressed_context") or "").strip()
    if not summary:
        note = str(draft.get("note") or "").strip()
        if note:
            summary = note
    if not summary:
        keywords = [str(x).strip() for x in list(draft.get("keywords") or []) if str(x).strip()]
        if keywords:
            summary = "，".join(keywords[:3])
    if not summary:
        return _EDGE_GAMING_DEFAULT_REPLY
    compact = " ".join(summary.split())
    return _truncate_for_prompt(compact, limit=80) or _EDGE_GAMING_DEFAULT_REPLY


def _build_edge_emotion_context_payload(
    *,
    emotion_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    context = dict(emotion_context or {})
    mood_state = str(context.get("mood_state") or "steady").strip().lower() or "steady"
    emotion_tag = str(context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    try:
        emotion_intensity = float(context.get("emotion_intensity") or 0.5)
    except (TypeError, ValueError):
        emotion_intensity = 0.5
    emotion_intensity = max(0.0, min(1.0, emotion_intensity))
    emotion_range_hint = str(context.get("emotion_range_hint") or "").strip() or None
    return {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "emotion_intensity": emotion_intensity,
        "emotion_range_hint": emotion_range_hint,
    }


def _run_gaming_edge_chat(
    *,
    request_id: str,
    session_id: str,
    message: str,
    emotion_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started_ms = int(time.time() * 1000)
    edge_emotion_context = _build_edge_emotion_context_payload(emotion_context=emotion_context)
    presence = _post_edge_fallback_rpc(
        request_id=request_id,
        endpoint_path="/v1/edge/fallback/presence",
        payload={
            "request_hint": "gaming_chat",
            "seq": 1,
            "emotion_context": dict(edge_emotion_context),
        },
    )
    presence_event = dict(presence.get("event") or {})
    if not bool(presence.get("success")):
        return {
            "success": False,
            "reason_code": str(presence.get("reason_code") or SERVICE_UNAVAILABLE_EDGE_RPC),
            "detail": str(presence.get("detail") or "edge presence call failed"),
            "presence": presence,
            "emotion_context": dict(edge_emotion_context),
        }
    if not bool(presence_event.get("available", False)):
        return {
            "success": False,
            "reason_code": str(presence.get("reason_code") or VOICE_FALLBACK_UNAVAILABLE),
            "detail": str(presence.get("detail") or "edge fallback llm unavailable"),
            "presence": presence,
            "emotion_context": dict(edge_emotion_context),
        }

    compile_result = _post_edge_fallback_rpc(
        request_id=request_id,
        endpoint_path="/v1/edge/fallback/asr_compile",
        payload={
            "session_id": session_id,
            "text": str(message or ""),
            "mode": "gaming_chat",
            "seq": 2,
            "emotion_context": dict(edge_emotion_context),
        },
    )
    if not bool(compile_result.get("success")):
        return {
            "success": False,
            "reason_code": str(compile_result.get("reason_code") or EDGE_RPC_FAILED),
            "detail": str(compile_result.get("detail") or "edge asr_compile failed"),
            "presence": presence,
            "compile": compile_result,
            "emotion_context": dict(edge_emotion_context),
        }

    compile_event = dict(compile_result.get("event") or {})
    draft = dict(compile_event.get("draft") or {})
    narrative = _build_gaming_edge_narrative(draft=draft)
    return {
        "success": True,
        "reason_code": None,
        "detail": "gaming chat routed to edge fallback llm",
        "narrative": narrative,
        "model_id": str(compile_event.get("model_id") or "edge-fallback-3b"),
        "draft_only": bool(compile_event.get("draft_only", True)),
        "latency_ms": max(0, int(time.time() * 1000) - started_ms),
        "presence": presence,
        "compile": compile_result,
        "emotion_context": dict(edge_emotion_context),
    }


async def _gaming_edge_chat_sse_generator(
    *,
    request_body: ChatRequest,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any],
    governor_result: Dict[str, Any],
    emotion_context: Dict[str, Any],
    emotion_snapshot: Dict[str, Any],
    intent_id: Any,
    reason_code: Any,
    sys_mode: str,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    """Gaming 模式 SCENE_CHAT 专用 SSE 生成器。

    在 gaming 模式下，SCENE_CHAT 优先走 edge fallback 3B 模型（低延迟）。
    若 edge 路由失败，则 fallback 到 _complex_scene_sse_generator（云端 LangGraph）。

    ⚠️ 设计说明（问题C）：Gaming Edge 路径为有意的最小路径（Minimal Path）设计。
    以下功能在此路径中被有意跳过，以最大化降低延迟（目标 < 200ms）：
      - VTS 情感驱动：游戏模式下用户注意力在游戏画面，VTS 表情无实际意义
      - Bond 积分更新：游戏模式下的简短回复不计入亲密度积分
      - Episodic 记忆写入：游戏模式下的对话不写入长期记忆
      - Retrieval 七路召回：跳过召回以降低延迟
    若需要完整功能，可将 HOST_GAMING_EDGE_CHAT_ENABLED=false 禁用此路径，
    回退到标准 SCENE_CHAT 流程（_sse_event_generator）。
    """
    from host_brain.app.dependencies import get_ledger
    from host_brain.app.observability_persist import persist_chat_observability_rows

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    _logger.info(
        "chat route switch: gaming mode -> edge fallback llm",
        extra={"request_id": request_id, "session_id": session_id, "sys_mode": sys_mode},
    )
    edge_chat_result = await asyncio.to_thread(
        _run_gaming_edge_chat,
        request_id=request_id,
        session_id=session_id,
        message=request_body.message,
        emotion_context=emotion_context,
    )

    if bool(edge_chat_result.get("success")):
        narrative = str(edge_chat_result.get("narrative") or "").strip() or _EDGE_GAMING_DEFAULT_REPLY
        edge_emotion_context = dict(edge_chat_result.get("emotion_context") or emotion_context or {})
        edge_route_decision = build_unified_route_decision(
            scene_type=SCENE_CHAT,
            scene_type_source="gaming_mode_edge_route",
            route_target=HOST_CHAT_ROUTE_EDGE_3B,
            route_reason_code=_EDGE_GAMING_ROUTE_REASON,
            next_node="edge_fallback_asr_compile",
            router_node="gaming_edge_route",
            provider_name=_EDGE_GAMING_PROVIDER,
            model_id=str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
            sys_mode=sys_mode,
        )
        retrieval_summary = {
            "status": "SKIPPED",
            "reason_code": _EDGE_GAMING_ROUTE_REASON,
            "route_count": 0,
            "routes": [],
            "route_rows": [],
            "route_errors": [],
            "candidate_count": 0,
            "rerank_topk": 0,
            "evidence_refs": [],
            "reason_chain": [],
            "degraded": False,
        }
        llm_summary = {
            "status": "OK",
            "reason_code": None,
            "stage": _EDGE_GAMING_STAGE,
            "provider": _EDGE_GAMING_PROVIDER,
            "model_id": str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
            "model_profile_id": "edge_gaming_3b_minimal",
            "scene_type": SCENE_CHAT,
            "scene_type_source": "gaming_mode_edge_route",
            "render_target_lang": infer_render_target_lang(user_message=request_body.message),
            "latency_ms": edge_chat_result.get("latency_ms"),
            "draft_only": bool(edge_chat_result.get("draft_only", True)),
            "route_target": HOST_CHAT_ROUTE_EDGE_3B,
            "route_reason_code": _EDGE_GAMING_ROUTE_REASON,
            "route_decision": edge_route_decision,
            "emotion_snapshot": dict(emotion_snapshot),
            "node_count": 1,
            "nodes": [{
                "node_id": "edge_fallback_asr_compile",
                "status": "OK",
                "reason_code": None,
                "provider": _EDGE_GAMING_PROVIDER,
                "model_id": str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
                "input": {
                    "user_message": request_body.message,
                    "sys_mode": sys_mode,
                    "route_target": "edge_3b",
                    "minimal_reply": True,
                    "mood_state": edge_emotion_context.get("mood_state"),
                    "emotion_tag": edge_emotion_context.get("emotion_tag"),
                    "emotion_intensity": edge_emotion_context.get("emotion_intensity"),
                },
                "output": {
                    "narrative": narrative,
                    "draft_only": bool(edge_chat_result.get("draft_only", True)),
                },
                "narrative_preview": _truncate_for_prompt(narrative, limit=120),
            }],
            "model_control": [{
                "route": "gaming_edge_3b",
                "sys_mode": sys_mode,
                "minimal_reply": True,
                "emotion_tag": edge_emotion_context.get("emotion_tag"),
            }],
            "prompt_context_validation": {},
            "prompt_context_observability": {},
        }
        episodic_summary = {
            "status": "SKIPPED",
            "reason_code": _EDGE_GAMING_EPISODIC_SKIPPED_REASON,
            "memory_id": None,
            "detail": "gaming edge route keeps minimal path and skips episodic persistence",
        }
        _obs_persist_started_ms = int(time.time() * 1000)
        obs_persist_latency_ms = 0
        try:
            persist_chat_observability_rows(
                ledger=get_ledger(),
                request_id=request_id,
                session_id=session_id,
                llm_summary=llm_summary,
                ts_ms=ts_ms,
                fallback_reason_code=None,
            )
        except Exception as exc:
            _logger.warning(
                "chat observability persistence failed (gaming edge route)",
                extra={"request_id": request_id, "error": str(exc)},
            )
        finally:
            obs_persist_latency_ms = max(0, int(time.time() * 1000) - _obs_persist_started_ms)
        trace_metrics = {
            "retrieval_latency_ms": None,
            "background_tasks_latency_ms": {
                "observability_persist_ms": obs_persist_latency_ms,
                "total_ms": obs_persist_latency_ms,
            },
        }
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "request_id": request_id,
            "audit_refs": {
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
                "retrieval": retrieval_summary,
                "llm": llm_summary,
                "scene_type": SCENE_CHAT,
                "scene_type_source": "gaming_mode_edge_route",
                "route_decision": edge_route_decision,
                "emotion_context": dict(emotion_context),
                "emotion_snapshot": dict(emotion_snapshot),
                "episodic_memory": dict(episodic_summary),
                "trace_metrics": trace_metrics,
            },
        })
        return

    # edge 路由失败，fallback 到云端 LangGraph（复杂场景生成器）
    edge_failure_reason = str(edge_chat_result.get("reason_code") or SERVICE_UNAVAILABLE_EDGE_RPC)
    edge_failure_detail = str(edge_chat_result.get("detail") or "edge route failed")
    _logger.warning(
        "gaming edge route failed; fallback to host graph",
        extra={
            "request_id": request_id,
            "session_id": session_id,
            "reason_code": edge_failure_reason,
            "detail": edge_failure_detail,
        },
    )
    from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415
    async for event in _complex_scene_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        scene_type=SCENE_CHAT,
        scene_type_source="gaming_mode_edge_fallback",
        governor_result=governor_result,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        intent_id=intent_id,
        reason_code=edge_failure_reason,
        metrics=metrics,
    ):
        yield event
