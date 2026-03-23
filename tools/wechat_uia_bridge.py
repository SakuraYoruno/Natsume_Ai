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
DEFAULT_UIA_AUTO_RECOVER = False
DEFAULT_VISION_MODEL = "gemma3:4b"
DEFAULT_VISION_API_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_VISION_TIMEOUT_S = 30.0
DEFAULT_VISION_MIN_CONFIDENCE = 0.50
DEFAULT_VISION_MIN_INTERVAL_S = 1.8
DEFAULT_OUTBOUND_ECHO_TTL_S = 180.0
DEFAULT_TRAY_WAKE_ENABLED = True
DEFAULT_TRAY_WAKE_ON_INBOUND = True
DEFAULT_TRAY_WAKE_COOLDOWN_S = 2.2
DEFAULT_TRAY_SCAN_FRAMES = 4
DEFAULT_TRAY_SCAN_FRAME_INTERVAL_S = 0.10
DEFAULT_TRAY_SCAN_BOX_W = 140
DEFAULT_TRAY_SCAN_BOX_H = 84
DEFAULT_TRAY_ANCHOR_X_RATIO = 0.956
DEFAULT_TRAY_ANCHOR_Y_RATIO = 0.985
DEFAULT_TRAY_SCAN_MIN_MOTION = 6.0
DEFAULT_TRAY_SCAN_MIN_SCORE = 20.0
DEFAULT_TRAY_SCAN_GREEN_WEIGHT = 0.33
DEFAULT_TRAY_WAKE_REQUIRE_BLINK = True
DEFAULT_TRAY_WAKE_DOUBLE_CLICK = True
DEFAULT_TRAY_WAKE_WAIT_S = 3.2
DEFAULT_NOTIFY_DB_PATH = r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db"
DEFAULT_NOTIFY_HANDLER_KEYWORDS = ("weixin", "wechat", "tencent", "微信")
DEFAULT_NOTIFY_PAYLOAD_KEYWORDS = ("微信", "weixin", "wechat", "tencent")
FILETIME_1601_TO_1970 = 116444736000000000
WECHAT_PROCESS_NAMES = {
    "wechat.exe",
    "weixin.exe",
    "wechatappex.exe",
    "wechatwin.exe",
}
WECHAT_MAIN_WINDOW_CLASS_HINTS = {
    "wechatmainwndforpc",
    "weixinmainwndforpc",
    "weixinmainwnd",
}
WECHAT_MAIN_WINDOW_CLASS_EXCLUDES = {
    "wxtrayiconmessagewindow",
    "qt51514wxtrayiconmessagewindowclass",
}
WECHAT_MAIN_WINDOW_TITLE_EXCLUDES = {
    "wxtrayiconmessagewindow",
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
WECHAT_BLOCKED_CHAT_HINTS = (
    "公众号",
    "订阅号",
    "服务通知",
    "微信团队",
    "微信支付",
    "微信助手",
    "文件传输助手",
    "filehelper",
)
DEFAULT_ALLOWED_CONTACT_NAME = ""

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


def _decode_unicode_escape_text(raw_text: str) -> str:
    text_value = str(raw_text or "").strip()
    if not text_value:
        return ""
    if "\\u" not in text_value and "\\U" not in text_value:
        return text_value
    try:
        def _replace(match: re.Match[str]) -> str:
            code = str(match.group(1) or "")
            try:
                return chr(int(code, 16))
            except Exception:
                return match.group(0)

        decoded = re.sub(r"\\u([0-9a-fA-F]{4})", _replace, text_value)
        decoded = re.sub(r"\\U([0-9a-fA-F]{8})", _replace, decoded)
        return str(decoded or text_value)
    except Exception:
        return text_value


def _resolve_allowed_contact_names() -> list[str]:
    raw = str(os.environ.get("EDGE_WECHAT_ALLOWED_CONTACTS") or "").strip()
    if not raw:
        raw = str(os.environ.get("EDGE_WECHAT_ALLOWED_CONTACTS_UNICODE") or "").strip()
    if not raw:
        raw = DEFAULT_ALLOWED_CONTACT_NAME
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\n;|]", raw):
        text_value = _decode_unicode_escape_text(str(part or "").strip())
        if not text_value:
            continue
        key = _normalize_target_hint(text_value) or text_value.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(text_value)
    return names


def _is_blocked_chat_target(target: str) -> bool:
    normalized = _normalize_target_hint(target)
    if not normalized:
        return False
    for active_hint in ACTIVE_CHAT_TARGET_HINTS:
        active_key = _normalize_target_hint(active_hint)
        if active_key and normalized == active_key:
            return True
    for blocked in WECHAT_BLOCKED_CHAT_HINTS:
        blocked_key = _normalize_target_hint(blocked)
        if blocked_key and blocked_key in normalized:
            return True
    return False


def _is_allowed_contact_target(target: str) -> bool:
    allowed = _resolve_allowed_contact_names()
    if not allowed:
        return True
    normalized = _normalize_target_hint(target)
    if not normalized:
        return False
    for name in allowed:
        allow_key = _normalize_target_hint(name)
        if allow_key and allow_key == normalized:
            return True
    return False


def _is_placeholder_sender_name(target: str) -> bool:
    value = _normalize_message_text(target)
    if not value:
        return True
    if "�" in value:
        return True
    compact = re.sub(r"[?？\s\.\-_:：·]+", "", value)
    if not compact:
        return True
    lower = compact.lower()
    if lower in {"unknown", "unk", "none", "null", "na", "n/a"}:
        return True
    return False


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


def _remember_outbound_text(text: str, *, ttl_s: float = DEFAULT_OUTBOUND_ECHO_TTL_S) -> None:
    key = _normalize_message_text(text)
    if not key:
        return
    now_mono = time.monotonic()
    with _RECENT_OUTBOUND_LOCK:
        stale = [k for k, t in _RECENT_OUTBOUND_TEXTS.items() if (now_mono - t) > ttl_s]
        for item in stale:
            _RECENT_OUTBOUND_TEXTS.pop(item, None)
        _RECENT_OUTBOUND_TEXTS[key] = now_mono


