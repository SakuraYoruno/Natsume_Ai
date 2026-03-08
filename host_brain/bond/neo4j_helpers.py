"""Neo4j client helper utilities (extracted from neo4j_client.py)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from host_brain.core.errors import CONFIG_VALUE_INVALID
from host_brain.bond.neo4j_schemas import Neo4jConfigIssue


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
        text = file_path.read_text(encoding="utf-8")
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
