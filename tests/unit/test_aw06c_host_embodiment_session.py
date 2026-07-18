from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from host_brain.channels.embodiment import (
    EdgeEmbodimentSessionConfig,
    _EdgeRpcEmbodimentCommandPort,
    build_durable_host_embodiment_session_config,
    build_host_embodiment_command,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from natsume_contracts.edge.transport_v1 import EdgeSessionLease, stable_lease_id


class _Result:
    def __init__(self, *, success=True, reason_code=None, detail="ok", event=None):
        self.success = success
        self.status = "ACCEPTED" if success else "REJECTED"
        self.reason_code = reason_code
        self.detail = detail
        self.event = dict(event or {})


def _lease(
    *,
    now: int = 1_000,
    fence: int = 1,
    session_id: str = "host-brain-primary",
    owner_id: str = "host-brain",
) -> EdgeSessionLease:
    return EdgeSessionLease(
        session_id=session_id,
        lease_id=stable_lease_id(
            session_id=session_id, owner_id=owner_id, fencing_token=fence
        ),
        owner_id=owner_id,
        fencing_token=fence,
        issued_at_ms=now,
        expires_at_ms=now + 30_000,
    )


def test_host_port_handshakes_binds_fence_and_reuses_live_lease(monkeypatch) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    calls: list[tuple[str, dict]] = []
    lease = _lease(now=clock["value"])

    async def fake_post_to_edge(*, path, payload, **kwargs):
        calls.append((path, dict(payload)))
        if path.endswith("/session"):
            return _Result(event={"lease": lease.as_dict()})
        receipt = EmbodimentReceipt(
            command_id=str(payload["command_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="done",
            received_at_ms=clock["value"],
            completed_at_ms=clock["value"],
            receipt_ref="edge-embodiment-receipt://test",
        )
        return _Result(event={"receipt": receipt.as_dict()})

    port = _EdgeRpcEmbodimentCommandPort(post_to_edge=fake_post_to_edge)
    first = asyncio.run(
        port.submit(
            build_host_embodiment_command(
                kind="mateengine", action="command", payload={"command": {"type": "reset"}}
            )
        )
    )
    second = asyncio.run(
        port.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "hello"}
            )
        )
    )

    assert first.success and second.success
    assert [path for path, _ in calls].count("/v1/edge/embodiment/session") == 1
    commands = [payload for path, payload in calls if path == "/v1/edge/embodiment"]
    assert len(commands) == 2
    assert all(row["session_id"] == lease.session_id for row in commands)
    assert all(row["lease_id"] == lease.lease_id for row in commands)
    assert all(row["fencing_token"] == lease.fencing_token for row in commands)


def test_host_port_fails_closed_when_session_handshake_is_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_post_to_edge(*, path, **kwargs):
        calls.append(path)
        return _Result(
            success=False,
            reason_code="EDGE_SESSION_BUSY",
            detail="another Host session owns the Edge lease",
        )

    receipt = asyncio.run(
        _EdgeRpcEmbodimentCommandPort(post_to_edge=fake_post_to_edge).submit(
            build_host_embodiment_command(
                kind="mateengine", action="command", payload={"command": {"type": "reset"}}
            )
        )
    )
    assert receipt.accepted is False
    assert receipt.reason_code == "EDGE_SESSION_BUSY"
    assert calls == ["/v1/edge/embodiment/session"]


def test_replacement_host_rejects_a_non_rotating_edge_handshake(monkeypatch) -> None:
    """A stale or forged handshake must not clear a replacement fence request."""

    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: 1_000)
    inherited = _lease(now=1_000, fence=7)
    calls: list[tuple[str, dict]] = []
    persisted: list[EdgeSessionLease] = []

    async def fake_post_to_edge(*, path, payload, **_kwargs):
        calls.append((str(path), dict(payload)))
        # Edge incorrectly echoes the old live credential rather than proving
        # the requested fence rotation.  Host must fail closed before either
        # persistence or a physical command.
        return _Result(event={"lease": inherited.as_dict()})

    port = _EdgeRpcEmbodimentCommandPort(
        post_to_edge=fake_post_to_edge,
        session_config=EdgeEmbodimentSessionConfig(
            session_id=inherited.session_id,
            owner_id=inherited.owner_id,
        ),
        initial_lease=inherited,
        rotate_initial_lease_fence=True,
        lease_persister=lambda lease: persisted.append(lease) or True,
    )

    receipt = asyncio.run(
        port.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "fenced"}
            )
        )
    )

    assert receipt.accepted is False
    assert receipt.reason_code == "EDGE_EMBODIMENT_LEASE_INVALID"
    assert persisted == []
    assert port.requires_transport_fence_rotation is True
    assert calls == [
        (
            "/v1/edge/embodiment/session",
            {
                "session_id": inherited.session_id,
                "owner_id": inherited.owner_id,
                "ttl_ms": 30_000,
                "reconnect_lease_id": inherited.lease_id,
                "reconnect_fencing_token": inherited.fencing_token,
                "rotate_fencing_token": True,
            },
        )
    ]


