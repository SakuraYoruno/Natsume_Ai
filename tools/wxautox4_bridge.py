"""wxautox4 Bridge — drop-in replacement for wechat_uia_bridge.py.

Uses the ``wxautox4`` Python package (pip install wxautox4) instead of raw
comtypes UIAutomation COM calls.  The **external protocol is identical**
to the legacy UIA bridge:

  Inbound  → HTTP POST to Gateway webhook  ``/v1/gateway/wechat/webhook``
  Outbound → poll JSONL command queue → execute wxautox4 API → write result

Switch between backends via a single env-var:

    EDGE_WECHAT_BRIDGE_BACKEND=wxautox4   # use this bridge
    EDGE_WECHAT_BRIDGE_BACKEND=uia        # use legacy UIA bridge

Requires:
  - wxautox4 activated (``wxautox4 -a <activation_code>``)
  - WeChat 4.1+ running and logged-in on the same desktop

Phase 1 additions (2026-03-27):
  - IsOnline() health check with periodic keepalive
  - GetMyInfo() self-info at startup
  - get_friends / get_groups / get_recent_chats query actions
  - get_history_messages query action
  - media download on inbound messages (image/video/voice)
  - RemoveListenChat / StopListening management actions
  - ChatWith navigation action
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("Natsume.wxautox4_bridge")

# ---------------------------------------------------------------------------
# Constants — kept in sync with wechat_uia_bridge.py
# ---------------------------------------------------------------------------
DEFAULT_EDGE_URL = "http://127.0.0.1:19000"
CALLBACK_PATH = "/v1/gateway/wechat/webhook"
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
DEFAULT_CALLBACK_TIMEOUT_S = 6.0
DEFAULT_INBOUND_POLL_INTERVAL_S = 1.0
DEFAULT_DEDUP_WINDOW_S = 12.0
DEFAULT_HEALTH_CHECK_INTERVAL_S = 30.0   # Phase 1: IsOnline keepalive interval
DEFAULT_MEDIA_DOWNLOAD_DIR = ""          # Phase 1: empty = no auto-download

# Media type codes matching the UIA bridge convention
MEDIA_TYPE_CODE_MAP: dict[str, int] = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "video": 43,
    "emoji": 47,
    "link": 49,
    "file": 49,      # wxautox4 may report files as link/49
    "system": 10000,
}


# ---------------------------------------------------------------------------
# Helpers — identical to wechat_uia_bridge.py helpers
# ---------------------------------------------------------------------------

def _normalize_optional_path(raw: str) -> str:
    """Return stripped path string, or empty string if empty/None."""
    return str(raw or "").strip()


def _ensure_parent_dir(path_text: str) -> None:
    parent = Path(path_text).parent
    parent.mkdir(parents=True, exist_ok=True)


def _build_msg_id(*, sender: str, content: str, ts_ms: int) -> str:
    digest = hashlib.sha1(
        f"{sender}|{content}|{ts_ms}".encode("utf-8", errors="ignore"),
    ).hexdigest()[:12]
    return f"wxautox4-{ts_ms}-{digest}"


def _read_jsonl_rows_from_offset(
    path_value: str, offset: int,
) -> tuple[list[dict[str, Any]], int]:
    path_text = _normalize_optional_path(path_value)
    if not path_text or not os.path.exists(path_text):
        return [], 0
    with open(path_text, "r", encoding="utf-8") as handle:
        handle.seek(max(0, int(offset)))
        rows: list[dict[str, Any]] = []
        for raw in handle:
            line = str(raw).strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows, int(handle.tell())


def _append_jsonl_row(path_value: str, row: dict[str, Any]) -> None:
    path_text = _normalize_optional_path(path_value)
    if not path_text:
        return
    _ensure_parent_dir(path_text)
    with open(path_text, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _post_to_edge_callback(
    edge_url: str, body: dict[str, Any], *, timeout_s: float,
) -> bool:
    """POST inbound message to Gateway webhook. Returns True on success."""
    base = str(edge_url or DEFAULT_EDGE_URL).strip().rstrip("/")
    url = f"{base}{CALLBACK_PATH}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=max(0.2, float(timeout_s)),
        ) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        logger.warning("edge callback rejected code=%s body=%s", exc.code, exc.read())
        return False
    except Exception as exc:
        logger.warning("edge callback failed: %s", exc)
        return False
    try:
        parsed = json.loads(payload.decode("utf-8", errors="ignore") or "{}")
    except Exception:
        return True
    if (
        isinstance(parsed, dict)
        and parsed.get("success") is False
        and str(parsed.get("status") or "").lower() == "rejected"
    ):
        return False
    return True


def _resolve_msg_type_code(msg: Any) -> int:
    """Map a wxautox4 message object to a WeChat raw type code integer."""
    # wxautox4 WxMsg may have a .type attribute (str or int)
    raw_type = getattr(msg, "type", None)
    if isinstance(raw_type, int):
        return raw_type
    if isinstance(raw_type, str):
        return MEDIA_TYPE_CODE_MAP.get(raw_type.lower(), 1)
    # Fallback: check content for media hints
    content = str(getattr(msg, "content", "") or "")
    if content.startswith("[图片]") or content.startswith("[Image]"):
        return 3
    if content.startswith("[语音]") or content.startswith("[Voice]"):
        return 34
    if content.startswith("[视频]") or content.startswith("[Video]"):
        return 43
    return 1  # default: text


def _safe_serialize(obj: Any) -> Any:
    """Recursively convert wxautox4 objects to JSON-serializable types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    # wxautox4 objects: try __dict__ first, then str
    d = getattr(obj, "__dict__", None)
    if d and isinstance(d, dict):
        return {str(k): _safe_serialize(v) for k, v in d.items() if not k.startswith("_")}
    return str(obj)


