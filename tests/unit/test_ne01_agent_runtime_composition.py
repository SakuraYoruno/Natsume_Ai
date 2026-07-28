from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

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
        "bond_neo4j_client": provider,
        "prompt_registry": provider,
        "bond_update_queue": provider,
        "model_route_catalog": provider,
        "bond_stage_engine": provider,
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

    class BondStageEngine:
        def __init__(self) -> None:
            self.session_ids: list[str] = []

        def get_bond_state(self, *, session_id: str) -> SimpleNamespace:
            self.session_ids.append(session_id)
            return SimpleNamespace(
                stage="FRIEND",
                score=0.8,
                label="host-scoped",
                interaction_count=3,
            )

    ledger = object()
    topic_store = object()
    tactic_store = object()
    tts_router = object()
    meta_learning_loop = object()
    catalog = RouteCatalog()
    bond_engine = BondStageEngine()
    captured: dict[str, object] = {}

    def fake_record_assistant_turn(**kwargs):  # noqa: ANN003, ANN202
        captured["assistant"] = kwargs
        return {"status": "OK"}

    async def fake_drive_stream_vts_emotion(**kwargs):  # noqa: ANN003, ANN202
        captured["vts"] = kwargs
        return {"status": "APPLIED"}

    def fake_resolve_tts_route(**kwargs):  # noqa: ANN003, ANN202
        captured["tts"] = kwargs
        return {"emotion_tag": "calm"}

    monkeypatch.setattr(
        agent_runtime_composition,
        "record_assistant_turn",
        fake_record_assistant_turn,
    )
    monkeypatch.setattr(
        agent_runtime_composition,
        "_drive_stream_vts_emotion",
        fake_drive_stream_vts_emotion,
    )
    monkeypatch.setattr(
        agent_runtime_composition,
        "resolve_tts_route",
        fake_resolve_tts_route,
    )
    host_services = _host_services(
        ledger=lambda: ledger,
        model_route_catalog=lambda: catalog,
        bond_stage_engine=lambda: bond_engine,
        session_topic_ledger_store=lambda: topic_store,
        session_tactic_ledger_store=lambda: tactic_store,
        tts_context_router=lambda: tts_router,
        meta_learning_loop=lambda: meta_learning_loop,
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

    bond_summary, evidence = asyncio.run(
        ports.resolve_state_arbiter_bond_fact(
            session_id="session-scoped-dependencies",
            request_id="request-scoped-dependencies",
        )
    )
    assert bond_summary["status"] == "APPLIED"
    assert evidence is not None
    assert bond_engine.session_ids == ["session-scoped-dependencies"]

    assert asyncio.run(
        ports.drive_stream_vts_emotion(
            request_id="request-scoped-dependencies",
            emotion_context={"emotion_tag": "CALM"},
            ts_ms=1,
        )
    ) == {"status": "APPLIED"}
    assert dict(captured["vts"])["ledger"] is ledger

    assert ports.resolve_tts_route(
        text="host-scoped tts",
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND"},
    ) == {"emotion_tag": "calm"}
    tts_kwargs = dict(captured["tts"])
    assert tts_kwargs["tts_context_router"] is tts_router
    assert tts_kwargs["meta_learning_loop"] is meta_learning_loop


def test_agent_runtime_composition_binds_chat_retrieval_to_host_scope(
    monkeypatch,
) -> None:
    ledger = object()
    life_memory_store = object()
    conversation_history = object()
    bond_neo4j_client = object()
    recall_service = object()
    captured: dict[str, object] = {}

    async def fake_run_retrieval(**kwargs):  # noqa: ANN003, ANN202
        captured["retrieval"] = kwargs
        return {"status": "OK"}, []

    def fake_build_recall_service(**kwargs):  # noqa: ANN003, ANN202
        captured["recall_service"] = kwargs
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
            conversation_history=lambda: conversation_history,
            bond_neo4j_client=lambda: bond_neo4j_client,
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
    assert dict(captured["recall_service"]) == {
        "ledger": ledger,
        "life_memory_store": life_memory_store,
        "bond_neo4j_client": bond_neo4j_client,
    }


def test_agent_runtime_scope_fails_closed_when_nested_host_provider_is_missing() -> None:
    with pytest.raises(
        HostLifecycleScopeError,
        match="HOST_SERVICE_PROVIDER_NOT_INJECTED:model_route_catalog",
    ):
        build_agent_runtime_application_ports(
            host_services=_host_services(model_route_catalog=None),
            life_authority_provider=lambda: (object(), object()),
        )


def test_agent_runtime_retires_surface_bond_flow_without_resolving_queue() -> None:
    def _unexpected_queue():
        raise AssertionError("canonical Agent Runtime must not resolve Bond queue")

    ports = build_agent_runtime_application_ports(
        host_services=_host_services(bond_update_queue=_unexpected_queue),
        life_authority_provider=lambda: (object(), object()),
    )

    assert ports.apply_surface_emotion_bond_flow(
        request_id="request-retired-surface-bond",
        person_id="person-retired-surface-bond",
        session_id="session-retired-surface-bond",
    ) == {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_SURFACE_BOND_FLOW_RETIRED",
    }


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
