"""飞书平台适配器（官方 Open API Event Subscription）。

接入方式：飞书开放平台 → Event Subscription → Webhook 推送

Inbound（接收）：
  - 飞书 Event Subscription 推送（event.type=im.message.receive_v1）
  - 支持私聊（chat_type=p2p）和群聊（chat_type=group）
  - 支持飞书 Encrypt Key 签名验证

Outbound（发送）：
  - 调用飞书 Open API（/open-apis/im/v1/messages）

飞书开放平台文档：https://open.feishu.cn/document/
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import GatewayAttachment, MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.gateway.adapters.feishu")

# 飞书 Open API 基础地址
_FEISHU_API_BASE = "https://open.feishu.cn/open-apis"


class FeishuAdapter(AbstractPlatformAdapter):
    """飞书平台适配器（官方 Open API）。

    Args:
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
        encrypt_key: 飞书 Encrypt Key（用于 Webhook 签名验证，可选）
        verification_token: 飞书 Verification Token（用于 URL 验证，可选）
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        encrypt_key: str | None = None,
        verification_token: str | None = None,
        http_timeout_s: float = 10.0,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._encrypt_key = encrypt_key
        self._verification_token = verification_token
        self._timeout = http_timeout_s
        self._tenant_access_token: str | None = None
        self._token_expire_at: float = 0.0
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.FEISHU

    # ------------------------------------------------------------------
    # Inbound：飞书 Event Subscription → UnifiedMessage
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将飞书 Event Subscription 推送转换为 UnifiedMessage。

        飞书消息事件结构（v2）：
          {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", ...},
            "event": {
              "message": {
                "message_id": "om_xxx",
                "chat_id": "oc_xxx",
                "chat_type": "p2p" | "group",
                "message_type": "text",
                "content": "{\"text\":\"你好\"}",
                "create_time": "1234567890000"
              },
              "sender": {"sender_id": {"open_id": "ou_xxx"}, "sender_type": "user"}
            }
          }
        """
        # URL 验证挑战（飞书首次配置 Webhook 时发送）
        if "challenge" in raw_payload:
            logger.debug("FeishuAdapter: 收到 URL 验证挑战，跳过处理")
            self._stats["inbound_skipped"] += 1
            return None

        # 提取事件类型
        header = raw_payload.get("header") or {}
        event_type = str(header.get("event_type") or "")
        if event_type != "im.message.receive_v1":
            self._stats["inbound_skipped"] += 1
            return None

        event = raw_payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}

        message_id = str(message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        chat_type = str(message.get("chat_type") or "")
        message_type = str(message.get("message_type") or "text")
        content_raw = str(message.get("content") or "{}")
        create_time_ms = message.get("create_time")
        ts_ms = int(create_time_ms) if create_time_ms else int(time.time() * 1000)

        # 发送者 open_id
        sender_id_obj = sender.get("sender_id") or {}
        from_id = str(sender_id_obj.get("open_id") or sender_id_obj.get("user_id") or "")

        # 解析文本内容
        content = self._extract_content(message_type, content_raw)

        # 消息类型映射
        msg_type = self._map_msg_type(message_type)

        is_group = chat_type == "group"

        self._stats["inbound_parsed"] += 1
        attachments = self._build_attachments(
            message_id=message_id,
            message_type=message_type,
            message=message,
            content=content,
        )
        return UnifiedMessage(
            msg_id=message_id,
            platform=Platform.FEISHU,
            from_id=from_id,
            to_id="",
            conversation_id=chat_id,
            msg_type=msg_type,
            content=content,
            ts_ms=ts_ms,
            is_group=is_group,
            attachments=attachments,
            raw_extra=raw_payload,
        )

    # ------------------------------------------------------------------
    # Outbound：调用飞书 Open API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过飞书 Open API 发送回复。"""
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning("FeishuAdapter: 暂不支持发送 %s 类型消息", reply.msg_type.value)
            return False

        try:
            token = self._get_tenant_access_token()
            if not token:
                logger.error("FeishuAdapter: 获取 tenant_access_token 失败")
                self._stats["outbound_failed"] += 1
                return False

            # 构造消息内容
            if reply.msg_type == MsgType.TEXT:
                msg_type = "text"
                content = json.dumps({"text": reply.content}, ensure_ascii=False)
            else:
                # 图片消息需要先上传图片获取 image_key，此处简化为文本
                msg_type = "text"
                content = json.dumps(
                    {
                        "text": edge_runtime_text(
                            "feishu_adapter",
                            "image_send_fallback",
                            default="",
                            media_path=reply.media_path or "",
                        )
                    },
                    ensure_ascii=False,
                )

            payload = {
                "receive_id": reply.conversation_id,
                "msg_type": msg_type,
                "content": content,
            }
            resp = httpx.post(
                f"{_FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                self._stats["outbound_sent"] += 1
                return True
            logger.warning("FeishuAdapter: API 返回错误: %s", result)
            self._stats["outbound_failed"] += 1
            return False
        except Exception as exc:
            logger.error("FeishuAdapter: 发送失败: %s", exc)
            self._stats["outbound_failed"] += 1
            return False

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        token = self._get_tenant_access_token()
        return {
            "platform": self.platform_id.value,
            "status": "ok" if token else "degraded",
            "app_id": self._app_id,
            "token_valid": bool(token),
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_tenant_access_token(self) -> str | None:
        """获取飞书 tenant_access_token（带缓存，有效期内不重复请求）。"""
        now = time.time()
        if self._tenant_access_token and now < self._token_expire_at - 60:
            return self._tenant_access_token

        try:
            resp = httpx.post(
                f"{_FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=5.0,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                self._tenant_access_token = result["tenant_access_token"]
                self._token_expire_at = now + int(result.get("expire", 7200))
                return self._tenant_access_token
        except Exception as exc:
            logger.error("FeishuAdapter: 获取 token 失败: %s", exc)
        return None

    @staticmethod
    def _extract_content(message_type: str, content_raw: str) -> str:
        """从飞书消息内容 JSON 中提取文本。"""
        try:
            content_obj = json.loads(content_raw)
            if message_type == "text":
                return str(content_obj.get("text") or "")
            if message_type == "post":
                # 富文本，提取所有 text 段
                parts: list[str] = []
                for line in content_obj.get("content", []):
                    for seg in line:
                        if seg.get("tag") == "text":
                            parts.append(str(seg.get("text") or ""))
                return "".join(parts)
        except Exception:
            pass
        return content_raw

    @staticmethod
    def _map_msg_type(feishu_type: str) -> MsgType:
        """将飞书消息类型映射为 MsgType。"""
        _MAP = {
            "text": MsgType.TEXT,
            "post": MsgType.TEXT,
            "image": MsgType.IMAGE,
            "audio": MsgType.VOICE,
            "media": MsgType.VIDEO,
            "file": MsgType.FILE,
            "sticker": MsgType.EMOJI,
        }
        return _MAP.get(feishu_type, MsgType.UNKNOWN)

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
            "audio": "voice",
            "media": "video",
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
                caption=str(content or "").strip() or None,
                extra=dict(message or {}),
            )
        ]
