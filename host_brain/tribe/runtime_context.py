"""Execution-local compatibility view of one app-owned Tribe runtime.

Legacy Tribe modules still call narrow module helpers for shared state,
registry, event bus and cross-Agent lookup.  During an active Host lifespan
those helpers must resolve the exact objects and Agent catalog owned by that
Host, never lazily create a second process runtime.  A ``ContextVar`` binds the
view to the current request/background execution so independent Host apps may
run concurrently without publishing a process-global active owner.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import RLock
from typing import Any


TribeAgentProvider = Callable[[], Any]
TribeAgentProviders = tuple[tuple[str, TribeAgentProvider], ...]
TribeAgents = tuple[tuple[str, Any], ...]


class TribeAgentCatalog:
    """Lazily own one fixed Agent graph for one Host scope."""

    def __init__(self, providers: TribeAgentProviders) -> None:
        self._providers = _validate_agent_providers(providers)
        self._provider_by_name = dict(self._providers)
        self._instances: dict[str, Any] = {}
        self._resolving: set[str] = set()
        self._lock = RLock()

    @property
    def providers(self) -> TribeAgentProviders:
        return self._providers

    def resolve(self, agent_name: str) -> Any:
        name = str(agent_name or "").strip()
        if not name:
            raise TypeError("agent_name is required")
        with self._lock:
            existing = self._instances.get(name)
            if existing is not None:
                return existing
            provider = self._provider_by_name.get(name)
            if provider is None:
                raise RuntimeError(f"TRIBE_AGENT_PROVIDER_NOT_REGISTERED:{name}")
            if name in self._resolving:
                raise RuntimeError(f"TRIBE_AGENT_PROVIDER_CYCLE:{name}")
            self._resolving.add(name)
            try:
                instance = provider()
                if instance is None:
                    raise RuntimeError(
                        f"TRIBE_AGENT_PROVIDER_UNAVAILABLE:{name}"
                    )
                self._instances[name] = instance
                return instance
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"TRIBE_AGENT_PROVIDER_UNAVAILABLE:{name}"
                ) from exc
            finally:
                self._resolving.remove(name)

    def resolve_all(self) -> TribeAgents:
        return tuple(
            (name, self.resolve(name))
            for name, _provider in self._providers
        )


@dataclass(frozen=True, slots=True)
class TribeRuntimeContext:
    state_registry: Any
    tribe_registry: Any
    event_bus: Any
    agent_catalog: TribeAgentCatalog
    active_owner_provider: Callable[[], Any | None]

    def __post_init__(self) -> None:
        for field_name in (
            "state_registry",
            "tribe_registry",
            "event_bus",
            "agent_catalog",
        ):
            if getattr(self, field_name) is None:
                raise TypeError(f"{field_name} is required")
        if not isinstance(self.agent_catalog, TribeAgentCatalog):
            raise TypeError("agent_catalog must be TribeAgentCatalog")
        if not callable(self.active_owner_provider):
            raise TypeError("active_owner_provider must be callable")

    def assert_active_owner(self, owner: Any) -> None:
        active_owner = self.active_owner_provider()
        if active_owner is None:
            raise RuntimeError("TRIBE_RUNTIME_CONTEXT_STALE")
        if active_owner is not owner:
            raise RuntimeError("TRIBE_RUNTIME_CONTEXT_OWNER_MISMATCH")


@dataclass(frozen=True, slots=True)
class TribeRuntimeBinding:
    owner: Any
    context: TribeRuntimeContext


@dataclass(frozen=True, slots=True)
class TribeRuntimeActivation:
    binding: TribeRuntimeBinding
    token: Token[TribeRuntimeBinding | None] | None


_ACTIVE_BINDING: ContextVar[TribeRuntimeBinding | None] = ContextVar(
    "natsume_active_tribe_runtime_binding",
    default=None,
)


def activate_tribe_runtime_context(
    *,
    owner: Any,
    context: TribeRuntimeContext,
) -> TribeRuntimeActivation:
    """Activate one borrowed view in the current execution context."""

    if owner is None:
        raise TypeError("owner is required")
    if not isinstance(context, TribeRuntimeContext):
        raise TypeError("context must be TribeRuntimeContext")
    context.assert_active_owner(owner)
    current = _ACTIVE_BINDING.get()
    if current is not None:
        if current.owner is owner and current.context is context:
            return TribeRuntimeActivation(binding=current, token=None)
        raise RuntimeError("TRIBE_RUNTIME_CONTEXT_ALREADY_BOUND")
    binding = TribeRuntimeBinding(owner=owner, context=context)
    token = _ACTIVE_BINDING.set(binding)
    return TribeRuntimeActivation(binding=binding, token=token)


def deactivate_tribe_runtime_context(
    activation: TribeRuntimeActivation,
) -> bool:
    """Withdraw an activation only from the execution that created it."""

    if not isinstance(activation, TribeRuntimeActivation):
        raise TypeError("activation must be TribeRuntimeActivation")
    current = _ACTIVE_BINDING.get()
    if current is not activation.binding:
        raise RuntimeError("TRIBE_RUNTIME_CONTEXT_BINDING_DRIFT")
    current.context.assert_active_owner(current.owner)
    if activation.token is None:
        return False
    _ACTIVE_BINDING.reset(activation.token)
    return True


def active_tribe_runtime_context() -> TribeRuntimeContext | None:
    """Return this execution's borrowed view without global fallback."""

    binding = _ACTIVE_BINDING.get()
    if binding is None:
        return None
    binding.context.assert_active_owner(binding.owner)
    return binding.context


