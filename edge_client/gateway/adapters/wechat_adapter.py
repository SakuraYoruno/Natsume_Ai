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
import json
import os
import re
import subprocess
import time
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

_WECHAT_WINDOW_TITLE_TOKENS: tuple[str, ...] = ("wechat", "weixin", "微信")


_WECHAT_PRIMARY_PROCESS_NAMES: set[str] = {
    "weixin.exe",
    "wechat.exe",
}
_WECHAT_AUX_PROCESS_NAMES: set[str] = {
    "weixinappex.exe",
    "weixinapp.exe",
    "wechatappex.exe",
    "wechatapp.exe",
}
_WECHAT_PROCESS_NAMES: set[str] = _WECHAT_PRIMARY_PROCESS_NAMES | _WECHAT_AUX_PROCESS_NAMES


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
        self._last_outbound: dict[str, Any] = {
            "success": None,
            "detail": "",
            "elapsed_ms": 0,
            "ts_ms": 0,
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
        conversation_id = str(wx_msg.conversation_id or "").strip()
        if self._is_placeholder_conversation_id(conversation_id):
            self._stats["inbound_skipped"] += 1
            logger.warning(
                "WeChatAdapter: skip placeholder conversation msg_id=%s conversation=%s",
                wx_msg.msg_id,
                conversation_id,
            )
            return None

        # 跳过自己发出的消息
        if wx_msg.is_sender or (
            bool(self._self_wxid) and str(wx_msg.from_wxid or "").strip() == str(self._self_wxid)
        ):
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

        is_group = conversation_id.endswith("@chatroom")

        self._stats["inbound_parsed"] += 1
        return UnifiedMessage(
            msg_id=wx_msg.msg_id,
            platform=Platform.WECHAT,
            from_id=wx_msg.from_wxid or conversation_id,
            to_id=wx_msg.to_wxid,
            conversation_id=conversation_id,
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

    def _send_reply_legacy(self, reply: UnifiedReply) -> bool:
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

    def send_reply(self, reply: UnifiedReply) -> bool:
        start_ms = int(time.time() * 1000)
        if reply.msg_type not in (MsgType.TEXT, MsgType.IMAGE):
            logger.warning("WeChatAdapter: unsupported msg_type=%s", reply.msg_type.value)
            self._last_outbound = {
                "success": False,
                "detail": f"unsupported msg_type={reply.msg_type.value}",
                "elapsed_ms": max(0, int(time.time() * 1000) - start_ms),
                "ts_ms": int(time.time() * 1000),
            }
            return False

        if reply.msg_type == MsgType.TEXT:
            normalized_text = self._normalize_outbound_text(reply.content)
            if normalized_text != str(reply.content or ""):
                logger.info("WeChatAdapter: normalized outbound text payload conversation=%s", reply.conversation_id)
            reply.content = normalized_text
            if not reply.content.strip():
                logger.warning("WeChatAdapter: empty outbound text after normalize conversation=%s", reply.conversation_id)
                self._last_outbound = {
                    "success": False,
                    "detail": "empty outbound text",
                    "elapsed_ms": max(0, int(time.time() * 1000) - start_ms),
                    "ts_ms": int(time.time() * 1000),
                }
                return False

        try:
            success = bool(self._send_via_pywinauto(reply))
        except Exception as exc:
            logger.error("WeChatAdapter: send failed conversation=%s err=%s", reply.conversation_id, exc)
            self._stats["outbound_failed"] += 1
            self._last_outbound = {
                "success": False,
                "detail": str(exc),
                "elapsed_ms": max(0, int(time.time() * 1000) - start_ms),
                "ts_ms": int(time.time() * 1000),
            }
            return False

        if success:
            self._stats["outbound_sent"] += 1
            detail = "sent"
        else:
            self._stats["outbound_failed"] += 1
            detail = "send pipeline returned false"
        self._last_outbound = {
            "success": success,
            "detail": detail,
            "elapsed_ms": max(0, int(time.time() * 1000) - start_ms),
            "ts_ms": int(time.time() * 1000),
        }
        return success

    def _normalize_outbound_text(self, raw_text: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        if not (text.startswith("{") and text.endswith("}")):
            return text
        try:
            payload = json.loads(text)
        except Exception:
            return text
        extracted = self._extract_text_from_payload(payload).strip()
        return extracted if extracted else text

    def _extract_text_from_payload(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            narrative = payload.get("narrative")
            if isinstance(narrative, dict):
                narrative_text = narrative.get("text")
                if isinstance(narrative_text, str) and narrative_text.strip():
                    return narrative_text
            for key in ("content", "text", "reply", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
                if isinstance(value, dict):
                    nested = self._extract_text_from_payload(value)
                    if nested.strip():
                        return nested
        return ""

    def _send_via_pywinauto(self, reply: UnifiedReply) -> bool:
        """通过 pywinauto 精确定位输入框并发送消息。"""
        try:
            from pywinauto import Application
        except ImportError:
            logger.error("WeChatAdapter: pywinauto 未安装，无法发送消息")
            return False

        if reply.msg_type == MsgType.TEXT:
            if self._send_to_top_session_global_wake(reply):
                return True
            if self._send_via_global_keyboard_flow(reply):
                return True

        try:
            main_wnd = self._resolve_desktop_wechat_window()
            app = None
            if main_wnd is None:
                app = Application(backend="uia")
                try:
                    app.connect(title_re=".*(Weixin|WeChat|WX|微信).*", timeout=1.5)
                except Exception:
                    try:
                        app.connect(path="Weixin.exe", timeout=1.5)
                    except Exception:
                        app = None

            if main_wnd is None and app is not None:
                main_wnd = self._resolve_main_window(app)
            if main_wnd is None:
                try:
                    alt_app = Application(backend="uia")
                    alt_app.connect(title_re=".*(Weixin|WeChat|WX|微信).*", timeout=1.5)
                    main_wnd = self._resolve_main_window(alt_app)
                except Exception:
                    try:
                        alt_app = Application(backend="uia")
                        alt_app.connect(path="Weixin.exe", timeout=1.5)
                        main_wnd = self._resolve_main_window(alt_app)
                    except Exception:
                        main_wnd = None
            if main_wnd is None:
                self._wake_wechat_client()
                if self._send_to_top_session_global_wake(reply):
                    return True
                if self._allow_keyboard_search_fallback() and self._send_via_global_keyboard_flow(reply):
                    return True
                logger.error("WeChatAdapter: main window not found")
                return False
            if self._window_interaction_score(main_wnd) <= 0:
                class_name = str(getattr(getattr(main_wnd, "element_info", None), "class_name", "") or "")
                class_norm = self._normalize_match_text(class_name)
                logger.warning(
                    "WeChatAdapter: main window interaction score is low, continue with keyboard fallback class=%s",
                    class_norm,
                )
                self._log_window_snapshot(
                    main_wnd,
                    reason="main_window_low_interaction",
                    conversation_id=reply.conversation_id,
                )
            try:
                main_wnd.restore()
            except Exception:
                pass
            try:
                main_wnd.set_focus()
            except Exception:
                pass
            time.sleep(0.15)
            send_started_ts = int(time.time() * 1000)
            self._dismiss_search_overlay(main_wnd)
            is_top_target = self._is_top_session_target(reply.conversation_id)
            if (
                reply.msg_type == MsgType.TEXT
                and self._send_to_top_session_directly(
                    main_wnd,
                    reply,
                    send_started_ts=send_started_ts,
                )
            ):
                return True
            if reply.msg_type == MsgType.TEXT and is_top_target and not self._allow_keyboard_search_fallback():
                if self._send_to_top_session_global_wake(reply):
                    return True
                if self._send_via_global_keyboard_flow(reply):
                    logger.info(
                        "WeChatAdapter: top-session fallback switched to global keyboard flow conversation=%s",
                        reply.conversation_id,
                    )
                    return True
                self._log_window_snapshot(
                    main_wnd,
                    reason="direct_top_session_input_unavailable",
                    conversation_id=reply.conversation_id,
                )
                return False

            # 确保聊天窗口已打开（通过搜索框定位联系人）
            if not self._ensure_chat_open(main_wnd, reply.conversation_id):
                logger.warning(
                    "WeChatAdapter: _ensure_chat_open failed conversation=%s",
                    reply.conversation_id,
                )
                if not self._allow_keyboard_search_fallback() and not is_top_target:
                    if self._send_via_global_keyboard_flow(reply):
                        logger.info(
                            "WeChatAdapter: ensure-chat-open fallback switched to global keyboard flow conversation=%s",
                            reply.conversation_id,
                        )
                        return True
                    self._log_window_snapshot(
                        main_wnd,
                        reason="ensure_chat_open_failed_search_disabled",
                        conversation_id=reply.conversation_id,
                    )
                    return False
                if self._allow_keyboard_search_fallback() and self._send_via_keyboard_flow(main_wnd, reply):
                    return True
                if not self._allow_active_chat_fallback():
                    self._log_window_snapshot(
                        main_wnd,
                        reason="ensure_chat_open_failed",
                        conversation_id=reply.conversation_id,
                    )
                    return False
                logger.warning(
                    "WeChatAdapter: _ensure_chat_open failed, fallback to active chat send conversation=%s",
                    reply.conversation_id,
                )
            self._switch_to_chat_tab(main_wnd)
            self._dismiss_search_overlay(main_wnd)

            input_box = self._resolve_input_box(main_wnd)
            if input_box is None:
                if self._allow_active_chat_fallback():
                    self._recover_to_chat_home(main_wnd)
                    if input_box is None and self._try_enter_chat_from_detail_view(main_wnd):
                        input_box = self._resolve_input_box(main_wnd)
                    self._switch_to_chat_tab(main_wnd)
                    self._dismiss_search_overlay(main_wnd)
                    input_box = self._resolve_input_box(main_wnd)
            if input_box is None:
                self._log_window_snapshot(
                    main_wnd,
                    reason="input_not_found",
                    conversation_id=reply.conversation_id,
                )
                logger.warning(
                    "WeChatAdapter: input box not found, fallback to window keyboard send conversation=%s",
                    reply.conversation_id,
                )
                if reply.msg_type == MsgType.TEXT:
                    if self._allow_keyboard_search_fallback() and self._send_via_keyboard_flow(main_wnd, reply):
                        return True
                    if self._send_via_global_keyboard_flow(reply):
                        logger.info(
                            "WeChatAdapter: input-missing fallback switched to global keyboard flow conversation=%s",
                            reply.conversation_id,
                        )
                        return True
                    return False
                if reply.msg_type == MsgType.IMAGE and reply.media_path:
                    return False
                logger.error(
                    "WeChatAdapter: fallback send unsupported conversation=%s msg_type=%s",
                    reply.conversation_id,
                    reply.msg_type.value,
                )
                return False

            if reply.msg_type == MsgType.TEXT:
                self._set_clipboard_text(reply.content)
                sent = self._paste_and_send(input_box, main_wnd)
                if not sent:
                    return False
                return self._verify_send_outcome(
                    conversation_id=reply.conversation_id,
                    expected_text=reply.content,
                    since_ts_ms=send_started_ts,
                )

            elif reply.msg_type == MsgType.IMAGE and reply.media_path:
                self._set_clipboard_file(reply.media_path)
                return self._paste_and_send(input_box, main_wnd)

        except Exception as exc:
            logger.error("WeChatAdapter: pywinauto 操作失败: %s", exc)

        return False

    def _send_to_top_session_directly(
        self,
        main_wnd: Any,
        reply: UnifiedReply,
        *,
        send_started_ts: int,
    ) -> bool:
        if not self._is_top_session_target(reply.conversation_id):
            logger.debug(
                "WeChatAdapter: skip direct top-session send (not top) conversation=%s",
                reply.conversation_id,
            )
            return False

        logger.info("WeChatAdapter: direct top-session send start conversation=%s", reply.conversation_id)
        try:
            self._switch_to_chat_tab(main_wnd)
            input_box = None
            for _ in range(2):
                self._dismiss_search_overlay(main_wnd)
                input_box = self._resolve_input_box(main_wnd)
                if input_box is not None:
                    break
                time.sleep(0.12)
            if input_box is None:
                logger.warning(
                    "WeChatAdapter: direct top-session send aborted quickly, input box unavailable conversation=%s",
                    reply.conversation_id,
                )
                return False
            self._set_clipboard_text(reply.content)
            if not self._paste_and_send(input_box, main_wnd):
                logger.warning(
                    "WeChatAdapter: direct top-session paste/send failed conversation=%s",
                    reply.conversation_id,
                )
                return False
            ok = self._verify_send_outcome(
                conversation_id=reply.conversation_id,
                expected_text=reply.content,
                since_ts_ms=send_started_ts,
                timeout_s=2.8,
            )
            if ok:
                logger.info(
                    "WeChatAdapter: direct top-session send success conversation=%s",
                    reply.conversation_id,
                )
            else:
                logger.warning(
                    "WeChatAdapter: direct top-session send verify failed conversation=%s",
                    reply.conversation_id,
                )
            return ok
        except Exception as exc:
            logger.warning(
                "WeChatAdapter: direct top-session send failed conversation=%s err=%s",
                reply.conversation_id,
                exc,
            )
            return False

    def _is_top_session_target(self, conversation_id: str) -> bool:
        try:
            return bool(self._svc.is_top_session(conversation_id))
        except Exception as exc:
            logger.debug(
                "WeChatAdapter: is_top_session query failed conversation=%s err=%s",
                conversation_id,
                exc,
            )
            return False

    def _send_via_global_keyboard_flow(self, reply: UnifiedReply) -> bool:
        if reply.msg_type != MsgType.TEXT:
            return False
        try:
            import win32gui
            import win32con
            from pywinauto.keyboard import send_keys
        except Exception:
            return False

        candidates = self._build_search_candidates(reply.conversation_id)
        if not candidates:
            return False

        hwnd = self._find_wechat_hwnd()
        if not hwnd:
            logger.warning("WeChatAdapter: global keyboard flow no wechat hwnd")
            return False
        if not self._activate_hwnd(hwnd):
            logger.warning(
                "WeChatAdapter: global keyboard flow activate hwnd failed target=%s foreground=%s",
                hwnd,
                self._foreground_window_debug(),
            )
        target_wnd = None
        try:
            from pywinauto import Desktop
            target_wnd = Desktop(backend="uia").window(handle=int(hwnd))
        except Exception:
            target_wnd = None

        def _emit_keys(keys: str) -> None:
            try:
                send_keys(keys)
                return
            except Exception:
                pass
            if target_wnd is not None:
                try:
                    target_wnd.type_keys(keys)
                    return
                except Exception:
                    pass

        logger.info(
            "WeChatAdapter: global keyboard flow foreground after activate=%s",
            self._foreground_window_debug(),
        )
        time.sleep(0.2)

        if target_wnd is not None:
            try:
                self._focus_chat_input_fallback(target_wnd)
                attempt_ts_ms = int(time.time() * 1000)
                sent = self._send_via_physical_active_chat(target_wnd, reply.content)
                logger.info(
                    "WeChatAdapter: global keyboard flow physical active-chat send attempted conversation=%s sent=%s",
                    reply.conversation_id,
                    sent,
                )
                if not sent:
                    self._set_clipboard_text(reply.content)
                    _emit_keys("^v")
                    time.sleep(0.06)
                    _emit_keys("{ENTER}")
                if self._verify_send_outcome(
                    conversation_id=reply.conversation_id,
                    expected_text=reply.content,
                    since_ts_ms=attempt_ts_ms,
                    timeout_s=2.4,
                ):
                    logger.info(
                        "WeChatAdapter: global keyboard flow success via active-chat direct send conversation=%s",
                        reply.conversation_id,
                    )
                    return True
            except Exception as exc:
                logger.warning(
                    "WeChatAdapter: global keyboard flow active-chat direct send failed conversation=%s err=%s",
                    reply.conversation_id,
                    exc,
                )

        if target_wnd is not None:
            try:
                self._switch_to_chat_tab(target_wnd)
                self._dismiss_search_overlay(target_wnd)
                selected = self._select_chat_from_chat_list(target_wnd, candidates, reply.conversation_id)
                if not selected:
                    selected = self._select_top_chat_entry(target_wnd)
                    if selected:
                        logger.info(
                            "WeChatAdapter: global keyboard flow selected top chat entry conversation=%s",
                            reply.conversation_id,
                        )
                if selected:
                    attempt_ts_ms = int(time.time() * 1000)
                    self._set_clipboard_text(reply.content)
                    if self._paste_and_send(target_wnd, target_wnd) and self._verify_send_outcome(
                        conversation_id=reply.conversation_id,
                        expected_text=reply.content,
                        since_ts_ms=attempt_ts_ms,
                        timeout_s=2.8,
                    ):
                        logger.info(
                            "WeChatAdapter: global keyboard flow success via chat-list click conversation=%s",
                            reply.conversation_id,
                        )
                        return True
                    logger.warning(
                        "WeChatAdapter: global keyboard flow chat-list click verify failed conversation=%s",
                        reply.conversation_id,
                    )
            except Exception as exc:
                logger.warning(
                    "WeChatAdapter: global keyboard flow chat-list preflight failed conversation=%s err=%s",
                    reply.conversation_id,
                    exc,
                )

        try:
            _emit_keys("^1")
            time.sleep(0.10)
            _emit_keys("{ESC}")
            time.sleep(0.08)
            _emit_keys("{HOME}")
            time.sleep(0.08)
            _emit_keys("{DOWN}")
            time.sleep(0.08)
            _emit_keys("{ENTER}")
            time.sleep(0.20)
            self._focus_chat_input_fallback(target_wnd)
            attempt_ts_ms = int(time.time() * 1000)
            self._set_clipboard_text(reply.content)
            _emit_keys("^v")
            time.sleep(0.05)
            _emit_keys("{ENTER}")
            if self._verify_send_outcome(
                conversation_id=reply.conversation_id,
                expected_text=reply.content,
                since_ts_ms=attempt_ts_ms,
                timeout_s=2.8,
            ):
                logger.info(
                    "WeChatAdapter: global keyboard flow success via first-chat fallback conversation=%s",
                    reply.conversation_id,
                )
                return True
            logger.warning(
                "WeChatAdapter: global keyboard flow first-chat fallback verify failed conversation=%s",
                reply.conversation_id,
            )
        except Exception as exc:
            logger.warning(
                "WeChatAdapter: global keyboard flow first-chat fallback failed conversation=%s err=%s",
                reply.conversation_id,
                exc,
            )

        for keyword in candidates[:3]:
            for search_shortcut in ("^n", "^f"):
                try:
                    logger.info(
                        "WeChatAdapter: global keyboard flow try conversation=%s keyword=%s shortcut=%s foreground=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                        self._foreground_window_debug(),
                    )
                    _emit_keys("^1")
                    time.sleep(0.12)
                    _emit_keys("{ESC}")
                    time.sleep(0.08)
                    _emit_keys(search_shortcut)
                    time.sleep(0.15)
                    _emit_keys("^a{BACKSPACE}")
                    self._set_clipboard_text(keyword)
                    _emit_keys("^v")
                    time.sleep(0.28)
                    _emit_keys("{DOWN}")
                    time.sleep(0.08)
                    _emit_keys("{ENTER}")
                    time.sleep(0.32)
                    _emit_keys("{ESC}")
                    time.sleep(0.08)
                    attempt_ts_ms = int(time.time() * 1000)
                    self._set_clipboard_text(reply.content)
                    _emit_keys("^v")
                    time.sleep(0.05)
                    _emit_keys("{ENTER}")
                    if self._verify_send_outcome(
                        conversation_id=reply.conversation_id,
                        expected_text=reply.content,
                        since_ts_ms=attempt_ts_ms,
                        timeout_s=2.8,
                    ):
                        logger.info(
                            "WeChatAdapter: global keyboard flow success conversation=%s keyword=%s shortcut=%s",
                            reply.conversation_id,
                            keyword,
                            search_shortcut,
                        )
                        return True
                    logger.warning(
                        "WeChatAdapter: global keyboard flow verify failed conversation=%s keyword=%s shortcut=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                    )
                except Exception as exc:
                    logger.warning(
                        "WeChatAdapter: global keyboard flow failed conversation=%s keyword=%s shortcut=%s err=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                        exc,
                    )
                    continue
        return False

    def _send_to_top_session_global_wake(self, reply: UnifiedReply) -> bool:
        if reply.msg_type != MsgType.TEXT:
            return False
        if not self._is_top_session_target(reply.conversation_id):
            return False
        try:
            import win32gui
            import win32con
            from pywinauto.keyboard import send_keys
        except Exception:
            return False

        hwnd = self._find_wechat_hwnd()
        if not hwnd:
            self._wake_wechat_client()
            hwnd = self._find_wechat_hwnd()
        if not hwnd:
            return False
        if not self._activate_hwnd(hwnd):
            logger.warning(
                "WeChatAdapter: top-session global-wake activate hwnd failed target=%s foreground=%s",
                hwnd,
                self._foreground_window_debug(),
            )
        target_wnd = None
        try:
            from pywinauto import Desktop
            target_wnd = Desktop(backend="uia").window(handle=int(hwnd))
        except Exception:
            target_wnd = None

        def _emit_keys(keys: str) -> None:
            try:
                send_keys(keys)
                return
            except Exception:
                pass
            if target_wnd is not None:
                try:
                    target_wnd.type_keys(keys)
                    return
                except Exception:
                    pass

        logger.info(
            "WeChatAdapter: top-session global-wake foreground after activate=%s",
            self._foreground_window_debug(),
        )
        time.sleep(0.18)
        try:
            _emit_keys("^1")
            time.sleep(0.10)
            _emit_keys("{ESC}")
            time.sleep(0.08)
            self._focus_chat_input_fallback(target_wnd)
            attempt_ts_ms = int(time.time() * 1000)
            try:
                self._set_clipboard_text(reply.content)
                _emit_keys("^v")
            except Exception as clip_exc:
                logger.warning(
                    "WeChatAdapter: global-wake clipboard unavailable, fallback type conversation=%s err=%s",
                    reply.conversation_id,
                    clip_exc,
                )
                if not self._type_text_direct(reply.content):
                    return False
            time.sleep(0.05)
            _emit_keys("{ENTER}")
            ok = self._verify_send_outcome(
                conversation_id=reply.conversation_id,
                expected_text=reply.content,
                since_ts_ms=attempt_ts_ms,
                timeout_s=2.8,
            )
            if ok:
                logger.info(
                    "WeChatAdapter: top-session global-wake send success conversation=%s",
                    reply.conversation_id,
                )
            else:
                logger.warning(
                    "WeChatAdapter: top-session global-wake verify failed conversation=%s",
                    reply.conversation_id,
                )
            return ok
        except Exception as exc:
            logger.warning(
                "WeChatAdapter: top-session global-wake send failed conversation=%s err=%s",
                reply.conversation_id,
                exc,
            )
            return False

    @staticmethod
    def _type_text_direct(text: str) -> bool:
        try:
            from pywinauto.keyboard import send_keys
        except Exception:
            return False
        try:
            send_keys(str(text or ""), with_spaces=True, with_tabs=True, with_newlines=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _focus_chat_input_fallback(main_wnd: Any) -> bool:
        try:
            main_wnd.set_focus()
        except Exception:
            pass
        try:
            rect = main_wnd.rectangle()
            width = max(0, int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0)))
            height = max(0, int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0)))
            if width <= 0 or height <= 0:
                return False
            click_points = (
                (0.78, 0.84),
                (0.74, 0.82),
                (0.84, 0.84),
            )
            for x_ratio, y_ratio in click_points:
                x = max(8, int(width * float(x_ratio)))
                y = max(8, int(height * float(y_ratio)))
                try:
                    main_wnd.click_input(coords=(x, y))
                    time.sleep(0.05)
                    return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    @staticmethod
    def _send_via_physical_active_chat(main_wnd: Any, text: str) -> bool:
        try:
            import pyautogui
        except Exception:
            return False
        if not str(text or "").strip():
            return False
        try:
            rect = main_wnd.rectangle()
            left = int(getattr(rect, "left", 0))
            top = int(getattr(rect, "top", 0))
            right = int(getattr(rect, "right", 0))
            bottom = int(getattr(rect, "bottom", 0))
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width <= 0 or height <= 0:
                return False
            x = int(left + width * 0.78)
            y = int(top + height * 0.84)
            logger.info(
                "WeChatAdapter: physical send click point=(%s,%s) rect=(%s,%s,%s,%s)",
                x,
                y,
                left,
                top,
                right,
                bottom,
            )
            pyautogui.click(x=x, y=y)
            time.sleep(0.06)
            pasted = False
            try:
                import pyperclip
                pyperclip.copy(str(text))
                pyautogui.hotkey("ctrl", "v")
                pasted = True
            except Exception:
                pasted = False
            if not pasted:
                pyautogui.typewrite(str(text), interval=0.01)
            time.sleep(0.05)
            pyautogui.press("enter")
            return True
        except Exception as exc:
            logger.warning("WeChatAdapter: physical active-chat send failed err=%s", exc)
            return False

    @staticmethod
    def _activate_hwnd(hwnd: int) -> bool:
        if int(hwnd or 0) <= 0:
            return False
        try:
            import win32gui
            import win32con
        except Exception:
            return False
        try:
            from pywinauto.keyboard import send_keys
        except Exception:
            send_keys = None

        target = int(hwnd)
        for _ in range(3):
            try:
                win32gui.ShowWindow(target, win32con.SW_RESTORE)
            except Exception:
                pass
            try:
                win32gui.BringWindowToTop(target)
            except Exception:
                pass
            try:
                win32gui.SetForegroundWindow(target)
            except Exception:
                if send_keys is not None:
                    try:
                        send_keys("%")
                    except Exception:
                        pass
                    try:
                        win32gui.SetForegroundWindow(target)
                    except Exception:
                        pass
            time.sleep(0.10)
            try:
                if int(win32gui.GetForegroundWindow() or 0) == target:
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _foreground_window_debug() -> dict[str, Any]:
        try:
            import win32gui
            import win32process
        except Exception:
            return {}
        try:
            hwnd = int(win32gui.GetForegroundWindow() or 0)
            if hwnd <= 0:
                return {}
            title = str(win32gui.GetWindowText(hwnd) or "")
            class_name = str(win32gui.GetClassName(hwnd) or "")
            _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            return {
                "hwnd": hwnd,
                "pid": int(pid),
                "class": class_name,
                "title": title[:80],
            }
        except Exception:
            return {}

    def _wake_wechat_client(self) -> bool:
        candidates: list[str] = []
        env_keys = (
            "WECHAT_EXE_PATH",
            "WECHAT_EXECUTABLE_PATH",
            "EDGE_WECHAT_EXECUTABLE_PATH",
        )
        for key in env_keys:
            value = str(os.environ.get(key, "") or "").strip()
            if value:
                candidates.append(value)
        candidates.append(r"C:\Program Files\Tencent\Weixin\Weixin.exe")

        for exe_path in candidates:
            path = str(exe_path or "").strip()
            if not path:
                continue
            if not os.path.exists(path):
                continue
            try:
                subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.8)
                logger.info("WeChatAdapter: wake wechat client via executable=%s", path)
                return True
            except Exception:
                continue
        return False

    def _find_wechat_hwnd(self) -> int:
        try:
            import win32gui
            import win32process
        except Exception:
            return 0

        windows: list[tuple[int, int, str, str, int, int]] = []
        inspected: list[dict[str, Any]] = []

        def _enum_handler(hwnd: int, _: Any) -> None:
            try:
                title = str(win32gui.GetWindowText(hwnd) or "")
                class_name = str(win32gui.GetClassName(hwnd) or "")
                class_norm = self._normalize_match_text(class_name)
                if self._is_blocked_wechat_window_class(class_norm):
                    return
                normalized = self._normalize_match_text(title)
                has_title_match = any(token in normalized for token in _WECHAT_WINDOW_TITLE_TOKENS)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = ""
                is_weixin_process = False
                is_aux_process = False
                try:
                    import psutil
                    proc_name = str(psutil.Process(int(pid)).name() or "").lower()
                    is_weixin_process = proc_name in _WECHAT_PROCESS_NAMES
                    is_aux_process = proc_name in _WECHAT_AUX_PROCESS_NAMES
                except Exception:
                    is_weixin_process = False
                    is_aux_process = False
                if not is_weixin_process and not has_title_match:
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                area = max(0, int(right) - int(left)) * max(0, int(bottom) - int(top))
                row = {
                    "hwnd": int(hwnd),
                    "pid": int(pid),
                    "class": class_name[:64],
                    "title": title[:64],
                    "area": int(area),
                    "process": proc_name,
                    "is_weixin_process": bool(is_weixin_process),
                    "has_title_match": bool(has_title_match),
                }
                if area <= 0:
                    if has_title_match:
                        area = 1
                        row["area"] = 1
                    else:
                        row["skip"] = "zero_area"
                        inspected.append(row)
                        return
                if is_weixin_process and not has_title_match and area < 40_000:
                    row["skip"] = "small_area_without_title"
                    inspected.append(row)
                    return
                interaction = self._interaction_score_for_hwnd(int(hwnd))
                row["interaction"] = int(interaction)
                if is_aux_process and "chrome_widgetwin" in class_norm and interaction <= 0:
                    row["skip"] = "aux_chrome_noninteractive"
                    inspected.append(row)
                    return
                if self._is_forced_shell_window(class_norm, normalized, interaction):
                    row["skip"] = "forced_shell"
                    inspected.append(row)
                    return
                row["accepted"] = True
                inspected.append(row)
                windows.append((area, hwnd, title, class_name, int(pid), interaction))
            except Exception:
                return

        try:
            win32gui.EnumWindows(_enum_handler, None)
        except Exception:
            return 0
        if not windows:
            if inspected:
                logger.warning(
                    "WeChatAdapter: _find_wechat_hwnd no matched window inspected=%s",
                    inspected[:8],
                )
            else:
                logger.warning("WeChatAdapter: _find_wechat_hwnd no matched window")
            return 0
        def _pick_score(item: tuple[int, int, str, str, int, int]) -> int:
            area, _hwnd, title, class_name, _pid, interaction = item
            class_norm = self._normalize_match_text(class_name)
            title_norm = self._normalize_match_text(title)
            score = int(area) + int(self._wechat_window_class_bias(class_norm))
            score += int(interaction) * 1_000_000
            if interaction <= 0 and "qwindowicon" in class_norm:
                score -= 800_000
            if any(token in title_norm for token in _WECHAT_WINDOW_TITLE_TOKENS):
                score += 500_000
            return score

        windows.sort(key=lambda item: (_pick_score(item), item[0]), reverse=True)
        preview = [
            {
                "hwnd": int(item[1]),
                "pid": int(item[4]),
                "class": str(item[3])[:64],
                "title": str(item[2])[:64],
                "area": int(item[0]),
                "interaction": int(item[5]),
                "score": int(_pick_score(item)),
            }
            for item in windows[:3]
        ]
        logger.info("WeChatAdapter: _find_wechat_hwnd candidates=%s", preview)
        best = windows[0]
        logger.info(
            "WeChatAdapter: _find_wechat_hwnd picked hwnd=%s pid=%s class=%s title=%s area=%s interaction=%s score=%s",
            best[1],
            best[4],
            best[3],
            best[2][:80],
            best[0],
            best[5],
            _pick_score(best),
        )
        return int(best[1])

    def _interaction_score_for_hwnd(self, hwnd: int) -> int:
        if int(hwnd or 0) <= 0:
            return 0
        try:
            from pywinauto import Desktop
        except Exception:
            return 0
        try:
            wnd = Desktop(backend="uia").window(handle=int(hwnd))
            if not wnd.exists(timeout=0.08):
                return 0
            return int(self._window_interaction_score(wnd))
        except Exception:
            return 0

    def _resolve_main_window(self, app: Any) -> Any | None:
        windows: list[Any] = []
        try:
            windows = list(app.windows())
        except Exception:
            windows = []

        best_wnd = None
        best_score = -1
        backup_wnd = None
        backup_score = -1
        for wnd in windows:
            try:
                if not wnd.is_visible():
                    continue
            except Exception:
                pass
            try:
                rect = wnd.rectangle()
                width = max(0, int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0)))
                height = max(0, int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0)))
            except Exception:
                width = 0
                height = 0
            area = width * height
            if area <= 0:
                continue
            title = self._normalize_match_text(self._safe_item_name(wnd))
            class_name = str(getattr(getattr(wnd, "element_info", None), "class_name", "") or "")
            class_norm = self._normalize_match_text(class_name)
            if self._is_blocked_wechat_window_class(class_norm):
                continue
            interactive_score = self._window_interaction_score(wnd)
            if self._is_forced_shell_window(class_norm, title, interactive_score):
                continue
            class_bias = self._wechat_window_class_bias(class_norm)
            backup = area + class_bias
            if title and any(token in title for token in _WECHAT_WINDOW_TITLE_TOKENS):
                backup += 2_000_000_000
            if backup > backup_score:
                backup_score = backup
                backup_wnd = wnd

            if interactive_score <= 0:
                continue

            score = area + interactive_score * 1_000_000 + class_bias
            if title and any(token in title for token in _WECHAT_WINDOW_TITLE_TOKENS):
                score += 2_000_000_000
            if score > best_score:
                best_score = score
                best_wnd = wnd

        if best_wnd is not None:
            return best_wnd

        try:
            fallback = app.window(title_re=".*(微信|Weixin|WeChat|WX).*")
            if fallback.exists(timeout=0.3):
                if self._window_interaction_score(fallback) > 0:
                    return fallback
        except Exception:
            pass

        try:
            top = app.top_window()
            if self._window_interaction_score(top) > 0:
                return top
            return None
        except Exception:
            return None

    def _resolve_desktop_wechat_window(self) -> Any | None:
        try:
            from pywinauto import Desktop
            windows = list(Desktop(backend="uia").windows())
        except Exception:
            return None

        best_wnd = None
        best_score = -1
        backup_wnd = None
        backup_score = -1
        for wnd in windows:
            title = self._normalize_match_text(self._safe_item_name(wnd))
            class_name = str(getattr(getattr(wnd, "element_info", None), "class_name", "") or "")
            class_norm = self._normalize_match_text(class_name)
            process_name = ""
            try:
                proc_id = int(getattr(getattr(wnd, "element_info", None), "process_id", 0) or 0)
                if proc_id > 0:
                    import psutil
                    process_name = str(psutil.Process(proc_id).name() or "").lower()
            except Exception:
                process_name = ""
            is_weixin_process = process_name in _WECHAT_PROCESS_NAMES
            if self._is_blocked_wechat_window_class(class_norm):
                continue
            if (
                not is_weixin_process
                and not any(token in title for token in _WECHAT_WINDOW_TITLE_TOKENS)
                and "qt" not in class_norm
                and "weixin" not in class_norm
                and "wechat" not in class_norm
            ):
                continue
            try:
                rect = wnd.rectangle()
                width = max(0, int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0)))
                height = max(0, int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0)))
            except Exception:
                width = 0
                height = 0
            area = width * height
            if area <= 0:
                continue
            interactive_score = self._window_interaction_score(wnd)
            if (
                process_name in _WECHAT_AUX_PROCESS_NAMES
                and "chrome_widgetwin" in class_norm
                and interactive_score <= 0
            ):
                continue
            if self._is_forced_shell_window(class_norm, title, interactive_score):
                continue
            class_bias = self._wechat_window_class_bias(class_norm)
            backup = area + class_bias
            if title and any(token in title for token in _WECHAT_WINDOW_TITLE_TOKENS):
                backup += 2_000_000_000
            if backup > backup_score:
                backup_score = backup
                backup_wnd = wnd

            if interactive_score <= 0:
                continue

            score = area + interactive_score * 1_000_000 + class_bias
            if title and any(token in title for token in _WECHAT_WINDOW_TITLE_TOKENS):
                score += 2_000_000_000
            if score > best_score:
                best_score = score
                best_wnd = wnd
        if best_wnd is not None:
            return best_wnd
        return None

    @staticmethod
    def _is_forced_shell_window(class_norm: str, title_norm: str, interaction: int) -> bool:
        if int(interaction) > 0:
            return False
        normalized_class = str(class_norm or "")
        if "qt51514qwindowicon" in normalized_class:
            return True
        if "qwindowicon" in normalized_class:
            return True
        if "qwindowtoolsavebits" in normalized_class:
            return True
        if "chrome_widgetwin" in normalized_class:
            return True
        return False

    @staticmethod
    def _window_richness(wnd: Any) -> int:
        try:
            return min(200, len(list(wnd.children())))
        except Exception:
            return 0

    def _window_interaction_score(self, wnd: Any) -> int:
        counts: dict[str, int] = {}
        for control_type in ("Edit", "Document", "List", "ListItem", "Button", "Pane", "Text"):
            try:
                counts[control_type] = len(list(wnd.descendants(control_type=control_type)))
            except Exception:
                counts[control_type] = 0

        if self._window_has_browser_shell_markers(wnd):
            return 0

        strong_controls = counts.get("Edit", 0) + counts.get("Document", 0)
        list_controls = counts.get("List", 0) + counts.get("ListItem", 0)
        text_controls = counts.get("Text", 0)
        structural_controls = list_controls + counts.get("Button", 0)
        pane_controls = counts.get("Pane", 0)
        if strong_controls <= 0 and structural_controls <= 0 and pane_controls <= 0:
            return 0
        score = (
            strong_controls * 6
            + counts.get("List", 0) * 3
            + counts.get("ListItem", 0) * 2
            + counts.get("Button", 0) * 2
            + min(12, text_controls)
            + min(20, pane_controls)
        )
        if strong_controls <= 0:
            if list_controls >= 2:
                score += 18
            elif list_controls >= 1:
                score += 12
            elif structural_controls >= 8:
                score += 20
            elif structural_controls >= 4 and pane_controls >= 4:
                score += 12
            elif counts.get("Button", 0) >= 6 and pane_controls >= 6:
                score += 10
            else:
                return 0
        return min(200, score)

    def _window_has_browser_shell_markers(self, wnd: Any) -> bool:
        markers = (
            "后退",
            "前进",
            "重新加载",
            "历史记录",
            "back",
            "forward",
            "reload",
            "history",
        )
        marker_hits: set[str] = set()
        controls: list[Any] = []
        try:
            controls.extend(list(wnd.descendants(control_type="Button")))
        except Exception:
            pass
        try:
            controls.extend(list(wnd.descendants(control_type="MenuItem")))
        except Exception:
            pass
        for ctrl in controls[:120]:
            name = self._normalize_match_text(self._safe_item_name(ctrl))
            if not name:
                continue
            for marker in markers:
                marker_norm = self._normalize_match_text(marker)
                if marker_norm and marker_norm in name:
                    marker_hits.add(marker_norm)
            if len(marker_hits) >= 2:
                return True
        return False

    def _ensure_chat_open(self, main_wnd: Any, conversation_id: str) -> bool:
        """确保指定会话的聊天窗口已打开。"""
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            logger.warning("WeChatAdapter: empty conversation_id, abort send")
            return False

        candidates = self._build_search_candidates(conversation_id)
        if not candidates:
            logger.warning("WeChatAdapter: no search candidates for conversation=%s", conversation_id)
            return False

        try:
            self._switch_to_chat_tab(main_wnd)
            self._dismiss_search_overlay(main_wnd)
            self._recover_to_chat_home(main_wnd)
            if self._select_chat_from_chat_list(main_wnd, candidates, conversation_id):
                return True
            if not self._allow_keyboard_search_fallback():
                logger.info(
                    "WeChatAdapter: search fallback disabled, skip search flow conversation=%s",
                    conversation_id,
                )
                return False

            search_box = self._resolve_search_box(main_wnd)
            if search_box is None:
                logger.warning(
                    "WeChatAdapter: search box not found after list-scan fallback, conversation=%s",
                    conversation_id,
                )
                return False
            chat_not_ready_hits = 0
            for keyword in candidates:
                if self._is_embedded_browser_view(main_wnd):
                    self._recover_to_chat_home(main_wnd)
                    search_box = self._resolve_search_box(main_wnd)
                    if search_box is None:
                        break
                self._clear_and_fill_search(search_box, keyword)
                time.sleep(0.45)
                matched = self._find_matched_result_item(main_wnd, candidates)
                if matched is None:
                    continue
                result_item, matched_name = matched
                result_item.click_input()
                time.sleep(0.25)
                self._dismiss_search_overlay(main_wnd)
                if not self._is_chat_context_ready(main_wnd):
                    chat_not_ready_hits += 1
                    logger.warning(
                        "WeChatAdapter: search matched but chat context not ready conversation=%s keyword=%s",
                        conversation_id,
                        keyword,
                    )
                    self._recover_to_chat_home(main_wnd)
                    search_box = self._resolve_search_box(main_wnd)
                    if search_box is None or chat_not_ready_hits >= 1:
                        break
                    continue
                logger.info(
                    "WeChatAdapter: chat selected conversation=%s keyword=%s matched=%s",
                    conversation_id,
                    keyword,
                    matched_name,
                )
                return True
            if self._select_top_chat_entry_for_target(main_wnd, conversation_id):
                return True
            logger.warning(
                "WeChatAdapter: chat open aborted, no matched search result conversation=%s candidates=%s",
                conversation_id,
                candidates,
            )
            return False
        except Exception as exc:
            logger.warning("WeChatAdapter: 打开聊天窗口失败 %s: %s", conversation_id, exc)
            return False

    def _resolve_search_box(self, main_wnd: Any) -> Any | None:
        probes: list[dict[str, Any]] = [
            {"auto_id": "Search", "control_type": "Edit"},
            {"title_re": ".*(搜索|Search).*", "control_type": "Edit"},
            {"name_re": ".*(搜索|Search).*", "control_type": "Edit"},
        ]
        for probe in probes:
            try:
                ctrl = main_wnd.child_window(**probe)
                if ctrl.exists(timeout=0.4):
                    return ctrl
            except Exception:
                continue

        try:
            edits = list(main_wnd.descendants(control_type="Edit"))
        except Exception:
            edits = []
        picked = self._pick_search_control(main_wnd, edits)
        if picked is not None:
            return picked
        for edit in edits[:12]:
            name = self._extract_control_text(edit)
            if re.search(r"(搜索|search)", name, re.IGNORECASE):
                return edit
        return None

    def _pick_search_control(self, main_wnd: Any, edits: list[Any]) -> Any | None:
        try:
            wnd_rect = main_wnd.rectangle()
            wnd_left = int(getattr(wnd_rect, "left", 0))
            wnd_top = int(getattr(wnd_rect, "top", 0))
            wnd_width = int(getattr(wnd_rect, "right", 0)) - wnd_left
            wnd_height = int(getattr(wnd_rect, "bottom", 0)) - wnd_top
        except Exception:
            wnd_left = 0
            wnd_top = 0
            wnd_width = 0
            wnd_height = 0

        best_ctrl = None
        best_score = -999
        for ctrl in edits[:40]:
            try:
                rect = ctrl.rectangle()
                left = int(getattr(rect, "left", 0))
                top = int(getattr(rect, "top", 0))
                right = int(getattr(rect, "right", 0))
                bottom = int(getattr(rect, "bottom", 0))
            except Exception:
                continue
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width < 80 or height < 18:
                continue

            score = 0
            if wnd_width > 0 and left <= wnd_left + int(wnd_width * 0.50):
                score += 2
            if wnd_height > 0 and top <= wnd_top + int(wnd_height * 0.30):
                score += 2
            if wnd_width > 0 and width <= int(wnd_width * 0.50):
                score += 1

            text = self._normalize_match_text(self._extract_control_text(ctrl))
            if text:
                if "search" in text or "\u641c\u7d22" in text:
                    score += 2
                if "clear" in text or "\u6e05\u7a7a" in text:
                    score -= 4
                if "input" in text or "message" in text or "chat" in text:
                    score -= 2
            if score > best_score:
                best_score = score
                best_ctrl = ctrl

        if best_ctrl is None or best_score < 3:
            return None
        return best_ctrl

    def _switch_to_chat_tab(self, main_wnd: Any) -> bool:
        names = ("聊天", "会话", "Chats", "Chat")
        controls = ("TabItem", "Button", "ListItem")
        for control_type in controls:
            for name in names:
                try:
                    ctrl = main_wnd.child_window(title=name, control_type=control_type)
                    if ctrl.exists(timeout=0.2):
                        ctrl.click_input()
                        time.sleep(0.15)
                        return True
                except Exception:
                    continue
        try:
            from pywinauto.keyboard import send_keys
        except Exception:
            send_keys = None
        try:
            if send_keys is not None:
                send_keys("^1")
                time.sleep(0.15)
                return True
        except Exception:
            pass
        try:
            main_wnd.set_focus()
        except Exception:
            pass
        try:
            main_wnd.type_keys("^1")
            time.sleep(0.15)
            return True
        except Exception:
            return False

    def _select_chat_from_chat_list(
        self,
        main_wnd: Any,
        candidates: list[str],
        conversation_id: str,
    ) -> bool:
        normalized_candidates = []
        for item in candidates:
            text = self._normalize_match_text(item)
            if text:
                normalized_candidates.append(text)
        if not normalized_candidates:
            return False

        try:
            wnd_rect = main_wnd.rectangle()
            left_bound = int(getattr(wnd_rect, "left", 0))
            right_bound = int(getattr(wnd_rect, "right", 0))
            left_panel_max_x = left_bound + int((right_bound - left_bound) * 0.46)
        except Exception:
            left_panel_max_x = 999999

        best_item = None
        best_name = ""
        best_score = -1
        scan_targets: list[Any] = []
        for control_type in ("ListItem", "Button"):
            try:
                scan_targets.extend(list(main_wnd.descendants(control_type=control_type)))
            except Exception:
                continue
        for item in scan_targets[:240]:
            try:
                rect = item.rectangle()
                if int(getattr(rect, "left", 0)) > left_panel_max_x:
                    continue
            except Exception:
                pass
            name = self._extract_control_text(item)
            normalized_name = self._normalize_match_text(name)
            if not normalized_name:
                continue
            if self._is_noise_click_target(normalized_name):
                continue
            score = -1
            for candidate in normalized_candidates:
                if normalized_name == candidate:
                    score = max(score, 3)
                elif len(candidate) >= 2 and (
                    candidate in normalized_name or normalized_name in candidate
                ):
                    score = max(score, 2)
            if score > best_score:
                best_score = score
                best_item = item
                best_name = name
                if best_score >= 3:
                    break

        if best_item is None or best_score < 2:
            logger.warning(
                "WeChatAdapter: chat-list fallback not matched conversation=%s candidates=%s",
                conversation_id,
                candidates,
            )
            return False
        try:
            best_item.click_input()
            time.sleep(0.25)
            self._dismiss_search_overlay(main_wnd)
            if not self._is_chat_context_ready(main_wnd):
                logger.warning(
                    "WeChatAdapter: chat-list fallback clicked but chat context not ready conversation=%s matched=%s",
                    conversation_id,
                    best_name,
                )
                return False
            logger.info(
                "WeChatAdapter: chat selected via list fallback conversation=%s matched=%s score=%d",
                conversation_id,
                best_name,
                best_score,
            )
            return True
        except Exception as exc:
            logger.warning(
                "WeChatAdapter: chat-list fallback click failed conversation=%s matched=%s err=%s",
                conversation_id,
                best_name,
                exc,
            )
            return False

    def _select_top_chat_entry_for_target(self, main_wnd: Any, conversation_id: str) -> bool:
        try:
            is_top_session = bool(self._svc.is_top_session(conversation_id))
        except Exception as exc:
            logger.debug(
                "WeChatAdapter: is_top_session query failed conversation=%s err=%s",
                conversation_id,
                exc,
            )
            return False
        if not is_top_session:
            return False
        if not self._select_top_chat_entry(main_wnd):
            return False
        logger.info(
            "WeChatAdapter: selected top chat entry for target conversation=%s",
            conversation_id,
        )
        return True

    def _select_top_chat_entry(self, main_wnd: Any) -> bool:
        try:
            wnd_rect = main_wnd.rectangle()
            left_bound = int(getattr(wnd_rect, "left", 0))
            right_bound = int(getattr(wnd_rect, "right", 0))
            left_panel_max_x = left_bound + int((right_bound - left_bound) * 0.40)
        except Exception:
            left_panel_max_x = 999999

        controls: list[Any] = []
        for control_type in ("ListItem", "Button"):
            try:
                controls.extend(list(main_wnd.descendants(control_type=control_type)))
            except Exception:
                continue

        top_item = None
        top_anchor: tuple[int, int] | None = None
        for item in controls[:240]:
            try:
                rect = item.rectangle()
                left = int(getattr(rect, "left", 0))
                top = int(getattr(rect, "top", 0))
                right = int(getattr(rect, "right", 0))
                bottom = int(getattr(rect, "bottom", 0))
            except Exception:
                continue
            if left > left_panel_max_x:
                continue
            if max(0, right - left) < 40 or max(0, bottom - top) < 16:
                continue
            text = self._normalize_match_text(self._extract_control_text(item))
            if self._is_noise_click_target(text):
                continue
            anchor = (top, left)
            if top_item is None or anchor < (top_anchor or anchor):
                top_item = item
                top_anchor = anchor

        if top_item is None:
            return False
        try:
            top_item.click_input()
            time.sleep(0.25)
        except Exception:
            return False
        self._dismiss_search_overlay(main_wnd)
        return self._is_chat_context_ready(main_wnd)

    def _build_search_candidates(self, conversation_id: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()

        def _add(raw: str | None) -> None:
            text = str(raw or "").strip()
            if not text:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            values.append(text)

        _add(conversation_id)
        try:
            contact = self._svc.get_contact(conversation_id)
        except Exception:
            contact = None
        if contact is None and not self._is_safe_conversation_ref(conversation_id):
            logger.warning(
                "WeChatAdapter: unsafe conversation_id without contact mapping, skip send conversation=%s",
                conversation_id,
            )
            return []
        if contact is not None:
            _add(contact.remark)
            _add(contact.nickname)
            _add(contact.alias)
            _add(contact.wxid)
        return values

    @staticmethod
    def _is_safe_conversation_ref(conversation_id: str) -> bool:
        value = str(conversation_id or "").strip()
        if not value:
            return False
        if WeChatAdapter._is_placeholder_conversation_id(value):
            return False
        if value in {
            "filehelper",
            "weixin",
            "newsapp",
            "notification_messages",
            "opencustomerservicemsg",
            "@opencustomerservicemsg",
        }:
            return True
        if value.startswith(("wxid_", "gh_")):
            return True
        if value.endswith(("@chatroom", "@openim", "@kefu.openim")):
            return True
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,63}", value))

    @staticmethod
    def _is_placeholder_conversation_id(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return True
        return normalized in _PLACEHOLDER_CONVERSATION_IDS

    @staticmethod
    def _clear_and_fill_search(search_box: Any, keyword: str) -> None:
        search_box.set_focus()
        search_box.type_keys("^a{BACKSPACE}")
        if keyword:
            WeChatAdapter._set_clipboard_text(keyword)
            search_box.type_keys("^v")

    def _find_matched_result_item(
        self,
        main_wnd: Any,
        candidates: list[str],
    ) -> tuple[Any, str] | None:
        normalized_candidates = []
        for item in candidates:
            normalized = self._normalize_match_text(item)
            if normalized:
                normalized_candidates.append(normalized)
        if not normalized_candidates:
            return None

        result_items: list[Any] = []
        for control_type in ("ListItem", "Button"):
            try:
                result_items.extend(list(main_wnd.descendants(control_type=control_type)))
            except Exception:
                continue
        try:
            wnd_rect = main_wnd.rectangle()
            left_bound = int(getattr(wnd_rect, "left", 0))
            right_bound = int(getattr(wnd_rect, "right", 0))
            left_panel_max_x = left_bound + int((right_bound - left_bound) * 0.40)
        except Exception:
            left_panel_max_x = 999999
        for item in result_items[:240]:
            try:
                rect = item.rectangle()
                if int(getattr(rect, "left", 0)) > left_panel_max_x:
                    continue
            except Exception:
                pass
            name = self._extract_control_text(item)
            normalized_name = self._normalize_match_text(name)
            if not normalized_name:
                continue
            if self._is_noise_click_target(normalized_name):
                continue
            for candidate in normalized_candidates:
                if len(candidate) < 2:
                    if normalized_name == candidate:
                        return item, name
                    continue
                if (
                    normalized_name == candidate
                    or candidate in normalized_name
                    or normalized_name in candidate
                ):
                    return item, name
        return None

    def _extract_control_text(self, item: Any) -> str:
        name = self._safe_item_name(item)
        if name:
            return name

        parts: list[str] = []
        try:
            texts = list(item.descendants(control_type="Text"))
        except Exception:
            texts = []
        for ctrl in texts[:12]:
            text = self._safe_item_name(ctrl)
            if not text:
                continue
            if text in parts:
                continue
            parts.append(text)
        return " ".join(parts)

    @staticmethod
    def _safe_item_name(item: Any) -> str:
        try:
            name = str(item.window_text() or "").strip()
            if name:
                return name
        except Exception:
            pass
        try:
            name = str(getattr(getattr(item, "element_info", None), "name", "") or "").strip()
            if name:
                return name
        except Exception:
            pass
        return ""

    @staticmethod
    def _normalize_match_text(text: str) -> str:
        return str(text or "").strip().replace(" ", "").replace("\u3000", "").casefold()

    @staticmethod
    def _is_blocked_wechat_window_class(class_norm: str) -> bool:
        normalized = str(class_norm or "")
        if not normalized:
            return False
        blocked_tokens = (
            "tooltips_class32",
            "windows.ui.core.corewindow",
            "windows.uicore.corewindow",
            "desktopwindowxamlsource",
            "applicationframewindow",
            "wxtrayiconmessagewindowclass",
            "trayiconmessagewindow",
            "qwindowtoolsavebits",
        )
        return any(token in normalized for token in blocked_tokens)

    @staticmethod
    def _wechat_window_class_bias(class_norm: str) -> int:
        normalized = str(class_norm or "")
        bias = 0
        if "trayiconmessagewindow" in normalized or "wxtrayicon" in normalized:
            bias -= 1_500_000
        if "chrome_widgetwin" in normalized:
            bias += 150_000
        if "qwindowicon" in normalized:
            bias -= 500_000
        if "qt51514qwindowicon" in normalized:
            bias -= 800_000
        return bias

    def _is_noise_click_target(self, normalized_text: str) -> bool:
        text = str(normalized_text or "")
        if not text:
            return True
        blocked_tokens = [
            "\u641c\u7d22",
            "\u76f8\u5173\u641c\u7d22",
            "\u5386\u53f2\u8bb0\u5f55",
            "\u5220\u9664",
            "\u6e05\u7a7a",
            "\u91cd\u65b0\u52a0\u8f7d",
            "\u540e\u9000",
            "\u524d\u8fdb",
            "\u6700\u5c0f\u5316",
            "\u6700\u5927\u5316",
            "\u5173\u95ed",
            "search",
            "history",
            "reload",
            "delete",
            "clear",
            "back",
            "forward",
            "close",
            "minimize",
            "maximize",
        ]
        for token in blocked_tokens:
            if token in text:
                return True
        return False

    def _is_chat_context_ready(self, main_wnd: Any) -> bool:
        if self._is_search_overlay_active(main_wnd):
            return False
        return self._resolve_input_box(main_wnd) is not None

    def _dismiss_search_overlay(self, main_wnd: Any) -> bool:
        if not self._is_search_overlay_active(main_wnd):
            return False
        for _ in range(2):
            try:
                main_wnd.set_focus()
            except Exception:
                pass
            try:
                main_wnd.type_keys("{ESC}")
            except Exception:
                pass
            time.sleep(0.12)
            if not self._is_search_overlay_active(main_wnd):
                return True
        return False

    def _is_search_overlay_active(self, main_wnd: Any) -> bool:
        markers = [
            "\u76f8\u5173\u641c\u7d22",
            "\u641c\u7d22\u7ed3\u679c",
            "\u5386\u53f2\u8bb0\u5f55",
            "Search Results",
            "Related Searches",
        ]
        normalized_markers = [self._normalize_match_text(item) for item in markers]
        try:
            texts = list(main_wnd.descendants(control_type="Text"))
        except Exception:
            texts = []
        for item in texts[:120]:
            text = self._safe_item_name(item)
            if not text:
                continue
            normalized = self._normalize_match_text(text)
            for marker in normalized_markers:
                if marker and marker in normalized:
                    return True
        return False

    def _recover_to_chat_home(self, main_wnd: Any) -> bool:
        try:
            from pywinauto.keyboard import send_keys
        except Exception:
            send_keys = None
        for _ in range(2):
            self._dismiss_search_overlay(main_wnd)
            self._switch_to_chat_tab(main_wnd)
            if self._is_chat_context_ready(main_wnd):
                return True
            if send_keys is not None:
                try:
                    send_keys("^1")
                except Exception:
                    pass
                time.sleep(0.12)
                try:
                    send_keys("{ESC}")
                except Exception:
                    pass
                time.sleep(0.12)
                if self._is_chat_context_ready(main_wnd):
                    return True
            if self._click_button_by_token(main_wnd, tokens=["\u8fd4\u56de\u804a\u5929", "\u8fd4\u56de", "\u540e\u9000", "back"]):
                time.sleep(0.12)
                continue
            try:
                main_wnd.set_focus()
            except Exception:
                pass
            try:
                main_wnd.type_keys("%{LEFT}")
            except Exception:
                pass
            time.sleep(0.12)
        return self._is_chat_context_ready(main_wnd)

    def _try_enter_chat_from_detail_view(self, main_wnd: Any) -> bool:
        if self._is_chat_context_ready(main_wnd):
            return True
        self._dismiss_search_overlay(main_wnd)
        if self._click_button_by_token(main_wnd, tokens=["\u53d1\u6d88\u606f", "\u7ee7\u7eed\u804a\u5929", "sendmessage"]):
            time.sleep(0.18)
            self._dismiss_search_overlay(main_wnd)
            if self._is_chat_context_ready(main_wnd):
                logger.info("WeChatAdapter: recovered chat context via detail-view message button")
                return True
        if self._click_button_by_token(main_wnd, tokens=["\u8fd4\u56de\u804a\u5929", "\u8fd4\u56de", "\u540e\u9000", "back"]):
            time.sleep(0.12)
            self._dismiss_search_overlay(main_wnd)
            if self._is_chat_context_ready(main_wnd):
                logger.info("WeChatAdapter: recovered chat context via detail-view back button")
                return True
        return self._is_chat_context_ready(main_wnd)

    def _is_embedded_browser_view(self, main_wnd: Any) -> bool:
        markers = [
            "\u540e\u9000",
            "\u524d\u8fdb",
            "\u91cd\u65b0\u52a0\u8f7d",
            "back",
            "forward",
            "reload",
        ]
        normalized_markers = [self._normalize_match_text(item) for item in markers]
        try:
            buttons = list(main_wnd.descendants(control_type="Button"))
        except Exception:
            buttons = []
        for button in buttons[:50]:
            text = self._normalize_match_text(self._extract_control_text(button))
            if not text:
                continue
            for marker in normalized_markers:
                if marker and marker in text:
                    return True
        return False

    def _click_button_by_token(self, main_wnd: Any, *, tokens: list[str]) -> bool:
        normalized_tokens = [self._normalize_match_text(item) for item in tokens]
        try:
            buttons = list(main_wnd.descendants(control_type="Button"))
        except Exception:
            buttons = []
        for button in buttons[:50]:
            text = self._normalize_match_text(self._extract_control_text(button))
            if not text:
                continue
            for token in normalized_tokens:
                if token and token in text:
                    try:
                        button.click_input()
                        return True
                    except Exception:
                        continue
        return False

    def _resolve_input_box(self, main_wnd: Any) -> Any | None:
        probes: list[dict[str, Any]] = [
            {"auto_id": "ChatInput", "control_type": "Edit"},
            {"title_re": ".*(输入|消息|Message|message|Input|input).*", "control_type": "Edit"},
            {"name_re": ".*(输入|消息|Message|message|Input|input).*", "control_type": "Edit"},
        ]
        for probe in probes:
            try:
                ctrl = main_wnd.child_window(**probe)
                if ctrl.exists(timeout=0.25):
                    return ctrl
            except Exception:
                continue

        for control_type in ("Edit", "Document"):
            try:
                controls = list(main_wnd.descendants(control_type=control_type))
            except Exception:
                controls = []
            picked = self._pick_input_control(main_wnd, controls)
            if picked is not None:
                return picked
        return None

    def _pick_input_control(self, main_wnd: Any, controls: list[Any]) -> Any | None:
        try:
            wnd_rect = main_wnd.rectangle()
            wnd_left = int(getattr(wnd_rect, "left", 0))
            wnd_top = int(getattr(wnd_rect, "top", 0))
            wnd_width = int(getattr(wnd_rect, "right", 0)) - wnd_left
            wnd_height = int(getattr(wnd_rect, "bottom", 0)) - wnd_top
        except Exception:
            wnd_left = 0
            wnd_top = 0
            wnd_width = 0
            wnd_height = 0

        best_ctrl = None
        best_score = -999
        for ctrl in controls[:160]:
            try:
                rect = ctrl.rectangle()
                left = int(getattr(rect, "left", 0))
                top = int(getattr(rect, "top", 0))
                right = int(getattr(rect, "right", 0))
                bottom = int(getattr(rect, "bottom", 0))
            except Exception:
                continue
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width < 120 or height < 16:
                continue

            score = 0
            if wnd_width > 0 and left >= wnd_left + int(wnd_width * 0.18):
                score += 1
            if wnd_height > 0 and top >= wnd_top + int(wnd_height * 0.40):
                score += 2
            if width >= 260:
                score += 1

            name = self._normalize_match_text(self._safe_item_name(ctrl))
            if name:
                if "search" in name:
                    score -= 4
                if any(token in name for token in ("input", "message", "chat", "send")):
                    score += 1

            if score > best_score:
                best_score = score
                best_ctrl = ctrl

        if best_ctrl is None or best_score < 2:
            return None
        return best_ctrl

    def _paste_and_send(self, input_box: Any, main_wnd: Any) -> bool:
        targets = [("input", input_box), ("window", main_wnd)]
        last_exc: Exception | None = None
        for label, target in targets:
            try:
                try:
                    target.set_focus()
                except Exception:
                    pass
                target.type_keys("^v")
                time.sleep(0.05)
                target.type_keys("{ENTER}")
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning("WeChatAdapter: paste/send failed target=%s err=%s", label, exc)
                continue
        try:
            from pywinauto.keyboard import send_keys
            send_keys("^v")
            time.sleep(0.08)
            send_keys("{ENTER}")
            return True
        except Exception as exc:
            last_exc = exc
        if last_exc is not None:
            logger.error("WeChatAdapter: paste/send failed all targets err=%s", last_exc)
        return False

    def _send_via_keyboard_flow(self, main_wnd: Any, reply: UnifiedReply) -> bool:
        if reply.msg_type != MsgType.TEXT:
            return False
        candidates = self._build_search_candidates(reply.conversation_id)
        if not candidates:
            return False
        try:
            from pywinauto.keyboard import send_keys
        except Exception:
            send_keys = None
        for keyword in candidates[:3]:
            for search_shortcut in ("^n", "^f"):
                try:
                    try:
                        main_wnd.set_focus()
                    except Exception:
                        pass
                    logger.info(
                        "WeChatAdapter: keyboard flow try conversation=%s keyword=%s shortcut=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                    )
                    if send_keys is not None:
                        send_keys("{ESC}")
                    else:
                        main_wnd.type_keys("{ESC}")
                    time.sleep(0.08)
                    if send_keys is not None:
                        send_keys(search_shortcut)
                    else:
                        main_wnd.type_keys(search_shortcut)
                    time.sleep(0.22)
                    if send_keys is not None:
                        send_keys("^a{BACKSPACE}")
                    else:
                        main_wnd.type_keys("^a{BACKSPACE}")
                    self._set_clipboard_text(keyword)
                    if send_keys is not None:
                        send_keys("^v")
                    else:
                        main_wnd.type_keys("^v")
                    time.sleep(0.28)
                    if send_keys is not None:
                        send_keys("{DOWN}")
                        time.sleep(0.08)
                        send_keys("{ENTER}")
                        time.sleep(0.08)
                        send_keys("{ESC}")
                    else:
                        main_wnd.type_keys("{ENTER}")
                        time.sleep(0.08)
                        main_wnd.type_keys("{ESC}")
                    time.sleep(0.35)
                    attempt_ts_ms = int(time.time() * 1000)
                    self._set_clipboard_text(reply.content)
                    if not self._paste_and_send(main_wnd, main_wnd):
                        continue
                    if self._verify_send_outcome(
                        conversation_id=reply.conversation_id,
                        expected_text=reply.content,
                        since_ts_ms=attempt_ts_ms,
                        timeout_s=2.5,
                    ):
                        return True
                    logger.warning(
                        "WeChatAdapter: keyboard flow verify failed conversation=%s keyword=%s shortcut=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                    )
                except Exception as exc:
                    logger.warning(
                        "WeChatAdapter: keyboard flow failed conversation=%s keyword=%s shortcut=%s err=%s",
                        reply.conversation_id,
                        keyword,
                        search_shortcut,
                        exc,
                    )
                    continue
        return False

    def _verify_send_outcome(
        self,
        *,
        conversation_id: str,
        expected_text: str,
        since_ts_ms: int,
        timeout_s: float = 8.0,
    ) -> bool:
        expected = str(expected_text or "").strip()
        if not expected:
            return True
        deadline = time.time() + max(0.5, float(timeout_s))
        while time.time() < deadline:
            try:
                recent = self._svc.get_messages(
                    conversation_id=conversation_id,
                    limit=20,
                    after_ts_ms=max(0, int(since_ts_ms) - 3000),
                )
            except Exception as exc:
                logger.debug(
                    "WeChatAdapter: verify send read failed conversation=%s err=%s",
                    conversation_id,
                    exc,
                )
                time.sleep(0.2)
                continue

            for msg in reversed(recent):
                content = str(msg.content or "").strip()
                if not content:
                    continue
                if msg.ts_ms and msg.ts_ms < int(since_ts_ms) - 3000:
                    continue
                if msg.is_sender and (content == expected or expected in content):
                    return True
                if content == expected and msg.ts_ms >= int(since_ts_ms) - 1000:
                    return True
            time.sleep(0.2)

        logger.warning(
            "WeChatAdapter: send verify failed conversation=%s expected=%s since_ts_ms=%s",
            conversation_id,
            expected[:80],
            since_ts_ms,
        )
        return False

    def _log_window_snapshot(self, main_wnd: Any, *, reason: str, conversation_id: str) -> None:
        try:
            title = self._safe_item_name(main_wnd)
            class_name = str(getattr(getattr(main_wnd, "element_info", None), "class_name", "") or "")
            try:
                rect = main_wnd.rectangle()
                rect_text = (
                    int(getattr(rect, "left", 0)),
                    int(getattr(rect, "top", 0)),
                    int(getattr(rect, "right", 0)),
                    int(getattr(rect, "bottom", 0)),
                )
            except Exception:
                rect_text = ()
            counts: dict[str, int] = {}
            for control_type in ("Edit", "Document", "List", "ListItem", "Button", "Pane", "Text"):
                try:
                    counts[control_type] = len(list(main_wnd.descendants(control_type=control_type)))
                except Exception:
                    counts[control_type] = -1

            sample: list[str] = []
            try:
                descendants = list(main_wnd.descendants())
            except Exception:
                descendants = []
            for item in descendants[:28]:
                name = self._safe_item_name(item)
                control_type = str(
                    getattr(getattr(item, "element_info", None), "control_type", "") or ""
                ).strip()
                if not name and not control_type:
                    continue
                piece = f"{control_type}:{name}"[:96]
                sample.append(piece)

            logger.warning(
                "WeChatAdapter: window snapshot reason=%s conversation=%s title=%s class=%s rect=%s counts=%s sample=%s",
                reason,
                conversation_id,
                title,
                class_name,
                rect_text,
                counts,
                sample,
            )
        except Exception as exc:
            logger.warning("WeChatAdapter: window snapshot failed reason=%s err=%s", reason, exc)

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        """将文本写入剪贴板。"""
        last_exc: Exception | None = None
        try:
            import win32clipboard
            for _ in range(12):
                opened = False
                try:
                    win32clipboard.OpenClipboard()
                    opened = True
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(str(text or ""), win32clipboard.CF_UNICODETEXT)
                    return
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.05)
                finally:
                    if opened:
                        try:
                            win32clipboard.CloseClipboard()
                        except Exception:
                            pass
        except ImportError:
            pass

        # 降级：使用 tkinter
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(str(text or ""))
            root.update()
            root.destroy()
            return
        except Exception as tk_exc:
            if last_exc is not None:
                raise RuntimeError(f"clipboard unavailable: {last_exc}; tkinter fallback failed: {tk_exc}") from tk_exc
            raise

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
    # 撤回消息（2分钟内有效）
    # ------------------------------------------------------------------

    def recall_message(self, msg_content: str) -> bool:
        """撤回指定内容的消息（仅限发出后 2 分钟内）。

        通过 pywinauto 右键菜单实现，不依赖任何 Hook API。

        Args:
            msg_content: 要撤回的消息文本内容（用于定位消息气泡）

        Returns:
            True 表示撤回成功
        """
        try:
            import pywinauto
            from pywinauto import Application

            app = Application(backend="uia").connect(title_re="微信")
            chat_wnd = app.top_window()

            # 找到消息气泡控件，右键点击
            msg_item = chat_wnd.child_window(name=msg_content, control_type="Text")
            msg_item.right_click_input()

            # 等待右键菜单出现，点击「撤回」
            import time as _time
            _time.sleep(0.3)
            recall_item = app.window(control_type="MenuItem", title="撤回")
            recall_item.wait("visible", timeout=2)
            recall_item.click_input()
            logger.info("WeChatAdapter: 消息已撤回")
            return True
        except Exception as exc:
            logger.warning("WeChatAdapter: 撤回消息失败: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @staticmethod
    def _allow_keyboard_fallback() -> bool:
        raw = str(os.environ.get("EDGE_WECHAT_SEND_KEYBOARD_FALLBACK", "") or "").strip().lower()
        if not raw:
            return False
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _allow_keyboard_search_fallback() -> bool:
        if not WeChatAdapter._allow_keyboard_fallback():
            return False
        raw = str(os.environ.get("EDGE_WECHAT_SEND_KEYBOARD_SEARCH_FALLBACK", "") or "").strip().lower()
        if not raw:
            return False
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _allow_active_chat_fallback() -> bool:
        if not WeChatAdapter._allow_keyboard_fallback():
            return False
        raw = str(os.environ.get("EDGE_WECHAT_SEND_FALLBACK_ACTIVE_CHAT", "") or "").strip().lower()
        if not raw:
            return False
        return raw in {"1", "true", "yes", "on"}

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态。"""
        db_stats = self._svc.stats()
        return {
            "platform": self.platform_id.value,
            "status": "ok" if db_stats["contact_db_exists"] else "degraded",
            "self_wxid": self._self_wxid,
            "db_stats": db_stats,
            "stats": dict(self._stats),
            "last_outbound": dict(self._last_outbound),
        }
