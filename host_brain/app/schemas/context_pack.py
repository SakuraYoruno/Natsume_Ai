"""
context_pack.py —— ContextPack 数据结构

定义七路召回证据的容器，包含 token 预算追踪字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# token 预算常量（各路上限）
# ---------------------------------------------------------------------------

TOKEN_BUDGET: Dict[str, int] = {
    "world_canon": 800,
    "style_canon": 400,
    "episodic_vector": 600,
    "bond_graph": 300,
    "artifact_vault": 200,
    "persona_utterance": 200,
    "sqlite_ledger": 200,
}

TOTAL_TOKEN_BUDGET: int = 2700
"""ContextPack 总 token 上限"""

# 截断优先级顺序（越靠前越优先保留）
TRUNCATION_PRIORITY: List[str] = [
    "world_canon",
    "style_canon",
    "episodic_vector",
    "bond_graph",
    "artifact_vault",
    "persona_utterance",
    "sqlite_ledger",
]


# ---------------------------------------------------------------------------
# 数据类：单路证据条目
# ---------------------------------------------------------------------------

@dataclass
class EvidenceSlot:
    """单路召回证据槽位。"""
    content: str
    """证据文本内容"""
    token_count: int = 0
    """实际 token 消耗"""
    truncated: bool = False
    """是否被截断"""
    conflict_suppressed: bool = False
    """是否因与 world_canon 冲突而被标记为 [CONFLICT_SUPPRESSED]"""
    source_ids: List[str] = field(default_factory=list)
    """来源记录 ID 列表"""


# ---------------------------------------------------------------------------
# 数据类：ContextPack
# ---------------------------------------------------------------------------

@dataclass
class ContextPack:
    """
    七路召回证据容器。

    各路证据按 TOKEN_BUDGET 限制截断，总量不超过 TOTAL_TOKEN_BUDGET。
    组装完成后在 subgraph_trace 中记录各路实际消耗和截断情况。
    """
    # 七路证据槽位
    world_canon: Optional[EvidenceSlot] = None
    """WorldCanonStore 证据（最高优先级，SSOT）"""
    style_canon: Optional[EvidenceSlot] = None
    """StyleCanonStore 证据"""
    episodic_vector: Optional[EvidenceSlot] = None
    """EpisodicStore 向量召回证据"""
    bond_graph: Optional[EvidenceSlot] = None
    """Bond 图谱证据"""
    artifact_vault: Optional[EvidenceSlot] = None
    """ArtifactVault 证据"""
    persona_utterance: Optional[EvidenceSlot] = None
    """Persona 话语证据"""
    sqlite_ledger: Optional[EvidenceSlot] = None
    """SQLiteLedger 证据"""

    # 追踪字段
    total_token_count: int = 0
    """实际总 token 消耗"""
    subgraph_trace: List[Dict] = field(default_factory=list)
    """各路证据的 token 消耗和截断情况审计记录"""
    conflict_events: List[Dict] = field(default_factory=list)
    """语义冲突事件列表"""

    def get_slot(self, slot_name: str) -> Optional[EvidenceSlot]:
        """按名称获取证据槽位。"""
        return getattr(self, slot_name, None)

    def set_slot(self, slot_name: str, slot: EvidenceSlot) -> None:
        """按名称设置证据槽位。"""
        if slot_name not in TOKEN_BUDGET:
            raise ValueError(f"未知的证据槽位名称: {slot_name}")
        setattr(self, slot_name, slot)

    def recalculate_total(self) -> int:
        """重新计算并更新总 token 消耗。"""
        total = 0
        for slot_name in TOKEN_BUDGET:
            slot = self.get_slot(slot_name)
            if slot is not None:
                total += slot.token_count
        self.total_token_count = total
        return total

    def to_prompt_text(self) -> str:
        """将所有有效证据槽位拼接为 prompt 注入文本。"""
        parts: List[str] = []
        for slot_name in TRUNCATION_PRIORITY:
            slot = self.get_slot(slot_name)
            if slot is None:
                continue
            if slot.conflict_suppressed:
                parts.append(f"[{slot_name.upper()}] [CONFLICT_SUPPRESSED]")
            else:
                parts.append(f"[{slot_name.upper()}]\n{slot.content}")
        return "\n\n".join(parts)
