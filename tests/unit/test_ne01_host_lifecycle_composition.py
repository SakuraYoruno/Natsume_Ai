from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import warnings

import pytest

import host_brain.app.host_lifecycle_composition as legacy_host_lifecycle
import host_brain.composition.host_application as host_application_module
import host_brain.contracts.host_lifecycle as canonical_host_lifecycle
from host_brain.audit.sqlite_ledger import SQLiteLedger
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
from host_brain.app.application_port_wiring import (
    build_process_application_ports,
)
from host_brain.app.emotion_context import HostEmotionRuntime
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.model_provider_composition import (
    ApplicationModelAttemptServicePort,
)
from host_brain.app.startup import build_startup_application_ports
from host_brain.app.tribe_composition import (
    TribeRuntimeAgentPorts,
    build_tribe_application_ports,
)
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.lifecycle_runtime.runtime_snapshot import (
    build_digital_life_runtime_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]


def test_host_lifecycle_contract_is_neutral_and_identity_preserving() -> None:
    """Composition must not depend back on app for the shared lifecycle type."""

    assert (
        legacy_host_lifecycle.HostLifecycleApplicationPorts
        is canonical_host_lifecycle.HostLifecycleApplicationPorts
    )
    assert (
        legacy_host_lifecycle.HostLifecycleScopeError
        is canonical_host_lifecycle.HostLifecycleScopeError
    )
    composition_source = (
        ROOT / "host_brain" / "composition" / "host_application.py"
    ).read_text(encoding="utf-8-sig")
    assert "host_brain.app.host_lifecycle_composition" not in composition_source
    assert "host_brain.contracts.host_lifecycle" in composition_source


class _AuthorityRuntime:
    def __init__(
        self,
        ledger: object,
        *,
        episodic_store: object | None = None,
        model_attempt_service: object | None = None,
    ) -> None:
        self.ledger = ledger
        self.episodic_store = episodic_store or object()
        self.life_memory_store = SimpleNamespace(ledger=ledger)
        self.model_attempt_service = model_attempt_service or object()
        self.model_route_catalog = object()
        self.prompt_registry = object()
        self.capability_broker = object()
        self.active = False

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("HOST_AUTHORITY_RUNTIME_ALREADY_ACTIVE")
        self.active = True

    def close(self) -> None:
        self.active = False
        close = getattr(self.ledger, "close", None)
        if callable(close):
            close()

    def _require(self, value: object) -> object:
        if not self.active:
            raise RuntimeError("HOST_AUTHORITY_RUNTIME_NOT_ACTIVE")
        return value

    def require_ledger(self) -> object:
        return self._require(self.ledger)

    def require_episodic_store(self) -> object:
        return self._require(self.episodic_store)

    def require_life_memory_store(self) -> object:
        return self._require(self.life_memory_store)

    def require_model_attempt_service(self) -> object:
        return self._require(self.model_attempt_service)

    def require_model_route_catalog(self) -> object:
        return self._require(self.model_route_catalog)

    def require_prompt_registry(self) -> object:
        return self._require(self.prompt_registry)

    def require_capability_broker(self) -> object:
        return self._require(self.capability_broker)


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
    emotion_runtime = HostEmotionRuntime()
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
        emotion_context_runtime=lambda: emotion_runtime,
        user_emotion_profile=emotion_runtime.user_emotion_profile,
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


def test_startup_ports_preserve_exact_host_provider_identity_and_fail_closed() -> None:
    provider_names = (
        "emotion_timeline",
        "skill_registry",
        "graph_runtime",
        "ledger",
        "prompt_registry",
        "routing_matrix",
        "intent_engine",
        "state_write_arbiter",
        "silence_engine",
        "model_route_catalog",
        "model_attempt_service",
        "episodic_store",
    )
    providers = {
        name: (lambda value=name: value)
        for name in provider_names
    }
    host_ports = replace(_ports([]), **providers)

    startup_ports = build_startup_application_ports(host_ports)

    for name, provider in providers.items():
        assert getattr(startup_ports, name) is provider
    with pytest.raises(
        HostLifecycleScopeError,
        match="HOST_SERVICE_PROVIDER_NOT_INJECTED:graph_runtime",
    ):
        build_startup_application_ports(replace(host_ports, graph_runtime=None))


