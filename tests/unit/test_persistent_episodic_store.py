from __future__ import annotations

from dataclasses import dataclass

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.episodic import EpisodicStore

import host_brain.app.dependencies as dependencies
from host_brain.memory.chroma_client import (
    ChromaRuntimeConfig,
    build_runtime_chroma_client,
    load_chroma_runtime_config,
    _sanitize_chroma_metadata,
)
from tests.fakes.chroma_client import FakeChromaClient


def test_sanitize_chroma_metadata_serializes_nested_values() -> None:
    payload = _sanitize_chroma_metadata(
        {
            "ts_ms": 123,
            "visible": True,
            "evidence_refs": ["chat://person/user-001", "chat://session/s-001"],
            "extra": {"a": 1},
        }
    )

    assert payload["ts_ms"] == 123
    assert payload["visible"] is True
    assert payload["evidence_refs"].startswith("[")
    assert payload["extra"].startswith("{")


def test_get_episodic_store_uses_persistent_chroma(monkeypatch) -> None:
    @dataclass(frozen=True)
    class _CfgResult:
        success: bool
        config: ChromaRuntimeConfig | None

        def as_dict(self) -> dict[str, object]:
            return {"success": self.success}

    config = ChromaRuntimeConfig(
        enabled=True,
        endpoint="http://127.0.0.1:8000",
        tenant="default_tenant",
        database="default_database",
        collection_profiles={"episodic": 16},
        default_collection="episodic",
    )

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, *, ledger, chroma_client, embedding_fn, embedding_model_id_hint=None, embedding_provider_hint=None) -> None:  # noqa: ANN001
            captured["ledger"] = ledger
            captured["chroma_client"] = chroma_client
            captured["embedding_fn"] = embedding_fn
            captured["embedding_model_id_hint"] = embedding_model_id_hint
            captured["embedding_provider_hint"] = embedding_provider_hint

    monkeypatch.setattr(dependencies, "_episodic_store", None)
    monkeypatch.setattr(dependencies, "get_ledger", lambda: "LEDGER")
    monkeypatch.setattr(
        "host_brain.memory.chroma_client.load_chroma_runtime_config",
        lambda env=None: _CfgResult(success=True, config=config),
    )
    monkeypatch.setattr(
        "host_brain.memory.chroma_client.PersistentChromaClient.from_runtime_config",
        lambda cfg: f"PERSISTENT::{cfg.endpoint}",
    )
    monkeypatch.setattr(dependencies, "EpisodicStore", _FakeStore)

    store = dependencies.get_episodic_store()

    assert isinstance(store, _FakeStore)
    assert captured["ledger"] == "LEDGER"
    assert captured["chroma_client"] == "PERSISTENT::http://127.0.0.1:8000"

    monkeypatch.setattr(dependencies, "_episodic_store", None)


def test_episodic_store_uses_chroma_collection_dim_for_embeddings(tmp_path, monkeypatch) -> None:
    captured: dict[str, int] = {}

    def _fake_embed(text: str, *, dim: int = 16):
        captured["dim"] = dim
        return [0.0] * dim, "fake-embed"

    monkeypatch.setattr("host_brain.memory.episodic.embed_text", _fake_embed)
    store = EpisodicStore(
        ledger=SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False),
        chroma_client=FakeChromaClient(collection_profiles={"episodic": 1024}, default_collection="episodic"),
    )

    result = store.write_memory(text="hello", evidence_refs=["ev-1"])

    assert result.success is True
    assert captured["dim"] == 1024



