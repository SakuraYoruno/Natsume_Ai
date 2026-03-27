"""WeChat adapter for gateway bridge mode.

This adapter keeps only the pieces required by the current architecture:
- inbound webhook payload normalization
- optional WxDbService -> UnifiedMessage conversion
- outbound command queue dispatch to the pure UIA bridge
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.gateway.normalizer import _PLACEHOLDER_CONVERSATION_IDS
from edge_client.services.wx_db_service import WxDbService, WxMessage

logger = logging.getLogger("Natsume.edge.gateway.adapters.wechat")


class WeChatAdapter(AbstractPlatformAdapter):
    def __init__(
        self,
        wx_db_service: WxDbService | None,
        *,
        self_wxid: str | None = None,
        bridge_mode: bool | None = None,
        db_watch_enabled: bool | None = None,
        bridge_inbound_backend: str | None = None,
    ) -> None:
        self._svc = wx_db_service
        self._self_wxid = str(self_wxid or "").strip() or None
        self._bridge_mode = bool(self._bridge_queue_enabled() if bridge_mode is None else bridge_mode)
        self._db_watch_enabled = bool((wx_db_service is not None) and (not self._bridge_mode)) if db_watch_enabled is None else bool(db_watch_enabled)
        self._bridge_inbound_backend_override = str(bridge_inbound_backend or "").strip().lower() or None
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }
        self._last_outbound: dict[str, Any] = {
            "success": None,
            "detail": "",
            "elapsed_ms": 0,
            "ts_ms": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.WECHAT

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        try:
            conversation_id = self._resolve_inbound_conversation_id(raw_payload)
            if self._is_placeholder_conversation_id(conversation_id):
                self._stats["inbound_skipped"] += 1
                return None
            from_wxid = self._resolve_inbound_from_wxid(raw_payload)
            if self._self_wxid and from_wxid == self._self_wxid:
                self._stats["inbound_skipped"] += 1
                return None
            msg_type = MsgType.from_wx_type(self._resolve_inbound_msg_type(raw_payload))
            if msg_type == MsgType.SYSTEM:
                self._stats["inbound_skipped"] += 1
                return None
            content = self._resolve_inbound_content(raw_payload)
            if msg_type == MsgType.TEXT and (not content):
                self._stats["inbound_skipped"] += 1
                return None
            ts_ms = self._resolve_inbound_ts_ms(raw_payload)
            sender_name = str(raw_payload.get("sender_name") or raw_payload.get("sender") or from_wxid or "").strip() or None
            is_group = (
                conversation_id.endswith("@chatroom")
                or bool(raw_payload.get("is_group_hint"))
            )
            self._stats["inbound_parsed"] += 1
            return UnifiedMessage(
                msg_id=str(raw_payload.get("msg_id") or raw_payload.get("id") or f"wechat-{ts_ms}"),
                platform=Platform.WECHAT,
                from_id=from_wxid or conversation_id,
                to_id=self._resolve_inbound_to_wxid(raw_payload),
                conversation_id=conversation_id,
                msg_type=msg_type,
                content=content,
                ts_ms=ts_ms,
                is_group=is_group,
                media_url=str(raw_payload.get("media_url") or "").strip() or None,
                sender_name=sender_name,
                raw_extra=dict(raw_payload),
            )
        except Exception as exc:
            logger.warning("WeChatAdapter: parse_inbound failed: %s", exc)
            self._stats["inbound_skipped"] += 1
            return None

    def convert_wx_message(self, wx_msg: WxMessage) -> UnifiedMessage | None:
        conversation_id = str(wx_msg.conversation_id or "").strip()
        if self._is_placeholder_conversation_id(conversation_id):
            self._stats["inbound_skipped"] += 1
            return None
        if wx_msg.is_sender or (self._self_wxid and str(wx_msg.from_wxid or "").strip() == self._self_wxid):
            self._stats["inbound_skipped"] += 1
            return None
        msg_type = MsgType.from_wx_type(wx_msg.msg_type)
        if msg_type == MsgType.SYSTEM:
            self._stats["inbound_skipped"] += 1
            return None
        if msg_type == MsgType.TEXT and (not str(wx_msg.content or "").strip()):
            self._stats["inbound_skipped"] += 1
            return None
        self._stats["inbound_parsed"] += 1
        return UnifiedMessage(
            msg_id=str(wx_msg.msg_id),
            platform=Platform.WECHAT,
            from_id=wx_msg.from_wxid or conversation_id,
            to_id=wx_msg.to_wxid,
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=str(wx_msg.content or ""),
            ts_ms=int(wx_msg.ts_ms),
            is_group=conversation_id.endswith("@chatroom"),
            media_url=wx_msg.media_url,
            sender_name=str((wx_msg.raw or {}).get("sender_name") or wx_msg.from_wxid or "").strip() or None,
            raw_extra=dict(wx_msg.raw or {}),
        )

    def send_reply(self, reply: UnifiedReply) -> bool:
        started_ms = int(time.time() * 1000)
        success = False
        detail = ""
        try:
            if reply.msg_type == MsgType.TEXT:
                if self._bridge_queue_enabled():
                    # 拟人化分段发送（公共模块）
                    success = self._send_text_humanized(reply)
                    detail = "bridge_queue_humanized"
                    return success
                detail = "bridge_queue_not_enabled"
                logger.warning(
                    "WeChatAdapter: bridge queue not enabled — set EDGE_WECHAT_BRIDGE_ENABLED=true and EDGE_WECHAT_BRIDGE_BACKEND=uia|wxautox4"
                )
                return False
            if reply.msg_type == MsgType.IMAGE:
                if self._bridge_queue_enabled():
                    success = self._send_image_via_bridge_queue(reply)
                    detail = "bridge_queue_image"
                    return success
                detail = "bridge_queue_not_enabled"
                return False
            if reply.msg_type == MsgType.VOICE:
                if self._bridge_queue_enabled():
                    success = self._send_voice_via_bridge_queue(reply)
                    detail = "bridge_queue_voice"
                    return success
                detail = "bridge_queue_not_enabled"
                return False
            if reply.msg_type == MsgType.FILE:  # Phase 1
                if self._bridge_queue_enabled():
                    success = self._send_file_via_bridge_queue(reply)
                    detail = "bridge_queue_file"
                    return success
                detail = "bridge_queue_not_enabled"
                return False
            detail = f"unsupported msg_type={reply.msg_type.value}"
            return False
        except Exception as exc:
            detail = str(exc)
            logger.error("WeChatAdapter: send failed conversation=%s err=%s", reply.conversation_id, exc)
            return False
        finally:
            elapsed = max(0, int(time.time() * 1000) - started_ms)
            if success:
                self._stats["outbound_sent"] += 1
            else:
                self._stats["outbound_failed"] += 1
            self._last_outbound = {
                "success": success,
                "detail": detail,
                "elapsed_ms": elapsed,
                "ts_ms": int(time.time() * 1000),
            }

    # ── Phase 1: query helpers ────────────────────────────────────────────────

    def query_bridge(self, action: str, target: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch a query-type command to the bridge and wait for result.

        Phase 1 supported actions: get_my_info, get_friends, get_groups,
        get_recent_chats, get_history_messages, listen_add, listen_remove,
        listen_stop, chat_with.

        Returns the result dict from the JSONL result file, or an error dict.
        """
        from edge_client.channels.wechat_command_queue import (
            enqueue_wechat_command,
            resolve_command_file_paths,
            wait_wechat_command_result,
        )
        if not self._bridge_queue_enabled():
            return {"success": False, "reason_code": "BRIDGE_NOT_ENABLED", "detail": "bridge queue not enabled"}
        queue_file, result_file = resolve_command_file_paths()
        now_ms = int(time.time() * 1000)
        row = enqueue_wechat_command(
            action=action,
            target=target,
            content=str((extra or {}).get("content") or ""),
            request_id=f"gateway-query-{now_ms}-{uuid.uuid4().hex[:8]}",
            queue_file=queue_file,
            ts_ms=now_ms,
            extra_fields={k: v for k, v in (extra or {}).items() if k != "content"} or None,
        )
        timeout_ms = self._resolve_command_timeout_ms() * 3  # queries may take longer
        result = wait_wechat_command_result(
            command_id=str(row.get("command_id") or ""),
            timeout_ms=timeout_ms,
            result_file=result_file,
        )
        return dict(result or {"success": False, "reason_code": "TIMEOUT", "detail": "no result within timeout"})

    def get_my_info(self) -> dict[str, Any]:
        """Return WeChat self account info via bridge. Phase 1."""
        return self.query_bridge("get_my_info")

    def get_friends(self) -> dict[str, Any]:
        """Return friend list via bridge. Phase 1."""
        return self.query_bridge("get_friends")

    def get_groups(self) -> dict[str, Any]:
        """Return recent group list via bridge. Phase 1."""
        return self.query_bridge("get_groups")

    def get_recent_chats(self) -> dict[str, Any]:
        """Return recent chat sessions via bridge. Phase 1."""
        return self.query_bridge("get_recent_chats")

    def get_history_messages(self, target: str, savepath: str = "") -> dict[str, Any]:
        """Return history messages for a chat target via bridge. Phase 1."""
        extra: dict[str, Any] = {}
        if savepath:
            extra["savepath"] = savepath
        return self.query_bridge("get_history_messages", target=target, extra=extra or None)

    def health(self) -> dict[str, Any]:
        bridge_queue_enabled = self._bridge_queue_enabled()
        db_stats: dict[str, Any] = {}
        if self._svc is not None:
            try:
                db_stats = dict(self._svc.stats() or {})
            except Exception as exc:
                db_stats = {"error": str(exc)}
        db_stats["bridge_mode"] = bool(self._bridge_mode)
        db_stats["db_watch_enabled"] = bool(self._db_watch_enabled)
        return {
            "platform": self.platform_id.value,
            "status": "ok",
            "self_wxid": self._self_wxid,
            "db_stats": db_stats,
            "stats": dict(self._stats),
            "last_outbound": dict(self._last_outbound),
            "bridge_mode": bool(self._bridge_mode),
            "bridge_queue_enabled": bridge_queue_enabled,
            "bridge_backend": self._bridge_backend(),
            "bridge_inbound_backend": self._effective_bridge_inbound_backend(),
            "outbound_mode": ("bridge_queue" if bridge_queue_enabled else "legacy_disabled"),
            "inbound_mode": ("webhook_only" if (not self._db_watch_enabled) else "db_watch_and_webhook"),
        }

    @staticmethod
    def _bridge_backend() -> str:
        return str(os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia") or "uia").strip().lower() or "uia"

    @staticmethod
    def _bridge_inbound_backend() -> str:
        return str(os.environ.get("EDGE_WECHAT_INBOUND_BACKEND", "webhook") or "webhook").strip().lower() or "webhook"

    def _effective_bridge_inbound_backend(self) -> str:
        return self._bridge_inbound_backend_override or self._bridge_inbound_backend()

    @staticmethod
    def _bridge_queue_enabled() -> bool:
        enabled = str(os.environ.get("EDGE_WECHAT_BRIDGE_ENABLED") or "true").strip().lower()
        backend = WeChatAdapter._bridge_backend()
        return enabled not in {"0", "false", "no", "off"} and backend in {"uia", "wxautox4"}

    @staticmethod
    def _resolve_command_timeout_ms() -> int:
        raw = str(os.environ.get("EDGE_WECHAT_COMMAND_TIMEOUT_MS") or "8000").strip()
        try:
            return max(500, int(float(raw)))
        except Exception:
            return 8000

    def _build_search_candidates(self, conversation_id: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for raw in [conversation_id]:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(text)
        return values

    def _send_via_bridge_queue(self, reply: UnifiedReply) -> bool:
        from edge_client.channels.wechat_command_queue import (
            enqueue_wechat_command,
            resolve_command_file_paths,
            wait_wechat_command_result,
        )

        queue_file, result_file = resolve_command_file_paths()
        now_ms = int(time.time() * 1000)
        request_id = str(reply.raw_extra.get("request_id") or f"gateway-reply-{now_ms}-{uuid.uuid4().hex[:8]}")
        target_candidates = self._build_search_candidates(reply.conversation_id)
        row = enqueue_wechat_command(
            action="send_text",
            target=reply.conversation_id,
            target_candidates=target_candidates,
            content=reply.content,
            request_id=request_id,
            queue_file=queue_file,
            ts_ms=now_ms,
        )
        result = wait_wechat_command_result(
            command_id=str(row.get("command_id") or ""),
            timeout_ms=self._resolve_command_timeout_ms(),
            result_file=result_file,
        )
        return bool((result or {}).get("success"))

    def _send_image_via_bridge_queue(self, reply: UnifiedReply) -> bool:
        """Send an image via the UIA bridge command queue.

        The image file path is expected in ``reply.media_path``.
        Falls back to ``reply.content`` if ``media_path`` is not set.
        """
        from edge_client.channels.wechat_command_queue import (
            enqueue_wechat_command,
            resolve_command_file_paths,
            wait_wechat_command_result,
        )

        media_path = str(reply.media_path or reply.content or "").strip()
        if not media_path:
            logger.warning("WeChatAdapter: send_image called but no media_path provided")
            return False

        queue_file, result_file = resolve_command_file_paths()
        now_ms = int(time.time() * 1000)
        request_id = str(reply.raw_extra.get("request_id") or f"gateway-image-{now_ms}-{uuid.uuid4().hex[:8]}")
        target_candidates = self._build_search_candidates(reply.conversation_id)
        row = enqueue_wechat_command(
            action="send_image",
            target=reply.conversation_id,
            target_candidates=target_candidates,
            content=media_path,
            request_id=request_id,
            queue_file=queue_file,
            ts_ms=now_ms,
        )
        result = wait_wechat_command_result(
            command_id=str(row.get("command_id") or ""),
            timeout_ms=self._resolve_command_timeout_ms(),
            result_file=result_file,
        )
        return bool((result or {}).get("success"))

    def _send_voice_via_bridge_queue(self, reply: UnifiedReply) -> bool:
        """Send a voice message (.silk file) via the UIA bridge command queue.

        The voice file path is expected in ``reply.media_path``.
        Falls back to ``reply.content`` if ``media_path`` is not set.

        If ``reply.media_data`` contains raw PCM bytes (from TTS synthesis),
        the method will encode them to .silk format, save to a temp file,
        and dispatch via the bridge queue.
        """
        from edge_client.channels.wechat_command_queue import (
            enqueue_wechat_command,
            resolve_command_file_paths,
            wait_wechat_command_result,
        )

        media_path = str(reply.media_path or "").strip()

        # Fallback: if no file path but we have raw PCM audio data, encode to .silk
        if not media_path and reply.media_data:
            from edge_client.gateway.media.voice_handler import encode_pcm_to_silk, save_silk_file
            sample_rate = int(reply.raw_extra.get("sample_rate", 16000))
            silk_data = encode_pcm_to_silk(reply.media_data, sample_rate=sample_rate)
            if silk_data is None:
                logger.warning("WeChatAdapter: silk encoding failed for voice reply")
                return False
            silk_path = save_silk_file(silk_data)
            if silk_path is None:
                logger.warning("WeChatAdapter: failed to save .silk temp file")
                return False
            media_path = str(silk_path)

        if not media_path:
            # Last resort: treat content as file path
            media_path = str(reply.content or "").strip()

        if not media_path:
            logger.warning("WeChatAdapter: send_voice called but no media_path or media_data provided")
            return False

        queue_file, result_file = resolve_command_file_paths()
        now_ms = int(time.time() * 1000)
        request_id = str(reply.raw_extra.get("request_id") or f"gateway-voice-{now_ms}-{uuid.uuid4().hex[:8]}")
        target_candidates = self._build_search_candidates(reply.conversation_id)
        row = enqueue_wechat_command(
            action="send_voice",
            target=reply.conversation_id,
            target_candidates=target_candidates,
            content=media_path,
            request_id=request_id,
            queue_file=queue_file,
            ts_ms=now_ms,
        )
        result = wait_wechat_command_result(
            command_id=str(row.get("command_id") or ""),
            timeout_ms=self._resolve_command_timeout_ms(),
            result_file=result_file,
        )
        return bool((result or {}).get("success"))

    def _send_text_humanized(self, reply: UnifiedReply) -> bool:
        """使用公共拟人化模块分段发送文字消息。

        将完整回复拆分为多段，逐段通过 bridge 队列发送，
        段间模拟打字延迟，让对话更自然。
        """
        from edge_client.gateway.humanize_sender import (
            humanize_send_sync,
            HumanizeSendConfig,
        )

        def _send_one_segment(segment: str) -> bool:
            """发送单段文字到 bridge 队列。"""
            from edge_client.channels.wechat_command_queue import (
                enqueue_wechat_command,
                resolve_command_file_paths,
                wait_wechat_command_result,
            )
            queue_file, result_file = resolve_command_file_paths()
            now_ms = int(time.time() * 1000)
            request_id = str(
                reply.raw_extra.get("request_id")
                or f"gateway-reply-{now_ms}-{uuid.uuid4().hex[:8]}"
            )
            target_candidates = self._build_search_candidates(reply.conversation_id)
            row = enqueue_wechat_command(
                action="send_text",
                target=reply.conversation_id,
                target_candidates=target_candidates,
                content=segment,
                request_id=request_id,
                queue_file=queue_file,
                ts_ms=now_ms,
            )
            result = wait_wechat_command_result(
                command_id=str(row.get("command_id") or ""),
                timeout_ms=self._resolve_command_timeout_ms(),
                result_file=result_file,
            )
            return bool((result or {}).get("success"))

        config = HumanizeSendConfig.from_env()
        send_result = humanize_send_sync(
            text=reply.content,
            send_fn=_send_one_segment,
            config=config,
        )

        if send_result.success:
            logger.info(
                "WeChatAdapter: humanized send OK segments=%d delay=%dms",
                send_result.sent_segments, send_result.total_delay_ms,
            )
        else:
            logger.warning(
                "WeChatAdapter: humanized send failed at segment %d/%d: %s",
                send_result.failed_segment_index + 1,
                send_result.total_segments,
                send_result.error[:100],
            )
        return send_result.success

    def _send_file_via_bridge_queue(self, reply: UnifiedReply) -> bool:  # Phase 1
        """Send a generic file via the bridge command queue.

        The file path is expected in ``reply.media_path``.
        Falls back to ``reply.content`` if ``media_path`` is not set.
        """
        from edge_client.channels.wechat_command_queue import (
            enqueue_wechat_command,
            resolve_command_file_paths,
            wait_wechat_command_result,
        )

        media_path = str(reply.media_path or reply.content or "").strip()
        if not media_path:
            logger.warning("WeChatAdapter: send_file called but no media_path provided")
            return False

        queue_file, result_file = resolve_command_file_paths()
        now_ms = int(time.time() * 1000)
        request_id = str(reply.raw_extra.get("request_id") or f"gateway-file-{now_ms}-{uuid.uuid4().hex[:8]}")
        target_candidates = self._build_search_candidates(reply.conversation_id)
        row = enqueue_wechat_command(
            action="send_file",
            target=reply.conversation_id,
            target_candidates=target_candidates,
            content=media_path,
            request_id=request_id,
            queue_file=queue_file,
            ts_ms=now_ms,
        )
        result = wait_wechat_command_result(
            command_id=str(row.get("command_id") or ""),
            timeout_ms=self._resolve_command_timeout_ms(),
            result_file=result_file,
        )
        return bool((result or {}).get("success"))

    # NOTE: Legacy _send_via_pywinauto has been removed.
    # All outbound messages go through the UIA Bridge command queue.

    @staticmethod
    def _is_placeholder_conversation_id(conversation_id: str) -> bool:
        normalized = str(conversation_id or "").strip().lower()
        return normalized in _PLACEHOLDER_CONVERSATION_IDS

    @staticmethod
    def _resolve_inbound_conversation_id(raw_payload: dict[str, Any]) -> str:
        for item in (
            raw_payload.get("conversation_id"),
            raw_payload.get("roomid"),
            raw_payload.get("StrTalker"),
            raw_payload.get("from_wxid"),
            raw_payload.get("sender"),
        ):
            value = str(item or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _resolve_inbound_from_wxid(raw_payload: dict[str, Any]) -> str:
        for item in (
            raw_payload.get("from_wxid"),
            raw_payload.get("sender"),
            raw_payload.get("sender_name"),
            raw_payload.get("StrTalker"),
        ):
            value = str(item or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _resolve_inbound_to_wxid(raw_payload: dict[str, Any]) -> str:
        for item in (raw_payload.get("to_wxid"), raw_payload.get("receiver")):
            value = str(item or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _resolve_inbound_content(raw_payload: dict[str, Any]) -> str:
        for item in (raw_payload.get("content"), raw_payload.get("text"), raw_payload.get("StrContent")):
            value = str(item or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _resolve_inbound_msg_type(raw_payload: dict[str, Any]) -> int:
        type_map = {
            "text": 1,
            "image": 3,
            "voice": 34,
            "video": 43,
            "emoji": 47,
            "link": 49,
            "system": 10000,
        }
        for key in ("msg_type", "type", "Type"):
            raw_value = raw_payload.get(key)
            if raw_value is None or raw_value == "":
                continue
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                mapped = type_map.get(str(raw_value).strip().lower())
                if mapped is not None:
                    return mapped
        return 1

    @staticmethod
    def _resolve_inbound_ts_ms(raw_payload: dict[str, Any]) -> int:
        for key, multiplier in (("ts_ms", 1), ("ts", 1000), ("CreateTime", 1000)):
            raw_value = raw_payload.get(key)
            if raw_value is None or raw_value == "":
                continue
            try:
                return int(float(raw_value) * multiplier)
            except (TypeError, ValueError):
                continue
        return int(time.time() * 1000)
