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


def test_model_router_single_key_prefers_explicit(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k-env")

    route = model_router.resolve_gemini_model_route(role="chat_agent", preferred_api_key="k0")

    assert route["api_key"] == "k0"
    assert route["key_configured"] is True
    assert route["key_source"] == "preferred"


def test_model_router_single_env_key_does_not_rotate(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("HOST_CHAT_AGENT_MODEL_ID", "gemini-3.1-pro-preview")

    r1 = model_router.resolve_gemini_model_route(role="chat_agent")
    r2 = model_router.resolve_gemini_model_route(role="chat_agent")
    r3 = model_router.resolve_gemini_model_route(role="chat_agent")

    assert r1["api_key"] == "k1"
    assert r2["api_key"] == "k1"
    assert r3["api_key"] == "k1"
    assert not any(name.endswith("_slot") for name in r1)
    assert not any(name.endswith("_pool_size") for name in r1)
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


def test_model_router_route_key_does_not_change_single_key(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")

    r1 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-A")
    r2 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-A")
    r3 = model_router.resolve_gemini_model_route(role="subagent", route_key="session-B")

    assert r1["api_key"] == r2["api_key"]
    assert r3["api_key"] == "k1"


def test_host_runtime_uses_single_gemini_key_for_primary_chain(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    _patch_runtime_dependencies(
        monkeypatch,
        gemini_api_key=None,
        codex_api_key="sk-codex",
        deepseek_api_key="sk-deepseek",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-2.5-flash")

    runtime1 = graph_builder.resolve_host_llm_runtime()
    runtime2 = graph_builder.resolve_host_llm_runtime()

    assert runtime1["provider_name"] == "gemini"
    assert runtime1["llm_api_key"] == "k1"
    assert runtime2["llm_api_key"] == "k1"
    assert runtime1["cloud_model_route"]["key_configured"] is True
    assert not any(name.endswith("_pool_size") for name in runtime1["cloud_model_route"])


def test_executive_router_runtime_preserves_non_gemini_base_provider_model(monkeypatch) -> None:
    monkeypatch.setenv("HOST_AGENT_CODEX_MIGRATION_ENABLED", "false")
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
    monkeypatch.setenv("HOST_AGENT_CODEX_MIGRATION_ENABLED", "false")
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
    monkeypatch.setenv("HOST_AGENT_CODEX_MIGRATION_ENABLED", "false")
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
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


def test_chat_runtime_uses_single_gemini_key_and_role_model(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
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
    assert runtime2["llm_api_key"] == "k1"
    assert runtime1["agent_model_route"]["role"] == "chat_agent"


def test_chat_runtime_default_profile_sets_output_cap(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("HOST_CHAT_AGENT_MODEL_ID", "gemini-2.5-pro")

    runtime = graph_builder.resolve_chat_agent_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-pro",
            "llm_api_key": None,
        }
    )

    assert runtime["agent_role"] == "chat_agent"
    assert runtime["agent_gen_params"]["max_tokens"] == 220


def test_chat_runtime_can_experiment_on_codex_spark(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_AGENT_CODEX_EXPERIMENT_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_AGENT_CODEX_MODEL_ID", "gpt-5.3-codex-spark")
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.codex_cli_lm_available",
        lambda: True,
    )

    runtime = graph_builder.resolve_chat_agent_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash",
            "llm_api_key": "gem-key",
            "timeout_ms": 8000,
        }
    )

    assert runtime["provider_name"] == "codex"
    assert runtime["model_id"] == "gpt-5.3-codex-spark"
    assert runtime["llm_api_key"] is None
    assert runtime["timeout_ms"] == 45000
    assert runtime["route_reason_code"] == "HOST_CHAT_AGENT_CODEX_EXPERIMENT"


def test_subagent_runtime_keeps_legacy_agent_role_value(monkeypatch) -> None:
    monkeypatch.setenv("HOST_AGENT_CODEX_MIGRATION_ENABLED", "false")
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
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


def test_non_chat_agent_runtime_migrates_to_codex_cli(monkeypatch) -> None:
    monkeypatch.setenv("HOST_AGENT_CODEX_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-5.4-mini")
    monkeypatch.setattr(
        "host_brain.app.graph_config_llm_runtime.codex_cli_lm_available",
        lambda: True,
    )

    runtime = graph_builder.resolve_executive_router_llm_runtime(
        base_runtime={
            "provider_name": "gemini",
            "model_id": "gemini-2.5-flash",
            "llm_api_key": "gem-key",
        }
    )

    assert runtime["provider_name"] == "codex"
    assert runtime["model_id"] == "gpt-5.4-mini"
    assert runtime["llm_api_key"] is None
    assert runtime["route_reason_code"] == "HOST_AGENT_CODEX_CLI_MIGRATION"


def test_model_router_prefers_runtime_secret_files(monkeypatch, tmp_path) -> None:
    key_profiles_path = tmp_path / "key_profiles.runtime.json"
    secret_store_path = tmp_path / "secret_store.runtime.local.json"
    key_profiles_path.write_text(
        json.dumps(
            {
                "current_epoch": 20260411,
                "host_node_id": "natsu-node-01",
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
    monkeypatch.setenv("GEMINI_API_KEY", "k_env_only")

    key = model_router.resolve_gemini_api_key()

    assert key == "k_file_1"


def test_inspect_gemini_key_status_is_redacted(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "_resolve_runtime_gemini_api_key", lambda: (None, None, None))
    monkeypatch.setenv("GEMINI_API_KEY", "k1")

    snapshot = model_router.inspect_gemini_key_status()

    assert snapshot["key_configured"] is True
    assert snapshot["source"] == "env"
    assert "keys" not in snapshot
    assert "api_key" not in snapshot
    assert not any(name.endswith("_pool_size") for name in snapshot)
