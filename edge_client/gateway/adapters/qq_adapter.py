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
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import GatewayAttachment, MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.runtime_texts import edge_runtime_text

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
        media_url = self._extract_media_url(raw_payload, msg_type=msg_type)
        media_url = self._resolve_inbound_media_ref(raw_payload, msg_type=msg_type, media_ref=media_url)
        if msg_type != MsgType.TEXT:
            normalized_content = str(content or "").strip()
            if (not normalized_content) or normalized_content.lower().startswith("[cq:"):
                content = self._fallback_content_for_msg_type(msg_type)
        elif not content:
            content = self._fallback_content_for_msg_type(msg_type)

        # 发送者昵称
        sender = raw_payload.get("sender") or {}
        sender_name = str(sender.get("nickname") or sender.get("card") or "")

        # 将 @ 自己的标记放入 raw_extra 供上层使用
        enriched_extra = dict(raw_payload)
        if is_at_self:
            enriched_extra["_at_self"] = True

        self._stats["inbound_parsed"] += 1
        attachments = self._build_attachments(
            msg_id=message_id,
            msg_type=msg_type,
            media_url=media_url,
            raw_payload=raw_payload,
            content=content,
        )
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
            media_url=media_url,
            attachments=attachments,
            raw_extra=enriched_extra,
        )

    # ------------------------------------------------------------------
    # Outbound：调用 OneBot v11 HTTP API 发送消息
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """通过 OneBot v11 HTTP API 发送回复。

        支持的消息类型：TEXT / IMAGE / VOICE。
        TEXT 类型自动接入拟人化分段发送（模拟打字延迟 + 分段），
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
            # TEXT 消息使用拟人化分段发送
            if reply.msg_type == MsgType.TEXT:
                success = self._send_text_humanized(reply)
            else:
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

    def _send_text_humanized(self, reply: UnifiedReply) -> bool:
        """使用公共拟人化模块分段发送文字消息。

        将完整回复拆分为多段，逐段通过 OneBot API 发送，
        段间模拟打字延迟，让对话更自然。
        """
        from edge_client.gateway.humanize_sender import (
            humanize_send_sync,
            HumanizeSendConfig,
        )

        def _send_one_segment(segment: str) -> bool:
            """发送单段文字到 QQ。"""
            conversation_id = reply.conversation_id
            message = [{"type": "text", "data": {"text": segment}}]

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
                return bool(result.get("status") == "ok" or result.get("retcode") == 0)
            except httpx.HTTPError as exc:
                logger.error("QQAdapter: HTTP 请求失败: %s", exc)
                return False

        config = HumanizeSendConfig.from_env()
        send_result = humanize_send_sync(
            text=reply.content,
            send_fn=_send_one_segment,
            config=config,
        )

        if send_result.success:
            logger.info(
                "QQAdapter: humanized send OK segments=%d delay=%dms",
                send_result.sent_segments, send_result.total_delay_ms,
            )
        else:
            logger.warning(
                "QQAdapter: humanized send failed at segment %d/%d: %s",
                send_result.failed_segment_index + 1,
                send_result.total_segments,
                send_result.error[:100],
            )
        return send_result.success

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
            onebot_data = ob_status.get("data")
            if not isinstance(onebot_data, dict):
                onebot_data = {}
            good_raw = onebot_data.get("good")
            if good_raw is None:
                good_raw = ob_status.get("good")
            if isinstance(good_raw, bool):
                is_good = good_raw
            elif isinstance(good_raw, (int, float)):
                is_good = (good_raw != 0)
            elif isinstance(good_raw, str):
                is_good = good_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                is_good = False
            return {
                "platform": self.platform_id.value,
                "status": "ok" if is_good else "degraded",
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
            if "mface" in types or "face" in types:
                return MsgType.EMOJI
            if "record" in types:
                return MsgType.VOICE
            if "video" in types:
                return MsgType.VIDEO
            if "file" in types:
                return MsgType.FILE
        raw_message = str(raw_payload.get("raw_message") or "").lower()
        if "[cq:record" in raw_message or "[cq:voice" in raw_message:
            return MsgType.VOICE
        if "[cq:image" in raw_message:
            return MsgType.IMAGE
        if "[cq:mface" in raw_message or "[cq:face" in raw_message:
            return MsgType.EMOJI
        if "[cq:video" in raw_message:
            return MsgType.VIDEO
        if "[cq:file" in raw_message:
            return MsgType.FILE
        return MsgType.TEXT

    @staticmethod
    def _extract_media_url(raw_payload: dict[str, Any], *, msg_type: MsgType) -> str | None:
        message_arr = raw_payload.get("message")
        if not isinstance(message_arr, list):
            return QQAdapter._extract_media_url_from_cq(raw_payload, msg_type=msg_type)
        target_types: dict[MsgType, set[str]] = {
            MsgType.VOICE: {"record", "voice"},
            MsgType.IMAGE: {"image"},
            MsgType.EMOJI: {"mface", "face"},
            MsgType.VIDEO: {"video"},
            MsgType.FILE: {"file"},
        }
        expected = target_types.get(msg_type) or set()
        if not expected:
            return None
        for seg in message_arr:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "").strip().lower()
            if seg_type not in expected:
                continue
            data = seg.get("data")
            if not isinstance(data, dict):
                continue
            value = QQAdapter._pick_inbound_media_ref(data, msg_type=msg_type)
            if value:
                return value
        return QQAdapter._extract_media_url_from_cq(raw_payload, msg_type=msg_type)

    @staticmethod
    def _extract_media_url_from_cq(raw_payload: dict[str, Any], *, msg_type: MsgType) -> str | None:
        raw_message = str(raw_payload.get("raw_message") or "").strip()
        if not raw_message:
            return None
        cq_type_map: dict[MsgType, tuple[str, ...]] = {
            MsgType.VOICE: ("record", "voice"),
            MsgType.IMAGE: ("image",),
            MsgType.EMOJI: ("mface", "face", "image"),
            MsgType.VIDEO: ("video",),
            MsgType.FILE: ("file",),
        }
        cq_types = cq_type_map.get(msg_type)
        if not cq_types:
            return None
        for cq_type in cq_types:
            match = re.search(rf"\[CQ:{re.escape(cq_type)},([^\]]+)\]", raw_message, flags=re.IGNORECASE)
            if not match:
                continue
            payload = str(match.group(1) or "")
            for key in QQAdapter._preferred_media_keys(msg_type):
                key_match = re.search(rf"(?:^|,){re.escape(key)}=([^,\]]+)", payload, flags=re.IGNORECASE)
                if key_match:
                    value = str(key_match.group(1) or "").strip()
                    if value:
                        return value
        return None

    @staticmethod
    def _preferred_media_keys(msg_type: MsgType) -> tuple[str, ...]:
        if msg_type in {MsgType.IMAGE, MsgType.EMOJI, MsgType.VIDEO}:
            return ("url", "path", "file")
        if msg_type == MsgType.FILE:
            return ("path", "url", "file")
        return ("file", "url", "path")

    @staticmethod
    def _pick_inbound_media_ref(data: dict[str, Any], *, msg_type: MsgType) -> str | None:
        for key in QQAdapter._preferred_media_keys(msg_type):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        return None

    def _resolve_inbound_media_ref(
        self,
        raw_payload: dict[str, Any],
        *,
        msg_type: MsgType,
        media_ref: str | None,
    ) -> str | None:
        normalized = str(media_ref or "").strip()
        if not normalized:
            return None
        if msg_type not in {MsgType.IMAGE, MsgType.EMOJI}:
            return normalized
        if self._is_remote_media_ref(normalized):
            return normalized
        if self._looks_like_local_path(normalized):
            data_url = self._encode_local_image_as_data_url(normalized)
            return data_url or normalized

        resolved = self._resolve_qq_image_ref(raw_payload, normalized)
        return resolved or normalized

    @staticmethod
    def _is_remote_media_ref(value: str) -> bool:
        lowered = str(value or "").strip().lower()
        return lowered.startswith(("http://", "https://", "data:"))

    @staticmethod
    def _looks_like_local_path(value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return False
        if normalized.startswith(("file:///", "file://")):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", normalized):
            return True
        return Path(normalized).is_absolute()

    def _resolve_qq_image_ref(self, raw_payload: dict[str, Any], media_ref: str) -> str | None:
        token = str(media_ref or "").strip()
        if not token:
            return None
        try:
            resp = httpx.get(
                f"{self._api_base}/get_image",
                params={"file": token},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            status_ok = str(body.get("status") or "").strip().lower() == "ok"
            ret_ok = int(body.get("retcode") or 0) == 0
            if not (status_ok or ret_ok):
                return None
            data = body.get("data")
            if not isinstance(data, dict):
                return None
            for key in ("url", "file", "path"):
                candidate = str(data.get(key) or "").strip()
                if not candidate:
                    continue
                if self._is_remote_media_ref(candidate):
                    return candidate
                encoded = self._encode_local_image_as_data_url(candidate)
                if encoded:
                    return encoded
                if self._looks_like_local_path(candidate):
                    return candidate
        except Exception as exc:
            logger.info(
                "QQAdapter: get_image failed msg_id=%s detail=%s",
                str(raw_payload.get("message_id") or ""),
                str(exc)[:120],
            )
            return None
        return None

    @staticmethod
    def _encode_local_image_as_data_url(path_ref: str) -> str | None:
        normalized = str(path_ref or "").strip()
        if normalized.startswith("file:///"):
            normalized = normalized.removeprefix("file:///")
        elif normalized.startswith("file://"):
            normalized = normalized.removeprefix("file://")
        path = Path(normalized)
        if not path.exists() or not path.is_file():
            return None
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            return None
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _fallback_content_for_msg_type(msg_type: MsgType) -> str:
        mapping: dict[MsgType, tuple[str, str]] = {
            MsgType.VOICE: ("qq_adapter", "voice_content"),
            MsgType.IMAGE: ("qq_adapter", "image_content"),
            MsgType.EMOJI: ("qq_adapter", "image_content"),
            MsgType.VIDEO: ("qq_adapter", "video_content"),
            MsgType.FILE: ("qq_adapter", "file_content"),
        }
        section_key = mapping.get(msg_type)
        if not section_key:
            return ""
        section, key = section_key
        return edge_runtime_text(section, key, default="")

    @staticmethod
    def _build_attachments(
        *,
        msg_id: str,
        msg_type: MsgType,
        media_url: str | None,
        raw_payload: dict[str, Any],
        content: str,
    ) -> list[GatewayAttachment]:
        attachment_type_map: dict[MsgType, str] = {
            MsgType.IMAGE: "image",
            MsgType.VOICE: "voice",
            MsgType.VIDEO: "video",
            MsgType.FILE: "file",
            MsgType.EMOJI: "emoji",
        }
        attachment_type = attachment_type_map.get(msg_type)
        if not attachment_type:
            return []
        media_ref = str(media_url or "").strip()
        if not media_ref and msg_type == MsgType.EMOJI:
            media_ref = QQAdapter._extract_media_url(raw_payload, msg_type=MsgType.IMAGE) or ""
        is_remote = media_ref.startswith(("http://", "https://", "data:"))
        return [
            GatewayAttachment(
                attachment_id=f"{msg_id}:0",
                type=attachment_type,
                local_path=(None if is_remote or not media_ref else media_ref),
                remote_url=(media_ref if is_remote else None),
                caption=str(content or "").strip() or None,
                extra={"platform": "qq"},
            )
        ]
