"""微信平台适配器。

基于 WxDbService（解密数据库方案）实现微信消息的 Inbound 解析和 Outbound 发送。

Inbound（接收）：
  - WxDbWatcher 监听数据库变更 → poll_new_messages() → 转换为 UnifiedMessage
  - 通过 Gateway Webhook 接收外部推送（兼容模式）

Outbound（发送）：
  - 剪贴板 + Ctrl+V → Enter（pywinauto 精确定位输入框）
  - 不依赖任何第三方 Hook API
"""

from __future__ import annotations

import logging
import time
from typing import Any

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.services.wx_db_service import WxDbService, WxMessage

logger = logging.getLogger("Natsume.edge.gateway.adapters.wechat")


class WeChatAdapter(AbstractPlatformAdapter):
    """微信平台适配器（基于 wx4_decrypt 解密方案）。

    Args:
        wx_db_service: WxDbService 实例
        self_wxid: 本机微信 wxid（用于过滤自己发出的消息）
    """

    def __init__(
        self,
        wx_db_service: WxDbService,
        *,
        self_wxid: str | None = None,
    ) -> None:
        self._svc = wx_db_service
        self._self_wxid = self_wxid
        self._stats: dict[str, int] = {
            "inbound_parsed": 0,
            "inbound_skipped": 0,
            "outbound_sent": 0,
            "outbound_failed": 0,
        }

    @property
    def platform_id(self) -> Platform:
        return Platform.WECHAT

    # ------------------------------------------------------------------
    # Inbound：将 WxMessage 转换为 UnifiedMessage
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将 Gateway Webhook 推送的原始 payload 转换为 UnifiedMessage。

        此方法用于兼容外部 Webhook 推送模式。
        主要接收路径是 WxDbWatcher → convert_wx_message()。
        """
        # 从 raw_payload 构造 WxMessage 再转换
        try:
            wx_msg = WxMessage(
                msg_id=str(raw_payload.get("msg_id") or raw_payload.get("MsgSvrID") or ""),
                from_wxid=str(raw_payload.get("from_wxid") or raw_payload.get("StrTalker") or ""),
                to_wxid=str(raw_payload.get("to_wxid") or ""),
                conversation_id=str(raw_payload.get("conversation_id") or raw_payload.get("StrTalker") or ""),
                content=str(raw_payload.get("content") or raw_payload.get("StrContent") or ""),
                msg_type=int(raw_payload.get("msg_type") or raw_payload.get("Type") or 1),
                ts_ms=int(raw_payload.get("ts_ms") or (time.time() * 1000)),
                is_sender=bool(raw_payload.get("is_sender") or raw_payload.get("IsSender") or False),
            )
            return self.convert_wx_message(wx_msg)
        except Exception as exc:
            logger.warning("WeChatAdapter: parse_inbound 失败: %s", exc)
            return None

    def convert_wx_message(self, wx_msg: WxMessage) -> UnifiedMessage | None:
        """将 WxMessage 转换为 UnifiedMessage。

        这是 WxDbWatcher 回调的主要转换入口。

        Args:
            wx_msg: 来自 WxDbService.poll_new_messages() 的消息

        Returns:
            UnifiedMessage，或 None（自己发的消息/系统消息等跳过）
        """
        # 跳过自己发出的消息
        if wx_msg.is_sender:
            self._stats["inbound_skipped"] += 1
            return None

        # 跳过系统消息
        msg_type = MsgType.from_wx_type(wx_msg.msg_type)
        if msg_type == MsgType.SYSTEM:
            self._stats["inbound_skipped"] += 1
            return None

        # 跳过空内容（非文本消息暂时跳过，Phase 5 处理多媒体）
        if not wx_msg.content and msg_type == MsgType.TEXT:
            self._stats["inbound_skipped"] += 1
            return None

        is_group = wx_msg.conversation_id.endswith("@chatroom")

        self._stats["inbound_parsed"] += 1
        return UnifiedMessage(
            msg_id=wx_msg.msg_id,
            platform=Platform.WECHAT,
            from_id=wx_msg.from_wxid or wx_msg.conversation_id,
            to_id=wx_msg.to_wxid,
            conversation_id=wx_msg.conversation_id,
            msg_type=msg_type,
            content=wx_msg.content,
            ts_ms=wx_msg.ts_ms,
            is_group=is_group,
            media_url=wx_msg.media_url,
            raw_extra=wx_msg.raw,
        )

    # ------------------------------------------------------------------
    # Outbound：通过 pywinauto 发送回复
    # ------------------------------------------------------------------

    def send_reply(self, reply: UnifiedReply) -> bool:
        """将回复发送到微信。

        发送策略：
          - 文本消息：剪贴板 + Ctrl+V → Enter
          - 图片消息：剪贴板粘贴图片文件
          - 其他类型：暂不支持，返回 False

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功
        """
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning(
                "WeChatAdapter: 暂不支持发送 %s 类型消息", reply.msg_type.value
            )
            return False

        try:
            success = self._send_via_pywinauto(reply)
            if success:
                self._stats["outbound_sent"] += 1
            else:
                self._stats["outbound_failed"] += 1
            return success
        except Exception as exc:
            logger.error("WeChatAdapter: 发送失败 conversation=%s: %s", reply.conversation_id, exc)
            self._stats["outbound_failed"] += 1
            return False

    def _send_via_pywinauto(self, reply: UnifiedReply) -> bool:
        """通过 pywinauto 精确定位输入框并发送消息。"""
        try:
            import pywinauto
            from pywinauto import Application
        except ImportError:
            logger.error("WeChatAdapter: pywinauto 未安装，无法发送消息")
            return False

        try:
            app = Application(backend="uia").connect(title_re="微信")
            main_wnd = app.window(title_re="微信")

            # 确保聊天窗口已打开（通过搜索框定位联系人）
            if not self._ensure_chat_open(main_wnd, reply.conversation_id):
                return False

            # 定位输入框（精确控件定位，不用截图猜坐标）
            input_box = main_wnd.child_window(
                control_type="Edit",
                found_index=0,
            )
            input_box.set_focus()

            if reply.msg_type == MsgType.TEXT:
                # 通过剪贴板粘贴（支持中文，避免 type_keys 编码问题）
                self._set_clipboard_text(reply.content)
                input_box.type_keys("^v")   # Ctrl+V
                input_box.type_keys("{ENTER}")
                return True

            elif reply.msg_type == MsgType.IMAGE and reply.media_path:
                # 图片通过剪贴板粘贴文件
                self._set_clipboard_file(reply.media_path)
                input_box.type_keys("^v")
                input_box.type_keys("{ENTER}")
                return True

        except Exception as exc:
            logger.error("WeChatAdapter: pywinauto 操作失败: %s", exc)

        return False

    def _ensure_chat_open(self, main_wnd: Any, conversation_id: str) -> bool:
        """确保指定会话的聊天窗口已打开。"""
        try:
            search_box = main_wnd.child_window(
                auto_id="Search",
                control_type="Edit",
            )
            search_box.set_focus()
            search_box.set_text("")
            search_box.type_keys(conversation_id, with_spaces=True)

            # 等待搜索结果
            import time as _time
            _time.sleep(0.5)

            result = main_wnd.child_window(control_type="ListItem")
            result.wait("visible", timeout=3)
            result.click_input()
            _time.sleep(0.3)
            return True
        except Exception as exc:
            logger.warning("WeChatAdapter: 打开聊天窗口失败 %s: %s", conversation_id, exc)
            return False

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        """将文本写入剪贴板。"""
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except ImportError:
            # 降级：使用 tkinter
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()

    @staticmethod
    def _set_clipboard_file(file_path: str) -> None:
        """将文件路径写入剪贴板（用于粘贴文件）。"""
        try:
            import win32clipboard
            import win32con
            from pathlib import Path
            path = str(Path(file_path).resolve())
            # HDROP 格式粘贴文件
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            # 构造 DROPFILES 结构
            import struct
            files = path + "\x00\x00"
            files_bytes = files.encode("utf-16-le")
            header = struct.pack("IIIII", 20, 0, 0, 0, 1)  # DROPFILES header
            data = header + files_bytes
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            win32clipboard.CloseClipboard()
        except Exception as exc:
            logger.warning("WeChatAdapter: 设置文件剪贴板失败: %s", exc)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        db_stats = self._svc.stats()
        return {
            "platform": self.platform_id.value,
            "status": "ok" if db_stats["contact_db_exists"] else "degraded",
            "self_wxid": self._self_wxid,
            "db_stats": db_stats,
            "stats": dict(self._stats),
        }
