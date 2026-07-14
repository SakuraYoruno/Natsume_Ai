from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import host_brain.composition.process_services as app_dependencies
import host_brain.app.graph_config_builder as graph_builder
import host_brain.app.model_router as model_router
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    HOST_CHAT_ROUTE_CLOUD,
    HOST_CHAT_ROUTE_HOST_14B,
    SCENE_CHAT,
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
    minimax_api_key: str | None = None,
    llm_provider: str = "gemini",
):
    return SimpleNamespace(
        success=True,
        config=SimpleNamespace(
            llm_provider=llm_provider,
            llm_timeout_ms=timeout_ms,
            llm_api_key=gemini_api_key,
            gemini_api_key=gemini_api_key,
            codex_api_key=codex_api_key,
            deepseek_api_key=deepseek_api_key,
            minimax_api_key=minimax_api_key,
        ),
    )


def _patch_runtime_dependencies(
    monkeypatch,
    *,
    timeout_ms: int = 15_000,
    gemini_api_key: str | None = "sk-gemini",
    codex_api_key: str | None = "sk-codex",
    deepseek_api_key: str | None = "sk-deepseek",
    minimax_api_key: str | None = None,
    llm_provider: str = "gemini",
    codex_cli_available: bool = False,
) -> None:
    class _CloudRoutingMatrix:
        def select_with_fallback(self, *, task_type, latency_budget):  # noqa: ANN001
            return SimpleNamespace(
                task_type=task_type,
                selected_model=SimpleNamespace(value=HOST_CHAT_ROUTE_CLOUD),
                latency_budget=latency_budget,
                is_fallback=False,
                fallback_reason=None,
            )

    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setattr(app_dependencies, "get_routing_matrix", lambda: _CloudRoutingMatrix())


_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "api_key",
        "llm_api_key",
        "chat_llm_api_key",
        "cloud_llm_api_key",
        "token",
        "credential",
        "credentials",
        "provider_fn",
        "base_url_override",
    }
)


def _assert_secret_free_runtime(value: Any, *, path: str = "runtime") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            assert normalized_key not in _FORBIDDEN_RUNTIME_KEYS, (
                f"{path}.{key} leaked secret/transport authority"
            )
            assert not callable(item), f"{path}.{key} leaked a callable"
            _assert_secret_free_runtime(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_secret_free_runtime(item, path=f"{path}[{index}]")
        return
    assert not callable(value), f"{path} leaked a callable"


def test_resolve_host_llm_runtime_prefers_gemini_primary(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch)
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-2.5-flash")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-v4-flash")

    runtime = graph_builder.resolve_host_llm_runtime()

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:DEFAULT"
    assert runtime["provider_name"] == "gemini"
    assert runtime["model_id"] == "gemini-2.5-flash-lite"
    assert runtime["available"] is True
    assert "remote_chain_status" not in runtime


def test_resolve_host_llm_runtime_does_not_route_from_codex_credentials(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key="sk-codex")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")
    monkeypatch.setenv("HOST_CODEX_BASE_URL", "https://codex-relay.example.com")

    runtime = graph_builder.resolve_host_llm_runtime()

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:DEFAULT"
    assert runtime["provider_name"] == "gemini"
    assert "remote_chain_status" not in runtime


def test_resolve_host_llm_runtime_does_not_route_from_deepseek_credentials(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key=None, deepseek_api_key="sk-deepseek")
    monkeypatch.setenv("HOST_DEEPSEEK_MODEL_ID", "deepseek-v4-flash")

    runtime = graph_builder.resolve_host_llm_runtime()

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:DEFAULT"
    assert runtime["provider_name"] == "gemini"
    assert "remote_chain_status" not in runtime


def test_resolve_host_llm_runtime_can_select_minimax_as_primary(monkeypatch) -> None:
    _patch_runtime_dependencies(
        monkeypatch,
        gemini_api_key=None,
        codex_api_key=None,
        deepseek_api_key=None,
        minimax_api_key="sk-minimax",
        llm_provider="minimax",
    )
    monkeypatch.setenv("HOST_MINIMAX_MODEL_ID", "M2-her")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("HOST_MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")

    runtime = graph_builder.resolve_host_llm_runtime()

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:DEFAULT"
    assert runtime["provider_name"] == "minimax"
    assert runtime["model_id"] == "M2-her"
    assert "remote_chain_status" not in runtime


def test_resolve_host_llm_runtime_prefers_healthy_local_chat_when_enabled(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key="sk-gemini", codex_api_key="sk-codex", deepseek_api_key="sk-deepseek")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_MODEL_ID", "qwen3:14b-q4_k_m")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_LOCAL_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime(scene_type=SCENE_CHAT)

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_HOST_14B
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:LOCAL_CHAT"
    assert runtime["provider_name"] == "openai"
    assert runtime["model_id"] == "qwen3:14b-q4_k_m"
    assert runtime["cloud_available"] is True
    assert "routing_matrix_decision" not in runtime


def test_resolve_host_llm_runtime_uses_local_as_final_fallback(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key=None, deepseek_api_key=None)
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_MODEL_ID", "qwen3:14b-q4_k_m")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_HEALTHCHECK_ENABLED", "false")
    monkeypatch.delenv("HOST_CHAT_LOCAL_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = graph_builder.resolve_host_llm_runtime()

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_HOST_14B
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:LOCAL_CHAT"
    assert runtime["provider_name"] == "openai"
    assert runtime["model_id"] == "qwen3:14b-q4_k_m"
    assert "remote_chain_status" not in runtime


def test_resolve_host_llm_runtime_reasoning_scene_still_uses_remote_chain(monkeypatch) -> None:
    _patch_runtime_dependencies(monkeypatch, gemini_api_key=None, codex_api_key="sk-codex", deepseek_api_key="sk-deepseek")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-4o-mini")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "codex")

    runtime = graph_builder.resolve_host_llm_runtime(scene_type=SCENE_EXEC_INTENT)

    _assert_secret_free_runtime(runtime)
    assert runtime["route_target"] == HOST_CHAT_ROUTE_CLOUD
    assert runtime["route_reason_code"] == "SERVER_ROUTE_CATALOG:EXECUTIVE_ROUTER"
    assert runtime["provider_name"] == "codex"
    assert runtime["model_id"] == "gpt-4o-mini"
    assert "routing_matrix_decision" not in runtime


def test_build_chat_graph_config_keeps_only_secret_free_model_route_metadata() -> None:
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

    _assert_secret_free_runtime(cfg, path="graph_config")
    overrides = cfg["model_node_overrides"]
    assert overrides[CHAT_DIRECT_NODE_ID]["provider"] == "openai"
    assert overrides[CHAT_DIRECT_NODE_ID]["model_id"] == "qwen3:14b-q4_k_m"
    assert overrides["core_reasoner_primary"]["provider"] == "gemini"
    allowed_route_keys = {
        "provider",
        "model_id",
        "timeout_ms",
        "available",
        "max_output_tokens",
    }
    for node_id, route_metadata in overrides.items():
        assert set(route_metadata) <= allowed_route_keys, node_id
        assert not any(callable(value) for value in route_metadata.values())
