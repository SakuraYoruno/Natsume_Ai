"""In-memory Neo4j SSOT contract for bond graph writes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from host_brain.core.errors import (
    BOND_EVIDENCE_REQUIRED,
    BOND_SINGLE_WRITER_VIOLATION,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    NEO4J_AUTH_FAILED,
    NEO4J_LEADER_UNAVAILABLE,
    NEO4J_SCHEMA_DRIFT,
)


BOND_WRITER_ID = "host_brain.bond.bond_dynamics"

REL_HAS_EVENT = "HAS_EVENT"
REL_ABOUT = "ABOUT"
REL_AFFECTS = "AFFECTS"

S_NEO4J_CONFIG = "S_NEO4J_CONFIG"
S_NEO4J_PROBE = "S_NEO4J_PROBE"

NEO4J_STATUS_UP = "UP"
NEO4J_STATUS_DOWN = "DOWN"


@dataclass(frozen=True)
class Neo4jConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class Neo4jRuntimeConfig:
    enabled: bool
    uri: str
    database: str
    auth_ref: str | None
    pool_size: int
    tls_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "uri": self.uri,
            "database": self.database,
            "auth_ref": self.auth_ref,
            "pool_size": self.pool_size,
            "tls_enabled": self.tls_enabled,
        }


@dataclass(frozen=True)
class Neo4jConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: Neo4jRuntimeConfig | None
    issues: tuple[Neo4jConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "config": (self.config.as_dict() if self.config is not None else None),
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


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


def load_neo4j_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> Neo4jConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[Neo4jConfigIssue] = []
    runtime_env = str(merged.get("Nastume_ENV") or "dev").strip().lower()

    enabled = _parse_bool(merged.get("BOND_NEO4J_ENABLED"), default=True)
    uri = str(merged.get("BOND_NEO4J_URI") or "bolt://127.0.0.1:7687").strip()
    database = str(merged.get("BOND_NEO4J_DATABASE") or "neo4j").strip()
    auth_ref = _as_optional_text(merged.get("BOND_NEO4J_AUTH_REF"))
    pool_size = _parse_int(
        merged.get("BOND_NEO4J_POOL_SIZE"),
        default=50,
        min_value=1,
        max_value=1000,
        field_name="BOND_NEO4J_POOL_SIZE",
        issues=issues,
    )
    tls_enabled = _parse_bool(
        merged.get("BOND_NEO4J_TLS_ENABLED"),
        default=(runtime_env == "prod"),
    )

    if enabled and not uri:
        issues.append(
            Neo4jConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="BOND_NEO4J_URI required when BOND_NEO4J_ENABLED=true",
                field="BOND_NEO4J_URI",
            )
        )
    if enabled and not database:
        issues.append(
            Neo4jConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="BOND_NEO4J_DATABASE required when BOND_NEO4J_ENABLED=true",
                field="BOND_NEO4J_DATABASE",
            )
        )
    if enabled and not auth_ref:
        issues.append(
            Neo4jConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="BOND_NEO4J_AUTH_REF required when BOND_NEO4J_ENABLED=true",
                field="BOND_NEO4J_AUTH_REF",
            )
        )

    if issues:
        return Neo4jConfigLoadResult(
            success=False,
            stage=S_NEO4J_CONFIG,
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = Neo4jRuntimeConfig(
        enabled=bool(enabled),
        uri=uri,
        database=database,
        auth_ref=auth_ref,
        pool_size=pool_size,
        tls_enabled=bool(tls_enabled),
    )
    return Neo4jConfigLoadResult(
        success=True,
        stage=S_NEO4J_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def build_neo4j_probe_report(
    *,
    config: Neo4jRuntimeConfig,
    request_id: str | None,
    probe: Mapping[str, Any] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    probe_data = dict(probe or {})
    if not config.enabled:
        return {
            "request_id": request_id,
            "stage": S_NEO4J_PROBE,
            "status": NEO4J_STATUS_DOWN,
            "reason_code": NEO4J_LEADER_UNAVAILABLE,
            "failure_class": "leader_unavailable",
            "detail": "neo4j disabled by config",
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }

    reachable = bool(probe_data.get("reachable", True))
    auth_ok = bool(probe_data.get("auth_ok", True))
    schema_ok = bool(probe_data.get("schema_ok", True))
    leader_ok = bool(probe_data.get("leader_ok", True))

    if not reachable or not leader_ok:
        return {
            "request_id": request_id,
            "stage": S_NEO4J_PROBE,
            "status": NEO4J_STATUS_DOWN,
            "reason_code": NEO4J_LEADER_UNAVAILABLE,
            "failure_class": "leader_unavailable",
            "detail": str(probe_data.get("detail") or "neo4j leader unavailable"),
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }
    if not auth_ok:
        return {
            "request_id": request_id,
            "stage": S_NEO4J_PROBE,
            "status": NEO4J_STATUS_DOWN,
            "reason_code": NEO4J_AUTH_FAILED,
            "failure_class": "auth_failed",
            "detail": str(probe_data.get("detail") or "neo4j authentication failed"),
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }
    if not schema_ok:
        return {
            "request_id": request_id,
            "stage": S_NEO4J_PROBE,
            "status": NEO4J_STATUS_DOWN,
            "reason_code": NEO4J_SCHEMA_DRIFT,
            "failure_class": "schema_drift",
            "detail": str(probe_data.get("detail") or "neo4j schema drift detected"),
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }

    return {
        "request_id": request_id,
        "stage": S_NEO4J_PROBE,
        "status": NEO4J_STATUS_UP,
        "reason_code": None,
        "failure_class": None,
        "detail": str(probe_data.get("detail") or "neo4j probe ok"),
        "config": config.as_dict(),
        "ts_ms": int(ts_ms if ts_ms is not None else 0),
    }


def build_neo4j_dependency_status(probe_report: Mapping[str, Any]) -> dict[str, Any]:
    status = str(probe_report.get("status") or "").strip().upper()
    if status == NEO4J_STATUS_UP:
        return {
            "available": True,
            "detail": "neo4j dependency healthy",
        }
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": (
            f"{probe_report.get('reason_code')}: "
            f"{str(probe_report.get('detail') or 'neo4j probe failed')}"
        ),
    }


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


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(
    value: Any,
    *,
    default: int,
    min_value: int,
    max_value: int,
    field_name: str,
    issues: list[Neo4jConfigIssue],
) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        issues.append(
            Neo4jConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be integer",
                field=field_name,
            )
        )
        return int(default)
    if parsed < min_value or parsed > max_value:
        issues.append(
            Neo4jConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be in range [{min_value}, {max_value}]",
                field=field_name,
            )
        )
        return int(default)
    return parsed


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
