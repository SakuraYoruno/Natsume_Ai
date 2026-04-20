"""企业微信平台适配器（企业微信开放 API）。

接入方式：企业微信开放平台 → Callback URL → Webhook 推送

Inbound（接收）：
  - 企业微信消息回调（MsgType=text/image/voice/video/file）
  - 支持企业内部应用消息和客服消息
  - 支持 Token + EncodingAESKey 签名验证

Outbound（发送）：
  - 调用企业微信 API（/cgi-bin/message/send）

企业微信开发文档：https://developer.work.weixin.qq.com/
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import GatewayAttachment, MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.gateway.adapters.wecom")

# 企业微信 API 基础地址
_WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComAdapter(AbstractPlatformAdapter):
    """企业微信平台适配器（企业微信开放 API）。

    Args:
        corp_id: 企业 ID（CorpID）
        corp_secret: 应用 Secret
        agent_id: 应用 AgentID（用于发送消息）
        token: 企业微信回调 Token（用于签名验证）
        encoding_aes_key: 企业微信 EncodingAESKey（用于消息解密）
    """

    def __init__(
        self,
        corp_id: str,
        corp_secret: str,
        agent_id: int | str,
        *,
        token: str | None = None,
        encoding_aes_key: str | None = None,
        http_timeout_s: float = 10.0,
    ) -> None:
        self._corp_id = corp_id
        self._corp_secret = corp_secret
        self._agent_id = int(agent_id)
        self._token = token
        self._encoding_aes_key = encoding_aes_key
        self._timeout = http_timeout_s
        self._access_token: str | None = None
        self._token_expire_at: float = 0.0
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.WECOM

    # ------------------------------------------------------------------
    # Inbound：企业微信回调 → UnifiedMessage
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将企业微信回调的原始 payload 转换为 UnifiedMessage。

        企业微信消息回调结构（JSON 格式，需先解密 XML）：
          {
            "ToUserName": "企业微信ID",
            "FromUserName": "员工UserID",
            "CreateTime": 1234567890,
            "MsgType": "text",
            "Content": "你好",
            "MsgId": "123456789",
            "AgentID": 1000001
          }
        """
        msg_type_raw = str(raw_payload.get("MsgType") or "").lower()
        from_user = str(raw_payload.get("FromUserName") or "")
        to_user = str(raw_payload.get("ToUserName") or "")
        msg_id = str(raw_payload.get("MsgId") or "")
        create_time = raw_payload.get("CreateTime")
        ts_ms = int(create_time) * 1000 if create_time else int(time.time() * 1000)

        if not from_user or not msg_id:
            self._stats["inbound_skipped"] += 1
            return None

        # 提取内容
        content = self._extract_content(msg_type_raw, raw_payload)
        msg_type = self._map_msg_type(msg_type_raw)

        # 企业微信没有群聊概念（应用消息），会话 ID 用发送者 UserID
        conversation_id = f"wecom_{from_user}"

        self._stats["inbound_parsed"] += 1
        attachments = self._build_attachments(
            msg_id=msg_id,
            msg_type=msg_type,
            raw=raw_payload,
            content=content,
        )
        return UnifiedMessage(
            msg_id=msg_id,
            platform=Platform.WECOM,
            from_id=from_user,
            to_id=to_user,
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=content,
            ts_ms=ts_ms,
            is_group=False,
            attachments=attachments,
            raw_extra=raw_payload,
        )

    # ------------------------------------------------------------------
    # Outbound：调用企业微信 API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过企业微信 API 发送回复。"""
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning("WeComAdapter: 暂不支持发送 %s 类型消息", reply.msg_type.value)
            return False

        try:
            token = self._get_access_token()
            if not token:
                logger.error("WeComAdapter: 获取 access_token 失败")
                self._stats["outbound_failed"] += 1
                return False

            # 从 conversation_id 提取 user_id
            conversation_id = reply.conversation_id
            if conversation_id.startswith("wecom_"):
                to_user = conversation_id.removeprefix("wecom_")
            else:
                to_user = conversation_id

            # 构造消息
            if reply.msg_type == MsgType.TEXT:
                payload = {
                    "touser": to_user,
                    "msgtype": "text",
                    "agentid": self._agent_id,
                    "text": {"content": reply.content},
                }
            else:
                # 图片消息需要先上传获取 media_id，此处简化
                payload = {
                    "touser": to_user,
                    "msgtype": "text",
                    "agentid": self._agent_id,
                    "text": {
                        "content": edge_runtime_text(
                            "wecom_adapter",
                            "image_send_fallback",
                            default="",
                            media_path=reply.media_path or "",
                        )
                    },
                }

            resp = httpx.post(
                f"{_WECOM_API_BASE}/message/send?access_token={token}",
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("errcode") == 0:
                self._stats["outbound_sent"] += 1
                return True
            logger.warning("WeComAdapter: API 返回错误: %s", result)
            self._stats["outbound_failed"] += 1
            return False
        except Exception as exc:
            logger.error("WeComAdapter: 发送失败: %s", exc)
            self._stats["outbound_failed"] += 1
            return False

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        token = self._get_access_token()
        return {
            "platform": self.platform_id.value,
            "status": "ok" if token else "degraded",
            "corp_id": self._corp_id,
            "agent_id": self._agent_id,
            "token_valid": bool(token),
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str | None:
        """获取企业微信 access_token（带缓存）。"""
        now = time.time()
        if self._access_token and now < self._token_expire_at - 60:
            return self._access_token

        try:
            resp = httpx.get(
                f"{_WECOM_API_BASE}/gettoken",
                params={"corpid": self._corp_id, "corpsecret": self._corp_secret},
                timeout=5.0,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("errcode") == 0:
                self._access_token = result["access_token"]
                self._token_expire_at = now + int(result.get("expires_in", 7200))
                return self._access_token
        except Exception as exc:
            logger.error("WeComAdapter: 获取 access_token 失败: %s", exc)
        return None

    @staticmethod
    def _extract_content(msg_type: str, raw: dict[str, Any]) -> str:
        """从企业微信消息中提取内容。"""
        if msg_type == "text":
            return str(raw.get("Content") or "")
        if msg_type == "image":
            return edge_runtime_text(
                "wecom_adapter",
                "image_content",
                default="",
                value=raw.get("PicUrl") or raw.get("MediaId") or "",
            )
        if msg_type == "voice":
            return edge_runtime_text(
                "wecom_adapter",
                "voice_content",
                default="",
                value=raw.get("MediaId") or "",
            )
        if msg_type == "video":
            return edge_runtime_text(
                "wecom_adapter",
                "video_content",
                default="",
                value=raw.get("MediaId") or "",
            )
        if msg_type == "file":
            return edge_runtime_text(
                "wecom_adapter",
                "file_content",
                default="",
                value=raw.get("MediaId") or "",
            )
        return str(raw.get("Content") or "")

    @staticmethod
    def _map_msg_type(wecom_type: str) -> MsgType:
        """将企业微信消息类型映射为 MsgType。"""
        _MAP = {
            "text": MsgType.TEXT,
            "image": MsgType.IMAGE,
            "voice": MsgType.VOICE,
            "video": MsgType.VIDEO,
            "file": MsgType.FILE,
            "event": MsgType.SYSTEM,
        }
        return _MAP.get(wecom_type, MsgType.UNKNOWN)

    @staticmethod
    def _build_attachments(
        *,
        msg_id: str,
        msg_type: MsgType,
        raw: dict[str, Any],
        content: str,
    ) -> list[GatewayAttachment]:
        attachment_type_map: dict[MsgType, str] = {
            MsgType.IMAGE: "image",
            MsgType.VOICE: "voice",
            MsgType.VIDEO: "video",
            MsgType.FILE: "file",
        }
        attachment_type = attachment_type_map.get(msg_type)
        if not attachment_type:
            return []
        media_id = str(raw.get("MediaId") or raw.get("PicUrl") or "").strip()
        is_remote = media_id.startswith(("http://", "https://"))
        return [
            GatewayAttachment(
                attachment_id=f"{msg_id}:0",
                type=attachment_type,
                remote_url=(media_id if is_remote else None),
                local_path=(None if is_remote or not media_id else media_id),
                caption=str(content or "").strip() or None,
                extra={"media_id": media_id} if media_id else {},
            )
        ]
