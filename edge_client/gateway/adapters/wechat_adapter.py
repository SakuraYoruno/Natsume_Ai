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
import os
import re
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

    def _send_via_pywinauto(self, reply: UnifiedReply) -> bool:
        """通过 pywinauto 精确定位输入框并发送消息。"""
        try:
            from pywinauto import Application
        except ImportError:
            logger.error("WeChatAdapter: pywinauto 未安装，无法发送消息")
            return False

        try:
            main_wnd = self._resolve_desktop_wechat_window()
            app = None
            if main_wnd is None:
                app = Application(backend="uia")
                try:
                    app.connect(title_re=".*(Weixin|WeChat|WX|微信).*", timeout=1.5)
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
                    main_wnd = None
            if main_wnd is None:
                logger.error("WeChatAdapter: main window not found")
                return False
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

            # 确保聊天窗口已打开（通过搜索框定位联系人）
            if not self._ensure_chat_open(main_wnd, reply.conversation_id):
                logger.warning(
                    "WeChatAdapter: _ensure_chat_open failed conversation=%s",
                    reply.conversation_id,
                )
                if self._allow_keyboard_fallback() and self._send_via_keyboard_flow(main_wnd, reply):
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
                    if self._allow_keyboard_fallback() and self._send_via_keyboard_flow(main_wnd, reply):
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

    def _resolve_main_window(self, app: Any) -> Any | None:
        windows: list[Any] = []
        try:
            windows = list(app.windows())
        except Exception:
            windows = []

        best_wnd = None
        best_score = -1
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
            score = area
            if title and any(token in title for token in ("微信", "weixin", "wechat")):
                score += 2_000_000_000
            if score > best_score:
                best_score = score
                best_wnd = wnd

        if best_wnd is not None:
            return best_wnd

        try:
            fallback = app.window(title_re=".*(Weixin|WeChat|WX).*")
            if fallback.exists(timeout=0.3):
                return fallback
        except Exception:
            pass

        try:
            return app.top_window()
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
        for wnd in windows:
            title = self._normalize_match_text(self._safe_item_name(wnd))
            class_name = str(getattr(getattr(wnd, "element_info", None), "class_name", "") or "")
            class_norm = self._normalize_match_text(class_name)
            if (
                not any(token in title for token in ("微信", "weixin", "wechat"))
                and "qt" not in class_norm
                and "weixin" not in class_norm
            ):
                continue
            try:
                rect = wnd.rectangle()
                width = max(0, int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0)))
                height = max(0, int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0)))
            except Exception:
                width = 0
                height = 0
            score = (width * height)
            if title and any(token in title for token in ("微信", "weixin", "wechat")):
                score += 2_000_000_000
            if score > best_score:
                best_score = score
                best_wnd = wnd
        return best_wnd

    @staticmethod
    def _window_richness(wnd: Any) -> int:
        try:
            return min(200, len(list(wnd.children())))
        except Exception:
            return 0

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

            search_box = self._resolve_search_box(main_wnd)
            if search_box is None:
                logger.warning(
                    "WeChatAdapter: search box not found after list-scan fallback, conversation=%s",
                    conversation_id,
                )
                return False
            for keyword in candidates:
                self._clear_and_fill_search(search_box, keyword)
                try:
                    search_box.type_keys("{ENTER}")
                except Exception:
                    pass
                time.sleep(0.45)
                matched = self._find_matched_result_item(main_wnd, candidates)
                if matched is None:
                    continue
                result_item, matched_name = matched
                result_item.click_input()
                time.sleep(0.25)
                self._dismiss_search_overlay(main_wnd)
                if not self._is_chat_context_ready(main_wnd):
                    logger.warning(
                        "WeChatAdapter: search matched but chat context not ready conversation=%s keyword=%s",
                        conversation_id,
                        keyword,
                    )
                    continue
                logger.info(
                    "WeChatAdapter: chat selected conversation=%s keyword=%s matched=%s",
                    conversation_id,
                    keyword,
                    matched_name,
                )
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
        for _ in range(3):
            self._dismiss_search_overlay(main_wnd)
            self._switch_to_chat_tab(main_wnd)
            if not self._is_embedded_browser_view(main_wnd):
                return True
            if self._click_button_by_token(main_wnd, tokens=["\u540e\u9000", "back"]):
                time.sleep(0.22)
                continue
            try:
                main_wnd.set_focus()
            except Exception:
                pass
            try:
                main_wnd.type_keys("%{LEFT}")
            except Exception:
                pass
            time.sleep(0.22)
        return not self._is_embedded_browser_view(main_wnd)

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
            try:
                try:
                    main_wnd.set_focus()
                except Exception:
                    pass
                logger.info(
                    "WeChatAdapter: keyboard flow try conversation=%s keyword=%s",
                    reply.conversation_id,
                    keyword,
                )
                if send_keys is not None:
                    send_keys("^f")
                else:
                    main_wnd.type_keys("^f")
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
                    send_keys("{ENTER}")
                else:
                    main_wnd.type_keys("{ENTER}")
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
                    "WeChatAdapter: keyboard flow verify failed conversation=%s keyword=%s",
                    reply.conversation_id,
                    keyword,
                )
            except Exception as exc:
                logger.warning(
                    "WeChatAdapter: keyboard flow failed conversation=%s keyword=%s err=%s",
                    reply.conversation_id,
                    keyword,
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
