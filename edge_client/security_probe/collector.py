from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import platform
import re
import socket
import subprocess
import time
from typing import Any


_ENCODED_COMMAND_RE = re.compile(r"-(?:e|ec|en|enc|enco|encod|encode|encoded|encodedcommand)\s+([A-Za-z0-9+/=]+)", re.IGNORECASE)
_DANGEROUS_DECODED_MARKERS = (
    "downloadstring",
    "frombase64string",
    "invoke-expression",
    "iex ",
    "invoke-webrequest",
    "start-bitstransfer",
    "set-mppreference",
    "disablerealtimemonitoring",
    "\\currentversion\\run",
    "schtasks",
    "new-service",
    "mimikatz",
    "rundll32",
    "regsvr32",
    "wmic",
    "net user",
)
_TRUSTED_AUTOMATION_MARKERS = (
    "long-lived powershell ast parser",
    "command-safety layer on windows",
    "newline-delimited json requests over stdin",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def stable_asset_id(hostname: str) -> str:
    digest = hashlib.sha256(hostname.lower().encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"host-{digest}"


def _signal(
    signal_id: str,
    title: str,
    severity: int,
    category: str,
    *,
    detail: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "title": title,
        "severity": max(0, min(100, int(severity))),
        "category": category,
        "detail": detail,
        "evidence": dict(evidence or {}),
        "ts_ms": now_ms(),
    }


def decode_powershell_encoded_command(cmdline: str) -> str:
    match = _ENCODED_COMMAND_RE.search(cmdline or "")
    if not match:
        return ""
    encoded = match.group(1)
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=False)
    except Exception:
        return ""
    for encoding in ("utf-16le", "utf-8"):
        try:
            decoded = raw.decode(encoding, errors="replace")
        except Exception:
            continue
        cleaned = decoded.replace("\x00", "").strip()
        if cleaned:
            return cleaned
    return ""


def score_encoded_powershell(cmdline: str) -> tuple[int, dict[str, Any]]:
    # 解码仅用于只读风险分类，证据只保留有界预览；评分是提示信号而非恶意行为
    # 的裁决，受信任自动化标记也只降低优先级、不授予执行豁免。
    decoded = decode_powershell_encoded_command(cmdline)
    if not decoded:
        return 70, {"encoded_command_decoded": False}
    decoded_lower = decoded.lower()
    trusted = [marker for marker in _TRUSTED_AUTOMATION_MARKERS if marker in decoded_lower]
    if trusted:
        return 25, {
            "encoded_command_decoded": True,
            "decoded_preview": decoded[:300],
            "decoded_dangerous_markers": [],
            "trusted_automation_markers": trusted[:3],
        }
    dangerous = [marker for marker in _DANGEROUS_DECODED_MARKERS if marker in decoded_lower]
    if dangerous:
        return 80, {
            "encoded_command_decoded": True,
            "decoded_preview": decoded[:300],
            "decoded_dangerous_markers": dangerous[:5],
        }
    return 25, {
        "encoded_command_decoded": True,
        "decoded_preview": decoded[:300],
        "decoded_dangerous_markers": [],
    }


def collect_ip_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        value = socket.gethostbyname(socket.gethostname())
        if value and not value.startswith("127."):
            addresses.add(value)
    except Exception:
        pass
    try:
        import psutil  # type: ignore

        for iface_addrs in psutil.net_if_addrs().values():
            for item in iface_addrs:
                addr = str(getattr(item, "address", "") or "").strip()
                if addr and "." in addr and not addr.startswith("127."):
                    addresses.add(addr)
    except Exception:
        pass
    return sorted(addresses)


def collect_process_signals(*, max_processes: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # 探针受数量上限约束，避免端点进程表过大拖慢主机；截断作为显式信号上报，
    # 不能把有限观察误解为“未发现风险”。
    try:
        import psutil  # type: ignore
    except Exception:
        return [
            _signal(
                "sensor_psutil_unavailable",
                "Process visibility unavailable",
                20,
                "sensor",
                detail="psutil is unavailable; process collection was skipped.",
            )
        ], {"process_count_seen": 0, "process_scan_truncated": False}

    signals: list[dict[str, Any]] = []
    seen = 0
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "username"]):
        if seen >= max_processes:
            break
        seen += 1
        try:
            name = str(proc.info.get("name") or "")
            exe = str(proc.info.get("exe") or "")
            cmd_raw = proc.info.get("cmdline") or []
            cmdline = " ".join(str(item) for item in cmd_raw) if isinstance(cmd_raw, list) else str(cmd_raw)
        except Exception:
            continue
        lower = f"{name} {exe} {cmdline}".lower()
        if "-encodedcommand" in lower or " -enc " in lower:
            severity, evidence = score_encoded_powershell(cmdline)
            signals.append(
                _signal(
                    "encoded_powershell",
                    "PowerShell encoded command",
                    severity,
                    "process",
                    detail=f"PowerShell encoded command observed in {name or 'unknown process'}.",
                    evidence={
                        "pid": proc.info.get("pid"),
                        "name": name,
                        "exe": exe,
                        "cmdline_preview": cmdline[:300],
                        **evidence,
                    },
                )
            )
            continue
        if "downloadstring" in lower or "invoke-webrequest" in lower:
            signals.append(
                _signal(
                    "powershell_remote_loader",
                    "PowerShell remote loader pattern",
                    78,
                    "process",
                    detail=f"Remote loader-like PowerShell behavior observed in {name or 'unknown process'}.",
                    evidence={"pid": proc.info.get("pid"), "name": name, "cmdline_preview": cmdline[:300]},
                )
            )
        if "certutil" in lower and ("-urlcache" in lower or "http" in lower):
            signals.append(
                _signal(
                    "certutil_download",
                    "certutil downloader pattern",
                    72,
                    "process",
                    detail="certutil appears to be used for download-like behavior.",
                    evidence={"pid": proc.info.get("pid"), "name": name, "cmdline_preview": cmdline[:300]},
                )
            )
    truncated = seen >= max_processes
    if truncated:
        # 被截断时降低可见性而非静默完成，供 Host 的风险判断纳入这一盲区。
        signals.append(
            _signal(
                "process_scan_truncated",
                "Process scan truncated",
                15,
                "sensor",
                detail=f"Process scan stopped at configured limit {max_processes}.",
            )
        )
    return signals, {"process_count_seen": seen, "process_scan_truncated": truncated}


