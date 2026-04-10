"""Unified Host input route for ASR / Gateway events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])

_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat",
    "active chat",
    "current_chat",
    "current chat",
    "current_session",
    "default",
    "unknown",
    "none",
    "null",
    "-",
    "_",
    "na",
    "n/a",
    "当前聊天",
    "当前会话",
}


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, dict) else {}


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or GATEWAY")
    person_id: str | None = Field(None, description="Optional canonical person id")

    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

    platform: str | None = Field(None, description="[GATEWAY] Platform: wechat/qq/feishu/wecom")
    msg_id: str | None = Field(None, description="[GATEWAY] Message ID")
    from_id: str | None = Field(None, description="[GATEWAY] Sender ID")
    to_id: str | None = Field(None, description="[GATEWAY] Receiver ID")
    conversation_id: str | None = Field(None, description="[GATEWAY] Conversation ID")
    content: str | None = Field(None, description="[GATEWAY] Message content")
    msg_type: str | None = Field(None, description="[GATEWAY] Message type")
    is_group: bool | None = Field(None, description="[GATEWAY] Is group message")
    sender_name: str | None = Field(None, description="[GATEWAY] Sender display name")
    media_url: str | None = Field(None, description="[GATEWAY] Media URL")
    reply_to_msg_id: str | None = Field(None, description="[GATEWAY] Reply-to message ID")
    raw_extra: dict[str, Any] | None = Field(None, description="[GATEWAY] Extra fields")

    ts_ms: int | None = Field(None, description="Event timestamp in ms")


class InputResponse(BaseModel):
    """Ingress response."""

    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


def _evaluate_immune_gate_for_input(
    *,
    text: str,
    source: str,
    session_id: str,
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

    return get_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:
    _ = request
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    source = str(body.source or "").upper().strip()

    _logger.info("input_routes: received edge input source=%s request_id=%s", source, request_id)

    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR input missing required fields: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()
        if body.is_final is not True:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail="ASR non-final segment ignored",
                ts_ms=ts_ms,
            )
    elif source == "GATEWAY":
        if not body.content or not body.conversation_id or not body.platform:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="GATEWAY input missing required fields: content, conversation_id, platform",
                ts_ms=ts_ms,
            )
        if _is_invalid_gateway_conversation_id(body.conversation_id):
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail=f"invalid gateway conversation_id: {body.conversation_id}",
                ts_ms=ts_ms,
            )
        text = str(body.content).strip()
        session_id = str(body.conversation_id).strip()
    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"unsupported source: {source}, only ASR/GATEWAY are accepted",
            ts_ms=ts_ms,
        )

    payload = body.model_dump(exclude_none=True)
    immune_result: dict[str, Any] = {}
    try:
        immune_result = _evaluate_immune_gate_for_input(
            text=text,
            source=source,
            session_id=session_id,
            person_id=str(body.person_id or "").strip() or None,
        )
    except Exception as exc:
        _logger.warning("input_routes: immune gate evaluate degraded request_id=%s: %s", request_id, exc)
        immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    payload["immune_gate"] = {
        "decision": immune_decision,
        "risk_level": immune_result.get("risk_level"),
        "score": immune_result.get("score"),
        "reason_codes": list(immune_result.get("reason_codes") or []),
    }
    if immune_decision == "BLOCK":
        _log_input_event(
            ledger=_get_ledger_safe(),
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="REJECTED",
            status="REJECTED",
            reason_code="IMMUNE_BLOCKED_HIGH_RISK",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
    if immune_decision == "REPORT":
        _logger.warning(
            "input_routes: immune gate report request_id=%s source=%s reasons=%s",
            request_id,
            source,
            immune_result.get("reason_codes"),
        )

    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415

        payload["person_id"] = resolve_person_id(
            str(body.person_id or "").strip() or None,
            session_id=session_id,
            platform=str(body.platform or "").strip() or None,
            from_id=str(body.from_id or "").strip() or None,
            conversation_id=str(body.conversation_id or "").strip() or None,
        )
        notify_user_interaction()
    except Exception:
        pass

    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status="ACCEPTED",
            detail="input accepted for processing",
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.error("input_routes: dispatch failed source=%s request_id=%s: %s", source, request_id, exc)
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"dispatch failed: {exc}",
            ts_ms=ts_ms,
        )


def _get_ledger_safe():
    try:
        from host_brain.app.dependencies import get_ledger

        return get_ledger()
    except Exception:
        return None


def _log_input_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    source: str,
    input_payload: dict[str, Any] | None = None,
    phase: str,
    status: str,
    detail: str = "",
    reason_code: str | None = None,
    latency_ms: int | None = None,
    ts_ms: int,
) -> None:
    if ledger is None:
        return
    try:
        payload: dict[str, Any] = {
            "source": source,
            "phase": phase,
            "latency_ms": latency_ms,
        }
        input_preview = _build_input_preview(input_payload, source=source)
        if input_preview:
            payload["input_preview"] = input_preview
        person_id = str((input_payload or {}).get("person_id") or "").strip()
        if person_id:
            payload["person_id"] = person_id
        if source == "GATEWAY":
            gateway_fields = (
                "platform",
                "conversation_id",
                "msg_id",
                "msg_type",
                "from_id",
                "to_id",
                "sender_name",
                "media_url",
                "reply_to_msg_id",
                "is_group",
                "decision",
                "gateway_status",
                "gateway_latency_ms",
                "reply_text_len",
                "reply_preview",
            )
            for field in gateway_fields:
                value = (input_payload or {}).get(field)
                if value is not None:
                    payload[field] = value
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": f"INPUT_{phase}",
                "direction": "EDGE_TO_HOST",
                "status": status,
                "reason_code": reason_code,
                "stage": f"input_dispatch.{phase.lower()}",
                "detail": detail[:400] if detail else None,
                "payload": payload,
                "ts_ms": ts_ms,
            }
        )
    except Exception as log_exc:
        _logger.debug("input_routes: ledger log failed (non-blocking): %s", log_exc)


def _log_dispatch_task_exception(task: asyncio.Task) -> None:
    """Log fire-and-forget task exceptions."""

    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.error("input_routes: dispatch task failed silently: %s", exc, exc_info=exc)


class _NoopMetrics:
    """Minimal metrics shim for top_agent direct invocation."""

    def __getattr__(self, _name: str):
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _noop


def _build_input_preview(payload: dict[str, Any] | None, *, source: str, limit: int = 120) -> str | None:
    payload_dict = dict(payload or {})
    preview_source = None
    if str(source or "").upper() == "GATEWAY":
        preview_source = payload_dict.get("content")
    elif str(source or "").upper() == "ASR":
        preview_source = payload_dict.get("text")
    preview = _build_reply_preview(str(preview_source or ""), limit=limit).strip()
    return preview or None


def _parse_sse_data_event(raw_event: str) -> dict[str, Any] | None:
    line = str(raw_event or "").strip()
    if not line.startswith("data:"):
        return None
    payload = line.removeprefix("data:").strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _build_gateway_reply_text(
    *,
    decision: str,
    narrative: str,
    reason_code: str | None,
) -> str:
    templates = _load_template_mapping("input_routes.fallback_texts")
    reply_text = str(narrative or "").strip()
    if reply_text:
        return reply_text
    if str(decision or "").upper() != "APPROVE":
        return str(templates.get("reject_default") or "")
    _ = reason_code
    return str(templates.get("approve_empty") or "")


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)]}..."


async def _run_top_agent_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
    from host_brain.app.schemas import ChatRequest  # noqa: PLC0415
    from host_brain.app.top_agent import top_agent_sse_generator  # noqa: PLC0415

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    person_id = resolve_person_id(
        str(payload.get("person_id") or "").strip() or None,
        session_id=session_id,
        platform=reply_channel,
        from_id=str(payload.get("from_id") or "").strip() or None,
        conversation_id=reply_target,
    )
    request_body = ChatRequest(
        message=text,
        person_id=person_id,
        session_id=session_id,
        input_source=source,
        reply_channel=reply_channel,
        reply_target=reply_target,
        suppress_edge_tts=(True if source == "GATEWAY" else None),
        suppress_edge_vts=(True if source == "GATEWAY" else None),
        # Phase XV P4：Gateway 通道文字永远中文，注入 render_target_lang
        # ASR/Web 通道保持 None，由 infer_render_target_lang() 推断（向后兼容）
        render_target_lang=("zh" if source == "GATEWAY" else None),
    )
    emotion_context = resolve_chat_emotion_context(request_body=request_body)
    emotion_snapshot = build_chat_emotion_snapshot(
        emotion_context=emotion_context,
        source=f"input_routes.{str(source or '').lower()}",
        ts_ms=ts_ms,
    )
    intent_draft: dict[str, Any] = {
        "id": f"intent::{request_id}",
        "action": "narrative_only",
        "source": "input_routes",
        "input_source": source,
    }
    governor_result: dict[str, Any] = {"decision": "APPROVE", "source": "input_routes"}

    commit_event: dict[str, Any] | None = None
    async for event in top_agent_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=request_id,
        metrics=_NoopMetrics(),
    ):
        parsed = _parse_sse_data_event(event)
        if parsed and str(parsed.get("phase") or "").upper() == "COMMIT":
            commit_event = parsed

    if commit_event is None:
        raise RuntimeError("top_agent_commit_missing")

    return {
        "decision": str(commit_event.get("decision") or "").strip().upper() or "REJECT",
        "reason_code": str(commit_event.get("reason_code") or "").strip() or None,
        "narrative": str(commit_event.get("narrative") or "").strip(),
        "commit_event": commit_event,
    }


async def _deliver_gateway_reply(
    *,
    request_id: str,
    payload: dict[str, Any],
    decision: str,
    reason_code: str | None,
    narrative: str,
    # Phase XV P4：ReplyFormDecider 所需的上下文
    emotion_tag: str = "NEUTRAL",
    bond_stage: str = "STRANGER",
    user_msg_type: str = "text",
    # Phase XV P6：双轨语言，Gateway 文字永远中文（narrative 已是中文）
    # tts_text_ja 为日语 TTS 文本（双语输出时使用，当前为占位）
    tts_text_ja: str | None = None,
) -> dict[str, Any]:
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415
    from host_brain.app.reply_form_decider import (  # noqa: PLC0415
        get_reply_form_decider, MsgType,
    )

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        raise RuntimeError("gateway_reply_missing_platform_or_conversation")

    reply_text = _build_gateway_reply_text(
        decision=decision,
        narrative=narrative,
        reason_code=reason_code,
    )
    preview = _build_reply_preview(reply_text, limit=120)

    # ── Phase XV P4：ReplyFormDecider ──────────────────────────────────
    # 将用户消息类型字符串转换为 MsgType
    try:
        user_mtype = MsgType(str(user_msg_type or "text").strip().lower())
    except ValueError:
        user_mtype = MsgType.TEXT

    decider = get_reply_form_decider()
    form_decision = decider.decide(
        user_msg_type=user_mtype,
        narrative_zh=reply_text,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
    )

    # ── 主回复（文字/语音）─────────────────────────────────────────────
    primary_content = reply_text
    primary_msg_type = form_decision.primary.value

    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=primary_content,
        msg_type=primary_msg_type,
        request_id=request_id,
        wait_result=True,
        timeout_ms=12000,
    )
    if not bool(getattr(result, "success", False)):
        status = str(getattr(result, "status", "FAILED"))
        rc = str(getattr(result, "reason_code", "") or "")
        detail = str(getattr(result, "detail", "") or "")
        raise RuntimeError(
            f"gateway_reply_failed status={status} reason_code={rc or 'UNKNOWN'} detail={detail[:180]}"
        )

    # ── 双发模式：先文字字幕，再日语语音（不阻塞主回复）────────────
    voice_sent = False
    if form_decision.is_dual:
        # P3/P6 完整实现：用 tts_text_ja 做 TTS 合成后发语音
        # P4 当前：发送一条文字作为语音消息的占位（保留接口，等 P6 双语输出完成后启用真正的 TTS）
        _voice_text = tts_text_ja or reply_text  # P6 完成后改为只用 tts_text_ja
        if _voice_text and form_decision.secondary == MsgType.VOICE:
            try:
                # 异步发送，不等待结果，不阻塞主回复
                import asyncio as _asyncio  # noqa: PLC0415
                _asyncio.create_task(_send_gateway_voice_reply(
                    platform=platform,
                    conversation_id=conversation_id,
                    tts_text=_voice_text,
                    request_id=request_id,
                    emotion_tag=emotion_tag,
                ))
                voice_sent = True
            except Exception:
                pass  # 语音发送失败不影响主回复

    return {
        "status": str(getattr(result, "status", "OK")),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "reply_text_len": len(reply_text),
        "reply_preview": preview,
        "reply_form": form_decision.primary.value,
        "reply_form_reason": form_decision.reason,
        "voice_sent": voice_sent,
    }


async def _send_gateway_voice_reply(
    *,
    platform: str,
    conversation_id: str,
    tts_text: str,
    request_id: str,
    emotion_tag: str = "NEUTRAL",
) -> None:
    """向 Gateway 发送语音消息（异步，fire-and-forget）。

    Phase XV P4 当前实现：
      - 调用 Edge TTS 合成（post_speak），等待合成完成
      - 当 Edge 端支持 TTS→音频文件 后，改为发送语音文件
      - 当前 post_gateway_reply msg_type="voice" 需要 content 为音频 base64 或文件路径
      - 临时方案：发送 msg_type="text" 的语音字幕（标记 🎵 前缀区分）

    Phase XV P6/P7 完整实现：
      - Edge TTS 合成 → 返回音频文件路径/base64
      - post_gateway_reply(msg_type="voice", content=audio_data)
    """
    import logging as _logging  # noqa: PLC0415
    _vlogger = _logging.getLogger("Natsume.gateway_voice")
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        # 触发 TTS 合成（本地扬声器）——Gateway 用户在外面听不到，
        # 但这样可以验证 TTS 合成链路正常工作
        # P6/P7 完成后：获取合成音频 → 发送 voice 消息
        _tts_text_normalized = str(tts_text or "").strip()
        if not _tts_text_normalized:
            return

        await post_speak(
            text=_tts_text_normalized,
            request_id=request_id,
            lang_hint="ja",  # Phase XV P6: 语音通道永远日语
            emotion_tag=emotion_tag,
        )
        _vlogger.debug(
            "gateway_voice TTS triggered (P4 placeholder): platform=%s len=%d",
            platform, len(_tts_text_normalized),
        )
    except Exception as exc:
        _vlogger.debug("gateway_voice send failed (non-critical): %s", exc)


async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    ledger = _get_ledger_safe()
    run_in_thread = _parse_bool_env("HOST_INPUT_DISPATCH_IN_THREAD", default=True)
    dispatch_mode = "fire_and_forget.threaded" if run_in_thread else "fire_and_forget.inline"

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )

    if run_in_thread:
        asyncio.create_task(
            _dispatch_to_graph_worker(
                request_id=request_id,
                session_id=session_id,
                source=source,
                text=text,
                payload=payload,
                ts_ms=ts_ms,
                ledger=ledger,
                dispatch_mode=dispatch_mode,
                run_in_thread=run_in_thread,
            )
        ).add_done_callback(_log_dispatch_task_exception)
        return

    await _dispatch_to_graph_worker(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
        ledger=ledger,
        dispatch_mode=dispatch_mode,
        run_in_thread=run_in_thread,
    )


async def _dispatch_to_graph_worker(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    dispatch_mode: str,
    run_in_thread: bool,
) -> None:
    """Run top_agent pipeline and optionally deliver Gateway reply."""

    dispatch_ts = int(time.time() * 1000)
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="DISPATCHED",
        status="DISPATCHED",
        detail=f"dispatch_mode={dispatch_mode} source={source}",
        ts_ms=dispatch_ts,
    )
    try:
        pipeline_result = await _run_top_agent_pipeline(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
        reason_code = str(pipeline_result.get("reason_code") or "").strip() or None
        narrative = str(pipeline_result.get("narrative") or "").strip()

        gateway_delivery: dict[str, Any] | None = None
        if source == "GATEWAY":
            # Phase XV P4：从 pipeline_result 提取情感和 Bond 上下文
            commit_event = pipeline_result.get("commit_event") or {}
            audit_refs = commit_event.get("audit_refs") or {}
            _emotion_tag = str(audit_refs.get("emotion_tag") or "NEUTRAL").strip().upper()
            # bond_stage 从 payload 的 bond_summary 取（若存在），否则用 STRANGER
            _bond_stage = str((payload.get("bond_summary") or {}).get("stage") or "STRANGER").strip().upper()
            _user_msg_type = str(payload.get("msg_type") or "text").strip().lower()

            gateway_delivery = await _deliver_gateway_reply(
                request_id=request_id,
                payload=payload,
                decision=decision,
                reason_code=reason_code,
                narrative=narrative,
                emotion_tag=_emotion_tag,
                bond_stage=_bond_stage,
                user_msg_type=_user_msg_type,
            )

        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        completion_status = "OK" if decision == "APPROVE" else "DEGRADED"
        detail_parts = [
            f"top_agent decision={decision}",
            f"mode={dispatch_mode}",
            f"thread_flag={str(bool(run_in_thread)).lower()}",
            f"latency_ms={latency_ms}",
        ]
        if reason_code:
            detail_parts.append(f"reason_code={reason_code}")
        if gateway_delivery is not None:
            detail_parts.append(f"gateway_status={gateway_delivery.get('status')}")
            detail_parts.append(f"gateway_latency_ms={gateway_delivery.get('latency_ms')}")
            detail_parts.append(f"reply_text_len={gateway_delivery.get('reply_text_len')}")
            detail_parts.append(f"reply_preview={gateway_delivery.get('reply_preview')}")

        completed_payload = dict(payload or {})
        completed_payload["decision"] = decision
        if reason_code:
            completed_payload["reason_code"] = reason_code
        if gateway_delivery is not None:
            completed_payload["gateway_status"] = gateway_delivery.get("status")
            completed_payload["gateway_latency_ms"] = gateway_delivery.get("latency_ms")
            completed_payload["reply_text_len"] = gateway_delivery.get("reply_text_len")
            completed_payload["reply_preview"] = gateway_delivery.get("reply_preview")

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=completed_payload,
            phase="COMPLETED",
            status=completion_status,
            detail=" ".join(detail_parts),
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: dispatch completed request_id=%s source=%s decision=%s latency_ms=%d mode=%s",
            request_id,
            source,
            decision,
            latency_ms,
            dispatch_mode,
        )
    except Exception as exc:
        fail_ts = int(time.time() * 1000)
        latency_ms = fail_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="FAILED",
            status="FAILED",
            reason_code="INPUT_GRAPH_ERROR",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=latency_ms,
            ts_ms=fail_ts,
        )
        _logger.error("input_routes: dispatch failed request_id=%s: %s", request_id, exc)
