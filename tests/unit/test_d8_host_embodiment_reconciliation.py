from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    EdgeEmbodimentSessionConfig,
    _EdgeRpcEmbodimentCommandPort,
    EmbodimentReceiptLookupUnavailable,
    HostEmbodimentRuntime,
    build_host_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
    EmbodimentRuntimeAuthority,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from natsume_contracts.edge.transport_v1 import EdgeSessionLease, stable_lease_id


class _Result:
    def __init__(
        self,
        *,
        success: bool = True,
        status: str = "ACCEPTED",
        reason_code: str | None = None,
        detail: str = "ok",
        event: dict | None = None,
    ) -> None:
        self.success = success
        self.status = status
        self.reason_code = reason_code
        self.detail = detail
        self.event = dict(event or {})


class _LookupPort:
    def __init__(self, outcome: EmbodimentReceipt | None | BaseException) -> None:
        self.outcome = outcome
        self.submit_calls: list[str] = []
        self.lookup_calls: list[str] = []

    async def submit(self, command):  # noqa: ANN001, ANN201
        self.submit_calls.append(command.command_id)
        raise AssertionError("reconciliation must never submit or replay an embodiment command")

    async def query_receipt(self, command):  # noqa: ANN001, ANN201
        self.lookup_calls.append(command.command_id)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _repository(tmp_path: Path, name: str) -> tuple[EmbodimentOutboxRepository, Path]:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"d8-reconcile-{name}",
    )
    assert migrated.success, migrated.as_dict()
    return EmbodimentOutboxRepository(db_path), db_path


def _command(name: str):
    return build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "status"}},
        request_id=f"d8-reconcile-{name}",
    )


def _receipt(command, *, status: str = "APPLIED") -> EmbodimentReceipt:  # noqa: ANN001
    accepted = status in {"APPLIED", "COMPLETED", "SUCCEEDED"}
    return EmbodimentReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        accepted=accepted,
        status=status,
        reason_code=None,
        detail=f"edge {status.lower()}",
        received_at_ms=command.issued_at_ms,
        completed_at_ms=command.issued_at_ms + 1,
        event={"external_side_effects": True},
        receipt_ref="edge-embodiment-receipt://d8-test",
    )


def _expire_dispatched_row(
    repository: EmbodimentOutboxRepository,
    db_path: Path,
    command,
) -> str:  # noqa: ANN001
    lease = repository.acquire_runtime_lease(
        worker_id="d8-reconcile-worker",
        lease_ms=60_000,
    )
    assert lease is not None
    authority = EmbodimentRuntimeAuthority(
        worker_id="d8-reconcile-worker",
        lease_id=str(lease["lease_id"]),
        fencing_token=int(lease["fencing_token"]),
    )
    row = repository.enqueue(command.as_dict(), authority=authority)
    claimed = repository.claim_next(
        worker_id="d8-reconcile-worker",
        authority=authority,
    )
    assert claimed is not None
    assert claimed["outbox_id"] == row["outbox_id"]
    # Simulate a Host process crash after the dispatch claim.  This does not
    # emulate an Edge result and must therefore enter IN_DOUBT first.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE host_embodiment_outbox SET claim_expires_at_ms = 0 "
            "WHERE outbox_id = ?",
            (str(row["outbox_id"]),),
        )
        connection.commit()
    assert repository.release_runtime_lease(
        worker_id="d8-reconcile-worker",
        lease_id=str(lease["lease_id"]),
        fencing_token=int(lease["fencing_token"]),
    )
    return str(row["outbox_id"])


def test_expired_host_dispatch_reconciles_found_edge_receipt_without_replay(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "found")
    command = _command("found")
    outbox_id = _expire_dispatched_row(repository, db_path, command)
    observed: list[str] = []
    port = _LookupPort(_receipt(command))
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="d8-reconcile-worker",
        command_port=port,
        receipt_observers=(lambda actual, _receipt_value: observed.append(actual.command_id),),
    )

    batch = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))

    assert batch.expired == 1
    assert batch.scanned == 1
    assert batch.applied == 1
    assert port.lookup_calls == [command.command_id]
    assert port.submit_calls == []
    assert observed == [command.command_id]
    row = repository.get(outbox_id)
    assert row is not None
    assert row["status"] == "APPLIED"
    assert row["receipt_digest"].startswith("sha256:")


def test_not_found_receipt_keeps_expired_host_command_in_doubt_without_replay(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "not-found")
    command = _command("not-found")
    outbox_id = _expire_dispatched_row(repository, db_path, command)
    port = _LookupPort(None)
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="d8-reconcile-worker",
        command_port=port,
    )

    batch = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))

    assert batch.expired == 1
    assert batch.not_found == 1
    assert batch.applied == 0
    assert port.submit_calls == []
    row = repository.get(outbox_id)
    assert row is not None
    assert row["status"] == "IN_DOUBT"
    assert row["receipt_json"] is None


