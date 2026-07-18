from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    HostEmbodimentRuntime,
    build_host_embodiment_command,
    queue_embodiment_command,
    submit_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt


ROOT = Path(__file__).resolve().parents[2]


def test_host_physical_command_is_durable_before_single_dispatch(tmp_path) -> None:
    db_path = tmp_path / "embodiment-outbox.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw08-embodiment-outbox",
    )
    assert migrated.success, migrated.as_dict()
    repository = EmbodimentOutboxRepository(db_path)
    delivered: list[str] = []

    class _Port:
        async def submit(self, command):  # noqa: ANN001, ANN201
            delivered.append(command.command_id)
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="APPLIED",
                reason_code=None,
                detail="applied",
                received_at_ms=command.issued_at_ms,
                completed_at_ms=command.issued_at_ms + 1,
                event={"external_side_effects": True},
            )

    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="aw08-dispatcher",
        command_port=_Port(),
    )
    command = build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "reset"}},
        request_id="aw08-outbox-request",
    )
    ack = queue_embodiment_command(command, runtime=runtime)
    assert ack.status == "PENDING"
    assert delivered == []
    assert repository.get(ack.outbox_id)["status"] == "PENDING"

    batch = asyncio.run(runtime.dispatch_pending_async(limit=10))
    assert batch.as_dict()["status"] == "SUCCEEDED"
    assert delivered == [command.command_id]
    assert repository.get(ack.outbox_id)["status"] == "APPLIED"


def test_sync_queue_has_no_fire_and_forget_edge_task() -> None:
    source = (ROOT / "host_brain/channels/embodiment.py").read_text(encoding="utf-8")

    assert "_pending_tasks" not in source
    assert "_on_submission_done" not in source
    assert "loop.create_task(submit_embodiment_command" not in source
    assert "_durable_outbox_authority" not in source
    assert "EMBODIMENT_RUNTIME_REQUIRED" in source


def test_direct_submission_without_an_explicit_runtime_fails_closed() -> None:
    command = build_host_embodiment_command(
        kind="voice", action="speak", payload={"text": "no default"}
    )
    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_REQUIRED"):
        asyncio.run(submit_embodiment_command(command))


def test_two_host_runtimes_isolate_outboxes_receipts_and_shutdown(tmp_path) -> None:
    def repository_for(name: str) -> EmbodimentOutboxRepository:
        db_path = tmp_path / f"{name}.sqlite3"
        migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
            request_id=f"aw08-{name}",
        )
        assert migrated.success, migrated.as_dict()
        return EmbodimentOutboxRepository(db_path)

    delivered_a: list[str] = []
    delivered_b: list[str] = []
    audited_a: list[str] = []
    audited_b: list[str] = []

    class _Port:
        def __init__(self, delivered: list[str]) -> None:
            self._delivered = delivered

        async def submit(self, command):  # noqa: ANN001, ANN201
            self._delivered.append(command.command_id)
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="APPLIED",
                reason_code=None,
                detail="applied",
                received_at_ms=command.issued_at_ms,
                completed_at_ms=command.issued_at_ms + 1,
            )

    repo_a = repository_for("host-a")
    repo_b = repository_for("host-b")
    runtime_a = HostEmbodimentRuntime(
        repository=repo_a,
        worker_id="host-a-worker",
        command_port=_Port(delivered_a),
        receipt_observers=(lambda command, _receipt: audited_a.append(command.command_id),),
    )
    runtime_b = HostEmbodimentRuntime(
        repository=repo_b,
        worker_id="host-b-worker",
        command_port=_Port(delivered_b),
        receipt_observers=(lambda command, _receipt: audited_b.append(command.command_id),),
    )

    command_a = build_host_embodiment_command(
        kind="voice", action="speak", payload={"text": "A"}, request_id="host-a"
    )
    ack_a = runtime_a.enqueue(command_a)
    assert repo_b.get(ack_a.outbox_id) is None
    assert asyncio.run(runtime_a.dispatch_pending_async()).applied == 1
    assert delivered_a == [command_a.command_id]
    assert delivered_b == []
    assert audited_a == [command_a.command_id]
    assert audited_b == []

    runtime_a.close()
    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_CLOSED"):
        runtime_a.enqueue(command_a)
    command_b = build_host_embodiment_command(
        kind="voice", action="speak", payload={"text": "B"}, request_id="host-b"
    )
    ack_b = runtime_b.enqueue(command_b)
    assert asyncio.run(runtime_b.dispatch_pending_async()).applied == 1
    assert repo_a.get(ack_b.outbox_id) is None
    assert delivered_b == [command_b.command_id]
    assert audited_b == [command_b.command_id]
