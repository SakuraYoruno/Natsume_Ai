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
import inspect
import json
import logging
import os
import threading
import urllib.parse
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
_EDGE_CLOCK_OFFSET_LOCK = threading.Lock()
_EDGE_FAILURE_COOLDOWN_LOCK = threading.Lock()
_EDGE_FAILURE_COOLDOWN_CACHE: dict[tuple[str, str], int] = {}

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
    with _EDGE_CLOCK_OFFSET_LOCK:
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


def _resolve_failure_cooldown_ms() -> int:
    raw = str(os.environ.get("HOST_EDGE_RPC_FAILURE_COOLDOWN_MS") or "15000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 15_000
    return max(0, min(value, 120_000))


def _set_edge_failure_cooldown(*, base_url: str, path: str, now_ms: int | None = None) -> None:
    cooldown_ms = _resolve_failure_cooldown_ms()
    if cooldown_ms <= 0:
        return
    expires_at_ms = int(now_ms or now_ms_fn()) + cooldown_ms
    with _EDGE_FAILURE_COOLDOWN_LOCK:
        _EDGE_FAILURE_COOLDOWN_CACHE[(str(base_url), str(path))] = expires_at_ms


def _clear_edge_failure_cooldown(*, base_url: str, path: str) -> None:
    with _EDGE_FAILURE_COOLDOWN_LOCK:
        _EDGE_FAILURE_COOLDOWN_CACHE.pop((str(base_url), str(path)), None)


def _is_edge_failure_cooldown_open(*, base_url: str, path: str, now_ms: int | None = None) -> bool:
    current_ms = int(now_ms or now_ms_fn())
    with _EDGE_FAILURE_COOLDOWN_LOCK:
        expires_at_ms = int(_EDGE_FAILURE_COOLDOWN_CACHE.get((str(base_url), str(path))) or 0)
        if expires_at_ms <= current_ms:
            _EDGE_FAILURE_COOLDOWN_CACHE.pop((str(base_url), str(path)), None)
            return False
        return True


def now_ms_fn() -> int:
    return int(now_ms())


async def _resolve_edge_clock_offset_ms(
    *,
    base_url: str,
    timeout_ms: int,
) -> int:
    import time

    now_value = int(time.time() * 1000)
    with _EDGE_CLOCK_OFFSET_LOCK:
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
        async with httpx.AsyncClient(timeout=probe_timeout_s, trust_env=False) as client:
            local_before_ms = int(time.time() * 1000)
            response = await client.get(f"{base_url}/v1/edge/health")
            local_after_ms = int(time.time() * 1000)
        raise_result = response.raise_for_status()
        if inspect.isawaitable(raise_result):
            await raise_result
        body = response.json()
        if inspect.isawaitable(body):
            body = await body
        if not isinstance(body, Mapping):
            return 0
        edge_ts_ms = _extract_edge_ts_ms(body)
        if edge_ts_ms is None:
            return 0
        midpoint_ms = (local_before_ms + local_after_ms) // 2
        offset_ms = int(edge_ts_ms) - int(midpoint_ms)
        with _EDGE_CLOCK_OFFSET_LOCK:
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
        with _EDGE_CLOCK_OFFSET_LOCK:
            if _EDGE_CLOCK_OFFSET_CACHE.get("base_url") == base_url:
                return int(_EDGE_CLOCK_OFFSET_CACHE.get("offset_ms") or 0)
        return 0


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------


def _resolve_edge_base_url() -> str:
    base_url_override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if base_url_override:
        return _normalize_base_url(base_url_override)

    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        return ""
    return f"{scheme}://{netloc}".rstrip("/")


def _resolve_edge_base_urls() -> list[str]:
    base_url_override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    primary = _resolve_edge_base_url()
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800

    candidates = [primary]
    if not base_url_override:
        candidates.append(f"http://{host}:{port}")
    allow_local_fallback = str(os.environ.get("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if allow_local_fallback and not base_url_override and host not in {"127.0.0.1", "localhost"}:
        candidates.extend([f"http://127.0.0.1:{port}", f"http://localhost:{port}"])

    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        url = _normalize_base_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _resolve_gateway_base_urls() -> list[str]:
    override = str(
        os.environ.get("HOST_EDGE_GATEWAY_BASE_URL")
        or os.environ.get("HOST_GATEWAY_BASE_URL")
        or ""
    ).strip()
    primary = _normalize_base_url(override) if override else ""
    host = (
        str(
            os.environ.get("EDGE_GATEWAY_HOST")
            or os.environ.get("EDGE_RPC_HOST")
            or "127.0.0.1"
        ).strip()
        or "127.0.0.1"
    )
    port_raw = str(os.environ.get("EDGE_GATEWAY_PORT") or "19000").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 19000

    candidates: list[str] = []
    if primary:
        candidates.append(primary)
    candidates.append(f"http://{host}:{port}")
    allow_local_fallback = str(os.environ.get("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if allow_local_fallback and host not in {"127.0.0.1", "localhost"}:
        candidates.extend([f"http://127.0.0.1:{port}", f"http://localhost:{port}"])

    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        url = _normalize_base_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _is_loopback_base_url(base_url: str) -> bool:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return False
    parsed = urllib.parse.urlparse(normalized)
    host = str(parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _resolve_block_loopback_wechat() -> bool:
    raw = str(os.environ.get("HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
    request_timeout_ms: int | None = None,
    block_loopback: bool = False,
) -> EdgeRpcCallResult:
    """Build envelope and asynchronously POST to Edge endpoint."""
    import time

    try:
        import httpx
    except ImportError:
        return EdgeRpcCallResult(
            success=False,
            status="DEGRADED",
            reason_code="HTTPX_NOT_INSTALLED",
            detail="httpx not installed",
            event={},
            latency_ms=0,
        )

    req_id = request_id or new_request_id()
    base_urls = _resolve_edge_base_urls()
    if block_loopback:
        filtered_urls = [item for item in base_urls if not _is_loopback_base_url(item)]
        if not filtered_urls:
            return EdgeRpcCallResult(
                success=False,
                status="REJECTED",
                reason_code="EDGE_RPC_LOCAL_BLOCKED",
                detail=(
                    "loopback edge endpoint blocked for this command; "
                    "set HOST_EDGE_RPC_BASE_URL to remote edge or disable "
                    "HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT"
                ),
                event={},
                latency_ms=0,
            )
        base_urls = filtered_urls
    shared_key = _resolve_hmac_key()
    allow_insecure = _resolve_allow_insecure()
    timeout_ms = (
        max(1000, int(request_timeout_ms))
        if request_timeout_ms is not None
        else _resolve_timeout_ms()
    )
    retries = _resolve_retries()

    t0 = int(time.time() * 1000)
    last_exc: Exception | None = None
    attempts: list[str] = []

    cooldown_skipped_urls: list[str] = []
    attempted_base_urls: list[str] = []

    for base_url in base_urls:
        if _is_edge_failure_cooldown_open(base_url=base_url, path=path, now_ms=t0):
            cooldown_skipped_urls.append(base_url)
            attempts.append(f"{base_url}:COOLDOWN")
            continue
        url = f"{base_url}{path}"
        clock_offset_ms = await _resolve_edge_clock_offset_ms(
            base_url=base_url,
            timeout_ms=timeout_ms,
        )
        forced_recalibrated = False

        for attempt in range(retries + 1):
            if base_url not in attempted_base_urls:
                attempted_base_urls.append(base_url)
            envelope = _build_envelope(
                request_id=req_id,
                payload=payload,
                shared_key=shared_key,
                allow_insecure=allow_insecure,
                ts_ms=int(now_ms()) + int(clock_offset_ms),
            )
            try:
                async with httpx.AsyncClient(timeout=float(timeout_ms) / 1000.0, trust_env=False) as client:
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
                    if result.reason_code == "EDGE_RPC_AUTH_EXPIRED" and not forced_recalibrated:
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
                    _clear_edge_failure_cooldown(base_url=base_url, path=path)
                    return result

                _logger.warning(
                    "Edge RPC %s returned non-200 status: %d (base=%s attempt=%d)",
                    path,
                    resp.status_code,
                    base_url,
                    attempt,
                )
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                attempts.append(f"{base_url}#{attempt}:HTTP{resp.status_code}")
            except Exception as exc:
                last_exc = exc
                attempts.append(f"{base_url}#{attempt}:{exc.__class__.__name__}")
                if attempt < retries:
                    _logger.debug(
                        "Edge RPC %s attempt failed and will retry (base=%s attempt=%d): %s",
                        path,
                        base_url,
                        attempt + 1,
                        exc,
                    )

    if cooldown_skipped_urls and not attempted_base_urls:
        latency_ms = max(0, int(time.time() * 1000) - t0)
        attempted_detail = ", ".join(cooldown_skipped_urls)
        return EdgeRpcCallResult(
            success=False,
            status="DEGRADED",
            reason_code="EDGE_RPC_COOLDOWN_OPEN",
            detail=f"edge rpc cooldown open; skipped={attempted_detail}",
            event={},
            latency_ms=latency_ms,
        )

    for attempted_base_url in attempted_base_urls:
        _set_edge_failure_cooldown(base_url=attempted_base_url, path=path, now_ms=int(time.time() * 1000))

    latency_ms = max(0, int(time.time() * 1000) - t0)
    _logger.warning(
        "Edge RPC %s failed across endpoints after %d attempts: %s",
        path,
        len(attempts),
        last_exc,
    )
    attempted_detail = ", ".join(attempts[-8:]) if attempts else "no_endpoint_attempted"
    return EdgeRpcCallResult(
        success=False,
        status="DEGRADED",
        reason_code="EDGE_RPC_CALL_FAILED",
        detail=f"{last_exc or 'unknown error'}; attempted={attempted_detail}",
        event={},
        latency_ms=latency_ms,
    )


async def _post_to_gateway(
    *,
    path: str,
    payload: dict[str, Any],
    request_timeout_ms: int | None = None,
) -> EdgeRpcCallResult:
    import time

    try:
        import httpx
    except ImportError:
        return EdgeRpcCallResult(
            success=False,
            status="DEGRADED",
            reason_code="HTTPX_NOT_INSTALLED",
            detail="httpx not installed",
            event={},
            latency_ms=0,
        )

    base_urls = _resolve_gateway_base_urls()
    timeout_ms = (
        max(1000, int(request_timeout_ms))
        if request_timeout_ms is not None
        else max(_resolve_timeout_ms(), 12000)
    )
    retries = _resolve_retries()

    t0 = int(time.time() * 1000)
    last_exc: Exception | None = None
    attempts: list[str] = []

    for base_url in base_urls:
        url = f"{base_url}{path}"
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=float(timeout_ms) / 1000.0, trust_env=False) as client:
                    resp = await client.post(url, json=payload)
                latency_ms = max(0, int(time.time() * 1000) - t0)
                if resp.status_code == 200:
                    try:
                        raw_body = resp.json()
                    except Exception:
                        raw_body = {}
                    body = raw_body if isinstance(raw_body, Mapping) else {}
                    status_text = str(body.get("status") or "unknown").strip().lower()
                    success_statuses = {"sent", "queued", "accepted", "ok"}
                    success = status_text in success_statuses or bool(body.get("success"))
                    reason_code = None if success else str(body.get("reason_code") or "GATEWAY_REPLY_FAILED")
                    detail = str(body.get("detail") or f"gateway reply status={status_text or 'unknown'}")
                    return EdgeRpcCallResult(
                        success=success,
                        status=str(body.get("status") or ("sent" if success else "failed")),
                        reason_code=reason_code,
                        detail=detail,
                        event=dict(body if isinstance(body, Mapping) else {}),
                        latency_ms=latency_ms,
                    )
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                attempts.append(f"{base_url}#{attempt}:HTTP{resp.status_code}")
            except Exception as exc:
                last_exc = exc
                attempts.append(f"{base_url}#{attempt}:{exc.__class__.__name__}")
                if attempt < retries:
                    _logger.debug(
                        "Gateway RPC %s attempt failed and will retry (base=%s attempt=%d): %s",
                        path,
                        base_url,
                        attempt + 1,
                        exc,
                    )

    latency_ms = max(0, int(time.time() * 1000) - t0)
    attempted_detail = ", ".join(attempts[-8:]) if attempts else "no_endpoint_attempted"
    return EdgeRpcCallResult(
        success=False,
        status="DEGRADED",
        reason_code="GATEWAY_REPLY_CALL_FAILED",
        detail=f"{last_exc or 'unknown error'}; attempted={attempted_detail}",
        event={},
        latency_ms=latency_ms,
    )

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
    emotion_intensity: float | None = None,
    style_note: str | None = None,
    lang_hint: str | None = None,
) -> EdgeRpcCallResult:
    """驱动 Edge TTS 语音合成。

    text: 要合成的文本
    voice_id: 声线 ID（可选，Edge 端有默认值）
    speed: 语速倍率（0.5~2.0）
    pitch: 音调偏移（-1.0~1.0）
    emotion_tag: 情感标签（可选，影响语调与 VTS 表情）
    emotion_intensity: 情感强度 [0,1]（可选，供 TTS/VTS 使用）
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
    if emotion_intensity is not None:
        payload["emotion_intensity"] = max(0.0, min(1.0, float(emotion_intensity)))
    if style_note:
        payload["style_note"] = str(style_note).strip()
    if lang_hint:
        payload["lang_hint"] = str(lang_hint).strip().lower()
    rpc_timeout_ms = max(_resolve_timeout_ms(), 60_000)
    result = await _post_to_edge(
        path="/v1/edge/speak",
        payload=payload,
        request_id=req_id,
        request_timeout_ms=rpc_timeout_ms,
    )
    _logger.debug(
        "post_speak text_len=%d → %s latency=%dms",
        len(normalized_text), result.status, result.latency_ms,
    )
    return result


async def post_wechat_command(
    *,
    action: str,
    target: str,
    target_candidates: list[str] | None = None,
    content: str | None = None,
    request_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 10000,
) -> EdgeRpcCallResult:
    """Send WeChat command to edge command queue."""
    payload: dict[str, Any] = {
        "action": str(action or "").strip().lower(),
        "target": str(target or "").strip(),
        "wait_result": bool(wait_result),
        "timeout_ms": int(timeout_ms),
    }
    candidates: list[str] = []
    seen: set[str] = set()
    for item in list(target_candidates or []):
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(value)
    if candidates:
        payload["target_candidates"] = candidates
    if content is not None:
        payload["content"] = str(content)
    rpc_timeout_ms = _resolve_timeout_ms()
    if bool(wait_result):
        rpc_timeout_ms = max(int(rpc_timeout_ms), int(timeout_ms) + 3_000)
    result = await _post_to_edge(
        path="/v1/edge/wechat/command",
        payload=payload,
        request_id=request_id,
        request_timeout_ms=rpc_timeout_ms,
        block_loopback=_resolve_block_loopback_wechat(),
    )
    _logger.debug(
        "post_wechat_command action=%s target=%s wait=%s -> %s latency=%dms",
        payload["action"],
        payload["target"],
        payload["wait_result"],
        result.status,
        result.latency_ms,
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


async def post_visual_locate(
    *,
    instruction: str,
    monitor_index: int = 1,
    auto_click: bool = False,
    request_id: str | None = None,
    timeout_ms: int | None = None,
) -> EdgeRpcCallResult:
    """让 Edge 端使用本地 VLM 对屏幕截图进行视觉定位。

    Edge 端会自动截图并调用本地 Ollama（qwen2.5vl:7b）推理，
    返回目标元素的屏幕坐标，无需将截图传输到 Host。

    Args:
        instruction: 自然语言定位指令，例如 "点击确认按钮"
        monitor_index: 截图目标显示器索引（默认 1=主显示器）
        auto_click: 定位成功后是否自动点击（默认 False）
        request_id: 请求 ID（可选，不传则自动生成）
        timeout_ms: 请求超时（ms），VLM 推理较慢建议设置 30000+

    Returns:
        EdgeRpcCallResult，event 字段包含：
          x / y / confidence / label / latency_ms / clicked
    """
    payload: dict[str, Any] = {
        "instruction": str(instruction).strip(),
        "monitor_index": int(monitor_index),
        "auto_click": bool(auto_click),
    }
    # VLM 推理耗时较长，默认超时设为 30s
    rpc_timeout_ms = timeout_ms if timeout_ms is not None else max(_resolve_timeout_ms(), 30_000)
    result = await _post_to_edge(
        path="/v1/edge/visual_locate",
        payload=payload,
        request_id=request_id,
        request_timeout_ms=rpc_timeout_ms,
    )
    _logger.debug(
        "post_visual_locate instruction=%r auto_click=%s → %s latency=%dms",
        instruction, auto_click, result.status, result.latency_ms,
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


async def post_gateway_reply(
    *,
    platform: str | None,
    conversation_id: str | None,
    content: str,
    msg_type: str = "text",
    media_path: str | None = None,
    raw_extra: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 12000,
) -> EdgeRpcCallResult:
    """通过 Gateway Server 向对应平台发送回复。

    调用 Edge Gateway Server 的 /v1/gateway/reply 端点，
    由 OutboundDispatcher 路由到对应平台适配器（微信/QQ/飞书/企微）。

    Args:
        platform: 目标平台（wechat/qq/feishu/wecom）
        conversation_id: 会话 ID（群 ID 或私聊 ID）
        content: 回复文本内容
        msg_type: 消息类型（默认 text）
        request_id: 请求 ID（可选）
        wait_result: 是否等待发送结果（默认 True）
        timeout_ms: 超时时间（ms，默认 12000）

    Returns:
        EdgeRpcCallResult
    """
    req_id = request_id or new_request_id()
    payload: dict[str, Any] = {
        "request_id": req_id,
        "platform": str(platform or "").strip().lower(),
        "conversation_id": str(conversation_id or "").strip(),
        "content": str(content),
        "msg_type": str(msg_type or "text").strip().lower(),
        "wait_result": bool(wait_result),
        "timeout_ms": int(timeout_ms),
    }
    if str(media_path or "").strip():
        payload["media_path"] = str(media_path).strip()
    if isinstance(raw_extra, Mapping) and raw_extra:
        payload["raw_extra"] = dict(raw_extra)
    gateway_timeout_ms = max(_resolve_timeout_ms(), 12_000)
    if bool(wait_result):
        gateway_timeout_ms = max(int(gateway_timeout_ms), int(timeout_ms) + 3_000)
    result = await _post_to_gateway(
        path="/v1/gateway/reply",
        payload=payload,
        request_timeout_ms=gateway_timeout_ms,
    )
    _logger.debug(
        "post_gateway_reply platform=%s conversation_id=%s wait=%s -> %s latency=%dms",
        payload["platform"],
        payload["conversation_id"],
        payload["wait_result"],
        result.status,
        result.latency_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Phase 4: 微信语音/视频通话便捷方法
# ---------------------------------------------------------------------------


async def post_voice_call(
    *,
    target: str,
    conversation_id: str | None = None,
    platform: str = "wechat",
    request_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 15000,
) -> EdgeRpcCallResult:
    """通过 Gateway 向微信联系人发起语音通话。

    Args:
        target: 联系人昵称或 wxid
        conversation_id: 会话 ID（默认使用 target）
        platform: 平台（默认 wechat）
        request_id: 请求 ID（可选）
        wait_result: 是否等待结果（默认 True）
        timeout_ms: 超时时间 ms（默认 15000，通话操作需要更长时间）
    """
    return await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id or target,
        content=target,
        msg_type="voice_call",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )


async def post_video_call(
    *,
    target: str,
    conversation_id: str | None = None,
    platform: str = "wechat",
    request_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 15000,
) -> EdgeRpcCallResult:
    """通过 Gateway 向微信联系人发起视频通话。

    Args:
        target: 联系人昵称或 wxid
        conversation_id: 会话 ID（默认使用 target）
        platform: 平台（默认 wechat）
        request_id: 请求 ID（可选）
        wait_result: 是否等待结果（默认 True）
        timeout_ms: 超时时间 ms（默认 15000）
    """
    return await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id or target,
        content=target,
        msg_type="video_call",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )


async def post_cancel_call(
    *,
    platform: str = "wechat",
    request_id: str | None = None,
    timeout_ms: int = 10000,
) -> EdgeRpcCallResult:
    """取消当前正在进行的微信通话。

    Args:
        platform: 平台（默认 wechat）
        request_id: 请求 ID（可选）
        timeout_ms: 超时时间 ms（默认 10000）
    """
    return await post_gateway_reply(
        platform=platform,
        conversation_id="_cancel_call",
        content="",
        msg_type="cancel_call",
        request_id=request_id,
        wait_result=True,
        timeout_ms=timeout_ms,
    )
