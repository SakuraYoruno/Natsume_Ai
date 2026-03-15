from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


RECALL_SLOT_TOKEN_BUDGET: Dict[str, int] = {
    "world_canon": 800,
    "style_canon": 400,
    "episodic_vector": 600,
    "bond_graph": 300,
    "artifact_vault": 200,
    "persona_utterance": 200,
    "sqlite_ledger": 200,
}

RECALL_PACK_TOKEN_BUDGET: int = 2700

# Higher-priority slots survive truncation longer.
RECALL_SLOT_PRIORITY: List[str] = [
    "world_canon",
    "style_canon",
    "episodic_vector",
    "bond_graph",
    "artifact_vault",
    "persona_utterance",
    "sqlite_ledger",
]


@dataclass
class RecallSlot:
    content: str
    token_count: int = 0
    truncated: bool = False
    conflict_suppressed: bool = False
    source_ids: List[str] = field(default_factory=list)


@dataclass
class RecallPack:
    """Internal recall container. Prompt-facing context pack is built elsewhere."""

    world_canon: Optional[RecallSlot] = None
    style_canon: Optional[RecallSlot] = None
    episodic_vector: Optional[RecallSlot] = None
    bond_graph: Optional[RecallSlot] = None
    artifact_vault: Optional[RecallSlot] = None
    persona_utterance: Optional[RecallSlot] = None
    sqlite_ledger: Optional[RecallSlot] = None

    total_token_count: int = 0
    subgraph_trace: List[Dict] = field(default_factory=list)
    conflict_events: List[Dict] = field(default_factory=list)

    def get_slot(self, slot_name: str) -> Optional[RecallSlot]:
        return getattr(self, slot_name, None)

    def set_slot(self, slot_name: str, slot: RecallSlot) -> None:
        if slot_name not in RECALL_SLOT_TOKEN_BUDGET:
            raise ValueError(f"unknown recall slot: {slot_name}")
        setattr(self, slot_name, slot)

    def recalculate_total(self) -> int:
        total = 0
        for slot_name in RECALL_SLOT_TOKEN_BUDGET:
            slot = self.get_slot(slot_name)
            if slot is not None:
                total += slot.token_count
        self.total_token_count = total
        return total

    def to_prompt_text(self) -> str:
        parts: List[str] = []
        for slot_name in RECALL_SLOT_PRIORITY:
            slot = self.get_slot(slot_name)
            if slot is None:
                continue
            if slot.conflict_suppressed:
                parts.append(f"[{slot_name.upper()}] [CONFLICT_SUPPRESSED]")
            else:
                parts.append(f"[{slot_name.upper()}]\n{slot.content}")
        return "\n\n".join(parts)
