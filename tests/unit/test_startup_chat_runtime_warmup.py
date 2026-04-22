from __future__ import annotations

from host_brain.app.startup import (
    warmup_chat_llm_runtime_profiles,
    warmup_chat_runtime_basics,
    warmup_retrieval_runtime_basics,
)


def test_warmup_chat_runtime_basics_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.prompt_template_defs.list_prompt_template_definitions",
        lambda: [object(), object(), object()],
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_prompt_template_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_routing_matrix",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_intent_engine",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_state_write_arbiter",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.thalamus_agent.get_thalamus_agent",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "host_brain.persona.emotional_silence.get_silence_engine",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: object(),
    )

    status = warmup_chat_runtime_basics(eager_persona=True)

    assert status.startswith("OK")
    assert "templates=3" in status
    assert "prompt_store=ready" in status
    assert "routing_matrix=ready" in status
    assert "intent_engine=ready" in status
    assert "state_write_arbiter=ready" in status
    assert "thalamus=ready" in status
    assert "persona_runtime=ready" in status


def test_warmup_chat_runtime_basics_reports_degraded(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_intent_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("intent engine unavailable")),
    )

    status = warmup_chat_runtime_basics(eager_persona=False)

    assert status == "DEGRADED: intent engine unavailable"


def test_warmup_chat_llm_runtime_profiles_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.resolve_host_llm_runtime_async",
        lambda **kwargs: __import__("asyncio").sleep(0, result={  # noqa: PLC421
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash-lite",
            "chat_model_id": "gemini-2.5-flash-lite",
            "timeout_ms": 12000,
            "llm_api_key": "key",
            "available": True,
        }),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.resolve_chat_agent_llm_runtime",
        lambda *, base_runtime: {
            **base_runtime,
            "model_id": "chat::default",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.resolve_executive_router_llm_runtime",
        lambda *, base_runtime: {**base_runtime, "model_id": "router::ready"},
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.resolve_subagent_llm_runtime",
        lambda *, base_runtime: {**base_runtime, "model_id": "subagent::ready"},
    )

    status = warmup_chat_llm_runtime_profiles()

    assert status.startswith("OK")
    assert "base=gemini:gemini-2.5-flash-lite" in status
    assert "default=chat::default" in status
    assert "router=router::ready" in status
    assert "subagent=subagent::ready" in status


def test_warmup_retrieval_runtime_basics_reports_ready(monkeypatch) -> None:
    class _FakeChroma:
        default_collection = "episodic"

        def search(self, **kwargs):  # noqa: ANN003
            return [{"memory_id": "m1"}]

    class _FakeStore:
        chroma = _FakeChroma()

        def _embed_text(self, text: str):  # noqa: ANN001
            return ([0.1, 0.2, 0.3], "models/Qwen3-Embedding-0.6B")

        def _resolve_embedding_dim(self) -> int:
            return 3

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_episodic_store",
        lambda: _FakeStore(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_retrieval_pipeline",
        lambda: type("Pipeline", (), {})(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_retrieval_orchestrator",
        lambda: type("Orchestrator", (), {})(),
    )

    status = warmup_retrieval_runtime_basics()

    assert status.startswith("OK")
    assert "embed_model=models/Qwen3-Embedding-0.6B" in status
    assert "embed_dim=3" in status
    assert "collection=episodic" in status
    assert "collection_dim=3" in status
    assert "chroma_hits=1" in status
