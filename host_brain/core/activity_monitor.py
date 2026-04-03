"""ActivityMonitor — Host 端用户活动监控。

在 Host 端本地运行，负责：
  1. 前台窗口进程检测（psutil + GetForegroundWindow）
  2. 游戏进程匹配 → 自动切换 gaming 模式
  3. 空闲检测 → 深夜长时间无操作自动切换 sleep
  4. 早间唤醒检查

设计原则：
  - 纯 Host 端运行，不依赖 Edge
  - 使用 threading.Timer 轮询，不阻塞主线程
  - 对资源占用极低（轮询间隔 30s / 60s）
  - Windows 专用 API 降级处理（非 Windows 跳过）
"""

from __future__ import annotations

import logging
import os
import platform
import re
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("Natsume.activity_monitor")


# ---------------------------------------------------------------------------
# 已知游戏进程列表（可通过环境变量扩展）
# ---------------------------------------------------------------------------

_DEFAULT_GAMING_PROCESSES: set[str] = {
    # Steam / 通用
    "steam.exe",
    "steamwebhelper.exe",
    # 原神
    "genshinimpact.exe",
    "yuanshen.exe",
    # 崩坏：星穹铁道
    "starrailbase.exe",
    "starraill.exe",
    # 绝区零
    "zzzthegame.exe",
    # 明日方舟
    "arknights.exe",
    # LOL
    "league of legends.exe",
    "leagueclient.exe",
    "leagueclientux.exe",
    # Valorant
    "valorant.exe",
    "valorant-win64-shipping.exe",
    # CS2
    "cs2.exe",
    # Minecraft
    "javaw.exe",  # Minecraft Java
    "minecraft.windows.exe",
    # Apex
    "r5apex.exe",
    # 永劫无间
    "naraka-win64-shipping.exe",
    # 其他常见
    "dota2.exe",
    "overwatch.exe",
    "fortnite.exe",
    "pubg.exe",
    "tslgame.exe",
    "gta5.exe",
    "eldenring.exe",
    "darksouls3.exe",
    "sekiro.exe",
    "riotclientservices.exe",
    "battlenet.exe",
    "honkaiimpact3rd.exe",
    # Epic Games Launcher
    "epicgameslauncher.exe",
}

# 通过环境变量追加自定义游戏进程
_EXTRA_GAMING = os.environ.get("HOST_GAMING_PROCESSES", "").strip()
if _EXTRA_GAMING:
    for p in _EXTRA_GAMING.split(","):
        p = p.strip().lower()
        if p:
            _DEFAULT_GAMING_PROCESSES.add(p)

# 游戏窗口标题关键词（当进程名匹配不到时，用标题做二次匹配）
_GAMING_TITLE_KEYWORDS: set[str] = {
    "原神", "genshin impact",
    "崩坏：星穹铁道", "honkai: star rail",
    "绝区零", "zenless zone zero",
    "league of legends",
    "valorant",
    "counter-strike",
    "minecraft",
    "apex legends",
    "永劫无间", "naraka",
}

# 向后兼容：保留历史公开常量名
GAMING_PROCESS_NAMES = _DEFAULT_GAMING_PROCESSES
GAMING_TITLE_KEYWORDS = _GAMING_TITLE_KEYWORDS


# ---------------------------------------------------------------------------
# Windows 前台窗口检测
# ---------------------------------------------------------------------------

