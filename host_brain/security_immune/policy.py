from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from host_brain.security_immune.models import ActionStatus, AgentMode, ResponseAction, RiskLevel


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _parse_mode(raw: str | None) -> AgentMode:
    text = str(raw or "").strip().lower()
    try:
        return AgentMode(text)
    except ValueError:
        return AgentMode.OBSERVE


def _default_auto_actions_by_risk() -> dict[RiskLevel, tuple[str, ...]]:
    return {
        RiskLevel.LOW: ("record",),
        RiskLevel.MEDIUM: (
            "collect_more_evidence",
            "quick_scan",
            "run_defender_quick_scan",
            "review_defender_threat_history",
            "update_defender_signatures",
        ),
        RiskLevel.HIGH: (
            "collect_more_evidence",
            "quick_scan",
            "run_defender_quick_scan",
            "review_defender_threat_history",
            "verify_defender_remediation",
            "update_defender_signatures",
            "isolate_file_copy",
        ),
        RiskLevel.CRITICAL: (
            "collect_more_evidence",
            "quick_scan",
            "run_defender_quick_scan",
            "review_defender_threat_history",
            "verify_defender_remediation",
            "update_defender_signatures",
            "isolate_file_copy",
            "block_confirmed_c2",
        ),
    }


@dataclass(frozen=True)
class SecurityImmunePolicy:
    enabled: bool = True
    mode: AgentMode = AgentMode.OBSERVE
    scan_interval_seconds: int = 300
    max_processes: int = 500
    state_dir: Path = Path("runtime/security_immune")
    provider_names: tuple[str, ...] = ("local_probe",)
    allow_local_defender_scan: bool = False
    osquery_enabled: bool = False
    osquery_path: str = "osqueryi"
    osquery_timeout_seconds: int = 10
    wazuh_enabled: bool = False
    wazuh_alerts_path: str = ""
    wazuh_max_alerts: int = 200
    wazuh_min_rule_level: int = 7
    defender_enabled: bool = False
    defender_scan_path: str = ""
    defender_max_detections: int = 20
    defender_detection_window_minutes: int = 10080
    defender_query_timeout_seconds: int = 15
    defender_scan_timeout_seconds: int = 120
    response_action_timeout_seconds: int = 120
    max_quarantine_file_bytes: int = 52_428_800
    auto_actions_by_risk: dict[RiskLevel, tuple[str, ...]] = field(default_factory=_default_auto_actions_by_risk)
    approval_required_actions: tuple[str, ...] = (
        "delete_file",
        "isolate_host",
        "disable_account",
        "firewall_policy_change",
        "upload_sample",
        "kill_critical_process",
    )

    @classmethod
    def from_env(cls) -> "SecurityImmunePolicy":
        state_raw = str(os.environ.get("SECURITY_IMMUNE_STATE_DIR") or "runtime/security_immune").strip()
        providers_raw = str(os.environ.get("SECURITY_IMMUNE_PROVIDERS") or "local_probe").strip()
        providers = tuple(item.strip() for item in providers_raw.split(",") if item.strip())
        return cls(
            enabled=_parse_bool_env("SECURITY_IMMUNE_ENABLED", default=True),
            mode=_parse_mode(os.environ.get("SECURITY_IMMUNE_MODE") or "observe"),
            scan_interval_seconds=_parse_int_env(
                "SECURITY_IMMUNE_SCAN_INTERVAL_SECONDS",
                default=300,
                minimum=30,
                maximum=86400,
            ),
            max_processes=_parse_int_env(
                "SECURITY_IMMUNE_MAX_PROCESSES",
                default=500,
                minimum=20,
                maximum=5000,
            ),
            state_dir=Path(state_raw),
            provider_names=providers or ("local_probe",),
            allow_local_defender_scan=_parse_bool_env(
                "SECURITY_IMMUNE_ALLOW_LOCAL_DEFENDER_SCAN",
                default=False,
            ),
            osquery_enabled=_parse_bool_env(
                "SECURITY_IMMUNE_OSQUERY_ENABLED",
                default=False,
            ),
            osquery_path=str(os.environ.get("SECURITY_IMMUNE_OSQUERY_PATH") or "osqueryi").strip() or "osqueryi",
            osquery_timeout_seconds=_parse_int_env(
                "SECURITY_IMMUNE_OSQUERY_TIMEOUT_SECONDS",
                default=10,
                minimum=1,
                maximum=120,
            ),
            wazuh_enabled=_parse_bool_env(
                "SECURITY_IMMUNE_WAZUH_ENABLED",
                default=False,
            ),
            wazuh_alerts_path=str(os.environ.get("SECURITY_IMMUNE_WAZUH_ALERTS_PATH") or "").strip(),
            wazuh_max_alerts=_parse_int_env(
                "SECURITY_IMMUNE_WAZUH_MAX_ALERTS",
                default=200,
                minimum=1,
                maximum=10000,
            ),
            wazuh_min_rule_level=_parse_int_env(
                "SECURITY_IMMUNE_WAZUH_MIN_RULE_LEVEL",
                default=7,
                minimum=0,
                maximum=15,
            ),
            defender_enabled=_parse_bool_env(
                "SECURITY_IMMUNE_DEFENDER_ENABLED",
                default=False,
            ),
            defender_scan_path=str(os.environ.get("SECURITY_IMMUNE_DEFENDER_SCAN_PATH") or "").strip(),
            defender_max_detections=_parse_int_env(
                "SECURITY_IMMUNE_DEFENDER_MAX_DETECTIONS",
                default=20,
                minimum=1,
                maximum=200,
            ),
            defender_detection_window_minutes=_parse_int_env(
                "SECURITY_IMMUNE_DEFENDER_DETECTION_WINDOW_MINUTES",
                default=10080,
                minimum=0,
                maximum=525600,
            ),
            defender_query_timeout_seconds=_parse_int_env(
                "SECURITY_IMMUNE_DEFENDER_QUERY_TIMEOUT_SECONDS",
                default=15,
                minimum=1,
                maximum=120,
            ),
            defender_scan_timeout_seconds=_parse_int_env(
                "SECURITY_IMMUNE_DEFENDER_SCAN_TIMEOUT_SECONDS",
                default=120,
                minimum=5,
                maximum=3600,
            ),
            response_action_timeout_seconds=_parse_int_env(
                "SECURITY_IMMUNE_RESPONSE_ACTION_TIMEOUT_SECONDS",
                default=120,
                minimum=1,
                maximum=3600,
            ),
            max_quarantine_file_bytes=_parse_int_env(
                "SECURITY_IMMUNE_MAX_QUARANTINE_FILE_BYTES",
                default=52_428_800,
                minimum=1024,
                maximum=1_073_741_824,
            ),
            auto_actions_by_risk=_default_auto_actions_by_risk(),
        )

    def requires_approval(self, action_id: str) -> bool:
        return str(action_id).strip().lower() in set(self.approval_required_actions)

    def action_status_for(self, *, risk: RiskLevel, action_id: str) -> ActionStatus:
        normalized = str(action_id or "").strip().lower()
        if self.requires_approval(normalized):
            return ActionStatus.APPROVAL_REQUIRED
        if self.mode in {AgentMode.OBSERVE, AgentMode.FORENSIC}:
            return ActionStatus.PLANNED
        if self.mode == AgentMode.HUNT and normalized in {
            "collect_more_evidence",
            "quick_scan",
            "record",
            "review_defender_threat_history",
            "verify_defender_remediation",
        }:
            return ActionStatus.AUTO_ALLOWED
        if self.mode in {AgentMode.CONTAIN, AgentMode.WARTIME}:
            allowed = set(self.auto_actions_by_risk.get(risk, ()))
            if normalized in allowed:
                return ActionStatus.AUTO_ALLOWED
        return ActionStatus.BLOCKED_BY_POLICY

    def build_action(self, *, risk: RiskLevel, action_id: str, title: str, reason: str) -> ResponseAction:
        return ResponseAction(
            action_id=action_id,
            title=title,
            status=self.action_status_for(risk=risk, action_id=action_id),
            reason=reason,
            reversible=action_id not in {"delete_file", "disable_account"},
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "scan_interval_seconds": self.scan_interval_seconds,
            "max_processes": self.max_processes,
            "state_dir": str(self.state_dir),
            "provider_names": list(self.provider_names),
            "allow_local_defender_scan": self.allow_local_defender_scan,
            "osquery_enabled": self.osquery_enabled,
            "osquery_path": self.osquery_path,
            "osquery_timeout_seconds": self.osquery_timeout_seconds,
            "wazuh_enabled": self.wazuh_enabled,
            "wazuh_alerts_path": self.wazuh_alerts_path,
            "wazuh_max_alerts": self.wazuh_max_alerts,
            "wazuh_min_rule_level": self.wazuh_min_rule_level,
            "defender_enabled": self.defender_enabled,
            "defender_scan_path": self.defender_scan_path,
            "defender_max_detections": self.defender_max_detections,
            "defender_detection_window_minutes": self.defender_detection_window_minutes,
            "defender_query_timeout_seconds": self.defender_query_timeout_seconds,
            "defender_scan_timeout_seconds": self.defender_scan_timeout_seconds,
            "response_action_timeout_seconds": self.response_action_timeout_seconds,
            "max_quarantine_file_bytes": self.max_quarantine_file_bytes,
            "approval_required_actions": list(self.approval_required_actions),
            "auto_actions_by_risk": {
                risk.value: list(actions) for risk, actions in self.auto_actions_by_risk.items()
            },
        }
