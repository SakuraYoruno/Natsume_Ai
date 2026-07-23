from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import warnings

import pytest

from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    HostLifecycleContainer,
    HostLifecycleScopeError,
    build_host_lifecycle_container,
)
from host_brain.composition.host_application import (
    HostApplicationPorts,
    build_production_host_application_ports,
)
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.model_provider_composition import (
    ApplicationModelAttemptServicePort,
)
from host_brain.app.tribe_composition import build_tribe_application_ports
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.lifecycle_runtime.runtime_snapshot import (
    build_digital_life_runtime_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]


class _Service:
    def __init__(self, name: str, events: list[str], *, fail_start: bool = False, fail_stop: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.start_calls = 0
        self.stop_calls = 0
        self.mode_transition_service = None

    def start(self, **_kwargs) -> None:
        self.start_calls += 1
        self.events.append(f"{self.name}:start")
        if self.fail_start:
            raise RuntimeError(f"{self.name} start failed")

    def stop(self) -> None:
        self.stop_calls += 1
        self.events.append(f"{self.name}:stop")
        if self.fail_stop:
            raise RuntimeError(f"{self.name} stop failed")

    def notify_user_interaction(self) -> None:
        self.events.append(f"{self.name}:notify")


def _ports(
    events: list[str],
    *,
    activity_factory=None,
    resource_factory=None,
) -> HostLifecycleApplicationPorts:
    def build_mode(**_kwargs):
        value = object()
        events.append("mode:create")
        return value

    def build_activity(**kwargs):
        value = (
            activity_factory()
            if activity_factory is not None
            else _Service("activity", events)
        )
        value.mode_transition_service = kwargs["mode_transition_service"]
        events.append("activity:create")
        return value

    def build_resource(**_kwargs):
        value = (
            resource_factory()
            if resource_factory is not None
            else _Service("resource", events)
        )
        events.append("resource:create")
        return value

    provider = lambda: object()
    return HostLifecycleApplicationPorts(
        memory_guard=provider,
        state_arbiter=provider,
        distillation_engine=provider,
        semantic_recaller=provider,
        episodic_store=provider,
        skill_registry=provider,
        tool_catalog=provider,
        capability_broker=provider,
        model_attempt_service=provider,
        mode_transition_service_factory=build_mode,
        activity_monitor_factory=build_activity,
        resource_manager_factory=build_resource,
    )


def test_host_lifecycle_ports_are_frozen_and_slotted() -> None:
    ports = _ports([])

    with pytest.raises(FrozenInstanceError):
        ports.memory_guard = object  # type: ignore[misc]
    assert not hasattr(ports, "__dict__")


def test_host_lifecycle_builder_requires_explicit_ports_without_locator_fallback() -> None:
    ports = _ports([])

    container = build_host_lifecycle_container(
        initial_sys_mode="focus",
        ports=ports,
        require_durable_hard_state=True,
    )

    source = (
        ROOT / "host_brain" / "app" / "host_lifecycle_composition.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    assert container.ports is ports
    assert container.get_hard_state().sys_mode == "focus"
    assert container.require_durable_hard_state is True
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "host_brain.composition.process_services"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "host_brain.composition"
        and any(alias.name == "process_services" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_production_host_port_root_is_frozen_and_isolates_legacy_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.composition import process_services

    ledger_provider = lambda: object()
    token_provider = lambda: object()
    bond_provider = lambda: object()
    setter = lambda _value: None
    monkeypatch.setattr(process_services, "get_ledger", ledger_provider)
    monkeypatch.setattr(process_services, "get_token_service", token_provider)
    monkeypatch.setattr(process_services, "get_bond_dynamics_writer", bond_provider)
    monkeypatch.setattr(process_services, "set_memory_service_container", setter)

    ports = build_production_host_application_ports()

    assert isinstance(ports, HostApplicationPorts)
    assert ports.lifecycle.ledger is ledger_provider
    assert ports.token_service is token_provider
    assert ports.bond_dynamics_writer is bond_provider
    assert ports.memory_service_container_setter is setter
    with pytest.raises(FrozenInstanceError):
        ports.token_service = object  # type: ignore[misc]
    assert not hasattr(ports, "__dict__")


def test_typed_host_bundle_supplies_lifecycle_and_tribe_without_locator() -> None:
    sentinel = object()

    class Timeline:
        def rebuild_summary(self, *, window_ms: int):
            del window_ms
            return {}

        def build_daily_diary(self, *, window_ms: int):
            del window_ms
            return {}

    timeline = Timeline()
    provider = lambda: sentinel
    host_services = replace(
        _ports([]),
        ledger=provider,
        life_memory_store=provider,
        bond_update_queue=provider,
        life_snapshot_reader=provider,
        silence_engine=provider,
        state_write_arbiter=provider,
        emotion_timeline=lambda: timeline,
        bond_stage_engine=provider,
        shared_memory_index=provider,
        milestone_tracker=provider,
        prompt_registry=provider,
        user_emotion_profile=lambda: {"source": "typed-bundle"},
    )

    lifecycle = build_lifecycle_runtime_application_ports(
        host_services=host_services,
    )
    tribe = build_tribe_application_ports(host_services=host_services)

    assert lifecycle.get_ledger is provider
    assert lifecycle.get_life_memory_store is provider
    assert lifecycle.get_model_attempt_service is host_services.model_attempt_service
    assert lifecycle.emotion_timeline is timeline
    assert lifecycle.get_user_emotion_profile() == {"source": "typed-bundle"}
    assert tribe.ledger is provider
    assert tribe.model_attempt_service is host_services.model_attempt_service
    assert tribe.emotion_timeline() is timeline


def test_missing_typed_host_bundle_fails_closed_without_locator_fallback() -> None:
    lifecycle = build_lifecycle_runtime_application_ports()
    tribe = build_tribe_application_ports()

    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        lifecycle.get_ledger()
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        lifecycle.emotion_timeline.rebuild_summary(window_ms=1)
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        tribe.ledger()


def test_typed_host_bundle_supplies_input_runtime_without_locator() -> None:
    sentinel = object()
    provider = lambda: sentinel
    notify_calls: list[str] = []
    host_services = replace(
        _ports([]),
        ledger=provider,
        life_snapshot_reader=provider,
        intent_engine=provider,
        multimodal_observer=provider,
        multimodal_processor=provider,
    )

    input_ports = build_input_runtime_application_ports(
        host_services=host_services,
        notify_user_interaction_callback=lambda: notify_calls.append("notify"),
    )

    assert input_ports.get_intent_engine is provider
    assert input_ports.get_ledger is provider
    assert input_ports.get_life_snapshot_reader is provider
    assert input_ports.get_multimodal_observer is provider
    assert input_ports.get_multimodal_processor is provider
    input_ports.notify_user_interaction()
    assert notify_calls == ["notify"]


def test_missing_input_host_bundle_fails_closed_without_locator_fallback() -> None:
    input_ports = build_input_runtime_application_ports()

    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        input_ports.get_intent_engine()
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        input_ports.get_ledger()
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        input_ports.notify_user_interaction()


def test_model_attempt_port_uses_injected_provider_and_fails_closed_without_it() -> None:
    service = SimpleNamespace(prompt_registry=object())
    port = ApplicationModelAttemptServicePort(lambda: service)

    assert port.prompt_registry is service.prompt_registry
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_MODEL_ATTEMPT_PROVIDER_NOT_INJECTED",
    ):
        ApplicationModelAttemptServicePort().prompt_registry


def test_hard_state_is_app_local_and_never_writes_bootstrap_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    first = HostLifecycleContainer(_ports([]), initial_sys_mode="normal")
    second = HostLifecycleContainer(_ports([]), initial_sys_mode="gaming")

    transition = first.set_sys_mode("focus")

    assert transition["from_mode"] == "normal"
    assert transition["to_mode"] == "focus"
    assert first.get_hard_state().sys_mode == "focus"
    assert second.get_hard_state().sys_mode == "gaming"
    assert first.get_hard_state() is not second.get_hard_state()
    assert __import__("os").environ["HOST_SYS_MODE"] == "normal"
    monkeypatch.setenv("HOST_SYS_MODE", "locked")
    assert first.get_hard_state().sys_mode == "focus"


def test_invalid_bootstrap_mode_fails_closed_as_maintenance() -> None:
    container = HostLifecycleContainer(
        _ports([]),
        initial_sys_mode="not-a-runtime-mode",
    )

    assert container.get_hard_state().sys_mode == "maintenance"


def test_first_mode_switch_completes_without_recursive_lock_deadlock() -> None:
    container = HostLifecycleContainer(_ports([]), initial_sys_mode="normal")
    completed = threading.Event()

    def switch() -> None:
        container.set_sys_mode("focus")
        completed.set()

    worker = threading.Thread(target=switch, daemon=True)
    worker.start()
    worker.join(timeout=1.0)

    assert completed.is_set()
    assert not worker.is_alive()
    assert container.get_hard_state().sys_mode == "focus"


def test_endocrine_resolver_runs_outside_owner_lock() -> None:
    resolver_events: list[str] = []
    container: HostLifecycleContainer

    def resolve(**kwargs):
        resolver_events.append(kwargs["hard_state"].sys_mode)
        switched = threading.Event()

        def switch_mode() -> None:
            container.set_sys_mode("focus")
            switched.set()

        worker = threading.Thread(target=switch_mode)
        worker.start()
        worker.join(timeout=1.0)
        assert switched.is_set(), "endocrine resolver must not run under owner lock"
        return {"gate_state": "open", "source": "test_resolver"}

    container = HostLifecycleContainer(
        _ports([]),
        initial_sys_mode="normal",
        endocrine_resolver=resolve,
    )

    result = container.get_endocrine_state(
        emotion_tag="CALM",
        emotion_intensity=0.2,
    )

    assert result == {"gate_state": "open", "source": "test_resolver"}
    assert resolver_events == ["normal"]
    assert container.get_hard_state().sys_mode == "focus"


def test_endocrine_resolver_failure_preserves_fallback_contract() -> None:
    def fail(**_kwargs):
        raise RuntimeError("resolver unavailable")

    container = HostLifecycleContainer(_ports([]), endocrine_resolver=fail)

    assert container.get_endocrine_state()["source"] == "endocrine_fallback"


@pytest.mark.asyncio
async def test_providers_never_lazy_create_before_start_or_after_close() -> None:
    events: list[str] = []
    container = HostLifecycleContainer(_ports(events))

    assert container.get_activity_monitor(create_if_missing=True) is None
    assert container.get_resource_manager(create_if_missing=True) is None
    assert events == []
    container.begin()
    await container.aclose(logger=logging.getLogger("test.lifecycle.empty"))
    assert container.get_activity_monitor(create_if_missing=True) is None
    assert container.get_resource_manager(create_if_missing=True) is None
    assert events == []


@pytest.mark.asyncio
async def test_explicit_start_publishes_exact_app_local_identity() -> None:
    events: list[str] = []
    container = HostLifecycleContainer(_ports(events))
    container.begin()

    activity = container.start_activity_monitor(ledger=object())
    resource = container.start_resource_manager(
        degradation_state_provider=lambda: None,
        run_immediately=False,
    )

    assert container.get_activity_monitor(create_if_missing=True) is activity
    assert container.get_resource_manager(create_if_missing=True) is resource
    assert container.get_mode_transition_service() is activity.mode_transition_service
    container.notify_user_interaction()
    await container.aclose(logger=logging.getLogger("test.lifecycle.identity"))
    assert events == [
        "mode:create",
        "activity:create",
        "activity:start",
        "resource:create",
        "resource:start",
        "activity:notify",
        "resource:stop",
        "activity:stop",
    ]


@pytest.mark.asyncio
async def test_two_containers_do_not_share_or_cross_close_services() -> None:
    events_a: list[str] = []
    events_b: list[str] = []
    first = HostLifecycleContainer(_ports(events_a))
    second = HostLifecycleContainer(_ports(events_b))
    first.begin()
    second.begin()
    activity_a = first.start_activity_monitor(ledger=object())
    activity_b = second.start_activity_monitor(ledger=object())

    assert activity_a is not activity_b
    assert first.get_mode_transition_service() is not second.get_mode_transition_service()
    await first.aclose(logger=logging.getLogger("test.lifecycle.first"))
    assert first.get_activity_monitor() is None
    assert second.get_activity_monitor() is activity_b
    assert activity_b.stop_calls == 0
    await second.aclose(logger=logging.getLogger("test.lifecycle.second"))
    assert activity_b.stop_calls == 1


@pytest.mark.asyncio
async def test_partial_activity_start_is_reclaimed_without_publication() -> None:
    events: list[str] = []
    failed = _Service("activity", events, fail_start=True)
    container = HostLifecycleContainer(
        _ports(events, activity_factory=lambda: failed)
    )
    container.begin()

    with pytest.raises(RuntimeError, match="activity start failed"):
        container.start_activity_monitor(ledger=object())

    assert container.get_activity_monitor(create_if_missing=True) is None
    assert container.get_mode_transition_service() is None
    assert failed.stop_calls == 1
    await container.aclose(logger=logging.getLogger("test.lifecycle.partial-activity"))
    assert failed.stop_calls == 1


@pytest.mark.asyncio
async def test_activity_factory_failure_never_publishes_mode_authority() -> None:
    events: list[str] = []

    def fail_factory():
        events.append("activity:factory-failed")
        raise RuntimeError("activity factory failed")

    container = HostLifecycleContainer(
        _ports(events, activity_factory=fail_factory)
    )
    container.begin()

    with pytest.raises(RuntimeError, match="activity factory failed"):
        container.start_activity_monitor(ledger=object())

    assert container.get_activity_monitor() is None
    assert container.get_mode_transition_service() is None
    await container.aclose(logger=logging.getLogger("test.lifecycle.factory-failure"))
    assert events == ["mode:create", "activity:factory-failed"]


@pytest.mark.asyncio
async def test_partial_resource_start_does_not_unpublish_active_monitor() -> None:
    events: list[str] = []
    failed = _Service("resource", events, fail_start=True)
    container = HostLifecycleContainer(
        _ports(events, resource_factory=lambda: failed)
    )
    container.begin()
    activity = container.start_activity_monitor(ledger=object())

    with pytest.raises(RuntimeError, match="resource start failed"):
        container.start_resource_manager(
            degradation_state_provider=lambda: None,
            run_immediately=False,
        )

    assert failed.stop_calls == 1
    assert container.get_resource_manager(create_if_missing=True) is None
    assert container.get_activity_monitor() is activity
    await container.aclose(logger=logging.getLogger("test.lifecycle.partial-resource"))
    assert failed.stop_calls == 1
    assert activity.stop_calls == 1


@pytest.mark.asyncio
async def test_close_unpublishes_before_stop_and_is_best_effort_exact_once() -> None:
    events: list[str] = []
    container: HostLifecycleContainer

    class Resource(_Service):
        def stop(self) -> None:
            assert container.get_resource_manager() is None
            with pytest.raises(
                HostLifecycleScopeError,
                match="HOST_LIFECYCLE_SCOPE_CLOSING",
            ):
                container.begin()
            super().stop()

    class Activity(_Service):
        def stop(self) -> None:
            assert container.get_activity_monitor() is None
            super().stop()

    activity = Activity("activity", events)
    resource = Resource("resource", events, fail_stop=True)
    container = HostLifecycleContainer(
        _ports(
            events,
            activity_factory=lambda: activity,
            resource_factory=lambda: resource,
        )
    )
    container.begin()
    container.start_activity_monitor(ledger=object())
    container.start_resource_manager(
        degradation_state_provider=lambda: None,
        run_immediately=False,
    )

    logger = logging.getLogger("test.lifecycle.close")
    await container.aclose(logger=logger)
    await container.aclose(logger=logger)

    assert resource.stop_calls == 1
    assert activity.stop_calls == 1
    assert events[-2:] == ["resource:stop", "activity:stop"]
    assert container.begin() == 2
    await container.aclose(logger=logger)


def test_process_tribe_ports_are_owner_fenced_and_rebind_only_after_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.composition import tribe_runtime
    from host_brain.tribe.application_ports import TribeApplicationPorts

    binding = tribe_runtime.ProcessTribeApplicationPortBinding()
    runtime = SimpleNamespace(application_port_binding=binding)
    agents = [SimpleNamespace(application_ports=None) for _ in range(3)]
    monkeypatch.setattr(
        tribe_runtime,
        "require_process_tribe_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        tribe_runtime,
        "_tribe_agents",
        lambda: tuple((f"agent-{index}", agent) for index, agent in enumerate(agents)),
    )

    owner_a = object()
    owner_b = object()
    ports_a = TribeApplicationPorts()
    ports_b = TribeApplicationPorts()
    tribe_runtime.bind_process_tribe_application_ports(
        owner=owner_a,
        ports=ports_a,
    )

    assert binding.owner is owner_a
    assert binding.ports is ports_a
    assert all(agent.application_ports is ports_a for agent in agents)
    with pytest.raises(
        RuntimeError,
        match="PROCESS_TRIBE_APPLICATION_PORTS_ALREADY_BOUND",
    ):
        tribe_runtime.bind_process_tribe_application_ports(
            owner=owner_b,
            ports=ports_b,
        )
    with pytest.raises(
        RuntimeError,
        match="PROCESS_TRIBE_APPLICATION_PORTS_OWNER_MISMATCH",
    ):
        tribe_runtime.unbind_process_tribe_application_ports(owner=owner_b)
    assert all(agent.application_ports is ports_a for agent in agents)

    assert tribe_runtime.unbind_process_tribe_application_ports(owner=owner_a) is True
    assert all(agent.application_ports is None for agent in agents)
    tribe_runtime.bind_process_tribe_application_ports(owner=owner_b, ports=ports_b)
    assert all(agent.application_ports is ports_b for agent in agents)


def test_resource_manager_immediate_sample_uses_the_owned_loop_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.core.resource_manager import SystemResourceManager

    manager = SystemResourceManager(
        interval_s=3600.0,
        llm_enabled=False,
        auto_gc_enabled=False,
        auto_trim_enabled=False,
        status_path=tmp_path / "resource-status.json",
    )
    sampled = threading.Event()
    thread_names: list[str] = []

    def record_sample(*, reason: str):
        thread_names.append(threading.current_thread().name)
        assert reason == "startup"
        sampled.set()
        return {"status": "OK", "reason": reason}

    monkeypatch.setattr(manager, "run_once", record_sample)
    try:
        manager.start(run_immediately=True)
        assert sampled.wait(timeout=2.0)
        owned_thread = manager._thread
    finally:
        manager.stop()

    assert owned_thread is not None
    assert owned_thread.is_alive() is False
    assert thread_names == ["system-resource-manager"]
    assert all(
        thread.name != "system-resource-manager-startup-sample"
        for thread in threading.enumerate()
    )


def test_http_hooks_has_zero_process_locator_or_global_publication_calls() -> None:
    path = ROOT / "host_brain" / "app" / "http_hooks.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    forbidden_calls = {
        "get_process_service_container",
        "set_activity_monitor",
        "set_resource_manager",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "host_brain.composition.process_services"
        if isinstance(node, ast.Import):
            assert all(
                alias.name != "host_brain.composition.process_services"
                for alias in node.names
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls


def test_runtime_snapshot_activity_projection_is_read_only() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def activity_provider(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    timeline = SimpleNamespace(
        rebuild_summary=lambda **_kwargs: {},
        build_daily_diary=lambda **_kwargs: {},
    )
    ports = LifecycleRuntimeApplicationPorts(
        get_activity_monitor=activity_provider,
        get_endocrine_state=lambda **_kwargs: {},
        get_episodic_store=lambda: object(),
        get_hard_state=lambda: SimpleNamespace(sys_mode="normal"),
        get_ledger=lambda: object(),
        get_life_memory_store=lambda: object(),
        get_model_attempt_service=lambda: object(),
        emotion_timeline=timeline,
        get_user_emotion_profile=lambda: {},
    )

    snapshot = build_digital_life_runtime_snapshot(ports=ports)

    assert calls == [((), {})]
    assert snapshot["slot_status"]["activity_monitor"] == "unavailable"


def test_host_lifecycle_cutover_reduces_truthful_locator_inventory() -> None:
    named_import_modules: dict[Path, int] = {}
    all_import_modules: set[Path] = set()
    for path in (ROOT / "host_brain").rglob("*.py"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imported_names = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "host_brain.composition.process_services"
            ):
                imported_names += len(node.names)
                all_import_modules.add(path)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "host_brain.composition"
                and any(alias.name == "process_services" for alias in node.names)
            ):
                all_import_modules.add(path)
            elif isinstance(node, ast.Import):
                direct_count = sum(
                    alias.name == "host_brain.composition.process_services"
                    for alias in node.names
                )
                imported_names += direct_count
                if direct_count:
                    all_import_modules.add(path)
        if imported_names:
            named_import_modules[path] = imported_names

    assert len(named_import_modules) == 31
    assert sum(named_import_modules.values()) == 68
    assert len(all_import_modules) == 34
    assert (
        ROOT / "host_brain" / "app" / "lifecycle_runtime_composition.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "tribe_composition.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "input_runtime_composition.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "model_provider_composition.py"
    ) not in all_import_modules
    assert ROOT / "host_brain" / "app" / "http_hooks.py" not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "host_lifecycle_composition.py"
    ) not in all_import_modules
    assert ROOT / "host_brain" / "app" / "asgi.py" not in all_import_modules
    assert (
        ROOT / "host_brain" / "composition" / "host_application.py"
    ) in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "conversation_writer.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "vts_emotion_driver.py"
    ) not in all_import_modules
    assert not (ROOT / "host_brain" / "app" / "intelligent_runtime.py").exists()
    assert (ROOT / "host_brain" / "app" / "asgi.py") not in named_import_modules