# ---------------------------------------------------------------------------
# Phase 1: HealthMonitor — periodic IsOnline keepalive
# ---------------------------------------------------------------------------

class HealthMonitor:
    """Runs periodic wx.IsOnline() checks and logs online/offline transitions.

    Can be extended later to push health events to the Gateway webhook.
    """

    def __init__(
        self,
        wx: Any,
        *,
        check_interval_s: float = DEFAULT_HEALTH_CHECK_INTERVAL_S,
    ) -> None:
        self._wx = wx
        self._interval = max(5.0, check_interval_s)
        self._shutdown = threading.Event()
        self._last_online: bool | None = None

    def run(self) -> None:
        logger.info("HealthMonitor started (interval=%.0fs)", self._interval)
        while not self._shutdown.is_set():
            self._check()
            self._shutdown.wait(timeout=self._interval)
        logger.info("HealthMonitor stopped.")

    def stop(self) -> None:
        self._shutdown.set()

    def _check(self) -> None:
        try:
            online = bool(self._wx.IsOnline())
        except Exception as exc:
            logger.warning("HealthMonitor: IsOnline() error: %s", exc)
            return
        if online != self._last_online:
            if online:
                logger.info("WeChat online status: ONLINE ✓")
            else:
                logger.warning("WeChat online status: OFFLINE — WeChat may have been closed or logged out!")
            self._last_online = online


# ---------------------------------------------------------------------------
# Inbound processor — receives messages from wxautox4 and posts to Gateway
# ---------------------------------------------------------------------------

