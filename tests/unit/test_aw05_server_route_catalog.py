from __future__ import annotations

import inspect
import os

import pytest

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
from host_brain.model_runtime.production_route_policy import (
    build_production_server_route_catalog,
)
from host_brain.model_runtime.live_prompts import (
    CODE_BENEFIT_EXECUTION_PROMPT_ID,
    CODE_BENEFIT_GENERATION_PARAMETERS,
)


_ROUTE_ENV_PREFIXES = (
    "HOST_MODEL_ROUTE_",
    "HOST_OPENAI_",
    "HOST_GEMINI_",
    "HOST_DEEPSEEK_",
    "HOST_CODEX_",
    "HOST_MINIMAX_",
    "HOST_CHAT_LOCAL_LLM_",
    "HOST_CHAT_HOST_14B_",
    "HOST_CHAT_AGENT_",
    "HOST_REALTIME_COMPANION_",
    "HOST_SURFACE_REPLY_",
    "HOST_SUBAGENT_",
    "HOST_NATSUME_AGENT_",
    "HOST_EXECUTIVE_ROUTER_",
    "HOST_DISPATCH_AGENT_",
    "HOST_VISION_LLM_",
    "HOST_MULTIMODAL_",
)
_ROUTE_ENV_KEYS = frozenset(
    {
        "HOST_LLM_PROVIDER",
        "HOST_LLM_TIMEOUT_MS",
        "HOST_OPENAI_MODEL_ID",
        "HOST_GEMINI_MODEL_ID",
        "HOST_DEEPSEEK_MODEL_ID",
        "HOST_CODEX_MODEL_ID",
        "HOST_MINIMAX_MODEL_ID",
        "HOST_CHAT_FORCE_CLOUD",
    }
)


