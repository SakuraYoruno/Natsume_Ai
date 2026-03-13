"""桌面控制 Executor。

将 desktop_registry.py 归一化层的 payload 路由到
edge_client/actuators/desktop_controller.py 中的 DesktopController 实例执行。

context 中预期注入的键：
  - desktop_controller: DesktopController 实例（来自 EdgeRuntime 或 dependencies）

支持的 action：
  - screenshot：桌面截图（调用 controller.capture_screenshot）
  - click：鼠标点击（调用 controller.execute_actions with CLICK）
  - type：键盘输入（调用 controller.execute_actions with TYPE）
  - hotkey：快捷键触发（调用 controller.execute_actions with KEY）
  - clipboard_read：读取剪贴板（pyperclip）
  - clipboard_write：写入剪贴板（pyperclip）
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("Natsume.skills.desktop_executor")


def execute_desktop_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行桌面控制操作。

    Args:
        args: 归一化 payload（来自 desktop_registry handler 的返回值）
        context: 执行上下文，需包含 desktop_controller（DesktopController 实例）

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"desktop::{action}")

    try:
        result = _dispatch_desktop_action(action, payload, context)
        return {
            "status": "OK",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
        }
    except Exception as exc:
        _logger.warning("Desktop executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_desktop_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的桌面操作。"""
    _action_map = {
        "screenshot": _execute_screenshot,
        "click": _execute_click,
        "type": _execute_type,
        "hotkey": _execute_hotkey,
        "clipboard_read": _execute_clipboard_read,
        "clipboard_write": _execute_clipboard_write,
    }
    handler = _action_map.get(action)
    if handler is None:
        return {"note": f"unknown desktop action: {action}"}
    return handler(payload, context)


def _get_desktop_controller(context: dict[str, Any] | None) -> Any:
    """从 context 中取出 DesktopController 实例，不存在时懒创建。"""
    ctx = context or {}
    controller = ctx.get("desktop_controller")
    if controller is None:
        # 懒创建：DesktopController 无需外部依赖即可实例化
        try:
            from edge_client.actuators.desktop_controller import DesktopController
            controller = DesktopController()
            _logger.debug("DesktopController 懒创建成功")
        except ImportError as exc:
            _logger.debug("DesktopController 导入失败: %s", exc)
    return controller


def _execute_screenshot(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """截取桌面截图，直接调用 DesktopController.capture_screenshot。"""
    save_path = payload.get("save_path")
    monitor = int(payload.get("monitor") or 1)  # desktop_controller 从 1 开始

    controller = _get_desktop_controller(context)
    if controller is None:
        return {"success": False, "note": "desktop_controller not available"}

    result = controller.capture_screenshot(monitor_index=monitor)
    _logger.info("桌面截图: monitor=%d, success=%s", monitor, result.success)

    detail: dict[str, Any] = {
        "success": result.success,
        "width": result.width,
        "height": result.height,
        "monitor_index": result.monitor_index,
        "latency_ms": result.latency_ms,
        "detail": result.detail,
    }

    # 如果指定了保存路径，写入文件
    if save_path and result.success and result.image_b64:
        try:
            import base64
            png_bytes = base64.b64decode(result.image_b64)
            with open(save_path, "wb") as f:
                f.write(png_bytes)
            detail["save_path"] = save_path
        except Exception as exc:
            detail["save_error"] = str(exc)
    elif result.success:
        detail["image_b64"] = result.image_b64
        detail["image_mime"] = result.image_mime

    return detail


def _execute_click(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """执行鼠标点击，直接调用 DesktopController.execute_actions。"""
    from edge_client.actuators.desktop_controller import DesktopAction, DesktopActionType

    x = payload.get("x")
    y = payload.get("y")
    click_action = str(payload.get("click_action") or "click").lower()
    move_duration_ms = int(payload.get("move_duration_ms") or 100)

    # 映射 action 类型
    _type_map = {
        "click": DesktopActionType.CLICK,
        "double_click": DesktopActionType.DOUBLE_CLICK,
        "right_click": DesktopActionType.RIGHT_CLICK,
        "move": DesktopActionType.MOVE,
    }
    action_type = _type_map.get(click_action, DesktopActionType.CLICK)

    controller = _get_desktop_controller(context)
    if controller is None:
        return {"success": False, "note": "desktop_controller not available"}

    action = DesktopAction(
        type=action_type,
        x=int(x) if x is not None else None,
        y=int(y) if y is not None else None,
        duration_ms=move_duration_ms,
    )
    report = controller.execute_actions([action])
    _logger.info("桌面点击: x=%s, y=%s, action=%s, success=%s", x, y, click_action, report.all_success)
    return report.as_dict()


def _execute_type(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """模拟键盘输入，直接调用 DesktopController.execute_actions。"""
    from edge_client.actuators.desktop_controller import DesktopAction, DesktopActionType

    text = str(payload.get("text") or "")
    press_enter = bool(payload.get("press_enter", False))

    controller = _get_desktop_controller(context)
    if controller is None:
        return {"success": False, "note": "desktop_controller not available"}

    actions = [DesktopAction(type=DesktopActionType.TYPE, text=text)]
    if press_enter:
        actions.append(DesktopAction(type=DesktopActionType.KEY, text="enter"))

    report = controller.execute_actions(actions)
    _logger.info("桌面输入: text=%s..., success=%s", text[:20], report.all_success)
    return report.as_dict()


def _execute_hotkey(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """触发快捷键，直接调用 DesktopController.execute_actions with KEY。"""
    from edge_client.actuators.desktop_controller import DesktopAction, DesktopActionType

    keys = payload.get("keys") or []
    repeat = int(payload.get("repeat") or 1)

    # 将 keys 列表转为 pyautogui 格式（如 ["ctrl", "c"] → "ctrl+c"）
    hotkey_str = "+".join(str(k) for k in keys) if keys else ""

    controller = _get_desktop_controller(context)
    if controller is None:
        return {"success": False, "note": "desktop_controller not available"}

    actions = [
        DesktopAction(type=DesktopActionType.KEY, text=hotkey_str)
        for _ in range(repeat)
    ]
    report = controller.execute_actions(actions)
    _logger.info("桌面快捷键: keys=%s, repeat=%d, success=%s", keys, repeat, report.all_success)
    return report.as_dict()


def _execute_clipboard_read(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """读取剪贴板内容（使用 pyperclip）。"""
    content_type = str(payload.get("content_type") or "text")

    try:
        import pyperclip
        text = pyperclip.paste()
        _logger.info("剪贴板读取: content_type=%s, length=%d", content_type, len(text or ""))
        return {"success": True, "content_type": "text", "text": text}
    except ImportError:
        return {"success": False, "error": "pyperclip 未安装"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _execute_clipboard_write(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """写入剪贴板内容（使用 pyperclip）。"""
    text = str(payload.get("text") or "")

    try:
        import pyperclip
        pyperclip.copy(text)
        _logger.info("剪贴板写入: length=%d", len(text))
        return {"success": True, "text_length": len(text)}
    except ImportError:
        return {"success": False, "error": "pyperclip 未安装"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
