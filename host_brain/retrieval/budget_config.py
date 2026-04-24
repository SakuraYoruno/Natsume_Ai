from __future__ import annotations

from typing import Dict, List

# 五路检索预算：本地 14B 与云端模型使用不同的 token 档位。

RETRIEVAL_SLOT_BUDGET_LOCAL: Dict[str, int] = {
    "world_canon": 1000,
    "episodic_vector": 1200,
    "bond_graph": 600,
    "artifact_vault": 300,
    "sqlite_ledger": 500,
}
RETRIEVAL_BUDGET_LOCAL: int = 3600

RETRIEVAL_SLOT_BUDGET_CLOUD: Dict[str, int] = {
    "world_canon": 2000,
    "episodic_vector": 3000,
    "bond_graph": 1500,
    "artifact_vault": 800,
    "sqlite_ledger": 1200,
}
RETRIEVAL_BUDGET_CLOUD: int = 8500

RETRIEVAL_SLOT_TOKEN_BUDGET: Dict[str, int] = dict(RETRIEVAL_SLOT_BUDGET_LOCAL)
RETRIEVAL_TOKEN_BUDGET: int = RETRIEVAL_BUDGET_LOCAL

RETRIEVAL_SLOT_PRIORITY: List[str] = [
    "world_canon",
    "episodic_vector",
    "bond_graph",
    "artifact_vault",
    "sqlite_ledger",
]


def get_budget_for_route(route_target: str) -> tuple[Dict[str, int], int]:
    if route_target == "host_14b":
        return dict(RETRIEVAL_SLOT_BUDGET_LOCAL), RETRIEVAL_BUDGET_LOCAL
    return dict(RETRIEVAL_SLOT_BUDGET_CLOUD), RETRIEVAL_BUDGET_CLOUD