def test_production_host_port_root_is_frozen_and_isolates_legacy_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = object()
    authority = _AuthorityRuntime(ledger)
    bond_client = object()
    edge_probe = lambda: object()
    edge_status_builder = lambda _probe: {}
    construction_ports = replace(
        build_process_application_ports(),
        persistence_db_path=lambda: ":memory:",
        probe_edge_rpc_health=edge_probe,
        build_edge_dependency_probe_statuses=edge_status_builder,
    )
    monkeypatch.setattr(
        host_application_module,
        "build_host_bond_client",
        lambda: bond_client,
    )
    ports = build_production_host_application_ports(
        process_application_ports=construction_ports,
        authority_runtime_owner=authority,  # type: ignore[arg-type]
    )

    assert isinstance(ports, HostApplicationPorts)
    assert ports.lifecycle.ledger.__self__ is authority
    with pytest.raises(
        RuntimeError,
        match="HOST_AUTHORITY_RUNTIME_NOT_ACTIVE",
    ):
        ports.lifecycle.ledger()
    with pytest.raises(
        RuntimeError,
        match="HOST_GOVERNANCE_RUNTIME_NOT_ACTIVE",
    ):
        ports.token_service()
    with pytest.raises(RuntimeError, match="HOST_BOND_RUNTIME_NOT_ACTIVE"):
        ports.bond_dynamics_writer()
    ports.begin_authority_runtime()
    ports.begin_governance_runtime()
    ports.begin_bond_runtime()
    token_service = ports.token_service()
    assert ports.token_service() is token_service
    bond_writer = ports.bond_dynamics_writer()
    assert ports.bond_dynamics_writer() is bond_writer
    assert bond_writer.ledger is ledger
    assert bond_writer._stage_engine is ports.lifecycle.bond_stage_engine()
    assert bond_writer.neo4j_client is bond_client
    assert ports.lifecycle.probe_edge_rpc_health is edge_probe
    assert (
        ports.lifecycle.build_edge_dependency_probe_statuses
        is edge_status_builder
    )
    emotion_runtime = ports.lifecycle.emotion_context_runtime()
    assert isinstance(emotion_runtime, HostEmotionRuntime)
    assert ports.lifecycle.emotion_context_runtime() is emotion_runtime
    assert (
        ports.lifecycle.user_emotion_profile()
        is emotion_runtime.user_emotion_profile()
    )
    with pytest.raises(RuntimeError, match="LIFE_MEMORY_SERVICE_NOT_COMPOSED"):
        ports.lifecycle.life_memory_service()
    memory_service = object()
    memory_container = SimpleNamespace(service=memory_service)
    ports.publish_memory_service_container(memory_container)
    assert ports.lifecycle.life_memory_service() is memory_service
    with pytest.raises(
        RuntimeError,
        match="HOST_MEMORY_SERVICE_PUBLICATION_MISMATCH",
    ):
        ports.withdraw_memory_service_container(
            SimpleNamespace(service=object())
        )
    ports.withdraw_memory_service_container(memory_container)
    with pytest.raises(RuntimeError, match="LIFE_MEMORY_SERVICE_NOT_COMPOSED"):
        ports.lifecycle.life_memory_service()
    with pytest.raises(FrozenInstanceError):
        ports.token_service = object  # type: ignore[misc]
    assert not hasattr(ports, "__dict__")
    ports.close_bond_runtime()
    ports.close_governance_runtime()
    ports.close_authority_runtime()


