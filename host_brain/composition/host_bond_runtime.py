"""App-local ownership for Bond stage, writers, queue and backing client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
from threading import RLock
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.bond.bond_stage_engine import (
    DEFAULT_SCORE_MAP,
    DEFAULT_STAGE_THRESHOLDS,
)
from host_brain.bond.neo4j_client import (
    InMemoryNeo4jBondClient,
    SqliteBondClient,
    build_neo4j_bond_client,
    load_neo4j_runtime_config,
)


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]
SettingsProvider = Callable[[], "HostBondRuntimeSettings"]

_logger = logging.getLogger("Natsume.host_bond_runtime")


@dataclass(frozen=True, slots=True)
class HostBondRuntimeSettings:
    stage_thresholds: Mapping[str, int]
    score_map: Mapping[str, int]
    max_queue_size: int
    worker_enabled: bool


class HostBondRuntimeCloseError(RuntimeError):
    """Stable close failure that preserves the exact owner for retry."""

    reason_code = "HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED"

    def __init__(self, failures: tuple[str, ...]) -> None:
        self.failures = failures
        super().__init__(f"{self.reason_code}:{','.join(failures)}")


class HostBondRuntimeOwner:
    """Own one Bond runtime epoch for one Host application root."""

    def __init__(
        self,
        *,
        ledger_provider: ServiceProvider,
        stage_engine_factory: Factory,
        dynamics_writer_factory: Factory,
        update_queue_factory: Factory,
        client_factory: ServiceProvider,
        settings_provider: SettingsProvider = lambda: load_host_bond_runtime_settings(),
    ) -> None:
        self._ledger_provider = ledger_provider
        self._stage_engine_factory = stage_engine_factory
        self._dynamics_writer_factory = dynamics_writer_factory
        self._update_queue_factory = update_queue_factory
        self._client_factory = client_factory
        self._settings_provider = settings_provider
        self._lock = RLock()
        self._active = False
        self._closing = False
        self._epoch = 0
        self._settings: HostBondRuntimeSettings | None = None
        self._stage_engine: Any | None = None
        self._client: Any | None = None
        self._writer: Any | None = None
        self._queue: Any | None = None

    def begin(self) -> None:
        with self._lock:
            if self._closing:
                raise RuntimeError("HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED")
            if self._active:
                raise RuntimeError("HOST_BOND_RUNTIME_ALREADY_ACTIVE")
            settings = self._settings_provider()
            if not isinstance(settings, HostBondRuntimeSettings):
                raise TypeError(
                    "settings_provider must return HostBondRuntimeSettings"
                )
            self._settings = settings
            self._active = True
            self._epoch += 1

    def require_stage_engine(self) -> Any:
        with self._lock:
            self._require_active()
            if self._stage_engine is None:
                settings = self._require_settings()
                self._stage_engine = self._stage_engine_factory(
                    ledger=self._ledger_provider(),
                    stage_thresholds=dict(settings.stage_thresholds),
                    score_map=dict(settings.score_map),
                )
            return self._stage_engine

    def require_client(self) -> Any:
        with self._lock:
            self._require_active()
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    def require_writer(self) -> Any:
        with self._lock:
            self._require_active()
            if self._writer is None:
                self._writer = self._build_writer()
            return self._writer

    def require_update_queue(self) -> Any:
        with self._lock:
            self._require_active()
            if self._queue is None:
                settings = self._require_settings()
                self._queue = self._update_queue_factory(
                    writer_factory=self.require_writer,
                    max_queue_size=int(settings.max_queue_size),
                    worker_enabled=bool(settings.worker_enabled),
                )
            return self._queue

    def close(self, *, timeout_s: float = 3.0) -> None:
        with self._lock:
            if not self._active and not self._closing:
                return
            self._closing = True
            queue = self._queue
            client = self._client

        failures: list[str] = []
        if queue is not None:
            try:
                drained = bool(queue.drain(timeout_s=float(timeout_s)))
                if not drained:
                    failures.append("BOND_QUEUE_DRAIN_TIMEOUT")
            except Exception:
                failures.append("BOND_QUEUE_DRAIN_FAILED")
            try:
                stopped = queue.shutdown(timeout_s=min(float(timeout_s), 1.0))
                if stopped is False:
                    failures.append("BOND_QUEUE_WORKER_STILL_ALIVE")
            except Exception:
                failures.append("BOND_QUEUE_SHUTDOWN_FAILED")

        if not failures and client is not None:
            close_client = getattr(client, "close", None)
            if callable(close_client):
                try:
                    close_client()
                except Exception:
                    failures.append("BOND_CLIENT_CLOSE_FAILED")

        with self._lock:
            if failures:
                raise HostBondRuntimeCloseError(tuple(failures))
            self._queue = None
            self._writer = None
            self._client = None
            self._stage_engine = None
            self._settings = None
            self._active = False
            self._closing = False

    @property
    def epoch(self) -> int:
        with self._lock:
            return int(self._epoch)

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._active and not self._closing)

    def _build_writer(self) -> Any:
        return self._dynamics_writer_factory(
            ledger=self._ledger_provider(),
            neo4j_client=self.require_client(),
            stage_engine=self.require_stage_engine(),
        )

    def _require_active(self) -> None:
        if self._closing:
            raise RuntimeError("HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED")
        if not self._active:
            raise RuntimeError("HOST_BOND_RUNTIME_NOT_ACTIVE")

    def _require_settings(self) -> HostBondRuntimeSettings:
        settings = self._settings
        if settings is None:
            raise RuntimeError("HOST_BOND_RUNTIME_NOT_ACTIVE")
        return settings


def load_host_bond_runtime_settings(
    *,
    env: Mapping[str, Any] | None = None,
) -> HostBondRuntimeSettings:
    source = dict(os.environ if env is None else env)
    stage_thresholds = dict(DEFAULT_STAGE_THRESHOLDS)
    score_map = dict(DEFAULT_SCORE_MAP)
    _apply_int_mapping_override(
        target=stage_thresholds,
        raw=source.get("HOST_BOND_STAGE_THRESHOLDS"),
        setting_name="HOST_BOND_STAGE_THRESHOLDS",
    )
    _apply_int_mapping_override(
        target=score_map,
        raw=source.get("HOST_BOND_SCORE_MAP"),
        setting_name="HOST_BOND_SCORE_MAP",
    )
    return HostBondRuntimeSettings(
        stage_thresholds=stage_thresholds,
        score_map=score_map,
        max_queue_size=_parse_bounded_int(
            source.get("HOST_BOND_QUEUE_MAX_SIZE"),
            default=1024,
            minimum=8,
            maximum=100_000,
        ),
        worker_enabled=_parse_bool(
            source.get("HOST_BOND_QUEUE_ENABLED"),
            default=True,
        ),
    )


def build_host_bond_client(
    *,
    env: Mapping[str, Any] | None = None,
) -> Any:
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


def _apply_int_mapping_override(
    *,
    target: dict[str, int],
    raw: Any,
    setting_name: str,
) -> None:
    text = str(raw or "").strip()
    if not text:
        return
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise TypeError("value must be a JSON object")
        target.update({str(key): int(value) for key, value in parsed.items()})
    except Exception as exc:
        _logger.warning("%s 解析失败，使用默认值: %s", setting_name, exc)


def _parse_bounded_int(
    raw: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _parse_bool(raw: Any, *, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return bool(default)
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


__all__ = [
    "HostBondRuntimeCloseError",
    "HostBondRuntimeOwner",
    "HostBondRuntimeSettings",
    "build_host_bond_client",
    "load_host_bond_runtime_settings",
]
