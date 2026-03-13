"""桌面控制 Skill 包（P1 优先级）。

提供对本地桌面的鼠标/键盘控制、截图和剪贴板操作能力，
支撑 PRD S2 场景（桌面自动化 + 辅助操作）。
与 edge_client/actuators/desktop_controller.py 深度集成。

风险分级说明：
  risk_level=2  只读操作（desktop_screenshot / clipboard_read）
  risk_level=3  写操作（desktop_click / desktop_type / desktop_hotkey / clipboard_write）
  risk_level=4  破坏性操作（desktop_kill_process），require_confirm=True

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/desktop_executor.py 路由到 edge_client/actuators/desktop_controller.py 执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的鼠标按键
_MOUSE_BUTTONS = frozenset({"left", "right", "middle"})
# 支持的鼠标动作
_MOUSE_ACTIONS = frozenset({"click", "double_click", "right_click", "move", "scroll"})


class DesktopSkillProvider:
    """桌面控制 Skill 集合。"""

    @skill(
        skill_name="desktop_screenshot",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="截取桌面屏幕截图（全屏或指定区域），返回 base64 图像或保存路径。",
    )
    def desktop_screenshot(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """截取桌面截图。

        Args:
            save_path (str): 截图保存路径（可选，不填则返回 base64）
            region (dict): 截图区域 {x, y, width, height}（可选，不填则全屏）
            monitor (int): 显示器编号（默认 0，主显示器）
            with_cursor (bool): 是否包含鼠标光标（默认 False）
        """
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
            "status": "OK",
            "tool": "desktop",
            "action": "screenshot",
            "save_path": save_path,
            "region": region,
            "monitor": monitor,
            "with_cursor": with_cursor,
            "return_base64": save_path is None,
            "manifest_ref": "desktop_screenshot::screen",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="desktop_click",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=False,
        description="在桌面指定坐标或目标元素上执行鼠标点击操作。",
    )
    def desktop_click(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """执行桌面鼠标点击。

        Args:
            x (int): 点击 X 坐标（与 target_desc 二选一）
            y (int): 点击 Y 坐标（与 target_desc 二选一）
            target_desc (str): 目标元素描述（视觉定位，与 x/y 二选一）
            action (str): 动作类型，click / double_click / right_click（默认 click）
            button (str): 鼠标按键，left / right / middle（默认 left）
            move_duration_ms (int): 鼠标移动时长毫秒数（默认 100，0 表示瞬移）
        """
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
            "status": "OK",
            "tool": "desktop",
            "action": "click",
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

    @skill(
        skill_name="desktop_type",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=False,
        description="在当前焦点位置模拟键盘输入文本（逐字输入，支持特殊字符）。",
    )
    def desktop_type(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """模拟键盘输入文本。

        Args:
            text (str): 要输入的文本（必填）
            delay_ms (int): 每次按键间隔毫秒数（默认 30，模拟人工输入）
            interval_ms (int): 字符间隔毫秒数（默认 0）
            press_enter (bool): 输入完成后是否按 Enter（默认 False）
        """
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
            "status": "OK",
            "tool": "desktop",
            "action": "type",
            "text": text,
            "delay_ms": delay_ms,
            "interval_ms": interval_ms,
            "press_enter": press_enter,
            "manifest_ref": f"desktop_type::{text[:40]}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="desktop_hotkey",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=False,
        description="触发桌面快捷键组合（如 Ctrl+C / Alt+F4 / Win+D 等）。",
    )
    def desktop_hotkey(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """触发快捷键组合。

        Args:
            keys (list[str]): 按键列表（必填，如 ["ctrl", "c"] / ["alt", "f4"]）
            hold_ms (int): 按键保持时长毫秒数（默认 50）
            repeat (int): 重复次数（默认 1）
        """
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
            "status": "OK",
            "tool": "desktop",
            "action": "hotkey",
            "keys": keys,
            "hold_ms": hold_ms,
            "repeat": repeat,
            "manifest_ref": f"desktop_hotkey::{'+'.join(keys)}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="clipboard_read",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="读取系统剪贴板内容（文本或图像）。",
    )
    def clipboard_read(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """读取剪贴板内容。

        Args:
            content_type (str): 内容类型，text / image / any（默认 text）
        """
        content_type = str((args or {}).get("content_type") or "text").strip().lower()
        if content_type not in {"text", "image", "any"}:
            content_type = "text"

        return {
            "status": "OK",
            "tool": "desktop",
            "action": "clipboard_read",
            "content_type": content_type,
            "manifest_ref": "clipboard_read::clipboard",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="clipboard_write",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="写入内容到系统剪贴板（覆盖当前剪贴板内容）。",
    )
    def clipboard_write(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """写入剪贴板内容。

        Args:
            text (str): 要写入的文本内容（必填）
        """
        text = str((args or {}).get("text") or "")

        return {
            "status": "OK",
            "tool": "desktop",
            "action": "clipboard_write",
            "text": text,
            "manifest_ref": f"clipboard_write::{text[:40]}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_desktop_skill_registry(provider: DesktopSkillProvider | None = None) -> SkillRegistry:
    """构建包含桌面控制 Skills 的 SkillRegistry。

    Desktop Skill 列表：
    - desktop_screenshot：桌面截图（risk=2，只读）
    - desktop_click：鼠标点击（risk=3，写操作）
    - desktop_type：键盘输入（risk=3，写操作）
    - desktop_hotkey：快捷键触发（risk=3，写操作）
    - clipboard_read：读取剪贴板（risk=2，只读）
    - clipboard_write：写入剪贴板（risk=3，写操作）

    Returns:
        已注册 6 个桌面控制 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or DesktopSkillProvider())
    return registry
