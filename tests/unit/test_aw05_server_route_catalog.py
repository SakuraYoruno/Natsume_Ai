from __future__ import annotations

import inspect

from host_brain.app.route_catalog_runtime import (
    resolve_host_llm_runtime,
    resolve_subagent_llm_runtime,
    resolve_surface_reply_llm_runtime,
)
from host_brain.model_runtime.route_catalog import ServerRouteCatalog


def test_route_catalog_is_secret_free_and_credential_independent(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-catalog")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-api-key")
    monkeypatch.setenv("HOST_GEMINI_BASE_URL", "https://secret-transport.invalid")

    route = ServerRouteCatalog().resolve(
        scene_type="EXEC_INTENT",
        cognitive_tier="C",
        prompt_id="test.prompt",
    )

    assert route["provider_name"] == "gemini"
    assert route["model_id"] == "gemini-catalog"
    assert route["timeout_ms"] == 5000
    rendered = repr(route)
    assert "secret-api-key" not in rendered
    assert "secret-transport.invalid" not in rendered
    assert all(token not in rendered for token in ("api_key", "base_url", "credential"))


def test_scene_lanes_preserve_configured_provider_model_and_timeout(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_SURFACE_REPLY_MODEL_ID", "surface-model")
    monkeypatch.setenv("HOST_SURFACE_REPLY_TIMEOUT_MS", "31000")
    monkeypatch.setenv("HOST_SUBAGENT_MODEL_ID", "subagent-model")
    monkeypatch.setenv("HOST_SUBAGENT_TIMEOUT_MS", "32000")

    surface = resolve_surface_reply_llm_runtime(base_runtime={"api_key": "ignored"})
    subagent = resolve_subagent_llm_runtime(base_runtime={"base_url_override": "ignored"})

    assert (surface["provider_name"], surface["model_id"], surface["timeout_ms"]) == (
        "deepseek",
        "surface-model",
        31000,
    )
    assert (subagent["provider_name"], subagent["model_id"], subagent["timeout_ms"]) == (
        "deepseek",
        "subagent-model",
        32000,
    )
    assert surface["agent_role"] == "surface_reply"
    assert subagent["agent_role"] == "sub_agent"
    assert "ignored" not in repr(surface) + repr(subagent)


def test_chat_graph_runtime_projection_uses_same_catalog(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_MODEL_ID", "qwen-local")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_TIMEOUT_MS", "91000")
    monkeypatch.setenv("OPENAI_API_KEY", "adapter-only")

    direct = ServerRouteCatalog().resolve(
        scene_type="CHAT",
        cognitive_tier="B",
        prompt_id="",
    )
    projected = resolve_host_llm_runtime(rollout_key="ignored", scene_type="CHAT")

    for field in ("provider_name", "model_id", "timeout_ms", "route_target"):
        assert projected[field] == direct[field]
    assert projected["chat_model_id"] == "qwen-local"
    assert projected["cloud_provider_name"] == "gemini"
    assert "adapter-only" not in repr(projected)


def test_production_route_policy_has_no_app_or_legacy_provider_import() -> None:
    import host_brain.model_runtime.production_route_policy as module

    source = inspect.getsource(module)
    for forbidden in (
        "host_brain.app.graph_config_builder",
        "host_brain.app.graph_config_llm_runtime",
        "host_brain.app.provider_factory",
    ):
        assert forbidden not in source
