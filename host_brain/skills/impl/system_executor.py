"""系统感知 Executor。

将 system_registry.py 归一化层的 payload 路由到系统 API 执行。

支持的 action：
  - info：获取系统信息
  - process_list：列出进程
  - process_kill：终止进程
  - network_status：检测网络状态
  - open_app：打开本地应用
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
from typing import Any

_logger = logging.getLogger("Natsume.skills.system_executor")
_START_MENU_DIRS = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
)
_KNOWN_APP_ALIASES: dict[str, dict[str, Any]] = {
    "酷狗音乐": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
    "酷狗": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
    "kugou": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
}


def execute_system_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行系统感知操作。

    Args:
        args: 归一化 payload（来自 system_registry handler 的返回值）
        context: 执行上下文

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"system::{action}")

    try:
        result = _dispatch_system_action(action, payload, context)
        success = not (isinstance(result, dict) and result.get("success") is False)
        reason_code = str(result.get("reason_code") or "").strip() if isinstance(result, dict) else ""
        return {
            "status": "OK" if success else "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
            "reason_code": reason_code or None,
        }
    except Exception as exc:
        _logger.warning("System executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_system_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的系统操作。"""
    _action_map = {
        "info": _execute_system_info,
        "process_list": _execute_process_list,
        "process_kill": _execute_process_kill,
        "network_status": _execute_network_status,
        "open_app": _execute_open_app,
    }
    handler = _action_map.get(action)
    if handler is None:
        return {"note": f"unknown system action: {action}"}
    return handler(payload, context)


def _execute_system_info(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """获取系统信息。"""
    include_cpu = bool(payload.get("include_cpu", True))
    include_memory = bool(payload.get("include_memory", True))
    include_disk = bool(payload.get("include_disk", True))
    include_network = bool(payload.get("include_network", False))
    include_gpu = bool(payload.get("include_gpu", False))

    result: dict[str, Any] = {}
    try:
        import psutil
        import platform

        result["platform"] = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }
        if include_cpu:
            result["cpu"] = {
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
                "percent": psutil.cpu_percent(interval=0.1),
                "freq_mhz": getattr(psutil.cpu_freq(), "current", None),
            }
        if include_memory:
            mem = psutil.virtual_memory()
            result["memory"] = {
                "total_gb": round(mem.total / 1024 ** 3, 2),
                "available_gb": round(mem.available / 1024 ** 3, 2),
                "percent": mem.percent,
            }
        if include_disk:
            disk = psutil.disk_usage("/")
            result["disk"] = {
                "total_gb": round(disk.total / 1024 ** 3, 2),
                "free_gb": round(disk.free / 1024 ** 3, 2),
                "percent": disk.percent,
            }
        if include_network:
            net = psutil.net_if_stats()
            result["network_interfaces"] = {
                name: {"isup": stats.isup, "speed": stats.speed}
                for name, stats in net.items()
            }
    except ImportError:
        result["note"] = "psutil 未安装，系统信息不可用"
    except Exception as exc:
        result["error"] = str(exc)

    _logger.info("系统信息获取完成")
    return result


