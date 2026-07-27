from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.app import internal_runtime_composition
from host_brain.app import surface_main_chain_adapter
from host_brain.app import surface_runtime_composition
from host_brain.surface_runtime import subagent_context
from host_brain.app.surface_runtime_composition import (
    SurfaceRuntimeCompositionError,
    build_surface_runtime_ports,
)


async def _async_stub(**_kwargs: Any) -> Any:
    return None


def _agent_runtime_ports(*, ledger: Any, model_attempt_service: Any) -> AgentRuntimeApplicationPorts:
    context_read_ports = ConversationContextReadPorts(
        conversation_history=lambda: "conversation-history",
        conversation_turn_store=lambda: "conversation-turn-store",
        session_topic_ledger_store=lambda: "session-topic-ledger-store",
        session_tactic_ledger_store=lambda: "session-tactic-ledger-store",
    )
    return AgentRuntimeApplicationPorts(
        record_user_turn=lambda **_kwargs: None,
        record_assistant_turn=lambda **_kwargs: None,
        run_retrieval=_async_stub,
        filter_retrieval_evidence_items_for_prompt=lambda **_kwargs: [],
        build_user_preference_pack=lambda **_kwargs: {},
        resolve_host_llm_runtime_async=_async_stub,
        resolve_surface_reply_llm_runtime=lambda **kwargs: dict(
            kwargs.get("base_runtime") or {}
        ),
        resolve_state_arbiter_bond_fact=_async_stub,
        apply_surface_emotion_bond_flow=lambda **_kwargs: {},
        build_conversation_experience_read_pack=lambda **kwargs: {
            "source": "experience-read-pack",
            "arguments": dict(kwargs),
        },
        drive_stream_vts_emotion=_async_stub,
        resolve_tts_route=lambda **_kwargs: {},
        conversation_history=lambda: "conversation-history",
        life_memory_service=lambda: object(),
        audit_ledger=lambda: ledger,
        prompt_registry=lambda: object(),
        model_attempt_service=lambda: model_attempt_service,
        context_read_ports=context_read_ports,
        task_status_projection=lambda: object(),
        persist_post_process_audit=_async_stub,
    )


def test_surface_runtime_ports_bind_one_lifespan_authority_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = object()
    command_service = object()
    snapshot_reader = object()
    model_attempt_service = object()
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        surface_runtime_composition,
        "build_surface_context_packet",
        lambda **kwargs: observed.update(kwargs) or {"status": "BOUND"},
    )

    ports = build_surface_runtime_ports(
        agent_runtime_ports=_agent_runtime_ports(
            ledger=ledger,
            model_attempt_service=model_attempt_service,
        ),
        life_authority_provider=lambda: (command_service, snapshot_reader),
    )

    assert ports.get_ledger() is ledger
    assert ports.get_life_command_service() is command_service
    assert ports.get_life_snapshot_reader() is snapshot_reader
    assert ports.get_conversation_history() == "conversation-history"
    assert ports.build_conversation_experience_read_pack(
        session_id="session-1",
        person_id="person-1",
        query_text="只读经历",
    ) == {
        "source": "experience-read-pack",
        "arguments": {
            "session_id": "session-1",
            "person_id": "person-1",
            "query_text": "只读经历",
        },
    }
    assert ports.model_attempt_service() is model_attempt_service
    assert ports.build_surface_context_packet(user_message="显式端口") == {
        "status": "BOUND"
    }
    assert observed["life_snapshot_reader"] is snapshot_reader
    assert observed["context_read_ports"].conversation_history() == (
        "conversation-history"
    )
    assert observed["user_message"] == "显式端口"


@pytest.mark.parametrize(
    "provider",
    (
        lambda: (_ for _ in ()).throw(RuntimeError("withdrawn")),
        lambda: (None, object()),
        lambda: (object(), None),
    ),
)
def test_surface_runtime_ports_fail_closed_when_life_authority_is_unavailable(
    provider,
) -> None:
    ports = build_surface_runtime_ports(
        agent_runtime_ports=_agent_runtime_ports(
            ledger=object(),
            model_attempt_service=object(),
        ),
        life_authority_provider=provider,
    )

    with pytest.raises(
        SurfaceRuntimeCompositionError,
        match="HOST_SURFACE_LIFE_AUTHORITY_UNAVAILABLE",
    ):
        ports.get_life_snapshot_reader()


def test_surface_adapter_and_internal_composition_have_no_hidden_locator_fallback() -> None:
    adapter_source = inspect.getsource(surface_main_chain_adapter)
    composition_source = inspect.getsource(internal_runtime_composition)

    assert "host_brain.composition.process_services" not in adapter_source
    assert "build_surface_runtime_ports(" in composition_source
    assert "surface_runtime_ports=surface_runtime_ports" in composition_source


def test_internal_runtime_lifespan_runs_host_graph_recovery_after_group_activation() -> None:
    source = inspect.getsource(internal_runtime_composition.activate_internal_runtime)
    activation = source.index("await task_runtime.activate_runtime()")
    host_continuation = source.index(
        "await composition.host_runtime.recover_startup_graphs()"
    )
    control_continuation = source.index(
        "await runtime_group.recover_startup_graphs()"
    )
    scheduler = source.index("await scheduler.activate_clock()")
    assert activation < host_continuation < control_continuation < scheduler


def test_agent_and_surface_runtime_ports_expose_experience_read_pack_only() -> None:
    agent_ports_source = inspect.getsource(AgentRuntimeApplicationPorts)
    surface_ports_source = inspect.getsource(surface_runtime_composition)
    assert "build_conversation_experience_read_pack" in agent_ports_source
    assert "get_conversation_experience_store" not in agent_ports_source
    assert "build_conversation_experience_read_pack" in surface_ports_source
    assert "get_conversation_experience_store" not in surface_ports_source
    surface_context_source = inspect.getsource(subagent_context)
    assert "build_conversation_experience_read_pack" in surface_context_source
    assert "get_conversation_experience_store" not in surface_context_source
