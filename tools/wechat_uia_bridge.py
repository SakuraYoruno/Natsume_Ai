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
DEFAULT_OUTBOUND_ECHO_TTL_S = 180.0

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

logger = logging.getLogger("Natsume.wechat_uia_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_OUTBOUND_ECHO_CACHE: dict[str, float] = {}


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


def _remember_outbound_text(content: str) -> None:
    text = _normalize_message_text(content)
    if text:
        _OUTBOUND_ECHO_CACHE[text] = time.monotonic()


def _is_recent_outbound_echo(content: str) -> bool:
    text = _normalize_message_text(content)
    if not text:
        return False
    now_mono = time.monotonic()
    stale = [item for item, ts in _OUTBOUND_ECHO_CACHE.items() if (now_mono - ts) > DEFAULT_OUTBOUND_ECHO_TTL_S]
    for item in stale:
        _OUTBOUND_ECHO_CACHE.pop(item, None)
    ts_value = _OUTBOUND_ECHO_CACHE.get(text)
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


class WeChatUiAutomationClient:
    def __init__(self, *, wechat_path: str = "") -> None:
        self._wechat_path = str(wechat_path or "").strip()
        self._schema = _load_schema_from_env()
        self._automation = self._create_automation()
        self._true_condition = self._automation.CreateTrueCondition() if self._automation is not None else None

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

    def get_main_element(self) -> IUIAutomationElement | None:
        return self._element_from_handle(self.find_main_hwnd())

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
        _remember_outbound_text(content_value)
        return True, None, f"uia_send_ok target={resolved_target or target}", resolved_target or target

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
            texts: list[str] = []
            for child in self._descendants(element, limit=120):
                if self._control_type(child) != UIA_TextControlTypeId:
                    continue
                text_value = self._name(child)
                if _is_noise_text(text_value, active_title=active_title):
                    continue
                if text_value not in texts:
                    texts.append(text_value)
            content = _normalize_message_text(" ".join(texts))
            if not content or _is_recent_outbound_echo(content):
                continue
            items.append(UiaMessageCandidate(active_title or "active_chat", content, f"{active_title}|{content}|{el_top}", el_top))
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
            if not content or _is_recent_outbound_echo(content):
                continue
            items.append(UiaMessageCandidate(active_title or "active_chat", content, f"{active_title}|{content}|bucket={bucket}", bucket))
        return items

    def collect_inbound_messages(self) -> tuple[str, list[UiaMessageCandidate]]:
        root = self.get_main_element()
        if root is None:
            return "", []
        active_title = self._active_chat_title(root)
        if active_title and (_is_blocked_chat_target(active_title) or (not _is_allowed_contact_target(active_title))):
            return active_title, []
        messages = self._message_item_candidates(root, active_title=active_title)
        if messages:
            return active_title, messages
        return active_title, self._message_text_fallback(root, active_title=active_title)


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

    def start(self) -> None:
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

    def process_cycle(self) -> int:
        if not self._enabled:
            return 0
        now_mono = time.monotonic()
        if now_mono < self._next_poll_at:
            return 0
        self._next_poll_at = now_mono + self._poll_interval_s
        active_title, messages = self._client.collect_inbound_messages()
        if not self._baseline_ready:
            for item in messages:
                self._known_signatures.add(item.signature)
            self._baseline_ready = True
            logger.info("inbound listener baseline initialized: messages=%d chat=%s", len(messages), active_title or "-")
            return 0
        forwarded = 0
        ts_ms = int(time.time() * 1000)
        for item in messages:
            if item.signature in self._known_signatures:
                continue
            self._known_signatures.add(item.signature)
            if self._is_recent_duplicate(item.signature):
                continue
            msg_id = _build_hook_msg_id(sender=item.sender, content=item.content, ts_ms=ts_ms)
            body = {
                "id": msg_id,
                "msg_id": msg_id,
                "sender": item.sender,
                "from_wxid": item.sender,
                "receiver": "local_wechat",
                "to_wxid": "local_wechat",
                "conversation_id": item.sender,
                "content": item.content,
                "text": item.content,
                "type": 1,
                "msg_type": "1",
                "ts": int(ts_ms / 1000),
                "bridge_backend": "uia_hook",
            }
            if _post_to_edge_callback(self._edge_url, body, timeout_s=self._callback_timeout_s):
                forwarded += 1
                logger.info("forwarded inbound message sender=%s content=%.80s", item.sender, item.content)
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
    if action != "send_text":
        result["reason_code"] = "WECHAT_ACTION_UNSUPPORTED"
        result["detail"] = f"unsupported action={action}"
        return result
    candidates = _collect_command_target_candidates(command)
    ok, reason_code, detail, resolved_target = controller.send_text(target=target, content=content, target_candidates=candidates)
    result["success"] = bool(ok)
    result["status"] = "ok" if ok else "error"
    result["reason_code"] = reason_code
    result["detail"] = detail
    if resolved_target:
        result["resolved_target"] = resolved_target
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
            time.sleep(max(0.1, float(args.command_poll_interval)))
    except KeyboardInterrupt:
        logger.info("interrupted; total_commands=%d command_success=%d inbound_forwarded=%d", total, total_success, inbound_total)
        return 0
    finally:
        inbound_bridge.stop()


if __name__ == "__main__":
    sys.exit(main())
