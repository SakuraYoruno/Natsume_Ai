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


def _mock_config_result(
    *,
    timeout_ms: int = 15_000,
    gemini_api_key: str | None = "sk-gemini",
    codex_api_key: str | None = "sk-codex",
    deepseek_api_key: str | None = "sk-deepseek",
):
    return SimpleNamespace(
        success=True,
        config=SimpleNamespace(
            llm_provider="gemini",
            llm_timeout_ms=timeout_ms,
            llm_api_key=gemini_api_key,
            gemini_api_key=gemini_api_key,
            codex_api_key=codex_api_key,
            deepseek_api_key=deepseek_api_key,
        ),
    )


def _patch_runtime_dependencies(
    monkeypatch,
    *,
    timeout_ms: int = 15_000,
    gemini_api_key: str | None = "sk-gemini",
    codex_api_key: str | None = "sk-codex",
    deepseek_api_key: str | None = "sk-deepseek",
) -> None:
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setattr(
        graph_builder,
        "load_host_config",
        lambda: _mock_config_result(
            timeout_ms=timeout_ms,
            gemini_api_key=gemini_api_key,
            codex_api_key=codex_api_key,
            deepseek_api_key=deepseek_api_key,
        ),
    )
    monkeypatch.setattr(
        graph_builder,
        "build_host_provider_fn",
        lambda **kwargs: {
            "provider_name": kwargs.get("provider_name"),
            "model_id": kwargs.get("model_id"),
            "base_url_override": kwargs.get("base_url_override"),
        },
    )
    monkeypatch.setattr(
        graph_builder,
        "_resolve_host_14b_health_status",
        lambda **kwargs: (True, "ok"),
    )
    monkeypatch.setattr(
        graph_builder,
        "_resolve_host_14b_health_status_async",
        lambda **kwargs: (True, "ok"),
    )


def test_resolve_host_llm_runtime_prefers_gemini_primary(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch)
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-2.5-flash")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-chat")

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CLOUD_CHAIN_PRIMARY_GEMINI"
    assert runtime["provider_name"] == "gemini"
    assert runtime["model_id"] == "gemini-2.5-flash"
    assert runtime["available"] is True
    assert runtime["remote_chain_status"][0]["chain_name"] == "gemini"
    assert runtime["remote_chain_status"][0]["available"] is True


def test_resolve_host_llm_runtime_falls_back_to_codex(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key="sk-codex")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")
    monkeypatch.setenv("HOST_CODEX_BASE_URL", "https://codex-relay.example.com")

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CLOUD_CHAIN_FALLBACK_CODEX"
    assert runtime["provider_name"] == "codex"
    assert runtime["model_id"] == "gpt-4o-mini"
    assert runtime["base_url_override"] == "https://codex-relay.example.com/v1"
    assert runtime["remote_chain_status"][0]["available"] is False
    assert runtime["remote_chain_status"][1]["chain_name"] == "codex"
    assert runtime["remote_chain_status"][1]["provider_name"] == "codex"
    assert runtime["remote_chain_status"][1]["available"] is True


def test_resolve_host_llm_runtime_falls_back_to_deepseek(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key=None, deepseek_api_key="sk-deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-chat")

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CLOUD_CHAIN_FALLBACK_DEEPSEEK"
    assert runtime["provider_name"] == "deepseek"
    assert runtime["model_id"] == "deepseek-chat"
    assert runtime["remote_chain_status"][0]["available"] is False
    assert runtime["remote_chain_status"][1]["available"] is False
    assert runtime["remote_chain_status"][2]["available"] is True


def test_resolve_host_llm_runtime_uses_local_as_final_fallback(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key=None, deepseek_api_key=None)
    monkeypatch.setenv("HOST_CHAT_HOST_14B_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_MODEL_ID", "qwen3:14b-q4_k_m")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_HOST_14B_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime()

    assert runtime["route_target"] == HOST_CHAT_ROUTE_HOST_14B
    assert runtime["route_reason_code"] == "HOST_CLOUD_CHAIN_FALLBACK_LOCAL"
    assert runtime["provider_name"] == "openai"
    assert runtime["model_id"] == "qwen3:14b-q4_k_m"
    assert runtime["llm_api_key"] == "host-local-placeholder-key"
    assert runtime["base_url_override"] == "http://127.0.0.1:11434/v1"
    assert runtime["remote_chain_status"][0]["available"] is False
    assert runtime["remote_chain_status"][1]["available"] is False
    assert runtime["remote_chain_status"][2]["available"] is False


def test_resolve_host_llm_runtime_reasoning_scene_still_uses_remote_chain(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key="sk-codex", deepseek_api_key="sk-deepseek")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")

    runtime = graph_builder.resolve_host_llm_runtime(scene_type=SCENE_EXEC_INTENT)

    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "HOST_CLOUD_CHAIN_FALLBACK_CODEX"
    routing_matrix_decision = dict(runtime.get("routing_matrix_decision") or {})
    assert routing_matrix_decision.get("task_type") == "reasoning"
    assert routing_matrix_decision.get("selected_model") == "cloud"
    assert routing_matrix_decision.get("runtime_selected_target") == HOST_CHAT_ROUTE_CLOUD


def test_build_chat_graph_config_keeps_explicit_chat_runtime_binding(monkeypatch) -> None:
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
        model_id="qwen3:14b-q4_k_m",
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
    assert overrides[CHAT_DIRECT_NODE_ID]["model_id"] == "qwen3:14b-q4_k_m"
    assert overrides[CHAT_DIRECT_NODE_ID]["provider_fn"]["base_url_override"] == "http://127.0.0.1:11434/v1"
    assert overrides["core_reasoner_primary"]["provider"] == "gemini"
    assert overrides["core_reasoner_primary"]["provider_fn"]["base_url_override"] is None
    assert any(
        row.get("node_id") == CHAT_DIRECT_NODE_ID and row.get("base_url_override") == "http://127.0.0.1:11434/v1"
        for row in captured_calls
    )
