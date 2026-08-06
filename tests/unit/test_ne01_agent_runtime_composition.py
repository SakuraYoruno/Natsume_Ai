from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from host_brain.app import agent_runtime_composition
from host_brain.app.agent_runtime_composition import (
    AgentRuntimeApplicationScope,
    AgentRuntimeCompositionError,
    build_agent_runtime_application_ports,
)
from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    HostLifecycleScopeError,
)


def _host_services(**overrides: object) -> HostLifecycleApplicationPorts:
    provider = lambda: object()
    factory = lambda **_kwargs: object()
    values: dict[str, object] = {
        "memory_guard": provider,
        "state_arbiter": provider,
        "distillation_engine": provider,
        "semantic_recaller": provider,
        "episodic_store": provider,
        "skill_registry": provider,
        "tool_catalog": provider,
        "capability_broker": provider,
        "model_attempt_service": provider,
        "mode_transition_service_factory": factory,
        "activity_monitor_factory": factory,
        "resource_manager_factory": factory,
        "ledger": provider,
        "life_memory_store": provider,
        "life_memory_service": provider,
        "conversation_history": provider,
        "conversation_turn_store": provider,
        "prompt_registry": provider,
        "model_route_catalog": provider,
        "session_topic_ledger_store": provider,
        "session_tactic_ledger_store": provider,
        "tts_context_router": provider,
        "meta_learning_loop": provider,
    }
    values.update(overrides)
    return HostLifecycleApplicationPorts(**values)  # type: ignore[arg-type]


