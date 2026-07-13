from __future__ import annotations

import asyncio
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    build_host_embodiment_command,
    bind_embodiment_outbox_authority,
    queue_embodiment_command,
)
from host_brain.channels.embodiment_outbox import (
    DurableEmbodimentOutbox,
    EmbodimentOutboxDispatcher,
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
    outbox = DurableEmbodimentOutbox(repository)
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

    unbind_outbox = bind_embodiment_outbox_authority(outbox)
    try:
        command = build_host_embodiment_command(
            kind="mateengine",
            action="command",
            payload={"command": {"type": "reset"}},
            request_id="aw08-outbox-request",
        )
        ack = queue_embodiment_command(command)
        assert ack.status == "PENDING"
        assert delivered == []
        assert repository.get(ack.outbox_id)["status"] == "PENDING"

        batch = asyncio.run(
            EmbodimentOutboxDispatcher(
                repository,
                worker_id="aw08-dispatcher",
                command_port=_Port(),
            ).dispatch_pending_async(limit=10)
        )
        assert batch.as_dict()["status"] == "SUCCEEDED"
        assert delivered == [command.command_id]
        assert repository.get(ack.outbox_id)["status"] == "APPLIED"
    finally:
        unbind_outbox()


def test_sync_queue_has_no_fire_and_forget_edge_task() -> None:
    source = (ROOT / "host_brain/channels/embodiment.py").read_text(encoding="utf-8")

    assert "_pending_tasks" not in source
    assert "_on_submission_done" not in source
    assert "loop.create_task(submit_embodiment_command" not in source
    assert "EMBODIMENT_OUTBOX_NOT_CONFIGURED" in source
