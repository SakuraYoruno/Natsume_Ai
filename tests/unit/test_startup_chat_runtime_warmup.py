from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.composition import process_services as dependencies
from host_brain.app.settings import get_persistence_settings
from host_brain.app.startup import (
    StartupApplicationPorts,
    warmup_chat_llm_runtime_profiles,
    warmup_chat_runtime_basics,
    warmup_codex_exec_intent_pool,
    warmup_retrieval_runtime_basics,
)
from host_brain.model_runtime.production_composition import (
    get_default_production_route_catalog,
)


def _ports(**overrides: object) -> StartupApplicationPorts:
    provider = lambda: object()
    values: dict[str, object] = {
        "emotion_timeline": provider,
        "skill_registry": provider,
        "graph_runtime": provider,
        "ledger": dependencies.get_ledger,
        "prompt_registry": provider,
        "routing_matrix": provider,
        "intent_engine": provider,
        "state_write_arbiter": provider,
        "silence_engine": provider,
        "model_route_catalog": get_default_production_route_catalog,
        "model_attempt_service": dependencies.get_model_attempt_service,
        "episodic_store": provider,
    }
    values.update(overrides)
    return StartupApplicationPorts(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate_startup_ledger(monkeypatch):
    monkeypatch.setenv("NATSUME_AUDIT_DB", ":memory:")
    get_persistence_settings.cache_clear()
    dependencies.get_process_service_container().ledger = None
    yield
    if dependencies.get_process_service_container().ledger is not None:
        dependencies.get_process_service_container().ledger.close()
    dependencies.get_process_service_container().ledger = None
    get_persistence_settings.cache_clear()


def test_warmup_chat_runtime_basics_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: object(),
    )

    status = warmup_chat_runtime_basics(
        ports=_ports(),
        eager_persona=True,
        thalamus_agent_provider=lambda: object(),
    )

    assert status.startswith("OK")
    assert "prompt_registry=ready" in status
    assert "routing_matrix=ready" in status
    assert "intent_engine=ready" in status
    assert "state_write_arbiter=ready" in status
    assert "thalamus=ready" in status
    assert "persona_runtime=ready" in status


def test_warmup_chat_runtime_basics_reports_degraded(monkeypatch) -> None:
    status = warmup_chat_runtime_basics(
        ports=_ports(
            intent_engine=lambda: (_ for _ in ()).throw(
                RuntimeError("intent engine unavailable")
            )
        ),
        eager_persona=False,
        thalamus_agent_provider=lambda: object(),
    )

    assert status == "DEGRADED: intent engine unavailable"


def test_warmup_chat_llm_runtime_profiles_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.route_catalog_runtime.resolve_host_llm_runtime_async",
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
        "host_brain.app.route_catalog_runtime.resolve_surface_reply_llm_runtime",
        lambda *, catalog, base_runtime: {
            **base_runtime,
            "model_id": "chat::default",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.route_catalog_runtime.resolve_executive_router_llm_runtime",
        lambda *, catalog, base_runtime: {
            **base_runtime,
            "model_id": "router::ready",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.route_catalog_runtime.resolve_subagent_llm_runtime",
        lambda *, catalog, base_runtime: {
            **base_runtime,
            "model_id": "subagent::ready",
        },
    )

    status = warmup_chat_llm_runtime_profiles(ports=_ports())

    assert status.startswith("OK")
    assert "base=gemini:gemini-2.5-flash-lite" in status
    assert "default=chat::default" in status
    assert "router=router::ready" in status
    assert "subagent=subagent::ready" in status


def test_warmup_codex_exec_intent_pool_reports_ready(monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setenv("HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED", "true")
    def _fake_complete(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            success=True,
            content="ready",
            request_id="startup-warmup",
            cached=False,
            route_decision=SimpleNamespace(
                decision_id="route-warmup",
                reason="server-owned",
                max_attempts=1,
            ),
            attempts=(),
            final_target=SimpleNamespace(
                adapter_id="production.codex",
                provider="codex",
                model_id="gpt-test",
            ),
            error_code=None,
        )

    monkeypatch.setattr(
        "host_brain.model_runtime.invocation.complete_compiled_prompt_sync",
        _fake_complete,
    )

    status = warmup_codex_exec_intent_pool(ports=_ports())

    assert status.startswith("OK")
    assert "model=gpt-test" in status
    assert calls["scene_type"] == "STARTUP_MODEL_WARMUP"
    assert calls["prompt_id"] == "startup.model_warmup.text"
    assert "model_id" not in calls


def test_warmup_codex_exec_intent_pool_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED", "false")

    status = warmup_codex_exec_intent_pool(ports=_ports())

    assert status == "SKIPPED (disabled)"


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

    status = warmup_retrieval_runtime_basics(
        ports=_ports(episodic_store=lambda: _FakeStore())
    )

    assert status.startswith("OK")
    assert "embed_model=hash-embed-v1" in status
    assert "embed_dim=3" in status
    assert "warmup_mode=chroma_hash_probe" in status
    assert "collection=episodic" in status
    assert "collection_dim=3" in status
    assert "chroma_hits=1" in status
    assert "legacy_pipeline=removed" in status
    assert "legacy_orchestrator=removed" in status
