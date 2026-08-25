"""LINE Messaging API 平台适配器。

接入方式：LINE Developers Console → Messaging API → Webhook URL

Inbound（接收）：
  - LINE Webhook 推送（events: message / follow / unfollow / join / leave / postback）
  - 支持 HMAC-SHA256 签名验证（X-Line-Signature）
  - 支持一对一聊天（source.type=user）和群聊（source.type=group/room）

Outbound（发送）：
  - 调用 LINE Messaging API（Reply API / Push API）
  - 支持文本、图片、贴图、视频消息

LINE Messaging API 文档：https://developers.line.biz/en/docs/messaging-api/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import GatewayAttachment, MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.gateway.adapters.line")

# LINE Messaging API 基础地址
_LINE_API_BASE = "https://api.line.me/v2/bot"


class LineAdapter(AbstractPlatformAdapter):
    # 适配器只处理已由网关按事件拆分并验签的 LINE 数据；完整 events[] 的
    # 兼容分支只供离线调用，HTTP 主链不会因此静默忽略同批其他事件。
    """LINE Messaging API 平台适配器。

    Args:
        channel_access_token: LINE Channel Access Token（长期有效）
        channel_secret: LINE Channel Secret（用于 Webhook 签名验证）
    """

    def __init__(
        self,
        channel_access_token: str,
        channel_secret: str,
        *,
        http_timeout_s: float = 10.0,
    ) -> None:
        self._channel_access_token = channel_access_token
        self._channel_secret = channel_secret
        self._timeout = http_timeout_s
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.LINE

    # ------------------------------------------------------------------
    # Webhook 签名验证
    # ------------------------------------------------------------------

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """验证 LINE Webhook 请求的 X-Line-Signature。

        LINE 使用 Channel Secret 作为密钥，对请求 body 做 HMAC-SHA256，
        然后 base64 编码后与 header 中的签名比对。
        """
        if not self._channel_secret:
            logger.warning("LineAdapter: channel_secret 未配置，跳过签名验证")
            return True

        computed = hmac.new(
            self._channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        computed_b64 = base64.b64encode(computed).decode("utf-8")

        return hmac.compare_digest(computed_b64, signature)

    # ------------------------------------------------------------------
    # Inbound：LINE Webhook Event → UnifiedMessage
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将 LINE Webhook 事件转换为 UnifiedMessage。

        LINE Webhook 事件结构：
          {
            "destination": "Uxxxx",
            "events": [
              {
                "type": "message",
                "replyToken": "xxx",
                "source": {"type": "user", "userId": "Uxxxx"},
                "timestamp": 1234567890123,
                "message": {
                  "id": "xxx",
                  "type": "text",
                  "text": "Hello"
                }
              }
            ]
          }

        Gateway server 层对已验签的 events[] 进行拆分，并对每个
        event 独立调用本方法。保留完整 envelope 兼容分支仅供旧的
        离线调用者；HTTP 入口不会通过该分支丢弃后续事件。
        """
        # 处理单个 event（由 server 层拆分后传入）
        event = raw_payload

        # 如果是完整的 webhook body（包含 events 数组），提取第一个 message event
        if "events" in raw_payload:
            events = raw_payload.get("events") or []
            message_events = [e for e in events if e.get("type") == "message"]
            if not message_events:
                self._stats["inbound_skipped"] += 1
                return None
            event = message_events[0]

        event_type = str(event.get("type") or "")

        # 只处理 message 事件
        if event_type != "message":
            self._stats["inbound_skipped"] += 1
            logger.debug("LineAdapter: 跳过非 message 事件: %s", event_type)
            return None

        message = event.get("message") or {}
        source = event.get("source") or {}

        message_id = str(message.get("id") or "").strip()
        if not message_id:
            webhook_event_id = str(event.get("webhookEventId") or "").strip()
            if webhook_event_id:
                message_id = f"line-event:{webhook_event_id}"
            else:
                # 不能用随机 UUID 代替平台 ID；规范摘要让同一 webhook 重试稳定
                # 地映射到同一个 inbox 幂等键。
                # Missing platform IDs must not introduce random UUIDs: a LINE
                # retry must normalize to the same durable idempotency payload.
                canonical_event = json.dumps(
                    event,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
                message_id = f"line-event-sha256:{hashlib.sha256(canonical_event).hexdigest()}"
        message_type = str(message.get("type") or "text")
        timestamp_ms = event.get("timestamp")
        try:
            ts_ms = int(timestamp_ms) if timestamp_ms is not None else 0
        except (TypeError, ValueError):
            ts_ms = 0

        # 发送者 ID
        source_type = str(source.get("type") or "user")
        from_id = str(source.get("userId") or "")

        # 会话 ID：群聊用 groupId/roomId，私聊用 userId
        if source_type == "group":
            conversation_id = str(source.get("groupId") or "")
            is_group = True
        elif source_type == "room":
            conversation_id = str(source.get("roomId") or "")
            is_group = True
        else:
            conversation_id = from_id
            is_group = False

        # 消息内容和类型
        content = self._extract_content(message_type, message)
        msg_type = self._map_msg_type(message_type)

        # replyToken 保存在 raw_extra 中，send_reply 时使用
        reply_token = str(event.get("replyToken") or "")

        self._stats["inbound_parsed"] += 1
        attachments = self._build_attachments(
            message_id=message_id,
            message_type=message_type,
            message=message,
            content=content,
        )
        return UnifiedMessage(
            msg_id=message_id,
            platform=Platform.LINE,
            from_id=from_id,
            to_id="",
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=content,
            ts_ms=ts_ms,
            is_group=is_group,
            attachments=attachments,
            raw_extra={
                "reply_token": reply_token,
                "source_type": source_type,
                "line_message_type": message_type,
                "raw_event": event,
            },
        )

    # ------------------------------------------------------------------
    # Outbound：调用 LINE Messaging API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过 LINE Messaging API 发送回复。

        优先使用 Reply API（replyToken），若无 replyToken 则使用 Push API。
        """
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning("LineAdapter: 暂不支持发送 %s 类型消息", reply.msg_type.value)
            return False

        try:
            # 构造 LINE 消息对象
            messages = self._build_line_messages(reply)
            if not messages:
                logger.warning("LineAdapter: 无法构造消息对象")
                self._stats["outbound_failed"] += 1
                return False

            # 优先用 Reply API
            reply_token = reply.raw_extra.get("reply_token") if reply.raw_extra else None
            if reply_token:
                # Reply token 是一次性、短时上下文能力；存在时优先消费它以保持
                # 对原消息的因果关联，失败不在此处擅自切换为主动 Push。
                return self._send_reply_message(reply_token, messages)
            else:
                # 缺少 token 才走主动 Push，目标只能来自已规范化的 conversation_id。
                # 使用 Push API（需要目标用户/群 ID）
                return self._send_push_message(reply.conversation_id, messages)

        except Exception as exc:
            logger.error("LineAdapter: 发送失败: %s", exc)
            self._stats["outbound_failed"] += 1
            return False

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        token_set = bool(self._channel_access_token)
        # 调用 LINE Bot Info API 检查 token 有效性
        bot_info = None
        if token_set:
            try:
                resp = httpx.get(
                    f"{_LINE_API_BASE}/info",
                    headers=self._auth_headers(),
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    bot_info = resp.json()
            except Exception:
                pass

        return {
            "platform": self.platform_id.value,
            "status": "ok" if bot_info else ("degraded" if token_set else "not_configured"),
            "token_set": token_set,
            "bot_info": bot_info,
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """构造 LINE API 请求头。"""
        return {
            "Authorization": f"Bearer {self._channel_access_token}",
            "Content-Type": "application/json",
        }

    def _send_reply_message(self, reply_token: str, messages: list[dict[str, Any]]) -> bool:
        """使用 Reply API 发送消息（replyToken 限时有效，仅一次）。"""
        payload = {
            "replyToken": reply_token,
            "messages": messages,
        }
        try:
            resp = httpx.post(
                f"{_LINE_API_BASE}/message/reply",
                json=payload,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                self._stats["outbound_sent"] += 1
                return True
            # replyToken 可能已过期，降级到 Push API
            logger.warning(
                "LineAdapter: Reply API 失败 status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            self._stats["outbound_failed"] += 1
            return False
        except Exception as exc:
            logger.error("LineAdapter: Reply API 异常: %s", exc)
            self._stats["outbound_failed"] += 1
            return False

    def _send_push_message(self, to: str, messages: list[dict[str, Any]]) -> bool:
        """使用 Push API 主动发送消息（不需要 replyToken）。"""
        if not to:
            logger.warning("LineAdapter: Push API 缺少目标 ID")
            self._stats["outbound_failed"] += 1
            return False

        payload = {
            "to": to,
            "messages": messages,
        }
        try:
            resp = httpx.post(
                f"{_LINE_API_BASE}/message/push",
                json=payload,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                self._stats["outbound_sent"] += 1
                return True
            logger.warning(
                "LineAdapter: Push API 失败 status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            self._stats["outbound_failed"] += 1
            return False
        except Exception as exc:
            logger.error("LineAdapter: Push API 异常: %s", exc)
            self._stats["outbound_failed"] += 1
            return False

    def _build_line_messages(self, reply: UnifiedReply) -> list[dict[str, Any]]:
        """将 UnifiedReply 转换为 LINE 消息对象列表。"""
        messages: list[dict[str, Any]] = []

        if reply.msg_type == MsgType.TEXT:
            # LINE 单条文本消息最大 5000 字符，超长需拆分
            text = reply.content or ""
            max_len = 5000
            if len(text) <= max_len:
                messages.append({"type": "text", "text": text})
            else:
                # 拆分为多条消息（LINE 一次最多 5 条）
                chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
                for chunk in chunks[:5]:
                    messages.append({"type": "text", "text": chunk})

        elif reply.msg_type == MsgType.IMAGE:
            # 图片消息需要 originalContentUrl 和 previewImageUrl
            image_url = reply.media_path or ""
            if image_url:
                messages.append({
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": image_url,
                })
            else:
                # 图片路径为空，降级为文本提示
                messages.append({
                    "type": "text",
                    "text": edge_runtime_text(
                        "line_adapter",
                        "image_fallback",
                        default="",
                    ),
                })

        return messages

    @staticmethod
    def _extract_content(message_type: str, message: dict[str, Any]) -> str:
        """从 LINE 消息对象中提取文本内容。"""
        if message_type == "text":
            return str(message.get("text") or "")
        if message_type == "sticker":
            package_id = message.get("packageId", "")
            sticker_id = message.get("stickerId", "")
            return edge_runtime_text(
                "line_adapter",
                "sticker_template",
                default="",
                package_id=package_id,
                sticker_id=sticker_id,
            )
        if message_type == "image":
            return edge_runtime_text("line_adapter", "image_content", default="")
        if message_type == "video":
            return edge_runtime_text("line_adapter", "video_content", default="")
        if message_type == "audio":
            return edge_runtime_text("line_adapter", "audio_content", default="")
        if message_type == "file":
            file_name = message.get("fileName") or edge_runtime_text(
                "line_adapter",
                "default_file_name",
                default="",
            )
            return edge_runtime_text(
                "line_adapter",
                "file_template",
                default="",
                file_name=file_name,
            )
        if message_type == "location":
            title = message.get("title") or edge_runtime_text(
                "line_adapter",
                "default_location_title",
                default="",
            )
            address = message.get("address") or ""
            lat = message.get("latitude", "")
            lng = message.get("longitude", "")
            return edge_runtime_text(
                "line_adapter",
                "location_template",
                default="",
                title=title,
                address=address,
                lat=lat,
                lng=lng,
            )
        return edge_runtime_text(
            "line_adapter",
            "unknown_message_template",
            default="",
            message_type=message_type,
        )

    @staticmethod
    def _map_msg_type(line_type: str) -> MsgType:
        """将 LINE 消息类型映射为 MsgType。"""
        _MAP = {
            "text": MsgType.TEXT,
            "image": MsgType.IMAGE,
            "video": MsgType.VIDEO,
            "audio": MsgType.VOICE,
            "file": MsgType.FILE,
            "sticker": MsgType.EMOJI,
            "location": MsgType.TEXT,
        }
        return _MAP.get(line_type, MsgType.UNKNOWN)

    @staticmethod
    def _build_attachments(
        *,
        message_id: str,
        message_type: str,
        message: dict[str, Any],
        content: str,
    ) -> list[GatewayAttachment]:
        attachment_type_map: dict[str, str] = {
            "image": "image",
            "video": "video",
            "audio": "voice",
            "file": "file",
            "sticker": "emoji",
        }
        attachment_type = attachment_type_map.get(str(message_type or "").strip().lower())
        if not attachment_type:
            return []
        return [
            GatewayAttachment(
                attachment_id=f"{message_id}:0",
                type=attachment_type,
                file_name=str(message.get("fileName") or "").strip() or None,
                caption=str(content or "").strip() or None,
                extra=dict(message or {}),
            )
        ]
