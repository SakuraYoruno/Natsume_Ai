"""In-memory Neo4j SSOT contract for bond graph writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.core.errors import BOND_EVIDENCE_REQUIRED, BOND_SINGLE_WRITER_VIOLATION


BOND_WRITER_ID = "host_brain.bond.bond_dynamics"

REL_HAS_EVENT = "HAS_EVENT"
REL_ABOUT = "ABOUT"
REL_AFFECTS = "AFFECTS"


@dataclass(frozen=True)
class BondEventRecord:
    event_id: str
    user_id: str
    session_id: str
    topic_id: str
    capability_hint: str
    evidence_refs: tuple[str, ...]
    payload: dict[str, Any]
    ts_ms: int


class InMemoryNeo4jBondClient:
    """Dependency-free stand-in for Neo4j bond graph contracts."""

    def __init__(
        self,
        *,
        constraint_assets: list[str | Path] | None = None,
    ) -> None:
        self._nodes: dict[str, dict[str, dict[str, Any]]] = {
            "User": {},
            "Session": {},
            "Event": {},
            "Topic": {},
            "CapabilityHint": {},
        }
        self._edges: list[dict[str, Any]] = []
        self._events: dict[str, BondEventRecord] = {}
        asset_paths = list(
            constraint_assets
            or [Path(__file__).parent / "migrations" / "0001_bond_constraints.cypher"]
        )
        self._constraint_statements = _load_constraint_statements(asset_paths)

    @property
    def constraint_statements(self) -> list[str]:
        return list(self._constraint_statements)

    @property
    def events(self) -> list[dict[str, Any]]:
        rows = [self._record_to_dict(record) for record in self._events.values()]
        rows.sort(key=lambda item: int(item["ts_ms"]))
        return rows

    @property
    def edges(self) -> list[dict[str, Any]]:
        return list(self._edges)

    def write_bond_event(
        self,
        *,
        writer_id: str,
        event_id: str,
        user_id: str,
        session_id: str,
        topic_id: str,
        capability_hint: str,
        evidence_refs: list[str] | tuple[str, ...],
        payload: dict[str, Any] | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        if str(writer_id).strip() != BOND_WRITER_ID:
            raise PermissionError(BOND_SINGLE_WRITER_VIOLATION)
        normalized_refs = _normalize_evidence_refs(evidence_refs)
        if not normalized_refs:
            raise ValueError(BOND_EVIDENCE_REQUIRED)

        user_key = str(user_id).strip()
        session_key = str(session_id).strip()
        topic_key = str(topic_id).strip()
        capability_key = str(capability_hint).strip()
        event_key = str(event_id).strip()
        if (
            not user_key
            or not session_key
            or not topic_key
            or not capability_key
            or not event_key
        ):
            raise ValueError(
                "bond event requires non-empty event_id/user_id/session_id/topic_id/capability_hint"
            )

        self._upsert_node("User", user_key, {"user_id": user_key})
        self._upsert_node("Session", session_key, {"session_id": session_key})
        self._upsert_node("Topic", topic_key, {"topic_id": topic_key})
        self._upsert_node("CapabilityHint", capability_key, {"capability_hint": capability_key})
        self._upsert_node(
            "Event",
            event_key,
            {
                "event_id": event_key,
                "evidence_refs": list(normalized_refs),
                "ts_ms": int(ts_ms),
            },
        )

        self._upsert_edge("User", user_key, REL_HAS_EVENT, "Event", event_key, event_key)
        self._upsert_edge(
            "Session", session_key, REL_HAS_EVENT, "Event", event_key, event_key
        )
        self._upsert_edge("Event", event_key, REL_ABOUT, "Topic", topic_key, event_key)
        self._upsert_edge(
            "Event", event_key, REL_AFFECTS, "CapabilityHint", capability_key, event_key
        )

        record = BondEventRecord(
            event_id=event_key,
            user_id=user_key,
            session_id=session_key,
            topic_id=topic_key,
            capability_hint=capability_key,
            evidence_refs=tuple(normalized_refs),
            payload=dict(payload or {}),
            ts_ms=int(ts_ms),
        )
        self._events[event_key] = record
        return self._record_to_dict(record)

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        record = self._events.get(str(event_id))
        if record is None:
            return None
        return self._record_to_dict(record)

    def trace_user_events(self, user_id: str) -> list[dict[str, Any]]:
        user_key = str(user_id).strip()
        event_ids: list[str] = []
        for edge in self._edges:
            if (
                edge["from_label"] == "User"
                and edge["from_id"] == user_key
                and edge["rel_type"] == REL_HAS_EVENT
                and edge["to_label"] == "Event"
            ):
                event_ids.append(edge["to_id"])
        rows = [
            self._record_to_dict(self._events[event_id])
            for event_id in event_ids
            if event_id in self._events
        ]
        rows.sort(key=lambda item: int(item["ts_ms"]))
        return rows

    def _upsert_node(self, label: str, node_id: str, payload: dict[str, Any]) -> None:
        bucket = self._nodes[label]
        current = dict(bucket.get(node_id, {}))
        current.update(payload)
        bucket[node_id] = current

    def _upsert_edge(
        self,
        from_label: str,
        from_id: str,
        rel_type: str,
        to_label: str,
        to_id: str,
        event_id: str,
    ) -> None:
        for edge in self._edges:
            if (
                edge["from_label"] == from_label
                and edge["from_id"] == from_id
                and edge["rel_type"] == rel_type
                and edge["to_label"] == to_label
                and edge["to_id"] == to_id
            ):
                return
        self._edges.append(
            {
                "from_label": from_label,
                "from_id": from_id,
                "rel_type": rel_type,
                "to_label": to_label,
                "to_id": to_id,
                "event_id": event_id,
            }
        )

    def _record_to_dict(self, record: BondEventRecord) -> dict[str, Any]:
        return {
            "event_id": record.event_id,
            "user_id": record.user_id,
            "session_id": record.session_id,
            "topic_id": record.topic_id,
            "capability_hint": record.capability_hint,
            "evidence_refs": list(record.evidence_refs),
            "payload": dict(record.payload),
            "ts_ms": int(record.ts_ms),
        }


def _normalize_evidence_refs(value: list[str] | tuple[str, ...] | Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[str] = []
    for item in value:
        ref = str(item or "").strip()
        if ref:
            refs.append(ref)
    return refs


def _load_constraint_statements(paths: list[str | Path]) -> list[str]:
    statements: list[str] = []
    for path in paths:
        file_path = Path(path)
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        for raw in text.split(";"):
            line = raw.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("CREATE CONSTRAINT") or upper.startswith("CREATE INDEX"):
                statements.append(line + ";")
    return statements
