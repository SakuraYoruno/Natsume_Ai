from __future__ import annotations

import json
from pathlib import Path

from host_brain.app import config as host_config


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8-sig",
    )


def test_load_host_config_resolves_ref_from_runtime_files(tmp_path: Path, monkeypatch) -> None:
    key_profiles_file = tmp_path / "key_profiles.runtime.json"
    secret_store_file = tmp_path / "secret_store.runtime.local.json"

    _write_json(
        key_profiles_file,
        {
            "current_epoch": 20260301,
            "host_node_id": "natsu-node-77",
            "profiles": [
                {
                    "provider": "gemini",
                    "node_id": "natsu-node-77",
                    "scope": "host.llm",
                    "secret_ref": "sec://gemini/natsu-node-77",
                    "rotation_epoch": 20270101,
                    "status": "active",
                }
            ],
        },
    )
    _write_json(
        secret_store_file,
        {
            "sec://gemini/natsu-node-77": "sk-gemini-demo-value",
        },
    )

    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE", secret_store_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_file)

    result = host_config.load_host_config(
        env={
            "Natsume_ENV": "prod",
            "HOST_LLM_PROVIDER": "gemini",
            "HOST_COLD_START_ALLOWED": "false",
            "GEMINI_API_KEY_REF": "sec://gemini/natsu-node-77",
        },
    )
    assert result.success, result.as_dict()
    assert result.config is not None
    assert result.config.llm_provider == "gemini"
    assert result.config.gemini_api_key == "sk-gemini-demo-value"
    assert result.config.llm_api_key == "sk-gemini-demo-value"
    assert result.config.llm_api_key_ref == "sec://gemini/natsu-node-77"
    assert result.config.key_profile_status == "resolved"


def test_load_host_config_injects_resolved_key_into_process_env(tmp_path: Path, monkeypatch) -> None:
    key_profiles_file = tmp_path / "key_profiles.runtime.json"
    secret_store_file = tmp_path / "secret_store.runtime.local.json"

    _write_json(
        key_profiles_file,
        {
            "current_epoch": 20260301,
            "host_node_id": "natsu-node-88",
            "profiles": [
                {
                    "provider": "gemini",
                    "node_id": "natsu-node-88",
                    "scope": "host.llm",
                    "secret_ref": "sec://gemini/natsu-node-88",
                    "rotation_epoch": 20270101,
                    "status": "active",
                }
            ],
        },
    )
    _write_json(
        secret_store_file,
        {
            "sec://gemini/natsu-node-88": "sk-gemini-injected",
        },
    )

    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE", secret_store_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_file)

    monkeypatch.setenv("Natsume_ENV", "prod")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "false")
    monkeypatch.setenv("GEMINI_API_KEY_REF", "sec://gemini/natsu-node-88")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = host_config.load_host_config()
    assert result.success, result.as_dict()
    assert result.config is not None
    assert result.config.gemini_api_key == "sk-gemini-injected"
    assert result.config.key_profile_status == "resolved"
    assert host_config.os.environ.get("GEMINI_API_KEY") == "sk-gemini-injected"


def test_load_host_config_rejects_codex_as_host_llm_provider() -> None:
    result = host_config.load_host_config(
        env={
            "Natsume_ENV": "prod",
            "HOST_LLM_PROVIDER": "codex",
            "HOST_COLD_START_ALLOWED": "false",
        },
    )

    assert result.success is False
    assert result.reason_code == host_config.CONFIG_VALUE_INVALID
    assert result.issues[0].field == "HOST_LLM_PROVIDER"


def test_build_host_runtime_statuses_accepts_mock_provider_without_key() -> None:
    result = host_config.load_host_config(
        env={
            "Natsume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
        },
    )

    assert result.success, result.as_dict()
    assert result.config is not None
    statuses = host_config.build_host_runtime_statuses(result.config)

    assert statuses["host.integrations.llm_gateway"]["available"] is True
    assert statuses["host.integrations.llm_gateway"]["detail"] == "llm gateway enabled"


def test_load_host_config_resolves_auxiliary_provider_refs(tmp_path: Path, monkeypatch) -> None:
    key_profiles_file = tmp_path / "key_profiles.runtime.json"
    secret_store_file = tmp_path / "secret_store.runtime.local.json"

    _write_json(
        key_profiles_file,
        {
            "current_epoch": 20260301,
            "host_node_id": "natsu-node-01",
            "profiles": [
                {
                    "provider": "deepseek",
                    "node_id": "natsu-node-01",
                    "scope": "host.llm",
                    "secret_ref": "sec://deepseek/natsu-node-01",
                    "rotation_epoch": 20270101,
                    "status": "active",
                },
                {
                    "provider": "minimax",
                    "node_id": "natsu-node-01",
                    "scope": "host.llm",
                    "secret_ref": "sec://minimax/natsu-node-01",
                    "rotation_epoch": 20270101,
                    "status": "active",
                },
            ],
        },
    )
    _write_json(
        secret_store_file,
        {
            "sec://deepseek/natsu-node-01": "sk-deepseek-demo-value",
            "sec://minimax/natsu-node-01": "sk-minimax-demo-value",
        },
    )

    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE", secret_store_file)
    monkeypatch.setattr(host_config, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_file)

    result = host_config.load_host_config(
        env={
            "Natsume_ENV": "prod",
            "HOST_LLM_PROVIDER": "deepseek",
            "HOST_COLD_START_ALLOWED": "false",
            "DEEPSEEK_API_KEY_REF": "sec://deepseek/natsu-node-01",
            "MINIMAX_API_KEY_REF": "sec://minimax/natsu-node-01",
        },
    )

    assert result.success, result.as_dict()
    assert result.config is not None
    assert result.config.deepseek_api_key == "sk-deepseek-demo-value"
    assert result.config.minimax_api_key == "sk-minimax-demo-value"
    assert result.config.llm_api_key == "sk-deepseek-demo-value"
