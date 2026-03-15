"""Host → Edge HTTP 推送客户端。

负责从 Host Brain 主动调用 Edge RPC 端点（VTS / TTS / 截图 / 自动化 / Fallback LLM）。
每次调用都构建带 HMAC 签名的 EdgeRpcEnvelope，通过 httpx 异步发送。

环境变量：
  EDGE_RPC_HOST          Edge 监听地址（默认 127.0.0.1）
  EDGE_RPC_PORT          Edge 监听端口（默认 17800）
  EDGE_HMAC_SHARED_KEY   HMAC 共享密钥（与 Edge 端一致）
  EDGE_ALLOW_INSECURE_AUTH  是否允许跳过签名（dev/test 环境，默认 true）
  HOST_EDGE_RPC_TIMEOUT_MS  单次请求超时（默认 3000ms）
  HOST_EDGE_RPC_RETRIES     失败重试次数（默认 1）
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.core.ids import new_request_id, now_ms

_logger = logging.getLogger("Natsume.edge_rpc_client")
_EDGE_CLOCK_OFFSET_TTL_MS = 30_000
_EDGE_CLOCK_OFFSET_CACHE: dict[str, Any] = {
    "base_url": "",
    "offset_ms": 0,
    "expires_at_ms": 0,
}

# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EdgeRpcCallResult:
    success: bool
    status: str
    reason_code: str | None
    detail: str
    event: dict[str, Any]
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "event": dict(self.event),
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# HMAC 签名（与 edge_client/rpc/auth.py 保持一致）
# ---------------------------------------------------------------------------


def _build_signature(
    *,
    request_id: str,
    ts_ms: int,
    nonce: str,
    payload: dict[str, Any],
    shared_key: str,
) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = f"{request_id}\n{int(ts_ms)}\n{nonce}\n{canonical}"
    digest = _hmac.new(
        str(shared_key).encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def _build_envelope(
    *,
    request_id: str,
    payload: dict[str, Any],
    shared_key: str | None,
    allow_insecure: bool,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    ts = int(ts_ms if ts_ms is not None else now_ms())
    nonce = str(uuid.uuid4())
    sig = (
        _build_signature(
            request_id=request_id,
            ts_ms=ts,
            nonce=nonce,
            payload=payload,
            shared_key=str(shared_key),
        )
        if shared_key and not allow_insecure
        else "insecure"
    )
    return {
        "request_id": request_id,
        "ts_ms": ts,
        "nonce": nonce,
        "signature": sig,
        "payload": payload,
    }


def _extract_edge_ts_ms(body: Mapping[str, Any]) -> int | None:
    candidates = [
        body.get("ts_ms"),
        body.get("timestamp"),
    ]
    event = body.get("event")
    if isinstance(event, Mapping):
        candidates.extend(
            [
                event.get("ts_ms"),
                event.get("timestamp"),
            ]
        )
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _invalidate_edge_clock_offset_cache(*, base_url: str | None = None) -> None:
    cached_base_url = str(_EDGE_CLOCK_OFFSET_CACHE.get("base_url") or "")
    if base_url is not None and cached_base_url and cached_base_url != str(base_url):
        return
    _EDGE_CLOCK_OFFSET_CACHE.update(
        {
            "base_url": "",
            "offset_ms": 0,
            "expires_at_ms": 0,
        }
    )


async def _resolve_edge_clock_offset_ms(
    *,
    base_url: str,
    timeout_ms: int,
) -> int:
    import time

    now_value = int(time.time() * 1000)
    if (
        _EDGE_CLOCK_OFFSET_CACHE.get("base_url") == base_url
        and int(_EDGE_CLOCK_OFFSET_CACHE.get("expires_at_ms") or 0) > now_value
    ):
        return int(_EDGE_CLOCK_OFFSET_CACHE.get("offset_ms") or 0)

    try:
        import httpx
    except ImportError:
        return 0

    probe_timeout_s = max(0.2, min(float(timeout_ms) / 1000.0, 3.0))
    try:
        async with httpx.AsyncClient(timeout=probe_timeout_s) as client:
            local_before_ms = int(time.time() * 1000)
            response = await client.get(f"{base_url}/v1/edge/health")
            local_after_ms = int(time.time() * 1000)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, Mapping):
            return 0
        edge_ts_ms = _extract_edge_ts_ms(body)
        if edge_ts_ms is None:
            return 0
        midpoint_ms = (local_before_ms + local_after_ms) // 2
        offset_ms = int(edge_ts_ms) - int(midpoint_ms)
        _EDGE_CLOCK_OFFSET_CACHE.update(
            {
                "base_url": base_url,
                "offset_ms": offset_ms,
                "expires_at_ms": local_after_ms + _EDGE_CLOCK_OFFSET_TTL_MS,
            }
        )
        return offset_ms
    except Exception as exc:
        _logger.debug("edge clock calibration skipped", extra={"base_url": base_url, "error": str(exc)})
        if _EDGE_CLOCK_OFFSET_CACHE.get("base_url") == base_url:
            return int(_EDGE_CLOCK_OFFSET_CACHE.get("offset_ms") or 0)
        return 0


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------


def _resolve_edge_base_url() -> str:
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


def _resolve_hmac_key() -> str | None:
    # Host only reads HOST_EDGE_RPC_SHARED_KEY for signing.
    key = str(os.environ.get("HOST_EDGE_RPC_SHARED_KEY") or "").strip()
    return key if key else None


def _resolve_allow_insecure() -> bool:
    raw = str(os.environ.get("EDGE_ALLOW_INSECURE_AUTH") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_EDGE_RPC_TIMEOUT_MS") or "3000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 3000
    return max(200, min(value, 30000))


def _resolve_retries() -> int:
    raw = str(os.environ.get("HOST_EDGE_RPC_RETRIES") or "1").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1
    return max(0, min(value, 3))


# ---------------------------------------------------------------------------
# 核心 HTTP 调用
# ---------------------------------------------------------------------------


async def _post_to_edge(
    *,
    path: str,
    payload: dict[str, Any],
    request_id: str | None = None,
) -> EdgeRpcCallResult:
    """构建 HMAC 信封并异步 POST 到 Edge 端点。"""
    import time

    try:
        import httpx
    except ImportError:
        return EdgeRpcCallResult(
            success=False,
            status="DEGRADED",
            reason_code="HTTPX_NOT_INSTALLED",
            detail="httpx 未安装，无法调用 Edge RPC",
            event={},
            latency_ms=0,
        )

    req_id = request_id or new_request_id()
    base_url = _resolve_edge_base_url()
    shared_key = _resolve_hmac_key()
    allow_insecure = _resolve_allow_insecure()
    timeout_ms = _resolve_timeout_ms()
    retries = _resolve_retries()

    url = f"{base_url}{path}"
    t0 = int(time.time() * 1000)
    last_exc: Exception | None = None
    clock_offset_ms = await _resolve_edge_clock_offset_ms(
        base_url=base_url,
        timeout_ms=timeout_ms,
    )
    forced_recalibrated = False

    for attempt in range(retries + 1):
        envelope = _build_envelope(
            request_id=req_id,
            payload=payload,
            shared_key=shared_key,
            allow_insecure=allow_insecure,
            ts_ms=int(now_ms()) + int(clock_offset_ms),
        )
        try:
            async with httpx.AsyncClient(timeout=float(timeout_ms) / 1000.0) as client:
                resp = await client.post(url, json=envelope)
            latency_ms = max(0, int(time.time() * 1000) - t0)

            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = EdgeRpcCallResult(
                    success=bool(body.get("success", True)),
                    status=str(body.get("status") or "ACCEPTED"),
                    reason_code=body.get("reason_code"),
                    detail=str(body.get("detail") or "ok"),
                    event=dict(body.get("event") or {}),
                    latency_ms=latency_ms,
                )
                if (
                    result.reason_code == "EDGE_RPC_AUTH_EXPIRED"
                    and not forced_recalibrated
                ):
                    forced_recalibrated = True
                    _invalidate_edge_clock_offset_cache(base_url=base_url)
                    clock_offset_ms = await _resolve_edge_clock_offset_ms(
                        base_url=base_url,
                        timeout_ms=timeout_ms,
                    )
                    _logger.info(
                        "Edge RPC %s auth expired; recalibrated edge clock offset to %dms and retrying once",
                        path,
                        clock_offset_ms,
                    )
                    continue
                return result
            else:
                latency_ms = max(0, int(time.time() * 1000) - t0)
                _logger.warning(
                    "Edge RPC %s 返回非 200 状态码: %d (attempt=%d)",
                    path, resp.status_code, attempt,
                )
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:
            latency_ms = max(0, int(time.time() * 1000) - t0)
            last_exc = exc
            if attempt < retries:
                _logger.debug(
                    "Edge RPC %s 第 %d 次调用失败，重试: %s",
                    path, attempt + 1, exc,
                )

    latency_ms = max(0, int(time.time() * 1000) - t0)
    _logger.warning(
        "Edge RPC %s 全部 %d 次尝试失败: %s",
        path, retries + 1, last_exc,
    )
    return EdgeRpcCallResult(
        success=False,
        status="DEGRADED",
        reason_code="EDGE_RPC_CALL_FAILED",
        detail=str(last_exc or "unknown error"),
        event={},
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


async def post_vts(
    *,
    action: str,
    emotion_tag: str | None = None,
    request_id: str | None = None,
) -> EdgeRpcCallResult:
    """驱动 Edge VTS 表情/动作。

    action: talk_start | talk_end | set_expression | reset
    emotion_tag: 仅 set_expression 时需要（如 HAPPY / SAD / NEUTRAL）
    """
    payload: dict[str, Any] = {"action": str(action).strip().lower()}
    if emotion_tag:
        payload["emotion_tag"] = str(emotion_tag).strip().upper()
    result = await _post_to_edge(
        path="/v1/edge/vts",
        payload=payload,
        request_id=request_id,
    )
    _logger.debug(
        "post_vts action=%s emotion=%s → %s latency=%dms",
        action, emotion_tag, result.status, result.latency_ms,
    )
    return result


async def post_speak(
    *,
    text: str,
    request_id: str | None = None,
    session_id: str | None = None,
    utterance_id: str | None = None,
    voice_id: str | None = None,
    speed: float = 1.0,
    pitch: float = 0.0,
    emotion_tag: str | None = None,
) -> EdgeRpcCallResult:
    """驱动 Edge TTS 语音合成。

    text: 要合成的文本
    voice_id: 声线 ID（可选，Edge 端有默认值）
    speed: 语速倍率（0.5~2.0）
    pitch: 音调偏移（-1.0~1.0）
    emotion_tag: 情感标签（可选，影响语调）
    """
    req_id = request_id or new_request_id()
    resolved_session_id = str(session_id or "").strip() or f"sess::{req_id}"
    resolved_utterance_id = str(utterance_id or "").strip() or f"utt::{req_id}"
    normalized_text = (
        str(text or "")
        .replace("〜", "ー")
        .replace("～", "ー")
        .replace("\u200b", "")
        .strip()
    )

    payload: dict[str, Any] = {
        "session_id": resolved_session_id,
        "utterance_id": resolved_utterance_id,
        "text": normalized_text,
        "speed": float(speed),
        "pitch": float(pitch),
    }
    if voice_id:
        payload["voice_id"] = str(voice_id).strip()
    if emotion_tag:
        payload["emotion_tag"] = str(emotion_tag).strip().upper()
    result = await _post_to_edge(
        path="/v1/edge/speak",
        payload=payload,
        request_id=req_id,
    )
    _logger.debug(
        "post_speak text_len=%d → %s latency=%dms",
        len(normalized_text), result.status, result.latency_ms,
    )
    return result


async def post_screenshot(
    *,
    monitor_index: int = 1,
    request_id: str | None = None,
) -> EdgeRpcCallResult:
    """请求 Edge 截图，返回 base64 编码的 PNG 图像。

    event 字段包含：image_b64 / image_mime / width / height / latency_ms
    """
    payload: dict[str, Any] = {"monitor_index": int(monitor_index)}
    result = await _post_to_edge(
        path="/v1/edge/screenshot",
        payload=payload,
        request_id=request_id,
    )
    _logger.debug(
        "post_screenshot monitor=%d → %s latency=%dms",
        monitor_index, result.status, result.latency_ms,
    )
    return result


async def post_automate(
    *,
    actions: list[dict[str, Any]],
    stop_on_error: bool = True,
    request_id: str | None = None,
) -> EdgeRpcCallResult:
    """在 Edge 端执行桌面自动化操作（点击/输入/滚动等）。

    actions: 操作列表，每项包含 type / x / y / text / amount 等字段
    stop_on_error: 遇到错误是否停止（默认 True）
    """
    payload: dict[str, Any] = {
        "actions": list(actions),
        "stop_on_error": bool(stop_on_error),
    }
    result = await _post_to_edge(
        path="/v1/edge/automate",
        payload=payload,
        request_id=request_id,
    )
    _logger.debug(
        "post_automate actions=%d → %s latency=%dms",
        len(actions), result.status, result.latency_ms,
    )
    return result


async def post_fallback_presence(
    *,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> EdgeRpcCallResult:
    """调用 Edge Fallback LLM 的 presence 接口（心跳/存活检测）。"""
    result = await _post_to_edge(
        path="/v1/edge/fallback/presence",
        payload=dict(payload or {}),
        request_id=request_id,
    )
    return result
