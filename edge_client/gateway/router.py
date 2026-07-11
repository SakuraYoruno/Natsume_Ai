"""Unified Message Gateway router."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import logging
import time
from typing import Any

from edge_client.channels.input_reporter import (
    EdgeInputReporter,
    InputReportResult,
    InputSourceType,
)
from edge_client.gateway.models import MsgType, UnifiedMessage
from edge_client.tribe.channel_agent import ChannelAgent
from edge_client.tribe.peripheral_agent import PeripheralAgent

logger = logging.getLogger("Natsume.edge.gateway.router")


class GatewayRouter:
    """Route normalized gateway messages into Host Brain input."""

    def __init__(
        self,
        reporter: EdgeInputReporter,
        tribe_bridge: Any | None = None,
        voice_asr: Any | None = None,
    ) -> None:
        self._reporter = reporter
        self._tribe_bridge = tribe_bridge
        self._voice_asr = voice_asr
        self._channel_agent = ChannelAgent(bridge=tribe_bridge)
        self._peripheral_agent = PeripheralAgent(bridge=tribe_bridge)
        self._stats: dict[str, int] = {
            "routed": 0,
            "failed": 0,
            "cached": 0,
        }
        self._last_route: dict[str, Any] | None = None

    def set_tribe_bridge(self, bridge: Any | None) -> None:
        self._tribe_bridge = bridge
        self._channel_agent.set_bridge(bridge)
        self._peripheral_agent.set_bridge(bridge)

    def route(self, msg: UnifiedMessage) -> InputReportResult:
        routed_msg = self._maybe_apply_voice_asr(msg)
        payload = routed_msg.as_report_payload()

        report_payload = getattr(self._reporter, "report_gateway_payload", None)
        if callable(report_payload):
            result = report_payload(payload)
        else:
            result = InputReportResult(
                success=False,
                source=InputSourceType.GATEWAY,
                reason_code="REPORTER_CONTRACT_UNAVAILABLE",
                detail="report_gateway_payload is not available",
                cached=False,
            )
        if result.success:
            self._stats["routed"] += 1
        elif result.cached:
            self._stats["cached"] += 1
        else:
            self._stats["failed"] += 1
        self._last_route = {
            "platform": routed_msg.platform.value,
            "conversation_id": routed_msg.conversation_id,
            "msg_id": routed_msg.msg_id,
            "msg_type": routed_msg.msg_type.value,
            "content_len": len(str(routed_msg.content or "")),
            "content_sha256": hashlib.sha256(
                str(routed_msg.content or "").encode("utf-8")
            ).hexdigest(),
            "success": bool(result.success),
            "cached": bool(result.cached),
            "reason_code": result.reason_code,
            "detail": str(result.detail or "")[:240],
            "ts_ms": int(time.time() * 1000),
        }

        logger.debug(
            "GatewayRouter: platform=%s msg_id=%s -> %s",
            routed_msg.platform.value,
            routed_msg.msg_id,
            "ok" if result.success else ("cached" if result.cached else "failed"),
        )

        # Phase XV：收到消息时向 PeripheralAgent 发布 VTS 感知事件
        # 让 Natsume 在收到社交消息时做对应的感知动作（看到消息/听到语音）
        try:
            asr_info = dict(routed_msg.raw_extra.get("gateway_voice_asr") or {})
            if routed_msg.msg_type == MsgType.VOICE and asr_info.get("text"):
                # 语音消息：ASR 识别后发布“听到声音”感知事件
                self._peripheral_agent.publish_voice_received(
                    platform=routed_msg.platform.value,
                    transcript=str(asr_info["text"]),
                    sender=str(routed_msg.sender_name or routed_msg.from_id or ""),
                )
            else:
                # 文字/图片/其他消息：发布“看到消息”感知事件
                self._peripheral_agent.publish_message_received(
                    platform=routed_msg.platform.value,
                    msg_type=routed_msg.msg_type.value,
                    sender=str(routed_msg.sender_name or routed_msg.from_id or ""),
                )
        except Exception as exc:
            logger.debug("GatewayRouter: peripheral VTS event failed msg_id=%s err=%s", routed_msg.msg_id, exc)

        self._channel_agent.on_message_received(
            platform=routed_msg.platform.value,
            conversation_id=routed_msg.conversation_id,
            msg_id=routed_msg.msg_id,
            msg_type=routed_msg.msg_type.value,
            is_group=bool(routed_msg.is_group),
            ts_ms=int(routed_msg.ts_ms),
            routed_success=bool(result.success),
            cached=bool(result.cached),
        )
        if (not result.success) or bool(result.cached):
            self._channel_agent.on_gateway_degraded(
                platform=routed_msg.platform.value,
                conversation_id=routed_msg.conversation_id,
                msg_id=routed_msg.msg_id,
                reason_code=result.reason_code,
                detail=result.detail,
                cached=bool(result.cached),
            )
        return result

    def _maybe_apply_voice_asr(self, msg: UnifiedMessage) -> UnifiedMessage:
        if self._voice_asr is None:
            return msg
        if msg.msg_type != MsgType.VOICE:
            return msg
        transcribe_fn = getattr(self._voice_asr, "transcribe", None)
        if not callable(transcribe_fn):
            return msg
        try:
            asr_result = transcribe_fn(msg)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("GatewayRouter: voice ASR degraded msg_id=%s err=%s", msg.msg_id, exc)
            return msg
        if not isinstance(asr_result, dict):
            return msg

        text = str(asr_result.get("text") or "").strip()
        if not text:
            return msg

        raw_extra = dict(msg.raw_extra or {})
        raw_extra["gateway_voice_asr"] = {
            "text": text,
            "confidence": asr_result.get("confidence"),
            "language": asr_result.get("language"),
            "media_ref": asr_result.get("media_ref"),
            "segments": asr_result.get("segments"),
        }
        attachments = list(msg.attachments or [])
        if attachments:
            patched_attachments = []
            patched = False
            for attachment in attachments:
                if str(getattr(attachment, "type", "") or "").strip().lower() == "voice":
                    patched_attachments.append(replace(attachment, transcript=text))
                    patched = True
                else:
                    patched_attachments.append(attachment)
            if patched:
                attachments = patched_attachments
        logger.info(
            "GatewayRouter: voice ASR transcript applied platform=%s msg_id=%s len=%d",
            msg.platform.value,
            msg.msg_id,
            len(text),
        )
        return replace(msg, content=text, raw_extra=raw_extra, attachments=attachments)

    def stats(self) -> dict[str, Any]:
        reporter_stats: dict[str, Any] = {}
        stats_fn = getattr(self._reporter, "stats", None)
        if callable(stats_fn):
            try:
                reporter_stats = dict(stats_fn() or {})
            except Exception as exc:  # pragma: no cover - observability only
                reporter_stats = {"error": str(exc)}
        return {
            **self._stats,
            "last_route": dict(self._last_route or {}),
            "reporter": reporter_stats,
        }
