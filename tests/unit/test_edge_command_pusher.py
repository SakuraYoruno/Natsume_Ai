from __future__ import annotations

from typing import Any

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


class _FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, *args, **kwargs) -> Any:  # noqa: ANN002, ANN003
        self.calls += 1
        raise RuntimeError("connect timeout")


def test_edge_command_pusher_enters_cooldown_after_failure(monkeypatch) -> None:
    session = _FailingSession()
    pusher = EdgeCommandPusher(
        edge_base_url="http://192.168.1.101:17800",
        timeout_ms=2000,
        failure_cooldown_ms=15000,
        session=session,
    )
    monkeypatch.setattr(pusher, "_now_ms", lambda: 1_000)

    first = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
    )

    assert first["reason"] == "HTTP_ERROR"
    assert session.calls == 1

    monkeypatch.setattr(pusher, "_now_ms", lambda: 2_000)
    second = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
    )

    assert second["reason"] == "COOLDOWN_ACTIVE"
    assert second["cooldown_remaining_ms"] > 0
    assert session.calls == 1


def test_edge_command_pusher_backoff_grows_on_repeated_failures(monkeypatch) -> None:
    session = _FailingSession()
    pusher = EdgeCommandPusher(
        edge_base_url="http://192.168.1.101:17800",
        timeout_ms=2000,
        failure_cooldown_ms=15000,
        max_failure_cooldown_ms=120000,
        session=session,
    )

    monkeypatch.setattr(pusher, "_now_ms", lambda: 1_000)
    first = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
    )
    assert first["reason"] == "HTTP_ERROR"

    monkeypatch.setattr(pusher, "_now_ms", lambda: 20_000)
    second = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
    )
    assert second["reason"] == "HTTP_ERROR"

    monkeypatch.setattr(pusher, "_now_ms", lambda: 21_000)
    during_backoff = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
    )
    assert during_backoff["reason"] == "COOLDOWN_ACTIVE"
    assert during_backoff["cooldown_remaining_ms"] >= 28000
