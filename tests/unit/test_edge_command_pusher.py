from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from host_brain.governor.action_preview import ActionPreviewService
from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_BLOCKED,
    ActionPreviewAuthorityError,
)
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof
from host_brain.tribe.edge_command_pusher import (
    EdgeCommandPusher,
    build_edge_command_preview_args,
    edge_command_preview_scope,
    init_edge_command_pusher,
    set_edge_command_pusher,
)


def _preview_service(require_execution=None) -> ActionPreviewService:  # noqa: ANN001
    service = ActionPreviewService(
        runtime_event_store=object(),
        guardian=SimpleNamespace(evaluate_capability=lambda **_kwargs: {}),
        authority_resolver=SimpleNamespace(resolve=lambda **_kwargs: None),
    )
    service.require_execution = (  # type: ignore[method-assign]
        require_execution or (lambda **_kwargs: SimpleNamespace())
    )
    return service


def _proof(
    *,
    command_type: str = "emotion_state_command",
    target_agent: str = "edge.somatic",
    source_agent: str = "host_brain",
    payload: dict[str, Any] | None = None,
    require_execution=None,  # noqa: ANN001
) -> ActionPreviewExecutionProof:
    args = build_edge_command_preview_args(
        command_type=command_type,
        target_agent=target_agent,
        source_agent=source_agent,
        payload=payload,
    )
    return ActionPreviewExecutionProof(
        service=_preview_service(require_execution),
        event_id="runtime-event://edge-command-preview",
        boundary={
            "action_kind": "edge.tribe_command",
            "action_scope": edge_command_preview_scope(args),
            "risk_level": 4,
            "action_args": args,
            "context_pack_digest": "sha256:" + "1" * 64,
            "scene_identity": {"decision_digest": "sha256:" + "2" * 64},
            "task_graph_id": "graph-edge-command",
            "graph_revision": 1,
            "task_node_id": "node-edge-command",
            "parent_governor_grant_ref": "grant://edge-command/parent",
            "node_grant_ref": "grant://edge-command/node",
            "node_grant": {"grant_ref": "grant://edge-command/node"},
            "authority_context": {"schema_version": "test.edge-command.v1"},
        },
    )


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
        action_preview_proof=_proof(),
    )

    assert first["reason"] == "HTTP_ERROR"
    assert session.calls == 1

    monkeypatch.setattr(pusher, "_now_ms", lambda: 2_000)
    second = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
        action_preview_proof=_proof(),
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
        action_preview_proof=_proof(),
    )
    assert first["reason"] == "HTTP_ERROR"

    monkeypatch.setattr(pusher, "_now_ms", lambda: 20_000)
    second = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
        action_preview_proof=_proof(),
    )
    assert second["reason"] == "HTTP_ERROR"

    monkeypatch.setattr(pusher, "_now_ms", lambda: 21_000)
    during_backoff = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
        action_preview_proof=_proof(),
    )
    assert during_backoff["reason"] == "COOLDOWN_ACTIVE"
    assert during_backoff["cooldown_remaining_ms"] >= 28000


def test_edge_command_pusher_constructor_flag_cannot_bypass_preview() -> None:
    session = _FailingSession()
    pusher = EdgeCommandPusher(
        edge_base_url="http://edge.local",
        session=session,
        enforce_action_preview=False,
    )

    result = pusher.push_command(
        command_type="gate_control_command",
        target_agent="edge.channel",
        payload={"gate_open": True},
    )

    assert result["reason"] == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert session.calls == 0


def test_edge_command_pusher_rejects_tamper_and_lost_lease_before_network() -> None:
    session = _FailingSession()
    pusher = EdgeCommandPusher(edge_base_url="http://edge.local", session=session)
    exact = _proof(payload={"gate_open": True})

    tampered = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
        payload={"gate_open": False},
        action_preview_proof=exact,
    )
    assert tampered["reason"] == "ACTION_PREVIEW_DRIFT"

    def _lost_lease(**_kwargs):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "Task node Lease is no longer active",
        )

    lost = pusher.push_command(
        command_type="emotion_state_command",
        target_agent="edge.somatic",
        action_preview_proof=_proof(require_execution=_lost_lease),
    )
    assert lost["reason"] == ACTION_PREVIEW_BLOCKED
    assert session.calls == 0


def test_edge_command_pusher_singleton_injection_cannot_replace_gated_type() -> None:
    class UngatedPusher(EdgeCommandPusher):
        pass

    injected = UngatedPusher(edge_base_url="http://edge.local")
    set_edge_command_pusher(None)
    try:
        for install in (set_edge_command_pusher, init_edge_command_pusher):
            try:
                install(injected)
            except TypeError as exc:
                assert "canonical gated type" in str(exc)
            else:  # pragma: no cover - fail-closed assertion
                raise AssertionError("subclass injection must not downgrade the gate")
    finally:
        set_edge_command_pusher(None)
