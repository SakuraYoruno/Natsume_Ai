from __future__ import annotations

from typing import Any, Mapping

from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_VECTOR_DIM_MISMATCH,
)
from host_brain.memory.chroma_client import ChromaClientProtocol, ChromaEntry


class FakeChromaClient(ChromaClientProtocol):
    def __init__(
        self,
        *,
        collection_profiles: Mapping[str, int] | None = None,
        default_collection: str = "episodic",
    ) -> None:
        self._items: dict[str, ChromaEntry] = {}
        self._collection_profiles: dict[str, int] = dict(collection_profiles or {default_collection: 16})
        self._default_collection = str(default_collection or "episodic")

    @property
    def collection_profiles(self) -> Mapping[str, int]:
        return dict(self._collection_profiles)

    @property
    def default_collection(self) -> str:
        return self._default_collection

    def upsert(
        self,
        *,
        memory_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> None:
        collection_name = str(collection or self._default_collection)
        self._validate_collection_embedding(collection_name, embedding)
        key = _row_key(collection_name, memory_id)
        self._items[key] = ChromaEntry(
            memory_id=memory_id,
            text=text,
            embedding=tuple(float(v) for v in embedding),
            metadata=dict(metadata or {}),
            collection=collection_name,
        )

    def delete(self, memory_id: str, *, collection: str | None = None) -> None:
        key = _row_key(str(collection or self._default_collection), memory_id)
        self._items.pop(key, None)

    def get(self, memory_id: str, *, collection: str | None = None) -> ChromaEntry | None:
        key = _row_key(str(collection or self._default_collection), memory_id)
        return self._items.get(key)

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        collection_name = str(collection or self._default_collection)
        query = tuple(float(v) for v in query_embedding)
        self._validate_collection_embedding(collection_name, query)
        scored: list[tuple[float, ChromaEntry]] = []
        for item in self._items.values():
            if item.collection != collection_name:
                continue
            score = _cosine_similarity(query, item.embedding)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        results: list[dict[str, Any]] = []
        for score, item in scored[: max(1, top_k)]:
            results.append(
                {
                    "memory_id": item.memory_id,
                    "text": item.text,
                    "score": score,
                    "metadata": dict(item.metadata),
                    "collection": item.collection,
                }
            )
        return results

    def validate_collection(self, collection: str, *, vector_dim: int | None = None) -> tuple[bool, str | None]:
        collection_name = str(collection or "").strip()
        if not collection_name:
            return False, CHROMA_COLLECTION_MISSING
        expected_dim = self._collection_profiles.get(collection_name)
        if expected_dim is None:
            return False, CHROMA_COLLECTION_MISSING
        if vector_dim is not None and int(vector_dim) != int(expected_dim):
            return False, CHROMA_VECTOR_DIM_MISMATCH
        return True, None

    def _validate_collection_embedding(self, collection: str, embedding: list[float] | tuple[float, ...]) -> None:
        ok, reason = self.validate_collection(collection, vector_dim=len(embedding))
        if ok:
            return
        if reason == CHROMA_COLLECTION_MISSING:
            raise ValueError(CHROMA_COLLECTION_MISSING)
        raise ValueError(CHROMA_VECTOR_DIM_MISMATCH)


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _row_key(collection: str, memory_id: str) -> str:
    return f"{str(collection).strip()}::{str(memory_id).strip()}"