def _is_recent_outbound_echo(text: str, *, ttl_s: float = DEFAULT_OUTBOUND_ECHO_TTL_S) -> bool:
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

    def collect_inbound_events(self) -> list[dict[str, Any]]:
        """Collect deduplicated inbound events from Windows notifications without posting callback."""
        if not self._enabled:
            return []
        now_mono = time.monotonic()
        if now_mono < self._next_poll_at:
            return []
        self._next_poll_at = now_mono + self._poll_interval_s

        try:
            max_id, events, handler_count = self._read_new_events_since(self._last_notification_id)
        except Exception as exc:
            self._stats["errors"] += 1
            if (now_mono - self._last_error_log_at) >= 12.0:
                self._last_error_log_at = now_mono
                logger.warning("notification inbound cycle failed: %s", exc)
            return []

        if handler_count <= 0 and (not events):
            if (now_mono - self._last_no_target_log_at) >= 20.0:
                self._last_no_target_log_at = now_mono
                logger.info(
                    "notification inbound: no target handlers matched keywords=%s",
                    ",".join(self._handler_keywords),
                )
            return []

        if not self._baseline_ready:
            self._last_notification_id = max(self._last_notification_id, int(max_id))
            self._baseline_ready = True
            logger.info(
                "notification inbound baseline initialized: max_id=%d handlers=%d",
                self._last_notification_id,
                handler_count,
            )
            return []

        ready_events: list[dict[str, Any]] = []
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
            ready_events.append(
                {
                    "notification_id": notification_id,
                    "sender": sender,
                    "content": content,
                    "ts_ms": int(event.get("ts_ms") or 0),
                }
            )

        if (now_mono - self._last_heartbeat_at) >= 15.0:
            self._last_heartbeat_at = now_mono
            logger.info(
                "notification inbound heartbeat: max_id=%d handlers=%d scanned=%d",
                self._last_notification_id,
                handler_count,
                len(events),
            )
        return ready_events

    def forward_events(self, events: list[dict[str, Any]], *, bridge_backend: str = "win_notify") -> int:
        """Forward collected notification events to edge callback."""
        forwarded = 0
        backend = str(bridge_backend or "win_notify").strip() or "win_notify"
        for event in events:
            notification_id = int(event.get("notification_id") or 0)
            sender = str(event.get("sender") or "").strip()
            content = _normalize_message_text(str(event.get("content") or ""))
            if not sender or not content:
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
                "bridge_backend": backend,
                "roomid": "",
            }
            ok = _post_to_edge_callback(
                edge_url=self._edge_url,
                body=body,
                timeout_s=self._callback_timeout_s,
            )
            if not ok:
                continue
            forwarded += 1
            self._stats["messages_forwarded"] += 1
            logger.info(
                "notification inbound forwarded sender=%s msg_id=%s backend=%s content=%.80s",
                sender,
                msg_id,
                backend,
                content,
            )
        return forwarded

    def process_cycle(self) -> int:
        events = self.collect_inbound_events()
        if not events:
            return 0
        return int(self.forward_events(events, bridge_backend="win_notify") or 0)

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
                    require_wechat_hint = False
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
        self._empty_preview_cycles = 0
        self._auto_recover_enabled = _parse_bool(
            os.environ.get("EDGE_WECHAT_UIA_AUTO_RECOVER"),
            default=DEFAULT_UIA_AUTO_RECOVER,
        )
        self._last_auto_recover_at = 0.0
        self._auto_recover_interval_s = 8.0
        self._last_preview_by_chat: dict[str, str] = {}
        self._recent_forwarded: dict[str, float] = {}
        self._last_sender_hint = ""
        self._last_sender_fallback_log_at = 0.0
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
            if previews:
                self._empty_preview_cycles = 0
            else:
                self._empty_preview_cycles += 1
            if (not previews) and ((now_mono - self._last_diag_log_at) >= 12.0):
                self._last_diag_log_at = now_mono
                logger.info(
                    "inbound listener heartbeat: events=%d previews=%d baseline=%s",
                    int(self._stats.get("events") or 0),
                    len(previews),
                    self._baseline_ready,
                )
            if (
                self._auto_recover_enabled
                and (not previews)
                and ((now_mono - self._last_auto_recover_at) >= self._auto_recover_interval_s)
            ):
                self._last_auto_recover_at = now_mono
                self._try_recover_chat_surface()
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
                raw_sender_value = str(sender or "").strip()
                preview_value = _normalize_message_text(preview)
                if not preview_value:
                    continue
                sender_value = self._resolve_sender_for_forward(
                    sender=raw_sender_value,
                    preview=preview_value,
                )
                if not sender_value:
                    continue
                sender_is_active_target = _is_active_chat_target(sender_value)
                if _is_blocked_chat_target(sender_value) and (not sender_is_active_target):
                    continue
                if (not sender_is_active_target) and (not _is_allowed_contact_target(sender_value)):
                    continue
                if not _is_active_chat_target(sender_value):
                    self._last_sender_hint = sender_value

                last_value = self._last_preview_by_chat.get(sender_value)
                if last_value is None and raw_sender_value and raw_sender_value != sender_value:
                    last_value = self._last_preview_by_chat.get(raw_sender_value)
                if last_value == preview_value:
                    continue
                self._last_preview_by_chat[sender_value] = preview_value
                if raw_sender_value and raw_sender_value != sender_value:
                    self._last_preview_by_chat[raw_sender_value] = preview_value

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

    def _try_recover_chat_surface(self) -> None:
        if not self._auto_recover_enabled:
            return
        try:
            focus_ok, focus_detail = _focus_wechat_main_window(wechat_path="")
            hwnd = _find_wechat_main_window()
            if not hwnd:
                logger.info(
                    "inbound auto recover: focus_ok=%s detail=%s hwnd=0x0",
                    focus_ok,
                    focus_detail,
                )
                return
            anchor_ok, anchor_detail = _focus_chat_record_list(hwnd=hwnd)
            logger.info(
                "inbound auto recover: focus_ok=%s anchor_ok=%s detail=%s",
                focus_ok,
                anchor_ok,
                f"{focus_detail}; {anchor_detail}",
            )
        except Exception as exc:
            logger.info("inbound auto recover failed: %s", exc)

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

    def _resolve_sender_for_forward(self, *, sender: str, preview: str) -> str:
        sender_value = _normalize_message_text(sender)
        if sender_value and (not _is_blocked_chat_target(sender_value)) and (not _is_placeholder_sender_name(sender_value)):
            return sender_value
        if sender_value and (not _is_active_chat_target(sender_value)) and (not _is_placeholder_sender_name(sender_value)):
            return ""

        allowed_names = [str(item or "").strip() for item in _resolve_allowed_contact_names() if str(item or "").strip()]
        fallback_candidates: list[str] = []
        if len(allowed_names) == 1:
            fallback_candidates.append(allowed_names[0])
        if self._last_sender_hint:
            fallback_candidates.append(self._last_sender_hint)
        fallback_candidates.append("active_chat")
        fallback_candidates.append("wechat_inbound")

        for candidate in fallback_candidates:
            candidate_value = _normalize_message_text(candidate)
            if not candidate_value:
                continue
            if _is_placeholder_sender_name(candidate_value):
                continue
            if _is_blocked_chat_target(candidate_value):
                if not _is_active_chat_target(candidate_value):
                    continue
            if allowed_names and (not _is_allowed_contact_target(candidate_value)) and (not _is_active_chat_target(candidate_value)):
                continue
            now_mono = time.monotonic()
            if (now_mono - self._last_sender_fallback_log_at) >= 8.0:
                self._last_sender_fallback_log_at = now_mono
                logger.info(
                    "inbound sender fallback applied: raw_sender=%s resolved_sender=%s preview=%.80s",
                    sender_value or "(empty)",
                    candidate_value,
                    preview,
                )
            return candidate_value
        return ""

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
        self._allow_uia_fallback = _parse_bool(
            os.environ.get("EDGE_WECHAT_OCR_ALLOW_UIA_FALLBACK"),
            default=False,
        )

    def _collect_chat_previews(self) -> list[tuple[str, str]]:
        # OCR mode must prioritize OCR extraction to avoid UIA text noise loops.
        previews = self._collect_chat_previews_via_ocr()
        if previews:
            self._ocr_empty_cycles = 0
            return previews
        self._ocr_empty_cycles += 1
        if self._ocr_disabled or self._ocr_empty_cycles >= 4:
            if not self._allow_uia_fallback:
                return []
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
            now_mono = time.monotonic()
            if self._auto_recover_enabled and ((now_mono - self._last_focus_retry_at) >= 20.0):
                self._last_focus_retry_at = now_mono
                focused, focus_detail = _focus_wechat_main_window(wechat_path="")
                if focused:
                    time.sleep(0.18)
                    hwnd = _find_wechat_main_window()
                else:
                    logger.info("ocr inbound no hwnd focus retry skipped: %s", focus_detail)
            if not hwnd:
                return []

        if not hwnd:
            return []

        right_ocr_rows, list_ocr_rows, right_previews, list_previews = self._collect_ocr_from_rois(hwnd=hwnd)
        if right_previews:
            return right_previews
        if list_previews:
            return list_previews

        now_mono = time.monotonic()
        if (
            self._auto_recover_enabled
            and (not right_ocr_rows)
            and (not list_ocr_rows)
            and ((now_mono - self._last_focus_retry_at) >= 20.0)
        ):
            self._last_focus_retry_at = now_mono
            focused, focus_detail = _focus_wechat_main_window(wechat_path="")
            if focused:
                time.sleep(0.18)
                retry_hwnd = _find_wechat_main_window() or hwnd
                retry_right_rows, retry_list_rows, retry_right_previews, retry_list_previews = self._collect_ocr_from_rois(
                    hwnd=retry_hwnd
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
                image = self._capture_region_bgr(
                    left=r_left,
                    top=r_top,
                    width=r_width,
                    height=r_height,
                    hwnd=hwnd,
                )
                if image is not None:
                    right_ocr_rows = self._run_ocr(image)
                    inbound_candidates = self._extract_inbound_candidates_from_message_roi(
                        ocr_rows=right_ocr_rows,
                        roi_width=r_width,
                        roi_height=r_height,
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
                image = self._capture_region_bgr(
                    left=l_left,
                    top=l_top,
                    width=l_width,
                    height=l_height,
                    hwnd=hwnd,
                )
                if image is not None:
                    list_ocr_rows = self._run_ocr(image)
                    list_previews = self._extract_chat_list_previews_from_ocr(
                        ocr_rows=list_ocr_rows,
                        roi_height=l_height,
                    )
                    if (not list_previews) and list_ocr_rows:
                        list_previews = self._extract_allowed_contact_preview_from_ocr(
                            ocr_rows=list_ocr_rows
                        )
        return right_ocr_rows, list_ocr_rows, right_previews, list_previews

    def _extract_inbound_candidates_from_message_roi(
        self,
        *,
        ocr_rows: list[tuple[int, int, str, float]],
        roi_width: int,
        roi_height: int,
    ) -> list[tuple[int, str]]:
        left_side_candidates: list[tuple[int, str]] = []
        all_candidates: list[tuple[int, int, str]] = []
        top_ignore_y = max(6, int(roi_height * 0.06))
        bottom_ignore_y = max(top_ignore_y + 8, int(roi_height * 0.84))
        for cy, cx, text_value, score in ocr_rows:
            if score < self._ocr_min_score:
                continue
            if cy <= top_ignore_y:
                continue
            if cy >= bottom_ignore_y:
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

    def _extract_allowed_contact_preview_from_ocr(
        self,
        *,
        ocr_rows: list[tuple[int, int, str, float]],
    ) -> list[tuple[str, str]]:
        allowed_names = _resolve_allowed_contact_names()
        if not allowed_names:
            return []
        allowed_pairs = [(_normalize_target_hint(name), name) for name in allowed_names if _normalize_target_hint(name)]
        if not allowed_pairs:
            return []

        best_row: tuple[int, int, str, str] | None = None
        best_score = -1.0
        for cy, cx, text_value, score in ocr_rows:
            if score < max(0.08, self._ocr_min_score * 0.60):
                continue
            norm_text = _normalize_message_text(text_value)
            if not norm_text:
                continue
            norm_key = _normalize_target_hint(norm_text)
            if not norm_key:
                continue
            for allow_key, allow_name in allowed_pairs:
                ratio = difflib.SequenceMatcher(None, norm_key, allow_key).ratio()
                if allow_key in norm_key or norm_key in allow_key:
                    ratio = max(ratio, 0.98)
                if ratio > best_score:
                    best_score = ratio
                    best_row = (int(cy), int(cx), norm_text, allow_name)

        if best_row is None or best_score < 0.62:
            return []
        target_cy, _target_cx, _name_text, allow_name = best_row
        allow_key = _normalize_target_hint(allow_name)

        preview_candidates: list[str] = []
        for cy, _cx, text_value, score in ocr_rows:
            if score < max(0.08, self._ocr_min_score * 0.55):
                continue
            if abs(int(cy) - int(target_cy)) > 52:
                continue
            norm_text = _normalize_message_text(text_value)
            if not norm_text:
                continue
            norm_key = _normalize_target_hint(norm_text)
            if not norm_key or norm_key == allow_key:
                continue
            if _is_probably_time_or_badge(norm_text):
                continue
            if self._is_probably_ui_noise_text(norm_text):
                continue
            preview_candidates.append(norm_text)

        if not preview_candidates:
            return []
        preview_candidates.sort(key=lambda value: (len(value), value), reverse=True)
        preview = _normalize_message_text(preview_candidates[0])[:120]
        if not preview:
            return []
        return [(allow_name, preview)]

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
        if ("聊天页" in value) or ("进入聊天页" in value):
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

    def _capture_region_bgr(
        self,
        *,
        left: int,
        top: int,
        width: int,
        height: int,
        hwnd: int | None = None,
    ) -> Any:
        def _is_mostly_dark_frame(image_bgr: Any) -> bool:
            try:
                import numpy as np

                arr = np.asarray(image_bgr)
                if arr.ndim != 3 or arr.shape[2] < 3:
                    return False
                rgb = arr[:, :, :3]
                if rgb.size <= 0:
                    return False
                mean_value = float(rgb.mean())
                bright_ratio = float((rgb > 18).sum()) / float(rgb.size)
                return mean_value < 7.0 or bright_ratio < 0.012
            except Exception:
                return False

        pil_error: Exception | None = None
        try:
            from PIL import ImageGrab
            import numpy as np

            bbox = (int(left), int(top), int(left + width), int(top + height))
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            arr = np.array(image)
            if arr.ndim == 3 and arr.shape[2] >= 3:
                # RGB -> BGR
                bgr = arr[:, :, :3][:, :, ::-1].copy()
                if not _is_mostly_dark_frame(bgr):
                    return bgr
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
                arr = None
            # BGRA -> BGR
            if arr is not None:
                bgr = arr[:, :, :3].copy()
                if not _is_mostly_dark_frame(bgr):
                    return bgr
        except Exception:
            pass
        fallback_hwnd = int(hwnd or 0)
        if fallback_hwnd > 0:
            rect = _get_window_rect(fallback_hwnd)
            if rect is not None:
                win_left, win_top, win_right, win_bottom = rect
                win_width = max(0, int(win_right) - int(win_left))
                win_height = max(0, int(win_bottom) - int(win_top))
                if win_width > 0 and win_height > 0:
                    x_ratio = max(0.0, min(1.0, float(int(left) - int(win_left)) / float(win_width)))
                    y_ratio = max(0.0, min(1.0, float(int(top) - int(win_top)) / float(win_height)))
                    w_ratio = max(0.0, min(1.0 - x_ratio, float(int(width)) / float(win_width)))
                    h_ratio = max(0.0, min(1.0 - y_ratio, float(int(height)) / float(win_height)))
                    image_bgr = _capture_window_bgr_via_printwindow(
                        hwnd=fallback_hwnd,
                        x_ratio=x_ratio,
                        y_ratio=y_ratio,
                        w_ratio=w_ratio,
                        h_ratio=h_ratio,
                    )
                    if image_bgr is not None:
                        return image_bgr
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
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info("vision inbound skipped: wechat main window not found")
            return []

        if self._vision_unavailable:
            return []

        if not _is_window_foreground_or_child(int(hwnd)):
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info("vision inbound skipped: wechat window is not foreground")
            fallback_rows = super()._collect_chat_previews()
            if fallback_rows:
                logger.info(
                    "vision inbound background fallback to ocr: previews=%d",
                    len(fallback_rows),
                )
                return fallback_rows
            return []

        now_mono = time.monotonic()
        if now_mono < self._next_vision_call_at:
            return []
        self._next_vision_call_at = now_mono + self._vision_min_interval_s

        roi = self._resolve_message_roi(hwnd)
        if roi is None:
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info("vision inbound skipped: roi not available")
            return []
        left, top, width, height = roi
        if width <= 20 or height <= 20:
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info(
                    "vision inbound skipped: invalid roi size left=%d top=%d width=%d height=%d",
                    int(left),
                    int(top),
                    int(width),
                    int(height),
                )
            return []

        image = self._capture_region_bgr(left=left, top=top, width=width, height=height, hwnd=hwnd)
        if image is None:
            now_mono = time.monotonic()
            if (now_mono - self._last_vision_diag_at) >= 10.0:
                self._last_vision_diag_at = now_mono
                logger.info("vision inbound skipped: screenshot capture failed")
            return []

        extracted = self._infer_latest_inbound_via_vision(image_bgr=image, hwnd=hwnd)
        if extracted:
            self._vision_fail_streak = 0
            return [extracted]

        self._vision_fail_streak += 1
        if self._vision_fail_streak >= 3:
            fallback_rows = super()._collect_chat_previews()
            if fallback_rows:
                now_mono = time.monotonic()
                if (now_mono - self._last_vision_diag_at) >= 8.0:
                    self._last_vision_diag_at = now_mono
                    logger.info(
                        "vision inbound fallback to ocr: fail_streak=%d previews=%d",
                        self._vision_fail_streak,
                        len(fallback_rows),
                    )
                self._vision_fail_streak = 0
                return fallback_rows
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


class WeChatVisionNotifyInboundBridge:
    """Inbound bridge: vision when WeChat is foreground, else notification DB polling."""

    def __init__(
        self,
        *,
        vision_bridge: WeChatVisionInboundBridge,
        notify_bridge: WeChatWindowsNotificationInboundBridge,
    ) -> None:
        self._vision_bridge = vision_bridge
        self._notify_bridge = notify_bridge
        self._last_mode = ""
        self._last_mode_log_at = 0.0
        self._next_focus_allowed_at = 0.0
        self._focus_cooldown_s = 1.2
        self._last_escalation_log_at = 0.0
        self._focus_escalation_enabled = _parse_bool(
            os.environ.get("EDGE_WECHAT_UIA_AUTO_RECOVER"),
            default=DEFAULT_UIA_AUTO_RECOVER,
        )
        self._next_tray_probe_at = 0.0
        self._tray_wake_enabled = _parse_bool(
            os.environ.get("EDGE_WECHAT_TRAY_WAKE_ON_INBOUND"),
            default=DEFAULT_TRAY_WAKE_ON_INBOUND,
        )
        self._tray_wake_cooldown_s = max(
            0.8,
            min(
                12.0,
                _env_float(
                    "EDGE_WECHAT_TRAY_WAKE_COOLDOWN_S",
                    DEFAULT_TRAY_WAKE_COOLDOWN_S,
                ),
            ),
        )

    def start(self) -> None:
        self._vision_bridge.start()
        self._notify_bridge.start()

    def stop(self) -> None:
        self._vision_bridge.stop()
        self._notify_bridge.stop()

    def process_cycle(self) -> int:
        hwnd = _find_wechat_main_window()
        foreground = _is_window_foreground_or_child(int(hwnd or 0))
        workspace = _detect_non_chat_workspace(hwnd=int(hwnd or 0)) if int(hwnd or 0) > 0 else ""
        force_notify_mode = bool(foreground and workspace in {"contacts", "favorites", "moments", "contact_profile"})
        if force_notify_mode and int(hwnd or 0) > 0:
            _focus_chat_record_list(hwnd=int(hwnd))
        mode = "vision" if (foreground and (not force_notify_mode)) else "win_notify"
        now_mono = time.monotonic()
        if mode != self._last_mode or (now_mono - self._last_mode_log_at) >= 20.0:
            self._last_mode = mode
            self._last_mode_log_at = now_mono
            logger.info(
                "vision_notify mode=%s hwnd=0x%X foreground=%s workspace=%s",
                mode,
                int(hwnd or 0),
                bool(foreground),
                workspace or "-",
            )
        if (not foreground) and self._tray_wake_enabled and now_mono >= self._next_tray_probe_at:
            self._next_tray_probe_at = now_mono + self._tray_wake_cooldown_s
            tray_ok, tray_detail = _wake_wechat_from_tray(reason="vision_notify_poll", require_blink=True)
            if tray_ok:
                for _ in range(10):
                    active_hwnd = _find_wechat_main_window()
                    if _is_window_foreground_or_child(int(active_hwnd or 0)):
                        break
                    time.sleep(0.10)
                tray_forwarded = int(self._vision_bridge.process_cycle() or 0)
                if tray_forwarded > 0:
                    self._log_escalation_once(
                        "vision_notify tray wake success forwarded=%d detail=%s",
                        tray_forwarded,
                        tray_detail,
                    )
                    return tray_forwarded
            else:
                self._log_escalation_once(
                    "vision_notify tray wake skipped detail=%s",
                    tray_detail,
                )
            hwnd = _find_wechat_main_window()
            foreground = _is_window_foreground_or_child(int(hwnd or 0))
            workspace = _detect_non_chat_workspace(hwnd=int(hwnd or 0)) if int(hwnd or 0) > 0 else ""
            force_notify_mode = bool(foreground and workspace in {"contacts", "favorites", "moments", "contact_profile"})
            if force_notify_mode and int(hwnd or 0) > 0:
                _focus_chat_record_list(hwnd=int(hwnd))
            mode = "vision" if (foreground and (not force_notify_mode)) else "win_notify"
        if foreground:
            if mode == "vision":
                return int(self._vision_bridge.process_cycle() or 0)

        notify_events = self._notify_bridge.collect_inbound_events()
        if not notify_events:
            return 0

        latest_event = notify_events[-1] if notify_events else {}
        sender = str(latest_event.get("sender") or "").strip()
        content_preview = _normalize_message_text(str(latest_event.get("content") or ""))
        if sender and _is_blocked_chat_target(sender):
            self._log_escalation_once(
                "vision_notify skip blocked sender=%s preview=%.80s",
                sender,
                content_preview,
            )
            return 0
        if sender and not _is_allowed_contact_target(sender):
            self._log_escalation_once(
                "vision_notify skip non-contact sender=%s preview=%.80s",
                sender,
                content_preview,
            )
            return 0
        if not sender:
            allowed_names = _resolve_allowed_contact_names()
            if allowed_names:
                sender = str(allowed_names[0] or "").strip()

        if not self._focus_escalation_enabled:
            return int(
                self._notify_bridge.forward_events(
                    notify_events,
                    bridge_backend="win_notify_no_focus",
                )
                or 0
            )

        if now_mono < self._next_focus_allowed_at:
            return int(
                self._notify_bridge.forward_events(
                    notify_events,
                    bridge_backend="win_notify_cooldown_fallback",
                )
                or 0
            )
        self._next_focus_allowed_at = now_mono + self._focus_cooldown_s

        focus_ok, focus_detail = _focus_wechat_main_window(wechat_path="")
        if not focus_ok:
            self._log_escalation_once(
                "vision_notify escalation focus failed sender=%s detail=%s; fallback notify",
                sender or "(unknown)",
                focus_detail,
            )
            return int(
                self._notify_bridge.forward_events(
                    notify_events,
                    bridge_backend="win_notify_focus_failed",
                )
                or 0
            )

        if sender and (not _is_active_chat_target(sender)):
            open_ok, open_detail = _open_chat_by_target(target=sender, wechat_path="")
            if not open_ok:
                self._log_escalation_once(
                    "vision_notify escalation open chat failed sender=%s detail=%s; fallback notify",
                    sender,
                    open_detail,
                )
                return int(
                    self._notify_bridge.forward_events(
                        notify_events,
                        bridge_backend="win_notify_open_failed",
                    )
                    or 0
                )

        for _ in range(8):
            active_hwnd = _find_wechat_main_window()
            if _is_window_foreground_or_child(int(active_hwnd or 0)):
                break
            time.sleep(0.08)
        time.sleep(0.18)

        vision_forwarded = int(self._vision_bridge.process_cycle() or 0)
        if vision_forwarded > 0:
            self._log_escalation_once(
                "vision_notify escalation success sender=%s notify_events=%d vision_forwarded=%d",
                sender or "(unknown)",
                len(notify_events),
                vision_forwarded,
            )
            return vision_forwarded

        fallback_forwarded = int(
            self._notify_bridge.forward_events(
                notify_events,
                bridge_backend="win_notify_vision_fallback",
            )
            or 0
        )
        if fallback_forwarded > 0:
            self._log_escalation_once(
                "vision_notify escalation fallback sender=%s notify_events=%d fallback_forwarded=%d preview=%.80s",
                sender or "(unknown)",
                len(notify_events),
                fallback_forwarded,
                content_preview,
            )
        return fallback_forwarded

    def _log_escalation_once(self, message: str, *args: Any) -> None:
        now_mono = time.monotonic()
        if (now_mono - self._last_escalation_log_at) < 3.0:
            return
        self._last_escalation_log_at = now_mono
        logger.info(message, *args)

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


def _resolve_wechat_launch_path(wechat_path: str) -> str:
    preferred = str(wechat_path or "").strip()
    if preferred:
        expanded = os.path.abspath(os.path.expandvars(preferred))
        if os.path.isfile(expanded):
            return expanded

    env_candidate = str(os.environ.get("EDGE_WECHAT_EXECUTABLE") or "").strip()
    if env_candidate:
        expanded_env = os.path.abspath(os.path.expandvars(env_candidate))
        if os.path.isfile(expanded_env):
            return expanded_env

    candidates: list[str] = []

    appdata = str(os.environ.get("APPDATA") or "").strip()
    if appdata:
        root = os.path.join(
            appdata,
            "Tencent",
            "xwechat",
            "xplugin",
            "plugins",
            "RadiumWMPF",
        )
        if os.path.isdir(root):
            version_dirs: list[str] = []
            try:
                version_dirs = [
                    os.path.join(root, name)
                    for name in os.listdir(root)
                    if os.path.isdir(os.path.join(root, name))
                ]
            except Exception:
                version_dirs = []
            version_dirs = sorted(
                version_dirs,
                key=lambda path: (
                    0,
                    int(os.path.basename(path)) if os.path.basename(path).isdigit() else -1,
                    os.path.basename(path),
                ),
                reverse=True,
            )
            for version_dir in version_dirs:
                candidates.append(os.path.join(version_dir, "WeChatWin.exe"))
                candidates.append(os.path.join(version_dir, "extracted", "runtime", "WeChat.exe"))
                candidates.append(os.path.join(version_dir, "extracted", "runtime", "WeChatAppEx.exe"))

    for root_hint in (r"D:\Natsume_Ai", r"D:\Natsume_AI"):
        candidates.append(os.path.join(root_hint, "runtime", "edge", "downloads", "WeChatWin.exe"))

    program_files = str(os.environ.get("ProgramFiles") or r"C:\Program Files")
    program_files_x86 = str(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
    for base in (program_files, program_files_x86):
        if not base:
            continue
        candidates.append(os.path.join(base, "Tencent", "WeChat", "WeChat.exe"))
        candidates.append(os.path.join(base, "Tencent", "WeChat", "Weixin.exe"))

    for candidate in candidates:
        expanded = os.path.abspath(os.path.expandvars(str(candidate)))
        if os.path.isfile(expanded):
            return expanded
    return ""


def _launch_wechat_if_needed(wechat_path: str) -> None:
    if _is_wechat_client_running():
        return
    launch_path = _resolve_wechat_launch_path(wechat_path)
    if not launch_path:
        return
    try:
        launch_args = [launch_path]
        if os.path.basename(launch_path).strip().lower() == "wechatwin.exe":
            launch_args.append("/S")
        subprocess.Popen(launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("wechat launch triggered: %s", " ".join(launch_args))
    except Exception as exc:
        logger.warning("failed to launch wechat executable: %s", exc)


def _enum_visible_windows(*, include_hidden: bool = False) -> list[tuple[int, str, str]]:
    windows: list[tuple[int, str, str]] = []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _callback(hwnd: int, _lparam: int) -> bool:
        try:
            if (not include_hidden) and (not bool(user32.IsWindowVisible(hwnd))):
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
        title_key = str(title or "").strip().lower()
        if class_key in WECHAT_MAIN_WINDOW_CLASS_EXCLUDES:
            continue
        if title_key in WECHAT_MAIN_WINDOW_TITLE_EXCLUDES:
            continue
        if class_key not in WECHAT_MAIN_WINDOW_CLASS_HINTS:
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
                class_key = str(_class_name or "").strip().lower()
                title_key = str(_title or "").strip().lower()
                if class_key in WECHAT_MAIN_WINDOW_CLASS_EXCLUDES:
                    continue
                if title_key in WECHAT_MAIN_WINDOW_TITLE_EXCLUDES:
                    continue
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


def _restore_hidden_wechat_window_once() -> tuple[bool, str]:
    try:
        wechat_pids = _list_wechat_client_pids()
    except Exception:
        wechat_pids = set()
    if not wechat_pids:
        return False, "wechat_pid_not_found"

    try:
        windows = _enum_visible_windows(include_hidden=True)
    except Exception as exc:
        return False, f"enum_windows_failed={exc.__class__.__name__}: {exc}"
    if not windows:
        return False, "enum_windows_empty"

    try:
        user32 = ctypes.windll.user32
    except Exception as exc:
        return False, f"user32_unavailable={exc.__class__.__name__}: {exc}"

    pid_ref = ctypes.c_ulong(0)
    candidates: list[tuple[int, int, int, int]] = []
    for hwnd, _class_name, _title in windows:
        class_key = str(_class_name or "").strip().lower()
        title_key = str(_title or "").strip().lower()
        if class_key in WECHAT_MAIN_WINDOW_CLASS_EXCLUDES:
            continue
        if title_key in WECHAT_MAIN_WINDOW_TITLE_EXCLUDES:
            continue
        pid_ref.value = 0
        try:
            user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid_ref))
        except Exception:
            continue
        pid = int(pid_ref.value or 0)
        if pid not in wechat_pids:
            continue
        rect = _get_window_rect(int(hwnd))
        if rect is None:
            continue
        left, top, right, bottom = rect
        width = max(0, int(right) - int(left))
        height = max(0, int(bottom) - int(top))
        area = int(width * height)
        candidates.append((int(area), int(width), int(height), int(hwnd)))

    if not candidates:
        return False, "wechat_top_window_not_found"

    candidates = sorted(candidates, reverse=True)
    tried: list[str] = []
    for _area, width, height, hwnd in candidates[:4]:
        try:
            user32.ShowWindow(int(hwnd), 9)
            user32.SetForegroundWindow(int(hwnd))
            time.sleep(0.18)
        except Exception:
            pass
        recovered = int(_find_wechat_main_window() or 0)
        if recovered > 0:
            return True, f"hidden_window_restored hwnd=0x{int(recovered):X} source=0x{int(hwnd):X}"
        tried.append(f"0x{int(hwnd):X}/{int(width)}x{int(height)}")
    return False, "hidden_window_restore_failed tried=" + ",".join(tried)


def _is_window_foreground_or_child(hwnd: int) -> bool:
    base_hwnd = int(hwnd or 0)
    if base_hwnd <= 0:
        return False
    try:
        user32 = ctypes.windll.user32
        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground <= 0:
            return False
        if foreground == base_hwnd:
            return True
        return bool(user32.IsChild(base_hwnd, foreground))
    except Exception:
        return False


def _is_windows_input_desktop_locked() -> bool | None:
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return None
    uoi_name = 2
    hdesk = user32.OpenInputDesktop(0, False, 0x0100)
    if not hdesk:
        return None
    try:
        needed = ctypes.c_uint(0)
        user32.GetUserObjectInformationW(hdesk, uoi_name, None, 0, ctypes.byref(needed))
        size = int(needed.value or 0)
        if size <= 2:
            return None
        buffer = ctypes.create_unicode_buffer(max(2, int(size // 2) + 1))
        ok = bool(
            user32.GetUserObjectInformationW(
                hdesk,
                uoi_name,
                ctypes.byref(buffer),
                ctypes.sizeof(buffer),
                ctypes.byref(needed),
            )
        )
        if not ok:
            return None
        desktop_name = str(buffer.value or "").strip().lower()
        if not desktop_name:
            return None
        return desktop_name == "winlogon"
    except Exception:
        return None
    finally:
        try:
            user32.CloseDesktop(hdesk)
        except Exception:
            pass


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
        restored, restore_detail = _restore_hidden_wechat_window_once()
        if restored:
            deadline_restore = time.time() + 3.0
            while time.time() < deadline_restore:
                hwnd = _find_wechat_main_window()
                if hwnd:
                    break
                time.sleep(0.15)
        if not hwnd:
            restore_detail = str(restore_detail)
        tray_ok, tray_detail = _wake_wechat_from_tray(reason="focus", require_blink=False)
        if tray_ok:
            deadline_retry = time.time() + 5.0
            while time.time() < deadline_retry:
                hwnd = _find_wechat_main_window()
                if hwnd:
                    break
                time.sleep(0.20)
        if not hwnd:
            if not restore_detail:
                restore_detail = "hidden_window_restore_skipped"
            return False, f"wechat main window not found; {restore_detail}; {tray_detail}"

    try:
        user32 = ctypes.windll.user32
        sw_restore = 9
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return True, f"focused hwnd=0x{hwnd:X}"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


_WECHAT_LOGIN_UI_TITLE_MARKERS = (
    "进入微信",
    "扫码登录",
    "请重新登录",
    "我知道了",
    "仅传输文件",
    "切换账号",
)
_WECHAT_LOGIN_UI_TITLE_REGEX = (
    r".*scan.*login.*",
    r".*log\s*in.*wechat.*",
)
_WECHAT_CONTACT_PROFILE_OCR_MARKERS = (
    "发消息",
    "语音聊天",
    "视频聊天",
    "微信号",
    "地区",
    "备注",
    "朋友圈",
    "message",
    "voice",
    "video",
)
_LOGIN_GATE_OCR_ENGINE = None
_LOGIN_GATE_OCR_DISABLED = False


def _ensure_login_gate_ocr_engine() -> Any:
    global _LOGIN_GATE_OCR_ENGINE, _LOGIN_GATE_OCR_DISABLED
    if _LOGIN_GATE_OCR_DISABLED:
        return None
    if _LOGIN_GATE_OCR_ENGINE is not None:
        return _LOGIN_GATE_OCR_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        _LOGIN_GATE_OCR_DISABLED = True
        return None
    try:
        _LOGIN_GATE_OCR_ENGINE = RapidOCR()
        return _LOGIN_GATE_OCR_ENGINE
    except Exception:
        _LOGIN_GATE_OCR_DISABLED = True
        return None


def _capture_window_bgr_via_printwindow(
    *,
    hwnd: int,
    x_ratio: float = 0.0,
    y_ratio: float = 0.0,
    w_ratio: float = 1.0,
    h_ratio: float = 1.0,
) -> Any:
    try:
        import numpy as np
    except Exception:
        return None
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return None
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc_window = user32.GetWindowDC(ctypes.c_void_p(int(hwnd)))
    if not hdc_window:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(ctypes.c_void_p(hdc_window))
    if not hdc_mem:
        user32.ReleaseDC(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(hdc_window))
        return None
    hbitmap = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(hdc_window), width, height)
    if not hbitmap:
        gdi32.DeleteDC(ctypes.c_void_p(hdc_mem))
        user32.ReleaseDC(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(hdc_window))
        return None
    old_obj = gdi32.SelectObject(ctypes.c_void_p(hdc_mem), ctypes.c_void_p(hbitmap))
    try:
        ok = int(user32.PrintWindow(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(hdc_mem), 2))
        if not ok:
            ok = int(user32.PrintWindow(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(hdc_mem), 0))
        if not ok:
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 3),
            ]

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        stride = width * 4
        buffer_len = stride * height
        pixels = (ctypes.c_ubyte * buffer_len)()
        copied = int(
            gdi32.GetDIBits(
                ctypes.c_void_p(hdc_mem),
                ctypes.c_void_p(hbitmap),
                0,
                height,
                ctypes.byref(pixels),
                ctypes.byref(bmi),
                0,
            )
        )
        if copied <= 0:
            return None
        arr = np.frombuffer(bytes(pixels), dtype=np.uint8).reshape((height, width, 4))
        bgr = arr[:, :, :3].copy()
        x0 = max(0, min(width, int(width * float(x_ratio))))
        y0 = max(0, min(height, int(height * float(y_ratio))))
        x1 = max(0, min(width, int(width * float(x_ratio + w_ratio))))
        y1 = max(0, min(height, int(height * float(y_ratio + h_ratio))))
        if x1 <= x0 or y1 <= y0:
            return bgr
        return bgr[y0:y1, x0:x1]
    except Exception:
        return None
    finally:
        try:
            if old_obj:
                gdi32.SelectObject(ctypes.c_void_p(hdc_mem), ctypes.c_void_p(old_obj))
        except Exception:
            pass
        try:
            gdi32.DeleteObject(ctypes.c_void_p(hbitmap))
        except Exception:
            pass
        try:
            gdi32.DeleteDC(ctypes.c_void_p(hdc_mem))
        except Exception:
            pass
        try:
            user32.ReleaseDC(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(hdc_window))
        except Exception:
            pass


def _encode_png_base64_from_bgr(image_bgr: Any, *, max_side: int = 0) -> str:
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
        if int(max_side or 0) > 0:
            max_dim = int(max(image.size or (0, 0)))
            if max_dim > int(max_side):
                ratio = float(max_side) / float(max_dim)
                target_w = max(64, int(image.size[0] * ratio))
                target_h = max(64, int(image.size[1] * ratio))
                image = image.resize((target_w, target_h))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return ""


def _parse_vision_json_payload(raw_content: str) -> dict[str, Any]:
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


def _call_vision_chat_api(*, image_b64: str, prompt: str, timeout_s: float) -> tuple[dict[str, Any] | None, str]:
    api_url = str(os.environ.get("EDGE_WECHAT_VISION_API_URL") or "").strip() or DEFAULT_VISION_API_URL
    primary_model = (
        str(os.environ.get("EDGE_WECHAT_VISION_MODEL") or "").strip()
        or str(os.environ.get("EDGE_WECHAT_VISUAL_PRIMARY_MODEL") or "").strip()
        or "gemma3:4b"
    )
    fallback_raw = str(os.environ.get("EDGE_WECHAT_VISION_FALLBACK_MODELS") or DEFAULT_VISION_MODEL).strip()
    candidates: list[str] = []
    seen: set[str] = set()
    for model_name in [primary_model] + [item.strip() for item in fallback_raw.split(",")]:
        name = str(model_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append(name)
    if not candidates:
        candidates.append(DEFAULT_VISION_MODEL)

    last_errors: list[str] = []
    for model in candidates[:3]:
        payload = {
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "user",
                    "content": str(prompt or ""),
                    "images": [str(image_b64 or "")],
                }
            ],
            "options": {"temperature": 0},
        }
        try:
            req = urllib.request.Request(
                url=api_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=max(1.0, float(timeout_s))) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception as exc:
            last_errors.append(f"{model}:{exc.__class__.__name__}")
            continue

        raw_content = ""
        if isinstance(body, dict):
            message_obj = body.get("message")
            if isinstance(message_obj, dict):
                raw_content = str(message_obj.get("content") or "")
            elif "response" in body:
                raw_content = str(body.get("response") or "")
            elif "content" in body:
                raw_content = str(body.get("content") or "")
        parsed = _parse_vision_json_payload(raw_content)
        if parsed:
            parsed["_vision_model"] = model
            return parsed, f"ok model={model}"
        preview = _normalize_message_text(raw_content)[:80]
        last_errors.append(f"{model}:parse_empty({preview})")
    return None, "vision_request_failed attempted=" + ";".join(last_errors[:3])


def _safe_float_ratio(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return -1.0
    if number < 0.0 or number > 1.0:
        return -1.0
    return number


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name) or "").strip() or str(default))
    except Exception:
        return float(default)


