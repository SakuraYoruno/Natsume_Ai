from __future__ import annotations

import json
import platform
import re
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from host_brain.security_immune.models import AssetSnapshot, SecuritySignal, now_ms
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import (
    ProviderResult,
    _stable_asset_id,
)


class DefenderProvider:
    provider_id = "defender"

    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def collect_assets(self) -> ProviderResult:
        if not self.policy.defender_enabled:
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail="SECURITY_IMMUNE_DEFENDER_ENABLED=false",
            )
        if platform.system().lower() != "windows":
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail="Microsoft Defender provider is Windows-only",
            )
        scan_detail = None
        if self.policy.defender_scan_path:
            scan_detail = self._run_custom_scan(Path(self.policy.defender_scan_path))
        try:
            status = self._run_powershell_json("Get-MpComputerStatus | ConvertTo-Json -Compress")
            detections = self._run_powershell_json(
                f"Get-MpThreatDetection | Select-Object -Last {int(self.policy.defender_max_detections)} | ConvertTo-Json -Compress"
            )
        except Exception as exc:
            return ProviderResult(
                provider_id=self.provider_id,
                status="degraded",
                detail=str(exc)[:300],
                metadata={"scan_detail": scan_detail},
            )
        signals = self._signals_from_status(status)
        signals.extend(self._signals_from_detections(detections))
        hostname = socket.gethostname() or "localhost"
        asset = AssetSnapshot(
            asset_id=_stable_asset_id(hostname),
            hostname=hostname,
            os_family="Windows",
            ip_addresses=[],
            tags=["defender", "authorized"],
            source="defender_provider",
            observed_ts_ms=now_ms(),
            signals=signals,
            metadata={
                "provider": self.provider_id,
                "status": self._status_summary(status),
                "detection_count": len(self._as_list(detections)),
                "scan_detail": scan_detail,
            },
        )
        return ProviderResult(
            provider_id=self.provider_id,
            status="ok",
            assets=[asset],
            detail="Microsoft Defender collection completed",
            metadata={
                "signal_count": len(signals),
                "detection_count": len(self._as_list(detections)),
                "scan_detail": scan_detail,
            },
        )

    def _run_custom_scan(self, path: Path) -> dict[str, Any]:
        if not self.policy.allow_local_defender_scan:
            return {"status": "skipped", "detail": "SECURITY_IMMUNE_ALLOW_LOCAL_DEFENDER_SCAN=false"}
        if not path.exists():
            return {"status": "skipped", "detail": f"scan path not found: {path}"}
        command = (
            "Start-MpScan "
            "-ScanType CustomScan "
            f"-ScanPath {json.dumps(str(path))} "
            "-ErrorAction Stop; "
            "Write-Output '{\"status\":\"started\"}'"
        )
        try:
            result = subprocess.run(  # noqa: S603
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=float(self.policy.defender_scan_timeout_seconds),
                check=False,
            )
        except Exception as exc:
            return {"status": "failed", "detail": str(exc)[:300]}
        if result.returncode != 0:
            return {"status": "failed", "detail": (result.stderr or result.stdout or "scan failed")[:300]}
        return {"status": "ok", "scan_path": str(path)}

    def _run_powershell_json(self, command: str) -> Any:
        result = subprocess.run(  # noqa: S603
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=float(self.policy.defender_query_timeout_seconds),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Defender PowerShell query failed")[:500])
        text = result.stdout.strip()
        if not text:
            return []
        return json.loads(text)

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(value)]
        return []

    def _signals_from_status(self, status: Any) -> list[SecuritySignal]:
        if not isinstance(status, dict):
            return [
                SecuritySignal(
                    signal_id="defender_status_unavailable",
                    title="Microsoft Defender status unavailable",
                    severity=20,
                    category="defender",
                    detail="Get-MpComputerStatus did not return an object.",
                    evidence={"provider": self.provider_id},
                )
            ]
        signals: list[SecuritySignal] = []
        if status.get("AntivirusEnabled") is False:
            signals.append(
                SecuritySignal(
                    signal_id="defender_antivirus_disabled",
                    title="Microsoft Defender antivirus disabled",
                    severity=85,
                    category="defender",
                    detail="AntivirusEnabled is false.",
                    evidence={"provider": self.provider_id},
                )
            )
        if status.get("RealTimeProtectionEnabled") is False:
            signals.append(
                SecuritySignal(
                    signal_id="defender_realtime_disabled",
                    title="Microsoft Defender realtime protection disabled",
                    severity=75,
                    category="defender",
                    detail="RealTimeProtectionEnabled is false.",
                    evidence={"provider": self.provider_id},
                )
            )
        try:
            age = int(status.get("AntivirusSignatureAge") or 0)
        except (TypeError, ValueError):
            age = 0
        if age >= 7:
            signals.append(
                SecuritySignal(
                    signal_id="defender_signatures_stale",
                    title="Microsoft Defender signatures stale",
                    severity=50,
                    category="defender",
                    detail=f"AntivirusSignatureAge={age} days.",
                    evidence={"provider": self.provider_id, "antivirus_signature_age_days": age},
                )
            )
        return signals

    def _signals_from_detections(self, detections: Any) -> list[SecuritySignal]:
        signals: list[SecuritySignal] = []
        for item in self._as_list(detections):
            if not self._is_detection_in_window(item):
                continue
            threat_name = str(item.get("ThreatName") or item.get("ThreatID") or "Defender threat").strip()
            severity, detail = self._detection_severity_and_detail(item)
            resource_states = self._resource_states(item.get("Resources"))
            signals.append(
                SecuritySignal(
                    signal_id="defender_threat_detection",
                    title=f"Microsoft Defender detected {threat_name}",
                    severity=severity,
                    category="defender",
                    detail=detail,
                    evidence={
                        "provider": self.provider_id,
                        "detection_id": item.get("DetectionID"),
                        "threat_id": item.get("ThreatID"),
                        "threat_name": threat_name,
                        "resources": item.get("Resources"),
                        "resource_states": resource_states,
                        "process_name": item.get("ProcessName"),
                        "initial_detection_time": item.get("InitialDetectionTime"),
                        "last_threat_status_change_time": item.get("LastThreatStatusChangeTime"),
                        "remediation_time": item.get("RemediationTime"),
                        "action_success": item.get("ActionSuccess"),
                        "threat_status_id": item.get("ThreatStatusID"),
                        "threat_status_error_code": item.get("ThreatStatusErrorCode"),
                        "cleaning_action_id": item.get("CleaningActionID"),
                    },
                )
            )
        return signals

    def _detection_severity_and_detail(self, item: dict[str, Any]) -> tuple[int, str]:
        status_id = str(item.get("ThreatStatusID") or "").strip()
        resource_states = self._resource_states(item.get("Resources"))
        resource_still_present = any(state.get("exists") is True for state in resource_states)
        if status_id in {"1", "2"} or resource_still_present:
            return 90, "Microsoft Defender reported an active or pending threat detection."
        if item.get("ActionSuccess") is True and item.get("RemediationTime"):
            return 35, "Microsoft Defender reported a remediated threat detection retained in history."
        if item.get("ActionSuccess") is True:
            return 55, "Microsoft Defender reported a detection with a successful response but no remediation timestamp."
        return 90, "Microsoft Defender reported a detection that still needs remediation review."

    def _is_detection_in_window(self, item: dict[str, Any]) -> bool:
        window_minutes = int(self.policy.defender_detection_window_minutes or 0)
        if window_minutes <= 0:
            return True
        observed_times = [
            self._parse_detection_time_ms(item.get("LastThreatStatusChangeTime")),
            self._parse_detection_time_ms(item.get("InitialDetectionTime")),
            self._parse_detection_time_ms(item.get("DetectionTime")),
        ]
        latest_ms = max((value for value in observed_times if value is not None), default=None)
        if latest_ms is None:
            return True
        return latest_ms >= now_ms() - (window_minutes * 60 * 1000)

    @staticmethod
    def _parse_detection_time_ms(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            raw_number = float(value)
            if raw_number <= 0:
                return None
            return int(raw_number if raw_number > 10_000_000_000 else raw_number * 1000)
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"/Date\((\d+)", text)
        if match:
            return int(match.group(1))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return int(parsed.timestamp() * 1000)

    def _resource_states(self, resources: Any) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for resource in self._as_resource_list(resources):
            path = self._local_path_from_resource(resource)
            state: dict[str, Any] = {"resource": resource}
            if path is not None:
                state["path"] = str(path)
                try:
                    state["exists"] = path.exists()
                except OSError:
                    state["exists"] = None
            states.append(state)
        return states

    @staticmethod
    def _as_resource_list(resources: Any) -> list[str]:
        if resources is None:
            return []
        if isinstance(resources, list):
            return [str(item) for item in resources]
        return [str(resources)]

    @staticmethod
    def _local_path_from_resource(resource: str) -> Path | None:
        text = str(resource or "").strip()
        lowered = text.lower()
        if lowered.startswith("file:_"):
            return Path(text[6:])
        if lowered.startswith("file:"):
            return Path(text[5:])
        return None

    @staticmethod
    def _status_summary(status: Any) -> dict[str, Any]:
        if not isinstance(status, dict):
            return {"available": False}
        return {
            "available": True,
            "antivirus_enabled": status.get("AntivirusEnabled"),
            "realtime_protection_enabled": status.get("RealTimeProtectionEnabled"),
            "antivirus_signature_age": status.get("AntivirusSignatureAge"),
            "quick_scan_age": status.get("QuickScanAge"),
            "full_scan_age": status.get("FullScanAge"),
        }