def test_production_host_port_root_owns_selected_state_per_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_episodic_store = object()
    second_episodic_store = object()
    first_model_attempt_service = object()
    second_model_attempt_service = object()
    first_authority = _AuthorityRuntime(
        SQLiteLedger(":memory:", check_same_thread=False),
        episodic_store=first_episodic_store,
        model_attempt_service=first_model_attempt_service,
    )
    second_authority = _AuthorityRuntime(
        SQLiteLedger(":memory:", check_same_thread=False),
        episodic_store=second_episodic_store,
        model_attempt_service=second_model_attempt_service,
    )
    construction_ports = replace(
        build_process_application_ports(),
        persistence_db_path=lambda: ":memory:",
    )
    monkeypatch.setattr(
        host_application_module,
        "build_host_graph_runtime",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        host_application_module,
        "build_host_routing_matrix",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        host_application_module,
        "build_host_bond_client",
        lambda: object(),
    )
    first = build_production_host_application_ports(
        process_application_ports=construction_ports,
        authority_runtime_owner=first_authority,  # type: ignore[arg-type]
    )
    second = build_production_host_application_ports(
        process_application_ports=construction_ports,
        authority_runtime_owner=second_authority,  # type: ignore[arg-type]
    )
    first.begin_authority_runtime()
    second.begin_authority_runtime()
    first.begin_governance_runtime()
    second.begin_governance_runtime()
    first.begin_bond_runtime()
    second.begin_bond_runtime()
    provider_pairs = (
        (
            first.lifecycle.silence_engine,
            second.lifecycle.silence_engine,
        ),
        (
            first.lifecycle.milestone_tracker,
            second.lifecycle.milestone_tracker,
        ),
        (
            first.lifecycle.multimodal_processor,
            second.lifecycle.multimodal_processor,
        ),
        (
            first.lifecycle.tts_context_router,
            second.lifecycle.tts_context_router,
        ),
        (
            first.lifecycle.meta_learning_loop,
            second.lifecycle.meta_learning_loop,
        ),
        (
            first.memory_worth_evaluator,
            second.memory_worth_evaluator,
        ),
        (
            first.security_immune_agent,
            second.security_immune_agent,
        ),
        (
            first.capability_gap_telemetry,
            second.capability_gap_telemetry,
        ),
        (
            first.capability_forge_service,
            second.capability_forge_service,
        ),
        (
            first.lifecycle.conversation_turn_store,
            second.lifecycle.conversation_turn_store,
        ),
        (
            first.lifecycle.conversation_history,
            second.lifecycle.conversation_history,
        ),
        (
            first.lifecycle.session_topic_ledger_store,
            second.lifecycle.session_topic_ledger_store,
        ),
        (
            first.lifecycle.session_tactic_ledger_store,
            second.lifecycle.session_tactic_ledger_store,
        ),
        (
            first.lifecycle.shared_memory_index,
            second.lifecycle.shared_memory_index,
        ),
        (
            first.lifecycle.multimodal_observer,
            second.lifecycle.multimodal_observer,
        ),
        (
            first.lifecycle.graph_runtime,
            second.lifecycle.graph_runtime,
        ),
        (
            first.lifecycle.routing_matrix,
            second.lifecycle.routing_matrix,
        ),
        (
            first.bond_dynamics_writer,
            second.bond_dynamics_writer,
        ),
        (
            first.lifecycle.memory_guard,
            second.lifecycle.memory_guard,
        ),
        (
            first.lifecycle.state_arbiter,
            second.lifecycle.state_arbiter,
        ),
        (
            first.lifecycle.state_write_arbiter,
            second.lifecycle.state_write_arbiter,
        ),
        (
            first.lifecycle.distillation_engine,
            second.lifecycle.distillation_engine,
        ),
        (
            first.lifecycle.semantic_recaller,
            second.lifecycle.semantic_recaller,
        ),
        (
            first.lifecycle.emotion_timeline,
            second.lifecycle.emotion_timeline,
        ),
        (
            first.token_service,
            second.token_service,
        ),
        (
            first.lifecycle.skill_registry,
            second.lifecycle.skill_registry,
        ),
        (
            first.lifecycle.tool_catalog,
            second.lifecycle.tool_catalog,
        ),
        (
            first.capability_scorecard_provider,
            second.capability_scorecard_provider,
        ),
        (
            first.lifecycle.intent_engine,
            second.lifecycle.intent_engine,
        ),
    )

    for first_provider, second_provider in provider_pairs:
        first_instance = first_provider()
        assert first_provider() is first_instance
        assert second_provider() is not first_instance

    assert (
        first.capability_forge_service()._telemetry
        is first.capability_gap_telemetry()
    )
    assert (
        second.capability_forge_service()._telemetry
        is second.capability_gap_telemetry()
    )
    assert (
        first.lifecycle.distillation_engine()._guard
        is first.lifecycle.memory_guard()
    )
    assert (
        first.lifecycle.distillation_engine()._arbiter
        is first.lifecycle.state_arbiter()
    )
    assert (
        second.lifecycle.distillation_engine()._guard
        is second.lifecycle.memory_guard()
    )
    assert (
        second.lifecycle.distillation_engine()._arbiter
        is second.lifecycle.state_arbiter()
    )
    assert (
        first.lifecycle.conversation_history()._repository
        is first.lifecycle.conversation_turn_store()
    )
    assert (
        second.lifecycle.conversation_history()._repository
        is second.lifecycle.conversation_turn_store()
    )
    assert (
        first.lifecycle.shared_memory_index()._store
        is first_episodic_store
    )
    assert (
        second.lifecycle.shared_memory_index()._store
        is second_episodic_store
    )
    assert (
        first.lifecycle.multimodal_observer()
        ._model_backend._model_attempt_service_provider()
        is first_model_attempt_service
    )
    assert (
        second.lifecycle.multimodal_observer()
        ._model_backend._model_attempt_service_provider()
        is second_model_attempt_service
    )
    first.close_bond_runtime()
    second.close_bond_runtime()
    first.close_governance_runtime()
    second.close_governance_runtime()
    first.close_authority_runtime()
    second.close_authority_runtime()


