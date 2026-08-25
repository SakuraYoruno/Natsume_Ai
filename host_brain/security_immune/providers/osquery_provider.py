from __future__ import annotations

import json
import platform
import socket
import subprocess
from typing import Any

from host_brain.security_immune.local_sensor import _encoded_command_severity
from host_brain.security_immune.models import AssetSnapshot, SecuritySignal, now_ms
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import (
    ProviderResult,
    _stable_asset_id,
)


class OsqueryProvider:
    provider_id = "osquery"

    _SUSPICIOUS_PROCESS_QUERY = """
        SELECT pid, name, path, cmdline
        FROM processes
        WHERE lower(cmdline) LIKE '%-encodedcommand%'
           OR lower(cmdline) LIKE '% -enc %'
           OR lower(cmdline) LIKE '%downloadstring%'
           OR lower(cmdline) LIKE '%invoke-webrequest%'
           OR lower(cmdline) LIKE '%certutil%';
    """

    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def collect_assets(self) -> ProviderResult:
        if not self.policy.osquery_enabled:
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail="SECURITY_IMMUNE_OSQUERY_ENABLED=false",
            )
        try:
            rows = self._run_query(self._SUSPICIOUS_PROCESS_QUERY)
        except FileNotFoundError:
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail=f"osquery executable not found: {self.policy.osquery_path}",
            )
        except Exception as exc:
            return ProviderResult(
                provider_id=self.provider_id,
                status="degraded",
                detail=str(exc)[:300],
            )

        signals = self._signals_from_process_rows(rows)
        hostname = socket.gethostname() or "localhost"
        asset = AssetSnapshot(
            asset_id=_stable_asset_id(hostname),
            hostname=hostname,
            os_family=platform.system() or "unknown",
            ip_addresses=[],
            tags=["osquery", "authorized"],
            source="osquery_provider",
            observed_ts_ms=now_ms(),
            signals=signals,
            metadata={
                "provider": self.provider_id,
                "query_count": 1,
                "suspicious_process_row_count": len(rows),
                "osquery_path": self.policy.osquery_path,
            },
        )
        return ProviderResult(
            provider_id=self.provider_id,
            status="ok",
            assets=[asset],
            detail="osquery collection completed",
            metadata={"suspicious_process_row_count": len(rows)},
        )

    def _run_query(self, query: str) -> list[dict[str, Any]]:
        result = subprocess.run(  # noqa: S603
            [self.policy.osquery_path, "--json", query],
            capture_output=True,
            text=True,
            timeout=float(self.policy.osquery_timeout_seconds),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "osquery query failed")[:500])
        payload = result.stdout.strip() or "[]"
        parsed = json.loads(payload)
        if not isinstance(parsed, list):
            raise RuntimeError("osquery returned non-list JSON")
        return [dict(row) for row in parsed if isinstance(row, dict)]

    def _signals_from_process_rows(self, rows: list[dict[str, Any]]) -> list[SecuritySignal]:
        signals: list[SecuritySignal] = []
        for row in rows:
            cmdline = str(row.get("cmdline") or "")
            lower = cmdline.lower()
            evidence = {
                "provider": self.provider_id,
                "pid": row.get("pid"),
                "name": row.get("name"),
                "path": row.get("path"),
                "cmdline_preview": cmdline[:300],
            }
            if "-encodedcommand" in lower or " -enc " in lower:
                severity, extra = _encoded_command_severity(cmdline)
                signals.append(
                    SecuritySignal(
                        signal_id="osquery_encoded_powershell",
                        title="osquery observed encoded PowerShell",
                        severity=severity,
                        category="process",
                        detail="osquery process table reported encoded PowerShell command line.",
                        evidence={**evidence, **extra},
                    )
                )
                continue
            if "downloadstring" in lower or "invoke-webrequest" in lower:
                signals.append(
                    SecuritySignal(
                        signal_id="osquery_powershell_remote_loader",
                        title="osquery observed PowerShell remote loader pattern",
                        severity=78,
                        category="process",
                        detail="osquery process table reported remote loader-like PowerShell behavior.",
                        evidence=evidence,
                    )
                )
                continue
            if "certutil" in lower:
                signals.append(
                    SecuritySignal(
                        signal_id="osquery_certutil_activity",
                        title="osquery observed certutil activity",
                        severity=55,
                        category="process",
                        detail="certutil activity needs context; it can be admin tooling or downloader behavior.",
                        evidence=evidence,
                    )
                )
        return signals
