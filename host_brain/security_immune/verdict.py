from __future__ import annotations

import hashlib
from typing import Iterable

from host_brain.security_immune.models import AssetSnapshot, IncidentRecord, RiskLevel, SecuritySignal, Verdict, now_ms
from host_brain.security_immune.policy import SecurityImmunePolicy


def risk_from_score(score: int) -> RiskLevel:
    if score >= 95:
        return RiskLevel.CRITICAL
    if score >= 70:
        return RiskLevel.HIGH
    if score >= 45:
        return RiskLevel.MEDIUM
    if score >= 15:
        return RiskLevel.LOW
    return RiskLevel.NONE


def verdict_from_signals(signals: Iterable[SecuritySignal], risk: RiskLevel) -> Verdict:
    if risk == RiskLevel.CRITICAL:
        return Verdict.MALICIOUS
    signal_ids = {signal.signal_id for signal in signals}
    if {
        "powershell_downloadstring",
        "regsvr32_remote",
        "wmic_process_create",
    } & signal_ids and risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return Verdict.SUSPICIOUS
    if risk in {RiskLevel.HIGH, RiskLevel.MEDIUM}:
        return Verdict.SUSPICIOUS
    if risk == RiskLevel.LOW:
        return Verdict.UNKNOWN
    return Verdict.BENIGN


def _incident_id(asset_id: str, signals: list[SecuritySignal]) -> str:
    parts = [asset_id, *(signal.signal_id for signal in signals[:8])]
    digest = hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"sec-inc-{digest}"


class MalwareVerdictEngine:
    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def evaluate_asset(self, asset: AssetSnapshot) -> IncidentRecord | None:
        actionable = [signal for signal in asset.signals if int(signal.severity) >= 45]
        if not actionable:
            return None
        max_score = max(int(signal.severity) for signal in actionable)
        score = min(100, max_score + max(0, len(actionable) - 1) * 8)
        risk = risk_from_score(score)
        verdict = verdict_from_signals(actionable, risk)
        confidence = min(0.98, 0.45 + (score / 100.0) * 0.45 + min(0.08, len(actionable) * 0.02))
        titles = ", ".join(signal.title for signal in actionable[:3])
        summary = (
            f"{asset.hostname} produced {len(actionable)} actionable security signal(s): {titles}."
        )
        actions = [
            self.policy.build_action(
                risk=risk,
                action_id="collect_more_evidence",
                title="Collect more endpoint evidence",
                reason="Additional process, autorun, login, and network context is needed before destructive response.",
            ),
            self.policy.build_action(
                risk=risk,
                action_id="quick_scan",
                title="Run authorized quick malware scan",
                reason="A native endpoint scanner can validate the behavior signal without uploading samples.",
            ),
        ]
        signal_ids = {signal.signal_id for signal in actionable}
        categories = {signal.category for signal in actionable}
        if "defender" in categories:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="run_defender_quick_scan",
                    title="Run Microsoft Defender quick scan",
                    reason="Endpoint probe reported Defender-related risk; a local scan can validate without exporting samples.",
                )
            )
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="review_defender_threat_history",
                    title="Review Defender threat history",
                    reason="Correlate Defender detections with resources, action status, and scan timestamps before containment.",
                )
            )
        if "defender_threat_detection" in signal_ids:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="verify_defender_remediation",
                    title="Verify Defender remediation",
                    reason="Confirm the detection was quarantined or removed and no related process remains active.",
                )
            )
        if "defender_realtime_disabled" in signal_ids or "defender_antivirus_disabled" in signal_ids:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="enable_defender_protection",
                    title="Review and re-enable Defender protection",
                    reason="Realtime or antivirus protection is disabled; re-enable only after confirming there is no maintenance exception.",
                )
            )
        if "network" in categories:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="collect_network_evidence",
                    title="Collect network evidence",
                    reason="Network anomaly signals need DNS, firewall, and process ownership context before containment.",
                )
            )
        if "many_established_connections" in signal_ids:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="review_top_remote_ips",
                    title="Review top remote endpoints",
                    reason="Connection volume is high; review remote endpoints before blocking.",
                )
            )
        if "wazuh" in categories:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="review_wazuh_alert_context",
                    title="Review Wazuh alert context",
                    reason="Wazuh alerts should be correlated with agent timeline, rule groups, and source log before containment.",
                )
            )
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="pull_wazuh_agent_timeline",
                    title="Pull Wazuh agent timeline",
                    reason="Collect adjacent alerts from the same Wazuh agent to reduce false positives and reconstruct the attack path.",
                )
            )
        if "defender_signatures_stale" in signal_ids:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="update_defender_signatures",
                    title="Update Defender signatures",
                    reason="Stale signatures reduce malware detection quality.",
                )
            )
        if risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="isolate_file_copy",
                    title="Copy suspicious file to quarantine for analysis",
                    reason="Preserve evidence while keeping the original host state stable.",
                )
            )
        if risk == RiskLevel.CRITICAL:
            actions.append(
                self.policy.build_action(
                    risk=risk,
                    action_id="isolate_host",
                    title="Isolate host from internal network",
                    reason="Critical risk may indicate active compromise; host isolation requires explicit approval unless wartime rules are enabled.",
                )
            )
        ts = now_ms()
        return IncidentRecord(
            incident_id=_incident_id(asset.asset_id, actionable),
            asset_id=asset.asset_id,
            risk=risk,
            verdict=verdict,
            confidence=confidence,
            title=f"{risk.value.upper()} endpoint security signal on {asset.hostname}",
            summary=summary,
            signals=actionable,
            recommended_actions=actions,
            created_ts_ms=ts,
            updated_ts_ms=ts,
        )
