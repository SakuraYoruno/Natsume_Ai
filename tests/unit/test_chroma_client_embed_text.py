from __future__ import annotations

import sys
from types import SimpleNamespace

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


def test_embed_text_hash_provider_bypasses_sentence_transformer(monkeypatch) -> None:
    monkeypatch.setenv("RETR_EMBED_PROVIDER", "hash")
    monkeypatch.setattr(
        chroma_client,
        "_get_sentence_transformer_model",
        lambda: (_ for _ in ()).throw(AssertionError("hash provider must not load local model")),
    )

    values, model_id = chroma_client.embed_text("hello world", dim=8)

    assert len(values) == 8
    assert model_id == chroma_client.EMBEDDING_MODEL_ID


def test_sentence_transformer_model_defaults_to_cpu_without_explicit_cuda(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs) -> None:
            captured["model_ref"] = model_ref
            captured.update(kwargs)

    chroma_client._sentence_transformer_models.clear()
    monkeypatch.setenv("RETR_MODEL_SUITE_DEVICE", "cuda")
    monkeypatch.delenv("RETR_EMBED_DEVICE", raising=False)
    monkeypatch.delenv("RETR_EMBED_ALLOW_CUDA", raising=False)
    monkeypatch.delenv("HOST_RETR_EMBED_ALLOW_CUDA", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer),
    )

    chroma_client._get_sentence_transformer_model()

    assert captured["device"] == "cpu"
    chroma_client._sentence_transformer_models.clear()


def test_sentence_transformer_model_honors_explicit_embed_device(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs) -> None:
            captured["model_ref"] = model_ref
            captured.update(kwargs)

    chroma_client._sentence_transformer_models.clear()
    monkeypatch.setenv("RETR_EMBED_DEVICE", "cuda")
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer),
    )

    chroma_client._get_sentence_transformer_model()

    assert captured["device"] == "cuda"
    chroma_client._sentence_transformer_models.clear()


def test_sentence_transformer_model_ref_prefers_repo_local_models() -> None:
    model_ref, local_only = chroma_client._resolve_sentence_transformer_model_ref("models/Qwen3-Embedding-0.6B")

    assert local_only
    assert model_ref.endswith("models\\Qwen3-Embedding-0.6B") or model_ref.endswith("models/Qwen3-Embedding-0.6B")
