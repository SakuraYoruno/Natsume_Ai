"""NE-06 V2 演进权威的显式应用内组合入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from host_brain.persistence.evolution_governance_ledger import (
    EvolutionGovernanceLedgerError,
    SQLiteEvolutionGovernanceLedger,
)
from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)
from host_brain.release.identity_activation import (
    IdentityReleaseActivationService,
)

from .version_activation import EvolutionVersionActivationService


@dataclass(frozen=True, slots=True)
class EvolutionGovernanceComposition:
    """V2 激活权威与只读 V1 历史投影的组合结果。"""

    enabled: bool
    reason_code: str
    ledger: SQLiteEvolutionGovernanceLedger | None = None
    repository: SQLiteEvolutionActivationV2Repository | None = None
    activation_service: EvolutionVersionActivationService | None = None
    identity_release_service: IdentityReleaseActivationService | None = None

    def close(self) -> None:
        # The repository opens short-lived SQLite connections per operation.
        return None


def build_evolution_governance_composition(
    *,
    db_path: str | Path,
) -> EvolutionGovernanceComposition:
    """仅当 0142 与 V1 只读隔离准备就绪时构造 V2。"""

    # 此处是唯一的版本权威装配点：V1 ledger 只能供历史读取，不能成为 V2
    # 写入的回退路径。任何迁移/仓库构造失败都会关闭组合，避免双写或分叉真相。
    try:
        repository = SQLiteEvolutionActivationV2Repository(db_path)
        activation_service = EvolutionVersionActivationService(repository)
        identity_release_service = IdentityReleaseActivationService(
            version_activation=activation_service,
        )
        legacy_query = SQLiteEvolutionGovernanceLedger(db_path)
    except (
        EvolutionActivationV2Error,
        EvolutionGovernanceLedgerError,
    ) as exc:
        return EvolutionGovernanceComposition(
            enabled=False,
            reason_code=exc.args[0] if exc.args else "EVOLUTION_GOVERNANCE_COMPOSITION_DISABLED",
        )
    except Exception:
        # 不泄露底层数据库细节；调用方只得到稳定的关闭原因，并保持演进激活不可用。
        return EvolutionGovernanceComposition(
            enabled=False,
            reason_code="EVOLUTION_GOVERNANCE_COMPOSITION_CONSTRUCTION_FAILED",
        )
    return EvolutionGovernanceComposition(
        enabled=True,
        reason_code="EVOLUTION_GOVERNANCE_V2_COMPOSITION_ENFORCE",
        ledger=legacy_query,
        repository=repository,
        activation_service=activation_service,
        identity_release_service=identity_release_service,
    )


__all__ = [
    "EvolutionGovernanceComposition",
    "build_evolution_governance_composition",
]
