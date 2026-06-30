"""Life memory data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class EpisodeRecallPacket:
    """Recall output centered on episodes rather than loose text snippets."""

    query: str
    candidates: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "packet_kind": "EpisodeRecallPacket",
            "query": self.query,
            "candidate_count": len(self.candidates),
            "candidates": list(self.candidates),
        }

