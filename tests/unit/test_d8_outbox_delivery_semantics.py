from __future__ import annotations

import asyncio

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    EmbodimentOutboxAck,
    HostEmbodimentRuntime,
    build_host_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import EmbodimentOutboxRepository
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from tests.action_preview_test_support import (
    build_scheduler_action_preview,
    issue_embodiment_action_preview,
    terminal_preview_kwargs,
)


def test_duplicate_terminal_outbox_ack_remains_a_successful_durable_admission(tmp_path) -> None:
    """A lost caller response must not turn an already-applied command into failure."""

    db_path = tmp_path / "d8-outbox-duplicate.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d8-outbox-duplicate"
    )
    assert migration.success, migration.as_dict()

    class _AppliedPort:
        async def submit(self, command):  # noqa: ANN001, ANN201
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

    runtime = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="d8-outbox-duplicate",
        command_port=_AppliedPort(),
    )
    try:
        command = build_host_embodiment_command(
            kind="mateengine",
            action="command",
            payload={"command": {"type": "reset"}},
            request_id="d8-outbox-duplicate",
            authority_ref="authority://task-node/d8-outbox-duplicate",
            grant_ref="governor-grant://preview-child/d8-outbox-duplicate",
        )
        service, scheduler_context = build_scheduler_action_preview(
            tmp_path,
            name="d8-outbox-duplicate",
        )
        preview = issue_embodiment_action_preview(
            service=service,
            scheduler_context=scheduler_context,
            command=command,
        )
        proof = terminal_preview_kwargs(service, preview)
        first = runtime.enqueue(command, **proof)
        assert first.status == "PENDING"
        assert asyncio.run(runtime.dispatch_pending_async(limit=1)).applied == 1

        repeated = runtime.enqueue(command, **proof)
        assert repeated.outbox_id == first.outbox_id
        assert repeated.status == "APPLIED"
        assert repeated.durable_admitted is True
        assert repeated.success is True
        assert repeated.effect_verified is True
    finally:
        runtime.close()


def test_rejected_terminal_ack_is_not_a_successful_durable_admission() -> None:
    ack = EmbodimentOutboxAck(
        outbox_id="outbox-rejected",
        command_id="command-rejected",
        idempotency_key="key-rejected",
        status="REJECTED",
        reason_code="EDGE_EMBODIMENT_REJECTED",
    )

    assert ack.durable_admitted is False
    assert ack.success is False
    assert ack.effect_verified is False