def test_typed_host_bundle_supplies_lifecycle_and_tribe_without_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Ledger:
        def get_session_seq(self, *, session_id: str) -> int | None:
            assert session_id == "session-1"
            return 7

    sentinel = Ledger()
    preference_calls: list[dict[str, object]] = []
    experience_calls: list[object] = []

    from host_brain.app.context import conversation_experience_store
    from host_brain.app import user_preference_store

    def fake_build_user_preference_pack(**kwargs: object) -> dict[str, object]:
        preference_calls.append(dict(kwargs))
        return {"source": "app-local-ledger"}

    def fake_build_conversation_experience_read_pack(
        *, ledger: object, **_kwargs: object
    ) -> dict[str, object]:
        experience_calls.append(ledger)
        return {"source": "app-local-experience-read"}

    monkeypatch.setattr(
        user_preference_store,
        "build_user_preference_pack",
        fake_build_user_preference_pack,
    )
    monkeypatch.setattr(
        conversation_experience_store,
        "build_conversation_experience_read_pack",
        fake_build_conversation_experience_read_pack,
    )

    class Timeline:
        def rebuild_summary(self, *, window_ms: int):
            del window_ms
            return {}

        def build_daily_diary(self, *, window_ms: int):
            del window_ms
            return {}

    timeline = Timeline()
    emotion_runtime = HostEmotionRuntime(timeline_provider=lambda: timeline)
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
        emotion_context_runtime=lambda: emotion_runtime,
        bond_stage_engine=provider,
        shared_memory_index=provider,
        milestone_tracker=provider,
        prompt_registry=provider,
        user_emotion_profile=emotion_runtime.user_emotion_profile,
    )

    lifecycle = build_lifecycle_runtime_application_ports(
        host_services=host_services,
    )
    tribe = build_tribe_application_ports(host_services=host_services)

    assert lifecycle.get_ledger is provider
    assert lifecycle.get_life_memory_store is provider
    assert lifecycle.get_model_attempt_service is host_services.model_attempt_service
    assert lifecycle.emotion_timeline.rebuild_summary(window_ms=1) == {}
    assert lifecycle.emotion_timeline.build_daily_diary(window_ms=1) == {}
    assert (
        lifecycle.get_user_emotion_profile()
        is emotion_runtime.user_emotion_profile()
    )
    assert tribe.ledger is provider
    assert tribe.model_attempt_service is host_services.model_attempt_service
    assert tribe.emotion_timeline() is timeline
    assert tribe.build_user_preference_pack(person_id="person-1", query_text="偏好") == {
        "source": "app-local-ledger"
    }
    assert preference_calls == [
        {"ledger": sentinel, "person_id": "person-1", "query_text": "偏好"}
    ]
    assert tribe.build_conversation_experience_read_pack(
        session_id="session-1",
        person_id="person-1",
        query_text="偏好",
    ) == {"source": "app-local-experience-read"}
    assert experience_calls == [sentinel]
    assert tribe.allocate_intent_seq(session_id="session-1") == 8
    assert tribe.allocate_intent_seq(session_id="session-1") == 9
    second_tribe = build_tribe_application_ports(host_services=host_services)
    assert second_tribe.allocate_intent_seq(session_id="session-1") == 8


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
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        tribe.build_user_preference_pack(person_id="person-1", query_text="偏好")
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        tribe.build_conversation_experience_read_pack(
            session_id="session-1",
            query_text="偏好",
        )
    with pytest.raises(
        RuntimeError,
        match="HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED",
    ):
        tribe.allocate_intent_seq(session_id="session-1")


