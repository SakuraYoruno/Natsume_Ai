"""App-local ownership for Host governance and capability-discovery runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from threading import RLock
from typing import Any

from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tokens import TokenService
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.capability_scorecard_provider import (
    FederatedCapabilityScorecardProvider,
)
from host_brain.skills.mcp_skill_bridge import (
    register_mcp_tools_into_skill_registry,
)
from host_brain.skills.provider_catalog import (
    BUILTIN_SKILL_PROVIDER_SPECS,
    register_configured_skill_providers,
)
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.catalog import ToolCatalog


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]
SettingsProvider = Callable[[], "HostGovernanceRuntimeSettings"]

_logger = logging.getLogger("Natsume.host_governance_runtime")


def _build_intent_decision_engine(**kwargs: Any) -> IntentDecisionEngine:
    """Keep the production constructor visible to the architecture gate."""

    return IntentDecisionEngine(**kwargs)


@dataclass(frozen=True, slots=True)
class HostGovernanceRuntimeSettings:
    mcp_registry_db_path: str
    mcp_bridge_enabled: bool
    skill_provider_flags: tuple[tuple[str, bool], ...]

    def skill_provider_enabled(self, name: str, *, default: bool) -> bool:
        flags = dict(self.skill_provider_flags)
        return bool(flags.get(str(name), default))


class HostGovernanceRuntimeCloseError(RuntimeError):
    """Stable close failure that retains the exact runtime for retry."""

    reason_code = "HOST_GOVERNANCE_RUNTIME_CLOSE_RETRY_REQUIRED"

    def __init__(self, failures: tuple[str, ...]) -> None:
        self.failures = failures
        super().__init__(f"{self.reason_code}:{','.join(failures)}")


class HostGovernanceRuntimeOwner:
    """Own one exact governance/capability object graph per Host epoch."""

    def __init__(
        self,
        *,
        ledger_provider: ServiceProvider,
        persistence_db_path_provider: Callable[[], str],
        settings_provider: SettingsProvider | None = None,
        tool_catalog_factory: Factory = ToolCatalog,
        token_service_factory: Factory = TokenService,
        mcp_registry_factory: Factory = MCPToolRegistry,
        skill_registry_factory: Factory = build_builtin_skill_registry,
        skill_provider_registrar: Factory = register_configured_skill_providers,
        mcp_skill_registrar: Factory = register_mcp_tools_into_skill_registry,
        scorecard_provider_factory: Factory = FederatedCapabilityScorecardProvider,
        policy_cache_factory: Factory = GovernorPolicyCache,
        intent_engine_factory: Factory = _build_intent_decision_engine,
    ) -> None:
        self._ledger_provider = ledger_provider
        self._persistence_db_path_provider = persistence_db_path_provider
        self._settings_provider = settings_provider
        self._tool_catalog_factory = tool_catalog_factory
        self._token_service_factory = token_service_factory
        self._mcp_registry_factory = mcp_registry_factory
        self._skill_registry_factory = skill_registry_factory
        self._skill_provider_registrar = skill_provider_registrar
        self._mcp_skill_registrar = mcp_skill_registrar
        self._scorecard_provider_factory = scorecard_provider_factory
        self._policy_cache_factory = policy_cache_factory
        self._intent_engine_factory = intent_engine_factory
        self._lock = RLock()
        self._active = False
        self._closing = False
        self._epoch = 0
        self._settings: HostGovernanceRuntimeSettings | None = None
        self._tool_catalog: Any | None = None
        self._token_service: Any | None = None
        self._mcp_registry: Any | None = None
        self._skill_registry: Any | None = None
        self._scorecard_provider: Any | None = None
        self._intent_engine: Any | None = None

    def begin(self) -> None:
        with self._lock:
            if self._closing:
                raise RuntimeError(
                    "HOST_GOVERNANCE_RUNTIME_CLOSE_RETRY_REQUIRED"
                )
            if self._active:
                raise RuntimeError("HOST_GOVERNANCE_RUNTIME_ALREADY_ACTIVE")
            settings = (
                self._settings_provider()
                if self._settings_provider is not None
                else load_host_governance_runtime_settings(
                    persistence_db_path_provider=(
                        self._persistence_db_path_provider
                    ),
                )
            )
            if not isinstance(settings, HostGovernanceRuntimeSettings):
                raise TypeError(
                    "settings_provider must return "
                    "HostGovernanceRuntimeSettings"
                )
            if not str(settings.mcp_registry_db_path or "").strip():
                raise RuntimeError("MCP_REGISTRY_CANONICAL_DB_PATH_REQUIRED")
            self._settings = settings
            self._active = True
            self._epoch += 1

    def require_tool_catalog(self) -> Any:
        with self._lock:
            self._require_active()
            if self._tool_catalog is None:
                self._tool_catalog = self._tool_catalog_factory()
            return self._tool_catalog

    def require_token_service(self) -> Any:
        with self._lock:
            self._require_active()
            if self._token_service is None:
                self._token_service = self._token_service_factory()
            return self._token_service

    def require_mcp_tool_registry(self) -> Any:
        with self._lock:
            self._require_active()
            if self._mcp_registry is None:
                settings = self._require_settings()
                self._mcp_registry = self._mcp_registry_factory(
                    db_path=settings.mcp_registry_db_path,
                    check_same_thread=False,
                )
            return self._mcp_registry

    def require_skill_registry(self) -> Any:
        with self._lock:
            self._require_active()
            if self._skill_registry is None:
                settings = self._require_settings()
                registry = self._skill_registry_factory()
                self._skill_provider_registrar(
                    registry,
                    parse_bool_env=(
                        lambda name, *, default: (
                            settings.skill_provider_enabled(
                                str(name),
                                default=bool(default),
                            )
                        )
                    ),
                    logger=_logger,
                )
                self._skill_registry = registry
            self._sync_mcp_skills(self._skill_registry)
            return self._skill_registry

    def require_capability_scorecard_provider(self) -> Any:
        with self._lock:
            self._require_active()
            if self._scorecard_provider is None:
                self._scorecard_provider = self._scorecard_provider_factory(
                    skill_registry=self.require_skill_registry(),
                    tool_catalog=self.require_tool_catalog(),
                    mcp_registry=self.require_mcp_tool_registry(),
                    pentest_get=_get_pentest_tool,
                    pentest_all=_all_pentest_tools,
                )
            return self._scorecard_provider

    def require_intent_engine(self) -> Any:
        with self._lock:
            self._require_active()
            if self._intent_engine is None:
                self._intent_engine = self._intent_engine_factory(
                    ledger=self._ledger_provider(),
                    token_service=self.require_token_service(),
                    registry=self.require_skill_registry(),
                    policy_cache=self._policy_cache_factory(ttl_ms=60_000),
                    capability_maturity_enabled=True,
                    capability_scorecard_provider=(
                        self.require_capability_scorecard_provider()
                    ),
                )
            return self._intent_engine

    def close(self) -> None:
        with self._lock:
            if not self._active and not self._closing:
                return
            self._closing = True
            registry = self._mcp_registry

        failures: list[str] = []
        if registry is not None:
            close_registry = getattr(registry, "close", None)
            if callable(close_registry):
                try:
                    close_registry()
                except Exception:
                    failures.append("MCP_TOOL_REGISTRY_CLOSE_FAILED")

        with self._lock:
            if failures:
                raise HostGovernanceRuntimeCloseError(tuple(failures))
            self._intent_engine = None
            self._scorecard_provider = None
            self._skill_registry = None
            self._mcp_registry = None
            self._token_service = None
            self._tool_catalog = None
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

    def _sync_mcp_skills(self, skill_registry: SkillRegistry) -> None:
        settings = self._require_settings()
        if not settings.mcp_bridge_enabled:
            for skill_name in tuple(skill_registry.list_skill_names()):
                if str(skill_name).startswith("mcp::"):
                    skill_registry.unregister(skill_name)
            return
        try:
            self._mcp_skill_registrar(
                skill_registry=skill_registry,
                mcp_registry=self.require_mcp_tool_registry(),
                broker=None,
            )
        except Exception as exc:
            _logger.warning("mcp skill bridge sync skipped: %s", exc)

    def _require_active(self) -> None:
        if self._closing:
            raise RuntimeError(
                "HOST_GOVERNANCE_RUNTIME_CLOSE_RETRY_REQUIRED"
            )
        if not self._active:
            raise RuntimeError("HOST_GOVERNANCE_RUNTIME_NOT_ACTIVE")

    def _require_settings(self) -> HostGovernanceRuntimeSettings:
        settings = self._settings
        if settings is None:
            raise RuntimeError("HOST_GOVERNANCE_RUNTIME_NOT_ACTIVE")
        return settings


def load_host_governance_runtime_settings(
    *,
    persistence_db_path_provider: Callable[[], str],
    env: Mapping[str, Any] | None = None,
) -> HostGovernanceRuntimeSettings:
    db_path = str(persistence_db_path_provider() or "").strip()
    if not db_path:
        raise RuntimeError("MCP_REGISTRY_CANONICAL_DB_PATH_REQUIRED")
    source = dict(os.environ if env is None else env)
    provider_flags = tuple(
        (
            spec.env_flag,
            _parse_bool(
                source.get(spec.env_flag),
                default=bool(spec.default_enabled),
            ),
        )
        for spec in BUILTIN_SKILL_PROVIDER_SPECS
    )
    return HostGovernanceRuntimeSettings(
        mcp_registry_db_path=db_path,
        mcp_bridge_enabled=_parse_bool(
            source.get("HOST_SKILL_MCP_BRIDGE_ENABLED"),
            default=False,
        ),
        skill_provider_flags=provider_flags,
    )


def _get_pentest_tool(tool_id: str) -> Any:
    from host_brain.pentest.agent.tool_registry import get

    return get(tool_id)


def _all_pentest_tools() -> Any:
    from host_brain.pentest.agent.tool_registry import all_descriptors

    return all_descriptors()


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
    "HostGovernanceRuntimeCloseError",
    "HostGovernanceRuntimeOwner",
    "HostGovernanceRuntimeSettings",
    "load_host_governance_runtime_settings",
]
