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
    # 环境变量只选择连接端点；后续 submit 仍会在远端场景强制 HTTPS，不能因为
    # 运维配置存在就默认其适合传输用户内容与 HMAC 凭据。
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
    # 只按引用键读取单个值，调用方永远不接收整个秘密存储映射；失败也统一返回 None，
    # 让 transport 以 AUTH_UNAVAILABLE 拒绝而非泄露文件结构或密钥内容。
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


# request_id 管理业务幂等，nonce 管理传输重放，两者不能互相替代；相同请求的
# 重送仍可使用不同 nonce，而 Edge 可根据 request_id 恢复同一持久结果。
def build_edge_envelope(
    *,
    request_id: str,
    payload: Mapping[str, Any],
    shared_key: str,
    ts_ms: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    # 签名覆盖规范化载荷、时间和随机 nonce；Edge 可区分篡改、重放与同一请求的幂等重试。
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


class _HttpEdgeTransportPort:
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
        # 远端端点强制 TLS；HMAC 只认证内容，不为明文网络提供保密性。
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
            # 不在传输层重试：异常可能发生在 Edge 已接收并开始执行之后，物理结果
            # 不可判定，必须向 durable outbox/对账者返回 IN_DOUBT 而不是重复副作用。
            # 网络异常后的物理结果未知，必须返回 IN_DOUBT 供上层对账，不能自动再次发送。
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


async def _submit_edge_request(
    *,
    path: str,
    payload: Mapping[str, Any],
    request_id: str | None = None,
    timeout_ms: int = 3_000,
    base_url: str | None = None,
    shared_key: str | None = None,
) -> EdgeTransportResult:
    return await _HttpEdgeTransportPort(
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
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
) -> EdgeTransportResult:
    denied = _require_terminal_action_preview(
        service=action_preview_service,
        event_id=action_preview_event_id,
        boundary=action_preview_boundary,
        allowed_action_kinds=frozenset(
            {"life-delivery.edge.proactive", "proactive.ui_push"}
        ),
        expected_scope=str(request_id or ""),
        expected_destination="edge.proactive",
        expected_payload=dict(message or {}),
    )
    if denied is not None:
        return denied
    return await _submit_edge_request(
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
    effect_id: str | None = None,
    wait_result: bool = True,
    timeout_ms: int = 12_000,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    accepted_effect_proof: Any | None = None,
) -> EdgeTransportResult:
    """Submit one idempotency-bound reply to the durable Gateway outbox."""

    # 最终回复必须重新打开 Preview 或 Acceptance Effect 权威，再构造可持久去重的 Gateway payload。
    req_id = str(request_id or new_request_id())
    # delivery_id（若存在）才是外层 outbox 的稳定投递键；request_id 可由调用方省略，
    # 因此预览作用域显式优先绑定 delivery_id，避免重试时生成新的授权对象。
    extra = dict(raw_extra or {})
    preview_payload: dict[str, Any] = {
        "platform": str(platform or "").strip().lower(),
        "conversation_id": str(conversation_id or "").strip(),
        "content": str(content),
        "msg_type": str(msg_type or "text").strip().lower(),
    }
    if str(media_path or "").strip():
        preview_payload["media_path"] = str(media_path).strip()
    if extra:
        preview_payload["raw_extra"] = extra
    stable_scope = str(delivery_id or req_id).strip()
    if accepted_effect_proof is not None:
        denied = _require_accepted_surface_effect(
            proof=accepted_effect_proof,
            effect_id=str(effect_id or ""),
            request_id=req_id,
            terminal_payload=preview_payload,
        )
    else:
        denied = _require_terminal_action_preview(
            service=action_preview_service,
            event_id=action_preview_event_id,
            boundary=action_preview_boundary,
            allowed_action_kinds=frozenset(
                {
                    "life-delivery.proactive.gateway",
                    "life-delivery.edge.gateway_reply",
                }
            ),
            expected_scope=stable_scope,
            expected_destination=None,
            expected_payload=preview_payload,
        )
    if denied is not None:
        return denied
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
    if effect_id is not None:
        # The stable Acceptance Effect identity is transport metadata.  It is
        # persisted in the durable outbox payload/digest but never forwarded
        # to a platform adapter or folded into the visible Preview action.
        payload["effect_id"] = str(effect_id)
    return await _submit_gateway_payload(
        path="/v1/gateway/reply",
        payload=payload,
        timeout_ms=max(12_000, int(timeout_ms) + 3_000 if wait_result else int(timeout_ms)),
    )


async def query_gateway_reply_delivery(
    *,
    effect_id: str,
    delivery_id: str,
    platform: str,
    conversation_id: str,
    payload_digest: str,
    timeout_ms: int = 5_000,
) -> EdgeTransportResult:
    """Read an exact Gateway outbox snapshot without resubmitting a reply.

    The query uses the same internal_reply HMAC profile, timestamp/nonce
    envelope and loopback-or-TLS policy as reply intake.  ``UNKNOWN`` is a
    successful *read* result, not proof that no effect exists.
    """

    return await _submit_gateway_payload(
        path="/v1/gateway/reply/query",
        payload={
            "schema_version": "Natsume.gateway.reply-query.v1",
            "effect_id": str(effect_id),
            "delivery_id": str(delivery_id),
            "platform": str(platform),
            "conversation_id": str(conversation_id),
            "payload_digest": str(payload_digest),
        },
        timeout_ms=max(500, int(timeout_ms)),
        accepted_statuses=frozenset({"committed", "unknown"}),
    )


def _require_accepted_surface_effect(
    *,
    proof: Any,
    effect_id: str,
    request_id: str,
    terminal_payload: Mapping[str, Any],
) -> EdgeTransportResult | None:
    """Validate the narrow Acceptance Effect -> text outbox projection.

    The Task dispatcher has already claimed the immutable effect under a live
    fence.  Ordinary chat delivery therefore needs exact effect/payload
    identity, not a second Guardian/Scene policy decision that can drift while
    an unrelated runtime heartbeat is being recorded.
    """

    from host_brain.runtime.acceptance_effect_execution import (  # noqa: PLC0415
        AcceptanceEffectExecutionProof,
    )

    if not isinstance(proof, AcceptanceEffectExecutionProof):
        return _rejected(
            "ACCEPTANCE_EFFECT_AUTHORITY_REQUIRED",
            "accepted Gateway reply lacks a typed Acceptance Effect proof",
        )
    if (
        proof.effect_type != "surface.reply_delivery"
        or str(proof.effect_id) != str(effect_id or "")
        or not _acceptance_surface_matches(
            action_args=proof.action_args_dict(),
            request_id=str(request_id or ""),
            terminal_payload=terminal_payload,
        )
    ):
        return _rejected(
            "ACCEPTANCE_EFFECT_DRIFT",
            "accepted Gateway reply differs from its durable effect",
        )
    return None


def _require_terminal_action_preview(
    *,
    service: Any,
    event_id: str | None,
    boundary: Mapping[str, Any] | None,
    allowed_action_kinds: frozenset[str],
    expected_scope: str,
    expected_destination: str | None,
    expected_payload: Mapping[str, Any],
) -> EdgeTransportResult | None:
    """Reopen exact Preview authority at the final network boundary."""

    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415

    # Preview receipt 必须与最终目的地和载荷完全一致，防止已批准的预览被替换为另一条网络动作。
    if not isinstance(service, ActionPreviewService):
        return _rejected(
            "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
            "terminal Edge/Gateway transport has no app-local ActionPreview authority",
        )
    if not str(event_id or "").strip() or not isinstance(boundary, Mapping):
        return _rejected(
            "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
            "terminal Edge/Gateway transport lacks an exact Preview boundary",
        )
    exact = dict(boundary)
    required = {
        "action_kind",
        "action_scope",
        "risk_level",
        "action_args",
        "context_pack_digest",
        "scene_identity",
        "task_graph_id",
        "graph_revision",
        "task_node_id",
        "parent_governor_grant_ref",
        "node_grant_ref",
        "node_grant",
        "authority_context",
    }
    args = exact.get("action_args")
    if frozenset(exact) != required or not isinstance(args, Mapping):
        return _rejected(
            "ACTION_PREVIEW_INVALID",
            "terminal Preview execution boundary drifted",
        )
    action_args = dict(args)
    action_kind = str(exact.get("action_kind") or "")
    destination = str(action_args.get("destination") or "").strip().lower()
    payload = action_args.get("payload")
    acceptance_surface = action_kind == "acceptance-effect.surface.reply_delivery"
    flat_derived_matches = (
        action_kind == "proactive.ui_push"
        and action_kind in allowed_action_kinds
        and str(exact.get("action_scope") or "") == str(expected_scope or "")
        and _canonical_preview_payload(action_args)
        == _canonical_preview_payload(expected_payload)
    )
    enveloped_derived_matches = (
        action_kind in allowed_action_kinds
        and str(exact.get("action_scope") or "") == str(expected_scope or "")
        and str(action_args.get("delivery_key") or "") == str(expected_scope or "")
        and isinstance(payload, Mapping)
        and _canonical_preview_payload(payload)
        == _canonical_preview_payload(expected_payload)
        and (
            (expected_destination is not None and destination == expected_destination)
            or (
                expected_destination is None
                and destination in {"proactive.gateway", "edge.gateway_reply"}
            )
        )
    )
    derived_matches = flat_derived_matches or enveloped_derived_matches
    acceptance_matches = acceptance_surface and _acceptance_surface_matches(
        action_args=action_args,
        request_id=str(expected_scope or ""),
        terminal_payload=expected_payload,
    )
    if not derived_matches and not acceptance_matches:
        return _rejected(
            "ACTION_PREVIEW_DRIFT",
            "terminal Edge/Gateway payload differs from its Preview receipt",
        )
    try:
        service.require_execution(event_id=str(event_id), **exact)
    except Exception as exc:
        return _rejected(
            str(getattr(exc, "reason_code", None) or "ACTION_PREVIEW_INVALID"),
            str(getattr(exc, "detail", None) or "terminal Preview authority reopen failed"),
        )
    return None


def _acceptance_surface_matches(
    *,
    action_args: Mapping[str, Any],
    request_id: str,
    terminal_payload: Mapping[str, Any],
) -> bool:
    """Validate the sole approved Acceptance Effect -> Gateway transform."""

    if (
        str(action_args.get("request_id") or "") != request_id
        or str(action_args.get("decision") or "").strip().upper() != "APPROVE"
    ):
        return False
    actual = dict(terminal_payload)
    # No caller-controlled transform is implied by Acceptance.  The only
    # permitted terminal shape is the exact minimal text delivery below.
    # Media and metadata need separate, action-bound Preview receipts.
    if frozenset(actual) != {
        "platform",
        "conversation_id",
        "content",
        "msg_type",
    }:
        return False
    expected = {
        "platform": str(action_args.get("reply_channel") or "").strip().lower(),
        "conversation_id": str(action_args.get("reply_target") or "").strip(),
        "content": str(action_args.get("narrative") or ""),
        "msg_type": "text",
    }
    return _canonical_preview_payload(actual) == _canonical_preview_payload(expected)


def _canonical_preview_payload(value: Mapping[str, Any]) -> str:
    normalized = dict(value)
    if "platform" in normalized:
        normalized["platform"] = str(normalized.get("platform") or "").strip().lower()
        normalized["conversation_id"] = str(normalized.get("conversation_id") or "").strip()
        normalized["content"] = str(normalized.get("content") or "")
        normalized["msg_type"] = str(normalized.get("msg_type") or "text").strip().lower()
        if not str(normalized.get("media_path") or "").strip():
            normalized.pop("media_path", None)
        if not dict(normalized.get("raw_extra") or {}):
            normalized.pop("raw_extra", None)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def _submit_gateway_payload(
    *,
    path: str,
    payload: Mapping[str, Any],
    timeout_ms: int,
    accepted_statuses: frozenset[str] | None = None,
) -> EdgeTransportResult:
    # Gateway 请求独立签名并遵从同一 loopback/TLS 策略；该函数只尝试一次，重试属于持久 outbox。
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
    accepted = accepted_statuses or frozenset(
        {"sent", "queued", "accepted", "acked", "duplicate", "ok"}
    )
    success = response.status_code == 200 and (
        status in accepted
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
    # Every caller reaches this helper before constructing or invoking an
    # HTTP client. Preserve that zero-effect fact so the Acceptance Effect
    # runtime can safely retry a corrected local configuration instead of
    # quarantining it as an unknown network outcome.
    return EdgeTransportResult(
        False,
        status,
        reason_code,
        detail,
        {"external_side_effects": False},
        0,
    )


__all__ = [
    "build_edge_envelope",
    "resolve_edge_base_url",
    "resolve_gateway_base_urls",
    "resolve_host_edge_rpc_shared_key",
    "query_gateway_reply_delivery",
    "submit_gateway_reply",
    "submit_proactive_push",
]