def test_stateful_tribe_helpers_require_explicit_ledger_authority() -> None:
    from host_brain.app.context.conversation_experience_store import (
        ConversationExperienceStore,
    )
    from host_brain.app.user_preference_store import UserPreferenceStore

    with pytest.raises(
        RuntimeError,
        match="USER_PREFERENCE_LEDGER_NOT_INJECTED",
    ):
        UserPreferenceStore()
    with pytest.raises(
        RuntimeError,
        match="CONVERSATION_EXPERIENCE_LEDGER_NOT_INJECTED",
    ):
        ConversationExperienceStore()


def test_conversation_experience_read_pack_exposes_no_writer_to_tribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.app.context import conversation_experience_store

    read_operations: list[tuple[str, object]] = []

    class ReadProjection:
        def get_belief(self, **kwargs: object) -> dict[str, object]:
            read_operations.append(("belief", dict(kwargs)))
            return {"selected_intent": "support"}

        def recall_lessons(self, **kwargs: object) -> list[dict[str, object]]:
            read_operations.append(("lessons", dict(kwargs)))
            return [{"lesson_id": "lesson-1"}]

        def record_inference(self, **_kwargs: object) -> None:
            raise AssertionError("read pack must not expose inference persistence")

        def record_outcome_feedback(self, **_kwargs: object) -> None:
            raise AssertionError("read pack must not expose feedback persistence")

    projection = ReadProjection()
    monkeypatch.setattr(
        conversation_experience_store,
        "get_conversation_experience_store",
        lambda **_kwargs: projection,
    )

    pack = conversation_experience_store.build_conversation_experience_read_pack(
        ledger=object(),
        session_id="session-1",
        person_id="person-1",
        domain="general",
        query_text="最近的状态",
        lesson_limit=5,
    )

    assert pack == {
        "previous_belief": {"selected_intent": "support"},
        "experience_lessons": [{"lesson_id": "lesson-1"}],
    }
    assert read_operations == [
        ("belief", {"session_id": "session-1", "person_id": "person-1"}),
        (
            "lessons",
            {
                "person_id": "person-1",
                "domain": "general",
                "query_text": "最近的状态",
                "limit": 5,
            },
        ),
    ]


def test_tribe_state_helpers_do_not_import_the_process_service_locator() -> None:
    for relative_path in (
        "host_brain/app/user_preference_store.py",
        "host_brain/app/context/conversation_experience_store.py",
        "host_brain/app/tribe_composition.py",
        "host_brain/app/intent_seq.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8-sig")
        assert "host_brain.composition.process_services" not in source


def test_input_service_log_has_no_dead_process_ledger_accessor() -> None:
    source = (ROOT / "host_brain" / "app" / "input_service_log.py").read_text(
        encoding="utf-8-sig"
    )

    assert "host_brain.composition.process_services" not in source
    assert "get_ledger_safe" not in source


def test_dead_surface_reply_postprocessor_is_deleted() -> None:
    assert not (
        ROOT / "host_brain" / "app" / "surface_reply_postprocessor.py"
    ).exists()


def test_retired_unbudgeted_surface_reply_judge_is_deleted() -> None:
    assert not (
        ROOT / "host_brain" / "app" / "codex_spark_reply_judge.py"
    ).exists()
    source = (ROOT / "host_brain" / "app" / "surface_main_chain_adapter.py").read_text(
        encoding="utf-8-sig"
    )
    assert "codex_spark_reply_judge" not in source


