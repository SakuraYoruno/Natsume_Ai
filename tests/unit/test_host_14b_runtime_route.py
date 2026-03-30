from __future__ import annotations

from types import SimpleNamespace

import host_brain.app.graph_config_builder as graph_builder
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    HOST_CHAT_ROUTE_CLOUD,
    HOST_CHAT_ROUTE_HOST_14B,
    SCENE_EXEC_INTENT,
    SCENE_TYPE_AUTO,
    build_chat_graph_config,
)


def _mock_config_result(*, provider: str = "gemini", timeout_ms: int = 15_000, api_key: str | None = "sk-gemini"):
    return SimpleNamespace(
        success=True,
        config=SimpleNamespace(
            llm_provider=provider,
            llm_timeout_ms=timeout_ms,
            llm_api_key=api_key,
        ),
    )


def _patch_runtime_dependencies(monkeypatch, *, provider: str = "gemini", api_key: str | None = "sk-gemini") -> None:
    monkeypatch.setattr(
        graph_builder,
        "load_host_config",
        lambda: _mock_config_result(provider=provider, api_key=api_key),
    )
    monkeypatch.setattr(
        graph_builder,
        "resolve_host_llm_model_id",
        lambda provider_name: f"{provider_name}-cloud-model",
    )
    monkeypatch.setattr(
        graph_builder,
        "build_host_provider_fn",
        lambda **kwargs: {
            "provider_name": kwargs.get("provider_name"),
            "model_id": kwargs.get("model_id"),
        },
    )


