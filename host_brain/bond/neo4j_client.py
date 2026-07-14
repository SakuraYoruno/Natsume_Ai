"""In-memory Neo4j SSOT contract for bond graph writes."""

from __future__ import annotations

from dataclasses import dataclass
import json
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
from host_brain.bond.neo4j_schemas import (
    BondEventRecord,
    Neo4jConfigIssue,
    Neo4jConfigLoadResult,
    Neo4jRuntimeConfig,
)
from host_brain.persistence.bond_fallback_repository import BondFallbackRepository


BOND_WRITER_ID = "host_brain.bond.bond_dynamics"

REL_HAS_EVENT = "HAS_EVENT"
REL_ABOUT = "ABOUT"
REL_AFFECTS = "AFFECTS"

S_NEO4J_CONFIG = "S_NEO4J_CONFIG"
S_NEO4J_PROBE = "S_NEO4J_PROBE"

NEO4J_STATUS_UP = "UP"
NEO4J_STATUS_DOWN = "DOWN"

DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")


def load_neo4j_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> Neo4jConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[Neo4jConfigIssue] = []
    runtime_env = str(merged.get("Natsume_ENV") or "dev").strip().lower()

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


class Neo4jBondClient:
    """Neo4j driver-backed bond reader/writer for production bond graph."""

    _EVENTS_QUERY = """
    MATCH (e:Event)
    OPTIONAL MATCH (e)-[:ABOUT]->(t:Topic)
    OPTIONAL MATCH (e)-[:AFFECTS]->(c:CapabilityHint)
    RETURN
      e.event_id AS event_id,
      coalesce(t.topic_id, e.topic_id, "") AS topic_id,
      coalesce(c.capability_hint, e.capability_hint, "") AS capability_hint,
      e.evidence_refs AS evidence_refs,
      e.payload AS payload,
      e.ts_ms AS ts_ms
    ORDER BY coalesce(e.ts_ms, 0) DESC
    LIMIT $limit
    """
    _EVENT_BY_ID_QUERY = """
    MATCH (e:Event {event_id: $event_id})
    OPTIONAL MATCH (e)-[:ABOUT]->(t:Topic)
    OPTIONAL MATCH (e)-[:AFFECTS]->(c:CapabilityHint)
    RETURN
      e.event_id AS event_id,
      coalesce(t.topic_id, e.topic_id, "") AS topic_id,
      coalesce(c.capability_hint, e.capability_hint, "") AS capability_hint,
      e.evidence_refs AS evidence_refs,
      e.payload AS payload,
      e.ts_ms AS ts_ms
    LIMIT 1
    """
    _USER_EVENTS_QUERY = """
    MATCH (:User {user_id: $user_id})-[:HAS_EVENT]->(e:Event)
    OPTIONAL MATCH (e)-[:ABOUT]->(t:Topic)
    OPTIONAL MATCH (e)-[:AFFECTS]->(c:CapabilityHint)
    RETURN
      e.event_id AS event_id,
      coalesce(t.topic_id, e.topic_id, "") AS topic_id,
      coalesce(c.capability_hint, e.capability_hint, "") AS capability_hint,
      e.evidence_refs AS evidence_refs,
      e.payload AS payload,
      e.ts_ms AS ts_ms
    ORDER BY coalesce(e.ts_ms, 0) ASC
    LIMIT $limit
    """
    _WRITE_EVENT_QUERY = """
    MERGE (u:User {user_id: $user_id})
    MERGE (s:Session {session_id: $session_id})
    MERGE (t:Topic {topic_id: $topic_id})
    MERGE (c:CapabilityHint {capability_hint: $capability_hint})
    MERGE (e:Event {event_id: $event_id})
    SET e.user_id = $user_id,
        e.session_id = $session_id,
        e.topic_id = $topic_id,
        e.capability_hint = $capability_hint,
        e.evidence_refs = $evidence_refs,
        e.payload = $payload,
        e.ts_ms = $ts_ms
    MERGE (u)-[:HAS_EVENT]->(e)
    MERGE (s)-[:HAS_EVENT]->(e)
    MERGE (e)-[:ABOUT]->(t)
    MERGE (e)-[:AFFECTS]->(c)
    RETURN
      e.event_id AS event_id,
      e.topic_id AS topic_id,
      e.capability_hint AS capability_hint,
      e.evidence_refs AS evidence_refs,
      e.payload AS payload,
      e.ts_ms AS ts_ms
    """

    def __init__(
        self,
        *,
        uri: str,
        database: str,
        auth_value: str,
        pool_size: int,
        tls_enabled: bool,
        fetch_limit: int = 256,
        constraint_assets: list[str | Path] | None = None,
    ) -> None:
        username, password = _parse_basic_auth(auth_value)
        try:
            from neo4j import GraphDatabase  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(f"neo4j driver unavailable: {exc}") from exc

        self._database = str(database).strip() or "neo4j"
        self._fetch_limit = max(1, int(fetch_limit))
        asset_paths = list(
            constraint_assets
            or [Path(__file__).parent / "migrations" / "0001_bond_constraints.cypher"]
        )
        self._constraint_statements = _load_constraint_statements(asset_paths)
        self._driver = GraphDatabase.driver(
            str(uri).strip(),
            auth=(username, password),
            max_connection_pool_size=max(1, int(pool_size)),
            encrypted=bool(tls_enabled),
        )

    @property
    def constraint_statements(self) -> list[str]:
        return list(self._constraint_statements)

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.fetch_events(limit=self._fetch_limit)

    def fetch_events(self, *, limit: int) -> list[dict[str, Any]]:
        row_limit = max(1, int(limit))
        rows: list[dict[str, Any]] = []
        with self._driver.session(database=self._database) as session:
            result = session.run(self._EVENTS_QUERY, limit=row_limit)
            for record in result:
                payload = record.data() if hasattr(record, "data") else dict(record)
                normalized = _normalize_bond_event_row(payload)
                if normalized is not None:
                    rows.append(normalized)
        return rows

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
        params = {
            "event_id": str(event_id).strip(),
            "user_id": str(user_id).strip(),
            "session_id": str(session_id).strip(),
            "topic_id": str(topic_id).strip(),
            "capability_hint": str(capability_hint).strip(),
            "evidence_refs": list(normalized_refs),
            "payload": json.dumps(dict(payload or {}), ensure_ascii=False),
            "ts_ms": int(ts_ms),
        }
        with self._driver.session(database=self._database) as session:
            record = session.run(self._WRITE_EVENT_QUERY, **params).single()
        row = record.data() if hasattr(record, "data") else dict(record or {})
        normalized = _normalize_bond_event_row(row)
        if normalized is None:
            raise RuntimeError("neo4j write_bond_event returned empty row")
        return {
            **normalized,
            "user_id": params["user_id"],
            "session_id": params["session_id"],
        }

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._driver.session(database=self._database) as session:
            record = session.run(self._EVENT_BY_ID_QUERY, event_id=str(event_id).strip()).single()
        if record is None:
            return None
        row = record.data() if hasattr(record, "data") else dict(record)
        return _normalize_bond_event_row(row)

    def trace_user_events(self, user_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self._driver.session(database=self._database) as session:
            result = session.run(self._USER_EVENTS_QUERY, user_id=str(user_id).strip(), limit=self._fetch_limit)
            for record in result:
                payload = record.data() if hasattr(record, "data") else dict(record)
                normalized = _normalize_bond_event_row(payload)
                if normalized is not None:
                    rows.append(normalized)
        return rows

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass


def resolve_neo4j_auth_value(
    *,
    auth_ref: str | None,
    secret_store: Mapping[str, Any] | None = None,
) -> str:
    ref = str(auth_ref or "").strip()
    if not ref:
        return ""
    if not ref.startswith("sec://"):
        return ref
    store = dict(secret_store or {})
    if not store:
        store = _load_json_file(_pick_existing_path(DEFAULT_SECRET_STORE, DEFAULT_SECRET_STORE_FALLBACK))
    return str(store.get(ref) or "").strip()


def build_neo4j_bond_client(
    *,
    config: Neo4jRuntimeConfig,
    secret_store: Mapping[str, Any] | None = None,
    fetch_limit: int = 256,
) -> Neo4jBondClient | None:
    if not config.enabled:
        return None
    auth_value = resolve_neo4j_auth_value(auth_ref=config.auth_ref, secret_store=secret_store)
    if not auth_value:
        raise RuntimeError("neo4j auth unresolved from BOND_NEO4J_AUTH_REF")
    return Neo4jBondClient(
        uri=config.uri,
        database=config.database,
        auth_value=auth_value,
        pool_size=config.pool_size,
        tls_enabled=config.tls_enabled,
        fetch_limit=fetch_limit,
    )


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


def _parse_basic_auth(value: str) -> tuple[str, str]:
    username, sep, password = str(value or "").partition(":")
    if not sep or not username.strip() or not password:
        raise ValueError("neo4j auth must be 'username:password'")
    return username.strip(), password


def _normalize_bond_event_row(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    event_id = str(raw.get("event_id") or "").strip()
    if not event_id:
        return None
    payload_raw = raw.get("payload")
    payload: dict[str, Any] = {}
    if isinstance(payload_raw, Mapping):
        payload = dict(payload_raw)
    elif isinstance(payload_raw, str):
        text = payload_raw.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, Mapping):
                payload = dict(parsed)
    return {
        "event_id": event_id,
        "topic_id": str(raw.get("topic_id") or "").strip() or None,
        "capability_hint": str(raw.get("capability_hint") or "").strip() or None,
        "evidence_refs": _normalize_string_list(raw.get("evidence_refs")),
        "payload": payload,
        "ts_ms": _coerce_non_negative_int(raw.get("ts_ms"), default=0),
    }


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[str] = []
    for item in value:
        cleaned = str(item or "").strip()
        if cleaned:
            rows.append(cleaned)
    return rows


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)
    return max(0, int(parsed))


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
        text = file_path.read_text(encoding="utf-8-sig")
        for raw in text.split(";"):
            line = raw.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("CREATE CONSTRAINT") or upper.startswith("CREATE INDEX"):
                statements.append(line + ";")
    return statements


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    if primary.exists() and primary.is_file():
        return primary
    return fallback


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


