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
from tests.action_preview_test_support import (
    build_scheduler_action_preview,
    issue_embodiment_action_preview,
    terminal_preview_kwargs,
)


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
        authority_ref="authority://task-node/aw08-outbox-request",
        grant_ref="governor-grant://preview-child/aw08-outbox",
    )
    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="aw08-outbox",
    )
    preview = issue_embodiment_action_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        command=command,
    )
    ack = queue_embodiment_command(
        command,
        runtime=runtime,
        **terminal_preview_kwargs(preview_service, preview),
    )
    assert ack.status == "PENDING"
    assert delivered == []
    assert repository.get(ack.outbox_id)["status"] == "PENDING"

    batch = asyncio.run(runtime.dispatch_pending_async(limit=10))
    assert batch.as_dict()["status"] == "SUCCEEDED"
    assert delivered == [command.command_id]
    assert repository.get(ack.outbox_id)["status"] == "APPLIED"


def test_edge_queue_ack_waits_for_terminal_receipt_without_replay(tmp_path) -> None:
    db_path = tmp_path / "embodiment-queued.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw08-embodiment-queued",
    )
    assert migrated.success, migrated.as_dict()
    repository = EmbodimentOutboxRepository(db_path)
    submitted: list[str] = []
    looked_up: list[str] = []

    class _QueuedPort:
        async def submit(self, command):  # noqa: ANN001, ANN201
            submitted.append(command.command_id)
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="ACCEPTED",
                reason_code=None,
                detail="tts queued",
                received_at_ms=command.issued_at_ms,
                completed_at_ms=command.issued_at_ms,
                event={"external_side_effects": "PENDING"},
            )

        async def query_receipt(self, command):  # noqa: ANN001, ANN201
            looked_up.append(command.command_id)
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="APPLIED",
                reason_code=None,
                detail="tts playback completed",
                received_at_ms=command.issued_at_ms,
                completed_at_ms=command.issued_at_ms + 1,
                event={"external_side_effects": True},
            )

    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="aw08-queued-dispatcher",
        command_port=_QueuedPort(),
    )
    command = build_host_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload={"emotion_tag": "HAPPY"},
        request_id="aw08-queued-request",
        authority_ref="authority://task-node/aw08-queued-request",
        grant_ref="governor-grant://preview-child/aw08-queued",
    )
    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="aw08-queued",
    )
    preview = issue_embodiment_action_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        command=command,
    )
    ack = runtime.enqueue(
        command,
        **terminal_preview_kwargs(preview_service, preview),
    )

    dispatched = asyncio.run(runtime.dispatch_pending_async(limit=1))
    assert dispatched.in_doubt == 1
    assert dispatched.rejected == 0
    assert repository.get(ack.outbox_id)["status"] == "IN_DOUBT"

    reconciled = asyncio.run(runtime.reconcile_in_doubt_async(limit=1))
    assert reconciled.applied == 1
    assert submitted == [command.command_id]
    assert looked_up == [command.command_id]
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

    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="aw08-runtime-isolation",
    )
    command_a = build_host_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload={"emotion_tag": "HAPPY"},
        request_id="host-a",
        authority_ref="authority://task-node/host-a",
        grant_ref="governor-grant://preview-child/aw08-runtime-isolation",
        idempotency_key="host-a-expression",
    )
    preview_a = issue_embodiment_action_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        command=command_a,
    )
    proof_a = terminal_preview_kwargs(preview_service, preview_a)
    ack_a = runtime_a.enqueue(command_a, **proof_a)
    assert repo_b.get(ack_a.outbox_id) is None
    assert asyncio.run(runtime_a.dispatch_pending_async()).applied == 1
    assert delivered_a == [command_a.command_id]
    assert delivered_b == []
    assert audited_a == [command_a.command_id]
    assert audited_b == []

    runtime_a.close()
    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_CLOSED"):
        runtime_a.enqueue(command_a, **proof_a)
    command_b = build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "reset"}},
        request_id="host-b",
        authority_ref="authority://task-node/host-b",
        grant_ref="governor-grant://preview-child/aw08-runtime-isolation",
        idempotency_key="host-b-mateengine",
    )
    preview_b = issue_embodiment_action_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        command=command_b,
    )
    ack_b = runtime_b.enqueue(
        command_b,
        **terminal_preview_kwargs(preview_service, preview_b),
    )
    assert asyncio.run(runtime_b.dispatch_pending_async()).applied == 1
    assert repo_a.get(ack_b.outbox_id) is None
    assert delivered_b == [command_b.command_id]
    assert audited_b == [command_b.command_id]