def test_episodic_store_defaults_to_runtime_chroma_client(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr("host_brain.memory.episodic.build_runtime_chroma_client", lambda: sentinel)

    store = EpisodicStore(chroma_client=None)

    assert store.chroma is sentinel


def test_build_runtime_chroma_client_rejects_disabled_config(monkeypatch) -> None:
    @dataclass(frozen=True)
    class _CfgResult:
        success: bool
        config: ChromaRuntimeConfig | None

        def as_dict(self) -> dict[str, object]:
            return {"success": self.success, "enabled": getattr(self.config, "enabled", None)}

    disabled = ChromaRuntimeConfig(
        enabled=False,
        endpoint="http://127.0.0.1:8000",
        tenant="default_tenant",
        database="default_database",
        collection_profiles={"episodic": 16},
        default_collection="episodic",
    )
    monkeypatch.setattr(
        "host_brain.memory.chroma_client.load_chroma_runtime_config",
        lambda env=None, local_overrides=None: _CfgResult(success=True, config=disabled),
    )

    with pytest.raises(RuntimeError, match="chroma runtime config invalid"):
        build_runtime_chroma_client()


def test_load_chroma_runtime_config_merges_project_dotenv_for_process_env(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.memory.chroma_client._load_project_dotenv_values",
        lambda: {
            "CHROMA_ENABLED": "true",
            "CHROMA_ENDPOINT": "http://127.0.0.1:18000",
            "CHROMA_COLLECTION_PROFILES_JSON": '{"episodic":1024}',
            "CHROMA_DEFAULT_COLLECTION": "episodic",
        },
    )
    for name in ("CHROMA_ENABLED", "CHROMA_ENDPOINT", "CHROMA_COLLECTION_PROFILES_JSON", "CHROMA_DEFAULT_COLLECTION"):
        monkeypatch.delenv(name, raising=False)

    result = load_chroma_runtime_config()

    assert result.success is True
    assert result.config is not None
    assert result.config.endpoint == "http://127.0.0.1:18000"
    assert result.config.collection_profiles == {"episodic": 1024}


def test_load_chroma_runtime_config_builds_endpoint_from_host_and_port() -> None:
    result = load_chroma_runtime_config(
        env={
            "CHROMA_ENABLED": "true",
            "CHROMA_HOST": "127.0.0.1",
            "CHROMA_PORT": "18000",
            "CHROMA_COLLECTION_PROFILES_JSON": '{"episodic":1024}',
            "CHROMA_DEFAULT_COLLECTION": "episodic",
        }
    )

    assert result.success is True
    assert result.config is not None
    assert result.config.endpoint == "http://127.0.0.1:18000"


def test_get_episodic_store_injects_embedding_callable(monkeypatch) -> None:
    @dataclass(frozen=True)
    class _CfgResult:
        success: bool
        config: ChromaRuntimeConfig | None

        def as_dict(self) -> dict[str, object]:
            return {"success": self.success}

    config = ChromaRuntimeConfig(
        enabled=True,
        endpoint="http://127.0.0.1:8000",
        tenant="default_tenant",
        database="default_database",
        collection_profiles={"episodic": 1024},
        default_collection="episodic",
    )

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, *, ledger, chroma_client, embedding_fn, embedding_model_id_hint=None, embedding_provider_hint=None) -> None:  # noqa: ANN001
            captured["ledger"] = ledger
            captured["chroma_client"] = chroma_client
            captured["embedding_fn"] = embedding_fn
            captured["embedding_model_id_hint"] = embedding_model_id_hint
            captured["embedding_provider_hint"] = embedding_provider_hint

    monkeypatch.setattr(dependencies, "_episodic_store", None)
    monkeypatch.setattr(dependencies, "get_ledger", lambda: "LEDGER")
    monkeypatch.setattr(
        "host_brain.memory.chroma_client.load_chroma_runtime_config",
        lambda env=None: _CfgResult(success=True, config=config),
    )
    monkeypatch.setattr(
        "host_brain.memory.chroma_client.PersistentChromaClient.from_runtime_config",
        lambda cfg: f"PERSISTENT::{cfg.endpoint}",
    )
    monkeypatch.setattr(
        dependencies,
        "_resolve_episodic_embedding_binding",
        lambda: ((lambda text: ([0.1, 0.2], "models/Qwen3-Embedding-0.6B")), "models/Qwen3-Embedding-0.6B", "sentence_transformers"),
    )
    monkeypatch.setattr(dependencies, "EpisodicStore", _FakeStore)

    store = dependencies.get_episodic_store()

    assert isinstance(store, _FakeStore)
    assert captured["embedding_fn"] is not None
    vector, model_id = captured["embedding_fn"]("hello")  # type: ignore[operator]
    assert model_id == "models/Qwen3-Embedding-0.6B"
    assert vector == [0.1, 0.2]

    monkeypatch.setattr(dependencies, "_episodic_store", None)



def test_get_episodic_store_sets_embedding_hints(monkeypatch) -> None:
    @dataclass(frozen=True)
    class _CfgResult:
        success: bool
        config: ChromaRuntimeConfig | None

        def as_dict(self) -> dict[str, object]:
            return {"success": self.success}

    config = ChromaRuntimeConfig(
        enabled=True,
        endpoint="http://127.0.0.1:8000",
        tenant="default_tenant",
        database="default_database",
        collection_profiles={"episodic": 1024},
        default_collection="episodic",
    )

    class _FakeStore:
        def __init__(self, *, ledger, chroma_client, embedding_fn, embedding_model_id_hint, embedding_provider_hint) -> None:  # noqa: ANN001
            self.embedding_model_id_hint = embedding_model_id_hint
            self.embedding_provider_hint = embedding_provider_hint

    monkeypatch.setattr(dependencies, "_episodic_store", None)
    monkeypatch.setattr(dependencies, "get_ledger", lambda: "LEDGER")
    monkeypatch.setattr("host_brain.memory.chroma_client.load_chroma_runtime_config", lambda env=None: _CfgResult(success=True, config=config))
    monkeypatch.setattr("host_brain.memory.chroma_client.PersistentChromaClient.from_runtime_config", lambda cfg: f"PERSISTENT::{cfg.endpoint}")
    monkeypatch.setattr(dependencies, "_resolve_episodic_embedding_binding", lambda: ((lambda text: ([0.1] * 1024, "models/Qwen3-Embedding-0.6B")), "models/Qwen3-Embedding-0.6B", "sentence_transformers"))
    monkeypatch.setattr(dependencies, "EpisodicStore", _FakeStore)

    store = dependencies.get_episodic_store()

    assert store.embedding_model_id_hint == "models/Qwen3-Embedding-0.6B"
    assert store.embedding_provider_hint == "sentence_transformers"
    monkeypatch.setattr(dependencies, "_episodic_store", None)
