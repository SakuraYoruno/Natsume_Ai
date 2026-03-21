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
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WECHAT_MAIN_WINDOW_CLASS = "WeChatMainWndForPC"

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
        return int(ctypes.windll.user32.FindWindowW(WECHAT_MAIN_WINDOW_CLASS, None))
    except Exception:
        return 0


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
    return False


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
    args = parser.parse_args()

    try:
        from pywechat.WechatAuto import check_new_message
        import pywechat.WechatAuto as pywechat_auto
        import pywechat.WechatTools as pywechat_tools
    except Exception as exc:
        logger.error("failed to import pywechat check_new_message: %s", exc)
        return 1

    poll_interval = max(0.5, float(args.poll_interval))
    dedup_size = max(128, int(args.dedup_size))
    resolved_wechat_path = (args.wechat_path or os.environ.get("EDGE_WECHAT_EXECUTABLE_PATH") or "").strip()
    dedup_queue: deque[str] = deque(maxlen=dedup_size)
    dedup_set: set[str] = set()
    total_sent = 0
    last_wechat_running: bool | None = None

    _patch_pywechat_process_probe(pywechat_auto=pywechat_auto, pywechat_tools=pywechat_tools)

    logger.info(
        "pywechat bridge started, edge_url=%s poll_interval=%.2fs wechat_path=%s",
        args.edge_url,
        poll_interval,
        resolved_wechat_path or "(auto)",
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

            if not has_main_window:
                ready = _ensure_main_window_visible(resolved_wechat_path)
                if not ready:
                    logger.warning("wechat main window unavailable; skip this poll cycle")
                    time.sleep(poll_interval)
                    continue

            raw = check_new_message(
                duration=None,
                save_file=False,
                target_folder=None,
                wechat_path=wechat_path_arg,
                close_wechat=False,
            )
            normalized = _normalize_batch(raw)
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
        except KeyboardInterrupt:
            logger.info("interrupted; total_sent=%d", total_sent)
            return 0
        except Exception as exc:
            logger.warning("poll cycle failed: %s", exc)
        time.sleep(poll_interval)


if __name__ == "__main__":
    sys.exit(main())
