"""chat_agent_postprocessor.py — ChatAgent 后处理（反思链、净化、PII、COMMIT、异步后处理）。

拆分自 chat_agent.py（P2-12）：
  - apply_response_intelligence 调用封装
  - PII 出口检查
  - INPUT_COMPLETED 同步写入
  - 异步后处理（fire-and-forget）
  - TTS 分句推送
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

_logger = logging.getLogger("Natsume.chat_agent")


async def _post_speak_sentence(
    *,
    text: str,
    utterance_id: str,
    emotion_tag: str,
    request_id: str,
    session_id: str,
) -> None:
    """Phase XV P7：向 Edge 发送单句 TTS 请求（fire-and-forget）。"""
    try:
        from host_brain.channels.edge_rpc_client import post_speak
        await post_speak(
            text=text,
            utterance_id=utterance_id,
            request_id=request_id,
            session_id=session_id,
            emotion_tag=emotion_tag,
            lang_hint="ja",
        )
    except Exception as exc:
        _logger.debug("[%s] P7 sentence TTS failed (non-critical): %s", request_id, exc)


def apply_response_intelligence_safe(
    *,
    request_id: str,
    user_message: str,
    narrative: str,
    emotion_context: Dict[str, Any],
    bond_summary: Dict[str, Any] | None,
    render_target_lang: str,
    allow_rewrite: bool,
) -> tuple[str, dict[str, Any]]:
    """安全调用 apply_response_intelligence，降级时返回原始 narrative。"""
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence
        return apply_response_intelligence(
            request_id=request_id,
            user_message=user_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            render_lang=render_target_lang,
            allow_rewrite=allow_rewrite,
        )
    except Exception as exc:
        return narrative, {"reflection": {"status": "DEGRADED", "reason_code": str(exc)[:100]}}


def scrub_pii_egress(narrative: str) -> tuple[str, dict[str, Any]]:
    """PII 出口检查，返回（净化后文本, pii_egress_stats）。"""
    pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            if _pii_eg.has_pii:
                pii_egress_stats = {
                    "detections": _pii_eg.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_eg.detections}),
                }
    except Exception:
        pass
    return narrative, pii_egress_stats


def log_input_completed(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    person_id: str,
    reply_channel: str | None,
    render_target_lang: str,
    sub_agent_id: str | None,
) -> None:
    """同步写入 INPUT_COMPLETED 事件到 voice_event_log。"""
    try:
        from host_brain.app.dependencies import get_ledger as _get_pre_ledger
        _get_pre_ledger().log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
            "stage": "chat_agent.pre_commit",
            "detail": (
                f"source={'GATEWAY' if reply_channel else 'CHAT_API'} "
                f"text_len={len(user_message)} reply_len={len(narrative)}"
            ),
            "payload": {
                "source": reply_channel or "CHAT_API",
                "person_id": person_id,
                "input_preview": _build_reply_preview(user_message, limit=160),
                "reply_preview": _build_reply_preview(narrative, limit=160),
                "render_target_lang": render_target_lang,
                "sub_agent_id": sub_agent_id,
            },
            "ts_ms": int(time.time() * 1000),
        })
    except Exception as _ve_exc:
        _logger.debug("[%s] pre-commit INPUT_COMPLETED failed: %s", request_id, _ve_exc)


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def schedule_async_post_process(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: Dict[str, Any],
    bond_summary: Dict[str, Any],
    ts_ms: int,
    render_target_lang: str,
    total_latency_ms: int,
    llm_latency_ms: int,
    first_token_ms: int | None,
    provider_name: str,
    model_id: str,
    buffer_stream: bool,
    delegated_agents: list,
    retrieval_summary: Dict[str, Any],
    pii_egress_stats: dict,
    retrieval_evidence_items: list,
    motive_state: Any,
    response_policy: Dict[str, Any],
    trace_stage_meta: Dict[str, Any],
    llm_input_digest: str | None = None,
) -> None:
    """调度异步后处理任务（fire-and-forget）。"""
    try:
        from host_brain.app.agent_runtime_shared import _async_post_process
        asyncio.create_task(
            _async_post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary=bond_summary,
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
                first_token_ms=first_token_ms,
                provider_name=provider_name,
                model_id=model_id,
                buffer_stream=buffer_stream,
                delegated_agents=delegated_agents,
                retrieval_summary=retrieval_summary,
                gov_decision={"decision": "APPROVE"},
                pii_ingress_stats={},
                pii_egress_stats=pii_egress_stats,
                llm_input_digest=llm_input_digest,
                retrieval_evidence_items=list(retrieval_evidence_items or []),
                motive_state=motive_state,
                response_policy=response_policy,
                trace_stage_meta=trace_stage_meta,
            )
        )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)
