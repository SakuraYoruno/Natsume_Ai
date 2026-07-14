"""vts_emotion_driver.py — VTS 情感驱动（从 chat_stream_handler.py 迁移）。

提供：
  - _drive_stream_vts_emotion: VTS 情感表情驱动（含速率限制和过渡帧）
  - _wrap_vts_with_latency: 包装版（返回 latency_ms）
  - _infer_emotion_tag_for_tts: 回复文案情感推断
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict

from host_brain.composition.process_services import get_ledger
from host_brain.channels.embodiment import submit_avatar_command
from host_brain.channels.vts_rate_limiter import VTSRateLimiter
from natsume_contracts.edge.v1 import now_ms

_logger = logging.getLogger("Natsume.vts_emotion_driver")

_stream_vts_rate_limiter = VTSRateLimiter(max_per_second=2.0, burst_size=2.0)
_stream_vts_rate_limit_per_second = 2.0

# 回复内容简单情感推断
_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起"),
    ("ANGRY", "生气|气死|烦"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你"),
    ("SURPRISED", "哇|真的吗|竟然"),
]


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案简单推断 emotion_tag。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _resolve_stream_vts_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_STREAM_VTS_TIMEOUT_MS") or "300").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 300
    return max(50, min(value, 3000))


def _resolve_stream_vts_max_per_second() -> float:
    raw = str(os.environ.get("HOST_STREAM_VTS_MAX_PER_SECOND") or "2.0").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 2.0
    return max(0.1, min(value, 20.0))


def _get_stream_vts_rate_limiter() -> VTSRateLimiter:
    global _stream_vts_rate_limit_per_second

    expected = _resolve_stream_vts_max_per_second()
    if abs(expected - _stream_vts_rate_limit_per_second) > 1e-6:
        _stream_vts_rate_limiter.update_rate(max_per_second=expected, burst_size=expected)
        _stream_vts_rate_limit_per_second = expected
    return _stream_vts_rate_limiter


async def _drive_stream_vts_emotion(
    *,
    request_id: str,
    emotion_context: Dict[str, Any] | None,
    ts_ms: int,
) -> Dict[str, Any]:
    if not _parse_bool_env("HOST_STREAM_VTS_EMOTION_ENABLED", default=True):
        return {
            "status": "SKIPPED",
            "reason_code": "HOST_STREAM_VTS_EMOTION_DISABLED",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "VTS 情感驱动已通过配置禁用",
        }

    normalized_emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    if not normalized_emotion_tag:
        return {
            "status": "SKIPPED",
            "reason_code": "EMOTION_TAG_MISSING",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "流式情感上下文中缺少情感标签",
        }

    ledger = get_ledger()
    limiter = _get_stream_vts_rate_limiter()
    limit_result = limiter.check()
    if not limit_result.allowed:
        limiter.log_rate_limit_event(
            ledger=ledger,
            request_id=request_id,
            emotion_tag=normalized_emotion_tag,
            ts_ms=ts_ms,
        )
        return {
            "status": "RATE_LIMITED",
            "reason_code": limit_result.reason_code,
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "RATE_LIMITER",
            "detail": limit_result.detail,
        }

    timeout_ms = _resolve_stream_vts_timeout_ms()
    try:
        receipt = await asyncio.wait_for(
            submit_avatar_command(
                action="set_expression",
                payload={"emotion_tag": normalized_emotion_tag},
                request_id=request_id,
                deadline_ms=now_ms() + timeout_ms,
                idempotency_key=f"{request_id}:stream-vts:{normalized_emotion_tag}:{int(ts_ms)}",
            ),
            timeout=float(timeout_ms) / 1000.0,
        )
        effect = dict(receipt.event or {})

        # 方向 C：计算过渡帧（VTS 渐变动画）
        transition_frames: list[dict[str, Any]] = []
        try:
            from host_brain.channels.vts import build_emotion_transition_frames  # noqa: PLC0415
            _prev_tag = str((emotion_context or {}).get("_prev_emotion_tag") or "").strip().upper()
            if receipt.success:
                frames = build_emotion_transition_frames(
                    from_tag=_prev_tag or None,
                    to_tag=normalized_emotion_tag,
                )
                if len(frames) > 1:
                    transition_frames = [
                        {
                            "frame_index": f.frame_index,
                            "emotion_tag": f.emotion_tag,
                            "expression_id": f.expression_id,
                            "delay_ms": f.delay_ms,
                            "interpolation_t": f.interpolation_t,
                        }
                        for f in frames
                    ]
        except Exception:
            pass

        return {
            "status": "APPLIED" if receipt.success else str(receipt.status),
            "reason_code": receipt.reason_code,
            "emotion_tag": normalized_emotion_tag,
            "expression_id": effect.get("expression_id"),
            "trigger": "HOST_COMMAND",
            "detail": receipt.detail,
            "command_id": receipt.command_id,
            "transition_frames": transition_frames,
        }
    except asyncio.TimeoutError:
        _logger.warning(
            "VTS 情感驱动超时",
            extra={"request_id": request_id, "timeout_ms": timeout_ms},
        )
        return {
            "status": "DEGRADED",
            "reason_code": "VTS_EXPRESSION_TIMEOUT",
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "VTS 情感驱动超时",
        }
    except Exception as exc:
        _logger.warning(
            "VTS 情感驱动失败: %s",
            exc,
            extra={"request_id": request_id},
        )
        return {
            "status": "DEGRADED",
            "reason_code": "VTS_CONNECTION_FAILED",
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "HOST",
            "detail": str(exc),
        }


async def _wrap_vts_with_latency(
    *,
    request_id: str,
    emotion_context: Dict[str, Any] | None,
    ts_ms: int,
    suppress_edge_vts: bool = False,
) -> tuple[Dict[str, Any], int]:
    """包装版，返回 (result_dict, latency_ms)。"""
    _t0 = int(time.time() * 1000)
    if bool(suppress_edge_vts):
        return {
            "status": "SKIPPED",
            "reason_code": "SUPPRESSED_BY_INPUT_POLICY",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "edge VTS suppressed by input policy",
        }, 0
    result = await _drive_stream_vts_emotion(
        request_id=request_id,
        emotion_context=emotion_context,
        ts_ms=ts_ms,
    )
    return result, max(0, int(time.time() * 1000) - _t0)
