"""Unified Message Gateway router."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from edge_client.channels.input_reporter import EdgeInputReporter, InputReportResult
from edge_client.gateway.models import MsgType, UnifiedMessage
from edge_client.tribe.channel_agent import ChannelAgent

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
        self._stats: dict[str, int] = {
            "routed": 0,
            "failed": 0,
            "cached": 0,
        }

    def set_tribe_bridge(self, bridge: Any | None) -> None:
        self._tribe_bridge = bridge
        self._channel_agent.set_bridge(bridge)

    def route(self, msg: UnifiedMessage) -> InputReportResult:
        routed_msg = self._maybe_apply_voice_asr(msg)
        payload = routed_msg.as_report_payload()

        from edge_client.channels.input_reporter import InputSourceType

        result = self._reporter._send(payload, InputSourceType.GATEWAY)
        if result.success:
            self._stats["routed"] += 1
        elif result.cached:
            self._stats["cached"] += 1
        else:
            self._stats["failed"] += 1

        logger.debug(
            "GatewayRouter: platform=%s msg_id=%s -> %s",
            routed_msg.platform.value,
            routed_msg.msg_id,
            "ok" if result.success else ("cached" if result.cached else "failed"),
        )
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
        logger.info(
            "GatewayRouter: voice ASR transcript applied platform=%s msg_id=%s len=%d",
            msg.platform.value,
            msg.msg_id,
            len(text),
        )
        return replace(msg, content=text, raw_extra=raw_extra)

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

