"""Pure UIAutomation bridge for WeChat desktop."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

try:
    from comtypes.client import CreateObject, GetModule

    GetModule("UIAutomationCore.dll")
    from comtypes.gen.UIAutomationClient import (
        CUIAutomation,
        IUIAutomation,
        IUIAutomationElement,
        IUIAutomationInvokePattern,
        IUIAutomationSelectionItemPattern,
        IUIAutomationValuePattern,
        TreeScope_Subtree,
        UIA_ButtonControlTypeId,
        UIA_EditControlTypeId,
        UIA_InvokePatternId,
        UIA_ListItemControlTypeId,
        UIA_SelectionItemPatternId,
        UIA_TextControlTypeId,
        UIA_ValuePatternId,
    )

    UIA_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    CreateObject = None
    GetModule = None
    CUIAutomation = None
    IUIAutomation = None
    IUIAutomationElement = Any
    IUIAutomationInvokePattern = Any
    IUIAutomationSelectionItemPattern = Any
    IUIAutomationValuePattern = Any
    TreeScope_Subtree = 7
    UIA_ButtonControlTypeId = 50000
    UIA_EditControlTypeId = 50004
    UIA_InvokePatternId = 10000
    UIA_ListItemControlTypeId = 50007
    UIA_SelectionItemPatternId = 10010
    UIA_TextControlTypeId = 50020
    UIA_ValuePatternId = 10002
    UIA_IMPORT_ERROR = exc

try:
    import win32con
    import win32gui
except Exception:  # pragma: no cover
    win32con = None
    win32gui = None


DEFAULT_EDGE_URL = "http://127.0.0.1:19000"
CALLBACK_PATH = "/v1/gateway/wechat/webhook"
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
DEFAULT_CALLBACK_TIMEOUT_S = 6.0
DEFAULT_LISTENER_POLL_INTERVAL_S = 1.2
DEFAULT_LISTENER_DEDUP_WINDOW_S = 12.0
DEFAULT_INBOUND_BACKEND = "uia_hook"
DEFAULT_ALLOWED_CONTACT_NAME = ""
DEFAULT_INBOUND_X_RATIO_MAX = 0.72
DEFAULT_LEFT_PANEL_RATIO_MAX = 0.42
DEFAULT_TOP_BAR_RATIO_MAX = 0.16
DEFAULT_INPUT_AREA_RATIO_MIN = 0.78
DEFAULT_OUTBOUND_ECHO_TTL_S = 60.0
DEFAULT_UI_TREE_RECOVERY_ENABLED = True
DEFAULT_UI_TREE_RECOVERY_RETRY_INTERVAL_S = 30.0
DEFAULT_UI_TREE_MIN_DESCENDANTS = 5

WECHAT_WINDOW_TITLE_HINTS = ("微信", "Weixin", "WeChat")
WECHAT_MAIN_WINDOW_CLASS_HINTS = (
    "mmui::ChatSingleWindow",
    "WeixinMainWndForPC",
    "WeChatMainWndForPC",
    "Qt51514QWindowIcon",
)
WECHAT_MAIN_WINDOW_CLASS_EXCLUDES = (
    "wxtrayiconmessagewindow",
    "qt51514wxtrayiconmessagewindowclass",
)
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

# Media message CSS class → semantic type mapping (WeChat 4.x QT)
WECHAT_MEDIA_ITEM_CLASSES: dict[str, str] = {
    "mmui::ChatImageItemView": "image",
    "mmui::ChatVoiceItemView": "voice",
    "mmui::ChatVideoItemView": "video",
    "mmui::ChatFileItemView": "file",
    "mmui::ChatEmojiItemView": "emoji",
    "mmui::ChatLinkItemView": "link",
    "mmui::ChatLocationItemView": "location",
}

# Media placeholder text → WeChat numeric msg_type code
MEDIA_TYPE_CODE_MAP: dict[str, int] = {
    "[image]": 3,
    "[voice]": 34,
    "[video]": 43,
    "[emoji]": 47,
    "[link]": 49,
    "[file]": 49,
    "[location]": 48,
}

# File extension → media type mapping for cache directory scanning
_EXT_TO_MEDIA_TYPE: dict[str, str] = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".bmp": "image", ".webp": "image", ".dat": "image",
    ".silk": "voice", ".slk": "voice", ".amr": "voice",
    ".mp4": "video", ".avi": "video", ".mkv": "video", ".mov": "video",
    ".mp3": "voice", ".wav": "voice",  # sometimes voice messages use these
}

logger = logging.getLogger("Natsume.wechat_uia_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# WeChat file cache watcher — correlate media placeholders with actual files
# ---------------------------------------------------------------------------

class WeChatCacheWatcher:
    """Monitor the WeChat file cache directory for newly created files.

    WeChat 4.x stores received media files under a predictable directory tree
    inside the user's data folder (typically at paths like
    ``FileStorage/MsgAttach/...`` or ``FileStorage/Image/...``).  This watcher
    periodically scans for recent files and keeps a short-lived cache so that
    when the UIA Bridge detects a media placeholder (e.g. ``[image]``), it can
    try to match it to an actual file path.

    The watcher is *best-effort* — if no file is found within the time window,
    the placeholder is forwarded without a ``media_url``.  This is safe because
    Phase 1 already guarantees the downstream system knows a media message
    exists.

    Configuration via environment variables:
        EDGE_WECHAT_CACHE_DIR — root of the WeChat data directory.
            Common locations:
            - ``C:\\Users\\xxx\\Documents\\xwechat_files\\wxid_xxx``
            - ``%APPDATA%\\Tencent\\WeChat\\...``
        EDGE_WECHAT_CACHE_WATCH_ENABLED — ``true`` to enable (default: false)
        EDGE_WECHAT_CACHE_WATCH_TTL_S — how long a file stays in the recent
            cache (default: 30 seconds)
    """

    # Subdirectories under the WeChat data root that contain media files
    _MEDIA_SUBDIRS: tuple[str, ...] = (
        "FileStorage/MsgAttach",
        "FileStorage/Image",
        "FileStorage/Video",
        "FileStorage/Voice",
        "FileStorage/File",
        "FileStorage/CustomEmotion",
        "FileStorage/Thumb",
    )

    def __init__(
        self,
        *,
        cache_dir: str = "",
        enabled: bool = False,
        ttl_s: float = 30.0,
        scan_interval_s: float = 2.0,
    ) -> None:
        self._cache_dir = str(cache_dir or "").strip()
        self._enabled = bool(enabled) and bool(self._cache_dir)
        self._ttl_s = max(5.0, float(ttl_s))
        self._scan_interval_s = max(0.5, float(scan_interval_s))
        self._next_scan_at = 0.0
        # Recent files: {file_path: (mtime, media_type)}
        self._recent_files: dict[str, tuple[float, str]] = {}
        # Track consumed files to avoid returning the same file twice
        self._consumed: set[str] = set()

        if self._enabled:
            logger.info(
                "WeChatCacheWatcher: enabled dir=%s ttl=%.0fs scan_interval=%.1fs",
                self._cache_dir, self._ttl_s, self._scan_interval_s,
            )
        else:
            if cache_dir and not enabled:
                logger.info("WeChatCacheWatcher: cache_dir configured but watcher disabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def scan(self) -> None:
        """Scan cache directories for recently modified files."""
        if not self._enabled:
            return
        now_mono = time.monotonic()
        if now_mono < self._next_scan_at:
            return
        self._next_scan_at = now_mono + self._scan_interval_s

        now_real = time.time()
        cutoff = now_real - self._ttl_s

        # Evict expired entries
        expired = [p for p, (mt, _) in self._recent_files.items() if mt < cutoff]
        for p in expired:
            self._recent_files.pop(p, None)
            self._consumed.discard(p)

        # Scan for new files
        root = Path(self._cache_dir)
        for subdir_rel in self._MEDIA_SUBDIRS:
            subdir = root / subdir_rel
            if not subdir.is_dir():
                continue
            # Use glob to find files modified recently — limit depth to 4 levels
            try:
                for fpath in subdir.rglob("*"):
                    if not fpath.is_file():
                        continue
                    try:
                        mtime = fpath.stat().st_mtime
                    except OSError:
                        continue
                    if mtime < cutoff:
                        continue
                    fpath_str = str(fpath)
                    if fpath_str in self._recent_files:
                        continue
                    ext = fpath.suffix.lower()
                    media_type = _EXT_TO_MEDIA_TYPE.get(ext, "")
                    if not media_type:
                        # Try to infer from parent directory name
                        parent_lower = fpath.parent.name.lower()
                        if "image" in parent_lower or "thumb" in parent_lower:
                            media_type = "image"
                        elif "video" in parent_lower:
                            media_type = "video"
                        elif "voice" in parent_lower:
                            media_type = "voice"
                        elif "file" in parent_lower:
                            media_type = "file"
                        elif "emotion" in parent_lower or "emoji" in parent_lower:
                            media_type = "emoji"
                        else:
                            media_type = "file"  # generic fallback
                    self._recent_files[fpath_str] = (mtime, media_type)
            except OSError:
                continue

    def consume_file(self, media_type: str) -> str | None:
        """Try to find and consume a recently added file matching *media_type*.

        Returns the file path if found, or None.  Once consumed, the same file
        will not be returned again (prevents the same image being matched to
        two different ``[image]`` placeholders).
        """
        if not self._enabled:
            return None

        # Find the most recent unconsumed file matching the requested type
        best_path: str | None = None
        best_mtime: float = 0.0
        for fpath, (mtime, ftype) in self._recent_files.items():
            if ftype != media_type:
                continue
            if fpath in self._consumed:
                continue
            if mtime > best_mtime:
                best_mtime = mtime
                best_path = fpath

        if best_path is not None:
            self._consumed.add(best_path)
            logger.debug("CacheWatcher: consumed %s file: %s", media_type, best_path)
        return best_path

_OUTBOUND_ECHO_CACHE: dict[tuple[str, str], float] = {}


def _normalize_optional_path(path_value: str) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    return os.path.abspath(text)


def _ensure_parent_dir(path_value: str) -> None:
    parent = os.path.dirname(path_value)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _parse_bool(raw: Any, *, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _split_csv(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        value = str(part or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
    return tuple(items)


def _normalize_match_text(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return re.sub(r"\s+", "", text)


def _normalize_message_text(raw: Any) -> str:
    text = str(raw or "").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _read_jsonl_rows_from_offset(path_value: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    path_text = _normalize_optional_path(path_value)
    if not path_text or (not os.path.exists(path_text)):
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


def _build_hook_msg_id(*, sender: str, content: str, ts_ms: int) -> str:
    digest = hashlib.sha1(f"{sender}|{content}|{ts_ms}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"uia-{ts_ms}-{digest}"


def _post_to_edge_callback(edge_url: str, body: dict[str, Any], *, timeout_s: float) -> bool:
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
        with urllib.request.urlopen(request, timeout=max(0.2, float(timeout_s))) as response:
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
    if isinstance(parsed, dict) and parsed.get("success") is False and str(parsed.get("status") or "").lower() == "rejected":
        return False
    return True


def _remember_outbound_text(content: str, *, target: str = "") -> None:
    """Record an outgoing text for echo suppression.

    The cache key is ``(target, text)`` so the same text sent to different
    contacts won't collide, and an identical text *received* from a different
    contact won't be mistakenly suppressed.
    """
    text = _normalize_message_text(content)
    if text:
        key = (_normalize_match_text(target), text)
        _OUTBOUND_ECHO_CACHE[key] = time.monotonic()


def _is_recent_outbound_echo(content: str, *, active_chat: str = "") -> bool:
    """Return True if *content* matches a recently-sent outbound text.

    Matching is scoped by *active_chat* — an outbound message sent to
    contact A will **not** suppress an identical inbound message from
    contact B.  The TTL (``DEFAULT_OUTBOUND_ECHO_TTL_S``) determines how
    long the cache entry survives.
    """
    text = _normalize_message_text(content)
    if not text:
        return False
    now_mono = time.monotonic()
    stale = [k for k, ts in _OUTBOUND_ECHO_CACHE.items() if (now_mono - ts) > DEFAULT_OUTBOUND_ECHO_TTL_S]
    for k in stale:
        _OUTBOUND_ECHO_CACHE.pop(k, None)
    chat_key = _normalize_match_text(active_chat)
    cache_key = (chat_key, text)
    ts_value = _OUTBOUND_ECHO_CACHE.get(cache_key)
    return ts_value is not None and (now_mono - ts_value) <= DEFAULT_OUTBOUND_ECHO_TTL_S


def _resolve_allowed_contact_names() -> list[str]:
    raw = str(os.environ.get("EDGE_WECHAT_ALLOWED_CONTACTS") or "").strip()
    if not raw:
        raw = str(os.environ.get("EDGE_WECHAT_ALLOWED_CONTACTS_UNICODE") or "").strip()
    if not raw:
        raw = DEFAULT_ALLOWED_CONTACT_NAME
    return [item for item in _split_csv(raw) if item]


def _is_active_chat_target(target: str) -> bool:
    return _normalize_match_text(target) in ACTIVE_CHAT_TARGET_HINTS


def _is_blocked_chat_target(target: str) -> bool:
    normalized = _normalize_match_text(target)
    return any(_normalize_match_text(item) in normalized for item in WECHAT_BLOCKED_CHAT_HINTS)


def _is_allowed_contact_target(target: str) -> bool:
    allowed = _resolve_allowed_contact_names()
    if not allowed:
        return True
    normalized_target = _normalize_match_text(target)
    return any(_normalize_match_text(item) == normalized_target for item in allowed)


def _is_noise_text(text_value: str, *, active_title: str = "") -> bool:
    text = _normalize_message_text(text_value)
    if not text:
        return True
    normalized = _normalize_match_text(text)
    if not normalized:
        return True
    if active_title and normalized == _normalize_match_text(active_title):
        return True
    if text in WECHAT_SYSTEM_LIST_TITLES:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"(昨天|前天|星期[一二三四五六日天])", text):
        return True
    return False


@dataclass(frozen=True)
class WeChatUiaSchema:
    main_window_classes: tuple[str, ...]
    chat_window_classes: tuple[str, ...]
    message_list_classes: tuple[str, ...]
    message_item_classes: tuple[str, ...]
    editor_classes: tuple[str, ...]
    title_classes: tuple[str, ...]
    search_classes: tuple[str, ...]


def _load_schema_from_env() -> WeChatUiaSchema:
    return WeChatUiaSchema(
        main_window_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_MAIN_WINDOW_CLASSES") or ",".join(WECHAT_MAIN_WINDOW_CLASS_HINTS)),
        chat_window_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_CHAT_WINDOW_CLASSES") or "mmui::ChatSingleWindow"),
        message_list_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_MESSAGE_LIST_CLASSES") or "mmui::ChatRecordListView,mmui::ChatRecordList"),
        message_item_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_MESSAGE_ITEM_CLASSES") or "mmui::ChatTextItemView"),
        editor_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_EDITOR_CLASSES") or "mmui::ChatInputTextEdit,mmui::InputTextEdit,Edit"),
        title_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_TITLE_CLASSES") or "mmui::ChatHeaderTitle,Text"),
        search_classes=_split_csv(os.environ.get("EDGE_WECHAT_UIA_SEARCH_CLASSES") or "Search,搜索,Edit"),
    )


@dataclass(frozen=True)
class UiaMessageCandidate:
    sender: str
    content: str
    signature: str
    top: int
    media_path: str | None = None


class WeChatUiAutomationClient:
    def __init__(self, *, wechat_path: str = "") -> None:
        self._wechat_path = str(wechat_path or "").strip()
        self._schema = _load_schema_from_env()
        self._automation = self._create_automation()
        self._true_condition = self._automation.CreateTrueCondition() if self._automation is not None else None
        self._ui_tree_recovery_enabled = _parse_bool(
            os.environ.get("WECHAT_UI_TREE_RECOVERY"), default=DEFAULT_UI_TREE_RECOVERY_ENABLED
        )
        self._ui_tree_recovered = False
        self._last_recovery_attempt = 0.0

    @staticmethod
    def _create_automation() -> IUIAutomation:
        if UIA_IMPORT_ERROR is not None or CreateObject is None or CUIAutomation is None or IUIAutomation is None:
            raise RuntimeError(f"UIAutomation unavailable: {UIA_IMPORT_ERROR}")
        return CreateObject(CUIAutomation, interface=IUIAutomation)

    def find_main_hwnd(self) -> int:
        if win32gui is None:
            return 0
        matches: list[tuple[int, int]] = []

        def _callback(hwnd: int, _lparam: int) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            class_name = str(win32gui.GetClassName(hwnd) or "").strip()
            class_norm = _normalize_match_text(class_name)
            title_norm = _normalize_match_text(title)
            if any(_normalize_match_text(item) == class_norm for item in WECHAT_MAIN_WINDOW_CLASS_EXCLUDES):
                return
            score = 0
            if any(_normalize_match_text(item) == class_norm for item in self._schema.main_window_classes):
                score += 8
            if any(_normalize_match_text(item) in class_norm for item in WECHAT_MAIN_WINDOW_CLASS_HINTS):
                score += 5
            if any(_normalize_match_text(item) in title_norm for item in WECHAT_WINDOW_TITLE_HINTS):
                score += 3
            if score > 0:
                matches.append((score, int(hwnd)))

        win32gui.EnumWindows(_callback, 0)
        if not matches:
            return 0
        matches.sort(reverse=True)
        return int(matches[0][1])

    def _element_from_handle(self, hwnd: int) -> IUIAutomationElement | None:
        if hwnd <= 0:
            return None
        try:
            return self._automation.ElementFromHandle(int(hwnd))
        except Exception:
            return None

    def _attempt_ui_tree_recovery(self, hwnd: int) -> bool:
        """Attempt to recover the full UI tree for WeChat 4.1.5+.

        WeChat 4.1.5+ uses an "on-demand" UIA Provider strategy — the full
        UI tree is only exposed when the system detects an accessibility client
        (e.g. a screen reader).  Calling ``ElementFromHandle`` and then
        iterating descendants acts as such a signal.  If the tree is still
        too sparse we retry periodically.
        """
        if not self._ui_tree_recovery_enabled:
            return True
        now_mono = time.monotonic()
        if self._ui_tree_recovered:
            return True
        if (now_mono - self._last_recovery_attempt) < DEFAULT_UI_TREE_RECOVERY_RETRY_INTERVAL_S:
            return self._ui_tree_recovered
        self._last_recovery_attempt = now_mono

        element = self._element_from_handle(hwnd)
        if element is None:
            logger.warning("ui_tree_recovery: ElementFromHandle returned None for hwnd=%d", hwnd)
            return False

        # Walk the tree once — this forces the UIA Provider to materialise
        descendants = self._descendants(element, limit=50)
        count = len(descendants)
        if count >= DEFAULT_UI_TREE_MIN_DESCENDANTS:
            self._ui_tree_recovered = True
            logger.info(
                "ui_tree_recovery: full UI tree available (descendants=%d, hwnd=%d)",
                count, hwnd,
            )
            return True

        # Tree is too sparse — try SetFocus to give the window foreground
        # which can also trigger the provider on some builds.
        try:
            element.SetFocus()
        except Exception:
            pass
        time.sleep(0.3)
        descendants = self._descendants(element, limit=50)
        count = len(descendants)
        if count >= DEFAULT_UI_TREE_MIN_DESCENDANTS:
            self._ui_tree_recovered = True
            logger.info(
                "ui_tree_recovery: full UI tree recovered after SetFocus (descendants=%d, hwnd=%d)",
                count, hwnd,
            )
            return True

        logger.warning(
            "ui_tree_recovery: sparse UI tree (descendants=%d < %d), "
            "will retry in %.0fs — consider starting Windows Narrator briefly "
            "or ensuring the WeChat window is visible",
            count, DEFAULT_UI_TREE_MIN_DESCENDANTS,
            DEFAULT_UI_TREE_RECOVERY_RETRY_INTERVAL_S,
        )
        return False

    def get_main_element(self) -> IUIAutomationElement | None:
        hwnd = self.find_main_hwnd()
        if hwnd <= 0:
            return None
        self._attempt_ui_tree_recovery(hwnd)
        return self._element_from_handle(hwnd)

    def _rect(self, element: IUIAutomationElement) -> tuple[int, int, int, int] | None:
        try:
            rect = element.CurrentBoundingRectangle
            left = int(getattr(rect, "left", 0))
            top = int(getattr(rect, "top", 0))
            right = int(getattr(rect, "right", 0))
            bottom = int(getattr(rect, "bottom", 0))
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

    def _name(self, element: IUIAutomationElement) -> str:
        try:
            return _normalize_message_text(element.CurrentName)
        except Exception:
            return ""

    def _class_name(self, element: IUIAutomationElement) -> str:
        try:
            return _normalize_message_text(element.CurrentClassName)
        except Exception:
            return ""

    def _control_type(self, element: IUIAutomationElement) -> int:
        try:
            return int(element.CurrentControlType)
        except Exception:
            return 0

    def _descendants(self, element: IUIAutomationElement, *, limit: int = 2200) -> list[IUIAutomationElement]:
        if element is None or self._true_condition is None:
            return []
        try:
            found = element.FindAll(TreeScope_Subtree, self._true_condition)
        except Exception:
            return []
        items: list[IUIAutomationElement] = []
        try:
            length = min(int(found.Length), int(limit))
        except Exception:
            return []
        for index in range(length):
            try:
                items.append(found.GetElement(index))
            except Exception:
                continue
        return items

    def _invoke(self, element: IUIAutomationElement) -> bool:
        try:
            pattern = element.GetCurrentPattern(UIA_SelectionItemPatternId)
            if pattern is not None:
                pattern.QueryInterface(IUIAutomationSelectionItemPattern).Select()
                return True
        except Exception:
            pass
        try:
            pattern = element.GetCurrentPattern(UIA_InvokePatternId)
            if pattern is not None:
                pattern.QueryInterface(IUIAutomationInvokePattern).Invoke()
                return True
        except Exception:
            pass
        try:
            element.SetFocus()
            return True
        except Exception:
            return False

    def _set_value(self, element: IUIAutomationElement, value: str) -> bool:
        text = str(value or "")
        try:
            pattern = element.GetCurrentPattern(UIA_ValuePatternId)
            if pattern is not None:
                pattern.QueryInterface(IUIAutomationValuePattern).SetValue(text)
                return True
        except Exception:
            return False
        return False

    def _focus_main_window(self) -> bool:
        hwnd = self.find_main_hwnd()
        if hwnd <= 0 or win32gui is None:
            return False
        try:
            if win32con is not None:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def _match_score(self, text_value: str, candidates: list[str]) -> int:
        normalized_text = _normalize_match_text(text_value)
        if not normalized_text:
            return -1
        best = -1
        for candidate in candidates:
            normalized_candidate = _normalize_match_text(candidate)
            if not normalized_candidate:
                continue
            if normalized_text == normalized_candidate:
                best = max(best, 4)
            elif normalized_candidate in normalized_text or normalized_text in normalized_candidate:
                best = max(best, 3)
        return best

    def _active_chat_title(self, root: IUIAutomationElement) -> str:
        root_rect = self._rect(root)
        if root_rect is None:
            return ""
        left, top, right, _bottom = root_rect
        width = max(1, right - left)
        title_bottom = top + int(width * 0.16)
        class_targets = {_normalize_match_text(item) for item in self._schema.title_classes}
        best_title = ""
        best_score = -1
        for element in self._descendants(root):
            if self._control_type(element) != UIA_TextControlTypeId:
                continue
            text_value = self._name(element)
            if _is_noise_text(text_value):
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, _el_bottom = rect
            if el_top > title_bottom:
                continue
            if el_right < left + int(width * 0.18):
                continue
            score = len(text_value)
            if _normalize_match_text(self._class_name(element)) in class_targets:
                score += 4
            if el_left > left + int(width * 0.30):
                score += 1
            if score > best_score:
                best_score = score
                best_title = text_value
        return best_title

    def _search_box(self, root: IUIAutomationElement) -> IUIAutomationElement | None:
        root_rect = self._rect(root)
        if root_rect is None:
            return None
        left, top, right, bottom = root_rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        max_x = left + int(width * DEFAULT_LEFT_PANEL_RATIO_MAX)
        max_y = top + int(height * 0.25)
        class_targets = {_normalize_match_text(item) for item in self._schema.search_classes}
        best: tuple[int, IUIAutomationElement] | None = None
        for element in self._descendants(root):
            if self._control_type(element) != UIA_EditControlTypeId:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, el_bottom = rect
            if el_right > max_x or el_bottom > max_y:
                continue
            name = self._name(element)
            class_name = self._class_name(element)
            score = 0
            if self._match_score(name, list(self._schema.search_classes)) >= 3:
                score += 4
            if _normalize_match_text(class_name) in class_targets:
                score += 2
            score += max(1, (max_y - el_top))
            if best is None or score > best[0]:
                best = (score, element)
        return best[1] if best else None

    def _chat_target_element(self, root: IUIAutomationElement, candidates: list[str]) -> tuple[IUIAutomationElement | None, str]:
        root_rect = self._rect(root)
        if root_rect is None:
            return None, ""
        left, _top, right, _bottom = root_rect
        width = max(1, right - left)
        max_x = left + int(width * DEFAULT_LEFT_PANEL_RATIO_MAX)
        allowed_names = _resolve_allowed_contact_names()
        best: tuple[int, int, IUIAutomationElement, str] | None = None
        for element in self._descendants(root):
            if self._control_type(element) not in {UIA_ListItemControlTypeId, UIA_ButtonControlTypeId, UIA_TextControlTypeId}:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, _el_right, _el_bottom = rect
            if el_left > max_x:
                continue
            name = self._name(element)
            if not name or _is_blocked_chat_target(name):
                continue
            if allowed_names and (not _is_allowed_contact_target(name)):
                continue
            score = self._match_score(name, candidates)
            if score < 0:
                continue
            anchor = (score, -el_top)
            if best is None or anchor > (best[0], best[1]):
                best = (score, -el_top, element, name)
        if best is None:
            return None, ""
        return best[2], best[3]

    def _send_button(self, root: IUIAutomationElement) -> IUIAutomationElement | None:
        root_rect = self._rect(root)
        if root_rect is None:
            return None
        left, top, right, bottom = root_rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        min_x = left + int(width * 0.58)
        min_y = top + int(height * DEFAULT_INPUT_AREA_RATIO_MIN)
        best: tuple[int, IUIAutomationElement] | None = None
        for element in self._descendants(root):
            if self._control_type(element) != UIA_ButtonControlTypeId:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, _el_right, _el_bottom = rect
            if el_left < min_x or el_top < min_y:
                continue
            name = self._name(element)
            if not re.search(r"(发送|send)", name, re.IGNORECASE):
                continue
            score = len(name)
            if best is None or score > best[0]:
                best = (score, element)
        return best[1] if best else None

    def _input_editor(self, root: IUIAutomationElement) -> IUIAutomationElement | None:
        root_rect = self._rect(root)
        if root_rect is None:
            return None
        left, top, right, bottom = root_rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        min_x = left + int(width * 0.24)
        min_y = top + int(height * DEFAULT_INPUT_AREA_RATIO_MIN)
        class_targets = {_normalize_match_text(item) for item in self._schema.editor_classes}
        best: tuple[int, IUIAutomationElement] | None = None
        for element in self._descendants(root):
            if self._control_type(element) != UIA_EditControlTypeId:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, el_bottom = rect
            if el_left < min_x or el_top < min_y:
                continue
            if el_right <= el_left or el_bottom <= el_top:
                continue
            score = (el_right - el_left)
            if _normalize_match_text(self._class_name(element)) in class_targets:
                score += 2000
            if best is None or score > best[0]:
                best = (score, element)
        return best[1] if best else None

    def _verify_active_chat(self, root: IUIAutomationElement, candidates: list[str]) -> tuple[bool, str]:
        title = self._active_chat_title(root)
        if not title:
            return False, "active_chat_title_empty"
        if self._match_score(title, candidates) >= 3:
            return True, title
        return False, title

    def open_chat(self, *, target: str, target_candidates: list[str] | None = None) -> tuple[bool, str, str]:
        target_value = str(target or "").strip()
        candidates = [target_value]
        for item in list(target_candidates or []):
            text = str(item or "").strip()
            if text and text not in candidates:
                candidates.append(text)
        if _is_active_chat_target(target_value):
            return True, target_value, "active_chat_target"
        root = self.get_main_element()
        if root is None:
            return False, "", "wechat main window not found"
        self._focus_main_window()
        ok, resolved = self._verify_active_chat(root, candidates)
        if ok:
            return True, resolved, f"active_chat_already_open={resolved}"
        match_element, resolved_name = self._chat_target_element(root, candidates)
        if match_element is not None and self._invoke(match_element):
            time.sleep(0.35)
            root = self.get_main_element() or root
            ok, resolved = self._verify_active_chat(root, candidates)
            if ok:
                return True, resolved, f"chat_list_select={resolved_name}"
        search_box = self._search_box(root)
        if search_box is None:
            return False, "", "search_box_not_found"
        for keyword in candidates:
            if not self._set_value(search_box, keyword):
                continue
            time.sleep(0.45)
            root = self.get_main_element() or root
            match_element, resolved_name = self._chat_target_element(root, candidates)
            if match_element is None or (not self._invoke(match_element)):
                continue
            time.sleep(0.35)
            root = self.get_main_element() or root
            ok, resolved = self._verify_active_chat(root, candidates)
            if ok:
                return True, resolved, f"search_select={resolved_name}"
        return False, "", f"chat_not_found target={target_value} candidates={candidates}"

    def send_text(self, *, target: str, content: str, target_candidates: list[str] | None = None) -> tuple[bool, str | None, str, str | None]:
        content_value = _normalize_message_text(content)
        if not content_value:
            return False, "OUTBOUND_EMPTY_TEXT", "empty text", None
        open_ok, resolved_target, open_detail = self.open_chat(target=target, target_candidates=target_candidates)
        if not open_ok:
            return False, "WECHAT_CHAT_NOT_FOUND", open_detail, None
        root = self.get_main_element()
        if root is None:
            return False, "WECHAT_MAIN_WINDOW_MISSING", "wechat main window not found", resolved_target or None
        editor = self._input_editor(root)
        if editor is None:
            return False, "WECHAT_EDITOR_NOT_FOUND", "message input editor not found", resolved_target or None
        if not self._set_value(editor, content_value):
            return False, "WECHAT_EDITOR_SET_VALUE_FAILED", "editor value pattern unavailable", resolved_target or None
        send_button = self._send_button(root)
        if send_button is None:
            return False, "WECHAT_SEND_BUTTON_NOT_FOUND", "send button not found", resolved_target or None
        if not self._invoke(send_button):
            return False, "WECHAT_SEND_BUTTON_INVOKE_FAILED", "send button invoke failed", resolved_target or None
        _remember_outbound_text(content_value, target=resolved_target or target)
        return True, None, f"uia_send_ok target={resolved_target or target}", resolved_target or target

    @staticmethod
    def _extract_nickname_from_children(
        text_children: list[tuple[tuple[int, int, int, int], str]],
    ) -> tuple[str, list[str]]:
        """Separate sender nickname from message body using positional heuristics.

        In WeChat group chats, each incoming message bubble shows the sender
        nickname *above* the actual message text.  This helper inspects the
        vertically-sorted Text children and returns ``(nickname, body_texts)``.

        Heuristic rules (all must hold for the first child to be treated as
        a nickname):
        1. There are at least 2 text children.
        2. The first child ends (bottom) **above** or very close to where the
           second child starts (top) — i.e. ``y_gap >= 2 px``.
        3. The first child is short (≤ 20 characters — typical for nicknames).
        4. The first child's rendered height is ≤ the second child's height
           (nickname font is usually the same size or smaller).

        If any rule fails, nickname is empty and all children are treated as
        body text (safe fallback — behaviour identical to the original code).
        """
        if len(text_children) < 2:
            return "", [t for _, t in text_children]

        first_rect, first_text = text_children[0]
        second_rect, _second_text = text_children[1]

        y_gap = second_rect[1] - first_rect[3]  # second.top - first.bottom
        first_height = first_rect[3] - first_rect[1]
        second_height = second_rect[3] - second_rect[1]

        if y_gap >= 2 and len(first_text) <= 20 and first_height <= second_height:
            # First child is the nickname
            seen: set[str] = set()
            body: list[str] = []
            for _, t in text_children[1:]:
                if t not in seen:
                    seen.add(t)
                    body.append(t)
            return first_text, body

        # Fallback: no nickname detected
        seen_fb: set[str] = set()
        body_fb: list[str] = []
        for _, t in text_children:
            if t not in seen_fb:
                seen_fb.add(t)
                body_fb.append(t)
        return "", body_fb

    def _message_item_candidates(self, root: IUIAutomationElement, *, active_title: str) -> list[UiaMessageCandidate]:
        root_rect = self._rect(root)
        if root_rect is None:
            return []
        left, _top, right, _bottom = root_rect
        width = max(1, right - left)
        min_x = left + int(width * 0.22)
        max_x = left + int(width * DEFAULT_INBOUND_X_RATIO_MAX)
        class_targets = {_normalize_match_text(item) for item in self._schema.message_item_classes}
        items: list[UiaMessageCandidate] = []
        for element in self._descendants(root):
            if _normalize_match_text(self._class_name(element)) not in class_targets:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, _el_bottom = rect
            center_x = int((el_left + el_right) / 2)
            if center_x <= min_x or center_x >= max_x:
                continue

            # Collect Text children with their bounding rects
            text_children: list[tuple[tuple[int, int, int, int], str]] = []
            for child in self._descendants(element, limit=120):
                if self._control_type(child) != UIA_TextControlTypeId:
                    continue
                text_value = self._name(child)
                if _is_noise_text(text_value, active_title=active_title):
                    continue
                child_rect = self._rect(child)
                if child_rect is None:
                    continue
                text_children.append((child_rect, text_value))

            # Sort by vertical position (top coordinate)
            text_children.sort(key=lambda x: x[0][1])

            # Attempt to separate nickname from body text
            nickname, body_texts = self._extract_nickname_from_children(text_children)

            content = _normalize_message_text(" ".join(body_texts))
            if not content or _is_recent_outbound_echo(content, active_chat=active_title):
                continue

            sender = nickname or active_title or "active_chat"
            sig = f"{active_title}|{sender}|{content}|{el_top}"
            items.append(UiaMessageCandidate(sender, content, sig, el_top))
        items.sort(key=lambda item: item.top)
        return items

    def _message_text_fallback(self, root: IUIAutomationElement, *, active_title: str) -> list[UiaMessageCandidate]:
        root_rect = self._rect(root)
        if root_rect is None:
            return []
        left, top, right, bottom = root_rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        min_x = left + int(width * 0.22)
        max_x = left + int(width * DEFAULT_INBOUND_X_RATIO_MAX)
        min_y = top + int(height * DEFAULT_TOP_BAR_RATIO_MAX)
        max_y = top + int(height * DEFAULT_INPUT_AREA_RATIO_MIN)
        grouped: dict[int, list[tuple[int, str]]] = {}
        for element in self._descendants(root):
            if self._control_type(element) != UIA_TextControlTypeId:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, el_bottom = rect
            center_x = int((el_left + el_right) / 2)
            center_y = int((el_top + el_bottom) / 2)
            if center_x <= min_x or center_x >= max_x or center_y <= min_y or center_y >= max_y:
                continue
            text_value = self._name(element)
            if _is_noise_text(text_value, active_title=active_title):
                continue
            grouped.setdefault(int(center_y / 22), []).append((center_x, text_value))
        items: list[UiaMessageCandidate] = []
        for bucket in sorted(grouped.keys()):
            ordered = sorted(grouped[bucket], key=lambda item: item[0])
            texts: list[str] = []
            for _x, text_value in ordered:
                if text_value not in texts:
                    texts.append(text_value)
            content = _normalize_message_text(" ".join(texts))
            if not content or _is_recent_outbound_echo(content, active_chat=active_title):
                continue
            items.append(UiaMessageCandidate(active_title or "active_chat", content, f"{active_title}|{content}|bucket={bucket}", bucket))
        return items

    def _message_media_candidates(
        self,
        root: IUIAutomationElement,
        *,
        active_title: str,
        cache_watcher: WeChatCacheWatcher | None = None,
    ) -> list[UiaMessageCandidate]:
        """Detect non-text message items (image/voice/video/file/emoji/link) and
        return placeholder candidates like ``[image]``, ``[voice]`` etc.

        Phase 1: Detects media message existence via CSS class name matching.
        Phase 2 (when *cache_watcher* is provided): Attempts to correlate each
        detected media placeholder with an actual file from the cache directory.
        The file path is stored in the candidate's ``media_path`` attribute.
        """
        root_rect = self._rect(root)
        if root_rect is None:
            return []
        left, _top, right, _bottom = root_rect
        width = max(1, right - left)
        min_x = left + int(width * 0.22)
        max_x = left + int(width * DEFAULT_INBOUND_X_RATIO_MAX)

        items: list[UiaMessageCandidate] = []
        for element in self._descendants(root):
            class_name = self._class_name(element)
            class_norm = _normalize_match_text(class_name)

            media_type: str | None = None
            for cls_hint, mtype in WECHAT_MEDIA_ITEM_CLASSES.items():
                if _normalize_match_text(cls_hint) in class_norm:
                    media_type = mtype
                    break
            if media_type is None:
                continue

            rect = self._rect(element)
            if rect is None:
                continue
            el_left, el_top, el_right, _el_bottom = rect
            center_x = int((el_left + el_right) / 2)
            if center_x <= min_x or center_x >= max_x:
                continue

            # Phase 2: attempt to correlate with an actual file
            media_path: str | None = None
            if cache_watcher is not None and cache_watcher.enabled:
                media_path = cache_watcher.consume_file(media_type)

            content = f"[{media_type}]"
            signature = f"{active_title}|{media_type}|{el_top}"
            items.append(UiaMessageCandidate(
                active_title or "active_chat",
                content,
                signature,
                el_top,
                media_path=media_path,
            ))

        items.sort(key=lambda item: item.top)
        return items

    def _scan_chat_list_unread(self, root: IUIAutomationElement) -> list[tuple[str, int]]:
        """Scan the left-side chat list for unread message indicators.

        Returns a list of ``(chat_name, unread_count)`` for chats that have
        unread messages.  Only visible list items within the left panel are
        inspected — no scrolling is performed.

        Each chat ListItem typically contains:
        - A Text child with the chat name (longest non-noise text)
        - A small Text child with a pure-digit unread count badge, positioned
          in the right half of the list item
        """
        root_rect = self._rect(root)
        if root_rect is None:
            return []
        left, _top, right, _bottom = root_rect
        width = max(1, right - left)
        max_x = left + int(width * DEFAULT_LEFT_PANEL_RATIO_MAX)

        results: list[tuple[str, int]] = []
        for element in self._descendants(root):
            if self._control_type(element) != UIA_ListItemControlTypeId:
                continue
            rect = self._rect(element)
            if rect is None:
                continue
            el_left, _el_top, el_right, _el_bottom = rect
            if el_right > max_x:
                continue

            chat_name = ""
            unread_count = 0
            for child in self._descendants(element, limit=20):
                if self._control_type(child) != UIA_TextControlTypeId:
                    continue
                text = self._name(child)
                if not text:
                    continue
                # Pure digit 1-3 chars → potential unread badge
                if re.fullmatch(r"\d{1,3}", text):
                    child_rect = self._rect(child)
                    if child_rect:
                        cx = (child_rect[0] + child_rect[2]) / 2
                        # Badge is in the right half of the list item
                        if cx > (el_left + el_right) / 2:
                            unread_count = int(text)
                            continue
                # Longest non-noise text → chat name
                if not _is_noise_text(text) and len(text) > len(chat_name):
                    chat_name = text

            if chat_name and unread_count > 0:
                if not _is_blocked_chat_target(chat_name) and _is_allowed_contact_target(chat_name):
                    results.append((chat_name, unread_count))

        return results

    def collect_inbound_messages(
        self, *, cache_watcher: WeChatCacheWatcher | None = None,
    ) -> tuple[str, list[UiaMessageCandidate]]:
        root = self.get_main_element()
        if root is None:
            return "", []
        active_title = self._active_chat_title(root)
        if active_title and (_is_blocked_chat_target(active_title) or (not _is_allowed_contact_target(active_title))):
            return active_title, []

        # Text messages (primary + fallback)
        text_messages = self._message_item_candidates(root, active_title=active_title)
        if not text_messages:
            text_messages = self._message_text_fallback(root, active_title=active_title)

        # Media messages (Phase 1: type-aware placeholders; Phase 2: with file paths)
        media_messages = self._message_media_candidates(
            root, active_title=active_title, cache_watcher=cache_watcher,
        )

        # Merge and sort by vertical position
        all_messages = text_messages + media_messages
        all_messages.sort(key=lambda item: item.top)
        return active_title, all_messages


class WeChatPureUiaInboundBridge:
    def __init__(self, *, edge_url: str, enabled: bool, poll_interval_s: float, callback_timeout_s: float, dedup_window_s: float, client: WeChatUiAutomationClient) -> None:
        self._edge_url = str(edge_url or DEFAULT_EDGE_URL).strip()
        self._enabled = bool(enabled)
        self._poll_interval_s = max(0.2, float(poll_interval_s))
        self._callback_timeout_s = max(0.2, float(callback_timeout_s))
        self._dedup_window_s = max(1.0, float(dedup_window_s))
        self._client = client
        self._next_poll_at = 0.0
        self._baseline_ready = False
        self._known_signatures: set[str] = set()
        self._recent_forwarded: dict[str, float] = {}
        # Multi-chat polling (H1)
        self._multi_chat_enabled = _parse_bool(
            os.environ.get("EDGE_WECHAT_MULTI_CHAT_POLL"), default=False
        )
        self._multi_chat_interval_s = max(
            2.0,
            float(os.environ.get("EDGE_WECHAT_MULTI_CHAT_POLL_INTERVAL_S") or "5.0"),
        )
        self._next_multi_chat_poll_at = 0.0
        # Media cache watcher (H3 Phase 2)
        cache_dir = str(os.environ.get("EDGE_WECHAT_CACHE_DIR") or "").strip()
        cache_watch_enabled = _parse_bool(
            os.environ.get("EDGE_WECHAT_CACHE_WATCH_ENABLED"), default=False
        )
        cache_ttl_s = max(
            5.0,
            float(os.environ.get("EDGE_WECHAT_CACHE_WATCH_TTL_S") or "30.0"),
        )
        self._cache_watcher = WeChatCacheWatcher(
            cache_dir=cache_dir,
            enabled=cache_watch_enabled,
            ttl_s=cache_ttl_s,
        )

    def start(self) -> None:
        if self._multi_chat_enabled:
            logger.info("multi-chat polling enabled, interval=%.1fs", self._multi_chat_interval_s)
        return

    def stop(self) -> None:
        return

    def _is_recent_duplicate(self, signature: str) -> bool:
        now_mono = time.monotonic()
        stale = [item for item, ts in self._recent_forwarded.items() if (now_mono - ts) > self._dedup_window_s]
        for item in stale:
            self._recent_forwarded.pop(item, None)
        if signature in self._recent_forwarded:
            return True
        self._recent_forwarded[signature] = now_mono
        return False

    def _forward_messages(self, messages: list[UiaMessageCandidate], *, active_title: str) -> int:
        """Forward new messages to edge callback. Returns count forwarded."""
        forwarded = 0
        ts_ms = int(time.time() * 1000)
        for item in messages:
            if item.signature in self._known_signatures:
                continue
            self._known_signatures.add(item.signature)
            if self._is_recent_duplicate(item.signature):
                continue
            msg_id = _build_hook_msg_id(sender=item.sender, content=item.content, ts_ms=ts_ms)
            # When a nickname was extracted (item.sender != active_title),
            # conversation_id stays as the chat title while sender_name
            # carries the individual member's display name.
            is_group_hint = bool(active_title and item.sender != active_title)
            body: dict[str, Any] = {
                "id": msg_id,
                "msg_id": msg_id,
                "sender": item.sender,
                "sender_name": item.sender,
                "from_wxid": item.sender,
                "receiver": "local_wechat",
                "to_wxid": "local_wechat",
                "conversation_id": active_title or item.sender,
                "content": item.content,
                "text": item.content,
                "type": MEDIA_TYPE_CODE_MAP.get(item.content, 1),
                "msg_type": str(MEDIA_TYPE_CODE_MAP.get(item.content, 1)),
                "ts": int(ts_ms / 1000),
                "bridge_backend": "uia_hook",
                "is_group_hint": is_group_hint,
            }
            # Phase 2: attach media file path if available
            if item.media_path:
                body["media_url"] = item.media_path
            if _post_to_edge_callback(self._edge_url, body, timeout_s=self._callback_timeout_s):
                forwarded += 1
                logger.info("forwarded inbound message sender=%s content=%.80s", item.sender, item.content)
        return forwarded

    def process_cycle(self) -> int:
        if not self._enabled:
            return 0
        now_mono = time.monotonic()
        if now_mono < self._next_poll_at:
            return 0
        self._next_poll_at = now_mono + self._poll_interval_s

        # Phase 2: scan cache directory for recently added media files
        self._cache_watcher.scan()

        # Collect from current chat
        active_title, messages = self._client.collect_inbound_messages(
            cache_watcher=self._cache_watcher,
        )
        if not self._baseline_ready:
            for item in messages:
                self._known_signatures.add(item.signature)
            self._baseline_ready = True
            logger.info("inbound listener baseline initialized: messages=%d chat=%s", len(messages), active_title or "-")
            return 0

        forwarded = self._forward_messages(messages, active_title=active_title)

        # Multi-chat polling: scan for unread chats and collect their messages
        if self._multi_chat_enabled and now_mono >= self._next_multi_chat_poll_at:
            self._next_multi_chat_poll_at = now_mono + self._multi_chat_interval_s
            root = self._client.get_main_element()
            if root is not None:
                unread_chats = self._client._scan_chat_list_unread(root)
                if unread_chats:
                    current_title = active_title or self._client._active_chat_title(root)
                    for chat_name, _count in unread_chats:
                        if current_title and _normalize_match_text(chat_name) == _normalize_match_text(current_title):
                            continue  # Already collected above
                        ok, _resolved, _detail = self._client.open_chat(target=chat_name)
                        if not ok:
                            logger.debug("multi-chat: failed to open chat=%s", chat_name)
                            continue
                        time.sleep(0.35)
                        _title, switched_msgs = self._client.collect_inbound_messages(
                            cache_watcher=self._cache_watcher,
                        )
                        forwarded += self._forward_messages(switched_msgs, active_title=chat_name)
                    # Switch back to original chat so the user doesn't notice
                    if current_title:
                        self._client.open_chat(target=current_title)
                        time.sleep(0.2)

        return forwarded


def _collect_command_target_candidates(command: dict[str, Any]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in [command.get("target"), *list(command.get("target_candidates") or [])]:
        text = str(raw or "").strip()
        if not text or _is_blocked_chat_target(text):
            continue
        key = _normalize_match_text(text)
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    allowed = _resolve_allowed_contact_names()
    if allowed:
        filtered = [item for item in values if _is_allowed_contact_target(item)]
        if filtered:
            return filtered
        return list(allowed)
    return values


def _execute_command(command: dict[str, Any], *, controller: WeChatUiAutomationClient) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    command_id = str(command.get("command_id") or "").strip() or f"uia-{now_ms}"
    request_id = str(command.get("request_id") or "").strip()
    action = str(command.get("action") or "").strip().lower()
    target = str(command.get("target") or "").strip()
    content = str(command.get("content") or "")
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
    candidates = _collect_command_target_candidates(command)

    if action == "send_text":
        ok, reason_code, detail, resolved_target = controller.send_text(
            target=target, content=content, target_candidates=candidates,
        )
        result["success"] = bool(ok)
        result["status"] = "ok" if ok else "error"
        result["reason_code"] = reason_code
        result["detail"] = detail
        if resolved_target:
            result["resolved_target"] = resolved_target
        return result

    if action in {"send_image", "send_file", "send_voice"}:
        # content should contain the file path for send_image/send_file/send_voice
        media_path = str(command.get("media_path") or content or "").strip()
        if not media_path or not os.path.isfile(media_path):
            result["reason_code"] = "WECHAT_MEDIA_FILE_NOT_FOUND"
            result["detail"] = f"media file not found: {media_path}"
            return result

        # Step 1: open the target chat
        open_ok, resolved_target, open_detail = controller.open_chat(
            target=target, target_candidates=candidates,
        )
        if not open_ok:
            result["reason_code"] = "WECHAT_CHAT_NOT_FOUND"
            result["detail"] = open_detail
            return result

        # Step 2: copy media to clipboard and paste
        if action == "send_image":
            from edge_client.gateway.media.image_handler import set_clipboard_image
            if not set_clipboard_image(media_path):
                result["reason_code"] = "WECHAT_CLIPBOARD_FAILED"
                result["detail"] = f"failed to copy image to clipboard: {media_path}"
                return result
        elif action == "send_voice":
            # 语音消息：将 .silk 文件以 CF_HDROP 格式写入剪贴板
            from edge_client.gateway.media.voice_handler import set_clipboard_file
            if not set_clipboard_file(media_path):
                result["reason_code"] = "WECHAT_CLIPBOARD_FAILED"
                result["detail"] = f"failed to copy voice file to clipboard: {media_path}"
                return result
        else:
            # For files, we still attempt clipboard — on failure, log and return
            # Future: implement file drag-and-drop or other mechanisms
            result["reason_code"] = "WECHAT_SEND_FILE_NOT_IMPLEMENTED"
            result["detail"] = "send_file via clipboard is not yet supported; use send_text with file link"
            return result

        # Step 3: paste (Ctrl+V) and send (Enter/click send button)
        root = controller.get_main_element()
        if root is None:
            result["reason_code"] = "WECHAT_MAIN_WINDOW_MISSING"
            result["detail"] = "wechat main window not found after chat open"
            return result

        editor = controller._input_editor(root)
        if editor is not None:
            try:
                editor.SetFocus()
            except Exception:
                pass

        # Simulate Ctrl+V paste
        try:
            import win32api
            import win32con as w32c
            # Ctrl down, V down, V up, Ctrl up
            win32api.keybd_event(w32c.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(0x56, 0, 0, 0)  # V key
            time.sleep(0.05)
            win32api.keybd_event(0x56, 0, w32c.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(w32c.VK_CONTROL, 0, w32c.KEYEVENTF_KEYUP, 0)
            time.sleep(0.5)  # Wait for WeChat to process the paste
        except ImportError:
            result["reason_code"] = "WECHAT_PYWIN32_MISSING"
            result["detail"] = "pywin32 required for keyboard simulation"
            return result

        # Click send button
        send_btn = controller._send_button(root)
        if send_btn is not None:
            controller._invoke(send_btn)
        else:
            # Fallback: press Enter to send
            try:
                import win32api
                import win32con as w32c
                win32api.keybd_event(w32c.VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(w32c.VK_RETURN, 0, w32c.KEYEVENTF_KEYUP, 0)
            except ImportError:
                pass

        result["success"] = True
        result["status"] = "ok"
        result["detail"] = f"uia_send_{action.replace('send_', '')}_ok target={resolved_target or target}"
        if resolved_target:
            result["resolved_target"] = resolved_target
        return result

    result["reason_code"] = "WECHAT_ACTION_UNSUPPORTED"
    result["detail"] = f"unsupported action={action}"
    return result


def _process_command_queue(*, queue_file: str, result_file: str, offset: int, controller: WeChatUiAutomationClient) -> tuple[int, int, int]:
    rows, next_offset = _read_jsonl_rows_from_offset(queue_file, offset)
    if not rows:
        return next_offset, 0, 0
    success_count = 0
    for command in rows:
        result = _execute_command(command, controller=controller)
        if bool(result.get("success")):
            success_count += 1
        _append_jsonl_row(result_file, result)
    return next_offset, len(rows), success_count


# ---------------------------------------------------------------------------
# JSONL file rotation — prevent unbounded growth (R4 risk mitigation)
# ---------------------------------------------------------------------------

_JSONL_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
_JSONL_LAST_ROTATION_CHECK = 0.0
_JSONL_ROTATION_CHECK_INTERVAL_S = 60.0


def _maybe_rotate_jsonl(path_value: str) -> bool:
    """Truncate a JSONL file if it exceeds *_JSONL_MAX_SIZE_BYTES*.

    Keeps the last 200 lines so that recent command results remain available
    for polling by ``wait_wechat_command_result``.
    """
    path_text = _normalize_optional_path(path_value)
    if not path_text or not os.path.exists(path_text):
        return False
    try:
        size = os.path.getsize(path_text)
    except OSError:
        return False
    if size <= _JSONL_MAX_SIZE_BYTES:
        return False

    try:
        with open(path_text, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        tail = lines[-200:] if len(lines) > 200 else lines
        with open(path_text, "w", encoding="utf-8") as fh:
            fh.writelines(tail)
        logger.info(
            "jsonl rotation: %s truncated from %d lines / %d bytes to %d lines",
            path_text, len(lines), size, len(tail),
        )
        return True
    except Exception as exc:
        logger.warning("jsonl rotation failed for %s: %s", path_text, exc)
        return False


def _check_jsonl_rotation(queue_file: str, result_file: str) -> None:
    """Periodically check and rotate both JSONL files."""
    global _JSONL_LAST_ROTATION_CHECK
    now_mono = time.monotonic()
    if (now_mono - _JSONL_LAST_ROTATION_CHECK) < _JSONL_ROTATION_CHECK_INTERVAL_S:
        return
    _JSONL_LAST_ROTATION_CHECK = now_mono
    _maybe_rotate_jsonl(queue_file)
    _maybe_rotate_jsonl(result_file)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pure UIAutomation bridge for WeChat desktop")
    parser.add_argument("--edge-url", default=(os.environ.get("EDGE_URL") or DEFAULT_EDGE_URL).strip())
    parser.add_argument("--command-poll-interval", type=float, default=float(os.environ.get("EDGE_WECHAT_COMMAND_POLL_INTERVAL_S") or DEFAULT_COMMAND_POLL_INTERVAL_S))
    parser.add_argument("--command-queue-file", default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or ""))
    parser.add_argument("--command-result-file", default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or ""))
    parser.add_argument("--listener-enabled", default=str(os.environ.get("EDGE_WECHAT_LISTENER_ENABLED") or "true").strip())
    parser.add_argument("--listener-poll-interval", type=float, default=float(os.environ.get("EDGE_WECHAT_LISTENER_POLL_INTERVAL_S") or DEFAULT_LISTENER_POLL_INTERVAL_S))
    parser.add_argument("--listener-dedup-window", type=float, default=float(os.environ.get("EDGE_WECHAT_LISTENER_DEDUP_WINDOW_S") or DEFAULT_LISTENER_DEDUP_WINDOW_S))
    parser.add_argument("--callback-timeout", type=float, default=float(os.environ.get("EDGE_WECHAT_CALLBACK_TIMEOUT_S") or DEFAULT_CALLBACK_TIMEOUT_S))
    parser.add_argument("--wechat-path", default=str(os.environ.get("EDGE_WECHAT_PATH") or "").strip())
    parser.add_argument("--inbound-backend", default=(os.environ.get("EDGE_WECHAT_INBOUND_BACKEND") or DEFAULT_INBOUND_BACKEND).strip(), help="Inbound backend: uia_hook|off")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    inbound_backend = str(args.inbound_backend or DEFAULT_INBOUND_BACKEND).strip().lower() or DEFAULT_INBOUND_BACKEND
    if inbound_backend not in {"uia_hook", "off", "disabled", "none"}:
        logger.warning("invalid inbound backend %s, fallback to uia_hook", inbound_backend)
        inbound_backend = "uia_hook"
    listener_enabled = _parse_bool(args.listener_enabled, default=True) and inbound_backend == "uia_hook"
    queue_file = _normalize_optional_path(args.command_queue_file)
    result_file = _normalize_optional_path(args.command_result_file)
    if not queue_file or not result_file:
        logger.error("command queue/result files are required")
        return 2
    _ensure_parent_dir(queue_file)
    _ensure_parent_dir(result_file)
    if not os.path.exists(queue_file):
        open(queue_file, "a", encoding="utf-8").close()
    if not os.path.exists(result_file):
        open(result_file, "a", encoding="utf-8").close()
    try:
        controller = WeChatUiAutomationClient(wechat_path=args.wechat_path)
    except Exception as exc:
        logger.error("failed to initialize UIAutomation client: %s", exc)
        return 2
    inbound_bridge = WeChatPureUiaInboundBridge(
        edge_url=str(args.edge_url or DEFAULT_EDGE_URL).strip(),
        enabled=listener_enabled,
        poll_interval_s=float(args.listener_poll_interval),
        callback_timeout_s=float(args.callback_timeout),
        dedup_window_s=float(args.listener_dedup_window),
        client=controller,
    )
    inbound_bridge.start()
    logger.info(
        "uia bridge started edge_url=%s queue=%s result=%s listener_enabled=%s inbound_backend=%s",
        str(args.edge_url or DEFAULT_EDGE_URL).strip(),
        queue_file,
        result_file,
        listener_enabled,
        inbound_backend,
    )
    offset = 0
    total = 0
    total_success = 0
    inbound_total = 0
    try:
        while True:
            offset, batch_count, batch_success = _process_command_queue(
                queue_file=queue_file,
                result_file=result_file,
                offset=offset,
                controller=controller,
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
            inbound_forwarded = inbound_bridge.process_cycle()
            if inbound_forwarded > 0:
                inbound_total += inbound_forwarded
                logger.info("forwarded inbound messages: batch=%d cumulative=%d", inbound_forwarded, inbound_total)
            _check_jsonl_rotation(queue_file, result_file)
            time.sleep(max(0.1, float(args.command_poll_interval)))
    except KeyboardInterrupt:
        logger.info("interrupted; total_commands=%d command_success=%d inbound_forwarded=%d", total, total_success, inbound_total)
        return 0
    finally:
        inbound_bridge.stop()


if __name__ == "__main__":
    sys.exit(main())
