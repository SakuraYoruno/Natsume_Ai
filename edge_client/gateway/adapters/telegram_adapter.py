"""Unified Message Gateway — Telegram Userbot 适配器。

使用 Telethon（MTProto 协议）以 **普通用户身份** 登录 Telegram，
而非 Bot API——对方看到的是一个正常的联系人在聊天，没有 bot 标签。

核心特性：
  - Inbound：接收私聊/群聊消息，转换为 UnifiedMessage
  - Outbound：发送回复，支持模拟打字延迟（"正在输入..." 提示）
  - 人感增强：可配置随机延迟、分段发送、在线状态管理
  - 自过滤：忽略自己发出的消息
  - 健康检查：连接状态 + 当前登录用户信息

配置项（环境变量）：
  TELEGRAM_API_ID          — Telegram API ID（从 my.telegram.org 获取）
  TELEGRAM_API_HASH        — Telegram API Hash
  TELEGRAM_PHONE           — 手机号（首次登录需要验证码）
  TELEGRAM_SESSION_NAME    — session 文件名（默认 "nanami"）
  TELEGRAM_SELF_ID         — 自身用户 ID（用于过滤自发消息，可选，启动时自动获取）
  TELEGRAM_TYPING_SPEED    — 模拟打字速度（字符/秒，默认 15.0）
  TELEGRAM_MIN_DELAY_S     — 回复最小延迟（秒，默认 1.0）
  TELEGRAM_MAX_DELAY_S     — 回复最大延迟（秒，默认 3.0）
  TELEGRAM_PROXY_TYPE      — 代理类型（socks5/http，可选）
  TELEGRAM_PROXY_ADDR      — 代理地址（可选）
  TELEGRAM_PROXY_PORT      — 代理端口（可选）
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.gateway.adapters.telegram")

# Telegram 消息类型 → 统一消息类型映射
_TG_MEDIA_TYPE_MAP: dict[str, MsgType] = {
    "text": MsgType.TEXT,
    "photo": MsgType.IMAGE,
    "voice": MsgType.VOICE,
    "video": MsgType.VIDEO,
    "document": MsgType.FILE,
    "sticker": MsgType.EMOJI,
    "animation": MsgType.IMAGE,  # GIF 归入图片
}


def _detect_msg_type(message: Any) -> MsgType:
    """根据 Telethon Message 对象检测消息类型。"""
    if message.photo:
        return MsgType.IMAGE
    if message.voice:
        return MsgType.VOICE
    if message.video:
        return MsgType.VIDEO
    if message.document:
        return MsgType.FILE
    if message.sticker:
        return MsgType.EMOJI
    if message.gif:
        return MsgType.IMAGE
    if message.text or message.raw_text:
        return MsgType.TEXT
    return MsgType.UNKNOWN


class TelegramAdapter(AbstractPlatformAdapter):
    """Telegram Userbot 适配器。

    使用 Telethon 以普通用户身份登录，不使用 Bot API。
    对方看到的就是一个正常的 Telegram 联系人。

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        phone: 手机号
        session_name: session 文件名（Telethon 会在本地缓存登录态）
        self_id: 自身用户 ID（可选，启动时自动获取）
        typing_speed: 模拟打字速度（字符/秒）
        min_delay_s: 回复最小延迟（秒）
        max_delay_s: 回复最大延迟（秒）
        proxy: 代理配置（可选）
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        *,
        session_name: str = "nanami",
        self_id: str | None = None,
        typing_speed: float = 15.0,
        min_delay_s: float = 1.0,
        max_delay_s: float = 3.0,
        proxy: dict[str, Any] | None = None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._session_name = session_name
        self._self_id = self_id
        self._typing_speed = max(1.0, typing_speed)
        self._min_delay_s = max(0.0, min_delay_s)
        self._max_delay_s = max(self._min_delay_s, max_delay_s)
        self._proxy = proxy

        self._client: Any = None  # TelegramClient 实例
        self._connected = False
        self._me: Any = None  # 当前登录用户信息
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._on_message_callback: Any = None  # Gateway 的消息回调

        self._stats: dict[str, int] = {
            "messages_received": 0,
            "messages_sent": 0,
            "messages_filtered": 0,
            "send_errors": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.TELEGRAM

    # ──────────────────────────────────────────────────────────────
    # 生命周期管理
    # ──────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """连接 Telegram（异步）。

        首次连接时需要用户在终端输入验证码。
        后续连接使用缓存的 session 文件自动登录。

        Returns:
            True 表示连接成功
        """
        try:
            from telethon import TelegramClient
        except ImportError:
            logger.error(
                "TelegramAdapter: telethon 未安装，请执行 pip install telethon"
            )
            return False

        # 构造代理参数
        proxy_kwargs: dict[str, Any] = {}
        if self._proxy:
            proxy_type = self._proxy.get("type", "socks5").lower()
            proxy_addr = self._proxy.get("addr", "")
            proxy_port = int(self._proxy.get("port", 0))
            if proxy_addr and proxy_port:
                import socks  # type: ignore[import-untyped]

                proxy_map = {
                    "socks5": socks.SOCKS5,
                    "socks4": socks.SOCKS4,
                    "http": socks.HTTP,
                }
                proxy_kwargs["proxy"] = (
                    proxy_map.get(proxy_type, socks.SOCKS5),
                    proxy_addr,
                    proxy_port,
                )

        self._client = TelegramClient(
            self._session_name,
            self._api_id,
            self._api_hash,
            **proxy_kwargs,
        )

        try:
            await self._client.connect()

            if not await self._client.is_user_authorized():
                logger.info("TelegramAdapter: 需要登录验证，发送验证码到 %s", self._phone)
                await self._client.send_code_request(self._phone)
                # 注意：这里需要用户在终端输入验证码
                # 在生产环境中，应该通过 Gateway API 接收验证码
                logger.warning(
                    "TelegramAdapter: 请在终端输入收到的验证码（首次登录需要）。"
                    "生产环境建议提前完成登录，复用 session 文件。"
                )
                # sign_in 会在 Telethon 的交互式流程中处理
                await self._client.start(phone=self._phone)
            else:
                await self._client.start(phone=self._phone)

            self._me = await self._client.get_me()
            self._self_id = str(self._me.id) if self._me else self._self_id
            self._connected = True
            self._event_loop = asyncio.get_event_loop()

            logger.info(
                "TelegramAdapter: 连接成功 user=%s id=%s",
                getattr(self._me, "first_name", "?"),
                self._self_id,
            )
            return True

        except Exception as exc:
            logger.error("TelegramAdapter: 连接失败: %s", exc)
            self._connected = False
            return False

    async def start_listening(
        self,
        on_message: Any,
    ) -> None:
        """注册消息监听器。

        Args:
            on_message: 收到消息时的回调函数，签名：(UnifiedMessage) -> None
        """
        if self._client is None:
            logger.error("TelegramAdapter: 未连接，无法监听")
            return

        from telethon import events

        self._on_message_callback = on_message

        @self._client.on(events.NewMessage(incoming=True))
        async def _handler(event: Any) -> None:
            try:
                msg = self._telethon_event_to_unified(event)
                if msg is None:
                    self._stats["messages_filtered"] += 1
                    return
                self._stats["messages_received"] += 1
                on_message(msg)
            except Exception as exc:
                logger.error("TelegramAdapter: 消息处理异常: %s", exc)

        logger.info("TelegramAdapter: 消息监听已注册")

    async def disconnect(self) -> None:
        """断开 Telegram 连接。"""
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as exc:
                logger.warning("TelegramAdapter: 断开连接异常: %s", exc)
            finally:
                self._connected = False
                logger.info("TelegramAdapter: 已断开连接")

    # ──────────────────────────────────────────────────────────────
    # AbstractPlatformAdapter 接口实现
    # ──────────────────────────────────────────────────────────────

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将 Webhook 推送的 Telegram 消息 payload 转换为 UnifiedMessage。

        注意：Telegram Userbot 模式下，消息主要通过 Telethon 事件监听器
        （start_listening）实时接收，而非 Webhook 推送。
        此方法作为 Gateway Webhook 兼容接口保留，处理通过
        POST /v1/gateway/telegram/webhook 手动推送的消息。

        payload 格式：
        {
            "msg_id": "123456",
            "from_id": "987654",
            "from_name": "用户昵称",
            "chat_id": "654321",
            "is_group": false,
            "text": "消息内容",
            "media_type": "text|photo|voice|video|document|sticker",
            "media_path": "/path/to/file",
            "ts": 1711428000
        }
        """
        msg_id = str(raw_payload.get("msg_id", ""))
        from_id = str(raw_payload.get("from_id", ""))
        if not msg_id or not from_id:
            return None

        # 自消息过滤
        if self._self_id and from_id == self._self_id:
            return None

        chat_id = str(raw_payload.get("chat_id", from_id))
        is_group = bool(raw_payload.get("is_group", False))

        # 消息类型
        media_type = str(raw_payload.get("media_type", "text")).lower()
        msg_type = _TG_MEDIA_TYPE_MAP.get(media_type, MsgType.UNKNOWN)

        # 会话 ID
        conversation_id = f"tg_group_{chat_id}" if is_group else f"tg_dm_{chat_id}"

        ts_ms = int(raw_payload.get("ts", 0)) * 1000
        if ts_ms == 0:
            ts_ms = int(time.time() * 1000)

        return UnifiedMessage(
            msg_id=msg_id,
            platform=Platform.TELEGRAM,
            from_id=from_id,
            to_id=self._self_id or "",
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=str(raw_payload.get("text", "")),
            ts_ms=ts_ms,
            media_url=raw_payload.get("media_path"),
            is_group=is_group,
            sender_name=raw_payload.get("from_name"),
            raw_extra=raw_payload,
        )

    def send_reply(self, reply: UnifiedReply) -> bool:
        """发送回复到 Telegram（同步包装）。

        内部调用异步的 _send_reply_async 方法。
        支持模拟打字延迟和分段发送。
        """
        if self._client is None or not self._connected:
            logger.error("TelegramAdapter: 未连接，无法发送")
            return False

        try:
            # 从 conversation_id 提取 chat_id
            chat_id = self._extract_chat_id(reply.conversation_id)
            if chat_id is None:
                logger.error(
                    "TelegramAdapter: 无法解析 conversation_id=%s",
                    reply.conversation_id,
                )
                return False

            loop = self._event_loop
            if loop is None or loop.is_closed():
                loop = asyncio.new_event_loop()

            future = asyncio.run_coroutine_threadsafe(
                self._send_reply_async(chat_id, reply),
                loop,
            )
            result = future.result(timeout=60.0)
            if result:
                self._stats["messages_sent"] += 1
            else:
                self._stats["send_errors"] += 1
            return result

        except Exception as exc:
            logger.error("TelegramAdapter: 发送异常: %s", exc)
            self._stats["send_errors"] += 1
            return False

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        me_info = {}
        if self._me:
            me_info = {
                "id": getattr(self._me, "id", None),
                "first_name": getattr(self._me, "first_name", None),
                "last_name": getattr(self._me, "last_name", None),
                "username": getattr(self._me, "username", None),
                "phone": getattr(self._me, "phone", None),
            }
        return {
            "platform": self.platform_id.value,
            "status": "ok" if self._connected else "disconnected",
            "connected": self._connected,
            "me": me_info,
            "stats": dict(self._stats),
        }

    # ──────────────────────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────────────────────

    def _telethon_event_to_unified(self, event: Any) -> UnifiedMessage | None:
        """将 Telethon 事件转换为 UnifiedMessage。"""
        message = event.message
        if message is None:
            return None

        # 自消息过滤
        sender_id = str(message.sender_id or "")
        if self._self_id and sender_id == self._self_id:
            return None

        # 系统消息过滤
        if getattr(message, "action", None) is not None:
            return None

        # 基本信息
        msg_id = str(message.id)
        chat_id = str(message.chat_id or message.peer_id)
        is_group = event.is_group or event.is_channel

        # 发送者信息
        sender_name = None
        sender = message.sender
        if sender:
            first = getattr(sender, "first_name", "") or ""
            last = getattr(sender, "last_name", "") or ""
            sender_name = f"{first} {last}".strip() or None

        # 消息类型
        msg_type = _detect_msg_type(message)

        # 文本内容
        content = message.text or message.raw_text or ""
        if msg_type == MsgType.IMAGE and not content:
            content = edge_runtime_text("telegram_adapter", "image_content", default="")
        elif msg_type == MsgType.VOICE and not content:
            content = edge_runtime_text("telegram_adapter", "voice_content", default="")
        elif msg_type == MsgType.VIDEO and not content:
            content = edge_runtime_text("telegram_adapter", "video_content", default="")
        elif msg_type == MsgType.FILE and not content:
            doc = message.document
            filename = ""
            if doc and doc.attributes:
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        filename = attr.file_name
                        break
            content = (
                edge_runtime_text(
                    "telegram_adapter",
                    "file_template",
                    default="",
                    file_name=filename,
                )
                if filename
                else edge_runtime_text("telegram_adapter", "file_fallback", default="")
            )
        elif msg_type == MsgType.EMOJI and not content:
            content = edge_runtime_text("telegram_adapter", "sticker_content", default="")

        # 引用消息
        reply_to_msg_id = None
        if message.reply_to and hasattr(message.reply_to, "reply_to_msg_id"):
            reply_to_msg_id = str(message.reply_to.reply_to_msg_id)

        # 会话 ID
        conversation_id = (
            f"tg_group_{chat_id}" if is_group else f"tg_dm_{chat_id}"
        )

        # 时间戳
        ts_ms = int(message.date.timestamp() * 1000) if message.date else int(time.time() * 1000)

        return UnifiedMessage(
            msg_id=msg_id,
            platform=Platform.TELEGRAM,
            from_id=sender_id,
            to_id=self._self_id or "",
            conversation_id=conversation_id,
            msg_type=msg_type,
            content=content,
            ts_ms=ts_ms,
            is_group=is_group,
            sender_name=sender_name,
            reply_to_msg_id=reply_to_msg_id,
            raw_extra={
                "telegram_chat_id": chat_id,
                "telegram_msg_id": msg_id,
            },
        )

    async def _send_reply_async(
        self,
        chat_id: int | str,
        reply: UnifiedReply,
    ) -> bool:
        """异步发送回复，带模拟打字延迟。"""
        try:
            entity = await self._client.get_entity(int(chat_id))
        except (ValueError, TypeError):
            try:
                entity = await self._client.get_entity(chat_id)
            except Exception as exc:
                logger.error("TelegramAdapter: 获取聊天实体失败 chat_id=%s: %s", chat_id, exc)
                return False

        # ── 文本消息 ──
        if reply.msg_type == MsgType.TEXT and reply.content:
            return await self._send_text_with_typing(entity, reply.content, reply.reply_to_msg_id)

        # ── 图片消息 ──
        if reply.msg_type == MsgType.IMAGE:
            return await self._send_media(entity, reply, "photo")

        # ── 语音消息 ──
        if reply.msg_type == MsgType.VOICE:
            return await self._send_media(entity, reply, "voice")

        # ── 其他文件 ──
        if reply.msg_type == MsgType.FILE:
            return await self._send_media(entity, reply, "file")

        # ── 默认当文本发 ──
        if reply.content:
            return await self._send_text_with_typing(entity, reply.content, reply.reply_to_msg_id)

        logger.warning("TelegramAdapter: 空回复，跳过发送")
        return False

    async def _send_text_with_typing(
        self,
        entity: Any,
        text: str,
        reply_to_msg_id: str | None = None,
    ) -> bool:
        """发送文本消息，使用公共拟人化模块分段发送 + Telegram 特有"正在输入..."状态。

        人感增强：
        1. 先显示"正在输入..."状态（Telegram 平台特有）
        2. 公共模块：智能分段 + 打字延迟 + 段间停顿
        3. 每段发送前重新触发"正在输入..."
        """
        try:
            from telethon.tl.functions.messages import SetTypingRequest
            from telethon.tl.types import SendMessageTypingAction
            from edge_client.gateway.humanize_sender import (
                humanize_send_async,
                HumanizeSendConfig,
            )

            reply_to = int(reply_to_msg_id) if reply_to_msg_id else None
            _segment_idx = 0

            async def _send_one_segment(segment: str) -> bool:
                nonlocal _segment_idx
                # 每段发送前显示"正在输入..."
                try:
                    await self._client(SetTypingRequest(
                        peer=entity,
                        action=SendMessageTypingAction(),
                    ))
                except Exception:
                    pass  # 输入状态失败不影响发送
                await self._client.send_message(
                    entity,
                    segment,
                    reply_to=reply_to if _segment_idx == 0 else None,
                )
                _segment_idx += 1
                return True

            config = HumanizeSendConfig.from_env()
            result = await humanize_send_async(
                text=text,
                send_fn=_send_one_segment,
                config=config,
            )
            return result.success

        except Exception as exc:
            logger.error("TelegramAdapter: 文本发送失败: %s", exc)
            return False

    async def _send_media(
        self,
        entity: Any,
        reply: UnifiedReply,
        media_type: str,
    ) -> bool:
        """发送媒体消息。"""
        try:
            file_path = reply.media_path
            file_data = reply.media_data

            if not file_path and not file_data:
                logger.warning("TelegramAdapter: 媒体消息无文件，跳过")
                return False

            # 如果有文本说明，先显示输入状态
            if reply.content:
                from telethon.tl.functions.messages import SetTypingRequest
                from telethon.tl.types import SendMessageTypingAction

                try:
                    await self._client(SetTypingRequest(
                        peer=entity,
                        action=SendMessageTypingAction(),
                    ))
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                except Exception:
                    pass

            send_kwargs: dict[str, Any] = {
                "entity": entity,
                "caption": reply.content or None,
            }

            if file_path:
                send_kwargs["file"] = file_path
            elif file_data:
                import io
                send_kwargs["file"] = io.BytesIO(file_data)

            if media_type == "voice":
                send_kwargs["voice_note"] = True

            await self._client.send_file(**send_kwargs)
            return True

        except Exception as exc:
            logger.error("TelegramAdapter: 媒体发送失败: %s", exc)
            return False

    @staticmethod
    def _split_message(text: str, max_length: int = 300) -> list[str]:
        """将长消息按段落/换行拆分成多条。

        规则：
        1. 优先按段落（双换行）拆分
        2. 每段不超过 max_length 字符
        3. 超长段落按换行拆分
        4. 最终不超过 5 段
        """
        if len(text) <= max_length:
            return [text]

        # 先按双换行拆
        paragraphs = text.split("\n\n")
        segments: list[str] = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_length:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    segments.append(current.strip())
                # 段落本身太长，按单换行再拆
                if len(para) > max_length:
                    lines = para.split("\n")
                    current = ""
                    for line in lines:
                        if len(current) + len(line) + 1 <= max_length:
                            current = f"{current}\n{line}" if current else line
                        else:
                            if current:
                                segments.append(current.strip())
                            current = line
                else:
                    current = para

        if current:
            segments.append(current.strip())

        # 限制最多 5 段
        if len(segments) > 5:
            # 合并多余的段到最后一段
            last_parts = segments[4:]
            segments = segments[:4]
            segments.append("\n\n".join(last_parts))

        return [s for s in segments if s]

    @staticmethod
    def _extract_chat_id(conversation_id: str) -> int | None:
        """从 conversation_id 提取 Telegram chat ID。

        conversation_id 格式：
          - tg_dm_{chat_id}  （私聊）
          - tg_group_{chat_id}  （群聊/频道）
        """
        for prefix in ("tg_dm_", "tg_group_"):
            if conversation_id.startswith(prefix):
                try:
                    return int(conversation_id[len(prefix):])
                except ValueError:
                    return None
        # 尝试直接解析
        try:
            return int(conversation_id)
        except ValueError:
            return None
