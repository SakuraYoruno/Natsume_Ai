from __future__ import annotations

import inspect

from host_brain.composition.process_services import get_model_route_catalog
import host_brain.app.route_catalog_runtime as route_runtime
from host_brain.app.route_catalog_runtime import (
    resolve_host_llm_runtime,
    resolve_subagent_llm_runtime,
    resolve_surface_reply_llm_runtime,
)
from host_brain.model_runtime.route_catalog import ServerRouteCatalog
from host_brain.model_runtime.production_composition import (
    get_default_production_route_catalog,
)


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


def test_cross_provider_role_models_are_ignored_for_selected_provider(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-runtime-default")
    monkeypatch.setenv("HOST_EXECUTIVE_ROUTER_MODEL_ID", "gemini-2.5-flash-lite")
    monkeypatch.setenv("HOST_SUBAGENT_MODEL_ID", "gemini-2.5-pro")

    executive = ServerRouteCatalog().resolve(
        scene_type="THALAMUS_INTENT",
        cognitive_tier="C",
        prompt_id="test.prompt",
    )
    subagent = ServerRouteCatalog().resolve(
        scene_type="SUBAGENT",
        cognitive_tier="B",
        prompt_id="test.prompt",
    )

    assert executive["model_id"] == "deepseek-runtime-default"
    assert subagent["model_id"] == "deepseek-runtime-default"
    assert any("HOST_EXECUTIVE_ROUTER_MODEL_ID" in item for item in executive["route_config_warnings"])
    assert any("HOST_SUBAGENT_MODEL_ID" in item for item in subagent["route_config_warnings"])


def test_provider_scoped_role_model_wins_before_generic_role_model(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_SUBAGENT_MODEL_ID", "deepseek-specialized-agent")
    monkeypatch.setenv("HOST_SUBAGENT_MODEL_ID", "gemini-2.5-pro")

    route = ServerRouteCatalog().resolve(
        scene_type="SUBAGENT",
        cognitive_tier="B",
        prompt_id="test.prompt",
    )

    assert route["model_id"] == "deepseek-specialized-agent"
    assert route["model_selection_source"] == "HOST_DEEPSEEK_SUBAGENT_MODEL_ID"
    assert route["route_config_warnings"] == []


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


def test_runtime_projection_uses_composition_owned_catalog() -> None:
    catalog = get_default_production_route_catalog()

    assert get_model_route_catalog() is catalog
    assert "ServerRouteCatalog(" not in inspect.getsource(route_runtime)
