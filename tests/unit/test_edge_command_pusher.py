from __future__ import annotations

from host_brain.tribe.edge_command_pusher import EdgeCommandPusher


def test_edge_command_pusher_from_env_reuses_host_edge_rpc_base_url(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_COMMAND_BASE_URL", raising=False)
    monkeypatch.setenv("HOST_EDGE_RPC_BASE_URL", "https://edge.remote:17800/v1/edge/health")
    monkeypatch.setenv("EDGE_RPC_HOST", "192.168.1.101")
    monkeypatch.setenv("EDGE_RPC_PORT", "17800")
    monkeypatch.delenv("EDGE_COMMAND_PORT", raising=False)

    pusher = EdgeCommandPusher.from_env()

    assert pusher.edge_base_url == "https://edge.remote:17800"


def test_edge_command_pusher_from_env_falls_back_to_edge_rpc_port(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_COMMAND_BASE_URL", raising=False)
    monkeypatch.delenv("HOST_EDGE_RPC_BASE_URL", raising=False)
    monkeypatch.delenv("EDGE_RPC_BASE_URL", raising=False)
    monkeypatch.delenv("EDGE_COMMAND_PORT", raising=False)
    monkeypatch.setenv("EDGE_RPC_HOST", "192.168.1.101")
    monkeypatch.setenv("EDGE_RPC_PORT", "17800")

    pusher = EdgeCommandPusher.from_env()

    assert pusher.edge_base_url == "http://192.168.1.101:17800"


def test_edge_command_pusher_from_env_keeps_command_port_override(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_COMMAND_BASE_URL", raising=False)
    monkeypatch.delenv("HOST_EDGE_RPC_BASE_URL", raising=False)
    monkeypatch.delenv("EDGE_RPC_BASE_URL", raising=False)
    monkeypatch.setenv("EDGE_RPC_HOST", "192.168.1.101")
    monkeypatch.setenv("EDGE_RPC_PORT", "17800")
    monkeypatch.setenv("EDGE_COMMAND_PORT", "18081")

    pusher = EdgeCommandPusher.from_env()

    assert pusher.edge_base_url == "http://192.168.1.101:18081"
