"""聊天 HTTP 入口的前置编排。

本层只固定请求身份、格式准入、免疫门禁与可观测预检，再委托输入运行时生成 SSE；
它不直接执行业务副作用，从而使拒绝与降级都能以单一提交事件清晰地返回客户端。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.schemas import ChatRequest
from host_brain.app.sse_response import sse_single_commit
from host_brain.app.vision.clawdbot_protocol import (
    VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.core.ids import new_request_id


_CHAT_HELP_MARKERS = (
    "帮",
    "请",
    "修",
    "查",
    "做",
    "实现",
    "分析",
    "plan",
    "fix",
    "implement",
    "help",
    "?",
    "？",
)


def build_chat_bio_ops_preflight_trace(request_body: ChatRequest) -> dict[str, Any]:
    """从已规范化的聊天字段构造仅建议用途的 Bio-Ops 预检轨迹。

    预检失败会降级为有原因码的轨迹而非阻断普通聊天，避免附属观测能力不可用时扩大为
    用户请求故障；其输出不授予设备或生命系统的任何执行权限。
    """
    message = str(getattr(request_body, "message", "") or "")
    emotion_context = dict(getattr(request_body, "emotion_context", None) or {})
    emotion_tag = str(getattr(request_body, "emotion_tag", "") or "").strip()
    if emotion_tag and "emotion_tag" not in emotion_context:
        emotion_context["emotion_tag"] = emotion_tag
    if getattr(request_body, "emotion_intensity", None) is not None and "arousal" not in emotion_context:
        emotion_context["arousal"] = getattr(request_body, "emotion_intensity", None)
    explicit_help_request = any(marker in message.lower() for marker in _CHAT_HELP_MARKERS)
    try:
        from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace  # noqa: PLC0415

        return build_bio_ops_runtime_trace(
            message=message,
            route_type="chat",
            target_id="",
            intent_draft=dict(getattr(request_body, "intent_draft", None) or {}),
            emotion_context=emotion_context,
            runtime_signals={
                "current_hour": int(time.localtime().tm_hour),
                "tts_status": "suppressed" if bool(getattr(request_body, "suppress_edge_tts", False)) else "ok",
                "user_stress": str(emotion_context.get("stress_level") or ""),
                "explicit_help_request": explicit_help_request,
            },
        )
    except Exception as exc:
        return {
            "source": "bio_ops.runtime_trace",
            "proposal_only": True,
            "status": "DEGRADED",
            "reason_code": "BIO_OPS_CHAT_PREFLIGHT_TRACE_FAILED",
            "detail": str(exc)[:200],
        }


async def handle_v1_chat_route(
    *,
    request_body: ChatRequest,
    request: Request,
    metrics: Any,
    logger: logging.Logger,
    evaluate_immune_gate_fn: Callable[[ChatRequest], dict[str, Any]],
    schedule_immune_gate_fn: Callable[..., None],
    handle_chat_fn: Callable[..., Any],
    notify_user_interaction_fn: Callable[[], None] | None = None,
) -> StreamingResponse:
    """执行 `/v1/chat` 的边界检查并将通过的文本请求交给主聊天链。

    非文本格式必须走受治理的视觉代理，不能在聊天入口旁路；免疫代理缺失时闭合阻断，
    而普通评估异常仅以可审计降级继续，保留既有可用性策略。
    """
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    response_format = str(
        getattr(request_body, "response_format", "text")
    ).strip().lower()
    if response_format != "text":
        # 动作/视觉输出需要独立准入，避免 SSE 聊天流承载未经授权的效果计划。
        request.state.request_id = request_id
        return sse_single_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
                "response_format": (
                    "actions" if response_format == "actions" else "unsupported"
                ),
                "action_plan_available": False,
                "side_effects_executed": False,
                "request_id": request_id,
            }
        )

    try:
        if notify_user_interaction_fn is not None:
            notify_user_interaction_fn()
    except Exception:
        pass
    request.state.request_id = request_id
    bio_ops_trace = build_chat_bio_ops_preflight_trace(request_body)
    request.state.bio_ops_runtime_trace = bio_ops_trace
    try:
        immune_result = evaluate_immune_gate_fn(request_body)
    except Exception as exc:
        if str(exc) == "HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED":
            # 缺少安全权威不是可安全降级的情形，必须拒绝而非假定低风险。
            logger.warning("immune gate authority unavailable")
            immune_result = {
                "decision": "BLOCK",
                "reason_codes": ["IMMUNE_GATE_UNAVAILABLE"],
            }
        else:
            logger.warning("immune gate evaluate degraded: %s", exc)
            immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    request.state.immune_inspection = immune_result
    schedule_immune_gate_fn(
        request_id=request_id,
        request_body=request_body,
        immune_result=immune_result,
    )
    # 先记录门禁结果再分支返回，确保被阻断请求也拥有可关联的审计事件。
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    if immune_decision == "BLOCK":
        immune_unavailable = "IMMUNE_GATE_UNAVAILABLE" in set(
            str(item) for item in immune_result.get("reason_codes") or ()
        )
        logger.warning(
            "immune gate blocked /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
        return sse_single_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": (
                    "IMMUNE_GATE_UNAVAILABLE"
                    if immune_unavailable
                    else "IMMUNE_BLOCKED_HIGH_RISK"
                ),
                "narrative": "",
                "request_id": request_id,
                "immune": {
                    "risk_level": immune_result.get("risk_level"),
                    "reason_codes": immune_result.get("reason_codes"),
                    "score": immune_result.get("score"),
                },
                "bio_ops_runtime_trace": bio_ops_trace,
            }
        )
    if immune_decision == "REPORT":
        logger.info(
            "immune gate report /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
    return await handle_chat_fn(request_body=request_body, request=request, metrics=metrics)


__all__ = ["build_chat_bio_ops_preflight_trace", "handle_v1_chat_route"]
