"""surface_reply_postprocessor.py — Surface reply 后处理（净化、PII、COMMIT、TTS）。

拆分自 surface_reply.py（P2-12）：
  - PII 出口检查
  - INPUT_COMPLETED 同步写入
  - durable projection 已提交后的 TTS
  - TTS 分句推送
  - G1: trigger_tts_after_commit — 对话完成后兜底触发 TTS
  - G2: broca_to_tts_params — Broca 参数映射到 TTS emotion_tag/intensity
"""
from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger("Natsume.surface_reply")


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
        from host_brain.channels.embodiment import submit_voice_command
        from host_brain.channels.tts_context_router import TtsContextRouter
        tts_params = TtsContextRouter().route(text=text, emotion_tag=emotion_tag)
        await submit_voice_command(
            action="speak",
            payload={
                "text": text,
                "utterance_id": utterance_id,
                "session_id": session_id,
                "emotion_tag": emotion_tag,
                "speed": float(tts_params.speed_scale),
                "pitch": float(tts_params.pitch_shift),
                "style_note": str(tts_params.style_note or ""),
                "lang_hint": str(tts_params.lang_hint or "zh"),
            },
            request_id=request_id,
            idempotency_key=f"{request_id}:sentence-tts:{utterance_id}",
        )
    except Exception as exc:
        _logger.debug("[%s] P7 sentence TTS failed (non-critical): %s", request_id, exc)


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
        from host_brain.composition.process_services import get_ledger as _get_pre_ledger
        _get_pre_ledger().log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
            "stage": "surface_reply.pre_commit",
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


# ---------------------------------------------------------------------------
from host_brain.tribe.speech import broca_to_tts_params


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