def test_host_port_renews_near_expiry_with_exact_reconnect_credentials(monkeypatch) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    first_lease = _lease(now=1_000)
    renewed_lease = EdgeSessionLease(
        session_id=first_lease.session_id,
        lease_id=first_lease.lease_id,
        owner_id=first_lease.owner_id,
        fencing_token=first_lease.fencing_token,
        issued_at_ms=first_lease.issued_at_ms,
        expires_at_ms=60_000,
    )
    session_payloads: list[dict] = []

    async def fake_post_to_edge(*, path, payload, **kwargs):
        if path.endswith("/session"):
            session_payloads.append(dict(payload))
            lease = first_lease if len(session_payloads) == 1 else renewed_lease
            return _Result(event={"lease": lease.as_dict()})
        receipt = EmbodimentReceipt(
            command_id=str(payload["command_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="done",
            received_at_ms=clock["value"],
            completed_at_ms=clock["value"],
        )
        return _Result(event={"receipt": receipt.as_dict()})

    port = _EdgeRpcEmbodimentCommandPort(post_to_edge=fake_post_to_edge)
    asyncio.run(port.submit(build_host_embodiment_command(kind="voice", action="speak", payload={"text": "one"})))
    clock["value"] = first_lease.expires_at_ms - 4_000
    asyncio.run(port.submit(build_host_embodiment_command(kind="voice", action="speak", payload={"text": "two"})))

    assert len(session_payloads) == 2
    assert session_payloads[1]["reconnect_lease_id"] == first_lease.lease_id
    assert session_payloads[1]["reconnect_fencing_token"] == first_lease.fencing_token


def test_host_session_handshake_serializes_across_event_loops(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: 1_000)
    lease = _lease(now=1_000)
    session_calls: list[str] = []

    async def fake_post_to_edge(*, path, payload, **kwargs):
        if path.endswith("/session"):
            session_calls.append(path)
            await asyncio.sleep(0.03)
            return _Result(event={"lease": lease.as_dict()})
        receipt = EmbodimentReceipt(
            command_id=str(payload["command_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="done",
            received_at_ms=1_000,
            completed_at_ms=1_000,
        )
        return _Result(event={"receipt": receipt.as_dict()})

    port = _EdgeRpcEmbodimentCommandPort(post_to_edge=fake_post_to_edge)

    def submit(index: int):
        return asyncio.run(
            port.submit(
                build_host_embodiment_command(
                    kind="voice", action="speak", payload={"text": str(index)}
                )
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(submit, (1, 2)))
    assert all(receipt.success for receipt in receipts)
    assert session_calls == ["/v1/edge/embodiment/session"]


def test_two_ports_keep_lease_and_reset_scoped_to_their_own_runtime(monkeypatch) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    session_calls: list[dict] = []

    async def fake_post_to_edge(*, path, payload, **_kwargs):
        if path.endswith("/session"):
            session_calls.append(dict(payload))
            lease = _lease(
                now=clock["value"],
                fence=len(session_calls),
                session_id=str(payload["session_id"]),
                owner_id=str(payload["owner_id"]),
            )
            return _Result(event={"lease": lease.as_dict()})
        receipt = EmbodimentReceipt(
            command_id=str(payload["command_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="done",
            received_at_ms=clock["value"],
            completed_at_ms=clock["value"],
        )
        return _Result(event={"receipt": receipt.as_dict()})

    first = _EdgeRpcEmbodimentCommandPort(
        post_to_edge=fake_post_to_edge,
        session_config=EdgeEmbodimentSessionConfig(
            session_id="host-a", owner_id="host-a-owner"
        ),
    )
    second = _EdgeRpcEmbodimentCommandPort(
        post_to_edge=fake_post_to_edge,
        session_config=EdgeEmbodimentSessionConfig(
            session_id="host-b", owner_id="host-b-owner"
        ),
    )

    async def exercise() -> None:
        await first.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "a"}
            )
        )
        await second.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "b"}
            )
        )
        first.reset_transport_session()
        await first.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "a2"}
            )
        )
        await second.submit(
            build_host_embodiment_command(
                kind="voice", action="speak", payload={"text": "b2"}
            )
        )

    asyncio.run(exercise())
    assert [call["session_id"] for call in session_calls] == ["host-a", "host-b", "host-a"]


def test_durable_host_session_identity_is_stable_per_canonical_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOST_EDGE_EMBODIMENT_SESSION_ID", "configured-host")
    monkeypatch.setenv("HOST_EDGE_EMBODIMENT_OWNER_ID", "configured-owner")

    first_store = tmp_path / "first" / "canonical.sqlite3"
    second_store = tmp_path / "second" / "canonical.sqlite3"
    first = build_durable_host_embodiment_session_config(first_store)
    same_first = build_durable_host_embodiment_session_config(first_store)
    second = build_durable_host_embodiment_session_config(second_store)

    assert first == same_first
    assert first.session_id.startswith("configured-host::canonical-")
    assert first.owner_id.startswith("configured-owner::canonical-")
    assert first.session_id != second.session_id
    assert first.owner_id != second.owner_id
