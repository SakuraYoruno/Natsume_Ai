"""QQ 平台适配器（OneBot v11 协议）。

接入方式：NapCat / Lagrange.Core → OneBot v11 HTTP Callback

Inbound（接收）：
  - OneBot v11 HTTP 上报（post_type=message）→ UnifiedMessage
  - 支持私聊（message_type=private）和群聊（message_type=group）
  - 处理 @ 提醒消息段（type=at）
  - 记录 notice / request 事件到日志

Outbound（发送）：
  - 调用 OneBot v11 HTTP API（send_private_msg / send_group_msg）
  - 支持 TEXT / IMAGE / VOICE 消息类型
  - IMAGE 支持 file:/// / http(s):// / base64:// 三种协议

OneBot v11 文档：https://11.onebot.dev/
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply

logger = logging.getLogger("Natsume.edge.gateway.adapters.qq")


# OneBot v11 消息类型映射
_OB_POST_TYPE_MESSAGE = "message"
_OB_POST_TYPE_NOTICE = "notice"
_OB_POST_TYPE_REQUEST = "request"
_OB_MSG_TYPE_PRIVATE = "private"
_OB_MSG_TYPE_GROUP = "group"

# 支持发送的消息类型
_SENDABLE_MSG_TYPES = frozenset({MsgType.TEXT, MsgType.IMAGE, MsgType.VOICE})


class QQAdapter(AbstractPlatformAdapter):
    """QQ 平台适配器（OneBot v11 HTTP 接入）。

    Args:
        onebot_api_base: OneBot HTTP API 地址（如 http://127.0.0.1:3000）
        self_id: 本机 QQ 号（用于过滤自己发出的消息和识别 @ 自己）
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
            "inbound_notice": 0,
            "inbound_request": 0,
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

        支持三种 post_type：
        - ``message``：消息事件 → 转换为 UnifiedMessage 返回
        - ``notice``：通知事件（撤回、表情回应、群变动等）→ 记录日志后返回 None
        - ``request``：请求事件（加好友、加群等）→ 记录日志后返回 None
        """
        post_type = str(raw_payload.get("post_type") or "")

        # ── notice / request 事件：记日志但不生成 UnifiedMessage ──
        if post_type == _OB_POST_TYPE_NOTICE:
            self._handle_notice(raw_payload)
            return None
        if post_type == _OB_POST_TYPE_REQUEST:
            self._handle_request(raw_payload)
            return None

        # ── 非 message 事件直接跳过 ──
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

        # 提取文本内容（过滤 @ 段，仅保留纯文本）
        content = self._extract_text_content(raw_payload, raw_message)

        # 检查是否 @ 了自己（群消息中）
        is_at_self = is_group and self._check_at_self(raw_payload, self_id_raw)

        # 消息类型判断
        msg_type = self._detect_msg_type(raw_payload)

        # 发送者昵称
        sender = raw_payload.get("sender") or {}
        sender_name = str(sender.get("nickname") or sender.get("card") or "")

        # 将 @ 自己的标记放入 raw_extra 供上层使用
        enriched_extra = dict(raw_payload)
        if is_at_self:
            enriched_extra["_at_self"] = True

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
            raw_extra=enriched_extra,
        )

    # ------------------------------------------------------------------
    # Outbound：调用 OneBot v11 HTTP API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过 OneBot v11 HTTP API 发送回复。

        支持的消息类型：TEXT / IMAGE / VOICE。
        IMAGE 支持三种来源：本地文件（media_path）、URL（content 中的 http 链接）、
        二进制数据（media_data → base64 编码）。

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功
        """
        if reply.msg_type not in _SENDABLE_MSG_TYPES:
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
        message = self._build_message_segments(reply)
        if not message:
            logger.warning("QQAdapter: 无法构造消息段 type=%s", reply.msg_type.value)
            return False

        # 判断是群消息还是私聊
        if conversation_id.startswith("qq_group_"):
            group_id = conversation_id.removeprefix("qq_group_")
            endpoint = f"{self._api_base}/send_group_msg"
            payload: dict[str, Any] = {"group_id": int(group_id), "message": message}
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
                logger.debug("QQAdapter: 发送成功 conversation=%s type=%s", conversation_id, reply.msg_type.value)
                return True
            logger.warning("QQAdapter: OneBot API 返回失败: %s", result)
            return False
        except httpx.HTTPError as exc:
            logger.error("QQAdapter: HTTP 请求失败: %s", exc)
            return False

    def _build_message_segments(self, reply: UnifiedReply) -> list[dict[str, Any]]:
        """根据 UnifiedReply 构造 OneBot v11 消息段数组。

        Returns:
            消息段列表；空列表表示无法构造。
        """
        segments: list[dict[str, Any]] = []

        # ── TEXT ──
        if reply.msg_type == MsgType.TEXT:
            if reply.content:
                segments.append({"type": "text", "data": {"text": reply.content}})

        # ── IMAGE ──
        elif reply.msg_type == MsgType.IMAGE:
            file_uri = self._resolve_media_uri(reply, fallback_type="image")
            if file_uri:
                segments.append({"type": "image", "data": {"file": file_uri}})

        # ── VOICE（OneBot v11 使用 record 消息段）──
        elif reply.msg_type == MsgType.VOICE:
            file_uri = self._resolve_media_uri(reply, fallback_type="record")
            if file_uri:
                segments.append({"type": "record", "data": {"file": file_uri}})

        return segments

    @staticmethod
    def _resolve_media_uri(reply: UnifiedReply, *, fallback_type: str = "image") -> str | None:
        """从 UnifiedReply 解析媒体文件 URI。

        优先级：
        1. media_path（本地文件）→ file:///
        2. content 以 http:// 或 https:// 开头 → 直接使用 URL
        3. media_data（二进制）→ base64://
        """
        # 本地文件
        if reply.media_path:
            path = reply.media_path.replace("\\", "/")
            if path.startswith("file:///"):
                return path
            return f"file:///{path}"

        # URL（content 字段可能是远程 URL）
        content = (reply.content or "").strip()
        if content.startswith("http://") or content.startswith("https://"):
            return content

        # 二进制 → base64
        if reply.media_data:
            b64 = base64.b64encode(reply.media_data).decode("ascii")
            return f"base64://{b64}"

        return None

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
    # Notice / Request 事件处理
    # ------------------------------------------------------------------

    def _handle_notice(self, raw_payload: dict[str, Any]) -> None:
        """处理 OneBot v11 notice 事件（撤回、表情回应、群变动等）。

        当前仅记录到日志供上层参考，未来可扩展为生成系统消息。
        """
        self._stats["inbound_notice"] += 1
        notice_type = raw_payload.get("notice_type", "unknown")
        sub_type = raw_payload.get("sub_type", "")
        user_id = raw_payload.get("user_id", "")
        group_id = raw_payload.get("group_id", "")

        if notice_type == "group_recall":
            operator_id = raw_payload.get("operator_id", "")
            message_id = raw_payload.get("message_id", "")
            logger.info(
                "QQAdapter notice: 群 %s 消息撤回 msg_id=%s operator=%s user=%s",
                group_id, message_id, operator_id, user_id,
            )
        elif notice_type == "friend_recall":
            message_id = raw_payload.get("message_id", "")
            logger.info(
                "QQAdapter notice: 好友 %s 消息撤回 msg_id=%s",
                user_id, message_id,
            )
        elif notice_type in ("group_increase", "group_decrease"):
            operator_id = raw_payload.get("operator_id", "")
            logger.info(
                "QQAdapter notice: 群 %s 成员变动 type=%s sub=%s user=%s operator=%s",
                group_id, notice_type, sub_type, user_id, operator_id,
            )
        elif notice_type == "notify":
            # 戳一戳、群荣誉等
            sub_type = raw_payload.get("sub_type", "")
            logger.info(
                "QQAdapter notice: 通知 sub_type=%s group=%s user=%s payload_keys=%s",
                sub_type, group_id, user_id, list(raw_payload.keys()),
            )
        else:
            logger.info(
                "QQAdapter notice: type=%s sub=%s group=%s user=%s",
                notice_type, sub_type, group_id, user_id,
            )

    def _handle_request(self, raw_payload: dict[str, Any]) -> None:
        """处理 OneBot v11 request 事件（加好友、加群请求等）。

        当前仅记录到日志；自动处理需要上层策略控制。
        """
        self._stats["inbound_request"] += 1
        request_type = raw_payload.get("request_type", "unknown")
        user_id = raw_payload.get("user_id", "")
        comment = raw_payload.get("comment", "")
        group_id = raw_payload.get("group_id", "")

        logger.info(
            "QQAdapter request: type=%s user=%s group=%s comment='%s'",
            request_type, user_id, group_id, comment,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text_content(raw_payload: dict[str, Any], raw_message: str) -> str:
        """从 OneBot v11 message 数组中提取纯文本内容。

        过滤掉 ``at`` 类型的消息段，只拼接 ``text`` 段的内容。
        """
        message_arr = raw_payload.get("message")
        if isinstance(message_arr, list):
            parts: list[str] = []
            for seg in message_arr:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text = str(seg.get("data", {}).get("text") or "").strip()
                    if text:
                        parts.append(text)
            if parts:
                return " ".join(parts)
        return raw_message

    def _check_at_self(self, raw_payload: dict[str, Any], self_id_raw: str) -> bool:
        """检查群消息中是否 @ 了自己。

        OneBot v11 的 at 消息段格式：
          {"type": "at", "data": {"qq": "123456"}}    # @ 指定用户
          {"type": "at", "data": {"qq": "all"}}        # @ 全体成员
        """
        effective_self_id = self._self_id or self_id_raw
        if not effective_self_id:
            return False

        message_arr = raw_payload.get("message")
        if not isinstance(message_arr, list):
            return False

        for seg in message_arr:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            at_qq = str(seg.get("data", {}).get("qq") or "")
            if at_qq == effective_self_id or at_qq == "all":
                return True
        return False

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
