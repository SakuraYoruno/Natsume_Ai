"""Test-only bridge for suites that still patch retired process locators.

Production code must never import this module.  The root pytest configuration
installs it under the retired module names before test collection so legacy
tests can be migrated independently of the production deletion.
"""

from __future__ import annotations

import os
from threading import RLock
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.process_application_ports import (
    ProcessApplicationPorts,
)
from host_brain.core.edge_endpoint_config import (
    resolve_edge_base_url as get_edge_base_url,
    resolve_edge_base_urls as get_edge_base_urls,
)
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.life_memory.store import LifeMemoryStore


class ProcessServiceContainer:
    """Mutable registry retained solely for legacy test patches."""

    _PRESERVED = frozenset(
        {"application_ports", "model_runtime_lock", "capability_runtime_lock"}
    )

    def __init__(self) -> None:
        self.application_ports: ProcessApplicationPorts | None = None
        self.model_runtime_lock = RLock()
        self.capability_runtime_lock = RLock()
        self.clear_service_references()

    def clear_service_references(self) -> None:
        for name in tuple(vars(self)):
            if name not in self._PRESERVED:
                setattr(self, name, None)
        for name in (
            "ledger",
            "episodic_store",
            "life_memory_store",
            "model_runtime_composition",
            "capability_runtime_composition",
            "conversation_history",
            "intent_engine",
            "skill_registry",
            "obs_metrics_collector",
            "token_service",
            "mcp_tool_registry",
        ):
            setattr(self, name, None)


_CONTAINER = ProcessServiceContainer()


def install_application_ports(ports: ProcessApplicationPorts) -> None:
    if not isinstance(ports, ProcessApplicationPorts):
        raise TypeError("ports must be ProcessApplicationPorts")
    _CONTAINER.application_ports = ports


def require_application_ports() -> ProcessApplicationPorts:
    ports = _CONTAINER.application_ports
    if ports is None:
        raise RuntimeError("TEST_PROCESS_APPLICATION_PORTS_NOT_CONFIGURED")
    return ports


def get_process_service_container() -> ProcessServiceContainer:
    return _CONTAINER


def get_ledger() -> SQLiteLedger:
    if _CONTAINER.ledger is None:
        db_path = str(
            require_application_ports().persistence_db_path()
            or "host_brain/data/audit.sqlite3"
        ).strip()
        if db_path == ":memory:":
            _CONTAINER.ledger = SQLiteLedger(
                db_path=db_path,
                check_same_thread=False,
                life_audit_anchor_writer=_write_life_audit_anchor,
            )
        else:
            from host_brain.audit.migration_runner import SQLiteMigrationRunner

            migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
                request_id="test-process-compat-ledger-startup",
            )
            if not migrated.success:
                raise RuntimeError(
                    f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: "
                    f"{migrated.detail}"
                )
            _CONTAINER.ledger = SQLiteLedger(
                db_path=db_path,
                check_same_thread=False,
                initialize_schema=False,
                life_audit_anchor_writer=_write_life_audit_anchor,
            )
    return _CONTAINER.ledger


def _write_life_audit_anchor(**anchor: Any) -> None:
    get_life_memory_store().write_background_episode(**anchor)


def get_capability_execution_journal() -> Any:
    from host_brain.capability_plane.journal import (
        SQLiteCapabilityExecutionJournal,
    )

    return SQLiteCapabilityExecutionJournal(get_ledger())


def get_capability_runtime_composition() -> Any:
    with _CONTAINER.capability_runtime_lock:
        if _CONTAINER.capability_runtime_composition is None:
            from host_brain.capability_plane.production_composition import (
                build_production_capability_runtime,
            )

            _CONTAINER.capability_runtime_composition = (
                build_production_capability_runtime(
                    execution_journal=get_capability_execution_journal(),
                )
            )
        return _CONTAINER.capability_runtime_composition


def get_capability_broker() -> Any:
    return get_capability_runtime_composition().broker


def reset_capability_runtime_composition(replacement: Any | None = None) -> None:
    with _CONTAINER.capability_runtime_lock:
        current = _CONTAINER.capability_runtime_composition
        _CONTAINER.capability_runtime_composition = replacement
    if current is not None and current is not replacement:
        current.broker.close()


def get_model_runtime_composition() -> Any:
    with _CONTAINER.model_runtime_lock:
        if _CONTAINER.model_runtime_composition is None:
            from host_brain.model_runtime.production_composition import (
                build_default_production_model_runtime,
            )

            ports = require_application_ports()
            _CONTAINER.model_runtime_composition = (
                build_default_production_model_runtime(
                    ledger=get_ledger(),
                    credential_resolver=ports.model_credential_resolver,
                    base_url_resolver=ports.model_base_url_resolver,
                    transport=ports.model_transport,
                )
            )
        return _CONTAINER.model_runtime_composition


def get_model_attempt_service() -> Any:
    return get_model_runtime_composition().attempt_service


