"""Edge 桌面控制器：截图 + 鼠标/键盘操作。

提供两个核心能力：
  1. capture_screenshot() — 使用 mss 截取屏幕，返回 base64 编码图像
  2. execute_actions()    — 使用 pyautogui 执行点击/输入/滚动等操作

设计原则：
  - 所有操作同步执行（pyautogui 本身是同步的）
  - 失败静默降级，返回结构化错误
  - 支持多显示器（monitor_index 参数）
  - 操作前可选截图验证（截图→操作→截图对比）
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO
from typing import Any

from edge_client.runtime_texts import edge_runtime_text

logger = logging.getLogger("Natsume.edge.actuators.desktop_controller")


# ---------------------------------------------------------------------------
# 操作类型
# ---------------------------------------------------------------------------

class DesktopActionType(str, Enum):
    CLICK = "click"               # 鼠标左键单击
    RIGHT_CLICK = "right_click"   # 鼠标右键单击
    DOUBLE_CLICK = "double_click" # 鼠标左键双击
    MOVE = "move"                 # 移动鼠标（不点击）
    TYPE = "type"                 # 键盘输入文本
    KEY = "key"                   # 按下快捷键（如 "ctrl+c"）
    SCROLL = "scroll"             # 滚动（正数向上，负数向下）
    DRAG = "drag"                 # 拖拽（从 x,y 到 x2,y2）
    SCREENSHOT = "screenshot"     # 截图（内嵌操作）
    WAIT = "wait"                 # 等待指定毫秒


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class DesktopAction:
    """单个桌面操作指令。"""
    type: DesktopActionType
    x: int | None = None          # 鼠标 X 坐标（像素）
    y: int | None = None          # 鼠标 Y 坐标（像素）
    x2: int | None = None         # 拖拽目标 X
    y2: int | None = None         # 拖拽目标 Y
    text: str | None = None       # 输入文本 / 快捷键
    amount: int | None = None     # 滚动量（像素行数）
    duration_ms: int = 0          # 操作持续时间（毫秒，0=瞬间）
    wait_after_ms: int = 100      # 操作后等待毫秒

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "type": self.type.value,
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "text": self.text,
            "amount": self.amount,
            "duration_ms": self.duration_ms,
            "wait_after_ms": self.wait_after_ms,
        }.items() if v is not None}


@dataclass(frozen=True)
class ActionExecuteResult:
    """单个操作执行结果。"""
    action_index: int
    action_type: str
    success: bool
    reason_code: str | None
    detail: str
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_index": self.action_index,
            "action_type": self.action_type,
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


@dataclass
class DesktopExecuteReport:
    """批量操作执行报告。"""
    total_actions: int
    success_count: int
    failed_count: int
    total_latency_ms: int
    results: list[ActionExecuteResult] = field(default_factory=list)
    error: str | None = None

    @property
    def all_success(self) -> bool:
        return self.failed_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_actions": self.total_actions,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "total_latency_ms": self.total_latency_ms,
            "all_success": self.all_success,
            "results": [r.as_dict() for r in self.results],
            "error": self.error,
        }


@dataclass(frozen=True)
class ScreenshotResult:
    """截图结果。"""
    success: bool
    image_b64: str          # base64 编码的 PNG 图像
    image_mime: str         # 固定为 "image/png"
    width: int
    height: int
    monitor_index: int
    latency_ms: int
    reason_code: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "image_b64": self.image_b64,
            "image_mime": self.image_mime,
            "width": self.width,
            "height": self.height,
            "monitor_index": self.monitor_index,
            "latency_ms": self.latency_ms,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# 桌面控制器
# ---------------------------------------------------------------------------

class DesktopController:
    """Edge 桌面控制器：截图 + 鼠标/键盘操作。

    Args:
        monitor_index: 截图目标显示器索引（0=全部显示器合并，1=主显示器，2=副显示器）
        move_duration_s: 鼠标移动动画时长（秒），0=瞬间移动
        failsafe: 是否启用 pyautogui 安全角（鼠标移到左上角时抛出异常）
    """

    def __init__(
        self,
        *,
        monitor_index: int = 1,
        move_duration_s: float = 0.1,
        failsafe: bool = True,
    ) -> None:
        self._monitor_index = monitor_index
        self._move_duration_s = move_duration_s
        self._failsafe = failsafe
        self._stats = {
            "screenshots": 0,
            "actions_executed": 0,
            "actions_failed": 0,
        }

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------

    def capture_screenshot(self, monitor_index: int | None = None) -> ScreenshotResult:
        """截取屏幕，返回 base64 编码的 PNG 图像。"""
        t0 = time.monotonic()
        mon_idx = monitor_index if monitor_index is not None else self._monitor_index

        try:
            import mss
            import mss.tools
        except ImportError as exc:
            return ScreenshotResult(
                success=False,
                image_b64="",
                image_mime="image/png",
                width=0,
                height=0,
                monitor_index=mon_idx,
                latency_ms=0,
                reason_code="DESKTOP_DEPENDENCY_MISSING",
                detail=f"mss 未安装: {exc}",
            )

        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if mon_idx >= len(monitors):
                    mon_idx = 1  # 回退到主显示器
                monitor = monitors[mon_idx]
                screenshot = sct.grab(monitor)

                # 转换为 PNG bytes
                png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)
                image_b64 = base64.b64encode(png_bytes).decode("utf-8")

                latency_ms = int((time.monotonic() - t0) * 1000)
                self._stats["screenshots"] += 1

                return ScreenshotResult(
                    success=True,
                    image_b64=image_b64,
                    image_mime="image/png",
                    width=screenshot.width,
                    height=screenshot.height,
                    monitor_index=mon_idx,
                    latency_ms=latency_ms,
                    detail=edge_runtime_text(
                        "desktop_controller",
                        "screenshot_success",
                        default="截图成功",
                    ),
                )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.error("DesktopController: 截图失败: %s", exc)
            return ScreenshotResult(
                success=False,
                image_b64="",
                image_mime="image/png",
                width=0,
                height=0,
                monitor_index=mon_idx,
                latency_ms=latency_ms,
                reason_code="DESKTOP_SCREENSHOT_FAILED",
                detail=str(exc),
            )

    # ------------------------------------------------------------------
    # 操作执行
    # ------------------------------------------------------------------

    def execute_actions(
        self,
        actions: list[DesktopAction],
        *,
        stop_on_error: bool = True,
    ) -> DesktopExecuteReport:
        """批量执行桌面操作。

        Args:
            actions: 操作列表
            stop_on_error: 遇到失败时是否停止后续操作
        """
        try:
            import pyautogui
            pyautogui.FAILSAFE = self._failsafe
            pyautogui.PAUSE = 0  # 我们自己控制等待时间
        except ImportError as exc:
            return DesktopExecuteReport(
                total_actions=len(actions),
                success_count=0,
                failed_count=len(actions),
                total_latency_ms=0,
                error=f"pyautogui 未安装: {exc}",
            )

        results: list[ActionExecuteResult] = []
        total_start = time.monotonic()

        for i, action in enumerate(actions):
            result = self._execute_single(action, index=i)
            results.append(result)
            self._stats["actions_executed"] += 1
            if not result.success:
                self._stats["actions_failed"] += 1
                if stop_on_error:
                    break
            # 操作后等待
            if action.wait_after_ms > 0:
                time.sleep(action.wait_after_ms / 1000.0)

        total_latency_ms = int((time.monotonic() - total_start) * 1000)
        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        return DesktopExecuteReport(
            total_actions=len(actions),
            success_count=success_count,
            failed_count=failed_count,
            total_latency_ms=total_latency_ms,
            results=results,
        )

    def _execute_single(self, action: DesktopAction, index: int) -> ActionExecuteResult:
        """执行单个操作。"""
        import pyautogui
        t0 = time.monotonic()
        duration = action.duration_ms / 1000.0 if action.duration_ms > 0 else self._move_duration_s

        try:
            if action.type == DesktopActionType.CLICK:
                if action.x is None or action.y is None:
                    raise ValueError("click 需要 x, y 坐标")
                pyautogui.click(action.x, action.y, duration=duration)

            elif action.type == DesktopActionType.RIGHT_CLICK:
                if action.x is None or action.y is None:
                    raise ValueError("right_click 需要 x, y 坐标")
                pyautogui.rightClick(action.x, action.y, duration=duration)

            elif action.type == DesktopActionType.DOUBLE_CLICK:
                if action.x is None or action.y is None:
                    raise ValueError("double_click 需要 x, y 坐标")
                pyautogui.doubleClick(action.x, action.y, duration=duration)

            elif action.type == DesktopActionType.MOVE:
                if action.x is None or action.y is None:
                    raise ValueError("move 需要 x, y 坐标")
                pyautogui.moveTo(action.x, action.y, duration=duration)

            elif action.type == DesktopActionType.TYPE:
                if not action.text:
                    raise ValueError("type 需要 text 参数")
                # 使用 pyperclip 粘贴方式输入中文（避免 typewrite 不支持中文）
                try:
                    import pyperclip
                    pyperclip.copy(action.text)
                    pyautogui.hotkey("ctrl", "v")
                except ImportError:
                    pyautogui.typewrite(action.text, interval=0.05)

            elif action.type == DesktopActionType.KEY:
                if not action.text:
                    raise ValueError("key 需要 text 参数（如 'ctrl+c'）")
                keys = [k.strip() for k in action.text.split("+")]
                if len(keys) == 1:
                    pyautogui.press(keys[0])
                else:
                    pyautogui.hotkey(*keys)

            elif action.type == DesktopActionType.SCROLL:
                x = action.x
                y = action.y
                amount = action.amount or 3
                if x is not None and y is not None:
                    pyautogui.scroll(amount, x=x, y=y)
                else:
                    pyautogui.scroll(amount)

            elif action.type == DesktopActionType.DRAG:
                if any(v is None for v in (action.x, action.y, action.x2, action.y2)):
                    raise ValueError("drag 需要 x, y, x2, y2 坐标")
                pyautogui.drag(
                    action.x2 - action.x,  # type: ignore[operator]
                    action.y2 - action.y,  # type: ignore[operator]
                    duration=max(duration, 0.2),
                    button="left",
                )

            elif action.type == DesktopActionType.WAIT:
                wait_ms = action.duration_ms or 500
                time.sleep(wait_ms / 1000.0)

            elif action.type == DesktopActionType.SCREENSHOT:
                # 内嵌截图操作（通常用于验证）
                self.capture_screenshot()

            latency_ms = int((time.monotonic() - t0) * 1000)
            return ActionExecuteResult(
                action_index=index,
                action_type=action.type.value,
                success=True,
                reason_code=None,
                detail=edge_runtime_text(
                    "desktop_controller",
                    "action_success",
                    default="执行成功",
                ),
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "DesktopController: 操作失败 index=%d type=%s: %s",
                index, action.type.value, exc,
            )
            return ActionExecuteResult(
                action_index=index,
                action_type=action.type.value,
                success=False,
                reason_code="DESKTOP_ACTION_FAILED",
                detail=str(exc),
                latency_ms=latency_ms,
            )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def get_screen_size() -> tuple[int, int]:
        """返回主显示器分辨率 (width, height)。"""
        try:
            import pyautogui
            return pyautogui.size()
        except Exception:
            return (1920, 1080)

    def health(self) -> dict[str, Any]:
        """返回控制器健康快照。"""
        w, h = self.get_screen_size()
        return {
            "monitor_index": self._monitor_index,
            "screen_size": {"width": w, "height": h},
            "failsafe": self._failsafe,
            "stats": dict(self._stats),
        }
