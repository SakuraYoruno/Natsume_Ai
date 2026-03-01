"""Deterministic config snapshot export/diff helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from host_brain.core.ids import now_ms


S_CONFIG_SNAPSHOT = "S_CONFIG_SNAPSHOT"
CONFIG_SNAPSHOT_VERSION = "v1"
CONFIG_SNAPSHOT_DIGEST_PREFIX = "cfg_snap::sha256:"
CONFIG_SNAPSHOT_DIFF_PREFIX = "cfg_diff::sha256:"
REDACTED_PLACEHOLDER = "<redacted>"


def build_config_snapshot(
    *,
    component: str,
    env: str,
    values: Mapping[str, Any],
    override_sources: Mapping[str, str],
    redacted_keys: list[str] | tuple[str, ...],
    version: str = CONFIG_SNAPSHOT_VERSION,
) -> dict[str, Any]:
    normalized_values = _sorted_mapping(values)
    normalized_sources = _sorted_string_mapping(override_sources)
    normalized_redacted = sorted(str(key) for key in redacted_keys)

    payload = {
        "component": str(component),
        "env": str(env),
        "version": str(version),
        "values": normalized_values,
        "override_sources": normalized_sources,
        "redacted_keys": normalized_redacted,
    }
    payload["digest"] = CONFIG_SNAPSHOT_DIGEST_PREFIX + _sha256(payload)
    payload["stage"] = S_CONFIG_SNAPSHOT
    return payload


def diff_config_snapshots(
    *,
    before_snapshot: Mapping[str, Any],
    after_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    before_values = _sorted_mapping(before_snapshot.get("values") or {})
    after_values = _sorted_mapping(after_snapshot.get("values") or {})

    before_keys = set(before_values.keys())
    after_keys = set(after_values.keys())

    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed: list[dict[str, Any]] = []
    for key in sorted(before_keys & after_keys):
        before_value = before_values.get(key)
        after_value = after_values.get(key)
        if before_value == after_value:
            continue
        changed.append(
            {
                "key": str(key),
                "before": before_value,
                "after": after_value,
            }
        )

    payload = {
        "from_digest": str(before_snapshot.get("digest") or ""),
        "to_digest": str(after_snapshot.get("digest") or ""),
        "added": added,
        "removed": removed,
        "changed": changed,
        "version": CONFIG_SNAPSHOT_VERSION,
    }
    digest = CONFIG_SNAPSHOT_DIFF_PREFIX + _sha256(payload)
    return {
        "stage": S_CONFIG_SNAPSHOT,
        "status": "DIFF_READY",
        "reason_code": None,
        "version": CONFIG_SNAPSHOT_VERSION,
        "digest": digest,
        "from_digest": payload["from_digest"],
        "to_digest": payload["to_digest"],
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
        },
    }


def resolve_override_sources(
    *,
    field_to_env_key: Mapping[str, str],
    env_layer: Mapping[str, Any],
    local_overrides: Mapping[str, Any],
) -> dict[str, str]:
    source_map: dict[str, str] = {}
    for field in sorted(field_to_env_key.keys()):
        env_key = str(field_to_env_key[field])
        if env_key in local_overrides:
            source_map[field] = "local_overrides"
            continue
        if env_key in env_layer:
            source_map[field] = "env"
            continue
        source_map[field] = "default"
    return source_map


def collect_redacted_keys(
    *,
    secret_env_keys: set[str] | list[str] | tuple[str, ...],
    env_layer: Mapping[str, Any],
    local_overrides: Mapping[str, Any],
) -> list[str]:
    redacted: set[str] = set()
    for key in secret_env_keys:
        env_key = str(key)
        if env_key in local_overrides:
            if _has_secret(local_overrides.get(env_key)):
                redacted.add(env_key)
            continue
        if env_key in env_layer and _has_secret(env_layer.get(env_key)):
            redacted.add(env_key)
    return sorted(redacted)


def redact_secret(raw_value: Any) -> str | None:
    if not _has_secret(raw_value):
        return None
    return REDACTED_PLACEHOLDER


def build_config_snapshot_audit_row(
    *,
    request_id: str,
    snapshot: Mapping[str, Any],
    diff: Mapping[str, Any] | None = None,
    status: str = "SNAPSHOT_EXPORTED",
    reason_code: str | None = None,
    detail: str = "",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "component": str(snapshot.get("component") or ""),
        "env": str(snapshot.get("env") or ""),
        "version": str(snapshot.get("version") or CONFIG_SNAPSHOT_VERSION),
        "digest": str(snapshot.get("digest") or ""),
        "override_sources": dict(snapshot.get("override_sources") or {}),
        "redacted_keys": list(snapshot.get("redacted_keys") or []),
        "snapshot_json": dict(snapshot),
        "diff_json": (dict(diff) if diff is not None else None),
        "status": str(status),
        "reason_code": reason_code,
        "stage": S_CONFIG_SNAPSHOT,
        "detail": str(detail),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }


def _sha256(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sorted_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): payload[key] for key in sorted(payload.keys(), key=lambda key: str(key))}


def _sorted_string_mapping(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key): str(payload[key])
        for key in sorted(payload.keys(), key=lambda key: str(key))
    }


def _has_secret(raw_value: Any) -> bool:
    return bool(str(raw_value or "").strip())