class InboundProcessor:
    """Polls wxautox4 for new messages and forwards them via webhook POST."""

    def __init__(
        self,
        wx: Any,
        *,
        edge_url: str = DEFAULT_EDGE_URL,
        poll_interval_s: float = DEFAULT_INBOUND_POLL_INTERVAL_S,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        callback_timeout_s: float = DEFAULT_CALLBACK_TIMEOUT_S,
        listen_targets: list[str] | None = None,
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,  # Phase 1
    ) -> None:
        self._wx = wx
        self._edge_url = edge_url
        self._poll_interval_s = max(0.2, poll_interval_s)
        self._dedup_window_s = max(1.0, dedup_window_s)
        self._callback_timeout_s = callback_timeout_s
        self._listen_targets = list(listen_targets or [])
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._shutdown = threading.Event()
        self._recent_signatures: dict[str, float] = {}  # signature -> mono timestamp
        self._stats = {"forwarded": 0, "deduped": 0, "errors": 0}

    def setup_listeners(self) -> None:
        """Register chat listeners with wxautox4."""
        for target in self._listen_targets:
            target = str(target or "").strip()
            if not target:
                continue
            try:
                self._wx.AddListenChat(who=target)
                logger.info("wxautox4: registered listener for '%s'", target)
            except Exception as exc:
                logger.warning("wxautox4: failed to register listener for '%s': %s", target, exc)

    def run(self) -> None:
        """Main poll loop — call from a daemon thread."""
        logger.info("InboundProcessor started (poll=%.1fs, dedup=%.1fs)", self._poll_interval_s, self._dedup_window_s)
        while not self._shutdown.is_set():
            try:
                self._poll_cycle()
            except Exception as exc:
                logger.warning("InboundProcessor poll error: %s", exc)
                self._stats["errors"] += 1
            self._shutdown.wait(timeout=self._poll_interval_s)
        logger.info("InboundProcessor stopped. stats=%s", self._stats)

    def stop(self) -> None:
        self._shutdown.set()

    def _poll_cycle(self) -> None:
        """Single poll iteration: get new messages and forward them."""
        try:
            msgs = self._wx.GetNewMessage()
        except Exception as exc:
            logger.debug("GetNewMessage error: %s", exc)
            return

        if not msgs:
            return

        ts_ms = int(time.time() * 1000)

        # wxautox4 GetNewMessage() returns:
        #   dict[str, list[WxMsg]]  — keyed by chat name / conversation_id
        if isinstance(msgs, dict):
            for chat_name, msg_list in msgs.items():
                if not isinstance(msg_list, (list, tuple)):
                    msg_list = [msg_list]
                for msg in msg_list:
                    self._forward_one_message(msg, chat_name=chat_name, ts_ms=ts_ms)
        elif isinstance(msgs, (list, tuple)):
            # Fallback: if wxautox4 returns a flat list
            for msg in msgs:
                chat_name = str(getattr(msg, "roomid", None) or getattr(msg, "sender", "") or "")
                self._forward_one_message(msg, chat_name=chat_name, ts_ms=ts_ms)

    def _try_download_media(self, msg: Any) -> str:
        """Phase 1: Attempt to download media file from message object.

        Returns local file path on success, or empty string on failure/skip.
        """
        if not self._media_download_dir:
            return ""
        msg_type_code = _resolve_msg_type_code(msg)
        # Only attempt download for image(3), voice(34), video(43)
        if msg_type_code not in (3, 34, 43):
            return ""
        save_dir = Path(self._media_download_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_path = msg.download(savepath=str(save_dir))
            if local_path and os.path.isfile(str(local_path)):
                logger.info("media downloaded: %s", local_path)
                return str(local_path)
        except AttributeError:
            pass  # message type has no download() method
        except Exception as exc:
            logger.debug("media download failed: %s", exc)
        return ""

    def _forward_one_message(self, msg: Any, *, chat_name: str, ts_ms: int) -> None:
        """Forward a single wxautox4 message to the edge callback."""
        sender = str(
            getattr(msg, "sender_name", None)
            or getattr(msg, "sender", None)
            or chat_name
            or ""
        ).strip()
        content = str(getattr(msg, "content", None) or str(msg) or "").strip()
        if not content:
            return

        # Dedup
        signature = f"{sender}|{content}|{ts_ms // 1000}"
        if self._is_recent_duplicate(signature):
            self._stats["deduped"] += 1
            return

        msg_id = _build_msg_id(sender=sender, content=content, ts_ms=ts_ms)
        room_id = str(getattr(msg, "roomid", None) or "").strip()
        is_group_hint = bool(room_id) or (sender != chat_name and bool(chat_name))
        msg_type_code = _resolve_msg_type_code(msg)

        # Phase 1: try to download media and attach local path
        media_path = str(getattr(msg, "media_path", None) or getattr(msg, "file_path", None) or "").strip()
        if not media_path:
            media_path = self._try_download_media(msg)

        # Build webhook body — format identical to wechat_uia_bridge.py
        body: dict[str, Any] = {
            "id": msg_id,
            "msg_id": msg_id,
            "sender": sender,
            "sender_name": sender,
            "from_wxid": str(getattr(msg, "sender", None) or sender),
            "receiver": "local_wechat",
            "to_wxid": "local_wechat",
            "conversation_id": room_id or chat_name or sender,
            "content": content,
            "text": content,
            "type": msg_type_code,
            "msg_type": str(msg_type_code),
            "ts": int(ts_ms / 1000),
            "bridge_backend": "wxautox4",
            "is_group_hint": is_group_hint,
        }

        if media_path:
            body["media_url"] = media_path

        if _post_to_edge_callback(self._edge_url, body, timeout_s=self._callback_timeout_s):
            self._stats["forwarded"] += 1
            logger.info("forwarded inbound: sender=%s content=%.80s", sender, content)
        else:
            self._stats["errors"] += 1

    def _is_recent_duplicate(self, signature: str) -> bool:
        now_mono = time.monotonic()
        # Prune stale entries
        stale = [k for k, ts in self._recent_signatures.items() if (now_mono - ts) > self._dedup_window_s]
        for k in stale:
            self._recent_signatures.pop(k, None)
        if signature in self._recent_signatures:
            return True
        self._recent_signatures[signature] = now_mono
        return False


# ---------------------------------------------------------------------------
# Outbound processor — consumes JSONL command queue, executes wxautox4 API
# ---------------------------------------------------------------------------

class OutboundProcessor:
    """Polls the JSONL command queue and dispatches commands via wxautox4.

    Phase 1 additions:
      - get_friends        → wx.GetFriends()
      - get_groups         → wx.GetRecentGroups()  (or GetAllSubWindow groups)
      - get_recent_chats   → wx.GetSession() (recent session list)
      - get_history_messages → wx.GetHistoryMessage(who, savepath)
      - get_my_info        → wx.GetMyInfo()
      - listen_add         → wx.AddListenChat(who)
      - listen_remove      → wx.RemoveListenChat(who)
      - listen_stop        → wx.StopListening()
      - chat_with          → wx.ChatWith(who)
    """

    # All supported actions (Phase 1 additions marked with # P1)
    SUPPORTED_ACTIONS = {
        # Original
        "send_text",
        "send_image",
        "send_file",
        "send_voice",
        # Phase 1 — query
        "get_friends",           # P1
        "get_groups",            # P1
        "get_recent_chats",      # P1
        "get_history_messages",  # P1
        "get_my_info",           # P1
        # Phase 1 — listen management
        "listen_add",            # P1
        "listen_remove",         # P1
        "listen_stop",           # P1
        # Phase 1 — navigation
        "chat_with",             # P1
    }

    def __init__(
        self,
        wx: Any,
        *,
        command_queue_file: str,
        command_result_file: str,
        poll_interval_s: float = DEFAULT_COMMAND_POLL_INTERVAL_S,
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,  # Phase 1
    ) -> None:
        self._wx = wx
        self._queue_file = command_queue_file
        self._result_file = command_result_file
        self._poll_interval_s = max(0.1, poll_interval_s)
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._shutdown = threading.Event()
        self._read_offset: int = 0
        self._processed_ids: set[str] = set()
        self._stats = {"executed": 0, "failed": 0, "unsupported": 0}

    def run(self) -> None:
        """Main command queue consumer loop — call from a daemon thread."""
        logger.info("OutboundProcessor started (poll=%.1fs)", self._poll_interval_s)
        while not self._shutdown.is_set():
            try:
                self._process_cycle()
            except Exception as exc:
                logger.warning("OutboundProcessor cycle error: %s", exc)
            self._shutdown.wait(timeout=self._poll_interval_s)
        logger.info("OutboundProcessor stopped. stats=%s", self._stats)

    def stop(self) -> None:
        self._shutdown.set()

    def _process_cycle(self) -> None:
        rows, new_offset = _read_jsonl_rows_from_offset(self._queue_file, self._read_offset)
        if new_offset > self._read_offset:
            self._read_offset = new_offset
        for cmd in rows:
            cmd_id = str(cmd.get("command_id") or "").strip()
            if not cmd_id or cmd_id in self._processed_ids:
                continue
            self._processed_ids.add(cmd_id)
            self._execute_command(cmd)

    def _execute_command(self, command: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        command_id = str(command.get("command_id") or "").strip() or f"wxautox4-{now_ms}"
        request_id = str(command.get("request_id") or "").strip()
        action = str(command.get("action") or "").strip().lower()
        target = str(command.get("target") or "").strip()
        content = str(command.get("content") or "")

        # Resolve target candidates — first non-empty wins
        candidates = []
        for raw in [target, *list(command.get("target_candidates") or [])]:
            text = str(raw or "").strip()
            if text and text not in candidates:
                candidates.append(text)
        chat_target = candidates[0] if candidates else ""

        result: dict[str, Any] = {
            "command_id": command_id,
            "request_id": request_id,
            "action": action,
            "target": target,
            "content": content,
            "ts_ms": now_ms,
            "success": False,
            "status": "error",
            "reason_code": None,
            "detail": "",
        }

        try:
            if action == "send_text":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "no chat target specified"
                    self._stats["failed"] += 1
                    logger.warning("command %s: no target", command_id)
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.SendMsg(content, who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: send_text to '%s' OK", command_id, chat_target)

            elif action in ("send_image", "send_file", "send_voice"):
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "no chat target specified"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                media_path = str(command.get("media_path") or content or "").strip()
                if not media_path or not os.path.isfile(media_path):
                    result["reason_code"] = "WECHAT_MEDIA_FILE_NOT_FOUND"
                    result["detail"] = f"media file not found: {media_path}"
                    self._stats["failed"] += 1
                else:
                    self._wx.SendFiles(media_path, who=chat_target)
                    result["success"] = True
                    result["status"] = "ok"
                    result["resolved_target"] = chat_target
                    self._stats["executed"] += 1
                    logger.info("command %s: %s to '%s' OK", command_id, action, chat_target)

            elif action in {"voice_call", "video_call"}:
                result["reason_code"] = "WECHAT_CALL_NOT_SUPPORTED"
                result["detail"] = f"wxautox4 does not support {action}"
                self._stats["unsupported"] += 1
                logger.warning("command %s: unsupported action '%s'", command_id, action)

            # ── Phase 1: Query actions ──────────────────────────────────────

            elif action == "get_my_info":
                info = self._wx.GetMyInfo()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(info)
                self._stats["executed"] += 1
                logger.info("command %s: get_my_info OK", command_id)

            elif action == "get_friends":
                friends = self._wx.GetFriends()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(friends)
                result["count"] = len(friends) if isinstance(friends, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_friends OK count=%s", command_id, result["count"])

            elif action == "get_groups":
                # wxautox4 v40.1.x: GetRecentGroups() returns recently active groups
                try:
                    groups = self._wx.GetRecentGroups()
                except AttributeError:
                    # Fallback: some versions use a different method name
                    groups = self._wx.GetAllSubWindow()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(groups)
                result["count"] = len(groups) if isinstance(groups, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_groups OK count=%s", command_id, result["count"])

            elif action == "get_recent_chats":
                # GetSession() returns the recent chat sessions list
                sessions = self._wx.GetSession()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(sessions)
                result["count"] = len(sessions) if isinstance(sessions, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_recent_chats OK count=%s", command_id, result["count"])

            elif action == "get_history_messages":
                # Required: target (who to get history from)
                # Optional: command["savepath"] or use media_download_dir
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "get_history_messages requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                savepath = str(command.get("savepath") or self._media_download_dir or "").strip()
                if not savepath:
                    # Return messages as data without saving to file
                    history = self._wx.GetHistoryMessage(who=chat_target)
                else:
                    Path(savepath).mkdir(parents=True, exist_ok=True)
                    history = self._wx.GetHistoryMessage(who=chat_target, savepath=savepath)
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(history)
                result["count"] = len(history) if isinstance(history, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_history_messages for '%s' OK count=%s", command_id, chat_target, result["count"])

            # ── Phase 1: Listen management ──────────────────────────────────

            elif action == "listen_add":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "listen_add requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.AddListenChat(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: listen_add '%s' OK", command_id, chat_target)

            elif action == "listen_remove":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "listen_remove requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.RemoveListenChat(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: listen_remove '%s' OK", command_id, chat_target)

            elif action == "listen_stop":
                self._wx.StopListening()
                result["success"] = True
                result["status"] = "ok"
                self._stats["executed"] += 1
                logger.info("command %s: listen_stop OK", command_id)

            # ── Phase 1: Navigation ─────────────────────────────────────────

            elif action == "chat_with":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "chat_with requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.ChatWith(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: chat_with '%s' OK", command_id, chat_target)

            else:
                result["reason_code"] = "WECHAT_UNKNOWN_ACTION"
                result["detail"] = f"unknown action: {action}"
                self._stats["unsupported"] += 1
                logger.warning("command %s: unknown action '%s'", command_id, action)

        except Exception as exc:
            result["reason_code"] = "WECHAT_WXAUTOX4_ERROR"
            result["detail"] = str(exc)
            self._stats["failed"] += 1
            logger.error("command %s: wxautox4 error: %s", command_id, exc)

        _append_jsonl_row(self._result_file, result)


# ---------------------------------------------------------------------------
# Main Bridge orchestrator
# ---------------------------------------------------------------------------

class WxAutoX4Bridge:
    """Top-level bridge that coordinates inbound + outbound processors.

    Lifecycle:
        bridge = WxAutoX4Bridge(...)
        bridge.start()   # blocks until shutdown

    Phase 1 additions:
        - HealthMonitor thread (IsOnline keepalive)
        - GetMyInfo() log at startup
        - media_download_dir support
        - health_check_interval_s config
    """

    def __init__(
        self,
        *,
        edge_url: str = DEFAULT_EDGE_URL,
        command_queue_file: str,
        command_result_file: str,
        command_poll_interval_s: float = DEFAULT_COMMAND_POLL_INTERVAL_S,
        inbound_poll_interval_s: float = DEFAULT_INBOUND_POLL_INTERVAL_S,
        callback_timeout_s: float = DEFAULT_CALLBACK_TIMEOUT_S,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        listen_targets: list[str] | None = None,
        health_check_interval_s: float = DEFAULT_HEALTH_CHECK_INTERVAL_S,  # Phase 1
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,              # Phase 1
    ) -> None:
        self._edge_url = edge_url
        self._queue_file = command_queue_file
        self._result_file = command_result_file
        self._command_poll_interval_s = command_poll_interval_s
        self._inbound_poll_interval_s = inbound_poll_interval_s
        self._callback_timeout_s = callback_timeout_s
        self._dedup_window_s = dedup_window_s
        self._listen_targets = list(listen_targets or [])
        self._health_check_interval_s = health_check_interval_s
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._shutdown = threading.Event()

    def start(self) -> int:
        """Initialize wxautox4 and run the bridge. Blocks until interrupted.

        Returns exit code (0 = clean, 1 = error).
        """
        logger.info("=" * 60)
        logger.info("wxautox4 Bridge for Natsume_Ai  [Phase 1]")
        logger.info("=" * 60)

        # ── Step 1: import and initialize wxautox4 ──
        try:
            from wxautox4 import WeChat  # type: ignore[import-untyped]
        except ImportError:
            logger.error(
                "wxautox4 not installed! Run: pip install wxautox4 && wxautox4 -a <activation_code>"
            )
            return 1

        logger.info("Initializing wxautox4.WeChat() — binding to WeChat window...")
        try:
            wx = WeChat()
        except Exception as exc:
            logger.error("Failed to initialize wxautox4.WeChat(): %s", exc)
            logger.error("Make sure WeChat 4.1+ is running and wxautox4 is activated.")
            return 1
        logger.info("wxautox4 bound to WeChat window successfully!")

        # ── Step 2: Phase 1 — log self info at startup ──
        try:
            my_info = wx.GetMyInfo()
            logger.info("WeChat self info: %s", _safe_serialize(my_info))
        except Exception as exc:
            logger.warning("GetMyInfo() failed (non-fatal): %s", exc)

        # ── Step 3: start HealthMonitor ──
        health = HealthMonitor(wx, check_interval_s=self._health_check_interval_s)
        health_thread = threading.Thread(
            target=health.run,
            name="wxautox4-health",
            daemon=True,
        )
        health_thread.start()

        # ── Step 4: start inbound processor ──
        inbound = InboundProcessor(
            wx,
            edge_url=self._edge_url,
            poll_interval_s=self._inbound_poll_interval_s,
            dedup_window_s=self._dedup_window_s,
            callback_timeout_s=self._callback_timeout_s,
            listen_targets=self._listen_targets,
            media_download_dir=self._media_download_dir,
        )
        inbound.setup_listeners()

        inbound_thread = threading.Thread(
            target=inbound.run,
            name="wxautox4-inbound",
            daemon=True,
        )
        inbound_thread.start()

        # ── Step 5: start outbound processor ──
        outbound = OutboundProcessor(
            wx,
            command_queue_file=self._queue_file,
            command_result_file=self._result_file,
            poll_interval_s=self._command_poll_interval_s,
            media_download_dir=self._media_download_dir,
        )

        outbound_thread = threading.Thread(
            target=outbound.run,
            name="wxautox4-outbound",
            daemon=True,
        )
        outbound_thread.start()

        # ── Step 6: keep alive ──
        logger.info("wxautox4 Bridge [Phase 1] is running. Press Ctrl+C to stop.")
        try:
            while not self._shutdown.is_set():
                self._shutdown.wait(timeout=1.0)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")

        health.stop()
        inbound.stop()
        outbound.stop()
        logger.info("wxautox4 Bridge stopped.")
        return 0


# ---------------------------------------------------------------------------
# CLI entry point — argument interface matches wechat_uia_bridge.py
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="wxautox4 Bridge for Natsume_Ai (drop-in replacement for UIA bridge) [Phase 1]",
    )
    parser.add_argument(
        "--edge-url",
        default=(os.environ.get("EDGE_URL") or DEFAULT_EDGE_URL).strip(),
        help="Gateway base URL (default: http://127.0.0.1:19000)",
    )
    parser.add_argument(
        "--command-poll-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_COMMAND_POLL_INTERVAL_S") or DEFAULT_COMMAND_POLL_INTERVAL_S,
        ),
        help="Command queue poll interval in seconds",
    )
    parser.add_argument(
        "--command-queue-file",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or ""),
        help="Path to JSONL command queue file",
    )
    parser.add_argument(
        "--command-result-file",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or ""),
        help="Path to JSONL command result file",
    )
    parser.add_argument(
        "--callback-timeout",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_CALLBACK_TIMEOUT_S") or DEFAULT_CALLBACK_TIMEOUT_S,
        ),
        help="Webhook callback timeout in seconds",
    )
    parser.add_argument(
        "--inbound-poll-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_INBOUND_POLL_INTERVAL_S") or DEFAULT_INBOUND_POLL_INTERVAL_S,
        ),
        help="Inbound message poll interval in seconds",
    )
    parser.add_argument(
        "--dedup-window",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_LISTENER_DEDUP_WINDOW_S") or DEFAULT_DEDUP_WINDOW_S,
        ),
        help="Deduplication window in seconds",
    )
    parser.add_argument(
        "--listen-targets",
        nargs="*",
        default=[
            t.strip()
            for t in str(os.environ.get("EDGE_WECHAT_WXAUTOX4_LISTEN_TARGETS") or "").split(",")
            if t.strip()
        ],
        help="Chat targets to listen to (space-separated); empty = rely on GetNewMessage()",
    )
    # Phase 1 new args
    parser.add_argument(
        "--health-check-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S") or DEFAULT_HEALTH_CHECK_INTERVAL_S,
        ),
        help="IsOnline health check interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--media-download-dir",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR") or ""),
        help="Directory to auto-download inbound media files (empty = disabled)",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Resolve command queue/result file paths (same logic as bootstrap.py)
    repo_root = Path(__file__).resolve().parents[1]
    runtime_edge = repo_root / "runtime" / "edge"
    runtime_edge.mkdir(parents=True, exist_ok=True)

    queue_file = args.command_queue_file or str(runtime_edge / "wechat_command_queue.jsonl")
    result_file = args.command_result_file or str(runtime_edge / "wechat_command_result.jsonl")

    bridge = WxAutoX4Bridge(
        edge_url=args.edge_url,
        command_queue_file=queue_file,
        command_result_file=result_file,
        command_poll_interval_s=args.command_poll_interval,
        inbound_poll_interval_s=args.inbound_poll_interval,
        callback_timeout_s=args.callback_timeout,
        dedup_window_s=args.dedup_window,
        listen_targets=args.listen_targets,
        health_check_interval_s=args.health_check_interval,
        media_download_dir=args.media_download_dir,
    )
    return bridge.start()


if __name__ == "__main__":
    sys.exit(main())
