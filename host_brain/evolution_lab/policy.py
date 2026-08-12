"""Codex 自演进实验的最小分支隔离策略。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvolutionBranchPolicy:
    """限制实验提交只能出现于明确的演进分支命名空间。"""
    allowed_prefixes: tuple[str, ...] = ("codex/evolve-", "evolution/")

    def validate(self, branch_name: str) -> tuple[bool, str | None]:
        # 这是运行实验前的防误操作围栏，不是 Git 授权替代品；不匹配的主干名称
        # 必须被拒绝，避免实验性变更与正常生产提交混在同一历史中。
        normalized = str(branch_name or "").strip()
        if not normalized:
            return False, "EVOLUTION_BRANCH_REQUIRED"
        if any(normalized.startswith(prefix) for prefix in self.allowed_prefixes):
            return True, None
        return False, "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED"


def validate_evolution_branch(branch_name: str, *, policy: EvolutionBranchPolicy | None = None) -> tuple[bool, str | None]:
    return (policy or EvolutionBranchPolicy()).validate(branch_name)
