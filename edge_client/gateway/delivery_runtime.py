"""持久化 Gateway inbox/outbox worker 运行时。

The injected transport-store port owns schema, idempotency, leases, fencing,
retry scheduling and DLQ state. Edge Gateway supplies delivery callbacks and
lifecycle management without importing any concrete Host repository.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


logger = logging.getLogger("Natsume.edge.gateway.delivery_runtime")

GATEWAY_TRANSPORT_MODE_ENV = "EDGE_GATEWAY_TRANSPORT_MODE"
GATEWAY_ACCEPT_NEW_INTAKE_ENV = "EDGE_GATEWAY_ACCEPT_NEW_INTAKE"
GW_TRANSPORT_DISABLED = "GW_TRANSPORT_DISABLED"
GW_TRANSPORT_SCHEMA_UNAVAILABLE = "GW_TRANSPORT_SCHEMA_UNAVAILABLE"
GW_TRANSPORT_INBOX_CONFLICT = "GW_TRANSPORT_INBOX_CONFLICT"
GW_TRANSPORT_OUTBOX_CONFLICT = "GW_TRANSPORT_OUTBOX_CONFLICT"
GW_TRANSPORT_DELIVERY_FAILED = "GW_TRANSPORT_DELIVERY_FAILED"
GW_TRANSPORT_REPLY_INVALID = "GW_TRANSPORT_REPLY_INVALID"
GW_TRANSPORT_WORKER_STOP_TIMEOUT = "GW_TRANSPORT_WORKER_STOP_TIMEOUT"
GW_REPLY_QUERY_NOT_FOUND = "GW_REPLY_QUERY_NOT_FOUND"
GW_REPLY_QUERY_IDENTITY_MISMATCH = "GW_REPLY_QUERY_IDENTITY_MISMATCH"
GW_REPLY_QUERY_STATE_UNKNOWN = "GW_REPLY_QUERY_STATE_UNKNOWN"

GATEWAY_REPLY_QUERY_SCHEMA_VERSION = "Natsume.gateway.reply-query.v1"
_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_QUERY_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")


class GatewayTransportMode(str, Enum):
    """The only supported Gateway transport mode."""

    DURABLE = "DURABLE"


def load_gateway_transport_mode(
    env: Mapping[str, str] | None = None,
) -> GatewayTransportMode:
    """Resolve the transport mode.

    Missing, retired and malformed values all converge on the durable owner;
    there is no compatibility queue or shadow acknowledgement path left.
    """

    source = os.environ if env is None else env
    raw = str(source.get(GATEWAY_TRANSPORT_MODE_ENV, "DURABLE")).strip().upper()
    if raw and raw != GatewayTransportMode.DURABLE.value:
        logger.warning(
            "Gateway transport mode %r is retired; DURABLE remains authoritative",
            raw,
        )
    return GatewayTransportMode.DURABLE


def load_gateway_accept_new_intake(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Resolve the independent durable-intake kill switch.

    The switch defaults on for a normal DURABLE rollout.  An invalid explicit
    value fails closed without disabling the durable delivery owner.
    The canonical switch stops every producer, including DB watcher and
    Telegram listener callbacks.
    """

    source = os.environ if env is None else env
    raw_value = source.get(GATEWAY_ACCEPT_NEW_INTAKE_ENV)
    if raw_value is None or not str(raw_value).strip():
        return True
    normalized = str(raw_value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.error(
        "Gateway intake switch is invalid; failing closed: %s=%r",
        GATEWAY_ACCEPT_NEW_INTAKE_ENV,
        raw_value,
    )
    return False


class GatewayDeliveryError(RuntimeError):
    """Stable, client-safe delivery boundary failure."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GW_TRANSPORT_DELIVERY_FAILED)
        self.detail = str(detail or "gateway delivery failed").replace("\r", " ").replace("\n", " ")[:240]
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class GatewayDeliveryReceipt:
    direction: str
    record_id: str
    status: str
    ack_ref: str | None = None
    reason_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "record_id": self.record_id,
            "status": self.status,
            "ack_ref": self.ack_ref,
            "reason_code": self.reason_code,
        }


def _stable_record_id(prefix: str, *parts: str) -> str:
    material = "\n".join(str(part or "").strip() for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()}"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        result = as_dict()
        if isinstance(result, Mapping):
            return dict(result)
    return {}


class GatewayDeliveryRuntime:
    """持有租约 worker，但将持久化语义委托给中央 store。"""

    _TERMINAL_OUTBOX_STATES = frozenset({"ACKED", "DELIVERED", "SENT", "DLQ", "DEAD_LETTER"})

    def __init__(
        self,
        *,
        mode: GatewayTransportMode | str,
        store: Any | None,
        reporter: Any | None,
        outbound: Any | None,
        accept_new_intake: bool = True,
        worker_interval_s: float = 0.25,
        lease_ms: int = 30_000,
        worker_id: str | None = None,
    ) -> None:
        if isinstance(mode, GatewayTransportMode):
            self.mode = mode
        else:
            try:
                self.mode = GatewayTransportMode(str(mode).strip().upper())
            except ValueError:
                logger.warning(
                    "Gateway transport runtime mode %r is retired; DURABLE remains authoritative",
                    mode,
                )
                self.mode = GatewayTransportMode.DURABLE
        self._store = store
        self._reporter = reporter
        self._outbound = outbound
        self._accept_new_intake = bool(accept_new_intake)
        self._worker_interval_s = max(0.02, float(worker_interval_s))
        self._lease_ms = max(1_000, int(lease_ms))
        self._worker_id = str(worker_id or f"edge-gateway-{uuid.uuid4().hex}")
        self._shutdown = threading.Event()
        self._inbox_worker: threading.Thread | None = None
        self._outbox_worker: threading.Thread | None = None
        self._ready = False
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stats: dict[str, Any] = {
            "inbox_completed": 0,
            "inbox_retried": 0,
            "inbox_uncertain": 0,
            "outbox_acked": 0,
            "outbox_retried": 0,
            "outbox_uncertain": 0,
            "worker_failures": 0,
            "last_error": "",
        }

    @property
    def intake_enabled(self) -> bool:
        """Only DURABLE may own intake acknowledgements and queue records."""

        return self.mode is GatewayTransportMode.DURABLE and self._accept_new_intake

    @property
    def accept_new_intake(self) -> bool:
        """Expose the independent kill-switch state without implying readiness."""

        return self._accept_new_intake

    @property
    def verification_enabled(self) -> bool:
        """Durable intake always verifies before acknowledging or persisting."""

        return True

    @property
    def delivery_enabled(self) -> bool:
        """Only DURABLE may execute Host/platform side effects."""

        return self.mode is GatewayTransportMode.DURABLE

    def assert_ready(self) -> None:
        if not self.delivery_enabled:
            return
        if self._store is None:
            raise GatewayDeliveryError(
                GW_TRANSPORT_SCHEMA_UNAVAILABLE,
                "durable Gateway transport store is not configured",
            )
        try:
            # 先验证持久化契约，避免 worker 已启动却在接收或发送副作用时才发现
            # inbox/outbox 表不可用；此处失败不会产生任何外部投递。
            self._store.assert_schema_ready()
        except Exception as exc:
            raise GatewayDeliveryError(GW_TRANSPORT_SCHEMA_UNAVAILABLE, str(exc)) from exc

    def start(self) -> None:
        """Start independent inbound and outbound durable workers.

        Platform delivery may block on a remote client timeout.  It must never
        hold up the durable inbox path that admits the user's next message.
        Both workers retain their own stable lease identity and share only the
        lifecycle shutdown signal.
        """

        # 入站与出站分线程并持有独立稳定租约：平台回调阻塞不能拖住新消息持久化，
        # 重复 start 只确认已有 worker，不会再启动一套竞争消费者。
        if not self.delivery_enabled:
            return
        with self._lifecycle_lock:
            self.assert_ready()
            inbox_alive = bool(
                self._inbox_worker is not None and self._inbox_worker.is_alive()
            )
            outbox_alive = bool(
                self._outbox_worker is not None and self._outbox_worker.is_alive()
            )
            if inbox_alive and outbox_alive:
                self._ready = True
                return
            if self._inbox_worker is not None and not inbox_alive:
                self._inbox_worker = None
            if self._outbox_worker is not None and not outbox_alive:
                self._outbox_worker = None
            self._shutdown.clear()
            try:
                if self._inbox_worker is None:
                    self._inbox_worker = threading.Thread(
                        target=self._inbox_worker_loop,
                        name="gateway-durable-inbox",
                        daemon=True,
                    )
                    self._inbox_worker.start()
                if self._outbox_worker is None:
                    self._outbox_worker = threading.Thread(
                        target=self._outbox_worker_loop,
                        name="gateway-durable-outbox",
                        daemon=True,
                    )
                    self._outbox_worker.start()
            except BaseException:
                # ``Thread.start`` can fail before the OS thread exists. Stop
                # a sibling that may already have started and retain only a
                # genuinely live thread so rollback remains retryable.
                self._shutdown.set()
                for attribute in ("_inbox_worker", "_outbox_worker"):
                    worker = getattr(self, attribute)
                    if worker is None:
                        continue
                    if getattr(worker, "ident", None) is not None or worker.is_alive():
                        worker.join(timeout=max(0.1, self._worker_interval_s * 2))
                    if not worker.is_alive():
                        setattr(self, attribute, None)
                self._ready = False
                raise
            self._ready = bool(
                self._inbox_worker is not None
                and self._inbox_worker.is_alive()
                and self._outbox_worker is not None
                and self._outbox_worker.is_alive()
            )

    def disable_intake(self) -> None:
        """Permanently close new intake for this runtime instance."""

        with self._lifecycle_lock:
            self._accept_new_intake = False

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Stop and join both workers, failing closed on an in-flight effect."""

        # 先关闭 intake 再发停止信号；超时仍存活的 worker 代表副作用状态未知，
        # 必须失败关闭而非假装已安全停止。
        with self._lifecycle_lock:
            self._accept_new_intake = False
            self._shutdown.set()
            deadline = time.monotonic() + max(0.1, float(timeout_s))
            for attribute in ("_inbox_worker", "_outbox_worker"):
                worker = getattr(self, attribute)
                if worker is None:
                    continue
                if getattr(worker, "ident", None) is not None or worker.is_alive():
                    worker.join(timeout=max(0.0, deadline - time.monotonic()))
                if not worker.is_alive():
                    setattr(self, attribute, None)
            self._ready = False
            alive_directions = [
                direction
                for direction, worker in (
                    ("inbox", self._inbox_worker),
                    ("outbox", self._outbox_worker),
                )
                if worker is not None and worker.is_alive()
            ]
            if alive_directions:
                raise GatewayDeliveryError(
                    GW_TRANSPORT_WORKER_STOP_TIMEOUT,
                    "durable delivery worker is still executing an in-flight operation: "
                    + ",".join(alive_directions),
                )

    def accept_inbound(
        self,
        *,
        platform: str,
        principal_id: str,
        tenant_id: str | None,
        idempotency_key: str,
        raw_body: bytes,
        normalized_payload: Mapping[str, Any],
        surface: str = "gateway.webhook",
        safe_headers: Mapping[str, Any] | None = None,
        secure_claim_ref: str | None = None,
        verification_audit_ref: str | None = None,
        platform_event_id: str | None = None,
        ordering_key: str | None = None,
        source_sequence: int | None = None,
        max_attempts: int = 8,
    ) -> GatewayDeliveryReceipt:
        # inbox_id 以 tenant/platform/principal/幂等键稳定生成。接收成功仅表示原始
        # 消息已安全入账，实际 Host 处理由 inbox worker 之后在可恢复租约下执行。
        if not self.intake_enabled or self._store is None:
            raise GatewayDeliveryError(GW_TRANSPORT_DISABLED, "new Gateway intake is disabled")
        normalized_tenant = str(tenant_id).strip() if tenant_id else "local"
        # 平台重传同一事件时必须算出同一 inbox_id：此处的“已接收”仅承诺
        # 原始证据已落盘，Host 业务处理仍由持租约的后台 worker 完成。
        inbox_id = _stable_record_id(
            "gateway-inbox",
            normalized_tenant,
            platform,
            principal_id,
            idempotency_key,
        )
        try:
            accepted = self._store.accept_inbox(
                inbox_id=inbox_id,
                surface=str(surface or "gateway.webhook").strip(),
                platform=str(platform).strip().lower(),
                principal_id=str(principal_id).strip(),
                tenant_id=normalized_tenant,
                idempotency_key=str(idempotency_key).strip(),
                raw_body=bytes(raw_body),
                safe_headers=dict(safe_headers or {}),
                normalized_payload=dict(normalized_payload),
                secure_claim_ref=secure_claim_ref,
                verification_audit_ref=verification_audit_ref,
                platform_event_id=platform_event_id,
                ordering_key=ordering_key,
                source_sequence=source_sequence,
                max_attempts=max(1, int(max_attempts)),
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "") or GW_TRANSPORT_DELIVERY_FAILED)
            raise GatewayDeliveryError(reason, str(exc)) from exc
        row = _mapping(accepted)
        status = str(row.get("status") or "").strip().upper()
        return GatewayDeliveryReceipt(
            direction="inbox",
            record_id=str(row.get("inbox_id") or inbox_id),
            status=status,
            ack_ref=(str(row.get("ack_ref")) if row.get("ack_ref") else None),
            reason_code=(str(row.get("reason_code")) if row.get("reason_code") else None),
        )

    def enqueue_reply(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        ordering_key: str | None = None,
        source_sequence: int | None = None,
        max_attempts: int = 8,
    ) -> GatewayDeliveryReceipt:
        if not self.intake_enabled or self._store is None:
            raise GatewayDeliveryError(GW_TRANSPORT_DISABLED, "new Gateway intake is disabled")
        from edge_client.gateway.outbound import sanitize_reply_payload

        safe_payload = sanitize_reply_payload(payload)
        platform = str(payload.get("platform") or "").strip().lower()
        # One Host business idempotency key owns exactly one outbox identity.
        # Destination/platform changes must conflict, never create a second send.
        normalized_tenant = str(tenant_id or "").strip()
        normalized_principal = str(principal_id or "").strip()
        if not normalized_tenant or not normalized_principal:
            raise GatewayDeliveryError(
                GW_TRANSPORT_REPLY_INVALID,
                "authenticated tenant_id and principal_id are required",
            )
        outbox_id = _stable_record_id(
            "gateway-outbox",
            normalized_tenant,
            normalized_principal,
            idempotency_key,
        )
        try:
            row = _mapping(self._store.enqueue_outbox(
                outbox_id=outbox_id,
                tenant_id=normalized_tenant,
                principal_id=normalized_principal,
                destination=f"gateway.platform.{platform}",
                idempotency_key=str(idempotency_key).strip(),
                payload=safe_payload,
                origin_inbox_id=None,
                ordering_key=ordering_key,
                source_sequence=source_sequence,
                max_attempts=max(1, int(max_attempts)),
            ))
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "") or GW_TRANSPORT_DELIVERY_FAILED)
            raise GatewayDeliveryError(reason, str(exc)) from exc
        return GatewayDeliveryReceipt(
            direction="outbox",
            record_id=str(row.get("outbox_id") or outbox_id),
            status=str(row.get("acceptance_status") or row.get("status") or "").strip().upper(),
            ack_ref=(str(row.get("ack_ref")) if row.get("ack_ref") else None),
            reason_code=(str(row.get("last_reason_code")) if row.get("last_reason_code") else None),
        )

    def accept_non_http(
        self,
        *,
        platform: str,
        principal_id: str,
        tenant_id: str,
        platform_event_id: str,
        normalized_payload: Mapping[str, Any],
        ordering_key: str | None = None,
        source_sequence: int | None = None,
        max_attempts: int = 8,
    ) -> GatewayDeliveryReceipt:
        """Persist watcher/listener input through the same 0059 inbox.

        No compatibility reporter/cache is invoked here.  Canonical JSON bytes
        bind the durable digest to the normalized payload, while the platform
        event identifier remains the stable idempotency identity.

        TASK-0592 remove-by: move this Edge composition helper behind the
        shared event-ingress port when non-HTTP channel ownership is split.
        """

        normalized_platform = str(platform or "").strip().lower()
        normalized_event_id = str(platform_event_id or "").strip()
        if not normalized_platform or not normalized_event_id:
            raise GatewayDeliveryError(
                GW_TRANSPORT_REPLY_INVALID,
                "non-HTTP platform and stable event id are required",
            )
        try:
            # 监听器事件没有 HTTP 原文时，也要先固定为规范 JSON 字节；否则相同
            # 语义但不同序列化会破坏持久摘要与后续的可审计重放。
            canonical_body = json.dumps(
                dict(normalized_payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise GatewayDeliveryError(
                GW_TRANSPORT_REPLY_INVALID,
                "non-HTTP normalized payload is not canonical JSON",
            ) from exc
        return self.accept_inbound(
            platform=normalized_platform,
            principal_id=principal_id,
            tenant_id=tenant_id,
            idempotency_key=f"{normalized_platform}:non-http:{normalized_event_id}",
            raw_body=canonical_body,
            normalized_payload=normalized_payload,
            surface="gateway.non-http",
            safe_headers={"x-edge-source": "gateway.non-http"},
            platform_event_id=normalized_event_id,
            ordering_key=ordering_key,
            source_sequence=source_sequence,
            max_attempts=max_attempts,
        )

    def get_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        if self._store is None:
            return None
        row = self._store.get_outbox(outbox_id=str(outbox_id))
        if row is None:
            return None
        return _mapping(row)

    def query_reply(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        effect_id: str,
        delivery_id: str,
        platform: str,
        conversation_id: str,
        payload_digest: str,
    ) -> dict[str, Any]:
        """Read one exact Host reply identity without creating or claiming work.

        The authenticated principal participates in the deterministic outbox
        primary key.  Consequently one HMAC principal cannot probe another
        principal's reply even when it guesses the business delivery id.
        Missing rows are deliberately ``UNKNOWN``: an HTTP response may have
        been lost while the original request is still crossing SQLite intake.
        """

        tenant = str(tenant_id or "")
        principal = str(principal_id or "")
        effect = str(effect_id or "")
        delivery = str(delivery_id or "")
        destination_platform = str(platform or "")
        conversation = str(conversation_id or "")
        digest = str(payload_digest or "")
        if (
            not tenant
            or tenant != tenant.strip()
            or not principal
            or principal != principal.strip()
            or _QUERY_IDENTITY_RE.fullmatch(effect) is None
            or _QUERY_IDENTITY_RE.fullmatch(delivery) is None
            or _QUERY_IDENTITY_RE.fullmatch(destination_platform) is None
            or destination_platform != destination_platform.lower()
            or not conversation
            or conversation != conversation.strip()
            or len(conversation) > 512
            or _SHA256_DIGEST_RE.fullmatch(digest) is None
        ):
            raise GatewayDeliveryError(
                GW_TRANSPORT_REPLY_INVALID,
                "Gateway reply query identity is invalid",
            )
        if self._store is None:
            raise GatewayDeliveryError(
                GW_TRANSPORT_SCHEMA_UNAVAILABLE,
                "durable Gateway transport store is not configured",
            )

        idempotency_key = f"gateway-reply:{delivery}"
        outbox_id = _stable_record_id(
            "gateway-outbox",
            tenant,
            principal,
            idempotency_key,
        )
        query_identity = {
            "schema_version": GATEWAY_REPLY_QUERY_SCHEMA_VERSION,
            "effect_id": effect,
            "delivery_id": delivery,
            "platform": destination_platform,
            "conversation_id": conversation,
            "payload_digest": digest,
        }
        try:
            # The immutable reply payload and mutable delivery state must be
            # reopened as one SQLite snapshot.  In particular, effect_id is a
            # payload field rather than an outbox index column, so echoing the
            # caller value would not prove which accepted effect owns the row.
            raw = self._store.get_outbox(
                outbox_id=outbox_id,
                include_payload=True,
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "") or "")
            if reason == "GATEWAY_TRANSPORT_MESSAGE_NOT_FOUND":
                return {
                    **query_identity,
                    "effect_status": "UNKNOWN",
                    "reason_code": GW_REPLY_QUERY_NOT_FOUND,
                    "outbox_id": None,
                    "delivery_state": "NOT_FOUND",
                    "status_version": None,
                    "fencing_token": None,
                    "delivery_receipt_digest": None,
                }
            raise GatewayDeliveryError(
                GW_TRANSPORT_DELIVERY_FAILED,
                "Gateway reply query could not reopen durable state",
            ) from exc
        row = _mapping(raw)
        if not row:
            return {
                **query_identity,
                "effect_status": "UNKNOWN",
                "reason_code": GW_REPLY_QUERY_NOT_FOUND,
                "outbox_id": None,
                "delivery_state": "NOT_FOUND",
                "status_version": None,
                "fencing_token": None,
                "delivery_receipt_digest": None,
            }

        persisted_payload: dict[str, Any] = {}
        payload_body = row.get("payload")
        if isinstance(payload_body, (bytes, bytearray, memoryview)):
            try:
                decoded_payload = json.loads(bytes(payload_body).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded_payload = None
            if isinstance(decoded_payload, dict):
                persisted_payload = decoded_payload

        expected_idempotency_digest = "sha256:" + hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
        exact = (
            str(row.get("outbox_id") or "") == outbox_id
            and str(row.get("tenant_id") or "") == tenant
            and str(row.get("principal_id") or "") == principal
            and str(row.get("destination") or "")
            == f"gateway.platform.{destination_platform}"
            and str(row.get("idempotency_digest") or "")
            == expected_idempotency_digest
            and str(row.get("payload_sha256") or "") == digest
            and str(row.get("ordering_key") or "")
            == f"{destination_platform}:{conversation}"
            and str(persisted_payload.get("effect_id") or "") == effect
        )
        if not exact:
            # 不返回部分匹配记录，防止调用方用可猜测的 delivery_id 枚举其他
            # 身份的回复；UNKNOWN 同时保留“请求尚未落盘”的恢复语义。
            return {
                **query_identity,
                "effect_status": "UNKNOWN",
                "reason_code": GW_REPLY_QUERY_IDENTITY_MISMATCH,
                "outbox_id": None,
                "delivery_state": "IDENTITY_MISMATCH",
                "status_version": None,
                "fencing_token": None,
                "delivery_receipt_digest": None,
            }

        state = str(row.get("status") or "").strip().upper()
        try:
            status_version = int(row.get("status_version"))
            fencing_token = int(row.get("fencing_token"))
        except (TypeError, ValueError):
            status_version = -1
            fencing_token = -1
        if (
            state not in {"READY", "CLAIMED", "RETRY_WAIT", "ACKED", "DLQ"}
            or status_version < 0
            or fencing_token < 0
        ):
            return {
                **query_identity,
                "effect_status": "UNKNOWN",
                "reason_code": GW_REPLY_QUERY_STATE_UNKNOWN,
                "outbox_id": None,
                "delivery_state": "UNKNOWN",
                "status_version": None,
                "fencing_token": None,
                "delivery_receipt_digest": None,
            }
        receipt_digest = str(row.get("ack_digest") or "").strip()
        if (
            (receipt_digest and _SHA256_DIGEST_RE.fullmatch(receipt_digest) is None)
            or (state == "ACKED" and not receipt_digest)
            or (state == "CLAIMED" and fencing_token < 1)
        ):
            return {
                **query_identity,
                "effect_status": "UNKNOWN",
                "reason_code": GW_REPLY_QUERY_STATE_UNKNOWN,
                "outbox_id": None,
                "delivery_state": "UNKNOWN",
                "status_version": None,
                "fencing_token": None,
                "delivery_receipt_digest": None,
            }
        return {
            **query_identity,
            # COMMITTED means the exact durable Gateway outbox intake exists.
            # The separate delivery_state/receipt digest state whether the
            # platform adapter has also confirmed its outbound attempt.
            "effect_status": "COMMITTED",
            "reason_code": None,
            "outbox_id": outbox_id,
            "delivery_state": state,
            "status_version": status_version,
            "fencing_token": fencing_token,
            "delivery_receipt_digest": receipt_digest or None,
        }

    def wait_outbox(self, outbox_id: str, *, timeout_ms: int) -> dict[str, Any] | None:
        deadline = time.monotonic() + (max(1, int(timeout_ms)) / 1000.0)
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.get_outbox(outbox_id)
            if last is None:
                return None
            status = str(last.get("status") or "").strip().upper()
            if status in self._TERMINAL_OUTBOX_STATES:
                return last
            self._shutdown.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        return last

    def stats(self) -> dict[str, Any]:
        inbox_worker_alive = bool(
            self._inbox_worker is not None and self._inbox_worker.is_alive()
        )
        outbox_worker_alive = bool(
            self._outbox_worker is not None and self._outbox_worker.is_alive()
        )
        worker_alive = inbox_worker_alive and outbox_worker_alive
        with self._lock:
            return {
                "mode": self.mode.value,
                "intake_enabled": self.intake_enabled,
                "accept_new_intake": self.accept_new_intake,
                "verification_enabled": self.verification_enabled,
                "delivery_enabled": self.delivery_enabled,
                "ready": bool(self._ready and worker_alive),
                "worker_alive": worker_alive,
                "inbox_worker_alive": inbox_worker_alive,
                "outbox_worker_alive": outbox_worker_alive,
                **dict(self._stats),
            }

    def _inbox_worker_loop(self) -> None:
        while not self._shutdown.is_set():
            worked = False
            try:
                for _ in range(16):
                    if self._shutdown.is_set():
                        break
                    inbox = self._store.claim_inbox(
                        worker_id=f"{self._worker_id}:inbox",
                        lease_ms=self._lease_ms,
                    )
                    if not inbox:
                        break
                    worked = True
                    self._deliver_inbox(_mapping(inbox))
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                self._record_worker_error(exc)
            if not worked:
                self._shutdown.wait(timeout=self._worker_interval_s)

    def _outbox_worker_loop(self) -> None:
        while not self._shutdown.is_set():
            worked = False
            try:
                for _ in range(16):
                    if self._shutdown.is_set():
                        break
                    outbox = self._store.claim_outbox(
                        worker_id=f"{self._worker_id}:outbox",
                        lease_ms=self._lease_ms,
                    )
                    if not outbox:
                        break
                    worked = True
                    self._deliver_outbox(_mapping(outbox))
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                self._record_worker_error(exc)
            if not worked:
                self._shutdown.wait(timeout=self._worker_interval_s)

    def _deliver_inbox(self, row: Mapping[str, Any]) -> None:
        inbox_id = str(row.get("inbox_id") or "")
        claim_token = str(row.get("claim_token") or "")
        fencing_token = int(row.get("fencing_token") or 0)
        try:
            payload = row.get("normalized_payload")
            if not isinstance(payload, Mapping):
                raise GatewayDeliveryError(GW_TRANSPORT_DELIVERY_FAILED, "inbox normalized_payload is missing")
            idempotency_key = str(row.get("idempotency_key") or inbox_id)
            deliver = getattr(self._reporter, "deliver_gateway_payload", None)
            if not callable(deliver):
                raise GatewayDeliveryError(GW_TRANSPORT_DELIVERY_FAILED, "durable Host reporter is unavailable")
        except Exception as exc:
            self._retry_inbox(row, exc)
            return
        try:
            # 先将“即将调用 Host”写入带栅栏的持久状态。进程若在网络调用后崩溃，
            # 重启者可以用相同业务幂等键查询既有结果，而不是创建第二个任务。
            self._store.mark_inbox_dispatch_started(
                inbox_id=inbox_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
            )
        except Exception as exc:
            # No external side effect has started, so a fenced retry is safe.
            self._retry_inbox(row, exc)
            return
        try:
            result = deliver(dict(payload), idempotency_key=idempotency_key)
            if not bool(getattr(result, "success", False)):
                detail = str(getattr(result, "detail", "Host delivery failed") or "Host delivery failed")
                reason = str(getattr(result, "reason_code", "") or GW_TRANSPORT_DELIVERY_FAILED)
                failure = GatewayDeliveryError(reason, detail)
                self._retry_inbox(row, failure)
                return
            receipt_ref = str(getattr(result, "receipt_ref", "") or "").strip()
            receipt_body_sha256 = str(
                getattr(result, "receipt_body_sha256", "") or ""
            ).strip()
            if not receipt_ref or not receipt_body_sha256:
                raise GatewayDeliveryError(
                    "GATEWAY_TRANSPORT_RECEIPT_INVALID",
                    "Host success response is missing its authenticated receipt",
                )
            self._store.complete_inbox(
                inbox_id=inbox_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                receipt_ref=receipt_ref,
                outbox_messages=(),
            )
            self._increment("inbox_completed")
        except Exception as exc:
            # The frozen inbox body and stable Host idempotency key make an
            # exact retry safe even when the first HTTP response was lost.
            # Secure Ingress and HostCommand replay return the existing result
            # instead of creating a second TaskGraph. Platform outbox effects
            # intentionally keep the stricter uncertain-delivery quarantine.
            self._retry_inbox(row, exc)

    def _retry_inbox(self, row: Mapping[str, Any], exc: Exception) -> None:
        reason = str(getattr(exc, "reason_code", "") or exc.__class__.__name__)
        try:
            result = self._store.retry_inbox(
                inbox_id=str(row.get("inbox_id") or ""),
                claim_token=str(row.get("claim_token") or ""),
                fencing_token=int(row.get("fencing_token") or 0),
                reason_code=reason[:128],
                base_delay_ms=1_000,
                max_delay_ms=60_000,
            )
            self._record_retry_outcome("inbox", result)
        except Exception as retry_exc:  # pragma: no cover - store owns fencing failure
            self._record_worker_error(retry_exc)

    def _deliver_outbox(self, row: Mapping[str, Any]) -> None:
        outbox_id = str(row.get("outbox_id") or "")
        claim_token = str(row.get("claim_token") or "")
        fencing_token = int(row.get("fencing_token") or 0)
        try:
            # prepare 只构造不可变投递计划，不应产生平台副作用；因此这一区间失败
            # 可以走普通重试。真正的发送必须在下一步持久标记之后才发生。
            payload = self._decode_outbox_payload(row.get("payload"))
            prepare_payload = getattr(self._outbound, "prepare_payload", None)
            dispatch_prepared = getattr(self._outbound, "dispatch_prepared", None)
            if not callable(prepare_payload) or not callable(dispatch_prepared):
                raise GatewayDeliveryError(
                    GW_TRANSPORT_DELIVERY_FAILED,
                    "typed durable outbound dispatcher is unavailable",
                )
            from edge_client.gateway.outbound import (  # noqa: PLC0415
                PreparedOutboundDelivery,
            )

            prepared = prepare_payload(payload)
            if not isinstance(prepared, PreparedOutboundDelivery):
                raise GatewayDeliveryError(
                    GW_TRANSPORT_DELIVERY_FAILED,
                    "outbound preflight did not return an immutable delivery plan",
                )
        except Exception as exc:
            # Preparation and adapter selection happen before dispatch_started;
            # these failures are therefore definitely free of platform side
            # effects and may use the ordinary fenced retry policy.
            self._retry_outbox(row, exc)
            return
        try:
            self._store.mark_outbox_dispatch_started(
                outbox_id=outbox_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
            )
        except Exception as exc:
            self._retry_outbox(row, exc)
            return
        try:
            outcome = dispatch_prepared(prepared)
            certainty = str(
                getattr(getattr(outcome, "certainty", None), "value", None)
                or getattr(outcome, "certainty", "")
                or ""
            ).strip().upper()
            if not bool(getattr(outcome, "accepted", False)) or certainty != (
                "ADAPTER_CONFIRMED"
            ):
                # 网络超时或适配器只接受未确认时，平台可能已收到消息；不能把它当成
                # 普通失败重发，必须留下不确定记录交由人工或专门对账恢复。
                self._leave_uncertain(
                    "outbox",
                    row,
                    GatewayDeliveryError(
                        GW_TRANSPORT_DELIVERY_FAILED,
                        "platform delivery was not adapter-confirmed",
                    ),
                )
                return
            ack_ref = str(getattr(outcome, "receipt_ref", "") or "").strip()
            digest = str(getattr(outcome, "receipt_digest", "") or "").strip().lower()
            if digest.startswith("sha256:"):
                digest = digest[7:]
            if (
                not ack_ref.startswith("adapter-confirmation:")
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                self._leave_uncertain(
                    "outbox",
                    row,
                    GatewayDeliveryError(
                        "GATEWAY_TRANSPORT_RECEIPT_INVALID",
                        "adapter confirmation receipt is missing or malformed",
                    ),
                )
                return
            self._store.ack_outbox(
                outbox_id=outbox_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                ack_ref=ack_ref,
                ack_digest=f"sha256:{digest}",
            )
            self._increment("outbox_acked")
        except Exception as exc:
            self._leave_uncertain("outbox", row, exc)

    def _retry_outbox(self, row: Mapping[str, Any], exc: Exception) -> None:
        reason = str(getattr(exc, "reason_code", "") or exc.__class__.__name__)
        try:
            result = self._store.retry_outbox(
                outbox_id=str(row.get("outbox_id") or ""),
                claim_token=str(row.get("claim_token") or ""),
                fencing_token=int(row.get("fencing_token") or 0),
                reason_code=reason[:128],
                base_delay_ms=1_000,
                max_delay_ms=60_000,
            )
            self._record_retry_outcome("outbox", result)
        except Exception as retry_exc:  # pragma: no cover - store owns fencing failure
            self._record_worker_error(retry_exc)

    def _leave_uncertain(
        self,
        direction: str,
        row: Mapping[str, Any],
        exc: Exception,
    ) -> None:
        normalized = str(direction).strip().lower()
        if normalized != "outbox":
            raise ValueError(
                "uncertain delivery quarantine is reserved for platform outbox effects"
            )
        try:
            # 此隔离通道仅适用于有外部可见副作用的 outbox；inbox 有 Host 幂等键
            # 保护，可以安全重试，二者不能混用同一恢复策略。
            self._store.retry_outbox(
                outbox_id=str(row.get("outbox_id") or ""),
                claim_token=str(row.get("claim_token") or ""),
                fencing_token=int(row.get("fencing_token") or 0),
                reason_code="GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN",
                base_delay_ms=1_000,
                max_delay_ms=60_000,
            )
            self._increment(f"{normalized}_uncertain")
        except Exception as persist_exc:  # lease expiry remains a safe fallback
            self._record_worker_error(persist_exc)
        self._record_worker_error(exc)
        logger.error(
            "Gateway %s delivery outcome is uncertain; record quarantined in DLQ",
            normalized,
        )

    def _record_retry_outcome(self, direction: str, result: Any) -> None:
        normalized = str(direction).strip().lower()
        row = _mapping(result)
        status = str(row.get("status") or "").strip().upper()
        reason = str(row.get("last_reason_code") or "").strip().upper()
        if status == "DLQ" and reason == "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN":
            self._increment(f"{normalized}_uncertain")
        else:
            self._increment(f"{normalized}_retried")

    @staticmethod
    def _decode_outbox_payload(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value).decode("utf-8")
        elif isinstance(value, str):
            raw = value
        else:
            raise GatewayDeliveryError(GW_TRANSPORT_REPLY_INVALID, "outbox payload is not JSON")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise GatewayDeliveryError(GW_TRANSPORT_REPLY_INVALID, "outbox payload must be an object")
        return parsed

    def _increment(self, key: str) -> None:
        with self._lock:
            self._stats[key] = int(self._stats.get(key) or 0) + 1

    def _record_worker_error(self, exc: Exception) -> None:
        with self._lock:
            self._stats["worker_failures"] = int(self._stats.get("worker_failures") or 0) + 1
            self._stats["last_error"] = str(exc)[:240]
        logger.error("Gateway durable delivery worker failed: %s", exc)


__all__ = [
    "GATEWAY_REPLY_QUERY_SCHEMA_VERSION",
    "GATEWAY_ACCEPT_NEW_INTAKE_ENV",
    "GATEWAY_TRANSPORT_MODE_ENV",
    "GW_REPLY_QUERY_IDENTITY_MISMATCH",
    "GW_REPLY_QUERY_NOT_FOUND",
    "GW_REPLY_QUERY_STATE_UNKNOWN",
    "GW_TRANSPORT_DELIVERY_FAILED",
    "GW_TRANSPORT_DISABLED",
    "GW_TRANSPORT_INBOX_CONFLICT",
    "GW_TRANSPORT_OUTBOX_CONFLICT",
    "GW_TRANSPORT_REPLY_INVALID",
    "GW_TRANSPORT_SCHEMA_UNAVAILABLE",
    "GatewayDeliveryError",
    "GatewayDeliveryReceipt",
    "GatewayDeliveryRuntime",
    "GatewayTransportMode",
    "load_gateway_accept_new_intake",
    "load_gateway_transport_mode",
]