def collect_network_summary(*, max_connections: int = 300) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # 只汇总网络端点与计数，不采集流量内容；本模块是健康/风险观察而非抓包器。
    try:
        import psutil  # type: ignore
    except Exception:
        return [], {"network_available": False, "connection_count_seen": 0}
    remote_counts: dict[str, int] = {}
    established = 0
    external_established = 0
    seen = 0
    listening = 0
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:
        return [], {"network_available": False, "connection_count_seen": 0}
    for conn in connections:
        if seen >= max_connections:
            break
        seen += 1
        status = str(getattr(conn, "status", "") or "")
        if status.upper() == "LISTEN":
            listening += 1
            continue
        raddr = getattr(conn, "raddr", None)
        if not raddr:
            continue
        established += 1
        ip = str(getattr(raddr, "ip", "") or "")
        if ip:
            remote_counts[ip] = remote_counts.get(ip, 0) + 1
            try:
                parsed_ip = ipaddress.ip_address(ip)
                is_local = parsed_ip.is_loopback or parsed_ip.is_link_local
            except ValueError:
                is_local = False
            if not is_local:
                external_established += 1
    signals: list[dict[str, Any]] = []
    if external_established >= 50:
        signals.append(
            _signal(
                "many_established_connections",
                "Large number of established network connections",
                45,
                "network",
                detail="High non-loopback established connection count may indicate unusual network activity.",
                evidence={"established_count": established, "external_established_count": external_established},
            )
        )
    return signals, {
        "network_available": True,
        "connection_count_seen": seen,
        "established_count": established,
        "external_established_count": external_established,
        "listening_count": listening,
        "top_remote_ips": sorted(remote_counts.items(), key=lambda item: item[1], reverse=True)[:10],
        "network_scan_truncated": seen >= max_connections,
    }


