from __future__ import annotations

from dataclasses import dataclass

import host_brain.app.dependencies as dependencies
from host_brain.memory.chroma_client import _sanitize_chroma_metadata
from host_brain.memory.chroma_client import ChromaRuntimeConfig


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
        def __init__(self, *, ledger, chroma_client) -> None:  # noqa: ANN001
            captured["ledger"] = ledger
            captured["chroma_client"] = chroma_client

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