def _env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(float(str(os.environ.get(name) or "").strip() or str(default)))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value


def _get_virtual_screen_rect() -> tuple[int, int, int, int] | None:
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return None
    try:
        sm_xvirtualscreen = 76
        sm_yvirtualscreen = 77
        sm_cxvirtualscreen = 78
        sm_cyvirtualscreen = 79
        left = int(user32.GetSystemMetrics(sm_xvirtualscreen))
        top = int(user32.GetSystemMetrics(sm_yvirtualscreen))
        width = int(user32.GetSystemMetrics(sm_cxvirtualscreen))
        height = int(user32.GetSystemMetrics(sm_cyvirtualscreen))
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height
    except Exception:
        return None


def _capture_screen_region_bgr(*, left: int, top: int, width: int, height: int) -> Any:
    region_left = int(left)
    region_top = int(top)
    region_w = max(0, int(width))
    region_h = max(0, int(height))
    if region_w <= 0 or region_h <= 0:
        return None

    try:
        from PIL import ImageGrab
        import numpy as np

        bbox = (
            region_left,
            region_top,
            region_left + region_w,
            region_top + region_h,
        )
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        arr = np.array(image)
        if arr.ndim == 3 and arr.shape[2] >= 3:
            return arr[:, :, :3][:, :, ::-1].copy()
    except Exception:
        pass

    try:
        import mss
        import numpy as np
    except Exception:
        return None
    try:
        monitor = {
            "left": region_left,
            "top": region_top,
            "width": region_w,
            "height": region_h,
        }
        with mss.mss() as sct:
            shot = sct.grab(monitor)
        arr = np.array(shot)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None
        return arr[:, :, :3].copy()
    except Exception:
        return None