def collect_windows_defender_signals(*, timeout_s: float = 5.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # 使用固定的只读 Defender 查询，不把任意检测文本交给 shell，且超时会降级为
    # “状态不可得”信号，而不会阻塞周期性探针。
    if platform.system().lower() != "windows":
        return [], {"defender_available": False, "reason": "non_windows"}
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-MpComputerStatus | ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception as exc:
        return [
            _signal(
                "defender_status_unavailable",
                "Windows Defender status unavailable",
                20,
                "defender",
                detail=str(exc)[:200],
            )
        ], {"defender_available": False, "error": str(exc)[:200]}
    if result.returncode != 0 or not result.stdout.strip():
        return [
            _signal(
                "defender_status_unavailable",
                "Windows Defender status unavailable",
                20,
                "defender",
                detail=(result.stderr or result.stdout or "empty Defender status")[:200],
            )
        ], {"defender_available": False, "returncode": result.returncode}
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [
            _signal(
                "defender_status_parse_failed",
                "Windows Defender status parse failed",
                20,
                "defender",
                detail=str(exc)[:200],
            )
        ], {"defender_available": False, "parse_error": str(exc)[:200]}
    signals: list[dict[str, Any]] = []
    antivirus_enabled = status.get("AntivirusEnabled")
    realtime_enabled = status.get("RealTimeProtectionEnabled")
    if antivirus_enabled is False:
        signals.append(
            _signal(
                "defender_antivirus_disabled",
                "Microsoft Defender antivirus disabled",
                85,
                "defender",
                detail="AntivirusEnabled is false.",
            )
        )
    if realtime_enabled is False:
        signals.append(
            _signal(
                "defender_realtime_disabled",
                "Microsoft Defender realtime protection disabled",
                75,
                "defender",
                detail="RealTimeProtectionEnabled is false.",
            )
        )
    try:
        age = int(status.get("AntivirusSignatureAge") or 0)
    except (TypeError, ValueError):
        age = 0
    if age >= 7:
        signals.append(
            _signal(
                "defender_signatures_stale",
                "Microsoft Defender signatures stale",
                50,
                "defender",
                detail=f"AntivirusSignatureAge={age} days.",
                evidence={"antivirus_signature_age_days": age},
            )
        )
    summary = {
        "defender_available": True,
        "antivirus_enabled": antivirus_enabled,
        "realtime_protection_enabled": realtime_enabled,
        "antivirus_signature_age": status.get("AntivirusSignatureAge"),
        "quick_scan_age": status.get("QuickScanAge"),
        "full_scan_age": status.get("FullScanAge"),
    }
    return signals, summary


def collect_asset_snapshot(
    *,
    max_processes: int = 500,
    max_connections: int = 300,
    include_defender: bool = True,
) -> dict[str, Any]:
    # 资产 ID 对主机名做截断摘要，稳定关联同一端点而不把原始身份当作跨系统主键；
    # 返回的是一次观测快照，Host 需按 observed_ts_ms 处理过期性。
    hostname = socket.gethostname() or "localhost"
    process_signals, process_meta = collect_process_signals(max_processes=max_processes)
    network_signals, network_meta = collect_network_summary(max_connections=max_connections)
    defender_signals: list[dict[str, Any]] = []
    defender_meta: dict[str, Any] = {"defender_available": False, "reason": "disabled_by_probe_config"}
    if include_defender:
        defender_signals, defender_meta = collect_windows_defender_signals()
    return {
        "asset_id": stable_asset_id(hostname),
        "hostname": hostname,
        "os_family": platform.system() or "unknown",
        "ip_addresses": collect_ip_addresses(),
        "tags": ["endpoint_probe", "authorized"],
        "source": "edge_security_probe",
        "observed_ts_ms": now_ms(),
        "signals": [*process_signals, *network_signals, *defender_signals],
        "metadata": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "probe": "edge_client.security_probe",
            "process": process_meta,
            "network": network_meta,
            "defender": defender_meta,
        },
    }