def _get_foreground_window_info() -> dict[str, Any] | None:
    """获取当前前台窗口信息（Windows 专用）。

    Returns:
        {"pid": int, "process_name": str, "window_title": str} 或 None
    """
    if platform.system() != "Windows":
        return None

    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # 获取前台窗口句柄
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # 获取窗口标题
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        # 获取进程 ID
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = pid.value

        # 获取进程名
        process_name = ""
        try:
            import psutil
            proc = psutil.Process(pid_val)
            process_name = proc.name().lower()
        except Exception:
            # psutil 不可用时，尝试 Windows API
            try:
                PROCESS_QUERY_INFORMATION = 0x0400
                PROCESS_VM_READ = 0x0010
                h_process = kernel32.OpenProcess(
                    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid_val
                )
                if h_process:
                    try:
                        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
                        exe_buf = ctypes.create_unicode_buffer(260)
                        psapi.GetModuleFileNameExW(h_process, None, exe_buf, 260)
                        process_name = os.path.basename(exe_buf.value).lower()
                    finally:
                        kernel32.CloseHandle(h_process)
            except Exception:
                pass

        return {
            "pid": pid_val,
            "process_name": process_name,
            "window_title": title,
        }

    except Exception as exc:
        logger.debug("_get_foreground_window_info failed: %s", exc)
        return None


def _is_gaming_process(process_name: str, window_title: str) -> bool:
    """判断是否为游戏进程。"""
    name_lower = process_name.lower()

    # 进程名匹配
    if name_lower in _DEFAULT_GAMING_PROCESSES:
        return True

    # 窗口标题关键词匹配
    title_lower = window_title.lower()
    for keyword in _GAMING_TITLE_KEYWORDS:
        if keyword.lower() in title_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# ActivityMonitor
# ---------------------------------------------------------------------------