def get_model_route_catalog() -> Any:
    with _CONTAINER.model_runtime_lock:
        current = _CONTAINER.model_runtime_composition
    if current is not None and current.route_catalog is not None:
        return current.route_catalog
    from host_brain.model_runtime.production_composition import (
        get_default_production_route_catalog,
    )

    return get_default_production_route_catalog()


def get_prompt_registry() -> Any:
    with _CONTAINER.model_runtime_lock:
        current = _CONTAINER.model_runtime_composition
    if current is not None:
        return current.prompt_registry
    from host_brain.model_runtime.production_composition import (
        get_default_production_prompt_registry,
    )

    return get_default_production_prompt_registry()


def reset_model_runtime_composition(replacement: Any | None = None) -> None:
    with _CONTAINER.model_runtime_lock:
        current = _CONTAINER.model_runtime_composition
        _CONTAINER.model_runtime_composition = replacement
    if current is not None and current is not replacement:
        current.attempt_service.clear_cache()


def _resolve_episodic_embedding_binding() -> tuple[Any, str, str]:
    from host_brain.memory.chroma_client import embed_text

    def _embed(text: str) -> tuple[list[float], str]:
        return embed_text(str(text or "").strip())

    return _embed, "memory.chroma_client.embed_text", "memory_chroma"


def _build_episodic_embedding_callable() -> Any:
    embedder, _model_id, _provider = _resolve_episodic_embedding_binding()
    return embedder


def get_episodic_store() -> EpisodicStore:
    if _CONTAINER.episodic_store is None:
        from host_brain.memory.chroma_client import (
            PersistentChromaClient,
            load_chroma_runtime_config,
        )

        config_result = load_chroma_runtime_config(env=os.environ)
        if not config_result.success or config_result.config is None:
            raise RuntimeError(
                "episodic chroma runtime config invalid: "
                f"{config_result.as_dict()}"
            )
        embedder, model_id, provider = _resolve_episodic_embedding_binding()
        _CONTAINER.episodic_store = EpisodicStore(
            ledger=get_ledger(),
            chroma_client=PersistentChromaClient.from_runtime_config(
                config_result.config
            ),
            embedding_fn=embedder,
            embedding_model_id_hint=model_id,
            embedding_provider_hint=provider,
        )
    return _CONTAINER.episodic_store


def get_life_memory_store() -> LifeMemoryStore:
    ledger = get_ledger()
    current = _CONTAINER.life_memory_store
    if current is None or getattr(current, "ledger", None) is not ledger:
        _CONTAINER.life_memory_store = LifeMemoryStore(ledger=ledger)
    return _CONTAINER.life_memory_store


def get_life_snapshot_reader() -> Any:
    from host_brain.composition.life import PROCESS_LIFE_PROVIDER

    return PROCESS_LIFE_PROVIDER.require().snapshot_reader


def get_life_command_service() -> Any:
    from host_brain.composition.life import PROCESS_LIFE_PROVIDER

    return PROCESS_LIFE_PROVIDER.require().command_service


def get_resource_manager(*, create_if_missing: bool = False) -> Any:
    del create_if_missing
    return None


def get_activity_monitor(*, create_if_missing: bool = False, ledger: Any = None) -> Any:
    del create_if_missing, ledger
    return None


def notify_user_interaction() -> None:
    monitor = get_activity_monitor()
    if monitor is not None:
        monitor.notify_user_interaction()


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def probe_edge_rpc_health(*args: Any, **kwargs: Any) -> Any:
    return require_application_ports().probe_edge_rpc_health(*args, **kwargs)


def build_edge_dependency_probe_statuses(*args: Any, **kwargs: Any) -> Any:
    return require_application_ports().build_edge_dependency_probe_statuses(
        *args,
        **kwargs,
    )


async def collect_dependency_probes(*args: Any, **kwargs: Any) -> Any:
    return await require_application_ports().collect_dependency_probes(
        *args,
        **kwargs,
    )


def collect_dependency_probes_sync(*args: Any, **kwargs: Any) -> Any:
    return require_application_ports().collect_dependency_probes_sync(
        *args,
        **kwargs,
    )


__all__ = [
    "ProcessServiceContainer",
    "build_edge_dependency_probe_statuses",
    "collect_dependency_probes",
    "collect_dependency_probes_sync",
    "get_activity_monitor",
    "get_capability_broker",
    "get_capability_execution_journal",
    "get_capability_runtime_composition",
    "get_edge_base_url",
    "get_edge_base_urls",
    "get_episodic_store",
    "get_ledger",
    "get_life_command_service",
    "get_life_memory_store",
    "get_life_snapshot_reader",
    "get_model_attempt_service",
    "get_model_route_catalog",
    "get_model_runtime_composition",
    "get_process_service_container",
    "get_prompt_registry",
    "get_resource_manager",
    "install_application_ports",
    "notify_user_interaction",
    "probe_edge_rpc_health",
    "reset_capability_runtime_composition",
    "reset_model_runtime_composition",
]
