"""
WeChat UIA bridge for Weixin/WeChat desktop clients.

This script polls pywechat's check_new_message() and forwards newly detected
messages to Edge callback endpoint:
  POST {edge_url}/v1/edge/wechat/callback
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any


DEFAULT_EDGE_URL = "http://127.0.0.1:17800"
CALLBACK_PATH = "/v1/edge/wechat/callback"
TIMEOUT_S = 10
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WECHAT_MAIN_WINDOW_CLASS = "WeChatMainWndForPC"
WECHAT_MAIN_WINDOW_CLASS_HINTS = {
    "wechatmainwndforpc",
    "weixinmainwndforpc",
    "weixinmainwnd",
}
WECHAT_WINDOW_TITLE_HINTS = ("微信", "Weixin", "WeChat")

logger = logging.getLogger("Natsume.wechat_pywechat_bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@dataclass(frozen=True)
class NormalizedMessage:
    friend_name: str
    friend_type: str
    sender: str
    content: str
    message_type: str
    room_id: str


def _is_string_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, (str, int, float, bool)) or item is None for item in value)


def _pick_value(item: dict[str, Any], key_hints: tuple[str, ...]) -> Any:
    # Exact hint hit.
    for hint in key_hints:
        if hint in item:
            return item.get(hint)
    # Fuzzy hint hit.
    for k, v in item.items():
        key_text = str(k)
        if any(hint in key_text for hint in key_hints):
            return v
    return None


def _normalize_batch(raw_batch: Any) -> list[NormalizedMessage]:
    if not raw_batch:
        return []
    if isinstance(raw_batch, str):
        try:
            raw_batch = json.loads(raw_batch)
        except Exception:
            return []
    if not isinstance(raw_batch, list):
        return []

    messages: list[NormalizedMessage] = []
    for row in raw_batch:
        if not isinstance(row, dict):
            continue

        friend_name = str(
            _pick_value(row, ("好友名称", "好友", "friend_name", "friend")) or ""
        ).strip()
        friend_type = str(_pick_value(row, ("好友类型", "friend_type")) or "").strip()

        contents_raw = _pick_value(row, ("消息内容", "新消息内容", "contents"))
        senders_raw = _pick_value(row, ("发送消息群成员", "新消息发送人", "senders"))
        types_raw = _pick_value(row, ("消息类型", "新消息类型", "types"))

        if not _is_string_list(contents_raw):
            continue
        contents = [str(x or "").strip() for x in contents_raw]
        if not contents:
            continue

        senders: list[str] = []
        if _is_string_list(senders_raw):
            senders = [str(x or "").strip() for x in senders_raw]
        msg_types: list[str] = []
        if _is_string_list(types_raw):
            msg_types = [str(x or "").strip() for x in types_raw]

        is_group = ("群" in friend_type) or ("group" in friend_type.lower())
        room_id = friend_name if is_group else ""
        for idx, content in enumerate(contents):
            if not content:
                continue
            sender = friend_name
            if idx < len(senders) and senders[idx]:
                sender = senders[idx]
            msg_type = "text"
            if idx < len(msg_types) and msg_types[idx]:
                msg_type = msg_types[idx]
            messages.append(
                NormalizedMessage(
                    friend_name=friend_name,
                    friend_type=friend_type,
                    sender=sender,
                    content=content,
                    message_type=msg_type,
                    room_id=room_id,
                )
            )
    return messages


def _post_to_edge(edge_url: str, body: dict[str, Any]) -> bool:
    url = f"{edge_url.rstrip('/')}{CALLBACK_PATH}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            if 200 <= int(resp.status) < 300:
                return True
            logger.warning("Edge callback status=%s", resp.status)
            return False
    except urllib.error.HTTPError as exc:
        logger.warning("Edge callback HTTP error: %s %s", exc.code, exc.reason)
        return False
    except Exception as exc:
        logger.warning("Edge callback failed: %s", exc)
        return False


def _build_msg_id(msg: NormalizedMessage, ts_ms: int) -> str:
    digest = hashlib.sha1(
        f"{msg.friend_name}|{msg.sender}|{msg.content}|{msg.message_type}|{ts_ms}".encode("utf-8")
    ).hexdigest()[:16]
    return f"pywechat-{ts_ms}-{digest}"


def _build_payload(msg: NormalizedMessage, ts_ms: int, self_wxid: str) -> dict[str, Any]:
    msg_id = _build_msg_id(msg, ts_ms)
    msg_type_text = str(msg.message_type or "")
    msg_type_lower = msg_type_text.lower()
    type_code = 1
    if ("图片" in msg_type_text) or ("image" in msg_type_lower):
        type_code = 3
    elif ("文件" in msg_type_text) or ("file" in msg_type_lower):
        type_code = 49
    elif ("语音" in msg_type_text) or ("voice" in msg_type_lower):
        type_code = 34

    sender = msg.sender or msg.friend_name or "wechat_uia"
    receiver = self_wxid or ""
    return {
        "id": msg_id,
        "msg_id": msg_id,
        "sender": sender,
        "from_wxid": sender,
        "receiver": receiver,
        "to_wxid": receiver,
        "content": msg.content,
        "text": msg.content,
        "type": type_code,
        "msg_type": str(type_code),
        "roomid": msg.room_id,
        "ts": ts_ms,
        "bridge_backend": "pywechat",
        "friend_name": msg.friend_name,
        "friend_type": msg.friend_type,
        "message_type_text": msg.message_type,
    }


def _is_wechat_client_running() -> bool:
    # pywechat's built-in probe only checks WeChat.exe in some versions.
    # Weixin.exe is also valid in production deployments.
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

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('"'):
            process_name = line.split('","', 1)[0].strip('"').lower()
        else:
            process_name = line.split(",", 1)[0].strip().lower()
        if process_name in WECHAT_PROCESS_NAMES:
            return True
    return False


def _patch_pywechat_process_probe(pywechat_auto: Any, pywechat_tools: Any) -> None:
    def _compat_is_wechat_running() -> bool:
        return _is_wechat_client_running()

    patched_targets: list[str] = []
    try:
        if hasattr(pywechat_tools, "Tools"):
            setattr(
                pywechat_tools.Tools,
                "is_wechat_running",
                staticmethod(_compat_is_wechat_running),
            )
            patched_targets.append("WechatTools.Tools.is_wechat_running")
    except Exception as exc:
        logger.warning("failed to patch WechatTools.Tools.is_wechat_running: %s", exc)

    try:
        if hasattr(pywechat_tools, "is_wechat_running"):
            setattr(pywechat_tools, "is_wechat_running", _compat_is_wechat_running)
            patched_targets.append("WechatTools.is_wechat_running")
    except Exception as exc:
        logger.warning("failed to patch WechatTools.is_wechat_running: %s", exc)

    try:
        if hasattr(pywechat_auto, "Tools"):
            setattr(
                pywechat_auto.Tools,
                "is_wechat_running",
                staticmethod(_compat_is_wechat_running),
            )
            patched_targets.append("WechatAuto.Tools.is_wechat_running")
    except Exception as exc:
        logger.warning("failed to patch WechatAuto.Tools.is_wechat_running: %s", exc)

    try:
        if hasattr(pywechat_auto, "is_wechat_running"):
            setattr(pywechat_auto, "is_wechat_running", _compat_is_wechat_running)
            patched_targets.append("WechatAuto.is_wechat_running")
    except Exception as exc:
        logger.warning("failed to patch WechatAuto.is_wechat_running: %s", exc)

    if patched_targets:
        logger.info("patched pywechat process probes: %s", ", ".join(patched_targets))
    else:
        logger.warning("did not patch any pywechat process probe target")


def _find_main_window_handle() -> int:
    try:
        exact = int(ctypes.windll.user32.FindWindowW(WECHAT_MAIN_WINDOW_CLASS, None))
        if exact:
            return exact
    except Exception:
        pass

    for hwnd, class_name, title in _enum_visible_windows():
        class_key = str(class_name or "").strip().lower()
        title_text = str(title or "").strip()
        if class_key in WECHAT_MAIN_WINDOW_CLASS_HINTS:
            return int(hwnd)
        if title_text and any(hint in title_text for hint in WECHAT_WINDOW_TITLE_HINTS):
            return int(hwnd)
    return 0


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


def _log_window_probe_snapshot(limit: int = 20) -> None:
    rows = _enum_visible_windows()
    if not rows:
        logger.info("window probe snapshot: no visible top-level windows")
        return
    parts = []
    for hwnd, class_name, title in rows[: max(1, int(limit))]:
        parts.append(f"0x{hwnd:X} class={class_name!r} title={title!r}")
    logger.info("window probe snapshot (%d): %s", len(rows), " | ".join(parts))


def _ensure_main_window_visible(wechat_path: str, timeout_s: float = 8.0) -> bool:
    if _find_main_window_handle():
        return True

    if wechat_path:
        try:
            subprocess.Popen([wechat_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            logger.warning("failed to launch wechat executable: %s", exc)

    deadline = time.time() + max(1.0, float(timeout_s))
    while time.time() < deadline:
        if _find_main_window_handle():
            return True
        time.sleep(0.25)
    _log_window_probe_snapshot(limit=15)
    return False


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


def _run_pywechat_call(fn: Any, kwargs: dict[str, Any]) -> tuple[bool, str]:
    try:
        fn(**kwargs)
        return True, "ok"
    except TypeError:
        pass
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"

    # Fallback for older pywechat signatures.
    compatibility = dict(kwargs)
    compatibility.pop("is_maximize", None)
    compatibility.pop("close_wechat", None)
    try:
        fn(**compatibility)
        return True, "ok(compat)"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


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


def _focus_wechat_main_window() -> tuple[bool, str]:
    hwnd = _find_main_window_handle()
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


def _send_text_via_hotkeys(target: str, content: str) -> tuple[bool, str]:
    try:
        import pyautogui
    except Exception as exc:
        return False, f"pyautogui import failed: {exc}"

    focus_ok, focus_detail = _focus_wechat_main_window()
    if not focus_ok:
        return False, focus_detail

    clip_ok, clip_detail = _set_clipboard_text(content)
    if not clip_ok:
        return False, f"clipboard failed: {clip_detail}"

    try:
        pyautogui.FAILSAFE = False
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.25)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        pyautogui.write(str(target or ""), interval=0.02)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.35)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.12)
        pyautogui.press("enter")
        return True, f"ok ({focus_detail})"
    except Exception as exc:
        return False, f"hotkey send failed: {exc.__class__.__name__}: {exc}"


def _execute_command(
    command: dict[str, Any],
    *,
    send_message_to_friend: Any,
    voice_call: Any,
    video_call: Any,
    wechat_path: str,
) -> dict[str, Any]:
    action = str(command.get("action") or "").strip().lower()
    target = str(command.get("target") or "").strip()
    content = str(command.get("content") or "")
    command_id = str(command.get("command_id") or "")
    request_id = str(command.get("request_id") or "")
    now_ms = int(time.time() * 1000)

    if not command_id:
        command_id = f"bridge-{now_ms}-{hashlib.sha1(str(command).encode('utf-8')).hexdigest()[:10]}"

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

    common_kwargs = {
        "friend": target,
        "wechat_path": (wechat_path or None),
        "is_maximize": True,
        "close_wechat": False,
    }
    if action == "send_text":
        ok, detail = _run_pywechat_call(
            send_message_to_friend,
            kwargs={**common_kwargs, "message": content},
        )
        if not ok:
            fallback_ok, fallback_detail = _send_text_via_hotkeys(target=target, content=content)
            if fallback_ok:
                ok = True
                detail = f"{detail}; fallback_hotkeys={fallback_detail}"
            else:
                detail = f"{detail}; fallback_hotkeys_failed={fallback_detail}"
    elif action == "voice_call":
        ok, detail = _run_pywechat_call(
            voice_call,
            kwargs=common_kwargs,
        )
    else:
        ok, detail = _run_pywechat_call(
            video_call,
            kwargs=common_kwargs,
        )

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
    send_message_to_friend: Any,
    voice_call: Any,
    video_call: Any,
    wechat_path: str,
) -> tuple[int, int, int]:
    rows, next_offset = _read_jsonl_rows_from_offset(queue_file, offset)
    if not rows:
        return next_offset, 0, 0

    success_count = 0
    for command in rows:
        result = _execute_command(
            command,
            send_message_to_friend=send_message_to_friend,
            voice_call=voice_call,
            video_call=video_call,
            wechat_path=wechat_path,
        )
        if bool(result.get("success")):
            success_count += 1
        if result_file:
            try:
                _append_jsonl_row(result_file, result)
            except Exception as exc:
                logger.warning("failed to append command result: %s", exc)
    return next_offset, len(rows), success_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Poll pywechat unread messages and forward to Edge callback."
    )
    parser.add_argument(
        "--edge-url",
        default=DEFAULT_EDGE_URL,
        help=f"Edge base URL (default: {DEFAULT_EDGE_URL})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--wechat-path",
        default="",
        help="Optional WeChat/Weixin executable path for pywechat.",
    )
    parser.add_argument(
        "--self-wxid",
        default="",
        help="Optional self wxid used as to_wxid.",
    )
    parser.add_argument(
        "--dedup-size",
        type=int,
        default=1024,
        help="Recent message fingerprint cache size (default: 1024)",
    )
    parser.add_argument(
        "--command-queue-file",
        default=(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or "").strip(),
        help="Optional JSONL command queue path for outbound operations.",
    )
    parser.add_argument(
        "--command-result-file",
        default=(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or "").strip(),
        help="Optional JSONL command result path.",
    )
    parser.add_argument(
        "--command-poll-interval",
        type=float,
        default=DEFAULT_COMMAND_POLL_INTERVAL_S,
        help=f"Command queue poll interval in seconds (default: {DEFAULT_COMMAND_POLL_INTERVAL_S})",
    )
    args = parser.parse_args()

    try:
        from pywechat.WechatAuto import (
            check_new_message,
            send_message_to_friend,
            video_call,
            voice_call,
        )
        import pywechat.WechatAuto as pywechat_auto
        import pywechat.WechatTools as pywechat_tools
    except Exception as exc:
        logger.error("failed to import pywechat check_new_message: %s", exc)
        return 1

    poll_interval = max(0.5, float(args.poll_interval))
    dedup_size = max(128, int(args.dedup_size))
    resolved_wechat_path = (args.wechat_path or os.environ.get("EDGE_WECHAT_EXECUTABLE_PATH") or "").strip()
    command_queue_file = _normalize_optional_path(args.command_queue_file)
    command_result_file = _normalize_optional_path(args.command_result_file)
    command_poll_interval = max(0.1, float(args.command_poll_interval))
    if command_queue_file:
        poll_interval = min(poll_interval, command_poll_interval)
    dedup_queue: deque[str] = deque(maxlen=dedup_size)
    dedup_set: set[str] = set()
    total_sent = 0
    total_commands = 0
    total_command_success = 0
    last_wechat_running: bool | None = None
    command_offset = 0

    if command_queue_file:
        _ensure_parent_dir(command_queue_file)
        if not os.path.exists(command_queue_file):
            with open(command_queue_file, "a", encoding="utf-8"):
                pass
    if command_result_file:
        _ensure_parent_dir(command_result_file)
        if not os.path.exists(command_result_file):
            with open(command_result_file, "a", encoding="utf-8"):
                pass

    _patch_pywechat_process_probe(pywechat_auto=pywechat_auto, pywechat_tools=pywechat_tools)

    logger.info(
        "pywechat bridge started, edge_url=%s poll_interval=%.2fs wechat_path=%s command_queue=%s command_result=%s",
        args.edge_url,
        poll_interval,
        resolved_wechat_path or "(auto)",
        command_queue_file or "(disabled)",
        command_result_file or "(disabled)",
    )
    while True:
        try:
            wechat_running = _is_wechat_client_running()
            has_main_window = _find_main_window_handle() > 0
            wechat_path_arg = None
            if resolved_wechat_path and not wechat_running:
                # Only pass path when client is not running, to avoid pywechat
                # repeatedly trying to "open" an already running Weixin process.
                wechat_path_arg = resolved_wechat_path

            if last_wechat_running is None or wechat_running != last_wechat_running:
                logger.info(
                    "wechat process running=%s main_window=%s path_arg=%s",
                    wechat_running,
                    has_main_window,
                    wechat_path_arg or "(auto)",
                )
                last_wechat_running = wechat_running

            skip_message_poll = False
            if not has_main_window:
                ready = _ensure_main_window_visible(resolved_wechat_path)
                if not ready:
                    logger.warning("wechat main window unavailable; skip message polling this cycle")
                    skip_message_poll = True

            normalized: list[NormalizedMessage] = []
            if not skip_message_poll:
                try:
                    raw = check_new_message(
                        duration=None,
                        save_file=False,
                        target_folder=None,
                        wechat_path=wechat_path_arg,
                        close_wechat=False,
                    )
                    normalized = _normalize_batch(raw)
                except Exception as exc:
                    logger.warning("check_new_message failed; continue with command queue: %s", exc)
                    normalized = []
            if normalized:
                logger.info("captured %d new message(s)", len(normalized))
            for msg in normalized:
                ts_ms = int(time.time() * 1000)
                fp = hashlib.sha1(
                    f"{msg.friend_name}|{msg.sender}|{msg.content}|{msg.message_type}".encode("utf-8")
                ).hexdigest()
                if fp in dedup_set:
                    continue
                payload = _build_payload(msg, ts_ms, args.self_wxid or "")
                ok = _post_to_edge(args.edge_url, payload)
                if ok:
                    total_sent += 1
                    dedup_queue.append(fp)
                    dedup_set.add(fp)
                    while len(dedup_set) > len(dedup_queue):
                        # Keep set and deque consistent when maxlen drops older items.
                        dedup_set = set(dedup_queue)
                else:
                    logger.warning("failed to forward message: sender=%s content=%.40s", msg.sender, msg.content)

            if command_queue_file:
                command_offset, batch_count, batch_success = _process_command_queue(
                    queue_file=command_queue_file,
                    result_file=command_result_file,
                    offset=command_offset,
                    send_message_to_friend=send_message_to_friend,
                    voice_call=voice_call,
                    video_call=video_call,
                    wechat_path=resolved_wechat_path,
                )
                if batch_count > 0:
                    total_commands += batch_count
                    total_command_success += batch_success
                    logger.info(
                        "processed command batch: total=%d success=%d failed=%d cumulative_total=%d cumulative_success=%d",
                        batch_count,
                        batch_success,
                        batch_count - batch_success,
                        total_commands,
                        total_command_success,
                    )
        except KeyboardInterrupt:
            logger.info(
                "interrupted; total_sent=%d total_commands=%d command_success=%d",
                total_sent,
                total_commands,
                total_command_success,
            )
            return 0
        except Exception as exc:
            logger.warning("poll cycle failed: %s", exc)
        time.sleep(poll_interval)


if __name__ == "__main__":
    sys.exit(main())