# ---------------------------------------------------------------------------
# 修复审计问题 #10：SqliteBondClient —— 持久化替代 InMemoryNeo4jBondClient
# ---------------------------------------------------------------------------

class SqliteBondClient:
    """Bond fallback facade backed by the catalog-owned repository.

    当 Neo4j 不可用时，作为 InMemoryNeo4jBondClient 的持久化替代方案。
    持久化连接、事务和 schema 所有权均位于 persistence adapter。

    环境变量：
        HOST_BOND_SQLITE_PATH: SQLite 文件路径，默认 data/bond_events.db
    """

    DEFAULT_DB_PATH = Path("data/bond_events.db")

    def __init__(self, db_path: Path | str | None = None) -> None:
        raw_path = str(
            db_path
            or os.environ.get("HOST_BOND_SQLITE_PATH")
            or self.DEFAULT_DB_PATH
        ).strip()
        self._db_path: str | Path = (
            raw_path if raw_path == ":memory:" else Path(raw_path).expanduser().resolve()
        )
        self._repository = BondFallbackRepository(self._db_path)

    @property
    def constraint_statements(self) -> list[str]:
        return []

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._repository.list_events()

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
        row: dict[str, Any] = {
            "event_id": str(event_id).strip(),
            "user_id": str(user_id).strip(),
            "session_id": str(session_id).strip(),
            "topic_id": str(topic_id).strip(),
            "capability_hint": str(capability_hint).strip(),
            "evidence_refs": list(normalized_refs),
            "payload": dict(payload or {}),
            "ts_ms": int(ts_ms),
        }
        if any(
            not str(row[field]).strip()
            for field in (
                "event_id",
                "user_id",
                "session_id",
                "topic_id",
                "capability_hint",
            )
        ):
            raise ValueError(
                "bond event requires non-empty "
                "event_id/user_id/session_id/topic_id/capability_hint"
            )
        return self._repository.write_event(row)

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        event_key = str(event_id).strip()
        if not event_key:
            return None
        return self._repository.get_event(event_key)

    def trace_user_events(self, user_id: str) -> list[dict[str, Any]]:
        user_key = str(user_id).strip()
        if not user_key:
            return []
        return self._repository.trace_user_events(user_key)

    def close(self) -> None:
        self._repository.close()
