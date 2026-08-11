"""Process-local construction and serialized publication of Task Runtime Outbox messages.

The boundary owns only the factory, publisher, publish limit, and one
process-local lock.  Durable Outbox state remains owned by the injected ports.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from .ports import RuntimeOutboxPublisherPort, TaskOutboxFactoryPort


class RuntimeOutbox:
    """Build canonical runtime messages and serialize one publisher instance."""

    def __init__(
        self,
        *,
        publisher: RuntimeOutboxPublisherPort,
        factory: TaskOutboxFactoryPort,
        publish_limit: int,
    ) -> None:
        self._publisher = publisher
        self._factory = factory
        self._publish_limit = max(1, int(publish_limit))
        self._publish_lock = asyncio.Lock()

    def build(
        self,
        *,
        command_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        graph_revision: int | None = None,
        attempt_id: str | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> Any:
        identity = (
            command_id,
            aggregate_type,
            aggregate_id,
            str(aggregate_version),
            event_type,
        )
        material = "\n".join(identity)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        version = int(aggregate_version)
        payload_aggregate_id = aggregate_id
        if aggregate_type == "TASK_GRAPH":
            if type(graph_revision) is not int or graph_revision < 1:
                raise ValueError(
                    "TASK_GRAPH Runtime Outbox requires a positive graph_revision"
                )
            payload_aggregate_id = f"{aggregate_id}@{graph_revision}"
        return self._factory.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            event_type=event_type,
            payload_ref=(
                f"taskstore://event/{aggregate_type}/{payload_aggregate_id}/{version}"
            ),
            idempotency_key=f"governed:{digest}",
            graph_revision=graph_revision,
            attempt_id=attempt_id,
            evidence_refs=tuple(
                dict.fromkeys(
                    _text(item) for item in evidence_refs if _text(item)
                )
            ),
            outbox_id=f"outbox::governed::{digest}",
        )

    async def publish_pending(
        self,
        *,
        best_effort: bool = False,
    ) -> dict[str, Any]:
        async with self._publish_lock:
            try:
                result = self._publisher.publish_pending(limit=self._publish_limit)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                if best_effort:
                    return {"status": "DEGRADED"}
                raise
        return dict(result) if isinstance(result, Mapping) else {"status": "OK"}


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


__all__ = ["RuntimeOutbox"]
