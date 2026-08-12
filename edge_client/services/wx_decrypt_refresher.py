"""微信数据库解密刷新器。

职责：
1. 启动后立即执行一次解密，保证 de_*.db 可用；
2. 支持 watchdog 监听 db_storage/*.db 变化触发刷新；
3. 保留定时刷新兜底（WECHAT_DECRYPT_REFRESH_INTERVAL_S）。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("Natsume.edge.services.wx_decrypt_refresher")


class WxDecryptRefresher:
    """微信解密刷新后台任务。"""

    def __init__(
        self,
        *,
        db_storage_dir: str | Path,
        decrypt_output_dir: str | Path,
        key_cache_path: str | Path | None = None,
        refresh_interval_s: int = 3600,
        min_trigger_interval_s: float = 1.5,
        fs_trigger_interval_s: float = 3.0,
    ) -> None:
        self._db_storage_dir = Path(db_storage_dir)
        self._decrypt_output_dir = Path(decrypt_output_dir)
        self._key_cache_path = Path(key_cache_path) if key_cache_path else None
        self._refresh_interval_s = max(30, int(refresh_interval_s))
        self._min_trigger_interval_s = max(0.5, float(min_trigger_interval_s))
        self._fs_trigger_interval_s = max(1.0, float(fs_trigger_interval_s))
        self._shutdown = threading.Event()
        self._trigger = threading.Event()
        self._worker: threading.Thread | None = None
        self._observer = None
        self._mode = "interval"
        self._last_refresh_monotonic = 0.0
        self._last_fs_trigger_monotonic = 0.0
        self._refresh_lock = threading.Lock()

    @property
    def mode(self) -> str:
        return self._mode

    def start(self) -> str:
        """启动刷新器。"""
        # 先置位触发信号再启动线程，保证即使没有新的文件系统事件，已有加密库也会
        # 被刷新为可供只读查询的解密副本。
        self._shutdown.clear()
        self._trigger.set()  # 先跑一次
        self._start_watchdog()
        self._worker = threading.Thread(
            target=self._loop,
            name="wx-decrypt-refresher",
            daemon=True,
        )
        self._worker.start()
        logger.info(
            "WxDecryptRefresher: started mode=%s db_storage=%s out=%s interval=%ss fs_trigger_interval=%ss",
            self._mode,
            self._db_storage_dir,
            self._decrypt_output_dir,
            self._refresh_interval_s,
            self._fs_trigger_interval_s,
        )
        return self._mode

    def stop(self) -> None:
        """停止刷新器。"""
        self._shutdown.set()
        self._trigger.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                pass
            self._observer = None
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        logger.info("WxDecryptRefresher: stopped")

    def _start_watchdog(self) -> None:
        # 文件监听只是降低可见延迟的加速层；可选依赖缺失不能阻断边缘端启动，
        # 因而始终保留定时刷新作为跨平台的恢复路径。
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:
            self._mode = "interval"
            return

        refresher = self

        class _DbStorageHandler(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:
                refresher._on_fs_event(event)

            def on_created(self, event: Any) -> None:
                refresher._on_fs_event(event)

            def on_moved(self, event: Any) -> None:
                refresher._on_fs_event(event)

        try:
            observer = Observer()
            observer.schedule(_DbStorageHandler(), path=str(self._db_storage_dir), recursive=True)
            observer.start()
            self._observer = observer
            self._mode = "watchdog+interval"
        except Exception as exc:
            logger.warning("WxDecryptRefresher: watchdog start failed, fallback interval: %s", exc)
            self._observer = None
            self._mode = "interval"

    def _on_fs_event(self, event: Any) -> None:
        try:
            is_dir = bool(getattr(event, "is_directory", False))
            src_path = str(getattr(event, "src_path", "") or "").lower()
        except Exception:
            is_dir = False
            src_path = ""
        if is_dir or not src_path:
            return
        file_name = Path(src_path).name
        if not file_name.endswith((".db", ".db-wal", ".db-shm", ".db-journal")):
            return
        # SQLite 会连续写入主库和日志文件。按单调时钟合并事件，避免一次事务触发多次
        # 昂贵解密；单调时钟不受系统校时影响，适合作为节流依据。
        now_mono = time.monotonic()
        if (now_mono - self._last_fs_trigger_monotonic) < self._fs_trigger_interval_s:
            return
        self._last_fs_trigger_monotonic = now_mono
        self._trigger.set()

    def _loop(self) -> None:
        # 同一事件既可能由监听器产生，也可能恰好与定时点重合；循环将二者收敛为一次
        # 刷新，并在停机信号到来后先退出，避免关闭期间继续读取外部数据库。
        next_interval_at = time.monotonic()
        while not self._shutdown.is_set():
            now_mono = time.monotonic()
            timeout = max(0.2, next_interval_at - now_mono)
            self._trigger.wait(timeout=timeout)
            if self._shutdown.is_set():
                break

            now_mono = time.monotonic()
            interval_due = now_mono >= next_interval_at
            trigger_due = self._trigger.is_set()
            if not interval_due and not trigger_due:
                continue

            if (now_mono - self._last_refresh_monotonic) < self._min_trigger_interval_s:
                # 丢弃已被更近一次刷新覆盖的触发，而不是累积队列，防止高频 WAL 写入
                # 让解密任务长期追赶不到最新状态。
                self._trigger.clear()
                continue

            self._trigger.clear()
            self._run_refresh()
            self._last_refresh_monotonic = time.monotonic()
            next_interval_at = self._last_refresh_monotonic + float(self._refresh_interval_s)

    def _run_refresh(self) -> None:
        # 解密会扫描同一输出目录，必须串行化；重入触发可安全跳过，因为后续定时或
        # 文件事件会再次唤醒刷新器，且本次运行已覆盖当前可见快照。
        if not self._refresh_lock.acquire(blocking=False):
            logger.debug("WxDecryptRefresher: refresh already in progress, skip trigger")
            return
        try:
            from tools.wx4_decrypt import decrypt_all_databases

            result = decrypt_all_databases(
                str(self._db_storage_dir),
                str(self._decrypt_output_dir),
                key_cache_path=str(self._key_cache_path) if self._key_cache_path else None,
            )
            ok = sum(1 for row in result.values() if str(row.get("status")) in {"ok", "ok_partial"})
            fail = sum(1 for row in result.values() if str(row.get("status")) == "fail")
            no_key = sum(1 for row in result.values() if str(row.get("status")) == "no_key")
            logger.info(
                "WxDecryptRefresher: decrypt finished ok=%d fail=%d no_key=%d total=%d",
                ok,
                fail,
                no_key,
                len(result),
            )
        except Exception as exc:
            # 外部客户端可能正在替换数据库或密钥尚不可用。记录失败并让后台循环重试，
            # 不把临时解密故障升级为网关的启动或消息处理故障。
            logger.warning("WxDecryptRefresher: decrypt failed: %s", exc)
        finally:
            self._refresh_lock.release()
