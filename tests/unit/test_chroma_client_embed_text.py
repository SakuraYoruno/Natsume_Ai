from __future__ import annotations

from host_brain.memory import chroma_client


class _FakeSentenceTransformerModel:
    def encode(self, _text: str, *, normalize_embeddings: bool = True):  # noqa: ARG002
        return [0.1] * 1024


def test_embed_text_falls_back_to_hash_when_requested_dim_mismatches_semantic_model(monkeypatch) -> None:
    monkeypatch.setattr(chroma_client, "_get_sentence_transformer_model", lambda: _FakeSentenceTransformerModel())
    monkeypatch.setattr(chroma_client, "_resolve_semantic_embedding_model_id", lambda: "models/Qwen3-Embedding-0.6B")

    values, model_id = chroma_client.embed_text("hello world", dim=16)

    assert len(values) == 16
    assert model_id == chroma_client.EMBEDDING_MODEL_ID


def test_embed_text_keeps_semantic_vector_when_requested_dim_matches(monkeypatch) -> None:
    monkeypatch.setattr(chroma_client, "_get_sentence_transformer_model", lambda: _FakeSentenceTransformerModel())
    monkeypatch.setattr(chroma_client, "_resolve_semantic_embedding_model_id", lambda: "models/Qwen3-Embedding-0.6B")

    values, model_id = chroma_client.embed_text("hello world", dim=1024)

    assert len(values) == 1024
    assert model_id == "models/Qwen3-Embedding-0.6B"
