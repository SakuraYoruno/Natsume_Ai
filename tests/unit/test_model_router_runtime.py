from __future__ import annotations

import json
from types import SimpleNamespace

import host_brain.app.graph_config_builder as graph_builder
import host_brain.app.model_router as model_router
from host_brain.app import config as host_config


def _mock_config_result(
    *,
    timeout_ms: int = 15_000,
    gemini_api_key: str | None = None,
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
    gemini_api_key: str | None = None,
    codex_api_key: str | None = "sk-codex",
    deepseek_api_key: str | None = "sk-deepseek",
) -> None:
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


def test_model_router_key_pool_parse_and_dedupe(monkeypatch) -> None:
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k2, k3")
    monkeypatch.setenv("GEMINI_API_KEY_1", "k1")
    monkeypatch.setenv("GEMINI_API_KEY_2", "k2")
    monkeypatch.setenv("GEMINI_API_KEY", "k4")

    keys = model_router.resolve_gemini_key_pool(preferred_api_key="k0")

    assert keys == ["k0", "k2", "k3", "k1", "k4"]


def test_model_router_round_robin_rotation(monkeypatch) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_STRATEGY", "round_robin")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1,k2,k3")
    monkeypatch.setenv("HOST_CHAT_AGENT_MODEL_ID", "gemini-3.1-pro-preview")

    r1 = model_router.resolve_gemini_model_route(role="chat_agent")
    r2 = model_router.resolve_gemini_model_route(role="chat_agent")
    r3 = model_router.resolve_gemini_model_route(role="chat_agent")
    r4 = model_router.resolve_gemini_model_route(role="chat_agent")

    assert r1["api_key"] == "k1"
    assert r2["api_key"] == "k2"
    assert r3["api_key"] == "k3"
    assert r4["api_key"] == "k1"
    assert r1["model_id"] == "gemini-3.1-pro-preview"
    assert r1["capability_profile"]["role"] == "chat_agent"
    assert r1["capability_profile"]["reasoning_tier"] in {"high", "medium", "low"}


def test_model_router_executive_router_model_prefers_new_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_EXECUTIVE_ROUTER_MODEL_ID", "gemini-3.1-pro-preview")
    monkeypatch.setenv("HOST_DISPATCH_AGENT_MODEL_ID", "gemini-2.5-flash")

    route = model_router.resolve_gemini_model_route(role="executive_router")

    assert route["model_id"] == "gemini-3.1-pro-preview"


def test_model_router_executive_router_model_falls_back_to_dispatch_env(monkeypatch) -> None:
    monkeypatch.delenv("HOST_EXECUTIVE_ROUTER_MODEL_ID", raising=False)
    monkeypatch.setenv("HOST_DISPATCH_AGENT_MODEL_ID", "gemini-2.5-pro")

    route = model_router.resolve_gemini_model_route(role="executive_router")

    assert route["model_id"] == "gemini-2.5-pro"


def test_model_router_hash_strategy_is_stable(monkeypatch) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_STRATEGY", "hash")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1,k2,k3,k4")

    r1 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-A")
    r2 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-A")
    r3 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-B")

    assert r1["key_slot"] == r2["key_slot"]
    assert r1["api_key"] == r2["api_key"]
    assert r3["api_key"] in {"k1", "k2", "k3", "k4"}


def test_host_runtime_uses_gemini_key_pool_for_primary_chain(monkeypatch) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    _patch_runtime_dependencies(
        monkeypatch,
        gemini_api_key=None,
        codex_api_key="sk-codex",
        deepseek_api_key="sk-deepseek",
    )
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1,k2")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_STRATEGY", "round_robin")
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-2.5-flash")

    runtime1 = graph_builder.resolve_host_llm_runtime()
    runtime2 = graph_builder.resolve_host_llm_runtime()

    assert runtime1["provider_name"] == "gemini"
    assert runtime1["llm_api_key"] == "k1"
    assert runtime2["llm_api_key"] == "k2"
    assert runtime1["cloud_model_route"]["key_pool_size"] == 2


