from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.episodic import EpisodicStore
from tests.fakes.chroma_client import FakeChromaClient


def test_write_memory_falls_back_to_hash_embedding_on_vector_dim_mismatch(tmp_path) -> None:
    store = EpisodicStore(
        ledger=SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False),
        chroma_client=FakeChromaClient(collection_profiles={"episodic": 16}, default_collection="episodic"),
        embedding_fn=lambda text: ([0.5] * 1024, "models/Qwen3-Embedding-0.6B"),
    )

    result = store.write_memory(
        text="请记住：我明天上午9点开会。",
        evidence_refs=["request:req_fallback"],
        request_id="req_fallback",
        intent_id="intent::req_fallback::memory_store",
    )

    assert result.success is True
    assert result.memory_id

    entry = store.chroma.get(str(result.memory_id))
    assert entry is not None
    assert len(entry.embedding) == 16
    assert entry.metadata["embedding_model_id"] == "hash-embed-v1"
