"""
WeChat desktop UI automation bridge.

This bridge consumes outbound command queue records and executes them on the
interactive WeChat/Weixin desktop client.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import difflib
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tempfile
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
DEFAULT_UIA_SCAN_CHAT_LIST = False
DEFAULT_FORCE_ACTIVE_CHAT_TARGET = True
DEFAULT_UIA_ACTIVE_INBOUND_X_RATIO_MAX = 0.70
DEFAULT_VISION_MODEL = "qwen2.5vl:3b"
DEFAULT_VISION_API_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_VISION_TIMEOUT_S = 30.0
DEFAULT_VISION_MIN_CONFIDENCE = 0.50
DEFAULT_VISION_MIN_INTERVAL_S = 1.8
DEFAULT_NOTIFY_DB_PATH = r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db"
DEFAULT_NOTIFY_HANDLER_KEYWORDS = ("weixin", "wechat", "tencent")
DEFAULT_NOTIFY_PAYLOAD_KEYWORDS = ("微信", "weixin", "wechat", "tencent")
FILETIME_1601_TO_1970 = 116444736000000000
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WECHAT_MAIN_WINDOW_CLASS_HINTS = {
    "wechatmainwndforpc",
    "weixinmainwndforpc",
    "weixinmainwnd",
}
WECHAT_WINDOW_TITLE_HINTS = ("微信", "Weixin", "WeChat")
ACTIVE_CHAT_TARGET_HINTS = {
    "active_chat",
    "activechat",
    "current_chat",
    "currentchat",
    "local_wechat",
    "localwechat",
    "wechat_active",
    "wechatactive",
    "当前会话",
}
WECHAT_SYSTEM_LIST_TITLES = {"聊天", "通讯录", "发现", "我", "设置"}

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


def _normalize_target_hint(text: str) -> str:
    normalized = _normalize_message_text(text).lower()
    normalized = normalized.replace(" ", "").replace("_", "")
    return normalized


def _is_active_chat_target(target: str) -> bool:
    raw = str(target or "").strip()
    if not raw:
        return False
    if raw in ACTIVE_CHAT_TARGET_HINTS:
        return True
    normalized = _normalize_target_hint(raw)
    return normalized in ACTIVE_CHAT_TARGET_HINTS


def _extract_element_text_candidates(element: Any) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    try:
        self_text = _normalize_message_text(element.window_text())
        if self_text and self_text not in seen:
            seen.add(self_text)
            values.append(self_text)
    except Exception:
        pass
    try:
        for child in element.descendants(control_type="Text"):
            text_value = _normalize_message_text(child.window_text())
            if not text_value or text_value in seen:
                continue
            seen.add(text_value)
            values.append(text_value)
    except Exception:
        pass
    return values


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


def _normalize_echo_compare_text(text: str) -> str:
    value = _normalize_message_text(text).lower()
    if not value:
        return ""
    value = re.sub(r"^(me|you)\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(我|你|本人|对方)\s*[:：]\s*", "", value)
    value = re.sub(r"[\s\.,!?;:，。！？；：“”\"'`~\-_\(\)\[\]\{\}<>|\\/]+", "", value)
    return value


def _looks_like_outbound_echo(candidate: str, outbound_text: str) -> bool:
    left = _normalize_echo_compare_text(candidate)
    right = _normalize_echo_compare_text(outbound_text)
    if not left or not right:
        return False
    if left == right:
        return True
    min_len = min(len(left), len(right))
    max_len = max(len(left), len(right))
    if min_len >= 4 and (left in right or right in left):
        return True
    if min_len >= 4 and max_len <= 64:
        ratio = difflib.SequenceMatcher(a=left, b=right).ratio()
        if ratio >= 0.78:
            return True
    return False


def _remember_outbound_text(text: str, *, ttl_s: float = 75.0) -> None:
    key = _normalize_message_text(text)
    if not key:
        return
    now_mono = time.monotonic()
    with _RECENT_OUTBOUND_LOCK:
        stale = [k for k, t in _RECENT_OUTBOUND_TEXTS.items() if (now_mono - t) > ttl_s]
        for item in stale:
            _RECENT_OUTBOUND_TEXTS.pop(item, None)
        _RECENT_OUTBOUND_TEXTS[key] = now_mono


def _is_recent_outbound_echo(text: str, *, ttl_s: float = 75.0) -> bool:
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
        for recent_outbound in _RECENT_OUTBOUND_TEXTS.keys():
            if _looks_like_outbound_echo(key, recent_outbound):
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


class WeChatWindowsNotificationInboundBridge:
    """Inbound bridge: poll Windows notification DB and forward WeChat notifications."""

    def __init__(
        self,
        *,
        edge_url: str,
        enabled: bool,
        poll_interval_s: float,
        callback_timeout_s: float,
        dedup_window_s: float,
        db_path: str,
        handler_keywords: list[str],
    ) -> None:
        self._edge_url = str(edge_url or DEFAULT_EDGE_URL).strip()
        self._enabled = bool(enabled)
        self._poll_interval_s = max(0.5, float(poll_interval_s))
        self._callback_timeout_s = max(0.2, float(callback_timeout_s))
        self._dedup_window_s = max(1.0, float(dedup_window_s))
        self._db_path = os.path.expandvars(str(db_path or DEFAULT_NOTIFY_DB_PATH).strip())
        self._handler_keywords = [str(k or "").strip().lower() for k in handler_keywords if str(k or "").strip()]
        if not self._handler_keywords:
            self._handler_keywords = list(DEFAULT_NOTIFY_HANDLER_KEYWORDS)
        self._payload_keywords = list(DEFAULT_NOTIFY_PAYLOAD_KEYWORDS)

        self._next_poll_at = 0.0
        self._baseline_ready = False
        self._last_notification_id = 0
        self._last_no_target_log_at = 0.0
        self._last_error_log_at = 0.0
        self._last_heartbeat_at = 0.0
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
            logger.info("notification inbound listener disabled")
            return
        logger.info(
            "notification inbound listener enabled db=%s keywords=%s",
            self._db_path,
            ",".join(self._handler_keywords),
        )

    def stop(self) -> None:
        return

    def process_cycle(self) -> int:
        if not self._enabled:
            return 0
        now_mono = time.monotonic()
        if now_mono < self._next_poll_at:
            return 0
        self._next_poll_at = now_mono + self._poll_interval_s

        try:
            max_id, events, handler_count = self._read_new_events_since(self._last_notification_id)
        except Exception as exc:
            self._stats["errors"] += 1
            if (now_mono - self._last_error_log_at) >= 12.0:
                self._last_error_log_at = now_mono
                logger.warning("notification inbound cycle failed: %s", exc)
            return 0

        if handler_count <= 0 and (not events):
            if (now_mono - self._last_no_target_log_at) >= 20.0:
                self._last_no_target_log_at = now_mono
                logger.info(
                    "notification inbound: no target handlers matched keywords=%s",
                    ",".join(self._handler_keywords),
                )
            return 0

        if not self._baseline_ready:
            self._last_notification_id = max(self._last_notification_id, int(max_id))
            self._baseline_ready = True
            logger.info(
                "notification inbound baseline initialized: max_id=%d handlers=%d",
                self._last_notification_id,
                handler_count,
            )
            return 0

        forwarded = 0
        for event in events:
            notification_id = int(event.get("notification_id") or 0)
            if notification_id > self._last_notification_id:
                self._last_notification_id = notification_id

            sender = str(event.get("sender") or "").strip()
            content = _normalize_message_text(str(event.get("content") or ""))
            if not sender or not content:
                continue
            if _is_probably_time_or_badge(content):
                continue
            if _is_recent_outbound_echo(content):
                continue
            if self._is_recent_duplicate(sender=sender, content=content):
                self._stats["messages_deduped"] += 1
                continue

            ts_ms = int(event.get("ts_ms") or 0) or int(time.time() * 1000)
            msg_id = f"notify-{notification_id}"
            body = {
                "id": msg_id,
                "msg_id": msg_id,
                "sender": sender,
                "from_wxid": sender,
                "receiver": "local_wechat",
                "to_wxid": "local_wechat",
                "content": content,
                "text": content,
                "type": 1,
                "msg_type": "1",
                "ts": int(ts_ms / 1000),
                "bridge_backend": "win_notify",
                "roomid": "",
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
                    "notification inbound forwarded sender=%s msg_id=%s content=%.80s",
                    sender,
                    msg_id,
                    content,
                )

        if (now_mono - self._last_heartbeat_at) >= 15.0:
            self._last_heartbeat_at = now_mono
            logger.info(
                "notification inbound heartbeat: max_id=%d handlers=%d scanned=%d",
                self._last_notification_id,
                handler_count,
                len(events),
            )
        return forwarded

    def _is_recent_duplicate(self, *, sender: str, content: str) -> bool:
        key = f"{str(sender or '').strip()}|{_normalize_message_text(content)}"
        if not key.strip("|"):
            return False
        now_mono = time.monotonic()
        stale = [k for k, ts in self._recent_forwarded.items() if (now_mono - ts) > self._dedup_window_s]
        for item in stale:
            self._recent_forwarded.pop(item, None)
        if key in self._recent_forwarded:
            return True
        self._recent_forwarded[key] = now_mono
        return False

    def _read_new_events_since(self, last_notification_id: int) -> tuple[int, list[dict[str, Any]], int]:
        db_copy = ""
        wal_copy = ""
        shm_copy = ""
        if not os.path.exists(self._db_path):
            raise RuntimeError(f"notification db not found: {self._db_path}")
        try:
            db_copy = tempfile.NamedTemporaryFile(prefix="natsume_wpn_", suffix=".db", delete=False).name
            shutil.copy2(self._db_path, db_copy)
            wal_src = self._db_path + "-wal"
            shm_src = self._db_path + "-shm"
            wal_copy = db_copy + "-wal"
            shm_copy = db_copy + "-shm"
            if os.path.exists(wal_src):
                shutil.copy2(wal_src, wal_copy)
            if os.path.exists(shm_src):
                shutil.copy2(shm_src, shm_copy)
        except Exception as exc:
            raise RuntimeError(f"copy notification db failed: {exc}") from exc

        try:
            con = sqlite3.connect(db_copy, timeout=2.0)
            cur = con.cursor()
            try:
                handler_rows = self._query_target_handlers(cur)
                handler_ids = [int(row["handler_id"]) for row in handler_rows]
                max_seen_id = int(last_notification_id or 0)
                events: list[dict[str, Any]] = []
                if handler_ids:
                    placeholder = ",".join("?" for _ in handler_ids)
                    sql = f"""
                    SELECT
                      n.Id,
                      n.HandlerId,
                      nh.PrimaryId,
                      n.ArrivalTime,
                      n.Payload
                    FROM Notification n
                    JOIN NotificationHandler nh ON nh.RecordId = n.HandlerId
                    WHERE n.HandlerId IN ({placeholder}) AND n.Id > ?
                    ORDER BY n.Id ASC
                    LIMIT 180
                    """
                    params: list[Any] = list(handler_ids)
                    params.append(int(last_notification_id or 0))
                    require_wechat_hint = False
                else:
                    sql = """
                    SELECT
                      n.Id,
                      n.HandlerId,
                      nh.PrimaryId,
                      n.ArrivalTime,
                      n.Payload
                    FROM Notification n
                    JOIN NotificationHandler nh ON nh.RecordId = n.HandlerId
                    WHERE n.Id > ?
                    ORDER BY n.Id ASC
                    LIMIT 180
                    """
                    params = [int(last_notification_id or 0)]
                    require_wechat_hint = True
                rows = cur.execute(sql, params).fetchall()
                for row in rows:
                    notification_id = int(row[0] or 0)
                    handler_id = int(row[1] or 0)
                    primary_id = str(row[2] or "")
                    arrival = row[3]
                    payload = row[4]
                    if notification_id > max_seen_id:
                        max_seen_id = notification_id
                    event = self._build_notification_event(
                        notification_id=notification_id,
                        handler_id=handler_id,
                        primary_id=primary_id,
                        arrival_filetime=arrival,
                        payload=payload,
                        require_wechat_hint=require_wechat_hint,
                    )
                    if event is None:
                        continue
                    events.append(event)
                self._stats["events"] += len(events)
                return max_seen_id, events, len(handler_ids)
            finally:
                con.close()
        finally:
            for path_value in (db_copy, wal_copy, shm_copy):
                if not path_value:
                    continue
                try:
                    if os.path.exists(path_value):
                        os.remove(path_value)
                except Exception:
                    pass

    def _query_target_handlers(self, cur: sqlite3.Cursor) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[str] = []
        for keyword in self._handler_keywords:
            where_parts.append("lower(PrimaryId) LIKE ?")
            params.append(f"%{keyword}%")
        if not where_parts:
            return []
        sql = (
            "SELECT RecordId, PrimaryId "
            "FROM NotificationHandler "
            f"WHERE {' OR '.join(where_parts)} "
            "ORDER BY RecordId"
        )
        rows = cur.execute(sql, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "handler_id": int(row[0] or 0),
                    "primary_id": str(row[1] or ""),
                }
            )
        return result

    def _build_notification_event(
        self,
        *,
        notification_id: int,
        handler_id: int,
        primary_id: str,
        arrival_filetime: Any,
        payload: Any,
        require_wechat_hint: bool,
    ) -> dict[str, Any] | None:
        payload_text = ""
        if isinstance(payload, bytes):
            payload_text = payload.decode("utf-8", errors="ignore")
        else:
            payload_text = str(payload or "")
        texts = self._extract_payload_texts(payload_text)
        if require_wechat_hint and (not self._payload_looks_like_wechat(primary_id=primary_id, payload_text=payload_text, texts=texts)):
            return None
        if not texts:
            return None
        sender = texts[0]
        content = texts[1] if len(texts) >= 2 else texts[0]
        sender = _normalize_message_text(sender)
        content = _normalize_message_text(content)
        if not sender:
            sender = self._fallback_sender(primary_id=primary_id, handler_id=handler_id)
        if not sender or not content:
            return None
        return {
            "notification_id": int(notification_id),
            "sender": sender,
            "content": content,
            "ts_ms": self._filetime_to_unix_ms(arrival_filetime),
        }

    def _payload_looks_like_wechat(self, *, primary_id: str, payload_text: str, texts: list[str]) -> bool:
        primary_lower = str(primary_id or "").lower()
        for keyword in self._handler_keywords:
            if keyword and keyword in primary_lower:
                return True
        payload_lower = str(payload_text or "").lower()
        merged_text = " ".join(texts)
        merged_lower = merged_text.lower()
        for keyword in self._payload_keywords:
            if keyword == "微信":
                if ("微信" in payload_text) or ("微信" in merged_text):
                    return True
                continue
            kw = str(keyword or "").strip().lower()
            if not kw:
                continue
            if (kw in payload_lower) or (kw in merged_lower):
                return True
        return False

    @staticmethod
    def _fallback_sender(*, primary_id: str, handler_id: int) -> str:
        text = str(primary_id or "").strip()
        if text:
            tail = text.rsplit("!", 1)[-1]
            return _normalize_message_text(tail or text)
        return f"handler_{handler_id}"

    @staticmethod
    def _filetime_to_unix_ms(value: Any) -> int:
        try:
            v = int(value)
        except Exception:
            return int(time.time() * 1000)
        if v <= 0:
            return int(time.time() * 1000)
        ts_ms = int((v - FILETIME_1601_TO_1970) / 10_000)
        if ts_ms <= 0:
            return int(time.time() * 1000)
        return ts_ms

    @staticmethod
    def _extract_payload_texts(payload_text: str) -> list[str]:
        if not payload_text:
            return []
        xml_texts = re.findall(r"<text[^>]*>(.*?)</text>", payload_text, flags=re.IGNORECASE | re.DOTALL)
        result: list[str] = []
        for raw in xml_texts:
            value = html.unescape(str(raw or ""))
            value = re.sub(r"<[^>]+>", " ", value)
            value = _normalize_message_text(value)
            if not value:
                continue
            if value in result:
                continue
            result.append(value)
        return result


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
        scan_chat_list: bool = DEFAULT_UIA_SCAN_CHAT_LIST,
        force_active_chat_target: bool = DEFAULT_FORCE_ACTIVE_CHAT_TARGET,
        active_inbound_x_ratio_max: float = DEFAULT_UIA_ACTIVE_INBOUND_X_RATIO_MAX,
    ) -> None:
        self._edge_url = str(edge_url or DEFAULT_EDGE_URL).strip()
        self._enabled = bool(enabled)
        self._poll_interval_s = max(0.2, float(poll_interval_s))
        self._callback_timeout_s = max(0.2, float(callback_timeout_s))
        self._dedup_window_s = max(1.0, float(dedup_window_s))
        self._scan_chat_list = bool(scan_chat_list)
        self._force_active_chat_target = bool(force_active_chat_target)
        self._active_inbound_x_ratio_max = max(0.45, min(0.90, float(active_inbound_x_ratio_max)))

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
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info("vision inbound skipped: wechat main window not found")
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

        if self._scan_chat_list:
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
                if sender in WECHAT_SYSTEM_LIST_TITLES:
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

        dedup_map: dict[str, str] = {}
        for sender, preview in rows:
            if sender and preview and sender not in dedup_map:
                dedup_map[sender] = preview

        # Always inspect active chat pane and prefer inbound-side text.
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
                panel_left = left + int(width * 0.34)
                panel_right = left + int(width * 0.97)
                inbound_x_max = left + int(width * self._active_inbound_x_ratio_max)
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

                    if cx < panel_left or cx > panel_right:
                        continue
                    if cy <= (top + int(height * 0.18)):
                        if not _is_probably_time_or_badge(text_value):
                            title_candidates.append((len(text_value), text_value))
                        continue
                    if cy >= (top + int(height * 0.83)):
                        continue
                    if _is_probably_time_or_badge(text_value):
                        continue
                    if cx > inbound_x_max:
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
                    if self._force_active_chat_target:
                        chat_title = "active_chat"
                    if chat_title and latest_text:
                        dedup_map[chat_title] = latest_text
        except Exception:
            pass

        dedup_rows = list(dedup_map.items())
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


class WeChatUiOcrInboundBridge(WeChatUiInboundBridge):
    """Inbound bridge: WinEvent hook + OCR parse of active chat message area."""

    def __init__(
        self,
        *,
        edge_url: str,
        enabled: bool,
        poll_interval_s: float,
        callback_timeout_s: float,
        dedup_window_s: float,
        ocr_min_score: float,
        inbound_x_ratio_max: float,
        scan_chat_list: bool = DEFAULT_UIA_SCAN_CHAT_LIST,
        force_active_chat_target: bool = DEFAULT_FORCE_ACTIVE_CHAT_TARGET,
        active_inbound_x_ratio_max: float = DEFAULT_UIA_ACTIVE_INBOUND_X_RATIO_MAX,
    ) -> None:
        super().__init__(
            edge_url=edge_url,
            enabled=enabled,
            poll_interval_s=poll_interval_s,
            callback_timeout_s=callback_timeout_s,
            dedup_window_s=dedup_window_s,
            scan_chat_list=scan_chat_list,
            force_active_chat_target=force_active_chat_target,
            active_inbound_x_ratio_max=active_inbound_x_ratio_max,
        )
        self._ocr_min_score = max(0.1, min(0.99, float(ocr_min_score)))
        self._inbound_x_ratio_max = max(0.2, min(0.95, float(inbound_x_ratio_max)))
        self._ocr_engine = None
        self._ocr_disabled = False
        self._last_ocr_diag_at = 0.0
        self._last_ocr_sender = "active_chat"
        self._last_focus_retry_at = 0.0
        self._ocr_empty_cycles = 0
        self._last_ocr_side_fallback_diag_at = 0.0

    def _collect_chat_previews(self) -> list[tuple[str, str]]:
        # OCR mode must prioritize OCR extraction to avoid UIA text noise loops.
        previews = self._collect_chat_previews_via_ocr()
        if previews:
            self._ocr_empty_cycles = 0
            return previews
        self._ocr_empty_cycles += 1
        if self._ocr_disabled or self._ocr_empty_cycles >= 4:
            fallback = super()._collect_chat_previews()
            if fallback:
                logger.info(
                    "ocr inbound fallback via uia: rows=%d empty_cycles=%d",
                    len(fallback),
                    self._ocr_empty_cycles,
                )
                self._ocr_empty_cycles = 0
            return fallback
        return []

    def _collect_chat_previews_via_ocr(self) -> list[tuple[str, str]]:
        hwnd = _find_wechat_main_window()
        if not hwnd:
            return []

        right_ocr_rows, list_ocr_rows, right_previews, list_previews = self._collect_ocr_from_rois(hwnd=hwnd)
        if right_previews:
            return right_previews
        if list_previews:
            return list_previews

        now_mono = time.monotonic()
        if (not right_ocr_rows) and (not list_ocr_rows) and ((now_mono - self._last_focus_retry_at) >= 20.0):
            self._last_focus_retry_at = now_mono
            focused, focus_detail = _focus_wechat_main_window(wechat_path="")
            if focused:
                time.sleep(0.18)
                retry_right_rows, retry_list_rows, retry_right_previews, retry_list_previews = self._collect_ocr_from_rois(
                    hwnd=hwnd
                )
                if retry_right_previews:
                    return retry_right_previews
                if retry_list_previews:
                    return retry_list_previews
                if retry_right_rows:
                    right_ocr_rows = retry_right_rows
                if retry_list_rows:
                    list_ocr_rows = retry_list_rows
            else:
                logger.info("ocr inbound focus retry skipped: %s", focus_detail)

        now_mono = time.monotonic()
        if (now_mono - self._last_ocr_diag_at) >= 18.0:
            self._last_ocr_diag_at = now_mono
            logger.info(
                "ocr inbound heartbeat: right_rows=%d list_rows=%d score>=%.2f x_max=%.2f right_samples=%s list_samples=%s",
                len(right_ocr_rows),
                len(list_ocr_rows),
                self._ocr_min_score,
                self._inbound_x_ratio_max,
                self._format_ocr_samples(right_ocr_rows),
                self._format_ocr_samples(list_ocr_rows),
            )
        return []

    def _collect_ocr_from_rois(
        self,
        *,
        hwnd: int,
    ) -> tuple[
        list[tuple[int, int, str, float]],
        list[tuple[int, int, str, float]],
        list[tuple[str, str]],
        list[tuple[str, str]],
    ]:
        right_ocr_rows: list[tuple[int, int, str, float]] = []
        right_previews: list[tuple[str, str]] = []
        right_roi = self._resolve_message_roi(hwnd)
        if right_roi is not None:
            r_left, r_top, r_width, r_height = right_roi
            if r_width > 20 and r_height > 20:
                image = self._capture_region_bgr(left=r_left, top=r_top, width=r_width, height=r_height)
                if image is not None:
                    right_ocr_rows = self._run_ocr(image)
                    inbound_candidates = self._extract_inbound_candidates_from_message_roi(
                        ocr_rows=right_ocr_rows,
                        roi_width=r_width,
                    )
                    if inbound_candidates:
                        inbound_candidates.sort(key=lambda x: x[0])
                        latest_text = inbound_candidates[-1][1]
                        sender = self._infer_active_chat_sender(hwnd=hwnd)
                        self._last_ocr_sender = sender
                        right_previews = [(sender, latest_text)]

        list_ocr_rows: list[tuple[int, int, str, float]] = []
        list_previews: list[tuple[str, str]] = []
        list_roi = self._resolve_chat_list_roi(hwnd)
        if list_roi is not None:
            l_left, l_top, l_width, l_height = list_roi
            if l_width > 20 and l_height > 20:
                image = self._capture_region_bgr(left=l_left, top=l_top, width=l_width, height=l_height)
                if image is not None:
                    list_ocr_rows = self._run_ocr(image)
                    list_previews = self._extract_chat_list_previews_from_ocr(
                        ocr_rows=list_ocr_rows,
                        roi_height=l_height,
                    )
        return right_ocr_rows, list_ocr_rows, right_previews, list_previews

    def _extract_inbound_candidates_from_message_roi(
        self,
        *,
        ocr_rows: list[tuple[int, int, str, float]],
        roi_width: int,
    ) -> list[tuple[int, str]]:
        left_side_candidates: list[tuple[int, str]] = []
        all_candidates: list[tuple[int, int, str]] = []
        for cy, cx, text_value, score in ocr_rows:
            if score < self._ocr_min_score:
                continue
            norm = _normalize_message_text(text_value)
            if not norm:
                continue
            if len(norm) <= 1:
                continue
            if _is_probably_time_or_badge(norm):
                continue
            if self._is_probably_ui_noise_text(norm):
                continue
            all_candidates.append((cy, cx, norm))
            if cx <= int(roi_width * self._inbound_x_ratio_max):
                left_side_candidates.append((cy, norm))

        if left_side_candidates:
            return left_side_candidates

        # Some WeChat UI variants render inbound bubbles beyond the configured
        # x-ratio threshold. Fallback to all visible message candidates and let
        # outbound-echo suppression + dedup handle self messages.
        fallback_candidates = [(cy, norm) for cy, _cx, norm in all_candidates]
        if fallback_candidates:
            now_mono = time.monotonic()
            if (now_mono - self._last_ocr_side_fallback_diag_at) >= 12.0:
                self._last_ocr_side_fallback_diag_at = now_mono
                logger.info(
                    "ocr inbound side-fallback: no left-side rows; using all candidates=%d x_max=%.2f",
                    len(fallback_candidates),
                    self._inbound_x_ratio_max,
                )
        return fallback_candidates

    def _extract_chat_list_previews_from_ocr(
        self,
        *,
        ocr_rows: list[tuple[int, int, str, float]],
        roi_height: int,
    ) -> list[tuple[str, str]]:
        filtered_rows: list[tuple[int, str]] = []
        for cy, _cx, text_value, score in ocr_rows:
            if score < self._ocr_min_score:
                continue
            norm = _normalize_message_text(text_value)
            if not norm:
                continue
            if len(norm) <= 1:
                continue
            if _is_probably_time_or_badge(norm):
                continue
            if self._is_probably_ui_noise_text(norm):
                continue
            filtered_rows.append((cy, norm))

        if len(filtered_rows) < 2:
            return []

        filtered_rows.sort(key=lambda x: x[0])
        line_gap = max(18, int(roi_height * 0.03))
        lines: list[tuple[int, list[str]]] = []
        for cy, text_value in filtered_rows:
            if (not lines) or abs(cy - lines[-1][0]) > line_gap:
                lines.append((cy, [text_value]))
                continue
            lines[-1][1].append(text_value)

        line_rows: list[tuple[int, str]] = []
        for y_value, texts in lines:
            if not texts:
                continue
            best = max(texts, key=lambda item: len(item))
            if not best:
                continue
            line_rows.append((y_value, best))

        if len(line_rows) < 2:
            return []

        row_gap_max = max(56, int(roi_height * 0.16))
        pairs: list[tuple[int, str, str]] = []
        for idx in range(len(line_rows) - 1):
            sender_y, sender = line_rows[idx]
            preview_y, preview = line_rows[idx + 1]
            gap = int(preview_y - sender_y)
            if gap <= 0 or gap > row_gap_max:
                continue
            if not self._is_probably_chat_sender(sender):
                continue
            if not self._is_probably_chat_preview(preview, sender=sender):
                continue
            pairs.append((preview_y, sender, preview))

        if not pairs:
            return []

        pairs.sort(key=lambda x: x[0])
        dedup: dict[str, tuple[int, str]] = {}
        for y_value, sender, preview in pairs:
            dedup[sender] = (y_value, preview)

        result: list[tuple[str, str]] = []
        for sender, (_y, preview) in dedup.items():
            result.append((sender, preview))
        return result

    def _is_probably_chat_sender(self, text_value: str) -> bool:
        value = _normalize_message_text(text_value)
        if not value:
            return False
        if len(value) > 48:
            return False
        if _is_probably_time_or_badge(value):
            return False
        if self._is_probably_ui_noise_text(value):
            return False
        return True

    def _is_probably_chat_preview(self, text_value: str, *, sender: str) -> bool:
        value = _normalize_message_text(text_value)
        if not value:
            return False
        if len(value) < 2:
            return False
        if value == _normalize_message_text(sender):
            return False
        if _is_probably_time_or_badge(value):
            return False
        if self._is_probably_ui_noise_text(value):
            return False
        return True

    @staticmethod
    def _is_probably_ui_noise_text(text_value: str) -> bool:
        value = _normalize_message_text(text_value)
        if not value:
            return True
        lowered = value.lower()
        if lowered in {
            "weixin",
            "wechat",
            "chat",
            "chats",
            "search",
            "messages",
            "message",
            "send",
            "voice call",
            "video call",
        }:
            return True
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return True
        if re.fullmatch(r"[A-Za-z]{1,3}\d{5,}", value):
            return True
        return False

    @staticmethod
    def _format_ocr_samples(ocr_rows: list[tuple[int, int, str, float]]) -> list[str]:
        samples: list[str] = []
        for cy, cx, text_value, score in ocr_rows[:8]:
            samples.append(f"({cy},{cx},{score:.2f}){_normalize_message_text(text_value)[:24]}")
        return samples

    def _resolve_message_roi(self, hwnd: int) -> tuple[int, int, int, int] | None:
        try:
            from pywinauto import Desktop
        except Exception:
            return None
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            rect = window.rectangle()
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
        except Exception:
            return None
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None

        # Right message panel body.
        roi_left = left + int(width * 0.34)
        roi_top = top + int(height * 0.12)
        roi_right = left + int(width * 0.97)
        roi_bottom = top + int(height * 0.90)
        roi_w = max(0, roi_right - roi_left)
        roi_h = max(0, roi_bottom - roi_top)
        return roi_left, roi_top, roi_w, roi_h

    def _resolve_chat_list_roi(self, hwnd: int) -> tuple[int, int, int, int] | None:
        try:
            from pywinauto import Desktop
        except Exception:
            return None
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            rect = window.rectangle()
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
        except Exception:
            return None

        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None

        # Left conversation list panel (excluding sidebar and title/search areas).
        roi_left = left + int(width * 0.06)
        roi_top = top + int(height * 0.10)
        roi_right = left + int(width * 0.43)
        roi_bottom = top + int(height * 0.88)
        roi_w = max(0, roi_right - roi_left)
        roi_h = max(0, roi_bottom - roi_top)
        return roi_left, roi_top, roi_w, roi_h

    def _capture_region_bgr(self, *, left: int, top: int, width: int, height: int) -> Any:
        pil_error: Exception | None = None
        try:
            from PIL import ImageGrab
            import numpy as np

            bbox = (int(left), int(top), int(left + width), int(top + height))
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            arr = np.array(image)
            if arr.ndim == 3 and arr.shape[2] >= 3:
                # RGB -> BGR
                return arr[:, :, :3][:, :, ::-1].copy()
        except Exception as exc:
            pil_error = exc

        try:
            import mss
            import numpy as np
        except Exception as exc:
            if not self._ocr_disabled:
                detail = str(exc)
                if pil_error is not None:
                    detail = f"pil={pil_error}; mss={exc}"
                logger.warning("ocr capture dependency missing: %s", detail)
                self._ocr_disabled = True
            return None

        monitor = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        try:
            with mss.mss() as sct:
                shot = sct.grab(monitor)
            arr = np.array(shot)
            if arr.ndim != 3 or arr.shape[2] < 3:
                return None
            # BGRA -> BGR
            return arr[:, :, :3].copy()
        except Exception:
            return None

    def _ensure_ocr_engine(self) -> Any:
        if self._ocr_disabled:
            return None
        if self._ocr_engine is not None:
            return self._ocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:
            logger.warning("rapidocr unavailable for inbound ocr: %s", exc)
            self._ocr_disabled = True
            return None
        try:
            self._ocr_engine = RapidOCR()
            logger.info("ocr inbound engine initialized: rapidocr_onnxruntime")
            return self._ocr_engine
        except Exception as exc:
            logger.warning("rapidocr init failed: %s", exc)
            self._ocr_disabled = True
            return None

    def _run_ocr(self, image_bgr: Any) -> list[tuple[int, int, str, float]]:
        engine = self._ensure_ocr_engine()
        if engine is None:
            return []
        try:
            result, _ = engine(image_bgr)
        except Exception as exc:
            logger.warning("ocr run failed: %s", exc)
            return []
        if not result:
            return []

        rows: list[tuple[int, int, str, float]] = []
        for item in result:
            try:
                box = item[0]
                text_value = str(item[1] or "")
                score = float(item[2] or 0.0)
                points = [(int(p[0]), int(p[1])) for p in box]
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                cx = int(sum(xs) / max(1, len(xs)))
                cy = int(sum(ys) / max(1, len(ys)))
            except Exception:
                continue
            rows.append((cy, cx, text_value, score))
        rows.sort(key=lambda x: (x[0], x[1]))
        return rows

    def _infer_active_chat_sender(self, *, hwnd: int) -> str:
        # Prefer UIA header text when available.
        try:
            from pywinauto import Desktop
            window = Desktop(backend="uia").window(handle=hwnd)
            rect = window.rectangle()
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
            width = right - left
            height = bottom - top
            title_candidates: list[tuple[int, str]] = []
            for node in window.descendants(control_type="Text"):
                try:
                    text_value = _normalize_message_text(node.window_text())
                    if not text_value:
                        continue
                    node_rect = node.rectangle()
                    cx = int((int(node_rect.left) + int(node_rect.right)) / 2)
                    cy = int((int(node_rect.top) + int(node_rect.bottom)) / 2)
                except Exception:
                    continue
                if cx < (left + int(width * 0.42)):
                    continue
                if cy > (top + int(height * 0.18)):
                    continue
                if _is_probably_time_or_badge(text_value):
                    continue
                if text_value in {"聊天信息", "搜索"}:
                    continue
                title_candidates.append((len(text_value), text_value))
            if title_candidates:
                title_candidates.sort(key=lambda x: x[0], reverse=True)
                return _normalize_message_text(title_candidates[0][1]) or self._last_ocr_sender
        except Exception:
            pass
        # Fallback: inspect Win32 child titles in case UIA header is opaque.
        try:
            child_rows = _enum_child_windows(hwnd)
            fallback_titles: list[tuple[int, str]] = []
            for _child_hwnd, _class_name, title in child_rows:
                title_value = _normalize_message_text(title)
                if not title_value:
                    continue
                if title_value in WECHAT_SYSTEM_LIST_TITLES:
                    continue
                if title_value in {"聊天信息", "搜索", "搜一搜"}:
                    continue
                if _is_probably_time_or_badge(title_value):
                    continue
                if title_value in {"微信", "WeChat", "Weixin"}:
                    continue
                fallback_titles.append((len(title_value), title_value))
            if fallback_titles:
                fallback_titles.sort(key=lambda x: x[0], reverse=True)
                resolved = _normalize_message_text(fallback_titles[0][1])
                if resolved:
                    self._last_ocr_sender = resolved
                    return resolved
        except Exception:
            pass
        return self._last_ocr_sender


class WeChatVisionInboundBridge(WeChatUiOcrInboundBridge):
    """Inbound bridge: screenshot + Ollama vision model extraction."""

    def __init__(
        self,
        *,
        edge_url: str,
        enabled: bool,
        poll_interval_s: float,
        callback_timeout_s: float,
        dedup_window_s: float,
        scan_chat_list: bool,
        force_active_chat_target: bool,
        active_inbound_x_ratio_max: float,
        vision_model: str,
        vision_api_url: str,
        vision_timeout_s: float,
        vision_min_confidence: float,
        vision_min_interval_s: float,
    ) -> None:
        super().__init__(
            edge_url=edge_url,
            enabled=enabled,
            poll_interval_s=poll_interval_s,
            callback_timeout_s=callback_timeout_s,
            dedup_window_s=dedup_window_s,
            ocr_min_score=0.45,
            inbound_x_ratio_max=0.62,
            scan_chat_list=scan_chat_list,
            force_active_chat_target=force_active_chat_target,
            active_inbound_x_ratio_max=active_inbound_x_ratio_max,
        )
        self._vision_model = str(vision_model or DEFAULT_VISION_MODEL).strip() or DEFAULT_VISION_MODEL
        self._vision_api_url = str(vision_api_url or DEFAULT_VISION_API_URL).strip() or DEFAULT_VISION_API_URL
        self._vision_timeout_s = max(1.0, float(vision_timeout_s))
        self._vision_min_confidence = max(0.05, min(0.99, float(vision_min_confidence)))
        self._vision_min_interval_s = max(0.6, float(vision_min_interval_s))
        self._next_vision_call_at = 0.0
        self._vision_unavailable = False
        self._vision_fail_streak = 0
        self._last_vision_diag_at = 0.0

    def _collect_chat_previews(self) -> list[tuple[str, str]]:
        hwnd = _find_wechat_main_window()
        if not hwnd:
            return []

        if self._vision_unavailable:
            return super()._collect_chat_previews()

        now_mono = time.monotonic()
        if now_mono < self._next_vision_call_at:
            return []
        self._next_vision_call_at = now_mono + self._vision_min_interval_s

        roi = self._resolve_message_roi(hwnd)
        if roi is None:
            return []
        left, top, width, height = roi
        if width <= 20 or height <= 20:
            return []

        image = self._capture_region_bgr(left=left, top=top, width=width, height=height)
        if image is None:
            return []

        extracted = self._infer_latest_inbound_via_vision(image_bgr=image, hwnd=hwnd)
        if extracted:
            self._vision_fail_streak = 0
            return [extracted]

        self._vision_fail_streak += 1
        # Only degrade when vision model is unavailable (e.g. not pulled).
        # For timeout/slow inference, keep vision-only to avoid OCR noise loops.
        if self._vision_unavailable and self._vision_fail_streak >= 2:
            return super()._collect_chat_previews()
        return []

    def _infer_latest_inbound_via_vision(
        self,
        *,
        image_bgr: Any,
        hwnd: int,
    ) -> tuple[str, str] | None:
        image_b64 = self._encode_png_base64(image_bgr=image_bgr)
        if not image_b64:
            return None

        prompt = (
            "你在读取微信PC端当前会话截图。"
            "请只返回严格JSON对象，不要markdown、不要解释。"
            "字段: inbound_text(字符串), confidence(0到1数字)。"
            "inbound_text要求: 当前会话中最新一条'对方发来的文本消息'；"
            "如果无法确定则返回空字符串。"
        )
        # Prefer ASCII prompt to avoid mojibake/encoding issues in runtime environments.
        prompt = (
            "You are reading a screenshot of a WeChat desktop chat panel. "
            "Return strict JSON only, no markdown and no explanation. "
            "Fields: inbound_text (string), confidence (number 0-1). "
            "inbound_text must be the latest inbound text message from the other side "
            "in the current active conversation. "
            "If uncertain or no readable inbound message exists, return empty inbound_text."
        )
        payload = {
            "model": self._vision_model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
            "options": {"temperature": 0},
        }
        try:
            req = urllib.request.Request(
                url=self._vision_api_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._vision_timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception as exc:
            detail = str(exc)
            if "model" in detail.lower() and "not found" in detail.lower():
                self._vision_unavailable = True
                logger.warning(
                    "vision inbound model unavailable (%s); fallback to ocr/uia",
                    detail,
                )
            else:
                logger.warning("vision inbound request failed: %s", detail)
            return None

        raw_content = ""
        if isinstance(body, dict):
            message_obj = body.get("message")
            if isinstance(message_obj, dict):
                raw_content = str(message_obj.get("content") or "")
            elif "response" in body:
                raw_content = str(body.get("response") or "")
            elif "content" in body:
                raw_content = str(body.get("content") or "")
        parsed = self._parse_vision_json(raw_content)
        inbound_text = _normalize_message_text(str(parsed.get("inbound_text") or parsed.get("text") or ""))
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0

        if not inbound_text:
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                raw_preview = _normalize_message_text(raw_content)[:120]
                logger.info("vision inbound empty result: raw=%.120s", raw_preview)
            return None
        if confidence < self._vision_min_confidence:
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info(
                    "vision inbound low confidence: conf=%.2f threshold=%.2f text=%.80s",
                    confidence,
                    self._vision_min_confidence,
                    inbound_text,
                )
            return None
        if _is_recent_outbound_echo(inbound_text):
            return None

        sender = "active_chat" if self._force_active_chat_target else self._infer_active_chat_sender(hwnd=hwnd)
        sender_value = _normalize_message_text(sender) or "active_chat"
        return sender_value, inbound_text

    @staticmethod
    def _encode_png_base64(*, image_bgr: Any) -> str:
        try:
            import numpy as np
            from PIL import Image
        except Exception:
            return ""
        try:
            arr = np.asarray(image_bgr)
            if arr.ndim != 3 or arr.shape[2] < 3:
                return ""
            rgb = arr[:, :, :3][:, :, ::-1]
            image = Image.fromarray(rgb)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""

    @staticmethod
    def _parse_vision_json(raw_content: str) -> dict[str, Any]:
        text = str(raw_content or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

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


def _list_wechat_client_pids() -> set[int]:
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

    pids: set[int] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('"'):
            parts = [part.strip('"') for part in line.split('","')]
        else:
            parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        process_name = str(parts[0] or "").strip().lower()
        if process_name not in WECHAT_PROCESS_NAMES:
            continue
        try:
            pids.add(int(str(parts[1]).strip()))
        except Exception:
            continue
    return pids


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
    windows = _enum_visible_windows()
    best_hint_hwnd = 0
    best_hint_area = 0
    for hwnd, class_name, title in windows:
        class_key = str(class_name or "").strip().lower()
        title_text = str(title or "").strip()
        if not (
            class_key in WECHAT_MAIN_WINDOW_CLASS_HINTS
            or (title_text and any(hint in title_text for hint in WECHAT_WINDOW_TITLE_HINTS))
        ):
            continue
        rect = _get_window_rect(int(hwnd))
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = max(0, int(right) - int(left))
        height = max(0, int(bottom) - int(top))
        area = int(width * height)
        if width < 280 or height < 220:
            continue
        if area > best_hint_area:
            best_hint_area = area
            best_hint_hwnd = int(hwnd)
    if best_hint_hwnd:
        return int(best_hint_hwnd)
    # Fallback for new client variants: match top-level visible windows by WeChat process pid.
    try:
        wechat_pids = _list_wechat_client_pids()
        if wechat_pids:
            user32 = ctypes.windll.user32
            pid_ref = ctypes.c_ulong(0)
            best_pid_hwnd = 0
            best_pid_area = 0
            for hwnd, _class_name, _title in windows:
                pid_ref.value = 0
                try:
                    user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid_ref))
                except Exception:
                    continue
                if int(pid_ref.value or 0) not in wechat_pids:
                    continue
                rect = _get_window_rect(int(hwnd))
                if rect is None:
                    continue
                left, top, right, bottom = rect
                width = max(0, int(right) - int(left))
                height = max(0, int(bottom) - int(top))
                area = int(width * height)
                if width < 280 or height < 220:
                    continue
                if area > best_pid_area:
                    best_pid_area = area
                    best_pid_hwnd = int(hwnd)
            if best_pid_hwnd:
                return int(best_pid_hwnd)
    except Exception:
        pass
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

    # Prefer UIA editor focus when control tree is available.
    try:
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=hwnd)
        editor_candidates: list[tuple[int, Any]] = []
        for node in window.descendants(control_type="Edit"):
            try:
                node_rect = node.rectangle()
                cx = int((int(node_rect.left) + int(node_rect.right)) / 2)
                cy = int((int(node_rect.top) + int(node_rect.bottom)) / 2)
            except Exception:
                continue
            if cx <= (left + int(width * 0.45)):
                continue
            if cy <= (top + int(height * 0.55)):
                continue
            editor_candidates.append((cy, node))
        if editor_candidates:
            editor_candidates.sort(key=lambda row: row[0], reverse=True)
            editor_candidates[0][1].click_input()
            time.sleep(0.10)
            return True, "message_input_uia_edit"
    except Exception:
        pass

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


def _target_text_matches(candidate: str, target: str) -> bool:
    target_key = _normalize_target_hint(target)
    candidate_key = _normalize_target_hint(candidate)
    if not target_key or not candidate_key:
        return False
    if target_key == candidate_key:
        return True
    if target_key in candidate_key:
        return True
    if candidate_key in target_key and min(len(candidate_key), len(target_key)) >= 2:
        return True
    return False


def _try_open_chat_by_list_click(target: str) -> tuple[bool, str]:
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return False, f"pywinauto import failed: {exc}"

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"
    try:
        window = Desktop(backend="uia").window(handle=hwnd)
        rect = window.rectangle()
        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)
        width = max(0, right - left)
        height = max(0, bottom - top)
    except Exception as exc:
        return False, f"bind window failed: {exc}"

    if width <= 0 or height <= 0:
        return False, "invalid window rect"

    exact_candidates: list[tuple[int, Any, str]] = []
    fuzzy_candidates: list[tuple[int, Any, str]] = []
    try:
        list_items = window.descendants(control_type="ListItem")
    except Exception as exc:
        return False, f"list item enumerate failed: {exc}"

    for item in list_items[:160]:
        try:
            item_rect = item.rectangle()
            cx = int((int(item_rect.left) + int(item_rect.right)) / 2)
            cy = int((int(item_rect.top) + int(item_rect.bottom)) / 2)
        except Exception:
            continue
        if cx > (left + int(width * 0.45)):
            continue
        if cy <= (top + int(height * 0.10)) or cy >= (top + int(height * 0.92)):
            continue

        texts = _extract_element_text_candidates(item)
        if not texts:
            continue

        best_text = ""
        exact_hit = False
        fuzzy_hit = False
        for text_value in texts:
            if not text_value:
                continue
            if _is_probably_time_or_badge(text_value):
                continue
            if text_value in WECHAT_SYSTEM_LIST_TITLES:
                continue
            if _target_text_matches(text_value, target):
                best_text = text_value
                if _normalize_target_hint(text_value) == _normalize_target_hint(target):
                    exact_hit = True
                else:
                    fuzzy_hit = True
                break
            if not best_text:
                best_text = text_value

        if exact_hit:
            exact_candidates.append((cy, item, best_text))
        elif fuzzy_hit:
            fuzzy_candidates.append((cy, item, best_text))

    candidate_rows = exact_candidates if exact_candidates else fuzzy_candidates
    if not candidate_rows:
        return False, f"chat list no match for target={target}"
    candidate_rows.sort(key=lambda row: row[0])
    _, selected_item, selected_text = candidate_rows[0]
    try:
        selected_item.click_input()
        time.sleep(0.22)
        return True, f"open_chat_by_list_click target={target} matched={selected_text}"
    except Exception as exc:
        return False, f"list_click failed: {exc.__class__.__name__}: {exc}"


def _open_chat_by_target(target: str, wechat_path: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    ok, detail = _focus_wechat_main_window(wechat_path)
    if not ok:
        return False, detail
    if _is_active_chat_target(target):
        return True, f"{detail}; active_chat_target"

    list_ok, list_detail = _try_open_chat_by_list_click(target=target)
    if list_ok:
        return True, f"{detail}; {list_detail}"

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
        return True, f"{detail}; {primary_detail}; list_click={list_detail}"

    fallback_ok, fallback_detail = _try_open_with_hotkey("f")
    if fallback_ok:
        return True, f"{detail}; {fallback_detail}; list_click={list_detail}"
    return False, f"open_chat failed: list={list_detail}; primary={primary_detail}; fallback={fallback_detail}"


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
        help="Enable inbound listener (UIA hook / notification poll).",
    )
    parser.add_argument(
        "--inbound-backend",
        default=(os.environ.get("EDGE_WECHAT_INBOUND_BACKEND") or DEFAULT_INBOUND_BACKEND).strip(),
        help=f"Inbound backend: uia_hook|uia_ocr|vision|win_notify|off (default: {DEFAULT_INBOUND_BACKEND}).",
    )
    parser.add_argument(
        "--uia-scan-chat-list",
        default=(os.environ.get("EDGE_WECHAT_UIA_SCAN_CHAT_LIST") or "0").strip(),
        help="UIA inbound: include left chat-list scanning (default: 0).",
    )
    parser.add_argument(
        "--force-active-chat-target",
        default=(os.environ.get("EDGE_WECHAT_FORCE_ACTIVE_CHAT_TARGET") or "1").strip(),
        help="UIA inbound: force sender/session_id to active_chat (default: 1).",
    )
    parser.add_argument(
        "--uia-active-inbound-x-ratio-max",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_UIA_ACTIVE_INBOUND_X_RATIO_MAX") or "0.70"),
        help="UIA active-chat X ratio max for inbound-side text (default: 0.70).",
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
    parser.add_argument(
        "--notify-db-path",
        default=(os.environ.get("EDGE_WECHAT_NOTIFY_DB_PATH") or DEFAULT_NOTIFY_DB_PATH).strip(),
        help=f"Windows notification DB path (default: {DEFAULT_NOTIFY_DB_PATH}).",
    )
    parser.add_argument(
        "--notify-handler-keywords",
        default=(os.environ.get("EDGE_WECHAT_NOTIFY_HANDLER_KEYWORDS") or ",".join(DEFAULT_NOTIFY_HANDLER_KEYWORDS)).strip(),
        help="Comma-separated keywords for NotificationHandler.PrimaryId matching.",
    )
    parser.add_argument(
        "--ocr-min-score",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_OCR_MIN_SCORE") or "0.45"),
        help="OCR minimum score threshold for inbound extraction.",
    )
    parser.add_argument(
        "--ocr-inbound-x-ratio-max",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_OCR_INBOUND_X_RATIO_MAX") or "0.62"),
        help="Right-panel X ratio max for inbound bubble candidates (left side only).",
    )
    parser.add_argument(
        "--vision-model",
        default=(os.environ.get("EDGE_WECHAT_VISION_MODEL") or DEFAULT_VISION_MODEL).strip(),
        help=f"Ollama vision model id (default: {DEFAULT_VISION_MODEL}).",
    )
    parser.add_argument(
        "--vision-api-url",
        default=(os.environ.get("EDGE_WECHAT_VISION_API_URL") or DEFAULT_VISION_API_URL).strip(),
        help=f"Ollama chat API URL (default: {DEFAULT_VISION_API_URL}).",
    )
    parser.add_argument(
        "--vision-timeout",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_VISION_TIMEOUT_S") or DEFAULT_VISION_TIMEOUT_S),
        help=f"Ollama vision request timeout in seconds (default: {DEFAULT_VISION_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--vision-min-confidence",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_VISION_MIN_CONFIDENCE") or DEFAULT_VISION_MIN_CONFIDENCE),
        help=f"Minimum confidence accepted from vision output (default: {DEFAULT_VISION_MIN_CONFIDENCE}).",
    )
    parser.add_argument(
        "--vision-min-interval",
        type=float,
        default=float(os.environ.get("EDGE_WECHAT_VISION_MIN_INTERVAL_S") or DEFAULT_VISION_MIN_INTERVAL_S),
        help=f"Minimum interval between vision calls in seconds (default: {DEFAULT_VISION_MIN_INTERVAL_S}).",
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
    elif inbound_backend not in {"uia_hook", "uia_ocr", "vision", "win_notify"}:
        logger.warning("invalid inbound backend %s, fallback to uia_hook", inbound_backend)
        inbound_backend = "uia_hook"
    uia_scan_chat_list = _parse_bool(args.uia_scan_chat_list, default=DEFAULT_UIA_SCAN_CHAT_LIST)
    force_active_chat_target = _parse_bool(
        args.force_active_chat_target,
        default=DEFAULT_FORCE_ACTIVE_CHAT_TARGET,
    )
    uia_active_inbound_x_ratio_max = max(0.45, min(0.90, float(args.uia_active_inbound_x_ratio_max)))
    vision_model = str(args.vision_model or DEFAULT_VISION_MODEL).strip() or DEFAULT_VISION_MODEL
    vision_api_url = str(args.vision_api_url or DEFAULT_VISION_API_URL).strip() or DEFAULT_VISION_API_URL
    vision_timeout_s = max(1.0, float(args.vision_timeout))
    vision_min_confidence = max(0.05, min(0.99, float(args.vision_min_confidence)))
    vision_min_interval_s = max(0.6, float(args.vision_min_interval))
    notify_keywords = [
        part.strip().lower()
        for part in str(args.notify_handler_keywords or "").split(",")
        if part.strip()
    ]
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
        (
            "uia bridge started edge_url=%s queue=%s result=%s wechat_path=%s "
            "listener_enabled=%s inbound_backend=%s scan_chat_list=%s "
            "force_active_chat_target=%s active_x_max=%.2f vision_model=%s"
        ),
        args.edge_url,
        queue_file,
        result_file,
        wechat_path or "(auto)",
        listener_enabled,
        inbound_backend,
        uia_scan_chat_list,
        force_active_chat_target,
        uia_active_inbound_x_ratio_max,
        vision_model,
    )

    if inbound_backend == "win_notify":
        inbound_bridge = WeChatWindowsNotificationInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
            db_path=str(args.notify_db_path or DEFAULT_NOTIFY_DB_PATH),
            handler_keywords=notify_keywords,
        )
    elif inbound_backend == "vision":
        inbound_bridge = WeChatVisionInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
            scan_chat_list=uia_scan_chat_list,
            force_active_chat_target=force_active_chat_target,
            active_inbound_x_ratio_max=uia_active_inbound_x_ratio_max,
            vision_model=vision_model,
            vision_api_url=vision_api_url,
            vision_timeout_s=vision_timeout_s,
            vision_min_confidence=vision_min_confidence,
            vision_min_interval_s=vision_min_interval_s,
        )
    elif inbound_backend == "uia_ocr":
        inbound_bridge = WeChatUiOcrInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
            ocr_min_score=float(args.ocr_min_score),
            inbound_x_ratio_max=float(args.ocr_inbound_x_ratio_max),
            scan_chat_list=uia_scan_chat_list,
            force_active_chat_target=force_active_chat_target,
            active_inbound_x_ratio_max=uia_active_inbound_x_ratio_max,
        )
    else:
        inbound_bridge = WeChatUiInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
            scan_chat_list=uia_scan_chat_list,
            force_active_chat_target=force_active_chat_target,
            active_inbound_x_ratio_max=uia_active_inbound_x_ratio_max,
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