def test_resolve_host_llm_runtime_defaults_to_host_14b_in_non_gaming(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_MODEL_ID", "qwen3:8b-q4_k_m")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("HOST_CHAT_HOST_14B_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_HOST_14B
    assert runtime["route_reason_code"] == "HOST_CHAT_HOST_14B_ACTIVE"
    assert runtime["provider_name"] == "openai"
    assert runtime["model_id"] == "qwen3:8b-q4_k_m"
    assert runtime["llm_api_key"] == "host-local-placeholder-key"
    assert runtime["chat_base_url_override"] == "http://127.0.0.1:11434/v1"
    assert runtime["base_url_override"] == "http://127.0.0.1:11434/v1"
    assert runtime["cloud_provider_name"] == "gemini"
    assert runtime["cloud_model_id"] == "gemini-cloud-model"


def test_resolve_host_llm_runtime_gaming_mode_forces_cloud(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "gaming")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CHAT_HOST_14B_DISABLED_GAMING_MODE"
    assert runtime["provider_name"] == "gemini"
    assert runtime["model_id"] == "gemini-cloud-model"


def test_resolve_host_llm_runtime_force_cloud_switch(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_FORCE_CLOUD", "true")

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CHAT_FORCE_CLOUD"
    assert runtime["provider_name"] == "gemini"


def test_resolve_host_llm_runtime_unhealthy_host_14b_falls_back_to_cloud(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_READY", "false")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CHAT_HOST_14B_UNHEALTHY"
    assert runtime["provider_name"] == "gemini"


def test_resolve_host_llm_runtime_gray_rollout_bucket_excluded_forces_cloud(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.setenv("HOST_CHAT_GRAY_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_GRAY_PERCENT", "0")
    monkeypatch.setenv("HOST_CHAT_GRAY_SCENES", "CHAT")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("HOST_CHAT_GRAY_USER_ALLOWLIST", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime(
        rollout_key="session::gray::excluded",
        scene_type="CHAT",
    )

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CHAT_GRAY_BUCKET_EXCLUDED"
    assert bool(runtime["gray_rollout"]["enabled"]) is True
    assert bool(runtime["gray_rollout"]["in_rollout"]) is False


def test_resolve_host_llm_runtime_gray_rollout_allowlist_overrides_percent(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.setenv("HOST_CHAT_GRAY_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_GRAY_PERCENT", "0")
    monkeypatch.setenv("HOST_CHAT_GRAY_SCENES", "CHAT")
    monkeypatch.setenv("HOST_CHAT_GRAY_USER_ALLOWLIST", "session::gray::allow")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime(
        rollout_key="session::gray::allow",
        scene_type="CHAT",
    )

    assert runtime["route_target"] == HOST_CHAT_ROUTE_HOST_14B
    assert runtime["route_reason_code"] == "HOST_CHAT_HOST_14B_ACTIVE"
    assert bool(runtime["gray_rollout"]["allowlist_hit"]) is True
    assert runtime["gray_rollout"]["reason_code"] == "HOST_CHAT_GRAY_ALLOWLIST_MATCH"


def test_resolve_host_llm_runtime_gray_rollout_scene_excluded_forces_cloud(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.setenv("HOST_CHAT_GRAY_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_GRAY_PERCENT", "100")
    monkeypatch.setenv("HOST_CHAT_GRAY_SCENES", "CHAT")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("HOST_CHAT_GRAY_USER_ALLOWLIST", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime(
        rollout_key="session::gray::work-code",
        scene_type="WORK_CODE",
    )

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CHAT_GRAY_SCENE_EXCLUDED"
    assert runtime["gray_rollout"]["scene_type"] == "WORK_CODE"


def test_resolve_host_llm_runtime_routing_matrix_reasoning_scene_overrides_to_cloud(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, provider="gemini", api_key="sk-gemini")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_MODEL_ID", "qwen3:8b-q4_k_m")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_FORCE_CLOUD", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime(scene_type=SCENE_EXEC_INTENT)

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["provider_name"] == "gemini"
    assert runtime["model_id"] == "gemini-cloud-model"
    routing_matrix_decision = dict(runtime.get("routing_matrix_decision") or {})
    assert routing_matrix_decision.get("task_type") == "reasoning"
    assert routing_matrix_decision.get("selected_model") == "cloud"
    assert bool(routing_matrix_decision.get("runtime_applied")) is True
    assert routing_matrix_decision.get("runtime_selected_target") == HOST_CHAT_ROUTE_CLOUD


def test_build_chat_graph_config_uses_host_14b_only_for_chat_direct(monkeypatch) -> None:
    captured_calls: list[dict] = []

    def _capture_provider_for_node(**kwargs):
        captured_calls.append(dict(kwargs))
        return {
            "provider_name": kwargs.get("provider_name"),
            "model_id": kwargs.get("model_id"),
            "node_id": kwargs.get("node_id"),
            "base_url_override": kwargs.get("base_url_override"),
        }

    monkeypatch.setattr(
        graph_builder,
        "build_host_provider_fn_for_node",
        _capture_provider_for_node,
    )

    cfg = build_chat_graph_config(
        provider_name="openai",
        model_id="qwen3:8b-q4_k_m",
        timeout_ms=10_000,
        available=True,
        api_key="host-local-placeholder-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_TYPE_AUTO,
        scene_type_source="graph_entry_router",
        cloud_provider_name="gemini",
        cloud_model_id="gemini-2.5-pro",
        cloud_timeout_ms=12_000,
        cloud_api_key="sk-gemini",
        cloud_available=True,
        chat_base_url_override="http://127.0.0.1:11434/v1",
    )

    overrides = cfg["model_node_overrides"]
    assert overrides[CHAT_DIRECT_NODE_ID]["provider"] == "openai"
    assert overrides[CHAT_DIRECT_NODE_ID]["model_id"] == "qwen3:8b-q4_k_m"
    assert overrides[CHAT_DIRECT_NODE_ID]["provider_fn"]["base_url_override"] == "http://127.0.0.1:11434/v1"
    assert overrides["core_reasoner_primary"]["provider"] == "gemini"
    assert overrides["core_reasoner_primary"]["provider_fn"]["base_url_override"] is None
    assert cfg["graph_meta"]["chat_provider"] == "openai"
    assert cfg["graph_meta"]["cloud_provider"] == "gemini"
    assert any(
        row.get("node_id") == CHAT_DIRECT_NODE_ID and row.get("base_url_override") == "http://127.0.0.1:11434/v1"
        for row in captured_calls
    )