def test_typed_host_bundle_supplies_input_runtime_without_locator() -> None:
    sentinel = object()
    thalamus = object()
    ans = object()
    immune = object()
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
        tribe_runtime_agent_ports=TribeRuntimeAgentPorts(
            get_thalamus_agent=lambda: thalamus,
            get_ans_agent=lambda: ans,
            get_immune_agent=lambda: immune,
        ),
    )

    assert input_ports.get_intent_engine is provider
    assert input_ports.get_ledger is provider
    assert input_ports.get_life_snapshot_reader is provider
    assert input_ports.get_multimodal_observer is provider
    assert input_ports.get_multimodal_processor is provider
    assert input_ports.require_thalamus_agent() is thalamus
    assert input_ports.require_ans_agent() is ans
    assert input_ports.require_immune_agent() is immune
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
    with pytest.raises(
        RuntimeError,
        match="HOST_INPUT_TRIBE_RUNTIME_PORTS_NOT_INJECTED",
    ):
        input_ports.require_thalamus_agent()
    with pytest.raises(
        RuntimeError,
        match="HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED",
    ):
        input_ports.require_immune_agent()


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
        status_journal=object(),
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
async def test_resource_manager_requires_durable_status_journal_before_factory() -> None:
    events: list[str] = []
    container = HostLifecycleContainer(_ports(events))
    container.begin()

    with pytest.raises(
        HostLifecycleScopeError,
        match="RESOURCE_MANAGER_STATUS_JOURNAL_NOT_INJECTED",
    ):
        container.start_resource_manager(
            status_journal=None,
            degradation_state_provider=lambda: None,
            run_immediately=False,
        )

    assert events == []
    assert container.get_resource_manager() is None
    await container.aclose(logger=logging.getLogger("test.lifecycle.no-journal"))


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
            status_journal=object(),
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
        status_journal=object(),
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


def test_app_tribe_ports_are_owner_fenced_and_rebind_only_after_release() -> None:
    from host_brain.app.tribe_runtime_scope import TribeRuntimeScope
    from host_brain.tribe.application_ports import TribeApplicationPorts
    from host_brain.tribe.event_bus import InMemoryTribeEventBus
    from host_brain.tribe.registry import HostTribeRegistry
    from host_brain.tribe.tribe_state import TribeStateRegistry

    previous_state_registries = [object() for _ in range(3)]
    agents = [
        SimpleNamespace(
            application_ports=None,
            state_registry=previous_state_registries[index],
        )
        for index in range(3)
    ]
    providers = tuple(
        (f"agent-{index}", lambda agent=agent: agent)
        for index, agent in enumerate(agents)
    )
    runtime = TribeRuntimeScope(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=InMemoryTribeEventBus(),
        agent_providers=providers,
    )
    concurrent_runtime = TribeRuntimeScope(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=InMemoryTribeEventBus(),
        agent_providers=providers,
    )

    owner_a = object()
    owner_b = object()
    ports_a = TribeApplicationPorts()
    ports_b = TribeApplicationPorts()
    runtime.bind_application_ports(
        owner=owner_a,
        ports=ports_a,
    )

    assert runtime.application_ports is ports_a
    assert all(agent.application_ports is ports_a for agent in agents)
    assert all(
        agent.state_registry is runtime.state_registry
        for agent in agents
    )
    from host_brain.tribe.event_bus import get_tribe_event_bus
    from host_brain.tribe.registry import get_host_tribe_registry
    from host_brain.tribe.runtime_context import active_tribe_runtime_context
    from host_brain.tribe.tribe_state import get_global_registry

    assert active_tribe_runtime_context() is None
    activation = runtime.activate_execution_context(owner=owner_a)
    try:
        active_context = active_tribe_runtime_context()
        assert active_context is not None
        assert active_context.state_registry is runtime.state_registry
        assert active_context.tribe_registry is runtime.tribe_registry
        assert active_context.event_bus is runtime.event_bus
        assert get_global_registry() is runtime.state_registry
        assert get_host_tribe_registry() is runtime.tribe_registry
        assert get_tribe_event_bus() is runtime.event_bus
        with pytest.raises(
            RuntimeError,
            match="TRIBE_APPLICATION_PORTS_ALREADY_BOUND",
        ):
            runtime.bind_application_ports(
                owner=owner_b,
                ports=ports_b,
            )
        with pytest.raises(
            RuntimeError,
            match="TRIBE_APPLICATION_PORTS_OWNER_MISMATCH",
        ):
            runtime.unbind_application_ports(owner=owner_b)
        with pytest.raises(
            RuntimeError,
            match="TRIBE_RUNTIME_CONTEXT_ALREADY_BOUND",
        ):
            concurrent_runtime.bind_application_ports(
                owner=owner_b,
                ports=ports_b,
            )
        assert all(agent.application_ports is ports_a for agent in agents)
    finally:
        runtime.deactivate_execution_context(activation)

    assert runtime.unbind_application_ports(owner=owner_a) is True
    assert all(agent.application_ports is None for agent in agents)
    assert [
        agent.state_registry for agent in agents
    ] == previous_state_registries
    assert active_tribe_runtime_context() is None
    concurrent_runtime.bind_application_ports(owner=owner_b, ports=ports_b)
    assert all(agent.application_ports is ports_b for agent in agents)
    assert all(
        agent.state_registry is concurrent_runtime.state_registry
        for agent in agents
    )
    assert concurrent_runtime.unbind_application_ports(owner=owner_b) is True


