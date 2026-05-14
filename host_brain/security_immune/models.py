from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Verdict(str, Enum):
    BENIGN = "benign"
    UNKNOWN = "unknown"
    SUSPICIOUS = "suspicious"
    PUA = "pua"
    MALICIOUS = "malicious"


class AgentMode(str, Enum):
    OBSERVE = "observe"
    HUNT = "hunt"
    CONTAIN = "contain"
    WARTIME = "wartime"
    FORENSIC = "forensic"


class ActionStatus(str, Enum):
    PLANNED = "planned"
    AUTO_ALLOWED = "auto_allowed"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    EXECUTED = "executed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SecuritySignal:
    signal_id: str
    title: str
    severity: int
    category: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=now_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "title": self.title,
            "severity": max(0, min(100, int(self.severity))),
            "category": self.category,
            "detail": self.detail,
            "evidence": dict(self.evidence),
            "ts_ms": int(self.ts_ms),
        }


@dataclass(frozen=True)
class AssetSnapshot:
    asset_id: str
    hostname: str
    os_family: str
    ip_addresses: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "local_sensor"
    observed_ts_ms: int = field(default_factory=now_ms)
    signals: list[SecuritySignal] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "hostname": self.hostname,
            "os_family": self.os_family,
            "ip_addresses": list(self.ip_addresses),
            "tags": list(self.tags),
            "source": self.source,
            "observed_ts_ms": int(self.observed_ts_ms),
            "signals": [signal.as_dict() for signal in self.signals],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResponseAction:
    action_id: str
    title: str
    status: ActionStatus
    reason: str
    reversible: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "title": self.title,
            "status": self.status.value,
            "reason": self.reason,
            "reversible": bool(self.reversible),
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class ActionExecutionResult:
    action_id: str
    status: ActionStatus
    detail: str
    started_ts_ms: int = field(default_factory=now_ms)
    completed_ts_ms: int = field(default_factory=now_ms)
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status.value,
            "detail": self.detail,
            "started_ts_ms": int(self.started_ts_ms),
            "completed_ts_ms": int(self.completed_ts_ms),
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    asset_id: str
    risk: RiskLevel
    verdict: Verdict
    confidence: float
    title: str
    summary: str
    signals: list[SecuritySignal] = field(default_factory=list)
    recommended_actions: list[ResponseAction] = field(default_factory=list)
    action_results: list[ActionExecutionResult] = field(default_factory=list)
    created_ts_ms: int = field(default_factory=now_ms)
    updated_ts_ms: int = field(default_factory=now_ms)
    status: str = "open"

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "asset_id": self.asset_id,
            "risk": self.risk.value,
            "verdict": self.verdict.value,
            "confidence": round(float(self.confidence), 4),
            "title": self.title,
            "summary": self.summary,
            "signals": [signal.as_dict() for signal in self.signals],
            "recommended_actions": [action.as_dict() for action in self.recommended_actions],
            "action_results": [result.as_dict() for result in self.action_results],
            "created_ts_ms": int(self.created_ts_ms),
            "updated_ts_ms": int(self.updated_ts_ms),
            "status": self.status,
        }


@dataclass(frozen=True)
class SecuritySnapshot:
    enabled: bool
    mode: AgentMode
    assets: list[AssetSnapshot]
    incidents: list[IncidentRecord]
    last_run_ts_ms: int | None
    run_count: int
    last_error: str | None = None
    generated_ts_ms: int = field(default_factory=now_ms)

    def as_dict(self) -> dict[str, Any]:
        risk_counts: dict[str, int] = {risk.value: 0 for risk in RiskLevel}
        for incident in self.incidents:
            risk_counts[incident.risk.value] = risk_counts.get(incident.risk.value, 0) + 1
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode.value,
            "asset_count": len(self.assets),
            "incident_count": len(self.incidents),
            "risk_counts": risk_counts,
            "assets": [asset.as_dict() for asset in self.assets],
            "incidents": [incident.as_dict() for incident in self.incidents],
            "last_run_ts_ms": self.last_run_ts_ms,
            "run_count": int(self.run_count),
            "last_error": self.last_error,
            "generated_ts_ms": int(self.generated_ts_ms),
        }
