"""Small append-only immune memory store."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from host_brain.core.ids import now_ms


@dataclass(slots=True)
class ImmuneMemoryRecord:
    record_id: str
    source: str
    status: str
    affected_components: list[str] = field(default_factory=list)
    work_order_id: str | None = None
    result_status: str | None = None
    created_at_ms: int = field(default_factory=now_ms)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryImmuneMemory:
    def __init__(self) -> None:
        self._records: list[ImmuneMemoryRecord] = []

    def append(self, record: ImmuneMemoryRecord) -> ImmuneMemoryRecord:
        self._records.append(record)
        return record

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        size = max(0, int(limit or 0))
        return [record.to_dict() for record in self._records[-size:]]
