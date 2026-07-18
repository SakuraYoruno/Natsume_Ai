"""Typed Host -> Edge transports.

This module owns the private HTTP/HMAC mechanics.  Domain code submits either
an Edge command through :class:`EdgeTransportPort` or an idempotency-bound
Gateway reply.  Neither path retries: embodiment has Edge durable receipts and
Gateway delivery is retried by its durable outbox owner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping
import urllib.parse
import uuid

from host_brain.core.ids import new_request_id, now_ms
from natsume_contracts.edge.transport_v1 import EdgeTransportResult


_LOOPBACK_EDGE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}".rstrip("/") if netloc else ""


def _is_loopback_edge_endpoint(base_url: str) -> bool:
    """Return whether an Edge RPC endpoint is explicitly local to this Host.

    HMAC authenticates the request body, but it does not provide transport
    confidentiality.  The only intentionally clear-text deployment is the
    local Edge sidecar.  Do not infer loopback from DNS: the exception is
    deliberately limited to stable, explicit loopback endpoint spellings.
    """

    try:
        host = str(urllib.parse.urlparse(base_url).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return host.rstrip(".") in _LOOPBACK_EDGE_HOSTS


def _edge_endpoint_requires_tls(base_url: str) -> bool:
    """Whether a configured Host-to-Edge endpoint violates transport policy.

    This guard is intentionally shared by the authenticated Edge RPC and the
    Gateway-reply ingress.  Both carry user-controlled content and an HMAC
    credential, so a remote clear-text endpoint would disclose more than the
    command transport alone.
    """

    try:
        scheme = str(urllib.parse.urlparse(base_url).scheme or "").lower()
    except (TypeError, ValueError):
        scheme = ""
    return scheme != "https" and not _is_loopback_edge_endpoint(base_url)


def resolve_edge_base_url() -> str:
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return _normalize_base_url(override)
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    try:
        port = int(str(os.environ.get("EDGE_RPC_PORT") or "17800").strip())
    except ValueError:
        port = 17800
    return f"http://{host or '127.0.0.1'}:{port}"


def resolve_gateway_base_urls() -> list[str]:
    override = str(
        os.environ.get("HOST_EDGE_GATEWAY_BASE_URL")
        or os.environ.get("HOST_GATEWAY_BASE_URL")
        or ""
    ).strip()
    host = str(
        os.environ.get("EDGE_GATEWAY_HOST")
        or os.environ.get("EDGE_RPC_HOST")
        or "127.0.0.1"
    ).strip()
    try:
        port = int(str(os.environ.get("EDGE_GATEWAY_PORT") or "19000").strip())
    except ValueError:
        port = 19000
    values = [_normalize_base_url(override)] if override else []
    values.append(f"http://{host or '127.0.0.1'}:{port}")
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _secret_store_value(reference: str) -> str | None:
    ref = str(reference or "").strip()
    if not ref:
        return None
    path = (
        Path(__file__).resolve().parents[2]
        / "configs"
        / "secrets"
        / "secret_store.runtime.local.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    value = str(payload.get(ref) or "").strip()
    return value or None


def resolve_host_edge_rpc_shared_key() -> str | None:
    direct = str(os.environ.get("HOST_EDGE_RPC_SHARED_KEY") or "").strip()
    if direct:
        return direct
    return _secret_store_value(
        str(os.environ.get("HOST_EDGE_RPC_SHARED_KEY_REF") or "")
    )


def _resolve_gateway_reply_key() -> str | None:
    direct = str(os.environ.get("HOST_GATEWAY_REPLY_HMAC_SECRET") or "").strip()
    if direct:
        return direct
    return _secret_store_value(
        str(os.environ.get("HOST_GATEWAY_REPLY_HMAC_SECRET_REF") or "")
    )


def _edge_signature(
    *,
    request_id: str,
    ts_ms: int,
    nonce: str,
    payload: Mapping[str, Any],
    shared_key: str,
) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    material = f"{request_id}\n{ts_ms}\n{nonce}\n{canonical}".encode("utf-8")
    return hmac.new(shared_key.encode("utf-8"), material, hashlib.sha256).hexdigest()


def build_edge_envelope(
    *,
    request_id: str,
    payload: Mapping[str, Any],
    shared_key: str,
    ts_ms: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    issued_at_ms = int(ts_ms if ts_ms is not None else now_ms())
    resolved_nonce = str(nonce or uuid.uuid4())
    body = dict(payload)
    return {
        "request_id": str(request_id),
        "ts_ms": issued_at_ms,
        "nonce": resolved_nonce,
        "signature": _edge_signature(
            request_id=str(request_id),
            ts_ms=issued_at_ms,
            nonce=resolved_nonce,
            payload=body,
            shared_key=shared_key,
        ),
        "payload": body,
    }


class HttpEdgeTransportPort:
    """One-attempt, authenticated implementation of ``EdgeTransportPort``."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        shared_key: str | None = None,
    ) -> None:
        self._base_url = _normalize_base_url(base_url or resolve_edge_base_url())
        self._shared_key = str(
            shared_key if shared_key is not None else resolve_host_edge_rpc_shared_key() or ""
        ).strip()

    async def submit(
        self,
        *,
        path: str,
        payload: Mapping[str, Any],
        request_id: str,
        timeout_ms: int,
    ) -> EdgeTransportResult:
        started = time.monotonic()
        if not self._base_url:
            return _rejected("EDGE_RPC_ENDPOINT_MISSING", "Edge endpoint is not configured")
        if _edge_endpoint_requires_tls(self._base_url):
            return _rejected(
                "EDGE_RPC_TLS_REQUIRED",
                "Non-loopback Host to Edge RPC endpoints must use HTTPS",
            )
        if not self._shared_key:
            return _rejected("EDGE_RPC_AUTH_UNAVAILABLE", "Edge HMAC key is not configured")
        try:
            import httpx
        except ImportError:
            return _rejected("HTTPX_NOT_INSTALLED", "httpx not installed", status="DEGRADED")

        envelope = build_edge_envelope(
            request_id=request_id,
            payload=payload,
            shared_key=self._shared_key,
        )
        try:
            async with httpx.AsyncClient(
                timeout=max(0.2, int(timeout_ms) / 1000.0),
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    json=envelope,
                )
        except Exception as exc:
            return EdgeTransportResult(
                success=False,
                status="IN_DOUBT",
                reason_code="EDGE_RPC_DELIVERY_UNCERTAIN",
                detail=f"{type(exc).__name__}: {str(exc)[:180]}",
                event={"external_side_effects": "UNKNOWN"},
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            response_body = response.json()
        except Exception:
            response_body = {}
        body = dict(response_body) if isinstance(response_body, Mapping) else {}
        if response.status_code != 200:
            return EdgeTransportResult(
                success=False,
                status="REJECTED" if response.status_code < 500 else "IN_DOUBT",
                reason_code=str(
                    body.get("reason_code")
                    or (
                        "EDGE_RPC_REJECTED"
                        if response.status_code < 500
                        else "EDGE_RPC_DELIVERY_UNCERTAIN"
                    )
                ),
                detail=str(body.get("detail") or f"HTTP {response.status_code}")[:240],
                event=dict(body.get("event") or {}),
                latency_ms=latency_ms,
            )
        return EdgeTransportResult(
            success=bool(body.get("success", True)),
            status=str(body.get("status") or "ACCEPTED"),
            reason_code=(str(body.get("reason_code")) if body.get("reason_code") else None),
            detail=str(body.get("detail") or "ok")[:240],
            event=dict(body.get("event") or {}),
            latency_ms=latency_ms,
        )


async def submit_edge_request(
    *,
    path: str,
    payload: Mapping[str, Any],
    request_id: str | None = None,
    timeout_ms: int = 3_000,
    base_url: str | None = None,
    shared_key: str | None = None,
) -> EdgeTransportResult:
    return await HttpEdgeTransportPort(
        base_url=base_url,
        shared_key=shared_key,
    ).submit(
        path=path,
        payload=payload,
        request_id=str(request_id or new_request_id()),
        timeout_ms=max(200, int(timeout_ms)),
    )


async def submit_proactive_push(
    *,
    message: Mapping[str, Any],
    request_id: str,
    timeout_ms: int = 5_000,
    base_url: str | None = None,
    shared_key: str | None = None,
) -> EdgeTransportResult:
    return await submit_edge_request(
        path="/v1/edge/proactive_push",
        payload=message,
        request_id=request_id,
        timeout_ms=timeout_ms,
        base_url=base_url,
        shared_key=shared_key,
    )


async def submit_gateway_reply(
    *,
    platform: str | None,
    conversation_id: str | None,
    content: str,
    msg_type: str = "text",
    media_path: str | None = None,
    raw_extra: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    delivery_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 12_000,
) -> EdgeTransportResult:
    """Submit one idempotency-bound reply to the durable Gateway outbox."""

    req_id = str(request_id or new_request_id())
    extra = dict(raw_extra or {})
    material = json.dumps(
        {
            "platform": str(platform or "").strip().lower(),
            "conversation_id": str(conversation_id or "").strip(),
            "content": str(content),
            "msg_type": str(msg_type or "text").strip().lower(),
            "media_path": str(media_path or "").strip(),
            "raw_extra": extra,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    stable_delivery_id = str(delivery_id or "").strip() or (
        "reply:" + hashlib.sha256(req_id.encode("utf-8") + b"\n" + material).hexdigest()
    )
    payload: dict[str, Any] = {
        "request_id": req_id,
        "delivery_id": stable_delivery_id,
        "platform": str(platform or "").strip().lower(),
        "conversation_id": str(conversation_id or "").strip(),
        "content": str(content),
        "msg_type": str(msg_type or "text").strip().lower(),
        "wait_result": bool(wait_result),
        "timeout_ms": int(timeout_ms),
    }
    if str(media_path or "").strip():
        payload["media_path"] = str(media_path).strip()
    if extra:
        payload["raw_extra"] = extra
    return await _submit_gateway_payload(
        path="/v1/gateway/reply",
        payload=payload,
        timeout_ms=max(12_000, int(timeout_ms) + 3_000 if wait_result else int(timeout_ms)),
    )


async def _submit_gateway_payload(
    *,
    path: str,
    payload: Mapping[str, Any],
    timeout_ms: int,
) -> EdgeTransportResult:
    started = time.monotonic()
    secret = str(_resolve_gateway_reply_key() or "")
    if not secret:
        return _rejected(
            "GATEWAY_REPLY_AUTH_UNAVAILABLE",
            "Gateway reply HMAC key is not configured",
        )
    try:
        import httpx
    except ImportError:
        return _rejected("HTTPX_NOT_INSTALLED", "httpx not installed", status="DEGRADED")
    body = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    canonical = (
        "natsume-webhook-v1\ninternal_reply\nPOST\n"
        f"{path}\n{timestamp}\n{nonce}\n"
    ).encode("utf-8") + body
    signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Natsume-Webhook-Timestamp": timestamp,
        "X-Natsume-Webhook-Nonce": nonce,
        "X-Natsume-Webhook-Signature": signature,
    }
    base_urls = resolve_gateway_base_urls()
    if not base_urls:
        return _rejected("GATEWAY_REPLY_ENDPOINT_MISSING", "Gateway endpoint is not configured")
    base_url = base_urls[0]
    if _edge_endpoint_requires_tls(base_url):
        return _rejected(
            "EDGE_GATEWAY_TLS_REQUIRED",
            "Non-loopback Host to Edge Gateway endpoints must use HTTPS",
        )
    try:
        async with httpx.AsyncClient(
            timeout=max(0.2, timeout_ms / 1000.0),
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{base_url}{path}",
                content=body,
                headers=headers,
            )
    except Exception as exc:
        return EdgeTransportResult(
            False,
            "IN_DOUBT",
            "GATEWAY_REPLY_DELIVERY_UNCERTAIN",
            f"{type(exc).__name__}: {str(exc)[:180]}",
            {"external_side_effects": "UNKNOWN"},
            int((time.monotonic() - started) * 1000),
        )
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        raw = response.json()
    except Exception:
        raw = {}
    parsed = dict(raw) if isinstance(raw, Mapping) else {}
    status = str(parsed.get("status") or "unknown").strip().lower()
    success = response.status_code == 200 and (
        status in {"sent", "queued", "accepted", "acked", "duplicate", "ok"}
        or parsed.get("success") is True
    )
    return EdgeTransportResult(
        success,
        str(parsed.get("status") or ("accepted" if success else "rejected")),
        None if success else str(parsed.get("reason_code") or "GATEWAY_REPLY_REJECTED"),
        str(parsed.get("detail") or f"gateway reply status={status}")[:240],
        parsed,
        latency_ms,
    )


def _rejected(reason_code: str, detail: str, *, status: str = "REJECTED") -> EdgeTransportResult:
    return EdgeTransportResult(False, status, reason_code, detail, {}, 0)


__all__ = [
    "HttpEdgeTransportPort",
    "build_edge_envelope",
    "resolve_edge_base_url",
    "resolve_gateway_base_urls",
    "resolve_host_edge_rpc_shared_key",
    "submit_edge_request",
    "submit_gateway_reply",
    "submit_proactive_push",
]
