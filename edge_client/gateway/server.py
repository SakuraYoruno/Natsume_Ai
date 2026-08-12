"""Unified Message Gateway — FastAPI Webhook Server。

监听端口 19000，接收各平台推送的消息 Webhook，
经过适配器解析 → 标准化 → 路由到 Host Brain。

端点：
  POST /v1/gateway/{platform}/webhook   接收平台消息推送
  POST /v1/gateway/reply                接收 Host Brain 的回复指令
  POST /v1/gateway/reply/query          认证只读查询 durable reply
  GET  /v1/gateway/health               健康检查
  GET  /v1/gateway/stats                统计信息
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from edge_client.gateway.delivery_runtime import (
    GATEWAY_REPLY_QUERY_SCHEMA_VERSION,
    GW_TRANSPORT_DISABLED,
    GatewayDeliveryError,
    GatewayDeliveryRuntime,
    GatewayTransportMode,
    load_gateway_transport_mode,
)
from edge_client.gateway.models import MsgType, Platform
from edge_client.gateway.normalizer import MessageNormalizer
from edge_client.gateway.outbound import ReplyPayloadRejected, sanitize_reply_payload
from edge_client.gateway.webhook_security import (
    RawWebhookEnvelope,
    VerifierRegistry,
    WebhookVerificationError,
)

logger = logging.getLogger("Natsume.edge.gateway.server")

_GATEWAY_START_TIME = time.time()
_MAX_HTTP_BODY_BYTES = 1_048_576
_MAX_WEBHOOK_EVENTS = 1_000
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_IDEMPOTENCY_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
_SENSITIVE_HEADER_FRAGMENTS = (
    "authorization",
    "cookie",
    "proof",
    "secret",
    "signature",
    "token",
)
_INBOUND_FORBIDDEN_EXTRA_KEYS = frozenset({
    "body",
    "events",
    "raw",
    "rawbody",
    "rawevent",
    "rawpayload",
})
_INBOUND_SENSITIVE_KEY_PARTS = frozenset({
    "accesskey",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "proof",
    "replytoken",
    "secret",
    "signature",
    "token",
})


def _raw_header_items(request: Request) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            bytes(name).decode("latin-1"),
            bytes(value).decode("latin-1"),
        )
        for name, value in request.scope.get("headers", ())
    )


def _safe_header_snapshot(items: tuple[tuple[str, str], ...]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in items:
        normalized = str(name or "").strip().lower()
        if not normalized or any(fragment in normalized for fragment in _SENSITIVE_HEADER_FRAGMENTS):
            continue
        if normalized in {"content-type", "user-agent", "x-request-id", "x-correlation-id"}:
            safe[normalized] = str(value or "")[:512]
    return safe


def _safe_inbound_delivery_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Host 只需要规范化事件的业务投影；认证原文、平台令牌与完整原始事件
    # 不应随 inbox 复制，递归限深/限长也防止恶意 webhook 放大持久化负载。
    """Remove platform credentials/raw event copies from Host-bound metadata."""

    safe = dict(payload)
    raw_extra = safe.get("raw_extra")
    if not isinstance(raw_extra, dict):
        safe.pop("raw_extra", None)
        return safe

    def _walk(value: Any, *, depth: int = 0) -> Any:
        if depth > 6:
            return None
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:4_096]
        if isinstance(value, list):
            return [_walk(item, depth=depth + 1) for item in value[:64]]
        if not isinstance(value, dict):
            return None
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key or "").strip()
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            if not key or compact in _INBOUND_FORBIDDEN_EXTRA_KEYS:
                continue
            if any(part in compact for part in _INBOUND_SENSITIVE_KEY_PARTS):
                continue
            result[key] = _walk(child, depth=depth + 1)
        return result

    sanitized = _walk(raw_extra)
    if isinstance(sanitized, dict) and sanitized:
        safe["raw_extra"] = sanitized
    else:
        safe.pop("raw_extra", None)
    return safe


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in pairs:
        key = str(raw_key)
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _nonnegative_sequence(value: Any) -> int | None:
    """Return a SQLite-safe source sequence without accepting bool coercion."""

    if value is None or isinstance(value, bool):
        return None
    try:
        sequence = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if sequence < 0 or sequence > _MAX_SQLITE_INTEGER:
        return None
    return sequence


def _nested_value(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _source_sequence(
    *,
    platform: str,
    payload: dict[str, Any],
    batch_index: int | None,
) -> int | None:
    """Extract an available-set sequence from authenticated platform data."""

    paths_by_platform: dict[str, tuple[tuple[str, ...], ...]] = {
        Platform.LINE.value: (("timestamp",),),
        Platform.QQ.value: (
            ("message_seq",),
            ("seq",),
            ("message_id",),
            ("time",),
        ),
        Platform.FEISHU.value: (
            ("event", "message", "create_time"),
            ("header", "create_time"),
        ),
        Platform.WECOM.value: (("CreateTime",), ("MsgId",)),
        Platform.WECHAT.value: (
            ("source_sequence",),
            ("sequence",),
            ("seq",),
            ("msg_id",),
            ("id",),
            ("NewMsgId",),
            ("MsgId",),
            ("CreateTime",),
            ("ts_ms",),
            ("ts",),
        ),
        Platform.TELEGRAM.value: (
            ("update_id",),
            ("msg_id",),
            ("message", "message_id"),
        ),
    }
    generic_paths = (
        ("source_sequence",),
        ("sequence",),
        ("seq",),
        ("update_id",),
        ("message_seq",),
        ("timestamp",),
    )
    for path in paths_by_platform.get(platform, ()) + generic_paths:
        sequence = _nonnegative_sequence(_nested_value(payload, *path))
        if sequence is not None:
            return sequence
    return _nonnegative_sequence(batch_index)


def _platform_event_id(
    *,
    platform: str,
    payload: dict[str, Any],
    normalized_msg_id: str,
) -> str | None:
    """Prefer the platform event identity, falling back to its message ID."""

    paths_by_platform: dict[str, tuple[tuple[str, ...], ...]] = {
        Platform.LINE.value: (("webhookEventId",), ("message", "id")),
        Platform.QQ.value: (("message_id",),),
        Platform.FEISHU.value: (
            ("header", "event_id"),
            ("event", "message", "message_id"),
        ),
        Platform.WECOM.value: (("MsgId",),),
        Platform.WECHAT.value: (
            ("msg_id",),
            ("id",),
            ("NewMsgId",),
            ("MsgId",),
        ),
        Platform.TELEGRAM.value: (("update_id",), ("msg_id",)),
    }
    for path in paths_by_platform.get(platform, ()):
        value = str(_nested_value(payload, *path) or "").strip()
        if value:
            return value
    fallback = str(normalized_msg_id or "").strip()
    return fallback or None


def _line_event_payloads(raw_payload: dict[str, Any]) -> list[tuple[int | None, dict[str, Any]]]:
    """Return one authenticated LINE event per item; never silently take the first."""

    if "events" not in raw_payload:
        # Preserve the adapter's single-event seam for local/offline callers.
        return [(None, raw_payload)]
    events = raw_payload.get("events")
    if not isinstance(events, list):
        raise ValueError("LINE events must be an array")
    if len(events) > _MAX_WEBHOOK_EVENTS:
        raise ValueError("LINE events array exceeds the limit")
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("LINE events must contain objects")
    return [(index, event) for index, event in enumerate(events)]


def _request_peer(request: Request) -> str | None:
    client = request.client
    return str(client.host) if client is not None and client.host else None


def _verified_request(
    *,
    registry: VerifierRegistry | None,
    platform: str,
    request: Request,
    raw_body: bytes,
) -> Any:
    # 先封存框架给出的有序 header/query 和原始 body，再交给注册表验签；
    # 未配置验证器不是兼容场景，HTTP 入口必须关闭。
    if registry is None:
        raise WebhookVerificationError(
            "GW_VERIFIER_UNAVAILABLE",
            platform=platform,
            detail="verifier registry is not configured",
        )
    return registry.verify(RawWebhookEnvelope(
        platform=platform,
        method=request.method,
        path=request.url.path,
        raw_body=raw_body,
        headers=_raw_header_items(request),
        query=tuple(request.query_params.multi_items()),
        received_at_ms=int(time.time() * 1000),
        request_id=(
            str(request.headers.get("x-request-id") or request.headers.get("x-correlation-id") or "").strip()
            or f"gateway-http:{uuid.uuid4().hex}"
        ),
        peer=_request_peer(request),
    ))


def _security_rejection(exc: WebhookVerificationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "status": "rejected",
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "platform": exc.platform,
        },
    )


def _delivery_rejection(exc: Exception, *, status_code: int = 503) -> JSONResponse:
    reason_code = str(
        getattr(exc, "reason_code", "GW_TRANSPORT_DELIVERY_FAILED")
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "rejected",
            "reason_code": reason_code,
            # Store/provider exception messages may contain paths, SQL text,
            # payload fragments or credentials.  The stable reason code is
            # the public diagnostic contract; raw details remain process-local.
            "detail": f"gateway delivery rejected ({reason_code})",
        },
    )


def create_gateway_app(
    *,
    normalizer: MessageNormalizer | None = None,
    adapters: dict[str, Any] | None = None,
    outbound: Any | None = None,
    delivery_runtime: GatewayDeliveryRuntime | None = None,
    webhook_verifiers: VerifierRegistry | None = None,
    reply_verifiers: VerifierRegistry | None = None,
) -> FastAPI:
    # 此工厂把 HTTP、认证、规范化和 durable transport 组合在一起；路由本身
    # 不直接调用模型或平台 API，所有副作用都转交给已配置的运行时。
    """创建 Gateway FastAPI 应用。

    Args:
        normalizer: MessageNormalizer 实例，None 时使用默认配置
        adapters: {platform_value: AbstractPlatformAdapter} 字典
        outbound: OutboundDispatcher 实例（Phase 2 接入）

    Returns:
        FastAPI 应用实例
    """
    _normalizer = normalizer or MessageNormalizer()
    _adapters: dict[str, Any] = adapters or {}
    _outbound = outbound
    _delivery = delivery_runtime
    _transport_mode = (
        delivery_runtime.mode
        if delivery_runtime is not None
        else load_gateway_transport_mode()
    )

    app = FastAPI(
        title="Natsume Unified Message Gateway",
        description="外部消息统一网关 — 将微信/QQ/飞书/企微消息路由到 Host Brain",
        version="0.1.0",
    )

    # The legacy WeChat query router can enqueue Bridge commands and has no
    # principal/scope/rate/replay contract.  Keep it fail-closed until
    # TASK-0494 installs the authenticated loopback-only command surface.
    @app.post("/v1/gateway/wechat/query")
    async def wechat_query_unavailable() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "status": "rejected",
                "reason_code": "GW_WECHAT_QUERY_AUTH_UNAVAILABLE",
                "detail": "authenticated WeChat query surface is not installed",
            },
        )

    # ------------------------------------------------------------------
    # POST /v1/gateway/{platform}/webhook — 接收平台消息推送
    # ------------------------------------------------------------------

    @app.post("/v1/gateway/{platform}/webhook")
    async def platform_webhook(platform: str, request: Request) -> JSONResponse:
        """接收平台消息推送 Webhook。"""
        normalized_platform = str(platform or "").strip().lower()
        adapter = _adapters.get(normalized_platform)
        if adapter is None:
            logger.warning("Gateway webhook: 未找到适配器 platform=%s", normalized_platform)
            raise HTTPException(
                status_code=404,
                detail=f"No adapter registered for platform: {normalized_platform}",
            )
        if _delivery is None or not _delivery.verification_enabled:
            # 没有权威 inbox/验签配置时不能以临时内存路径接收外部事件。
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP webhook intake is disabled",
                ),
                status_code=503,
            )

        # Raw bytes and duplicate header/query fields are frozen before parsing.
        # 大小上限在读取后立即执行，避免解析、验签或存储超大攻击体。
        raw_body = await request.body()
        if not raw_body or len(raw_body) > _MAX_HTTP_BODY_BYTES:
            return JSONResponse(
                status_code=413 if raw_body else 400,
                content={
                    "status": "rejected",
                    "reason_code": "GW_BODY_INVALID",
                    "detail": "request body is empty or exceeds the limit",
                },
            )
        try:
            verified = _verified_request(
                registry=webhook_verifiers,
                platform=normalized_platform,
                request=request,
                raw_body=raw_body,
            )
        except WebhookVerificationError as exc:
            logger.warning("Gateway webhook verification rejected platform=%s reason=%s", normalized_platform, exc.reason_code)
            return _security_rejection(exc)
        if not _delivery.intake_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP webhook intake is disabled",
                )
            )

        try:
            parsed = json.loads(
                verified.payload_bytes.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonfinite_json,
            )
            if not isinstance(parsed, dict):
                raise ValueError("webhook payload must be a JSON object")
            raw_payload: dict[str, Any] = parsed
        except Exception as exc:
            logger.warning(
                "Gateway webhook: verified body parse failed platform=%s error=%s",
                normalized_platform,
                exc.__class__.__name__,
            )
            return JSONResponse(
                status_code=400,
                content={"status": "rejected", "reason_code": "GW_BODY_INVALID", "detail": "verified body is not valid JSON"},
            )

        if normalized_platform == Platform.FEISHU.value and "challenge" in raw_payload:
            claims = dict(verified.claims or {})
            challenge = raw_payload.get("challenge")
            if (
                claims.get("authenticated_decrypt") is not True
                or claims.get("verification_token_verified") is not True
                or not isinstance(challenge, str)
                or not challenge
                or len(challenge) > 4_096
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "status": "rejected",
                        "reason_code": "GW_VERIFICATION_FAILED",
                        "detail": "Feishu challenge is not fully authenticated",
                    },
                )
            return JSONResponse({"challenge": challenge})

        if normalized_platform == Platform.QQ.value:
            logger.info(
                "Gateway webhook verified platform=%s post_type=%s message_type=%s body_sha256=%s",
                normalized_platform,
                raw_payload.get("post_type"),
                raw_payload.get("message_type"),
                verified.body_sha256[:16],
            )

        try:
            inbound_items = (
                _line_event_payloads(raw_payload)
                if normalized_platform == Platform.LINE.value
                else [(None, raw_payload)]
            )
        except ValueError:
            return JSONResponse(
                status_code=422,
                content={
                    "status": "rejected",
                    "reason_code": "GW_LINE_EVENTS_INVALID",
                    "detail": "LINE events must be a bounded array of objects",
                },
            )

        line_batch = normalized_platform == Platform.LINE.value and "events" in raw_payload
        if line_batch and not inbound_items:
            return JSONResponse({
                "status": "ignored",
                "reason_code": "GW_LINE_EMPTY_BATCH",
                "platform": normalized_platform,
                "event_count": 0,
                "processed_count": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "ignored_count": 0,
                "filtered_count": 0,
                "transport_mode": _transport_mode.value,
            })

        # Parse and normalize every event before the first durable write. This
        # avoids acknowledging only the first LINE event and avoids predictable
        # adapter/shape failures after a partially persisted package.
        prepared: list[tuple[int | None, dict[str, Any], Any]] = []
        ignored_count = 0
        filtered_count = 0
        try:
            for batch_index, event_payload in inbound_items:
                msg = adapter.parse_inbound(event_payload)
                if msg is None:
                    ignored_count += 1
                    continue

                # Durable HTTP intake must reach the authoritative inbox on
                # every retry; the in-memory L1 dedupe is non-HTTP only.
                # HTTP 重试的幂等性由 inbox 的身份键保证；这里不能提前以进程
                # 内缓存丢弃，否则重启或并发入口会造成不可证明的确认差异。
                normalized = _normalizer.normalize(msg, deduplicate=False)
                if normalized is None:
                    filtered_count += 1
                    if normalized_platform == Platform.QQ.value:
                        logger.info(
                            "Gateway webhook filtered platform=%s body_sha256=%s",
                            normalized_platform,
                            verified.body_sha256[:16],
                        )
                    continue
                prepared.append((batch_index, event_payload, normalized))
        except Exception as exc:
            logger.error(
                "Gateway webhook: 适配器解析异常 platform=%s error=%s",
                normalized_platform,
                exc.__class__.__name__,
            )
            return JSONResponse(
                status_code=422,
                content={"status": "rejected", "reason_code": "GW_ADAPTER_PARSE_FAILED", "detail": "adapter parse failed"},
            )

        if not prepared:
            if not line_batch:
                return JSONResponse({
                    "status": "filtered" if filtered_count else "ignored",
                })
            return JSONResponse({
                "status": "ignored",
                "reason_code": "GW_LINE_NO_PROCESSABLE_EVENTS",
                "platform": normalized_platform,
                "event_count": len(inbound_items),
                "processed_count": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "ignored_count": ignored_count,
                "filtered_count": filtered_count,
                "transport_mode": _transport_mode.value,
            })

        safe_headers = _safe_header_snapshot(_raw_header_items(request))
        receipts: list[tuple[Any, Any]] = []
        for batch_index, event_payload, normalized in prepared:
            platform_event_id = _platform_event_id(
                platform=normalized_platform,
                payload=event_payload,
                normalized_msg_id=str(normalized.msg_id or ""),
            )
            event_identity = platform_event_id or (
                f"body:{verified.body_sha256}:index:{batch_index}"
                if batch_index is not None
                else f"body:{verified.body_sha256}"
            )
            try:
                # 事件身份优先使用平台 ID；仅在其缺失时退回已验证 body 摘要，
                # 让相同 webhook 重投映射到同一持久收据而不是再次执行。
                receipt = _delivery.accept_inbound(
                    platform=normalized_platform,
                    principal_id=str(verified.principal_id),
                    tenant_id="local",
                    idempotency_key=f"{normalized_platform}:event:{event_identity}",
                    raw_body=raw_body,
                    normalized_payload=_safe_inbound_delivery_payload(
                        normalized.as_report_payload()
                    ),
                    safe_headers=safe_headers,
                    secure_claim_ref=None,
                    verification_audit_ref=f"gateway-webhook-verification://{verified.body_sha256}",
                    platform_event_id=platform_event_id,
                    ordering_key=str(normalized.conversation_id or "") or None,
                    source_sequence=_source_sequence(
                        platform=normalized_platform,
                        payload=event_payload,
                        batch_index=batch_index,
                    ),
                )
            except GatewayDeliveryError as exc:
                return _delivery_rejection(exc)

            if receipt.status == "CONFLICT":
                # 同一幂等键对应不同内容是安全冲突，不可把新内容当重试接受。
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "rejected",
                        "reason_code": receipt.reason_code or "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT",
                        "inbox_id": receipt.record_id,
                        "failed_event_index": batch_index,
                    },
                )
            if receipt.status not in {"NEW", "DUPLICATE_SAME"}:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "rejected",
                        "reason_code": "GW_TRANSPORT_ACCEPTANCE_UNCONFIRMED",
                        "detail": "durable inbox did not confirm acceptance",
                        "failed_event_index": batch_index,
                    },
                )
            receipts.append((normalized, receipt))

        accepted_count = sum(receipt.status == "NEW" for _, receipt in receipts)
        duplicate_count = sum(
            receipt.status == "DUPLICATE_SAME" for _, receipt in receipts
        )
        if line_batch:
            first_normalized, first_receipt = receipts[0]
            return JSONResponse({
                "status": "accepted" if accepted_count else "duplicate",
                "platform": normalized_platform,
                "event_count": len(inbound_items),
                "processed_count": len(receipts),
                "accepted_count": accepted_count,
                "duplicate_count": duplicate_count,
                "ignored_count": ignored_count,
                "filtered_count": filtered_count,
                "msg_id": first_normalized.msg_id if len(receipts) == 1 else None,
                "inbox_id": first_receipt.record_id if len(receipts) == 1 else None,
                "inbox_ids": [receipt.record_id for _, receipt in receipts],
                "transport_mode": _transport_mode.value,
            })

        normalized, receipt = receipts[0]
        return JSONResponse({
            "status": "duplicate" if duplicate_count else "accepted",
            "msg_id": normalized.msg_id,
            "platform": normalized_platform,
            "inbox_id": receipt.record_id,
            "ack_ref": receipt.ack_ref,
            "transport_mode": _transport_mode.value,
        })

    @app.get("/v1/gateway/wecom/webhook", response_class=PlainTextResponse)
    async def wecom_url_verification(request: Request) -> Any:
        """Complete WeCom URL verification only after authenticated decrypt."""

        if Platform.WECOM.value not in _adapters:
            return JSONResponse(
                status_code=404,
                content={"status": "rejected", "reason_code": "GW_VERIFIER_UNAVAILABLE"},
            )
        if _delivery is None or not _delivery.verification_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP webhook intake is disabled",
                )
            )
        try:
            verified = _verified_request(
                registry=webhook_verifiers,
                platform=Platform.WECOM.value,
                request=request,
                raw_body=b"",
            )
        except WebhookVerificationError as exc:
            return _security_rejection(exc)
        if not _delivery.intake_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP webhook intake is disabled",
                )
            )
        claims = dict(verified.claims or {})
        if (
            claims.get("url_verification") is not True
            or claims.get("authenticated_decrypt") is not True
            or claims.get("receive_id_verified") is not True
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "status": "rejected",
                    "reason_code": "GW_VERIFICATION_FAILED",
                    "detail": "WeCom URL verification is not fully authenticated",
                },
            )
        try:
            echo = verified.payload_bytes.decode("utf-8")
        except UnicodeDecodeError:
            echo = ""
        if not echo or len(echo) > 4_096 or any(char in echo for char in "\r\n"):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "rejected",
                    "reason_code": "GW_BODY_INVALID",
                    "detail": "decrypted WeCom echostr is invalid",
                },
            )
        return PlainTextResponse(echo)

    # ------------------------------------------------------------------
    # POST /v1/gateway/reply — 接收 Host Brain 的回复指令
    # ------------------------------------------------------------------

    @app.post("/v1/gateway/reply/query")
    async def query_reply(request: Request) -> JSONResponse:
        """Reopen one exact durable reply through the authenticated surface.

        This endpoint never enqueues, claims, retries or sends an outbox row.
        A missing row is intentionally UNKNOWN rather than negative proof,
        because the original reply request may still be crossing intake after
        the Host lost its HTTP response.
        """

        if _delivery is None or not _delivery.verification_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable Gateway reply query is disabled",
                )
            )
        raw_body = await request.body()
        if not raw_body or len(raw_body) > _MAX_HTTP_BODY_BYTES:
            return JSONResponse(
                status_code=413 if raw_body else 400,
                content={
                    "status": "rejected",
                    "reason_code": "GW_BODY_INVALID",
                    "detail": "query body is empty or too large",
                },
            )
        # 查询使用独立的内部认证面；它只观察确定的 durable 快照，绝不顺带
        # 认领、重试或发送，避免恢复检查本身制造新的外部副作用。
        try:
            verified = _verified_request(
                registry=reply_verifiers,
                platform="internal_reply",
                request=request,
                raw_body=raw_body,
            )
        except WebhookVerificationError as exc:
            return _security_rejection(exc)
        try:
            parsed = json.loads(
                verified.payload_bytes.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonfinite_json,
            )
            required = {
                "schema_version",
                "effect_id",
                "delivery_id",
                "platform",
                "conversation_id",
                "payload_digest",
            }
            if not isinstance(parsed, dict) or set(parsed) != required:
                raise ValueError("reply query shape is invalid")
            if parsed.get("schema_version") != GATEWAY_REPLY_QUERY_SCHEMA_VERSION:
                raise ValueError("reply query schema is invalid")
            platform = parsed.get("platform")
            conversation_id = parsed.get("conversation_id")
            effect_id = parsed.get("effect_id")
            delivery_id = parsed.get("delivery_id")
            payload_digest = parsed.get("payload_digest")
            if (
                not isinstance(platform, str)
                or platform != platform.strip().lower()
                or not isinstance(conversation_id, str)
                or conversation_id != conversation_id.strip()
                or not isinstance(effect_id, str)
                or _IDEMPOTENCY_TOKEN_RE.fullmatch(effect_id) is None
                or not isinstance(delivery_id, str)
                or _IDEMPOTENCY_TOKEN_RE.fullmatch(delivery_id) is None
                or not isinstance(payload_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", payload_digest) is None
            ):
                raise ValueError("reply query identity is invalid")
            Platform(platform)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "rejected",
                    "reason_code": "GW_REPLY_QUERY_INVALID",
                    "detail": "invalid authenticated reply query",
                },
            )
        try:
            snapshot = _delivery.query_reply(
                tenant_id="local",
                principal_id=str(verified.principal_id),
                effect_id=effect_id,
                delivery_id=delivery_id,
                platform=platform,
                conversation_id=conversation_id,
                payload_digest=payload_digest,
            )
        except GatewayDeliveryError as exc:
            return _delivery_rejection(exc)
        effect_status = str(snapshot.get("effect_status") or "UNKNOWN")
        return JSONResponse(
            {
                "status": (
                    "committed" if effect_status == "COMMITTED" else "unknown"
                ),
                **snapshot,
            }
        )

    @app.post("/v1/gateway/reply")
    async def receive_reply(request: Request) -> JSONResponse:
        """Authenticate and durably enqueue one Host Brain reply."""
        if _outbound is None:
            raise HTTPException(
                status_code=503,
                detail="OutboundDispatcher not configured (Phase 2)",
            )
        if _delivery is None or not _delivery.verification_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP reply intake is disabled",
                )
            )
        raw_body = await request.body()
        if not raw_body or len(raw_body) > _MAX_HTTP_BODY_BYTES:
            return JSONResponse(
                status_code=413 if raw_body else 400,
                content={"status": "rejected", "reason_code": "GW_BODY_INVALID", "detail": "reply body is empty or too large"},
            )
        # 在 outbox 写入前完成验签、严格 JSON 解析与敏感元数据净化；这样被
        # 拒绝的请求可证明没有触发任何平台适配器。
        try:
            verified = _verified_request(
                registry=reply_verifiers,
                platform="internal_reply",
                request=request,
                raw_body=raw_body,
            )
        except WebhookVerificationError as exc:
            return _security_rejection(exc)
        if not _delivery.intake_enabled:
            return _delivery_rejection(
                GatewayDeliveryError(
                    GW_TRANSPORT_DISABLED,
                    "durable HTTP reply intake is disabled",
                )
            )

        try:
            parsed = json.loads(
                verified.payload_bytes.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonfinite_json,
            )
            if not isinstance(parsed, dict):
                raise ValueError("reply payload must be an object")
            payload = sanitize_reply_payload(parsed)
            platform = Platform(str(payload.get("platform") or "").strip().lower())
            MsgType(str(payload.get("msg_type") or "text").strip().lower())
            if not str(payload.get("conversation_id") or "").strip():
                raise ValueError("conversation_id is required")
        except ReplyPayloadRejected as exc:
            return _delivery_rejection(exc, status_code=400)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"status": "rejected", "reason_code": "GW_REPLY_PAYLOAD_INVALID", "detail": "invalid reply payload"},
            )

        wait_result_raw = payload.get("wait_result", True)
        if not isinstance(wait_result_raw, bool):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "rejected",
                    "reason_code": "GW_REPLY_PAYLOAD_INVALID",
                    "detail": "wait_result must be a boolean",
                },
            )
        wait_result = wait_result_raw
        timeout_ms_raw = payload.get("timeout_ms", 22000)
        try:
            if isinstance(timeout_ms_raw, bool):
                raise ValueError("boolean timeout")
            timeout_ms = min(60_000, max(1_000, int(timeout_ms_raw)))
        except (TypeError, ValueError):
            timeout_ms = 22000

        try:
            durable_payload = {
                "request_id": str(payload.get("request_id") or "").strip(),
                "delivery_id": str(payload.get("delivery_id") or "").strip(),
                "platform": platform.value,
                "conversation_id": str(payload.get("conversation_id") or "").strip(),
                "msg_type": str(payload.get("msg_type") or "text").strip().lower(),
                "content": str(payload.get("content") or ""),
                "media_path": payload.get("media_path"),
                "reply_to_msg_id": payload.get("reply_to_msg_id"),
                "raw_extra": dict(payload.get("raw_extra") or {}),
            }
            effect_id_raw = payload.get("effect_id")
            if effect_id_raw is not None:
                if (
                    not isinstance(effect_id_raw, str)
                    or _IDEMPOTENCY_TOKEN_RE.fullmatch(effect_id_raw) is None
                ):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "status": "rejected",
                            "reason_code": "GW_REPLY_EFFECT_ID_INVALID",
                            "detail": "authenticated reply effect_id is invalid",
                        },
                    )
                durable_payload["effect_id"] = effect_id_raw
            request_id = str(durable_payload.get("request_id") or "").strip()
            if _IDEMPOTENCY_TOKEN_RE.fullmatch(request_id) is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "rejected",
                        "reason_code": "GW_REPLY_IDEMPOTENCY_KEY_INVALID",
                        "detail": "authenticated reply request_id is required",
                    },
                )
            delivery_id = str(durable_payload.get("delivery_id") or request_id).strip()
            if _IDEMPOTENCY_TOKEN_RE.fullmatch(delivery_id) is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "rejected",
                        "reason_code": "GW_REPLY_IDEMPOTENCY_KEY_INVALID",
                        "detail": "authenticated reply delivery_id is invalid",
                    },
                )
            durable_payload["delivery_id"] = delivery_id
            receipt = _delivery.enqueue_reply(
                tenant_id="local",
                principal_id=str(verified.principal_id),
                idempotency_key=f"gateway-reply:{delivery_id}",
                payload=durable_payload,
                ordering_key=f"{platform.value}:{durable_payload['conversation_id']}",
            )
        except (GatewayDeliveryError, ReplyPayloadRejected) as exc:
            conflict = "CONFLICT" in str(getattr(exc, "reason_code", ""))
            return _delivery_rejection(exc, status_code=409 if conflict else 503)

        duplicate = receipt.status == "DUPLICATE_SAME"
        canonical_outbox = _delivery.get_outbox(receipt.record_id) or {}
        canonical_payload_digest = str(
            canonical_outbox.get("payload_sha256") or ""
        ).strip()
        if not canonical_payload_digest.startswith("sha256:"):
            # Intake may already have crossed SQLite commit.  Returning a
            # positive response without its immutable payload binding would
            # make Host-side attribution unsafe.
            return JSONResponse(
                status_code=500,
                content={
                    "status": "in_doubt",
                    "reason_code": "GW_REPLY_OUTBOX_ATTRIBUTION_MISSING",
                    "outbox_id": receipt.record_id,
                },
            )
        durable_identity = {
            "delivery_id": delivery_id,
            "payload_digest": canonical_payload_digest,
        }
        if duplicate:
            # 同一 delivery_id 的重放只读取首个持久行；不再启动第二个等待者
            # 或新的发送尝试，保持 Host 可重试而出站至多一次由 worker 主张。
            # Replayed proof/business requests must not allocate another
            # long-poll thread. Return the authoritative cached state while
            # the original worker owns any in-flight delivery.
            row = _delivery.get_outbox(receipt.record_id) or {}
            state = str(row.get("status") or "READY").strip().upper()
            if state in {"DLQ", "DEAD_LETTER"}:
                return JSONResponse({
                    "status": "failed",
                    "duplicate": True,
                    **durable_identity,
                    "outbox_id": receipt.record_id,
                    "conversation_id": durable_payload["conversation_id"],
                    "platform": platform.value,
                    "reason_code": str(row.get("last_reason_code") or "GW_REPLY_DLQ"),
                })
            return JSONResponse({
                "status": "sent" if state in {"ACKED", "DELIVERED", "SENT"} else "queued",
                "duplicate": True,
                **durable_identity,
                "outbox_id": receipt.record_id,
                "conversation_id": durable_payload["conversation_id"],
                "platform": platform.value,
                "delivery_state": state,
            })
        if not wait_result:
            return JSONResponse({
                "status": "queued",
                "duplicate": False,
                **durable_identity,
                "outbox_id": receipt.record_id,
                "conversation_id": durable_payload["conversation_id"],
                "platform": platform.value,
            })

        # SQLite 等待是阻塞操作，移到线程避免占用 ASGI 事件循环；超时只表示
        # 当前尚无终态，响应仍诚实地返回 queued 而非伪造发送成功。
        row = await asyncio.to_thread(
            _delivery.wait_outbox,
            receipt.record_id,
            timeout_ms=timeout_ms,
        )
        state = str((row or {}).get("status") or "READY").strip().upper()
        if state in {"DLQ", "DEAD_LETTER"}:
            return JSONResponse({
                "status": "failed",
                **durable_identity,
                "outbox_id": receipt.record_id,
                "conversation_id": durable_payload["conversation_id"],
                "platform": platform.value,
                "reason_code": str((row or {}).get("last_reason_code") or "GW_REPLY_DLQ"),
            })
        return JSONResponse({
            "status": "sent" if state in {"ACKED", "DELIVERED", "SENT"} else "queued",
            **durable_identity,
            "outbox_id": receipt.record_id,
            "duplicate": duplicate,
            "conversation_id": durable_payload["conversation_id"],
            "platform": platform.value,
            "delivery_state": state,
        })

    # ------------------------------------------------------------------
    # GET /v1/gateway/health — 健康检查
    # ------------------------------------------------------------------

    @app.get("/v1/gateway/health")
    async def health() -> JSONResponse:
        """Return a constant-size public liveness view without PII/config."""

        transport_stats = _delivery.stats() if _delivery is not None else {
            "mode": _transport_mode.value,
            "intake_enabled": False,
            "verification_enabled": False,
            "delivery_enabled": False,
            "worker_alive": False,
        }
        safe_transport = {
            key: transport_stats.get(key)
            for key in (
                "mode",
                "intake_enabled",
                "verification_enabled",
                "delivery_enabled",
                "worker_alive",
            )
        }

        return JSONResponse({
            "status": "ok",
            "uptime_s": round(time.time() - _GATEWAY_START_TIME, 1),
            "adapters": {"registered_count": len(_adapters)},
            "outbound_ready": _outbound is not None,
            "transport": safe_transport,
        })

    # ------------------------------------------------------------------
    # GET /v1/gateway/stats — 统计信息
    # ------------------------------------------------------------------

    @app.get("/v1/gateway/stats")
    async def stats() -> JSONResponse:
        """Detailed stats require the TASK-0494 authenticated admin surface."""

        return JSONResponse(
            status_code=503,
            content={
                "status": "rejected",
                "reason_code": "GW_ADMIN_AUTH_UNAVAILABLE",
                "detail": "authenticated Gateway stats surface is not installed",
            },
        )

    return app