class ActivityMonitor:
    """Host 端用户活动监控服务。

    用法::

        monitor = ActivityMonitor(
            mode_transition_service=mode_service,
        )
        monitor.start()

        # 系统关闭时
        monitor.stop()
    """

    def __init__(
        self,
        *,
        mode_service: Any = None,
        mode_transition_service: Any = None,
        gaming_check_interval_s: float = 30.0,
        idle_check_interval_s: float = 300.0,
        on_activity_detected: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._mode_service = mode_transition_service if mode_transition_service is not None else mode_service
        self._gaming_interval = float(gaming_check_interval_s)
        self._idle_interval = float(idle_check_interval_s)
        # 向后兼容旧测试和调用点的内部字段名
        self._gaming_check_interval_s = self._gaming_interval
        self._idle_check_interval_s = self._idle_interval
        self._on_activity = on_activity_detected

        # 运行状态
        self._running = False
        self._shutdown_event = threading.Event()
        self._gaming_thread: threading.Thread | None = None
        self._idle_thread: threading.Thread | None = None

        # 状态跟踪
        self._current_foreground: str = ""
        self._is_gaming: bool = False
        self._gaming_process_name: str = ""
        self._last_activity_ts: float = time.time()
        self._stats_lock = threading.Lock()

        # 统计
        self._gaming_detect_count = 0
        self._gaming_exit_count = 0
        self._idle_trigger_count = 0

        logger.info(
            "ActivityMonitor created (gaming_interval=%.1fs, idle_interval=%.1fs, platform=%s)",
            self._gaming_interval,
            self._idle_interval,
            platform.system(),
        )

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动活动监控。"""
        if self._running:
            logger.warning("ActivityMonitor already running")
            return

        if platform.system() != "Windows":
            logger.warning("ActivityMonitor: 非 Windows 平台，游戏进程检测不可用（其他功能正常）")

        self._running = True
        self._shutdown_event.clear()

        # 游戏进程检测线程
        self._gaming_thread = threading.Thread(
            target=self._gaming_check_loop,
            name="activity-gaming-check",
            daemon=True,
        )
        self._gaming_thread.start()

        # 空闲检测线程
        self._idle_thread = threading.Thread(
            target=self._idle_check_loop,
            name="activity-idle-check",
            daemon=True,
        )
        self._idle_thread.start()

        logger.info("ActivityMonitor started")

    def stop(self) -> None:
        """停止活动监控。"""
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()
        if self._gaming_thread and self._gaming_thread.is_alive():
            self._gaming_thread.join(timeout=5.0)
        if self._idle_thread and self._idle_thread.is_alive():
            self._idle_thread.join(timeout=5.0)
        logger.info("ActivityMonitor stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── 游戏进程检测循环 ──────────────────────────────────────────────────

    def _gaming_check_loop(self) -> None:
        """每 N 秒检测一次前台窗口，判断是否为游戏进程。"""
        while not self._shutdown_event.is_set():
            try:
                self._check_foreground()
            except Exception as exc:
                logger.debug("Gaming check error: %s", exc)
            self._shutdown_event.wait(timeout=self._gaming_interval)

    def _check_foreground(self) -> None:
        """检测前台窗口进程，触发 gaming 模式切换。"""
        info = _get_foreground_window_info()
        if info is None:
            return

        process_name = info.get("process_name", "")
        window_title = info.get("window_title", "")
        is_gaming = _is_gaming_process(process_name, window_title)

        with self._stats_lock:
            prev_gaming = self._is_gaming
            self._current_foreground = process_name
            self._is_gaming = is_gaming

            if is_gaming:
                self._gaming_process_name = process_name
                self._last_activity_ts = time.time()

        # 状态变化时触发事件
        if is_gaming and not prev_gaming:
            # 进入游戏
            self._gaming_detect_count += 1
            logger.info("🎮 游戏进程检测到: %s (%s)", process_name, window_title)
            if self._mode_service:
                self._mode_service.on_event("gaming_process_detected", {
                    "process_name": process_name,
                    "window_title": window_title,
                    "pid": info.get("pid"),
                })
            if self._on_activity:
                self._on_activity({
                    "type": "gaming_detected",
                    "process_name": process_name,
                    "window_title": window_title,
                })

        elif not is_gaming and prev_gaming:
            # 退出游戏
            self._gaming_exit_count += 1
            logger.info("🎮 游戏进程退出，当前前台: %s", process_name)
            if self._mode_service:
                self._mode_service.on_event("gaming_process_exited", {
                    "process_name": process_name,
                })
            if self._on_activity:
                self._on_activity({
                    "type": "gaming_exited",
                    "process_name": process_name,
                })

        # 非游戏进程有活动 → 更新最后活动时间
        if not is_gaming and process_name:
            with self._stats_lock:
                self._last_activity_ts = time.time()

    # ── 空闲检测循环 ──────────────────────────────────────────────────────

    def _idle_check_loop(self) -> None:
        """每 N 秒检查一次空闲状态和早间唤醒。"""
        while not self._shutdown_event.is_set():
            try:
                self._check_idle()
                self._check_morning()
            except Exception as exc:
                logger.debug("Idle check error: %s", exc)
            self._shutdown_event.wait(timeout=self._idle_interval)

    def _check_idle(self) -> None:
        """空闲检测：通知 ModeTransitionService。"""
        if self._mode_service:
            self._mode_service.on_event("idle_check")

    def _check_morning(self) -> None:
        """早间唤醒检查。"""
        if self._mode_service:
            self._mode_service.on_event("morning_check")

    # ── 外部事件接口 ──────────────────────────────────────────────────────

    def notify_user_interaction(self) -> None:
        """通知有用户互动（消息输入、语音输入等），更新活动时间。"""
        with self._stats_lock:
            self._last_activity_ts = time.time()
        if self._mode_service:
            self._mode_service.update_last_interaction()
            self._mode_service.on_event("user_message_received", {
                "channel": "host_interaction",
            })

    # ── 查询接口 ──────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """获取活动监控状态。"""
        with self._stats_lock:
            idle_seconds = time.time() - self._last_activity_ts
            return {
                "running": self._running,
                "platform": platform.system(),
                "current_foreground": self._current_foreground,
                "is_gaming": self._is_gaming,
                "gaming_process_name": self._gaming_process_name,
                "idle_seconds": round(idle_seconds, 1),
                "idle_minutes": round(idle_seconds / 60, 1),
                "stats": {
                    "gaming_detect_count": self._gaming_detect_count,
                    "gaming_exit_count": self._gaming_exit_count,
                    "idle_trigger_count": self._idle_trigger_count,
                },
                "gaming_check_interval_s": self._gaming_interval,
                "idle_check_interval_s": self._idle_interval,
                "known_gaming_processes_count": len(_DEFAULT_GAMING_PROCESSES),
            }
