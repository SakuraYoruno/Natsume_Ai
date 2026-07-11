"""Edge → Host 输入上报客户端。

负责将 Edge 端的两类输入源上报给 Host Brain：
  - ASR 语音识别结果（source=ASR）
  - 微信小号接收到的消息（source=WECHAT）

设计原则：
  - 同步 HTTP POST，失败静默降级（不阻断 ASR/微信主流程）
  - 支持 HMAC 签名（复用 EdgeConfig.hmac_shared_key）
  - 上报失败时写入本地缓存，供后续重试
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.channels.input_reporter")


class InputSourceType(str, Enum):
    """输入来源类型。"""
    ASR = "ASR"           # 语音识别
    WECHAT = "WECHAT"     # 微信小号消息
    GATEWAY = "GATEWAY"   # 统一消息网关（微信/QQ/飞书/企微等外部平台）


@dataclass(frozen=True)
class InputReportResult:
    """上报结果。"""
    success: bool
    source: InputSourceType
    reason_code: str | None
    detail: str
    cached: bool = False  # True 表示已写入本地缓存等待重试
    receipt_ref: str | None = None
    receipt_body_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "cached": self.cached,
            "receipt_ref": self.receipt_ref,
            "receipt_body_sha256": self.receipt_body_sha256,
        }


@dataclass
class _PendingReport:
    """待重试的上报记录。"""
    payload: dict[str, Any]
    source: InputSourceType
    retry_count: int = 0
    created_at: float = field(default_factory=time.monotonic)
    created_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    body_b64: str | None = None
    idempotency_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "source": self.source.value,
            "retry_count": int(self.retry_count),
            "created_at": float(self.created_at),
            "created_at_epoch_ms": int(self.created_at_epoch_ms),
            "body_b64": self.body_b64,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "_PendingReport | None":
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            return None
        try:
            source = InputSourceType(str(raw.get("source") or "GATEWAY").strip().upper())
        except Exception:
            source = InputSourceType.GATEWAY
        try:
            retry_count = max(0, int(raw.get("retry_count") or 0))
        except Exception:
            retry_count = 0
        try:
            created_at = float(raw.get("created_at") or time.monotonic())
        except Exception:
            created_at = time.monotonic()
        try:
            created_at_epoch_ms = int(raw.get("created_at_epoch_ms") or (time.time() * 1000))
        except Exception:
            created_at_epoch_ms = int(time.time() * 1000)
        body_b64 = str(raw.get("body_b64") or "").strip() or None
        idempotency_key = str(raw.get("idempotency_key") or "").strip() or None
        return cls(
            payload=dict(payload),
            source=source,
            retry_count=retry_count,
            created_at=created_at,
            created_at_epoch_ms=created_at_epoch_ms,
            body_b64=body_b64,
            idempotency_key=idempotency_key,
        )


class EdgeInputReporter:
    """Edge → Host 输入上报客户端。

    线程安全，支持失败缓存和后台重试。

    Args:
        host_url: Host Brain 地址，如 "http://192.168.1.100:8000"
        hmac_key: HMAC 签名密钥（来自 EdgeConfig.hmac_shared_key），None 时跳过签名
        timeout_s: HTTP 请求超时秒数
        max_cache: 本地缓存最大条数（超出时丢弃最旧的）
        max_retry: 最大重试次数
        retry_interval_s: 后台重试间隔秒数
    """

    INPUT_ENDPOINT = "/v1/host/input"

    def __init__(
        self,
        *,
        host_url: str,
        hmac_key: str | None = None,
        timeout_s: float = 3.0,
        max_cache: int = 500,
        max_retry: int = 8640,
        retry_interval_s: float = 10.0,
        cache_file: str | None = None,
        secure_principal_id: str | None = None,
        secure_tenant_id: str | None = None,
        secure_key_id: str | None = None,
        secure_audience: str | None = None,
        secure_scope: str | None = None,
    ) -> None:
        self._host_url = host_url.rstrip("/")
        self._hmac_key = hmac_key
        self._timeout_s = float(timeout_s)
        self._max_cache = int(max_cache)
        self._max_retry = int(max_retry)
        self._retry_interval_s = float(retry_interval_s)
        self._cache_file = Path(cache_file).expanduser() if cache_file else None
        self._legacy_dlq_file = (
            self._cache_file.with_suffix(self._cache_file.suffix + ".dlq.jsonl")
            if self._cache_file is not None
            else None
        )
        self._secure_principal_id = str(
            secure_principal_id
            or os.environ.get("EDGE_GATEWAY_HOST_PRINCIPAL_ID")
            or "service.edge.gateway"
        ).strip()
        self._secure_tenant_id = str(
            secure_tenant_id
            or os.environ.get("EDGE_GATEWAY_HOST_TENANT_ID")
            or "local"
        ).strip()
        self._secure_key_id = str(
            secure_key_id
            or os.environ.get("EDGE_GATEWAY_HOST_KEY_ID")
            or "edge.gateway.hmac.v1"
        ).strip()
        self._secure_audience = str(
            secure_audience
            or os.environ.get("EDGE_GATEWAY_HOST_AUDIENCE")
            or "host"
        ).strip()
        self._secure_scope = str(
            secure_scope
            or os.environ.get("EDGE_GATEWAY_HOST_SCOPE")
            or "host:write"
        ).strip()

        self._cache: list[_PendingReport] = []
        self._lock = threading.Lock()
        self._retry_thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._total_sent = 0
        self._total_failed = 0
        self._last_result: dict[str, Any] | None = None
        self._last_payload_summary: dict[str, Any] | None = None
        self._last_persist_error: str = ""
        self._last_dlq_error: str = ""
        self._legacy_dlq_count = 0
        self._retry_inflight_ids: set[int] = set()
        self._load_cache_from_disk()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def report_asr(
        self,
        *,
        session_id: str,
        text: str,
        confidence: float,
        is_final: bool,
        seq: int,
        ts_ms: int | None = None,
        alternates: list[dict[str, Any]] | None = None,
        evidence_ref: str | None = None,
    ) -> InputReportResult:
        """上报 ASR 识别结果到 Host Brain。"""
        payload: dict[str, Any] = {
            "source": InputSourceType.ASR.value,
            "session_id": session_id,
            "text": text,
            "confidence": float(confidence),
            "is_final": bool(is_final),
            "seq": int(seq),
            "ts_ms": int(ts_ms or (time.time() * 1000)),
            "alternates": alternates or [],
        }
        if evidence_ref:
            payload["evidence_ref"] = evidence_ref
        return self._send(payload, InputSourceType.ASR)

    def report_wechat(
        self,
        *,
        from_wxid: str,
        to_wxid: str,
        content: str,
        msg_type: str = "TEXT",
        msg_id: str | None = None,
        room_wxid: str | None = None,
        ts_ms: int | None = None,
        raw_extra: dict[str, Any] | None = None,
    ) -> InputReportResult:
        """上报微信小号接收到的消息到 Host Brain。"""
        payload: dict[str, Any] = {
            "source": InputSourceType.WECHAT.value,
            "from_wxid": from_wxid,
            "to_wxid": to_wxid,
            "content": content,
            "msg_type": msg_type,
            "ts_ms": int(ts_ms or (time.time() * 1000)),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        if room_wxid:
            payload["room_wxid"] = room_wxid
        if raw_extra:
            payload["raw_extra"] = raw_extra
        return self._send(payload, InputSourceType.WECHAT)

    def report_gateway(
        self,
        *,
        platform: str,
        conversation_id: str,
        content: str,
        from_id: str = "",
        to_id: str = "",
        msg_type: str = "text",
        msg_id: str | None = None,
        is_group: bool | None = None,
        sender_name: str | None = None,
        media_url: str | None = None,
        reply_to_msg_id: str | None = None,
        ts_ms: int | None = None,
        raw_extra: dict[str, Any] | None = None,
    ) -> InputReportResult:
        payload: dict[str, Any] = {
            "source": InputSourceType.GATEWAY.value,
            "platform": str(platform or "").strip().lower(),
            "conversation_id": str(conversation_id or "").strip(),
            "content": str(content or ""),
            "msg_type": str(msg_type or "text").strip().lower(),
            "ts_ms": int(ts_ms or (time.time() * 1000)),
        }
        from_id_value = str(from_id or "").strip()
        to_id_value = str(to_id or "").strip()
        if from_id_value:
            payload["from_id"] = from_id_value
        if to_id_value:
            payload["to_id"] = to_id_value
        if msg_id:
            payload["msg_id"] = str(msg_id).strip()
        if is_group is not None:
            payload["is_group"] = bool(is_group)
        sender_name_value = str(sender_name or "").strip()
        if sender_name_value:
            payload["sender_name"] = sender_name_value
        media_url_value = str(media_url or "").strip()
        if media_url_value:
            payload["media_url"] = media_url_value
        reply_to_msg_id_value = str(reply_to_msg_id or "").strip()
        if reply_to_msg_id_value:
            payload["reply_to_msg_id"] = reply_to_msg_id_value
        if raw_extra:
            payload["raw_extra"] = raw_extra
        return self._send(payload, InputSourceType.GATEWAY)

    def report_gateway_payload(self, payload: dict[str, Any]) -> InputReportResult:
        """Compatibility entrypoint for non-HTTP watcher/MTProto Gateway paths."""

        normalized = dict(payload)
        normalized["source"] = InputSourceType.GATEWAY.value
        return self._send(normalized, InputSourceType.GATEWAY)

    def start_retry_loop(self) -> None:
        """启动后台重试线程（可选，用于缓存重试）。"""
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._shutdown.clear()
        self._retry_thread = threading.Thread(
            target=self._retry_loop,
            name="edge-input-reporter-retry",
            daemon=True,
        )
        self._retry_thread.start()
        logger.info("EdgeInputReporter: 后台重试线程已启动")

    def stop_retry_loop(self) -> None:
        """停止后台重试线程。"""
        self._shutdown.set()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=5.0)
        logger.info("EdgeInputReporter: 后台重试线程已停止")

    def cache_size(self) -> int:
        """返回当前缓存条数。"""
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """Return a small observability snapshot for gateway health checks."""
        with self._lock:
            return {
                "host_url": self._host_url,
                "input_endpoint": self.INPUT_ENDPOINT,
                "timeout_s": self._timeout_s,
                "max_retry": self._max_retry,
                "retry_interval_s": self._retry_interval_s,
                "cache_size": len(self._cache),
                "cache_file": str(self._cache_file or ""),
                "legacy_dlq_file": str(self._legacy_dlq_file or ""),
                "legacy_dlq_count": self._legacy_dlq_count,
                "last_persist_error": self._last_persist_error,
                "last_dlq_error": self._last_dlq_error,
                "total_sent": self._total_sent,
                "total_failed": self._total_failed,
                "last_result": dict(self._last_result or {}),
                "last_payload": dict(self._last_payload_summary or {}),
                "secure_principal_id": self._secure_principal_id,
                "secure_key_id": self._secure_key_id,
                "secure_auth_enabled": bool(self._hmac_key),
            }

    def deliver_gateway_payload(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> InputReportResult:
        """Perform one durable inbox delivery attempt without local recaching.

        The central Gateway inbox owns retry/DLQ for this path.  Exact payload
        bytes are frozen for the attempt while identity proof metadata and nonce
        may be refreshed on a later lease attempt.
        """

        source = InputSourceType.GATEWAY
        body = self._serialize_payload(payload)
        stable_idempotency_key = self._host_idempotency_key(idempotency_key, body)
        started = time.monotonic()
        try:
            receipt = self._do_http_post_frozen(
                payload,
                body=body,
                idempotency_key=stable_idempotency_key,
                require_durable_receipt=True,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._record_result(
                payload,
                source,
                success=True,
                cached=False,
                reason_code=None,
                detail="durable inbox delivered",
                elapsed_ms=elapsed_ms,
            )
            return InputReportResult(
                True,
                source,
                None,
                "durable inbox delivered",
                False,
                receipt_ref=str((receipt or {}).get("receipt_ref") or "") or None,
                receipt_body_sha256=(
                    str((receipt or {}).get("body_sha256") or "") or None
                ),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            reason_code = self._classify_durable_delivery_failure(exc)
            self._record_result(
                payload,
                source,
                success=False,
                cached=False,
                reason_code=reason_code,
                detail=str(exc),
                elapsed_ms=elapsed_ms,
            )
            return InputReportResult(
                False,
                source,
                reason_code,
                str(exc),
                False,
            )

    @staticmethod
    def _classify_durable_delivery_failure(exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return (
                "INPUT_REPORT_DELIVERY_UNCERTAIN"
                if int(getattr(exc, "code", 0) or 0) >= 500
                else "INPUT_REPORT_REJECTED"
            )
        if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError, BrokenPipeError)):
            return "INPUT_REPORT_DELIVERY_UNCERTAIN"
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout, ConnectionResetError, BrokenPipeError)):
                return "INPUT_REPORT_DELIVERY_UNCERTAIN"
            return "INPUT_REPORT_CONNECT_FAILED"
        if isinstance(exc, RuntimeError) and "Host rejected input" in str(exc):
            return "INPUT_REPORT_REJECTED"
        return "INPUT_REPORT_DELIVERY_UNCERTAIN"

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _send(self, payload: dict[str, Any], source: InputSourceType) -> InputReportResult:
        """发送 HTTP POST，失败时写入缓存。"""
        started = time.monotonic()
        try:
            self._do_http_post(payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._record_result(
                payload,
                source,
                success=True,
                cached=False,
                reason_code=None,
                detail="",
                elapsed_ms=elapsed_ms,
            )
            return InputReportResult(
                success=True,
                source=source,
                reason_code=None,
                detail=edge_runtime_text(
                    "input_reporter",
                    "report_success",
                    default="",
                ),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "EdgeInputReporter: 上报失败（source=%s），写入缓存: %s",
                source.value, exc,
            )
            frozen_body = self._serialize_payload(payload)
            self._cache_report(
                payload,
                source,
                body=frozen_body,
                idempotency_key=self._host_idempotency_key(None, frozen_body),
            )
            self._record_result(
                payload,
                source,
                success=False,
                cached=True,
                reason_code="INPUT_REPORT_FAILED",
                detail=str(exc),
                elapsed_ms=elapsed_ms,
            )
            return InputReportResult(
                success=False,
                source=source,
                reason_code="INPUT_REPORT_FAILED",
                detail=str(exc),
                cached=True,
            )

    def _record_result(
        self,
        payload: dict[str, Any],
        source: InputSourceType,
        *,
        success: bool,
        cached: bool,
        reason_code: str | None,
        detail: str,
        elapsed_ms: int,
    ) -> dict[str, Any] | None:
        now_ms = int(time.time() * 1000)
        with self._lock:
            if success:
                self._total_sent += 1
            else:
                self._total_failed += 1
            self._last_payload_summary = self._summarize_payload(payload)
            self._last_result = {
                "success": bool(success),
                "cached": bool(cached),
                "source": source.value,
                "reason_code": reason_code,
                "detail": str(detail or "")[:240],
                "elapsed_ms": int(elapsed_ms),
                "ts_ms": now_ms,
            }

    @staticmethod
    def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        content = str(payload.get("content") or payload.get("text") or "")
        summary = {
            "source": str(payload.get("source") or ""),
            "platform": str(payload.get("platform") or ""),
            "conversation_id": str(payload.get("conversation_id") or payload.get("session_id") or ""),
            "msg_id": str(payload.get("msg_id") or ""),
            "msg_type": str(payload.get("msg_type") or ""),
            "from_id": str(payload.get("from_id") or payload.get("from_wxid") or ""),
            "to_id": str(payload.get("to_id") or payload.get("to_wxid") or ""),
            "content_len": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        return {k: v for k, v in summary.items() if v not in ("", 0)}

    @staticmethod
    def _serialize_payload(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _host_idempotency_key(value: str | None, body: bytes) -> str:
        material = str(value or "").strip().encode("utf-8") or bytes(body)
        return f"edge-host:{hashlib.sha256(material).hexdigest()}"

    def _do_http_post(self, payload: dict[str, Any]) -> None:
        """执行 HTTP POST，带 HMAC 签名。"""
        body = self._serialize_payload(payload)
        self._do_http_post_frozen(
            payload,
            body=body,
            idempotency_key=self._host_idempotency_key(None, body),
        )

    def _do_http_post_frozen(
        self,
        payload: dict[str, Any],
        *,
        body: bytes,
        idempotency_key: str,
        require_durable_receipt: bool = False,
    ) -> dict[str, Any] | None:
        """POST exact frozen bytes while refreshing proof metadata per attempt."""
        headers: dict[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Edge-Source": "edge_input_reporter",
        }
        if self._hmac_key:
            headers.update(self._build_secure_ingress_headers(
                body=body,
                idempotency_key=idempotency_key,
            ))

        url = self._host_url + self.INPUT_ENDPOINT
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Host 返回非 2xx 状态码: {resp.status}")
            resp_body = resp.read()
            if not resp_body:
                if require_durable_receipt:
                    raise RuntimeError("durable Host receipt is empty")
                return None
            try:
                data = json.loads(resp_body.decode("utf-8", errors="strict"))
            except Exception as exc:
                if require_durable_receipt:
                    raise RuntimeError("durable Host receipt is invalid JSON") from exc
                return None
            if not isinstance(data, dict):
                if require_durable_receipt:
                    raise RuntimeError("durable Host receipt must be an object")
                return None
            status_value = str(data.get("status") or "").strip().upper()
            success_value = data.get("success")
            if (success_value is False) or (status_value in {"REJECTED", "ERROR"}):
                detail = str(data.get("detail") or "").strip()
                if detail:
                    raise RuntimeError(f"Host rejected input: {detail}")
                raise RuntimeError("Host rejected input")
            if require_durable_receipt:
                expected_source = str(payload.get("source") or "GATEWAY").strip().upper()
                expected_body_sha256 = hashlib.sha256(body).hexdigest()
                expected_idempotency_sha256 = hashlib.sha256(
                    str(idempotency_key).encode("utf-8")
                ).hexdigest()
                receipt_ref = str(data.get("receipt_ref") or "").strip()
                if (
                    success_value is not True
                    or status_value not in {"ACCEPTED", "COMPLETED", "IGNORED"}
                    or str(data.get("source") or "").strip().upper() != expected_source
                    or not receipt_ref.startswith("host-input-result://")
                    or str(data.get("body_sha256") or "").strip()
                    != expected_body_sha256
                    or str(data.get("idempotency_sha256") or "").strip()
                    != expected_idempotency_sha256
                ):
                    raise RuntimeError("durable Host receipt contract mismatch")
            return data

    def _build_secure_ingress_headers(
        self,
        *,
        body: bytes,
        idempotency_key: str,
    ) -> dict[str, str]:
        from host_brain.secure_ingress.credential import build_hmac_sha256_signature  # noqa: PLC0415
        from host_brain.secure_ingress.surfaces import (  # noqa: PLC0415
            IngressSurface,
            build_surface_request,
        )
        from natsume_contracts.identity import (  # noqa: PLC0415
            CredentialProofKind,
            CredentialProofMetadata,
            IdentitySchemaVersion,
            IngressIdentityEnvelope,
            Principal,
            PrincipalKind,
        )

        now_ms = int(time.time() * 1000)
        request_id = f"req:{uuid.uuid4().hex}"
        nonce = f"nonce:{uuid.uuid4().hex}"
        metadata = CredentialProofMetadata(
            kind=CredentialProofKind.HMAC_SHA256,
            key_id=self._secure_key_id,
            algorithm="hmac-sha256",
            proof_id=f"proof:{uuid.uuid4().hex}",
            issued_at_epoch_ms=now_ms,
            expires_at_epoch_ms=now_ms + 60_000,
        )
        identity = IngressIdentityEnvelope(
            schema_version=IdentitySchemaVersion.V1,
            principal=Principal(
                principal_id=self._secure_principal_id,
                kind=PrincipalKind.SERVICE,
                tenant_id=self._secure_tenant_id,
                granted_scopes=(self._secure_scope,),
            ),
            credential_proof=metadata,
            audience=self._secure_audience,
            request_id=request_id,
            requested_scopes=(self._secure_scope,),
        )
        secure_request = build_surface_request(
            surface=IngressSurface.HOST,
            identity=identity,
            credential=None,
            method="POST",
            normalized_path=self.INPUT_ENDPOINT,
            idempotency_key=idempotency_key,
            nonce=nonce,
            raw_body=bytes(body),
            received_at_epoch_ms=now_ms,
            transport_key=f"principal:{self._secure_principal_id}",
            correlation_id=request_id,
        )
        signature = build_hmac_sha256_signature(secure_request, self._hmac_key or "")
        identity_header = base64.urlsafe_b64encode(
            identity.to_canonical_json().encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Natsume-Identity": identity_header,
            "X-Natsume-Proof": signature,
            "X-Natsume-Idempotency-Key": idempotency_key,
            "X-Natsume-Nonce": nonce,
            "X-Natsume-Correlation-Id": request_id,
        }

    def _cache_report(
        self,
        payload: dict[str, Any],
        source: InputSourceType,
        *,
        body: bytes | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """Write local compatibility cache; overflow is archived before removal."""
        with self._lock:
            if len(self._cache) >= self._max_cache:
                drop_index = next(
                    (
                        index
                        for index, candidate in enumerate(self._cache)
                        if id(candidate) not in self._retry_inflight_ids
                    ),
                    None,
                )
                if drop_index is not None:
                    dropped = self._cache[drop_index]
                    if self._persist_legacy_dlq_locked(
                        dropped,
                        reason_code="INPUT_REPORT_CACHE_OVERFLOW",
                    ):
                        self._cache.pop(drop_index)
                        logger.warning(
                            "EdgeInputReporter: cache overflow moved oldest record to legacy DLQ source=%s",
                            dropped.source.value,
                        )
            frozen_body = bytes(body) if body is not None else self._serialize_payload(payload)
            self._cache.append(_PendingReport(
                payload=payload,
                source=source,
                body_b64=base64.b64encode(frozen_body).decode("ascii"),
                idempotency_key=(
                    str(idempotency_key).strip()
                    if idempotency_key
                    else self._host_idempotency_key(None, frozen_body)
                ),
            ))
            self._persist_cache_locked()

    def _retry_loop(self) -> None:
        """后台重试线程：定期尝试重发缓存中的上报。"""
        while not self._shutdown.wait(timeout=self._retry_interval_s):
            with self._lock:
                pending = list(self._cache)

            if not pending:
                continue

            for item in pending:
                if self._shutdown.is_set():
                    break
                with self._lock:
                    if not any(candidate is item for candidate in self._cache):
                        continue
                if item.retry_count >= self._max_retry:
                    moved_to_dlq = False
                    with self._lock:
                        if any(candidate is item for candidate in self._cache):
                            moved_to_dlq = self._persist_legacy_dlq_locked(
                                item,
                                reason_code="INPUT_REPORT_RETRY_EXHAUSTED",
                            )
                            if moved_to_dlq:
                                self._cache = [candidate for candidate in self._cache if candidate is not item]
                                self._persist_cache_locked()
                    if not moved_to_dlq:
                        continue
                    self._record_result(
                        item.payload,
                        item.source,
                        success=False,
                        cached=False,
                        reason_code="INPUT_REPORT_RETRY_EXHAUSTED",
                        detail=f"retry exhausted after {item.retry_count} attempts; moved to legacy DLQ",
                        elapsed_ms=0,
                    )
                    logger.warning(
                        "EdgeInputReporter: retry exhausted; moved record to legacy DLQ source=%s",
                        item.source.value,
                    )
                    continue
                with self._lock:
                    self._retry_inflight_ids.add(id(item))
                try:
                    started = time.monotonic()
                    try:
                        frozen_body = (
                            base64.b64decode(item.body_b64, validate=True)
                            if item.body_b64
                            else self._serialize_payload(item.payload)
                        )
                    except Exception as exc:
                        raise RuntimeError("persistent input body is invalid") from exc
                    self._do_http_post_frozen(
                        item.payload,
                        body=frozen_body,
                        idempotency_key=(
                            item.idempotency_key
                            or self._host_idempotency_key(None, frozen_body)
                        ),
                    )
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    self._record_result(
                        item.payload,
                        item.source,
                        success=True,
                        cached=False,
                        reason_code=None,
                        detail=f"retry_success retry={item.retry_count + 1}",
                        elapsed_ms=elapsed_ms,
                    )
                    logger.info(
                        "EdgeInputReporter: 重试成功 source=%s retry=%d",
                        item.source.value, item.retry_count + 1,
                    )
                    with self._lock:
                        self._cache = [candidate for candidate in self._cache if candidate is not item]
                        self._retry_inflight_ids.discard(id(item))
                        self._persist_cache_locked()
                except Exception as exc:
                    with self._lock:
                        if any(candidate is item for candidate in self._cache):
                            item.retry_count += 1
                            self._persist_cache_locked()
                        self._retry_inflight_ids.discard(id(item))
                    self._record_result(
                        item.payload,
                        item.source,
                        success=False,
                        cached=True,
                        reason_code="INPUT_REPORT_RETRY_FAILED",
                        detail=str(exc),
                        elapsed_ms=0,
                    )
                    logger.debug(
                        "EdgeInputReporter: 重试失败 source=%s retry=%d: %s",
                        item.source.value, item.retry_count, exc,
                    )

    def _load_cache_from_disk(self) -> None:
        if self._cache_file is None:
            return
        try:
            if not self._cache_file.exists():
                return
            raw_text = self._cache_file.read_text(encoding="utf-8").strip()
            if not raw_text:
                return
            raw_items = json.loads(raw_text)
            if not isinstance(raw_items, list):
                raise ValueError("persistent input queue must be a JSON array")
            loaded: list[_PendingReport] = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                item = _PendingReport.from_dict(raw_item)
                if item is not None:
                    loaded.append(item)
            # Never truncate recovered durable records.  The configured limit
            # controls new intake; overflow records are archived to DLQ first.
            self._cache = loaded
            if loaded:
                logger.warning(
                    "EdgeInputReporter: loaded %d pending input reports from %s",
                    len(loaded),
                    self._cache_file,
                )
        except Exception as exc:
            self._last_persist_error = str(exc)[:240]
            logger.warning("EdgeInputReporter: failed to load persistent input queue: %s", exc)

    def _persist_cache_locked(self) -> None:
        if self._cache_file is None:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_file.with_suffix(self._cache_file.suffix + ".tmp")
            payload = [item.as_dict() for item in self._cache]
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self._cache_file)
            self._last_persist_error = ""
        except Exception as exc:
            self._last_persist_error = str(exc)[:240]
            logger.warning("EdgeInputReporter: failed to persist input queue: %s", exc)

    def _persist_legacy_dlq_locked(
        self,
        item: _PendingReport,
        *,
        reason_code: str,
    ) -> bool:
        """Atomically append one exhausted legacy item before queue removal."""

        if self._legacy_dlq_file is None:
            self._last_dlq_error = "legacy DLQ path is not configured"
            return False
        try:
            self._legacy_dlq_file.parent.mkdir(parents=True, exist_ok=True)
            frozen_body = (
                base64.b64decode(item.body_b64, validate=True)
                if item.body_b64
                else self._serialize_payload(item.payload)
            )
            dlq_ref = "legacy-input-dlq:" + hashlib.sha256(
                b"\n".join((
                    item.source.value.encode("utf-8"),
                    str(item.created_at_epoch_ms).encode("ascii"),
                    frozen_body,
                ))
            ).hexdigest()
            existing = (
                self._legacy_dlq_file.read_text(encoding="utf-8")
                if self._legacy_dlq_file.exists()
                else ""
            )
            if dlq_ref not in existing:
                record = {
                    "schema_version": "natsume.edge.gateway-legacy-dlq.v1",
                    "dlq_ref": dlq_ref,
                    "reason_code": str(reason_code).strip().upper(),
                    "dlq_at_epoch_ms": int(time.time() * 1000),
                    "report": item.as_dict(),
                }
                rendered = json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ) + "\n"
                tmp_path = self._legacy_dlq_file.with_suffix(
                    self._legacy_dlq_file.suffix + ".tmp"
                )
                tmp_path.write_text(existing + rendered, encoding="utf-8")
                tmp_path.replace(self._legacy_dlq_file)
                self._legacy_dlq_count += 1
            self._last_dlq_error = ""
            return True
        except Exception as exc:
            self._last_dlq_error = str(exc)[:240]
            logger.warning("EdgeInputReporter: failed to persist legacy DLQ: %s", exc)
            return False
