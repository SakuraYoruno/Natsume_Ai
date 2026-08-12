"""Unified Message Gateway — 回复分发器。

接收 Host Brain 的 UnifiedReply，根据平台路由到对应适配器的 send_reply 方法。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedReply

logger = logging.getLogger("Natsume.edge.gateway.outbound")

GW_REPLY_PAYLOAD_INVALID = "GW_REPLY_PAYLOAD_INVALID"
GW_REPLY_SENSITIVE_CONTEXT = "GW_REPLY_SENSITIVE_CONTEXT"
GW_REPLY_ADAPTER_UNAVAILABLE = "GW_REPLY_ADAPTER_UNAVAILABLE"

_MAX_REPLY_EXTRA_BYTES = 16 * 1024
_MAX_REPLY_EXTRA_DEPTH = 5
_MAX_REPLY_EXTRA_ITEMS = 96
_MAX_REPLY_EXTRA_STRING = 2_048
_SENSITIVE_KEY_PARTS = frozenset({
    "accesskey",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "hmac",
    "password",
    "proof",
    "replytoken",
    "secret",
    "sessiontoken",
    "signature",
    "token",
})


class OutboundDeliveryCertainty(str, Enum):
    """What the Gateway can honestly prove about one outbound attempt."""

    DEFINITELY_NOT_SENT = "DEFINITELY_NOT_SENT"
    ADAPTER_CONFIRMED = "ADAPTER_CONFIRMED"
    MAY_HAVE_COMMITTED = "MAY_HAVE_COMMITTED"


@dataclass(frozen=True, slots=True)
class OutboundDeliveryOutcome:
    """Typed result of preflight rejection or one adapter invocation.

    ``ADAPTER_CONFIRMED`` deliberately does not claim that the remote platform
    acknowledged the message.  The current adapter contract only returns a
    boolean, so the strongest honest success evidence is adapter confirmation.
    """

    accepted: bool
    certainty: OutboundDeliveryCertainty
    receipt_ref: str | None = None
    receipt_digest: str | None = None
    reason: str = ""


class ReplyPayloadRejected(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail).replace("\r", " ").replace("\n", " ")[:240]
        self.outcome = OutboundDeliveryOutcome(
            accepted=False,
            certainty=OutboundDeliveryCertainty.DEFINITELY_NOT_SENT,
            reason=self.reason_code,
        )
        super().__init__(f"{self.reason_code}: {self.detail}")


class OutboundPreparationRejected(ReplyPayloadRejected):
    """Preflight rejection; no platform adapter was invoked."""


class OutboundDispatchUncertain(RuntimeError):
    """Compatibility-facade signal for an adapter exception after invocation."""

    def __init__(self, outcome: OutboundDeliveryOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.reason)


@dataclass(frozen=True, slots=True)
class PreparedOutboundDelivery:
    """Deeply immutable outbound plan produced before any adapter side effect.

    Mutable reply metadata is stored as canonical JSON bytes and the selected
    adapter is captured during preflight.  This prevents caller mutation or a
    later registry change from altering what a prepared attempt will send.
    """

    platform: Platform
    msg_type: MsgType
    conversation_id: str
    content: str
    media_path: str | None
    reply_to_msg_id: str | None
    raw_extra_json: bytes
    payload_digest: str
    _adapter: AbstractPlatformAdapter = field(repr=False, compare=False)

    def build_reply(self) -> UnifiedReply:
        """Build the adapter DTO from already-validated immutable fields."""

        raw_extra = json.loads(self.raw_extra_json.decode("utf-8"))
        return UnifiedReply(
            conversation_id=self.conversation_id,
            platform=self.platform,
            msg_type=self.msg_type,
            content=self.content,
            media_path=self.media_path,
            reply_to_msg_id=self.reply_to_msg_id,
            raw_extra=raw_extra,
        )


def sanitize_reply_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an authenticated reply without accepting platform secrets.

    Existing non-secret routing metadata in ``raw_extra`` remains compatible.
    Platform reply tokens/credentials and caller-supplied reply contexts are
    rejected.  A server-owned ``reply_context_ref`` may be carried as an opaque
    reference for a later resolver.
    """

    # 调用方只能携带有限、可 JSON 化的路由元数据；token、凭据、签名及可变
    # reply_context 一律拒绝，后者必须由服务端以不透明引用另行解析。
    if not isinstance(payload, Mapping):
        raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "reply payload must be an object")
    safe = dict(payload)
    raw_extra = safe.get("raw_extra")
    if raw_extra is None:
        safe["raw_extra"] = {}
        return safe
    if not isinstance(raw_extra, Mapping):
        raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "raw_extra must be an object")

    item_count = 0

    def _walk(value: Any, *, depth: int, path: str) -> Any:
        nonlocal item_count
        if depth > _MAX_REPLY_EXTRA_DEPTH:
            raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "raw_extra nesting is too deep")
        item_count += 1
        if item_count > _MAX_REPLY_EXTRA_ITEMS:
            raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "raw_extra contains too many values")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > _MAX_REPLY_EXTRA_STRING:
                raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, f"{path} is too long")
            return value
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, child in value.items():
                key = str(raw_key or "").strip()
                normalized = key.lower().replace("-", "_")
                compact_key = re.sub(r"[^a-z0-9]", "", normalized)
                if not key or len(key) > 96:
                    raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "raw_extra key is invalid")
                if normalized == "reply_context":
                    raise ReplyPayloadRejected(
                        GW_REPLY_SENSITIVE_CONTEXT,
                        "reply_context must be supplied as a server-owned reply_context_ref",
                    )
                if any(part in compact_key for part in _SENSITIVE_KEY_PARTS):
                    raise ReplyPayloadRejected(
                        GW_REPLY_SENSITIVE_CONTEXT,
                        f"sensitive reply metadata is forbidden at {path}.{key}",
                    )
                if normalized == "reply_context_ref":
                    ref = str(child or "").strip()
                    if not ref or len(ref) > 512 or "://" not in ref:
                        raise ReplyPayloadRejected(
                            GW_REPLY_PAYLOAD_INVALID,
                            "reply_context_ref must be a bounded opaque reference",
                        )
                    result[key] = ref
                    continue
                result[key] = _walk(child, depth=depth + 1, path=f"{path}.{key}")
            return result
        if isinstance(value, (list, tuple)):
            if len(value) > 32:
                raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, f"{path} list is too large")
            return [_walk(child, depth=depth + 1, path=f"{path}[]") for child in value]
        raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, f"{path} contains a non-JSON value")

    clean_extra = _walk(raw_extra, depth=0, path="raw_extra")
    try:
        encoded = json.dumps(
            clean_extra,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReplyPayloadRejected(
            GW_REPLY_PAYLOAD_INVALID,
            "raw_extra must contain finite JSON values",
        ) from exc
    if len(encoded) > _MAX_REPLY_EXTRA_BYTES:
        raise ReplyPayloadRejected(GW_REPLY_PAYLOAD_INVALID, "raw_extra exceeds the size limit")
    safe["raw_extra"] = clean_extra
    return safe


class OutboundDispatcher:
    """回复分发器。

    维护 {Platform: AbstractPlatformAdapter} 映射，
    将 UnifiedReply 路由到对应平台的发送接口。

    Args:
        adapters: 平台适配器字典 {Platform: adapter}
    """

    def __init__(self, adapters: dict[Platform, AbstractPlatformAdapter] | None = None) -> None:
        self._adapters: dict[Platform, AbstractPlatformAdapter] = adapters or {}
        self._stats: dict[str, int] = {
            "dispatched": 0,
            "failed": 0,
            "no_adapter": 0,
        }

    def register(self, adapter: AbstractPlatformAdapter) -> None:
        """注册平台适配器。"""
        self._adapters[adapter.platform_id] = adapter
        logger.info("OutboundDispatcher: 注册适配器 platform=%s", adapter.platform_id.value)

    def dispatch(self, reply: UnifiedReply) -> bool:
        """将回复分发到对应平台。

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功
        """
        adapter = self._adapters.get(reply.platform)
        if adapter is None:
            logger.warning(
                "OutboundDispatcher: 未找到适配器 platform=%s", reply.platform.value
            )
            self._stats["no_adapter"] += 1
            return False

        try:
            success = adapter.send_reply(reply)
            if success:
                self._stats["dispatched"] += 1
            else:
                self._stats["failed"] += 1
            return success
        except Exception as exc:
            logger.error(
                "OutboundDispatcher: 分发异常 platform=%s conversation_sha256=%s type=%s",
                reply.platform.value,
                hashlib.sha256(
                    str(reply.conversation_id).encode("utf-8")
                ).hexdigest()[:16],
                type(exc).__name__,
            )
            self._stats["failed"] += 1
            return False

    def dispatch_payload(self, payload: Mapping[str, Any]) -> bool:
        """Compatibility facade over the typed two-phase delivery contract.

        New durable callers should use ``prepare_payload`` before recording the
        dispatch boundary, then call ``dispatch_prepared`` exactly once.
        """

        try:
            prepared = self.prepare_payload(payload)
        except OutboundPreparationRejected as exc:
            if exc.reason_code == GW_REPLY_ADAPTER_UNAVAILABLE:
                self._stats["no_adapter"] += 1
                return False
            raise
        outcome = self.dispatch_prepared(prepared)
        if outcome.reason.startswith("ADAPTER_EXCEPTION:"):
            # Preserve the former facade's uncertain-exception signal.  The
            # typed API carries the same state without throwing.
            raise OutboundDispatchUncertain(outcome)
        return outcome.accepted

    def prepare_payload(self, payload: Mapping[str, Any]) -> PreparedOutboundDelivery:
        """Validate and freeze an outbound payload without invoking an adapter.

        Raises:
            OutboundPreparationRejected: with a ``DEFINITELY_NOT_SENT`` outcome
                when sanitization, enum/required-field validation, or adapter
                selection fails.
        """

        # 两阶段交付先冻结已净化 payload 与选定 adapter，再由 durable outbox 记录
        # “即将调用”的边界。注册表后续变化或调用方修改字典都不能改变本次尝试。
        try:
            safe = sanitize_reply_payload(payload)
        except ReplyPayloadRejected as exc:
            raise OutboundPreparationRejected(exc.reason_code, exc.detail) from exc
        try:
            platform = Platform(str(safe.get("platform") or "").strip().lower())
            msg_type = MsgType(str(safe.get("msg_type") or "text").strip().lower())
        except ValueError as exc:
            raise OutboundPreparationRejected(
                GW_REPLY_PAYLOAD_INVALID,
                "platform or msg_type is unsupported",
            ) from exc
        raw_conversation_id = safe.get("conversation_id")
        if not isinstance(raw_conversation_id, str) or not raw_conversation_id.strip():
            raise OutboundPreparationRejected(
                GW_REPLY_PAYLOAD_INVALID,
                "conversation_id is required",
            )
        conversation_id = raw_conversation_id.strip()
        adapter = self._adapters.get(platform)
        if adapter is None or not callable(getattr(adapter, "send_reply", None)):
            raise OutboundPreparationRejected(
                GW_REPLY_ADAPTER_UNAVAILABLE,
                f"no adapter registered for {platform.value}",
            )

        def _optional_text(field_name: str, *, default: str | None = None) -> str | None:
            value = safe.get(field_name)
            if value is None or value == "":
                return default
            if not isinstance(value, str):
                raise OutboundPreparationRejected(
                    GW_REPLY_PAYLOAD_INVALID,
                    f"{field_name} must be a string",
                )
            return value.strip() if field_name != "content" else value

        content = _optional_text("content", default="") or ""
        media_path = _optional_text("media_path")
        reply_to_msg_id = _optional_text("reply_to_msg_id")
        raw_extra_json = json.dumps(
            safe.get("raw_extra") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        canonical_payload = json.dumps(
            {
                "content": content,
                "conversation_id": conversation_id,
                "media_path": media_path,
                "msg_type": msg_type.value,
                "platform": platform.value,
                "raw_extra": json.loads(raw_extra_json.decode("utf-8")),
                "reply_to_msg_id": reply_to_msg_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return PreparedOutboundDelivery(
            platform=platform,
            msg_type=msg_type,
            conversation_id=conversation_id,
            content=content,
            media_path=media_path,
            reply_to_msg_id=reply_to_msg_id,
            raw_extra_json=raw_extra_json,
            payload_digest=hashlib.sha256(canonical_payload).hexdigest(),
            _adapter=adapter,
        )

    def dispatch_prepared(self, prepared: PreparedOutboundDelivery) -> OutboundDeliveryOutcome:
        """Invoke the captured adapter exactly once and return honest evidence."""

        # adapter 异常或非布尔返回都可能已经到达远端，因此只能标 MAY_HAVE_COMMITTED；
        # 只有 adapter 明确返回 True 才能记录其本地确认，仍不等同平台最终送达。
        if not isinstance(prepared, PreparedOutboundDelivery):
            return OutboundDeliveryOutcome(
                accepted=False,
                certainty=OutboundDeliveryCertainty.DEFINITELY_NOT_SENT,
                reason=GW_REPLY_PAYLOAD_INVALID,
            )
        try:
            adapter_result = prepared._adapter.send_reply(prepared.build_reply())
        except Exception as exc:
            self._stats["failed"] += 1
            logger.error(
                "OutboundDispatcher: adapter attempt raised platform=%s type=%s",
                prepared.platform.value,
                type(exc).__name__,
            )
            return OutboundDeliveryOutcome(
                accepted=False,
                certainty=OutboundDeliveryCertainty.MAY_HAVE_COMMITTED,
                reason=f"ADAPTER_EXCEPTION:{type(exc).__name__}",
            )
        if adapter_result is True:
            self._stats["dispatched"] += 1
            receipt_digest = hashlib.sha256(
                (
                    "natsume-adapter-confirmation-v1\n"
                    f"{prepared.platform.value}\n{prepared.payload_digest}"
                ).encode("utf-8")
            ).hexdigest()
            return OutboundDeliveryOutcome(
                accepted=True,
                certainty=OutboundDeliveryCertainty.ADAPTER_CONFIRMED,
                receipt_ref=(
                    f"adapter-confirmation:{prepared.platform.value}:"
                    f"{receipt_digest[:24]}"
                ),
                receipt_digest=receipt_digest,
                reason="ADAPTER_RETURNED_TRUE",
            )
        self._stats["failed"] += 1
        reason = (
            "ADAPTER_RETURNED_FALSE"
            if adapter_result is False
            else f"ADAPTER_RESULT_INVALID:{type(adapter_result).__name__}"
        )
        return OutboundDeliveryOutcome(
            accepted=False,
            certainty=OutboundDeliveryCertainty.MAY_HAVE_COMMITTED,
            reason=reason,
        )

    def stats(self) -> dict[str, Any]:
        """返回分发统计快照。"""
        return dict(self._stats)


__all__ = [
    "GW_REPLY_ADAPTER_UNAVAILABLE",
    "GW_REPLY_PAYLOAD_INVALID",
    "GW_REPLY_SENSITIVE_CONTEXT",
    "OutboundDeliveryCertainty",
    "OutboundDeliveryOutcome",
    "OutboundDispatchUncertain",
    "OutboundDispatcher",
    "OutboundPreparationRejected",
    "PreparedOutboundDelivery",
    "ReplyPayloadRejected",
    "sanitize_reply_payload",
]