def test_agent_runtime_scope_owns_one_lifespan_bundle_and_injects_life_authority(
    monkeypatch,
) -> None:
    ledger = object()
    life_service = object()
    life_reader = object()
    captured: dict[str, object] = {}
    host_services = _host_services(ledger=lambda: ledger)
    scope = AgentRuntimeApplicationScope(host_services=host_services)

    def fake_record_user_turn(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return {"status": "OK"}

    monkeypatch.setattr(
        agent_runtime_composition,
        "record_user_turn",
        fake_record_user_turn,
    )

    assert scope.provider() is None
    ports = scope.initialize(
        life_authority_provider=lambda: (life_service, life_reader),
    )
    assert scope.provider() is ports
    assert callable(ports.stream_realtime_chat)
    assert ports.record_user_turn(
        request_id="request-ne01-agent-runtime-ports",
        session_id="session-ne01-agent-runtime-ports",
        person_id="person-ne01-agent-runtime-ports",
        content="accepted input",
        ts_ms=1,
        lang="zh",
        metadata={},
    ) == {"status": "OK"}
    assert captured["ledger"] is ledger
    assert captured["life_service"] is life_service
    assert captured["life_reader"] is life_reader

    with pytest.raises(
        AgentRuntimeCompositionError,
        match="HOST_AGENT_RUNTIME_PORTS_ALREADY_INITIALIZED",
    ):
        scope.initialize(life_authority_provider=lambda: (life_service, life_reader))

    scope.clear()
    assert scope.provider() is None
    assert scope.initialize(
        life_authority_provider=lambda: (life_service, life_reader),
    ) is not ports


def test_agent_runtime_composition_fails_closed_for_missing_host_or_life_authority() -> None:
    with pytest.raises(
        HostLifecycleScopeError,
        match="HOST_SERVICE_PROVIDER_NOT_INJECTED:life_memory_store",
    ):
        build_agent_runtime_application_ports(
            host_services=_host_services(life_memory_store=None),
            life_authority_provider=lambda: (object(), object()),
        )

    ports = build_agent_runtime_application_ports(
        host_services=_host_services(),
        life_authority_provider=lambda: (None, None),
    )
    with pytest.raises(
        AgentRuntimeCompositionError,
        match="HOST_AGENT_RUNTIME_LIFE_AUTHORITY_UNAVAILABLE",
    ):
        ports.record_user_turn(
            request_id="request-ne01-missing-life",
            session_id="session-ne01-missing-life",
            person_id="person-ne01-missing-life",
            content="input",
            ts_ms=1,
            lang="zh",
            metadata={},
        )


def test_agent_runtime_scope_resolves_nested_dependencies_from_host_bundle(
    monkeypatch,
) -> None:
    class RouteCatalog:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def resolve(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            return {
                "provider_name": "host-scoped-provider",
                "model_id": "host-scoped-model",
                "timeout_ms": 321,
                "available": True,
            }

    ledger = object()
    topic_store = object()
    tactic_store = object()
    catalog = RouteCatalog()
    captured: dict[str, object] = {}

    def fake_record_assistant_turn(**kwargs):  # noqa: ANN003, ANN202
        captured["assistant"] = kwargs
        return {"status": "OK"}

    monkeypatch.setattr(
        agent_runtime_composition,
        "record_assistant_turn",
        fake_record_assistant_turn,
    )

    host_services = _host_services(
        ledger=lambda: ledger,
        model_route_catalog=lambda: catalog,
        session_topic_ledger_store=lambda: topic_store,
        session_tactic_ledger_store=lambda: tactic_store,
    )
    ports = build_agent_runtime_application_ports(
        host_services=host_services,
        life_authority_provider=lambda: (object(), object()),
    )
    assert ports.context_read_ports.session_topic_ledger_store() is topic_store
    assert ports.context_read_ports.session_tactic_ledger_store() is tactic_store

    assert ports.record_assistant_turn(
        request_id="request-scoped-dependencies",
        session_id="session-scoped-dependencies",
        person_id="person-scoped-dependencies",
        content="accepted reply",
        ts_ms=1,
        lang="zh",
        metadata={},
    ) == {"status": "OK"}
    assistant_kwargs = dict(captured["assistant"])
    assert assistant_kwargs["ledger"] is ledger
    assert assistant_kwargs["session_topic_ledger_store"] is topic_store
    assert assistant_kwargs["session_tactic_ledger_store"] is tactic_store

    runtime = asyncio.run(
        ports.resolve_host_llm_runtime_async(
            rollout_key="session-scoped-dependencies",
            scene_type="CHAT",
        )
    )
    assert runtime["chat_provider_name"] == "host-scoped-provider"
    assert [call["scene_type"] for call in catalog.calls] == ["CHAT", "MULTIMODAL"]


def test_agent_runtime_composition_binds_chat_retrieval_to_host_scope(
    monkeypatch,
) -> None:
    ledger = object()
    life_memory_store = object()
    chroma_client = object()
    episodic_store = type("EpisodicStore", (), {"chroma": chroma_client})()
    conversation_history = object()
    recall_service = object()
    captured: dict[str, object] = {}
    recall_service_builds: list[dict[str, object]] = []
    legacy_bond_client_calls: list[str] = []

    async def fake_run_retrieval(**kwargs):  # noqa: ANN003, ANN202
        captured["retrieval"] = kwargs
        return {"status": "OK"}, []

    def fake_build_recall_service(**kwargs):  # noqa: ANN003, ANN202
        captured["recall_service"] = kwargs
        recall_service_builds.append(dict(kwargs))
        return recall_service

    def fake_build_user_preference_pack(**kwargs):  # noqa: ANN003, ANN202
        captured["preference_pack"] = kwargs
        return {"status": "OK"}

    monkeypatch.setattr(agent_runtime_composition, "_run_retrieval", fake_run_retrieval)
    monkeypatch.setattr(
        agent_runtime_composition,
        "_build_surface_memory_recall_service",
        fake_build_recall_service,
    )
    monkeypatch.setattr(
        agent_runtime_composition,
        "build_user_preference_pack",
        fake_build_user_preference_pack,
    )
    ports = build_agent_runtime_application_ports(
        host_services=_host_services(
            ledger=lambda: ledger,
            life_memory_store=lambda: life_memory_store,
            episodic_store=lambda: episodic_store,
            conversation_history=lambda: conversation_history,
            bond_neo4j_client=lambda: legacy_bond_client_calls.append("resolved"),
        ),
        life_authority_provider=lambda: (object(), object()),
    )

    assert asyncio.run(
        ports.run_retrieval(
            request_id="request-chat-retrieval-scope",
            message="回顾刚才的约定",
            person_id="person-chat-retrieval-scope",
            session_id="session-chat-retrieval-scope",
            ts_ms=1,
            emotion_tag="NEUTRAL",
            render_target_lang="zh",
        )
    ) == ({"status": "OK"}, [])

    retrieval_kwargs = dict(captured["retrieval"])
    assert retrieval_kwargs["life_memory_store"] is life_memory_store
    assert retrieval_kwargs["conversation_history"] is conversation_history
    assert callable(retrieval_kwargs["build_user_preference_pack"])
    assert callable(retrieval_kwargs["recall_service_factory"])
    assert retrieval_kwargs["build_user_preference_pack"](
        person_id="person-chat-retrieval-scope",
        query_text="回顾刚才的约定",
    ) == {"status": "OK"}
    assert dict(captured["preference_pack"])["ledger"] is ledger
    assert retrieval_kwargs["recall_service_factory"]() is recall_service
    assert retrieval_kwargs["recall_service_factory"]() is recall_service
    assert dict(captured["recall_service"]) == {
        "ledger": ledger,
        "life_memory_store": life_memory_store,
        "chroma_client": chroma_client,
    }
    assert len(recall_service_builds) == 1
    assert legacy_bond_client_calls == []


def test_agent_runtime_scope_fails_closed_when_nested_host_provider_is_missing() -> None:
    with pytest.raises(
        HostLifecycleScopeError,
        match="HOST_SERVICE_PROVIDER_NOT_INJECTED:model_route_catalog",
    ):
        build_agent_runtime_application_ports(
            host_services=_host_services(model_route_catalog=None),
            life_authority_provider=lambda: (object(), object()),
        )


def test_agent_runtime_ports_exclude_legacy_direct_effect_callbacks() -> None:
    ports = build_agent_runtime_application_ports(
        host_services=_host_services(),
        life_authority_provider=lambda: (object(), object()),
    )

    assert not hasattr(ports, "apply_surface_emotion_bond_flow")
    assert not hasattr(ports, "drive_stream_vts_emotion")
    assert not hasattr(ports, "resolve_tts_route")


def test_production_adapters_do_not_import_the_retired_global_port_bundle() -> None:
    root = Path(__file__).resolve().parents[2]
    production_files = (
        "host_brain/app/cortex_orchestrator.py",
        "host_brain/app/input_runtime_composition.py",
        "host_brain/app/life_projection_handlers.py",
        "host_brain/app/acceptance_effect_handlers.py",
        "host_brain/app/sub_agent_protocol.py",
    )
    for relative in production_files:
        source = (root / relative).read_text(encoding="utf-8-sig")
        assert "host_brain.app.agent_runtime_shared" not in source, relative
        assert "import AGENT_RUNTIME_PORTS" not in source, relative
        assert ".AGENT_RUNTIME_PORTS" not in source, relative

    retired_source = (root / "host_brain/app/agent_runtime_shared.py").read_text(
        encoding="utf-8-sig"
    )
    assert "AGENT_RUNTIME_PORTS" not in retired_source