def test_app_tribe_ports_cross_scope_bind_is_atomic() -> None:
    from host_brain.app.tribe_runtime_scope import TribeRuntimeScope
    from host_brain.tribe.application_ports import TribeApplicationPorts
    from host_brain.tribe.event_bus import InMemoryTribeEventBus
    from host_brain.tribe.registry import HostTribeRegistry
    from host_brain.tribe.tribe_state import TribeStateRegistry

    agents_by_runtime = [
        [SimpleNamespace(application_ports=None) for _ in range(3)]
        for _ in range(2)
    ]
    runtimes = [
        TribeRuntimeScope(
            state_registry=TribeStateRegistry(),
            tribe_registry=HostTribeRegistry(),
            event_bus=InMemoryTribeEventBus(),
            agent_providers=tuple(
                (f"agent-{index}", lambda agent=agent: agent)
                for index, agent in enumerate(agents)
            ),
        )
        for agents in agents_by_runtime
    ]
    owners = [object(), object()]
    ports = [TribeApplicationPorts(), TribeApplicationPorts()]
    start = threading.Barrier(3)
    result_lock = threading.Lock()
    outcomes: list[tuple[int, str]] = []

    def bind(index: int) -> None:
        start.wait()
        try:
            runtimes[index].bind_application_ports(
                owner=owners[index],
                ports=ports[index],
            )
        except RuntimeError as exc:
            outcome = str(exc)
        else:
            outcome = "BOUND"
        with result_lock:
            outcomes.append((index, outcome))

    workers = [threading.Thread(target=bind, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    start.wait()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    bound = [(index, outcome) for index, outcome in outcomes if outcome == "BOUND"]
    rejected = [(index, outcome) for index, outcome in outcomes if outcome != "BOUND"]
    assert len(bound) == 2
    assert rejected == []
    for index, agents in enumerate(agents_by_runtime):
        assert all(agent.application_ports is ports[index] for agent in agents)
        assert runtimes[index].unbind_application_ports(owner=owners[index])


def test_app_tribe_scope_rolls_back_context_when_agent_resolution_fails() -> None:
    from host_brain.app.tribe_runtime_scope import TribeRuntimeScope
    from host_brain.tribe.application_ports import TribeApplicationPorts
    from host_brain.tribe.event_bus import InMemoryTribeEventBus
    from host_brain.tribe.registry import HostTribeRegistry
    from host_brain.tribe.runtime_context import active_tribe_runtime_context
    from host_brain.tribe.tribe_state import TribeStateRegistry

    first = SimpleNamespace(application_ports=None, state_registry=object())
    previous_registry = first.state_registry

    def unavailable() -> None:
        raise LookupError("fixture unavailable")

    runtime = TribeRuntimeScope(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=InMemoryTribeEventBus(),
        agent_providers=(
            ("first", lambda: first),
            ("unavailable", unavailable),
        ),
    )

    with pytest.raises(RuntimeError, match="TRIBE_AGENT_PROVIDER_UNAVAILABLE"):
        runtime.bind_application_ports(
            owner=object(),
            ports=TribeApplicationPorts(),
        )

    assert active_tribe_runtime_context() is None
    assert first.application_ports is None
    assert first.state_registry is previous_registry
    assert runtime.application_ports is None


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

    assert len(named_import_modules) == 0
    assert sum(named_import_modules.values()) == 0
    assert len(all_import_modules) == 0
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
        ROOT / "host_brain" / "app" / "internal_security_immune_routes.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "chat_retrieval_helpers.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "composition" / "host_application.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "conversation_writer.py"
    ) not in all_import_modules
    assert (
        ROOT / "host_brain" / "app" / "vts_emotion_driver.py"
    ) not in all_import_modules
    assert not (ROOT / "host_brain" / "app" / "intelligent_runtime.py").exists()
    assert (ROOT / "host_brain" / "app" / "asgi.py") not in named_import_modules