def _resolve_tray_probe_region() -> tuple[int, int, int, int, int, int] | None:
    screen_rect = _get_virtual_screen_rect()
    if screen_rect is None:
        return None
    screen_left, screen_top, screen_width, screen_height = screen_rect
    if screen_width <= 0 or screen_height <= 0:
        return None

    x_ratio = _safe_float_ratio(
        os.environ.get("EDGE_WECHAT_TRAY_ANCHOR_X_RATIO", str(DEFAULT_TRAY_ANCHOR_X_RATIO))
    )
    y_ratio = _safe_float_ratio(
        os.environ.get("EDGE_WECHAT_TRAY_ANCHOR_Y_RATIO", str(DEFAULT_TRAY_ANCHOR_Y_RATIO))
    )
    if x_ratio < 0:
        x_ratio = float(DEFAULT_TRAY_ANCHOR_X_RATIO)
    if y_ratio < 0:
        y_ratio = float(DEFAULT_TRAY_ANCHOR_Y_RATIO)

    anchor_x = int(screen_left + (screen_width * x_ratio))
    anchor_y = int(screen_top + (screen_height * y_ratio))

    box_w = _env_int(
        "EDGE_WECHAT_TRAY_SCAN_BOX_W",
        DEFAULT_TRAY_SCAN_BOX_W,
        min_value=32,
        max_value=max(64, screen_width),
    )
    box_h = _env_int(
        "EDGE_WECHAT_TRAY_SCAN_BOX_H",
        DEFAULT_TRAY_SCAN_BOX_H,
        min_value=24,
        max_value=max(48, screen_height),
    )

    left = anchor_x - (box_w // 2)
    top = anchor_y - (box_h // 2)
    min_left = int(screen_left)
    min_top = int(screen_top)
    max_left = int(screen_left + screen_width - box_w)
    max_top = int(screen_top + screen_height - box_h)
    left = max(min_left, min(left, max_left))
    top = max(min_top, min(top, max_top))

    return int(left), int(top), int(box_w), int(box_h), int(anchor_x), int(anchor_y)


def _estimate_wechat_tray_click_point() -> tuple[int, int, bool, str]:
    region = _resolve_tray_probe_region()
    if region is None:
        return 0, 0, False, "tray_region_unavailable"
    left, top, width, height, anchor_x, anchor_y = region

    frames = _env_int(
        "EDGE_WECHAT_TRAY_SCAN_FRAMES",
        DEFAULT_TRAY_SCAN_FRAMES,
        min_value=2,
        max_value=8,
    )
    frame_interval_s = _env_float(
        "EDGE_WECHAT_TRAY_SCAN_FRAME_INTERVAL_S",
        DEFAULT_TRAY_SCAN_FRAME_INTERVAL_S,
    )
    frame_interval_s = max(0.02, min(0.60, float(frame_interval_s)))

    frame_list: list[Any] = []
    for index in range(frames):
        image_bgr = _capture_screen_region_bgr(
            left=int(left),
            top=int(top),
            width=int(width),
            height=int(height),
        )
        if image_bgr is not None:
            frame_list.append(image_bgr)
        if index < (frames - 1):
            time.sleep(frame_interval_s)

    if len(frame_list) < 2:
        return int(anchor_x), int(anchor_y), False, f"tray_scan_frames={len(frame_list)}"

    try:
        import numpy as np
    except Exception:
        return int(anchor_x), int(anchor_y), False, "tray_scan_numpy_missing"

    motion_map = None
    valid_pairs = 0
    for idx in range(1, len(frame_list)):
        prev = np.asarray(frame_list[idx - 1], dtype=np.float32)
        curr = np.asarray(frame_list[idx], dtype=np.float32)
        if prev.ndim != 3 or curr.ndim != 3:
            continue
        h = min(prev.shape[0], curr.shape[0])
        w = min(prev.shape[1], curr.shape[1])
        if h <= 0 or w <= 0:
            continue
        prev = prev[:h, :w, :3]
        curr = curr[:h, :w, :3]
        delta = np.abs(curr - prev).mean(axis=2)
        if motion_map is None:
            motion_map = delta
        else:
            motion_map = motion_map + delta
        valid_pairs += 1

    if motion_map is None or valid_pairs <= 0:
        return int(anchor_x), int(anchor_y), False, "tray_scan_motion_empty"
    motion_map = motion_map / float(max(1, valid_pairs))

    last = np.asarray(frame_list[-1], dtype=np.float32)
    if last.ndim != 3 or last.shape[2] < 3:
        return int(anchor_x), int(anchor_y), False, "tray_scan_last_frame_invalid"
    h2 = min(last.shape[0], motion_map.shape[0])
    w2 = min(last.shape[1], motion_map.shape[1])
    if h2 <= 0 or w2 <= 0:
        return int(anchor_x), int(anchor_y), False, "tray_scan_size_invalid"
    last = last[:h2, :w2, :3]
    motion_map = motion_map[:h2, :w2]

    b = last[:, :, 0]
    g = last[:, :, 1]
    r = last[:, :, 2]
    green_bias = np.maximum(0.0, g - r) + np.maximum(0.0, g - b)
    green_weight = max(0.0, min(2.0, _env_float("EDGE_WECHAT_TRAY_SCAN_GREEN_WEIGHT", DEFAULT_TRAY_SCAN_GREEN_WEIGHT)))
    score_map = motion_map + (green_bias * float(green_weight))

    flat_idx = int(np.argmax(score_map))
    y_idx, x_idx = np.unravel_index(flat_idx, score_map.shape)
    motion_value = float(motion_map[y_idx, x_idx])
    score_value = float(score_map[y_idx, x_idx])
    min_motion = _env_float("EDGE_WECHAT_TRAY_SCAN_MIN_MOTION", DEFAULT_TRAY_SCAN_MIN_MOTION)
    min_score = _env_float("EDGE_WECHAT_TRAY_SCAN_MIN_SCORE", DEFAULT_TRAY_SCAN_MIN_SCORE)
    blink_detected = bool(motion_value >= min_motion and score_value >= min_score)

    if blink_detected:
        click_x = int(left + x_idx)
        click_y = int(top + y_idx)
        detail = (
            f"tray_blink_detected motion={motion_value:.2f} score={score_value:.2f} "
            f"point=({click_x},{click_y}) frames={len(frame_list)}"
        )
        return click_x, click_y, True, detail
    detail = (
        f"tray_blink_not_detected motion={motion_value:.2f} score={score_value:.2f} "
        f"anchor=({int(anchor_x)},{int(anchor_y)})"
    )
    return int(anchor_x), int(anchor_y), False, detail


def _click_screen_point(*, x: int, y: int, double: bool) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"
    click_x = int(x)
    click_y = int(y)
    try:
        pyautogui.FAILSAFE = False
        if bool(double):
            pyautogui.doubleClick(click_x, click_y, interval=0.10)
        else:
            pyautogui.click(click_x, click_y)
        time.sleep(0.24)
        return True, f"screen_click x={click_x} y={click_y} double={bool(double)}"
    except Exception as exc:
        return False, f"screen_click_failed={exc.__class__.__name__}: {exc}"


def _wake_wechat_from_tray(*, reason: str = "", require_blink: bool | None = None) -> tuple[bool, str]:
    if not _parse_bool(os.environ.get("EDGE_WECHAT_TRAY_WAKE_ENABLED"), default=DEFAULT_TRAY_WAKE_ENABLED):
        return False, "tray_wake_disabled"
    if not _is_wechat_client_running():
        return False, "tray_wake_skipped_wechat_not_running"

    click_x, click_y, blink_detected, probe_detail = _estimate_wechat_tray_click_point()
    if click_x <= 0 and click_y <= 0:
        return False, f"tray_wake_probe_failed {probe_detail}"
    require_blink_flag = (
        _parse_bool(
            os.environ.get("EDGE_WECHAT_TRAY_WAKE_REQUIRE_BLINK"),
            default=DEFAULT_TRAY_WAKE_REQUIRE_BLINK,
        )
        if require_blink is None
        else bool(require_blink)
    )
    if require_blink_flag and (not blink_detected):
        return False, f"tray_wake_skip_no_blink reason={reason or '-'} {probe_detail}"

    double_click = _parse_bool(
        os.environ.get("EDGE_WECHAT_TRAY_WAKE_DOUBLE_CLICK"),
        default=DEFAULT_TRAY_WAKE_DOUBLE_CLICK,
    )
    click_ok, click_detail = _click_screen_point(x=int(click_x), y=int(click_y), double=bool(double_click))
    if not click_ok:
        return False, f"tray_wake_click_failed {click_detail}; {probe_detail}"

    wake_wait_s = max(0.8, min(8.0, _env_float("EDGE_WECHAT_TRAY_WAKE_WAIT_S", DEFAULT_TRAY_WAKE_WAIT_S)))
    deadline = time.time() + wake_wait_s
    hwnd = 0
    while time.time() < deadline:
        hwnd = _find_wechat_main_window()
        if hwnd:
            break
        time.sleep(0.12)
    if not hwnd:
        return False, (
            f"tray_wake_no_window reason={reason or '-'} blink={int(bool(blink_detected))} "
            f"{probe_detail}; {click_detail}"
        )

    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.20)
    except Exception:
        pass
    return True, (
        f"tray_wake_ok hwnd=0x{int(hwnd):X} reason={reason or '-'} blink={int(bool(blink_detected))} "
        f"{probe_detail}; {click_detail}"
    )


def _click_wechat_window_ratio(*, hwnd: int, x_ratio: float, y_ratio: float, double: bool = False) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"
    x_norm = _safe_float_ratio(x_ratio)
    y_norm = _safe_float_ratio(y_ratio)
    if x_norm < 0 or y_norm < 0:
        return False, "invalid_ratio"
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False, "GetWindowRect failed"
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return False, "invalid window rect"
    click_x = int(left + width * x_norm)
    click_y = int(top + height * y_norm)
    try:
        pyautogui.FAILSAFE = False
        if double:
            pyautogui.doubleClick(click_x, click_y)
        else:
            pyautogui.click(click_x, click_y)
        time.sleep(0.25)
        return True, f"ratio_click x={click_x} y={click_y} double={bool(double)}"
    except Exception as exc:
        return False, f"ratio_click_failed={exc.__class__.__name__}: {exc}"


def _press_wechat_enter_key() -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"
    try:
        pyautogui.FAILSAFE = False
        pyautogui.press("enter")
        time.sleep(0.20)
        return True, "enter_pressed"
    except Exception as exc:
        return False, f"enter_press_failed={exc.__class__.__name__}: {exc}"


def _ocr_find_target_ratio_on_chat_list(*, hwnd: int, target: str) -> tuple[float, float, str]:
    image_bgr = _capture_window_bgr_via_printwindow(
        hwnd=int(hwnd),
        x_ratio=0.06,
        y_ratio=0.10,
        w_ratio=0.37,
        h_ratio=0.82,
    )
    if image_bgr is None:
        return -1.0, -1.0, "ocr_capture_failed"
    engine = _ensure_login_gate_ocr_engine()
    if engine is None:
        return -1.0, -1.0, "ocr_engine_unavailable"
    try:
        result, _ = engine(image_bgr)
    except Exception as exc:
        return -1.0, -1.0, f"ocr_failed={exc.__class__.__name__}"
    if not result:
        return -1.0, -1.0, "ocr_empty"

    try:
        roi_h, roi_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    except Exception:
        return -1.0, -1.0, "ocr_invalid_image"
    if roi_w <= 0 or roi_h <= 0:
        return -1.0, -1.0, "ocr_invalid_shape"

    target_key = _normalize_target_hint(target)
    best_ratio_x = -1.0
    best_ratio_y = -1.0
    best_detail = "ocr_target_not_found"
    best_similarity = 0.0
    for item in result:
        try:
            points = item[0]
            text_value = _normalize_message_text(str(item[1] or ""))
            score_value = float(item[2] or 0.0)
        except Exception:
            continue
        if not text_value or score_value < 0.18:
            continue
        if _is_probably_time_or_badge(text_value):
            continue
        text_key = _normalize_target_hint(text_value)
        if not text_key:
            continue
        matched = _target_text_matches(text_value, target)
        similarity = 0.0
        if target_key and text_key:
            try:
                similarity = float(difflib.SequenceMatcher(None, text_key, target_key).ratio())
            except Exception:
                similarity = 0.0
        if (not matched) and similarity < 0.42:
            continue
        xs: list[float] = []
        ys: list[float] = []
        try:
            for pair in points:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                xs.append(float(pair[0]))
                ys.append(float(pair[1]))
        except Exception:
            xs = []
            ys = []
        if not xs or not ys:
            continue
        cx = float(sum(xs) / len(xs))
        cy = float(sum(ys) / len(ys))
        x_ratio = 0.06 + 0.37 * max(0.0, min(1.0, cx / float(roi_w)))
        y_ratio = 0.10 + 0.82 * max(0.0, min(1.0, cy / float(roi_h)))
        if matched:
            return x_ratio, y_ratio, f"ocr_target={text_value}"
        if (similarity > best_similarity) or (best_ratio_x < 0):
            best_similarity = similarity
            best_ratio_x = x_ratio
            best_ratio_y = y_ratio
            best_detail = f"ocr_fuzzy_target={text_value}|sim={similarity:.2f}"
    if best_ratio_x >= 0 and best_ratio_y >= 0:
        return best_ratio_x, best_ratio_y, best_detail
    return -1.0, -1.0, "ocr_target_not_found"


def _visual_open_chat_by_target(target: str) -> tuple[bool, str]:
    if not _parse_bool(os.environ.get("EDGE_WECHAT_VISUAL_OPEN_ENABLED"), default=True):
        return False, "visual_open_disabled"
    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"
    locked = _is_windows_input_desktop_locked()
    if locked is True:
        return False, "desktop_locked"

    try:
        max_steps_raw = float(os.environ.get("EDGE_WECHAT_VISUAL_OPEN_STEPS") or 3)
    except Exception:
        max_steps_raw = 3
    max_steps = int(max(1, min(5, int(max_steps_raw))))
    try:
        timeout_s = float(os.environ.get("EDGE_WECHAT_VISUAL_TIMEOUT_S") or DEFAULT_VISION_TIMEOUT_S)
    except Exception:
        timeout_s = DEFAULT_VISION_TIMEOUT_S
    try:
        max_side = int(float(os.environ.get("EDGE_WECHAT_VISUAL_MAX_SIDE") or 960))
    except Exception:
        max_side = 960
    attempt_details: list[str] = []
    for step_index in range(max_steps):
        ready_ok, ready_detail = _verify_chat_surface_ready(
            target=target,
            allow_unverified_without_profile=True,
        )
        if ready_ok:
            return True, f"visual_step={step_index}; {ready_detail}"
        profile_ocr, profile_detail = _detect_contact_profile_via_ocr(hwnd=int(hwnd))
        if profile_ocr:
            profile_click_ok, profile_click_detail = _click_contact_profile_message_button(hwnd=int(hwnd))
            attempt_details.append(
                f"step={step_index} profile_ocr={profile_detail} profile_click={profile_click_ok} detail={profile_click_detail}"
            )
            if profile_click_ok:
                time.sleep(0.25)
                retry_ok, retry_detail = _verify_chat_surface_ready(
                    target=target,
                    allow_unverified_without_profile=True,
                )
                if retry_ok:
                    return True, f"visual_profile_click={retry_detail}; attempts={' | '.join(attempt_details[:4])}"
            continue

        image_bgr = _capture_window_bgr_via_printwindow(hwnd=int(hwnd))
        if image_bgr is None:
            attempt_details.append(f"step={step_index} printwindow_capture_failed")
            break
        image_b64 = _encode_png_base64_from_bgr(image_bgr=image_bgr, max_side=max_side)
        if not image_b64:
            attempt_details.append(f"step={step_index} image_encode_failed")
            break

        prompt = (
            "You are reading a screenshot of WeChat desktop. "
            f"Target contact name is: {target}. "
            "Return strict JSON only with fields: "
            "page (chat_list|contacts|contact_profile|chat_thread|unknown), "
            "target_visible (boolean), "
            "target_x_ratio (number 0..1 or null), "
            "target_y_ratio (number 0..1 or null), "
            "message_btn_x_ratio (number 0..1 or null), "
            "message_btn_y_ratio (number 0..1 or null), "
            "confidence (number 0..1)."
        )
        parsed, parse_detail = _call_vision_chat_api(
            image_b64=image_b64,
            prompt=prompt,
            timeout_s=timeout_s,
        )
        if not parsed:
            attempt_details.append(f"step={step_index} {parse_detail}")
            break

        page = str(parsed.get("page") or "").strip().lower()
        target_visible = bool(parsed.get("target_visible"))
        target_x = _safe_float_ratio(parsed.get("target_x_ratio"))
        target_y = _safe_float_ratio(parsed.get("target_y_ratio"))
        message_x = _safe_float_ratio(parsed.get("message_btn_x_ratio"))
        message_y = _safe_float_ratio(parsed.get("message_btn_y_ratio"))
        ocr_detail = "ocr_skip"
        if page in {"chat_list", "contacts"}:
            ocr_x, ocr_y, ocr_detail = _ocr_find_target_ratio_on_chat_list(hwnd=int(hwnd), target=target)
            if ocr_x >= 0 and ocr_y >= 0:
                target_visible = True
                target_x = ocr_x
                target_y = ocr_y
            elif target_x >= 0 and target_x > 0.46:
                target_x = -1.0
                target_y = -1.0
            if page == "chat_list" and target_x >= 0 and target_y >= 0:
                if target_x > 0.43 or target_y < 0.10 or target_y > 0.42:
                    target_x = -1.0
                    target_y = -1.0
                    ocr_detail = f"{ocr_detail};vision_coord_rejected"
            if page == "chat_list" and (target_x < 0 or target_y < 0):
                heuristic_points = (
                    (0.20, 0.14),
                    (0.20, 0.18),
                    (0.22, 0.22),
                    (0.18, 0.26),
                )
                point_index = max(0, min(len(heuristic_points) - 1, step_index))
                point_x, point_y = heuristic_points[point_index]
                target_visible = True
                target_x = float(point_x)
                target_y = float(point_y)
                ocr_detail = f"{ocr_detail};heuristic_row_click#{point_index}"

        click_ok = False
        click_detail = "no_click"
        if page == "contact_profile" and message_x >= 0 and message_y >= 0:
            click_ok, click_detail = _click_wechat_window_ratio(
                hwnd=int(hwnd),
                x_ratio=message_x,
                y_ratio=message_y,
                double=False,
            )
        elif target_visible and target_x >= 0 and target_y >= 0:
            should_double = (page in {"contacts", "chat_list"}) or ("heuristic_row_click" in ocr_detail)
            click_ok, click_detail = _click_wechat_window_ratio(
                hwnd=int(hwnd),
                x_ratio=target_x,
                y_ratio=target_y,
                double=should_double,
            )
        elif page == "contact_profile":
            click_ok, click_detail = _click_wechat_window_ratio(
                hwnd=int(hwnd),
                x_ratio=0.76,
                y_ratio=0.82,
                double=False,
            )

        attempt_details.append(
            f"step={step_index} page={page or '-'} visible={target_visible} ocr={ocr_detail} click={click_ok} detail={click_detail}"
        )
        if not click_ok:
            continue
        time.sleep(0.28)
        post_ok, post_detail = _verify_chat_surface_ready(
            target=target,
            allow_unverified_without_profile=True,
        )
        if post_ok:
            return True, f"visual_post_click={post_detail}; attempts={' | '.join(attempt_details[:4])}"
        if page == "chat_list":
            enter_ok, enter_detail = _press_wechat_enter_key()
            attempt_details.append(f"step={step_index} post_enter={enter_ok} detail={enter_detail}")
            if enter_ok:
                enter_verify_ok, enter_verify_detail = _verify_chat_surface_ready(
                    target=target,
                    allow_unverified_without_profile=True,
                )
                if enter_verify_ok:
                    return True, f"visual_post_enter={enter_verify_detail}; attempts={' | '.join(attempt_details[:4])}"
        if ("strict_chat_surface_check" in post_detail) or ("unverified_surface_rejected" in post_detail):
            profile_click_ok, profile_click_detail = _click_contact_profile_message_button(hwnd=int(hwnd))
            attempt_details.append(
                f"step={step_index} post_profile_click={profile_click_ok} detail={profile_click_detail}"
            )
            if profile_click_ok:
                time.sleep(0.25)
                retry_ok, retry_detail = _verify_chat_surface_ready(
                    target=target,
                    allow_unverified_without_profile=True,
                )
                if retry_ok:
                    return True, f"visual_post_profile={retry_detail}; attempts={' | '.join(attempt_details[:4])}"

    ready_ok, ready_detail = _verify_chat_surface_ready(
        target=target,
        allow_unverified_without_profile=True,
    )
    if ready_ok:
        return True, f"visual_final_verify={ready_detail}; attempts={' | '.join(attempt_details[:4])}"
    return False, f"visual_open_failed verify={ready_detail}; attempts={' | '.join(attempt_details[:4])}"


def _detect_wechat_login_gate_via_ocr(*, hwnd: int) -> tuple[bool, str]:
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False, ""
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width < 320 or height < 240:
        return False, ""
    monitor = {
        "left": int(left + width * 0.18),
        "top": int(top + height * 0.16),
        "width": int(width * 0.64),
        "height": int(height * 0.72),
    }
    if int(monitor["width"]) < 120 or int(monitor["height"]) < 120:
        return False, ""

    try:
        import mss
        import numpy as np
    except Exception:
        return False, ""
    try:
        with mss.mss() as sct:
            shot = sct.grab(monitor)
        arr = np.array(shot)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return False, ""
        image_bgr = arr[:, :, :3].copy()
    except Exception:
        return False, ""

    engine = _ensure_login_gate_ocr_engine()
    if engine is None:
        return False, ""
    try:
        result, _ = engine(image_bgr)
    except Exception:
        return False, ""
    if not result:
        return False, ""

    texts: list[str] = []
    for item in result:
        try:
            text_value = _normalize_message_text(str(item[1] or ""))
        except Exception:
            continue
        if text_value:
            texts.append(text_value)
    if not texts:
        return False, ""

    merged_text = "\n".join(texts)
    markers = [marker for marker in _WECHAT_LOGIN_UI_TITLE_MARKERS if marker and marker in merged_text]
    if markers:
        unique_markers = ", ".join(dict.fromkeys(markers))
        return True, f"login ui detected(ocr): {unique_markers}"
    return False, ""


def _detect_contact_profile_via_ocr(*, hwnd: int) -> tuple[bool, str]:
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False, ""
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width < 320 or height < 240:
        return False, ""
    monitor = {
        "left": int(left + width * 0.44),
        "top": int(top + height * 0.10),
        "width": int(width * 0.54),
        "height": int(height * 0.84),
    }
    if int(monitor["width"]) < 120 or int(monitor["height"]) < 120:
        return False, ""

    try:
        import mss
        import numpy as np
    except Exception:
        return False, ""
    try:
        with mss.mss() as sct:
            shot = sct.grab(monitor)
        arr = np.array(shot)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return False, ""
        image_bgr = arr[:, :, :3].copy()
    except Exception:
        return False, ""

    engine = _ensure_login_gate_ocr_engine()
    if engine is None:
        return False, ""
    try:
        result, _ = engine(image_bgr)
    except Exception:
        return False, ""
    if not result:
        return False, ""

    texts: list[str] = []
    for item in result:
        try:
            text_value = _normalize_message_text(str(item[1] or ""))
        except Exception:
            continue
        if text_value:
            texts.append(text_value)
    if not texts:
        return False, ""

    joined = "\n".join(texts).lower()
    normalized_joined = _normalize_target_hint(joined)
    hits: list[str] = []
    for marker in _WECHAT_CONTACT_PROFILE_OCR_MARKERS:
        marker_text = str(marker or "").strip()
        if not marker_text:
            continue
        marker_key = _normalize_target_hint(marker_text)
        if marker_key and marker_key in normalized_joined:
            hits.append(marker_text)
            continue
        if marker_text.lower() in joined:
            hits.append(marker_text)
    unique_hits = list(dict.fromkeys(hits))
    if len(unique_hits) >= 2:
        return True, "contact_profile_ocr=" + ",".join(unique_hits[:4])
    return False, ""


def _detect_wechat_login_gate(*, hwnd: int | None = None) -> tuple[bool, str]:
    target_hwnd = int(hwnd or 0)
    if target_hwnd <= 0:
        target_hwnd = _find_wechat_main_window()
    if target_hwnd <= 0:
        return False, "wechat main window not found"
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return False, f"pywinauto import failed: {exc}"
    try:
        window = Desktop(backend="uia").window(handle=target_hwnd)
    except Exception as exc:
        return False, f"bind window failed: {exc}"

    markers: list[str] = []
    for title in _WECHAT_LOGIN_UI_TITLE_MARKERS:
        try:
            if bool(window.child_window(title=title).exists(timeout=0.15)):
                markers.append(title)
        except Exception:
            continue
    if not markers:
        for pattern in _WECHAT_LOGIN_UI_TITLE_REGEX:
            try:
                if bool(window.child_window(title_re=pattern).exists(timeout=0.15)):
                    markers.append(pattern)
            except Exception:
                continue
    if markers:
        unique_markers = ", ".join(dict.fromkeys(markers))
        return True, f"login ui detected: {unique_markers}"
    ocr_blocked, ocr_detail = _detect_wechat_login_gate_via_ocr(hwnd=target_hwnd)
    if ocr_blocked:
        return True, ocr_detail
    return False, "chat ui detected"


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


def _has_message_input_editor(*, hwnd: int) -> bool | None:
    try:
        from pywinauto import Desktop
    except Exception:
        return None
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return None
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return None
    try:
        window = Desktop(backend="uia").window(handle=int(hwnd))
    except Exception:
        return None
    try:
        for control_type in ("Edit", "Document"):
            for node in window.descendants(control_type=control_type):
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
                return True
    except Exception:
        return None
    return False


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
        for control_type in ("Edit", "Document"):
            for node in window.descendants(control_type=control_type):
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
            return True, "message_input_uia_detected_no_click"
    except Exception:
        pass

    editor_exists = _has_message_input_editor(hwnd=hwnd)
    if editor_exists is False:
        workspace = _detect_non_chat_workspace(hwnd=hwnd)
        if workspace:
            return False, f"non_chat_workspace={workspace}"
    require_editor = _parse_bool(
        os.environ.get("EDGE_WECHAT_REQUIRE_EDITOR_CHECK"),
        default=False,
    )
    if editor_exists is False and require_editor:
        return False, "message_input_edit_not_found"

    allow_mouse_fallback = _parse_bool(
        os.environ.get("EDGE_WECHAT_ALLOW_INPUT_FALLBACK_CLICK"),
        default=False,
    )
    if not allow_mouse_fallback:
        return False, "message_input_mouse_fallback_disabled"

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
    list_items: list[Any] = []
    try:
        for control_type in ("ListItem", "DataItem", "TreeItem"):
            list_items.extend(window.descendants(control_type=control_type))
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


def _focus_chat_record_list(*, hwnd: int) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False, "GetWindowRect failed"
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return False, "invalid window rect"
    click_x = int(left + width * 0.06)
    click_y = int(top + height * 0.20)
    try:
        pyautogui.FAILSAFE = False
        pyautogui.click(click_x, click_y)
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "1")
        time.sleep(0.16)
        return True, f"chat_list_anchor_click x={click_x} y={click_y}"
    except Exception as exc:
        return False, f"chat_list_anchor_failed: {exc.__class__.__name__}: {exc}"


