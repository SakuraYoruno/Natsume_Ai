from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# 预算双路由：本地 14B 和云端 Gemini 使用不同的 token 预算
# ---------------------------------------------------------------------------
# 本地 14B（Qwen3:14B，32K 上下文）：总 prompt 控制在 ~5000t 以内
# 云端 Gemini（2.5 Flash，1M 上下文）：充裕预算，让检索质量最大化
# ---------------------------------------------------------------------------

RECALL_SLOT_BUDGET_LOCAL: Dict[str, int] = {
    "world_canon": 1000,
    "episodic_vector": 1200,
    "bond_graph": 600,
    "artifact_vault": 300,
    "sqlite_ledger": 500,
}
RECALL_BUDGET_LOCAL: int = 3600  # 本地 14B 总检索预算

RECALL_SLOT_BUDGET_CLOUD: Dict[str, int] = {
    "world_canon": 2000,
    "episodic_vector": 3000,
    "bond_graph": 1500,
    "artifact_vault": 800,
    "sqlite_ledger": 1200,
}
RECALL_BUDGET_CLOUD: int = 8500  # 云端 Gemini 总检索预算

# 默认使用本地预算（向后兼容）
RECALL_SLOT_TOKEN_BUDGET: Dict[str, int] = dict(RECALL_SLOT_BUDGET_LOCAL)
RECALL_PACK_TOKEN_BUDGET: int = RECALL_BUDGET_LOCAL


def get_budget_for_route(route_target: str) -> tuple[Dict[str, int], int]:
    """根据 LLM 路由目标返回对应的预算配置。

    Args:
        route_target: "host_14b" → 本地预算, "cloud" / 其他 → 云端预算

    Returns:
        (slot_budget_dict, total_budget)
    """
    if route_target == "host_14b":
        return dict(RECALL_SLOT_BUDGET_LOCAL), RECALL_BUDGET_LOCAL
    return dict(RECALL_SLOT_BUDGET_CLOUD), RECALL_BUDGET_CLOUD

# Higher-priority slots survive truncation longer.
RECALL_SLOT_PRIORITY: List[str] = [
    "world_canon",
    "episodic_vector",
    "bond_graph",
    "artifact_vault",
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
    episodic_vector: Optional[RecallSlot] = None
    bond_graph: Optional[RecallSlot] = None
    artifact_vault: Optional[RecallSlot] = None
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
