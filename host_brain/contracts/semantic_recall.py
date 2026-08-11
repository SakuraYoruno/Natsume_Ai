"""Layer-neutral semantic recall result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticHit:
    """One semantic recall hit."""

    doc_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "chromadb"


@dataclass
class SemanticRecallResult:
    """Portable result shared by recall adapters and read authorities."""

    hits: list[SemanticHit]
    query: str
    top_k: int
    threshold: float
    total_found: int
    filtered_count: int
    degraded: bool = False
    degraded_reason: str | None = None
    latency_ms: int = 0

    def to_context_text(self, max_tokens: int = 600) -> str:
        if not self.hits:
            return ""
        lines = ["[EPISODIC_VECTOR]"]
        total_chars = len(lines[0])
        for index, hit in enumerate(self.hits, 1):
            line = (
                f"[{index}] (score={hit.score:.2f}) "
                f"{hit.content}"
            )
            total_chars += len(line)
            if total_chars // 3 > max_tokens:
                break
            lines.append(line)
        return "\n".join(lines)


__all__ = ["SemanticHit", "SemanticRecallResult"]
