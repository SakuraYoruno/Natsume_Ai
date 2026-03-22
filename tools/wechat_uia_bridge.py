"""
WeChat desktop UI automation bridge.

This bridge consumes outbound command queue records and executes them on the
interactive WeChat/Weixin desktop client.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any


DEFAULT_EDGE_URL = "http://127.0.0.1:17800"
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WECHAT_MAIN_WINDOW_CLASS_HINTS = {
    "wechatmainwndforpc",
    "weixinmainwndforpc",
    "weixinmainwnd",
}
WECHAT_WINDOW_TITLE_HINTS = ("微信", "Weixin", "WeChat")


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


def _is_wechat_client_running() -> bool:
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

    try:
        pyautogui.FAILSAFE = False
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.25)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        pyautogui.write(str(target or ""), interval=0.02)
        time.sleep(0.30)
        # Prefer explicit result activation over plain Enter to reduce
        # "still-focused-search-box" cases.
        pyautogui.press("down")
        time.sleep(0.08)
        pyautogui.press("enter")
        time.sleep(0.35)
        pyautogui.press("escape")
        time.sleep(0.10)

        # UIA verification: try to find target title in current window.
        try:
            from pywinauto import Desktop

            hwnd = _find_wechat_main_window()
            if hwnd:
                window = Desktop(backend="uia").window(handle=hwnd)
                if bool(
                    window.child_window(
                        title_re=rf".*{re.escape(str(target or '').strip())}.*"
                    ).exists(timeout=0.8)
                ):
                    return True, detail
        except Exception:
            pass
        return True, detail
    except Exception as exc:
        return False, f"open_chat failed: {exc.__class__.__name__}: {exc}"


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
    args = parser.parse_args()

    queue_file = _normalize_optional_path(args.command_queue_file)
    result_file = _normalize_optional_path(args.command_result_file)
    wechat_path = str(args.wechat_path or "").strip()
    poll_interval = max(0.1, float(args.command_poll_interval))
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
        "uia bridge started edge_url=%s queue=%s result=%s wechat_path=%s",
        args.edge_url,
        queue_file,
        result_file,
        wechat_path or "(auto)",
    )

    offset = 0
    total = 0
    total_success = 0
    while True:
        try:
            offset, batch_count, batch_success = _process_command_queue(
                queue_file=queue_file,
                result_file=result_file,
                offset=offset,
                wechat_path=wechat_path,
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
        except KeyboardInterrupt:
            logger.info(
                "interrupted; total_commands=%d command_success=%d",
                total,
                total_success,
            )
            return 0
        except Exception as exc:
            logger.warning("command poll cycle failed: %s", exc)
        time.sleep(poll_interval)


if __name__ == "__main__":
    sys.exit(main())