def _should_require_message_input_editor() -> bool:
    return _parse_bool(
        os.environ.get("EDGE_WECHAT_REQUIRE_EDITOR_CHECK"),
        default=False,
    )


def _verify_message_input_ready(*, target: str) -> tuple[bool, str]:
    verify_hwnd = _find_wechat_main_window()
    if not verify_hwnd:
        return False, "wechat main window not found"
    editor_exists = _has_message_input_editor(hwnd=verify_hwnd)
    if editor_exists is False:
        workspace = _detect_non_chat_workspace(hwnd=verify_hwnd)
        if workspace:
            return False, f"non_chat_workspace={workspace}"
    if editor_exists is False and _should_require_message_input_editor():
        return False, f"no message input for target={target}"
    if editor_exists is False:
        return True, f"message_input_unverified target={target}"
    if editor_exists is None:
        return True, f"message_input_unknown target={target}"
    return True, "message_input_ready"


def _verify_chat_thread_via_ocr(*, hwnd: int, target: str) -> tuple[bool, str]:
    image_bgr = _capture_window_bgr_via_printwindow(
        hwnd=int(hwnd),
        x_ratio=0.34,
        y_ratio=0.06,
        w_ratio=0.64,
        h_ratio=0.90,
    )
    if image_bgr is None:
        return False, "thread_ocr_capture_failed"
    engine = _ensure_login_gate_ocr_engine()
    if engine is None:
        return False, "thread_ocr_engine_unavailable"
    try:
        result, _ = engine(image_bgr)
    except Exception as exc:
        return False, f"thread_ocr_failed={exc.__class__.__name__}"
    if not result:
        return False, "thread_ocr_empty"

    try:
        roi_h = int(image_bgr.shape[0])
        roi_w = int(image_bgr.shape[1])
    except Exception:
        return False, "thread_ocr_invalid_shape"
    if roi_w <= 0 or roi_h <= 0:
        return False, "thread_ocr_invalid_shape"

    unique_tokens: set[str] = set()
    top_hits = 0
    mid_hits = 0
    bottom_hits = 0
    target_hits = 0
    top_text_candidates: list[str] = []
    for item in result:
        try:
            points = item[0]
            text_value = _normalize_message_text(str(item[1] or ""))
            score_value = float(item[2] or 0.0)
        except Exception:
            continue
        if not text_value or score_value < 0.28:
            continue
        if _is_probably_time_or_badge(text_value):
            continue

        key = _normalize_target_hint(text_value) or text_value.lower()
        if key:
            unique_tokens.add(key)
        if _target_text_matches(text_value, target):
            target_hits += 1

        ys: list[float] = []
        try:
            for pair in points:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                ys.append(float(pair[1]))
        except Exception:
            ys = []
        if ys:
            cy = float(sum(ys) / len(ys))
            y_ratio = max(0.0, min(1.0, cy / float(roi_h)))
            if y_ratio <= 0.16:
                top_hits += 1
                top_text_candidates.append(text_value)
            elif y_ratio >= 0.78:
                bottom_hits += 1
            else:
                mid_hits += 1

    header_text = ""
    for candidate in top_text_candidates:
        norm = _normalize_target_hint(candidate)
        if not norm:
            continue
        if norm in {"search", "chat", "message", "messages"}:
            continue
        if len(candidate) > len(header_text):
            header_text = candidate
    if header_text and _is_blocked_chat_target(header_text):
        return (
            False,
            f"thread_header_blocked={header_text} target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )

    target_required = bool(_normalize_target_hint(target)) and (not _is_active_chat_target(target))
    if target_required and header_text and (not _target_text_matches(header_text, target)):
        return (
            False,
            f"thread_header_mismatch={header_text} target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )
    if target_required and target_hits <= 0:
        return (
            False,
            f"thread_target_missing header={header_text or '-'} target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )

    token_count = len(unique_tokens)
    if target_hits >= 1 and (mid_hits >= 1 or bottom_hits >= 1 or top_hits >= 1):
        return (
            True,
            f"thread_ocr_confirmed target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )
    if mid_hits >= 2 and (top_hits >= 1 or bottom_hits >= 1):
        return (
            True,
            f"thread_ocr_confirmed target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )
    if mid_hits >= 1 and bottom_hits >= 1 and token_count >= 2:
        return (
            True,
            f"thread_ocr_confirmed target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
        )
    return (
        False,
        f"thread_ocr_not_chat target={target_hits} top={top_hits} mid={mid_hits} bottom={bottom_hits}",
    )


def _is_contact_profile_layout(*, hwnd: int) -> bool:
    try:
        from pywinauto import Desktop
    except Exception:
        return False
    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return False
    try:
        window = Desktop(backend="uia").window(handle=int(hwnd))
    except Exception:
        return False

    rows: list[tuple[int, int, list[str]]] = []
    try:
        for button in window.descendants(control_type="Button")[:280]:
            try:
                button_rect = button.rectangle()
                cx = int((int(button_rect.left) + int(button_rect.right)) / 2)
                cy = int((int(button_rect.top) + int(button_rect.bottom)) / 2)
            except Exception:
                continue
            if cx <= (left + int(width * 0.44)):
                continue
            if cy <= (top + int(height * 0.40)) or cy >= (top + int(height * 0.86)):
                continue
            texts = _extract_element_text_candidates(button)
            rows.append((cy, cx, texts))
    except Exception:
        return False
    if len(rows) < 3:
        return False

    rows.sort(key=lambda row: row[0])
    middle = rows[len(rows) // 2][0]
    y_tolerance = max(24, int(height * 0.05))
    band = [row for row in rows if abs(int(row[0]) - int(middle)) <= y_tolerance]
    if len(band) < 3:
        return False

    xs = sorted(int(row[1]) for row in band)
    spread = xs[-1] - xs[0]
    if spread < int(width * 0.16):
        return False

    token_hits = 0
    profile_tokens = (
        _normalize_target_hint("发消息"),
        _normalize_target_hint("语音聊天"),
        _normalize_target_hint("视频聊天"),
        _normalize_target_hint("message"),
        _normalize_target_hint("voice"),
        _normalize_target_hint("video"),
        _normalize_target_hint("chat"),
    )
    for _cy, _cx, texts in band:
        for text_value in texts:
            key = _normalize_target_hint(text_value)
            if not key:
                continue
            if any(token and token in key for token in profile_tokens):
                token_hits += 1
                break
    if token_hits >= 2:
        return True
    return len(band) >= 3


def _verify_chat_surface_ready(*, target: str, allow_unverified_without_profile: bool = False) -> tuple[bool, str]:
    verify_ok, verify_detail = _verify_message_input_ready(target=target)
    if not verify_ok:
        return False, verify_detail
    if "message_input_ready" in verify_detail:
        return True, verify_detail

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, f"{verify_detail}; wechat main window not found"
    desktop_locked = _is_windows_input_desktop_locked()
    if desktop_locked is True:
        return False, f"{verify_detail}; desktop_locked"

    login_gate, login_detail = _detect_wechat_login_gate(hwnd=int(hwnd))
    if login_gate:
        return False, f"{verify_detail}; login_gate={login_detail}"

    workspace = _detect_non_chat_workspace(hwnd=int(hwnd))
    if workspace:
        return False, f"{verify_detail}; non_chat_workspace={workspace}"

    profile_layout = _is_contact_profile_layout(hwnd=int(hwnd))
    if profile_layout:
        return False, f"{verify_detail}; contact_profile_layout_detected"
    profile_ocr, profile_ocr_detail = _detect_contact_profile_via_ocr(hwnd=int(hwnd))
    if profile_ocr:
        return False, f"{verify_detail}; {profile_ocr_detail}"

    thread_ok, thread_detail = _verify_chat_thread_via_ocr(hwnd=int(hwnd), target=target)
    if thread_ok:
        return True, f"{verify_detail}; {thread_detail}"

    if allow_unverified_without_profile:
        return False, f"{verify_detail}; {thread_detail}; unverified_surface_rejected"
    return False, f"{verify_detail}; {thread_detail}; strict_chat_surface_check"


def _click_contact_profile_message_button(*, hwnd: int) -> tuple[bool, str]:
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return False, f"pywinauto import failed: {exc}"
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    rect = _get_window_rect(int(hwnd))
    if rect is None:
        return False, "GetWindowRect failed"
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return False, "invalid window rect"

    try:
        window = Desktop(backend="uia").window(handle=int(hwnd))
    except Exception as exc:
        return False, f"bind window failed: {exc}"

    message_tokens = (
        _normalize_target_hint("发消息"),
        _normalize_target_hint("message"),
        _normalize_target_hint("chat"),
    )

    try:
        candidates: list[tuple[int, int, Any, str]] = []
        for button in window.descendants(control_type="Button")[:260]:
            try:
                button_rect = button.rectangle()
                cx = int((int(button_rect.left) + int(button_rect.right)) / 2)
                cy = int((int(button_rect.top) + int(button_rect.bottom)) / 2)
            except Exception:
                continue
            if cx <= (left + int(width * 0.44)):
                continue
            if cy <= (top + int(height * 0.42)) or cy >= (top + int(height * 0.86)):
                continue
            texts = _extract_element_text_candidates(button)
            label = ""
            token_hit = False
            for text_value in texts:
                key = _normalize_target_hint(text_value)
                if not key:
                    continue
                if any(token and token in key for token in message_tokens):
                    token_hit = True
                    label = text_value
                    break
                if (not label) and ("voice" in key or "video" in key):
                    label = text_value
            score = 100 if token_hit else 10
            candidates.append((score, cy * 10000 + cx, button, label))
        if candidates:
            candidates.sort(key=lambda row: (-int(row[0]), int(row[1])))
            best_score = int(candidates[0][0])
            if best_score >= 100:
                _sort_key, _pos_key, best_btn, best_label = candidates[0]
                best_btn.click_input()
                time.sleep(0.28)
                return True, f"profile_message_button={best_label or '(uia)'}"
            layout_candidates = sorted(candidates, key=lambda row: int(row[1]))
            if len(layout_candidates) >= 3:
                # Typical contact-profile footer has 3 buttons: message / voice / video.
                _, _, best_btn, best_label = layout_candidates[0]
                best_btn.click_input()
                time.sleep(0.28)
                return True, f"profile_message_button_layout={best_label or '(leftmost)'}"
    except Exception:
        pass

    try:
        pyautogui.FAILSAFE = False
        fallback_points = (
            (0.76, 0.82),
            (0.72, 0.82),
            (0.80, 0.82),
            (0.76, 0.76),
        )
        for ratio_x, ratio_y in fallback_points:
            click_x = int(left + width * ratio_x)
            click_y = int(top + height * ratio_y)
            pyautogui.click(click_x, click_y)
            time.sleep(0.24)
        return True, "profile_message_button_coord_fallback"
    except Exception as exc:
        return False, f"profile_message_button_coord_failed={exc.__class__.__name__}"


def _detect_non_chat_workspace(*, hwnd: int) -> str:
    try:
        from pywinauto import Desktop
    except Exception:
        return ""
    try:
        window = Desktop(backend="uia").window(handle=int(hwnd))
    except Exception:
        return ""
    tokens: set[str] = set()
    try:
        for node in window.descendants(control_type="Text")[:280]:
            try:
                text_value = _normalize_message_text(node.window_text())
            except Exception:
                continue
            if not text_value:
                continue
            tokens.add(_normalize_target_hint(text_value))
    except Exception:
        return ""
    joined = " ".join(sorted(token for token in tokens if token))
    if any(token in joined for token in (_normalize_target_hint("收藏"), "favorite")):
        return "favorites"
    if any(
        token in joined
        for token in (
            _normalize_target_hint("通讯录"),
            _normalize_target_hint("联系人"),
            "contact",
        )
    ):
        return "contacts"
    if (
        _normalize_target_hint("发消息") in joined
        and (
            _normalize_target_hint("语音聊天") in joined
            or _normalize_target_hint("视频聊天") in joined
            or _normalize_target_hint("地区") in joined
        )
    ):
        return "contact_profile"
    if _is_contact_profile_layout(hwnd=int(hwnd)):
        return "contact_profile"
    profile_ocr, _profile_ocr_detail = _detect_contact_profile_via_ocr(hwnd=int(hwnd))
    if profile_ocr:
        return "contact_profile"
    if any(token in joined for token in (_normalize_target_hint("朋友圈"), "moments")):
        return "moments"
    return ""


def _try_open_chat_by_contacts_search(target: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return False, f"pywinauto import failed: {exc}"

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"
    rect = _get_window_rect(hwnd)
    if rect is None:
        return False, "GetWindowRect failed"
    left, top, right, bottom = rect
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return False, "invalid window rect"

    nav_detail = "contacts_nav_hotkey"
    try:
        pyautogui.FAILSAFE = False
        pyautogui.hotkey("ctrl", "2")
        time.sleep(0.20)
    except Exception as exc:
        return False, f"contacts_nav_failed: {exc.__class__.__name__}: {exc}"

    direct_click_detail = "direct_contact_not_found"
    try:
        window = Desktop(backend="uia").window(handle=hwnd)
        target_candidates: list[tuple[int, Any, str]] = []
        fallback_candidates: list[tuple[int, Any, str]] = []
        direct_items: list[Any] = []
        for control_type in ("ListItem", "DataItem", "TreeItem"):
            direct_items.extend(window.descendants(control_type=control_type))
        for item in direct_items[:260]:
            try:
                item_rect = item.rectangle()
                cx = int((int(item_rect.left) + int(item_rect.right)) / 2)
                cy = int((int(item_rect.top) + int(item_rect.bottom)) / 2)
            except Exception:
                continue
            if cx >= (left + int(width * 0.44)):
                continue
            if cy <= (top + int(height * 0.10)) or cy >= (top + int(height * 0.95)):
                continue
            texts = _extract_element_text_candidates(item)
            if not texts:
                continue
            matched_text = ""
            fallback_text = ""
            for text_value in texts:
                if not text_value:
                    continue
                if _is_probably_time_or_badge(text_value):
                    continue
                if text_value in WECHAT_SYSTEM_LIST_TITLES:
                    continue
                if _target_text_matches(text_value, target):
                    matched_text = text_value
                    break
                if (not fallback_text) and (not _is_blocked_chat_target(text_value)):
                    fallback_text = text_value
            if matched_text:
                target_candidates.append((cy, item, matched_text))
            elif fallback_text:
                fallback_candidates.append((cy, item, fallback_text))
        selected_pool = target_candidates if target_candidates else fallback_candidates
        if selected_pool:
            selected_pool.sort(key=lambda row: row[0])
            _, direct_item, matched_text = selected_pool[0]
            direct_item.click_input(double=True)
            time.sleep(0.36)
            detail_key = (
                "direct_contact_double_click"
                if target_candidates
                else "direct_contact_first_friend"
            )
            direct_click_detail = f"{detail_key}={matched_text}"
            direct_verify_ok, direct_verify_detail = _verify_chat_surface_ready(
                target=target,
                allow_unverified_without_profile=False,
            )
            if direct_verify_ok:
                return True, (
                    f"open_chat_by_contacts_search target={target}; {nav_detail}; "
                    f"{direct_click_detail}; {direct_verify_detail}"
                )
            direct_click_detail = f"{direct_click_detail}; {direct_verify_detail}"
    except Exception as exc:
        direct_click_detail = f"direct_contact_probe_failed={exc.__class__.__name__}"

    if direct_click_detail == "direct_contact_not_found":
        fallback_x = int(left + width * 0.17)
        fallback_y = int(top + height * 0.54)
        try:
            pyautogui.doubleClick(fallback_x, fallback_y)
            time.sleep(0.34)
            direct_click_detail = (
                f"direct_contact_coord_fallback x={fallback_x} y={fallback_y}"
            )
            coord_verify_ok, coord_verify_detail = _verify_chat_surface_ready(
                target=target,
                allow_unverified_without_profile=False,
            )
            if coord_verify_ok:
                return True, (
                    f"open_chat_by_contacts_search target={target}; {nav_detail}; "
                    f"{direct_click_detail}; {coord_verify_detail}"
                )
            direct_click_detail = f"{direct_click_detail}; {coord_verify_detail}"
        except Exception as exc:
            direct_click_detail = f"direct_contact_coord_failed={exc.__class__.__name__}"

    time.sleep(0.20)
    search_x = int(left + width * 0.18)
    search_y = int(top + height * 0.10)
    try:
        pyautogui.FAILSAFE = False
        pyautogui.click(search_x, search_y)
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        clip_ok, _ = _set_clipboard_text(str(target or ""))
        if clip_ok:
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(str(target or ""), interval=0.02)
        time.sleep(0.26)
        pyautogui.press("enter")
        time.sleep(0.42)
        pyautogui.press("enter")
        time.sleep(0.30)
    except Exception as exc:
        return False, f"contacts_search_failed: {exc.__class__.__name__}: {exc}"

    verify_hwnd = _find_wechat_main_window()
    msg_button_detail = "message_button_skip_no_hwnd"
    if verify_hwnd:
        click_ok, click_detail = _click_contact_profile_message_button(hwnd=int(verify_hwnd))
        msg_button_detail = click_detail if click_ok else f"message_button_click_failed={click_detail}"
    verify_ok, verify_detail = _verify_chat_surface_ready(target=target)
    if verify_ok:
        return True, (
            f"open_chat_by_contacts_search target={target}; {nav_detail}; "
            f"{direct_click_detail}; {msg_button_detail}; {verify_detail}"
        )

    retry_detail = "retry_not_needed"
    retry_hwnd = _find_wechat_main_window()
    if retry_hwnd and _is_contact_profile_layout(hwnd=int(retry_hwnd)):
        click_ok, click_detail = _click_contact_profile_message_button(hwnd=int(retry_hwnd))
        time.sleep(0.26)
        retry_ok, retry_verify_detail = _verify_chat_surface_ready(target=target)
        retry_detail = f"{click_detail}; {retry_verify_detail}"
        if click_ok and retry_ok:
            return True, (
                f"open_chat_by_contacts_search target={target}; {nav_detail}; "
                f"{direct_click_detail}; {msg_button_detail}; retry={retry_detail}"
            )
    else:
        retry_ok, retry_verify_detail = _verify_chat_surface_ready(target=target)
        retry_detail = retry_verify_detail
        if retry_ok:
            return True, (
                f"open_chat_by_contacts_search target={target}; {nav_detail}; "
                f"{direct_click_detail}; {msg_button_detail}; retry={retry_detail}"
            )

    try:
        pyautogui.hotkey("ctrl", "1")
        time.sleep(0.22)
    except Exception:
        pass
    fallback_list_ok, fallback_list_detail = _try_open_chat_by_list_click(target=target)
    if fallback_list_ok:
        fallback_verify_ok, fallback_verify_detail = _verify_chat_surface_ready(target=target)
        if fallback_verify_ok:
            return True, (
                f"open_chat_by_contacts_search target={target}; {nav_detail}; "
                f"{direct_click_detail}; {msg_button_detail}; fallback_chat_list={fallback_list_detail}; "
                f"{fallback_verify_detail}"
            )
        fallback_list_detail = f"{fallback_list_detail}; {fallback_verify_detail}"

    return False, (
        f"contacts_search_no_chat_input target={target}; {nav_detail}; "
        f"{direct_click_detail}; {msg_button_detail}; verify={verify_detail}; retry={retry_detail}; "
        f"fallback_list={fallback_list_detail}"
    )


def _open_chat_by_target(target: str, wechat_path: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    ok, detail = _focus_wechat_main_window(wechat_path)
    if not ok:
        return False, detail
    focused_hwnd = _find_wechat_main_window()
    if focused_hwnd:
        desktop_locked = _is_windows_input_desktop_locked()
        if desktop_locked is True:
            return False, f"{detail}; desktop_locked"
        login_gate, login_detail = _detect_wechat_login_gate(hwnd=int(focused_hwnd))
        if login_gate:
            return False, f"{detail}; login_gate={login_detail}"
    if _is_active_chat_target(target):
        return True, f"{detail}; active_chat_target"
    chat_hwnd = _find_wechat_main_window()
    if chat_hwnd:
        chat_tab_ok, chat_tab_detail = _focus_chat_record_list(hwnd=chat_hwnd)
        detail = f"{detail}; {chat_tab_detail}" if chat_tab_detail else detail

    list_ok, list_detail = _try_open_chat_by_list_click(target=target)
    if list_ok:
        list_verify_ok, list_verify_detail = _verify_chat_surface_ready(target=target)
        if list_verify_ok:
            return True, f"{detail}; {list_detail}; {list_verify_detail}"
        list_detail = f"{list_detail}; {list_verify_detail}"

    contacts_ok, contacts_detail = _try_open_chat_by_contacts_search(target=target)
    if contacts_ok:
        return True, f"{detail}; {contacts_detail}; list_click={list_detail}"

    visual_ok, visual_detail = _visual_open_chat_by_target(target=target)
    if visual_ok:
        return True, f"{detail}; {visual_detail}; list_click={list_detail}; contacts={contacts_detail}"

    allow_global_search = _parse_bool(
        os.environ.get("EDGE_WECHAT_ALLOW_GLOBAL_SEARCH"),
        default=False,
    )
    if not allow_global_search:
        return False, (
            f"open_chat failed: list={list_detail}; contacts={contacts_detail}; visual={visual_detail}; "
            "global_search_disabled"
        )

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

    def _try_open_with_search_box() -> tuple[bool, str]:
        hwnd_local = _find_wechat_main_window()
        if not hwnd_local:
            return False, "wechat main window not found"
        rect_local = _get_window_rect(hwnd_local)
        if rect_local is None:
            return False, "GetWindowRect failed"
        lft, tp, rgt, btm = rect_local
        wd = max(0, int(rgt) - int(lft))
        ht = max(0, int(btm) - int(tp))
        if wd <= 0 or ht <= 0:
            return False, "invalid window rect"

        search_x = int(lft + wd * 0.18)
        search_y = int(tp + ht * 0.065)
        try:
            pyautogui.FAILSAFE = False
            pyautogui.click(search_x, search_y)
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            clip_ok, _clip_detail = _set_clipboard_text(str(target or ""))
            if clip_ok:
                pyautogui.hotkey("ctrl", "v")
            else:
                pyautogui.write(str(target or ""), interval=0.02)
            time.sleep(0.32)
            pyautogui.press("down")
            time.sleep(0.08)
            pyautogui.press("enter")
            time.sleep(0.45)
            pyautogui.press("escape")
            time.sleep(0.10)
            if _is_soso_page_active():
                return False, "stuck_on_soso_page"
            verify_ok, verify_detail = _verify_chat_surface_ready(target=target)
            if not verify_ok:
                return False, f"open_chat_search_box {verify_detail}"
            return True, f"open_chat_search_box; {verify_detail}"
        except Exception as exc:
            return False, f"{exc.__class__.__name__}: {exc}"

    search_ok, search_detail = _try_open_with_search_box()
    if search_ok:
        return True, f"{detail}; {search_detail}; list_click={list_detail}; contacts={contacts_detail}"

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
            verify_ok, verify_detail = _verify_chat_surface_ready(target=target)
            if not verify_ok:
                return False, f"open_chat_hotkey=ctrl+{hotkey_second} {verify_detail}"
            return True, f"open_chat_hotkey=ctrl+{hotkey_second}; {verify_detail}"
        except Exception as exc:
            return False, f"{exc.__class__.__name__}: {exc}"

    primary_ok, primary_detail = _try_open_with_hotkey("n")
    if primary_ok:
        return True, f"{detail}; {primary_detail}; list_click={list_detail}; contacts={contacts_detail}"

    fallback_ok, fallback_detail = _try_open_with_hotkey("f")
    if fallback_ok:
        return True, f"{detail}; {fallback_detail}; list_click={list_detail}; contacts={contacts_detail}"
    return False, (
        f"open_chat failed: list={list_detail}; contacts={contacts_detail}; visual={visual_detail}; "
        f"search={search_detail}; primary={primary_detail}; fallback={fallback_detail}"
    )


def _send_text(content: str, wechat_path: str, *, target: str) -> tuple[bool, str]:
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
    verify_ok, verify_detail = _verify_chat_surface_ready(target=target)
    if not verify_ok:
        return False, f"chat_surface_not_ready: {verify_detail}"

    try:
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyautogui.press("enter")
        return True, f"ok ({input_detail}; {verify_detail})"
    except Exception as exc:
        return False, f"send_text failed: {exc.__class__.__name__}: {exc}"


def _send_text_active_chat_fast(content: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    clip_ok, clip_detail = _set_clipboard_text(content)
    if not clip_ok:
        return False, f"clipboard failed: {clip_detail}"

    hwnd = _find_wechat_main_window()
    if not hwnd:
        return False, "wechat main window not found"
    try:
        user32 = ctypes.windll.user32
        sw_restore = 9
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass

    try:
        pyautogui.FAILSAFE = False
        time.sleep(0.08)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.12)
        pyautogui.press("enter")
        return True, "active_chat_fast_paste_enter"
    except Exception as exc:
        return False, f"active_chat_fast_send failed: {exc.__class__.__name__}: {exc}"


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


def _collect_command_target_candidates(command: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text_value = str(value or "").strip()
        if not text_value:
            return
        if _is_blocked_chat_target(text_value):
            return
        key = _normalize_target_hint(text_value) or text_value.lower()
        if not key:
            return
        if key in seen:
            return
        seen.add(key)
        candidates.append(text_value)

    _add(command.get("target"))
    raw_candidates = command.get("target_candidates")
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            _add(item)

    allowed_names = _resolve_allowed_contact_names()
    if not allowed_names:
        return candidates

    filtered = [item for item in candidates if _is_allowed_contact_target(item)]
    if filtered:
        return filtered
    return [allowed_names[0]]


def _execute_command(command: dict[str, Any], *, wechat_path: str) -> dict[str, Any]:
    action = str(command.get("action") or "").strip().lower()
    target_candidates = _collect_command_target_candidates(command)
    target = str(target_candidates[0] if target_candidates else "").strip()
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
    if action not in {"open_chat", "send_text", "voice_call", "video_call"}:
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
        active_only = bool(target_candidates) and all(_is_active_chat_target(item) for item in target_candidates)
        if active_only:
            send_ok, send_detail = _send_text_active_chat_fast(content=content)
            return {
                **base_row,
                "status": ("ok" if send_ok else "error"),
                "success": bool(send_ok),
                "reason_code": (None if send_ok else "WECHAT_COMMAND_EXEC_FAILED"),
                "detail": send_detail,
                "resolved_target": "active_chat",
                "target_candidates": target_candidates,
            }

    focus_ok, focus_detail = _focus_wechat_main_window(wechat_path)
    if not focus_ok:
        return {
            **base_row,
            "status": "error",
            "success": False,
            "reason_code": "WECHAT_UI_FOCUS_FAILED",
            "detail": focus_detail,
        }

    login_blocked, login_detail = _detect_wechat_login_gate()
    if login_blocked:
        return {
            **base_row,
            "status": "error",
            "success": False,
            "reason_code": "WECHAT_NOT_LOGGED_IN",
            "detail": login_detail,
        }

    open_failures: list[str] = []
    exec_failures: list[str] = []
    resolved_target = target

    if action == "open_chat":
        ok = False
        detail = ""
        for candidate in target_candidates:
            open_ok, open_detail = _open_chat_by_target(target=candidate, wechat_path=wechat_path)
            if open_ok:
                resolved_target = candidate
                ok = True
                detail = f"{open_detail} (resolved_target={candidate})"
                break
            open_failures.append(f"{candidate}:{open_detail}")
        if not ok:
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": "open_chat failed: " + " | ".join(open_failures[:4]),
            }
    elif action == "send_text":
        ok = False
        detail = ""
        for candidate in target_candidates:
            open_ok, open_detail = _open_chat_by_target(target=candidate, wechat_path=wechat_path)
            if not open_ok:
                open_failures.append(f"{candidate}:{open_detail}")
                continue
            send_ok, send_detail = _send_text(content=content, wechat_path=wechat_path, target=candidate)
            if send_ok:
                _remember_outbound_text(content)
                resolved_target = candidate
                ok = True
                detail = f"{send_detail} ({open_detail}; resolved_target={candidate})"
                break
            exec_failures.append(f"{candidate}:{send_detail}")
        if not ok:
            if exec_failures:
                return {
                    **base_row,
                    "status": "error",
                    "success": False,
                    "reason_code": "WECHAT_COMMAND_EXEC_FAILED",
                    "detail": "send_text failed after open: " + " | ".join(exec_failures[:4]),
                }
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": "open_chat failed: " + " | ".join(open_failures[:4]),
            }
    elif action == "voice_call":
        ok = False
        detail = ""
        for candidate in target_candidates:
            open_ok, open_detail = _open_chat_by_target(target=candidate, wechat_path=wechat_path)
            if not open_ok:
                open_failures.append(f"{candidate}:{open_detail}")
                continue
            call_ok, call_detail = _click_call_button(mode="voice_call", wechat_path=wechat_path)
            if call_ok:
                resolved_target = candidate
                ok = True
                detail = f"{call_detail} ({open_detail}; resolved_target={candidate})"
                break
            exec_failures.append(f"{candidate}:{call_detail}")
        if not ok:
            if exec_failures:
                return {
                    **base_row,
                    "status": "error",
                    "success": False,
                    "reason_code": "WECHAT_COMMAND_EXEC_FAILED",
                    "detail": "voice_call failed after open: " + " | ".join(exec_failures[:4]),
                }
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": "open_chat failed: " + " | ".join(open_failures[:4]),
            }
    else:
        ok = False
        detail = ""
        for candidate in target_candidates:
            open_ok, open_detail = _open_chat_by_target(target=candidate, wechat_path=wechat_path)
            if not open_ok:
                open_failures.append(f"{candidate}:{open_detail}")
                continue
            call_ok, call_detail = _click_call_button(mode="video_call", wechat_path=wechat_path)
            if call_ok:
                resolved_target = candidate
                ok = True
                detail = f"{call_detail} ({open_detail}; resolved_target={candidate})"
                break
            exec_failures.append(f"{candidate}:{call_detail}")
        if not ok:
            if exec_failures:
                return {
                    **base_row,
                    "status": "error",
                    "success": False,
                    "reason_code": "WECHAT_COMMAND_EXEC_FAILED",
                    "detail": "video_call failed after open: " + " | ".join(exec_failures[:4]),
                }
            return {
                **base_row,
                "status": "error",
                "success": False,
                "reason_code": "WECHAT_UI_OPEN_CHAT_FAILED",
                "detail": "open_chat failed: " + " | ".join(open_failures[:4]),
            }

    return {
        **base_row,
        "status": ("ok" if ok else "error"),
        "success": bool(ok),
        "reason_code": (None if ok else "WECHAT_COMMAND_EXEC_FAILED"),
        "detail": (detail if ok else detail),
        "resolved_target": resolved_target,
        "target_candidates": target_candidates,
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
        help=f"Inbound backend: uia_hook|uia_ocr|vision|win_notify|vision_notify|off (default: {DEFAULT_INBOUND_BACKEND}).",
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
    elif inbound_backend not in {"uia_hook", "uia_ocr", "vision", "win_notify", "vision_notify"}:
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
    uia_auto_recover = _parse_bool(
        os.environ.get("EDGE_WECHAT_UIA_AUTO_RECOVER"),
        default=DEFAULT_UIA_AUTO_RECOVER,
    )
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
            "force_active_chat_target=%s auto_recover=%s active_x_max=%.2f vision_model=%s"
        ),
        args.edge_url,
        queue_file,
        result_file,
        wechat_path or "(auto)",
        listener_enabled,
        inbound_backend,
        uia_scan_chat_list,
        force_active_chat_target,
        uia_auto_recover,
        uia_active_inbound_x_ratio_max,
        vision_model,
    )

    if inbound_backend == "vision_notify":
        vision_bridge = WeChatVisionInboundBridge(
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
        notify_bridge = WeChatWindowsNotificationInboundBridge(
            edge_url=str(args.edge_url or DEFAULT_EDGE_URL),
            enabled=listener_enabled,
            poll_interval_s=float(args.listener_poll_interval),
            callback_timeout_s=float(args.callback_timeout),
            dedup_window_s=float(args.listener_dedup_window),
            db_path=str(args.notify_db_path or DEFAULT_NOTIFY_DB_PATH),
            handler_keywords=notify_keywords,
        )
        inbound_bridge = WeChatVisionNotifyInboundBridge(
            vision_bridge=vision_bridge,
            notify_bridge=notify_bridge,
        )
    elif inbound_backend == "win_notify":
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
