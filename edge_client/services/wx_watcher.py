"""WeChat decrypted DB watcher.

Uses watchdog when available; falls back to interval polling otherwise.
This module must never fail Gateway startup when watcher bootstrap fails.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from edge_client.services.wx_db_service import WxDbService, WxMessage

logger = logging.getLogger("Natsume.edge.services.wx_watcher")

NewMessageCallback = Callable[[list[WxMessage]], None]


class WxDbWatcher:
    """Watch decrypted WeChat DB files and trigger incremental reads."""

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
        self._mode = "none"  # watchdog | poll | none

    def start(self) -> str:
        """Start watching. Prefer watchdog, fallback to polling."""
        # 监听的是解密输出而非原始微信库：将密钥与加密格式隔离在刷新器中，本组件
        # 只把增量读取结果交给上层回调，不能直接发送消息或修改源库。
        decrypt_dir = Path(self._svc._dir)
        if not decrypt_dir.exists():
            logger.warning(
                "WxDbWatcher: decrypt output dir not found yet, fallback handling enabled: %s",
                decrypt_dir,
            )

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            svc = self._svc
            callback = self._on_new_messages

            class _Handler(FileSystemEventHandler):
                def on_modified(self, event):  # type: ignore[override]
                    _on_event(event)

                def on_created(self, event):  # type: ignore[override]
                    _on_event(event)

                def on_moved(self, event):  # type: ignore[override]
                    _on_event(event)

            def _on_event(event) -> None:
                try:
                    if getattr(event, "is_directory", False):
                        return
                    src_path = str(getattr(event, "src_path", "") or "")
                    if "de_message_" not in src_path or not src_path.endswith(".db"):
                        return
                    # 文件事件可能重复、乱序或发生在事务尚未完全落盘时；服务层使用
                    # 持久水位做幂等增量读取，因此回调只消费真正尚未观察到的消息。
                    new_msgs = svc.poll_new_messages()
                    if new_msgs:
                        callback(new_msgs)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("WxDbWatcher: incremental read failed: %s", exc)

            self._observer = Observer()
            self._observer.schedule(
                _Handler(),
                path=str(decrypt_dir),
                recursive=False,
            )
            self._observer.start()
            self._mode = "watchdog"
            logger.info("WxDbWatcher: started in watchdog mode, path=%s", decrypt_dir)
        except ImportError:
            # watchdog 为可选优化；缺失时轮询仍以同一水位协议恢复，不改变消息语义。
            logger.warning(
                "WxDbWatcher: watchdog not installed, fallback to %.1fs polling",
                self._poll_interval_s,
            )
            self._start_poll_fallback()
            self._mode = "poll"
        except Exception as exc:
            logger.warning(
                "WxDbWatcher: watchdog start failed, fallback to %.1fs polling: %s",
                self._poll_interval_s,
                exc,
            )
            self._start_poll_fallback()
            self._mode = "poll"

        return self._mode

    def stop(self) -> None:
        """Stop watcher."""
        # 先设置共享退出事件，再等待后台线程，确保轮询不会在资源释放后继续调用回调。
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
            self._poll_thread = None
        self._mode = "none"
        logger.info("WxDbWatcher: stopped")

    def _start_poll_fallback(self) -> None:
        # 每次 start 都清除上次 stop 的状态，使同一进程内重启监听器能够重新进入循环。
        self._shutdown.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="wx-db-watcher-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        # Event.wait 同时承担定时器和可中断睡眠：关闭时无需等待完整轮询周期即可退出。
        while not self._shutdown.wait(timeout=self._poll_interval_s):
            try:
                new_msgs = self._svc.poll_new_messages()
                if new_msgs:
                    self._on_new_messages(new_msgs)
            except Exception as exc:
                logger.warning("WxDbWatcher: polling failed: %s", exc)

    @property
    def mode(self) -> str:
        return self._mode
