"""chat_agent_postprocessor.py — ChatAgent 后处理（反思链、净化、PII、COMMIT、异步后处理）。

拆分自 chat_agent.py（P2-12）：
  - apply_response_intelligence 调用封装
  - PII 出口检查
  - INPUT_COMPLETED 同步写入
  - 异步后处理（fire-and-forget）
  - TTS 分句推送
  - G1: trigger_tts_after_commit — 对话完成后兜底触发 TTS
  - G2: broca_to_tts_params — Broca 参数映射到 TTS emotion_tag/intensity
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


# ---------------------------------------------------------------------------
# G2: Broca → TTS 参数映射表
# ---------------------------------------------------------------------------

# tone → emotion_tag 映射（LanguageSystemAgent.broca_generate_plan 产出的 tone）
_BROCA_TONE_TO_EMOTION_TAG: dict[str, str] = {
    "warm":        "LOVING",
    "caring":      "LOVING",
    "gentle":      "CALM",
    "supportive":  "CALM",
    "playful":     "HAPPY",
    "cheerful":    "HAPPY",
    "excited":     "HAPPY",
    "serious":     "NEUTRAL",
    "direct":      "NEUTRAL",
    "balanced":    "NEUTRAL",
    "cautious":    "NEUTRAL",
    "empathetic":  "SAD",
    "sad":         "SAD",
    "concerned":   "SAD",
    "angry":       "ANGRY",
    "firm":        "ANGRY",
    "surprised":   "SURPRISED",
    "fearful":     "FEAR",
}


def broca_to_tts_params(
    *,
    tone: str = "balanced",
    warmth_weight: float = 0.5,
    emotion_tag: str = "NEUTRAL",
) -> dict[str, Any]:
    """G2: 将 Broca 规划参数映射为 TTS emotion_tag / emotion_intensity。

    优先使用 emotion_tag（来自 LimbicAgent），
    tone 作为辅助修正，warmth_weight 映射为 emotion_intensity。
    """
    # 优先使用已有 emotion_tag，若为 NEUTRAL 则尝试从 tone 推断
    resolved_tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    if resolved_tag == "NEUTRAL":
        tone_key = str(tone or "balanced").strip().lower()
        resolved_tag = _BROCA_TONE_TO_EMOTION_TAG.get(tone_key, "NEUTRAL")

    # warmth_weight [0,1] → emotion_intensity [0.2, 0.9]
    # 低暖度（0.0）→ 0.2（平静），高暖度（1.0）→ 0.9（强烈）
    clamped = max(0.0, min(1.0, float(warmth_weight or 0.5)))
    intensity = round(0.2 + clamped * 0.7, 3)

    return {
        "emotion_tag": resolved_tag,
        "emotion_intensity": intensity,
    }


# ---------------------------------------------------------------------------
# G1: 对话完成后兜底触发 TTS
# ---------------------------------------------------------------------------

async def trigger_tts_after_commit(
    *,
    narrative: str,
    request_id: str,
    session_id: str,
    emotion_tag: str = "NEUTRAL",
    emotion_intensity: float | None = None,
    suppress_tts: bool = False,
    source: str = "chat_commit",
) -> None:
    """G1/D2: 对话 COMMIT 后触发 TTS 发声（统一走 MotorCortexAgent.dispatch_speak()）。

    Phase C 改造：原来直接调用 post_speak，现在统一走 MotorCortexAgent.dispatch_speak()，
    确保发声结果写入 state_registry 并发布 MOTOR_SPEAK_DISPATCHED 事件。
    """
    import os as _os
    if suppress_tts:
        return
    text = str(narrative or "").strip()
    if not text:
        return
    tts_enabled = str(_os.environ.get("HOST_TTS_ENABLED") or "true").strip().lower()
    if tts_enabled not in {"1", "true", "yes", "on"}:
        return

    try:
        from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
        from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

        splitter = SentenceSplitter()
        sentences = list(splitter.feed(text))
        remainder = splitter.flush()
        if remainder:
            sentences.append(remainder)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = [text]

        motor = get_motor_cortex_agent()
        for idx, sentence in enumerate(sentences):
            utterance_id = f"utt_commit_{request_id}_{idx:03d}"
            try:
                motor.dispatch_speak(
                    text=sentence,
                    emotion_tag=emotion_tag,
                    emotion_intensity=float(emotion_intensity) if emotion_intensity is not None else 0.5,
                    utterance_id=utterance_id,
                    request_id=request_id,
                    session_id=session_id,
                    source=source,
                    require_gate=False,
                )
            except Exception as exc:
                _logger.debug(
                    "[%s] trigger_tts_after_commit dispatch_speak[%d] failed: %s",
                    request_id, idx, exc,
                )
    except Exception as exc:
        _logger.debug("[%s] trigger_tts_after_commit failed: %s", request_id, exc)
