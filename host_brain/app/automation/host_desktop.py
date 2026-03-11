"""Host 端本地桌面自动化。

Host Brain 运行在本机时，直接使用 pyautogui/mss 操作本机桌面，
用于游戏辅助、日常操作等常驻任务。

与 Edge 端 DesktopController 的区别：
  - Edge 端：Host 通过 RPC 远程控制 Edge 所在机器
  - Host 端：Host 直接操作自己所在机器（本地调用，无网络开销）

使用方式：
    from host_brain.app.automation.host_desktop import HostDesktopController
    ctrl = HostDesktopController()
    ctrl.capture_screenshot()
    ctrl.execute_actions([...])
"""

from __future__ import annotations

# Host 端本地自动化复用 Edge 端 DesktopController 的全部实现
# 两者逻辑完全相同，只是部署位置不同
from edge_client.actuators.desktop_controller import (
    DesktopAction,
    DesktopActionType,
    DesktopController,
    DesktopExecuteReport,
    ActionExecuteResult,
    ScreenshotResult,
)

# Host 端别名，语义更清晰
HostDesktopController = DesktopController

__all__ = [
    "HostDesktopController",
    "DesktopController",
    "DesktopAction",
    "DesktopActionType",
    "DesktopExecuteReport",
    "ActionExecuteResult",
    "ScreenshotResult",
]
