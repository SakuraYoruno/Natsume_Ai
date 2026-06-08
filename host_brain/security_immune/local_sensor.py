from __future__ import annotations

import base64
import hashlib
import platform
import re
import socket
from typing import Any

from host_brain.security_immune.models import AssetSnapshot, SecuritySignal, now_ms
from host_brain.security_immune.policy import SecurityImmunePolicy


_SUSPICIOUS_COMMAND_PATTERNS: tuple[tuple[str, str, int, str], ...] = (
    ("encoded_powershell", "PowerShell encoded command", 70, "-encodedcommand"),
    ("encoded_powershell_short", "PowerShell encoded command", 70, " -enc "),
    ("powershell_downloadstring", "PowerShell remote loader pattern", 80, "downloadstring"),
    ("powershell_iex", "PowerShell inline execution pattern", 65, "iex "),
    ("frombase64string", "Base64 payload decoding", 65, "frombase64string"),
    ("certutil_download", "certutil used as downloader", 72, "certutil"),
    ("bitsadmin_transfer", "bitsadmin transfer command", 70, "bitsadmin"),
    ("regsvr32_remote", "regsvr32 remote script execution", 78, "regsvr32"),
    ("wmic_process_create", "WMIC remote process creation pattern", 74, "process call create"),
    ("rundll32_temp", "rundll32 launched from temporary location", 72, "rundll32"),
)

_TEMP_PATH_MARKERS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "/tmp/", "/var/tmp/")
_ENCODED_COMMAND_RE = re.compile(r"-(?:e|ec|en|enc|enco|encod|encode|encoded|encodedcommand)\s+([A-Za-z0-9+/=]+)", re.IGNORECASE)
_DANGEROUS_DECODED_MARKERS = (
    "downloadstring",
    "frombase64string",
    "invoke-expression",
    "iex ",
    "invoke-webrequest",
    "start-bitstransfer",
    "set-mppreference",
    "add-mppreference",
    "disablerealtimemonitoring",
    "reg add",
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


def _stable_asset_id(hostname: str) -> str:
    digest = hashlib.sha256(hostname.lower().encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"host-{digest}"


def _safe_cmdline(proc: Any) -> str:
    try:
        value = proc.info.get("cmdline") if hasattr(proc, "info") else None
        if isinstance(value, list):
            return " ".join(str(item) for item in value if item is not None)
        if value:
            return str(value)
    except Exception:
        pass
    return ""


def _safe_name(proc: Any) -> str:
    try:
        value = proc.info.get("name") if hasattr(proc, "info") else None
        if value:
            return str(value)
    except Exception:
        pass
    return ""


def _decode_powershell_encoded_command(cmdline: str) -> str:
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


def _encoded_command_severity(cmdline: str) -> tuple[int, dict[str, Any]]:
    decoded = _decode_powershell_encoded_command(cmdline)
    if not decoded:
        return 70, {"encoded_command_decoded": False}
    decoded_lower = decoded.lower()
    trusted_markers = [marker for marker in _TRUSTED_AUTOMATION_MARKERS if marker in decoded_lower]
    if trusted_markers:
        return 25, {
            "encoded_command_decoded": True,
            "decoded_preview": decoded[:300],
            "decoded_dangerous_markers": [],
            "trusted_automation_markers": trusted_markers[:3],
        }
    dangerous = [marker for marker in _DANGEROUS_DECODED_MARKERS if marker in decoded_lower]
    if dangerous:
        return 80, {
            "encoded_command_decoded": True,
            "decoded_preview": decoded[:300],
            "decoded_dangerous_markers": dangerous[:5],
        }
    # Encoded PowerShell is common in automation wrappers; without dangerous
    # decoded behavior it should be observed but not promoted to an incident.
    return 25, {
        "encoded_command_decoded": True,
        "decoded_preview": decoded[:300],
        "decoded_dangerous_markers": [],
    }


class LocalHostSensor:
    """Observe-only host sensor for the first security immune MVP."""

    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def collect(self) -> AssetSnapshot:
        hostname = socket.gethostname() or "localhost"
        signals = self._collect_process_signals()
        metadata = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "sensor": "local_host",
        }
        return AssetSnapshot(
            asset_id=_stable_asset_id(hostname),
            hostname=hostname,
            os_family=platform.system() or "unknown",
            ip_addresses=self._collect_ip_addresses(),
            tags=["local", "authorized"],
            source="local_host_sensor",
            observed_ts_ms=now_ms(),
            signals=signals,
            metadata=metadata,
        )

    def _collect_ip_addresses(self) -> list[str]:
        addresses: set[str] = set()
        try:
            addresses.add(socket.gethostbyname(socket.gethostname()))
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

    def _collect_process_signals(self) -> list[SecuritySignal]:
        try:
            import psutil  # type: ignore
        except Exception:
            return [
                SecuritySignal(
                    signal_id="sensor_psutil_unavailable",
                    title="Local process visibility unavailable",
                    severity=20,
                    category="sensor",
                    detail="psutil is unavailable; local process hunt was skipped.",
                )
            ]

        signals: list[SecuritySignal] = []
        seen = 0
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "username"]):
            if seen >= self.policy.max_processes:
                break
            seen += 1
            cmdline = _safe_cmdline(proc)
            name = _safe_name(proc)
            text = f"{name} {cmdline}".lower()
            exe = ""
            try:
                exe = str(proc.info.get("exe") or "")
            except Exception:
                exe = ""
            for signal_id, title, severity, needle in _SUSPICIOUS_COMMAND_PATTERNS:
                if needle not in text:
                    continue
                extra_evidence: dict[str, Any] = {}
                if signal_id in {"encoded_powershell", "encoded_powershell_short"}:
                    severity, extra_evidence = _encoded_command_severity(cmdline)
                if signal_id == "certutil_download" and "-urlcache" not in text and "http" not in text:
                    continue
                if signal_id == "regsvr32_remote" and "http" not in text and "scrobj" not in text:
                    continue
                if signal_id == "rundll32_temp":
                    lower_exe = exe.lower()
                    if not any(marker in lower_exe or marker in text for marker in _TEMP_PATH_MARKERS):
                        continue
                signals.append(
                    SecuritySignal(
                        signal_id=signal_id,
                        title=title,
                        severity=severity,
                        category="process",
                        detail=f"Suspicious process command line pattern detected in {name or 'unknown process'}.",
                        evidence={
                            "pid": proc.info.get("pid"),
                            "name": name,
                            "exe": exe,
                            "cmdline_preview": cmdline[:300],
                            **extra_evidence,
                        },
                    )
                )
                break
        if seen >= self.policy.max_processes:
            signals.append(
                SecuritySignal(
                    signal_id="process_scan_truncated",
                    title="Process scan truncated",
                    severity=15,
                    category="sensor",
                    detail=f"Process enumeration stopped at configured limit {self.policy.max_processes}.",
                )
            )
        return signals
