from __future__ import annotations

from host_brain.memory.chroma_client import PersistentChromaClient


class _FakeCollection:
    def get(self, **_kwargs):
        class _ArrayLike(list):
            pass

        return {
            "ids": ["mem-1"],
            "documents": ["hello"],
            "embeddings": [_ArrayLike([0.1, 0.2, 0.3])],
            "metadatas": [{"embedding_model_id": "models/Qwen3-Embedding-0.6B"}],
        }


class _FakeClient:
    def get_or_create_collection(self, name: str):  # noqa: ARG002
        return _FakeCollection()


def test_persistent_chroma_get_handles_embedding_array_without_truthiness_error() -> None:
    client = object.__new__(PersistentChromaClient)
    client._client = _FakeClient()
    client._default_collection = "episodic"
    client._collection_profiles = {"episodic": 3}

    entry = PersistentChromaClient.get(client, "mem-1")

    assert entry is not None
    assert entry.memory_id == "mem-1"
    assert entry.metadata["embedding_model_id"] == "models/Qwen3-Embedding-0.6B"



def test_resolve_semantic_embedding_model_id_reads_current_env(monkeypatch) -> None:
    from host_brain.memory import chroma_client as cc

    monkeypatch.setenv("RETR_EMBED_MODEL_ID", "models/Qwen3-Embedding-0.6B")

    assert cc._resolve_semantic_embedding_model_id() == "models/Qwen3-Embedding-0.6B"
