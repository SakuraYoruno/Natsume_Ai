from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

from host_brain.security_immune.models import AssetSnapshot, SecuritySignal, now_ms
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import ProviderResult


def _stable_asset_id(value: str) -> str:
    digest = hashlib.sha256(value.lower().encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"wazuh-{digest}"


def _rule_level_to_severity(level: int) -> int:
    if level >= 14:
        return 100
    if level >= 12:
        return 90
    if level >= 10:
        return 75
    if level >= 7:
        return 55
    return max(0, min(100, level * 7))


class WazuhProvider:
    provider_id = "wazuh"

    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def collect_assets(self) -> ProviderResult:
        if not self.policy.wazuh_enabled:
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail="SECURITY_IMMUNE_WAZUH_ENABLED=false",
            )
        if not self.policy.wazuh_alerts_path:
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail="SECURITY_IMMUNE_WAZUH_ALERTS_PATH is empty",
            )
        path = Path(self.policy.wazuh_alerts_path)
        if not path.exists():
            return ProviderResult(
                provider_id=self.provider_id,
                status="skipped",
                detail=f"Wazuh alerts file not found: {path}",
            )
        try:
            alerts = self._load_recent_alerts(path)
        except Exception as exc:
            return ProviderResult(
                provider_id=self.provider_id,
                status="degraded",
                detail=str(exc)[:300],
            )
        assets = self._assets_from_alerts(alerts)
        signal_count = sum(len(asset.signals) for asset in assets)
        return ProviderResult(
            provider_id=self.provider_id,
            status="ok",
            assets=assets,
            detail="Wazuh alerts collection completed",
            metadata={
                "alerts_path": str(path),
                "alert_count": len(alerts),
                "signal_count": signal_count,
                "min_rule_level": self.policy.wazuh_min_rule_level,
            },
        )

    def _load_recent_alerts(self, path: Path) -> list[dict[str, Any]]:
        lines: deque[str] = deque(maxlen=max(1, int(self.policy.wazuh_max_alerts)))
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    lines.append(line)
        alerts: list[dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            rule = dict(payload.get("rule") or {})
            try:
                level = int(rule.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if level < int(self.policy.wazuh_min_rule_level):
                continue
            alerts.append(payload)
        return alerts

    def _assets_from_alerts(self, alerts: list[dict[str, Any]]) -> list[AssetSnapshot]:
        by_asset: dict[str, dict[str, Any]] = {}
        for alert in alerts:
            agent = dict(alert.get("agent") or {})
            manager = dict(alert.get("manager") or {})
            agent_id = str(agent.get("id") or "").strip()
            hostname = str(agent.get("name") or manager.get("name") or "wazuh-agent").strip()
            ip = str(agent.get("ip") or "").strip()
            asset_key = agent_id or hostname or ip or "wazuh-agent"
            asset_id = f"wazuh-agent-{agent_id}" if agent_id else _stable_asset_id(asset_key)
            item = by_asset.setdefault(
                asset_id,
                {
                    "hostname": hostname,
                    "ip_addresses": [],
                    "signals": [],
                    "alert_count": 0,
                },
            )
            if ip and ip.lower() not in {"any", "unknown"} and ip not in item["ip_addresses"]:
                item["ip_addresses"].append(ip)
            signal = self._signal_from_alert(alert)
            item["signals"].append(signal)
            item["alert_count"] += 1

        assets: list[AssetSnapshot] = []
        for asset_id, item in by_asset.items():
            assets.append(
                AssetSnapshot(
                    asset_id=asset_id,
                    hostname=str(item["hostname"] or asset_id),
                    os_family="unknown",
                    ip_addresses=list(item["ip_addresses"]),
                    tags=["wazuh", "authorized"],
                    source="wazuh_provider",
                    observed_ts_ms=now_ms(),
                    signals=list(item["signals"]),
                    metadata={
                        "provider": self.provider_id,
                        "wazuh_alert_count": int(item["alert_count"]),
                    },
                )
            )
        return assets

    def _signal_from_alert(self, alert: dict[str, Any]) -> SecuritySignal:
        rule = dict(alert.get("rule") or {})
        decoder = dict(alert.get("decoder") or {})
        try:
            level = int(rule.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        rule_id = str(rule.get("id") or "unknown").strip()
        description = str(rule.get("description") or "Wazuh alert").strip()
        groups = [str(group) for group in (rule.get("groups") or []) if group is not None]
        return SecuritySignal(
            signal_id=f"wazuh_rule_{rule_id}",
            title=description,
            severity=_rule_level_to_severity(level),
            category="wazuh",
            detail=f"Wazuh rule level={level} groups={','.join(groups[:6])}",
            evidence={
                "provider": self.provider_id,
                "alert_id": alert.get("id"),
                "timestamp": alert.get("timestamp"),
                "rule_id": rule_id,
                "rule_level": level,
                "rule_groups": groups,
                "decoder_name": decoder.get("name"),
                "location": alert.get("location"),
                "full_log_preview": str(alert.get("full_log") or "")[:500],
            },
        )
