from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from host_brain.security_immune.actions import SecurityResponseExecutor
from host_brain.security_immune.evidence import EvidenceStore
from host_brain.security_immune.local_sensor import LocalHostSensor
from host_brain.security_immune.models import (
    ActionExecutionResult,
    ActionStatus,
    AgentMode,
    AssetSnapshot,
    IncidentRecord,
    ResponseAction,
    RiskLevel,
    SecuritySignal,
    SecuritySnapshot,
    Verdict,
    now_ms,
)
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import SecuritySignalProvider
from host_brain.security_immune.providers.factory import build_provider_chain
from host_brain.security_immune.verdict import MalwareVerdictEngine

_logger = logging.getLogger("Natsume.security_immune")


class SecurityImmuneAgent:
    """Authorized-network defensive agent.

    The first implementation is intentionally conservative: observe, score,
    persist evidence, and prepare policy-gated response plans.
    """

    def __init__(
        self,
        *,
        policy: SecurityImmunePolicy | None = None,
        ledger_provider: Callable[[], Any] | None = None,
        sensor: LocalHostSensor | None = None,
        providers: list[SecuritySignalProvider] | None = None,
        evidence_store: EvidenceStore | None = None,
        action_executor: SecurityResponseExecutor | None = None,
    ) -> None:
        self.policy = policy or SecurityImmunePolicy.from_env()
        self.ledger_provider = ledger_provider
        self.providers = list(providers) if providers is not None else build_provider_chain(self.policy, sensor=sensor)
        self.sensor = sensor or LocalHostSensor(self.policy)
        self.verdict_engine = MalwareVerdictEngine(self.policy)
        self.evidence_store = evidence_store or EvidenceStore(self.policy.state_dir)
        self.action_executor = action_executor or SecurityResponseExecutor(self.policy)
        self._lock = threading.Lock()
        self._assets: dict[str, AssetSnapshot] = {}
        self._incidents: dict[str, IncidentRecord] = {}
        self._last_run_ts_ms: int | None = None
        self._run_count = 0
        self._last_error: str | None = None
        self._provider_statuses: list[dict[str, object]] = []
        self._restore_cached_state()

    def _restore_cached_state(self) -> None:
        raw = self.evidence_store.load_snapshot()
        if not raw:
            return
        # Keep persisted state visible after an obs-only process starts. The
        # runtime reconstructs dicts for display, and fresh scans will replace
        # them with typed records in the host process.
        for item in raw.get("assets") or []:
            if not isinstance(item, dict):
                continue
            signals = [
                SecuritySignal(
                    signal_id=str(signal.get("signal_id") or "unknown"),
                    title=str(signal.get("title") or "Unknown signal"),
                    severity=int(signal.get("severity") or 0),
                    category=str(signal.get("category") or "unknown"),
                    detail=str(signal.get("detail") or ""),
                    evidence=dict(signal.get("evidence") or {}),
                    ts_ms=int(signal.get("ts_ms") or now_ms()),
                )
                for signal in item.get("signals") or []
                if isinstance(signal, dict)
            ]
            asset = AssetSnapshot(
                asset_id=str(item.get("asset_id") or ""),
                hostname=str(item.get("hostname") or "unknown"),
                os_family=str(item.get("os_family") or "unknown"),
                ip_addresses=[str(ip) for ip in item.get("ip_addresses") or []],
                tags=[str(tag) for tag in item.get("tags") or []],
                source=str(item.get("source") or "persisted"),
                observed_ts_ms=int(item.get("observed_ts_ms") or now_ms()),
                signals=signals,
                metadata=dict(item.get("metadata") or {}),
            )
            if asset.asset_id:
                self._assets[asset.asset_id] = asset
        for item in raw.get("incidents") or []:
            if not isinstance(item, dict):
                continue
            signals = [
                SecuritySignal(
                    signal_id=str(signal.get("signal_id") or "unknown"),
                    title=str(signal.get("title") or "Unknown signal"),
                    severity=int(signal.get("severity") or 0),
                    category=str(signal.get("category") or "unknown"),
                    detail=str(signal.get("detail") or ""),
                    evidence=dict(signal.get("evidence") or {}),
                    ts_ms=int(signal.get("ts_ms") or now_ms()),
                )
                for signal in item.get("signals") or []
                if isinstance(signal, dict)
            ]
            actions = []
            for action in item.get("recommended_actions") or []:
                if not isinstance(action, dict):
                    continue
                try:
                    status = ActionStatus(str(action.get("status") or ActionStatus.PLANNED.value))
                except ValueError:
                    status = ActionStatus.PLANNED
                actions.append(
                    ResponseAction(
                        action_id=str(action.get("action_id") or "unknown"),
                        title=str(action.get("title") or "Recommended action"),
                        status=status,
                        reason=str(action.get("reason") or ""),
                        reversible=bool(action.get("reversible", True)),
                        params=dict(action.get("params") or {}),
                    )
                )
            action_results = []
            for result in item.get("action_results") or []:
                if not isinstance(result, dict):
                    continue
                try:
                    status = ActionStatus(str(result.get("status") or ActionStatus.SKIPPED.value))
                except ValueError:
                    status = ActionStatus.SKIPPED
                action_results.append(
                    ActionExecutionResult(
                        action_id=str(result.get("action_id") or "unknown"),
                        status=status,
                        detail=str(result.get("detail") or ""),
                        started_ts_ms=int(result.get("started_ts_ms") or now_ms()),
                        completed_ts_ms=int(result.get("completed_ts_ms") or now_ms()),
                        params=dict(result.get("params") or {}),
                    )
                )
            try:
                risk = RiskLevel(str(item.get("risk") or RiskLevel.NONE.value))
            except ValueError:
                risk = RiskLevel.NONE
            try:
                verdict = Verdict(str(item.get("verdict") or Verdict.UNKNOWN.value))
            except ValueError:
                verdict = Verdict.UNKNOWN
            incident = IncidentRecord(
                incident_id=str(item.get("incident_id") or ""),
                asset_id=str(item.get("asset_id") or ""),
                risk=risk,
                verdict=verdict,
                confidence=float(item.get("confidence") or 0.0),
                title=str(item.get("title") or "Persisted security incident"),
                summary=str(item.get("summary") or ""),
                signals=signals,
                recommended_actions=actions,
                action_results=action_results,
                created_ts_ms=int(item.get("created_ts_ms") or now_ms()),
                updated_ts_ms=int(item.get("updated_ts_ms") or now_ms()),
                status=str(item.get("status") or "open"),
            )
            if incident.incident_id:
                self._incidents[incident.incident_id] = incident
        self._last_run_ts_ms = raw.get("last_run_ts_ms")
        self._run_count = int(raw.get("run_count") or 0)
        self._last_error = raw.get("last_error")
        self._provider_statuses = [
            dict(item) for item in raw.get("provider_statuses") or [] if isinstance(item, dict)
        ]

    async def run_once(self, *, reason: str = "scheduled") -> dict[str, Any]:
        if not self.policy.enabled:
            snapshot = self.export_runtime_state()
            snapshot["detail"] = "SECURITY_IMMUNE_ENABLED=false"
            return snapshot
        try:
            provider_results = []
            assets: list[AssetSnapshot] = []
            for provider in self.providers:
                try:
                    result = provider.collect_assets()
                except Exception as provider_exc:
                    provider_id = str(getattr(provider, "provider_id", type(provider).__name__))
                    provider_results.append(
                        {
                            "provider_id": provider_id,
                            "status": "degraded",
                            "asset_count": 0,
                            "signal_count": 0,
                            "detail": str(provider_exc)[:300],
                            "metadata": {},
                            "ts_ms": now_ms(),
                        }
                    )
                    continue
                provider_results.append(result.as_dict())
                assets.extend(result.assets)
            assets = self._merge_assets(assets)

            incidents: list[IncidentRecord] = []
            for asset in assets:
                incident = self.verdict_engine.evaluate_asset(asset)
                if incident is not None:
                    incidents.append(incident)
            asset_by_id = {asset.asset_id: asset for asset in assets}
            incidents = [
                self.action_executor.execute_incident(incident, asset_by_id.get(incident.asset_id))
                for incident in incidents
            ]
            with self._lock:
                refreshed_asset_ids = {asset.asset_id for asset in assets}
                if refreshed_asset_ids:
                    self._incidents = {
                        incident_id: existing
                        for incident_id, existing in self._incidents.items()
                        if existing.asset_id not in refreshed_asset_ids
                    }
                for asset in assets:
                    self._assets[asset.asset_id] = asset
                for incident in incidents:
                    self._incidents[incident.incident_id] = incident
                self._last_run_ts_ms = now_ms()
                self._run_count += 1
                self._last_error = None
                self._provider_statuses = provider_results
                snapshot = self._snapshot_locked().as_dict()
            snapshot["provider_statuses"] = provider_results
            snapshot["reason"] = reason
            self.evidence_store.save_snapshot(snapshot)
            signal_count = sum(len(asset.signals) for asset in assets)
            action_result_count = sum(len(incident.action_results) for incident in incidents)
            executed_action_count = sum(
                1
                for incident in incidents
                for result in incident.action_results
                if result.status == ActionStatus.EXECUTED
            )
            self.evidence_store.append_event(
                {
                    "event_type": "SECURITY_IMMUNE_SCAN",
                    "reason": reason,
                    "asset_count": len(assets),
                    "signal_count": signal_count,
                    "incident_count": len(incidents),
                    "action_result_count": action_result_count,
                    "executed_action_count": executed_action_count,
                    "ts_ms": self._last_run_ts_ms,
                }
            )
            self._log_ledger_event(
                event_type="SECURITY_IMMUNE_SCAN",
                status="DEGRADED" if incidents else "OK",
                detail=f"reason={reason} assets={len(assets)} signals={signal_count} incidents={len(incidents)}",
                payload={
                    "asset_count": len(assets),
                    "signal_count": signal_count,
                    "incident_count": len(incidents),
                    "action_result_count": action_result_count,
                    "executed_action_count": executed_action_count,
                    "provider_statuses": provider_results,
                },
            )
            return snapshot
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                snapshot = self._snapshot_locked().as_dict()
                snapshot["provider_statuses"] = list(self._provider_statuses)
            snapshot["reason"] = reason
            self.evidence_store.save_snapshot(snapshot)
            _logger.warning("Security immune run_once degraded: %s", exc)
            self._log_ledger_event(
                event_type="SECURITY_IMMUNE_SCAN_FAILED",
                status="FAILED",
                detail=str(exc),
                payload={"reason": reason},
            )
            return snapshot

    def ingest_asset_snapshot(self, payload: dict[str, Any], *, source: str = "probe") -> dict[str, Any]:
        signals = [
            SecuritySignal(
                signal_id=str(item.get("signal_id") or "probe_signal"),
                title=str(item.get("title") or "Probe signal"),
                severity=int(item.get("severity") or 0),
                category=str(item.get("category") or "probe"),
                detail=str(item.get("detail") or ""),
                evidence=dict(item.get("evidence") or {}),
                ts_ms=int(item.get("ts_ms") or now_ms()),
            )
            for item in payload.get("signals") or []
            if isinstance(item, dict)
        ]
        asset = AssetSnapshot(
            asset_id=str(payload.get("asset_id") or payload.get("hostname") or "unknown"),
            hostname=str(payload.get("hostname") or payload.get("asset_id") or "unknown"),
            os_family=str(payload.get("os_family") or "unknown"),
            ip_addresses=[str(ip) for ip in payload.get("ip_addresses") or []],
            tags=[str(tag) for tag in payload.get("tags") or []],
            source=source,
            observed_ts_ms=int(payload.get("observed_ts_ms") or now_ms()),
            signals=signals,
            metadata=dict(payload.get("metadata") or {}),
        )
        incident = self.verdict_engine.evaluate_asset(asset)
        if incident is not None:
            incident = self.action_executor.execute_incident(incident, asset)
        with self._lock:
            self._assets[asset.asset_id] = asset
            self._incidents = {
                incident_id: existing
                for incident_id, existing in self._incidents.items()
                if existing.asset_id != asset.asset_id
            }
            if incident is not None:
                self._incidents[incident.incident_id] = incident
            self._last_run_ts_ms = now_ms()
            snapshot = self._snapshot_locked().as_dict()
        self.evidence_store.save_snapshot(snapshot)
        self.evidence_store.append_event(
            {
                "event_type": "SECURITY_IMMUNE_PROBE_INGEST",
                "asset_id": asset.asset_id,
                "signal_count": len(signals),
                "incident_id": incident.incident_id if incident else None,
                "action_result_count": len(incident.action_results) if incident else 0,
                "executed_action_count": (
                    sum(1 for result in incident.action_results if result.status == ActionStatus.EXECUTED)
                    if incident
                    else 0
                ),
                "ts_ms": now_ms(),
            }
        )
        self._log_ledger_event(
            event_type="SECURITY_IMMUNE_PROBE_INGEST",
            status="DEGRADED" if incident else "OK",
            detail=f"source={source} signals={len(signals)} incident={bool(incident)}",
            payload={
                "source": source,
                "asset_id": asset.asset_id,
                "hostname": asset.hostname,
                "signal_count": len(signals),
                "incident_id": incident.incident_id if incident else None,
                "risk": incident.risk.value if incident else "none",
                "action_result_count": len(incident.action_results) if incident else 0,
                "executed_action_count": (
                    sum(1 for result in incident.action_results if result.status == ActionStatus.EXECUTED)
                    if incident
                    else 0
                ),
            },
        )
        return snapshot

    def export_runtime_state(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot_locked().as_dict()
        snapshot["policy"] = self.policy.as_dict()
        snapshot["provider_statuses"] = list(self._provider_statuses)
        snapshot["recent_events"] = self.evidence_store.load_recent_events(limit=50)
        return snapshot

    def _snapshot_locked(self) -> SecuritySnapshot:
        return SecuritySnapshot(
            enabled=self.policy.enabled,
            mode=self.policy.mode if isinstance(self.policy.mode, AgentMode) else AgentMode.OBSERVE,
            assets=list(self._assets.values()),
            incidents=list(self._incidents.values()),
            last_run_ts_ms=self._last_run_ts_ms,
            run_count=self._run_count,
            last_error=self._last_error,
        )

    @staticmethod
    def _merge_assets(assets: list[AssetSnapshot]) -> list[AssetSnapshot]:
        merged: dict[str, AssetSnapshot] = {}
        for asset in assets:
            existing = merged.get(asset.asset_id)
            if existing is None:
                merged[asset.asset_id] = asset
                continue
            metadata = dict(existing.metadata)
            provider_meta = metadata.setdefault("provider_metadata", {})
            if isinstance(provider_meta, dict):
                provider_meta[str(existing.source)] = dict(existing.metadata)
                provider_meta[str(asset.source)] = dict(asset.metadata)
            else:
                metadata["provider_metadata"] = {
                    str(existing.source): dict(existing.metadata),
                    str(asset.source): dict(asset.metadata),
                }
            merged[asset.asset_id] = AssetSnapshot(
                asset_id=asset.asset_id,
                hostname=existing.hostname or asset.hostname,
                os_family=existing.os_family if existing.os_family != "unknown" else asset.os_family,
                ip_addresses=sorted({*existing.ip_addresses, *asset.ip_addresses}),
                tags=sorted({*existing.tags, *asset.tags}),
                source="provider_fusion",
                observed_ts_ms=max(existing.observed_ts_ms, asset.observed_ts_ms),
                signals=[*existing.signals, *asset.signals],
                metadata=metadata,
            )
        return list(merged.values())

    def _log_ledger_event(
        self,
        *,
        event_type: str,
        status: str,
        detail: str,
        payload: dict[str, Any],
    ) -> None:
        if self.ledger_provider is None:
            return
        try:
            ledger = self.ledger_provider()
        except Exception:
            return
        if ledger is None or not hasattr(ledger, "log_voice_event"):
            return
        try:
            ledger.log_voice_event(
                {
                    "request_id": "security_immune",
                    "session_id": "security_immune",
                    "event_type": event_type,
                    "direction": "INTERNAL",
                    "status": status,
                    "reason_code": None,
                    "stage": "security_immune.agent",
                    "detail": detail,
                    "payload": payload,
                    "ts_ms": now_ms(),
                }
            )
        except Exception:
            return