def test_lookup_fault_keeps_host_command_in_doubt_without_replay(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "lookup-fault")
    command = _command("lookup-fault")
    outbox_id = _expire_dispatched_row(repository, db_path, command)
    port = _LookupPort(RuntimeError("edge receipt transport unavailable"))
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="d8-reconcile-worker",
        command_port=port,
    )

    batch = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))

    assert batch.expired == 1
    assert batch.unavailable == 1
    assert port.submit_calls == []
    row = repository.get(outbox_id)
    assert row is not None
    assert row["status"] == "IN_DOUBT"
    assert row["receipt_json"] is None


def test_queued_edge_receipt_stays_in_doubt_until_terminal_without_replay(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "queued")
    command = _command("queued")
    outbox_id = _expire_dispatched_row(repository, db_path, command)
    queued = EmbodimentReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        accepted=True,
        status="ACCEPTED",
        reason_code=None,
        detail="tts queued",
        received_at_ms=command.issued_at_ms,
        completed_at_ms=command.issued_at_ms,
        event={"external_side_effects": "PENDING"},
        receipt_ref="edge-embodiment-receipt://d8-queued",
    )
    port = _LookupPort(queued)
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="d8-reconcile-worker",
        command_port=port,
    )

    first = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))
    second = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))

    assert first.still_in_doubt == 1
    assert second.still_in_doubt == 1
    assert port.submit_calls == []
    assert port.lookup_calls == [command.command_id, command.command_id]
    row = repository.get(outbox_id)
    assert row is not None
    assert row["status"] == "IN_DOUBT"


def test_repeated_reconciliation_cannot_overwrite_or_execute_an_applied_receipt(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "duplicate")
    command = _command("duplicate")
    outbox_id = _expire_dispatched_row(repository, db_path, command)
    port = _LookupPort(_receipt(command))
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="d8-reconcile-worker",
        command_port=port,
    )

    first = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))
    settled = repository.get(outbox_id)
    assert settled is not None
    original_receipt_json = settled["receipt_json"]
    port.outcome = _receipt(command, status="REJECTED")
    second = asyncio.run(runtime.reconcile_in_doubt_async(limit=10))

    assert first.applied == 1
    assert second.scanned == 0
    assert port.lookup_calls == [command.command_id]
    assert port.submit_calls == []
    row = repository.get(outbox_id)
    assert row is not None
    assert row["status"] == "APPLIED"
    assert row["receipt_json"] == original_receipt_json


def test_edge_rpc_receipt_query_uses_live_lease_and_distinguishes_not_found(monkeypatch) -> None:
    clock = {"value": 10_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    command = _command("edge-port-query")
    lease = EdgeSessionLease(
        session_id="d8-host",
        lease_id=stable_lease_id(
            session_id="d8-host",
            owner_id="d8-owner",
            fencing_token=1,
        ),
        owner_id="d8-owner",
        fencing_token=1,
        issued_at_ms=clock["value"],
        expires_at_ms=clock["value"] + 30_000,
    )
    calls: list[tuple[str, dict]] = []
    state = {"found": True}

    async def post_to_edge(*, path, payload, **_kwargs):
        calls.append((path, dict(payload)))
        if path.endswith("/session"):
            return _Result(event={"lease": lease.as_dict()})
        if state["found"]:
            return _Result(
                event={
                    "receipt_query": {"status": "FOUND"},
                    "receipt": _receipt(command).as_dict(),
                }
            )
        return _Result(
            reason_code="EMBODIMENT_RECEIPT_NOT_FOUND",
            event={"receipt_query": {"status": "NOT_FOUND"}, "receipt": None},
        )

    port = _EdgeRpcEmbodimentCommandPort(
        post_to_edge=post_to_edge,
        session_config=EdgeEmbodimentSessionConfig(
            session_id="d8-host",
            owner_id="d8-owner",
        ),
    )
    found = asyncio.run(port.query_receipt(command))
    state["found"] = False
    missing = asyncio.run(port.query_receipt(command))

    assert found is not None
    assert found.command_id == command.command_id
    assert missing is None
    assert [path for path, _payload in calls] == [
        "/v1/edge/embodiment/session",
        "/v1/edge/embodiment/receipt",
        "/v1/edge/embodiment/receipt",
    ]
    query_payload = calls[1][1]
    assert query_payload["command_id"] == command.command_id
    assert query_payload["idempotency_key"] == command.idempotency_key
    assert query_payload["lease_id"] == lease.lease_id
    assert query_payload["fencing_token"] == lease.fencing_token


def test_edge_rpc_receipt_query_raises_for_unavailable_lookup(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: 10_000)
    command = _command("edge-port-unavailable")

    async def post_to_edge(*, path, **_kwargs):
        if path.endswith("/session"):
            return _Result(success=False, reason_code="EDGE_SESSION_BUSY")
        raise AssertionError("receipt query must not run without a live lease")

    port = _EdgeRpcEmbodimentCommandPort(post_to_edge=post_to_edge)
    with pytest.raises(EmbodimentReceiptLookupUnavailable, match="EDGE_SESSION_BUSY"):
        asyncio.run(port.query_receipt(command))