def _execute_process_list(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """列出运行中的进程。"""
    name_filter = payload.get("name_filter")
    pid_filter = payload.get("pid_filter")
    limit = int(payload.get("limit") or 50)
    sort_by = str(payload.get("sort_by") or "cpu")

    processes = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = proc.info
                if name_filter and name_filter.lower() not in (info.get("name") or "").lower():
                    continue
                if pid_filter is not None and info.get("pid") != pid_filter:
                    continue
                processes.append({
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                    "status": info.get("status"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 排序
        sort_key = {"cpu": "cpu_percent", "memory": "memory_percent", "pid": "pid", "name": "name"}.get(sort_by, "cpu_percent")
        processes.sort(key=lambda p: p.get(sort_key) or 0, reverse=(sort_by in {"cpu", "memory"}))
        processes = processes[:limit]
    except ImportError:
        return {"processes": [], "note": "psutil 未安装"}
    except Exception as exc:
        return {"processes": [], "error": str(exc)}

    _logger.info("进程列表获取: count=%d", len(processes))
    return {"processes": processes, "count": len(processes)}


def _execute_process_kill(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """终止进程。"""
    pid = payload.get("pid")
    name = payload.get("name")
    force = bool(payload.get("force", False))
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {"dry_run": True, "pid": pid, "name": name}

    killed = []
    try:
        import psutil
        import signal as _signal

        sig = _signal.SIGKILL if force else _signal.SIGTERM

        if pid is not None:
            try:
                proc = psutil.Process(pid)
                proc.send_signal(sig)
                killed.append({"pid": pid, "name": proc.name()})
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                return {"killed": [], "error": str(exc)}
        elif name:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if name.lower() in (proc.info.get("name") or "").lower():
                        proc.send_signal(sig)
                        killed.append({"pid": proc.pid, "name": proc.info.get("name")})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except ImportError:
        return {"killed": [], "note": "psutil 未安装"}
    except Exception as exc:
        return {"killed": killed, "error": str(exc)}

    _logger.info("进程终止: killed=%s", killed)
    return {"killed": killed, "count": len(killed)}


def _execute_network_status(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """检测网络状态。"""
    host = str(payload.get("host") or "8.8.8.8")
    port = payload.get("port")
    timeout_ms = int(payload.get("timeout_ms") or 3000)
    get_public_ip = bool(payload.get("get_public_ip", False))

    result: dict[str, Any] = {"host": host}
    timeout_s = timeout_ms / 1000.0

    # Ping 检测
    try:
        import socket
        sock = socket.create_connection(
            (host, port or 80),
            timeout=timeout_s,
        )
        sock.close()
        result["reachable"] = True
        result["port"] = port or 80
    except Exception as exc:
        result["reachable"] = False
        result["error"] = str(exc)

    # 获取公网 IP
    if get_public_ip:
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.ipify.org", timeout=timeout_s) as resp:
                result["public_ip"] = resp.read().decode("utf-8").strip()
        except Exception:
            result["public_ip"] = None

    _logger.info("网络状态检测: host=%s, reachable=%s", host, result.get("reachable"))
    return result


def _iter_open_app_terms(payload: dict[str, Any]) -> list[str]:
    raw_values = [
        payload.get("app_name"),
        payload.get("app_id"),
        payload.get("query"),
        payload.get("path"),
    ]
    terms: list[str] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        terms.append(value)
        normalized = value.lower()
        alias = _KNOWN_APP_ALIASES.get(value) or _KNOWN_APP_ALIASES.get(normalized)
        if alias:
            for term in alias.get("search_terms", ()):
                term_text = str(term or "").strip()
                if term_text:
                    terms.append(term_text)
        else:
            for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
                alias_key_text = str(alias_key or "").strip().lower()
                if alias_key_text and alias_key_text in normalized:
                    for term in alias_meta.get("search_terms", ()):
                        term_text = str(term or "").strip()
                        if term_text:
                            terms.append(term_text)
            cleaned = (
                value.replace("帮我", "")
                .replace("替我", "")
                .replace("本地的", "")
                .replace("本地", "")
                .replace("应用程序", "")
                .replace("应用", "")
                .replace("打开", "")
                .replace("启动", "")
                .replace("一下", "")
                .replace("吧", "")
                .replace("吗", "")
                .replace("？", "")
                .replace("。", "")
                .strip()
            )
            if cleaned and cleaned.lower() != normalized:
                terms.append(cleaned)
    unique: list[str] = []
    seen: set[str] = set()
    for item in terms:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _resolve_open_app_target(payload: dict[str, Any]) -> tuple[str, str]:
    explicit_path = str(payload.get("path") or "").strip()
    if explicit_path:
        path_obj = Path(explicit_path)
        if path_obj.exists():
            return ("path", str(path_obj))

    terms = _iter_open_app_terms(payload)
    lowered_terms = tuple(term.lower() for term in terms if term)

    for root in _START_MENU_DIRS:
        if not root.exists():
            continue
        try:
            for candidate in root.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in {".lnk", ".exe", ".url"}:
                    continue
                candidate_name = candidate.stem.lower()
                if any(term in candidate_name for term in lowered_terms):
                    return ("path", str(candidate))
        except Exception:
            continue

    app_id = str(payload.get("app_id") or "").strip()
    if not app_id:
        for term in terms:
            alias = _KNOWN_APP_ALIASES.get(term) or _KNOWN_APP_ALIASES.get(term.lower())
            if alias and str(alias.get("app_id") or "").strip():
                app_id = str(alias.get("app_id")).strip()
                break
    if app_id:
        return ("app_id", app_id)

    raise FileNotFoundError(f"unable to resolve local app target from terms={terms!r}")


def _resolve_open_app_display_name(payload: dict[str, Any], *, target_kind: str, target_value: str) -> str:
    explicit_name = str(payload.get("app_name") or "").strip()
    if explicit_name:
        cleaned_name = (
            explicit_name.replace("帮我", "")
            .replace("替我", "")
            .replace("本地的", "")
            .replace("本地", "")
            .replace("应用程序", "")
            .replace("应用", "")
            .replace("软件", "")
            .replace("程序", "")
            .replace("打开", "")
            .replace("启动", "")
            .replace("运行", "")
            .replace("一下", "")
            .replace("吧", "")
            .replace("吗", "")
            .replace("？", "")
            .replace("。", "")
            .strip()
        )
        return cleaned_name or explicit_name

    query = str(payload.get("query") or "").strip()
    lowered_query = query.lower()
    for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
        search_terms = tuple(str(item or "").strip().lower() for item in alias_meta.get("search_terms", ()))
        if any(term and term in lowered_query for term in search_terms):
            return str(alias_key).strip()

    if target_kind == "path":
        try:
            return Path(target_value).stem or target_value
        except Exception:
            return target_value

    if target_kind == "app_id":
        for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
            if str(alias_meta.get("app_id") or "").strip().lower() == str(target_value or "").strip().lower():
                return str(alias_key).strip()

    return target_value


def _launch_windows_shell_app_id(app_id: str) -> dict[str, Any]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f'Start-Process "shell:AppsFolder\\\\{app_id}"',
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "success": True,
        "launch_mode": "app_id",
        "target": app_id,
        "pid": int(getattr(proc, "pid", 0) or 0),
        "message": f"已为你打开 {app_id}。",
    }


def _launch_windows_path(target_path: str) -> dict[str, Any]:
    os.startfile(target_path)  # type: ignore[attr-defined]
    return {
        "success": True,
        "launch_mode": "path",
        "target": target_path,
        "message": f"已经替你打开了。",
    }


def _execute_open_app(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """打开本地应用，优先使用显式路径/开始菜单快捷方式，其次回退到 shell app id。"""
    _ = context
    try:
        target_kind, target_value = _resolve_open_app_target(payload)
        if target_kind == "path":
            result = _launch_windows_path(target_value)
        else:
            result = _launch_windows_shell_app_id(target_value)
        result["app_name"] = _resolve_open_app_display_name(payload, target_kind=target_kind, target_value=target_value)
        if not str(result.get("message") or "").strip():
            result["message"] = f"已经替你打开 {result['app_name']}。"
        _logger.info("打开本地应用: app=%s target=%s mode=%s", result["app_name"], target_value, result["launch_mode"])
        return result
    except Exception as exc:
        _logger.warning("打开本地应用失败: payload=%s error=%s", payload, exc)
        return {
            "success": False,
            "reason_code": "APP_LAUNCH_FAILED",
            "detail": str(exc),
            "app_name": str(payload.get("app_name") or ""),
            "message": "抱歉，我这边没能把这个应用打开。",
        }
