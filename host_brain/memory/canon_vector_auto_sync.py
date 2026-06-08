"""Best-effort auto sync for canon SSOT writes into Chroma projection."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Mapping

AUTO_SYNC_ENABLED_ENV = "CANON_VECTOR_AUTO_SYNC_ENABLED"
AUTO_SYNC_MODE_ENV = "CANON_VECTOR_AUTO_SYNC_MODE"
AUTO_SYNC_COLLECTION_ENV = "CANON_VECTOR_COLLECTION"
AUTO_SYNC_BACKEND_ENV = "CANON_VECTOR_BACKEND"
AUTO_SYNC_CHROMA_PATH_ENV = "CANON_VECTOR_CHROMA_PATH"
AUTO_SYNC_COLLECTION_DIM_ENV = "CANON_VECTOR_COLLECTION_DIM"
AUTO_SYNC_NODE_ID_ENV = "CANON_VECTOR_NODE_ID"
AUTO_SYNC_EMBED_TIMEOUT_MS_ENV = "CANON_VECTOR_EMBED_TIMEOUT_MS"

DEFAULT_AUTO_SYNC_ENABLED = False
DEFAULT_AUTO_SYNC_MODE = "async"
DEFAULT_AUTO_SYNC_EMBED_TIMEOUT_MS = 60_000
DEFAULT_AUTO_SYNC_COLLECTION = "canon"
DEFAULT_AUTO_SYNC_BACKEND = "http"
DEFAULT_AUTO_SYNC_CHROMA_PATH = "runtime/chroma/data"
DEFAULT_AUTO_SYNC_WORLD_ROOT = "host_brain/canon/world"

_logger = logging.getLogger("Natsume.canon_vector_auto_sync")
_ACTIVE_JOBS_LOCK = threading.Lock()
_ACTIVE_JOBS: set[str] = set()


def trigger_canon_vector_auto_sync(
    *,
    include_world: bool,
    world_root: str | Path = DEFAULT_AUTO_SYNC_WORLD_ROOT,
    reason: str = "",
    env: Mapping[str, Any] | None = None,
) -> None:
    """Auto-sync canon vectors after SSOT write.

    This is best-effort and never raises to the write path.
    """
    if not include_world:
        return

    env_values: dict[str, Any] = dict(env or os.environ)
    enabled = _parse_bool(
        env_values.get(AUTO_SYNC_ENABLED_ENV),
        default=DEFAULT_AUTO_SYNC_ENABLED,
    )
    if not enabled:
        return

    mode = str(env_values.get(AUTO_SYNC_MODE_ENV) or DEFAULT_AUTO_SYNC_MODE).strip().lower()
    if mode not in {"sync", "async"}:
        mode = DEFAULT_AUTO_SYNC_MODE

    collection = str(env_values.get(AUTO_SYNC_COLLECTION_ENV) or DEFAULT_AUTO_SYNC_COLLECTION).strip()
    if not collection:
        collection = DEFAULT_AUTO_SYNC_COLLECTION

    backend = str(env_values.get(AUTO_SYNC_BACKEND_ENV) or DEFAULT_AUTO_SYNC_BACKEND).strip().lower()
    if backend not in {"http", "persistent"}:
        backend = DEFAULT_AUTO_SYNC_BACKEND

    chroma_path = Path(str(env_values.get(AUTO_SYNC_CHROMA_PATH_ENV) or DEFAULT_AUTO_SYNC_CHROMA_PATH)).resolve()
    collection_dim = _coerce_positive_int(env_values.get(AUTO_SYNC_COLLECTION_DIM_ENV), default=0)
    resolved_collection_dim = int(collection_dim) if collection_dim > 0 else None
    node_id = str(env_values.get(AUTO_SYNC_NODE_ID_ENV) or env_values.get("HOST_NODE_ID") or "").strip() or None
    resolved_world_root = Path(world_root).resolve()
    local_overrides = _build_local_overrides(env_values)

    job_key = (
        f"{int(include_world)}:{resolved_world_root}:{collection}:{backend}"
    )
    if not _mark_job_active(job_key):
        return

    worker = threading.Thread(
        target=_run_auto_sync_job,
        kwargs={
            "job_key": job_key,
            "include_world": bool(include_world),
            "world_root": resolved_world_root,
            "collection": collection,
            "backend": backend,
            "chroma_path": chroma_path,
            "collection_dim": resolved_collection_dim,
            "node_id": node_id,
            "reason": str(reason or ""),
            "env_values": env_values,
            "local_overrides": local_overrides,
        },
        daemon=True,
        name="canon-vector-auto-sync",
    )
    if mode == "sync":
        _run_auto_sync_job(
            job_key=job_key,
            include_world=bool(include_world),
            world_root=resolved_world_root,
            collection=collection,
            backend=backend,
            chroma_path=chroma_path,
            collection_dim=resolved_collection_dim,
            node_id=node_id,
            reason=str(reason or ""),
            env_values=env_values,
            local_overrides=local_overrides,
        )
        return
    worker.start()


def _run_auto_sync_job(
    *,
    job_key: str,
    include_world: bool,
    world_root: Path,
    collection: str,
    backend: str,
    chroma_path: Path,
    collection_dim: int | None,
    node_id: str | None,
    reason: str,
    env_values: Mapping[str, Any],
    local_overrides: Mapping[str, Any],
) -> None:
    try:
        from host_brain.memory.canon_vector_projection import sync_canon_vector_index

        summary = sync_canon_vector_index(
            world_root=world_root,
            collection=collection,
            backend=backend,
            chroma_path=chroma_path,
            collection_dim=collection_dim,
            include_world=include_world,
            env=env_values,
            local_overrides=local_overrides,
            node_id=node_id,
        )
        _logger.info(
            "canon vector auto sync completed",
            extra={
                "reason": reason,
                "include_world": include_world,
                "docs_synced": int(summary.get("docs_synced") or 0),
                "issues_count": int(summary.get("issues_count") or 0),
            },
        )
    except Exception as exc:
        _logger.warning(
            "canon vector auto sync failed: %s",
            exc,
            extra={
                "reason": reason,
                "include_world": include_world,
                "world_root": str(world_root),
            },
        )
    finally:
        _mark_job_done(job_key)


def _build_local_overrides(env_values: Mapping[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    timeout_floor = _coerce_positive_int(
        env_values.get(AUTO_SYNC_EMBED_TIMEOUT_MS_ENV),
        default=DEFAULT_AUTO_SYNC_EMBED_TIMEOUT_MS,
    )
    if timeout_floor <= 0:
        return overrides

    route_override = _build_embed_route_timeout_override(
        routes_raw=env_values.get("RETR_EMBED_ROUTES_JSON"),
        timeout_floor_ms=timeout_floor,
    )
    if route_override:
        overrides["RETR_EMBED_ROUTES_JSON"] = route_override
        return overrides

    current_timeout = _coerce_positive_int(env_values.get("RETR_EMBED_TIMEOUT_MS"), default=0)
    if current_timeout <= 0 or current_timeout >= timeout_floor:
        return overrides
    overrides["RETR_EMBED_TIMEOUT_MS"] = str(timeout_floor)
    return overrides


def _build_embed_route_timeout_override(*, routes_raw: Any, timeout_floor_ms: int) -> str | None:
    raw_text = str(routes_raw or "").strip()
    if not raw_text:
        return None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    updated = False
    normalized: dict[str, Any] = {}
    for route_id, route_config in payload.items():
        if not isinstance(route_config, dict):
            normalized[str(route_id)] = route_config
            continue
        route_copy = dict(route_config)
        current_timeout = _coerce_positive_int(route_copy.get("timeout_ms"), default=0)
        if current_timeout < int(timeout_floor_ms):
            route_copy["timeout_ms"] = int(timeout_floor_ms)
            updated = True
        normalized[str(route_id)] = route_copy
    if not updated:
        return None
    return json.dumps(normalized, ensure_ascii=False)


def _parse_bool(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return bool(default)
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_positive_int(raw: Any, *, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return value if value > 0 else int(default)


def _mark_job_active(job_key: str) -> bool:
    with _ACTIVE_JOBS_LOCK:
        if job_key in _ACTIVE_JOBS:
            return False
        _ACTIVE_JOBS.add(job_key)
        return True


def _mark_job_done(job_key: str) -> None:
    with _ACTIVE_JOBS_LOCK:
        _ACTIVE_JOBS.discard(job_key)
