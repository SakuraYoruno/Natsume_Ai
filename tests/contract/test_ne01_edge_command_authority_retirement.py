from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUSHER = ROOT / "host_brain" / "tribe" / "edge_command_pusher.py"


def test_legacy_edge_command_transport_is_removed() -> None:
    assert not PUSHER.exists()


def test_current_edge_command_port_remains_governed() -> None:
    domain_ports = (
        ROOT
        / "host_brain"
        / "app"
        / "observability_runtime"
        / "domain_command_ports.py"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "host_brain" / "app" / "governed_operator_runtime.py"
    ).read_text(encoding="utf-8")
    routes = (
        ROOT / "host_brain" / "app" / "internal_tribe_routes.py"
    ).read_text(encoding="utf-8")

    assert '"edge.command.push": _edge_command_push' in domain_ports
    assert "execute_edge_command_push" in domain_ports
    assert "execute_edge_command_push" in runtime
    assert "/edge_command/status" not in routes
    assert "/edge_command/push" not in routes