def test_executive_router_runtime_preserves_non_gemini_base_provider_model(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DISPATCH_AGENT_MODEL_ID", "gemini-2.5-flash")

    runtime = graph_builder.resolve_executive_router_llm_runtime(
        base_runtime={
            "provider_name": "codex",
            "model_id": "gpt-4o-mini",
            "llm_api_key": "sk-codex",
        }
    )

    assert runtime["provider_name"] == "codex"
    assert runtime["model_id"] == "gpt-4o-mini"
    assert runtime["llm_api_key"] == "sk-codex"


def test_executive_router_runtime_uses_legacy_dispatch_env_for_non_gemini(monkeypatch) -> None:
    monkeypatch.delenv("HOST_EXECUTIVE_ROUTER_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_EXECUTIVE_ROUTER_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("HOST_DISPATCH_AGENT_MODEL_ID", "gpt-4.1-mini")
    monkeypatch.setenv("HOST_DISPATCH_AGENT_TIMEOUT_MS", "9100")

    runtime = graph_builder.resolve_executive_router_llm_runtime(
        base_runtime={
            "provider_name": "codex",
            "llm_api_key": "sk-codex",
        }
    )

    assert runtime["agent_role"] == "executive_router"
    assert runtime["model_id"] == "gpt-4.1-mini"
    assert runtime["timeout_ms"] == 9100


def test_executive_router_runtime_exposes_executive_role_metadata(monkeypatch) -> None:
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1")
    monkeypatch.setenv("HOST_DISPATCH_AGENT_MODEL_ID", "gemini-2.5-flash")

    runtime = graph_builder.resolve_executive_router_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "llm_api_key": None,
        }
    )

    route = dict(runtime.get("agent_model_route") or {})
    assert runtime["agent_role"] == "executive_router"
    assert route.get("role") == "executive_router"
    assert not list(route.get("role_aliases") or [])


def test_chat_runtime_uses_gemini_key_pool_and_role_model(monkeypatch) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1,k2")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_STRATEGY", "round_robin")
    monkeypatch.setenv("HOST_CHAT_AGENT_MODEL_ID", "gemini-3.1-pro-preview")

    runtime1 = graph_builder.resolve_chat_agent_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash",
            "llm_api_key": None,
        }
    )
    runtime2 = graph_builder.resolve_chat_agent_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash",
            "llm_api_key": None,
        }
    )

    assert runtime1["model_id"] == "gemini-3.1-pro-preview"
    assert runtime1["llm_api_key"] == "k1"
    assert runtime2["llm_api_key"] == "k2"
    assert runtime1["agent_model_route"]["role"] == "chat_agent"


def test_subagent_runtime_keeps_legacy_agent_role_value(monkeypatch) -> None:
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1")
    monkeypatch.setenv("HOST_SUBAGENT_MODEL_ID", "gemini-2.5-pro")

    runtime = graph_builder.resolve_subagent_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash",
            "llm_api_key": None,
        }
    )

    assert runtime["agent_role"] == "sub_agent"
    assert runtime["model_id"] == "gemini-2.5-pro"


def test_model_router_prefers_runtime_secret_files(monkeypatch, tmp_path) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    key_profiles_path = tmp_path / "key_profiles.runtime.json"
    secret_store_path = tmp_path / "secret_store.runtime.local.json"
    key_profiles_path.write_text(
        json.dumps(
            {
                "current_epoch": 20260411,
                "profiles": [
                    {
                        "provider": "gemini",
                        "node_id": "natsu-node-01",
                        "scope": "host.llm",
                        "secret_ref": "sec://gemini/natsu-node-01",
                        "rotation_epoch": 20270101,
                        "status": "active",
                    },
                    {
                        "provider": "gemini",
                        "node_id": "natsu-node-02",
                        "scope": "host.llm",
                        "secret_ref": "sec://gemini/natsu-node-02",
                        "rotation_epoch": 20270101,
                        "status": "active",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    secret_store_path.write_text(
        json.dumps(
            {
                "sec://gemini/natsu-node-01": "k_file_1",
                "sec://gemini/natsu-node-02": "k_file_2",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES", key_profiles_path)
    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_path)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE", secret_store_path)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_path)
    monkeypatch.delenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", raising=False)
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "false")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k_env_only")

    keys = model_router.resolve_gemini_key_pool()

    assert keys == ["k_file_1", "k_file_2"]


def test_inspect_gemini_key_pool_is_redacted(monkeypatch) -> None:
    model_router._reset_gemini_key_pool_cursor_for_tests()
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("HOST_GEMINI_API_KEYS", "k1,k2")

    snapshot = model_router.inspect_gemini_key_pool()

    assert snapshot["pool_size"] == 2
    assert snapshot["source"] in {"env_fallback", "preferred+env_fallback"}
    assert "keys" not in snapshot
    assert "api_key" not in snapshot
