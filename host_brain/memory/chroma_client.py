"""In-memory vector client used by episodic store contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any


EMBEDDING_MODEL_ID = "hash-embed-v1"


@dataclass(frozen=True)
class ChromaEntry:
    memory_id: str
    text: str
    embedding: tuple[float, ...]
    metadata: dict[str, Any]


class InMemoryChromaClient:
    """Dependency-free vector index for contract-level tests."""

    def __init__(self) -> None:
        self._items: dict[str, ChromaEntry] = {}

    def upsert(
        self,
        *,
        memory_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._items[memory_id] = ChromaEntry(
            memory_id=memory_id,
            text=text,
            embedding=tuple(float(v) for v in embedding),
            metadata=dict(metadata or {}),
        )

    def delete(self, memory_id: str) -> None:
        self._items.pop(memory_id, None)

    def get(self, memory_id: str) -> ChromaEntry | None:
        return self._items.get(memory_id)

    def search(self, *, query_embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        q = tuple(float(v) for v in query_embedding)
        scored: list[tuple[float, ChromaEntry]] = []
        for item in self._items.values():
            score = _cosine_similarity(q, item.embedding)
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
                }
            )
        return results


def embed_text(text: str, *, dim: int = 16) -> tuple[list[float], str]:
    """Create deterministic pseudo-embedding from text."""

    normalized = str(text).strip().encode("utf-8")
    digest = hashlib.sha256(normalized).digest()
    values: list[float] = []
    for idx in range(dim):
        byte_value = digest[idx % len(digest)]
        values.append(round(byte_value / 255.0, 6))
    return values, EMBEDDING_MODEL_ID


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
