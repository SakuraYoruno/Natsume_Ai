"""桌面控制 Skill 包。

声明式 Provider：新增桌面自动化 action 时，只需补一个
`DeclarativeSkillSpec` 和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec

_MOUSE_BUTTONS = frozenset({"left", "right", "middle"})
_MOUSE_ACTIONS = frozenset({"click", "double_click", "right_click", "move", "scroll"})
def _normalize_desktop_screenshot(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    save_path = str((args or {}).get("save_path") or "").strip() or None
    region = (args or {}).get("region")
    if not isinstance(region, dict):
        region = None
    try:
        monitor = max(0, int((args or {}).get("monitor") or 0))
    except (TypeError, ValueError):
        monitor = 0
    with_cursor_raw = (args or {}).get("with_cursor")
    with_cursor = bool(with_cursor_raw) if with_cursor_raw is not None else False

    return {
        "save_path": save_path,
        "region": region,
        "monitor": monitor,
        "with_cursor": with_cursor,
        "return_base64": save_path is None,
        "manifest_ref": "desktop_screenshot::screen",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_desktop_click(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    x_raw = (args or {}).get("x")
    y_raw = (args or {}).get("y")
    x = int(x_raw) if x_raw is not None else None
    y = int(y_raw) if y_raw is not None else None
    target_desc = str((args or {}).get("target_desc") or "").strip() or None
    action = str((args or {}).get("action") or "click").strip().lower()
    if action not in _MOUSE_ACTIONS:
        action = "click"
    button = str((args or {}).get("button") or "left").strip().lower()
    if button not in _MOUSE_BUTTONS:
        button = "left"
    try:
        move_duration_ms = max(0, min(2000, int((args or {}).get("move_duration_ms") or 100)))
    except (TypeError, ValueError):
        move_duration_ms = 100

    return {
        "x": x,
        "y": y,
        "target_desc": target_desc,
        "click_action": action,
        "button": button,
        "move_duration_ms": move_duration_ms,
        "manifest_ref": f"desktop_click::{target_desc or f'{x},{y}'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_desktop_type(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    text = str((args or {}).get("text") or "")
    try:
        delay_ms = max(0, min(500, int((args or {}).get("delay_ms") or 30)))
    except (TypeError, ValueError):
        delay_ms = 30
    try:
        interval_ms = max(0, min(1000, int((args or {}).get("interval_ms") or 0)))
    except (TypeError, ValueError):
        interval_ms = 0
    press_enter_raw = (args or {}).get("press_enter")
    press_enter = bool(press_enter_raw) if press_enter_raw is not None else False

    return {
        "text": text,
        "delay_ms": delay_ms,
        "interval_ms": interval_ms,
        "press_enter": press_enter,
        "manifest_ref": f"desktop_type::{text[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_desktop_hotkey(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    keys_raw = (args or {}).get("keys")
    if isinstance(keys_raw, list):
        keys = [str(k).strip().lower() for k in keys_raw if k]
    elif isinstance(keys_raw, str):
        keys = [k.strip().lower() for k in keys_raw.split("+") if k.strip()]
    else:
        keys = []
    try:
        hold_ms = max(0, min(5000, int((args or {}).get("hold_ms") or 50)))
    except (TypeError, ValueError):
        hold_ms = 50
    try:
        repeat = max(1, min(20, int((args or {}).get("repeat") or 1)))
    except (TypeError, ValueError):
        repeat = 1

    return {
        "keys": keys,
        "hold_ms": hold_ms,
        "repeat": repeat,
        "manifest_ref": f"desktop_hotkey::{'+'.join(keys)}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_clipboard_read(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    content_type = str((args or {}).get("content_type") or "text").strip().lower()
    if content_type not in {"text", "image", "any"}:
        content_type = "text"

    return {
        "content_type": content_type,
        "manifest_ref": "clipboard_read::clipboard",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_clipboard_write(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    text = str((args or {}).get("text") or "")
    return {
        "text": text,
        "manifest_ref": f"clipboard_write::{text[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


class DesktopSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="desktop_screenshot",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="截取桌面屏幕截图（全屏或指定区域），返回 base64 图像或保存路径。",
            tool="desktop",
            action="screenshot",
            normalize_args=_normalize_desktop_screenshot,
        ),
        DeclarativeSkillSpec(
            skill_name="desktop_click",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            description="在桌面指定坐标或目标元素上执行鼠标点击操作。",
            tool="desktop",
            action="click",
            normalize_args=_normalize_desktop_click,
        ),
        DeclarativeSkillSpec(
            skill_name="desktop_type",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            description="在当前焦点位置模拟键盘输入文本（逐字输入，支持特殊字符）。",
            tool="desktop",
            action="type",
            normalize_args=_normalize_desktop_type,
        ),
        DeclarativeSkillSpec(
            skill_name="desktop_hotkey",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            description="触发桌面快捷键组合（如 Ctrl+C / Alt+F4 / Win+D 等）。",
            tool="desktop",
            action="hotkey",
            normalize_args=_normalize_desktop_hotkey,
        ),
        DeclarativeSkillSpec(
            skill_name="clipboard_read",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="读取系统剪贴板内容（文本或图像）。",
            tool="desktop",
            action="clipboard_read",
            normalize_args=_normalize_clipboard_read,
        ),
        DeclarativeSkillSpec(
            skill_name="clipboard_write",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="写入内容到系统剪贴板（覆盖当前剪贴板内容）。",
            tool="desktop",
            action="clipboard_write",
            normalize_args=_normalize_clipboard_write,
        ),
    )
