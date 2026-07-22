"""App-local ownership for Host lifecycle services.

The composition root resolves process collaborators once, then publishes only
the services owned by one FastAPI application lifespan.  Request and worker
paths never have to rediscover ActivityMonitor or SystemResourceManager through
the mutable process service locator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateJournalPort,
)


ServiceProvider = Callable[[], Any]
KeywordFactory = Callable[..., Any]


class HostLifecycleScopeError(RuntimeError):
    """Raised when one application lifespan violates lifecycle ownership."""


@dataclass(frozen=True, slots=True)
class HostLifecycleApplicationPorts:
    """Immutable collaborators resolved by the Host composition root."""

    memory_guard: ServiceProvider
    state_arbiter: ServiceProvider
    distillation_engine: ServiceProvider
    semantic_recaller: ServiceProvider
    episodic_store: ServiceProvider
    skill_registry: ServiceProvider
    tool_catalog: ServiceProvider
    capability_broker: ServiceProvider
    model_attempt_service: ServiceProvider
    mode_transition_service_factory: KeywordFactory
    activity_monitor_factory: KeywordFactory
    resource_manager_factory: KeywordFactory
    ledger: ServiceProvider | None = None
    life_memory_store: ServiceProvider | None = None
    bond_update_queue: ServiceProvider | None = None
    life_snapshot_reader: ServiceProvider | None = None
    silence_engine: ServiceProvider | None = None
    state_write_arbiter: ServiceProvider | None = None
    emotion_timeline: ServiceProvider | None = None
    bond_stage_engine: ServiceProvider | None = None
    shared_memory_index: ServiceProvider | None = None
    milestone_tracker: ServiceProvider | None = None
    prompt_registry: ServiceProvider | None = None
    user_emotion_profile: ServiceProvider | None = None
    intent_engine: ServiceProvider | None = None
    multimodal_observer: ServiceProvider | None = None
    multimodal_processor: ServiceProvider | None = None
    conversation_history: ServiceProvider | None = None
    life_memory_service: ServiceProvider | None = None

    def require_provider(self, field_name: str) -> ServiceProvider:
        provider = getattr(self, str(field_name or ""), None)
        if not callable(provider):
            raise HostLifecycleScopeError(
                f"HOST_SERVICE_PROVIDER_NOT_INJECTED:{field_name}"
            )
        return provider


@dataclass(slots=True)
class HostLifecycleContainer:
    """Own ActivityMonitor and SystemResourceManager for one ASGI application.

    A candidate is retained before ``start()`` runs so a partial-start failure
    is still reclaimed.  It becomes visible to request paths only after start
    succeeds.  ``aclose`` clears publication before awaiting teardown and is
    exact-once for each ``begin`` epoch.
    """

    ports: HostLifecycleApplicationPorts
    initial_sys_mode: str = "normal"
    require_durable_hard_state: bool = False
    endocrine_resolver: KeywordFactory | None = field(default=None, repr=False)
    _epoch: int = field(default=0, init=False, repr=False)
    _open: bool = field(default=False, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)
    _mode_transition_service: Any | None = field(default=None, init=False, repr=False)
    _owned_activity_monitor: Any | None = field(default=None, init=False, repr=False)
    _activity_monitor: Any | None = field(default=None, init=False, repr=False)
    _owned_resource_manager: Any | None = field(default=None, init=False, repr=False)
    _resource_manager: Any | None = field(default=None, init=False, repr=False)
    _hard_state_owner: HostHardStateOwner = field(init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        bootstrap_kwargs: dict[str, Any] = {
            "initial_sys_mode": self.initial_sys_mode,
            "require_durable": self.require_durable_hard_state,
        }
        if self.endocrine_resolver is not None:
            bootstrap_kwargs["endocrine_resolver"] = self.endocrine_resolver
        self._hard_state_owner = HostHardStateOwner.bootstrap(**bootstrap_kwargs)

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def begin(self) -> int:
        """Open one lifespan epoch without inheriting a previous instance."""

        with self._lock:
            if self._open:
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_ALREADY_OPEN")
            if self._closing:
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_CLOSING")
            if any(
                service is not None
                for service in (
                    self._mode_transition_service,
                    self._owned_activity_monitor,
                    self._activity_monitor,
                    self._owned_resource_manager,
                    self._resource_manager,
                )
            ):
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_NOT_CLEAN")
            self._epoch += 1
            self._open = True
            return self._epoch

    def _require_open(self) -> None:
        if not self._open:
            raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_NOT_OPEN")

    def start_activity_monitor(self, *, ledger: Any) -> Any:
        """Construct, start, and publish this app's activity monitor."""

        with self._lock:
            self._require_open()
            if self._owned_activity_monitor is not None:
                raise HostLifecycleScopeError("ACTIVITY_MONITOR_ALREADY_OWNED")
            monitor: Any | None = None
            try:
                mode_service = self.ports.mode_transition_service_factory(
                    get_hard_state=self.get_hard_state,
                    set_sys_mode=self.set_sys_mode,
                    history_provider=self.get_sys_mode_history,
                    exact_history_provider=self.reopen_sys_mode_transition,
                    ledger=ledger,
                )
                monitor = self.ports.activity_monitor_factory(
                    mode_transition_service=mode_service,
                )
                self._owned_activity_monitor = monitor
                monitor.start()
            except Exception:
                self._owned_activity_monitor = None
                if monitor is not None:
                    try:
                        monitor.stop()
                    except Exception:
                        pass
                raise
            self._mode_transition_service = mode_service
            self._activity_monitor = monitor
            return monitor

    def start_resource_manager(
        self,
        *,
        degradation_state_provider: Callable[[], Mapping[str, Any] | None] | None,
        run_immediately: bool,
    ) -> Any:
        """Construct, start, and publish this app's resource manager."""

        with self._lock:
            self._require_open()
            if self._owned_resource_manager is not None:
                raise HostLifecycleScopeError("RESOURCE_MANAGER_ALREADY_OWNED")
            manager = self.ports.resource_manager_factory(
                model_attempt_service=self.ports.model_attempt_service(),
                degradation_state_provider=degradation_state_provider,
            )
            self._owned_resource_manager = manager
            try:
                manager.start(run_immediately=bool(run_immediately))
            except Exception:
                self._owned_resource_manager = None
                try:
                    manager.stop()
                except Exception:
                    pass
                raise
            self._resource_manager = manager
            return manager

    def get_activity_monitor(self, *args: Any, **kwargs: Any) -> Any | None:
        """Return only the successfully started app-local monitor."""

        del args, kwargs
        with self._lock:
            return self._activity_monitor

    def get_resource_manager(self, *args: Any, **kwargs: Any) -> Any | None:
        """Return only the successfully started app-local manager."""

        del args, kwargs
        with self._lock:
            return self._resource_manager

    def get_mode_transition_service(self) -> Any | None:
        with self._lock:
            return self._mode_transition_service

    def get_hard_state(self) -> Any:
        """Return this application's immutable HardState snapshot."""

        return self._hard_state_owner.get_hard_state()

    def set_sys_mode(
        self,
        target_mode: str,
        **metadata: Any,
    ) -> dict[str, Any]:
        """Atomically switch this application's live system mode."""

        return self._hard_state_owner.set_sys_mode(target_mode, **metadata)

    def bind_hard_state_journal(
        self, journal: HostHardStateJournalPort
    ) -> dict[str, object]:
        """Bind and recover the canonical HardState journal for this lifespan."""

        snapshot = self._hard_state_owner.bind_durable_journal(journal)
        return {
            "runtime_scope_id": snapshot.runtime_scope_id,
            "revision": snapshot.revision,
            "sys_mode": snapshot.sys_mode,
            "event_id": snapshot.event_id,
            "event_digest": snapshot.event_digest,
            "updated_at_ms": snapshot.updated_at_ms,
        }

    def get_sys_mode_history(self, *, limit: int = 100) -> list[dict[str, object]]:
        return self._hard_state_owner.durable_history(limit=limit)

    def reopen_sys_mode_transition(
        self,
        *,
        event_id: str,
        target_mode: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None:
        """Reopen an exact historical HardState commit through this app scope."""

        return self._hard_state_owner.reopen_sys_mode_transition(
            event_id=event_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
        )

    def get_hard_state_durability_status(self) -> dict[str, object]:
        return self._hard_state_owner.durability_status()

    def get_endocrine_state(
        self,
        *,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        """Resolve endocrine projection from this application's HardState."""

        return self._hard_state_owner.get_endocrine_state(
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )

    def notify_user_interaction(self) -> None:
        with self._lock:
            monitor = self._activity_monitor
            if monitor is not None:
                monitor.notify_user_interaction()

    def stop_activity_monitor(self, *, logger: Any) -> None:
        """Stop mode producers before the Host runtime fence is released."""

        with self._lock:
            monitor = self._owned_activity_monitor
            self._owned_activity_monitor = None
            self._activity_monitor = None
            self._mode_transition_service = None
        if monitor is not None:
            try:
                monitor.stop()
                logger.info("ActivityMonitor 已停止")
            except Exception as exc:
                logger.warning("ActivityMonitor 停止失败: %s", exc)
        self._hard_state_owner.unbind_durable_journal()

    async def aclose(self, *, logger: Any) -> None:
        """Unpublish and stop all services exactly once for the open epoch."""

        with self._lock:
            if not self._open:
                return
            self._open = False
            self._closing = True
            activity_monitor = self._owned_activity_monitor
            resource_manager = self._owned_resource_manager
            self._mode_transition_service = None
            self._owned_activity_monitor = None
            self._activity_monitor = None
            self._owned_resource_manager = None
            self._resource_manager = None
            self._hard_state_owner.unbind_durable_journal()
        try:
            try:
                if resource_manager is not None:
                    resource_manager.stop()
                    logger.info("SystemResourceManager 已停止")
            except Exception as exc:
                logger.warning("SystemResourceManager 停止失败: %s", exc)
            try:
                if activity_monitor is not None:
                    activity_monitor.stop()
                    logger.info("ActivityMonitor 已停止")
            except Exception as exc:
                logger.warning("ActivityMonitor 停止失败: %s", exc)
        finally:
            with self._lock:
                self._closing = False


def build_host_lifecycle_container() -> HostLifecycleContainer:
    """Resolve the production Host lifecycle graph once per ``create_app``."""

    from host_brain.composition import process_services
    from host_brain.app import emotion_context
    from host_brain.app.settings.core import get_core_settings
    from host_brain.core.activity_monitor import ActivityMonitor
    from host_brain.core.mode_transition import ModeTransitionService
    from host_brain.core.resource_manager import SystemResourceManager

    return HostLifecycleContainer(
        initial_sys_mode=get_core_settings().sys_mode,
        require_durable_hard_state=True,
        ports=HostLifecycleApplicationPorts(
            memory_guard=process_services.get_memory_guard,
            state_arbiter=process_services.get_state_arbiter,
            distillation_engine=process_services.get_distillation_engine,
            semantic_recaller=process_services.get_semantic_recaller,
            episodic_store=process_services.get_episodic_store,
            skill_registry=process_services.get_skill_registry,
            tool_catalog=process_services.get_tool_catalog,
            capability_broker=process_services.get_capability_broker,
            model_attempt_service=process_services.get_model_attempt_service,
            ledger=process_services.get_ledger,
            life_memory_store=process_services.get_life_memory_store,
            bond_update_queue=process_services.get_bond_update_queue,
            life_snapshot_reader=process_services.get_life_snapshot_reader,
            silence_engine=process_services.get_silence_engine,
            state_write_arbiter=process_services.get_state_write_arbiter,
            emotion_timeline=process_services.get_emotion_timeline,
            bond_stage_engine=process_services.get_bond_stage_engine,
            shared_memory_index=process_services.get_shared_memory_index,
            milestone_tracker=process_services.get_milestone_tracker,
            prompt_registry=process_services.get_prompt_registry,
            user_emotion_profile=emotion_context.get_user_emotion_profile,
            intent_engine=process_services.get_intent_engine,
            multimodal_observer=process_services.get_multimodal_observer,
            multimodal_processor=process_services.get_multimodal_processor,
            conversation_history=process_services.get_conversation_history,
            life_memory_service=process_services.get_life_memory_service,
            mode_transition_service_factory=ModeTransitionService,
            activity_monitor_factory=ActivityMonitor,
            resource_manager_factory=SystemResourceManager,
        )
    )


__all__ = [
    "HostLifecycleApplicationPorts",
    "HostLifecycleContainer",
    "HostLifecycleScopeError",
    "build_host_lifecycle_container",
]
