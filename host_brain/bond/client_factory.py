"""Canonical factories for Bond-compatible persistence clients.

The production Host only needs the shared Neo4j projection client.  The memory
and migrated SQLite factories remain available for maintenance and isolated
legacy projection tests; no legacy runtime owner is constructed here.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.bond.neo4j_client import (
    InMemoryNeo4jBondClient,
    SqliteBondClient,
    build_neo4j_bond_client,
    load_neo4j_runtime_config,
)


_logger = logging.getLogger("Natsume.bond_client_factory")


def build_host_bond_client(
    *,
    env: Mapping[str, Any] | None = None,
) -> Any:
    """Build the explicitly selected memory, SQLite, or Neo4j Bond client."""

    source = dict(os.environ if env is None else env)
    backend = str(source.get("HOST_BOND_BACKEND") or "").strip().lower()
    sqlite_path = str(source.get("HOST_BOND_SQLITE_PATH") or "").strip()

    if backend == "sqlite" or (sqlite_path and backend != "neo4j"):
        return _build_migrated_bond_sqlite_client(sqlite_path or None)
    if backend == "memory":
        return InMemoryNeo4jBondClient()

    config_result = load_neo4j_runtime_config(env=source)
    if config_result.success and config_result.config is not None:
        prefers_neo4j = backend == "neo4j" or (
            not backend and bool(config_result.config.enabled)
        )
        if prefers_neo4j:
            try:
                client = build_neo4j_bond_client(config=config_result.config)
                if client is not None:
                    return client
            except Exception:
                if backend == "neo4j" or bool(config_result.config.enabled):
                    raise

    if sqlite_path:
        return _build_migrated_bond_sqlite_client(sqlite_path)
    return InMemoryNeo4jBondClient()


def build_host_neo4j_projection_client(
    *,
    env: Mapping[str, Any] | None = None,
) -> Any | None:
    """Build the shared real Neo4j projection client when explicitly enabled."""

    source = dict(os.environ if env is None else env)
    config_result = load_neo4j_runtime_config(env=source)
    config = config_result.config if config_result.success else None
    if config is None or not bool(config.enabled):
        return None
    try:
        return build_neo4j_bond_client(config=config)
    except Exception:
        _logger.warning(
            "Neo4j 投影客户端不可用，图谱能力将安全降级",
            exc_info=True,
        )
        return None


def _build_migrated_bond_sqlite_client(
    db_path: str | os.PathLike[str] | None,
) -> SqliteBondClient:
    effective_path = str(
        db_path
        or os.environ.get("HOST_BOND_SQLITE_PATH")
        or SqliteBondClient.DEFAULT_DB_PATH
    ).strip()
    if effective_path != ":memory:":
        migrated = SQLiteMigrationRunner(db_path=effective_path).apply_migrations(
            request_id="bond-fallback-startup"
        )
        if not migrated.success:
            raise RuntimeError(
                f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: "
                f"{migrated.detail}"
            )
    return SqliteBondClient(db_path=effective_path)


__all__ = [
    "build_host_bond_client",
    "build_host_neo4j_projection_client",
]
