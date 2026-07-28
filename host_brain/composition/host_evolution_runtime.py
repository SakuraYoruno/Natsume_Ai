"""App-local ownership for NE-06 evolution and Knowledge read authority."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from threading import RLock
from typing import Any

from host_brain.evolution.knowledge_recaller import (
    KnowledgeOSSemanticRecaller,
)
from host_brain.evolution.version_activation import (
    EvolutionVersionActivationService,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)


ServiceProvider = Callable[[], Any]
RepositoryFactory = Callable[..., SQLiteEvolutionActivationV2Repository]
ServiceFactory = Callable[
    [SQLiteEvolutionActivationV2Repository],
    EvolutionVersionActivationService,
]


class HostEvolutionRuntimeOwner:
    """Own one evolution repository/service graph per Host application."""

    def __init__(
        self,
        *,
        persistence_db_path_provider: ServiceProvider,
        repository_factory: RepositoryFactory = (
            SQLiteEvolutionActivationV2Repository
        ),
        service_factory: ServiceFactory = (
            EvolutionVersionActivationService
        ),
        scope_provider: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        if not callable(persistence_db_path_provider):
            raise TypeError(
                "persistence_db_path_provider must be callable"
            )
        if not callable(repository_factory):
            raise TypeError("repository_factory must be callable")
        if not callable(service_factory):
            raise TypeError("service_factory must be callable")
        self._db_path_provider = persistence_db_path_provider
        self._repository_factory = repository_factory
        self._service_factory = service_factory
        self._scope_provider = scope_provider or _default_scope
        self._lock = RLock()
        self._repository: SQLiteEvolutionActivationV2Repository | None = None
        self._service: EvolutionVersionActivationService | None = None
        self._semantic_recaller: KnowledgeOSSemanticRecaller | None = None

    def require_repository(
        self,
    ) -> SQLiteEvolutionActivationV2Repository:
        with self._lock:
            if self._repository is None:
                raw = self._db_path_provider()
                path = Path(str(raw or "")).expanduser().resolve()
                self._repository = self._repository_factory(path)
            return self._repository

    def require_service(self) -> EvolutionVersionActivationService:
        with self._lock:
            if self._service is None:
                self._service = self._service_factory(
                    self.require_repository()
                )
            return self._service

    def require_scope(self) -> tuple[str, str]:
        workspace_id, owner_id = self._scope_provider()
        return (
            _scope_value(
                workspace_id,
                default="host-default",
                field="workspace_id",
            ),
            _scope_value(
                owner_id,
                default="natsume_ai",
                field="owner_id",
            ),
        )

    def decorate_semantic_recaller(self, legacy_recaller: Any) -> Any:
        with self._lock:
            if self._semantic_recaller is None:
                self._semantic_recaller = KnowledgeOSSemanticRecaller(
                    repository=self.require_repository(),
                    legacy_recaller=legacy_recaller,
                    scope_provider=self._scope_provider,
                    read_mode_provider=_knowledge_read_mode,
                )
            elif (
                getattr(
                    self._semantic_recaller,
                    "_legacy_recaller",
                    None,
                )
                is not legacy_recaller
            ):
                raise RuntimeError(
                    "HOST_EVOLUTION_SEMANTIC_RECALLER_IDENTITY_DRIFT"
                )
            return self._semantic_recaller


def _default_scope() -> tuple[str, str]:
    workspace_id = _scope_value(
        os.environ.get("HOST_KNOWLEDGE_WORKSPACE_ID"),
        default="host-default",
        field="workspace_id",
    )
    owner_id = _scope_value(
        os.environ.get("HOST_KNOWLEDGE_OWNER_ID"),
        default="natsume_ai",
        field="owner_id",
    )
    return workspace_id, owner_id


def _scope_value(value: Any, *, default: str, field: str) -> str:
    result = str(value or default).strip()
    if (
        not result
        or len(result) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in result)
    ):
        raise RuntimeError(
            f"HOST_EVOLUTION_{field.upper()}_INVALID"
        )
    return result


def _knowledge_read_mode() -> str:
    mode = str(
        os.environ.get("HOST_KNOWLEDGE_OS_READ_MODE")
        or "SHADOW"
    ).strip().upper()
    if mode not in {"SHADOW", "CANARY", "ENFORCE"}:
        raise RuntimeError("HOST_KNOWLEDGE_OS_READ_MODE_INVALID")
    return mode


__all__ = ["HostEvolutionRuntimeOwner"]