@pytest.fixture(autouse=True)
def _isolated_route_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep higher-precedence route lanes from leaking across test modules."""

    for name in tuple(os.environ):
        if name in _ROUTE_ENV_KEYS or name.startswith(_ROUTE_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)


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

    catalog = ServerRouteCatalog()
    surface = resolve_surface_reply_llm_runtime(
        catalog=catalog,
        base_runtime={"api_key": "ignored"},
    )
    subagent = resolve_subagent_llm_runtime(
        catalog=catalog,
        base_runtime={"base_url_override": "ignored"},
    )

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


def test_realtime_companion_uses_tier_b_without_surface_pro_override(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_LLM_TIMEOUT_MS", "8000")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-v4-pro")
    monkeypatch.setenv("HOST_SURFACE_REPLY_MODEL_ID", "deepseek-v4-pro")
    monkeypatch.setenv("HOST_SURFACE_REPLY_TIMEOUT_MS", "45000")
    for name in (
        "HOST_MODEL_ROUTE_SCENE_CHAT_SURFACE_REALTIME_TIER_B_MODEL_ID",
        "HOST_MODEL_ROUTE_TIER_B_MODEL_ID",
        "HOST_MODEL_ROUTE_SCENE_CHAT_SURFACE_REALTIME_MODEL_ID",
        "HOST_DEEPSEEK_REALTIME_COMPANION_MODEL_ID",
        "HOST_REALTIME_COMPANION_MODEL_ID",
        "HOST_CHAT_AGENT_MODEL_ID",
        "HOST_REALTIME_COMPANION_TIMEOUT_MS",
        "HOST_CHAT_AGENT_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)

    realtime = resolve_surface_reply_llm_runtime(
        catalog=ServerRouteCatalog(),
        base_runtime={"runtime_path": "realtime_companion"},
    )
    governed = resolve_surface_reply_llm_runtime(
        catalog=ServerRouteCatalog(),
        base_runtime={},
    )

    assert realtime["scene_type"] == "CHAT_SURFACE_REALTIME"
    assert realtime["route_lane"] == "realtime_companion"
    assert realtime["model_id"] == "deepseek-v4-flash"
    assert realtime["timeout_ms"] == 15000
    assert realtime["model_selection_source"] == "provider_fallback"
    assert governed["scene_type"] == "CHAT_SURFACE"
    assert governed["model_id"] == "deepseek-v4-pro"
    assert governed["timeout_ms"] == 45000

    production_realtime = build_production_server_route_catalog().resolve(
        scene_type="CHAT_SURFACE_REALTIME",
        cognitive_tier="B",
        prompt_id="surface.main_chain.live.json",
    )
    assert production_realtime["generation_parameters"]["max_tokens"] == 640
    assert production_realtime["generation_parameters"]["thinking_mode"] == "disabled"


def test_code_benefit_prompt_uses_a_bounded_non_thinking_profile(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_MODEL_ROUTE_SCENE_CODE_BENCHMARK_TIMEOUT_MS", "30000")

    route = build_production_server_route_catalog().resolve(
        scene_type="CODE_BENCHMARK",
        cognitive_tier="RESEARCH",
        prompt_id=CODE_BENEFIT_EXECUTION_PROMPT_ID,
    )

    assert route["timeout_ms"] == 30000
    assert route["generation_parameters"] == dict(CODE_BENEFIT_GENERATION_PARAMETERS)


def test_realtime_companion_accepts_dedicated_provider_override(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv(
        "HOST_DEEPSEEK_REALTIME_COMPANION_MODEL_ID",
        "deepseek-realtime-custom",
    )
    monkeypatch.setenv("HOST_REALTIME_COMPANION_TIMEOUT_MS", "7200")

    route = resolve_surface_reply_llm_runtime(
        catalog=ServerRouteCatalog(),
        base_runtime={"runtime_path": "realtime_companion"},
    )

    assert route["model_id"] == "deepseek-realtime-custom"
    assert route["model_selection_source"] == (
        "HOST_DEEPSEEK_REALTIME_COMPANION_MODEL_ID"
    )
    assert route["timeout_ms"] == 7200


def test_realtime_companion_accepts_dedicated_provider_lane(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_REALTIME_COMPANION_PROVIDER", "gemini")
    monkeypatch.delenv(
        "HOST_MODEL_ROUTE_SCENE_CHAT_SURFACE_REALTIME_PROVIDER",
        raising=False,
    )

    route = resolve_surface_reply_llm_runtime(
        catalog=ServerRouteCatalog(),
        base_runtime={"runtime_path": "realtime_companion"},
    )

    assert route["provider_name"] == "gemini"
    assert route["model_id"] == "gemini-2.5-flash-lite"
    assert route["route_lane"] == "realtime_companion"


def test_natsume_agent_has_independent_model_timeout_lane(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_LLM_TIMEOUT_MS", "8000")

    route = build_production_server_route_catalog().resolve(
        scene_type="NATSUME_AGENT",
        cognitive_tier="B",
        prompt_id="agent.react.v1",
    )

    assert route["route_lane"] == "agent"
    assert route["model_id"] == "deepseek-v4-flash"
    assert route["timeout_ms"] == 45000
    assert route["generation_parameters"]["max_tokens"] == 2400

    monkeypatch.setenv("HOST_NATSUME_AGENT_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_NATSUME_AGENT_MODEL_TIMEOUT_MS", "61000")
    overridden = build_production_server_route_catalog().resolve(
        scene_type="NATSUME_AGENT",
        cognitive_tier="B",
        prompt_id="agent.react.v1",
    )
    assert overridden["model_id"] == "deepseek-v4-flash"
    assert overridden["timeout_ms"] == 61000


@pytest.mark.parametrize("tier", ("A", "B", "C", "D"))
def test_default_text_tiers_use_non_thinking_deepseek_v4_flash(
    monkeypatch,
    tier: str,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")

    route = build_production_server_route_catalog().resolve(
        scene_type="NATSUME_AGENT",
        cognitive_tier=tier,
        prompt_id="agent.react.v1",
    )

    assert route["provider_name"] == "deepseek"
    assert route["model_id"] == "deepseek-v4-flash"
    assert route["generation_parameters"]["thinking_mode"] == "disabled"


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


def test_multimodal_lane_isolated_from_global_text_provider(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_MULTIMODAL_PROVIDER", "gemini")
    monkeypatch.setenv("HOST_MULTIMODAL_MODEL_ID_SYNC", "gemini-3.6-flash")
    monkeypatch.setenv("HOST_MULTIMODAL_TIMEOUT_MS", "23000")

    multimodal = ServerRouteCatalog().resolve(
        scene_type="MULTIMODAL",
        cognitive_tier="B",
        prompt_id="multimodal.observer.json",
    )
    surface = ServerRouteCatalog().resolve(
        scene_type="CHAT_SURFACE_REALTIME",
        cognitive_tier="B",
        prompt_id="chat.surface.realtime",
    )

    assert multimodal["provider_name"] == "gemini"
    assert multimodal["model_id"] == "gemini-3.6-flash"
    assert multimodal["model_selection_source"] == "HOST_MULTIMODAL_MODEL_ID_SYNC"
    assert multimodal["timeout_ms"] == 23000
    assert multimodal["route_lane"] == "multimodal"
    assert surface["provider_name"] == "deepseek"
    assert surface["model_id"] == "deepseek-v4-flash"
    assert surface["generation_parameters"]["thinking_mode"] == "disabled"


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
    projected = resolve_host_llm_runtime(
        catalog=ServerRouteCatalog(),
        rollout_key="ignored",
        scene_type="CHAT",
    )

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
    source = inspect.getsource(route_runtime)
    assert "ServerRouteCatalog(" not in source
    assert "process_services" not in source
