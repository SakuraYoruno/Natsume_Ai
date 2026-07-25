"""App-local ownership for Host durable and execution authority resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from threading import RLock
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.journal import (
    SQLiteCapabilityExecutionJournal,
)
from host_brain.capability_plane.production_composition import (
    build_production_capability_runtime,
)
from host_brain.memory.chroma_client import (
    PersistentChromaClient,
    embed_text,
    load_chroma_runtime_config,
)
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.life_memory.store import LifeMemoryStore
from host_brain.model_runtime.production_composition import (
    build_app_local_production_model_runtime,
)


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]


def _build_host_model_runtime(**kwargs: Any) -> Any:
    """Keep the canonical production constructor mechanically auditable."""

    return build_app_local_production_model_runtime(**kwargs)


def _build_host_capability_runtime(**kwargs: Any) -> Any:
    """Keep the canonical production constructor mechanically auditable."""

    return build_production_capability_runtime(**kwargs)


@dataclass(frozen=True, slots=True)
class HostAuthorityRuntimeSettings:
    persistence_db_path: str


class HostAuthorityRuntimeCloseError(RuntimeError):
    """Stable close failure that retains unfinished resources for retry."""

    reason_code = "HOST_AUTHORITY_RUNTIME_CLOSE_RETRY_REQUIRED"

    def __init__(self, failures: tuple[str, ...]) -> None:
        self.failures = failures
        super().__init__(f"{self.reason_code}:{','.join(failures)}")


class HostAuthorityRuntimeOwner:
    """Own one durable/model/capability object graph per Host epoch."""

    def __init__(
        self,
        *,
        persistence_db_path_provider: Callable[[], str],
        model_credential_resolver: Callable[[str], str | None],
        model_base_url_resolver: Callable[[str], str | None],
        model_transport: Callable[..., Any],
        settings_provider: Callable[
            [], HostAuthorityRuntimeSettings
        ] | None = None,
        ledger_factory: Factory = SQLiteLedger,
        migration_runner_factory: Factory = SQLiteMigrationRunner,
        chroma_config_loader: Factory = load_chroma_runtime_config,
        chroma_client_factory: Factory = (
            PersistentChromaClient.from_runtime_config
        ),
        episodic_store_factory: Factory = EpisodicStore,
        life_memory_store_factory: Factory = LifeMemoryStore,
        model_runtime_factory: Factory = _build_host_model_runtime,
        capability_journal_factory: Factory = (
            SQLiteCapabilityExecutionJournal
        ),
        capability_runtime_factory: Factory = _build_host_capability_runtime,
    ) -> None:
        self._persistence_db_path_provider = persistence_db_path_provider
        self._model_credential_resolver = model_credential_resolver
        self._model_base_url_resolver = model_base_url_resolver
        self._model_transport = model_transport
        self._settings_provider = settings_provider
        self._ledger_factory = ledger_factory
        self._migration_runner_factory = migration_runner_factory
        self._chroma_config_loader = chroma_config_loader
        self._chroma_client_factory = chroma_client_factory
        self._episodic_store_factory = episodic_store_factory
        self._life_memory_store_factory = life_memory_store_factory
        self._model_runtime_factory = model_runtime_factory
        self._capability_journal_factory = capability_journal_factory
        self._capability_runtime_factory = capability_runtime_factory
        self._lock = RLock()
        self._close_lock = RLock()
        self._active = False
        self._closing = False
        self._epoch = 0
        self._settings: HostAuthorityRuntimeSettings | None = None
        self._chroma_config_result: Any | None = None
        self._ledger: Any | None = None
        self._episodic_store: Any | None = None
        self._life_memory_store: Any | None = None
        self._model_runtime: Any | None = None
        self._capability_runtime: Any | None = None
        self._closed_components: set[str] = set()

    def begin(self) -> None:
        with self._lock:
            if self._closing:
                raise RuntimeError(
                    "HOST_AUTHORITY_RUNTIME_CLOSE_RETRY_REQUIRED"
                )
            if self._active:
                raise RuntimeError("HOST_AUTHORITY_RUNTIME_ALREADY_ACTIVE")
            settings = (
                self._settings_provider()
                if self._settings_provider is not None
                else load_host_authority_runtime_settings(
                    persistence_db_path_provider=(
                        self._persistence_db_path_provider
                    )
                )
            )
            if not isinstance(settings, HostAuthorityRuntimeSettings):
                raise TypeError(
                    "settings_provider must return "
                    "HostAuthorityRuntimeSettings"
                )
            try:
                self._settings = settings
                self._chroma_config_result = self._chroma_config_loader(
                    env=os.environ
                )
                self._active = True
                self._closed_components.clear()
                self._ledger = self._build_ledger(settings)
            except BaseException:
                self._ledger = None
                self._settings = None
                self._chroma_config_result = None
                self._active = False
                raise
            self._epoch += 1

    def require_ledger(self) -> Any:
        with self._lock:
            self._require_active()
            if self._ledger is None:
                raise RuntimeError("HOST_AUTHORITY_RUNTIME_LEDGER_REQUIRED")
            return self._ledger

    def require_life_memory_store(self) -> Any:
        with self._lock:
            self._require_active()
            ledger = self.require_ledger()
            if (
                self._life_memory_store is None
                or getattr(self._life_memory_store, "ledger", None) is not ledger
            ):
                self._life_memory_store = self._life_memory_store_factory(
                    ledger=ledger
                )
            return self._life_memory_store

    def require_episodic_store(self) -> Any:
        with self._lock:
            self._require_active()
            if self._episodic_store is None:
                config_result = self._chroma_config_result
                if (
                    config_result is None
                    or not bool(getattr(config_result, "success", False))
                    or getattr(config_result, "config", None) is None
                ):
                    detail = (
                        config_result.as_dict()
                        if config_result is not None
                        and callable(
                            getattr(config_result, "as_dict", None)
                        )
                        else config_result
                    )
                    raise RuntimeError(
                        "episodic chroma runtime config invalid: "
                        f"{detail}"
                    )
                chroma_client = self._chroma_client_factory(
                    config_result.config
                )
                self._episodic_store = self._episodic_store_factory(
                    ledger=self.require_ledger(),
                    chroma_client=chroma_client,
                    embedding_fn=_embed_episodic_text,
                    embedding_model_id_hint=(
                        "memory.chroma_client.embed_text"
                    ),
                    embedding_provider_hint="memory_chroma",
                )
            return self._episodic_store

    def require_model_runtime(self) -> Any:
        with self._lock:
            self._require_active()
            if self._model_runtime is None:
                self._model_runtime = self._model_runtime_factory(
                    ledger=self.require_ledger(),
                    credential_resolver=self._model_credential_resolver,
                    base_url_resolver=self._model_base_url_resolver,
                    transport=self._model_transport,
                )
            return self._model_runtime

    def require_model_attempt_service(self) -> Any:
        return self.require_model_runtime().attempt_service

    def require_model_route_catalog(self) -> Any:
        catalog = self.require_model_runtime().route_catalog
        if catalog is None:
            raise RuntimeError("HOST_MODEL_ROUTE_CATALOG_REQUIRED")
        return catalog

    def require_prompt_registry(self) -> Any:
        return self.require_model_runtime().prompt_registry

    def require_capability_runtime(self) -> Any:
        with self._lock:
            self._require_active()
            if self._capability_runtime is None:
                journal = self._capability_journal_factory(
                    self.require_ledger()
                )
                self._capability_runtime = self._capability_runtime_factory(
                    execution_journal=journal
                )
            return self._capability_runtime

    def require_capability_broker(self) -> Any:
        return self.require_capability_runtime().broker

    def close(self) -> None:
        with self._close_lock:
            self._close_once()

    def _close_once(self) -> None:
        with self._lock:
            if not self._active and not self._closing:
                return
            self._closing = True
            capability_runtime = self._capability_runtime
            model_runtime = self._model_runtime
            episodic_store = self._episodic_store
            ledger = self._ledger

        failures: list[str] = []
        close_steps = (
            (
                "capability",
                "CAPABILITY_RUNTIME_CLOSE_FAILED",
                (
                    getattr(
                        getattr(capability_runtime, "broker", None),
                        "close",
                        None,
                    )
                    if capability_runtime is not None
                    else None
                ),
            ),
            (
                "model",
                "MODEL_RUNTIME_CLOSE_FAILED",
                (
                    getattr(
                        getattr(model_runtime, "attempt_service", None),
                        "clear_cache",
                        None,
                    )
                    if model_runtime is not None
                    else None
                ),
            ),
            (
                "episodic",
                "EPISODIC_RUNTIME_CLOSE_FAILED",
                (
                    getattr(
                        getattr(episodic_store, "chroma", None),
                        "close",
                        None,
                    )
                    if episodic_store is not None
                    else None
                ),
            ),
            (
                "ledger",
                "LEDGER_CLOSE_FAILED",
                getattr(ledger, "close", None),
            ),
        )
        for component, reason_code, closer in close_steps:
            if component in self._closed_components:
                continue
            if callable(closer):
                try:
                    closer()
                except BaseException:
                    failures.append(reason_code)
                    continue
            self._closed_components.add(component)

        with self._lock:
            if failures:
                raise HostAuthorityRuntimeCloseError(tuple(failures))
            self._capability_runtime = None
            self._model_runtime = None
            self._episodic_store = None
            self._life_memory_store = None
            self._ledger = None
            self._chroma_config_result = None
            self._settings = None
            self._closed_components.clear()
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

    def _build_ledger(
        self,
        settings: HostAuthorityRuntimeSettings,
    ) -> Any:
        db_path = str(settings.persistence_db_path or "").strip()
        if db_path == ":memory:":
            return self._ledger_factory(
                db_path=db_path,
                check_same_thread=False,
                life_audit_anchor_writer=self._write_life_audit_anchor,
            )
        migrated = self._migration_runner_factory(
            db_path=db_path
        ).apply_migrations(request_id="host-authority-runtime-startup")
        if not bool(getattr(migrated, "success", False)):
            raise RuntimeError(
                f"{getattr(migrated, 'reason_code', None) or 'MIGRATION_EXEC_FAILED'}: "
                f"{getattr(migrated, 'detail', '')}"
            )
        return self._ledger_factory(
            db_path=db_path,
            check_same_thread=False,
            initialize_schema=False,
            life_audit_anchor_writer=self._write_life_audit_anchor,
        )

    def _write_life_audit_anchor(self, **anchor: Any) -> None:
        self.require_life_memory_store().write_background_episode(**anchor)

    def _require_active(self) -> None:
        if self._closing:
            raise RuntimeError(
                "HOST_AUTHORITY_RUNTIME_CLOSE_RETRY_REQUIRED"
            )
        if not self._active:
            raise RuntimeError("HOST_AUTHORITY_RUNTIME_NOT_ACTIVE")


def load_host_authority_runtime_settings(
    *,
    persistence_db_path_provider: Callable[[], str],
) -> HostAuthorityRuntimeSettings:
    db_path = str(persistence_db_path_provider() or "").strip()
    if not db_path:
        raise RuntimeError("HOST_AUTHORITY_RUNTIME_DB_PATH_REQUIRED")
    return HostAuthorityRuntimeSettings(persistence_db_path=db_path)


def _embed_episodic_text(text: str) -> tuple[list[float], str]:
    return embed_text(str(text or "").strip())


__all__ = [
    "HostAuthorityRuntimeCloseError",
    "HostAuthorityRuntimeOwner",
    "HostAuthorityRuntimeSettings",
    "load_host_authority_runtime_settings",
]
