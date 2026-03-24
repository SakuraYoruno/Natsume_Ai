"""QQ 平台适配器（OneBot v11 协议）。

接入方式：NapCat / Lagrange.Core → OneBot v11 HTTP Callback

Inbound（接收）：
  - OneBot v11 HTTP 上报（post_type=message）→ UnifiedMessage
  - 支持私聊（message_type=private）和群聊（message_type=group）

Outbound（发送）：
  - 调用 OneBot v11 HTTP API（send_private_msg / send_group_msg）

OneBot v11 文档：https://11.onebot.dev/
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply

logger = logging.getLogger("Natsume.edge.gateway.adapters.qq")


# OneBot v11 消息类型映射
_OB_POST_TYPE_MESSAGE = "message"
_OB_MSG_TYPE_PRIVATE = "private"
_OB_MSG_TYPE_GROUP = "group"


class QQAdapter(AbstractPlatformAdapter):
    """QQ 平台适配器（OneBot v11 HTTP 接入）。

    Args:
        onebot_api_base: OneBot HTTP API 地址（如 http://127.0.0.1:3000）
        self_id: 本机 QQ 号（用于过滤自己发出的消息）
        http_timeout_s: HTTP 请求超时（秒）
    """

    def __init__(
        self,
        onebot_api_base: str,
        *,
        self_id: str | int | None = None,
        http_timeout_s: float = 10.0,
    ) -> None:
        self._api_base = onebot_api_base.rstrip("/")
        self._self_id = str(self_id) if self_id else None
        self._timeout = http_timeout_s
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.QQ

    # ------------------------------------------------------------------
    # Inbound：OneBot v11 上报 → UnifiedMessage
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将 OneBot v11 上报的原始 payload 转换为 UnifiedMessage。

        OneBot v11 消息事件结构：
          {
            "post_type": "message",
            "message_type": "private" | "group",
            "time": 1234567890,
            "self_id": 123456,
            "user_id": 789012,
            "group_id": 111222,   # 仅群消息
            "message_id": 999,
            "raw_message": "你好",
            "message": [{"type": "text", "data": {"text": "你好"}}],
            "sender": {"user_id": 789012, "nickname": "张三"}
          }
        """
        # 只处理消息事件
        post_type = str(raw_payload.get("post_type") or "")
        if post_type != _OB_POST_TYPE_MESSAGE:
            self._stats["inbound_skipped"] += 1
            return None

        message_type = str(raw_payload.get("message_type") or "")
        user_id = str(raw_payload.get("user_id") or "")
        group_id = str(raw_payload.get("group_id") or "")
        message_id = str(raw_payload.get("message_id") or "")
        raw_message = str(raw_payload.get("raw_message") or "")
        ts_s = raw_payload.get("time")
        ts_ms = int(ts_s) * 1000 if ts_s else int(time.time() * 1000)

        # 过滤自己发出的消息
        self_id_raw = str(raw_payload.get("self_id") or "")
        if self._self_id and user_id == self._self_id:
            self._stats["inbound_skipped"] += 1
            return None
        if self_id_raw and user_id == self_id_raw:
            self._stats["inbound_skipped"] += 1
            return None

        # 会话 ID：群消息用 qq_group_{group_id}，私聊用 qq_dm_{user_id}
        is_group = message_type == _OB_MSG_TYPE_GROUP
        if is_group:
            conversation_id = f"qq_group_{group_id}"
        else:
            conversation_id = f"qq_dm_{user_id}"

        # 提取文本内容（从 message 数组中拼接 text 类型）
        content = self._extract_text_content(raw_payload, raw_message)

        # 消息类型判断（简化：有图片则为 image，否则为 text）
        msg_type = self._detect_msg_type(raw_payload)

        # 发送者昵称
        sender = raw_payload.get("sender") or {}
        sender_name = str(sender.get("nickname") or sender.get("card") or "")

        self._stats["inbound_parsed"] += 1
        return UnifiedMessage(
            msg_id=message_id,
            platform=Platform.QQ,
            from_id=user_id,
            to_id=self._self_id or self_id_raw,
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=content,
            ts_ms=ts_ms,
            is_group=is_group,
            sender_name=sender_name or None,
            raw_extra=raw_payload,
        )

    # ------------------------------------------------------------------
    # Outbound：调用 OneBot v11 HTTP API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过 OneBot v11 HTTP API 发送回复。

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功
        """
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning("QQAdapter: 暂不支持发送 %s 类型消息", reply.msg_type.value)
            return False

        try:
            success = self._send_via_onebot(reply)
            if success:
                self._stats["outbound_sent"] += 1
            else:
                self._stats["outbound_failed"] += 1
            return success
        except Exception as exc:
            logger.error("QQAdapter: 发送失败 conversation=%s: %s", reply.conversation_id, exc)
            self._stats["outbound_failed"] += 1
            return False

    def _send_via_onebot(self, reply: UnifiedReply) -> bool:
        """调用 OneBot v11 API 发送消息。"""
        conversation_id = reply.conversation_id

        # 构造消息段
        if reply.msg_type == MsgType.TEXT:
            message = [{"type": "text", "data": {"text": reply.content}}]
        elif reply.msg_type == MsgType.IMAGE and reply.media_path:
            message = [{"type": "image", "data": {"file": f"file:///{reply.media_path}"}}]
        else:
            return False

        # 判断是群消息还是私聊
        if conversation_id.startswith("qq_group_"):
            group_id = conversation_id.removeprefix("qq_group_")
            endpoint = f"{self._api_base}/send_group_msg"
            payload = {"group_id": int(group_id), "message": message}
        elif conversation_id.startswith("qq_dm_"):
            user_id = conversation_id.removeprefix("qq_dm_")
            endpoint = f"{self._api_base}/send_private_msg"
            payload = {"user_id": int(user_id), "message": message}
        else:
            logger.warning("QQAdapter: 无法识别会话 ID 格式: %s", conversation_id)
            return False

        try:
            resp = httpx.post(endpoint, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == "ok" or result.get("retcode") == 0:
                logger.debug("QQAdapter: 发送成功 conversation=%s", conversation_id)
                return True
            logger.warning("QQAdapter: OneBot API 返回失败: %s", result)
            return False
        except httpx.HTTPError as exc:
            logger.error("QQAdapter: HTTP 请求失败: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态（调用 OneBot /get_status 接口）。"""
        try:
            resp = httpx.get(f"{self._api_base}/get_status", timeout=3.0)
            resp.raise_for_status()
            ob_status = resp.json()
            return {
                "platform": self.platform_id.value,
                "status": "ok" if ob_status.get("good") else "degraded",
                "self_id": self._self_id,
                "onebot_api_base": self._api_base,
                "onebot_status": ob_status,
                "stats": dict(self._stats),
            }
        except Exception as exc:
            return {
                "platform": self.platform_id.value,
                "status": "error",
                "self_id": self._self_id,
                "onebot_api_base": self._api_base,
                "detail": str(exc),
                "stats": dict(self._stats),
            }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text_content(raw_payload: dict[str, Any], raw_message: str) -> str:
        """从 OneBot v11 message 数组中提取纯文本内容。"""
        message_arr = raw_payload.get("message")
        if isinstance(message_arr, list):
            parts: list[str] = []
            for seg in message_arr:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text = str(seg.get("data", {}).get("text") or "")
                    if text:
                        parts.append(text)
            if parts:
                return "".join(parts)
        return raw_message

    @staticmethod
    def _detect_msg_type(raw_payload: dict[str, Any]) -> MsgType:
        """从 OneBot v11 message 数组检测消息类型。"""
        message_arr = raw_payload.get("message")
        if isinstance(message_arr, list):
            types = {seg.get("type") for seg in message_arr if isinstance(seg, dict)}
            if "image" in types:
                return MsgType.IMAGE
            if "record" in types:
                return MsgType.VOICE
            if "video" in types:
                return MsgType.VIDEO
            if "file" in types:
                return MsgType.FILE
        return MsgType.TEXT
