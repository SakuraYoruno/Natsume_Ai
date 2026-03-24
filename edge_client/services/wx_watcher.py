"""微信数据库变更监听器（watchdog 实现）。

使用 watchdog 监听 db_storage 解密输出目录下 de_message_*.db 文件的写入事件，
毫秒级触发增量消息读取，替代 5s 定时轮询。

延迟：从 5s 降至 <100ms，且无 CPU 空转开销。

依赖：pip install watchdog
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from edge_client.services.wx_db_service import WxDbService, WxMessage

logger = logging.getLogger("Natsume.edge.services.wx_watcher")

# 新消息回调类型：接收 WxMessage 列表
NewMessageCallback = Callable[[list[WxMessage]], None]


class WxDbWatcher:
    """监听微信数据库文件变更，触发增量消息读取。

    Args:
        wx_db_service: WxDbService 实例
        on_new_messages: 新消息回调，接收 list[WxMessage]
        poll_interval_s: watchdog 不可用时的降级轮询间隔（秒）
    """

    def __init__(
        self,
        wx_db_service: WxDbService,
        on_new_messages: NewMessageCallback,
        *,
        poll_interval_s: float = 5.0,
    ) -> None:
        self._svc = wx_db_service
        self._on_new_messages = on_new_messages
        self._poll_interval_s = poll_interval_s
        self._observer = None
        self._poll_thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._mode = "none"  # "watchdog" | "poll" | "none"

    def start(self) -> str:
        """启动监听。优先使用 watchdog，降级到定时轮询。

        Returns:
            实际使用的模式："watchdog" 或 "poll"
        """
        decrypt_dir = self._svc._dir
        if not decrypt_dir.exists():
            logger.warning("WxDbWatcher: 解密输出目录不存在: %s，延迟启动", decrypt_dir)

        # 尝试启动 watchdog
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            svc = self._svc
            callback = self._on_new_messages

            class _Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    if (
                        not event.is_directory
                        and "de_message_" in event.src_path
                        and event.src_path.endswith(".db")
                    ):
                        try:
                            new_msgs = svc.poll_new_messages()
                            if new_msgs:
                                callback(new_msgs)
                        except Exception as exc:
                            logger.warning("WxDbWatcher: 增量读取异常: %s", exc)

            self._observer = Observer()
            self._observer.schedule(
                _Handler(),
                path=str(decrypt_dir),
                recursive=False,
            )
            self._observer.start()
            self._mode = "watchdog"
            logger.info("WxDbWatcher: watchdog 模式已启动，监听目录: %s", decrypt_dir)

        except ImportError:
            logger.warning("WxDbWatcher: watchdog 未安装，降级为 %.1fs 定时轮询", self._poll_interval_s)
            self._start_poll_fallback()
            self._mode = "poll"

        return self._mode

    def stop(self) -> None:
        """停止监听。"""
        self._shutdown.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                pass
            self._observer = None
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
        self._mode = "none"
        logger.info("WxDbWatcher: 已停止")

    def _start_poll_fallback(self) -> None:
        """启动降级定时轮询线程。"""
        self._shutdown.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="wx-db-watcher-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        """定时轮询循环（watchdog 不可用时的降级方案）。"""
        while not self._shutdown.wait(timeout=self._poll_interval_s):
            try:
                new_msgs = self._svc.poll_new_messages()
                if new_msgs:
                    self._on_new_messages(new_msgs)
            except Exception as exc:
                logger.warning("WxDbWatcher: 轮询异常: %s", exc)

    @property
    def mode(self) -> str:
        """当前监听模式：'watchdog' | 'poll' | 'none'。"""
        return self._mode
