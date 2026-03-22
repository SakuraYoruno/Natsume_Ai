"""
WeChat desktop UI automation bridge.

This bridge consumes outbound command queue records and executes them on the
interactive WeChat/Weixin desktop client.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_EDGE_URL = "http://127.0.0.1:17800"
CALLBACK_PATH = "/v1/edge/wechat/callback"
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
DEFAULT_CALLBACK_TIMEOUT_S = 6.0
DEFAULT_LISTENER_POLL_INTERVAL_S = 1.2
DEFAULT_LISTENER_DEDUP_WINDOW_S = 12.0
DEFAULT_INBOUND_BACKEND = "uia_hook"
DEFAULT_WCF_LOGIN_WAIT_S = 12.0
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WECHAT_MAIN_WINDOW_CLASS_HINTS = {
    "wechatmainwndforpc",
    "weixinmainwndforpc",
    "weixinmainwnd",
}
WECHAT_WINDOW_TITLE_HINTS = ("微信", "Weixin", "WeChat")

EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_NAMECHANGE = 0x800C
EVENT_OBJECT_VALUECHANGE = 0x800E
WINEVENT_OUTOFCONTEXT = 0x0000
PM_REMOVE = 0x0001
WM_QUIT = 0x0012


logger = logging.getLogger("Natsume.wechat_uia_bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _normalize_optional_path(path_value: str) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    return os.path.abspath(text)


def _ensure_parent_dir(path_value: str) -> None:
    parent = os.path.dirname(path_value)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _read_jsonl_rows_from_offset(path_value: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path_value:
        return [], max(0, int(offset))

    _ensure_parent_dir(path_value)
    if not os.path.exists(path_value):
        with open(path_value, "a", encoding="utf-8"):
            pass

    current_offset = max(0, int(offset))
    try:
        file_size = os.path.getsize(path_value)
        if current_offset > file_size:
            current_offset = 0
    except Exception:
        current_offset = 0

    rows: list[dict[str, Any]] = []
    try:
        with open(path_value, "r", encoding="utf-8") as handle:
            handle.seek(current_offset, os.SEEK_SET)
            while True:
                raw_line = handle.readline()
                if not raw_line:
                    break
                current_offset = handle.tell()
                line = str(raw_line).strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    logger.warning("invalid command json line dropped: %.120s", line)
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception as exc:
        logger.warning("failed to read command queue %s: %s", path_value, exc)
    return rows, current_offset


def _append_jsonl_row(path_value: str, row: dict[str, Any]) -> None:
    if not path_value:
        return
    _ensure_parent_dir(path_value)
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    with open(path_value, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.write("\n")


def _parse_bool(value: Any, *, default: bool) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _normalize_message_text(text: str) -> str:
    value = str(text or "").replace("\u200b", "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _is_probably_time_or_badge(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", value):
        return True
    if re.fullmatch(r"\d+", value):
        return True
    if re.fullmatch(r"\d+\s*条", value):
        return True
    return False


def _build_hook_msg_id(sender: str, content: str, ts_ms: int) -> str:
    payload = f"{sender}\n{content}\n{ts_ms}"
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"uiahook-{ts_ms}-{digest}"


_RECENT_OUTBOUND_LOCK = threading.Lock()
_RECENT_OUTBOUND_TEXTS: dict[str, float] = {}


def _remember_outbound_text(text: str, *, ttl_s: float = 20.0) -> None:
    key = _normalize_message_text(text)
    if not key:
        return
    now_mono = time.monotonic()
    with _RECENT_OUTBOUND_LOCK:
        stale = [k for k, t in _RECENT_OUTBOUND_TEXTS.items() if (now_mono - t) > ttl_s]
        for item in stale:
            _RECENT_OUTBOUND_TEXTS.pop(item, None)
        _RECENT_OUTBOUND_TEXTS[key] = now_mono


def _is_recent_outbound_echo(text: str, *, ttl_s: float = 20.0) -> bool:
    key = _normalize_message_text(text)
    if not key:
        return False
    now_mono = time.monotonic()
    with _RECENT_OUTBOUND_LOCK:
        stale = [k for k, t in _RECENT_OUTBOUND_TEXTS.items() if (now_mono - t) > ttl_s]
        for item in stale:
            _RECENT_OUTBOUND_TEXTS.pop(item, None)
        if key in _RECENT_OUTBOUND_TEXTS:
            return True
        # Common preview format for self-sent messages.
        for prefix in ("我: ", "我：", "You: ", "Me: "):
            if key.startswith(prefix):
                stripped = key[len(prefix) :].strip()
                if stripped and stripped in _RECENT_OUTBOUND_TEXTS:
                    return True
        return False


def _post_to_edge_callback(edge_url: str, body: dict[str, Any], *, timeout_s: float) -> bool:
    url = f"{str(edge_url or DEFAULT_EDGE_URL).rstrip('/')}{CALLBACK_PATH}"
    try:
        req = urllib.request.Request(
            url=url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout_s))) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except urllib.error.HTTPError as exc:
        logger.warning("wechat callback http error: code=%s reason=%s", exc.code, exc.reason)
    except Exception as exc:
        logger.warning("wechat callback request failed: %s", exc)
    return False


class WeChatWcferryInboundBridge:
    """Inbound bridge using wcferry message receive channel."""

    def __init__(
        self,
        *,
        edge_url: str,
        enabled: bool,
        callback_timeout_s: float,
        login_wait_s: float = DEFAULT_WCF_LOGIN_WAIT_S,
    ) -> None:
        self._edge_url = str(edge_url or DEFAULT_EDGE_URL).strip()
        self._enabled = bool(enabled)
        self._callback_timeout_s = max(0.2, float(callback_timeout_s))
        self._login_wait_s = max(0.0, float(login_wait_s))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._wcf = None
        self._self_wxid = ""
        self._stats = {
            "messages_forwarded": 0,
            "messages_deduped": 0,
            "errors": 0,
        }
        self._recent_msg_ids: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def start(self) -> None:
        if not self._enabled:
            logger.info("wcferry inbound listener disabled")
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="wechat_wcferry_inbound",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._wcf is not None and hasattr(self._wcf, "disable_receiving_msg"):
                self._wcf.disable_receiving_msg()  # type: ignore[attr-defined]
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def process_cycle(self) -> int:
        # wcferry loop is push-driven in background thread.
        return 0

    def _is_duplicate_msg(self, msg_id: str) -> bool:
        value = str(msg_id or "").strip()
        if not value:
            return False
        now_mono = time.monotonic()
        stale = [k for k, ts in self._recent_msg_ids.items() if (now_mono - ts) > 60.0]
        for item in stale:
            self._recent_msg_ids.pop(item, None)
        if value in self._recent_msg_ids:
            return True
        self._recent_msg_ids[value] = now_mono
        return False

    def _run_loop(self) -> None:
        logger.info("wcferry inbound init start")
        try:
            from wcferry import Wcf
        except Exception as exc:
            logger.warning("wcferry import failed for inbound backend: %s", exc)
            return
        try:
            # Force non-blocking dial during constructor; some Weixin builds
            # can stall forever in Pair1.dial(block=True).
            patched = False
            pair1_dial = None
            try:
                import pynng  # type: ignore

                pair1_cls = getattr(pynng, "Pair1", None)
                pair1_dial = getattr(pair1_cls, "dial", None) if pair1_cls else None
                if callable(pair1_dial):
                    def _dial_non_block(self_obj, address, block=True):  # type: ignore[no-redef]
                        return pair1_dial(self_obj, address, block=False)

                    setattr(pair1_cls, "dial", _dial_non_block)
                    patched = True
            except Exception:
                patched = False

            try:
                # block=False avoids constructor-level login wait loop.
                self._wcf = Wcf(debug=False, block=False)
            finally:
                if patched:
                    try:
                        import pynng  # type: ignore

                        pair1_cls = getattr(pynng, "Pair1", None)
                        if pair1_cls is not None and callable(pair1_dial):
                            setattr(pair1_cls, "dial", pair1_dial)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("wcferry init failed: %s", exc)
            return
        logger.info("wcferry inbound init ok")

        login_state = None
        if hasattr(self._wcf, "is_login"):
            start_mono = time.monotonic()
            while not self._stop_event.is_set():
                try:
                    login_state = bool(self._wcf.is_login())
                except Exception:
                    login_state = None
                if login_state:
                    break
                if (time.monotonic() - start_mono) >= self._login_wait_s:
                    break
                time.sleep(0.8)
        logger.info(
            "wcferry login probe: is_login=%s wait_s=%.1f",
            login_state,
            self._login_wait_s,
        )

        try:
            self._self_wxid = str(getattr(self._wcf, "self_wxid", "") or "").strip()
        except Exception:
            self._self_wxid = ""

        try:
            ok = bool(self._wcf.enable_receiving_msg(pyq=False))
        except Exception as exc:
            logger.warning("wcferry enable_receiving_msg failed: %s", exc)
            return
        if not ok:
            logger.warning("wcferry inbound enable_receiving_msg returned false")
            return

        logger.info("wcferry inbound started self_wxid=%s", self._self_wxid or "(unknown)")
        while not self._stop_event.is_set():
            try:
                msg = self._wcf.get_msg(block=True)
            except Exception as exc:
                self._stats["errors"] += 1
                logger.debug("wcferry get_msg failed: %s", exc)
                continue
            if msg is None:
                continue

            msg_type = str(getattr(msg, "type", "") or "").strip()
            if msg_type != "1":
                continue

            from_self = False
            from_self_fn = getattr(msg, "from_self", None)
            if callable(from_self_fn):
                try:
                    from_self = bool(from_self_fn())
                except Exception:
                    from_self = False
            if from_self:
                continue

            sender = str(getattr(msg, "sender", "") or "").strip()
            content = _normalize_message_text(str(getattr(msg, "content", "") or ""))
            room_id = str(getattr(msg, "roomid", "") or "").strip()
            msg_id = str(getattr(msg, "id", "") or "").strip()
            ts_raw = getattr(msg, "ts", None)

            if not sender or not content:
                continue
            if self._self_wxid and sender == self._self_wxid:
                continue
            if _is_recent_outbound_echo(content):
                continue
            if self._is_duplicate_msg(msg_id):
                self._stats["messages_deduped"] += 1
                continue

            ts_ms = int(time.time() * 1000)
            if ts_raw is not None:
                try:
                    ts_ms = int(float(ts_raw))
                    if ts_ms < 10_000_000_000:
                        ts_ms *= 1000
                except Exception:
                    ts_ms = int(time.time() * 1000)

            if not msg_id:
                msg_id = _build_hook_msg_id(sender=sender, content=content, ts_ms=ts_ms)

            body = {
                "id": msg_id,
                "msg_id": msg_id,
                "sender": sender,
                "from_wxid": sender,
                "receiver": self._self_wxid or "local_wechat",
                "to_wxid": self._self_wxid or "local_wechat",
                "content": content,
                "text": content,
                "type": 1,
                "msg_type": "1",
                "roomid": room_id,
                "ts": int(ts_ms / 1000),
                "bridge_backend": "wcferry_inbound",
            }
            ok = _post_to_edge_callback(
                edge_url=self._edge_url,
                body=body,
                timeout_s=self._callback_timeout_s,
            )
            if ok:
                self._stats["messages_forwarded"] += 1
                logger.info(
                    "wcferry inbound forwarded sender=%s room=%s msg_id=%s content=%.80s",
                    sender,
                    room_id or "(private)",
                    msg_id,
                    content,
                )


class _WinMsg(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
        ("lPrivate", ctypes.c_uint),
    ]


class WeChatUiInboundBridge:
    """Inbound bridge: WinEvent hook triggers + UIA extraction + callback POST."""

    def __init__(
        self,
        *,
        edge_url: str,
        enabled: bool,
        poll_interval_s: float,
        callback_timeout_s: float,
        dedup_window_s: float,
    ) -> None:
        self._edge_url = str(edge_url or DEFAULT_EDGE_URL).strip()
        self._enabled = bool(enabled)
        self._poll_interval_s = max(0.2, float(poll_interval_s))
        self._callback_timeout_s = max(0.2, float(callback_timeout_s))
        self._dedup_window_s = max(1.0, float(dedup_window_s))

        self._stop_event = threading.Event()
        self._dirty_event = threading.Event()
        self._hook_thread: threading.Thread | None = None
        self._hook_thread_id = 0
        self._hook_handles: list[int] = []
        self._hook_cb_fn = None

        self._next_poll_at = 0.0
        self._baseline_ready = False
        self._last_diag_log_at = 0.0
        self._last_tree_diag_at = 0.0
        self._last_preview_by_chat: dict[str, str] = {}
        self._recent_forwarded: dict[str, float] = {}
        self._stats = {
            "events": 0,
            "messages_forwarded": 0,
            "messages_deduped": 0,
            "errors": 0,
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def start(self) -> None:
        if not self._enabled:
            logger.info("inbound listener disabled")
            return
        self._hook_thread = threading.Thread(
            target=self._hook_loop,
            name="wechat_uia_win_event_hook",
            daemon=True,
        )
        self._hook_thread.start()
        # Trigger first snapshot to establish baseline quickly.
        self._dirty_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        if self._hook_thread_id > 0:
            try:
                ctypes.windll.user32.PostThreadMessageW(int(self._hook_thread_id), WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._hook_thread is not None:
            self._hook_thread.join(timeout=2.0)

    def process_cycle(self) -> int:
        if not self._enabled:
            return 0

        now_mono = time.monotonic()
        should_process = False
        if self._dirty_event.is_set():
            should_process = True
        elif now_mono >= self._next_poll_at:
            should_process = True
        if not should_process:
            return 0

        self._dirty_event.clear()
        self._next_poll_at = now_mono + self._poll_interval_s
        forwarded = 0
        try:
            previews = self._collect_chat_previews()
            if (not previews) and ((now_mono - self._last_diag_log_at) >= 12.0):
                self._last_diag_log_at = now_mono
                logger.info(
                    "inbound listener heartbeat: events=%d previews=%d baseline=%s",
                    int(self._stats.get("events") or 0),
                    len(previews),
                    self._baseline_ready,
                )
            if not self._baseline_ready:
                for sender, preview in previews:
                    sender_value = str(sender or "").strip()
                    preview_value = _normalize_message_text(preview)
                    if sender_value and preview_value:
                        self._last_preview_by_chat[sender_value] = preview_value
                self._baseline_ready = True
                logger.info("inbound listener baseline initialized: chats=%d", len(self._last_preview_by_chat))
                return 0

            ts_ms = int(time.time() * 1000)
            for sender, preview in previews:
                sender_value = str(sender or "").strip()
                preview_value = _normalize_message_text(preview)
                if not sender_value or not preview_value:
                    continue

                last_value = self._last_preview_by_chat.get(sender_value)
                if last_value == preview_value:
                    continue
                self._last_preview_by_chat[sender_value] = preview_value

                if _is_recent_outbound_echo(preview_value):
                    continue

                if self._is_recent_duplicate(sender=sender_value, content=preview_value):
                    self._stats["messages_deduped"] += 1
                    continue

                msg_id = _build_hook_msg_id(sender=sender_value, content=preview_value, ts_ms=ts_ms)
                body = {
                    "id": msg_id,
                    "msg_id": msg_id,
                    "sender": sender_value,
                    "from_wxid": sender_value,
                    "receiver": "local_wechat",
                    "to_wxid": "local_wechat",
                    "content": preview_value,
                    "text": preview_value,
                    "type": 1,
                    "msg_type": "1",
                    "ts": int(ts_ms / 1000),
                    "bridge_backend": "uia_hook",
                }
                ok = _post_to_edge_callback(
                    edge_url=self._edge_url,
                    body=body,
                    timeout_s=self._callback_timeout_s,
                )
                if ok:
                    forwarded += 1
                    self._stats["messages_forwarded"] += 1
                    logger.info(
                        "forwarded inbound preview sender=%s content=%.80s msg_id=%s",
                        sender_value,
                        preview_value,
                        msg_id,
                    )
        except Exception as exc:
            self._stats["errors"] += 1
            logger.warning("inbound process cycle failed: %s", exc)
        return forwarded

    def _is_recent_duplicate(self, *, sender: str, content: str) -> bool:
        signature = f"{sender}|{content}"
        now_mono = time.monotonic()
        stale = [k for k, ts in self._recent_forwarded.items() if (now_mono - ts) > self._dedup_window_s]
        for item in stale:
            self._recent_forwarded.pop(item, None)
        if signature in self._recent_forwarded:
            return True
        self._recent_forwarded[signature] = now_mono
        return False

    def _hook_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        win_event_proc = ctypes.WINFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        self._hook_cb_fn = win_event_proc(self._on_win_event)
        self._hook_thread_id = int(kernel32.GetCurrentThreadId())
        for event_code in (EVENT_OBJECT_SHOW, EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_VALUECHANGE):
            try:
                handle = user32.SetWinEventHook(
                    event_code,
                    event_code,
                    0,
                    self._hook_cb_fn,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT,
                )
            except Exception:
                handle = 0
            if handle:
                self._hook_handles.append(int(handle))

        msg = _WinMsg()
        while not self._stop_event.is_set():
            has_msg = bool(user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE))
            if has_msg:
                if int(msg.message) == WM_QUIT:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.05)

        for handle in self._hook_handles:
            try:
                user32.UnhookWinEvent(ctypes.c_void_p(handle))
            except Exception:
                pass
        self._hook_handles.clear()

    def _on_win_event(
        self,
        _h_win_event_hook: Any,
        _event: int,
        hwnd: Any,
        _id_object: int,
        _id_child: int,
        _event_thread: int,
        _event_time: int,
    ) -> None:
        try:
            self._stats["events"] += 1
            main_hwnd = _find_wechat_main_window()
            if not main_hwnd:
                return
            hwnd_value = int(hwnd or 0)
            if hwnd_value <= 0:
                self._dirty_event.set()
                return
            user32 = ctypes.windll.user32
            if hwnd_value == main_hwnd or bool(user32.IsChild(main_hwnd, hwnd_value)):
                self._dirty_event.set()
        except Exception:
            return

    def _collect_chat_previews(self) -> list[tuple[str, str]]:
        hwnd = _find_wechat_main_window()
        if not hwnd:
            return []
        try:
            from pywinauto import Desktop
        except Exception as exc:
            logger.warning("pywinauto import failed for inbound collect: %s", exc)
            return []
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
        except Exception as exc:
            logger.warning("failed to bind wechat window for inbound collect: %s", exc)
            return []

        rows: list[tuple[str, str]] = []

        try:
            list_items = window.descendants(control_type="ListItem")
        except Exception:
            list_items = []

        for item in list_items[:80]:
            try:
                texts: list[str] = []
                item_title = _normalize_message_text(item.window_text())
                if item_title:
                    texts.append(item_title)
                for child in item.descendants(control_type="Text"):
                    text_value = _normalize_message_text(child.window_text())
                    if text_value:
                        texts.append(text_value)
            except Exception:
                continue

            if not texts:
                continue
            clean: list[str] = []
            seen: set[str] = set()
            for text_value in texts:
                if not text_value or text_value in seen:
                    continue
                seen.add(text_value)
                clean.append(text_value)
            if len(clean) < 2:
                continue

            sender = clean[0]
            if sender in {"聊天", "通讯录", "发现", "我", "设置"}:
                continue
            preview = ""
            for candidate in clean[1:]:
                if candidate == sender:
                    continue
                if _is_probably_time_or_badge(candidate):
                    continue
                preview = candidate
                break
            if not preview:
                continue
            rows.append((sender, preview))

        dedup_rows: list[tuple[str, str]] = []
        seen_sender: set[str] = set()
        for sender, preview in rows:
            if sender in seen_sender:
                continue
            seen_sender.add(sender)
            dedup_rows.append((sender, preview))

        # Some Weixin builds expose weak/obfuscated chat-list control trees.
        # Fallback: derive current chat title + newest visible bubble text
        # from right-side message area.
        try:
            rect = window.rectangle()
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width > 0 and height > 0:
                title_candidates: list[tuple[int, str]] = []
                message_candidates: list[tuple[int, int, str]] = []
                text_nodes = window.descendants(control_type="Text")
                for node in text_nodes:
                    try:
                        text_value = _normalize_message_text(node.window_text())
                        if not text_value:
                            continue
                        node_rect = node.rectangle()
                        cx = int((int(node_rect.left) + int(node_rect.right)) / 2)
                        cy = int((int(node_rect.top) + int(node_rect.bottom)) / 2)
                    except Exception:
                        continue

                    if cx < (left + int(width * 0.45)):
                        continue
                    if cy <= (top + int(height * 0.18)):
                        if not _is_probably_time_or_badge(text_value):
                            title_candidates.append((len(text_value), text_value))
                        continue
                    if cy >= (top + int(height * 0.83)):
                        continue
                    if _is_probably_time_or_badge(text_value):
                        continue
                    if text_value in {"发送(S)", "聊天信息"}:
                        continue
                    message_candidates.append((cy, cx, text_value))

                if message_candidates:
                    message_candidates.sort(key=lambda x: (x[0], x[1]))
                    latest_text = message_candidates[-1][2]
                    chat_title = "active_chat"
                    if title_candidates:
                        title_candidates.sort(key=lambda x: x[0], reverse=True)
                        chat_title = title_candidates[0][1]
                    if chat_title not in seen_sender:
                        dedup_rows.append((chat_title, latest_text))
        except Exception:
            pass

        if (not dedup_rows) and ((time.monotonic() - self._last_tree_diag_at) >= 45.0):
            self._last_tree_diag_at = time.monotonic()
            self._log_tree_diagnostics(window)
        return dedup_rows

    def _log_tree_diagnostics(self, window: Any) -> None:
        main_hwnd = _find_wechat_main_window()
        win32_children = _enum_child_windows(main_hwnd)
        win32_samples: list[str] = []
        for _, cls, title in win32_children[:80]:
            title_value = _normalize_message_text(title)
            if not title_value:
                continue
            win32_samples.append(f"{cls}:{title_value[:40]}")
            if len(win32_samples) >= 12:
                break

        try:
            descendants = window.descendants()
        except Exception as exc:
            logger.info("inbound tree diag: descendants failed: %s", exc)
            return
        type_counter: dict[str, int] = {}
        text_samples: list[str] = []
        for node in descendants[:500]:
            try:
                control_type = str(getattr(node.element_info, "control_type", "") or "Unknown")
            except Exception:
                control_type = "Unknown"
            type_counter[control_type] = int(type_counter.get(control_type, 0)) + 1
            if len(text_samples) >= 18:
                continue
            try:
                text_value = _normalize_message_text(node.window_text())
            except Exception:
                text_value = ""
            if not text_value:
                continue
            text_samples.append(f"{control_type}:{text_value[:40]}")

        top_types = sorted(type_counter.items(), key=lambda x: x[1], reverse=True)[:10]
        logger.info(
            "inbound tree diag: top_types=%s sample_texts=%s win32_child_texts=%s",
            top_types,
            text_samples,
            win32_samples,
        )

def _is_wechat_client_running() -> bool:
    return bool(_list_wechat_client_process_names())


def _list_wechat_client_process_names() -> set[str]:
    cmd = ["tasklist", "/fo", "csv", "/nh"]
    for encoding in ("utf-8", "gbk"):
        try:
            output = subprocess.check_output(
                cmd,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding=encoding,
                errors="ignore",
            )
            break
        except Exception:
            output = ""
    else:
        output = ""

    names: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('"'):
            process_name = line.split('","', 1)[0].strip('"').lower()
        else:
            process_name = line.split(",", 1)[0].strip().lower()
        if process_name in WECHAT_PROCESS_NAMES:
            names.add(process_name)
    return names


def _launch_wechat_if_needed(wechat_path: str) -> None:
    if _is_wechat_client_running():
        return
    if not wechat_path:
        return
    try:
        subprocess.Popen([wechat_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.warning("failed to launch wechat executable: %s", exc)


def _enum_visible_windows() -> list[tuple[int, str, str]]:
    windows: list[tuple[int, str, str]] = []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _callback(hwnd: int, _lparam: int) -> bool:
        try:
            if not bool(user32.IsWindowVisible(hwnd)):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd))
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            title = str(title_buf.value or "").strip()
            class_name = str(class_buf.value or "").strip()
            if title or class_name:
                windows.append((int(hwnd), class_name, title))
        except Exception:
            return True
        return True

    try:
        user32.EnumWindows(enum_proc(_callback), 0)
    except Exception:
        return []
    return windows


def _enum_child_windows(hwnd: int) -> list[tuple[int, str, str]]:
    if int(hwnd or 0) <= 0:
        return []
    user32 = ctypes.windll.user32
    windows: list[tuple[int, str, str]] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _callback(child_hwnd: int, _lparam: int) -> bool:
        try:
            length = int(user32.GetWindowTextLengthW(child_hwnd))
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(child_hwnd, title_buf, length + 1)
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child_hwnd, class_buf, 256)
            title = str(title_buf.value or "").strip()
            class_name = str(class_buf.value or "").strip()
            windows.append((int(child_hwnd), class_name, title))
        except Exception:
            return True
        return True

    try:
        user32.EnumChildWindows(int(hwnd), enum_proc(_callback), 0)
    except Exception:
        return []
    return windows


def _find_wechat_main_window() -> int:
    for hwnd, class_name, title in _enum_visible_windows():
        class_key = str(class_name or "").strip().lower()
        title_text = str(title or "").strip()
        if class_key in WECHAT_MAIN_WINDOW_CLASS_HINTS:
            return int(hwnd)
        if title_text and any(hint in title_text for hint in WECHAT_WINDOW_TITLE_HINTS):
            return int(hwnd)
    return 0


def _focus_wechat_main_window(wechat_path: str) -> tuple[bool, str]:
    _launch_wechat_if_needed(wechat_path)
    deadline = time.time() + 8.0
    hwnd = 0
    while time.time() < deadline:
        hwnd = _find_wechat_main_window()
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        return False, "wechat main window not found"

    try:
        user32 = ctypes.windll.user32
        sw_restore = 9
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return True, f"focused hwnd=0x{hwnd:X}"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    user32 = ctypes.windll.user32
    if not bool(user32.GetWindowRect(hwnd, ctypes.byref(rect))):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _focus_message_input_area(wechat_path: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    ok, detail = _focus_wechat_main_window(wechat_path)
    if not ok:
        return False, detail

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"

    rect = _get_window_rect(hwnd)
    if rect is None:
        return False, "GetWindowRect failed"
    left, top, right, bottom = rect
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width <= 0 or height <= 0:
        return False, "invalid window rect"

    # WeChat message editor is usually in the lower-right panel.
    click_x = int(left + width * 0.74)
    click_y = int(top + height * 0.88)
    try:
        pyautogui.FAILSAFE = False
        pyautogui.click(x=click_x, y=click_y)
        time.sleep(0.12)
        return True, f"message_input_clicked x={click_x} y={click_y}"
    except Exception as exc:
        return False, f"focus_input_click failed: {exc.__class__.__name__}: {exc}"


def _set_clipboard_text(text: str) -> tuple[bool, str]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    cf_unicode_text = 13
    gmem_moveable = 0x0002
    payload = (str(text or "") + "\x00").encode("utf-16-le")

    try:
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_bool
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_bool
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_bool
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_bool
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
    except Exception:
        pass

    if not bool(user32.OpenClipboard(0)):
        return False, "OpenClipboard failed"
    h_mem = None
    try:
        if not bool(user32.EmptyClipboard()):
            return False, "EmptyClipboard failed"
        h_mem = kernel32.GlobalAlloc(gmem_moveable, len(payload))
        if not h_mem:
            return False, "GlobalAlloc failed"
        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            return False, "GlobalLock failed"
        ctypes.memmove(ptr, payload, len(payload))
        kernel32.GlobalUnlock(h_mem)
        if not bool(user32.SetClipboardData(cf_unicode_text, h_mem)):
            return False, "SetClipboardData failed"
        h_mem = None
        return True, "ok"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    finally:
        if h_mem:
            kernel32.GlobalFree(h_mem)
        user32.CloseClipboard()


def _open_chat_by_target(target: str, wechat_path: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    ok, detail = _focus_wechat_main_window(wechat_path)
    if not ok:
        return False, detail

    def _is_soso_page_active() -> bool:
        try:
            from pywinauto import Desktop
        except Exception:
            return False
        hwnd = _find_wechat_main_window()
        if not hwnd:
            return False
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            if bool(window.child_window(title="搜一搜").exists(timeout=0.2)):
                return True
            if bool(window.child_window(title="搜索发现").exists(timeout=0.2)):
                return True
        except Exception:
            return False
        return False

    def _try_open_with_hotkey(hotkey_second: str) -> tuple[bool, str]:
        try:
            pyautogui.FAILSAFE = False
            pyautogui.hotkey("ctrl", hotkey_second)
            time.sleep(0.28)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            pyautogui.write(str(target or ""), interval=0.02)
            time.sleep(0.28)
            pyautogui.press("down")
            time.sleep(0.08)
            pyautogui.press("enter")
            time.sleep(0.38)
            pyautogui.press("escape")
            time.sleep(0.10)
            if _is_soso_page_active():
                return False, "stuck_on_soso_page"
            return True, f"open_chat_hotkey=ctrl+{hotkey_second}"
        except Exception as exc:
            return False, f"{exc.__class__.__name__}: {exc}"

    primary_ok, primary_detail = _try_open_with_hotkey("n")
    if primary_ok:
        return True, f"{detail}; {primary_detail}"

    fallback_ok, fallback_detail = _try_open_with_hotkey("f")
    if fallback_ok:
        return True, f"{detail}; {fallback_detail}"
    return False, f"open_chat failed: primary={primary_detail}; fallback={fallback_detail}"


def _send_text(content: str, wechat_path: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    clip_ok, clip_detail = _set_clipboard_text(content)
    if not clip_ok:
        return False, f"clipboard failed: {clip_detail}"

    input_ok, input_detail = _focus_message_input_area(wechat_path=wechat_path)
    if not input_ok:
        return False, f"focus_input failed: {input_detail}"

    try:
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyautogui.press("enter")
        return True, f"ok ({input_detail})"
    except Exception as exc:
        return False, f"send_text failed: {exc.__class__.__name__}: {exc}"


def _click_call_button(mode: str, wechat_path: str) -> tuple[bool, str]:
    ok, detail = _focus_wechat_main_window(wechat_path)
    if not ok:
        return False, detail

    title_patterns = (
        (r".*语音通话.*", r".*Voice.*")
        if mode == "voice_call"
        else (r".*视频通话.*", r".*Video.*")
    )
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return False, f"pywinauto import failed: {exc}"

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"

    try:
        window = Desktop(backend="uia").window(handle=hwnd)
    except Exception as exc:
        return False, f"pywinauto bind window failed: {exc}"

    for pattern in title_patterns:
        try:
            btn = window.child_window(title_re=pattern, control_type="Button")
            if bool(btn.exists(timeout=0.5)):
                btn.click_input()
                return True, f"clicked {pattern} ({detail})"
        except Exception:
            continue
    return False, "call button not found"


def _execute_command(command: dict[str, Any], *, wechat_path: str) -> dict[str, Any]:
    action = str(command.get("action") or "").strip().lower()
    target = str(command.get("target") or "").strip()
    content = str(command.get("content") or "")
    command_id = str(command.get("command_id") or "")
    request_id = str(command.get("request_id") or "")
    now_ms = int(time.time() * 1000)

    if not command_id:
        command_id = f"uia-{now_ms}"

    base_row = {
        "command_id": command_id,
        "request_id": request_id,
        "action": action,
        "target": target,
        "finished_ts_ms": now_ms,
    }
    if action not in {"send_text", "voice_call", "video_call"}:
        return {
            **base_row,
            "status": "error",
            "success": False,
            "reason_code": "WECHAT_COMMAND_ACTION_INVALID",
            "detail": f"unsupported action: {action}",
        }
    if not target:
        return {
            **base_row,
            "status": "error",
            "success": False,
            "reason_code": "WECHAT_COMMAND_TARGET_MISSING",
            "detail": "target is required",
        }
    if action == "send_text" and not content.strip():
        return {
            **base_row,
            "status": "error",
            "success": False,
            "reason_code": "WECHAT_COMMAND_CONTENT_MISSING",
            "detail": "send_text requires non-empty content",
        }

    if action == "send_text":
        open_ok, open_detail = _open_chat_by_target(target=target, wechat_path=wechat_path)
        if not open_ok:
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": open_detail,
            }
        ok, detail = _send_text(content=content, wechat_path=wechat_path)
        if ok:
            _remember_outbound_text(content)
            detail = f"{detail} ({open_detail})"
    elif action == "voice_call":
        open_ok, open_detail = _open_chat_by_target(target=target, wechat_path=wechat_path)
        if not open_ok:
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": open_detail,
            }
        ok, detail = _click_call_button(mode="voice_call", wechat_path=wechat_path)
    else:
        open_ok, open_detail = _open_chat_by_target(target=target, wechat_path=wechat_path)
        if not open_ok:
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": open_detail,
            }
        ok, detail = _click_call_button(mode="video_call", wechat_path=wechat_path)

    return {
        **base_row,
        "status": ("ok" if ok else "error"),
        "success": bool(ok),
        "reason_code": (None if ok else "WECHAT_COMMAND_EXEC_FAILED"),
        "detail": detail,
    }


def _process_command_queue(
    *,
    queue_file: str,
    result_file: str,
    offset: int,
    wechat_path: str,
) -> tuple[int, int, int]:
    rows, next_offset = _read_jsonl_rows_from_offset(queue_file, offset)
    if not rows:
        return next_offset, 0, 0

    success_count = 0
    for command in rows:
        result = _execute_command(command, wechat_path=wechat_path)
        if bool(result.get("success")):
            success_count += 1
        try:
            _append_jsonl_row(result_file, result)
        except Exception as exc:
            logger.warning("failed to append command result: %s", exc)
    return next_offset, len(rows), success_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consume WeChat command queue with pure desktop UI automation."
    )
    parser.add_argument(
        "--edge-url",
        default=DEFAULT_EDGE_URL,
        help=f"Edge base URL (reserved; default: {DEFAULT_EDGE_URL})",
    )
    parser.add_argument(
        "--command-queue-file",
        default=(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or "").strip(),
        help="JSONL command queue file path.",
    )
    parser.add_argument(
        "--command-result-file",
        default=(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or "").strip(),
        help="JSONL command result file path.",
    )
    parser.add_argument(
        "--command-poll-interval",
        type=float,
        default=DEFAULT_COMMAND_POLL_INTERVAL_S,
        help=f"Command queue poll interval in seconds (default: {DEFAULT_COMMAND_POLL_INTERVAL_S})",
    )
    parser.add_argument(
        "--wechat-path",
        default=(os.environ.get("EDGE_WECHAT_EXECUTABLE_PATH") or "").strip(),
        help="Optional WeChat/Weixin executable path.",
    )
    parser.add_argument(
        "--listener-enabled",
        default=(os.environ.get("EDGE_WECHAT_LISTENER_ENABLED") or "1").strip(),
        help="Enable inbound WinEvent/UIA listener (default from EDGE_WECHAT_LISTENER_ENABLED).",
    )
    parser.add_argument(
        "--inbound-backend",
        default=(os.environ.get("EDGE_WECHAT_INBOUND_BACKEND") or DEFAULT_INBOUND_BACKEND).strip(),
        help=f"Inbound backend: uia_hook|wcferry|off (default: {DEFAULT_INBOUND_BACKEND}).",
    )
    parser.add_argument(
        "--listener-poll-interval",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_LISTENER_POLL_INTERVAL_S") or DEFAULT_LISTENER_POLL_INTERVAL_S),
        help=f"Inbound preview poll interval in seconds (default: {DEFAULT_LISTENER_POLL_INTERVAL_S}).",
    )
    parser.add_argument(
        "--listener-dedup-window",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_LISTENER_DEDUP_WINDOW_S") or DEFAULT_LISTENER_DEDUP_WINDOW_S),
        help=f"Inbound dedup window in seconds (default: {DEFAULT_LISTENER_DEDUP_WINDOW_S}).",
    )
    parser.add_argument(
        "--callback-timeout",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_CALLBACK_TIMEOUT_S") or DEFAULT_CALLBACK_TIMEOUT_S),
        help=f"Timeout for posting /v1/edge/wechat/callback (default: {DEFAULT_CALLBACK_TIMEOUT_S}).",
    )
    args = parser.parse_args()

    queue_file = _normalize_optional_path(args.command_queue_file)
    result_file = _normalize_optional_path(args.command_result_file)
    wechat_path = str(args.wechat_path or "").strip()
    poll_interval = max(0.1, float(args.command_poll_interval))
    listener_enabled = _parse_bool(args.listener_enabled, default=True)
    inbound_backend = str(args.inbound_backend or DEFAULT_INBOUND_BACKEND).strip().lower()
    if inbound_backend in {"none", "off", "disabled"}:
        listener_enabled = False
        inbound_backend = "off"
    elif inbound_backend not in {"uia_hook", "wcferry"}:
        logger.warning("invalid inbound backend %s, fallback to uia_hook", inbound_backend)
        inbound_backend = "uia_hook"
    if inbound_backend == "wcferry":
        wechat_base = os.path.basename(str(wechat_path or "").replace("/", "\\")).strip().lower()
        running_names = _list_wechat_client_process_names()
        weixin_only_runtime = ("weixin.exe" in running_names and "wechat.exe" not in running_names)
        if wechat_base == "weixin.exe" or weixin_only_runtime:
            logger.warning(
                "wcferry disabled on Weixin runtime (WxInitSDK inject unsupported/unstable); fallback to uia_hook"
            )
            inbound_backend = "uia_hook"
    if not queue_file or not result_file:
        logger.error("queue/result file path is required")
        return 2

    _ensure_parent_dir(queue_file)
    _ensure_parent_dir(result_file)
    if not os.path.exists(queue_file):
        with open(queue_file, "a", encoding="utf-8"):
            pass
    if not os.path.exists(result_file):
        with open(result_file, "a", encoding="utf-8"):
            pass

    try:
        import pyautogui  # noqa: F401
    except Exception as exc:
        logger.error("pyautogui unavailable: %s", exc)
        return 3

    logger.info(
        "uia bridge started edge_url=%s queue=%s result=%s wechat_path=%s listener_enabled=%s inbound_backend=%s",
        args.edge_url,
        queue_file,
        result_file,
        wechat_path or "(auto)",
        listener_enabled,
        inbound_backend,
    )

    if inbound_backend == "wcferry":
        inbound_bridge = WeChatWcferryInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            callback_timeout_s=float(args.callback_timeout),
        )
    else:
        inbound_bridge = WeChatUiInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
        )
    inbound_bridge.start()

    offset = 0
    total = 0
    total_success = 0
    inbound_total = 0
    try:
        while True:
            try:
                offset, batch_count, batch_success = _process_command_queue(
                    queue_file=queue_file,
                    result_file=result_file,
                    offset=offset,
                    wechat_path=wechat_path,
                )
                inbound_forwarded = inbound_bridge.process_cycle()
                if inbound_forwarded > 0:
                    inbound_total += inbound_forwarded
                    logger.info(
                        "forwarded inbound messages: batch=%d cumulative=%d",
                        inbound_forwarded,
                        inbound_total,
                    )
                if batch_count > 0:
                    total += batch_count
                    total_success += batch_success
                    logger.info(
                        "processed command batch: total=%d success=%d failed=%d cumulative_total=%d cumulative_success=%d",
                        batch_count,
                        batch_success,
                        batch_count - batch_success,
                        total,
                        total_success,
                    )
            except Exception as exc:
                logger.warning("bridge poll cycle failed: %s", exc)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info(
            "interrupted; total_commands=%d command_success=%d inbound_forwarded=%d",
            total,
            total_success,
            inbound_total,
        )
        return 0
    finally:
        inbound_bridge.stop()


if __name__ == "__main__":
    sys.exit(main())
