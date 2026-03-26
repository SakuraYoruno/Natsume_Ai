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
from edge_client.services.wx_db_service import WxDbService, WxMessage

logger = logging.getLogger("Natsume.edge.gateway.adapters.wechat")

_PLACEHOLDER_CONVERSATION_IDS: set[str] = {
    "active_chat",
    "active chat",
    "current_chat",
    "current chat",
    "current_session",
    "default",
    "unknown",
    "none",
    "null",
    "-",
    "_",
    "na",
    "n/a",
    "当前聊天",
    "当前会话",
}


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
                is_group=conversation_id.endswith("@chatroom"),
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
            if reply.msg_type != MsgType.TEXT:
                detail = f"unsupported msg_type={reply.msg_type.value}"
                return False
            if self._bridge_queue_enabled():
                success = self._send_via_bridge_queue(reply)
                detail = "bridge_queue"
                return success
            success = self._send_via_pywinauto(reply)
            detail = "legacy_fallback"
            return success
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
        return enabled not in {"0", "false", "no", "off"} and backend == "uia"

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

    def _send_via_pywinauto(self, _reply: UnifiedReply) -> bool:
        logger.warning("WeChatAdapter: legacy pywinauto path has been retired; use bridge mode")
        return False

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
