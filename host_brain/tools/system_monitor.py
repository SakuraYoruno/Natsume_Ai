"""系统监控与进程守护（Phase XII 阶段 5）。

职责：系统资源监控、进程管理、网络探测、进程守护（崩溃自动重启）。
依赖：psutil（可选，不可用时降级到基础 OS 命令）。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_logger = logging.getLogger("Natsume.tools.system_monitor")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class SystemSnapshot:
    """系统资源快照。"""
    cpu_percent: float = 0.0
    memory_total_gb: float = 0.0
    memory_used_gb: float = 0.0
    memory_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0
    gpu_name: str = ""
    gpu_memory_used_mb: float = 0.0
    gpu_memory_total_mb: float = 0.0
    gpu_utilization: float = 0.0
    network_sent_mb: float = 0.0
    network_recv_mb: float = 0.0
    uptime_hours: float = 0.0
    os_name: str = ""
    hostname: str = ""
    ts_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory": f"{self.memory_used_gb:.1f}/{self.memory_total_gb:.1f}GB ({self.memory_percent:.0f}%)",
            "disk": f"{self.disk_used_gb:.0f}/{self.disk_total_gb:.0f}GB ({self.disk_percent:.0f}%)",
            "gpu": f"{self.gpu_name} {self.gpu_memory_used_mb:.0f}/{self.gpu_memory_total_mb:.0f}MB" if self.gpu_name else "N/A",
            "network": f"↑{self.network_sent_mb:.0f}MB ↓{self.network_recv_mb:.0f}MB",
            "uptime_hours": round(self.uptime_hours, 1),
            "os": self.os_name,
            "hostname": self.hostname,
        }


@dataclass
class ProcessInfo:
    """进程信息。"""
    pid: int
    name: str
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    status: str = ""
    cmdline: str = ""
    create_time: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "status": self.status,
        }


@dataclass
class NetworkInfo:
    """网络连接信息。"""
    local_addr: str
    remote_addr: str
    status: str
    pid: int | None = None
    process_name: str = ""


@dataclass
class GuardConfig:
    """进程守护配置。"""
    name: str
    command: str
    cwd: str = ""
    restart_delay_s: float = 5.0
    max_restarts: int = 10
    check_interval_s: float = 30.0


@dataclass
class GuardStatus:
    """守护状态。"""
    name: str
    pid: int | None = None
    running: bool = False
    restart_count: int = 0
    last_restart_ts: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# SystemMonitor
# ---------------------------------------------------------------------------
class SystemMonitor:
    """系统监控器。

    用法::

        monitor = SystemMonitor()

        # 系统快照
        snapshot = monitor.get_snapshot()
        print(f"CPU: {snapshot.cpu_percent}%")

        # 进程列表
        procs = monitor.list_processes(top_n=10, sort_by="memory")

        # 网络连接
        conns = monitor.list_connections()

        # Ping 测试
        latency = monitor.ping("8.8.8.8")
    """

    def __init__(self) -> None:
        self._psutil_available = False
        try:
            import psutil  # noqa: PLC0415, F401
            self._psutil_available = True
        except ImportError:
            _logger.info("psutil 不可用，降级到基础 OS 命令")

    def get_snapshot(self) -> SystemSnapshot:
        """获取系统资源快照。"""
        snap = SystemSnapshot(
            os_name=f"{platform.system()} {platform.release()}",
            hostname=platform.node(),
            ts_ms=int(time.time() * 1000),
        )

        if self._psutil_available:
            import psutil  # noqa: PLC0415
            snap.cpu_percent = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            snap.memory_total_gb = round(mem.total / 1024 ** 3, 2)
            snap.memory_used_gb = round(mem.used / 1024 ** 3, 2)
            snap.memory_percent = mem.percent
            disk = psutil.disk_usage("/") if os.name != "nt" else psutil.disk_usage("C:\\")
            snap.disk_total_gb = round(disk.total / 1024 ** 3, 1)
            snap.disk_used_gb = round(disk.used / 1024 ** 3, 1)
            snap.disk_percent = disk.percent
            net = psutil.net_io_counters()
            snap.network_sent_mb = round(net.bytes_sent / 1024 ** 2, 1)
            snap.network_recv_mb = round(net.bytes_recv / 1024 ** 2, 1)
            snap.uptime_hours = round((time.time() - psutil.boot_time()) / 3600, 1)

        # GPU 信息（nvidia-smi）
        try:
            gpu_out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if gpu_out.returncode == 0:
                parts = gpu_out.stdout.strip().split(",")
                if len(parts) >= 4:
                    snap.gpu_name = parts[0].strip()
                    snap.gpu_memory_used_mb = float(parts[1].strip())
                    snap.gpu_memory_total_mb = float(parts[2].strip())
                    snap.gpu_utilization = float(parts[3].strip())
        except Exception:
            pass

        return snap

    def list_processes(
        self, *, top_n: int = 20, sort_by: str = "memory",
    ) -> list[ProcessInfo]:
        """列出进程（按 CPU 或内存排序）。"""
        if not self._psutil_available:
            return []

        import psutil  # noqa: PLC0415
        procs: list[ProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = proc.info
                mem_mb = (info.get("memory_info") or proc.memory_info()).rss / 1024 ** 2
                procs.append(ProcessInfo(
                    pid=info["pid"],
                    name=info.get("name", ""),
                    cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                    memory_mb=round(mem_mb, 1),
                    status=info.get("status", ""),
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        key = "memory_mb" if sort_by == "memory" else "cpu_percent"
        procs.sort(key=lambda p: -getattr(p, key, 0))
        return procs[:top_n]

    def list_connections(self, *, kind: str = "inet") -> list[NetworkInfo]:
        """列出网络连接。"""
        if not self._psutil_available:
            return []

        import psutil  # noqa: PLC0415
        conns: list[NetworkInfo] = []
        for c in psutil.net_connections(kind=kind):
            local = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            remote = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            pname = ""
            if c.pid:
                try:
                    pname = psutil.Process(c.pid).name()
                except Exception:
                    pass
            conns.append(NetworkInfo(
                local_addr=local, remote_addr=remote,
                status=c.status, pid=c.pid, process_name=pname,
            ))
        return conns

    def ping(self, host: str, *, count: int = 4) -> dict[str, Any]:
        """Ping 测试。"""
        cmd = ["ping", "-n" if os.name == "nt" else "-c", str(count), host]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            return {
                "host": host,
                "success": result.returncode == 0,
                "output": result.stdout[:500],
            }
        except Exception as exc:
            return {"host": host, "success": False, "error": str(exc)[:200]}

    def kill_process(self, pid: int) -> bool:
        """终止进程。"""
        if not self._psutil_available:
            return False
        import psutil  # noqa: PLC0415
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            proc.wait(timeout=5)
            return True
        except Exception as exc:
            _logger.warning("终止进程 %d 失败: %s", pid, exc)
            return False


# ---------------------------------------------------------------------------
# ProcessGuard 进程守护
# ---------------------------------------------------------------------------
class ProcessGuard:
    """进程守护——崩溃自动重启。

    用法::

        guard = ProcessGuard()
        guard.add(GuardConfig(
            name="my_app",
            command="python app.py",
            cwd="D:\\\\myapp",
            max_restarts=5,
        ))
        guard.start_all()  # 启动所有守护
        guard.stop_all()   # 停止所有守护
    """

    def __init__(self) -> None:
        self._configs: dict[str, GuardConfig] = {}
        self._statuses: dict[str, GuardStatus] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._guards: dict[str, threading.Thread] = {}
        self._running = False

    def add(self, config: GuardConfig) -> None:
        """添加守护配置。"""
        self._configs[config.name] = config
        self._statuses[config.name] = GuardStatus(name=config.name)

    def start(self, name: str) -> bool:
        """启动单个守护。"""
        config = self._configs.get(name)
        if not config:
            return False

        self._running = True
        thread = threading.Thread(
            target=self._guard_loop,
            args=(config,),
            daemon=True,
            name=f"guard_{name}",
        )
        self._guards[name] = thread
        thread.start()
        _logger.info("ProcessGuard: 启动守护 %s", name)
        return True

    def start_all(self) -> None:
        """启动所有守护。"""
        for name in self._configs:
            self.start(name)

    def stop(self, name: str) -> None:
        """停止单个守护。"""
        proc = self._processes.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._statuses[name] = GuardStatus(name=name, running=False)

    def stop_all(self) -> None:
        """停止所有守护。"""
        self._running = False
        for name in list(self._processes):
            self.stop(name)

    def get_status(self, name: str) -> GuardStatus | None:
        """获取守护状态。"""
        return self._statuses.get(name)

    def get_all_status(self) -> list[dict[str, Any]]:
        """获取所有守护状态。"""
        return [
            {
                "name": s.name,
                "pid": s.pid,
                "running": s.running,
                "restart_count": s.restart_count,
            }
            for s in self._statuses.values()
        ]

    def _guard_loop(self, config: GuardConfig) -> None:
        """守护循环——监控进程，崩溃重启。"""
        status = self._statuses[config.name]

        while self._running and status.restart_count < config.max_restarts:
            # 启动进程
            try:
                proc = subprocess.Popen(
                    config.command,
                    shell=True,
                    cwd=config.cwd or None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self._processes[config.name] = proc
                status.pid = proc.pid
                status.running = True
                _logger.info(
                    "ProcessGuard: %s 已启动 (PID=%d, 重启次数=%d)",
                    config.name, proc.pid, status.restart_count,
                )

                # 等待进程结束
                proc.wait()
                status.running = False

                if not self._running:
                    break

                # 进程异常退出→重启
                status.restart_count += 1
                status.last_restart_ts = time.time()
                _logger.warning(
                    "ProcessGuard: %s 崩溃 (exit=%d)，%ds 后重启 (%d/%d)",
                    config.name, proc.returncode,
                    int(config.restart_delay_s),
                    status.restart_count, config.max_restarts,
                )
                time.sleep(config.restart_delay_s)

            except Exception as exc:
                status.error = str(exc)[:200]
                status.running = False
                _logger.error("ProcessGuard: %s 守护异常: %s", config.name, exc)
                time.sleep(config.restart_delay_s)

        if status.restart_count >= config.max_restarts:
            _logger.error(
                "ProcessGuard: %s 达到最大重启次数 (%d)，停止守护",
                config.name, config.max_restarts,
            )
