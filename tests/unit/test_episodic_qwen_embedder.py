from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.chroma_client import InMemoryChromaClient
from host_brain.memory.episodic import EpisodicStore


def test_episodic_store_uses_injected_embedding_callable_for_write_and_recall(tmp_path) -> None:
    calls: list[str] = []

    def _embed(text: str):
        calls.append(text)
        return [0.5] * 1024, "models/Qwen3-Embedding-0.6B"

    store = EpisodicStore(
        ledger=SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False),
        chroma_client=InMemoryChromaClient(collection_profiles={"episodic": 1024}, default_collection="episodic"),
        embedding_fn=_embed,
    )

    result = store.write_memory(text="我最喜欢的水果是芒果", evidence_refs=["ev-1"])
    assert result.success is True

    rows = store.recall(query_text="最喜欢的水果", top_k=3)

    assert calls[0] == "我最喜欢的水果是芒果"
    assert calls[1] == "最喜欢的水果"
    assert rows
    assert rows[0]["metadata"]["embedding_model_id"] == "models/Qwen3-Embedding-0.6B"