def require_tribe_runtime_context() -> TribeRuntimeContext:
    context = active_tribe_runtime_context()
    if context is None:
        raise RuntimeError("TRIBE_RUNTIME_CONTEXT_NOT_BOUND")
    return context


def require_active_tribe_agent(
    agent_name: str,
    *,
    expected_type: type[Any],
) -> Any:
    """Resolve one Agent from the exact execution-bound Host catalog."""

    context = require_tribe_runtime_context()
    agent = context.agent_catalog.resolve(agent_name)
    if not isinstance(agent, expected_type):
        raise RuntimeError(f"TRIBE_RUNTIME_AGENT_TYPE_DRIFT:{agent_name}")
    return agent


def assert_tribe_runtime_context_owner(
    *,
    owner: Any,
    context: TribeRuntimeContext,
) -> None:
    """Fail closed when execution state drifted from the active binding."""

    binding = _ACTIVE_BINDING.get()
    if binding is None or binding.owner is not owner:
        raise RuntimeError("TRIBE_RUNTIME_CONTEXT_OWNER_MISMATCH")
    if binding.context is not context:
        raise RuntimeError("TRIBE_RUNTIME_CONTEXT_BINDING_DRIFT")
    context.assert_active_owner(owner)


def _validate_agent_providers(value: Any) -> TribeAgentProviders:
    if not isinstance(value, tuple) or not value:
        raise TypeError("agent_providers must be a non-empty tuple")
    normalized: list[tuple[str, TribeAgentProvider]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(
                "agent_providers entries must be (name, provider) tuples"
            )
        name = str(item[0] or "").strip()
        provider = item[1]
        if not name or name in names:
            raise TypeError(
                "agent_providers names must be unique and non-empty"
            )
        if not callable(provider):
            raise TypeError(f"agent provider must be callable: {name}")
        names.add(name)
        normalized.append((name, provider))
    return tuple(normalized)


__all__ = [
    "TribeAgentCatalog",
    "TribeAgentProvider",
    "TribeAgentProviders",
    "TribeAgents",
    "TribeRuntimeActivation",
    "TribeRuntimeBinding",
    "TribeRuntimeContext",
    "activate_tribe_runtime_context",
    "active_tribe_runtime_context",
    "assert_tribe_runtime_context_owner",
    "deactivate_tribe_runtime_context",
    "require_active_tribe_agent",
    "require_tribe_runtime_context",
]
